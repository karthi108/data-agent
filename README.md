# Data Agent

An autonomous agent that answers plain-language questions (English or French) about a database:
it plans, explores the schema, writes and runs read-only SQL, checks the result, fixes its own
errors, asks when the question is ambiguous, and answers with a chart. See [PLAN.md](PLAN.md).

**Status: week 1.** Repo, CI, BIRD Mini-Dev data, frozen split and the single-shot baseline.
The agent itself is not built yet.

## Results

Every number here comes from a script in this repo; raw per-question results are in
[`eval/results/`](eval/results/), and every run (good or bad) is logged in
[`eval/CHANGES.md`](eval/CHANGES.md).

| System | Split | Execution accuracy | Cost / question | Latency p50 / p95 |
|---|---|---|---|---|
| Single-shot text-to-SQL (`gpt-4.1-mini`, BIRD fixed prompt) | dev (150) | 44.0% (66/150) | $0.0020 | 4.9 s / 12.6 s |

Dev numbers are what we improve against, so they are not the quoted result. Held-out numbers:
none yet. The held-out set is run once per system, after development is done.

## Reproduce

```bash
uv sync
uv run python scripts/fetch_bird.py          # ~800 MB: BIRD Mini-Dev questions + SQLite DBs
uv run python scripts/check_gold.py          # sanity check: every gold query runs
export OPENAI_API_KEY=...
uv run python scripts/run_eval.py --system single_shot --split dev --run-name single_shot_gpt41mini
```

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
