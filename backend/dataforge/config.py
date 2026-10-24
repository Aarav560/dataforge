"""Runtime configuration. Everything here is free/local by design — no paid APIs."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("DATAFORGE_HOME", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = ROOT / "dataforge.sqlite3"
STORAGE = ROOT / "storage"  # local stand-in for S3/R2
RAW_DIR = STORAGE / "raw"
DATASETS_DIR = STORAGE / "datasets"

for _d in (ROOT, STORAGE, RAW_DIR, DATASETS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Local LLM via Ollama (free). Falls back to a rule-based planner when unreachable.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "")  # empty = first installed model
LLM_TIMEOUT = float(os.environ.get("DATAFORGE_LLM_TIMEOUT", "90"))

# Optional free credentials. Every connector works (or degrades gracefully) without them.
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")  # raises GitHub rate limits; free
HF_TOKEN = os.environ.get("HF_TOKEN", "")  # only needed for gated datasets / push-to-hub
KAGGLE_USERNAME = os.environ.get("KAGGLE_USERNAME", "")
KAGGLE_KEY = os.environ.get("KAGGLE_KEY", "")

USER_AGENT = "DataForge/0.1 (ML dataset research tool; +https://localhost)"
HTTP_TIMEOUT = float(os.environ.get("DATAFORGE_HTTP_TIMEOUT", "20"))
MAX_DOWNLOAD_BYTES = int(os.environ.get("DATAFORGE_MAX_DOWNLOAD_MB", "2048")) * 1024 * 1024
SEARCH_CACHE_TTL = int(os.environ.get("DATAFORGE_CACHE_TTL", str(6 * 3600)))
RESULTS_PER_SOURCE = int(os.environ.get("DATAFORGE_RESULTS_PER_SOURCE", "12"))

CORS_ORIGINS = os.environ.get("DATAFORGE_CORS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
