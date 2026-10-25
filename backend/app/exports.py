"""Export dataset versions to training formats, with license gating.

Formats: parquet, csv, jsonl, hf (Hugging Face layout, optionally pushed to
the Hub), coco and yolo (image datasets), tfrecord (tf.train.Example).
"""

import json
import shutil
import struct
import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from .cards import combined_rights

FORMATS = {
    "parquet": "Apache Parquet, one file per split",
    "csv": "CSV, one file per split (nested values as JSON)",
    "jsonl": "JSON Lines, one file per split",
    "hf": "Hugging Face dataset layout (data/*.parquet + README card), optionally pushed to the Hub",
    "coco": "COCO JSON with copied images (needs asset_id, path, label; bbox optional)",
    "yolo": "YOLO: classification folders, or images/labels with bbox",
    "tfrecord": "TFRecord of tf.train.Example, one file per split",
}
PUBLIC_FORMATS = {"hf_push"}


class ExportError(Exception):
    pass


# ---- license gate -----------------------------------------------------------

def check(sources: list[dict], fmt: str, push: bool, acknowledge_unknown: bool = False) -> dict:
    """Decide whether an export may proceed. Local exports warn; publishing blocks."""
    rights = combined_rights(sources)
    warnings, blockers = [], []
    local_only = [s["title"] for s in sources if s["license_class"] == "local_only"]
    no_redist = [s["title"] for s in sources if s["rights"]["redistribution"] is False]
    unknown = [s["title"] for s in sources if s["rights"]["redistribution"] is None]
    share_alike = [s["title"] for s in sources if s["rights"]["share_alike"]]
    restricted = [s["title"] for s in sources if s["license_class"] in ("non_commercial", "research_only")]
    if local_only:
        warnings.append(f"Contains local-use-only media ({', '.join(local_only[:3])}): keep this export private.")
    if rights["commercial"] is False:
        warnings.append("At least one source forbids commercial use; don't use this export commercially.")
    if rights["attribution_required"]:
        warnings.append(f"Attribution required for {len(rights['attribution_required'])} source(s); keep the dataset card with the data.")
    if push:
        if no_redist:
            blockers.append(f"Can't publish: redistribution not allowed by {', '.join(no_redist[:3])}.")
        if share_alike and restricted:
            blockers.append(
                f"Can't publish: share-alike sources ({', '.join(share_alike[:2])}) can't be combined with "
                f"non-commercial or research-only terms ({', '.join(restricted[:2])})."
            )
        if unknown and not acknowledge_unknown:
            blockers.append(f"Unknown redistribution terms for {', '.join(unknown[:3])}; confirm you have the right to publish.")
    return {"allowed": not blockers, "blockers": blockers, "warnings": warnings, "rights": rights,
            "needs_acknowledgement": bool(push and unknown and not acknowledge_unknown)}


# ---- helpers ----------------------------------------------------------------

def _splits(table: pa.Table) -> dict[str, pa.Table]:
    if "split" not in table.column_names:
        return {"train": table}
    out = {}
    for name in pc.unique(table.column("split")).to_pylist():
        key = name if name else "unsplit"
        out[key] = table.filter(pc.equal(table.column("split"), name)) if name else table.filter(pc.is_null(table.column("split")))
    return out


def _flatten_for_csv(t: pa.Table) -> pa.Table:
    cols = {}
    for f in t.schema:
        col = t.column(f.name)
        if pa.types.is_list(f.type) or pa.types.is_large_list(f.type) or pa.types.is_struct(f.type) or pa.types.is_map(f.type):
            cols[f.name] = pa.array([None if v is None else json.dumps(v, default=str) for v in col.to_pylist()], pa.string())
        else:
            cols[f.name] = col
    return pa.table(cols)


def _image_rows(table: pa.Table, asset_paths: dict[int, Path]):
    for col in ("asset_id", "path", "label"):
        if col not in table.column_names:
            raise ExportError(f"image formats need columns asset_id, path and label (missing {col}); use the Load images step")
    for row in table.to_pylist():
        root = asset_paths.get(row["asset_id"])
        if root is None:
            raise ExportError(f"asset {row['asset_id']} is not downloaded")
        src = (root / row["path"]).resolve()
        if root.resolve() not in src.parents or not src.is_file():
            raise ExportError(f"image {row['path']} not found in asset {row['asset_id']}")
        yield row, src


# ---- TFRecord (no TensorFlow dependency) ------------------------------------

def _crc32c_table():
    poly, table = 0x82F63B78, []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ poly if c & 1 else c >> 1
        table.append(c)
    return table


_CRC_TABLE = _crc32c_table()


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for b in data:
        crc = _CRC_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def _masked_crc(data: bytes) -> int:
    crc = crc32c(data)
    return (((crc >> 15) | (crc << 17)) + 0xA282EAD8) & 0xFFFFFFFF


def _varint(n: int) -> bytes:
    n &= (1 << 64) - 1  # negative int64 as two's complement
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _field(num: int, payload: bytes) -> bytes:
    return _varint(num << 3 | 2) + _varint(len(payload)) + payload


def _feature(value) -> bytes:
    values = value if isinstance(value, list) else [value]
    values = [v for v in values if v is not None]
    if all(isinstance(v, bool) or isinstance(v, int) for v in values):
        packed = b"".join(_varint(int(v)) for v in values)
        return _field(3, _field(1, packed) if values else b"")  # Int64List
    if all(isinstance(v, (int, float)) for v in values):
        packed = b"".join(struct.pack("<f", float(v)) for v in values)
        return _field(2, _field(1, packed) if values else b"")  # FloatList
    items = b"".join(_field(1, v if isinstance(v, bytes) else str(v).encode()) for v in values)
    return _field(1, items)  # BytesList


