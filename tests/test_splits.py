"""The committed split must never change. These tests run in CI without the BIRD data."""

from __future__ import annotations

import pytest

from data_agent import paths
from data_agent.bird import Question
from data_agent.splits import Split, load_split, make_split, split_ids_sha256, validate_split

# Pinned when the split was frozen (before any model run). If this test fails, the split was
# edited: revert it. Never update this constant.
FROZEN_SPLIT_IDS_SHA256 = "57a91fd7c896500e9b46c531056dad163e86d808a5434d3cc44c765cdc22aa47"


def test_committed_split_is_frozen() -> None:
    split = load_split(paths.SPLIT_FILE)
    assert split_ids_sha256(split) == FROZEN_SPLIT_IDS_SHA256


def test_committed_split_sizes_and_coverage() -> None:
    split = load_split(paths.SPLIT_FILE)
    assert (len(split.dev), len(split.heldout), len(split.reserve)) == (150, 150, 200)
    assert sorted(split.dev + split.heldout + split.reserve) == list(range(500))


def _fake_questions(n: int) -> dict[int, Question]:
    dbs = ["a", "b", "c"]
    diffs = ["simple", "moderate", "challenging"]
    return {
        i: Question(i, dbs[i % 3], f"q{i}", "", "SELECT 1", diffs[(i // 3) % 3]) for i in range(n)
    }


def test_make_split_is_deterministic_and_disjoint() -> None:
    qs = _fake_questions(100)
    s1, s2 = make_split(qs, seed=1), make_split(qs, seed=1)
    assert s1 == s2
    validate_split(s1)
    assert len(s1.dev) == len(s1.heldout) == 30
    assert len(s1.reserve) == 40
    assert make_split(qs, seed=2) != s1


def test_validate_split_rejects_overlap() -> None:
    with pytest.raises(ValueError, match="overlap"):
        validate_split(Split(dev=(1, 2), heldout=(2, 3), reserve=()))
