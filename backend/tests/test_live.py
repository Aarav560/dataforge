import asyncio

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import respx
from fastapi.testclient import TestClient

from app import events, jobs, previews
from app.db import SessionLocal
from app.main import app
from app.models import Asset, DataNeed, Project, SearchResult
from conftest import FakeConnector, raw


@pytest.fixture(autouse=True)
def fresh_bus():
    events.reset()
    yield
    events.reset()


@pytest.fixture
def connectors(monkeypatch):
    fakes = {
        "huggingface": FakeConnector("huggingface", {"dataset", "model"},
                                     [raw("huggingface", "u/tetris", license="mit", description="tetris games")]),
        "kaggle": FakeConnector("kaggle", {"dataset"}, fail=True),
        "github": FakeConnector("github", {"dataset", "model", "environment", "code"},
                                [raw("github", "x/tetris", license="gpl-3.0")]),
    }
    monkeypatch.setattr(jobs, "build_connectors", lambda client, settings: fakes)
    return fakes


def test_websocket_streams_search_events(connectors):
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"goal": "classify tetris screenshots"}).json()
        pid, need = project["id"], project["needs"][0]
        with client.websocket_connect(f"/api/projects/{pid}/ws") as ws:
            client.post(f"/api/projects/{pid}/search", json={"need_ids": [need["id"]]})
            seen = []
            while not seen or seen[-1]["type"] != "need_done":
                seen.append(ws.receive_json())
        types = [e["type"] for e in seen]
        assert types[0] == "need_started" and types[-1] == "need_done"
        hits = [e for e in seen if e["type"] == "hits"]
        assert {h["source"] for h in hits} == {"huggingface", "github"}
        # The second query per source returns the same items, so only the first adds rows.
        for source in ("huggingface", "github"):
            assert sum(len(h["result_ids"]) for h in hits if h["source"] == source) == 1
        errors = [e for e in seen if e["type"] == "source_error"]
        assert errors and errors[0]["source"] == "kaggle"

        # A late subscriber gets the backlog replayed.
        with client.websocket_connect(f"/api/projects/{pid}/ws") as ws:
            first = ws.receive_json()
            assert first["replay"] and first["type"] == "need_started"

        stats = client.get(f"/api/projects/{pid}/stats").json()
        assert stats["results"] == 2 and stats["workers_active"] == 0


def test_websocket_unknown_project():
    with TestClient(app) as client:
        with pytest.raises(Exception):
            with client.websocket_connect("/api/projects/999/ws") as ws:
                ws.receive_json()


def _seed_result(**kw) -> int:
    with SessionLocal() as db:
        need = DataNeed(description="d", kind=kw.pop("need_kind", "dataset"), queries={})
        result = SearchResult(url="u", **kw)
        need.results.append(result)
        db.add(Project(goal="g", needs=[need]))
        db.commit()
        return result.id


def test_license_matrix():
    a = _seed_result(source="huggingface", kind="dataset", external_id="a/b", title="a/b", license="cc-by-nc-4.0",
                     license_class="non_commercial")
    with TestClient(app) as client:
        [row] = client.post("/api/license-matrix", json={"result_ids": [a]}).json()
    assert row["rights"]["commercial"] is False and row["rights"]["attribution"] is True


@respx.mock
def test_result_previews():
    respx.route(host="testserver").pass_through()
    ds = _seed_result(source="huggingface", kind="dataset", external_id="a/b", title="a/b")
    yt = _seed_result(source="youtube", kind="video", external_id="vid1", title="v", meta={"thumbnail": "t"})
    gh = _seed_result(source="github", kind="code", external_id="o/r", title="o/r")
    respx.get("https://datasets-server.huggingface.co/splits").respond(json={"splits": [{"config": "default", "split": "train"}]})
    respx.get("https://datasets-server.huggingface.co/first-rows").respond(json={
        "features": [{"name": "img", "type": {"_type": "Image"}}, {"name": "label", "type": {"_type": "Value"}}],
        "rows": [{"row": {"img": {"src": "https://x/1.png"}, "label": 3}}],
    })
    respx.get("https://raw.githubusercontent.com/o/r/HEAD/README.md").respond(status_code=404)
    respx.get("https://raw.githubusercontent.com/o/r/HEAD/readme.md").respond(text="# Hello")
    with TestClient(app) as client:
        p = client.get(f"/api/results/{ds}/preview").json()
        assert p["type"] == "table" and p["images"] == ["https://x/1.png"] and p["rows"] == [{"img": "[image]", "label": 3}]
        assert client.get(f"/api/results/{yt}/preview").json()["embed"].endswith("/embed/vid1")
        assert client.get(f"/api/results/{gh}/preview").json() == {"type": "markdown", "text": "# Hello"}


def test_asset_files_and_path_safety(tmp_path):
    root = tmp_path / "asset"
    (root / "sub").mkdir(parents=True)
    pq.write_table(pa.table({"x": list(range(100))}), root / "sub" / "data.parquet")
    (root / "train.csv").write_text("a,b\n1,2\n3,4\n")
    (root / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "secret.png").write_bytes(b"\x89PNG")
    rid = _seed_result(source="huggingface", kind="dataset", external_id="a/b", title="a/b")
    with SessionLocal() as db:
        asset = Asset(result_id=rid, status="done", storage_path=str(root))
        db.add(asset)
        db.commit()
        aid = asset.id
    with TestClient(app) as client:
        files = {f["path"]: f["kind"] for f in client.get(f"/api/assets/{aid}/files").json()}
        assert files == {"pic.png": "image", "sub/data.parquet": "table", "train.csv": "table"}
        p = client.get(f"/api/assets/{aid}/preview", params={"path": "sub/data.parquet"}).json()
        assert p["type"] == "table" and len(p["rows"]) == previews.MAX_ROWS
        csv = client.get(f"/api/assets/{aid}/preview", params={"path": "train.csv"}).json()
        assert csv["rows"] == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
        img = client.get(f"/api/assets/{aid}/file", params={"path": "pic.png"})
        assert img.status_code == 200 and img.headers["content-security-policy"] == "sandbox"
        assert client.get(f"/api/assets/{aid}/file", params={"path": "../secret.png"}).status_code == 400
        assert client.get(f"/api/assets/{aid}/file", params={"path": "train.csv"}).status_code == 415


def test_local_bus_is_thread_safe():
    bus = events._LocalBus()

    async def main():
        stream = bus.subscribe(1)
        first = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0)
        await asyncio.to_thread(bus.publish, 1, {"type": "x"})
        assert (await asyncio.wait_for(first, 1))["type"] == "x"
        await stream.aclose()

    asyncio.run(main())


@respx.mock
def test_hf_preview_falls_back_to_card():
    respx.route(host="testserver").pass_through()
    ds = _seed_result(source="huggingface", kind="dataset", external_id="a/b", title="a/b")
    respx.get("https://datasets-server.huggingface.co/splits").respond(json={"splits": [{"config": "default", "split": "train"}]})
    respx.get("https://datasets-server.huggingface.co/first-rows").respond(status_code=500)
    respx.get("https://huggingface.co/datasets/a/b/raw/main/README.md").respond(text="# Card")
    with TestClient(app) as client:
        assert client.get(f"/api/results/{ds}/preview").json() == {"type": "markdown", "text": "# Card"}
