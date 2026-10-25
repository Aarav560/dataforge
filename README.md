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

**Phase 3: maximalist UI**
- Dockable, resizable panels (flexlayout) whose arrangement is remembered per browser; single-panel tabs on phones
- Live crawl feed over WebSockets: results are saved and pushed per source as each answers, with per-source counters
- 3D source graph (goal → needs → sources → results, colored by license), click to preview
- Preview pane: dataset viewer rows and images, model cards, READMEs, paper abstracts with linked resources, YouTube embeds, and a browser for downloaded files
- License matrix of selected and downloaded sources × commercial / redistribution / attribution / share-alike
- Pipeline canvas (React Flow): add steps, wire them, edit parameters, save and run with live step status
- Stats HUD (rows, results, bytes gathered, active workers, API quota) and a Ctrl+K command bar (`/search`, `/pipeline`, `/download`, `/show`, `/open`)

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
  app/routes_live.py      WebSocket events, stats, previews, license matrix, asset files
  app/events.py     event bus (in-process, or Redis pub/sub across worker processes)
  app/worker.py     arq worker entry point
frontend/           Next.js (App Router) UI; proxies /api/* to the backend
  components/panels/  plan, feed, graph, result wall, canvas, preview, license matrix
  lib/workspace.tsx   shared project state, refreshed from the event stream
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

The browser connects to the backend's WebSocket directly (`ws://<host>:8000` by default; set `NEXT_PUBLIC_WS_URL` when the backend lives elsewhere). If the socket is down, the UI falls back to polling.

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
| `WS` | `/api/projects/{id}/ws` | live events (recent backlog replayed on connect) |
| `GET` | `/api/projects/{id}/stats` | HUD numbers |
| `GET` | `/api/results/{id}/preview` | remote preview of a result |
| `POST` | `/api/license-matrix` | `{result_ids}` → rights per source |
| `GET` | `/api/assets/{id}/files`, `/preview`, `/file` | browse downloaded files |

## Tests

```bash
cd backend && .venv/bin/python -m pytest
cd frontend && npm run typecheck && npm run build
```

## Not yet built

Phases 4–5: diffs between versions, dataset cards, export formats, pre-indexing and semantic cache, auto-labeling, and auth (Clerk / Auth.js).

The video template is tuned for clean, high-contrast footage. For real broadcasts, set a manual playfield box on the *Detect board* step if auto-detection fails. The `projects.user_id` column is in place for auth, but the API is currently single-user and unauthenticated, so don't expose it publicly.
