"""Live UI support: event stream, stats HUD, previews and license matrix."""

import asyncio
import contextlib

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import events, previews, quota
from .auth import accessible_project_ids, current_user, project_role, read, websocket_user
from .db import SessionLocal, get_db
from .jobs import make_http_client
from .licensing import license_rights
from .models import Asset, DataNeed, DatasetVersion, Project, SearchResult, User

router = APIRouter(prefix="/api")


@router.websocket("/projects/{project_id}/ws")
async def project_events(websocket: WebSocket, project_id: int):
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        user = websocket_user(websocket, db)
        if project is None or user is None or project_role(db, user, project) is None:
            await websocket.close(code=4404)
            return
    await websocket.accept()
    stream = events.bus().subscribe(project_id)

    async def pump():
        async for event in stream:
            await websocket.send_json(event)

    async def watch():
        # Detect the client going away even when no events are flowing.
        with contextlib.suppress(WebSocketDisconnect):
            while True:
                await websocket.receive_text()

    tasks = [asyncio.create_task(pump()), asyncio.create_task(watch())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        with contextlib.suppress(Exception):
            await stream.aclose()


@router.get("/projects/{project_id}/stats", dependencies=[read("project")])
def project_stats(project_id: int, db: Session = Depends(get_db)) -> dict:
    if db.get(Project, project_id) is None:
        raise HTTPException(404, "project not found")
    in_project = select(DataNeed.id).where(DataNeed.project_id == project_id)
    results = db.scalar(select(func.count()).select_from(SearchResult).where(SearchResult.need_id.in_(in_project))) or 0
    asset_q = select(Asset, SearchResult.size_bytes).join(SearchResult).where(SearchResult.need_id.in_(in_project))
    assets = db.execute(asset_q).all()
    versions = db.scalars(select(DatasetVersion).where(DatasetVersion.project_id == project_id)).all()
    searching = db.scalar(
        select(func.count()).select_from(DataNeed).where(DataNeed.project_id == project_id, DataNeed.status == "searching")
    ) or 0
    downloading = [(a, size) for a, size in assets if a.status in ("queued", "downloading")]
    running = [v for v in versions if v.status == "running"]
    latest = max((v for v in versions if v.status == "done"), key=lambda v: v.version, default=None)
    return {
        "results": results,
        "bytes_gathered": sum(a.bytes_downloaded for a, _ in assets if a.status == "done"),
        "assets_done": sum(1 for a, _ in assets if a.status == "done"),
        "workers_active": searching + len(downloading) + len(running),
        "searching": searching,
        "downloads": [
            {"asset_id": a.id, "bytes": a.bytes_downloaded, "size": size, "status": a.status} for a, size in downloading
        ],
        "running_versions": len(running),
        "latest_version": latest.version if latest else None,
        "rows": latest.row_count if latest else 0,
        "quota": quota.snapshot(),
    }


@router.get("/results/{result_id}/preview", dependencies=[read("result")])
async def preview_result(result_id: int, db: Session = Depends(get_db)) -> dict:
    result = db.get(SearchResult, result_id)
    if result is None:
        raise HTTPException(404, "result not found")
    async with make_http_client() as client:
        try:
            return await previews.result_preview(result, client)
        except Exception as exc:  # previews are best-effort
            return {"type": "error", "error": f"preview unavailable: {exc}"}


class MatrixRequest(BaseModel):
    result_ids: list[int] = Field(min_length=1, max_length=200)


@router.post("/license-matrix")
def license_matrix(body: MatrixRequest, db: Session = Depends(get_db), user: User = Depends(current_user)) -> list[dict]:
    visible = select(DataNeed.id).where(DataNeed.project_id.in_(accessible_project_ids(db, user)))
    results = db.scalars(
        select(SearchResult).where(SearchResult.id.in_(body.result_ids), SearchResult.need_id.in_(visible))
    ).all()
    return [
        {"result_id": r.id, "title": r.title, "source": r.source, "license": r.license,
         "license_class": r.license_class, "rights": license_rights(r.license)}
        for r in results
    ]


def _asset_root(db: Session, asset_id: int):
    asset = db.get(Asset, asset_id)
    if asset is None or asset.status != "done" or not asset.storage_path:
        raise HTTPException(404, "asset not downloaded")
    from pathlib import Path

    return Path(asset.storage_path)


@router.get("/assets/{asset_id}/files", dependencies=[read("asset")])
def asset_files(asset_id: int, db: Session = Depends(get_db)) -> list[dict]:
    return previews.list_files(_asset_root(db, asset_id))


def _asset_path(db: Session, asset_id: int, path: str):
    root = _asset_root(db, asset_id)
    try:
        target = previews.safe_child(root, path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not target.is_file():
        raise HTTPException(404, "file not found")
    return target


@router.get("/assets/{asset_id}/preview", dependencies=[read("asset")])
def asset_preview(asset_id: int, path: str = Query(...), db: Session = Depends(get_db)) -> dict:
    target = _asset_path(db, asset_id, path)
    try:
        return previews.file_preview(target)
    except Exception as exc:
        return {"type": "error", "error": f"cannot preview {target.name}: {exc}"}


@router.get("/assets/{asset_id}/file", dependencies=[read("asset")])
def asset_file(asset_id: int, path: str = Query(...), db: Session = Depends(get_db)):
    target = _asset_path(db, asset_id, path)
    kind = previews.file_kind(target)
    if kind not in ("image", "video"):
        raise HTTPException(415, "only images and videos are served inline")
    # Media from the asset store is untrusted: never let it render as a document.
    return FileResponse(target, headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})
