import json
import struct
import zipfile

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import exports
from app.exports import ExportError, _masked_crc, check, export


def _read_varint(buf, i):
    shift = result = 0
    while True:
        b = buf[i]
        result |= (b & 0x7F) << shift
        i += 1
        if not b & 0x80:
            return result, i
        shift += 7


def _fields(buf):
    i, out = 0, []
    while i < len(buf):
        key, i = _read_varint(buf, i)
        n, i = _read_varint(buf, i)
        out.append((key >> 3, buf[i : i + n]))
        i += n
    return out


def decode_example(data: bytes) -> dict:
    """Minimal tf.train.Example decoder for the round-trip test."""
    [(_, features)] = _fields(data)
    row = {}
    for _, entry in _fields(features):
        parts = dict(_fields(entry))
        key, feature = parts[1].decode(), dict(_fields(parts[2]))
        if 3 in feature:  # int64 list, packed
            packed = dict(_fields(feature[3])).get(1, b"")
            vals, i = [], 0
            while i < len(packed):
                v, i = _read_varint(packed, i)
                vals.append(v - (1 << 64) if v >= 1 << 63 else v)
            row[key] = vals
        elif 2 in feature:
            packed = dict(_fields(feature[2])).get(1, b"")
            row[key] = [round(x, 4) for x in struct.unpack(f"<{len(packed) // 4}f", packed)]
        else:
            row[key] = [v.decode() for _, v in _fields(feature[1])]
    return row


def read_tfrecord(path):
    data, i, out = path.read_bytes(), 0, []
    while i < len(data):
        (n,) = struct.unpack("<Q", data[i : i + 8])
        assert struct.unpack("<I", data[i + 8 : i + 12])[0] == _masked_crc(data[i : i + 8])
        payload = data[i + 12 : i + 12 + n]
        assert struct.unpack("<I", data[i + 12 + n : i + 16 + n])[0] == _masked_crc(payload)
        out.append(decode_example(payload))
        i += 16 + n
    return out


TABLE = pa.table({
    "q": ["1 + 1", "2 - 5", "x"],
    "answer": [2, -3, 0],
    "score": [0.5, 1.25, 2.0],
    "state": [[1, 0], [0, 1], [1, 1]],
    "split": ["train", "train", "test"],
})


@pytest.mark.parametrize("fmt", ["parquet", "csv", "jsonl", "hf", "tfrecord"])
def test_tabular_formats(tmp_path, fmt):
    files = {p.relative_to(tmp_path / "out").as_posix() for p in export(TABLE, fmt, tmp_path / "out", {}, "# Card")}
    if fmt == "hf":
        assert files == {"README.md", "data/train-00000-of-00001.parquet", "data/test-00000-of-00001.parquet"}
        assert pq.read_table(tmp_path / "out/data/train-00000-of-00001.parquet").num_rows == 2
        return
    ext = {"parquet": "parquet", "csv": "csv", "jsonl": "jsonl", "tfrecord": "tfrecord"}[fmt]
    assert files == {f"train.{ext}", f"test.{ext}", "DATASET_CARD.md"}
    if fmt == "csv":
        lines = (tmp_path / "out/train.csv").read_text().splitlines()
        assert lines[1].endswith('"[1, 0]","train"') or "[1, 0]" in lines[1]
    if fmt == "jsonl":
        assert json.loads((tmp_path / "out/test.jsonl").read_text())["state"] == [1, 1]
    if fmt == "tfrecord":
        rows = read_tfrecord(tmp_path / "out/train.tfrecord")
        assert rows[1] == {"q": ["2 - 5"], "answer": [-3], "score": [1.25], "state": [0, 1], "split": ["train"]}


def _images(tmp_path):
    root = tmp_path / "asset"
    rows = []
    for label in ("cat", "dog"):
        (root / label).mkdir(parents=True)
        for i in range(2):
            Image.new("RGB", (10, 20)).save(root / label / f"{i}.png")
            rows.append({"asset_id": 1, "path": f"{label}/{i}.png", "label": label, "width": 10, "height": 20,
                         "split": "train" if i == 0 else "val"})
    return root, pa.Table.from_pylist(rows)


