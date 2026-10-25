import asyncio

import pytest
from fastapi.testclient import TestClient

from app import jobs
from app.main import app
from conftest import FakeConnector, raw


@pytest.fixture
def connectors(monkeypatch):
    fakes = {
        "huggingface": FakeConnector(
            "huggingface",
            {"dataset", "model"},
            [
                raw("huggingface", "user/tetris-games", description="Logged tetris games", license="mit", downloads=5000),
                raw("huggingface", "user/cats", description="cat photos", license="cc-by-nc-4.0"),
                raw("huggingface", "user/tetris-dqn", kind="model", license="apache-2.0"),
            ],
        ),
        "kaggle": FakeConnector("kaggle", {"dataset"}, fail=True),
        "github": FakeConnector(
            "github",
            {"dataset", "model", "environment", "code"},
            [raw("github", "other/tetris-games", description="mirror", kind="dataset")],
        ),
    }
    monkeypatch.setattr(jobs, "build_connectors", lambda client, settings: fakes)
    return fakes


def _wait(client, project_id, predicate, path=""):
    for _ in range(100):
        data = client.get(f"/api/projects/{project_id}{path}").json()
        if predicate(data):
            return data
        asyncio.run(asyncio.sleep(0.02))
    raise AssertionError(f"timed out: {data}")


def test_full_flow(connectors):
    with TestClient(app) as client:
        assert client.get("/api/health").json()["planner"] == "heuristic"

        project = client.post("/api/projects", json={"goal": "I want to train a Tetris AI"}).json()
        pid = project["id"]
        assert project["approach"] == "reinforcement_learning"
        assert len(project["needs"]) == 6

        # Edit the plan before searching.
        code_need = project["needs"][-1]
        assert client.delete(f"/api/needs/{code_need['id']}").status_code == 204
        added = client.post(
            f"/api/projects/{pid}/needs",
            json={"description": "Gameplay logs", "kind": "dataset", "queries": ["tetris replays"]},
        ).json()
        assert set(added["queries"]) == {"huggingface", "kaggle", "openml", "github"}

        assert client.post(f"/api/projects/{pid}/search").status_code == 202
        project = _wait(client, pid, lambda p: all(n["status"] in ("done", "error") for n in p["needs"]))
        dataset_need = project["needs"][0]
        assert dataset_need["status"] == "done"  # kaggle failed, others worked
        assert "kaggle is down" in dataset_need["error"]

        results = client.get(f"/api/projects/{pid}/results", params={"need_id": dataset_need["id"]}).json()
        assert results == sorted(results, key=lambda r: -r["score"])
        top = results[0]
        assert top["external_id"] == "user/tetris-games"
        assert top["license_class"] == "commercial"
        assert top["meta"]["possible_mirrors"] == ["github:other/tetris-games"]
        nc = client.get(f"/api/projects/{pid}/results", params={"license_class": "non_commercial"}).json()
        assert {r["external_id"] for r in nc} == {"user/cats"}

        resp = client.post(f"/api/projects/{pid}/downloads", json={"result_ids": [top["id"]]})
        assert resp.status_code == 202
        assets = _wait(client, pid, lambda a: a and a[0]["status"] == "done", "/assets")
        assert assets[0]["bytes_downloaded"] == 5 and len(assets[0]["checksum"]) == 64

        # Re-requesting a downloaded result reuses the asset.
        again = client.post(f"/api/projects/{pid}/downloads", json={"result_ids": [top["id"]]}).json()
        assert again[0]["id"] == assets[0]["id"]


def test_download_rejects_foreign_results(connectors):
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"goal": "tetris"}).json()["id"]
        resp = client.post(f"/api/projects/{pid}/downloads", json={"result_ids": [999]})
        assert resp.status_code == 404


def test_validation():
    with TestClient(app) as client:
        assert client.post("/api/projects", json={"goal": ""}).status_code == 422
        assert client.get("/api/projects/42").status_code == 404


