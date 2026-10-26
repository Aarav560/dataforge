import json

import httpx
import pytest
from PIL import Image as PILImage

from app import jobs
from app.db import SessionLocal
from app.main import app
from app.mcp_server import DataForge, build_server, http_app
from app.models import Asset, DataNeed, Project, SearchResult
from conftest import FakeConnector, raw


@pytest.fixture
def server(monkeypatch):
    fakes = {
        "huggingface": FakeConnector("huggingface", {"dataset", "model"},
                                     [raw("huggingface", "u/tetris-games", description="tetris games", license="mit")]),
        "kaggle": FakeConnector("kaggle", {"dataset"}),
        "openml": FakeConnector("openml", {"dataset"}),
        "github": FakeConnector("github", {"dataset", "code", "environment", "model"}),
    }
    monkeypatch.setattr(jobs, "build_connectors", lambda client, settings: fakes)
    monkeypatch.setattr("app.mcp_server.POLL", 0.05)
    client = DataForge("http://dataforge.test", token="", transport=httpx.ASGITransport(app=app))
    return build_server(client)


async def call(server, name, **args):
    """Call a tool and return its structured result (or raw content blocks)."""
    result = await server.call_tool(name, args)
    assert not result.is_error, result.content
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    texts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
    try:
        return json.loads(texts[0]) if len(texts) == 1 else result.content
    except (json.JSONDecodeError, IndexError):
        return result.content


async def test_tools_listed(server):
    names = {t.name for t in await server.list_tools()}
    assert {"create_project", "search", "get_results", "download", "run_pipeline", "get_review_queue",
            "submit_labels", "apply_reviews", "write_card", "export", "train_baseline"} <= names


async def test_end_to_end_flow(server):
    project = await call(server, "create_project", goal="classify pet descriptions", approach="supervised",
                         approach_reason="labeled text", needs=[
                             {"description": "Pet text datasets", "kind": "dataset", "queries": ["tetris games"]}])
    assert project["planner"] == "provided" and project["needs"][0]["kind"] == "dataset"
    pid = project["id"]

    searched = await call(server, "search", project_id=pid, timeout=20)
    assert searched["needs"][0]["status"] == "done"
    results = await call(server, "get_results", project_id=pid)
    assert results[0]["title"] == "u/tetris-games" and results[0]["license_class"] == "commercial"

    graph = {"nodes": [{"id": "n1", "type": "inline_rows", "params": {"csv": "text\nthe cat sat\na dog barked\nnothing here"}},
                       {"id": "n2", "type": "auto_label", "params": {"labels": "cat,dog"}}],
             "edges": [{"source": "n1", "target": "n2"}]}
    v1 = await call(server, "run_pipeline", project_id=pid, graph=graph, timeout=30)
    assert v1["status"] == "done" and v1["row_count"] == 3

    queue = await call(server, "get_review_queue", version_id=v1["id"])
    # Without images the tool returns a single JSON block.
    info = queue if isinstance(queue, dict) else json.loads(queue[0].text)
    assert info["labels"] == ["cat", "dog"] and len(info["items"]) == 3
    by_text = {i["text"]: i["id"] for i in info["items"]}
    rec = await call(server, "submit_labels", decisions=[
        {"review_id": by_text["the cat sat"], "label": "cat"},
        {"review_id": by_text["a dog barked"], "label": "dog"},
        {"review_id": by_text["nothing here"], "reject": True},
    ])
    assert rec == {"recorded": 3, "errors": []}
    v2 = await call(server, "apply_reviews", version_id=v1["id"], timeout=30)
    assert v2["status"] == "done" and v2["row_count"] == 2 and v2["parent_version_id"] == v1["id"]

    card = await call(server, "write_card", version_id=v2["id"], summary="Two hand-checked pet sentences.",
                      limitations=["Tiny: only for smoke tests."])
    assert "Two hand-checked pet sentences." in card and "Tiny: only for smoke tests." in card

    exp = await call(server, "export", version_id=v2["id"], format="csv", timeout=30)
    assert exp["status"] == "done" and exp["download_url"].endswith(f"/api/exports/{exp['id']}/download")
    diff = await call(server, "diff_versions", version_id=v2["id"])
    assert diff["rows"]["delta"] == -1


async def test_baseline_and_rows(server):
    project = await call(server, "create_project", goal="tetris ai")
    graph = {"nodes": [{"id": "n1", "type": "tetris_synthetic", "params": {"games": 4, "max_steps": 40}}], "edges": []}
    v = await call(server, "run_pipeline", project_id=project["id"], graph=graph, timeout=60)
    rows = await call(server, "get_rows", version_id=v["id"], limit=2)
    assert rows["rows"][0]["state"] == "[200 values]"
    b = await call(server, "train_baseline", version_id=v["id"], target="column", timeout=120)
    assert b["status"] == "done" and b["excluded"] == ["done", "lines", "reward", "rotation"]
    assert b["metrics"]["accuracy"] >= 0


async def test_image_review_items_are_images(server, tmp_path):
    root = tmp_path / "imgs" / "pets"
    root.mkdir(parents=True)
    for i in range(2):
        PILImage.new("RGB", (900, 600), color=(i * 200, 0, 0)).save(root / f"{i}.png")
    with SessionLocal() as db:
        need = DataNeed(description="d", kind="dataset", queries={})
        result = SearchResult(source="huggingface", kind="dataset", external_id="a/b", url="u", title="a/b")
        need.results.append(result)
        result.assets.append(Asset(status="done", storage_path=str(tmp_path / "imgs")))
        project = Project(goal="pets", needs=[need])
        db.add(project)
        db.commit()
        pid, aid = project.id, result.assets[0].id
    graph = {"nodes": [{"id": "n1", "type": "load_images", "params": {"asset_ids": [aid], "label_from": "none"}},
                       {"id": "n2", "type": "auto_label", "params": {"labels": "cat,dog", "column": "image"}}],
             "edges": [{"source": "n1", "target": "n2"}]}
    v = await call(server, "run_pipeline", project_id=pid, graph=graph, timeout=30)
    assert v["status"] == "done", v
    content = (await server.call_tool("get_review_queue", {"version_id": v["id"]})).content
    images = [c for c in content if getattr(c, "type", "") == "image"]
    assert len(images) == 2 and images[0].mime_type == "image/jpeg"


async def test_errors_are_readable(server):
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="404.*project not found"):
        await server.call_tool("get_project", {"project_id": 999})


def test_http_mode_requires_token():
    from starlette.testclient import TestClient

    with TestClient(http_app("s3cret"), base_url="http://127.0.0.1:8765") as c:
        assert c.post("/mcp", json={}).status_code == 401
        assert c.post("/mcp", json={}, headers={"Authorization": "Bearer nope"}).status_code == 401
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
        ok = c.post("/mcp", json=init, headers={"Authorization": "Bearer s3cret",
                                                "Accept": "application/json, text/event-stream"})
        assert ok.status_code == 200 and "DataForge" in ok.text
    with TestClient(http_app("s3cret", allowed_hosts=["df.example.com"]), base_url="https://df.example.com") as c:
        ok = c.post("/mcp", json=init, headers={"Authorization": "Bearer s3cret",
                                                "Accept": "application/json, text/event-stream"})
        assert ok.status_code == 200
