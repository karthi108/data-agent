# Evaluation log

Every run, good or bad, in order. Each entry names the script, the split, the raw results folder
and what changed since the previous run. Failures are explained, not hidden.

## 2026-11-24: data and split (no model run)

- Data: BIRD Mini-Dev SQLite, annotations from Hugging Face `birdsql/bird_mini_dev` at commit
  `5b256b5a` ("Mini-Dev 1024", 2026 update); sha256 matches BIRD's own manifest. Databases from
  the official Mini-Dev package. All hashes: [`data_manifest.json`](data_manifest.json).
- Gold check (`scripts/check_gold.py`): 500/500 gold queries run and return rows on these
  databases ([`gold_check.json`](gold_check.json)), matching BIRD's manifest. Question 40's gold
  query takes ~25 s alone and exceeded 30 s under parallel load, so gold SQL gets a 300 s budget;
  predicted SQL keeps BIRD's 30 s.
- Split (`scripts/make_split.py`, seed 20261124, stratified by database and difficulty):
  150 dev / 150 held-out / 200 reserve, in
  [`splits/bird_minidev_split_v1.json`](splits/bird_minidev_split_v1.json). Committed before any
  model run; `tests/test_splits.py` pins its hash.
- Note: the 2026 annotations are mostly "challenging" (460/500), so scores are not comparable to
  numbers published on the 2024 Mini-Dev release.
