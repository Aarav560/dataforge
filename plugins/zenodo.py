"""Example DataForge plugin: search and download datasets from Zenodo.

Drop files like this into the `plugins/` folder (or set PLUGINS_DIR). Any
`Connector` subclass defined here becomes a source for the kinds it declares.
"""

import html
import re
from pathlib import Path

from app.connectors.base import Connector, RawResult, parse_dt, safe_relpath, stream_to_file

API = "https://zenodo.org/api/records"


class ZenodoConnector(Connector):
    """Zenodo: open research data from CERN, with DOIs and explicit licenses."""

    name = "zenodo"
    kinds = frozenset({"dataset"})

    def __init__(self, client, settings=None):
        super().__init__(client)

    async def search(self, query: str, kind: str, limit: int) -> list[RawResult]:
        resp = await self.client.get(API, params={"q": query, "type": "dataset", "size": limit, "sort": "bestmatch"})
        resp.raise_for_status()
        out = []
        for hit in resp.json().get("hits", {}).get("hits", []):
            meta = hit.get("metadata", {})
            if meta.get("access_right") != "open":
                continue
            description = re.sub(r"<[^>]+>", " ", html.unescape(meta.get("description") or ""))
            files = hit.get("files") or []
            stats = hit.get("stats") or {}
            out.append(
                RawResult(
                    source=self.name,
                    kind="dataset",
                    external_id=str(hit["id"]),
                    url=hit.get("links", {}).get("self_html") or f"https://zenodo.org/records/{hit['id']}",
                    title=meta.get("title") or str(hit["id"]),
                    description=re.sub(r"\s+", " ", description).strip()[:1000] or None,
                    size_bytes=sum(f.get("size", 0) for f in files) or None,
                    format=",".join(sorted({f["key"].rsplit(".", 1)[-1] for f in files if "." in f.get("key", "")}))[:60] or None,
                    license=(meta.get("license") or {}).get("id"),
                    updated_at=parse_dt(hit.get("modified")),
                    downloads=stats.get("downloads"),
                    likes=stats.get("views"),
                    tags=meta.get("keywords") or [],
                    meta={"doi": hit.get("doi"), "creators": [c.get("name") for c in meta.get("creators", [])][:5]},
                )
            )
        return out

    async def download(self, external_id: str, kind: str, dest: Path, max_bytes: int) -> int:
        resp = await self.client.get(f"{API}/{external_id}")
        resp.raise_for_status()
        total = 0
        for f in resp.json().get("files") or []:
            rel = safe_relpath(f["key"])
            total += await stream_to_file(self.client, f["links"]["self"], dest / rel, max_bytes - total)
        return total
