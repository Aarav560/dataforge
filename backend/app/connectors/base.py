from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx


@dataclass
class RawResult:
    """A search hit normalized across sources, before ranking."""

    source: str
    kind: str
    external_id: str
    url: str
    title: str
    description: str | None = None
    size_bytes: int | None = None
    format: str | None = None
    license: str | None = None
    updated_at: datetime | None = None
    # Popularity signals used by quality scoring.
    downloads: int | None = None
    likes: int | None = None
    usability: float | None = None
    tags: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


class Connector(ABC):
    name: str
    # Result kinds this connector can serve, e.g. {"dataset", "model"}.
    kinds: frozenset[str]

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    @abstractmethod
    async def search(self, query: str, kind: str, limit: int) -> list[RawResult]: ...

    @abstractmethod
    async def download(self, external_id: str, kind: str, dest: Path, max_bytes: int) -> int:
        """Download into `dest` (a directory). Returns bytes written."""


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class DownloadTooLarge(Exception):
    pass


async def stream_to_file(
    client: httpx.AsyncClient,
    url: str,
    path: Path,
    budget: int,
    **kwargs,
) -> int:
    """Stream `url` to `path`, raising DownloadTooLarge past `budget` bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    async with client.stream("GET", url, follow_redirects=True, **kwargs) as resp:
        resp.raise_for_status()
        declared = resp.headers.get("content-length")
        if declared and int(declared) > budget:
            raise DownloadTooLarge(f"{url} is {declared} bytes, budget {budget}")
        with path.open("wb") as fh:
            async for chunk in resp.aiter_bytes():
                written += len(chunk)
                if written > budget:
                    fh.close()
                    path.unlink(missing_ok=True)
                    raise DownloadTooLarge(f"{url} exceeded budget of {budget} bytes")
                fh.write(chunk)
    return written


def safe_relpath(name: str) -> Path:
    """Reject absolute paths and `..` segments in remote file names."""
    rel = Path(name)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"unsafe path from remote: {name!r}")
    return rel
