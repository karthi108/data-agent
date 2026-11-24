# Data Agent: build plan

An autonomous AI agent that answers plain-language questions (English or French) about a database: it plans, explores the schema, writes and runs SQL, checks the result, fixes its own errors, asks when the question is ambiguous, and answers with a chart. Every number in the answer comes from a query it actually ran.

**Goal for the CV (in about 3 weeks):** a live link anyone can try, a public benchmark score against a baseline, and a visible agent trace.

Target CV line once measured (fill the numbers only after the runs):
> Built an autonomous data agent (planning, SQL tools, self-correction, clarifying questions, grounding guard); X% execution accuracy on a held-out BIRD subset frozen before the first run, against Y% for single-shot text-to-SQL; live at <url>.

---

## 1. Problem, solution, value

| | |
|---|---|
| **Problem** | Only analysts can query company data; everyone else waits days or decides without data. |
| **Solution** | An agent that does the analyst's loop: plan → explore → query → check → fix → answer with a chart. |
| **Value for a company** | Answers in minutes, no SQL needed, no invented numbers (every figure traces to a query). |
| **Value for the CV** | Proves real agent engineering: planning, tools, self-correction, safety, evaluation. Different domain from Allô Paris. Matches the DRIVECO agent work over BigQuery. |

## 2. How the agent works

```
question
  └─► planner: what tables, what steps, is the question ambiguous?
        ├─ ambiguous ─► ask_user (one clarifying question)
        └─► loop (max 8 steps, cost cap):
              list_tables / describe_table (columns + 3 sample rows)
              run_sql (read-only) ─► error or suspicious result? ─► fix and retry
              ─► enough evidence? ─► draft answer + chart spec
  └─► grounding guard: every number in the answer must appear in a query result
        pass ─► answer + chart + trace      fail ─► 1 retry ─► "could not answer reliably"
```

**Tools (the agent's only way to touch data):**
- `list_tables()`, `describe_table(name)`: schema plus sample rows
- `run_sql(query)`: SELECT only, parsed with sqlglot, read-only connection, 5 s timeout, 1,000-row cap
- `make_chart(spec)`: Vega-Lite or Plotly spec from a result the agent already got
- `ask_user(question)`: one clarifying question, used only when the planner flags ambiguity

**Safety (the same signature as Allô Paris):**
- read-only database user and a SELECT-only parser: the agent can never write, drop or delete
- grounding guard: numbers in the answer are checked against the query results
- step cap, token and cost cap per question, daily budget cap on the live site
- prompt-injection check: data values are treated as data (a row saying "ignore your instructions" changes nothing)

## 3. Stack

Python 3.12, FastAPI, SQLite (benchmark databases) and DuckDB (optional), sqlglot, OpenAI `gpt-4.1-mini` (same model as Allô Paris so the cost figures compare; add a Mistral run later), Plotly or Vega-Lite, pytest, ruff, mypy strict, gitleaks, GitHub Actions, Render.

## 4. Evaluation (the part recruiters remember)

- **Benchmark:** BIRD Mini-Dev (500 questions over real SQLite databases, public). Metric: **execution accuracy** (does the query result match the gold result).
- **Split before any run:** a dev subset (about 150 questions) to improve against, and a **held-out subset (about 150) committed to git before its first run**, never tuned against. The first run is the quoted number.
- **Baselines on the same held-out set:**
  1. single-shot text-to-SQL (one LLM call, no loop): shows what the agent loop adds
  2. agent without self-correction: shows what self-correction adds
  3. full agent
- **Also report:** cost per question, p50/p95 latency, average steps, how often the guard blocked an answer, and the failure reasons (kept in `eval/CHANGES.md`, bad results included).
- **Own clarification test:** 20 deliberately ambiguous questions frozen in advance: did it ask instead of guessing?
- Every number on the README and the CV comes from a script in the repo.

## 5. Live demo

- One public dataset the agent can query on the live site (pick in week 1: a BIRD database with a business feel, or a French open dataset from data.gouv.fr with a clear licence).
- The page shows the answer, the chart, and an expandable **agent trace** (plan, each SQL query, each error and fix): this is what makes the agent visible.
- 5 example questions as buttons, including one ambiguous one and one that needs a self-correction.
- Daily cost cap; free Render plan is fine.

## 6. Milestones

| Week | Deliverable | Done when |
|---|---|---|
| 1 | Repo, CI (ruff, mypy, pytest, gitleaks), BIRD Mini-Dev downloaded, dev/held-out split committed, single-shot baseline running on dev | baseline dev score recorded |
| 1 | Tools with read-only guard, agent loop, trace logging | agent answers dev questions end to end |
| 2 | Self-correction, clarifying questions, grounding guard, cost caps | dev score beats baseline; guard tests pass |
| 2 | **Held-out run (once):** baseline, no-self-correction, full agent | numbers committed with raw results |
| 3 | Web UI with trace view and charts, deploy on Render | live link checked |
| 3 | README "In 30 seconds" (live link, architecture, results table, limits), 60-second demo video | ready for the CV |

## 7. Honest limits to state everywhere

- Benchmark accuracy is not real-company accuracy: the databases are public and clean.
- Held-out numbers are single runs.
- Many text-to-SQL tools exist; the claim is "built and measured", not "first".
- Built with AI coding assistance.

## 8. Rules carried over from Allô Paris

- Freeze a held-out set before its first run; a held-out failure becomes a new dev question, never a targeted fix.
- Keep bad results; explain every failure.
- No number in the README without a script that produced it.
