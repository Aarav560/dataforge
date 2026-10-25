# DataForge

Turn a plain-language ML goal ("I want to train a Tetris AI") into a ready-to-train dataset. See [DataForge_Plan.md](DataForge_Plan.md) for the full product plan.

Implemented so far:

**Phase 1: core loop**
- Next.js frontend + FastAPI backend, Postgres, Redis job queue
- Planner: goal → ML approach → data needs → search queries per source (Claude when `ANTHROPIC_API_KEY` is set, keyword heuristic otherwise)
- Connectors: Hugging Face (datasets + models), Kaggle, GitHub, searched in parallel
- Ranked results with relevance/quality scores, license badges and mirror detection
- Download selected results through background workers

**Phase 2: media and synthetic data**
- YouTube search (Data API or yt-dlp) and yt-dlp downloads; YouTube's standard license is flagged *local use only*
- arXiv and Semantic Scholar connectors; dataset/model/code links in abstracts are resolved and added as results
- Pipeline engine (DAG of steps) with a *video → state/action* template: frame sampling, PySceneDetect scene filtering, playfield detection, grid sampling, action inference
- Synthetic Data Advisor (rules, refined by Claude when available) with built-in Tetris and arithmetic generators, or a generator scaffold script for other domains
- Every pipeline run is a new dataset version (Parquet), previewable in the UI

## Layout

```
backend/            FastAPI app, arq worker, tests
  app/main.py       REST API
  app/planner.py    goal → plan (Claude structured output / heuristic fallback)
  app/connectors/   huggingface, kaggle, github, youtube, arxiv, semanticscholar
  app/ranking.py    relevance + quality scoring
  app/licensing.py  license string → commercial / non_commercial / research_only / unknown
  app/jobs.py       search fan-out, download and pipeline jobs
  app/pipelines/    engine.py (DAG executor), steps.py, video.py, templates.py
  app/synthetic/    tetris.py (engine + agents), render.py, advisor.py
  app/routes_datasets.py  pipelines, versions, synthetic advice
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
| `YOUTUBE_API_KEY` | Use the YouTube Data API for search (yt-dlp is used otherwise) |
| `S2_API_KEY` | Semantic Scholar key; unauthenticated calls are rate-limited hard |
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
| `GET` | `/api/pipeline-steps`, `/api/pipeline-templates` | step catalog and templates |
| `POST` | `/api/projects/{id}/pipelines` | `{template, asset_ids}` or `{graph}` |
| `GET`/`PUT`/`DELETE` | `/api/pipelines/{id}` | read / edit graph / delete |
| `POST` | `/api/pipelines/{id}/run` | run → new dataset version |
| `GET` | `/api/projects/{id}/versions`, `/api/versions/{id}` | versions |
| `GET` | `/api/versions/{id}/rows` | paged rows (`offset`, `limit`) |
| `GET` | `/api/projects/{id}/synthetic` | synthetic-data advice (`?refresh=true`) |

## Tests

```bash
cd backend && .venv/bin/python -m pytest
cd frontend && npm run typecheck && npm run build
```

## Not yet built

Phases 3–5: the maximalist dockable UI with WebSocket streaming, diffs between versions, dataset cards, export formats, pre-indexing and semantic cache, auto-labeling, and auth (Clerk / Auth.js).

The video template is tuned for clean, high-contrast footage. For real broadcasts, set a manual playfield box on the *Detect board* step if auto-detection fails. The `projects.user_id` column is in place for auth, but the API is currently single-user and unauthenticated, so don't expose it publicly.
