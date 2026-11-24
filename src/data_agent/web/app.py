"""FastAPI app: one page plus a JSON API around the agent.

Run locally:
  free scripted preview:  DATA_AGENT_DEMO=1 uv run uvicorn data_agent.web.app:app
  real agent (paid):      uv run uvicorn data_agent.web.app:app   (needs OPENAI_API_KEY)

Settings (environment): DATA_AGENT_DB (SQLite file, default BIRD student_club),
DATA_AGENT_DAILY_BUDGET_USD (default 1.00), DATA_AGENT_HOURLY_LIMIT (questions per client
per hour, default 20), DATA_AGENT_DEMO=1 (scripted model, no API calls).
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from data_agent import paths
from data_agent.agent import Agent, AgentConfig
from data_agent.bird import db_path
from data_agent.llm import ChatModel
from data_agent.tools import MAX_ROWS_SHOWN, Database, _short
from data_agent.web.budget import DailyBudget
from data_agent.web.chart import build_chart

STATIC = Path(__file__).parent / "static"

EXAMPLES: list[dict[str, str]] = [
    {"label": "Spending by category", "question": "How much did we spend in each budget category?"},
    {"label": "Events per month", "question": "How many events did we hold each month?"},
    {"label": "Biggest majors", "question": "Which five majors have the most club members?"},
    {"label": "Ambiguous: biggest expense", "question": "What was our biggest expense?"},
    {
        "label": "Needs a fix: Kickoff costs",
        "question": "What did we spend in total on expenses for the Yearly Kickoff event?",
    },
]


class Clarification(BaseModel):
    asked: str = Field(max_length=500)
    answered: str = Field(min_length=1, max_length=300)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    clarifications: list[Clarification] = Field(default_factory=list, max_length=1)


class RateLimiter:
    """At most ``limit`` requests per client per rolling hour (in memory)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client: str) -> bool:
        now = time.time()
        with self._lock:
            hits = self._hits[client]
            while hits and now - hits[0] > 3600:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


def create_app(
    model_factory: Callable[[], ChatModel],
    db_file: Path,
    budget: DailyBudget,
    demo: bool = False,
    hourly_limit: int = 20,
) -> FastAPI:
    app = FastAPI(title="Data Agent", docs_url=None, redoc_url=None)
    config = AgentConfig(clarify="ask")
    limiter = RateLimiter(hourly_limit)
    model = model_factory()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return {
            "examples": EXAMPLES,
            "demo": demo,
            "database": db_file.stem,
            "tables": Database(db_file).tables(),
            "budget_left_usd": round(budget.remaining(), 4),
            "max_cost_per_question_usd": config.cost_cap_usd,
        }

    @app.post("/api/ask")
    def ask(req: AskRequest, request: Request) -> dict[str, Any]:
        client = request.client.host if request.client else "unknown"
        if not limiter.allow(client):
            raise HTTPException(429, "Too many questions from you this hour. Try again later.")
        # Worst case for one question is the per-question cap (plus a small margin for the
        # reviewer/guard turns that can land just after the cap check).
        if not demo and not budget.can_spend(config.cost_cap_usd * 1.2):
            return {
                "status": "budget",
                "message": "The daily budget for this demo is used up. Come back tomorrow (UTC).",
            }
        db = Database(db_file)
        clar = [(c.asked, c.answered) for c in req.clarifications]
        try:
            res = Agent(model, config).run(db, req.question, clarifications=clar)
        except Exception as exc:  # never leak internals to the page
            raise HTTPException(500, "The agent failed on this question.") from exc
        budget.add(res.cost_usd)
        base: dict[str, Any] = {
            "trace": res.trace,
            "steps": res.steps,
            "cost_usd": round(res.cost_usd, 5),
            "latency_s": round(res.latency_s, 2),
            "budget_left_usd": round(budget.remaining(), 4),
        }
        if res.stop_reason == "asked_user":
            return {
                **base,
                "status": "clarify",
                "clarification": {
                    "question": res.clarification_question,
                    "options": res.clarification_options,
                },
            }
        result: dict[str, Any] | None = None
        chart = None
        if res.final_sql:
            outcome = db.run_sql(res.final_sql)  # local, free: the rows behind the answer
            r = outcome.result
            if outcome.error is None and r is not None and r.rows is not None:
                result = {
                    "columns": r.columns,
                    "rows": [[_short(v) for v in row] for row in r.rows[:MAX_ROWS_SHOWN]],
                    "row_count": len(r.rows),
                    "truncated": outcome.truncated or len(r.rows) > MAX_ROWS_SHOWN,
                }
                chart = build_chart(r.columns or [], r.rows)
        return {
            **base,
            "status": "answered",
            "answer": res.answer or "No answer was produced.",
            "sql": res.final_sql,
            "result": result,
            "chart": chart,
            "guard": res.guard,
            "stop_reason": res.stop_reason,
        }

    return app


def app_from_env() -> FastAPI:
    demo = os.environ.get("DATA_AGENT_DEMO") == "1"
    db_file = Path(os.environ.get("DATA_AGENT_DB", str(db_path("student_club"))))
    if not db_file.is_file():
        raise RuntimeError(f"database not found: {db_file} (run scripts/fetch_bird.py)")
    budget = DailyBudget(
        float(os.environ.get("DATA_AGENT_DAILY_BUDGET_USD", "1.0")),
        paths.REPO_ROOT / ".runtime" / "budget.json",
    )
    limit = int(os.environ.get("DATA_AGENT_HOURLY_LIMIT", "20"))
    factory: Callable[[], ChatModel]
    if demo:
        from data_agent.web.demo import DemoModel

        factory = DemoModel
    else:
        from data_agent.llm import OpenAIChat

        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set (or use DATA_AGENT_DEMO=1).")
        factory = OpenAIChat
    return create_app(factory, db_file, budget, demo=demo, hourly_limit=limit)


def __getattr__(name: str) -> Any:
    # `uvicorn data_agent.web.app:app` builds the app from the environment on first access,
    # so importing this module (tests, tooling) never needs a key or a database.
    if name == "app":
        return app_from_env()
    raise AttributeError(name)
