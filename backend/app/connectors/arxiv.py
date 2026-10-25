import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .base import Connector, RawResult, extract_links, parse_dt, stream_to_file

API = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _text(el, path: str) -> str | None:
    found = el.find(path, NS)
    return re.sub(r"\s+", " ", found.text).strip() if found is not None and found.text else None


class ArxivConnector(Connector):
    name = "arxiv"
    kinds = frozenset({"paper"})

    async def search(self, query: str, kind: str, limit: int) -> list[RawResult]:
        terms = [t for t in re.findall(r"[\w-]+", query) if len(t) > 1]
        search = " AND ".join(f"all:{t}" for t in terms) or f"all:{query}"
        resp = await self.client.get(
            API, params={"search_query": search, "max_results": limit, "sortBy": "relevance"}
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        out = []
        for entry in root.findall("a:entry", NS):
            abs_url = _text(entry, "a:id")
            if not abs_url:
                continue
            arxiv_id = abs_url.rsplit("/abs/", 1)[-1]
            summary = _text(entry, "a:summary")
            comment = _text(entry, "arxiv:comment")
            authors = [_text(a, "a:name") for a in entry.findall("a:author", NS)]
            links = extract_links(" ".join(filter(None, [summary, comment])))
            out.append(
                RawResult(
                    source=self.name,
                    kind="paper",
                    external_id=arxiv_id,
                    url=abs_url,
                    title=_text(entry, "a:title") or arxiv_id,
                    description=summary,
                    format="pdf",
                    updated_at=parse_dt(_text(entry, "a:updated")),
                    tags=[c.get("term") for c in entry.findall("a:category", NS) if c.get("term")],
                    meta={
                        "authors": authors[:10],
                        "published": _text(entry, "a:published"),
                        "comment": comment,
                        "links": [l.__dict__ for l in links],
                    },
                )
            )
        return out

    async def download(self, external_id: str, kind: str, dest: Path, max_bytes: int) -> int:
        safe = external_id.replace("/", "_")
        return await stream_to_file(self.client, f"https://arxiv.org/pdf/{external_id}", dest / f"{safe}.pdf", max_bytes)
