"""Background jobs: fan-out search for a data need, and asset downloads.

These run either in an arq worker (when REDIS_URL is set) or in-process.
"""

import asyncio
import hashlib
import logging
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import delete, select

from . import embeddings, events, hubindex, quota, ranking
from .config import Settings, get_settings
from .connectors import Connector, LinkRef, RawResult, build_connectors
from .db import SessionLocal
from .licensing import classify_license
from .models import Asset, DataNeed, DatasetVersion, SearchResult

log = logging.getLogger(__name__)

USER_AGENT = "DataForge/0.1 (+https://github.com/aarav560/dataforge)"


def make_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, read=120.0),
        headers={"User-Agent": USER_AGENT},
        event_hooks={"response": [quota.record]},
    )


async def _follow_paper_links(best: dict, connectors: dict[str, Connector], description: str, limit: int) -> list[str]:
    """Resolve dataset/model/code links found in paper abstracts into results."""
    papers = sorted((v for v in best.values() if v[0].kind == "paper"), key=lambda v: -v[3])
    refs: dict[tuple[str, str], tuple[LinkRef, RawResult, float]] = {}
    for raw, rel, *_ in papers:
        for link in raw.meta.get("links") or []:
            ref = LinkRef(**link)
            key = (ref.source, ref.external_id.lower())
            if key not in refs and ref.source in connectors and (ref.source, ref.external_id) not in best:
                refs[key] = (ref, raw, rel)
    chosen = list(refs.values())[:limit]
    outcomes = await asyncio.gather(*(connectors[r.source].resolve(r) for r, _, _ in chosen), return_exceptions=True)
    errors = []
    for (ref, paper, paper_rel), found in zip(chosen, outcomes):
        if isinstance(found, BaseException):
            errors.append(f"{ref.source} link {ref.external_id} from paper: {found}")
            continue
        if found is None:
            continue
        found.meta["via_paper"] = {"source": paper.source, "id": paper.external_id, "title": paper.title}
        lic = classify_license(found.license)
        # Being cited by a relevant paper is itself a relevance signal.
        rel = max(ranking.relevance(found, description, ""), round(0.8 * paper_rel, 4))
        qual = ranking.quality(found, lic)
        best[(found.source, found.external_id)] = (found, rel, qual, ranking.combined(rel, qual), lic)
    return errors


def _dedupe_key(title: str) -> str:
    name = title.rsplit("/", 1)[-1].lower()
    return re.sub(r"[^a-z0-9]", "", name)


def _score(raw: RawResult, description: str, query: str):
    lic = classify_license(raw.license)
    rel = ranking.relevance(raw, description, query)
    qual = ranking.quality(raw, lic)
    return (raw, rel, qual, ranking.combined(rel, qual), lic)


def _merge(best: dict, scored: list) -> list[tuple[str, str]]:
    """Keep the best-scoring copy of each result; return keys that changed."""
    changed = []
    for entry in scored:
        raw = entry[0]
        key = (raw.source, raw.external_id)
        if key not in best or best[key][3] < entry[3]:
            best[key] = entry
            changed.append(key)
    return changed


def _save_results(need_id: int, entries: list) -> list[int]:
    """Upsert scored results for a need; returns their row ids."""
    if not entries:
        return []
    with SessionLocal() as db:
        existing = {
            (r.source, r.external_id): r
            for r in db.scalars(select(SearchResult).where(SearchResult.need_id == need_id)).all()
        }
        rows = []
        for raw, rel, qual, score, lic in entries:
            meta = dict(raw.meta)
            meta.update(downloads=raw.downloads, likes=raw.likes, usability=raw.usability, tags=raw.tags[:20])
            values = dict(
                kind=raw.kind, url=raw.url, title=raw.title, description=raw.description,
                size_bytes=raw.size_bytes, format=raw.format, license=raw.license, license_class=lic,
                relevance=rel, quality=qual, score=score, meta=meta, updated_at=raw.updated_at,
            )
            row = existing.get((raw.source, raw.external_id))
            if row is None:
                row = SearchResult(need_id=need_id, source=raw.source, external_id=raw.external_id, **values)
                db.add(row)
            else:
                for k, v in values.items():
                    setattr(row, k, v)
            rows.append(row)
        db.commit()
        return [r.id for r in rows]


