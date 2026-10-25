from pathlib import Path

import httpx

from .base import Connector, RawResult, parse_dt, stream_to_file

API = "https://www.kaggle.com/api/v1"


def parse_item(item: dict) -> RawResult | None:
    ref = item.get("ref")
    if not ref or item.get("isPrivate"):
        return None
    return RawResult(
        source="kaggle",
        kind="dataset",
        external_id=ref,
        url=item.get("url") or f"https://www.kaggle.com/datasets/{ref}",
        title=item.get("title") or ref,
        description=item.get("subtitle") or item.get("description") or None,
        size_bytes=item.get("totalBytes"),
        license=item.get("licenseName"),
        updated_at=parse_dt(item.get("lastUpdated")),
        downloads=item.get("downloadCount"),
        likes=item.get("voteCount"),
        usability=item.get("usabilityRating"),
        meta={"thumbnail": item.get("thumbnailImageUrl")},
    )


class KaggleConnector(Connector):
    name = "kaggle"
    kinds = frozenset({"dataset"})

    def __init__(self, client, username: str | None = None, key: str | None = None):
        super().__init__(client)
        self.auth = httpx.BasicAuth(username, key) if username and key else None

    async def search(self, query: str, kind: str, limit: int) -> list[RawResult]:
        resp = await self.client.get(
            f"{API}/datasets/list",
            params={"search": query, "page": 1, "sortBy": "hottest"},
            auth=self.auth,
        )
        resp.raise_for_status()
        return [r for item in resp.json()[:limit] if (r := parse_item(item)) is not None]

    async def list_page(self, page: int) -> list[RawResult]:
        resp = await self.client.get(f"{API}/datasets/list", params={"page": page, "sortBy": "votes"}, auth=self.auth)
        resp.raise_for_status()
        return [r for item in resp.json() if (r := parse_item(item)) is not None]

    async def resolve(self, ref) -> RawResult | None:
        slug = ref.external_id.split("/")[-1]
        for r in await self.search(slug.replace("-", " "), "dataset", 20):
            if r.external_id.lower() == ref.external_id.lower():
                return r
        return RawResult(source=self.name, kind="dataset", external_id=ref.external_id, url=ref.url, title=ref.external_id)

    async def download(self, external_id: str, kind: str, dest: Path, max_bytes: int) -> int:
        if self.auth is None:
            raise RuntimeError("Kaggle downloads need KAGGLE_USERNAME and KAGGLE_KEY")
        slug = external_id.replace("/", "__")
        return await stream_to_file(
            self.client,
            f"{API}/datasets/download/{external_id}",
            dest / f"{slug}.zip",
            max_bytes,
            auth=self.auth,
        )
