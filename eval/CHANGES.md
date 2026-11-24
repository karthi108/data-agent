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

## 2026-11-24: agent v2, dev split (above the baseline, but within noise)

- Changes from v1 (commit `17e5cb9`), each switchable in `AgentConfig`:
  1. schema overview in the first message (tables, columns, types, keys, 3 example values per
     column), so no steps are spent rediscovering it;
  2. new `column_values` tool: most frequent distinct values of a column, optional substring
     search, so filters use values that exist;
  3. step 1 may answer without a tool call, so the model can write a plan;
  4. self-correction on submit: the submitted query is executed and sent back if it fails or
     returns 0 rows (the 0-row check is asked once);
  5. a reminder when 2 steps are left.
  Same limits as v1: 8 steps, $0.05 per question, 5 s per query.
- Script: `scripts/run_eval.py --system agent --split dev --run-name agent_v2_gpt41mini`.
  Raw results and traces: [`results/dev/agent_v2_gpt41mini/`](results/dev/agent_v2_gpt41mini/).
- **Dev execution accuracy: 48.0% (72/150)**: +7.3 points over v1 (40.7%), +4.0 over the
  single-shot baseline (44.0%).
- **The gain over the baseline is not significant.** Per question, v2 is right where the
  baseline is wrong 27 times and the reverse 21 times (45 both right); a two-sided sign test on
  those 48 gives p = 0.47. One run each, so this is "about the same as single-shot, slightly
  ahead", not a proven improvement.
- Cost $1.02 total ($0.0068 per question, 3.4x the baseline); latency p50 6.4 s, p95 16.6 s;
  4.9 steps per question (v1: 6.0).
- What changed in behaviour:
  - forced submissions fell from 41 to 23 (7 of those 23 right; 65/127 = 51% right when the
    agent submitted on its own);
  - SQL errors in submitted queries fell from 5 to 0, and v2 called `column_values` 251 times;
  - the self-correction gate itself almost never fired: only 1 question had a submission sent
    back (and it stayed wrong). The gain comes from the schema and the value lookups, not from
    the submit check. This matters for the planned "no self-correction" ablation;
  - still no written plan: 0/150 traces have one, even with tool use optional on step 1.
- Remaining failures: 60 wrong values, 14 wrong row count, 2 wrong columns, 1 timeout (q188),
  1 float precision only. Most errors are now logic, not syntax: wrong grain, wrong
  denominator, wrong tie handling.
- `git.dirty: true` is again only the untracked 3-question smoke run
  ([`results/dev/agent_v2_gpt41mini__smoke3/`](results/dev/agent_v2_gpt41mini__smoke3/)).

## 2026-11-24: run-to-run noise on dev (baseline and agent v2, run twice each)

- Reruns with no code change: `single_shot_gpt41mini_rep2` and `agent_v2_gpt41mini_rep2`
  ([`results/dev/`](results/dev/)). Comparison: `scripts/compare_runs.py`, output
  [`noise_dev_baseline_vs_agent_v2.json`](noise_dev_baseline_vs_agent_v2.json).
- **Totals are stable, single questions are not.** Even at temperature 0:
  - single-shot: 44.0% then 44.7%. 54 questions right both times, 71 wrong both times,
    **25 flip** between the two runs;
  - agent v2: 48.0% then 47.3%. 60 right both times, 67 wrong both times, **23 flip**.
- **Agent v2 vs baseline over both runs: +3.3 points on average, not significant.** Per
  question (share of runs correct), the agent is better on 35 questions and worse on 33 (82
  tied); two-sided sign test p = 0.90. On this dev set, the agent and single-shot cannot be
  told apart yet.
- Rerun costs: single-shot $0.30, agent $1.06. Rerun failure counts: single-shot 5 SQL errors,
  3 timeouts, 75 wrong results; agent 2 timeouts, 77 wrong results. Each rerun's summary shows
  `git.dirty: true` only because the other rerun's results folder was not committed yet.
- What this means from now on:
  - a change on dev must be judged on at least 2 runs, and its gain counted against flips of
    about 1 question in 6 (≈ 23–25 of 150), not just a total that moves by under a point;
  - the held-out numbers will be single runs (PLAN.md), so a gap of a few points between
    systems there must be reported as within noise unless the paired test says otherwise.

