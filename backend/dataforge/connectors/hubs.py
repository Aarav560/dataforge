"""Dataset & model hubs: Hugging Face, Kaggle, OpenML, Zenodo, UCI."""
from __future__ import annotations

import asyncio
import json
import re
import time
from urllib.parse import quote, urlencode

import base64

from .. import http
from ..config import HF_TOKEN, KAGGLE_KEY, KAGGLE_USERNAME, ROOT
from .base import Connector, Hit, clean_text

_SIZE_CAT = {
    "n<1K": 500, "1K<n<10K": 5_000, "10K<n<100K": 50_000, "100K<n<1M": 500_000,
    "1M<n<10M": 5_000_000, "10M<n<100M": 50_000_000, "100M<n<1B": 500_000_000, "n>1T": 10**12,
}


def _tag(tags: list[str], prefix: str) -> list[str]:
    return [t.split(":", 1)[1] for t in tags if t.startswith(prefix + ":")]


def _hf_headers() -> dict:
    return {"Authorization": f"Bearer {HF_TOKEN}"} if HF_TOKEN else {}


class HuggingFaceDatasets(Connector):
    name, label, kinds = "hf_datasets", "Hugging Face Datasets", ("dataset",)

    async def search(self, query, limit):
        q = urlencode({"search": query, "limit": limit, "full": "true", "sort": "downloads", "direction": -1})
        data = await http.get_json(f"https://huggingface.co/api/datasets?{q}", headers=_hf_headers())
        hits = []
        for d in data:
            tags = d.get("tags", [])
            card = d.get("cardData") or {}
            lic = card.get("license") or (_tag(tags, "license") or [None])[0]
            if isinstance(lic, list):
                lic = lic[0] if lic else None
            size_cat = (_tag(tags, "size_categories") or [None])[0]
            hits.append(Hit(
                source=self.name, kind="dataset", url=f"https://huggingface.co/datasets/{d['id']}",
                title=d["id"], description=card.get("pretty_name") or d.get("description") or " ".join(_tag(tags, "task_categories")),
                format=",".join(_tag(tags, "format")) or None, license=lic,
                meta={"downloads": d.get("downloads", 0), "likes": d.get("likes", 0), "updated": d.get("lastModified"),
                      "rows_estimate": _SIZE_CAT.get(size_cat or ""), "size_category": size_cat,
                      "modalities": _tag(tags, "modality"), "tasks": _tag(tags, "task_categories"),
                      "gated": bool(d.get("gated"))},
                download={"type": "hf_dataset", "repo": d["id"]},
            ))
        return hits


class HuggingFaceModels(Connector):
    name, label, kinds = "hf_models", "Hugging Face Models", ("model",)

    async def search(self, query, limit):
        q = urlencode({"search": query, "limit": limit, "full": "true", "sort": "downloads", "direction": -1})
        data = await http.get_json(f"https://huggingface.co/api/models?{q}", headers=_hf_headers())
        hits = []
        for d in data:
            tags = d.get("tags", [])
            lic = (_tag(tags, "license") or [None])[0]
            hits.append(Hit(
                source=self.name, kind="model", url=f"https://huggingface.co/{d['id']}", title=d["id"],
                description=" · ".join(filter(None, [d.get("pipeline_tag"), d.get("library_name")])),
                format=d.get("library_name"), license=lic,
                meta={"downloads": d.get("downloads", 0), "likes": d.get("likes", 0), "updated": d.get("lastModified"),
                      "pipeline": d.get("pipeline_tag")},
                download={"type": "hf_model", "repo": d["id"]},
            ))
        return hits


class Kaggle(Connector):
    """Kaggle's public listing works without auth; downloads need a free Kaggle API key."""
    name, label, kinds, requires = "kaggle", "Kaggle", ("dataset",), "KAGGLE_USERNAME/KAGGLE_KEY (free)"

    async def search(self, query, limit):
        headers = {}
        if KAGGLE_USERNAME and KAGGLE_KEY:
            tok = base64.b64encode(f"{KAGGLE_USERNAME}:{KAGGLE_KEY}".encode()).decode()
            headers["Authorization"] = f"Basic {tok}"
        data = await http.get_json(f"https://www.kaggle.com/api/v1/datasets/list?search={quote(query)}&sortBy=hottest",
                                   headers=headers)
        hits = []
        for d in data[:limit]:
            hits.append(Hit(
                source=self.name, kind="dataset", url=d.get("url") or f"https://www.kaggle.com/datasets/{d['ref']}",
                title=d.get("title") or d["ref"], description=d.get("subtitle") or d.get("description") or "",
                size_bytes=d.get("totalBytes"), license=d.get("licenseName"),
                meta={"downloads": d.get("downloadCount", 0), "votes": d.get("voteCount", 0),
                      "updated": d.get("lastUpdated"), "usability": d.get("usabilityRating"),
                      "thumbnail": d.get("thumbnailImageUrl"), "ref": d["ref"],
                      "tags": [t.get("name") for t in d.get("tags", [])]},
                download={"type": "kaggle", "ref": d["ref"]},
            ))
        return hits


