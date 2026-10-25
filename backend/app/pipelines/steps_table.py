"""Tabular, image and text transform steps."""

import hashlib
import re
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from ..previews import IMAGE_EXTS, TABLE_EXTS
from .engine import Context, Env, Param, PipelineError, step


def _need_table(ctx: Context, label: str) -> pa.Table:
    if ctx.table is None:
        raise PipelineError(f"{label} needs a table input")
    return ctx.table


def _cols(text: str) -> list[str]:
    return [c.strip() for c in text.split(",") if c.strip()]


def _check_cols(t: pa.Table, cols: list[str], label: str) -> None:
    missing = [c for c in cols if c not in t.column_names]
    if missing:
        raise PipelineError(f"{label}: unknown column(s) {missing}; available: {t.column_names}")


def read_table_file(path: Path) -> pa.Table:
    import pyarrow.csv as pacsv
    import pyarrow.json as pajson
    import pyarrow.parquet as pq

    ext = path.suffix.lower()
    if ext == ".parquet":
        return pq.read_table(path)
    if ext in (".csv", ".tsv"):
        return pacsv.read_csv(path, parse_options=pacsv.ParseOptions(delimiter="\t" if ext == ".tsv" else ","))
    if ext in (".jsonl", ".ndjson"):
        return pajson.read_json(path)
    import json

    data = json.loads(path.read_text())
    records = data if isinstance(data, list) else data.get("data") if isinstance(data, dict) else None
    if not isinstance(records, list):
        raise PipelineError(f"{path.name}: JSON must be a list of records")
    return pa.Table.from_pylist([r for r in records if isinstance(r, dict)])


@step(
    "load_tables",
    "Load tables",
    "table",
    "Read CSV/TSV/JSONL/JSON/Parquet files from the input and stack them into one table.",
    {
        "pattern": Param("str", "", "Only files whose path contains this text"),
        "add_source": Param("bool", True, "Add a source_file column"),
        "max_files": Param("int", 200),
    },
)
def load_tables(ctx: Context, p: dict, env: Env) -> Context:
    files = [f for f in ctx.files if f.suffix.lower() in TABLE_EXTS and p["pattern"] in str(f)][: p["max_files"]]
    if not files:
        raise PipelineError("no table files (csv, tsv, jsonl, json, parquet) in the input")
    tables = []
    for f in files:
        try:
            t = read_table_file(f)
        except Exception as exc:
            env.log(f"skipped {f.name}: {exc}")
            continue
        if p["add_source"]:
            t = t.append_column("source_file", pa.array([f.name] * t.num_rows, pa.string()))
        tables.append(t)
    if not tables:
        raise PipelineError("no table file could be read")
    merged = pa.concat_tables(tables, promote_options="permissive")
    env.log(f"loaded {len(tables)} files, {merged.num_rows} rows, columns {merged.column_names}")
    return Context(table=merged, videos=ctx.videos, files=ctx.files)


@step(
    "select_columns",
    "Select / rename columns",
    "table",
    "Keep and optionally rename columns. Mapping like 'img:image, label' keeps img as image and label as is.",
    {"columns": Param("str", required=True, help="Comma-separated, old:new to rename")},
)
def select_columns(ctx: Context, p: dict, env: Env) -> Context:
    t = _need_table(ctx, "Select columns")
    pairs = [(c.split(":")[0].strip(), (c.split(":")[1] if ":" in c else c).strip()) for c in _cols(p["columns"])]
    _check_cols(t, [o for o, _ in pairs], "Select columns")
    out = pa.table({new: t.column(old) for old, new in pairs})
    return Context(table=out, videos=ctx.videos, files=ctx.files)


@step(
    "clean",
    "Clean",
    "table",
    "Trim strings, drop rows with nulls or empty strings in the given columns.",
    {
        "required": Param("str", "", "Columns that must be non-empty (default: columns that are mostly filled)"),
        "trim": Param("bool", True, "Strip whitespace from strings"),
        "min_length": Param("int", 0, "Minimum length for string columns in 'required'"),
    },
)
def clean(ctx: Context, p: dict, env: Env) -> Context:
    t = _need_table(ctx, "Clean")
    # By default require only columns present in most rows, so stacking files
    # with different columns doesn't drop everything.
    required = _cols(p["required"]) or [
        c for c in t.column_names if t.num_rows and t.column(c).null_count / t.num_rows < 0.5
    ]
    _check_cols(t, required, "Clean")
    if p["trim"]:
        for i, field in enumerate(t.schema):
            if pa.types.is_string(field.type) or pa.types.is_large_string(field.type):
                t = t.set_column(i, field.name, pc.utf8_trim_whitespace(t.column(i)))
    mask = None
    for c in required:
        col = t.column(c)
        keep = pc.is_valid(col)
        if pa.types.is_string(col.type) or pa.types.is_large_string(col.type):
            keep = pc.and_(keep, pc.greater_equal(pc.utf8_length(pc.fill_null(col, "")), max(1, p["min_length"])))
        mask = keep if mask is None else pc.and_(mask, keep)
    before = t.num_rows
    if mask is not None:
        t = t.filter(mask)
    env.log(f"clean: dropped {before - t.num_rows} of {before} rows")
    return Context(table=t, videos=ctx.videos, files=ctx.files)


