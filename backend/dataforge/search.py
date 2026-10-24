"""Parallel multi-source search (plan §3.2, §6): fan-out, stream, cache, rank."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time

from . import db, ranking
from .config import RESULTS_PER_SOURCE, SEARCH_CACHE_TTL
from .connectors import REGISTRY
from .events import Job, bus

MAX_CONCURRENCY = 16
SOURCE_TIMEOUT = 45


def _cache_key(source: str, query: str) -> str:
    norm = " ".join(sorted(ranking.tokens(query))) or query.lower().strip()
    return hashlib.sha1(f"{source}|{norm}".encode()).hexdigest()


def cache_get(source: str, query: str) -> list[dict] | None:
    """Semantic-ish cache: queries are keyed by their sorted content tokens, so reworded
    near-duplicates ("tetris dataset" / "Dataset: Tetris") hit the same entry."""
    rows = db.query("SELECT results_json, created_at FROM search_cache WHERE key = ?", (_cache_key(source, query),))
    if rows and time.time() - rows[0]["created_at"] < SEARCH_CACHE_TTL:
        return rows[0]["results"]
    return None


def cache_put(source: str, query: str, results: list[dict]) -> None:
    db.execute("INSERT OR REPLACE INTO search_cache (key, source, query, results_json, created_at) VALUES (?,?,?,?,?)",
               (_cache_key(source, query), source, query, json.dumps(results), time.time()))


async def run_search(job: Job, project: dict, plan: dict, need_ids: list[str] | None = None, use_llm_judge: bool = False) -> dict:
    pid = project["id"]
    goal = project["goal"]
    needs = [n for n in plan["needs"] if n.get("enabled", True) and (not need_ids or n["id"] in need_ids)]
    tasks: list[tuple[dict, str, str]] = [(n, s, q) for n in needs for s in n["sources"] if s in REGISTRY for q in n["queries"][:REGISTRY[s].max_queries]]
    total = len(tasks)
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    done = 0
    counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    seen_urls: dict[str, set] = {n["id"]: set() for n in needs}

    # Clear previous (unselected) results for these needs so re-search refreshes the wall.
    for n in needs:
        db.execute("DELETE FROM search_results WHERE project_id = ? AND need_id = ? AND selected = 0", (pid, n["id"]))
        for r in db.query("SELECT url FROM search_results WHERE project_id = ? AND need_id = ?", (pid, n["id"])):
            seen_urls[n["id"]].add(r["url"])
        n["status"] = "searching"
        bus.publish(pid, "need", need_id=n["id"], status="searching")

    async def one(need: dict, source: str, query: str) -> None:
        nonlocal done
        conn = REGISTRY[source]
        async with sem:
            t0 = time.time()
            cached = cache_get(source, query)
            try:
                if cached is not None:
                    raw = cached
                    origin = "cache"
                else:
                    hits = await asyncio.wait_for(conn.search(query, RESULTS_PER_SOURCE), timeout=SOURCE_TIMEOUT)
                    raw = [h.to_dict() for h in hits]
                    cache_put(source, query, raw)
                    origin = "live"
            except Exception as e:  # noqa: BLE001 — one failing source must not sink the search
                errors[source] = str(e)[:200] or type(e).__name__
                job.log(f"[{conn.label}] '{query}' failed: {errors[source]}", level="warn", source=source)
                raw, origin = [], "error"
            fresh = []
            for r in raw:
                if not r.get("url") or r["url"] in seen_urls[need["id"]]:
                    continue
                seen_urls[need["id"]].add(r["url"])
                r = {**r, "id": db.new_id(), "need_id": need["id"], "project_id": pid}
                fresh.append(r)
            if fresh:
                ranking.relevance(fresh, f"{need['description']} {query}", goal)
                for r in fresh:
                    ranking.vet(r)
                    db.insert("search_results", id=r["id"], project_id=pid, need_id=need["id"], source=r["source"], kind=r["kind"],
                              url=r["url"], title=r["title"][:300], description=r.get("description", ""), size_bytes=r.get("size_bytes"),
                              format=r.get("format"), license=r.get("license"), license_class=r["license_class"],
                              relevance=r["relevance"], quality=r["quality"], score=r["score"], meta_json=r.get("meta", {}))
                bus.publish(pid, "results", need_id=need["id"], source=source, results=[_public(r) for r in fresh])
            counts[source] = counts.get(source, 0) + len(fresh)
            done += 1
            if origin != "error":
                job.log(f"[{conn.label}] {len(fresh)} hits for '{query}' ({origin}, {time.time() - t0:.1f}s)", source=source)
            job.progress(done / max(1, total), f"{done}/{total} source queries")

    await asyncio.gather(*(one(*t) for t in tasks))

    # Post-pass per need: re-score relevance across the full result set, mark dupes, optional LLM judge.
    for n in needs:
        rows = db.query("SELECT * FROM search_results WHERE project_id = ? AND need_id = ?", (pid, n["id"]))
        for r in rows:
            r["meta"] = r.get("meta") or {}
        ranking.relevance(rows, f"{n['description']} {' '.join(n['queries'])}", goal)
        for r in rows:
            ranking.vet(r)
        if use_llm_judge:
            job.log(f"LLM judge vetting top results for '{n['description']}'")
            await ranking.llm_judge(rows, n["description"], goal)
        ranking.mark_duplicates(rows)
        for r in rows:
            db.update("search_results", r["id"], relevance=r["relevance"], quality=r["quality"], score=r["score"],
                      license_class=r["license_class"], dup_of=r.get("dup_of"), meta_json=r["meta"])
        n["status"] = "done"
        bus.publish(pid, "need", need_id=n["id"], status="done", count=len(rows))
    bus.publish(pid, "rescored")
    db.update("projects", pid, plan_json=plan)
    return {"counts": counts, "errors": errors, "queries": total}


def _public(r: dict) -> dict:
    return {k: r.get(k) for k in ("id", "need_id", "source", "kind", "url", "title", "description", "size_bytes", "format",
                                  "license", "license_class", "relevance", "quality", "score", "meta", "dup_of", "selected")}


def list_results(project_id: str) -> list[dict]:
    rows = db.query("SELECT * FROM search_results WHERE project_id = ? ORDER BY score DESC", (project_id,))
    return [_public(r) for r in rows]