class _LocalIndex:
    """Tiny pre-indexed catalog (plan §6): synced in the background, cached on disk, searched locally."""

    def __init__(self, name: str, ttl: float = 24 * 3600):
        self.path = ROOT / f"index_{name}.json"
        self.items: list[dict] = []
        self.loaded = 0.0
        self.ttl = ttl
        self.lock = asyncio.Lock()
        if self.path.exists():
            try:
                self.items = json.loads(self.path.read_text(encoding="utf-8"))
                self.loaded = self.path.stat().st_mtime
            except (OSError, ValueError):
                pass

    def stale(self) -> bool:
        return not self.items or time.time() - self.loaded > self.ttl

    def set(self, items: list[dict]) -> None:
        self.items, self.loaded = items, time.time()
        try:
            self.path.write_text(json.dumps(items), encoding="utf-8")
        except OSError:
            pass

    async def ensure(self, fetch, wait: float = 4.0) -> None:
        """Refresh when stale. If we already have (old) items, refresh in the background."""
        if not self.stale():
            return
        async def refresh():
            async with self.lock:
                if self.stale():
                    self.set(await fetch())
        if self.items:
            if not self.lock.locked():
                asyncio.create_task(refresh())
            return
        try:
            await asyncio.wait_for(asyncio.shield(refresh()), timeout=wait)
        except asyncio.TimeoutError:
            raise RuntimeError("building local index (first run) — results will appear on the next search") from None

    def search(self, query: str, key: str, limit: int) -> list[dict]:
        from ..ranking import tokens
        terms = [t for t in tokens(query) if len(t) > 2]
        scored = []
        for it in self.items:
            words = set(re.findall(r"[a-z]+|\d+", str(it.get(key, "")).lower()))
            s = sum(1 for t in terms if t in words or (len(t) >= 5 and any(w.startswith(t) for w in words)))
            if s:
                scored.append((s, it))
        scored.sort(key=lambda x: -x[0])
        return [it for _, it in scored[:limit]]


class OpenML(Connector):
    name, label, kinds = "openml", "OpenML", ("dataset",)
    _index = _LocalIndex("openml")

    @staticmethod
    async def _fetch() -> list[dict]:
        data = await http.get_json("https://www.openml.org/api/v1/json/data/list/status/active/limit/10000", timeout=120)
        return data["data"]["dataset"]

    async def warm(self) -> None:
        await self._index.ensure(self._fetch, wait=300)

    async def search(self, query, limit):
        await self._index.ensure(self._fetch)
        hits = []
        for d in self._index.search(query, "name", limit):
            q = {x["name"]: x["value"] for x in d.get("quality", [])}
            rows = int(float(q.get("NumberOfInstances", 0) or 0))
            hits.append(Hit(
                source=self.name, kind="dataset", url=f"https://www.openml.org/d/{d['did']}", title=d["name"],
                description=f"{rows:,} rows · {int(float(q.get('NumberOfFeatures', 0) or 0))} features · "
                            f"{int(float(q.get('NumberOfClasses', 0) or 0))} classes",
                format=d.get("format"), license="Public",  # OpenML datasets default to CC-BY unless noted
                meta={"rows_estimate": rows, "missing_values": int(float(q.get("NumberOfMissingValues", 0) or 0)),
                      "version": d.get("version"), "did": d["did"]},
                download={"type": "openml", "did": d["did"]},
            ))
        return hits


class UCI(Connector):
    name, label, kinds = "uci", "UCI ML Repository", ("dataset",)
    _index = _LocalIndex("uci")

    @staticmethod
    async def _fetch() -> list[dict]:
        return (await http.get_json("https://archive.ics.uci.edu/api/datasets/list")).get("data", [])

    async def warm(self) -> None:
        await self._index.ensure(self._fetch, wait=60)

    async def search(self, query, limit):
        await self._index.ensure(self._fetch)
        return [Hit(
            source=self.name, kind="dataset", title=d["name"],
            url=f"https://archive.ics.uci.edu/dataset/{d['id']}/{quote(d['name'].lower().replace(' ', '+'))}",
            description="UCI Machine Learning Repository dataset", license="CC BY 4.0",
            meta={"uci_id": d["id"]}, download={"type": "uci", "id": d["id"]},
        ) for d in self._index.search(query, "name", limit)]


class Zenodo(Connector):
    name, label, kinds = "zenodo", "Zenodo", ("dataset",)

    async def search(self, query, limit):
        data = await http.get_json(f"https://zenodo.org/api/records?{urlencode({'q': query, 'size': limit, 'type': 'dataset', 'sort': 'bestmatch'})}")
        hits = []
        for r in data.get("hits", {}).get("hits", []):
            md = r.get("metadata", {})
            files = r.get("files", []) or []
            lic = md.get("license", {})
            hits.append(Hit(
                source=self.name, kind="dataset", url=r.get("links", {}).get("self_html") or f"https://zenodo.org/records/{r['id']}",
                title=md.get("title", ""), description=clean_text(md.get("description")),
                size_bytes=sum(f.get("size", 0) for f in files) or None,
                format=",".join(sorted({f.get("key", "").rsplit(".", 1)[-1] for f in files if "." in f.get("key", "")}))[:60] or None,
                license=lic.get("id") if isinstance(lic, dict) else lic,
                meta={"downloads": r.get("stats", {}).get("downloads", 0), "updated": r.get("modified"),
                      "doi": r.get("doi"), "files": [{"name": f["key"], "size": f.get("size"), "url": f.get("links", {}).get("self")} for f in files[:50]]},
                download={"type": "files", "files": [{"name": f["key"], "url": f.get("links", {}).get("self"), "size": f.get("size")} for f in files[:50]]},
            ))
        return hits
