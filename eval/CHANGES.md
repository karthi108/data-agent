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

## 2026-11-24: agent v1, dev split (worse than the baseline)

- System: agent v1 (`src/data_agent/agent.py` at commit `fb6f703`): `gpt-4.1-mini`
  tool-calling loop with `list_tables`, `describe_table` (columns, foreign keys, 3 sample rows),
  read-only `run_sql` (sqlglot SELECT-only guard + `mode=ro`, 5 s timeout, 1,000-row cap) and
  `submit_answer`; max 8 model calls, $0.05 cost cap, submit forced on the last step. Gets the
  question and BIRD's evidence, but no schema up front. No explicit self-correction yet (it does
  see `run_sql` errors and can retry on its own).
- Script: `scripts/run_eval.py --system agent --split dev --run-name agent_v1_gpt41mini`.
  Raw results and per-question traces:
  [`results/dev/agent_v1_gpt41mini/`](results/dev/agent_v1_gpt41mini/).
- **Dev execution accuracy: 40.7% (61/150), 3.3 points below the single-shot baseline (44.0%).**
- Cost $0.89 total ($0.0060 per question, 3x the baseline); latency p50 7.9 s, p95 15.1 s;
  6.0 model calls per question on average.
- Agent and baseline get different questions right: 40 both, 21 agent only, 26 baseline only,
  63 neither.
- Why it does worse (from the traces):
  - **The step cap is hit too often**: 41/150 questions used all 8 steps and were forced to
    submit; only 8 of those 41 were right, against 53/108 (49%) when the agent submitted on
    its own.
  - **It does not look at the data values.** The baseline prompt shows example values for every
    column; the agent only sees 3 sample rows per table. 28 of its 448 queries returned 0 rows
    and it often retried near-identical filters (q2, q8: guessed codes and strings) instead of
    checking `SELECT DISTINCT` values.
  - **No plan is written**: with tool use forced every step, the model never writes the plan the
    prompt asks for (0/150 traces have one).
  - **Unchecked submissions**: 5 submitted queries fail to run (e.g. q2 `no such column: School`,
    q61 `DISTINCT aggregates must have exactly one argument`); 10 agent queries hit the 5 s
    timeout while exploring.
  - Failure shapes: 63 wrong values, 14 wrong row count, 5 wrong columns, 5 SQL errors,
    1 timeout, 1 system error.
- **Bug found**: q400 crashed (`system_error`) because sqlglot raised `TokenError` on a query it
  could not tokenize and the guard only caught `ParseError`. Fixed after this run (the guard now
  refuses on any sqlglot error), with a regression test. The 40.7% above includes the bug.
- `git.dirty: true` in the summary is the untracked 3-question smoke run
  ([`results/dev/agent_v1_gpt41mini__smoke3/`](results/dev/agent_v1_gpt41mini__smoke3/)),
  kept as well; the agent code was the committed `fb6f703`.
- Next (dev only): show distinct values / value search, raise or rebalance the step budget,
  verify the submitted query before accepting it, let the first turn plan without forcing a
  tool call.
