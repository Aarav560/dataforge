"""Pipelines, dataset versions and synthetic-data advice."""

from datetime import datetime

import pyarrow.parquet as pq
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import jobqueue
from .auth import read, write
from .config import get_settings
from .db import get_db
from .models import DatasetVersion, Pipeline, Project
from .pipelines import REGISTRY, PipelineError, validate
from .pipelines import templates
from .synthetic.advisor import advise

router = APIRouter(prefix="/api")


class PipelineCreate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    template: str | None = None
    asset_ids: list[int] = Field(default_factory=list, max_length=100)
    graph: dict | None = None


class PipelineUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    graph: dict | None = None


class PipelineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    template: str | None
    graph_json: dict
    created_at: datetime
    updated_at: datetime


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    pipeline_id: int | None
    parent_version_id: int | None
    version: int
    status: str
    # Stored as `schema`; renamed to avoid shadowing BaseModel.schema.
    columns: list = Field(validation_alias="schema")
    row_count: int
    stats: dict
    log: str
    error: str | None
    card_md: str | None
    export_paths: dict
    graph_json: dict
    created_at: datetime
    finished_at: datetime | None


def _project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return project


def _pipeline(db: Session, pipeline_id: int) -> Pipeline:
    pipeline = db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(404, "pipeline not found")
    return pipeline


def _version(db: Session, version_id: int) -> DatasetVersion:
    version = db.get(DatasetVersion, version_id)
    if version is None:
        raise HTTPException(404, "version not found")
    return version


def _clean_graph(graph: dict) -> dict:
    try:
        validate(graph)
    except PipelineError as exc:
        raise HTTPException(422, str(exc)) from exc
    # validate() annotates nodes with parents; store only what was given.
    return {
        "nodes": [{k: v for k, v in n.items() if not k.startswith("_")} for n in graph.get("nodes", [])],
        "edges": graph.get("edges", []),
        **({"layout": graph["layout"]} if "layout" in graph else {}),
    }


@router.get("/pipeline-steps")
def list_steps() -> list[dict]:
    return [s.describe() for s in REGISTRY.values()]


@router.get("/pipeline-templates")
def list_templates() -> list[dict]:
    return templates.describe()


@router.post("/projects/{project_id}/pipelines", response_model=PipelineOut, status_code=201, dependencies=[write("project")])
def create_pipeline(project_id: int, body: PipelineCreate, db: Session = Depends(get_db)):
    _project(db, project_id)
    if body.graph is not None:
        graph = body.graph
    elif body.template:
        try:
            graph = templates.build(body.template, body.asset_ids)
        except KeyError:
            raise HTTPException(404, f"unknown template {body.template!r}") from None
    else:
        raise HTTPException(422, "provide a template or a graph")
    graph = _clean_graph(graph)
    label = templates.TEMPLATES.get(body.template or "", {}).get("label", "Custom pipeline")
    pipeline = Pipeline(project_id=project_id, name=body.name or label, template=body.template, graph_json=graph)
    db.add(pipeline)
    db.commit()
    return pipeline


@router.get("/projects/{project_id}/pipelines", response_model=list[PipelineOut], dependencies=[read("project")])
def list_pipelines(project_id: int, db: Session = Depends(get_db)):
    _project(db, project_id)
    return db.scalars(select(Pipeline).where(Pipeline.project_id == project_id).order_by(Pipeline.id)).all()


@router.get("/pipelines/{pipeline_id}", response_model=PipelineOut, dependencies=[read("pipeline")])
def get_pipeline(pipeline_id: int, db: Session = Depends(get_db)):
    return _pipeline(db, pipeline_id)


@router.put("/pipelines/{pipeline_id}", response_model=PipelineOut, dependencies=[write("pipeline")])
def update_pipeline(pipeline_id: int, body: PipelineUpdate, db: Session = Depends(get_db)):
    pipeline = _pipeline(db, pipeline_id)
    if body.name is not None:
        pipeline.name = body.name
    if body.graph is not None:
        pipeline.graph_json = _clean_graph(body.graph)
    db.commit()
    return pipeline


@router.delete("/pipelines/{pipeline_id}", status_code=204, dependencies=[write("pipeline")])
def delete_pipeline(pipeline_id: int, db: Session = Depends(get_db)):
    db.delete(_pipeline(db, pipeline_id))
    db.commit()


async def start_version(db: Session, project_id: int, graph: dict, pipeline_id: int | None) -> DatasetVersion:
    latest = db.scalar(select(func.max(DatasetVersion.version)).where(DatasetVersion.project_id == project_id)) or 0
    version = DatasetVersion(project_id=project_id, pipeline_id=pipeline_id, version=latest + 1, graph_json=graph)
    db.add(version)
    db.commit()
    try:
        await jobqueue.enqueue("run_pipeline", version.id)
    except jobqueue.QueueUnavailable as exc:
        version.status, version.error = "error", str(exc)
        db.commit()
        raise
    return version


