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

**Phase 4: dataset platform**
- Transform library: load/stack tables, select/rename, clean, dedupe, filter, stratified or grouped splits, sampling, image indexing, text chunking, inline rows
- Web pages → text: robots.txt-aware crawler that refuses private addresses, plus PII detection with redact/drop/flag
- Templates: web → text corpus, images → labeled set, multiple CSVs → unified table
- Version stats (DuckDB): nulls, distinct counts, histograms, class balance, image sample grid; row and schema diffs between versions
- Auto dataset cards (Hugging Face-compatible): sources, combined license terms, build steps, detected limitations; Claude writes the summary when keyed
- Exports: Parquet, CSV, JSONL, HF layout, COCO, YOLO, TFRecord (no TensorFlow needed); push to the Hub; license gate blocks publishing incompatible or non-redistributable combinations
- Hub index: nightly sync of Hugging Face, Kaggle and OpenML metadata with local embeddings (bge-small); searches use it first, plus a semantic cache of recent queries
- Auto-labeling with Claude (text or images) or keyword fallback; low-confidence rows go to a keyboard-driven review queue; applying reviews creates a derived version

**Phase 5: power features**
- Accounts and team projects (`AUTH_ENABLED=true`): email/password sign-in with scrypt hashes and HttpOnly session cookies (or `Authorization: Bearer` for scripts), teams with owner/editor/viewer roles, projects shared by moving them into a team; every route checks access, and other users' ids answer 404
- Plugin connectors: drop a `Connector` subclass into `plugins/` or install a package with a `dataforge.connectors` entry point; it joins searches for the kinds it declares. `plugins/zenodo.py` is a working example
- Train a baseline: a quick scikit-learn model on any dataset version (numeric, vector, categorical, TF-IDF text or pixel features), scored against a trivial predictor, with leakage-prone columns pre-excluded; metrics, confusion matrix and the model file
- Alembic migrations, applied at startup

