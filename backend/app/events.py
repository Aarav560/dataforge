"""Project event bus for the live UI.

Jobs call publish() from async code or worker threads. With REDIS_URL set,
events go through Redis pub/sub so an arq worker process can reach API
processes; otherwise they fan out in-process.
"""

import asyncio
import json
import logging
import threading
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator

from .config import get_settings

log = logging.getLogger(__name__)

CHANNEL = "dataforge:project:{}"
# Recent events kept per project so a new subscriber sees context.
BACKLOG = 200


class _LocalBus:
    def __init__(self):
        self._subs: dict[int, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = defaultdict(set)
        self._backlog: dict[int, deque] = defaultdict(lambda: deque(maxlen=BACKLOG))
        self._lock = threading.Lock()

    def publish(self, project_id: int, event: dict) -> None:
        with self._lock:
            self._backlog[project_id].append(event)
            subs = list(self._subs.get(project_id, ()))
        for loop, queue in subs:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                pass  # subscriber's loop is closed

    async def subscribe(self, project_id: int) -> AsyncIterator[dict]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        entry = (asyncio.get_running_loop(), queue)
        with self._lock:
            backlog = list(self._backlog.get(project_id, ()))
            self._subs[project_id].add(entry)
        try:
            for event in backlog:
                yield {**event, "replay": True}
            while True:
                yield await queue.get()
        finally:
            with self._lock:
                self._subs[project_id].discard(entry)


class _RedisBus:
    def __init__(self, url: str):
        self.url = url
        self._sync = None

    def publish(self, project_id: int, event: dict) -> None:
        import redis

        if self._sync is None:
            self._sync = redis.Redis.from_url(self.url)
        payload = json.dumps(event, default=str)
        key = CHANNEL.format(project_id)
        try:
            pipe = self._sync.pipeline()
            pipe.publish(key, payload)
            pipe.lpush(key + ":backlog", payload)
            pipe.ltrim(key + ":backlog", 0, BACKLOG - 1)
            pipe.expire(key + ":backlog", 86400)
            pipe.execute()
        except redis.RedisError as exc:
            log.warning("event publish failed: %s", exc)

    async def subscribe(self, project_id: int) -> AsyncIterator[dict]:
        import redis.asyncio as aredis

        client = aredis.Redis.from_url(self.url)
        pubsub = client.pubsub()
        key = CHANNEL.format(project_id)
        await pubsub.subscribe(key)
        try:
            for raw in reversed(await client.lrange(key + ":backlog", 0, BACKLOG - 1)):
                yield {**json.loads(raw), "replay": True}
            async for msg in pubsub.listen():
                if msg.get("type") == "message":
                    yield json.loads(msg["data"])
        finally:
            await pubsub.unsubscribe(key)
            await pubsub.aclose()
            await client.aclose()


_bus = None


def bus():
    global _bus
    if _bus is None:
        url = get_settings().redis_url
        _bus = _RedisBus(url) if url else _LocalBus()
    return _bus


def publish(project_id: int | None, type_: str, **data) -> None:
    if project_id is None:
        return
    bus().publish(project_id, {"type": type_, "ts": time.time(), **data})


def reset() -> None:
    """Drop the bus (tests switch settings between runs)."""
    global _bus
    _bus = None
