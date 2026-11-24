"""Read-only SQLite execution with a wall-clock timeout.

The database is opened with ``mode=ro`` so no statement can modify it, whatever the SQL says.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Row = tuple[Any, ...]


@dataclass(frozen=True)
class ExecResult:
    rows: list[Row] | None
    error: str | None
    elapsed_s: float

    @property
    def ok(self) -> bool:
        return self.error is None


def connect_readonly(db_file: Path) -> sqlite3.Connection:
    if not db_file.is_file():
        raise FileNotFoundError(db_file)
    uri = f"{db_file.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    # BIRD databases contain some non-UTF-8 bytes; keep them rather than crash.
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    return conn


def execute_readonly(
    db_file: Path,
    sql: str,
    timeout_s: float = 30.0,
    max_rows: int | None = None,
) -> ExecResult:
    """Run ``sql`` on a read-only connection, aborting after ``timeout_s`` seconds."""
    start = time.perf_counter()
    deadline = start + timeout_s
    try:
        conn = connect_readonly(db_file)
    except (sqlite3.Error, FileNotFoundError) as exc:
        return ExecResult(None, f"connect: {exc}", time.perf_counter() - start)
    try:
        # Returning non-zero from the handler interrupts the running statement.
        conn.set_progress_handler(lambda: int(time.perf_counter() > deadline), 10_000)
        cur = conn.execute(sql)
        rows = cur.fetchall() if max_rows is None else cur.fetchmany(max_rows)
        return ExecResult([tuple(r) for r in rows], None, time.perf_counter() - start)
    except sqlite3.Error as exc:
        elapsed = time.perf_counter() - start
        if time.perf_counter() > deadline:
            return ExecResult(None, f"timeout after {timeout_s:.0f}s", elapsed)
        return ExecResult(None, f"{type(exc).__name__}: {exc}", elapsed)
    except Exception as exc:  # e.g. multiple statements, encoding issues
        return ExecResult(None, f"{type(exc).__name__}: {exc}", time.perf_counter() - start)
    finally:
        conn.close()