**Claude connector**
- An MCP server (`backend/app/mcp_server.py`) so Claude Desktop and Claude Code can drive DataForge on your Claude plan; see [Use DataForge from Claude](#use-dataforge-from-claude-connector)

## Layout

```
backend/            FastAPI app, arq worker, tests
  app/main.py       REST API
  app/planner.py    goal → plan (Claude structured output / heuristic fallback)
  app/connectors/   huggingface, kaggle, openml, github, youtube, arxiv, semanticscholar
  app/ranking.py    relevance + quality scoring
  app/licensing.py  license string → usage class and rights
  app/auth.py       sessions, passwords, project access (app/routes_auth.py: sign-in, teams, sharing)
  app/plugins.py    connector plugin loader
  app/baseline.py   train-a-baseline
  app/mcp_server.py Claude connector (MCP server over stdio or HTTP)
  app/jobs.py       search fan-out, download and pipeline jobs
  app/pipelines/    engine.py (DAG executor), steps*.py, video.py, templates.py
  app/synthetic/    tetris.py (engine + agents), render.py, advisor.py
  app/routes_datasets.py  pipelines, versions, synthetic advice
  app/routes_live.py      WebSocket events, stats, previews, license matrix, asset files
  app/analytics.py  DuckDB stats and diffs
  app/cards.py      dataset cards
  app/exports.py    export formats and the license gate
  app/hubindex.py   hub metadata index and semantic query cache (app/embeddings.py)
  app/events.py     event bus (in-process, or Redis pub/sub across worker processes)
  app/worker.py     arq worker entry point
  migrations/       Alembic migrations
plugins/            connector plugins (zenodo.py example)
frontend/           Next.js (App Router) UI; proxies /api/* to the backend
  components/panels/  plan, feed, graph, results, canvas, preview, licenses, workspace, review, team, sources
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
| `HF_TOKEN` | Access to gated Hugging Face repos, and pushing exports to the Hub |
| `YOUTUBE_API_KEY` | Use the YouTube Data API for search (yt-dlp is used otherwise) |
| `S2_API_KEY` | Semantic Scholar key; unauthenticated calls are rate-limited hard |
| `DATA_DIR` | Where downloads, versions and exports land (default `backend/data`) |
| `INDEX_AUTO_SYNC` | Sync the hub index nightly (default `true`; in-process mode syncs when the index is empty or stale) |
| `INDEX_SIZE_PER_SOURCE` | Entries per source and kind in the hub index (default 5000) |
| `MAX_DOWNLOAD_BYTES` | Per-asset download cap (default 5 GiB) |
| `AUTH_ENABLED` | Require sign-in and enable teams (default `false`: single-user, everything owned by a local user) |
| `OPEN_SIGNUP` | Let anyone create an account (default `true`; when `false`, only the first account can sign up) |
| `SECURE_COOKIES` | Mark session cookies `Secure`; set when serving over HTTPS |
| `PLUGINS_DIR` | Folder of connector plugins (default `plugins/` at the repo root) |
| `AUTO_MIGRATE` | Apply Alembic migrations at startup (default `true`) |

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
| `GET` | `/api/versions/{id}/diff` | diff against `?against=`, the parent, or the previous version |
| `POST` | `/api/versions/{id}/card` | regenerate the dataset card |
| `GET` | `/api/export-formats`, `/api/versions/{id}/export-check` | formats and license verdict |
| `POST` | `/api/versions/{id}/exports` | `{format, push_to_hub, repo_id, private}` |
| `GET` | `/api/exports/{id}/download` | zip of an export |
| `GET` | `/api/versions/{id}/reviews` | review queue (`status`, paging) |
| `POST` | `/api/reviews/{id}` | `{action: accept\|correct\|reject\|reset, label}` |
| `POST` | `/api/versions/{id}/apply-reviews` | derived version with reviews applied |
| `GET`/`POST` | `/api/index`, `/api/index/sync` | hub index status / sync now |
| `POST` | `/api/auth/signup`, `/login`, `/logout`; `GET /api/auth/me` | accounts and sessions |
| `GET`/`POST` | `/api/teams`, `/api/teams/{id}/members` | teams and members (`PATCH`/`DELETE` a member) |
| `PUT` | `/api/projects/{id}/team` | `{team_id}` share with a team, or `null` for private |
| `GET` | `/api/sources` | built-in and plugin connectors |
| `GET` | `/api/versions/{id}/baseline-options` | target candidates and suggested exclusions |
| `POST`/`GET` | `/api/versions/{id}/baselines` | `{target, exclude}` train / list runs |
| `GET` | `/api/baselines/{id}/model` | trained model (joblib) |
| `POST` | `/api/auth/token` | bearer token for scripts and the Claude connector |
| `PUT` | `/api/versions/{id}/card` | `{summary, limitations}` written by the caller |
| `POST` | `/api/projects` with `plan` | create a project from a caller-written plan (skips the planner) |

## Tests

```bash
cd backend && .venv/bin/python -m pytest
cd frontend && npm run typecheck && npm run build
```

## Use DataForge from Claude (connector)

DataForge ships an MCP server, so Claude can drive it from Claude Desktop or Claude Code on your normal Claude plan, with no API key. Claude does the reasoning (writing the plan, choosing results, labeling, writing the dataset card), and DataForge does the data work through 29 tools: projects and needs, search, results and previews, license matrix, downloads, synthetic advice, pipelines, versions, rows, diffs, review queue and labels, cards, exports and baselines. Image rows in the review queue are sent as images, so Claude can label them by looking.

Start the backend first. The connector talks to it at `DATAFORGE_URL` (default `http://localhost:8000`).

**Claude Desktop**: add this to `claude_desktop_config.json` (Settings → Developer → Edit config), using absolute paths:

```json
{
  "mcpServers": {
    "dataforge": {
      "command": "/path/to/dataforge/backend/.venv/bin/python",
      "args": ["/path/to/dataforge/backend/app/mcp_server.py"],
      "env": { "DATAFORGE_URL": "http://localhost:8000" }
    }
  }
}
```

**Claude Code**:

```bash
claude mcp add dataforge --env DATAFORGE_URL=http://localhost:8000 -- \
  /path/to/dataforge/backend/.venv/bin/python /path/to/dataforge/backend/app/mcp_server.py
```

Then ask, for example: *"Use DataForge to plan a dataset for a Tetris AI, search for it, pick the commercially usable results, generate synthetic games, and train a baseline."*

With `AUTH_ENABLED=true`, create a token (Sources panel → *Create connector token*, or `POST /api/auth/token`) and pass it as `DATAFORGE_TOKEN` in the `env` above. The connector acts as that user.

**Over HTTP** (another machine, or a server): run `python -m app.mcp_server --http --port 8765 --token "$MCP_TOKEN"` (add `--allowed-host your.domain` behind a tunnel or proxy), then in Claude Code: `claude mcp add --transport http dataforge https://your.domain/mcp --header "Authorization: Bearer $MCP_TOKEN"`. claude.ai in the browser can't send a fixed token header, so it isn't supported yet; it would need OAuth on the connector.

## Writing a connector plugin

```python
# plugins/my_catalog.py
from app.connectors.base import Connector, RawResult

class MyCatalog(Connector):
    """Our internal data catalog."""
    name = "mycatalog"
    kinds = frozenset({"dataset"})

    def __init__(self, client, settings=None):
        super().__init__(client)

    async def search(self, query, kind, limit): ...      # -> list[RawResult]
    async def download(self, external_id, kind, dest, max_bytes): ...  # -> bytes written
```

Restart the backend; the *Sources* panel shows loaded plugins and any load errors. Plugins run with the server's permissions, so only install ones you trust.

## Deploying

Before exposing DataForge beyond your machine: set `AUTH_ENABLED=true`, `SECURE_COOKIES=true` behind HTTPS, `OPEN_SIGNUP=false` once your team has signed up, and `CORS_ORIGINS` to your frontend's origin. Run the API and the arq worker as separate processes against Postgres and Redis.

## Limitations

The video template is tuned for clean, high-contrast footage. For real broadcasts, set a manual playfield box on the *Detect board* step if auto-detection fails.

Databases created before migrations existed are adopted automatically only if their schema already matches; otherwise back them up and recreate them.

The first index sync downloads the ~65 MB embedding model (cached in `DATA_DIR/models`) and takes a few minutes; without it, the index falls back to hashed bag-of-words vectors.
