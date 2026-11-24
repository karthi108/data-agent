# 60-second demo video: script

Record the live site (or `scripts/serve.py --demo` if the site is down) at 1280x800, browser
zoom 110%, light theme. Speak over it; no music needed. Times are targets.

| Time | Screen | Say |
|---|---|---|
| 0:00-0:07 | Ask page, cursor in the question box | "This is a data agent: you ask a question in plain language and it answers from a real database, with SQL you can check." |
| 0:07-0:20 | Click **Spending by category**. Answer appears with the green "Numbers verified" badge and the chart | "It plans, looks up real values, runs read-only SQL and draws the chart straight from the result. The green badge means every number in the sentence was found in a query result." |
| 0:20-0:32 | Click **Needs a self-correction**, then the **Steps** tab; the failed query is open in red | "When a query fails, it reads the error and fixes it. Here it tried a column that doesn't exist, then joined through the budget table instead. Every step is visible." |
| 0:32-0:42 | Click **Ambiguous on purpose**; the clarifying question with two options appears; click one | "If a question can mean two things, it asks instead of guessing." |
| 0:42-0:50 | Open **Data catalog**, type `cost` in the filter | "The catalog shows what you can ask about." |
| 0:50-0:60 | Open **How it works** (or the GitHub README results table) | "It's measured on the BIRD benchmark against a single-shot baseline, on a held-out set frozen before the first run. Read-only, cost-capped, and every result is logged, including the bad ones." |

## Tips

- Do one dry run first so the free Render service is awake (it sleeps after 15 minutes).
- If a live answer differs from this script, keep it: the point is that it is real.
- Keep the cursor still while talking; click only when the line says so.
- Upload unlisted (YouTube or Loom) and put the link in the README and on the CV.
