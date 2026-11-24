"""Clarification test: does the agent ask on ambiguous questions and answer clear ones?"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data_agent import paths

CLARIFY_SET = paths.EVAL_DIR / "clarify" / "clarify_set_v1.json"


@dataclass(frozen=True)
class ClarifyItem:
    id: str
    kind: str  # "ambiguous" | "clear"
    db_id: str
    question: str


def load_set(path: Path = CLARIFY_SET) -> list[ClarifyItem]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = [ClarifyItem(i["id"], i["kind"], i["db_id"], i["question"]) for i in data["items"]]
    if len({i.id for i in items}) != len(items):
        raise ValueError("duplicate ids in clarification set")
    if {i.kind for i in items} != {"ambiguous", "clear"}:
        raise ValueError("clarification set needs both ambiguous and clear items")
    return items


def set_sha256(items: list[ClarifyItem]) -> str:
    """Hash of ids, kinds, databases and question texts (independent of formatting)."""
    canonical = json.dumps(
        [[i.id, i.kind, i.db_id, i.question] for i in items], separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class ClarifyOutcome:
    item: ClarifyItem
    asked: bool
    clarification_question: str
    stop_reason: str
    cost_usd: float
    error: str | None = None

    @property
    def correct(self) -> bool:
        return self.error is None and self.asked == (self.item.kind == "ambiguous")


AskFn = Callable[[ClarifyItem], ClarifyOutcome]


def score(outcomes: list[ClarifyOutcome]) -> dict[str, Any]:
    amb = [o for o in outcomes if o.item.kind == "ambiguous"]
    clear = [o for o in outcomes if o.item.kind == "clear"]

    def rate(k: int, n: int) -> float | None:
        return round(100 * k / n, 1) if n else None

    asked_amb = sum(o.asked for o in amb)
    asked_clear = sum(o.asked for o in clear)
    return {
        "ambiguous": {"n": len(amb), "asked": asked_amb, "asked_pct": rate(asked_amb, len(amb))},
        "clear": {
            "n": len(clear),
            "asked": asked_clear,
            "answered_without_asking_pct": rate(len(clear) - asked_clear, len(clear)),
        },
        "balanced_accuracy_pct": rate(
            asked_amb * len(clear) + (len(clear) - asked_clear) * len(amb),
            2 * len(amb) * len(clear),
        ),
        "errors": sum(o.error is not None for o in outcomes),
        "cost_usd_total": round(sum(o.cost_usd for o in outcomes), 4),
    }
