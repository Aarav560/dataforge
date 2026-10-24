"""Papers: arXiv, Semantic Scholar, OpenAlex — all free. Extracts dataset mentions + links."""
from __future__ import annotations

import re
import defusedxml.ElementTree as ET
from urllib.parse import urlencode

from .. import http
from .base import Connector, Hit, clean_text

_LINK = re.compile(r"https?://(?:github\.com|huggingface\.co|zenodo\.org|kaggle\.com|www\.kaggle\.com|drive\.google\.com|osf\.io)/[^\s)\]}>,;\"']+", re.I)
_DATASET_MENTION = re.compile(r"\b([A-Z][A-Za-z0-9\-]{2,}(?:\s[A-Z0-9][A-Za-z0-9\-]+)?)\s+(?:dataset|benchmark|corpus)\b")


def extract_data_refs(text: str) -> dict:
    links = sorted({m.rstrip(".") for m in _LINK.findall(text or "")})
    names = sorted({m for m in _DATASET_MENTION.findall(text or "") if m.lower() not in {"the", "this", "our", "new", "a"}})
    return {"linked_resources": links, "datasets_mentioned": names[:10]}


class ArXiv(Connector):
    name, label, kinds = "arxiv", "arXiv", ("paper",)
    max_queries = 2
    NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

    async def search(self, query, limit):
        terms = " AND ".join(f"all:{t}" for t in re.findall(r"\w+", query)[:8])
        r = await http.get(f"https://export.arxiv.org/api/query?{urlencode({'search_query': terms, 'max_results': limit, 'sortBy': 'relevance'})}")
        r.raise_for_status()
        root = ET.fromstring(r.text)
        hits = []
        for e in root.findall("a:entry", self.NS):
            abstract = clean_text(e.findtext("a:summary", "", self.NS))
            comment = e.findtext("arxiv:comment", "", self.NS) or ""
            pdf = next((l.get("href") for l in e.findall("a:link", self.NS) if l.get("title") == "pdf"), None)
            hits.append(Hit(
                source=self.name, kind="paper", url=e.findtext("a:id", "", self.NS),
                title=clean_text(e.findtext("a:title", "", self.NS)), description=abstract, format="pdf",
                license="arXiv non-exclusive",
                meta={"published": e.findtext("a:published", "", self.NS), "updated": e.findtext("a:updated", "", self.NS),
                      "authors": [a.findtext("a:name", "", self.NS) for a in e.findall("a:author", self.NS)][:6],
                      "pdf": pdf, **extract_data_refs(abstract + " " + comment)},
                download={"type": "url", "url": pdf, "filename": (e.findtext("a:id", "", self.NS).rsplit("/", 1)[-1] + ".pdf")} if pdf else None,
            ))
        return hits


class SemanticScholar(Connector):
    name, label, kinds = "semantic_scholar", "Semantic Scholar", ("paper",)
    max_queries = 1

    async def search(self, query, limit):
        fields = "title,abstract,url,year,citationCount,openAccessPdf,externalIds,venue"
        r = await http.get(f"https://api.semanticscholar.org/graph/v1/paper/search?{urlencode({'query': query, 'limit': limit, 'fields': fields})}", retries=1)
        if r.status_code == 429:
            raise RuntimeError("Semantic Scholar anonymous pool is rate-limited — OpenAlex covers papers meanwhile")
        r.raise_for_status()
        hits = []
        for p in r.json().get("data", []) or []:
            pdf = (p.get("openAccessPdf") or {}).get("url")
            hits.append(Hit(
                source=self.name, kind="paper", url=p.get("url") or "", title=p.get("title") or "",
                description=p.get("abstract") or "", format="pdf" if pdf else None,
                meta={"citations": p.get("citationCount", 0), "year": p.get("year"), "venue": p.get("venue"), "pdf": pdf,
                      "arxiv": (p.get("externalIds") or {}).get("ArXiv"), **extract_data_refs(p.get("abstract") or "")},
                download={"type": "url", "url": pdf} if pdf else None,
            ))
        return hits


class OpenAlex(Connector):
    name, label, kinds = "openalex", "OpenAlex", ("paper",)

    @staticmethod
    def _abstract(inv: dict | None) -> str:
        if not inv:
            return ""
        pos = sorted((i, w) for w, idx in inv.items() for i in idx)
        return " ".join(w for _, w in pos)

    async def search(self, query, limit):
        q = urlencode({"search": query, "per-page": limit, "mailto": "dataforge@localhost"})
        data = await http.get_json(f"https://api.openalex.org/works?{q}")
        hits = []
        for w in data.get("results", []):
            abstract = self._abstract(w.get("abstract_inverted_index"))
            oa = w.get("open_access") or {}
            loc = w.get("primary_location") or {}
            pdf = loc.get("pdf_url") or (oa.get("oa_url") if oa.get("is_oa") else None)
            hits.append(Hit(
                source=self.name, kind="paper", url=w.get("doi") or w.get("id"), title=w.get("title") or "",
                description=abstract, license=loc.get("license"),
                meta={"citations": w.get("cited_by_count", 0), "year": w.get("publication_year"), "pdf": pdf,
                      "venue": ((loc.get("source") or {}).get("display_name")), **extract_data_refs(abstract)},
                download={"type": "url", "url": pdf} if pdf and pdf.endswith(".pdf") else None,
            ))
        return hits
