# DataForge — ML Dataset Researcher

Type an ML goal ("I want to train a Tetris AI"). DataForge plans the data you need, searches 17 free sources in parallel, vets licences and builds a versioned dataset that's ready to train on. When simulating the data beats scraping it, DataForge generates it instead. Built from [DataForge_Plan.md](DataForge_Plan.md).

**Testing it?** Start with [TESTING.md](TESTING.md) — test 1 walks through training a chess engine. What's unfinished is in [ROADMAP.md](ROADMAP.md).

**$0 to run.** There are no paid APIs. Planning runs on a local LLM through [Ollama](https://ollama.com), and every source uses a free public endpoint.

## Run it

```bash
# 1. backend (Python 3.12)
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # macOS/Linux: .venv/bin/pip
.venv/Scripts/python -X utf8 -m uvicorn dataforge.main:app --port 8000

# 2. frontend (Node 20+)
cd frontend
npm install
npm run dev                                         # → http://localhost:3000
```

Optional: run `ollama serve` with any chat model pulled (e.g. `ollama pull llama3.2`). Without it, the planner falls back to built-in rules and everything else still works.

Tests: `cd backend && .venv/Scripts/python -m pytest tests -q`. There are 36 tests, including a ground-truth test that renders Tetris games to MP4 and checks that the video → state/action pipeline recovers the moves.

## Flow

`Goal → Plan → Search → Vet → Gather → Transform → Export`

| Step | What happens |
|---|---|
| Plan | A local LLM plus rules turn the goal into an ML approach and data needs, each with search queries per source. The plan tree is editable. |
| Search | Every (need × source × query) runs at once with per-host token buckets. Results stream to the UI over a WebSocket and are cached on disk. |
| Vet | Each result gets a relevance score (TF-IDF, with an optional local-LLM judge), a quality score (popularity, recency, completeness) and a licence class. Mirrored copies across hubs are flagged as duplicates. |
| Gather | Selected results download in parallel under a size cap: HF auto-Parquet, OpenML, UCI, Zenodo, Kaggle, GitHub zips, arXiv PDFs, archive.org, YouTube (yt-dlp, video-only stream) and web pages (robots.txt respected). |
| Transform | A React Flow node graph runs the steps. Templates: *CSVs → unified table*, *Video → state/action*, *Web → text corpus*, *Images → labelled set*. Extra steps: PII scan and local-LLM auto-label with a human review queue. |
| Synthetic | The advisor scores how well simulation fits the goal. Built-in generators: Tetris self-play (bitboard engine, ~1.5k rows/s), arithmetic and grid mazes. For other domains it scaffolds a script. |
| Export | Every run creates a diffable version with stats and an auto-generated dataset card. Formats: Parquet, CSV, JSONL, HF (with optional push), COCO, YOLO and TFRecord (no TensorFlow needed). The licence check blocks incompatible exports. A "train a baseline" button is included. |

## Sources (all free)

Hugging Face datasets/models · Kaggle (listing works without a key; downloads need a free key) · OpenML · UCI · Zenodo · GitHub repos/code · RL environment catalog (Gymnasium, ALE, PettingZoo, …) · arXiv · OpenAlex · Semantic Scholar · YouTube via yt-dlp · Internet Archive · Openverse (incl. Flickr CC) · Wikimedia Commons · DuckDuckGo web search.

Optional free keys go in environment variables: `GITHUB_TOKEN` (raises the 10 searches/min limit), `KAGGLE_USERNAME` + `KAGGLE_KEY` (downloads), `HF_TOKEN` (gated sets, push-to-hub).

## Deviations from the plan (to stay free and local)

| Plan | Built | Why |
|---|---|---|
| Postgres + pgvector | SQLite (same tables as plan §8) | no server to run |
| Redis + arq workers | in-process asyncio jobs + event bus | no Docker; single user |
| S3 / R2 | local `backend/data/storage` | free |
| Paid LLM | Ollama (local) + rule fallback | no paid APIs |
| Brave / Exa / Tavily | DuckDuckGo (`ddgs`) | free |
| YouTube Data API | yt-dlp `ytsearch` | no key |
| Papers With Code | OpenAlex | PwC was shut down |
| Embedding semantic cache | token-normalized query cache | no embedding API needed |
| Auth (Clerk), team projects | not built | single-user local tool first (plan §11) |

## Known limitations

- Video → state/action is heuristic. It is verified on rendered footage, but real broadcasts with overlays may need `board_bbox` set in the pipeline node.
- Semantic Scholar's anonymous pool is often rate-limited; OpenAlex covers papers when that happens.
- Some HF datasets have no auto-Parquet; preview then fails and gather falls back to raw files (max 1,000 per asset).
- TFRecord export uses pure-Python CRC32C, so it is slow on very large datasets.
