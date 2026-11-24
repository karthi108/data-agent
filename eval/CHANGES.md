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

## 2026-11-24: single-shot baseline, dev split

- System: one `gpt-4.1-mini` call per question (served snapshot `gpt-4.1-mini-2025-04-14`,
  temperature 0) with BIRD's official fixed baseline prompt (schema with example values, question,
  evidence). No loop, no retry, no self-correction.
- Script: `scripts/run_baseline.py --split dev --run-name single_shot_gpt41mini`; failure
  breakdown: `scripts/analyze_run.py`. Raw results:
  [`results/dev/single_shot_gpt41mini/`](results/dev/single_shot_gpt41mini/).
- **Dev execution accuracy: 44.0% (66/150)**, strict BIRD EX.
  - by difficulty: challenging 42.4% (59/139), moderate 60.0% (6/10), simple 1/1.
  - by database: weakest california_schools 2/15, thrombosis_prediction 6/26, superhero 4/15;
    strongest student_club 9/11, codebase_community 6/9, formula_1 11/17.
- Cost $0.297 total ($0.0020 per question); latency p50 4.9 s, p95 12.6 s; 1 step per question.
- Why the 84 failures failed:
  - 53 wrong values (query runs and has the right shape, but the logic is wrong: filters,
    joins, duplicates counted, wrong denominator. Examples: q68 counts distinct printings
    (`id`, 630) where distinct cards (`name`, 275) were asked; q79 joins sets to their
    translations, so each set's size is summed once per language: 7930 vs 793);
  - 23 wrong row count (filter too loose or too strict);
  - 4 SQL errors (q13 unquoted column with a space; q412, q417, q462 invented column names);
  - 2 wrong columns, 1 timeout (q185, over 30 s);
  - 1 float precision only (q0: same value to 15 digits, counted wrong under strict EX as in BIRD).
- These are what the agent loop should fix: 5 execution errors are exactly what self-correction
  targets; the 76 logic and row-count errors need schema exploration and result checking.
- Also kept: a 3-question smoke test of the pipeline run just before
  ([`results/dev/single_shot_gpt41mini__smoke3/`](results/dev/single_shot_gpt41mini__smoke3/)).
  Its untracked folder is why the full run's summary records `git.dirty: true`; the code was at
  the committed state.
