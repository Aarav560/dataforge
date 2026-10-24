# DataForge — ML Dataset Researcher

> Working name. A web app that turns a plain-language ML goal ("I want to train a Tetris AI") into a ready-to-train dataset by finding, vetting, gathering, transforming and merging data and models from across the internet.

---

## 1. Vision

Most ML projects stall at the data step. DataForge acts as a **research agent for data**: you describe what you want to build, and it works out what data you need, searches dozens of sources in parallel, ranks what it finds, and builds a clean, versioned dataset you can export straight into training.

It should also recognize when the internet isn't the best answer. Sometimes simulating or generating the data beats scraping it, and the tool should propose that.

---

## 2. Core User Flow

```
Goal  →  Plan  →  Search  →  Vet  →  Gather  →  Transform  →  Export
```

1. **Goal input** — user types a goal in the command bar.
2. **Planner** — an LLM breaks it into *data needs*.
   - Tetris example: board-state/action pairs, gameplay video, existing datasets, pretrained agents, a simulator/environment, research papers.
3. **Parallel search** — each need fans out to relevant source connectors at once.
4. **Ranking & vetting** — each result scored on relevance, size, format, license, freshness, quality.
5. **Gather** — user selects results; workers download them.
6. **Transform** — pipelines convert raw sources (video, HTML, CSVs) into a unified schema.
7. **Export** — versioned dataset in Parquet / HF Datasets / COCO / JSONL / etc., with an auto-generated dataset card.

---

## 3. Feature List

### 3.1 Goal Planner
- LLM decomposes goal → data needs → search queries per source.
- Shows the plan as an editable tree; user can add/remove needs before searching.
- Suggests the **ML approach** (supervised, RL, imitation learning) since that changes what data is needed.

### 3.2 Multi-Source Search
| Category | Sources |
|---|---|
| Datasets | Hugging Face Hub, Kaggle, OpenML, Zenodo, UCI, Papers With Code datasets, government open-data portals |
| Models | Hugging Face models, GitHub (repos with checkpoints), TF Hub, ONNX Model Zoo |
| Environments | Gymnasium, PettingZoo, open-source game clones on GitHub |
| Papers | arXiv, Semantic Scholar (extract "datasets used" and follow links) |
| Video | YouTube Data API + yt-dlp, Internet Archive |
| Images | Wikimedia Commons, Openverse, Flickr CC |
| General web | Search API (Brave / Exa / Tavily) → async crawler (Crawl4AI / Playwright) |
| Code | GitHub code search for scrapers, loaders, preprocessing scripts |

### 3.3 Ranking & Vetting
- **Relevance score** — embedding similarity + LLM judge against the data need.
- **Quality signals** — downloads, stars, citations, last updated, row/frame count, missing values.
- **License detection** — parse license fields + LLM read of README/ToS; classify as commercial OK / non-commercial / research only / unknown.
- **Duplicate detection** — flag the same dataset mirrored across hubs.

### 3.4 Synthetic Data Advisor
- Detects when simulation/generation is better than collection (games, physics, math, tabular).
- Generates the data itself or scaffolds the script.
- Tetris: spin up a Tetris engine, run heuristic/random agents for millions of games, log `(state, action, reward)`.

### 3.5 Pipeline Builder
Drag-and-drop node graph of transform steps:
- Download → Extract frames → Detect/crop → Label → Clean → Dedupe → Split → Export
- Reusable **templates**:
  - *Video → State/Action pairs* (gameplay footage)
  - *Web pages → Text corpus*
  - *Images → Labeled classification set*
  - *Multiple CSVs → Unified table*
- Auto-labeling with vision models / LLMs, with a human review queue.

### 3.6 Dataset Workspace
- Versioning (every pipeline run = new version, diffable).
- Stats panel: class balance, distributions, sample grid.
- Auto-generated **dataset card** (sources, licenses, schema, known biases).
- Export formats: Parquet, CSV, JSONL, HF Datasets (push to Hub), COCO, YOLO, TFRecord.

### 3.7 Project Memory
- Saved projects, search history, cached results.
- "Similar projects" — reuse plans from past goals.

---

## 4. Worked Example: Tetris AI

| Data need | Where DataForge looks | Output |
|---|---|---|
| Existing datasets | HF, Kaggle ("tetris", "tetromino") | Any logged games |
| Gameplay footage | YouTube (pro play, Classic Tetris World Championship) | Videos → frames |
| Environment | Gymnasium / GitHub Tetris gym envs | Simulator for RL |
| Pretrained agents | GitHub, HF models | Baselines / teacher policies |
| Papers | arXiv ("tetris reinforcement learning") | Approaches + dataset links |
| **Synthetic** | Generated via Tetris engine | Millions of `(state, action, reward)` rows |

**Video → dataset pipeline:**
1. Download gameplay video (yt-dlp).
2. Scene detection to drop menus/transitions (PySceneDetect).
3. Detect the board region (OpenCV / small detector).
4. Grid-sample cells → 10×20 binary matrix + current/next piece.
5. Diff consecutive states → infer action (move, rotate, drop).
6. Output `(state, next_piece, action)` → Parquet.

---

## 5. Architecture

```
┌──────────────────────────┐
│  Next.js Frontend        │  maximalist UI, WebSocket stream
└────────────┬─────────────┘
             │ REST + WS
┌────────────▼─────────────┐
│  FastAPI Gateway         │  auth, projects, planner, ranking
└────────────┬─────────────┘
             │ jobs
┌────────────▼─────────────┐
│  Redis Queue             │
└──┬─────────┬─────────┬───┘
   │         │         │
┌──▼──┐  ┌───▼───┐  ┌──▼────────┐
│Search│  │Gather │  │Transform  │   async workers (arq / Celery)
│workers│ │workers│  │workers(GPU)│
└──┬──┘  └───┬───┘  └──┬────────┘
   │         │         │
┌──▼─────────▼─────────▼───┐
│ Postgres + pgvector       │  metadata, embeddings, cache
│ S3 / Cloudflare R2        │  raw + processed files
└───────────────────────────┘
```

