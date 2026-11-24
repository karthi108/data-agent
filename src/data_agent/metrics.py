"""Evaluation metrics."""

from __future__ import annotations

import math
from collections.abc import Sequence

from data_agent.sql_exec import Row


def execution_match(predicted: Sequence[Row], gold: Sequence[Row]) -> bool:
    """BIRD's strict execution accuracy: ``set(predicted) == set(gold)``.

    This mirrors the official evaluator: row order and duplicate rows are ignored, values are
    compared exactly (no float tolerance), and column order matters inside each row.
    """
    return set(predicted) == set(gold)


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile (pct in 0..100). Returns NaN for an empty input."""
    if not values:
        return math.nan
    if not 0 <= pct <= 100:
        raise ValueError("pct must be within 0..100")
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]
