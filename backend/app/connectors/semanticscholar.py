from pathlib import Path

from .base import Connector, RawResult, extract_links, parse_dt, stream_to_file

API = "https://api.semanticscholar.org/graph/v1"
FIELDS = "title,abstract,url,year,citationCount,externalIds,openAccessPdf,publicationDate,fieldsOfStudy,authors"


class SemanticScholarConnector(Connector):
    name = "semanticscholar"
    kinds = frozenset({"paper"})

    def __init__(self, client, api_key: str | None = None):
        super().__init__(client)
        self.headers = {"x-api-key": api_key} if api_key else {}

    async def search(self, query: str, kind: str, limit: int) -> list[RawResult]:
        resp = await self.client.get(
            f"{API}/paper/search", params={"query": query, "limit": limit, "fields": FIELDS}, headers=self.headers
        )
        resp.raise_for_status()
        out = []
        for p in resp.json().get("data") or []:
            pdf = (p.get("openAccessPdf") or {}).get("url")
            ext = p.get("externalIds") or {}
            out.append(
                RawResult(
                    source=self.name,
                    kind="paper",
                    external_id=p["paperId"],
                    url=p.get("url") or f"https://www.semanticscholar.org/paper/{p['paperId']}",
                    title=p.get("title") or p["paperId"],
                    description=p.get("abstract"),
                    format="pdf" if pdf else None,
                    updated_at=parse_dt(p.get("publicationDate")),
                    # Citations are the popularity signal for papers.
                    downloads=p.get("citationCount"),
                    tags=p.get("fieldsOfStudy") or [],
                    meta={
                        "pdf": pdf,
                        "arxiv": ext.get("ArXiv"),
                        "doi": ext.get("DOI"),
                        "year": p.get("year"),
                        "authors": [a.get("name") for a in (p.get("authors") or [])[:10]],
                        "links": [l.__dict__ for l in extract_links(p.get("abstract"))],
                    },
                )
            )
        return out

    async def download(self, external_id: str, kind: str, dest: Path, max_bytes: int) -> int:
        resp = await self.client.get(
            f"{API}/paper/{external_id}", params={"fields": "openAccessPdf,externalIds"}, headers=self.headers
        )
        resp.raise_for_status()
        data = resp.json()
        url = (data.get("openAccessPdf") or {}).get("url")
        arxiv = (data.get("externalIds") or {}).get("ArXiv")
        if not url and arxiv:
            url = f"https://arxiv.org/pdf/{arxiv}"
        if not url:
            raise RuntimeError("no open-access PDF for this paper")
        return await stream_to_file(self.client, url, dest / f"{external_id}.pdf", max_bytes, headers=self.headers)
