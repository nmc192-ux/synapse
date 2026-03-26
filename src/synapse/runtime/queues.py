from __future__ import annotations

import asyncio
import json
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from synapse.config import settings

try:
    from redis.asyncio import Redis
except Exception:  # pragma: no cover - optional import
    Redis = None  # type: ignore[assignment]


class BrowserTaskEnvelope(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action: str
    session_id: str | None = None
    agent_id: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    arguments: dict[str, Any] = Field(default_factory=dict)


class BrowserTaskResult(BaseModel):
    request_id: str
    worker_id: str
    action: str
    success: bool = True
    payload: Any = None
    error: str | None = None
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BrowserTaskQueue(ABC):
    def __init__(self, name: str) -> None:
        self.name = name

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    @abstractmethod
    async def put(self, item: BrowserTaskEnvelope) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get(self, timeout: float | None = None) -> BrowserTaskEnvelope:
        raise NotImplementedError


class InMemoryBrowserTaskQueue(BrowserTaskQueue):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._queue: asyncio.Queue[BrowserTaskEnvelope] = asyncio.Queue()

    async def put(self, item: BrowserTaskEnvelope) -> None:
        await self._queue.put(item)

    async def get(self, timeout: float | None = None) -> BrowserTaskEnvelope:
        if timeout is None:
            return await self._queue.get()
        return await asyncio.wait_for(self._queue.get(), timeout=timeout)


class RedisBrowserTaskQueue(BrowserTaskQueue):
    def __init__(self, name: str, redis_url: str) -> None:
        super().__init__(name)
        self._redis_url = redis_url
        self._redis: Redis | None = None

    async def start(self) -> None:
        if Redis is None:
            raise RuntimeError("redis package is not available.")
        if self._redis is None:
            self._redis = Redis.from_url(self._redis_url, decode_responses=True)
            await self._redis.ping()

    async def stop(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def put(self, item: BrowserTaskEnvelope) -> None:
        redis = self._require_redis()
        await redis.rpush(self.name, item.model_dump_json())

    async def get(self, timeout: float | None = None) -> BrowserTaskEnvelope:
        redis = self._require_redis()
        if timeout is None:
            payload = await redis.blpop(self.name, timeout=0)
        else:
            payload = await redis.blpop(self.name, timeout=max(1, int(timeout)))
        if payload is None:
            raise TimeoutError(f"Timed out waiting for browser task on queue {self.name}.")
        _, raw = payload
        return BrowserTaskEnvelope.model_validate_json(raw)

    def _require_redis(self) -> Redis:
        if self._redis is None:
            raise RuntimeError("Redis browser task queue is not started.")
        return self._redis


class FallbackBrowserTaskQueue(BrowserTaskQueue):
    def __init__(self, name: str, redis_url: str) -> None:
        super().__init__(name)
        self._primary = RedisBrowserTaskQueue(name, redis_url)
        self._fallback = InMemoryBrowserTaskQueue(name)
        self._active: BrowserTaskQueue | None = None

    async def start(self) -> None:
        try:
            await self._primary.start()
            self._active = self._primary
        except Exception:
            if settings.redis_required or not settings.runtime_state_fallback_memory:
                raise
            self._active = self._fallback
            await self._active.start()

    async def stop(self) -> None:
        if self._active is not None:
            await self._active.stop()
        await self._primary.stop()
        self._active = None

    async def put(self, item: BrowserTaskEnvelope) -> None:
        await self._require_active().put(item)

    async def get(self, timeout: float | None = None) -> BrowserTaskEnvelope:
        return await self._require_active().get(timeout=timeout)

    def _require_active(self) -> BrowserTaskQueue:
        if self._active is None:
            raise RuntimeError("Browser task queue is not started.")
        return self._active


def create_browser_task_queue(name: str) -> BrowserTaskQueue:
    if settings.redis_url and Redis is not None:
        return FallbackBrowserTaskQueue(name, settings.redis_url)
    return InMemoryBrowserTaskQueue(name)
