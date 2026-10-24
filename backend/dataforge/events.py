"""In-process pub/sub + background job runner (local stand-in for Redis + arq)."""
from __future__ import annotations

import asyncio
import time
import traceback
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable

from . import db


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._history: dict[str, deque] = defaultdict(lambda: deque(maxlen=400))

    def subscribe(self, channel: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._subs[channel].add(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue) -> None:
        self._subs[channel].discard(q)

    def history(self, channel: str) -> list[dict]:
        return list(self._history[channel])

    def publish(self, channel: str, type: str, **data: Any) -> None:
        evt = {"type": type, "ts": time.time(), **data}
        self._history[channel].append(evt)
        for q in list(self._subs[channel]):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                pass


bus = EventBus()

_tasks: set[asyncio.Task] = set()
STATS = {"workers_active": 0}


class Job:
    def __init__(self, project_id: str, kind: str):
        row = db.insert("jobs", project_id=project_id, kind=kind, status="queued", progress=0.0,
                        message="", updated_at=time.time())
        self.id = row["id"]
        self.project_id = project_id
        self.kind = kind

    def progress(self, frac: float, message: str = "") -> None:
        db.update("jobs", self.id, progress=round(frac, 4), message=message, status="running",
                  updated_at=time.time())
        bus.publish(self.project_id, "job", job_id=self.id, kind=self.kind, status="running",
                    progress=frac, message=message)

    def log(self, message: str, level: str = "info", source: str | None = None) -> None:
        bus.publish(self.project_id, "log", job_id=self.id, level=level, message=message, source=source)


def spawn(project_id: str, kind: str, fn: Callable[[Job], Awaitable[Any]]) -> str:
    job = Job(project_id, kind)

    async def runner() -> None:
        STATS["workers_active"] += 1
        bus.publish(project_id, "job", job_id=job.id, kind=kind, status="running", progress=0.0, message="started")
        try:
            result = await fn(job)
            db.update("jobs", job.id, status="done", progress=1.0, result_json=result if isinstance(result, (dict, list)) else {"result": result},
                      updated_at=time.time())
            bus.publish(project_id, "job", job_id=job.id, kind=kind, status="done", progress=1.0, result=result)
        except Exception as e:  # noqa: BLE001 — surface any failure to the UI
            traceback.print_exc()
            db.update("jobs", job.id, status="error", message=str(e)[:500], updated_at=time.time())
            bus.publish(project_id, "job", job_id=job.id, kind=kind, status="error", message=str(e)[:500])
        finally:
            STATS["workers_active"] -= 1

    task = asyncio.create_task(runner())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job.id
