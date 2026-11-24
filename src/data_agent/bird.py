"""BIRD Mini-Dev (SQLite) questions and databases."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from data_agent import paths


@dataclass(frozen=True)
class Question:
    question_id: int
    db_id: str
    question: str
    evidence: str
    gold_sql: str
    difficulty: str


def load_questions(path: Path = paths.BIRD_QUESTIONS) -> dict[int, Question]:
    """Load Mini-Dev questions keyed by question_id."""
    questions: dict[int, Question] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            q = Question(
                question_id=int(row["question_id"]),
                db_id=str(row["db_id"]),
                question=str(row["question"]),
                evidence=str(row.get("evidence") or ""),
                gold_sql=str(row["SQL"]),
                difficulty=str(row["difficulty"]),
            )
            if q.question_id in questions:
                raise ValueError(f"duplicate question_id {q.question_id} in {path}")
            questions[q.question_id] = q
    return questions


def load_baseline_prompts(path: Path = paths.BIRD_BASELINE_PROMPTS) -> dict[int, str]:
    """Load BIRD's official fixed baseline prompt for each question_id."""
    prompts: dict[int, str] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                prompts[int(row["question_id"])] = str(row["prompt"])
    return prompts


def db_path(db_id: str, root: Path = paths.BIRD_DATABASES) -> Path:
    """Path of the SQLite file for a BIRD database id."""
    return root / db_id / f"{db_id}.sqlite"
