"""Dataset workspace (plan §3.6): versions, stats, cards, exports, diffs."""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path

import polars as pl

from . import db, licenses, llm
from .config import DATASETS_DIR, HF_TOKEN

EXPORT_FORMATS = ["parquet", "csv", "jsonl", "hf", "coco", "yolo", "tfrecord"]


def _vdir(project_id: str, version: int) -> Path:
    return DATASETS_DIR / project_id / f"v{version}"


def next_version(project_id: str) -> int:
    rows = db.query("SELECT MAX(version) AS v FROM dataset_versions WHERE project_id = ?", (project_id,))
    return (rows[0]["v"] or 0) + 1


def compute_stats(df: pl.DataFrame) -> dict:
    cols = []
    for c, t in df.schema.items():
        s = df[c]
        info = {"name": c, "dtype": str(t), "nulls": int(s.null_count()), "unique": int(s.n_unique()) if df.height < 2_000_000 else None}
        if t.is_numeric() and df.height:
            nn = s.drop_nulls()
            if nn.len():
                info.update(min=float(nn.min()), max=float(nn.max()), mean=float(nn.mean()), std=float(nn.std() or 0))
                try:
                    hist = nn.cast(pl.Float64).hist(bin_count=20)
                    info["hist"] = [{"x": round(float(b), 4), "n": int(n)} for b, n in zip(hist[hist.columns[0]], hist[hist.columns[-1]])]
                except Exception:  # noqa: BLE001
                    pass
        elif (t == pl.String or t == pl.Boolean or t == pl.Categorical) and df.height:
            vc = s.value_counts(sort=True).head(15)
            info["top"] = [{"value": str(v)[:60], "n": int(n)} for v, n in zip(vc[c], vc["count"])]
        cols.append(info)
    label = next((c for c in ("label", "action", "class", "target", "y", "category") if c in df.columns), None)
    balance = None
    if label:
        vc = df[label].value_counts(sort=True).head(30)
        balance = {"column": label, "counts": [{"value": str(v), "n": int(n)} for v, n in zip(vc[label], vc["count"])]}
    # Sample across classes so the preview grid shows every label, not just the first rows.
    if label and df.height and df[label].n_unique() > 1:
        per = max(1, 24 // min(24, df[label].n_unique()))
        sample = df.filter(pl.int_range(pl.len()).over(label) < per).head(24)
    else:
        sample = df.head(25)
    return {"rows": df.height, "columns": cols, "class_balance": balance, "has_images": "image_path" in df.columns,
            "sample": json.loads(sample.write_json()) if sample.height else []}


def _sources_for(project_id: str) -> list[dict]:
    rows = db.query("""SELECT DISTINCT r.id, r.title, r.url, r.source, r.license, r.license_class, r.kind
                       FROM assets a JOIN search_results r ON r.id = a.result_id
                       WHERE a.project_id = ? AND a.status = 'done'""", (project_id,))
    return rows


async def write_card(project: dict, version: int, stats: dict, sources: list[dict], notes: list[str], use_llm: bool = True) -> str:
    classes = [s["license_class"] or "unknown" for s in sources]
    ok, problems = licenses.compatible(classes)
    plan = project.get("plan") or {}
    schema_lines = "\n".join(f"| `{c['name']}` | {c['dtype']} | {c['nulls']:,} | {c.get('unique') if c.get('unique') is not None else '–'} |" for c in stats["columns"])
    src_lines = "\n".join(f"| [{s['title']}]({s['url']}) | {s['source']} | {s['license'] or 'unknown'} | {licenses.CLASSES.get(s['license_class'] or 'unknown')} |" for s in sources) or "| (synthetic / none) | | | |"
    bias = []
    if stats.get("class_balance"):
        cb = stats["class_balance"]["counts"]
        if cb and len(cb) > 1 and cb[0]["n"] > 5 * max(1, cb[-1]["n"]):
            bias.append(f"Class imbalance in `{stats['class_balance']['column']}`: most common value is {cb[0]['n'] / max(1, cb[-1]['n']):.0f}× the least common.")
    if any(s["kind"] == "video" for s in sources):
        bias.append("Video-derived labels are inferred heuristically (board detection + state diffing) and contain noise.")
    if any(s["source"] == "web" for s in sources):
        bias.append("Web text reflects the viewpoints and errors of its source sites.")
    if not sources:
        bias.append("Fully synthetic: distribution reflects the generator/agent policy, not human behaviour.")
    summary = ""
    if use_llm:
        out = await llm.complete_json(
            "Write a 2-sentence dataset summary for a dataset card. JSON: {\"summary\": \"...\"}",
            f"Goal: {project['goal']}\nRows: {stats['rows']}\nColumns: {[c['name'] for c in stats['columns']][:30]}\nSources: {[s['title'] for s in sources][:10]}\nSteps: {notes}")
        if isinstance(out, dict) and isinstance(out.get("summary"), str):
            summary = out["summary"].strip()
    if not summary:
        summary = (f"Dataset built by DataForge for the goal “{project['goal']}”. It contains {stats['rows']:,} rows "
                   f"and {len(stats['columns'])} columns assembled from {len(sources) or 'synthetic'} source(s).")
    lic_note = "Combined licence: **compatible for research use**." if ok else "Combined licence: **not compatible for commercial use**."
    return f"""---
license: other
pretty_name: {project['goal'][:60]}
tags: [dataforge]
---

# {project['goal']} — v{version}

{summary}

**Approach:** {(plan.get('approach') or {}).get('primary', 'n/a')} · **Built:** {time.strftime('%Y-%m-%d %H:%M')} · **Rows:** {stats['rows']:,}

## Sources & attribution
| Source | Hub | Licence | Usage class |
|---|---|---|---|
{src_lines}

{lic_note}
{chr(10).join('- ⚠ ' + p for p in problems)}

## Schema
| Column | Type | Nulls | Unique |
|---|---|---|---|
{schema_lines}

## Processing steps
{chr(10).join(f'{i + 1}. {n}' for i, n in enumerate(notes)) or '—'}

## Known biases & limitations
{chr(10).join('- ' + b for b in bias) or '- None detected automatically — review before use.'}

## Legal
YouTube / scraped media are for **local, non-redistributable** training only. Respect every source licence listed above.
"""


async def create_version(project: dict, df: pl.DataFrame, notes: list[str], extra_sources: list[dict] | None = None,
                         review: list | None = None, pii: dict | None = None) -> dict:
    pid = project["id"]
    v = next_version(pid)
    d = _vdir(pid, v)
    d.mkdir(parents=True, exist_ok=True)
    df.write_parquet(d / "data.parquet")
    stats = compute_stats(df)
    if pii is not None:
        stats["pii"] = pii
    sources = _sources_for(pid) + (extra_sources or [])
    card = await write_card(project, v, stats, sources, notes)
    (d / "README.md").write_text(card, encoding="utf-8")
    row = db.insert("dataset_versions", project_id=pid, version=v, schema_json=[{"name": c, "dtype": str(t)} for c, t in df.schema.items()],
                    row_count=df.height, card_md=card, export_paths_json={"data": str(d / "data.parquet")},
                    stats_json={**stats, "notes": notes, "review": review or []}, sources_json=sources)
    return {"id": row["id"], "version": v, "rows": df.height}


def load(version_row: dict) -> pl.DataFrame:
    return pl.read_parquet(data_path(version_row))


def data_path(version_row: dict) -> Path:
    """The version's own data file. Export archives live under per-format keys; older rows stored the data
    under "parquet", which a Parquet export used to overwrite with its zip — recover from that too."""
    paths = version_row.get("export_paths") or {}
    p = Path(paths.get("data") or paths["parquet"])
    if p.suffix == ".zip":  # <vN>/exports/parquet.zip → <vN>/data.parquet
        p = p.parent.parent / "data.parquet"
    return p


def export(version_row: dict, fmt: str, intended_use: str = "research", push_repo: str | None = None) -> dict:
    classes = [s.get("license_class") or "unknown" for s in version_row.get("sources") or []]
    ok, problems = licenses.compatible(classes, intended_use)
    if not ok:
        raise PermissionError("Export blocked by licence check: " + "; ".join(problems))
    df = load(version_row)
    d = data_path(version_row).parent
    out = d / "exports" / fmt
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    has_split = "split" in df.columns
    parts = {s: df.filter(pl.col("split") == s) for s in df["split"].unique().to_list()} if has_split else {"train": df}

    def flat(frame: pl.DataFrame) -> pl.DataFrame:
        return frame.with_columns([pl.col(c).cast(pl.String) for c, t in frame.schema.items() if t.is_nested() or t == pl.Object])

    if fmt == "parquet":
        for s, p in parts.items():
            p.write_parquet(out / f"{s}.parquet")
    elif fmt == "csv":
        for s, p in parts.items():
            flat(p).write_csv(out / f"{s}.csv")
    elif fmt == "jsonl":
        for s, p in parts.items():
            p.write_ndjson(out / f"{s}.jsonl")
    elif fmt == "hf":
        (out / "data").mkdir()
        for s, p in parts.items():
            p.write_parquet(out / "data" / f"{s}-00000-of-00001.parquet")
        (out / "README.md").write_text(version_row["card_md"], encoding="utf-8")
        if push_repo:
            if not HF_TOKEN:
                raise RuntimeError("Set a free HF_TOKEN (huggingface.co/settings/tokens) to push to the Hub")
            from huggingface_hub import HfApi
            api = HfApi(token=HF_TOKEN)
            api.create_repo(push_repo, repo_type="dataset", exist_ok=True, private=True)
            api.upload_folder(folder_path=str(out), repo_id=push_repo, repo_type="dataset")
            problems.append(f"pushed to https://huggingface.co/datasets/{push_repo} (private)")
    elif fmt in ("coco", "yolo"):
        if "image_path" not in df.columns or "label" not in df.columns:
            raise RuntimeError(f"{fmt.upper()} export needs `image_path` and `label` columns (use the Images → Labeled template)")
        _export_vision(parts, out, fmt)
    elif fmt == "tfrecord":
        from . import tfrecord
        for s, p in parts.items():
            tfrecord.write(out / f"{s}.tfrecord", flat(p).iter_rows(named=True))
    else:
        raise ValueError(f"unknown format {fmt}")
    if fmt != "hf":
        (out / "README.md").write_text(version_row["card_md"], encoding="utf-8")
    zip_base = d / "exports" / f"{fmt}"
    archive = _zip_dir(out, Path(f"{zip_base}.zip"))
    paths = dict(version_row["export_paths"])
    paths.setdefault("data", str(data_path(version_row)))
    paths[fmt] = archive
    db.update("dataset_versions", version_row["id"], export_paths_json=paths)
    return {"format": fmt, "path": archive, "dir": str(out), "warnings": problems}


_STORED = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".zip", ".gz", ".parquet"}


