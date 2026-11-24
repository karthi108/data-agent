"""Create the frozen dev / held-out / reserve split of BIRD Mini-Dev.

Run once, commit the output, and never regenerate it. The script refuses to overwrite an
existing split file.
"""

from __future__ import annotations

import json
import sys
from collections import Counter

from data_agent import paths
from data_agent.bird import load_questions
from data_agent.splits import SPLIT_NAMES, file_sha256, make_split, validate_split

SEED = 20261124


def main() -> None:
    if paths.SPLIT_FILE.exists():
        sys.exit(f"{paths.SPLIT_FILE} already exists. The split is frozen; do not regenerate it.")
    questions = load_questions()
    split = make_split(questions, SEED)
    validate_split(split)

    stats: dict[str, object] = {}
    for name in SPLIT_NAMES:
        ids = split.ids(name)
        stats[name] = {
            "n": len(ids),
            "by_difficulty": dict(sorted(Counter(questions[i].difficulty for i in ids).items())),
            "by_db": dict(sorted(Counter(questions[i].db_id for i in ids).items())),
        }
    doc: dict[str, object] = {
        "description": (
            "Frozen split of BIRD Mini-Dev (SQLite, 500 questions). dev: improve against it. "
            "heldout: never tuned against, each system is run on it once and that run is quoted. "
            "reserve: untouched, kept for later."
        ),
        "source_file": paths.BIRD_QUESTIONS.name,
        "source_sha256": file_sha256(paths.BIRD_QUESTIONS),
        "seed": SEED,
        "method": "stratified by (db_id, difficulty), shuffled within strata, dealt 3:3:4",
        "stats": stats,
        **{name: list(split.ids(name)) for name in SPLIT_NAMES},
    }
    paths.SPLIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    paths.SPLIT_FILE.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(stats, indent=1))
    print(f"wrote {paths.SPLIT_FILE}  sha256={file_sha256(paths.SPLIT_FILE)}")


if __name__ == "__main__":
    main()
