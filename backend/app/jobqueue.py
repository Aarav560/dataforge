"""Dispatch jobs to Redis/arq when configured, otherwise run them in-process."""

import asyncio
import logging

from .config import get_settings
from . import jobs

log = logging.getLogger(__name__)

_JOBS = {
    "search_need": jobs.search_need,
    "download_asset": jobs.download_asset,
    "run_pipeline": jobs.run_pipeline,
    "sync_index": jobs.sync_index,
    "run_export": jobs.run_export,
    "apply_reviews": jobs.apply_reviews,
    "train_baseline": jobs.train_baseline,
}
_pool = None
_background: set[asyncio.Task] = set()


class QueueUnavailable(Exception):
    """Redis is configured but unreachable."""


async def _arq_pool():
    global _pool
    if _pool is None:
        from arq import create_pool
        from arq.connections import RedisSettings

        settings = RedisSettings.from_dsn(get_settings().redis_url)
        # Fail fast: a request shouldn't hang while Redis is down.
        settings.conn_retries = 1
        settings.conn_timeout = 2
        _pool = await create_pool(settings)
    return _pool


async def enqueue(name: str, *args) -> None:
    if name not in _JOBS:
        raise KeyError(name)
    if get_settings().redis_url:
        try:
            pool = await asyncio.wait_for(_arq_pool(), timeout=8)
            await asyncio.wait_for(pool.enqueue_job(name, *args), timeout=5)
        except (OSError, asyncio.TimeoutError, ConnectionError) as exc:
            raise QueueUnavailable(f"job queue unavailable: {exc or 'timed out'}") from exc
        except Exception as exc:
            if type(exc).__module__.startswith("redis"):
                raise QueueUnavailable(f"job queue unavailable: {exc}") from exc
            raise
        return
    task = asyncio.create_task(_JOBS[name](*args))
    _background.add(task)
    task.add_done_callback(_background.discard)


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