## 2026-11-24: agent v3 (reviewer), dev split, 2 runs: no gain, reviewer switched off

- Motivation: in v2's 67 questions wrong in both runs, most errors were queries that do not do
  exactly what the question says: extra helper columns (q292 returns the sort column, q479 a
  group label), wrong row shape (q61 one aggregated cell instead of a list, q181 one row where
  ties must all be listed), conditions missed or invented (q412 adds an unrequested filter;
  q72 substring `LIKE` where exact keywords are meant), wrong counting grain (q411 rows instead
  of patients). A few are debatable gold (q381 treats weight 0 as "not recorded"; q155 is 1
  minute off from float rounding).
- Change (v3, commits `9d8e4de` then `bab8818`): after a submitted query runs and returns rows, one
  reviewer call compares question, SQL and result preview against a fixed checklist (columns,
  row shape, ties, stated and invented conditions, counting grain, ratios and rounding, exact
  vs substring matching) and sends up to 3 issues back to the agent once. The first version
  listed up to 6 loose "issues" on the smoke test, so before the full runs it was tightened to
  max 3, each quoting the question words it breaks (first smoke run kept as
  [`results/dev/agent_v3a_gpt41mini__smoke3/`](results/dev/agent_v3a_gpt41mini__smoke3/)).
  Also: a forced last-step submission that fails to run now falls back to the last query that
  ran (6 times over the two runs).
- Runs: [`agent_v3_gpt41mini`](results/dev/agent_v3_gpt41mini/) **50.0%** and
  [`agent_v3_gpt41mini_rep2`](results/dev/agent_v3_gpt41mini_rep2/) **45.3%**, mean
  **47.7%**: exactly v2's mean (48.0% / 47.3%).
- Paired over 2 runs each (`scripts/compare_runs.py`):
  - v3 vs v2: better on 21 questions, worse on 23, p = 0.88
    ([`noise_dev_agent_v2_vs_v3.json`](noise_dev_agent_v2_vs_v3.json));
  - v3 vs single-shot: +3.3 points, better on 34, worse on 34, p = 1.0
    ([`noise_dev_baseline_vs_agent_v3.json`](noise_dev_baseline_vs_agent_v3.json)).
- What the reviewer did (`scripts/review_effect.py`, scoring the query it sent back against the
  final answer; [`review_effect_agent_v3.json`](review_effect_agent_v3.json)):
  - it flagged about half of all submissions (63 of 124 reviews in run 1, 54 of 124 in run 2);
  - of the flagged questions, **15 went from wrong to right and 15 from right to wrong** (87
    unchanged). Net effect zero: the same model cannot reliably tell its own good queries from
    bad ones;
  - it eats the step budget: forced last-step submissions rose from ~23 per run (v2) to 54 and
    50.
- Cost $1.35 and $1.33 per run ($0.009 per question, +30% over v2); 5.9 steps per question.
- Latency: run 1's p95 is 852 s because questions 303-335 all stalled about 14 minutes at the
  same moment, an API stall with retries, not the agent (run 2: p50 10.5 s, p95 23.9 s).
- Decision: **reviewer off by default** (`AgentConfig.review = False`, `--review` to switch it
  on), since it adds cost and latency for no measured gain. The default agent is v2's behaviour
  plus the forced-submit fallback.
- `git.dirty: true` in both summaries: the other run's results folder and the smoke folders
  were not committed yet; the agent code was the committed `bab8818`.

## 2026-11-24: grounding guard (agent v4), checked offline on agent v2's answers (no model calls)

- Change (`src/data_agent/grounding.py`, wired into `agent.py`, `AgentConfig.grounding = True`):
  every number in the agent's final answer must match a number seen in a query result it ran
  (numeric cells, numbers inside text cells such as dates, row counts). Tolerances: the
  rounding the answer shows (57.14 matches 57.142857) and percentage scale (57.14% matches
  0.5714). Numbers that appear in the question or hint need no grounding. If a number is not
  grounded the agent gets one retry; after that, or on the last step, the answer text is
  replaced by "I could not answer this reliably...". The SQL is kept, so EX scores (which use
  the SQL) are not affected; the guard protects the answer the user reads. Outcomes are
  recorded per question (`guard` in predictions, `grounding_guard` in summaries).
