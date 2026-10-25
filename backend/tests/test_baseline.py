import asyncio

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.baseline import BaselineError, suggested_exclusions, target_candidates, train
from app.main import app
from app.pipelines import Env, run
from app.pipelines.engine import linear
from test_api import _wait_version


def test_target_candidates_and_exclusions():
    schema = [{"name": n, "type": t} for n, t in
              [("state", "list<item: uint8>"), ("rotation", "int8"), ("column", "int8"), ("reward", "int32"), ("split", "string")]]
    assert target_candidates(schema) == ["rotation", "column", "reward"]
    assert suggested_exclusions(["state", "rotation", "column", "reward", "lines"], "column") == ["lines", "reward", "rotation"]


def test_classification_on_tetris(tmp_path):
    t = run(linear(("tetris_synthetic", {"games": 8, "max_steps": 60}), ("split", {"group": "game"})),
            Env({}, tmp_path, log=lambda m: None)).table
    pq.write_table(t, tmp_path / "t.parquet")
    r = train(tmp_path / "t.parquet", "column", {}, tmp_path / "m.joblib")
    assert r["task"] == "classification" and r["split"] == "split column"
    assert r["excluded"] == ["done", "lines", "reward", "rotation"]
    assert {f["column"] for f in r["features"]} == {"state", "piece", "next_piece"}
    assert r["metrics"]["accuracy"] > r["metrics"]["baseline_accuracy"]
    assert len(r["confusion"]["labels"]) == len(r["confusion"]["matrix"])
    assert (tmp_path / "m.joblib").exists()


def test_regression_and_text(tmp_path):
    rows = [{"text": f"value {i % 7} item", "size": float(i), "price": 3.0 * i + 1} for i in range(200)]
    pq.write_table(pa.Table.from_pylist(rows), tmp_path / "r.parquet")
    r = train(tmp_path / "r.parquet", "price", {})
    assert r["task"] == "regression" and r["metrics"]["r2"] > 0.9
    assert r["metrics"]["mae"] < r["metrics"]["baseline_mae"]


def test_image_baseline(tmp_path):
    root = tmp_path / "imgs"
    root.mkdir()
    rows = []
    for i in range(40):
        name = f"{i}.png"
        Image.new("L", (20, 20), color=255 if i % 2 else 0).save(root / name)
        rows.append({"asset_id": 1, "path": name, "label": "white" if i % 2 else "black"})
    pq.write_table(pa.Table.from_pylist(rows), tmp_path / "i.parquet")
    r = train(tmp_path / "i.parquet", "label", {1: root})
    assert r["features"] == [{"column": "path", "as": "pixels[16x16]"}] and r["metrics"]["accuracy"] == 1.0


def test_errors(tmp_path):
    pq.write_table(pa.table({"label": ["a"] * 20, "x": list(range(20))}), tmp_path / "e.parquet")
    with pytest.raises(BaselineError, match="single class"):
        train(tmp_path / "e.parquet", "label", {})
    with pytest.raises(BaselineError, match="no column"):
        train(tmp_path / "e.parquet", "nope", {})


def test_baseline_api():
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"goal": "tetris ai"}).json()["id"]
        g = linear(("tetris_synthetic", {"games": 4, "max_steps": 40}), ("split", {"group": "game"}))
        pipe = client.post(f"/api/projects/{pid}/pipelines", json={"graph": g}).json()
        v = _wait_version(client, client.post(f"/api/pipelines/{pipe['id']}/run").json()["id"])
        opts = client.get(f"/api/versions/{v['id']}/baseline-options", params={"target": "column"}).json()
        assert "column" in opts["targets"] and opts["suggested_exclude"] == ["done", "lines", "reward", "rotation"]
        assert "game" not in opts["columns"] and "state" in opts["columns"]
        assert client.post(f"/api/versions/{v['id']}/baselines", json={"target": "nope"}).status_code == 422
        run_id = client.post(f"/api/versions/{v['id']}/baselines", json={"target": "column", "exclude": ["reward"]}).json()["id"]
        for _ in range(300):
            [b] = client.get(f"/api/versions/{v['id']}/baselines").json()
            if b["status"] in ("done", "error"):
                break
            asyncio.run(asyncio.sleep(0.05))
        assert b["status"] == "done", b
        assert b["result"]["excluded"] == ["reward"]
        model = client.get(f"/api/baselines/{run_id}/model")
        assert model.status_code == 200 and len(model.content) > 100
