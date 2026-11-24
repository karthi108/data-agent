"""Run a system on a BIRD Mini-Dev split and score it with BIRD's strict execution accuracy.

Systems:
- single_shot: one LLM call per question with BIRD's official fixed baseline prompt (schema +
  question + evidence), no loop, no retry.
- agent: the data agent (src/data_agent/agent.py): explores the schema with tools, runs
  read-only SQL, submits a final query. Step cap and cost cap per question.

Held-out rules (enforced here, see PLAN.md):
- refuses to run on any split unless the split file is committed and unchanged;
- a held-out run needs --confirm-frozen-heldout-run, a clean git tree, cannot be partial, and
  cannot reuse a run name: the first held-out run of a system is the one that is quoted;
- the reserve split cannot be run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from data_agent import paths
from data_agent.agent import SYSTEM_PROMPT, TOOLS, VERSION, Agent, AgentConfig
from data_agent.baseline import extract_sql
from data_agent.bird import Question, db_path, load_baseline_prompts, load_questions
from data_agent.evaluate import Prediction, Record, System, run, summarize, write_records
from data_agent.llm import LLMClient, OpenAIChat, OpenAIClient
from data_agent.splits import load_split, split_ids_sha256, split_is_committed
from data_agent.tools import QUERY_TIMEOUT_S, Database


def git_state() -> dict[str, str | bool]:
    def out(*args: str) -> str:
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=paths.REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()

    return {"commit": out("rev-parse", "HEAD"), "dirty": bool(out("status", "--porcelain"))}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def single_shot_system(client: LLMClient, prompts: dict[int, str]) -> System:
    """The official prompt in, the last SQL block out."""

    def system(q: Question) -> Prediction:
        completion = client.complete(prompts[q.question_id])
        return Prediction(
            sql=extract_sql(completion.text),
            raw_response=completion.text,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cost_usd=completion.cost_usd,
            latency_s=completion.latency_s,
            steps=1,
            served_model=completion.served_model,
        )

    return system


def agent_system(agent: Agent) -> System:
    def system(q: Question) -> Prediction:
        res = agent.run(Database(db_path(q.db_id)), q.question, q.evidence)
        error = None
        if res.stop_reason == "model_error" and not res.final_sql:
            error = next((e["error"] for e in res.trace if e.get("type") == "error"), "error")
        return Prediction(
            sql=res.final_sql,
            raw_response=res.answer,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            cost_usd=res.cost_usd,
            latency_s=res.latency_s,
            steps=res.steps,
            served_model=res.served_model,
            system_error=error,
            stop_reason=res.stop_reason,
            guard=res.guard,
            trace=res.trace,
        )

    return system


def load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines; never overrides variables already set."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--system", choices=["single_shot", "agent"], required=True)
    p.add_argument("--split", choices=["dev", "heldout"], required=True)
    p.add_argument("--run-name", required=True, help="e.g. agent_v1_gpt41mini")
    p.add_argument("--model", default="gpt-4.1-mini")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--limit", type=int, help="dev only: first N questions (smoke test)")
    p.add_argument("--confirm-frozen-heldout-run", action="store_true")
    p.add_argument("--max-steps", type=int, default=AgentConfig.max_steps)
    p.add_argument("--no-schema", action="store_true", help="agent: no schema in first message")
    p.add_argument("--no-plan", action="store_true", help="agent: force tool use on step 1")
    p.add_argument("--no-self-correct", action="store_true", help="agent: accept any submission")
    p.add_argument("--review", action="store_true", help="agent: reviewer on submission (v3)")
    p.add_argument("--no-grounding", action="store_true", help="agent: no grounding guard")
    args = p.parse_args()

    load_dotenv(paths.REPO_ROOT / ".env")
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set (shell variable or .env file in the repo root).")
    if not split_is_committed():
        sys.exit("The split file is not committed (or has local changes). Commit it first.")
    split = load_split()
    out_dir = paths.RESULTS_DIR / args.split / args.run_name

    if args.split == "heldout":
        if not args.confirm_frozen_heldout_run:
            sys.exit("Held-out run refused: pass --confirm-frozen-heldout-run. It runs once.")
        if args.limit is not None:
            sys.exit("Held-out run refused: partial held-out runs are not allowed.")
        if out_dir.exists():
            sys.exit(f"Held-out run refused: {out_dir} exists. The first run is the quoted one.")
        if git_state()["dirty"]:
            sys.exit("Held-out run refused: commit everything first so the run maps to a commit.")
    elif out_dir.exists() and args.limit is None:
        sys.exit(f"{out_dir} exists; pick a new --run-name (results are never overwritten).")

    questions = load_questions()
    ids = list(split.ids(args.split))
    if args.limit is not None:
        ids = ids[: args.limit]
        out_dir = out_dir.with_name(f"{args.run_name}__smoke{args.limit}")
    out_dir.mkdir(parents=True, exist_ok=args.limit is not None)

    config: dict[str, Any]
    if args.system == "single_shot":
        system = single_shot_system(OpenAIClient(model=args.model), load_baseline_prompts())
        config = {
            "description": "single-shot text-to-SQL (BIRD fixed baseline prompt, 1 call, no retry)",
            "prompts_sha256": hashlib.sha256(paths.BIRD_BASELINE_PROMPTS.read_bytes()).hexdigest(),
        }
    else:
        agent_config = AgentConfig(
            max_steps=args.max_steps,
            schema_up_front=not args.no_schema,
            plan_turn=not args.no_plan,
            self_correct=not args.no_self_correct,
            review=args.review,
            grounding=not args.no_grounding,
        )
        system = agent_system(Agent(OpenAIChat(model=args.model), agent_config))
        config = {
            "description": f"data agent {VERSION}",
            "agent_version": VERSION,
            **agent_config.to_dict(),
            "query_timeout_s": QUERY_TIMEOUT_S,
            "system_prompt_sha256": sha256_text(SYSTEM_PROMPT),
            "tools_sha256": sha256_text(json.dumps(TOOLS, sort_keys=True)),
        }

    done = 0
    lock = threading.Lock()

    def progress(rec: Record) -> None:
        nonlocal done
        with lock:
            done += 1
            mark = "ok " if rec.correct else (rec.failure or "?")
            extra = f" steps={rec.steps} {rec.stop_reason}" if rec.stop_reason else ""
            print(f"[{done:3d}/{len(ids)}] q{rec.question_id:<3d} {mark}{extra}", flush=True)

    started = datetime.now(UTC)
    # Written before any question is sent: a crashed run still leaves evidence and, for the
    # held-out split, still blocks a second attempt under the same name.
    (out_dir / "run_started.json").write_text(
        json.dumps(
            {"started_utc": started.isoformat(timespec="seconds"), "n": len(ids), **vars(args)},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    t0 = time.perf_counter()
    records = run(system, (questions[i] for i in ids), workers=args.workers, on_record=progress)
    wall = time.perf_counter() - t0

    summary = {
        "run_name": args.run_name,
        "system": args.system,
        "config": config,
        "split": args.split,
        "limit": args.limit,
        "model": args.model,
        "temperature": 0,
        "started_utc": started.isoformat(timespec="seconds"),
        "wall_clock_s": round(wall, 1),
        "workers": args.workers,
        "git": git_state(),
        "split_ids_sha256": split_ids_sha256(split),
        "metric": "BIRD strict execution accuracy: set(pred_rows) == set(gold_rows)",
        **summarize(records),
    }
    write_records(records, out_dir / "predictions.jsonl")
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    keys = ("overall", "failures", "stop_reasons", "grounding_guard", "cost_usd_total", "avg_steps")
    print(json.dumps({k: summary[k] for k in keys}, indent=2))
    print(f"results: {out_dir}")


if __name__ == "__main__":
    main()