def _place(src: Path, dst: Path) -> None:
    """Hard-link instead of copying when possible — exports of large image sets become near-instant."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _zip_dir(src: Path, dest: Path) -> str:
    """Zip a folder; already-compressed media is stored as-is instead of being deflated a second time."""
    tmp = dest.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w") as z:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(src).as_posix(),
                        compress_type=zipfile.ZIP_STORED if f.suffix.lower() in _STORED else zipfile.ZIP_DEFLATED)
    tmp.replace(dest)
    return str(dest)


def _export_vision(parts: dict, out: Path, fmt: str) -> None:
    import cv2
    labels = sorted({l for p in parts.values() for l in p["label"].drop_nulls().to_list()})
    lid = {l: i for i, l in enumerate(labels)}
    has_boxes = any("bbox" in p.columns for p in parts.values())
    if fmt == "yolo" and not has_boxes:
        # Classification set → Ultralytics "classify" layout: <split>/<class>/<image>
        for split, p in parts.items():
            sdir = out / {"validation": "val"}.get(split, split)
            for i, r in enumerate(p.iter_rows(named=True)):
                src = Path(r["image_path"])
                if src.exists() and r.get("label") is not None:
                    cdir = sdir / re.sub(r"[^\w.-]+", "_", r["label"])
                    cdir.mkdir(parents=True, exist_ok=True)
                    _place(src, cdir / f"{i:07d}{src.suffix.lower()}")
        (out / "classes.txt").write_text("\n".join(labels) + "\n", encoding="utf-8")
        return
    for split, p in parts.items():
        imgdir = out / ("images" if fmt == "coco" else split) / (split if fmt == "coco" else "images")
        imgdir.mkdir(parents=True, exist_ok=True)
        images, anns = [], []
        for i, r in enumerate(p.iter_rows(named=True)):
            src = Path(r["image_path"])
            if not src.exists() or r.get("label") is None:
                continue
            dst = imgdir / f"{i:07d}{src.suffix.lower()}"
            _place(src, dst)
            h, w = r.get("height"), r.get("width")
            if not h or not w:
                im = cv2.imread(str(src))
                h, w = (im.shape[:2] if im is not None else (0, 0))
            bbox = r.get("bbox")  # optional "x,y,w,h" pixels; default whole image (classification)
            bx = [float(x) for x in str(bbox).split(",")] if bbox else [0, 0, w, h]
            if fmt == "coco":
                images.append({"id": i, "file_name": dst.name, "width": w, "height": h})
                anns.append({"id": i, "image_id": i, "category_id": lid[r["label"]], "bbox": bx, "area": bx[2] * bx[3], "iscrowd": 0})
            else:
                lbl = imgdir.parent / "labels"
                lbl.mkdir(exist_ok=True)
                cx, cy = (bx[0] + bx[2] / 2) / max(1, w), (bx[1] + bx[3] / 2) / max(1, h)
                (lbl / f"{dst.stem}.txt").write_text(f"{lid[r['label']]} {cx:.6f} {cy:.6f} {bx[2] / max(1, w):.6f} {bx[3] / max(1, h):.6f}\n")
        if fmt == "coco":
            (out / "annotations").mkdir(exist_ok=True)
            (out / "annotations" / f"instances_{split}.json").write_text(json.dumps(
                {"images": images, "annotations": anns, "categories": [{"id": i, "name": l} for l, i in lid.items()]}))
    if fmt == "yolo":
        (out / "data.yaml").write_text("path: .\n" + "".join(f"{s}: {s}/images\n" for s in parts) +
                                       f"nc: {len(labels)}\nnames: {json.dumps(labels)}\n")


def diff(a: dict, b: dict) -> dict:
    """Diff two versions: schema, row counts, and row-level adds/removes (by row hash on shared columns)."""
    sa = {c["name"]: c["dtype"] for c in a["schema"]}
    sb = {c["name"]: c["dtype"] for c in b["schema"]}
    out = {"from": a["version"], "to": b["version"], "rows": {"from": a["row_count"], "to": b["row_count"], "delta": b["row_count"] - a["row_count"]},
           "columns_added": sorted(set(sb) - set(sa)), "columns_removed": sorted(set(sa) - set(sb)),
           "type_changes": [{"column": c, "from": sa[c], "to": sb[c]} for c in sorted(set(sa) & set(sb)) if sa[c] != sb[c]]}
    shared = sorted(c for c in set(sa) & set(sb) if sa[c] == sb[c] and c != "split")
    key = next((k for k in ("image_hash", "id", "image_path", "url") if k in shared), None)
    if key:
        # Identity-keyed diff: match rows by key, then count what changed per column (e.g. labels renamed).
        try:
            da, dbf = load(a).select(shared).unique(key), load(b).select(shared).unique(key)
            ka, kb = set(da[key].to_list()), set(dbf[key].to_list())
            out["key"] = key
            out["rows_added"], out["rows_removed"] = len(kb - ka), len(ka - kb)
            j = da.join(dbf, on=key, suffix="__b")
            changed = {}
            for c in shared:
                if c == key:
                    continue
                n = int(j.select((pl.col(c).cast(pl.String).fill_null("∅") != pl.col(f"{c}__b").cast(pl.String).fill_null("∅")).sum()).item())
                if n:
                    changed[c] = n
            any_change = pl.any_horizontal([pl.col(c).cast(pl.String).fill_null("∅") != pl.col(f"{c}__b").cast(pl.String).fill_null("∅")
                                            for c in changed]) if changed else pl.lit(False)
            n_changed = int(j.select(any_change.sum()).item()) if changed else 0
            out["rows_changed"], out["rows_unchanged"] = n_changed, j.height - n_changed
            out["values_changed"] = changed
            if "label" in changed:
                pairs = (j.filter(pl.col("label") != pl.col("label__b")).group_by(["label", "label__b"]).len()
                         .sort("len", descending=True).head(8))
                out["label_changes"] = [{"from": r["label"], "to": r["label__b"], "n": r["len"]} for r in pairs.iter_rows(named=True)]
        except Exception as e:  # noqa: BLE001
            out["row_diff_error"] = str(e)[:200]
    elif shared:
        try:
            da, dbf = load(a).select(shared), load(b).select(shared)
            ha, hb = set(da.hash_rows().to_list()), set(dbf.hash_rows().to_list())
            out["rows_added"], out["rows_removed"], out["rows_unchanged"] = len(hb - ha), len(ha - hb), len(ha & hb)
        except Exception as e:  # noqa: BLE001
            out["row_diff_error"] = str(e)[:200]
    return out
