"""Run the clarification test (eval/clarify/clarify_set_v1.json) with the agent in "ask" mode.

PAID: one agent run per item (40 items, roughly $0.10-0.30 in total with gpt-4.1-mini). Refuses
to run without --confirm-spend. Like the held-out set, the first run of a system is the quoted
one: the set must be committed and unchanged, and a run name is never reused.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from data_agent import paths
from data_agent.agent import VERSION, Agent, AgentConfig
from data_agent.bird import db_path
from data_agent.clarify_eval import (
    CLARIFY_SET,
    ClarifyItem,
    ClarifyOutcome,
    load_set,
    score,
    set_sha256,
)
from data_agent.llm import OpenAIChat
from data_agent.splits import split_is_committed
from data_agent.tools import Database


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--run-name", required=True)
    p.add_argument("--model", default="gpt-4.1-mini")
    p.add_argument("--confirm-spend", action="store_true", help="required: this makes paid calls")
    args = p.parse_args()

    if not args.confirm_spend:
        sys.exit("Refused: this run makes paid API calls. Pass --confirm-spend once approved.")
    load_dotenv(paths.REPO_ROOT / ".env")
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set.")
    if not split_is_committed(CLARIFY_SET):
        sys.exit("The clarification set is not committed (or has local changes). Commit it first.")
    out_dir = paths.EVAL_DIR / "clarify" / "results" / args.run_name
    if out_dir.exists():
        sys.exit(f"{out_dir} exists. The first run is the quoted one; pick a new run name.")
    out_dir.mkdir(parents=True)

    items = load_set()
    agent = Agent(OpenAIChat(model=args.model), AgentConfig(clarify="ask"))

    def run_one(item: ClarifyItem) -> ClarifyOutcome:
        try:
            res = agent.run(Database(db_path(item.db_id)), item.question)
        except Exception as exc:
            return ClarifyOutcome(item, False, "", "error", 0.0, f"{type(exc).__name__}: {exc}")
        return ClarifyOutcome(
            item, res.asked, res.clarification_question, res.stop_reason, res.cost_usd
        )

    outcomes = []
    for item in items:
        o = run_one(item)
        outcomes.append(o)
        print(f"{item.id} {item.kind:9s} asked={o.asked!s:5s} {o.clarification_question[:90]}")

    summary = {
        "run_name": args.run_name,
        "agent_version": VERSION,
        "model": args.model,
        "started_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "set_sha256": set_sha256(items),
        **score(outcomes),
    }
    with (out_dir / "outcomes.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for o in outcomes:
            row = {**asdict(o), "item": asdict(o.item), "correct": o.correct}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
