from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import pytest

from data_agent.metrics import execution_match, percentile
from data_agent.sql_exec import execute_readonly


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
    conn.executemany("INSERT INTO t VALUES (?, ?)", [(1, "a"), (2, "b"), (3, "c")])
    conn.commit()
    conn.close()
    return path


def test_select_returns_rows(db: Path) -> None:
    res = execute_readonly(db, "SELECT id FROM t ORDER BY id")
    assert res.ok
    assert res.rows == [(1,), (2,), (3,)]


def test_writes_are_rejected(db: Path) -> None:
    for sql in ("DELETE FROM t", "DROP TABLE t", "INSERT INTO t VALUES (9, 'x')"):
        res = execute_readonly(db, sql)
        assert not res.ok, sql
    assert execute_readonly(db, "SELECT COUNT(*) FROM t").rows == [(3,)]


def test_syntax_error_is_reported(db: Path) -> None:
    res = execute_readonly(db, "SELEC id FROM t")
    assert not res.ok
    assert "syntax error" in (res.error or "")


def test_timeout(db: Path) -> None:
    slow = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"
    res = execute_readonly(db, slow, timeout_s=0.3)
    assert res.error is not None
    assert res.error.startswith("timeout")


def test_missing_db(tmp_path: Path) -> None:
    res = execute_readonly(tmp_path / "nope.sqlite", "SELECT 1")
    assert not res.ok


def test_execution_match_is_bird_strict() -> None:
    assert execution_match([(1, "a"), (2, "b")], [(2, "b"), (1, "a")])  # order ignored
    assert execution_match([(1,), (1,)], [(1,)])  # duplicates ignored, as in BIRD
    assert not execution_match([("a", 1)], [(1, "a")])  # column order matters
    assert not execution_match([(0.1 + 0.2,)], [(0.3,)])  # no float tolerance
    assert not execution_match([], [(1,)])


def test_percentile() -> None:
    assert percentile([5.0, 1.0, 3.0, 2.0, 4.0], 50) == 3.0
    assert percentile([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0], 95) == 10.0
    assert math.isnan(percentile([], 50))
