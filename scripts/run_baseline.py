"""Single-shot text-to-SQL baseline on BIRD Mini-Dev.

One LLM call per question with BIRD's official fixed baseline prompt (schema + question +
evidence), no loop, no retry, no self-correction. Scored with BIRD's strict execution accuracy.

Held-out rules (enforced here, see PLAN.md):
- refuses to run on any split unless the split file is committed and unchanged;
- a held-out run needs --confirm-frozen-heldout-run, cannot be partial, and cannot reuse a run
  name: the first held-out run of a system is the one that is quoted;
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

from data_agent import paths
from data_agent.baseline import extract_sql
from data_agent.bird import Question, load_baseline_prompts, load_questions
from data_agent.evaluate import Prediction, Record, System, run, summarize, write_records
from data_agent.llm import LLMClient, OpenAIClient
from data_agent.splits import load_split, split_ids_sha256, split_is_committed


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


def make_system(client: LLMClient, prompts: dict[int, str]) -> System:
    """Single-shot system: the official prompt in, the last SQL block out."""

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
    p.add_argument("--split", choices=["dev", "heldout"], required=True)
    p.add_argument("--run-name", required=True, help="e.g. single_shot_gpt41mini")
    p.add_argument("--model", default="gpt-4.1-mini")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--limit", type=int, help="dev only: first N questions (smoke test)")
    p.add_argument("--confirm-frozen-heldout-run", action="store_true")
    args = p.parse_args()

    load_dotenv(paths.REPO_ROOT / ".env")
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set (shell variable or .env file in the repo root).")
    if not split_is_committed():
        sys.exit(
            "The split file is not committed (or has local changes). Commit it before any run."
        )
    split = load_split()
    out_dir = paths.RESULTS_DIR / args.split / args.run_name

    if args.split == "heldout":
        if not args.confirm_frozen_heldout_run:
            sys.exit("Held-out run refused: pass --confirm-frozen-heldout-run. It runs once.")
        if args.limit is not None:
            sys.exit("Held-out run refused: partial held-out runs are not allowed.")
        if out_dir.exists():
            sys.exit(f"Held-out run refused: {out_dir} exists. The first run is the quoted one.")
    elif out_dir.exists() and args.limit is None:
        sys.exit(f"{out_dir} exists; pick a new --run-name (results are never overwritten).")

    questions = load_questions()
    prompts = load_baseline_prompts()
    ids = list(split.ids(args.split))
    if args.limit is not None:
        ids = ids[: args.limit]
        out_dir = out_dir.with_name(f"{args.run_name}__smoke{args.limit}")
    out_dir.mkdir(parents=True, exist_ok=args.limit is not None)

    client = OpenAIClient(model=args.model)
    system = make_system(client, prompts)

    done = 0
    lock = threading.Lock()

    def progress(rec: Record) -> None:
        nonlocal done
        with lock:
            done += 1
            mark = "ok " if rec.correct else (rec.failure or "?")
            print(f"[{done:3d}/{len(ids)}] q{rec.question_id:<3d} {mark}", flush=True)

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
        "system": "single-shot text-to-SQL (BIRD fixed baseline prompt, 1 call, no retry)",
        "split": args.split,
        "limit": args.limit,
        "model": args.model,
        "temperature": 0,
        "started_utc": started.isoformat(timespec="seconds"),
        "wall_clock_s": round(wall, 1),
        "workers": args.workers,
        "git": git_state(),
        "split_ids_sha256": split_ids_sha256(split),
        "prompts_sha256": hashlib.sha256(paths.BIRD_BASELINE_PROMPTS.read_bytes()).hexdigest(),
        "metric": "BIRD strict execution accuracy: set(pred_rows) == set(gold_rows)",
        **summarize(records),
    }
    write_records(records, out_dir / "predictions.jsonl")
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps({k: summary[k] for k in ("overall", "failures", "cost_usd_total")}, indent=2))
    print(f"results: {out_dir}")


if __name__ == "__main__":
    main()
