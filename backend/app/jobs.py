"""Background jobs: fan-out search for a data need, and asset downloads.

These run either in an arq worker (when REDIS_URL is set) or in-process.
"""

import asyncio
import hashlib
import logging
import re
import shutil
from pathlib import Path

import httpx
from sqlalchemy import delete

from . import ranking
from .config import Settings, get_settings
from .connectors import Connector, RawResult, build_connectors
from .db import SessionLocal
from .licensing import classify_license
from .models import Asset, DataNeed, SearchResult

log = logging.getLogger(__name__)

USER_AGENT = "DataForge/0.1 (+https://github.com/aarav560/dataforge)"


def make_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0), headers={"User-Agent": USER_AGENT})


def _dedupe_key(title: str) -> str:
    name = title.rsplit("/", 1)[-1].lower()
    return re.sub(r"[^a-z0-9]", "", name)


async def search_need(
    need_id: int,
    *,
    client: httpx.AsyncClient | None = None,
    settings: Settings | None = None,
    connectors: dict[str, Connector] | None = None,
) -> None:
    settings = settings or get_settings()
    own_client = client is None
    client = client or make_http_client()
    try:
        connectors = connectors or build_connectors(client, settings)
        with SessionLocal() as db:
            need = db.get(DataNeed, need_id)
            if need is None:
                return
            need.status, need.error = "searching", None
            db.commit()
            description, kind, queries = need.description, need.kind, dict(need.queries or {})

        calls = []
        for source, source_queries in queries.items():
            conn = connectors.get(source)
            if conn is None or kind not in conn.kinds:
                continue
            for q in source_queries:
                calls.append((source, q, conn.search(q, kind, settings.results_per_query)))

        outcomes = await asyncio.gather(*(c[2] for c in calls), return_exceptions=True)

        best: dict[tuple[str, str], tuple[RawResult, float, float, float, str]] = {}
        errors: list[str] = []
        for (source, query, _), outcome in zip(calls, outcomes):
            if isinstance(outcome, BaseException):
                log.warning("search %s %r failed: %s", source, query, outcome)
                errors.append(f"{source} ({query}): {outcome}")
                continue
            for raw in outcome:
                lic = classify_license(raw.license)
                rel = ranking.relevance(raw, description, query)
                qual = ranking.quality(raw, lic)
                score = ranking.combined(rel, qual)
                key = (raw.source, raw.external_id)
                if key not in best or best[key][3] < score:
                    best[key] = (raw, rel, qual, score, lic)

        # Flag likely mirrors: same repo name on different hubs.
        by_name: dict[str, list[tuple[str, str]]] = {}
        for key, (raw, *_rest) in best.items():
            by_name.setdefault(_dedupe_key(raw.title), []).append(key)

        with SessionLocal() as db:
            need = db.get(DataNeed, need_id)
            if need is None:
                return
            db.execute(delete(SearchResult).where(SearchResult.need_id == need_id))
            for key, (raw, rel, qual, score, lic) in best.items():
                mirrors = [
                    f"{s}:{e}" for s, e in by_name.get(_dedupe_key(raw.title), []) if s != raw.source
                ]
                meta = dict(raw.meta)
                meta.update(downloads=raw.downloads, likes=raw.likes, usability=raw.usability, tags=raw.tags[:20])
                if mirrors:
                    meta["possible_mirrors"] = mirrors
                db.add(
                    SearchResult(
                        need_id=need_id,
                        source=raw.source,
                        kind=raw.kind,
                        external_id=raw.external_id,
                        url=raw.url,
                        title=raw.title,
                        description=raw.description,
                        size_bytes=raw.size_bytes,
                        format=raw.format,
                        license=raw.license,
                        license_class=lic,
                        relevance=rel,
                        quality=qual,
                        score=score,
                        meta=meta,
                        updated_at=raw.updated_at,
                    )
                )
            if calls and len(errors) == len(calls):
                need.status = "error"
            else:
                need.status = "done"
            need.error = "\n".join(errors) or None
            db.commit()
    except Exception as exc:
        log.exception("search_need %s crashed", need_id)
        with SessionLocal() as db:
            need = db.get(DataNeed, need_id)
            if need is not None:
                need.status, need.error = "error", str(exc)
                db.commit()
    finally:
        if own_client:
            await client.aclose()


def _checksum(path: Path) -> str:
    h = hashlib.sha256()
    files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
    for f in files:
        h.update(str(f.relative_to(path) if path.is_dir() else f.name).encode())
        with f.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def _dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


async def _report_progress(asset_id: int, dest: Path, interval: float = 2.0) -> None:
    while True:
        await asyncio.sleep(interval)
        size = await asyncio.to_thread(_dir_size, dest)
        with SessionLocal() as db:
            asset = db.get(Asset, asset_id)
            if asset is None or asset.status != "downloading":
                return
            asset.bytes_downloaded = size
            db.commit()


async def download_asset(
    asset_id: int,
    *,
    client: httpx.AsyncClient | None = None,
    settings: Settings | None = None,
    connectors: dict[str, Connector] | None = None,
) -> None:
    settings = settings or get_settings()
    own_client = client is None
    client = client or make_http_client()
    dest = settings.data_dir / "assets" / str(asset_id)
    try:
        connectors = connectors or build_connectors(client, settings)
        with SessionLocal() as db:
            asset = db.get(Asset, asset_id)
            if asset is None:
                return
            result = asset.result
            source, external_id, kind = result.source, result.external_id, result.kind
            asset.status, asset.error, asset.storage_path = "downloading", None, str(dest)
            db.commit()

        conn = connectors[source]
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        progress = asyncio.create_task(_report_progress(asset_id, dest))
        try:
            written = await conn.download(external_id, kind, dest, settings.max_download_bytes)
        finally:
            progress.cancel()
        checksum = await asyncio.to_thread(_checksum, dest)

        with SessionLocal() as db:
            asset = db.get(Asset, asset_id)
            asset.status, asset.bytes_downloaded, asset.checksum = "done", written, checksum
            db.commit()
    except Exception as exc:
        log.warning("download_asset %s failed: %s", asset_id, exc)
        shutil.rmtree(dest, ignore_errors=True)
        with SessionLocal() as db:
            asset = db.get(Asset, asset_id)
            if asset is not None:
                asset.status, asset.error, asset.storage_path = "error", str(exc), None
                db.commit()
    finally:
        if own_client:
            await client.aclose()