def test_coco_and_yolo(tmp_path):
    root, table = _images(tmp_path)
    export(table, "coco", tmp_path / "coco", {1: root}, None)
    coco = json.loads((tmp_path / "coco/annotations_train.json").read_text())
    assert [c["name"] for c in coco["categories"]] == ["cat", "dog"]
    assert len(coco["images"]) == 2 and coco["annotations"][0]["bbox"] == [0, 0, 10, 20]
    export(table, "yolo", tmp_path / "yolo", {1: root}, None)
    assert sorted(p.parent.name for p in (tmp_path / "yolo/train").rglob("*.png")) == ["cat", "dog"]

    boxed = table.append_column("bbox", pa.array([[2, 4, 4, 8]] * 4))
    export(boxed, "yolo", tmp_path / "yolod", {1: root}, None)
    label = (tmp_path / "yolod/labels/train/0000000.txt").read_text().split()  # row 0 is a cat
    assert label == ["0", "0.400000", "0.400000", "0.400000", "0.400000"]
    assert (tmp_path / "yolod/data.yaml").exists()


def test_image_formats_need_columns(tmp_path):
    with pytest.raises(ExportError, match="asset_id, path and label"):
        export(TABLE, "coco", tmp_path / "x", {}, None)


def _src(title, cls, rights):
    return {"title": title, "license_class": cls, "rights": rights, "kind": "dataset", "url": None, "license": None}


OPEN = {"commercial": True, "redistribution": True, "attribution": True, "share_alike": False}
SA = {"commercial": True, "redistribution": True, "attribution": True, "share_alike": True}
NC = {"commercial": False, "redistribution": True, "attribution": True, "share_alike": False}
LOCAL = {"commercial": False, "redistribution": False, "attribution": True, "share_alike": False}
UNKNOWN = {"commercial": None, "redistribution": None, "attribution": None, "share_alike": None}


def test_license_gate():
    assert check([_src("a", "commercial", OPEN)], "hf", push=True)["allowed"]
    local = check([_src("vid", "local_only", LOCAL)], "parquet", push=False)
    assert local["allowed"] and "local-use-only" in local["warnings"][0]
    assert not check([_src("vid", "local_only", LOCAL)], "hf", push=True)["allowed"]
    mixed = check([_src("sa", "commercial", SA), _src("nc", "non_commercial", NC)], "hf", push=True)
    assert not mixed["allowed"] and "share-alike" in mixed["blockers"][0]
    unk = check([_src("u", "unknown", UNKNOWN)], "hf", push=True)
    assert not unk["allowed"] and unk["needs_acknowledgement"]
    assert check([_src("u", "unknown", UNKNOWN)], "hf", push=True, acknowledge_unknown=True)["allowed"]


def test_export_api_flow(monkeypatch):
    from test_api import _wait_version

    from app.main import app

    pushed = {}
    monkeypatch.setattr(exports, "push_to_hub", lambda folder, repo, token, private: pushed.update(repo=repo) or f"https://hf.co/{repo}")
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "hf_token", "hf_test")
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"goal": "arithmetic"}).json()["id"]
        pipe = client.post(f"/api/projects/{pid}/pipelines", json={"template": "arithmetic_synthetic"}).json()
        v = _wait_version(client, client.post(f"/api/pipelines/{pipe['id']}/run").json()["id"])
        assert set(client.get("/api/export-formats").json()) >= {"parquet", "hf", "tfrecord", "coco", "yolo"}
        assert client.get(f"/api/versions/{v['id']}/export-check", params={"format": "hf", "push": True}).json()["allowed"]

        def wait(eid):
            for _ in range(200):
                e = next(x for x in client.get(f"/api/versions/{v['id']}/exports").json() if x["id"] == eid)
                if e["status"] in ("done", "error"):
                    return e
                import asyncio

                asyncio.run(asyncio.sleep(0.02))

        e = wait(client.post(f"/api/versions/{v['id']}/exports", json={"format": "csv"}).json()["id"])
        assert e["status"] == "done", e
        resp = client.get(f"/api/exports/{e['id']}/download")
        assert resp.status_code == 200
        import io

        names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
        assert names == ["DATASET_CARD.md", "train.csv"]

        bad = client.post(f"/api/versions/{v['id']}/exports", json={"format": "csv", "push_to_hub": True, "repo_id": "me/x"})
        assert bad.status_code == 422
        h = wait(client.post(f"/api/versions/{v['id']}/exports",
                             json={"format": "hf", "push_to_hub": True, "repo_id": "me/arith"}).json()["id"])
        assert h["status"] == "done" and h["hub_url"] == "https://hf.co/me/arith" and pushed["repo"] == "me/arith"
        assert client.get(f"/api/versions/{v['id']}").json()["export_paths"]["hub"] == "https://hf.co/me/arith"
