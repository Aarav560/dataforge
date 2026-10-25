"""arq worker. Run with: arq app.worker.WorkerSettings"""

from arq import cron
from arq.connections import RedisSettings

from . import jobs
from .config import get_settings
from .db import init_db


async def search_need(ctx, need_id: int) -> None:
    await jobs.search_need(need_id, client=ctx["http"])


async def download_asset(ctx, asset_id: int) -> None:
    await jobs.download_asset(asset_id, client=ctx["http"])


async def run_pipeline(ctx, version_id: int) -> None:
    await jobs.run_pipeline(version_id)


async def run_export(ctx, export_id: int) -> None:
    await jobs.run_export(export_id)


async def apply_reviews(ctx, version_id: int) -> None:
    await jobs.apply_reviews(version_id)


async def train_baseline(ctx, run_id: int) -> None:
    await jobs.train_baseline(run_id)


async def sync_index(ctx) -> None:
    await jobs.sync_index()


async def nightly_index(ctx) -> None:
    if get_settings().index_auto_sync:
        await jobs.sync_index()


async def startup(ctx) -> None:
    init_db()
    ctx["http"] = jobs.make_http_client()


async def shutdown(ctx) -> None:
    await ctx["http"].aclose()


class WorkerSettings:
    functions = [search_need, download_asset, run_pipeline, sync_index, run_export, apply_reviews, train_baseline]
    cron_jobs = [cron(nightly_index, hour=3, minute=17)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url or "redis://localhost:6379")
    max_jobs = 20
    job_timeout = 60 * 60