def test_restart_fails_interrupted_jobs():
    from app.db import SessionLocal
    from app.models import Asset, DataNeed, Project, SearchResult

    with SessionLocal() as db:
        need = DataNeed(description="d", kind="dataset", queries={}, status="searching")
        result = SearchResult(source="huggingface", kind="dataset", external_id="a/b", url="u", title="a/b")
        need.results.append(result)
        result.assets.append(Asset(status="downloading"))
        db.add(Project(goal="g", needs=[need]))
        db.commit()
        pid = need.project_id

    with TestClient(app) as client:
        project = client.get(f"/api/projects/{pid}").json()
        assert project["needs"][0]["status"] == "error"
        [asset] = client.get(f"/api/projects/{pid}/assets").json()
        assert asset["status"] == "error" and "restart" in asset["error"]


async def test_download_progress_reported(tmp_path, monkeypatch):
    from app.db import SessionLocal
    from app.models import Asset, DataNeed, Project, SearchResult

    with SessionLocal() as db:
        need = DataNeed(description="d", kind="dataset", queries={})
        result = SearchResult(source="huggingface", kind="dataset", external_id="a/b", url="u", title="a/b")
        need.results.append(result)
        asset = Asset()
        result.assets.append(asset)
        db.add(Project(goal="g", needs=[need]))
        db.commit()
        asset_id = asset.id

    seen = []

    class Slow(FakeConnector):
        async def download(self, external_id, kind, dest, max_bytes):
            (dest / "part").write_bytes(b"x" * 10)
            await asyncio.sleep(0.15)
            with SessionLocal() as db:
                seen.append(db.get(Asset, asset_id).bytes_downloaded)
            return 10

    monkeypatch.setattr(jobs, "_report_progress", _fast_progress)
    settings = jobs.get_settings().model_copy(update={"data_dir": tmp_path})
    await jobs.download_asset(
        asset_id, client=object(), settings=settings, connectors={"huggingface": Slow("huggingface", {"dataset"})}
    )
    assert seen == [10]
    with SessionLocal() as db:
        assert db.get(Asset, asset_id).status == "done"


_orig_progress = jobs._report_progress


def _fast_progress(asset_id, dest, **kw):
    return _orig_progress(asset_id, dest, interval=0.05, **kw)


def test_paper_links_are_followed(monkeypatch):
    class Papers(FakeConnector):
        pass

    paper = raw(
        "arxiv", "1905.01652", kind="paper", title="Tetris RL", description="tetris reinforcement learning",
        meta={"links": [{"source": "huggingface", "kind": "dataset", "external_id": "foo/tetris-logs",
                         "url": "https://huggingface.co/datasets/foo/tetris-logs"}]},
    )

    class HF(FakeConnector):
        async def resolve(self, ref):
            return raw("huggingface", ref.external_id, kind=ref.kind, license="mit")

    fakes = {"arxiv": Papers("arxiv", {"paper"}, [paper]), "semanticscholar": FakeConnector("semanticscholar", {"paper"}),
             "huggingface": HF("huggingface", {"dataset", "model"})}
    monkeypatch.setattr(jobs, "build_connectors", lambda client, settings: fakes)
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"goal": "I want to train a Tetris AI"}).json()
        pid = project["id"]
        need = next(n for n in project["needs"] if n["kind"] == "paper")
        client.post(f"/api/projects/{pid}/search", json={"need_ids": [need["id"]]})
        _wait(client, pid, lambda p: all(n["status"] != "searching" for n in p["needs"]))
        results = client.get(f"/api/projects/{pid}/results", params={"need_id": need["id"]}).json()
        followed = next(r for r in results if r["external_id"] == "foo/tetris-logs")
        assert followed["kind"] == "dataset"
        assert followed["meta"]["via_paper"]["id"] == "1905.01652"
        assert followed["relevance"] > 0


def _wait_version(client, version_id):
    for _ in range(300):
        v = client.get(f"/api/versions/{version_id}").json()
        if v["status"] != "running":
            return v
        asyncio.run(asyncio.sleep(0.05))
    raise AssertionError("version still running")


