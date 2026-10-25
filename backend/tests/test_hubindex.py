import asyncio

import httpx
import respx
from fastapi.testclient import TestClient

from app import hubindex, jobs
from app.config import Settings
from app.connectors.openml import OpenMLConnector
from app.main import app
from conftest import FakeConnector, raw


def test_serialization_roundtrip():
    r = raw("huggingface", "a/b", description="x", license="mit", tags=["t"])
    back = hubindex.from_json(hubindex.to_json(r))
    assert back == r


def test_index_search_ranks_by_similarity():
    hubindex.upsert_entries([
        raw("openml", "1", title="tetris-games (v1)", description="logged tetris games"),
        raw("openml", "2", title="iris (v1)", description="flower measurements"),
        raw("openml", "3", title="credit-g (v1)", description="credit risk"),
    ])
    hits = hubindex.search_index("openml", "dataset", "tetris games", 2, 0.1)
    assert hits[0][0].external_id == "1" and hits[0][0].meta["from_index"]
    assert hubindex.indexed_sources() == {("openml", "dataset")}


def test_query_cache_exact_and_ttl():
    s = Settings(cache_ttl_hours=1, cache_similarity=0.99)
    assert hubindex.cache_get("huggingface", "dataset", "tetris", s) is None
    hubindex.cache_put("huggingface", "dataset", "tetris", [raw("huggingface", "a/b")])
    hit = hubindex.cache_get("huggingface", "dataset", "  Tetris ", s)
    assert [r.external_id for r in hit] == ["a/b"] and hit[0].meta["from_cache"]
    assert hubindex.cache_get("kaggle", "dataset", "tetris", s) is None
    assert hubindex.cache_get("huggingface", "dataset", "bird photos", s) is None


def test_search_uses_cache_then_index(monkeypatch):
    hf = FakeConnector("huggingface", {"dataset", "model"}, [raw("huggingface", "u/tetris", description="tetris")])
    fakes = {"huggingface": hf, "kaggle": FakeConnector("kaggle", {"dataset"}),
             "openml": FakeConnector("openml", {"dataset"}), "github": FakeConnector("github", {"dataset", "code"})}
    monkeypatch.setattr(jobs, "build_connectors", lambda client, settings: fakes)
    hubindex.upsert_entries([raw("openml", "42", title="tetris-logs (v1)", description="tetris logs")])
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"goal": "classify tetris boards"}).json()
        need = project["needs"][0]

        def search():
            client.post(f"/api/projects/{project['id']}/search", json={"need_ids": [need["id"]]})
            for _ in range(200):
                if client.get(f"/api/projects/{project['id']}").json()["needs"][0]["status"] != "searching":
                    return client.get(f"/api/projects/{project['id']}/results").json()
                asyncio.run(asyncio.sleep(0.02))

        first = search()
        assert any(r["source"] == "openml" and r["meta"].get("from_index") for r in first)
        calls = len(hf.searched)
        second = search()
        assert len(hf.searched) == calls  # served from the semantic cache
        assert any(r["meta"].get("from_cache") for r in second if r["source"] == "huggingface")


@respx.mock
async def test_openml_connector_and_sync(tmp_path):
    listing = {"data": {"dataset": [{"did": 61, "name": "iris", "version": 1, "format": "ARFF",
                                     "quality": [{"name": "NumberOfInstances", "value": "150.0"}]}]}}
    respx.get("https://www.openml.org/api/v1/json/data/list/data_name/iris/limit/5/status/active").respond(json=listing)
    respx.get("https://www.openml.org/api/v1/json/data/list/data_name/nothing/limit/5/status/active").respond(status_code=412)
    respx.get("https://www.openml.org/api/v1/json/data/list/limit/1000/offset/0/status/active").respond(json=listing)
    respx.get("https://www.openml.org/api/v1/json/data/61").respond(
        json={"data_set_description": {"name": "iris", "parquet_url": "https://minio.example/iris.pq"}})
    respx.get("https://minio.example/iris.pq").respond(content=b"PAR1")
    async with httpx.AsyncClient() as client:
        conn = OpenMLConnector(client)
        [r] = await conn.search("iris", "dataset", 5)
        assert r.title == "iris (v1)" and r.description == "150 rows"
        assert await conn.search("nothing", "dataset", 5) == []
        assert await conn.download("61", "dataset", tmp_path, 100) == 4
        report = await hubindex.sync(Settings(), client, sources=("openml",))
    assert report["openml:dataset"] == 1
    assert hubindex.stats()["entries"][0]["count"] == 1
