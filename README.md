# Data Agent

An autonomous agent that answers plain-language questions (English or French) about a database:
it plans, explores the schema, writes and runs read-only SQL, checks the result, fixes its own
errors, asks when the question is ambiguous, and answers with a chart. See [PLAN.md](PLAN.md).

**Status: week 1 done.** Repo, CI, BIRD Mini-Dev data, frozen split, single-shot baseline, and
agent v1 (read-only SQL tools, agent loop, traces). Agent v2 is level with the
baseline on dev once run-to-run noise is measured; see [`eval/CHANGES.md`](eval/CHANGES.md).

## Results

Every number here comes from a script in this repo; raw per-question results are in
[`eval/results/`](eval/results/), and every run (good or bad) is logged in
[`eval/CHANGES.md`](eval/CHANGES.md).

| System | Split | Execution accuracy | Cost / question | Latency p50 / p95 |
|---|---|---|---|---|
| Single-shot text-to-SQL (`gpt-4.1-mini`, BIRD fixed prompt) | dev (150), 2 runs | 44.0% / 44.7% | $0.0020 | 4.9 s / 12.6 s |
| Agent v1 (tools + read-only SQL, no explicit self-correction) | dev (150) | 40.7% (61/150) | $0.0060 | 7.9 s / 15.1 s |
| Agent v2 (+ schema up front, value lookup, submit check) | dev (150), 2 runs | 48.0% / 47.3% | $0.0068 | 6.4 s / 16.6 s |
| Agent v3 (v2 + reviewer, now off by default) | dev (150), 2 runs | 50.0% / 45.3% | $0.0089 | 10.5 s / 23.9 s* |

Dev numbers are what we improve against, so they are not the quoted result. Run twice each, the
baseline scored 44.0% / 44.7% and agent v2 48.0% / 47.3%; about 1 question in 6 flips between
identical runs, and the agent's +3.3-point average lead is not significant (sign test p = 0.90).
A reviewer step (v3) fixed 15 answers and broke 15, so it is off by default. The grounding
guard (every number in an answer must come from a query result), replayed offline on agent v2's
150 dev answers, flagged 3, all real: an invented count, a contradicted result, an unqueried
percentage. *v3 latency is
run 2; run 1 hit an API stall. Held-out numbers:
none yet. The held-out set is run once per system, after development is done.

## Reproduce

```bash
uv sync
uv run python scripts/fetch_bird.py          # ~800 MB: BIRD Mini-Dev questions + SQLite DBs
uv run python scripts/check_gold.py          # sanity check: every gold query runs
export OPENAI_API_KEY=...
uv run python scripts/run_eval.py --system single_shot --split dev --run-name single_shot_gpt41mini
```

## Web UI

One page: ask a question about a database, get the answer, the result table, a chart and the
full agent trace (plan, every query with its errors and fixes, value lookups, the submit check,
the grounding check). Ambiguous questions come back as a clarifying question with options.
Charts are drawn straight from the final query's rows, so they cannot show a number the query
did not return. Limits: read-only SQL, $0.05 and 8 steps per question, a daily budget
(`DATA_AGENT_DAILY_BUDGET_USD`, default $1) and 20 questions per visitor per hour.

```bash
uv run python scripts/serve.py --demo   # free preview: scripted answers to the 5 examples
uv run python scripts/serve.py          # real agent (paid, needs OPENAI_API_KEY)
```

Default database: BIRD `student_club` (budgets, expenses, events, members; 2.6 MB). Set
`DATA_AGENT_DB` to use another SQLite file.

## Evaluation rules

- Benchmark: [BIRD Mini-Dev](https://huggingface.co/datasets/birdsql/bird_mini_dev) (SQLite,
  500 questions, CC BY-SA 4.0), annotations pinned to one Hugging Face commit and checked by hash.
- Metric: BIRD's strict execution accuracy (`set(predicted_rows) == set(gold_rows)`).
- Split ([`eval/splits/`](eval/splits/)): 150 dev / 150 held-out / 200 reserve, committed to git
  before any model run. A test pins its hash, so any edit fails CI.
- The held-out set is never tuned against. The runner refuses a held-out run unless the split is
  committed, the run is explicitly confirmed, and that run name has never been used before.
- A held-out failure becomes a new dev question, never a targeted fix.

## Honest limits

- Benchmark accuracy is not real-company accuracy: the databases are public and clean.
- Held-out numbers will be single runs.
- Many text-to-SQL tools exist; the claim is "built and measured", not "first".
- Built with AI coding assistance.
