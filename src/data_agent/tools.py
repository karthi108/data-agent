"""The agent's only way to touch data: list_tables, describe_table, run_sql.

Safety has two independent layers:
1. ``check_select_only`` parses the SQL with sqlglot and accepts exactly one SELECT statement
   (CTEs and UNION/INTERSECT/EXCEPT allowed), with no DML, DDL, PRAGMA or ATTACH anywhere.
2. Every query runs on a connection opened with SQLite ``mode=ro``, so even a statement that
   slipped past the parser cannot write.
Plus a per-query timeout and a row cap.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from data_agent.sql_exec import ExecResult, connect_readonly, execute_readonly

QUERY_TIMEOUT_S = 5.0
MAX_ROWS = 1000  # rows fetched from SQLite
MAX_ROWS_SHOWN = 50  # rows shown to the model; the count tells it how many exist
MAX_VALUE_CHARS = 100
SAMPLE_ROWS = 3

_FORBIDDEN: tuple[type[exp.Expression], ...] = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Create, exp.Alter,
    exp.TruncateTable, exp.Command, exp.Pragma, exp.Attach, exp.Detach, exp.Transaction,
    exp.Commit, exp.Set, exp.Use, exp.Copy, exp.LoadData,
)  # fmt: skip


class UnsafeQueryError(ValueError):
    """The query is not a single read-only SELECT."""


def check_select_only(sql: str) -> None:
    """Raise UnsafeQueryError unless ``sql`` is exactly one read-only SELECT statement."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except SqlglotError as exc:  # ParseError, TokenError, ...: all mean "refuse"
        raise UnsafeQueryError(f"could not parse SQL: {str(exc).splitlines()[0]}") from exc
    if len(statements) != 1:
        raise UnsafeQueryError(f"exactly one statement allowed, got {len(statements)}")
    root = statements[0]
    if not isinstance(root, exp.Select | exp.SetOperation):
        raise UnsafeQueryError(f"only SELECT queries are allowed, got {root.key.upper()}")
    for node in root.walk():
        if isinstance(node, _FORBIDDEN):
            raise UnsafeQueryError(f"{node.key.upper()} is not allowed")


def _short(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
        return value[:MAX_VALUE_CHARS] + "...[truncated]"
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return value


@dataclass
class QueryOutcome:
    """What run_sql returns, kept whole for the trace and the evaluator."""

    sql: str
    result: ExecResult | None
    error: str | None
    truncated: bool = False

    def to_model(self) -> str:
        """JSON shown to the model. Data values are data, never instructions."""
        if self.error is not None or self.result is None or self.result.rows is None:
            return json.dumps({"error": self.error})
        rows = self.result.rows
        return json.dumps(
            {
                "columns": self.result.columns,
                "row_count": f"{len(rows)}+ (capped)" if self.truncated else len(rows),
                "rows": [[_short(v) for v in r] for r in rows[:MAX_ROWS_SHOWN]],
                "rows_shown": min(len(rows), MAX_ROWS_SHOWN),
            },
            ensure_ascii=False,
            default=str,
        )


class Database:
    def __init__(self, path: Path, query_timeout_s: float = QUERY_TIMEOUT_S) -> None:
        self.path = path
        self.query_timeout_s = query_timeout_s
        self._tables: list[str] | None = None

    def _conn(self) -> sqlite3.Connection:
        return connect_readonly(self.path)

    def tables(self) -> list[str]:
        if self._tables is None:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            finally:
                conn.close()
            self._tables = [str(r[0]) for r in rows]
        return self._tables

    def list_tables(self) -> str:
        conn = self._conn()
        try:
            out = []
            for t in self.tables():
                cols = conn.execute(f'PRAGMA table_info("{t}")').fetchall()
                out.append({"table": t, "columns": [str(c[1]) for c in cols]})
        finally:
            conn.close()
        return json.dumps(out, ensure_ascii=False)

    def describe_table(self, name: str) -> str:
        # The name must be an existing table: it is matched against the catalogue, never
        # interpolated from free text.
        matches = [t for t in self.tables() if t.lower() == name.strip().strip('"`[]').lower()]
        if not matches:
            return json.dumps({"error": f"no table named {name!r}", "tables": self.tables()})
        table = matches[0]
        conn = self._conn()
        try:
            cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            fks = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
            sample = conn.execute(f'SELECT * FROM "{table}" LIMIT {SAMPLE_ROWS}').fetchall()
        finally:
            conn.close()
        return json.dumps(
            {
                "table": table,
                "columns": [{"name": c[1], "type": c[2], "primary_key": bool(c[5])} for c in cols],
                "foreign_keys": [{"column": f[3], "references": f"{f[2]}.{f[4]}"} for f in fks],
                "sample_rows": [[_short(v) for v in r] for r in sample],
            },
            ensure_ascii=False,
            default=str,
        )

    def run_sql(self, sql: str) -> QueryOutcome:
        try:
            check_select_only(sql)
        except UnsafeQueryError as exc:
            return QueryOutcome(sql, None, f"refused: {exc}")
        res = execute_readonly(self.path, sql, self.query_timeout_s, max_rows=MAX_ROWS + 1)
        if not res.ok or res.rows is None:
            return QueryOutcome(sql, res, res.error)
        truncated = len(res.rows) > MAX_ROWS
        if truncated:
            res = ExecResult(res.rows[:MAX_ROWS], None, res.elapsed_s, res.columns)
        return QueryOutcome(sql, res, None, truncated)