def _norm(v) -> str:
    return re.sub(r"\s+", " ", str(v).lower()).strip()


@step(
    "dedupe",
    "Dedupe",
    "table",
    "Drop duplicate rows by the given columns (all by default). Text is compared case- and whitespace-insensitively.",
    {"columns": Param("str", "", "Key columns (default: all)"), "normalize": Param("bool", True)},
)
def dedupe(ctx: Context, p: dict, env: Env) -> Context:
    t = _need_table(ctx, "Dedupe")
    cols = _cols(p["columns"]) or t.column_names
    _check_cols(t, cols, "Dedupe")
    seen, keep = set(), []
    data = [t.column(c).to_pylist() for c in cols]
    for i, values in enumerate(zip(*data)):
        key = hashlib.sha1(
            "\x1f".join(_norm(v) if p["normalize"] else repr(v) for v in values).encode()
        ).digest()
        if key not in seen:
            seen.add(key)
            keep.append(i)
    env.log(f"dedupe: removed {t.num_rows - len(keep)} duplicates")
    return Context(table=t.take(pa.array(keep, pa.int64())), videos=ctx.videos, files=ctx.files)


_OPS = {"==": pc.equal, "!=": pc.not_equal, ">": pc.greater, ">=": pc.greater_equal, "<": pc.less, "<=": pc.less_equal}


@step(
    "filter_rows",
    "Filter rows",
    "table",
    "Keep rows matching 'column op value' (ops: == != > >= < <= contains).",
    {"expr": Param("str", required=True, help="e.g. 'lines > 0' or 'text contains tetris'")},
)
def filter_rows(ctx: Context, p: dict, env: Env) -> Context:
    t = _need_table(ctx, "Filter rows")
    m = re.fullmatch(r"\s*(\w+)\s*(==|!=|>=|<=|>|<|contains)\s*(.+?)\s*", p["expr"])
    if not m:
        raise PipelineError("expr must look like 'column op value'")
    col, op, raw = m.groups()
    _check_cols(t, [col], "Filter rows")
    column = t.column(col)
    if op == "contains":
        mask = pc.match_substring(pc.cast(column, pa.string()), raw.strip("'\""), ignore_case=True)
    else:
        value: object = raw.strip("'\"")
        if pa.types.is_integer(column.type) or pa.types.is_floating(column.type):
            try:
                value = float(raw)
            except ValueError as exc:
                raise PipelineError(f"{col} is numeric; {raw!r} is not a number") from exc
        elif pa.types.is_boolean(column.type):
            value = raw.lower() in ("true", "1", "yes")
        mask = _OPS[op](column, pa.scalar(value, type=column.type if not isinstance(value, float) else pa.float64()))
    out = t.filter(pc.fill_null(mask, False))
    env.log(f"filter: kept {out.num_rows} of {t.num_rows} rows")
    return Context(table=out, videos=ctx.videos, files=ctx.files)


@step(
    "split",
    "Train/val/test split",
    "table",
    "Add a 'split' column. Stratifies by a label column when given.",
    {
        "train": Param("float", 0.8),
        "val": Param("float", 0.1),
        "test": Param("float", 0.1),
        "stratify": Param("str", "", "Label column to stratify by"),
        "group": Param("str", "", "Keep rows sharing this column in one split (e.g. game, video)"),
        "seed": Param("int", 0),
    },
)
def split(ctx: Context, p: dict, env: Env) -> Context:
    t = _need_table(ctx, "Split")
    ratios = np.array([p["train"], p["val"], p["test"]], dtype=float)
    if (ratios < 0).any() or ratios.sum() <= 0:
        raise PipelineError("split ratios must be non-negative and sum to more than 0")
    ratios /= ratios.sum()
    names = np.array(["train", "val", "test"])
    rng = np.random.default_rng(p["seed"])
    n = t.num_rows
    assign = np.empty(n, dtype=object)

    def allocate(indices: np.ndarray) -> None:
        indices = rng.permutation(indices)
        bounds = np.round(np.cumsum(ratios) * len(indices)).astype(int)
        for name, chunk in zip(names, np.split(indices, bounds[:-1])):
            assign[chunk] = name

    if p["group"]:
        _check_cols(t, [p["group"]], "Split")
        groups = np.array([str(g) for g in t.column(p["group"]).to_pylist()])
        uniq = np.unique(groups)
        group_split = np.empty(len(uniq), dtype=object)
        perm = rng.permutation(len(uniq))
        bounds = np.round(np.cumsum(ratios) * len(uniq)).astype(int)
        for name, chunk in zip(names, np.split(perm, bounds[:-1])):
            group_split[chunk] = name
        lookup = dict(zip(uniq, group_split))
        assign = np.array([lookup[g] for g in groups], dtype=object)
    elif p["stratify"]:
        _check_cols(t, [p["stratify"]], "Split")
        labels = np.array([str(v) for v in t.column(p["stratify"]).to_pylist()])
        for label in np.unique(labels):
            allocate(np.flatnonzero(labels == label))
    else:
        allocate(np.arange(n))
    out = t.append_column("split", pa.array(assign.tolist(), pa.string()))
    counts = {str(k): int((assign == k).sum()) for k in names}
    env.log(f"split: {counts}")
    return Context(table=out, videos=ctx.videos, files=ctx.files)


