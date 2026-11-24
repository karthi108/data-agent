from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from data_agent.baseline import extract_sql
from data_agent.bird import Question
from data_agent.evaluate import Prediction, run, score, summarize
from data_agent.llm import cost_usd


def test_extract_sql_takes_last_sql_block() -> None:
    text = "think...\n```sql\nSELECT 1\n```\nbetter:\n```sql\n-- final\nSELECT 2;\n```"
    assert extract_sql(text) == "-- final\nSELECT 2"


def test_extract_sql_fallbacks() -> None:
    assert extract_sql("```\nSELECT 3\n```") == "SELECT 3"
    assert extract_sql("no code here") == ""


def test_cost() -> None:
    assert cost_usd("gpt-4.1-mini", 1_000_000, 1_000_000) == pytest.approx(2.0)
    with pytest.raises(KeyError):
        cost_usd("unknown-model", 1, 1)


@pytest.fixture
def db_root(tmp_path: Path) -> Path:
    path = tmp_path / "shop" / "shop.sqlite"
    path.parent.mkdir()
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sales (region TEXT, amount INTEGER)")
    conn.executemany("INSERT INTO sales VALUES (?, ?)", [("N", 10), ("S", 5), ("N", 7)])
    conn.commit()
    conn.close()
    return tmp_path


Q = Question(
    7, "shop", "Total sales in N?", "", "SELECT SUM(amount) FROM sales WHERE region='N'", "simple"
)


def test_score_categories(db_root: Path) -> None:
    def cat(pred: Prediction) -> str | None:
        return score(Q, pred, db_root).failure

    assert cat(Prediction(sql="SELECT 17")) is None
    assert cat(Prediction(sql="SELECT SUM(amount) FROM sales")) == "wrong_result"
    assert cat(Prediction(sql="SELECT nope FROM sales")) == "sql_error"
    assert cat(Prediction(sql="")) == "no_sql"
    assert cat(Prediction(sql="", system_error="APIError")) == "system_error"


def test_run_counts_crashing_system_as_wrong(db_root: Path) -> None:
    def system(q: Question) -> Prediction:
        if q.question_id == 7:
            raise RuntimeError("boom")
        return Prediction(sql=q.gold_sql)

    q8 = Question(8, "shop", "?", "", "SELECT COUNT(*) FROM sales", "simple")
    records = run(system, [Q, q8], workers=2, db_root=db_root)
    assert [r.correct for r in records] == [False, True]
    assert records[0].failure == "system_error"
    summary = summarize(records)
    assert summary["overall"] == {"n": 2, "correct": 1, "ex": 50.0}
    assert summary["failures"] == {"system_error": 1}
