"""Chart spec built straight from the final query result (no model involved).

Because the chart is drawn from the result rows themselves, it can only show numbers the query
returned. Rules: at least 2 rows; x = first non-numeric column, y = first numeric column after
it (or the second column if all are numeric); dates or months on x give a line chart, anything
else a bar chart sorted by value.
"""

from __future__ import annotations

import re
from typing import Any

MAX_POINTS = 50
_DATE = re.compile(r"^\d{4}(-\d{2}){1,2}")


def _is_number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def build_chart(columns: list[str], rows: list[tuple[Any, ...]]) -> dict[str, Any] | None:
    if len(rows) < 2 or len(columns) < 2:
        return None
    rows = rows[:MAX_POINTS]
    numeric = [
        i
        for i in range(len(columns))
        if all(_is_number(r[i]) for r in rows if r[i] is not None)
        and any(r[i] is not None for r in rows)
    ]
    non_numeric = [i for i in range(len(columns)) if i not in numeric]
    if not numeric:
        return None
    x = non_numeric[0] if non_numeric else 0
    y = next((i for i in numeric if i != x), None)
    if y is None:
        return None
    xs = [str(r[x]) for r in rows]
    temporal = all(_DATE.match(v) for v in xs)
    values = [{"x": xv, "y": r[y]} for xv, r in zip(xs, rows, strict=True)]
    x_title, y_title = columns[x], columns[y]
    tooltip = [
        {"field": "x", "title": x_title},
        {"field": "y", "title": y_title, "type": "quantitative"},
    ]
    if temporal:
        mark: dict[str, Any] = {"type": "line", "point": True}
        encoding: dict[str, Any] = {
            "x": {
                "field": "x",
                "type": "ordinal",
                "title": x_title,
                "axis": {"labelAngle": 0, "labelOverlap": "greedy"},
            },
            "y": {"field": "y", "type": "quantitative", "title": y_title},
            "tooltip": tooltip,
        }
        height = 260
    else:
        # Horizontal bars: category labels stay readable whatever their length.
        mark = {"type": "bar"}
        encoding = {
            "y": {
                "field": "x",
                "type": "nominal",
                "title": None,
                "sort": "-x",
                "axis": {"labelLimit": 220},
            },
            "x": {
                "field": "y",
                "type": "quantitative",
                "title": y_title,
                "axis": {"labelOverlap": "greedy", "tickCount": 5},
            },
            "tooltip": tooltip,
        }
        height = 26 * len(values) + 20
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "background": "transparent",
        "data": {"values": values},
        "mark": mark,
        "encoding": encoding,
        "width": "container",
        "height": height,
    }
