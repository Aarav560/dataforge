import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import jobqueue
from .config import get_settings
from .db import SessionLocal, get_db, init_db
from .models import Asset, DataNeed, Project, SearchResult
from .planner import make_plan
from .schemas import (
    AssetOut,
    DownloadRequest,
    NeedIn,
    NeedOut,
    NeedPatch,
    ProjectCreate,
    ProjectOut,
    ProjectSummary,
    ResultOut,
    SearchRequest,
    queries_for,
)

logging.basicConfig(level=logging.INFO)


def _fail_interrupted_jobs() -> None:
    """In-process jobs die with the server; don't leave them looking active."""
    msg = "interrupted by server restart; retry"
    with SessionLocal() as db:
        db.execute(update(DataNeed).where(DataNeed.status == "searching").values(status="error", error=msg))
        db.execute(
            update(Asset).where(Asset.status.in_(("queued", "downloading"))).values(status="error", error=msg)
        )
        db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if not get_settings().redis_url:
        _fail_interrupted_jobs()
    yield
    await jobqueue.close()


app = FastAPI(title="DataForge", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _project_or_404(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return project


def _need_or_404(db: Session, need_id: int) -> DataNeed:
    need = db.get(DataNeed, need_id)
    if need is None:
        raise HTTPException(404, "need not found")
    return need


def _need_out(db: Session, need: DataNeed) -> NeedOut:
    count = db.scalar(select(func.count()).where(SearchResult.need_id == need.id)) or 0
    return NeedOut.model_validate(need).model_copy(update={"result_count": count})


def _project_out(db: Session, project: Project) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        goal=project.goal,
        approach=project.approach,
        approach_reason=(project.plan_json or {}).get("approach_reason"),
        planner=project.planner,
        created_at=project.created_at,
        needs=[_need_out(db, n) for n in project.needs],
    )


@app.get("/api/health")
def health() -> dict:
    settings = get_settings()
    return {
        "ok": True,
        "planner": "claude" if settings.anthropic_api_key else "heuristic",
        "queue": "redis" if settings.redis_url else "in-process",
        "kaggle_downloads": bool(settings.kaggle_username and settings.kaggle_key),
    }


@app.post("/api/projects", response_model=ProjectOut, status_code=201)
async def create_project(body: ProjectCreate, db: Session = Depends(get_db)):
    plan = await make_plan(body.goal.strip(), get_settings())
    project = Project(goal=body.goal.strip(), approach=plan.approach, plan_json=plan.to_json(), planner=plan.planner)
    for n in plan.needs:
        project.needs.append(
            DataNeed(description=n.description, kind=n.kind, approach=plan.approach, queries=n.source_queries())
        )
    db.add(project)
    db.commit()
    return _project_out(db, project)


@app.get("/api/projects", response_model=list[ProjectSummary])
def list_projects(db: Session = Depends(get_db)):
    return db.scalars(select(Project).order_by(Project.created_at.desc()).limit(100)).all()


@app.get("/api/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db)):
    return _project_out(db, _project_or_404(db, project_id))


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    db.delete(_project_or_404(db, project_id))
    db.commit()


@app.post("/api/projects/{project_id}/needs", response_model=NeedOut, status_code=201)
def add_need(project_id: int, body: NeedIn, db: Session = Depends(get_db)):
    project = _project_or_404(db, project_id)
    need = DataNeed(
        project_id=project.id,
        description=body.description,
        kind=body.kind,
        approach=project.approach,
        queries=queries_for(body.kind, body.queries),
    )
    db.add(need)
    db.commit()
    return _need_out(db, need)


@app.patch("/api/needs/{need_id}", response_model=NeedOut)
def update_need(need_id: int, body: NeedPatch, db: Session = Depends(get_db)):
    need = _need_or_404(db, need_id)
    if need.status == "searching":
        raise HTTPException(409, "need is being searched")
    if body.description is not None:
        need.description = body.description
    if body.kind is not None or body.queries is not None:
        kind = body.kind or need.kind
        queries = body.queries or next(iter(need.queries.values()), [])
        need.kind, need.queries = kind, queries_for(kind, queries)
        need.status = "pending"
    db.commit()
    return _need_out(db, need)


@app.delete("/api/needs/{need_id}", status_code=204)
def delete_need(need_id: int, db: Session = Depends(get_db)):
    db.delete(_need_or_404(db, need_id))
    db.commit()


@app.post("/api/projects/{project_id}/search", response_model=ProjectOut, status_code=202)
async def start_search(project_id: int, body: SearchRequest | None = None, db: Session = Depends(get_db)):
    project = _project_or_404(db, project_id)
    wanted = set(body.need_ids) if body and body.need_ids else None
    targets = [n for n in project.needs if (wanted is None or n.id in wanted) and n.status != "searching"]
    for need in targets:
        need.status, need.error = "searching", None
    db.commit()
    for need in targets:
        await jobqueue.enqueue("search_need", need.id)
    return _project_out(db, project)


@app.get("/api/projects/{project_id}/results", response_model=list[ResultOut])
def list_results(
    project_id: int,
    need_id: int | None = None,
    license_class: str | None = None,
    source: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    stmt = select(SearchResult).join(DataNeed).where(DataNeed.project_id == project_id)
    if need_id is not None:
        stmt = stmt.where(SearchResult.need_id == need_id)
    if license_class:
        stmt = stmt.where(SearchResult.license_class == license_class)
    if source:
        stmt = stmt.where(SearchResult.source == source)
    stmt = stmt.order_by(SearchResult.score.desc()).limit(min(max(limit, 1), 1000))
    return db.scalars(stmt).all()


def _asset_out(asset: Asset) -> AssetOut:
    return AssetOut(
        id=asset.id,
        result_id=asset.result_id,
        title=asset.result.title,
        source=asset.result.source,
        storage_path=asset.storage_path,
        checksum=asset.checksum,
        bytes_downloaded=asset.bytes_downloaded,
        status=asset.status,
        error=asset.error,
        created_at=asset.created_at,
    )


@app.post("/api/projects/{project_id}/downloads", response_model=list[AssetOut], status_code=202)
async def start_downloads(project_id: int, body: DownloadRequest, db: Session = Depends(get_db)):
    _project_or_404(db, project_id)
    results = db.scalars(
        select(SearchResult)
        .join(DataNeed)
        .where(DataNeed.project_id == project_id, SearchResult.id.in_(body.result_ids))
    ).all()
    if len(results) != len(set(body.result_ids)):
        raise HTTPException(404, "some results do not belong to this project")
    assets, created = [], []
    for result in results:
        active = next((a for a in result.assets if a.status in ("queued", "downloading", "done")), None)
        # Failed attempts are replaced by a fresh asset below.
        if active is not None:
            assets.append(active)
            continue
        asset = Asset(result_id=result.id)
        db.add(asset)
        assets.append(asset)
        created.append(asset)
    db.commit()
    for asset in created:
        await jobqueue.enqueue("download_asset", asset.id)
    return [_asset_out(a) for a in assets]


@app.get("/api/projects/{project_id}/assets", response_model=list[AssetOut])
def list_assets(project_id: int, db: Session = Depends(get_db)):
    _project_or_404(db, project_id)
    assets = db.scalars(
        select(Asset)
        .join(SearchResult)
        .join(DataNeed)
        .where(DataNeed.project_id == project_id)
        .order_by(Asset.created_at.desc())
    ).all()
    return [_asset_out(a) for a in assets]
