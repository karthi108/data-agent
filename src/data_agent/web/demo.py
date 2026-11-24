"""Scripted stand-in for the model, for previewing the web UI without API calls.

It is NOT an AI: it replays fixed tool calls for the five example questions (the real agent
loop, tools, guard and database still run). The page shows a "demo mode" banner whenever it is
used. Any other question gets a fixed "demo mode" reply.
"""

from __future__ import annotations

import json
from typing import Any

from data_agent.llm import ChatTurn, ToolCall, assistant_message

Step = tuple[str, list[tuple[str, dict[str, Any]]]]  # (text, tool calls)

SPEND = (
    "SELECT category, ROUND(SUM(spent), 2) AS total_spent FROM budget "
    "GROUP BY category ORDER BY total_spent DESC"
)
EVENTS = (
    "SELECT substr(event_date, 1, 7) AS month, COUNT(*) AS events FROM event "
    "GROUP BY month ORDER BY month"
)
MAJORS = (
    "SELECT m.major_name, COUNT(*) AS members FROM member AS mb "
    "JOIN major AS m ON mb.link_to_major = m.major_id "
    "GROUP BY m.major_name ORDER BY members DESC LIMIT 5"
)
TOP_EXPENSE = "SELECT expense_description, cost FROM expense ORDER BY cost DESC LIMIT 1"
TOP_CATEGORY = (
    "SELECT category, ROUND(SUM(spent), 2) AS total_spent FROM budget "
    "GROUP BY category ORDER BY total_spent DESC LIMIT 1"
)
KICKOFF_BAD = "SELECT SUM(cost) FROM expense WHERE event_name = 'Yearly Kickoff'"
KICKOFF = (
    "SELECT ROUND(SUM(x.cost), 2) AS total_cost FROM expense AS x "
    "JOIN budget AS b ON x.link_to_budget = b.budget_id "
    "JOIN event AS e ON b.link_to_event = e.event_id "
    "WHERE e.event_name = 'Yearly Kickoff'"
)

SCRIPTS: dict[str, list[Step]] = {
    "How much did we spend in each budget category?": [
        ("Plan: sum budget.spent per category, largest first.", [("run_sql", {"query": SPEND})]),
        (
            "",
            [
                (
                    "submit_answer",
                    {
                        "sql": SPEND,
                        "answer": (
                            "Food has the highest spending at 1234.73, "
                            "followed by Advertisement at 495.02."
                        ),
                    },
                )
            ],
        ),
    ],
    "How many events did we hold each month?": [
        ("Plan: count events per month of event_date.", [("run_sql", {"query": EVENTS})]),
        (
            "",
            [
                (
                    "submit_answer",
                    {"sql": EVENTS, "answer": ("The busiest month was 2019-09 with 8 events.")},
                )
            ],
        ),
    ],
    "Which five majors have the most club members?": [
        (
            "Plan: join member to major, count members per major, keep the top 5.",
            [("run_sql", {"query": MAJORS})],
        ),
        (
            "",
            [
                (
                    "submit_answer",
                    {
                        "sql": MAJORS,
                        "answer": ("Human Development and Family Studies leads with 3 members."),
                    },
                )
            ],
        ),
    ],
    "What was our biggest expense?": [
        (
            "",
            [
                (
                    "ask_user",
                    {
                        "question": "Do you mean the single largest expense, "
                        "or the budget category we spent the most on?",
                        "options": [
                            "The single largest expense",
                            "The category with the most spending",
                        ],
                    },
                )
            ],
        ),
    ],
    "What did we spend in total on expenses for the Yearly Kickoff event?": [
        (
            "Plan: find the event, then sum its expenses.",
            [("column_values", {"table": "event", "column": "event_name", "contains": "Kickoff"})],
        ),
        ("", [("run_sql", {"query": KICKOFF_BAD})]),
        (
            "expense has no event_name: expenses link to a budget, which links to the event.",
            [("run_sql", {"query": KICKOFF})],
        ),
        (
            "",
            [
                (
                    "submit_answer",
                    {"sql": KICKOFF, "answer": ("Expenses for the Yearly Kickoff total 449.13.")},
                )
            ],
        ),
    ],
}

RESOLVED: dict[str, list[Step]] = {
    "category": [
        ("", [("run_sql", {"query": TOP_CATEGORY})]),
        (
            "",
            [
                (
                    "submit_answer",
                    {
                        "sql": TOP_CATEGORY,
                        "answer": ("Food is the category we spent the most on: 1234.73."),
                    },
                )
            ],
        ),
    ],
    "single": [
        ("", [("run_sql", {"query": TOP_EXPENSE})]),
        (
            "",
            [
                (
                    "submit_answer",
                    {
                        "sql": TOP_EXPENSE,
                        "answer": ("The single largest expense was Club shirts at 295.12."),
                    },
                )
            ],
        ),
    ],
}

UNKNOWN: list[Step] = [
    (
        "",
        [
            (
                "submit_answer",
                {
                    "sql": "SELECT 'demo mode' AS note",
                    "answer": "Demo mode only answers the example questions. "
                    "The live site uses the real agent.",
                },
            )
        ],
    ),
]


def _turn(text: str, calls: list[tuple[str, dict[str, Any]]], n: int) -> ChatTurn:
    tcs = [
        ToolCall(f"demo_{n}_{i}", name, json.dumps(args)) for i, (name, args) in enumerate(calls)
    ]
    return ChatTurn(text, tcs, assistant_message(text, tcs), 0, 0, 0.0, "gpt-4.1-mini", "demo")


class DemoModel:
    model = "gpt-4.1-mini"

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str | dict[str, Any] = "auto",
    ) -> ChatTurn:
        user = next(m["content"] for m in messages if m["role"] == "user")
        question = user.split("Question: ", 1)[-1].split("\n", 1)[0].strip()
        script = SCRIPTS.get(question, UNKNOWN)
        if "The user answered:" in user:
            answer = user.rsplit("The user answered:", 1)[1].lower()
            script = RESOLVED["category" if "categor" in answer else "single"]
        n = sum(1 for m in messages if m["role"] == "assistant")
        text, calls = script[min(n, len(script) - 1)]
        return _turn(text, calls, n)
