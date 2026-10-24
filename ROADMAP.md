# DataForge — status and what's left

Built against [DataForge_Plan.md](DataForge_Plan.md). How to test: [TESTING.md](TESTING.md).

## Where things stand

| Plan phase | Status | Notes |
|---|---|---|
| 1. MVP core loop | ✅ Done | FastAPI + SQLite, local-LLM planner with rules fallback, 17 free connectors, ranking, licence badges, downloads |
| 2. Media & synthetic | ✅ Done | YouTube (yt-dlp), video → state/action pipeline (verified on rendered Tetris), Synthetic Data Advisor, Tetris / arithmetic / maze generators, arXiv + OpenAlex + Semantic Scholar |
| 3. Rich UI | ✅ Done | Stage-based workspace, live activity over WebSocket, 3D source map, preview drawer, licence matrix, pipeline canvas |
| 4. Dataset platform | ✅ Mostly | Versions + content-keyed diffs, dataset cards, Parquet/CSV/JSONL/HF/COCO/YOLO/TFRecord exports, auto-labelling + review queue. Missing: pre-indexed HF/Kaggle metadata, embedding-based semantic cache |
| 5. Power features | 🟡 Partial | "Train a baseline" done. Missing: team projects, plugin loading |

Verified end to end on real data: **plant disease** (13,775 images, 15 classes, COCO + YOLO export, 78.6% baseline) and **Tetris** (synthetic self-play + rendered video at 119/120 moves recovered). 36 automated tests pass.

## What's left

Ordered by what most blocks real use. Each item names the file(s) involved.

### P0 — needed before calling it finished

- [ ] **Chess has no generator.** The advisor recommends simulation for chess (score 0.8) but has nothing to run. Add a `chess` generator using `python-chess` (pip, free): self-play or random positions labelled by a material/mobility heuristic, with an optional Stockfish hook if the user installs it. → `backend/dataforge/synthetic/generators.py`, `advisor.py`
- [ ] **Chess-aware baseline.** FEN strings and move lists aren't featurized, so the quick baseline can't check a chess dataset. Add a FEN → 8×8×12 board encoder. → `backend/dataforge/baseline.py`
- [ ] **LLM planning times out on cold start.** The first plan after Ollama starts takes 60–120 s and silently falls back to rules (seen in the chess test). Warm the model at backend startup, raise the timeout, and show "planned by rules (LLM timed out)" in the UI. → `backend/dataforge/llm.py`, `main.py`, `frontend/components/PlanTree.tsx`
- [ ] **Rule-based queries are clumsy for multi-word subjects** ("chess engine gameplay"). Strip tool words (engine, bot, model, AI) from the subject, or have the domain templates use the core noun. → `backend/dataforge/planner.py`
- [ ] **PGN / compressed archives aren't handled.** Chess data is mostly `.pgn` / `.pgn.zst` (Lichess). Add `.zst` / `.gz` / `.tar` extraction and a "PGN → positions" pipeline step. → `backend/dataforge/pipelines/nodes.py`
- [ ] **Running work doesn't survive a restart.** Jobs run in-process; a restart marks them "interrupted". Either resume downloads (`.part` files + HTTP Range) or document clearly. → `backend/dataforge/events.py`, `gather.py`

### P1 — reliability & speed

- [ ] Background-thread steps call `job.log` → asyncio queue from another thread; switch to `loop.call_soon_threadsafe`. → `events.py`
- [ ] Downloads: resume with HTTP Range; retry individual files; show per-asset progress in Gather.
- [ ] Hugging Face image downloads run ~4–5 files/s; batch via `huggingface_hub.snapshot_download` with `allow_patterns`.
- [ ] Exports duplicate data on disk (~2.3 GB per image export). Add "Delete export" and an option to export a manifest instead of copying images.
- [ ] TFRecord CRC is pure Python — slow on big sets; use `crc32c` wheel when available.
- [ ] Image hashing reads every file twice (decode + md5); merge into one pass.
- [ ] Semantic Scholar anonymous pool is usually rate-limited; back off and stop querying it for the session after the first 429.

### P2 — plan items not built yet

- [ ] Pre-indexed hub metadata (nightly HF + Kaggle sync) and embedding-based semantic cache (plan §6).
- [ ] Vision auto-labelling (only text auto-labelling exists; needs a free local vision model).
- [ ] Auth + shared/team projects (plan §5, §10 phase 5) — currently single-user local.
- [ ] Plugin loading for custom connectors (the `register()` hook exists; no discovery/UI).
- [ ] Gymnasium environment rollouts as a built-in generator (currently a copy-paste script).

### P3 — polish

- [ ] Frontend: 39 lint warnings (`any` on backend JSON) — add response types.
- [ ] Pipeline canvas: undo, step reordering, per-step output preview.
- [ ] Dataset page: delete versions, rename, pin a "release" version.
- [ ] README screenshots.

## Known limitations (by design for now)

- Free sources only. Kaggle *downloads* need a free Kaggle API key; GitHub search is capped at 10/min without a free token.
- YouTube media is marked local-only and blocks commercial export.
- Video → state/action is heuristic; real broadcast footage may need `board_bbox` set by hand.
