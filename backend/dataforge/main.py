"""FastAPI gateway (plan §5): projects, planner, search, gather, pipelines, datasets, live WebSocket stream."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import polars as pl
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import baseline, datasets, db, gather, http, licenses, llm, planner, ranking, search
from .config import CORS_ORIGINS, GITHUB_TOKEN, RAW_DIR, STORAGE
from .connectors import REGISTRY, catalog
from .events import STATS, Job, bus, spawn
from .pipelines import TEMPLATES, public_node_types, run_pipeline
from .synthetic import GENERATORS


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.conn()
    # Work in flight when the server stopped can't resume (in-process workers) — mark it interrupted.
    db.execute("UPDATE jobs SET status = 'error', message = 'interrupted by server restart' WHERE status IN ('queued', 'running')")
    db.execute("UPDATE assets SET status = 'error', error = 'interrupted by server restart' WHERE status = 'downloading'")
    # Pre-index the hubs that have no search API (plan §6) in the background.
    for name in ("openml", "uci"):
        c = REGISTRY.get(name)
        if c and hasattr(c, "warm"):
            asyncio.create_task(_quiet(c.warm()))
    yield
    if http._client:
        await http._client.aclose()


async def _quiet(coro):
    try:
        await coro
    except Exception as e:  # noqa: BLE001
        print(f"[warm] {e}")


app = FastAPI(title="DataForge", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


def _project(pid: str) -> dict:
    p = db.get("projects", pid)
    if not p:
        raise HTTPException(404, "project not found")
    return p


# ───────────────────────── meta ─────────────────────────
@app.get("/api/health")
async def health():
    return {"ok": True, "llm": await llm.status(), "connectors": catalog(), "workers_active": STATS["workers_active"]}


@app.get("/api/stats")
async def stats(project_id: str | None = None):
    where, args = ("WHERE project_id = ?", (project_id,)) if project_id else ("", ())
    gb = db.query(f"SELECT COALESCE(SUM(size_bytes),0) AS b FROM assets {where}", args)[0]["b"]
    rows = db.query(f"SELECT COALESCE(SUM(row_count),0) AS r FROM dataset_versions {where}", args)[0]["r"]
    res = db.query(f"SELECT COUNT(*) AS n FROM search_results {where}", args)[0]["n"]
    running = db.query(f"SELECT kind, progress, message, created_at, updated_at FROM jobs {where + (' AND' if where else 'WHERE')} status = 'running'", args)
    eta = None
    for j in running:
        if j["progress"] and j["progress"] > 0.02:
            el = (j["updated_at"] or 0) - (j["created_at"] or 0)
            rem = el * (1 - j["progress"]) / j["progress"]
            eta = max(eta or 0, rem)
    return {"bytes_gathered": gb, "rows": rows, "results": res, "workers_active": STATS["workers_active"], "jobs_running": len(running),
            "eta_sec": eta, "api_quota": {"github": "5000/h (token)" if GITHUB_TOKEN else "60/h, 10 searches/min (anonymous)",
                                          "semantic_scholar": "shared pool", "others": "free / unmetered"}}


@app.get("/api/connectors")
def connectors():
    return catalog()


# ───────────────────────── projects & planner ─────────────────────────
class NewProject(BaseModel):
    goal: str = Field(min_length=3, max_length=500)
    use_llm: bool = True


@app.post("/api/projects")
async def create_project(body: NewProject):
    plan = await planner.make_plan(body.goal.strip(), use_llm=body.use_llm)
    p = db.insert("projects", goal=body.goal.strip(), plan_json=plan)
    return {"id": p["id"], "goal": p["goal"], "plan": plan}


@app.get("/api/projects")
def list_projects():
    rows = db.query("""SELECT p.id, p.goal, p.created_at,
                         (SELECT COUNT(*) FROM search_results r WHERE r.project_id = p.id) AS results,
                         (SELECT COUNT(*) FROM dataset_versions v WHERE v.project_id = p.id) AS versions
                       FROM projects p ORDER BY p.created_at DESC""")
    return rows


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    p = _project(pid)
    p["jobs"] = db.query("SELECT * FROM jobs WHERE project_id = ? ORDER BY created_at DESC LIMIT 30", (pid,))
    return p


class PlanBody(BaseModel):
    plan: dict


@app.put("/api/projects/{pid}/plan")
def save_plan(pid: str, body: PlanBody):
    p = _project(pid)
    plan = planner.validate_plan({**(p["plan"] or {}), **body.plan})
    db.update("projects", pid, plan_json=plan)
    return plan


@app.post("/api/projects/{pid}/replan")
async def replan(pid: str, use_llm: bool = True):
    p = _project(pid)
    plan = await planner.make_plan(p["goal"], use_llm=use_llm)
    db.update("projects", pid, plan_json=plan)
    return plan


@app.get("/api/projects/{pid}/similar")
def similar(pid: str):
    """Project memory: reuse plans from past goals with similar wording."""
    p = _project(pid)
    others = db.query("SELECT id, goal, plan_json FROM projects WHERE id != ?", (pid,))
    if not others:
        return []
    items = [{"id": o["id"], "title": o["goal"], "description": "", "meta": {}} for o in others]
    ranking.relevance(items, p["goal"], p["goal"])
    return [{"id": i["id"], "goal": i["title"], "similarity": i["relevance"]} for i in sorted(items, key=lambda x: -x["relevance"])[:5] if i["relevance"] > 0.1]


# ───────────────────────── search ─────────────────────────
class SearchBody(BaseModel):
    need_ids: list[str] | None = None
    llm_judge: bool = False


@app.post("/api/projects/{pid}/search")
async def start_search(pid: str, body: SearchBody):
    p = _project(pid)
    plan = p["plan"]
    return {"job_id": spawn(pid, "search", lambda job: search.run_search(job, p, plan, body.need_ids, body.llm_judge))}


@app.get("/api/projects/{pid}/results")
def results(pid: str):
    _project(pid)
    return search.list_results(pid)


class SelectBody(BaseModel):
    selected: bool


@app.post("/api/results/{rid}/select")
def select(rid: str, body: SelectBody):
    if not db.get("search_results", rid):
        raise HTTPException(404)
    db.update("search_results", rid, selected=body.selected)
    return {"ok": True}


@app.get("/api/results/{rid}/preview")
async def preview(rid: str):
    """Lazy preview (plan §6): sample rows without downloading the whole dataset."""
    r = db.get("search_results", rid)
    if not r:
        raise HTTPException(404)
    meta = r.get("meta") or {}
    dl = meta.get("download") or {}
    out: dict = {"kind": r["kind"], "title": r["title"], "url": r["url"], "description": r["description"], "meta": meta}
    try:
        if dl.get("type") == "hf_dataset":
            repo = quote(dl["repo"], safe="")
            splits = await http.get_json(f"https://datasets-server.huggingface.co/splits?dataset={repo}")
            s = (splits.get("splits") or [{}])[0]
            if s:
                fr = await http.get_json(f"https://datasets-server.huggingface.co/first-rows?dataset={repo}&config={quote(s['config'])}&split={quote(s['split'])}")
                out["table"] = {"columns": [f["name"] for f in fr.get("features", [])],
                                "rows": [row["row"] for row in fr.get("rows", [])[:20]], "split": s["split"], "config": s["config"]}
        elif dl.get("type") == "openml":
            d = (await http.get_json(f"https://www.openml.org/api/v1/json/data/{dl['did']}"))["data_set_description"]
            out["description"] = (d.get("description") or "")[:1500]
            if d.get("parquet_url"):
                out["table"] = await _remote_table(d["parquet_url"])
        elif dl.get("type") in ("files", "url"):
            urls = [f["url"] for f in dl.get("files", [])] if dl.get("type") == "files" else [dl.get("url")]
            tab = next((u for u in urls if u and any(u.split("?")[0].lower().endswith(e) for e in (".csv", ".parquet", ".tsv", ".jsonl", "/content"))), None)
            if tab:
                out["table"] = await _remote_table(tab)
        elif r["kind"] == "video" and meta.get("video_id"):
            out["embed"] = f"https://www.youtube-nocookie.com/embed/{meta['video_id']}"
    except Exception as e:  # noqa: BLE001
        out["preview_error"] = str(e)[:200]
    return out


async def _remote_table(url: str) -> dict:
    """DuckDB reads remote Parquet/CSV with HTTP range requests — only the needed bytes are fetched."""
    def run():
        import duckdb
        con = duckdb.connect()
        try:
            con.execute("INSTALL httpfs; LOAD httpfs;")
        except Exception:  # noqa: BLE001
            pass
        fn = "read_parquet" if ".parquet" in url.lower() else "read_csv_auto"
        rel = con.execute(f"SELECT * FROM {fn}(?) LIMIT 20", [url])
        cols = [d[0] for d in rel.description]
        rows = [dict(zip(cols, map(lambda v: v if isinstance(v, (int, float, str, type(None))) else str(v), row))) for row in rel.fetchall()]
        return {"columns": cols, "rows": rows}
    return await asyncio.wait_for(asyncio.to_thread(run), timeout=30)


# ───────────────────────── gather ─────────────────────────
class GatherBody(BaseModel):
    result_ids: list[str] | None = None


@app.post("/api/projects/{pid}/gather")
async def start_gather(pid: str, body: GatherBody):
    _project(pid)
    ids = body.result_ids or [r["id"] for r in db.query("SELECT id FROM search_results WHERE project_id = ? AND selected = 1", (pid,))]
    if not ids:
        raise HTTPException(400, "select at least one result")
    return {"job_id": spawn(pid, "gather", lambda job: gather.run_gather(job, pid, ids))}


@app.get("/api/projects/{pid}/assets")
def assets(pid: str):
    _project(pid)
    return gather.list_assets(pid)


@app.get("/api/projects/{pid}/license-matrix")
def license_matrix(pid: str, intended_use: str = "research"):
    rows = db.query("SELECT id, title, source, license, license_class, meta_json FROM search_results WHERE project_id = ? AND selected = 1", (pid,))
    items = []
    for r in rows:
        rights = (r.get("meta") or {}).get("rights") or licenses.classify(r["license"])[1]
        items.append({"id": r["id"], "title": r["title"], "source": r["source"], "license": r["license"],
                      "class": r["license_class"], "color": licenses.color(r["license_class"] or "unknown"), "rights": rights})
    ok, problems = licenses.compatible([i["class"] or "unknown" for i in items], intended_use)
    return {"items": items, "compatible": ok, "problems": problems}


# ───────────────────────── synthetic ─────────────────────────
class SynthBody(BaseModel):
    generator: str
    params: dict = {}


@app.get("/api/synthetic/generators")
def generators():
    return {k: {kk: vv for kk, vv in v.items() if kk != "fn"} for k, v in GENERATORS.items()}


@app.post("/api/projects/{pid}/synthetic")
async def synthesize(pid: str, body: SynthBody):
    p = _project(pid)
    if body.generator not in GENERATORS:
        raise HTTPException(400, f"unknown generator; choose from {list(GENERATORS)}")
    g = GENERATORS[body.generator]
    params = {**g["params"], **{k: v for k, v in body.params.items() if k in g["params"] or k == "seed"}}
    params["rows_target"] = max(100, min(int(params.get("rows_target", 20000)), 5_000_000))

    async def run(job: Job):
        loop = asyncio.get_running_loop()
        cb = lambda f, m: loop.call_soon_threadsafe(job.progress, f * 0.9, m)  # noqa: E731
        cols = await asyncio.to_thread(g["fn"], on_progress=cb, **params)
        df = pl.DataFrame(cols)
        out = RAW_DIR / pid / f"synthetic_{body.generator}"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{body.generator}_{db.new_id()}.parquet"
        df.write_parquet(path)
        res = db.insert("search_results", project_id=pid, need_id=None, source="synthetic", kind="dataset", url=f"synthetic://{body.generator}",
                        title=f"Synthetic · {g['label']} ({df.height:,} rows)", description=f"schema: {g['schema']}", size_bytes=path.stat().st_size,
                        format="parquet", license="CC0 (generated)", license_class="commercial_ok", relevance=1.0, quality=1.0, score=1.0,
                        meta_json={"params": params, "generator": body.generator, "rights": licenses.classify("cc0")[1]}, selected=1)
        db.insert("assets", project_id=pid, result_id=res["id"], storage_path=str(out), size_bytes=path.stat().st_size, status="done", checksum="")
        bus.publish(pid, "results", need_id=None, source="synthetic", results=[search._public(db.get("search_results", res["id"]))])
        job.progress(0.95, "writing dataset version")
        v = await datasets.create_version(p, df, [f"Generated {df.height:,} rows with {g['label']} {params}"])
        return {"rows": df.height, "path": str(path), "version": v}

    return {"job_id": spawn(pid, "synthetic", run)}


# ───────────────────────── pipelines ─────────────────────────
@app.get("/api/pipelines/templates")
def templates():
    return {"templates": {k: {"label": v["label"], "graph": v["graph"]} for k, v in TEMPLATES.items()}, "node_types": public_node_types()}


class PipelineBody(BaseModel):
    template: str | None = None
    graph: dict | None = None
    id: str | None = None


@app.post("/api/projects/{pid}/pipelines")
def save_pipeline(pid: str, body: PipelineBody):
    _project(pid)
    graph = body.graph or (TEMPLATES[body.template]["graph"] if body.template in TEMPLATES else None)
    if not graph:
        raise HTTPException(400, "provide a graph or a known template")
    if body.id and db.get("pipelines", body.id):
        db.update("pipelines", body.id, graph_json=graph, template=body.template)
        return db.get("pipelines", body.id)
    return db.insert("pipelines", project_id=pid, template=body.template, graph_json=graph)


@app.get("/api/projects/{pid}/pipelines")
def list_pipelines(pid: str):
    return db.query("SELECT * FROM pipelines WHERE project_id = ? ORDER BY created_at DESC", (pid,))


@app.post("/api/pipelines/{plid}/run")
async def run_pipe(plid: str):
    pl_row = db.get("pipelines", plid)
    if not pl_row:
        raise HTTPException(404)
    p = _project(pl_row["project_id"])

    async def run(job: Job):
        ctx = await run_pipeline(job, p["id"], pl_row["graph"])
        if ctx.get("table") is None:
            raise RuntimeError("pipeline produced no table — end it with a table-producing step")
        job.progress(0.97, "writing dataset version")
        v = await datasets.create_version(p, ctx["table"], ctx["notes"], review=ctx.get("review"), pii=ctx.get("pii"))
        return v

    return {"job_id": spawn(p["id"], "pipeline", run)}


# ───────────────────────── dataset versions ─────────────────────────
def _version(vid: str) -> dict:
    v = db.get("dataset_versions", vid)
    if not v:
        raise HTTPException(404, "version not found")
    return v


@app.get("/api/projects/{pid}/versions")
def versions(pid: str):
    rows = db.query("SELECT id, project_id, version, row_count, schema_json, export_paths_json, created_at FROM dataset_versions WHERE project_id = ? ORDER BY version DESC", (pid,))
    return rows


@app.get("/api/versions/{vid}")
def version(vid: str):
    return _version(vid)


@app.get("/api/versions/{a}/diff/{b}")
def version_diff(a: str, b: str):
    return datasets.diff(_version(a), _version(b))


@app.get("/api/versions/{vid}/rows")
def version_rows(vid: str, offset: int = 0, limit: int = 100):
    df = datasets.load(_version(vid)).slice(offset, min(limit, 1000))
    return {"columns": df.columns, "rows": json.loads(df.write_json())}


class ExportBody(BaseModel):
    format: str
    intended_use: str = "research"
    push_repo: str | None = None


@app.post("/api/versions/{vid}/export")
async def export_version(vid: str, body: ExportBody):
    v = _version(vid)
    if body.format not in datasets.EXPORT_FORMATS:
        raise HTTPException(400, f"format must be one of {datasets.EXPORT_FORMATS}")
    try:
        return await asyncio.to_thread(datasets.export, v, body.format, body.intended_use, body.push_repo)
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    except (RuntimeError, ValueError) as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/versions/{vid}/download/{fmt}")
def download(vid: str, fmt: str):
    v = _version(vid)
    path = (v["export_paths"] or {}).get(fmt)
    if not path or not Path(path).exists():
        raise HTTPException(404, "export not found — run the export first")
    return FileResponse(path, filename=f"dataforge_v{v['version']}_{fmt}{Path(path).suffix}")


class ReviewBody(BaseModel):
    decisions: list[dict]  # [{"row": int, "label": str}]


@app.post("/api/versions/{vid}/review")
async def apply_review(vid: str, body: ReviewBody):
    """Human review queue: apply accepted/corrected labels → new version."""
    v = _version(vid)
    df = datasets.load(v)
    if "label" not in df.columns:
        raise HTTPException(400, "version has no label column")
    labels = df["label"].to_list()
    for d in body.decisions:
        i = int(d["row"])
        if 0 <= i < len(labels):
            labels[i] = d.get("label")
    df = df.with_columns(pl.Series("label", labels, dtype=pl.String))
    if "label_confidence" in df.columns:
        conf = df["label_confidence"].to_list()
        for d in body.decisions:
            if 0 <= int(d["row"]) < len(conf):
                conf[int(d["row"])] = 1.0
        df = df.with_columns(pl.Series("label_confidence", conf, dtype=pl.Float64))
    p = _project(v["project_id"])
    notes = list((v.get("stats") or {}).get("notes", [])) + [f"human review: {len(body.decisions)} labels confirmed/corrected"]
    reviewed = {int(d["row"]) for d in body.decisions}
    remaining = [r for r in (v.get("stats") or {}).get("review", []) if r["row"] not in reviewed]
    return await datasets.create_version(p, df, notes, review=remaining)


class BaselineBody(BaseModel):
    target: str | None = None


@app.post("/api/versions/{vid}/baseline")
async def train_baseline(vid: str, body: BaselineBody):
    v = _version(vid)
    try:
        return await asyncio.to_thread(baseline.train, datasets.load(v), body.target)
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/files")
def serve_file(path: str):
    """Serve a stored file (frames, images) for previews — confined to the storage root."""
    p = Path(path).resolve()
    root = STORAGE.resolve()
    if root not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/api/jobs/{jid}")
def job(jid: str):
    j = db.get("jobs", jid)
    if not j:
        raise HTTPException(404)
    return j


# ───────────────────────── live stream ─────────────────────────
@app.websocket("/ws/projects/{pid}")
async def ws(websocket: WebSocket, pid: str):
    await websocket.accept()
    q = bus.subscribe(pid)
    try:
        for evt in bus.history(pid)[-200:]:
            await websocket.send_json({**evt, "replay": True})
        while True:
            evt = await q.get()
            await websocket.send_json(evt)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(pid, q)

