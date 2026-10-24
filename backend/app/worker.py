"""arq worker. Run with: arq app.worker.WorkerSettings"""

from arq.connections import RedisSettings

from . import jobs
from .config import get_settings
from .db import init_db


async def search_need(ctx, need_id: int) -> None:
    await jobs.search_need(need_id, client=ctx["http"])


async def download_asset(ctx, asset_id: int) -> None:
    await jobs.download_asset(asset_id, client=ctx["http"])


async def startup(ctx) -> None:
    init_db()
    ctx["http"] = jobs.make_http_client()


async def shutdown(ctx) -> None:
    await ctx["http"].aclose()


class WorkerSettings:
    functions = [search_need, download_asset]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url or "redis://localhost:6379")
    max_jobs = 20
    job_timeout = 60 * 60
