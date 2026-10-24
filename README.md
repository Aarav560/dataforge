# DataForge

Turn a plain-language ML goal ("I want to train a Tetris AI") into a ready-to-train dataset. See [DataForge_Plan.md](DataForge_Plan.md) for the full product plan.

This repository implements **Phase 1 (MVP)** of the roadmap:

- Next.js frontend + FastAPI backend, Postgres, Redis job queue
- Planner: goal → ML approach → data needs → search queries per source (Claude when `ANTHROPIC_API_KEY` is set, keyword heuristic otherwise)
- Connectors: Hugging Face (datasets + models), Kaggle, GitHub, searched in parallel
- Ranked results with relevance/quality scores, license badges and mirror detection
- Download selected results through background workers

## Layout

```
backend/            FastAPI app, arq worker, tests
  app/main.py       REST API
  app/planner.py    goal → plan (Claude structured output / heuristic fallback)
  app/connectors/   huggingface.py, kaggle.py, github.py
  app/ranking.py    relevance + quality scoring
  app/licensing.py  license string → commercial / non_commercial / research_only / unknown
  app/jobs.py       search fan-out and download jobs
  app/worker.py     arq worker entry point
frontend/           Next.js (App Router) UI; proxies /api/* to the backend
docker-compose.yml  Postgres (pgvector) + Redis
```

## Running

Postgres and Redis are optional for local development: without `DATABASE_URL` the backend uses SQLite in `backend/data/`, and without `REDIS_URL` jobs run inside the API process.

```bash
# 1. Infrastructure (optional)
docker compose up -d

# 2. Backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env            # fill in what you have; every key is optional
.venv/bin/uvicorn app.main:app --reload --port 8000
.venv/bin/arq app.worker.WorkerSettings   # only when REDIS_URL is set

# 3. Frontend
cd frontend
npm install
npm run dev                      # http://localhost:3000
```

### Configuration (`backend/.env`)

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | e.g. `postgresql+psycopg://dataforge:dataforge@localhost:5432/dataforge` |
| `REDIS_URL` | e.g. `redis://localhost:6379`; enables the arq worker queue |
| `ANTHROPIC_API_KEY` | Enables the Claude planner (`claude-opus-5-5`, structured output, server-side refusal fallback) |
| `GITHUB_TOKEN` | Raises GitHub search rate limits (unauthenticated search allows 10 requests/min) |
| `KAGGLE_USERNAME`, `KAGGLE_KEY` | Required for Kaggle downloads (search works without) |
| `HF_TOKEN` | Access to gated Hugging Face repos |
| `DATA_DIR` | Where downloads land (default `backend/data`) |
| `MAX_DOWNLOAD_BYTES` | Per-asset download cap (default 5 GiB) |

## API

| Method | Path | |
|---|---|---|
| `POST` | `/api/projects` | `{goal}` → plan with data needs |
| `GET` | `/api/projects`, `/api/projects/{id}` | list / detail |
| `POST` | `/api/projects/{id}/needs` | add a need |
| `PATCH`/`DELETE` | `/api/needs/{id}` | edit / remove a need |
| `POST` | `/api/projects/{id}/search` | search all needs (or `{need_ids}`) |
| `GET` | `/api/projects/{id}/results` | ranked results; filters `need_id`, `source`, `license_class` |
| `POST` | `/api/projects/{id}/downloads` | `{result_ids}` → queued assets |
| `GET` | `/api/projects/{id}/assets` | download status |

## Tests

```bash
cd backend && .venv/bin/python -m pytest
cd frontend && npm run typecheck && npm run build
```

## Not yet built

Everything from Phase 2 onward: YouTube/arXiv connectors, transform pipelines, synthetic data, the maximalist dockable UI with WebSocket streaming, versioning, dataset cards, export formats, and auth (Clerk / Auth.js). The `projects.user_id` column is in place for auth, but the API is currently single-user and unauthenticated, so don't expose it publicly.
