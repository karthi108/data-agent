"""Replay the grounding guard on a finished agent run, offline (no model calls).

For each question, re-runs locally every query the agent ran (from its trace) plus the final
SQL, to rebuild the evidence it saw, then checks the agent's final answer text with the guard.
Shows how often the guard would pass, find no numbers, or flag an answer, with examples.

Usage: uv run python scripts/guard_replay.py RUN_DIR --out FILE
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from data_agent.bird import db_path, load_questions
from data_agent.grounding import check_answer
from data_agent.tools import Database


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("run_dir", type=Path)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()

    questions = load_questions()
    preds = {
        r["question_id"]: r
        for r in map(
            json.loads, (args.run_dir / "predictions.jsonl").read_text("utf-8").split("\n")[:-1]
        )
    }
    verdicts: Counter[str] = Counter()
    flagged = []
    for line in (args.run_dir / "traces.jsonl").read_text("utf-8").splitlines():
        t = json.loads(line)
        qid = t["question_id"]
        q = questions[qid]
        answer = preds[qid]["raw_response"]
        db = Database(db_path(q.db_id))
        for e in t["trace"]:
            if e["type"] in {"sql", "submit_check"} and e.get("error") is None:
                db.run_sql(e["query"])
        if preds[qid]["pred_sql"]:
            db.run_sql(preds[qid]["pred_sql"])
        report = check_answer(answer, db.observed, context=f"{q.question}\n{q.evidence}")
        verdict = (
            "no_answer"
            if not answer
            else "no_numbers"
            if not report.checked
            else ("pass" if report.ok else "flagged")
        )
        verdicts[verdict] += 1
        if verdict == "flagged":
            flagged.append(
                {
                    "question_id": qid,
                    "answer": answer,
                    "ungrounded": report.ungrounded,
                    "correct_sql": preds[qid]["correct"],
                }
            )
    report_out = {"run": args.run_dir.as_posix(), "verdicts": dict(verdicts), "flagged": flagged}
    args.out.write_text(json.dumps(report_out, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(dict(verdicts)))
    for f in flagged[:15]:
        print(f"q{f['question_id']} ungrounded={f['ungrounded']} :: {f['answer'][:160]}")


if __name__ == "__main__":
    main()
