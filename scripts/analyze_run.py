"""Break down the failures of one evaluation run by shape of the mismatch.

For each wrong result, re-runs both queries (read-only) and classifies the difference:
- wrong_columns: different number of columns (extra or missing output fields)
- wrong_row_count: same columns, different number of distinct rows
- float_precision_only: same rows once floats are rounded to 6 decimals (still wrong under
  BIRD's strict metric, which stays the headline number)
- wrong_values: same shape, different values (logic, filter, rounding or type errors)

Usage: uv run python scripts/analyze_run.py eval/results/dev/<run_name>
Writes failure_analysis.json next to the run's predictions.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from data_agent.bird import db_path
from data_agent.evaluate import GOLD_TIMEOUT_S, SQL_TIMEOUT_S
from data_agent.sql_exec import execute_readonly


def classify(rec: dict[str, Any]) -> str:
    db = db_path(rec["db_id"])
    pred = execute_readonly(db, rec["pred_sql"], SQL_TIMEOUT_S)
    gold = execute_readonly(db, rec["gold_sql"], GOLD_TIMEOUT_S)
    if not pred.ok or not gold.ok or pred.rows is None or gold.rows is None:
        return "rerun_failed"
    pred_cols = len(pred.rows[0]) if pred.rows else None
    gold_cols = len(gold.rows[0]) if gold.rows else None
    if pred_cols is not None and pred_cols != gold_cols:
        return "wrong_columns"
    if len(set(pred.rows)) != len(set(gold.rows)):
        return "wrong_row_count"
    if _rounded(pred.rows) == _rounded(gold.rows):
        return "float_precision_only"  # wrong under strict EX, equal at 6 decimals
    return "wrong_values"


def _rounded(rows: list[tuple[Any, ...]]) -> set[tuple[Any, ...]]:
    return {tuple(round(v, 6) if isinstance(v, float) else v for v in row) for row in rows}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run_dir", type=Path)
    args = p.parse_args()
    records = [
        json.loads(line)
        for line in (args.run_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    detail: dict[int, str] = {}
    for rec in records:
        if rec["failure"] == "wrong_result":
            detail[rec["question_id"]] = classify(rec)
        elif rec["failure"]:
            detail[rec["question_id"]] = rec["failure"]
    errors = {
        rec["question_id"]: (rec["pred_error"] or "").splitlines()[0]
        for rec in records
        if rec["failure"] in {"sql_error", "timeout", "system_error"}
    }
    report = {
        "n": len(records),
        "correct": sum(r["correct"] for r in records),
        "failure_breakdown": dict(Counter(detail.values()).most_common()),
        "by_question": {str(k): v for k, v in sorted(detail.items())},
        "execution_errors": {str(k): v for k, v in sorted(errors.items())},
    }
    out = args.run_dir / "failure_analysis.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: report[k] for k in ("failure_breakdown", "execution_errors")}, indent=2))


if __name__ == "__main__":
    main()
