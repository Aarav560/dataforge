"""Text embeddings for the hub index and semantic query cache.

Uses a small local ONNX model (BAAI/bge-small-en-v1.5 via fastembed). If the
model can't be loaded (offline, no wheel), falls back to a hashed
bag-of-words vector so everything keeps working with lexical similarity.
"""

import hashlib
import logging
import os
import re
import threading

import numpy as np

log = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-small-en-v1.5"
HASH_DIM = 384
_model = None
_model_failed = False
_lock = threading.Lock()


def _load():
    global _model, _model_failed
    with _lock:
        if os.environ.get("DATAFORGE_EMBEDDINGS") == "hashing":
            _model_failed = True
        if _model is None and not _model_failed:
            try:
                from fastembed import TextEmbedding

                from .config import get_settings

                cache = get_settings().data_dir / "models"
                cache.mkdir(parents=True, exist_ok=True)
                _model = TextEmbedding(MODEL_NAME, cache_dir=str(cache))
            except Exception as exc:  # no network, unsupported platform, ...
                log.warning("embedding model unavailable (%s); using hashed bag-of-words", exc)
                _model_failed = True
    return _model


def backend() -> str:
    return "fastembed" if _load() is not None else "hashing"


def _hashed(texts: list[str]) -> np.ndarray:
    out = np.zeros((len(texts), HASH_DIM), dtype=np.float32)
    for i, text in enumerate(texts):
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:4], "little")
            out[i, h % HASH_DIM] += 1.0 if h & 1 << 31 else -1.0
    return out


def embed(texts: list[str]) -> np.ndarray:
    """Unit-normalized embeddings, shape (len(texts), dim)."""
    if not texts:
        return np.zeros((0, HASH_DIM), dtype=np.float32)
    model = _load()
    vecs = np.array(list(model.embed(texts)), dtype=np.float32) if model is not None else _hashed(texts)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.where(norms == 0, 1, norms)


def model_id() -> str:
    """Identifies the vector space, so vectors from different backends never mix."""
    return MODEL_NAME if _load() is not None else f"hashing-{HASH_DIM}"


def default_threshold() -> float:
    """Similarity above which an index hit counts as relevant.

    Applied to cosine plus the index's lexical bonus. bge-small scores
    unrelated English text around 0.5-0.7, while hashed bag-of-words vectors
    only overlap on shared words.
    """
    return 0.75 if _load() is not None else 0.3