def encode_example(row: dict) -> bytes:
    entries = b"".join(_field(1, _field(1, k.encode()) + _field(2, _feature(v))) for k, v in row.items())
    return _field(1, entries)  # Example.features


def write_tfrecord(path: Path, table: pa.Table) -> None:
    with path.open("wb") as fh:
        for row in table.to_pylist():
            data = encode_example({k: (json.dumps(v) if isinstance(v, dict) else v) for k, v in row.items()})
            length = struct.pack("<Q", len(data))
            fh.write(length + struct.pack("<I", _masked_crc(length)) + data + struct.pack("<I", _masked_crc(data)))


# ---- writers ----------------------------------------------------------------

def export(table: pa.Table, fmt: str, out: Path, asset_paths: dict[int, Path], card_md: str | None) -> list[Path]:
    if fmt not in FORMATS:
        raise ExportError(f"unknown format {fmt!r}")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    splits = _splits(table)
    if fmt == "parquet":
        for name, t in splits.items():
            pq.write_table(t, out / f"{name}.parquet")
    elif fmt == "csv":
        for name, t in splits.items():
            pacsv.write_csv(_flatten_for_csv(t), out / f"{name}.csv")
    elif fmt == "jsonl":
        for name, t in splits.items():
            with (out / f"{name}.jsonl").open("w") as fh:
                for row in t.to_pylist():
                    fh.write(json.dumps(row, default=str) + "\n")
    elif fmt == "hf":
        (out / "data").mkdir()
        for name, t in splits.items():
            pq.write_table(t, out / "data" / f"{name}-00000-of-00001.parquet")
        (out / "README.md").write_text(card_md or "# Dataset\n")
    elif fmt == "tfrecord":
        for name, t in splits.items():
            write_tfrecord(out / f"{name}.tfrecord", t)
    elif fmt == "coco":
        _coco(table, out, asset_paths)
    elif fmt == "yolo":
        _yolo(table, out, asset_paths)
    if card_md and fmt != "hf":
        (out / "DATASET_CARD.md").write_text(card_md)
    return sorted(p for p in out.rglob("*") if p.is_file())


def _coco(table: pa.Table, out: Path, asset_paths: dict[int, Path]) -> None:
    labels = sorted({str(v) for v in table.column("label").to_pylist() if v is not None} if "label" in table.column_names else [])
    cat_ids = {name: i + 1 for i, name in enumerate(labels)}
    per_split: dict[str, dict] = {}
    for i, (row, src) in enumerate(_image_rows(table, asset_paths)):
        split = row.get("split") or "train"
        doc = per_split.setdefault(split, {"images": [], "annotations": [],
                                           "categories": [{"id": v, "name": k} for k, v in cat_ids.items()]})
        name = f"{i:07d}{src.suffix.lower()}"
        (out / split).mkdir(exist_ok=True)
        shutil.copy2(src, out / split / name)
        w, h = row.get("width") or 0, row.get("height") or 0
        doc["images"].append({"id": i + 1, "file_name": name, "width": w, "height": h})
        if row.get("label") is not None:
            # Classification rows cover the whole image.
            bbox = row.get("bbox") or [0, 0, w, h]
            doc["annotations"].append({"id": len(doc["annotations"]) + 1, "image_id": i + 1,
                                       "category_id": cat_ids[str(row["label"])], "bbox": bbox,
                                       "area": float(bbox[2] * bbox[3]), "iscrowd": 0})
    for split, doc in per_split.items():
        (out / f"annotations_{split}.json").write_text(json.dumps(doc))


def _yolo(table: pa.Table, out: Path, asset_paths: dict[int, Path]) -> None:
    detection = "bbox" in table.column_names
    labels = sorted({str(v) for v in table.column("label").to_pylist() if v is not None})
    ids = {name: i for i, name in enumerate(labels)}
    for i, (row, src) in enumerate(_image_rows(table, asset_paths)):
        if row.get("label") is None:
            continue
        split = row.get("split") or "train"
        name = f"{i:07d}{src.suffix.lower()}"
        if detection:
            (out / "images" / split).mkdir(parents=True, exist_ok=True)
            (out / "labels" / split).mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out / "images" / split / name)
            x, y, w, h = row["bbox"]
            W, H = row.get("width") or 1, row.get("height") or 1
            line = f"{ids[str(row['label'])]} {(x + w / 2) / W:.6f} {(y + h / 2) / H:.6f} {w / W:.6f} {h / H:.6f}\n"
            (out / "labels" / split / f"{i:07d}.txt").write_text(line)
        else:
            dest = out / split / str(row["label"]).replace("/", "_")
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / name)
    if detection:
        names = "\n".join(f"  {i}: {n}" for n, i in ids.items())
        (out / "data.yaml").write_text(f"path: .\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n{names}\n")


def zip_dir(src: Path, dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(src))
    return dest


def push_to_hub(folder: Path, repo_id: str, token: str, private: bool = True) -> str:
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    api.create_repo(repo_id, repo_type="dataset", private=private, exist_ok=True)
    api.upload_folder(folder_path=str(folder), repo_id=repo_id, repo_type="dataset",
                      commit_message="Upload dataset from DataForge")
    return f"https://huggingface.co/datasets/{repo_id}"