@router.post("/pipelines/{pipeline_id}/run", response_model=VersionOut, status_code=202, dependencies=[write("pipeline")])
async def run_pipeline(pipeline_id: int, db: Session = Depends(get_db)):
    pipeline = _pipeline(db, pipeline_id)
    graph = _clean_graph(pipeline.graph_json)
    return await start_version(db, pipeline.project_id, graph, pipeline.id)


@router.get("/projects/{project_id}/versions", response_model=list[VersionOut], dependencies=[read("project")])
def list_versions(project_id: int, db: Session = Depends(get_db)):
    _project(db, project_id)
    return db.scalars(
        select(DatasetVersion).where(DatasetVersion.project_id == project_id).order_by(DatasetVersion.version.desc())
    ).all()


@router.get("/versions/{version_id}", response_model=VersionOut, dependencies=[read("version")])
def get_version(version_id: int, db: Session = Depends(get_db)):
    return _version(db, version_id)


def _jsonable(value):
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, datetime):
        return value.isoformat()
    return value


@router.get("/versions/{version_id}/rows", dependencies=[read("version")])
def version_rows(version_id: int, offset: int = 0, limit: int = 50, db: Session = Depends(get_db)) -> dict:
    version = _version(db, version_id)
    if version.status != "done" or not version.storage_path:
        raise HTTPException(409, f"version is {version.status}")
    pf = pq.ParquetFile(version.storage_path)
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    rows: list[dict] = []
    seen = 0
    for batch in pf.iter_batches(batch_size=1024):
        if seen + batch.num_rows <= offset:
            seen += batch.num_rows
            continue
        start = max(offset - seen, 0)
        rows.extend(batch.slice(start, limit - len(rows)).to_pylist())
        seen += batch.num_rows
        if len(rows) >= limit:
            break
    return {
        "total": pf.metadata.num_rows,
        "offset": offset,
        "rows": [{k: _jsonable(v) for k, v in r.items()} for r in rows],
    }


@router.get("/projects/{project_id}/synthetic", dependencies=[read("project")])
async def synthetic_advice(project_id: int, refresh: bool = False, db: Session = Depends(get_db)) -> dict:
    project = _project(db, project_id)
    plan = dict(project.plan_json or {})
    if refresh or "synthetic" not in plan:
        plan["synthetic"] = (await advise(project.goal, get_settings())).to_json()
        project.plan_json = plan
        db.commit()
    return plan["synthetic"]


def _done_path(version: DatasetVersion):
    from pathlib import Path

    if version.status != "done" or not version.storage_path:
        raise HTTPException(409, f"version v{version.version} is {version.status}")
    return Path(version.storage_path)


@router.get("/versions/{version_id}/diff", dependencies=[read("version")])
def version_diff(version_id: int, against: int | None = None, db: Session = Depends(get_db)) -> dict:
    """Diff against `against`, else the parent version, else the previous version of the project."""
    from .analytics import diff

    version = _version(db, version_id)
    if against is not None:
        base = _version(db, against)
        if base.project_id != version.project_id:
            raise HTTPException(400, "versions belong to different projects")
    elif version.parent_version_id:
        base = _version(db, version.parent_version_id)
    else:
        base = db.scalar(
            select(DatasetVersion)
            .where(DatasetVersion.project_id == version.project_id, DatasetVersion.version < version.version,
                   DatasetVersion.status == "done")
            .order_by(DatasetVersion.version.desc())
        )
        if base is None:
            raise HTTPException(404, "no earlier version to compare with")
    result = diff(_done_path(base), _done_path(version))
    result["base"] = {"id": base.id, "version": base.version}
    result["target"] = {"id": version.id, "version": version.version}
    return jsonable(result)


def jsonable(value):
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return _jsonable(value) if isinstance(value, (bytes, datetime)) else value


class CardSections(BaseModel):
    summary: str = Field(min_length=1, max_length=4000)
    limitations: list[str] = Field(default_factory=list, max_length=30)


@router.post("/versions/{version_id}/card", response_model=VersionOut, dependencies=[write("version")])
async def regenerate_card(version_id: int, db: Session = Depends(get_db)):
    from .cards import build_card

    version = _version(db, version_id)
    _done_path(version)
    version.card_md = await build_card(db, version, get_settings())
    db.commit()
    return version


