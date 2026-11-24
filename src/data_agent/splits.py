"""Dev / held-out / reserve split of BIRD Mini-Dev, and the rules that protect it.

Rules (from PLAN.md):
- The split is committed to git before any model run.
- The held-out set is never tuned against. It is run once per system; the first run is the
  quoted number. A held-out failure becomes a new *dev* question, never a targeted fix.
- The reserve set is not used for anything yet; it stays untouched.
"""

from __future__ import annotations

import hashlib
import json
import random
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from data_agent import paths
from data_agent.bird import Question

SplitName = Literal["dev", "heldout", "reserve"]
SPLIT_NAMES: tuple[SplitName, ...] = ("dev", "heldout", "reserve")

# Position inside every block of 10 consecutive (stratum-sorted) questions -> split.
# 3/10 dev, 3/10 held-out, 4/10 reserve => 150 / 150 / 200 of 500.
_PATTERN: tuple[SplitName, ...] = (
    "dev", "heldout", "reserve", "dev", "heldout",
    "reserve", "dev", "heldout", "reserve", "reserve",
)  # fmt: skip


@dataclass(frozen=True)
class Split:
    dev: tuple[int, ...]
    heldout: tuple[int, ...]
    reserve: tuple[int, ...]

    def ids(self, name: SplitName) -> tuple[int, ...]:
        return {"dev": self.dev, "heldout": self.heldout, "reserve": self.reserve}[name]


def make_split(questions: dict[int, Question], seed: int) -> Split:
    """Deterministic split stratified by (db_id, difficulty).

    Questions are grouped by stratum, shuffled inside each stratum with ``seed``, laid end to end,
    and dealt out with a fixed 10-slot pattern, so every database and difficulty level is spread
    across dev, held-out and reserve in roughly a 3:3:4 ratio.
    """
    rng = random.Random(seed)  # noqa: S311 - reproducible split, not crypto
    strata: dict[tuple[str, str], list[int]] = defaultdict(list)
    for qid in sorted(questions):
        q = questions[qid]
        strata[(q.db_id, q.difficulty)].append(qid)
    ordered: list[int] = []
    for key in sorted(strata):
        ids = strata[key][:]
        rng.shuffle(ids)
        ordered.extend(ids)
    buckets: dict[SplitName, list[int]] = {name: [] for name in SPLIT_NAMES}
    for i, qid in enumerate(ordered):
        buckets[_PATTERN[i % len(_PATTERN)]].append(qid)
    return Split(*(tuple(sorted(buckets[name])) for name in SPLIT_NAMES))


def load_split(path: Path = paths.SPLIT_FILE) -> Split:
    data = json.loads(path.read_text(encoding="utf-8"))
    split = Split(
        dev=tuple(data["dev"]), heldout=tuple(data["heldout"]), reserve=tuple(data["reserve"])
    )
    validate_split(split)
    return split


def validate_split(split: Split) -> None:
    sets = [set(split.ids(n)) for n in SPLIT_NAMES]
    for name, ids, s in zip(SPLIT_NAMES, (split.ids(n) for n in SPLIT_NAMES), sets, strict=True):
        if len(ids) != len(s):
            raise ValueError(f"duplicate ids in {name}")
    if sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2]:
        raise ValueError("splits overlap")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def split_is_committed(path: Path = paths.SPLIT_FILE, repo: Path = paths.REPO_ROOT) -> bool:
    """True if the split file is tracked by git and has no uncommitted changes."""

    def git(*args: str) -> int:
        return subprocess.run(  # noqa: S603 - fixed git arguments, no shell
            ["git", *args],  # noqa: S607
            cwd=repo,
            capture_output=True,
            check=False,
        ).returncode

    rel = path.resolve().relative_to(repo.resolve()).as_posix()
    return (
        git("ls-files", "--error-unmatch", rel) == 0
        and git("diff", "--quiet", "HEAD", "--", rel) == 0
    )


def split_ids_sha256(split: Split) -> str:
    """Hash of the id lists only (independent of formatting and line endings)."""
    canonical = json.dumps(
        {name: list(split.ids(name)) for name in SPLIT_NAMES},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
