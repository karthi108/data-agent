"""Grounding guard: every number in an answer must come from a query result the agent ran.

A number in the answer counts as grounded when it matches a number observed in a query result
(a numeric cell, a number inside a text cell such as a date, or a result's row count), allowing:
- the rounding the answer itself shows (57.14 matches 57.142857; 1616 matches 1616.0);
- percentage scale (57.14% or 57.14 matches 0.5714, and 0.5714 matches 57.14);
Numbers that appear in the question or hint (thresholds, years, ids the user gave) need no
grounding. Dates are checked as whole dates, whatever their format ("2014-09-19",
"19 September 2014", "September 19, 2014", "September 2014"): a date in the answer is grounded
if the same date appears in the question, the hint or a query result. Pure function, no model
calls.
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

_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july", "august",
         "september", "october", "november", "december"]
    )
}  # fmt: skip
_MON = "|".join([*_MONTHS, *(m[:3] for m in _MONTHS), "sept"])
_DATE_PATTERNS = [
    # 2014-09-19 or 2014-09 (optionally followed by a time)
    re.compile(r"(?<!\d)(\d{4})-(\d{1,2})(?:-(\d{1,2}))?(?![\d-])"),
    # 19 September 2014
    re.compile(rf"(?<!\w)(\d{{1,2}})\s+({_MON})\.?,?\s+(\d{{4}})(?!\d)", re.IGNORECASE),
    # September 19, 2014
    re.compile(rf"(?<!\w)({_MON})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})(?!\d)", re.IGNORECASE),
    # September 2014
    re.compile(rf"(?<!\w)({_MON})\.?\s+(\d{{4}})(?!\d)", re.IGNORECASE),
]


def _month(name: str) -> int:
    key = name.lower().rstrip(".")
    return _MONTHS.get(key) or next(v for k, v in _MONTHS.items() if k.startswith(key[:3]))


def find_dates(text: str) -> list[tuple[tuple[int, int], str]]:
    """Dates in ``text`` as ((start, end), canonical) with canonical 'YYYY-MM-DD' or 'YYYY-MM'."""
    found: list[tuple[tuple[int, int], str]] = []
    taken: list[tuple[int, int]] = []
    for i, pat in enumerate(_DATE_PATTERNS):
        for m in pat.finditer(text):
            span = m.span()
            if any(a < span[1] and span[0] < b for a, b in taken):
                continue
            g = m.groups()
            if i == 0:
                year, month, day = int(g[0]), int(g[1]), int(g[2]) if g[2] else None
            elif i == 1:
                day, month, year = int(g[0]), _month(g[1]), int(g[2])
            elif i == 2:
                month, day, year = _month(g[0]), int(g[1]), int(g[2])
            else:
                month, year, day = _month(g[0]), int(g[1]), None
            if not 1 <= month <= 12 or (day is not None and not 1 <= day <= 31):
                continue
            canonical = f"{year:04d}-{month:02d}" + (f"-{day:02d}" if day else "")
            found.append((span, canonical))
            taken.append(span)
    return sorted(found)


def date_values(texts: Iterable[str]) -> set[str]:
    """Canonical dates found in ``texts`` (for result cells and the question)."""
    out: set[str] = set()
    for t in texts:
        out.update(c for _, c in find_dates(t))
    return out


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


def check_answer(
    answer: str,
    observed: set[float],
    context: str = "",
    observed_dates: set[str] | None = None,
) -> GroundingReport:
    """Check every number and date in ``answer``; numbers and dates in ``context`` are free."""
    known_dates = date_values([context]) | (observed_dates or set())
    report = GroundingReport()
    # Dates first, as whole values; then remove them so their parts are not read as numbers.
    rest = answer
    for (start, end), canonical in reversed(find_dates(answer)):
        raw = answer[start:end]
        report.checked.append(raw)
        month_level = len(canonical) == 7
        ok = canonical in known_dates or (
            month_level and any(d.startswith(canonical + "-") for d in known_dates)
        )
        if not ok:
            report.ungrounded.append(raw)
        rest = rest[:start] + " " + rest[end:]
    context_rest = context
    for (start, end), _ in reversed(find_dates(context)):
        context_rest = context_rest[:start] + " " + context_rest[end:]
    given = {m.value for m in extract_numbers(context)} | {
        m.value for m in extract_numbers(context_rest)
    }
    for mention in extract_numbers(rest):
        if mention.value in given:
            continue
        report.checked.append(mention.raw)
        if not any(_matches(mention, x) for x in observed):
            report.ungrounded.append(mention.raw)
    return report