def _flag_mirrors(need_id: int) -> None:
    """Mark likely mirrors: the same repo name on different hubs."""
    with SessionLocal() as db:
        results = db.scalars(select(SearchResult).where(SearchResult.need_id == need_id)).all()
        by_name: dict[str, list[SearchResult]] = {}
        for r in results:
            by_name.setdefault(_dedupe_key(r.title), []).append(r)
        for r in results:
            mirrors = [f"{o.source}:{o.external_id}" for o in by_name[_dedupe_key(r.title)] if o.source != r.source]
            if mirrors != (r.meta or {}).get("possible_mirrors", []):
                meta = dict(r.meta or {})
                if mirrors:
                    meta["possible_mirrors"] = mirrors
                else:
                    meta.pop("possible_mirrors", None)
                r.meta = meta
        db.commit()


async def search_need(
    need_id: int,
    *,
    client: httpx.AsyncClient | None = None,
    settings: Settings | None = None,
    connectors: dict[str, Connector] | None = None,
) -> None:
    """Fan a need's queries out to every source, saving and announcing hits as each source answers."""
    settings = settings or get_settings()
    own_client = client is None
    client = client or make_http_client()
    project_id = None
    try:
        connectors = connectors or build_connectors(client, settings)
        with SessionLocal() as db:
            need = db.get(DataNeed, need_id)
            if need is None:
                return
            project_id = need.project_id
            need.status, need.error = "searching", None
            db.execute(delete(SearchResult).where(SearchResult.need_id == need_id))
            db.commit()
            description, kind, queries = need.description, need.kind, dict(need.queries or {})

        indexed = await asyncio.to_thread(hubindex.indexed_sources)

        async def one(source: str, query: str, conn: Connector):
            """Answer from the semantic cache, then the hub index, then the live API."""
            started = time.monotonic()
            try:
                cached = await asyncio.to_thread(hubindex.cache_get, source, kind, query, settings)
                if cached is not None:
                    return source, query, cached, time.monotonic() - started, "cache"
                if settings.index_first and (source, kind) in indexed:
                    hits = await asyncio.to_thread(
                        hubindex.search_index, source, kind, query, settings.results_per_query,
                        settings.index_min_similarity or embeddings.default_threshold(),
                    )
                    # OpenML's live search is exact-name only, so any index hit beats it.
                    if len(hits) >= settings.index_min_hits or (hits and source == "openml"):
                        return source, query, [r for r, _ in hits], time.monotonic() - started, "index"
                found = await conn.search(query, kind, settings.results_per_query)
                await asyncio.to_thread(hubindex.cache_put, source, kind, query, found)
                return source, query, found, time.monotonic() - started, "live"
            except Exception as exc:  # reported per source, never fatal for the need
                return source, query, exc, time.monotonic() - started, "live"

        calls = [
            one(source, q, conn)
            for source, source_queries in queries.items()
            if (conn := connectors.get(source)) is not None and kind in conn.kinds
            for q in source_queries
        ]
        events.publish(project_id, "need_started", need_id=need_id, description=description, kind=kind,
                       sources=sorted(queries), calls=len(calls))

        best: dict[tuple[str, str], tuple] = {}
        errors: list[str] = []
        for fut in asyncio.as_completed(calls):
            source, query, outcome, elapsed, origin = await fut
            if isinstance(outcome, BaseException):
                log.warning("search %s %r failed: %s", source, query, outcome)
                errors.append(f"{source} ({query}): {outcome}")
                events.publish(project_id, "source_error", need_id=need_id, source=source, query=query,
                               error=str(outcome)[:300])
                continue
            changed = _merge(best, [_score(raw, description, query) for raw in outcome])
            ids = _save_results(need_id, [best[k] for k in changed])
            events.publish(project_id, "hits", need_id=need_id, source=source, query=query, count=len(outcome),
                           new=len(ids), ms=round(elapsed * 1000), result_ids=ids, origin=origin,
                           titles=[best[k][0].title for k in changed[:5]])

        before = set(best)
        errors += await _follow_paper_links(best, connectors, description, settings.max_followed_links)
        followed = [k for k in best if k not in before]
        if followed:
            ids = _save_results(need_id, [best[k] for k in followed])
            events.publish(project_id, "hits", need_id=need_id, source="via_paper", query="", count=len(ids),
                           new=len(ids), ms=0, result_ids=ids, titles=[best[k][0].title for k in followed[:5]])
        _flag_mirrors(need_id)

        with SessionLocal() as db:
            need = db.get(DataNeed, need_id)
            if need is None:
                return
            need.status = "error" if calls and len(errors) >= len(calls) and not best else "done"
            need.error = "\n".join(errors) or None
            db.commit()
            status = need.status
        events.publish(project_id, "need_done", need_id=need_id, status=status, results=len(best), errors=len(errors))
    except Exception as exc:
        log.exception("search_need %s crashed", need_id)
        with SessionLocal() as db:
            need = db.get(DataNeed, need_id)
            if need is not None:
                need.status, need.error = "error", str(exc)
                db.commit()
        events.publish(project_id, "need_done", need_id=need_id, status="error", results=0, errors=1)
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