def test_synthetic_pipeline_flow():
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"goal": "I want to train a Tetris AI"}).json()["id"]
        advice = client.get(f"/api/projects/{pid}/synthetic").json()
        assert advice["recommended"] and advice["template"] == "tetris_synthetic"

        templates = {t["id"] for t in client.get("/api/pipeline-templates").json()}
        assert {"video_state_action", "tetris_synthetic", "arithmetic_synthetic"} <= templates
        steps = {s["type"]: s for s in client.get("/api/pipeline-steps").json()}
        assert steps["tetris_synthetic"]["is_source"]

        pipe = client.post(f"/api/projects/{pid}/pipelines", json={"template": "tetris_synthetic"}).json()
        graph = pipe["graph_json"]
        graph["nodes"][0]["params"] = {"games": 2, "max_steps": 30}
        assert client.put(f"/api/pipelines/{pipe['id']}", json={"graph": graph}).status_code == 200

        v1 = client.post(f"/api/pipelines/{pipe['id']}/run").json()
        v1 = _wait_version(client, v1["id"])
        assert v1["status"] == "done", v1
        assert v1["version"] == 1 and v1["row_count"] > 0
        assert {"name": "state", "type": "list<item: uint8>"} in v1["columns"]

        page = client.get(f"/api/versions/{v1['id']}/rows", params={"offset": 1, "limit": 3}).json()
        assert page["total"] == v1["row_count"] and len(page["rows"]) == 3
        assert page["rows"][0]["step"] == 1

        v2 = _wait_version(client, client.post(f"/api/pipelines/{pipe['id']}/run").json()["id"])
        assert v2["version"] == 2
        assert [v["version"] for v in client.get(f"/api/projects/{pid}/versions").json()] == [2, 1]


def test_invalid_graph_rejected():
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"goal": "tetris"}).json()["id"]
        bad = {"graph": {"nodes": [{"id": "a", "type": "grid_sample"}], "edges": []}}
        resp = client.post(f"/api/projects/{pid}/pipelines", json=bad)
        assert resp.status_code == 422 and "needs an input" in resp.json()["detail"]
        assert client.post(f"/api/projects/{pid}/pipelines", json={"template": "nope"}).status_code == 404


def test_pipeline_failure_recorded():
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"goal": "tetris"}).json()["id"]
        pipe = client.post(
            f"/api/projects/{pid}/pipelines", json={"template": "video_state_action", "asset_ids": [12345]}
        ).json()
        v = _wait_version(client, client.post(f"/api/pipelines/{pipe['id']}/run").json()["id"])
        assert v["status"] == "error" and "not downloaded" in v["error"]
        assert client.get(f"/api/versions/{v['id']}/rows").status_code == 409


def test_queue_unavailable_returns_503(monkeypatch):
    from app import jobqueue
    from app.config import get_settings

    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"goal": "tetris"}).json()["id"]
        monkeypatch.setattr(get_settings(), "redis_url", "redis://127.0.0.1:1")
        monkeypatch.setattr(jobqueue, "_pool", None)
        resp = client.post(f"/api/projects/{pid}/search")
        assert resp.status_code == 503 and "queue unavailable" in resp.json()["detail"]
        needs = client.get(f"/api/projects/{pid}").json()["needs"]
        assert all(n["status"] == "error" for n in needs)


def test_version_stats_card_and_diff():
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"goal": "solve arithmetic"}).json()["id"]
        graph = {"nodes": [{"id": "n1", "type": "arithmetic_synthetic", "params": {"count": 200, "operators": "+"}},
                           {"id": "n2", "type": "split", "params": {"seed": 1}}],
                 "edges": [{"source": "n1", "target": "n2"}]}
        pipe = client.post(f"/api/projects/{pid}/pipelines", json={"graph": graph}).json()
        v1 = _wait_version(client, client.post(f"/api/pipelines/{pipe['id']}/run").json()["id"])
        assert v1["status"] == "done", v1
        split = next(c for c in v1["stats"]["columns"] if c["name"] == "split")
        assert {t["value"] for t in split["top"]} == {"train", "val", "test"}
        card = v1["card_md"]
        assert "## Splits" in card and "Synthetic arithmetic" in card and "simulated data" in card

        assert client.get(f"/api/versions/{v1['id']}/diff").status_code == 404  # nothing earlier

        graph["nodes"][0]["params"]["count"] = 250
        client.put(f"/api/pipelines/{pipe['id']}", json={"graph": graph})
        v2 = _wait_version(client, client.post(f"/api/pipelines/{pipe['id']}/run").json()["id"])
        d = client.get(f"/api/versions/{v2['id']}/diff").json()
        assert d["base"]["version"] == 1 and d["rows"]["delta"] == 50
        assert d["rows"]["added"] >= 50

        regen = client.post(f"/api/versions/{v1['id']}/card").json()
        assert regen["card_md"].startswith("---")
