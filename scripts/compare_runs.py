"""Compare evaluation runs on the same split: run-to-run noise and system-vs-system difference.

Usage:
  uv run python scripts/compare_runs.py --a RUN_DIR [RUN_DIR ...] --b RUN_DIR [RUN_DIR ...] \
      --out eval/noise_<name>.json

--a and --b are repeated runs of system A and system B (e.g. two baseline runs, two agent runs).
Reports, for each system, the score of every run, how many questions flip between runs, and
the paired difference between A and B averaged over runs, with an exact two-sided sign test.
"""

from __future__ import annotations

import argparse
import json
from math import comb
from pathlib import Path
from typing import Any


def load(run_dir: Path) -> dict[int, bool]:
    lines = (run_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
    return {int(r["question_id"]): bool(r["correct"]) for r in map(json.loads, filter(None, lines))}


def sign_test(wins: int, losses: int) -> float:
    """Exact two-sided sign test p-value (ties excluded)."""
    n = wins + losses
    if n == 0:
        return 1.0
    tail = sum(comb(n, k) for k in range(max(wins, losses), n + 1)) / 2**n
    return min(1.0, 2 * tail)


def system_stats(runs: list[dict[int, bool]]) -> dict[str, Any]:
    ids = sorted(runs[0])
    scores = [round(100 * sum(r[i] for i in ids) / len(ids), 2) for r in runs]
    always = sum(all(r[i] for r in runs) for i in ids)
    never = sum(not any(r[i] for r in runs) for i in ids)
    return {
        "runs": len(runs),
        "ex_per_run": scores,
        "ex_mean": round(sum(scores) / len(scores), 2),
        "correct_in_every_run": always,
        "wrong_in_every_run": never,
        "flipping_questions": len(ids) - always - never,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--a", nargs="+", type=Path, required=True)
    p.add_argument("--b", nargs="+", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()

    runs_a, runs_b = [load(d) for d in args.a], [load(d) for d in args.b]
    ids = sorted(runs_a[0])
    if any(sorted(r) != ids for r in runs_a + runs_b):
        raise SystemExit("runs do not cover the same questions")

    # Per question: fraction of runs correct for A and for B; B "wins" when its rate is higher.
    rate_a = {i: sum(r[i] for r in runs_a) / len(runs_a) for i in ids}
    rate_b = {i: sum(r[i] for r in runs_b) / len(runs_b) for i in ids}
    b_wins = sum(rate_b[i] > rate_a[i] for i in ids)
    a_wins = sum(rate_a[i] > rate_b[i] for i in ids)
    report = {
        "a": {"runs": [str(d.as_posix()) for d in args.a], **system_stats(runs_a)},
        "b": {"runs": [str(d.as_posix()) for d in args.b], **system_stats(runs_b)},
        "paired": {
            "questions": len(ids),
            "b_better": b_wins,
            "a_better": a_wins,
            "tied": len(ids) - a_wins - b_wins,
            "mean_ex_difference_b_minus_a": round(
                100 * (sum(rate_b.values()) - sum(rate_a.values())) / len(ids), 2
            ),
            "sign_test_p_two_sided": round(sign_test(b_wins, a_wins), 3),
        },
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
