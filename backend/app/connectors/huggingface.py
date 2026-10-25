from pathlib import Path
from urllib.parse import quote

from .base import Connector, RawResult, parse_dt, safe_relpath, stream_to_file

API = "https://huggingface.co/api"
HUB = "https://huggingface.co"


def _tag_values(tags: list[str], prefix: str) -> list[str]:
    return [t.split(":", 1)[1] for t in tags if t.startswith(prefix + ":")]


def _query_variants(query: str) -> list[str]:
    words = query.split()
    variants = [query]
    if len(words) > 1:
        variants += ["-".join(words), " ".join(words[:2]), max(words, key=len)]
    return list(dict.fromkeys(variants))


class HuggingFaceConnector(Connector):
    name = "huggingface"
    kinds = frozenset({"dataset", "model"})

    def __init__(self, client, token: str | None = None):
        super().__init__(client)
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    async def search(self, query: str, kind: str, limit: int) -> list[RawResult]:
        # Hub search matches substrings of repo ids, so multi-word queries often
        # miss. Relax the query step by step until something matches.
        for variant in _query_variants(query):
            items = await self._search(variant, kind, limit)
            if items:
                return items
        return []

    async def _search(self, query: str, kind: str, limit: int) -> list[RawResult]:
        endpoint = "datasets" if kind == "dataset" else "models"
        resp = await self.client.get(
            f"{API}/{endpoint}",
            params={"search": query, "limit": limit, "full": "true", "sort": "downloads"},
            headers=self.headers,
        )
        resp.raise_for_status()
        return [r for item in resp.json() if (r := self._parse(item, kind)) is not None]

    async def resolve(self, ref) -> RawResult | None:
        endpoint = "datasets" if ref.kind == "dataset" else "models"
        resp = await self.client.get(f"{API}/{endpoint}/{ref.external_id}", headers=self.headers)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return self._parse(resp.json(), ref.kind)

    def _parse(self, item: dict, kind: str) -> RawResult | None:
        if item.get("private") or item.get("disabled"):
            return None
        tags = item.get("tags") or []
        card = item.get("cardData") or {}
        license_ = card.get("license") or next(iter(_tag_values(tags, "license")), None)
        if isinstance(license_, list):
            license_ = license_[0] if license_ else None
        repo_id = item["id"]
        url = f"{HUB}/datasets/{repo_id}" if kind == "dataset" else f"{HUB}/{repo_id}"
        formats = _tag_values(tags, "format")
        description = item.get("description") or card.get("pretty_name")
        if kind == "model" and item.get("pipeline_tag"):
            description = description or item["pipeline_tag"]
        return RawResult(
            source=self.name,
            kind=kind,
            external_id=repo_id,
            url=url,
            title=repo_id,
            description=description,
            format=",".join(formats) or item.get("library_name"),
            license=license_,
            updated_at=parse_dt(item.get("lastModified")),
            downloads=item.get("downloads"),
            likes=item.get("likes"),
            tags=tags,
            meta={
                "gated": bool(item.get("gated")),
                "size_categories": _tag_values(tags, "size_categories"),
                "pipeline_tag": item.get("pipeline_tag"),
            },
        )

    async def download(self, external_id: str, kind: str, dest: Path, max_bytes: int) -> int:
        endpoint = "datasets" if kind == "dataset" else "models"
        resp = await self.client.get(
            f"{API}/{endpoint}/{external_id}", params={"blobs": "true"}, headers=self.headers
        )
        resp.raise_for_status()
        siblings = resp.json().get("siblings") or []
        prefix = f"{HUB}/datasets/{external_id}" if kind == "dataset" else f"{HUB}/{external_id}"
        total = 0
        for sib in siblings:
            name = sib["rfilename"]
            if name == ".gitattributes":
                continue
            rel = safe_relpath(name)
            total += await stream_to_file(
                self.client,
                f"{prefix}/resolve/main/{quote(name)}",
                dest / rel,
                max_bytes - total,
                headers=self.headers,
            )
        return total
