"""Daily spending cap for the live site."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path


class DailyBudget:
    """Tracks API spend per UTC day. A question is refused if it could push the day over the cap.

    State is kept in memory and, if ``path`` is given, in a small JSON file so a restart on the
    same disk keeps the day's total. (On hosts with an ephemeral disk a restart resets it; the
    per-question cap still bounds each request.)
    """

    def __init__(self, limit_usd: float, path: Path | None = None) -> None:
        self.limit_usd = limit_usd
        self.path = path
        self._lock = threading.Lock()
        self._day = ""
        self._spent = 0.0
        self._load()

    @staticmethod
    def _today() -> str:
        return datetime.now(UTC).date().isoformat()

    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._day, self._spent = str(data["day"]), float(data["spent_usd"])
        except (ValueError, KeyError, OSError):
            self._day, self._spent = "", 0.0

    def _roll(self) -> None:
        today = self._today()
        if self._day != today:
            self._day, self._spent = today, 0.0

    def remaining(self) -> float:
        with self._lock:
            self._roll()
            return max(0.0, self.limit_usd - self._spent)

    def can_spend(self, worst_case_usd: float) -> bool:
        with self._lock:
            self._roll()
            return self._spent + worst_case_usd <= self.limit_usd

    def add(self, cost_usd: float) -> None:
        with self._lock:
            self._roll()
            self._spent += max(0.0, cost_usd)
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(
                    json.dumps({"day": self._day, "spent_usd": round(self._spent, 6)}),
                    encoding="utf-8",
                )
