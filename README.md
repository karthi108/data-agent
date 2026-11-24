# Data Agent

An AI agent that answers plain-language questions about a database. It plans, looks up real
values, runs read-only SQL, fixes its own failing queries, asks when a question is ambiguous,
and answers with a chart. Every number in an answer must come from a query it actually ran.

## In 30 seconds

- **Live demo:** https://data-agent-glgi.onrender.com (free hosting: the first visit after a
  quiet spell can take about a minute to wake up; capped at $1 of model usage per day). Local
  preview without an API key: `uv run python scripts/serve.py --demo`.
- **Held-out result** (BIRD Mini-Dev, 150 questions frozen in git before the first run, one
  run per system, `gpt-4.1-mini`):

| | Single-shot text-to-SQL | This agent |
|---|---|---|
| Execution accuracy | 43.3% | **47.3%** |
| Queries that failed to run | 7 | **0** |
| Cost per question | $0.002 | $0.007 |
| Latency p50 / p95 | 6.0 s / 18.1 s | 7.0 s / 19.7 s |

  The +4.0-point gap is **not statistically significant** (paired sign test p = 0.50; about 1
  question in 6 flips between identical runs). The clear gains are robustness and checked
  answers: no failing queries, and a **grounding guard** that withheld 2 answers whose SQL was
  right but whose sentence stated a number no query returned ("85.6%" when the result was
  85.3).
- **Clarifying questions:** on a frozen set of 20 ambiguous and 20 clear questions, it asked on
  2 of 20 ambiguous and 0 of 20 clear (balanced accuracy 55%). It rarely over-asks but mostly
  guesses when it should ask: a known weakness.

```
question ─► plan ─► look up real values ─► run read-only SQL ─► error? fix and retry
                                                   │
                       submit ─► re-run final query ─► grounding guard ─► answer + chart + trace
                                                        (numbers must appear in results;
                                                         1 retry, then "could not answer")
```

Safety: one SELECT only (parsed with sqlglot) on a read-only connection, 5 s per query,
8 steps and $0.05 per question, a daily budget and a per-visitor rate limit on the site, and
database contents are treated as data, never as instructions.

## Results

Every number here comes from a script in this repo; raw per-question results and agent traces
are in [`eval/results/`](eval/results/), and every run, good or bad, is explained in
[`eval/CHANGES.md`](eval/CHANGES.md).

| System | Split | Execution accuracy | Cost / question | Latency p50 / p95 |
|---|---|---|---|---|
| **Single-shot text-to-SQL** | **held-out (150), 1 run** | **43.3% (65/150)** | $0.0020 | 6.0 s / 18.1 s |
| **Agent v4 (final)** | **held-out (150), 1 run** | **47.3% (71/150)** | $0.0072 | 7.0 s / 19.7 s |
| Single-shot text-to-SQL (`gpt-4.1-mini`, BIRD fixed prompt) | dev (150), 2 runs | 44.0% / 44.7% | $0.0020 | 4.9 s / 12.6 s |
| Agent v1 (tools + read-only SQL) | dev (150) | 40.7% | $0.0060 | 7.9 s / 15.1 s |
| Agent v2 (+ schema up front, value lookup, submit check) | dev (150), 2 runs | 48.0% / 47.3% | $0.0068 | 6.4 s / 16.6 s |
| Agent v3 (v2 + reviewer, now off) | dev (150), 2 runs | 50.0% / 45.3% | $0.0089 | 10.5 s / 23.9 s* |
| Agent v4 (v2 + grounding guard + clarifying questions) | dev (150) | 46.7% | $0.0075 | 7.2 s / 17.6 s |

What the dev work showed: the agent loop alone (v1) was worse than single-shot; showing the
schema with example values and letting the agent look up real column values (v2) brought it
level or slightly ahead; a reviewer step (v3) fixed 15 answers and broke 15, so it is off. On
the held-out set the grounding guard withheld 3 answers: 2 real catches and 1 false alarm on a
reformatted date, fixed afterwards (the fix does not affect the scores, which use the SQL).
*v3 run 1 hit an API stall; latency shown is run 2.

## Reproduce

```bash
uv sync
uv run python scripts/fetch_bird.py          # ~800 MB: BIRD Mini-Dev questions + SQLite DBs
uv run python scripts/check_gold.py          # sanity check: every gold query runs
export OPENAI_API_KEY=...
uv run python scripts/run_eval.py --system single_shot --split dev --run-name single_shot_gpt41mini
```

## Web UI

Four sections in a sidebar (a top bar on phones):
- **Ask**: suggested questions, then the answer with a verification badge and tabs for the
  chart, the result table (CSV download), the SQL (copy) and the steps the agent took (each
  query, its errors and fixes, value lookups, the submit check, the grounding check).
  Ambiguous questions come back as a clarifying question with options.
- **History**: questions asked in this browser; reopening one shows the saved result without
  re-running it.
- **Data catalog**: searchable tables and columns with types, keys and example values.
- **How it works**: the agent loop, safeguards, limits and a link to the evaluation log.
Charts are drawn straight from the final query's rows, so they cannot show a number the query
did not return. Limits: read-only SQL, $0.05 and 8 steps per question, a daily budget
(`DATA_AGENT_DAILY_BUDGET_USD`, default $1) and 20 questions per visitor per hour.

```bash
uv run python scripts/serve.py --demo   # free preview: scripted answers to the 5 examples
uv run python scripts/serve.py          # real agent (paid, needs OPENAI_API_KEY)
```

Default database: BIRD `student_club` (budgets, expenses, events, members; 2.6 MB), bundled in
[`deploy/data/`](deploy/data/) with its CC BY-SA notice. Set `DATA_AGENT_DB` to use another
SQLite file.

**Deploy:** [`render.yaml`](render.yaml) describes one free Render web service (Frankfurt,
deploys only after CI passes, `/healthz` health check). Steps, spending controls and the
free-plan limits are in [`deploy/DEPLOY.md`](deploy/DEPLOY.md). Live at
https://data-agent-glgi.onrender.com since 2026-11-24.

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
- Held-out numbers are single runs, and the accuracy gap to single-shot is within noise.
- The agent asks too rarely on ambiguous questions (2 of 20 on the frozen test).
- The "agent without self-correction" held-out baseline from the plan was not run.
- Many text-to-SQL tools exist; the claim is "built and measured", not "first".
- Built with AI coding assistance.
