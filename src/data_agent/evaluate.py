"""Run a text-to-SQL system on a split and score it with execution accuracy."""

from __future__ import annotations

import json
import statistics
import traceback
from collections import defaultdict
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from data_agent.bird import Question, db_path
from data_agent.metrics import execution_match, percentile
from data_agent.sql_exec import execute_readonly

SQL_TIMEOUT_S = 30.0  # budget for predicted SQL, same as BIRD's official evaluator
# Gold SQL is the reference, not under test: give it room (q40 takes ~25 s on its own and can
# exceed 30 s when other queries run in parallel).
GOLD_TIMEOUT_S = 300.0


@dataclass
class Prediction:
    """What a system returns for one question."""

    sql: str
    raw_response: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    steps: int = 1
    served_model: str = ""
    system_error: str | None = None  # the system itself failed (API error, crash...)
    stop_reason: str = ""  # agent only: submitted, forced_submit, cost_cap, ...
    guard: str = ""  # agent only: grounding guard outcome (pass, blocked, ...)
    trace: list[dict[str, Any]] | None = None  # agent only: every step, written to traces.jsonl


@dataclass
class Record:
    question_id: int
    db_id: str
    difficulty: str
    question: str
    gold_sql: str
    pred_sql: str
    correct: bool
    failure: str | None  # None when correct; otherwise a short category
    pred_error: str | None
    gold_error: str | None
    pred_row_count: int | None
    gold_row_count: int | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_s: float
    steps: int
    served_model: str
    raw_response: str
    stop_reason: str = ""
    guard: str = ""
    trace: list[dict[str, Any]] | None = None


System = Callable[[Question], Prediction]


def score(question: Question, pred: Prediction, db_root: Path | None = None) -> Record:
    db = db_path(question.db_id) if db_root is None else db_path(question.db_id, db_root)
    gold = execute_readonly(db, question.gold_sql, GOLD_TIMEOUT_S)
    pred_res = None
    failure: str | None
    if pred.system_error is not None:
        failure = "system_error"
    elif not pred.sql.strip():
        failure = "no_sql"
    else:
        pred_res = execute_readonly(db, pred.sql, SQL_TIMEOUT_S)
        if not gold.ok:
            failure = "gold_error"  # benchmark problem, counted as wrong and reported
        elif not pred_res.ok:
            failure = "timeout" if (pred_res.error or "").startswith("timeout") else "sql_error"
        elif execution_match(pred_res.rows or [], gold.rows or []):
            failure = None
        else:
            failure = "wrong_result"
    return Record(
        question_id=question.question_id,
        db_id=question.db_id,
        difficulty=question.difficulty,
        question=question.question,
        gold_sql=question.gold_sql,
        pred_sql=pred.sql,
        correct=failure is None,
        failure=failure,
        pred_error=pred.system_error or (pred_res.error if pred_res else None),
        gold_error=gold.error,
        pred_row_count=len(pred_res.rows) if pred_res and pred_res.rows is not None else None,
        gold_row_count=len(gold.rows) if gold.rows is not None else None,
        input_tokens=pred.input_tokens,
        output_tokens=pred.output_tokens,
        cost_usd=pred.cost_usd,
        latency_s=pred.latency_s,
        steps=pred.steps,
        served_model=pred.served_model,
        raw_response=pred.raw_response,
        stop_reason=pred.stop_reason,
        guard=pred.guard,
        trace=pred.trace,
    )


def _safe_call(system: System, q: Question) -> Prediction:
    try:
        return system(q)
    except Exception as exc:
        return Prediction(
            sql="", system_error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}"
        )


def run(
    system: System,
    questions: Iterable[Question],
    workers: int = 8,
    db_root: Path | None = None,
    on_record: Callable[[Record], None] | None = None,
) -> list[Record]:
    qs = list(questions)

    def one(q: Question) -> Record:
        rec = score(q, _safe_call(system, q), db_root)
        if on_record is not None:
            on_record(rec)
        return rec

    with ThreadPoolExecutor(max_workers=workers) as pool:
        records = list(pool.map(one, qs))
    return sorted(records, key=lambda r: r.question_id)


def _rate(records: list[Record]) -> dict[str, Any]:
    n = len(records)
    k = sum(r.correct for r in records)
    return {"n": n, "correct": k, "ex": round(100 * k / n, 2) if n else None}


def summarize(records: list[Record]) -> dict[str, Any]:
    by_db: dict[str, list[Record]] = defaultdict(list)
    by_diff: dict[str, list[Record]] = defaultdict(list)
    failures: dict[str, int] = defaultdict(int)
    stops: dict[str, int] = defaultdict(int)
    guards: dict[str, int] = defaultdict(int)
    for r in records:
        by_db[r.db_id].append(r)
        by_diff[r.difficulty].append(r)
        if r.failure:
            failures[r.failure] += 1
        if r.stop_reason:
            stops[r.stop_reason] += 1
        if r.guard:
            guards[r.guard] += 1
    latencies = [r.latency_s for r in records if r.failure != "system_error"]
    total_cost = sum(r.cost_usd for r in records)
    return {
        "overall": _rate(records),
        "by_difficulty": {k: _rate(v) for k, v in sorted(by_diff.items())},
        "by_db": {k: _rate(v) for k, v in sorted(by_db.items())},
        "failures": dict(sorted(failures.items())),
        "cost_usd_total": round(total_cost, 4),
        "cost_usd_per_question": round(total_cost / len(records), 5) if records else None,
        "latency_s_p50": round(percentile(latencies, 50), 2) if latencies else None,
        "latency_s_p95": round(percentile(latencies, 95), 2) if latencies else None,
        "avg_steps": round(statistics.fmean(r.steps for r in records), 2) if records else None,
        "max_steps": max((r.steps for r in records), default=None),
        "stop_reasons": dict(sorted(stops.items())),
        "grounding_guard": dict(sorted(guards.items())),
        "input_tokens_total": sum(r.input_tokens for r in records),
        "output_tokens_total": sum(r.output_tokens for r in records),
        "served_models": sorted({r.served_model for r in records if r.served_model}),
    }


def write_records(records: list[Record], path: Path) -> None:
    """Write predictions; agent traces go to a sibling traces.jsonl (one line per question)."""
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for r in records:
            row = asdict(r)
            row.pop("trace")
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    traces = [(r.question_id, r.trace) for r in records if r.trace is not None]
    if traces:
        with path.with_name("traces.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
            for qid, trace in traces:
                line = {"question_id": qid, "trace": trace}
                fh.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
