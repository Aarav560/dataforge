"""Transform nodes (plan §3.5). Each node: fn(ctx, params, job) → ctx.

ctx = {"files": [Path], "table": pl.DataFrame | None, "notes": [str], "review": [dict]}
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import numpy as np
import polars as pl

from .. import db, llm
from ..config import RAW_DIR

TABLE_EXT = {".csv", ".tsv", ".parquet", ".jsonl", ".json", ".arff", ".txt", ".data", ".xlsx"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4v", ".ogv"}

NODE_TYPES: dict[str, dict] = {}


def node(type_: str, label: str, category: str, params: dict | None = None, help: str = ""):
    def deco(fn):
        NODE_TYPES[type_] = {"type": type_, "label": label, "category": category, "params": params or {}, "help": help, "fn": fn}
        return fn
    return deco


def _all_files(paths: list[Path]) -> list[Path]:
    out = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out += [f for f in p.rglob("*") if f.is_file() and not f.name.endswith(".part")]
        elif p.is_file():
            out.append(p)
    return out


# ───────────────────────── sources ─────────────────────────
@node("source", "Assets", "source", {"asset_ids": []}, "Downloaded assets feeding this pipeline (empty = all project assets).")
def n_source(ctx, params, job):
    ids = params.get("asset_ids") or []
    q = "SELECT * FROM assets WHERE project_id = ? AND status = 'done'"
    rows = db.query(q, (ctx["project_id"],))
    if ids:
        rows = [r for r in rows if r["id"] in ids]
    ctx["files"] = _all_files([Path(r["storage_path"]) for r in rows])
    ctx["asset_rows"] = rows
    ctx["notes"].append(f"{len(rows)} assets · {len(ctx['files'])} files")
    return ctx


@node("unzip", "Extract archives", "prep", {}, "Unpack .zip archives in place (zip-slip safe).")
def n_unzip(ctx, params, job):
    new, reused = [], 0
    for f in list(ctx["files"]):
        if f.suffix.lower() != ".zip":
            continue
        dest = f.with_suffix("")
        marker = dest / ".dataforge_extracted"
        if marker.exists():  # already unpacked by an earlier run; its files are already in ctx["files"]
            reused += 1
            continue
        dest.mkdir(exist_ok=True)
        with zipfile.ZipFile(f) as z:
            for m in z.infolist():
                name = m.filename
                if m.is_dir() or name.startswith("__MACOSX/") or Path(name).name.startswith("._"):
                    continue
                target = (dest / name).resolve()
                if not str(target).startswith(str(dest.resolve())):  # zip-slip guard
                    continue
                if len(str(target)) > 240:  # Windows MAX_PATH: keep the class folders, shorten the file name
                    import hashlib
                    short = hashlib.sha1(target.name.encode()).hexdigest()[:16] + target.suffix.lower()
                    target = target.parent / short
                    if len(str(target)) > 240:
                        target = dest.resolve() / "_long" / short
                    if not str(target).startswith(str(dest.resolve())):
                        continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(m) as src, open(target, "wb") as out:
                    while chunk := src.read(1 << 20):
                        out.write(chunk)
                new.append(target)
        marker.write_text("ok")
        if job:
            job.log(f"{f.name}: extracted {len(new):,} files")
    seen: set[Path] = set()
    files = []
    for f in [f for f in ctx["files"] if f.suffix.lower() != ".zip"] + new:
        rp = Path(f).resolve()
        if rp not in seen and not rp.name.startswith(".dataforge") and "__MACOSX" not in rp.parts:
            seen.add(rp)
            files.append(rp)
    ctx["files"] = files
    ctx["notes"].append(f"extracted {len(new):,} files" + (f" · reused {reused} already-extracted archive(s)" if reused else ""))
    return ctx


# ───────────────────────── tables ─────────────────────────
def _read_arff(path: Path) -> pl.DataFrame:
    cols, data, in_data = [], [], False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("%"):
            continue
        if not in_data:
            if s.lower().startswith("@attribute"):
                m = re.match(r"@attribute\s+('[^']+'|\"[^\"]+\"|\S+)", s, re.I)
                cols.append(m.group(1).strip("'\"") if m else f"c{len(cols)}")
            elif s.lower().startswith("@data"):
                in_data = True
        else:
            data.append([v.strip().strip("'\"") for v in s.split(",")])
    df = pl.DataFrame(data, schema=cols, orient="row") if data else pl.DataFrame({c: [] for c in cols})
    return df.with_columns([pl.col(c).replace("?", None) for c in cols])


def read_table(path: Path) -> pl.DataFrame | None:
    ext = path.suffix.lower()
    try:
        if ext == ".parquet":
            return pl.read_parquet(path)
        if ext in (".csv", ".data"):
            return pl.read_csv(path, infer_schema_length=5000, ignore_errors=True, truncate_ragged_lines=True)
        if ext == ".tsv":
            return pl.read_csv(path, separator="\t", infer_schema_length=5000, ignore_errors=True, truncate_ragged_lines=True)
        if ext == ".jsonl":
            return pl.read_ndjson(path, ignore_errors=True)
        if ext == ".json":
            obj = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                obj = next((v for v in obj.values() if isinstance(v, list)), [obj])
            return pl.DataFrame(obj) if isinstance(obj, list) and obj and isinstance(obj[0], dict) else None
        if ext == ".arff":
            return _read_arff(path)
    except Exception:  # noqa: BLE001 — skip unreadable files, report count
        return None
    return None


def _norm_col(c: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", c.strip().lower())).strip("_") or "col"


@node("load_tables", "Load tables", "tabular", {"max_rows_per_file": 0}, "Read CSV/TSV/Parquet/JSON(L)/ARFF files into tables.")
def n_load_tables(ctx, params, job):
    frames, bad = [], 0
    for f in ctx["files"]:
        if f.suffix.lower() not in TABLE_EXT - {".txt"}:
            continue
        df = read_table(f)
        if df is None or df.width == 0:
            bad += 1
            continue
        if params.get("max_rows_per_file"):
            df = df.head(int(params["max_rows_per_file"]))
        df = df.rename({c: _norm_col(c) for c in df.columns}, strict=False) if len(set(map(_norm_col, df.columns))) == df.width else df
        # Nested JSON (structs/lists) → JSON strings: keeps merges cheap and schemas flat.
        nested = [c for c, t in df.schema.items() if t.is_nested()]
        if nested:
            df = df.with_columns([pl.col(c).map_elements(lambda v: json.dumps(v, default=str) if v is not None else None,
                                                         return_dtype=pl.String) for c in nested])
        frames.append(df.with_columns(pl.lit(str(f.relative_to(RAW_DIR)) if RAW_DIR in f.parents else f.name).alias("_source_file")))
    ctx["frames"] = frames
    ctx["notes"].append(f"loaded {len(frames)} tables" + (f", skipped {bad} unreadable" if bad else ""))
    return ctx


def _concat(frames: list[pl.DataFrame]) -> pl.DataFrame:
    # Cast mismatched dtypes to string so diagonal concat never fails.
    dtypes: dict[str, set] = {}
    for f in frames:
        for c, t in f.schema.items():
            dtypes.setdefault(c, set()).add(t)
    conflict = {c for c, ts in dtypes.items() if len(ts) > 1}
    frames = [f.with_columns([pl.col(c).cast(pl.String) for c in conflict if c in f.columns]) for f in frames]
    return pl.concat(frames, how="diagonal_relaxed")


@node("unify", "Unify schema", "tabular", {"mode": "largest_group", "min_overlap": 0.5, "max_columns": 300},
      "Group tables by schema (column overlap), merge each group, keep the largest group "
      "(mode=all merges every group — can be very wide).")
def n_unify(ctx, params, job):
    frames = ctx.get("frames") or ([ctx["table"]] if ctx.get("table") is not None else [])
    if not frames:
        raise RuntimeError("no tables to unify")
    overlap = float(params.get("min_overlap", 0.5))
    groups: list[tuple[set, list[pl.DataFrame]]] = []
    for f in sorted(frames, key=lambda f: -f.height):
        cols = set(f.columns) - {"_source_file"}
        for gcols, members in groups:
            if len(cols & gcols) / max(1, len(cols | gcols)) >= overlap:
                members.append(f)
                gcols |= cols
                break
        else:
            groups.append((cols, [f]))
    merged = [_concat(m) for _, m in groups]
    merged.sort(key=lambda d: -d.height)
    if params.get("mode") == "all" and len(merged) > 1:
        total_cols = len({c for d in merged for c in d.columns})
        if total_cols > int(params.get("max_columns", 300)):
            raise RuntimeError(f"merging all {len(merged)} schema groups would create {total_cols} columns — use mode=largest_group")
        table = _concat(merged)
    else:
        table = merged[0]
    ctx["table"] = table
    ctx["schema_groups"] = [{"rows": d.height, "columns": d.columns[:20]} for d in merged[:10]]
    dropped = f" · {len(merged) - 1} other schema group(s) set aside" if len(merged) > 1 and params.get("mode") != "all" else ""
    ctx["notes"].append(f"unified {len(frames)} tables into {len(merged)} schema group(s) → {table.height:,} rows × {table.width} cols{dropped}")
    return ctx


@node("clean", "Clean", "tabular", {"drop_null_rows": False, "drop_constant_cols": True, "strip_strings": True, "max_null_frac": 0.95},
      "Strip whitespace, drop near-empty and constant columns.")
def n_clean(ctx, params, job):
    df = _need_table(ctx)
    before = df.shape
    if params.get("strip_strings", True):
        df = df.with_columns([pl.col(c).str.strip_chars() for c, t in df.schema.items() if t == pl.String])
    mx = float(params.get("max_null_frac", 0.95))
    if df.height:
        df = df.select([c for c in df.columns if df[c].null_count() / df.height <= mx or c.startswith("_")])
    if params.get("drop_constant_cols", True) and df.height > 1:
        df = df.select([c for c in df.columns if c.startswith("_") or df[c].n_unique() > 1])
    if params.get("drop_null_rows"):
        df = df.drop_nulls()
    ctx["table"] = df
    ctx["notes"].append(f"clean: {before} → {df.shape}")
    return ctx


@node("dedupe", "Dedupe", "tabular", {"subset": []}, "Remove exact duplicate rows (optionally on a subset of columns).")
def n_dedupe(ctx, params, job):
    df = _need_table(ctx)
    subset = [c for c in (params.get("subset") or []) if c in df.columns] or [c for c in df.columns if not c.startswith("_")]
    n = df.height
    df = df.unique(subset=subset, keep="first", maintain_order=True)
    ctx["table"] = df
    ctx["notes"].append(f"dedupe: removed {n - df.height:,} duplicate rows")
    return ctx


@node("pii_scan", "PII scan", "safety", {"action": "redact"}, "Detect emails, phone numbers, IPs, card & SSN-like numbers in text columns (plan §9).")
def n_pii(ctx, params, job):
    df = _need_table(ctx)
    # Patterns require separators/word boundaries so hashes, IDs and plain numbers aren't flagged.
    pats = {"email": r"\b[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}\b",
            "phone": r"(?:^|[^\w])(?:\+\d{1,3}[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b",
            "ipv4": r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
            "ssn": r"\b\d{3}-\d{2}-\d{4}\b", "card": r"\b(?:\d{4}[ -]){3}\d{4}\b"}
    found = {}
    exprs = []
    for c, t in df.schema.items():
        if t != pl.String or c.startswith("_"):
            continue
        for name, p in pats.items():
            k = int(df[c].str.contains(p).sum() or 0)
            if k:
                found[f"{c}:{name}"] = k
        if params.get("action", "redact") == "redact":
            e = pl.col(c)
            for name, p in pats.items():
                e = e.str.replace_all(p, f"[{name.upper()}]")
            exprs.append(e.alias(c))
    if exprs:
        df = df.with_columns(exprs)
    ctx["table"] = df
    ctx["pii"] = found
    ctx["notes"].append(f"PII: {sum(found.values())} matches in {len(found)} column/type pairs" + (" (redacted)" if found and params.get('action', 'redact') == 'redact' else ""))
    return ctx


@node("split", "Train/val/test split", "output",
      {"train": 0.8, "val": 0.1, "test": 0.1, "seed": 42, "stratify": "", "keep_existing": True},
      "Adds a `split` column, stratified if a label column is given. With keep_existing, validation/test rows the "
      "source authors already held out stay held out (avoids leakage); only missing splits are carved from the rest.")
def n_split(ctx, params, job):
    df = _need_table(ctx)
    rng = np.random.default_rng(int(params.get("seed", 42)))
    ratios = {"train": float(params.get("train", 0.8)), "validation": float(params.get("val", 0.1)), "test": float(params.get("test", 0.1))}
    strat = params.get("stratify")
    splits = np.full(df.height, None, dtype=object)
    free = np.ones(df.height, dtype=bool)
    if params.get("keep_existing", True) and "orig_split" in df.columns:
        orig = np.array(df["orig_split"].to_list(), dtype=object)
        held = np.isin(orig, ["validation", "test"])
        splits[held] = orig[held]
        free = ~held
    # Carve only the splits the source didn't provide, in proportion, from the free rows.
    need = {k: v for k, v in ratios.items() if k == "train" or not (splits == k).any()}
    total = sum(need.values()) or 1.0
    order = list(need)
    idx_all = np.arange(df.height)
    if not strat or strat not in df.columns:
        groups = [idx_all[free]]
    else:
        by_label = df.with_row_index("_i").group_by(strat).agg(pl.col("_i"))["_i"].to_list()
        groups = [g[free[g]] for g in (np.array(ix) for ix in by_label)]
    for g in groups:
        g = rng.permutation(g)
        cuts = np.cumsum([int(round(len(g) * need[k] / total)) for k in order[:-1]])
        for k, part in zip(order, np.split(g, cuts)):
            splits[part] = k
    splits = splits.astype(str)
    ctx["table"] = df.with_columns(pl.Series("split", splits))
    kept = int((~free).sum())
    ctx["notes"].append("split: " + ", ".join(f"{k}={v:,}" for k, v in zip(*np.unique(splits, return_counts=True)))
                        + (f" · kept {kept:,} source-held-out rows" if kept else ""))
    return ctx


@node("relabel", "Rename labels", "label", {"column": "label", "mapping": "", "tidy": True, "drop": ""},
      "Map labels to consistent names (mapping: 'old=new; old2=new2'), tidy names (Tomato___Late_blight → Tomato · late blight), "
      "and drop unwanted classes (comma-separated).")
def n_relabel(ctx, params, job):
    df = _need_table(ctx)
    col = params.get("column") or "label"
    if col not in df.columns:
        raise RuntimeError(f"no '{col}' column to relabel")
    mapping = {}
    for pair in re.split(r"[;\n]", str(params.get("mapping") or "")):
        if "=" in pair:
            k, v = pair.split("=", 1)
            mapping[k.strip()] = v.strip()

    def tidy(x: str) -> str:
        parts = [p for p in re.split(r"_{2,}", x) if p]
        parts = [re.sub(r"[_]+", " ", p).strip() for p in parts]
        if len(parts) > 1:
            crop, rest = parts[0], " ".join(parts[1:]).lower()
            rest = re.sub(rf"^{re.escape(crop.lower())}\s+", "", rest)  # "Tomato · tomato mosaic virus" → "mosaic virus"
            return f"{crop.capitalize()} · {rest}"
        return parts[0] if parts else x

    before = df[col].n_unique()
    vals = [mapping.get(v, v) if v is not None else None for v in df[col].to_list()]
    if params.get("tidy", True):
        vals = [tidy(v) if v is not None and v not in mapping.values() else v for v in vals]
    df = df.with_columns(pl.Series(col, vals, dtype=pl.String))
    drop = {d.strip() for d in str(params.get("drop") or "").split(",") if d.strip()}
    n = df.height
    if drop:
        df = df.filter(~pl.col(col).is_in(list(drop)))
    ctx["table"] = df
    ctx["notes"].append(f"relabel: {before} → {df[col].n_unique()} labels" + (f" · dropped {n - df.height:,} rows" if drop else ""))
    return ctx


def _need_table(ctx) -> pl.DataFrame:
    if ctx.get("table") is None:
        if ctx.get("frames"):
            return n_unify(ctx, {}, None)["table"]
        raise RuntimeError("this step needs a table — add 'Load tables' (or a media → table step) before it")
    return ctx["table"]


# ───────────────────────── text ─────────────────────────
@node("html_to_text", "Web pages → text", "text", {"min_chars": 200}, "Turn fetched pages / text files into a text corpus.")
def n_html_to_text(ctx, params, job):
    from bs4 import BeautifulSoup
    rows = []
    for f in ctx["files"]:
        ext = f.suffix.lower()
        if ext == ".txt":
            raw = f.read_text(encoding="utf-8", errors="replace")
            url = raw.split("\n", 1)[0].removeprefix("URL: ") if raw.startswith("URL: ") else ""
            text = raw.split("\n\n", 1)[-1] if raw.startswith("URL: ") else raw
            title = ""
        elif ext in (".html", ".htm"):
            if f.with_suffix(".txt").exists():
                continue
            soup = BeautifulSoup(f.read_text(encoding="utf-8", errors="replace"), "html.parser")
            for t in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                t.decompose()
            text, url, title = re.sub(r"\s+", " ", soup.get_text(" ")).strip(), "", soup.title.string if soup.title else ""
        else:
            continue
        if len(text) >= int(params.get("min_chars", 200)):
            rows.append({"text": text, "url": url, "title": title or f.stem, "_source_file": f.name, "n_chars": len(text)})
    ctx["table"] = pl.DataFrame(rows) if rows else pl.DataFrame({"text": [], "url": [], "title": [], "_source_file": [], "n_chars": []})
    ctx["notes"].append(f"text corpus: {len(rows)} documents")
    return ctx


@node("chunk_text", "Chunk text", "text", {"chunk_chars": 2000, "overlap": 200}, "Split long documents into overlapping chunks.")
def n_chunk(ctx, params, job):
    df = _need_table(ctx)
    size, ov = int(params.get("chunk_chars", 2000)), int(params.get("overlap", 200))
    rows = []
    for r in df.iter_rows(named=True):
        t = r.get("text") or ""
        for i, start in enumerate(range(0, max(1, len(t)), max(1, size - ov))):
            rows.append({**r, "text": t[start:start + size], "chunk": i})
    ctx["table"] = pl.DataFrame(rows) if rows else df
    ctx["notes"].append(f"chunked → {len(rows):,} chunks")
    return ctx


@node("llm_label", "Auto-label (local LLM)", "label", {"column": "text", "labels": "", "max_rows": 200},
      "Zero-shot label rows with the local Ollama model; low-confidence rows go to the review queue.")
async def n_llm_label(ctx, params, job):
    df = _need_table(ctx)
    col = params.get("column") or "text"
    labels = [s.strip() for s in str(params.get("labels", "")).split(",") if s.strip()]
    if col not in df.columns or not labels:
        raise RuntimeError("auto-label needs an existing text column and a comma-separated label list")
    n = min(df.height, int(params.get("max_rows", 200)))
    out_l, out_c = [None] * df.height, [None] * df.height
    sysmsg = f'Classify the text into exactly one of: {labels}. Respond JSON {{"label": "...", "confidence": 0-1}}.'
    for i in range(n):
        res = await llm.complete_json(sysmsg, str(df[col][i])[:1500], temperature=0)
        if isinstance(res, dict) and res.get("label") in labels:
            out_l[i], out_c[i] = res["label"], float(res.get("confidence") or 0.5)
            if out_c[i] < 0.7:
                ctx["review"].append({"row": i, "text": str(df[col][i])[:300], "suggested": out_l[i], "confidence": out_c[i], "labels": labels})
        if job and i % 10 == 0:
            job.log(f"auto-label {i}/{n}")
    ctx["table"] = df.with_columns(pl.Series("label", out_l, dtype=pl.String), pl.Series("label_confidence", out_c, dtype=pl.Float64))
    ctx["notes"].append(f"auto-labeled {sum(1 for x in out_l if x)}/{n} rows · {len(ctx['review'])} queued for review")
    return ctx


# ───────────────────────── images ─────────────────────────
_GENERIC_DIRS = {"train", "test", "val", "valid", "validation", "images", "image", "imgs", "img", "data", "dataset", "datasets",
                 "raw", "jpg", "jpeg", "png", "photos", "pictures", "all", "split", "samples", "color", "colour", "segmented", "grayscale"}
_SPLIT_DIRS = {"train": "train", "training": "train", "test": "test", "testing": "test", "val": "validation", "valid": "validation",
               "validation": "validation"}


def _folder_label(f: Path, roots: list[Path]) -> tuple[str, str | None]:
    """Nearest non-generic parent folder is the class; a train/test/val folder anywhere above it (up to the asset
    root) records the original split — split folders usually sit *above* the class folders."""
    label, split = None, None
    for parent in f.parents:
        if any(parent == r for r in roots):
            break
        name = parent.name
        if name.lower() in _SPLIT_DIRS and split is None:
            split = _SPLIT_DIRS[name.lower()]
        if label is None and name.lower() not in _GENERIC_DIRS and not name.endswith("_frames"):
            label = name
    return label or "unknown", split


def _parquet_images(path: Path, out_dir: Path) -> list[dict]:
    """Hugging Face Parquet stores images as {bytes, path} structs and class names in the schema metadata."""
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(path)
    schema = pf.schema_arrow
    img_cols = [f.name for f in schema if str(f.type).startswith("struct<bytes: binary")]
    if not img_cols:
        return []
    names: dict[str, list[str]] = {}
    meta = (schema.metadata or {}).get(b"huggingface")
    if meta:
        try:
            feats = json.loads(meta)["info"]["features"]
            names = {k: v["names"] for k, v in feats.items() if isinstance(v, dict) and v.get("_type") == "ClassLabel"}
        except (KeyError, ValueError, TypeError):
            pass
    label_col = next(iter(names), None) or next((c for c in ("label", "labels", "class", "category", "disease") if c in schema.names), None)
    split = next((v for k, v in _SPLIT_DIRS.items() if k in path.stem.lower() or k in path.parent.name.lower()), None)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for bi in range(pf.num_row_groups):
        t = pf.read_row_group(bi, columns=img_cols[:1] + ([label_col] if label_col else [])).to_pylist()
        for i, r in enumerate(t):
            img = r[img_cols[0]] or {}
            data = img.get("bytes")
            if not data:
                continue
            ext = Path(img.get("path") or "x.jpg").suffix.lower() or ".jpg"
            ext = ext if ext in IMAGE_EXT else ".jpg"
            target = out_dir / f"{path.stem}_{bi}_{i}{ext}"
            target.write_bytes(data)
            lab = r.get(label_col) if label_col else None
            if label_col in names and isinstance(lab, int) and 0 <= lab < len(names[label_col]):
                lab = names[label_col][lab]
            rows.append({"image_path": str(target), "label": str(lab) if lab is not None else "unknown", "orig_split": split,
                         "_source_file": path.name})
    return rows


@node("images_to_labeled", "Images → labeled set", "vision", {"label_from": "folder", "min_side": 16},
      "Build (image_path, label) rows from image files and from Hugging Face Parquet with embedded images. "
      "Labels come from the nearest meaningful folder (train/test/images are skipped), the filename prefix, "
      "the dataset's class names, or the source title. Unreadable images are dropped; a content hash enables cross-dataset dedupe.")
def n_images(ctx, params, job):
    import hashlib

    import cv2
    mode = params.get("label_from", "folder")
    roots = [Path(a["storage_path"]) for a in ctx.get("asset_rows", [])]
    title_by_dir = {Path(a["storage_path"]): (db.get("search_results", a["result_id"]) or {}).get("title", "") for a in ctx.get("asset_rows", [])}
    rows: list[dict] = []
    # 1) Parquet with embedded images (Hugging Face)
    for f in [f for f in ctx["files"] if f.suffix.lower() == ".parquet"]:
        try:
            got = _parquet_images(f, f.parent / f"{f.stem}_images")
        except Exception as e:  # noqa: BLE001 — not an image parquet
            got = []
            if job:
                job.log(f"{f.name}: no embedded images ({type(e).__name__})")
        rows += got
    extracted = {r["image_path"] for r in rows}
    # 2) Plain image files
    for f in ctx["files"]:
        if f.suffix.lower() not in IMAGE_EXT or str(f) in extracted:
            continue
        split = None
        if mode == "filename":
            label = re.split(r"[_\-\s\d]", f.stem)[0].lower() or "unknown"
        elif mode == "source":
            label = next((t for d, t in title_by_dir.items() if d in f.parents or d == f), "unknown")
        else:
            label, split = _folder_label(f, roots)
        rows.append({"image_path": str(f), "label": label, "orig_split": split, "_source_file": f.name})
    # 3) Verify, measure, hash
    min_side = int(params.get("min_side", 16))
    keep, bad = [], 0
    for i, r in enumerate(rows):
        data = Path(r["image_path"]).read_bytes()
        im = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_REDUCED_GRAYSCALE_2)
        if im is None or min(im.shape[:2]) * 2 < min_side:
            bad += 1
            continue
        h, w = im.shape[:2]
        keep.append({**r, "height": h * 2, "width": w * 2, "image_hash": hashlib.md5(data).hexdigest()})
        if job and i and i % 1000 == 0:
            job.log(f"checked {i:,}/{len(rows):,} images")
    schema = {"image_path": pl.String, "label": pl.String, "orig_split": pl.String, "_source_file": pl.String,
              "height": pl.Int64, "width": pl.Int64, "image_hash": pl.String}
    df = pl.DataFrame(keep, schema=schema)
    ctx["table"] = df
    ctx["notes"].append(f"{df.height:,} images · {df['label'].n_unique() if df.height else 0} labels"
                        + (f" · dropped {bad} unreadable/tiny" if bad else ""))
    return ctx


# ───────────────────────── video → state/action ─────────────────────────
@node("extract_frames", "Extract frames", "video", {"every_sec": 0.2, "max_frames": 6000, "scene_threshold": 0.45},
      "Sample frames with OpenCV; drop menus/transitions via histogram scene-change detection (PySceneDetect-style).")
def n_frames(ctx, params, job):
    import cv2
    vids = [f for f in ctx["files"] if f.suffix.lower() in VIDEO_EXT]
    if not vids:
        raise RuntimeError("no video files in the selected assets")
    every, cap_n, thr = float(params.get("every_sec", 0.5)), int(params.get("max_frames", 4000)), float(params.get("scene_threshold", 0.45))
    frames_meta = []
    for v in vids:
        out = v.parent / f"{v.stem}_frames"
        out.mkdir(exist_ok=True)
        cap = cv2.VideoCapture(str(v))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        step = max(1, int(fps * every))
        idx, prev_hist, scene = 0, None, 0
        while len(frames_meta) < cap_n:
            ok = cap.grab()
            if not ok:
                break
            if idx % step == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                hist = cv2.normalize(cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256]), None).flatten()
                cut = prev_hist is not None and cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA) > thr
                scene += int(cut)
                prev_hist = hist
                p = out / f"{idx:07d}.jpg"
                cv2.imwrite(str(p), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                frames_meta.append({"video": v.name, "frame_idx": idx, "t_sec": round(idx / fps, 3), "scene": scene,
                                    "frame_path": str(p), "scene_cut": cut})
            idx += 1
        cap.release()
        if job:
            job.log(f"{v.name}: {len(frames_meta)} frames so far")
    df = pl.DataFrame(frames_meta)
    # Drop very short scenes (menus/transitions): keep scenes with ≥ 3 sampled frames.
    if df.height:
        sizes = df.group_by(["video", "scene"]).len()
        df = df.join(sizes, on=["video", "scene"]).filter(pl.col("len") >= 3).drop("len")
    ctx["table"] = df
    ctx["notes"].append(f"{df.height} frames kept from {len(vids)} videos")
    return ctx


def _find_board(gray: np.ndarray, aspect=(2.0, 0.5)) -> tuple[int, int, int, int] | None:
    import cv2
    edges = cv2.Canny(gray, 40, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    cnts, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    best, best_area = None, 0
    H, W = gray.shape
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w < W * 0.12 or h < H * 0.4:
            continue
        ratio = h / max(1, w)
        if abs(ratio - aspect[0]) <= aspect[1] and w * h > best_area:
            best, best_area = (x, y, w, h), w * h
    return _refine_bbox(gray, best) if best else None


def _refine_bbox(gray: np.ndarray, bb: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Contours land on the playfield's frame. Trim frame lines (rows/cols much brighter than the
    interior's typical level) inward so the 10×20 grid aligns with the actual cells."""
    x, y, w, h = bb
    roi = gray[y:y + h, x:x + w].astype(float)
    if roi.size == 0:
        return bb
    # A frame line is bright across (nearly) its whole length; a row of locked pieces is not.
    bright = roi > np.median(roi[h // 4: 3 * h // 4, w // 4: 3 * w // 4]) + 40
    col_m, row_m = bright.mean(axis=0), bright.mean(axis=1)
    lvl_c = lvl_r = 0.85
    l, r, t, b = 0, w - 1, 0, h - 1
    lim_w, lim_h = w // 8, h // 16
    while l < lim_w and col_m[l] <= lvl_c: l += 1          # outside margin
    while l < lim_w and col_m[l] > lvl_c: l += 1           # frame line
    while r > w - 1 - lim_w and col_m[r] <= lvl_c: r -= 1
    while r > w - 1 - lim_w and col_m[r] > lvl_c: r -= 1
    while t < lim_h and row_m[t] <= lvl_r: t += 1
    while t < lim_h and row_m[t] > lvl_r: t += 1
    while b > h - 1 - lim_h and row_m[b] <= lvl_r: b -= 1
    while b > h - 1 - lim_h and row_m[b] > lvl_r: b -= 1
    # If trimming ran to its limit, there was no clear frame line — keep the original box.
    if l >= lim_w or r <= w - 1 - lim_w or t >= lim_h or b <= h - 1 - lim_h:
        return bb
    return (x + l, y + t, r - l + 1, b - t + 1)


@node("grid_sample", "Board → grid state", "video", {"rows": 20, "cols": 10, "board_bbox": "", "fill_threshold": 45},
      "Detect the playfield (≈1:2 rectangle) and sample a rows×cols occupancy grid per frame. "
      "Override detection with board_bbox='x,y,w,h' in fractions of the frame. Cells are filled when brighter / more "
      "saturated than the board's own background; frames that look >85% full are discarded as detection failures.")
def n_grid(ctx, params, job):
    import cv2
    df = _need_table(ctx)
    R, C = int(params.get("rows", 20)), int(params.get("cols", 10))
    thr = float(params.get("fill_threshold", 60))
    fixed = [float(x) for x in str(params.get("board_bbox") or "").split(",") if x.strip()]
    states, bboxes = [], []
    bbox_cache: dict[str, tuple] = {}
    for r in df.iter_rows(named=True):
        img = cv2.imread(r["frame_path"])
        if img is None:
            states.append(None); bboxes.append(None); continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        Hh, Ww = gray.shape
        if len(fixed) == 4:
            bb = (int(fixed[0] * Ww), int(fixed[1] * Hh), int(fixed[2] * Ww), int(fixed[3] * Hh))
        else:
            bb = bbox_cache.get(r["video"]) or _find_board(gray, (R / C, 0.6))
            if bb:
                bbox_cache.setdefault(r["video"], bb)
        if not bb:
            states.append(None); bboxes.append(None); continue
        x, y, w, h = bb
        board = gray[y:y + h, x:x + w]
        hsv = cv2.cvtColor(img[y:y + h, x:x + w], cv2.COLOR_BGR2HSV)
        vals = np.zeros((R, C)); sats = np.zeros((R, C))
        for i in range(R):
            for j in range(C):
                y0, y1 = int(i * h / R), int((i + 1) * h / R)
                x0, x1 = int(j * w / C), int((j + 1) * w / C)
                # inner 50% of the cell avoids grid lines
                cy0, cy1 = y0 + (y1 - y0) // 4, y1 - (y1 - y0) // 4
                cx0, cx1 = x0 + (x1 - x0) // 4, x1 - (x1 - x0) // 4
                if cy1 > cy0 and cx1 > cx0:
                    vals[i, j] = np.median(board[cy0:cy1, cx0:cx1])
                    sats[i, j] = np.median(hsv[cy0:cy1, cx0:cx1, 1])
        # Adaptive: the emptiest cells (top rows are usually empty) define the background level.
        bg_v, bg_s = np.percentile(vals, 15), np.percentile(sats, 15)
        # Saturation of near-black pixels is noise (JPEG chroma bleed), so it only counts when the cell is also lit.
        filled = ((vals - bg_v) > thr) | (((sats - bg_s) > 70) & ((vals - bg_v) > 20))
        if filled.mean() > 0.85:  # board detection failed (menu / transition / wrong crop)
            states.append(None); bboxes.append(None); continue
        states.append("".join("1" if x else "0" for x in filled.flat)); bboxes.append(f"{x},{y},{w},{h}")
    df = df.with_columns(pl.Series("board", states, dtype=pl.String), pl.Series("board_bbox", bboxes, dtype=pl.String))
    df = df.filter(pl.col("board").is_not_null())
    ctx["table"] = df
    ctx["notes"].append(f"grid states for {df.height} frames ({R}×{C})")
    return ctx


@node("infer_actions", "Diff states → actions", "video", {"rows": 20, "cols": 10},
      "Diff consecutive board states: newly locked cells give (piece shape, column, rotation-signature, lines cleared).")
def n_actions(ctx, params, job):
    df = _need_table(ctx)
    R, C = int(params.get("rows", 20)), int(params.get("cols", 10))
    out = []
    prev = None
    for r in df.sort(["video", "frame_idx"]).iter_rows(named=True):
        b = np.frombuffer(r["board"].encode(), dtype=np.uint8).reshape(R, C) - 48
        if prev is not None and prev[0] == r["video"]:
            pb = prev[1]
            prev_cnt, cnt = int(pb.sum()), int(b.sum())
            lines = max(0, round((prev_cnt + 4 - cnt) / C)) if cnt < prev_cnt + 4 else 0
            added = (b == 1) & (pb == 0)
            n_add = int(added.sum())
            if n_add == 4 or lines:  # a piece locked
                ys, xs = np.where(added)
                shape = ""
                if n_add == 4:
                    sy, sx = ys - ys.min(), xs - xs.min()
                    shape = ";".join(f"{a},{b2}" for a, b2 in sorted(zip(sy.tolist(), sx.tolist())))
                out.append({**{k: r[k] for k in ("video", "frame_idx", "t_sec")}, "state": prev[2], "next_state": r["board"],
                            "placed_shape": shape, "column": int(xs.min()) if n_add else None, "lines_cleared": lines,
                            "action": f"place@{int(xs.min()) if n_add else -1}:{shape}"})
        prev = (r["video"], b, r["board"])
    ctx["table"] = pl.DataFrame(out) if out else pl.DataFrame({"video": [], "state": [], "action": []})
    ctx["notes"].append(f"inferred {len(out)} (state, action) transitions")
    return ctx


async def run_node(type_: str, ctx: dict, params: dict, job) -> dict:
    fn = NODE_TYPES[type_]["fn"]
    import asyncio
    import inspect
    if inspect.iscoroutinefunction(fn):
        return await fn(ctx, params, job)
    return await asyncio.to_thread(fn, ctx, params, job)


def public_node_types() -> list[dict]:
    return [{k: v for k, v in t.items() if k != "fn"} for t in NODE_TYPES.values()]

