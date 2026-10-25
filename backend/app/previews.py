"""Lightweight previews of search results and downloaded files.

Remote previews never download whole datasets: Hugging Face's dataset viewer
returns the first rows, and READMEs are fetched raw.
"""

import json
import mimetypes
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.json as pajson
import pyarrow.parquet as pq

from .models import SearchResult

HF_VIEWER = "https://datasets-server.huggingface.co"
MAX_ROWS = 20
MAX_TEXT = 20_000

TABLE_EXTS = {".parquet", ".csv", ".tsv", ".jsonl", ".ndjson", ".json"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov"}
TEXT_EXTS = {".md", ".txt", ".py", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".rst"}


def _jsonable(v):
    if isinstance(v, bytes):
        return f"<{len(v)} bytes>"
    if isinstance(v, (list, tuple)) and len(v) > 50:
        return list(v[:50]) + ["…"]
    return v if isinstance(v, (str, int, float, bool, type(None), list, dict)) else str(v)


async def _readme(client: httpx.AsyncClient, urls: list[str]) -> str | None:
    for url in urls:
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code == 200:
            return resp.text[:MAX_TEXT]
    return None


async def _hf_card(client: httpx.AsyncClient, repo: str) -> dict:
    readme = await _readme(client, [f"https://huggingface.co/datasets/{repo}/raw/main/README.md"])
    return {"type": "markdown", "text": readme or "The dataset viewer has no rows for this dataset and it has no README."}


async def _hf_dataset(client: httpx.AsyncClient, repo: str) -> dict:
    splits = await client.get(f"{HF_VIEWER}/splits", params={"dataset": repo})
    if splits.status_code != 200 or not splits.json().get("splits"):
        return await _hf_card(client, repo)
    first = splits.json()["splits"][0]
    resp = await client.get(
        f"{HF_VIEWER}/first-rows", params={"dataset": repo, "config": first["config"], "split": first["split"]}
    )
    # The viewer fails on many datasets (unsupported formats, still processing).
    if resp.status_code != 200:
        return await _hf_card(client, repo)
    data = resp.json()
    columns = [{"name": f["name"], "type": json.dumps(f.get("type", {}).get("_type") or f.get("type"))[:60]}
               for f in data.get("features", [])]
    rows, images = [], []
    for item in data.get("rows", [])[:MAX_ROWS]:
        row = {}
        for k, v in item.get("row", {}).items():
            if isinstance(v, dict) and "src" in v:
                images.append(v["src"])
                row[k] = "[image]"
            else:
                row[k] = _jsonable(v)
        rows.append(row)
    return {"type": "table", "config": first["config"], "split": first["split"], "columns": columns,
            "rows": rows, "images": images[:24]}


async def result_preview(result: SearchResult, client: httpx.AsyncClient) -> dict:
    meta = result.meta or {}
    if result.source == "youtube":
        return {"type": "video", "embed": f"https://www.youtube-nocookie.com/embed/{result.external_id}",
                "thumbnail": meta.get("thumbnail")}
    if result.source == "huggingface" and result.kind == "dataset":
        return await _hf_dataset(client, result.external_id)
    if result.source == "huggingface":
        text = await _readme(client, [f"https://huggingface.co/{result.external_id}/raw/main/README.md"])
        return {"type": "markdown", "text": text or "No model card."}
    if result.source == "github":
        base = f"https://raw.githubusercontent.com/{result.external_id}/HEAD"
        text = await _readme(client, [f"{base}/README.md", f"{base}/readme.md", f"{base}/README.rst"])
        return {"type": "markdown", "text": text or "No README."}
    if result.kind == "paper":
        return {"type": "paper", "abstract": result.description, "authors": meta.get("authors", []),
                "links": meta.get("links", []), "pdf": meta.get("pdf")}
    return {"type": "meta", "description": result.description, "size_bytes": result.size_bytes,
            "format": result.format, "license": result.license, "thumbnail": meta.get("thumbnail")}


def safe_child(root: Path, rel: str) -> Path:
    """Resolve `rel` under `root`, refusing anything that escapes it."""
    root = root.resolve()
    path = (root / rel).resolve()
    if path != root and root not in path.parents:
        raise ValueError("path escapes the asset directory")
    return path


def file_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in TABLE_EXTS:
        return "table"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in TEXT_EXTS:
        return "text"
    return "binary"


def list_files(root: Path, limit: int = 500) -> list[dict]:
    root = root.resolve()
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out.append({"path": str(p.relative_to(root)), "size": p.stat().st_size, "kind": file_kind(p)})
            if len(out) >= limit:
                break
    return out


def _sample_table(path: Path) -> pa.Table:
    ext = path.suffix.lower()
    if ext == ".parquet":
        pf = pq.ParquetFile(path)
        return pa.Table.from_batches([next(pf.iter_batches(batch_size=MAX_ROWS))]) if pf.metadata.num_rows else pf.schema_arrow.empty_table()
    if ext in (".csv", ".tsv"):
        opts = pacsv.ParseOptions(delimiter="\t" if ext == ".tsv" else ",")
        reader = pacsv.open_csv(path, parse_options=opts, read_options=pacsv.ReadOptions(block_size=1 << 20))
        return pa.Table.from_batches([reader.read_next_batch()]).slice(0, MAX_ROWS)
    if ext in (".jsonl", ".ndjson"):
        with path.open("rb") as fh:
            head = b"".join(line for _, line in zip(range(MAX_ROWS), fh))
        return pajson.read_json(pa.py_buffer(head))
    data = json.loads(path.read_bytes()[: 5 << 20])
    records = data if isinstance(data, list) else data.get("data") if isinstance(data, dict) else None
    if not isinstance(records, list):
        raise ValueError("JSON file is not a list of records")
    return pa.Table.from_pylist([r for r in records[:MAX_ROWS] if isinstance(r, dict)])


def file_preview(path: Path) -> dict:
    kind = file_kind(path)
    if kind == "table":
        t = _sample_table(path)
        return {"type": "table", "columns": [{"name": f.name, "type": str(f.type)} for f in t.schema],
                "rows": [{k: _jsonable(v) for k, v in r.items()} for r in t.to_pylist()]}
    if kind == "text":
        return {"type": "text", "text": path.read_text(errors="replace")[:MAX_TEXT]}
    if kind in ("image", "video"):
        return {"type": kind, "mime": mimetypes.guess_type(path.name)[0]}
    return {"type": "binary", "size": path.stat().st_size}
