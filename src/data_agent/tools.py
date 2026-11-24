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
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from data_agent.grounding import observed_numbers
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


VALUES_SHOWN = 25  # column_values: most frequent values returned
EXAMPLE_VALUES = 3  # schema overview: example values per column
EXAMPLE_TIMEOUT_S = 2.0
_SCHEMA_CACHE: dict[str, str] = {}
_INFO_CACHE: dict[str, list[dict[str, Any]]] = {}
_SCHEMA_LOCK = threading.Lock()


def _deadline(conn: sqlite3.Connection, seconds: float) -> None:
    end = time.perf_counter() + seconds
    conn.set_progress_handler(lambda: int(time.perf_counter() > end), 10_000)


def _examples(conn: sqlite3.Connection, table: str, column: str) -> str:
    _deadline(conn, EXAMPLE_TIMEOUT_S)
    try:
        rows = conn.execute(
            f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL '
            f"LIMIT {EXAMPLE_VALUES}"
        ).fetchall()
    except sqlite3.Error:
        return "[?]"
    finally:
        conn.set_progress_handler(None, 0)
    vals = []
    for (v,) in rows:
        if isinstance(v, str):
            v = v if len(v) <= 40 else v[:40] + "..."
        elif isinstance(v, bytes):
            v = f"<{len(v)} bytes>"
        vals.append(v)
    return json.dumps(vals, ensure_ascii=False, default=str)


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
        # Every number seen in a successful query result (cells and row counts): the evidence
        # the grounding guard checks answers against. One Database per question.
        self.observed: set[float] = set()

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

    def _resolve_table(self, name: str) -> str | None:
        # Names are matched against the catalogue, never interpolated from free text.
        wanted = name.strip().strip('"`[]').lower()
        return next((t for t in self.tables() if t.lower() == wanted), None)

    def _columns(self, table: str) -> list[tuple[Any, ...]]:
        conn = self._conn()
        try:
            return list(conn.execute(f'PRAGMA table_info("{table}")').fetchall())
        finally:
            conn.close()

    def schema_overview(self) -> str:
        """Compact schema: every table and column with type, keys and up to 3 example values.

        Computed once per database file and cached (it is the same for every question).
        """
        key = str(self.path.resolve())
        with _SCHEMA_LOCK:
            if key not in _SCHEMA_CACHE:
                _SCHEMA_CACHE[key] = self._build_overview()
            return _SCHEMA_CACHE[key]

    def schema_info(self) -> list[dict[str, Any]]:
        """The same schema as ``schema_overview`` in structured form (for the web Data view)."""
        key = str(self.path.resolve())
        with _SCHEMA_LOCK:
            if key not in _INFO_CACHE:
                _INFO_CACHE[key] = self._build_info()
            return _INFO_CACHE[key]

    def _build_info(self) -> list[dict[str, Any]]:
        conn = self._conn()
        out: list[dict[str, Any]] = []
        try:
            for table in self.tables():
                cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                fks = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
                fk_by_col = {str(f[3]): f"{f[2]}.{f[4]}" for f in fks}
                columns = []
                for c in cols:
                    name = str(c[1])
                    raw = _examples(conn, table, name)
                    examples = json.loads(raw) if raw != "[?]" else []
                    columns.append(
                        {
                            "name": name,
                            "type": str(c[2]),
                            "primary_key": bool(c[5]),
                            "references": fk_by_col.get(name),
                            "examples": examples,
                        }
                    )
                out.append({"table": table, "columns": columns})
        finally:
            conn.close()
        return out

    def _build_overview(self) -> str:
        conn = self._conn()
        lines: list[str] = []
        try:
            for table in self.tables():
                cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                fks = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
                lines.append(f"TABLE {table}")
                for c in cols:
                    name, ctype, pk = str(c[1]), str(c[2]), bool(c[5])
                    examples = _examples(conn, table, name)
                    flags = " PRIMARY KEY" if pk else ""
                    lines.append(f"  {name} {ctype}{flags} -- examples: {examples}")
                for f in fks:
                    lines.append(f"  FOREIGN KEY ({f[3]}) REFERENCES {f[2]}({f[4]})")
        finally:
            conn.close()
        return "\n".join(lines)

    def column_values(self, table: str, column: str, contains: str = "") -> str:
        """Most frequent distinct values of a column (optionally those containing a substring)."""
        resolved = self._resolve_table(table)
        if resolved is None:
            return json.dumps({"error": f"no table named {table!r}", "tables": self.tables()})
        names = [str(c[1]) for c in self._columns(resolved)]
        col = next((n for n in names if n.lower() == column.strip().strip('"`[]').lower()), None)
        if col is None:
            return json.dumps({"error": f"no column {column!r} in {resolved}", "columns": names})
        where, params = "", tuple[str, ...]()
        if contains:
            where, params = f' WHERE CAST("{col}" AS TEXT) LIKE ?', (f"%{contains}%",)
        sql = (
            f'SELECT "{col}", COUNT(*) AS n FROM "{resolved}"{where} '
            f'GROUP BY "{col}" ORDER BY n DESC LIMIT {VALUES_SHOWN}'
        )
        count_sql = f'SELECT COUNT(DISTINCT "{col}") FROM "{resolved}"{where}'
        conn = self._conn()
        try:
            _deadline(conn, self.query_timeout_s)
            rows = conn.execute(sql, params).fetchall()
            distinct = conn.execute(count_sql, params).fetchone()[0]
        except sqlite3.Error as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
        finally:
            conn.close()
        return json.dumps(
            {
                "table": resolved,
                "column": col,
                "filter": contains or None,
                "distinct_values": distinct,
                "top_values": [{"value": _short(v), "rows": n} for v, n in rows],
            },
            ensure_ascii=False,
            default=str,
        )

    def describe_table(self, name: str) -> str:
        table = self._resolve_table(name)
        if table is None:
            return json.dumps({"error": f"no table named {name!r}", "tables": self.tables()})
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
        rows = res.rows or []
        self.observed |= observed_numbers(v for row in rows for v in row)
        self.observed.add(float(len(rows)))
        return QueryOutcome(sql, res, None, truncated)