async def _report_progress(asset_id: int, dest: Path, interval: float = 2.0, project_id: int | None = None) -> None:
    last, last_t = 0, time.monotonic()
    while True:
        await asyncio.sleep(interval)
        size = await asyncio.to_thread(_dir_size, dest)
        with SessionLocal() as db:
            asset = db.get(Asset, asset_id)
            if asset is None or asset.status != "downloading":
                return
            asset.bytes_downloaded = size
            db.commit()
        now = time.monotonic()
        rate = (size - last) / max(now - last_t, 1e-6)
        last, last_t = size, now
        events.publish(project_id, "download_progress", asset_id=asset_id, bytes=size, rate=round(rate))


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
            project_id, title = result.need.project_id, result.title
            asset.status, asset.error, asset.storage_path = "downloading", None, str(dest)
            db.commit()
        events.publish(project_id, "download_started", asset_id=asset_id, title=title, source=source)

        conn = connectors[source]
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        progress = asyncio.create_task(_report_progress(asset_id, dest, project_id=project_id))
        try:
            written = await conn.download(external_id, kind, dest, settings.max_download_bytes)
        finally:
            progress.cancel()
        checksum = await asyncio.to_thread(_checksum, dest)

        with SessionLocal() as db:
            asset = db.get(Asset, asset_id)
            asset.status, asset.bytes_downloaded, asset.checksum = "done", written, checksum
            db.commit()
        events.publish(project_id, "download_done", asset_id=asset_id, title=title, bytes=written)
    except Exception as exc:
        log.warning("download_asset %s failed: %s", asset_id, exc)
        shutil.rmtree(dest, ignore_errors=True)
        project_id = None
        with SessionLocal() as db:
            asset = db.get(Asset, asset_id)
            if asset is not None:
                asset.status, asset.error, asset.storage_path = "error", str(exc), None
                project_id = asset.result.need.project_id
                db.commit()
        events.publish(project_id, "download_error", asset_id=asset_id, error=str(exc)[:300])
    finally:
        if own_client:
            await client.aclose()


async def finalize_version(version_id: int, settings: Settings) -> None:
    """Compute stats and the dataset card, then mark the version done."""
    from .analytics import compute_stats
    from .cards import build_card

    with SessionLocal() as db:
        version = db.get(DatasetVersion, version_id)
        path = Path(version.storage_path)
    stats = await asyncio.to_thread(compute_stats, path)
    await asyncio.to_thread(_create_review_items, version_id, path)
    with SessionLocal() as db:
        version = db.get(DatasetVersion, version_id)
        version.stats = stats
        db.commit()
        card = await build_card(db, version, settings)
        version.card_md = card
        version.status = "done"
        version.finished_at = datetime.now(timezone.utc)
        db.commit()


