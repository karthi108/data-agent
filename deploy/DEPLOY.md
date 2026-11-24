# Deploying the demo on Render

The repository is ready to deploy as one free Render web service from `render.yaml`. Going live
is a manual step: it needs a Render account and an OpenAI key, and every question asked on the
public site is billed to that key.

## Before you start: cap the spend on the OpenAI side

The app has its own limits, but on Render's free plan they are not a hard ceiling (see
"Limits" below). The real backstop is OpenAI:

1. In the OpenAI dashboard, create a **separate project** for the demo and an API key in it.
2. Set a **monthly budget / usage limit** on that project (for example $5).
3. Use only that key on Render. Never reuse your personal key.

## Deploy

1. Sign in at https://dashboard.render.com and connect your GitHub account.
2. **New > Blueprint**, pick the `data-agent` repository, branch `main`.
3. Render reads `render.yaml` and asks for the one secret value: paste the demo project's
   `OPENAI_API_KEY` there. It is stored in Render, never in git.
4. **Apply.** The first build takes a few minutes (`uv sync`), then Render polls `/healthz`.
5. Open the `onrender.com` URL. Ask one suggested question to check the real agent end to end.

Later deploys are automatic: every push to `main` deploys **after CI passes**
(`autoDeployTrigger: checksPass`).

## Controls in the Render dashboard (Environment tab)

| Variable | Default | Effect |
|---|---|---|
| `DATA_AGENT_PAUSED` | `0` | `1` refuses all questions without calling the model. Render restarts the service with the new value. |
| `DATA_AGENT_DAILY_BUDGET_USD` | `1.00` | Questions are refused once the day's spend could pass this. |
| `DATA_AGENT_HOURLY_LIMIT` | `20` | Questions per visitor (IP address) per hour. |
| `DATA_AGENT_DB` | bundled `student_club` | SQLite file to query. |

## Limits to know

- **The daily budget counter resets when the service restarts.** The free plan has no
  persistent disk, so the counter lives in memory and in a file that disappears on restart or
  redeploy. Each question is still capped at $0.05 and 8 steps, and the OpenAI project limit
  above is the hard ceiling.
- **Cold starts.** Free services sleep after about 15 minutes without traffic; the next
  visitor waits roughly a minute while it wakes up.
- **Rate limit per IP** is in memory too, so it also resets on restart, and visitors behind
  one shared IP (an office, a university) share the 20 per hour.
- **Single instance.** The free plan runs one instance, which matches the in-memory counters.
  Scaling to several instances would need a shared store (for example Render Key Value).

## Run the same thing locally

```bash
uv sync --frozen --no-dev
DATA_AGENT_PAUSED=0 uv run uvicorn data_agent.web.app:app --port 8000 --proxy-headers
```

Or the free scripted preview, with no API calls: `uv run python scripts/serve.py --demo`.
