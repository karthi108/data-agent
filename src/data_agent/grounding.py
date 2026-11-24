"""Grounding guard: every number in an answer must come from a query result the agent ran.

A number in the answer counts as grounded when it matches a number observed in a query result
(a numeric cell, a number inside a text cell such as a date, or a result's row count), allowing:
- the rounding the answer itself shows (57.14 matches 57.142857; 1616 matches 1616.0);
- percentage scale (57.14% or 57.14 matches 0.5714, and 0.5714 matches 57.14);
Numbers that appear in the question or hint (thresholds, years, ids the user gave) need no
grounding. Pure function, no model calls.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

# A number not glued to letters (skips "Q1", "COVID-19", "gpt4"): optional sign, digits with
# optional thousands commas, optional decimals, optional percent sign.
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.])(?<![A-Za-z]-)"
    r"(-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(\s?%)?(?![A-Za-z0-9_])"
)
_REL_TOL = 1e-9


@dataclass(frozen=True)
class Mention:
    raw: str  # as written in the answer
    value: float
    decimals: int  # digits after the decimal point, as written
    percent: bool


@dataclass
class GroundingReport:
    checked: list[str] = field(default_factory=list)  # numbers that needed grounding
    ungrounded: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.ungrounded


def extract_numbers(text: str) -> list[Mention]:
    out = []
    for m in _NUMBER.finditer(text):
        digits = m.group(1)
        decimals = len(digits.split(".", 1)[1]) if "." in digits else 0
        try:
            value = float(digits.replace(",", ""))
        except ValueError:
            continue
        out.append(Mention(m.group(0).strip(), value, decimals, bool(m.group(2))))
    return out


def observed_numbers(cells: Iterable[Any]) -> set[float]:
    """Numbers visible in result cells: numeric values, and numbers inside text values."""
    seen: set[float] = set()
    for v in cells:
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, int | float):
            if math.isfinite(float(v)):
                seen.add(float(v))
        elif isinstance(v, str):
            seen.update(m.value for m in extract_numbers(v))
    return seen


def _matches(mention: Mention, x: float) -> bool:
    # Tolerance = half a unit of the last digit the answer shows (it may have rounded).
    tol = 0.5 * 10 ** (-mention.decimals) + _REL_TOL * max(1.0, abs(x))
    candidates = [mention.value, mention.value / 100, mention.value * 100]
    scales = [1.0, 0.01, 100.0]
    return any(abs(c - x) <= tol * s + _REL_TOL for c, s in zip(candidates, scales, strict=True))


def check_answer(answer: str, observed: set[float], context: str = "") -> GroundingReport:
    """Check every number in ``answer`` against ``observed``; numbers in ``context`` are free."""
    given = {m.value for m in extract_numbers(context)}
    report = GroundingReport()
    for mention in extract_numbers(answer):
        if mention.value in given:
            continue
        report.checked.append(mention.raw)
        if not any(_matches(mention, x) for x in observed):
            report.ungrounded.append(mention.raw)
    return report