### Stack
| Layer | Choice |
|---|---|
| Frontend | Next.js (App Router), TypeScript, Tailwind, Framer Motion |
| UI libs | cmdk, React Flow, react-force-graph-3d, TanStack Table, Recharts/visx |
| Backend | FastAPI (Python — sits next to the ML tooling) |
| Queue / workers | Redis + arq (or Celery) |
| Database | Postgres + pgvector |
| Storage | S3 / Cloudflare R2 |
| Crawling | httpx (async), Crawl4AI, Playwright |
| Media | yt-dlp, ffmpeg, PySceneDetect, OpenCV |
| Data | Polars, PyArrow, DuckDB (fast previews of large files) |
| LLM | Planner, relevance judge, license reader, dataset-card writer |
| Auth | Clerk / Auth.js |

---

## 6. Making It Fast

"Parse the internet very fast" = **wide, parallel, streamed, cached** — not crawling everything.

- **Fan-out:** every source is an independent async worker; 10–20 run at once per query.
- **Stream results:** push each hit to the UI over WebSockets the moment it arrives — first results in ~1s.
- **Pre-index the big hubs:** nightly sync of HF + Kaggle + OpenML metadata into Postgres/pgvector, so most searches never hit the live API.
- **Semantic cache:** embed queries; near-duplicate searches return cached results instantly.
- **Rate-limit aware:** per-source token buckets, backoff, API-key rotation.
- **Lazy heavy work:** only download/process what the user selects; preview with DuckDB on remote Parquet/CSV without full download.

---

## 7. Maximalist UI

Dense, terminal-meets-mission-control layout. Dark theme, neon accents, monospace data, everything live.

### Panels
1. **Command Bar** (top) — cmdk palette; type goals or commands (`/search`, `/pipeline`, `/export`).
2. **Plan Tree** (left) — goal → data needs → queries; editable, with status per node.
3. **Live Crawl Feed** (left-bottom) — scrolling log: `[HF] 42 hits · [Kaggle] 17 hits · [YouTube] 128 videos…` with ticking counters.
4. **Source Graph** (center) — 3D force graph linking goal → needs → sources → datasets; nodes pulse as they're found.
5. **Result Wall** (center) — cards with thumbnail/preview, size, format, license badge, relevance bar, quality stars.
6. **Preview Pane** (right) — video scrubber, image grid, table sample, schema view.
7. **License Matrix** (right) — grid of selected sources × usage rights (commercial / redistribution / attribution).
8. **Pipeline Canvas** (tab) — React Flow node editor for transforms.
9. **Stats HUD** (bottom bar) — total rows, GB gathered, workers active, API quota left, ETA.

### Design notes
- Resizable, dockable panels (like an IDE / Bloomberg terminal).
- Micro-animations on every update; sound effects optional.
- Keyboard-first: every action reachable from the command bar.
- Still keep it readable: clear hierarchy inside the chaos, consistent color coding (green = usable, amber = check license, red = blocked).

---

## 8. Data Model (core tables)

| Table | Key fields |
|---|---|
| `projects` | id, user_id, goal, plan_json, created_at |
| `data_needs` | id, project_id, description, approach, status |
| `search_results` | id, need_id, source, url, title, size, format, license, relevance, quality, embedding |
| `assets` | id, result_id, storage_path, checksum, status |
| `pipelines` | id, project_id, graph_json, template |
| `dataset_versions` | id, project_id, version, schema, row_count, card_md, export_paths |

---

## 9. Legal & Ethics (build in from day one)

- Track license + ToS for every asset; block export of incompatible combinations.
- Respect `robots.txt` and source rate limits.
- YouTube/scraped media: default to **local, non-redistributable** use; warn clearly.
- PII detection pass on text/image datasets.
- Dataset card always lists sources and attribution.

---

## 10. Roadmap

### Phase 1 — MVP (core loop)
- [ ] Next.js + FastAPI skeleton, Postgres, Redis
- [ ] LLM planner (goal → needs → queries)
- [ ] Connectors: Hugging Face, Kaggle, GitHub
- [ ] Ranked results list with license badges
- [ ] Download selected datasets
- Plain UI

### Phase 2 — Media & synthetic
- [ ] YouTube search + yt-dlp gathering
- [ ] Video → frames → state/action pipeline template
- [ ] Synthetic Data Advisor + generators
- [ ] arXiv / Semantic Scholar connectors

### Phase 3 — Maximalist UI
- [ ] Dockable panel layout
- [ ] Live crawl feed over WebSockets
- [ ] 3D source graph
- [ ] Preview pane + License matrix
- [ ] Pipeline canvas (React Flow)

### Phase 4 — Dataset platform
- [ ] Versioning + diffs
- [ ] Auto dataset cards
- [ ] All export formats + push-to-HF
- [ ] Pre-indexed hub metadata + semantic cache
- [ ] Auto-labeling + human review queue

### Phase 5 — Power features
- [ ] Shared/team projects
- [ ] Plugin system for custom source connectors
- [ ] "Train a baseline" button (quick model on the built dataset)

---

## 11. Open Questions

- Solo or team build?
- Budget for paid search APIs (Brave / Exa / Tavily) and LLM calls?
- Local GPU available for vision/labeling pipelines, or cloud GPU?
- Single-user tool first, or multi-user SaaS from the start?
