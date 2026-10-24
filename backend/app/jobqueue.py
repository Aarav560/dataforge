"""Dispatch jobs to Redis/arq when configured, otherwise run them in-process."""

import asyncio
import logging

from .config import get_settings
from . import jobs

log = logging.getLogger(__name__)

_JOBS = {"search_need": jobs.search_need, "download_asset": jobs.download_asset}
_pool = None
_background: set[asyncio.Task] = set()


async def _arq_pool():
    global _pool
    if _pool is None:
        from arq import create_pool
        from arq.connections import RedisSettings

        _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _pool


async def enqueue(name: str, *args) -> None:
    if name not in _JOBS:
        raise KeyError(name)
    if get_settings().redis_url:
        pool = await _arq_pool()
        await pool.enqueue_job(name, *args)
        return
    task = asyncio.create_task(_JOBS[name](*args))
    _background.add(task)
    task.add_done_callback(_background.discard)


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
