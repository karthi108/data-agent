"""Sanity check the benchmark data: every gold SQL must run on its database and return rows.

BIRD's own manifest reports 0 errors and 500/500 non-empty results for the SQLite gold queries.
If this script disagrees, our databases do not match the annotations and scores would be wrong.
No model is involved; this only touches the gold SQL.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor

from data_agent import paths
from data_agent.bird import Question, db_path, load_questions
from data_agent.evaluate import GOLD_TIMEOUT_S
from data_agent.sql_exec import ExecResult, execute_readonly


def main() -> None:
    questions = load_questions()

    def run(q: Question) -> tuple[Question, ExecResult]:
        return q, execute_readonly(db_path(q.db_id), q.gold_sql, GOLD_TIMEOUT_S)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, questions.values()))

    errors = [(q, r) for q, r in results if not r.ok]
    empty = [(q, r) for q, r in results if r.ok and not r.rows]
    slow = sorted((r.elapsed_s, q.question_id) for q, r in results if r.elapsed_s > 5)
    for q, r in errors:
        print(f"ERROR  q{q.question_id} {q.db_id}: {r.error}")
    for q, _ in empty:
        print(f"EMPTY  q{q.question_id} {q.db_id}")
    report = {
        "questions": len(results),
        "errors": len(errors),
        "empty": len(empty),
        "slow_over_5s": [{"question_id": i, "seconds": round(s, 1)} for s, i in slow],
        "max_seconds": round(max(r.elapsed_s for _, r in results), 2),
    }
    out = paths.EVAL_DIR / "gold_check.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, indent=2))
    if errors or empty:
        sys.exit(1)


if __name__ == "__main__":
    main()
