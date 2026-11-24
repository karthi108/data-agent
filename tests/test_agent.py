"""Agent loop tests with a scripted fake model: no network, no API key."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from data_agent.agent import Agent, AgentConfig
from data_agent.llm import ChatTurn, ToolCall, assistant_message
from data_agent.tools import Database


def turn(*calls: tuple[str, dict[str, Any]], content: str = "", cost_tokens: int = 100) -> ChatTurn:
    tcs = [ToolCall(f"call_{i}", name, json.dumps(args)) for i, (name, args) in enumerate(calls)]
    return ChatTurn(
        content=content,
        tool_calls=tcs,
        assistant_message=assistant_message(content, tcs),
        input_tokens=cost_tokens,
        output_tokens=10,
        latency_s=0.01,
        model="gpt-4.1-mini",
        served_model="fake",
    )


def review(verdict: str, *issues: str) -> ChatTurn:
    return turn(("report_review", {"verdict": verdict, "issues": list(issues)}))


class FakeModel:
    model = "gpt-4.1-mini"

    def __init__(self, turns: list[ChatTurn], reviews: list[ChatTurn] | None = None) -> None:
        self.turns = turns
        self.reviews = reviews or []
        self.calls: list[dict[str, Any]] = []
        self.review_calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str | dict[str, Any] = "auto",
    ) -> ChatTurn:
        if tools and tools[0]["function"]["name"] == "report_review":
            self.review_calls.append({"messages": [dict(m) for m in messages]})
            n = len(self.review_calls) - 1
            return self.reviews[n] if n < len(self.reviews) else review("ok")
        self.calls.append({"messages": [dict(m) for m in messages], "tool_choice": tool_choice})
        return self.turns[len(self.calls) - 1]


@pytest.fixture
def db(tmp_path: Path) -> Database:
    path = tmp_path / "s.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sales (region TEXT, amount INTEGER)")
    conn.executemany("INSERT INTO sales VALUES (?, ?)", [("N", 10), ("S", 5), ("N", 7)])
    conn.commit()
    conn.close()
    return Database(path)


FINAL = "SELECT SUM(amount) FROM sales WHERE region = 'N'"


def test_full_loop_with_error_and_fix(db: Database) -> None:
    model = FakeModel(
        [
            turn(("list_tables", {}), content="Plan: find the sales table, sum amount for N."),
            turn(("run_sql", {"query": "SELECT SUM(amt) FROM sales"})),  # wrong column
            turn(("describe_table", {"table": "sales"})),
            turn(("run_sql", {"query": FINAL})),
            turn(("submit_answer", {"sql": FINAL, "answer": "17"})),
        ]
    )
    res = Agent(model).run(db, "Total sales in N?", "N is the north region")
    assert res.stop_reason == "submitted"
    assert res.final_sql == FINAL
    assert res.steps == 5
    types = [e["type"] for e in res.trace]
    assert types == [
        "question", "plan", "list_tables", "sql", "describe_table", "sql",
        "submit_check", "review", "submit",
    ]  # fmt: skip
    sql_events = [e for e in res.trace if e["type"] == "sql"]
    assert "no such column" in sql_events[0]["error"]
    assert sql_events[1]["preview"] == [["17"]]
    first_user = model.calls[0]["messages"][1]["content"]
    assert "Hint: N is the north region" in first_user
    assert "TABLE sales" in first_user and "examples:" in first_user  # schema up front
    # every tool call got a tool reply before the next model call
    second_call = model.calls[1]["messages"]
    assert second_call[-1]["role"] == "tool" and second_call[-1]["tool_call_id"] == "call_0"
    assert res.cost_usd > 0


def test_last_step_forces_submit(db: Database) -> None:
    model = FakeModel(
        [
            turn(("run_sql", {"query": FINAL})),
            turn(("submit_answer", {"sql": FINAL, "answer": "17"})),
        ]
    )
    res = Agent(model, AgentConfig(max_steps=2)).run(db, "q")
    assert model.calls[0]["tool_choice"] == "auto"  # plan turn
    assert model.calls[1]["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_answer"},
    }
    assert res.stop_reason == "forced_submit"


def test_cost_cap_falls_back_to_last_successful_query(db: Database) -> None:
    model = FakeModel(
        [
            turn(("run_sql", {"query": FINAL}), cost_tokens=200_000),  # $0.08 > cap
            turn(("submit_answer", {"sql": "never", "answer": ""})),
        ]
    )
    res = Agent(model, AgentConfig(cost_cap_usd=0.05)).run(db, "q")
    assert res.stop_reason == "cost_cap"
    assert res.final_sql == FINAL
    assert res.trace[-1]["type"] == "fallback"
    assert len(model.calls) == 1


def test_write_attempt_is_refused_in_trace(db: Database) -> None:
    model = FakeModel(
        [
            turn(("run_sql", {"query": "DROP TABLE sales"})),
            turn(("submit_answer", {"sql": FINAL, "answer": "17"})),
        ]
    )
    res = Agent(model).run(db, "q")
    refused = next(e for e in res.trace if e["type"] == "sql")
    assert refused["error"].startswith("refused")
    assert db.run_sql("SELECT COUNT(*) FROM sales").result.rows == [(3,)]  # type: ignore[union-attr]


def test_model_error_is_recorded(db: Database) -> None:
    class Broken(FakeModel):
        def chat(self, *a: Any, **k: Any) -> ChatTurn:
            raise RuntimeError("API down")

    res = Agent(Broken([])).run(db, "q")
    assert res.stop_reason == "model_error"
    assert res.final_sql == ""
    assert any(e["type"] == "error" for e in res.trace)


def test_failed_submission_is_sent_back_then_fixed(db: Database) -> None:
    model = FakeModel(
        [
            turn(("submit_answer", {"sql": "SELECT SUM(amt) FROM sales", "answer": "?"})),
            turn(("submit_answer", {"sql": FINAL, "answer": "17"})),
        ]
    )
    res = Agent(model).run(db, "q")
    assert res.stop_reason == "submitted"
    assert res.final_sql == FINAL
    assert res.corrections == 1
    rejection = json.loads(model.calls[1]["messages"][-1]["content"])
    assert rejection["status"] == "rejected" and "no such column" in rejection["reason"]


def test_empty_result_is_questioned_once(db: Database) -> None:
    empty = "SELECT amount FROM sales WHERE region = 'X'"
    model = FakeModel(
        [
            turn(("submit_answer", {"sql": empty, "answer": "none"})),
            turn(("submit_answer", {"sql": empty, "answer": "none"})),
        ]
    )
    res = Agent(model).run(db, "q")
    assert res.final_sql == empty
    assert res.corrections == 1
    assert res.steps == 2


def test_without_self_correction_any_submission_is_accepted(db: Database) -> None:
    bad = "SELECT SUM(amt) FROM sales"
    model = FakeModel([turn(("submit_answer", {"sql": bad, "answer": "?"}))])
    res = Agent(model, AgentConfig(self_correct=False, schema_up_front=False)).run(db, "q")
    assert res.final_sql == bad
    assert res.corrections == 0
    assert "TABLE" not in model.calls[0]["messages"][1]["content"]


def test_column_values_tool(db: Database) -> None:
    model = FakeModel(
        [
            turn(("column_values", {"table": "sales", "column": "region", "contains": ""})),
            turn(("submit_answer", {"sql": FINAL, "answer": "17"})),
        ]
    )
    Agent(model).run(db, "q")
    out = json.loads(model.calls[1]["messages"][-1]["content"])
    assert out["distinct_values"] == 2
    assert out["top_values"][0] == {"value": "N", "rows": 2}


def test_reviewer_issue_is_sent_back_once_then_fixed(db: Database) -> None:
    extra = "SELECT region, SUM(amount) FROM sales WHERE region = 'N'"
    model = FakeModel(
        [
            turn(("submit_answer", {"sql": extra, "answer": "17"})),
            turn(("submit_answer", {"sql": FINAL, "answer": "17"})),
        ],
        reviews=[review("fix", "The question asks only for the total; drop the region column.")],
    )
    res = Agent(model).run(db, "Total sales in N?")
    assert res.final_sql == FINAL
    assert res.corrections == 1
    assert res.reviews == 1  # reviewed once only; the fixed query is accepted without review
    sent_back = json.loads(model.calls[1]["messages"][-1]["content"])
    assert sent_back["status"] == "rejected" and "drop the region column" in sent_back["reason"]
    reviewer_input = model.review_calls[0]["messages"][1]["content"]
    assert "Submitted SQL:" in reviewer_input and '"row_count": 1' in reviewer_input
    assert [e["verdict"] for e in res.trace if e["type"] == "review"] == ["fix"]
    assert res.steps == 2  # reviewer calls are not agent steps


def test_reviewer_ok_accepts_and_fix_verdict_needs_issues(db: Database) -> None:
    model = FakeModel([turn(("submit_answer", {"sql": FINAL, "answer": "17"}))])
    res = Agent(model).run(db, "q")
    assert res.stop_reason == "submitted" and res.reviews == 1 and res.corrections == 0


def test_review_can_be_switched_off(db: Database) -> None:
    model = FakeModel([turn(("submit_answer", {"sql": FINAL, "answer": "17"}))])
    res = Agent(model, AgentConfig(review=False)).run(db, "q")
    assert res.reviews == 0 and model.review_calls == []
