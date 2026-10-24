"""Image-dataset path: HF Parquet with embedded images, folder labels, relabel, leak-safe split, YOLO export, baseline."""
import json
from pathlib import Path

import cv2
import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from dataforge import baseline, datasets
from dataforge.gather import sample_evenly
from dataforge.pipelines.nodes import _folder_label, _parquet_images, n_images, n_relabel, n_split


def _png(color) -> bytes:
    img = np.zeros((32, 32, 3), np.uint8)
    img[:] = color
    return cv2.imencode(".png", img)[1].tobytes()


def test_parquet_embedded_images_use_classlabel_names(tmp_path):
    imgs = [{"bytes": _png((0, 200, 0)), "path": "a.png"}, {"bytes": _png((0, 0, 200)), "path": "b.png"}]
    t = pa.table({"image": imgs, "label": [0, 1]})
    meta = {b"huggingface": json.dumps({"info": {"features": {"image": {"_type": "Image"},
                                                             "label": {"names": ["healthy", "rust"], "_type": "ClassLabel"}}}}).encode()}
    p = tmp_path / "train" / "0000.parquet"
    p.parent.mkdir()
    pq.write_table(t.replace_schema_metadata(meta), p)
    rows = _parquet_images(p, tmp_path / "out")
    assert [r["label"] for r in rows] == ["healthy", "rust"]
    assert rows[0]["orig_split"] == "train" and Path(rows[0]["image_path"]).exists()


def test_folder_labels_skip_generic_dirs(tmp_path):
    f = tmp_path / "tomato-dataset" / "val" / "Tomato___Leaf_Mold" / "x.jpg"
    assert _folder_label(f, [tmp_path]) == ("Tomato___Leaf_Mold", "validation")  # split folder above the class
    g = tmp_path / "maize" / "v1" / "train" / "Healthy" / "images" / "y.jpg"
    assert _folder_label(g, [tmp_path]) == ("Healthy", "train")
    h = tmp_path / "set" / "test" / "Rust" / "z.jpg"
    assert _folder_label(h, [tmp_path])[0] == "Rust"


def test_images_node_hashes_and_drops_corrupt(tmp_path):
    good, dup, bad = tmp_path / "rust" / "a.png", tmp_path / "rust" / "b.png", tmp_path / "rust" / "c.png"
    good.parent.mkdir()
    good.write_bytes(_png((0, 0, 200)))
    dup.write_bytes(_png((0, 0, 200)))
    bad.write_bytes(b"not an image")
    ctx = n_images({"files": [good, dup, bad], "asset_rows": [{"storage_path": str(tmp_path), "result_id": None}], "notes": [], "table": None}, {}, None)
    t = ctx["table"]
    assert t.height == 2 and t["image_hash"].n_unique() == 1 and set(t["label"]) == {"rust"}


def test_relabel_and_split_keep_heldout():
    df = pl.DataFrame({"label": ["Tomato___Late_blight", "MLN", "Tomato___Tomato_mosaic_virus", "MLN"] * 10,
                       "orig_split": ["validation", "train"] * 20})
    ctx = n_relabel({"table": df, "notes": []}, {"mapping": "MLN=Maize · lethal necrosis", "tidy": True}, None)
    assert set(ctx["table"]["label"]) == {"Tomato · late blight", "Tomato · mosaic virus", "Maize · lethal necrosis"}
    ctx = n_split(ctx, {"stratify": "label"}, None)
    t = ctx["table"]
    # authors' validation rows stay validation; a test split is carved only from the free (train) rows
    assert (t.filter(pl.col("orig_split") == "validation")["split"] == "validation").all()
    assert set(t.filter(pl.col("orig_split") == "train")["split"]) == {"train", "test"}


def test_sample_evenly_keeps_every_class():
    files = [(f"a/{i}.jpg", "u") for i in range(500)] + [(f"b/{i}.jpg", "u") for i in range(3)]
    out = sample_evenly(files, 50)
    assert len(out) == 50 and sum(1 for f in out if f[0].startswith("b/")) == 3


def test_yolo_classify_export_and_image_baseline(tmp_path):
    rows = []
    for i in range(60):
        lab, col = ("healthy", (0, 180, 0)) if i % 2 else ("blight", (20, 60, 120))
        p = tmp_path / lab / f"{i}.png"
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(_png(tuple(c + (i % 7) for c in col)))
        rows.append({"image_path": str(p), "label": lab, "split": "train" if i < 48 else "validation"})
    df = pl.DataFrame(rows)
    out = tmp_path / "yolo"
    out.mkdir()
    datasets._export_vision({s: df.filter(pl.col("split") == s) for s in ("train", "validation")}, out, "yolo")
    assert len(list((out / "train" / "healthy").glob("*.png"))) == 24
    assert (out / "val" / "blight").is_dir() and (out / "classes.txt").read_text().split() == ["blight", "healthy"]
    r = baseline.train(df, "label")
    assert r["accuracy"] == 1.0 and "colour" in r["model"]


def test_size_cap_keeps_finished_files(tmp_path, monkeypatch):
    import asyncio

    from dataforge import db, gather

    async def fake_stream(url, dest, budget, job, label, headers=None):
        budget.take(100)  # each file 100 bytes; cap allows two
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x" * 100)
        return 100, "sha"

    class J:
        def log(self, *a, **k): pass
        def progress(self, *a, **k): pass

    monkeypatch.setattr(gather, "stream_to", fake_stream)
    pid = db.insert("projects", goal="cap test", plan_json={})["id"]
    files = [{"name": f"c{i}/{i}.jpg", "url": f"u{i}"} for i in range(5)]
    r = db.insert("search_results", project_id=pid, source="zenodo", kind="dataset", url="x", title="t",
                  meta_json={"download": {"type": "files", "files": files}})
    res = asyncio.run(gather.gather_one(J(), {**r, "meta": {"download": {"type": "files", "files": files}}}, gather.Budget(250)))
    assert res["status"] == "partial" and res["files"] == 2
    a = db.query("SELECT status, error FROM assets WHERE result_id = ?", (r["id"],))[0]
    assert a["status"] == "done" and a["error"].startswith("partial")


def test_parquet_export_does_not_clobber_version_data(tmp_path, monkeypatch):
    import asyncio

    from dataforge import db
    pid = db.insert("projects", goal="export test", plan_json={})["id"]
    df = pl.DataFrame({"x": [1, 2, 3], "label": ["a", "b", "a"]})
    v = asyncio.run(datasets.create_version({"id": pid, "goal": "export test", "plan": {}}, df, ["n"]))
    for fmt in ("parquet", "csv", "parquet"):
        row = db.get("dataset_versions", v["id"])
        datasets.export(row, fmt)
    row = db.get("dataset_versions", v["id"])
    assert datasets.load(row).height == 3 and row["export_paths"]["parquet"].endswith(".zip")