@router.put("/versions/{version_id}/card", response_model=VersionOut, dependencies=[write("version")])
def write_card(version_id: int, body: CardSections, db: Session = Depends(get_db)):
    """Use a summary and limitations written by the caller (e.g. Claude via the connector)."""
    from .cards import default_limitations, facts, render

    version = _version(db, version_id)
    _done_path(version)
    f = facts(db, version)
    found = default_limitations(f)
    extra = [x.strip()[:500] for x in body.limitations if x.strip() and x.strip() not in found]
    version.card_md = render(f, body.summary.strip(), found + extra)
    db.commit()
    return version


class ExportRequest(BaseModel):
    format: str
    push_to_hub: bool = False
    repo_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][\w.-]{0,95}/[A-Za-z0-9][\w.-]{0,95}$")
    private: bool = True
    acknowledge_unknown_licenses: bool = False


class ExportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version_id: int
    format: str
    status: str
    push_repo: str | None
    private: bool
    files: int
    hub_url: str | None
    warnings: list
    error: str | None
    created_at: datetime


@router.get("/export-formats")
def export_formats() -> dict:
    from .exports import FORMATS

    return FORMATS


def _export_check(db: Session, version: DatasetVersion, fmt: str, push: bool, ack: bool) -> dict:
    from .cards import version_sources
    from .exports import check

    return check(version_sources(db, version.graph_json or {}), fmt, push, ack)


@router.get("/versions/{version_id}/export-check", dependencies=[read("version")])
def export_check(version_id: int, format: str = "parquet", push: bool = False, acknowledge: bool = False,
                 db: Session = Depends(get_db)) -> dict:
    return _export_check(db, _version(db, version_id), format, push, acknowledge)


@router.post("/versions/{version_id}/exports", response_model=ExportOut, status_code=202, dependencies=[write("version")])
async def start_export(version_id: int, body: ExportRequest, db: Session = Depends(get_db)):
    from .exports import FORMATS
    from .models import Export

    version = _version(db, version_id)
    _done_path(version)
    if body.format not in FORMATS:
        raise HTTPException(422, f"unknown format {body.format!r}")
    if body.push_to_hub and (body.format != "hf" or not body.repo_id):
        raise HTTPException(422, "pushing to the Hub needs format 'hf' and a repo_id like 'user/name'")
    verdict = _export_check(db, version, body.format, body.push_to_hub, body.acknowledge_unknown_licenses)
    if not verdict["allowed"]:
        raise HTTPException(403, " ".join(verdict["blockers"]))
    exp = Export(version_id=version.id, format=body.format, push_repo=body.repo_id if body.push_to_hub else None,
                 private=body.private, warnings=verdict["warnings"])
    db.add(exp)
    db.commit()
    try:
        await jobqueue.enqueue("run_export", exp.id)
    except jobqueue.QueueUnavailable as exc:
        exp.status, exp.error = "error", str(exc)
        db.commit()
        raise
    return exp


@router.get("/versions/{version_id}/exports", response_model=list[ExportOut], dependencies=[read("version")])
def list_exports(version_id: int, db: Session = Depends(get_db)):
    from .models import Export

    _version(db, version_id)
    return db.scalars(select(Export).where(Export.version_id == version_id).order_by(Export.id.desc())).all()


@router.get("/exports/{export_id}/download", dependencies=[read("export")])
def download_export(export_id: int, db: Session = Depends(get_db)):
    from fastapi.responses import FileResponse

    from .models import Export

    exp = db.get(Export, export_id)
    if exp is None or exp.status != "done" or not exp.zip_path:
        raise HTTPException(404, "export not ready")
    version = db.get(DatasetVersion, exp.version_id)
    return FileResponse(exp.zip_path, media_type="application/zip",
                        filename=f"dataset-v{version.version}-{exp.format}.zip")


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version_id: int
    row_index: int
    payload: dict
    label_column: str
    proposed_label: str | None
    confidence: float | None
    status: str
    final_label: str | None


class ReviewDecision(BaseModel):
    action: str = Field(pattern="^(accept|correct|reject|reset)$")
    label: str | None = Field(default=None, max_length=200)


@router.get("/versions/{version_id}/reviews", dependencies=[read("version")])
def list_reviews(version_id: int, status: str = "pending", offset: int = 0, limit: int = 50,
                 db: Session = Depends(get_db)) -> dict:
    from .jobs import label_config
    from .models import ReviewItem

    version = _version(db, version_id)
    base = select(ReviewItem).where(ReviewItem.version_id == version_id)
    counts = dict(db.execute(select(ReviewItem.status, func.count()).where(ReviewItem.version_id == version_id)
                             .group_by(ReviewItem.status)).all())
    if status != "all":
        base = base.where(ReviewItem.status == status)
    items = db.scalars(base.order_by(ReviewItem.confidence.asc().nulls_first(), ReviewItem.row_index)
                       .offset(max(offset, 0)).limit(min(max(limit, 1), 200))).all()
    cfg = label_config(version.graph_json or {}) or (version.graph_json or {}).get("labels") or {}
    return {"counts": counts, "labels": cfg.get("labels", []),
            "items": [ReviewOut.model_validate(i).model_dump() for i in items]}


