from pathlib import Path

from .base import Connector, RawResult, parse_dt, stream_to_file

API = "https://api.github.com"

# Extra qualifiers that steer repository search toward each result kind.
_KIND_HINTS = {
    "dataset": "dataset",
    "model": "pretrained",
    "environment": "environment",
    "code": "",
}


class GitHubConnector(Connector):
    name = "github"
    kinds = frozenset({"dataset", "model", "environment", "code"})

    def __init__(self, client, token: str | None = None):
        super().__init__(client)
        self.headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    async def search(self, query: str, kind: str, limit: int) -> list[RawResult]:
        hint = _KIND_HINTS.get(kind, "")
        q = query if not hint or hint in query.lower() else f"{query} {hint}"
        resp = await self.client.get(
            f"{API}/search/repositories",
            params={"q": q, "per_page": limit, "sort": "stars", "order": "desc"},
            headers=self.headers,
        )
        resp.raise_for_status()
        out = []
        for item in resp.json().get("items", []):
            if item.get("private") or (item.get("archived") and item.get("stargazers_count", 0) < 10):
                continue
            lic = item.get("license") or {}
            spdx = lic.get("spdx_id")
            out.append(
                RawResult(
                    source=self.name,
                    kind=kind,
                    external_id=item["full_name"],
                    url=item["html_url"],
                    title=item["full_name"],
                    description=item.get("description"),
                    # GitHub reports repo size in KB.
                    size_bytes=(item.get("size") or 0) * 1024 or None,
                    format=item.get("language"),
                    license=None if spdx in (None, "NOASSERTION") else spdx,
                    updated_at=parse_dt(item.get("pushed_at") or item.get("updated_at")),
                    likes=item.get("stargazers_count"),
                    downloads=item.get("forks_count"),
                    tags=item.get("topics") or [],
                    meta={"default_branch": item.get("default_branch"), "archived": item.get("archived")},
                )
            )
        return out

    async def download(self, external_id: str, kind: str, dest: Path, max_bytes: int) -> int:
        slug = external_id.replace("/", "__")
        return await stream_to_file(
            self.client,
            f"{API}/repos/{external_id}/zipball",
            dest / f"{slug}.zip",
            max_bytes,
            headers=self.headers,
        )