MAX_REVIEW_ITEMS = 5000


def label_config(graph: dict) -> dict | None:
    """Settings of the last auto-label step in a graph, if any."""
    nodes = [n for n in graph.get("nodes", []) if n.get("type") == "auto_label"]
    if not nodes:
        return None
    params = nodes[-1].get("params") or {}
    return {
        "labels": [l.strip() for l in str(params.get("labels", "")).split(",") if l.strip()],
        "column": params.get("column") or "text",
        "output": params.get("output") or "label",
    }


def _create_review_items(version_id: int, path: Path) -> None:
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    from .models import ReviewItem

    with SessionLocal() as db:
        version = db.get(DatasetVersion, version_id)
        cfg = label_config(version.graph_json or {}) or (version.graph_json or {}).get("labels")
    t = pq.read_table(path)
    if "needs_review" not in t.column_names or not cfg:
        return
    out = cfg["output"]
    idx = pc.indices_nonzero(pc.fill_null(t.column("needs_review"), False)).to_pylist()[:MAX_REVIEW_ITEMS]
    if not idx:
        return
    rows = t.take(idx).to_pylist()
    items = []
    for i, r in zip(idx, rows):
        if cfg["column"] == "image":
            payload = {"asset_id": r.get("asset_id"), "path": r.get("path")}
        else:
            payload = {"text": str(r.get(cfg["column"]) or "")[:2000]}
        items.append(ReviewItem(version_id=version_id, row_index=i, payload=payload, label_column=out,
                                proposed_label=r.get(out), confidence=r.get("label_confidence")))
    with SessionLocal() as db:
        db.add_all(items)
        db.commit()