@step("sample", "Sample rows", "table", "Random sample of N rows.", {"n": Param("int", 1000), "seed": Param("int", 0)})
def sample(ctx: Context, p: dict, env: Env) -> Context:
    t = _need_table(ctx, "Sample")
    if t.num_rows <= p["n"]:
        return ctx
    idx = np.sort(np.random.default_rng(p["seed"]).choice(t.num_rows, size=p["n"], replace=False))
    return Context(table=t.take(pa.array(idx)), videos=ctx.videos, files=ctx.files)


@step(
    "load_images",
    "Load images",
    "source",
    "Index image files from downloaded assets. With 'folder' labels, each image's parent folder is its class.",
    {
        "asset_ids": Param("int_list", required=True),
        "label_from": Param("choice", "folder", "Where labels come from", ["folder", "none"]),
        "max_images": Param("int", 200_000),
    },
    source=True,
)
def load_images(ctx: Context, p: dict, env: Env) -> Context:
    from PIL import Image

    rows: dict[str, list] = {k: [] for k in ("asset_id", "path", "label", "width", "height")}
    for aid in p["asset_ids"]:
        root = env.asset_paths.get(aid)
        if root is None or not root.exists():
            raise PipelineError(f"asset {aid} is not downloaded")
        for f in sorted(x for x in root.rglob("*") if x.suffix.lower() in IMAGE_EXTS):
            if len(rows["path"]) >= p["max_images"]:
                break
            try:
                with Image.open(f) as im:
                    w, h = im.size
            except Exception:
                env.log(f"skipped unreadable image {f.name}")
                continue
            rel = f.relative_to(root)
            rows["asset_id"].append(aid)
            rows["path"].append(str(rel))
            rows["label"].append(rel.parent.name if p["label_from"] == "folder" and rel.parent != Path(".") else None)
            rows["width"].append(w)
            rows["height"].append(h)
    if not rows["path"]:
        raise PipelineError("no images found in the selected assets")
    schema = pa.schema([("asset_id", pa.int32()), ("path", pa.string()), ("label", pa.string()),
                        ("width", pa.int32()), ("height", pa.int32())])
    return Context(table=pa.table(rows, schema=schema))


@step(
    "chunk_text",
    "Chunk text",
    "text",
    "Split a text column into overlapping chunks (one row per chunk).",
    {"column": Param("str", "text"), "size": Param("int", 2000, "Characters per chunk"), "overlap": Param("int", 200)},
)
def chunk_text(ctx: Context, p: dict, env: Env) -> Context:
    t = _need_table(ctx, "Chunk text")
    _check_cols(t, [p["column"]], "Chunk text")
    if p["size"] <= 0 or not 0 <= p["overlap"] < p["size"]:
        raise PipelineError("size must be > 0 and 0 <= overlap < size")
    out = []
    for row in t.to_pylist():
        text = row.get(p["column"]) or ""
        stride = p["size"] - p["overlap"]
        for i, start in enumerate(range(0, max(len(text), 1), stride)):
            piece = text[start : start + p["size"]]
            if piece.strip():
                out.append({**row, p["column"]: piece, "chunk": i})
            if start + p["size"] >= len(text):
                break
    schema = t.schema.append(pa.field("chunk", pa.int32())) if "chunk" not in t.column_names else t.schema
    return Context(table=pa.Table.from_pylist(out, schema=schema), videos=ctx.videos, files=ctx.files)


@step(
    "inline_rows",
    "Inline rows",
    "source",
    "Paste a small CSV table (header row first), e.g. seed examples or a label map.",
    {"csv": Param("str", required=True, help="CSV text with a header row")},
    source=True,
)
def inline_rows(ctx: Context, p: dict, env: Env) -> Context:
    import pyarrow.csv as pacsv

    if len(p["csv"]) > 1_000_000:
        raise PipelineError("inline CSV is limited to 1 MB; download a file instead")
    try:
        t = pacsv.read_csv(pa.py_buffer(p["csv"].strip().encode()))
    except pa.ArrowInvalid as exc:
        raise PipelineError(f"invalid CSV: {exc}") from exc
    return Context(table=t)
