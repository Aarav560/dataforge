"""Gather workers (plan §2.5): download selected results to local storage (stand-in for S3/R2)."""
from __future__ import annotations

import asyncio
import base64
import os
import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse, unquote

from . import db, http
from .config import HF_TOKEN, KAGGLE_KEY, KAGGLE_USERNAME, MAX_DOWNLOAD_BYTES, RAW_DIR
from .connectors.base import clean_text
from .events import Job


MAX_FILES_PER_ASSET = int(os.environ.get("DATAFORGE_MAX_FILES", "3000"))


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:120] or "file"


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, limit: int):
        self.limit, self.used = limit, 0

    def take(self, n: int) -> None:
        self.used += n
        if self.used > self.limit:
            raise BudgetExceeded(f"download exceeds size cap ({self.limit / 1e9:.1f} GB) — raise DATAFORGE_MAX_DOWNLOAD_MB")


async def stream_to(url: str, dest: Path, budget: Budget, job: Job, label: str, headers: dict | None = None) -> tuple[int, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    size = 0
    async with http.client().stream("GET", url, headers=headers or {}, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        if total and budget.used + total > budget.limit:
            raise BudgetExceeded(f"{label}: {total / 1e6:.0f} MB exceeds remaining size cap")
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            last = 0
            async for chunk in r.aiter_bytes(1 << 16):
                f.write(chunk)
                sha.update(chunk)
                size += len(chunk)
                budget.take(len(chunk))
                if size - last > 8 << 20:
                    last = size
                    job.log(f"{label}: {size / 1e6:.0f} MB" + (f" / {total / 1e6:.0f} MB" if total else ""))
        tmp.replace(dest)
    return size, sha.hexdigest()


def _cleanup_part(dest: Path) -> None:
    try:
        dest.with_suffix(dest.suffix + ".part").unlink(missing_ok=True)
    except OSError:
        pass


async def _hf_dataset(spec, out, budget, job):
    repo = spec["repo"]
    files: list[tuple[str, str]] = []
    try:  # auto-converted Parquet from the free datasets-server
        data = await http.get_json(f"https://datasets-server.huggingface.co/parquet?dataset={repo}")
        for f in data.get("parquet_files", [])[:64]:
            files.append((f"{f['config']}/{f['split']}/{f['filename']}", f["url"]))
    except Exception:  # noqa: BLE001
        pass
    if files and await _hf_images_not_embedded(repo, data):
        job.log(f"{repo}: Parquet only references images — fetching the image files from the repo instead")
        files = []
    if not files:
        info = await http.get_json(f"https://huggingface.co/api/datasets/{repo}", headers={"Authorization": f"Bearer {HF_TOKEN}"} if HF_TOKEN else {})
        for s in info.get("siblings", []):
            name = s["rfilename"]
            if re.search(r"\.(parquet|csv|tsv|jsonl?|txt|arrow|zip|tar|gz|npz|npy|png|jpe?g|webp|bmp|mp4)$", name, re.I):
                files.append((name, f"https://huggingface.co/datasets/{repo}/resolve/main/{name}"))
    if not files:
        raise RuntimeError("no downloadable data files found (dataset may use a loading script)")
    return files


async def _hf_images_not_embedded(repo: str, parquet_listing: dict) -> bool:
    """Some image datasets' auto-Parquet holds only hf:// path references (a few hundred bytes per row)."""
    try:
        info = await http.get_json(f"https://datasets-server.huggingface.co/info?dataset={repo}")
    except Exception:  # noqa: BLE001
        return False
    for cfg in (info.get("dataset_info") or {}).values():
        feats = cfg.get("features") or {}
        if not any(isinstance(v, dict) and v.get("_type") == "Image" for v in feats.values()):
            continue
        rows = sum(sp.get("num_examples", 0) for sp in (cfg.get("splits") or {}).values())
        size = sum(f.get("size", 0) for f in parquet_listing.get("parquet_files", []))
        return rows > 0 and size / rows < 2000
    return False


def sample_evenly(files: list[tuple[str, str]], cap: int) -> list[tuple[str, str]]:
    """Round-robin across folders so a cap keeps every class represented instead of the first N files."""
    groups: dict[str, list[tuple[str, str]]] = {}
    for f in files:
        groups.setdefault(str(Path(f[0]).parent), []).append(f)
    out: list[tuple[str, str]] = []
    queues = [g[::-1] for g in groups.values()]
    while len(out) < cap and any(queues):
        for q in queues:
            if q and len(out) < cap:
                out.append(q.pop())
    return out


async def _hf_model(spec, out, budget, job):
    info = await http.get_json(f"https://huggingface.co/api/models/{spec['repo']}")
    return [(s["rfilename"], f"https://huggingface.co/{spec['repo']}/resolve/main/{s['rfilename']}") for s in info.get("siblings", [])
            if not s["rfilename"].startswith(".")]


async def _openml(spec, out, budget, job):
    d = (await http.get_json(f"https://www.openml.org/api/v1/json/data/{spec['did']}"))["data_set_description"]
    if d.get("parquet_url"):
        return [(f"{_safe(d['name'])}.parquet", d["parquet_url"])]
    return [(f"{_safe(d['name'])}.arff", d["url"])]


async def _uci(spec, out, budget, job):
    d = (await http.get_json(f"https://archive.ics.uci.edu/api/dataset?id={spec['id']}"))["data"]
    url = d.get("data_url") or f"https://archive.ics.uci.edu/static/public/{spec['id']}/{_safe(d['name']).lower()}.zip"
    return [(Path(urlparse(url).path).name or f"uci_{spec['id']}.csv", url)]


async def _archive_org(spec, out, budget, job):
    md = await http.get_json(f"https://archive.org/metadata/{spec['identifier']}")
    files = [f for f in md.get("files", []) if f.get("source") == "original" and not f["name"].endswith((".xml", ".sqlite", ".torrent"))]
    files.sort(key=lambda f: int(f.get("size") or 0))
    return [(f["name"], f"https://archive.org/download/{spec['identifier']}/{f['name']}") for f in files[:20]]


async def _kaggle(spec, out, budget, job):
    if not (KAGGLE_USERNAME and KAGGLE_KEY):
        raise RuntimeError("Kaggle downloads need a free API key: set KAGGLE_USERNAME and KAGGLE_KEY (kaggle.com → Settings → API)")
    return [(f"{_safe(spec['ref'])}.zip", f"https://www.kaggle.com/api/v1/datasets/download/{spec['ref']}")]


def _kaggle_headers() -> dict:
    tok = base64.b64encode(f"{KAGGLE_USERNAME}:{KAGGLE_KEY}".encode()).decode()
    return {"Authorization": f"Basic {tok}"}


async def _youtube(spec, out: Path, budget: Budget, job: Job) -> tuple[int, str, str]:
    """Video-only H.264 stream ≤720p: frames are all the pipelines need, OpenCV decodes it, and no ffmpeg
    merge is required. Falls back to any single-file format. Local, non-redistributable use only."""
    def run():
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "outtmpl": str(out / "%(id)s.%(ext)s"),
                "format": "bestvideo[ext=mp4][vcodec^=avc1][height<=720]/bestvideo[ext=mp4][height<=720]/best[ext=mp4][height<=720]/bestvideo[height<=720]/best", "max_filesize": budget.limit - budget.used,
                "writeinfojson": True, "noplaylist": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(spec["url"], download=True)
            return ydl.prepare_filename(info)
    path = Path(await asyncio.to_thread(run))
    size = path.stat().st_size if path.exists() else 0
    budget.take(size)
    return size, _sha_file(path) if path.exists() else "", str(path)


async def _webpage(spec, out: Path, budget: Budget, job: Job) -> tuple[int, str, str]:
    url = spec["url"]
    if not await http.robots_allowed(url):
        raise RuntimeError("blocked by robots.txt")
    r = await http.get(url)
    r.raise_for_status()
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "html.parser")
    for t in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        t.decompose()
    text = clean_text(soup.get_text(" "))
    name = _safe(urlparse(url).netloc + urlparse(url).path)
    (out / f"{name}.html").write_text(r.text, encoding="utf-8")
    p = out / f"{name}.txt"
    p.write_text(f"URL: {url}\nTITLE: {soup.title.string if soup.title else ''}\n\n{text}", encoding="utf-8")
    budget.take(len(r.content))
    return len(r.content), hashlib.sha256(r.content).hexdigest(), str(p)


def _sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


LISTERS = {"hf_dataset": _hf_dataset, "hf_model": _hf_model, "openml": _openml, "uci": _uci,
           "archive_org": _archive_org, "kaggle": _kaggle}
DIRECT = {"youtube": _youtube, "webpage": _webpage}


async def gather_one(job: Job, result: dict, budget: Budget) -> dict:
    pid = result["project_id"]
    spec = (result.get("meta") or {}).get("download") or {}
    out = RAW_DIR / pid / _safe(f"{result['source']}_{result['title']}")[:100]
    out.mkdir(parents=True, exist_ok=True)
    asset = db.insert("assets", project_id=pid, result_id=result["id"], storage_path=str(out), status="downloading", size_bytes=0)
    typ = spec.get("type")
    job.log(f"⇣ {result['title']} ({typ})", source=result["source"])
    try:
        if typ == "pip":
            (out / "INSTALL.txt").write_text(f"pip install {spec['package']}\n{result['url']}\n", encoding="utf-8")
            db.update("assets", asset["id"], status="done", size_bytes=0, checksum="")
            return {"result_id": result["id"], "status": "done", "path": str(out), "note": f"pip install {spec['package']}"}
        if typ in DIRECT:
            size, sha, path = await DIRECT[typ](spec, out, budget, job)
            db.update("assets", asset["id"], status="done", size_bytes=size, checksum=sha, storage_path=path)
            return {"result_id": result["id"], "status": "done", "path": path, "size": size}
        if typ in LISTERS:
            files = await LISTERS[typ](spec, out, budget, job)
        elif typ == "files":
            files = [(f["name"], f["url"]) for f in spec.get("files", []) if f.get("url")]
        elif typ == "github":
            files = [(f"{_safe(spec['repo'])}.zip", f"https://codeload.github.com/{spec['repo']}/zip/refs/heads/{spec.get('branch', 'main')}")]
        elif typ == "url" and spec.get("url"):
            files = [(spec.get("filename") or unquote(Path(urlparse(spec["url"]).path).name) or "download", spec["url"])]
        else:
            raise RuntimeError(f"no download method for this result ({typ or 'none'}) — open the link manually")
        headers = _kaggle_headers() if typ == "kaggle" else ({"Authorization": f"Bearer {HF_TOKEN}"} if HF_TOKEN and typ.startswith("hf") else None)
        if len(files) > MAX_FILES_PER_ASSET:
            job.log(f"{result['title']}: {len(files):,} files — fetching an even sample of {MAX_FILES_PER_ASSET:,} across folders", level="warn")
            files = sample_evenly(files, MAX_FILES_PER_ASSET)
        sem = asyncio.Semaphore(16 if len(files) > 50 else 8)  # many small files are latency-bound
        shas: dict[str, str] = {}
        total = 0

        capped = False

        async def fetch(name: str, url: str) -> None:
            nonlocal total, capped
            if capped:
                return
            async with sem:
                if capped:
                    return
                dest = out / Path(*[_safe(p) for p in Path(name).parts])
                try:
                    size, sha = await stream_to(url, dest, budget, job, name, headers)
                except BudgetExceeded:
                    capped = True
                    _cleanup_part(dest)
                    return
                total += size
                shas[name] = sha
                if len(files) > 20 and len(shas) % 50 == 0:
                    job.log(f"{result['title']}: {len(shas)}/{len(files)} files")

        await asyncio.gather(*(fetch(n, u) for n, u in files))
        if not shas:
            raise BudgetExceeded("size cap reached before any file finished — raise DATAFORGE_MAX_DOWNLOAD_MB")
        digest = hashlib.sha256("".join(shas[n] for n, _ in files if n in shas).encode()).hexdigest()
        # Hitting the cap keeps what finished: a partial asset is still usable (sampling is spread across classes).
        note = f"partial: {len(shas):,}/{len(files):,} files (size cap)" if capped else None
        if capped:
            job.log(f"{result['title']}: size cap reached — kept {len(shas):,}/{len(files):,} files", level="warn")
        db.update("assets", asset["id"], status="done", size_bytes=total, checksum=digest, error=note)
        return {"result_id": result["id"], "status": "partial" if capped else "done", "path": str(out), "size": total,
                "files": len(shas), "note": note}
    except Exception as e:  # noqa: BLE001
        db.update("assets", asset["id"], status="error", error=str(e)[:500])
        job.log(f"✗ {result['title']}: {e}", level="error", source=result["source"])
        return {"result_id": result["id"], "status": "error", "error": str(e)[:300]}


async def run_gather(job: Job, project_id: str, result_ids: list[str]) -> dict:
    results = [r for r in (db.get("search_results", rid) for rid in result_ids) if r and r["project_id"] == project_id]
    budget = Budget(MAX_DOWNLOAD_BYTES)
    sem = asyncio.Semaphore(4)
    done = 0
    outs = []

    async def one(r):
        nonlocal done
        async with sem:
            res = await gather_one(job, r, budget)
        done += 1
        job.progress(done / len(results), f"{done}/{len(results)} downloaded · {budget.used / 1e6:.0f} MB")
        outs.append(res)

    await asyncio.gather(*(one(r) for r in results))
    return {"items": outs, "bytes": budget.used}


def list_assets(project_id: str) -> list[dict]:
    rows = db.query("""SELECT a.*, r.title, r.source, r.kind, r.license, r.license_class, r.url
                       FROM assets a LEFT JOIN search_results r ON r.id = a.result_id
                       WHERE a.project_id = ? ORDER BY a.created_at DESC""", (project_id,))
    return rows
