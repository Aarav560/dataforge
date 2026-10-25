"""Pre-indexed hub metadata and the semantic query cache.

The nightly sync stores popular datasets/models from Hugging Face, Kaggle and
OpenML with embeddings, so most searches are answered locally. The query cache
reuses results of near-identical earlier searches for a while.
"""

import asyncio
import logging
import re
import threading
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
from sqlalchemy import delete, func, select

from . import embeddings
from .config import Settings, get_settings
from .connectors import RawResult, build_connectors
from .connectors.base import parse_dt
from .db import SessionLocal
from .models import HubEntry, QueryCache

log = logging.getLogger(__name__)


def to_json(r: RawResult) -> dict:
    d = asdict(r)
    d["updated_at"] = r.updated_at.isoformat() if r.updated_at else None
    return d


def from_json(d: dict) -> RawResult:
    d = dict(d)
    d["updated_at"] = parse_dt(d.get("updated_at"))
    return RawResult(**d)


def _text(r: RawResult) -> str:
    # Title plus the start of the description carries most of the signal, and
    # short inputs keep embedding the whole catalog fast on a CPU.
    return " ".join(filter(None, [r.title.replace("/", " ").replace("-", " ").replace("_", " "), r.description or "",
                                   " ".join(t for t in r.tags if ":" not in t)]))[:400]


_STOP = {"the", "and", "for", "with", "data", "dataset", "datasets", "model", "models", "from", "of", "a", "an", "to", "in"}


def _lexical(query: str, texts: list[str]) -> np.ndarray:
    """Fraction of query words found as substrings, e.g. 'digits' in 'pendigits'."""
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) >= 3 and w not in _STOP]
    if not words:
        return np.zeros(len(texts), dtype=np.float32)
    return np.array([sum(w in t for w in words) / len(words) for t in texts], dtype=np.float32)


# Weight of the lexical bonus on top of cosine similarity. Embeddings of short
# dataset names are nearly flat, so exact word overlap breaks the ties.
LEXICAL_WEIGHT = 0.25


# ---- index ------------------------------------------------------------------

class _Matrix:
    """In-memory copy of the index embeddings, reloaded when the table changes."""

    def __init__(self):
        self.lock = threading.Lock()
        self.signature = None
        self.ids: dict[tuple[str, str], np.ndarray] = {}
        self.vecs: dict[tuple[str, str], np.ndarray] = {}
        self.texts: dict[tuple[str, str], list[str]] = {}

    def get(self, source: str, kind: str):
        model = embeddings.model_id()
        with SessionLocal() as db:
            sig = db.execute(select(func.count(HubEntry.id), func.max(HubEntry.synced_at))).one()
            sig = (sig[0], str(sig[1]), model)
            with self.lock:
                if sig != self.signature:
                    ids, vecs, texts = {}, {}, {}
                    for hid, src, knd, emb, data in db.execute(
                        select(HubEntry.id, HubEntry.source, HubEntry.kind, HubEntry.embedding, HubEntry.data)
                        .where(HubEntry.embedding_model == model)
                    ):
                        ids.setdefault((src, knd), []).append(hid)
                        vecs.setdefault((src, knd), []).append(emb)
                        texts.setdefault((src, knd), []).append(_text(from_json(data)).lower())
                    self.ids = {k: np.array(v) for k, v in ids.items()}
                    self.vecs = {k: np.array(v, dtype=np.float32) for k, v in vecs.items()}
                    self.texts = texts
                    self.signature = sig
            key = (source, kind)
            return self.ids.get(key), self.vecs.get(key), self.texts.get(key)


_matrix = _Matrix()


def indexed_sources() -> set[tuple[str, str]]:
    with SessionLocal() as db:
        return {(s, k) for s, k in db.execute(select(HubEntry.source, HubEntry.kind).distinct())}


def search_index(source: str, kind: str, query: str, limit: int, min_similarity: float) -> list[tuple[RawResult, float]]:
    ids, vecs, texts = _matrix.get(source, kind)
    if ids is None or not len(ids):
        return []
    q = embeddings.embed([query])[0]
    sims = vecs @ q + LEXICAL_WEIGHT * _lexical(query, texts)
    order = np.argsort(-sims)[:limit]
    chosen = [(int(ids[i]), float(sims[i])) for i in order if sims[i] >= min_similarity]
    if not chosen:
        return []
    with SessionLocal() as db:
        rows = {e.id: e for e in db.scalars(select(HubEntry).where(HubEntry.id.in_([c[0] for c in chosen])))}
    out = []
    for hid, sim in chosen:
        if hid in rows:
            r = from_json(rows[hid].data)
            r.meta = {**r.meta, "from_index": True, "index_similarity": round(sim, 3)}
            out.append((r, sim))
    return out


def upsert_entries(results: list[RawResult]) -> int:
    if not results:
        return 0
    vecs = embeddings.embed([_text(r) for r in results])
    model = embeddings.model_id()
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        existing = {
            (e.source, e.kind, e.external_id): e
            for e in db.scalars(
                select(HubEntry).where(
                    HubEntry.source.in_({r.source for r in results}),
                    HubEntry.external_id.in_([r.external_id for r in results]),
                )
            )
        }
        for r, v in zip(results, vecs):
            row = existing.get((r.source, r.kind, r.external_id))
            if row is None:
                row = HubEntry(source=r.source, kind=r.kind, external_id=r.external_id)
                db.add(row)
            row.data, row.embedding, row.embedding_model, row.synced_at = to_json(r), v.tolist(), model, now
        db.commit()
    return len(results)