async def apply_reviews(version_id: int, *, settings: Settings | None = None) -> None:
    """Write a new version with reviewed labels applied and rejected rows dropped."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from sqlalchemy import func

    from .models import ReviewItem

    settings = settings or get_settings()
    new_id = project_id = None
    try:
        with SessionLocal() as db:
            parent = db.get(DatasetVersion, version_id)
            project_id = parent.project_id
            reviews = db.scalars(select(ReviewItem).where(ReviewItem.version_id == version_id,
                                                          ReviewItem.status != "pending")).all()
            decisions = {r.row_index: (r.status, r.final_label, r.label_column) for r in reviews}
            latest = db.scalar(select(func.max(DatasetVersion.version)).where(DatasetVersion.project_id == project_id)) or 0
            cfg = label_config(parent.graph_json or {})
            child = DatasetVersion(
                project_id=project_id, pipeline_id=parent.pipeline_id, parent_version_id=parent.id, version=latest + 1,
                graph_json={**(parent.graph_json or {}), "derived": {"from_version": parent.version, "op": "apply_reviews",
                                                                     "reviews": len(decisions)},
                            **({"labels": cfg} if cfg else {})},
            )
            db.add(child)
            db.commit()
            new_id, parent_path = child.id, Path(parent.storage_path)
        events.publish(project_id, "version_started", version_id=new_id)
        rows = (await asyncio.to_thread(pq.read_table, parent_path)).to_pylist()
        table = pq.read_table(parent_path)
        kept = []
        for i, r in enumerate(rows):
            status, label, column = decisions.get(i, (None, None, None))
            if status == "rejected":
                continue
            if status in ("accepted", "corrected"):
                r = {**r, column: label, "needs_review": False}
                if "label_source" in r:
                    r["label_source"] = "human"
                if "label_confidence" in r:
                    r["label_confidence"] = 1.0
            kept.append(r)
        out_dir = settings.data_dir / "versions" / str(new_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "data.parquet"
        new_table = pa.Table.from_pylist(kept, schema=table.schema)
        await asyncio.to_thread(pq.write_table, new_table, path)
        with SessionLocal() as db:
            child = db.get(DatasetVersion, new_id)
            child.row_count = new_table.num_rows
            child.schema = [{"name": f.name, "type": str(f.type)} for f in new_table.schema]
            child.storage_path = str(path)
            child.log = f"applied {len(decisions)} reviews to v{parent.version}; dropped {len(rows) - len(kept)} rows"
            db.commit()
        await finalize_version(new_id, settings)
        events.publish(project_id, "version_done", version_id=new_id, rows=new_table.num_rows)
    except Exception as exc:
        log.warning("apply_reviews %s failed: %s", version_id, exc)
        if new_id is not None:
            with SessionLocal() as db:
                child = db.get(DatasetVersion, new_id)
                child.status, child.error = "error", str(exc)
                db.commit()
        events.publish(project_id, "version_error", version_id=new_id, error=str(exc)[:300])


def _project_asset_paths(db, project_id: int) -> dict[int, Path]:
    rows = db.execute(
        select(Asset.id, Asset.storage_path)
        .join(SearchResult)
        .join(DataNeed)
        .where(DataNeed.project_id == project_id, Asset.status == "done")
    ).all()
    return {aid: Path(path) for aid, path in rows if path}


async def run_pipeline(version_id: int, *, settings: Settings | None = None) -> None:
    import pyarrow.parquet as pq

    from .pipelines import Env, run

    settings = settings or get_settings()
    out_dir = settings.data_dir / "versions" / str(version_id)
    lines: list[str] = []

    project_id = None

    def log_line(msg: str) -> None:
        lines.append(msg)
        events.publish(project_id, "pipeline_log", version_id=version_id, message=msg)

    def on_step(node_id: str, state: str) -> None:
        events.publish(project_id, "pipeline_step", version_id=version_id, node_id=node_id, state=state)

    try:
        with SessionLocal() as db:
            version = db.get(DatasetVersion, version_id)
            if version is None:
                return
            graph = version.graph_json
            project_id = version.project_id
            asset_paths = _project_asset_paths(db, version.project_id)
        events.publish(project_id, "version_started", version_id=version_id)

        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True)
        env = Env(asset_paths=asset_paths, work_dir=out_dir, settings=settings, log=log_line)
        ctx = await asyncio.to_thread(run, graph, env, on_step)
        if ctx.table is None:
            raise RuntimeError("pipeline produced no table; end it with a step that outputs rows")
        path = out_dir / "data.parquet"
        await asyncio.to_thread(pq.write_table, ctx.table, path)

        with SessionLocal() as db:
            version = db.get(DatasetVersion, version_id)
            version.row_count = ctx.table.num_rows
            version.schema = [{"name": f.name, "type": str(f.type)} for f in ctx.table.schema]
            version.storage_path = str(path)
            version.log = "\n".join(lines)
            db.commit()
        await finalize_version(version_id, settings)
        with SessionLocal() as db:
            rows = db.get(DatasetVersion, version_id).row_count
        events.publish(project_id, "version_done", version_id=version_id, rows=rows)
    except Exception as exc:
        log.warning("run_pipeline %s failed: %s", version_id, exc)
        shutil.rmtree(out_dir, ignore_errors=True)
        with SessionLocal() as db:
            version = db.get(DatasetVersion, version_id)
            if version is not None:
                version.status, version.error = "error", str(exc)
                version.log = "\n".join(lines)
                version.finished_at = datetime.now(timezone.utc)
                db.commit()
        events.publish(project_id, "version_error", version_id=version_id, error=str(exc)[:300])


async def sync_index(*, settings: Settings | None = None) -> dict:
    report = await hubindex.sync(settings)
    await asyncio.to_thread(hubindex.cache_prune, settings or get_settings())
    log.info("hub index sync: %s", report)
    return report


async def index_sync_loop(settings: Settings) -> None:
    """In-process scheduler: sync when the index is empty or older than the interval."""
    from datetime import timedelta

    await asyncio.sleep(30)
    while True:
        entries = (await asyncio.to_thread(hubindex.stats))["entries"]
        newest = max((e["synced_at"] for e in entries if e["synced_at"]), default=None)
        if newest is not None and newest.tzinfo is None:
            newest = newest.replace(tzinfo=timezone.utc)
        due = newest is None or datetime.now(timezone.utc) - newest > timedelta(hours=settings.index_sync_hours)
        if due:
            try:
                await sync_index(settings=settings)
            except Exception:
                log.exception("scheduled index sync failed")
        await asyncio.sleep(3600)


async def run_export(export_id: int, *, settings: Settings | None = None) -> None:
    import pyarrow.parquet as pq

    from . import exports
    from .models import Export

    settings = settings or get_settings()
    project_id = None
    try:
        with SessionLocal() as db:
            exp = db.get(Export, export_id)
            version = db.get(DatasetVersion, exp.version_id)
            project_id, fmt, repo, private = version.project_id, exp.format, exp.push_repo, exp.private
            source_path, card = Path(version.storage_path), version.card_md
            asset_paths = _project_asset_paths(db, version.project_id)
            exp.status = "running"
            db.commit()
            version_id = version.id
        events.publish(project_id, "export_started", export_id=export_id, format=fmt)
        out = settings.data_dir / "versions" / str(version_id) / "exports" / f"{export_id}-{fmt}"
        table = await asyncio.to_thread(pq.read_table, source_path)
        files = await asyncio.to_thread(exports.export, table, fmt, out, asset_paths, card)
        zip_path = await asyncio.to_thread(exports.zip_dir, out, out.with_suffix(".zip"))
        hub_url = None
        if repo:
            if not settings.hf_token:
                raise exports.ExportError("HF_TOKEN is not set on the server")
            hub_url = await asyncio.to_thread(exports.push_to_hub, out, repo, settings.hf_token, private)
        with SessionLocal() as db:
            exp = db.get(Export, export_id)
            exp.status, exp.zip_path, exp.files, exp.hub_url = "done", str(zip_path), len(files), hub_url
            version = db.get(DatasetVersion, version_id)
            version.export_paths = {**(version.export_paths or {}), fmt: str(zip_path), **({"hub": hub_url} if hub_url else {})}
            db.commit()
        events.publish(project_id, "export_done", export_id=export_id, format=fmt, files=len(files), hub_url=hub_url)
    except Exception as exc:
        log.warning("export %s failed: %s", export_id, exc)
        with SessionLocal() as db:
            exp = db.get(Export, export_id)
            if exp is not None:
                exp.status, exp.error = "error", str(exc)
                db.commit()
        events.publish(project_id, "export_error", export_id=export_id, error=str(exc)[:300])


async def train_baseline(run_id: int, *, settings: Settings | None = None) -> None:
    from . import baseline
    from .models import BaselineRun

    settings = settings or get_settings()
    project_id = None
    try:
        with SessionLocal() as db:
            run_row = db.get(BaselineRun, run_id)
            version = db.get(DatasetVersion, run_row.version_id)
            project_id, path, target, exclude = version.project_id, Path(version.storage_path), run_row.target, run_row.exclude
            asset_paths = _project_asset_paths(db, version.project_id)
            run_row.status = "running"
            db.commit()
            model_out = settings.data_dir / "versions" / str(version.id) / "baselines" / f"{run_id}.joblib"
        events.publish(project_id, "baseline_started", run_id=run_id, target=target)
        result = await asyncio.to_thread(baseline.train, path, target, asset_paths, model_out, exclude)
        with SessionLocal() as db:
            run_row = db.get(BaselineRun, run_id)
            run_row.status, run_row.result, run_row.model_path = "done", result, str(model_out)
            run_row.finished_at = datetime.now(timezone.utc)
            db.commit()
        events.publish(project_id, "baseline_done", run_id=run_id, target=target, task=result["task"],
                       metrics=result["metrics"])
    except Exception as exc:
        log.warning("baseline %s failed: %s", run_id, exc)
        with SessionLocal() as db:
            run_row = db.get(BaselineRun, run_id)
            if run_row is not None:
                run_row.status, run_row.error = "error", str(exc)
                run_row.finished_at = datetime.now(timezone.utc)
                db.commit()
        events.publish(project_id, "baseline_error", run_id=run_id, error=str(exc)[:300])
