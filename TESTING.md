# Testing DataForge

How to run DataForge yourself, check that each stage works, and report what breaks. What's still unfinished is tracked in [ROADMAP.md](ROADMAP.md).

- **Test 1 — Train a chess engine** (start here): the full flow on a new goal.
- **Test 2 — Plant disease classifier:** large image datasets, COCO/YOLO export.
- **Test 3 — Tetris AI:** synthetic data generation and the video pipeline.
- **Automated tests:** run before every commit.

Mark each step ✅ / ❌ as you go. A ❌ is useful: file it with the template at the bottom.

---

## 0. Setup (once)

You need Python 3.12, Node 20+, and optionally [Ollama](https://ollama.com) for LLM planning (everything works without it).

```bash
# backend
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # macOS/Linux: .venv/bin/pip
.venv/Scripts/python -X utf8 -m uvicorn dataforge.main:app --port 8000
```

```bash
# frontend (second terminal)
cd frontend
npm install
npm run dev
```

```bash
# optional: local LLM for planning
ollama pull llama3.2
```

Open http://localhost:3000.

**Smoke check**
- [ ] The home page shows "Say what you want to train." and a list of 17 sources.
- [ ] The LLM chip next to "Plan with the local LLM" shows your model name, or "Ollama not running · built-in rules".
- [ ] The bottom bar says "Free APIs only".

---

## Test 1 — Train a chess engine

**Why this test:** chess is a goal DataForge has never been tuned for. It exercises planning, search across every source, licence checks, downloading Parquet from Hugging Face, merging mismatched tables, and export. It also hits several known gaps on purpose (marked **Known gap**), so you can confirm they still exist or notice when they're fixed.

**Time:** about 15 minutes. **Download size:** about 5 MB.

### 1.1 Plan

1. On the home page, type `Train a chess engine` and click **Plan dataset**.
   - [ ] Within about 2 minutes you land on stage **01 Plan**.
   - [ ] Approach is **reinforcement learning**; alternatives include imitation learning and "supervised (state → best move)".
   - [ ] There are 6 needs: dataset, environment, video, model, paper, code.
   - [ ] An orange box says **Simulation fits this goal**.
   - **Known gap:** with a cold Ollama, planning can take 90+ s and fall back to rules (the left column says "planned by built-in rules"). That's expected for now — note how long it took.
2. Look at the queries. Rule-based plans produce clumsy ones like `chess engine gameplay`. Fix them to test plan editing:
   - In **Existing labeled datasets**, remove `chess engine dataset` (×) and add `chess positions` and `lichess` (type, press Enter).
   - In **Gameplay / demonstration footage**, switch the need **off** with its toggle.
   - [ ] The counts under the Search button update (needs, sources, queries).
   - [ ] Reload the page — your edits are still there.

### 1.2 Search

3. Click **Search N sources →**.
   - [ ] Stage **02 Search** opens and results stream in. The stage bar shows an orange progress bar and a percentage.
   - [ ] It finishes in about a minute with 150–250 results.
   - [ ] Click **Activity** (top right): per-source hit counts are shown. Semantic Scholar usually shows a rate-limit warning — expected; OpenAlex covers papers.
4. Check the ranking makes sense:
   - [ ] **Datasets** filter: chess datasets from Kaggle / Hugging Face are at the top (e.g. *Chess Engine*, *Chess-Nut-Engine/chess-sft-…*).
   - [ ] **Environments** filter: `chess-gym`, `python-chess` and `Stockfish` appear.
   - [ ] Left column → **Licence → Local use only**: only YouTube results.
   - [ ] **Map** view renders the 3D graph; clicking a dot opens its preview.
5. Click the result **Chess-Nut-Engine/chess-sft-eval** (filter box: `sft-eval`).
   - [ ] The preview drawer opens with licence rights, size, score bars.
   - [ ] A **Sample rows** table loads without downloading (columns like `fen`, `prompt`, `gold_answer`).

### 1.3 Gather

6. Tick **Chess-Nut-Engine/chess-sft-eval** and the environment **python-chess**, then click **Review & download →**.
   - [ ] **03 Gather** lists both with licence pills.
   - [ ] **Licence check** → switch **Research / Commercial**: both stay "Can be exported" (python-chess is GPL = share-alike, still commercial-OK).
7. Click **Download 2**.
   - [ ] Both appear under **Downloaded** with a ✓ in under a minute (~5 MB, 19 Parquet files).
   - [ ] python-chess shows `pip install chess` as its install note (environments are installed, not downloaded).

### 1.4 Transform

8. Open **04 Transform**, pick **Multiple CSVs → Unified table**, click **Run pipeline**.
   - [ ] Each step gets a ✓ and a note.
   - [ ] **Unify schema** says something like "unified 19 tables into N schema group(s) … other schema group(s) set aside".
   - **Known gap:** the 19 files have different columns. DataForge keeps the *largest* group — the benchmark prompts (`prompt`, `gold_answer`) — not the engine-style `eval_evaluation` table (`fen`, `best_move`, `cp`). To get that one, click **Unify schema**, set `mode` to `all`, run again, and confirm you get a wider table with a `cp` column. Record which one you'd want by default.

### 1.5 Dataset

9. Open **06 Dataset**.
   - [ ] **v1** exists with row/column counts, column cards and a "How this version was built" list.
   - [ ] **Dataset card** tab lists both sources with licences.
   - [ ] If you ran unify twice: **Compare with → v1** shows columns added and rows added.
10. **Export** → **Parquet** → **Export**, then **Download .zip**.
    - [ ] The zip has `train/validation/test.parquet` and a `README.md` card.
11. **Quick baseline** → Predict: `task_type` (or `cp` if you ran `mode=all`) → **Train**.
    - **Known gap:** there's no chess board encoder yet, so the baseline only sees simple columns. Expect a result for `task_type`, and a low or failed result for anything that needs the board (`fen`). Record what you got.

### 1.6 Synthesize

12. Open **05 Synthesize**.
    - [ ] Fit score is about 80 and says generating beats collecting.
    - **Known gap:** there's no chess generator yet — the generator list shows only Tetris / arithmetic / maze, with nothing pre-selected. The starter script is generic Gymnasium code.

**Test 1 passes** if every unmarked box is ✅. Note anything that felt confusing — that counts as a finding too.

---

## Test 2 — Plant disease classifier (images)

Goal: `Detect plant diseases from leaf photos`. Heavy: ~2.3 GB of downloads, ~5 GB for exports.

1. Plan → Search. [ ] Top datasets are plant-disease sets from Zenodo / Hugging Face / Kaggle.
2. Select Zenodo **tomato-leaf-diseases-detection** (192 MB) and HF **virajwirle/Plant-disease-datasets**; download.
   - [ ] The HF set downloads real `.jpg` files, not just Parquet (its Parquet only references images).
   - [ ] If it hits the 2 GB size cap, it's marked partial and the finished files are kept.
3. Transform → **Images → Labeled classification set**. Click **Rename labels** and set mapping:
   `Healthy=Maize · healthy; MLN=Maize · lethal necrosis; MSV=Maize · streak virus; NLB=Maize · northern leaf blight; NLS=Maize · northern leaf spot`. Run.
   - [ ] ~13,700 images, 15 labels, ~150 duplicates removed, note says "kept … source-held-out rows".
4. Dataset → sample grid shows real leaf photos from several classes; export **COCO** and **YOLO**.
   - [ ] YOLO zip has `train/ val/ test/<class>/` folders and `classes.txt`.
5. Quick baseline (auto-detect) → [ ] accuracy far above the majority baseline (we measured 78.6%).
6. Run the pipeline again → [ ] it takes ~1–2 min (archive reused) and **Compare with v1** shows changed values, not "everything replaced".

## Test 3 — Tetris AI (synthetic + video)

Goal: `I want to train a Tetris AI`.

1. Synthesize → Tetris engine self-play, rows 20000 → **Generate dataset**.
   - [ ] Activity shows progress; a new version appears with sample boards drawn as grids.
   - [ ] Quick baseline predicting `column` beats the majority baseline.
2. Search → select one YouTube gameplay video → download.
   - [ ] It downloads an `.mp4` and is labelled **Local use only**; a commercial export is blocked.
3. Transform → **Video → State/Action pairs** → run.
   - [ ] Frames are extracted. On real footage the board may not be found — that's a known limitation; `board_bbox` can be set on the **Board → grid state** step.

---

## Automated tests

Run before every commit (CI runs the same on every PR):

```bash
cd backend && .venv/Scripts/python -X utf8 -m pytest tests -q
```

```bash
cd frontend && npx tsc --noEmit && npx eslint .
```

Expect **36 passed**. The suite is offline (no network, no Ollama). It includes a ground-truth test that renders Tetris games to MP4 and checks the video pipeline recovers ≥ 90% of moves.

**When you fix a bug, add a test for it** in `backend/tests/` — see `test_images.py` for examples of tests built from real bugs.

---

## Reporting a problem

Open a GitHub issue with:

```
Test / step:      e.g. Test 1 · 1.4 Transform, step 8
What I did:
What I expected:
What happened:     (paste the error text; screenshot if it's visual)
Activity log:      (click Activity → copy the last ~10 lines)
Backend log:       (terminal running uvicorn → any traceback)
Setup:             OS, Python/Node version, Ollama model or "none"
```