- Unit tests: 14 for the number matching, 8 for the agent paths (pass, retry then pass,
  blocked after retry, blocked on last step, numbers from the question, row counts, no
  numbers, guard on with self-correction off).
- **Offline replay on agent v2's 150 dev answers** (`scripts/guard_replay.py`: re-runs every
  query from the traces locally to rebuild the evidence, then checks the saved answer text; no
  API calls; [`guard_replay_agent_v2.json`](guard_replay_agent_v2.json)): 121 pass,
  26 have no numbers, **3 flagged, and all 3 are real problems**:
  - q188: every counting query timed out, yet the answer claims "10982 players" (invented);
  - q302: the SQL is right and returns an average of 16.1 and the verdict "more", but the
    answer says 18.1 and "fewer" (the text contradicts the agent's own correct result);
  - q404: the final query returns NULL, yet the answer says "28.57%" (2/7 computed in the
    model's head, never queried).
  No false alarms in this sample. Limits: the guard only checks numbers, not words (q302's
  "fewer" would not be caught alone), and a number that is right by coincidence (another cell
  with the same value) passes. False negatives are not measured.
- Not yet measured live: how the retry changes the agent's behaviour in a real run. That needs
  a paid run and will be done only with approval.

## 2026-11-24: clarifying questions (agent v4) and the frozen clarification test (no run yet)

- Change: new `ask_user` tool (one short question plus the readings being chosen between) and a
  prompt rule: ask only when reasonable readings would give different answers (which entity,
  metric, period, or threshold), not when one reading is natural, the hint settles it, or the
  database can answer it. Modes (`AgentConfig.clarify`): `ask` for live use (the run stops and
  returns the question; `Agent.run(..., clarifications=[(asked, answer)])` resumes, and the agent
  cannot ask twice), `decline` for benchmarks (same tools; the reply is "no one can answer, state
  your assumption"; asks are counted in `clarification_asked`), `off` (tool not offered).
  `run_eval.py` uses `decline` by default.
- Frozen test set, committed before any run:
  [`clarify/clarify_set_v1.json`](clarify/clarify_set_v1.json), hash pinned in
  `tests/test_clarify_set.py`. 20 ambiguous questions over 9 BIRD databases (entity: two
  Schumachers, four heroes named Atlas, 80 users named Chris; time: 64 Monaco Grands Prix;
  metric: "fastest", "most active", "most popular"; vague thresholds: "high", "large",
  "recently"; undefined codes: loan status A-D, thrombosis 0-3; grain: 56,822 card printings
  vs 21,738 distinct cards), every ambiguity checked against the local databases. Plus **20
  clear controls** on the same databases, so an agent that asks about everything is caught:
  scoring is balanced accuracy, where "always ask" and "never ask" both score 50%.
- Runner: `scripts/run_clarify_eval.py` (agent in `ask` mode). It makes paid calls, so it
  refuses without `--confirm-spend`; it also refuses if the set is not committed and never
  reuses a run name (first run is the quoted one). Tested end to end with a scripted model.
- **Not run yet** (no API spending without approval). Estimated cost of one run: about
  $0.10-0.30.
- Note for the held-out run: the system prompt and tool list changed after the last dev
  measurement (v2/v3), because of the clarification rule. The agent's current version has not
  been measured on dev. A dev run of the final version before the held-out run (about $1) would
  confirm it did not get worse; that also needs approval.

## 2026-11-24: web UI (no evaluation impact)

- `src/data_agent/web/`: FastAPI app and one page showing the answer, result table, chart and
  expandable agent trace; clarifying questions come back with option buttons and resume the run.
- Charts are built from the final query's rows by fixed rules (horizontal bars for
  category/number, a line for months or dates), not by the model, so they only show numbers the
  query returned. PLAN.md had the agent produce the chart spec; this is simpler and cannot
  invent data.
- Limits: the agent's per-question caps, a daily budget (`DailyBudget`, default $1 per UTC day;
  a question is refused if it could cross it) and 20 questions per client per hour.
- Demo mode (`scripts/serve.py --demo`): a scripted stand-in for the model replays fixed tool
  calls for the 5 example questions, with a visible banner; the real loop, tools, database,
  submit check and grounding guard still run. Used to build and check the page with no API
  calls. Checked in the browser: all 5 examples, the clarification round trip, the trace
  (failed query in red, fix in green), dark mode and phone width (no horizontal scroll).
- 8 tests (API, clarification round trip, budget refusal and persistence, rate limit, input
  limits, chart rules, and all 5 demo examples end to end on the real database, where every
  demo answer passes the grounding guard).

## 2026-11-24: web UI redesign (no evaluation impact)

- Sidebar navigation with four sections: Ask, History (saved in the browser, reopening does
  not re-run or cost anything), Data catalog (new `/api/schema`: tables, columns, types, keys,
  example values; searchable) and How it works (loop, safeguards, limits, link to this log).
- Ask: suggested questions labelled by type; result card with the answer, a verification
  badge, steps/time/cost, and tabs for Chart, Table (CSV download), SQL (formatted, copy) and
  Steps (numbered timeline; failed steps open by default).
- Look: IBM Plex Sans/Mono, neutral palette with one accent, light/dark/auto theme switch,
  keyboard use (Enter to ask, `/` to focus, arrow keys on tabs), phone layout with a top bar.
- Checked in the browser in demo mode: every view on phone width, desktop layout metrics,
  light and dark themes, the self-correction trace, the clarification round trip, catalog
  filtering, and reopening from History (no new API request). Fixed along the way: a tab-bar
  scrollbar, a stretched top bar on short pages on phones, wrapped example IDs, and a cached
  old page (now served with `Cache-Control: no-cache`).
- The agent's own schema text is unchanged (the catalog uses a separate structured copy), so
  this has no effect on agent behaviour or scores.

## 2026-11-24: deploy preparation (not deployed; no evaluation impact)

- `render.yaml`: one free Render web service (Frankfurt), `uv sync --frozen --no-dev`, uvicorn
  with proxy headers (`FORWARDED_ALLOW_IPS` as an env var, after a shell-quoted `"*"` argument
  expanded into file names in a local test), `/healthz` health check, deploys only after CI
  passes, `OPENAI_API_KEY` declared with `sync: false` (never in git). Validated against
  Render's JSON schema.
- `deploy/data/student_club.sqlite` bundled (2.6 MB, CC BY-SA 4.0, NOTICE included; a test
  checks it is byte-identical to the verified benchmark copy).
- App: `/healthz`, security headers (Content-Security-Policy limited to this origin and the
  two pinned CDNs, no framing, nosniff), API docs disabled, and `DATA_AGENT_PAUSED=1` to refuse
  questions without calling the model.
- Checked locally with the exact start command, paused, with a placeholder key (no API
  calls): health check, pause refusal, headers, hidden docs, and the forwarded client address
  reaching the rate limiter. Chart, fonts and console checked in the browser under the CSP.
- Honest limit, written in `deploy/DEPLOY.md`: on the free plan the daily budget counter and
  the rate limit reset when the service restarts, so the hard spending ceiling must be an
  OpenAI project budget on a dedicated key.

## 2026-11-24: dev check of the final agent (v4) before the held-out run

- Agent v4 = v2 behaviour + forced-submit fallback + grounding guard + clarification rule and
  `ask_user` tool (benchmark mode `decline`); reviewer off. Code at `def39e7`.
- Run: [`results/dev/agent_v4_gpt41mini/`](results/dev/agent_v4_gpt41mini/).
  **Dev EX 46.7% (70/150)**. Against v2's two runs (48.0%, 47.3%): better on 14 questions,
  worse on 18, p = 0.60 ([`noise_dev_agent_v2_vs_v4.json`](noise_dev_agent_v2_vs_v4.json)).
  Within noise: the late changes did not measurably hurt accuracy.
- Grounding guard on 150 real answers: 118 pass, 2 pass after the retry, 1 blocked,
  28 with no numbers, 1 with no answer. Clarification: the agent asked 0 times on the 150
  BIRD questions (already clarified by the benchmark), so no sign of over-asking.
- Cost $1.12 ($0.0075 per question); latency p50 7.2 s, p95 17.6 s; 5.1 steps per question.
- Decision: this version (v4, unchanged) goes to the held-out run. `git.dirty: true` was the
  untracked video script only.
