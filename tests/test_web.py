"""Web UI tests: API, budget, rate limit, charts. No network, no API key."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from data_agent.bird import db_path
from data_agent.llm import ChatTurn, ToolCall, assistant_message
from data_agent.web.app import EXAMPLES, create_app
from data_agent.web.budget import DailyBudget
from data_agent.web.chart import build_chart
from data_agent.web.demo import DemoModel

SQL = "SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY total DESC"


def _turn(name: str, args: dict[str, Any], cost_tokens: int = 1000) -> ChatTurn:
    calls = [ToolCall("c0", name, json.dumps(args))]
    return ChatTurn("", calls, assistant_message("", calls), cost_tokens, 10, 0.0, "gpt-4.1-mini")


class ScriptedModel:
    model = "gpt-4.1-mini"

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str | dict[str, Any] = "auto",
    ) -> ChatTurn:
        user = messages[1]["content"]
        if "which region" in user.lower() and "The user answered" not in user:
            return _turn("ask_user", {"question": "Which region?", "options": ["N", "S"]})
        return _turn("submit_answer", {"sql": SQL, "answer": "N leads with 17, S has 5."})


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "shop.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sales (region TEXT, amount INTEGER)")
    conn.executemany("INSERT INTO sales VALUES (?, ?)", [("N", 10), ("S", 5), ("N", 7)])
    conn.commit()
    conn.close()
    return path


def client(db_file: Path, budget: DailyBudget | None = None, limit: int = 20) -> TestClient:
    app = create_app(ScriptedModel, db_file, budget or DailyBudget(1.0), hourly_limit=limit)
    return TestClient(app)


def test_page_and_config(db_file: Path) -> None:
    c = client(db_file)
    assert "Agent trace" in c.get("/").text
    cfg = c.get("/api/config").json()
    assert cfg["database"] == "shop" and cfg["tables"] == ["sales"]
    assert cfg["demo"] is False and len(cfg["examples"]) == 5


def test_answer_with_result_chart_and_trace(db_file: Path) -> None:
    d = client(db_file).post("/api/ask", json={"question": "Total sales by region?"}).json()
    assert d["status"] == "answered"
    assert d["answer"] == "N leads with 17, S has 5."
    assert d["guard"] == "pass"
    assert d["result"]["columns"] == ["region", "total"]
    assert d["result"]["rows"] == [["N", 17], ["S", 5]]
    assert d["chart"]["mark"] == {"type": "bar"}
    assert any(e["type"] == "submit" for e in d["trace"])


def test_clarification_round_trip(db_file: Path) -> None:
    c = client(db_file)
    first = c.post("/api/ask", json={"question": "Sales in which region?"}).json()
    assert first["status"] == "clarify"
    assert first["clarification"] == {"question": "Which region?", "options": ["N", "S"]}
    second = c.post(
        "/api/ask",
        json={
            "question": "Sales in which region?",
            "clarifications": [{"asked": "Which region?", "answered": "N"}],
        },
    ).json()
    assert second["status"] == "answered"


def test_budget_exhausted(db_file: Path) -> None:
    d = client(db_file, DailyBudget(0.01)).post("/api/ask", json={"question": "Total?"}).json()
    assert d["status"] == "budget"


def test_rate_limit_and_validation(db_file: Path) -> None:
    c = client(db_file, limit=1)
    assert c.post("/api/ask", json={"question": "Total?"}).status_code == 200
    assert c.post("/api/ask", json={"question": "Total?"}).status_code == 429
    c2 = client(db_file)
    assert c2.post("/api/ask", json={"question": "x" * 501}).status_code == 422
    too_many = [{"asked": "a", "answered": "b"}, {"asked": "c", "answered": "d"}]
    body = {"question": "Total?", "clarifications": too_many}
    assert c2.post("/api/ask", json=body).status_code == 422


def test_budget_tracks_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    b = DailyBudget(0.10, path)
    assert b.can_spend(0.05)
    b.add(0.08)
    assert not b.can_spend(0.05) and round(b.remaining(), 2) == 0.02
    assert round(DailyBudget(0.10, path).remaining(), 2) == 0.02  # survives a restart


def test_chart_rules() -> None:
    bar = build_chart(["cat", "n"], [("a", 3), ("b", 5)])
    assert bar is not None and bar["mark"] == {"type": "bar"}
    assert bar["data"]["values"] == [{"x": "a", "y": 3}, {"x": "b", "y": 5}]
    line = build_chart(["month", "n"], [("2019-09", 8), ("2019-10", 7)])
    assert line is not None and line["mark"]["type"] == "line"
    assert build_chart(["n"], [(1,), (2,)]) is None  # one column
    assert build_chart(["a", "b"], [("x", 1)]) is None  # one row
    assert build_chart(["a", "b"], [("x", "y"), ("z", "w")]) is None  # nothing numeric


@pytest.mark.skipif(not db_path("student_club").exists(), reason="BIRD data not downloaded")
def test_demo_examples_run_end_to_end() -> None:
    app = create_app(DemoModel, db_path("student_club"), DailyBudget(1.0), demo=True)
    c = TestClient(app)
    outcomes = {}
    for ex in EXAMPLES:
        d = c.post("/api/ask", json={"question": ex["question"]}).json()
        outcomes[ex["label"]] = d
        if d["status"] == "answered":
            assert d["guard"] == "pass", (ex, d["answer"])  # demo answers are grounded too
    assert outcomes["Ambiguous: biggest expense"]["status"] == "clarify"
    fix = outcomes["Needs a fix: Kickoff costs"]
    assert any(e["type"] == "sql" and e["error"] for e in fix["trace"])  # the error...
    assert fix["result"]["rows"] == [[449.13]]  # ...and the fixed answer
    assert outcomes["Events per month"]["chart"]["mark"]["type"] == "line"
    q = "What was our biggest expense?"
    resolved = c.post(
        "/api/ask",
        json={
            "question": q,
            "clarifications": [
                {
                    "asked": outcomes["Ambiguous: biggest expense"]["clarification"]["question"],
                    "answered": "The category with the most spending",
                }
            ],
        },
    ).json()
    assert resolved["status"] == "answered" and resolved["result"]["rows"] == [["Food", 1234.73]]
