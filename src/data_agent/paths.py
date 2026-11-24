"""Filesystem locations shared by scripts and tests."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
BIRD_DIR = DATA_DIR / "bird"
BIRD_QUESTIONS = BIRD_DIR / "mini_dev_sqlite.jsonl"
BIRD_BASELINE_PROMPTS = BIRD_DIR / "baseline_mini_dev_sqlite.jsonl"
BIRD_DATABASES = BIRD_DIR / "dev_databases"

EVAL_DIR = REPO_ROOT / "eval"
SPLIT_FILE = EVAL_DIR / "splits" / "bird_minidev_split_v1.json"
RESULTS_DIR = EVAL_DIR / "results"
