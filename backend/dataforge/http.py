"""Shared async HTTP client with per-host token buckets, backoff and robots.txt checks."""
from __future__ import annotations

import asyncio
import time
import urllib.robotparser
from urllib.parse import urlparse

import httpx

from .config import HTTP_TIMEOUT, USER_AGENT

_client: httpx.AsyncClient | None = None

# Requests/second per host (conservative defaults for free, unauthenticated tiers).
HOST_RATES = {
    "api.github.com": 0.16,  # anonymous search API: 10/min
    "export.arxiv.org": 0.34,  # arXiv asks for one request every 3s
    "api.semanticscholar.org": 0.5,
    "zenodo.org": 1.0,
    "www.openml.org": 2.0,
    "huggingface.co": 20.0,
    "api.openverse.org": 1.0,
    "commons.wikimedia.org": 2.0,
    "archive.org": 1.0,
    "www.kaggle.com": 1.0,
}


class TokenBucket:
    def __init__(self, rate: float, burst: int = 3):
        self.rate, self.capacity = rate, burst
        self.tokens = float(burst)
        self.updated = time.monotonic()
        self.lock = asyncio.Lock()

    async def take(self) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


_buckets: dict[str, TokenBucket] = {}
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True,
                                    headers={"User-Agent": USER_AGENT},
                                    limits=httpx.Limits(max_connections=64, max_keepalive_connections=32))
    return _client


async def _throttle(url: str) -> None:
    host = urlparse(url).netloc
    b = _buckets.get(host)
    if b is None:
        b = _buckets[host] = TokenBucket(HOST_RATES.get(host, 4.0), burst=5 if host == "api.github.com" else 3)
    await b.take()


async def get(url: str, *, retries: int = 2, **kw) -> httpx.Response:
    last: Exception | None = None
    for attempt in range(retries + 1):
        await _throttle(url)
        try:
            r = await client().get(url, **kw)
            if r.status_code in (429, 502, 503, 504) and attempt < retries:
                wait = float(r.headers.get("Retry-After", 2 ** (attempt + 1))) if r.headers.get("Retry-After", "").isdigit() else 2 ** (attempt + 1)
                await asyncio.sleep(min(wait, 10))
                continue
            return r
        except httpx.TransportError as e:
            last = e
            await asyncio.sleep(2 ** attempt)
    raise last or RuntimeError(f"GET failed: {url}")


async def get_json(url: str, **kw):
    r = await get(url, **kw)
    r.raise_for_status()
    return r.json()


async def robots_allowed(url: str) -> bool:
    """Honor robots.txt for general web crawling (plan §9)."""
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = await client().get(f"{base}/robots.txt", timeout=5)
            if r.status_code >= 400:
                _robots[base] = None  # no robots.txt → allowed
            else:
                rp.parse(r.text.splitlines())
                _robots[base] = rp
        except httpx.HTTPError:
            _robots[base] = None
    rp = _robots[base]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)
