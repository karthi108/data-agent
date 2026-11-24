"""The clarification set is frozen like the held-out split; scoring is checked without the API."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from data_agent.agent import Agent, AgentConfig
from data_agent.clarify_eval import ClarifyItem, ClarifyOutcome, load_set, score, set_sha256
from data_agent.llm import ChatTurn, ToolCall, assistant_message
from data_agent.tools import Database

# Pinned when the set was written, before any run. Never update this constant: write a v2 set.
FROZEN_CLARIFY_SET_SHA256 = "83bf8f811f3b347bf14feadd177c8001817b5062e7beeed5f34cd2fd1a942a09"
BIRD_DBS = {
    "california_schools", "card_games", "codebase_community", "debit_card_specializing",
    "european_football_2", "financial", "formula_1", "student_club", "superhero",
    "thrombosis_prediction", "toxicology",
}  # fmt: skip


def test_set_is_frozen() -> None:
    assert set_sha256(load_set()) == FROZEN_CLARIFY_SET_SHA256


def test_set_shape() -> None:
    items = load_set()
    assert sum(i.kind == "ambiguous" for i in items) == 20
    assert sum(i.kind == "clear" for i in items) == 20
    assert {i.db_id for i in items} <= BIRD_DBS


def outcome(kind: str, asked: bool) -> ClarifyOutcome:
    return ClarifyOutcome(ClarifyItem("x", kind, "db", "q"), asked, "", "", 0.01)


def test_scoring() -> None:
    perfect = [outcome("ambiguous", True)] * 4 + [outcome("clear", False)] * 4
    always = [outcome("ambiguous", True)] * 4 + [outcome("clear", True)] * 4
    never = [outcome("ambiguous", False)] * 4 + [outcome("clear", False)] * 4
    assert score(perfect)["balanced_accuracy_pct"] == 100.0
    assert score(always)["balanced_accuracy_pct"] == 50.0
    assert score(never)["balanced_accuracy_pct"] == 50.0
    s = score([outcome("ambiguous", True), outcome("ambiguous", False), outcome("clear", True)])
    assert s["ambiguous"] == {"n": 2, "asked": 1, "asked_pct": 50.0}
    assert s["clear"]["answered_without_asking_pct"] == 0.0


def _turn(name: str, args: dict[str, Any]) -> ChatTurn:
    calls = [ToolCall("c0", name, json.dumps(args))]
    return ChatTurn("", calls, assistant_message("", calls), 100, 10, 0.0, "gpt-4.1-mini")


class OracleModel:
    """Asks exactly when the question is one of the set's ambiguous items."""

    model = "gpt-4.1-mini"

    def __init__(self, ambiguous: set[str]) -> None:
        self.ambiguous = ambiguous

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str | dict[str, Any] = "auto",
    ) -> ChatTurn:
        text = messages[1]["content"]
        if any(f"Question: {q}" in text for q in self.ambiguous):
            return _turn("ask_user", {"question": "Which one do you mean?", "options": []})
        return _turn("submit_answer", {"sql": "SELECT 1", "answer": "1"})


def test_end_to_end_with_scripted_agent(tmp_path: Path) -> None:
    db_file = tmp_path / "t.sqlite"
    sqlite3.connect(db_file).close()
    items = load_set()
    model = OracleModel({i.question for i in items if i.kind == "ambiguous"})
    agent = Agent(model, AgentConfig(clarify="ask", schema_up_front=False))
    outcomes = []
    for item in items:
        res = agent.run(Database(db_file), item.question)
        outcomes.append(
            ClarifyOutcome(item, res.asked, res.clarification_question, res.stop_reason, 0.0)
        )
    s = score(outcomes)
    assert s["balanced_accuracy_pct"] == 100.0
    assert all(o.stop_reason == "asked_user" for o in outcomes if o.item.kind == "ambiguous")