@router.post("/reviews/{review_id}", response_model=ReviewOut, dependencies=[write("review")])
def decide_review(review_id: int, body: ReviewDecision, db: Session = Depends(get_db)):
    from .models import ReviewItem

    item = db.get(ReviewItem, review_id)
    if item is None:
        raise HTTPException(404, "review item not found")
    if body.action == "accept":
        if not item.proposed_label:
            raise HTTPException(422, "nothing proposed to accept; pick a label")
        item.status, item.final_label = "accepted", item.proposed_label
    elif body.action == "correct":
        if not body.label:
            raise HTTPException(422, "a label is required")
        item.status, item.final_label = "corrected", body.label
    elif body.action == "reject":
        item.status, item.final_label = "rejected", None
    else:
        item.status, item.final_label = "pending", None
    item.reviewed_at = datetime.now().astimezone() if item.status != "pending" else None
    db.commit()
    return item


@router.post("/versions/{version_id}/apply-reviews", status_code=202, dependencies=[write("version")])
async def apply_reviews(version_id: int, db: Session = Depends(get_db)) -> dict:
    from .models import ReviewItem

    version = _version(db, version_id)
    _done_path(version)
    decided = db.scalar(select(func.count()).select_from(ReviewItem)
                        .where(ReviewItem.version_id == version_id, ReviewItem.status != "pending")) or 0
    if not decided:
        raise HTTPException(409, "no reviewed items to apply")
    await jobqueue.enqueue("apply_reviews", version_id)
    return {"queued": True, "reviews": decided}


class BaselineRequest(BaseModel):
    target: str | None = Field(default=None, max_length=200)
    # None applies the suggested leakage exclusions.
    exclude: list[str] | None = Field(default=None, max_length=200)


class BaselineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version_id: int
    target: str
    exclude: list | None
    status: str
    result: dict
    error: str | None
    created_at: datetime
    finished_at: datetime | None


@router.get("/versions/{version_id}/baseline-options", dependencies=[read("version")])
def baseline_options(version_id: int, target: str | None = None, db: Session = Depends(get_db)) -> dict:
    from .baseline import SKIP, suggested_exclusions, target_candidates

    version = _version(db, version_id)
    candidates = target_candidates(version.schema or [])
    chosen = target or (candidates[0] if candidates else None)
    # Columns that could be features; bookkeeping columns are never used.
    columns = [c["name"] for c in version.schema or [] if c["name"] not in SKIP]
    return {"targets": candidates, "target": chosen, "columns": columns,
            "suggested_exclude": suggested_exclusions(columns, chosen) if chosen else []}


@router.post("/versions/{version_id}/baselines", response_model=BaselineOut, status_code=202, dependencies=[write("version")])
async def start_baseline(version_id: int, body: BaselineRequest, db: Session = Depends(get_db)):
    from .baseline import target_candidates
    from .models import BaselineRun

    version = _version(db, version_id)
    _done_path(version)
    candidates = target_candidates(version.schema or [])
    target = body.target or (candidates[0] if candidates else None)
    if not target or target not in [c["name"] for c in version.schema or []]:
        raise HTTPException(422, f"choose a target column; candidates: {candidates}")
    run_row = BaselineRun(version_id=version.id, target=target, exclude=body.exclude)
    db.add(run_row)
    db.commit()
    try:
        await jobqueue.enqueue("train_baseline", run_row.id)
    except jobqueue.QueueUnavailable as exc:
        run_row.status, run_row.error = "error", str(exc)
        db.commit()
        raise
    return run_row


@router.get("/versions/{version_id}/baselines", response_model=list[BaselineOut], dependencies=[read("version")])
def list_baselines(version_id: int, db: Session = Depends(get_db)):
    from .models import BaselineRun

    return db.scalars(select(BaselineRun).where(BaselineRun.version_id == version_id).order_by(BaselineRun.id.desc())).all()


@router.get("/baselines/{baseline_id}/model", dependencies=[read("baseline")])
def download_model(baseline_id: int, db: Session = Depends(get_db)):
    from fastapi.responses import FileResponse

    from .models import BaselineRun

    run_row = db.get(BaselineRun, baseline_id)
    if run_row is None or run_row.status != "done" or not run_row.model_path:
        raise HTTPException(404, "model not ready")
    return FileResponse(run_row.model_path, media_type="application/octet-stream",
                        filename=f"baseline-{baseline_id}-{run_row.target}.joblib")
