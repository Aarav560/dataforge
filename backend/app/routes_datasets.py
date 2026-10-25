"""Pipelines, dataset versions and synthetic-data advice."""

from datetime import datetime

import pyarrow.parquet as pq
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import jobqueue
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
    version: int
    status: str
    # Stored as `schema`; renamed to avoid shadowing BaseModel.schema.
    columns: list = Field(validation_alias="schema")
    row_count: int
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


@router.post("/projects/{project_id}/pipelines", response_model=PipelineOut, status_code=201)
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


@router.get("/projects/{project_id}/pipelines", response_model=list[PipelineOut])
def list_pipelines(project_id: int, db: Session = Depends(get_db)):
    _project(db, project_id)
    return db.scalars(select(Pipeline).where(Pipeline.project_id == project_id).order_by(Pipeline.id)).all()


@router.get("/pipelines/{pipeline_id}", response_model=PipelineOut)
def get_pipeline(pipeline_id: int, db: Session = Depends(get_db)):
    return _pipeline(db, pipeline_id)


@router.put("/pipelines/{pipeline_id}", response_model=PipelineOut)
def update_pipeline(pipeline_id: int, body: PipelineUpdate, db: Session = Depends(get_db)):
    pipeline = _pipeline(db, pipeline_id)
    if body.name is not None:
        pipeline.name = body.name
    if body.graph is not None:
        pipeline.graph_json = _clean_graph(body.graph)
    db.commit()
    return pipeline


@router.delete("/pipelines/{pipeline_id}", status_code=204)
def delete_pipeline(pipeline_id: int, db: Session = Depends(get_db)):
    db.delete(_pipeline(db, pipeline_id))
    db.commit()


async def start_version(db: Session, project_id: int, graph: dict, pipeline_id: int | None) -> DatasetVersion:
    latest = db.scalar(select(func.max(DatasetVersion.version)).where(DatasetVersion.project_id == project_id)) or 0
    version = DatasetVersion(project_id=project_id, pipeline_id=pipeline_id, version=latest + 1, graph_json=graph)
    db.add(version)
    db.commit()
    await jobqueue.enqueue("run_pipeline", version.id)
    return version


@router.post("/pipelines/{pipeline_id}/run", response_model=VersionOut, status_code=202)
async def run_pipeline(pipeline_id: int, db: Session = Depends(get_db)):
    pipeline = _pipeline(db, pipeline_id)
    graph = _clean_graph(pipeline.graph_json)
    return await start_version(db, pipeline.project_id, graph, pipeline.id)


@router.get("/projects/{project_id}/versions", response_model=list[VersionOut])
def list_versions(project_id: int, db: Session = Depends(get_db)):
    _project(db, project_id)
    return db.scalars(
        select(DatasetVersion).where(DatasetVersion.project_id == project_id).order_by(DatasetVersion.version.desc())
    ).all()


@router.get("/versions/{version_id}", response_model=VersionOut)
def get_version(version_id: int, db: Session = Depends(get_db)):
    return _version(db, version_id)


def _jsonable(value):
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, datetime):
        return value.isoformat()
    return value


@router.get("/versions/{version_id}/rows")
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


@router.get("/projects/{project_id}/synthetic")
async def synthetic_advice(project_id: int, refresh: bool = False, db: Session = Depends(get_db)) -> dict:
    project = _project(db, project_id)
    plan = dict(project.plan_json or {})
    if refresh or "synthetic" not in plan:
        plan["synthetic"] = (await advise(project.goal, get_settings())).to_json()
        project.plan_json = plan
        db.commit()
    return plan["synthetic"]
