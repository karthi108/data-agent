"""Measure what the reviewer did in an agent run.

For every question where the reviewer sent a submission back, score the query it sent back
(the agent's answer before review) and compare it with the final answer:
  fixed  = was wrong before review, right after
  broken = was right before review, wrong after
  same   = no change in correctness

Usage: uv run python scripts/review_effect.py RUN_DIR [RUN_DIR ...] --out FILE
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from data_agent.bird import db_path
from data_agent.evaluate import GOLD_TIMEOUT_S, SQL_TIMEOUT_S
from data_agent.metrics import execution_match
from data_agent.sql_exec import execute_readonly


def is_correct(db_id: str, sql: str, gold_sql: str) -> bool:
    pred = execute_readonly(db_path(db_id), sql, SQL_TIMEOUT_S)
    gold = execute_readonly(db_path(db_id), gold_sql, GOLD_TIMEOUT_S)
    if not pred.ok or not gold.ok:
        return False
    return execution_match(pred.rows or [], gold.rows or [])


def analyse(run_dir: Path) -> dict[str, Any]:
    preds = {
        r["question_id"]: r
        for r in map(json.loads, (run_dir / "predictions.jsonl").read_text("utf-8").splitlines())
    }
    verdicts: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    detail = []
    for line in (run_dir / "traces.jsonl").read_text("utf-8").splitlines():
        t = json.loads(line)
        qid, trace = t["question_id"], t["trace"]
        reviews = [e for e in trace if e["type"] == "review"]
        for rv in reviews:
            verdicts[rv.get("verdict", "error")] += 1
        flagged = [rv for rv in reviews if rv.get("verdict") == "fix" and rv.get("issues")]
        if not flagged:
            continue
        step = flagged[0]["step"]
        before = next(
            e["query"] for e in trace if e["type"] == "submit_check" and e["step"] == step
        )
        rec = preds[qid]
        was = is_correct(rec["db_id"], before, rec["gold_sql"])
        now = bool(rec["correct"])
        kind = "fixed" if now and not was else "broken" if was and not now else "same"
        outcomes[kind] += 1
        detail.append({"question_id": qid, "effect": kind, "stop_reason": rec["stop_reason"]})
    return {
        "run": run_dir.as_posix(),
        "review_verdicts": dict(verdicts),
        "flagged_questions": sum(outcomes.values()),
        "effect": {k: outcomes.get(k, 0) for k in ("fixed", "broken", "same")},
        "flagged_detail": detail,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("runs", nargs="+", type=Path)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    report = [analyse(r) for r in args.runs]
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    for r in report:
        print(r["run"], r["review_verdicts"], "flagged:", r["flagged_questions"], r["effect"])


if __name__ == "__main__":
    main()