async def _hf_pages(client: httpx.AsyncClient, endpoint: str, kind: str, total: int, conn) -> list[RawResult]:
    out: list[RawResult] = []
    url = f"https://huggingface.co/api/{endpoint}"
    params = {"limit": 1000, "full": "true", "sort": "downloads", "direction": -1}
    while url and len(out) < total:
        resp = await client.get(url, params=params, headers=conn.headers)
        resp.raise_for_status()
        out += [r for item in resp.json() if (r := conn._parse(item, kind)) is not None]
        url = resp.links.get("next", {}).get("url")
        params = None  # the next link carries the cursor and params
    return out[:total]


async def sync(settings: Settings | None = None, client: httpx.AsyncClient | None = None,
               sources: tuple[str, ...] = ("huggingface", "kaggle", "openml")) -> dict:
    """Refresh the index. Each source is synced independently; failures are reported, not fatal."""
    settings = settings or get_settings()
    own = client is None
    client = client or httpx.AsyncClient(timeout=60)
    report: dict[str, object] = {}
    try:
        conns = build_connectors(client, settings)
        n = settings.index_size_per_source
        jobs = []
        if "huggingface" in sources:
            jobs.append(("huggingface:dataset", _hf_pages(client, "datasets", "dataset", n, conns["huggingface"])))
            jobs.append(("huggingface:model", _hf_pages(client, "models", "model", n, conns["huggingface"])))
        if "kaggle" in sources:
            async def kaggle_pages():
                out: list[RawResult] = []
                for page in range(1, n // 20 + 2):
                    items = await conns["kaggle"].list_page(page)
                    if not items:
                        break
                    out += items
                    if len(out) >= n:
                        break
                return out[:n]
            jobs.append(("kaggle:dataset", kaggle_pages()))
        if "openml" in sources:
            async def openml_pages():
                out = []
                for offset in range(0, n, 1000):
                    page = await conns["openml"].list_page(offset, 1000)
                    out += page
                    if len(page) < 1000:
                        break
                return out[:n]
            jobs.append(("openml:dataset", openml_pages()))
        outcomes = await asyncio.gather(*(j[1] for j in jobs), return_exceptions=True)
        for (name, _), outcome in zip(jobs, outcomes):
            if isinstance(outcome, BaseException):
                log.warning("index sync %s failed: %s", name, outcome)
                report[name] = f"error: {outcome}"
            else:
                report[name] = await asyncio.to_thread(upsert_entries, outcome)
        report["embedding_model"] = embeddings.model_id()
        return report
    finally:
        if own:
            await client.aclose()


def stats() -> dict:
    with SessionLocal() as db:
        rows = db.execute(
            select(HubEntry.source, HubEntry.kind, func.count(HubEntry.id), func.max(HubEntry.synced_at))
            .group_by(HubEntry.source, HubEntry.kind)
        ).all()
        cache = db.scalar(select(func.count(QueryCache.id))) or 0
    return {
        "entries": [{"source": s, "kind": k, "count": c, "synced_at": t} for s, k, c, t in rows],
        "cached_queries": cache,
        "embedding_model": embeddings.model_id(),
    }


# ---- semantic query cache ---------------------------------------------------

def cache_get(source: str, kind: str, query: str, settings: Settings) -> list[RawResult] | None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.cache_ttl_hours)
    model = embeddings.model_id()
    with SessionLocal() as db:
        rows = db.scalars(
            select(QueryCache).where(
                QueryCache.source == source, QueryCache.kind == kind,
                QueryCache.embedding_model == model, QueryCache.created_at >= cutoff,
            ).order_by(QueryCache.created_at.desc()).limit(500)
        ).all()
        if not rows:
            return None
        exact = next((r for r in rows if r.query.strip().lower() == query.strip().lower()), None)
        if exact is None:
            q = embeddings.embed([query])[0]
            sims = np.array([r.embedding for r in rows], dtype=np.float32) @ q
            best = int(np.argmax(sims))
            if sims[best] < settings.cache_similarity:
                return None
            exact = rows[best]
        results = [from_json(d) for d in exact.results]
    for r in results:
        r.meta = {**r.meta, "from_cache": True}
    return results


def cache_put(source: str, kind: str, query: str, results: list[RawResult]) -> None:
    vec = embeddings.embed([query])[0]
    with SessionLocal() as db:
        db.add(QueryCache(source=source, kind=kind, query=query, embedding=vec.tolist(),
                          embedding_model=embeddings.model_id(), results=[to_json(r) for r in results]))
        db.commit()


def cache_prune(settings: Settings) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.cache_ttl_hours)
    with SessionLocal() as db:
        n = db.execute(delete(QueryCache).where(QueryCache.created_at < cutoff)).rowcount
        db.commit()
        return n or 0
