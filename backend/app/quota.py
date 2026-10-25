"""Track API rate-limit headers seen per host, for the stats HUD."""

import time

import httpx

_HEADERS = (
    ("x-ratelimit-remaining", "x-ratelimit-limit", "x-ratelimit-reset"),
    ("ratelimit-remaining", "ratelimit-limit", "ratelimit-reset"),
)
_seen: dict[str, dict] = {}


async def record(response: httpx.Response) -> None:
    for remaining, limit, reset in _HEADERS:
        if remaining in response.headers:
            try:
                _seen[response.url.host] = {
                    "remaining": int(response.headers[remaining]),
                    "limit": int(response.headers.get(limit, 0)) or None,
                    "reset": response.headers.get(reset),
                    "seen_at": time.time(),
                }
            except ValueError:
                pass
            return


def snapshot() -> dict[str, dict]:
    return dict(_seen)
