"""Per-conversation locks and per-key rate limits, in Redis or (for one process) in memory."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis

from agent_gateway.domain.errors import GatewayError

_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


class LockTimeout(GatewayError):
    status = 409
    type = "invalid_request_error"
    code = "conversation_busy"


class RedisLock:
    def __init__(self, redis: Redis, *, ttl_ms: int = 300_000, wait_seconds: float = 60.0) -> None:
        self._r, self._ttl, self._wait = redis, ttl_ms, wait_seconds

    @asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[None]:
        name, token = f"lock:{key}", secrets.token_hex(8)
        deadline = time.monotonic() + self._wait
        while not await self._r.set(name, token, nx=True, px=self._ttl):
            if time.monotonic() > deadline:
                raise LockTimeout("another request on this conversation is still running")
            await asyncio.sleep(0.05)
        try:
            yield
        finally:
            await self._r.eval(_RELEASE, 1, name, token)


class RedisRateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._r = redis

    async def allow(self, key_id: str, limit_per_minute: int) -> bool:
        name = f"rl:{key_id}:{int(time.time() // 60)}"
        count = await self._r.incr(name)
        if count == 1:
            await self._r.expire(name, 120)
        return count <= limit_per_minute


class MemoryLock:
    """Single-process lock, for development and tests."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[None]:
        async with self._locks[key]:
            yield


class MemoryRateLimiter:
    def __init__(self) -> None:
        self._counts: dict[tuple[str, int], int] = defaultdict(int)

    async def allow(self, key_id: str, limit_per_minute: int) -> bool:
        slot = (key_id, int(time.time() // 60))
        self._counts[slot] += 1
        return self._counts[slot] <= limit_per_minute


class RedisTextCache:
    """Short-lived text cache (compacted client prompts). A hit renews the entry's life."""

    def __init__(self, redis: Redis) -> None:
        self._r = redis

    async def _touch(self, group: str, key: str, ttl: int, keep: int | None) -> None:
        name = f"grp:{group}"
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.lrem(name, 0, key)
            pipe.lpush(name, key)
            pipe.expire(name, ttl)
            if keep is not None:
                pipe.lrange(name, keep, -1)
                pipe.ltrim(name, 0, keep - 1)
            results = await pipe.execute()
        if keep is not None and results[3]:
            await self._r.delete(*results[3])           # the older entries a new prompt displaces

    async def get(self, key: str, refresh_seconds: int, group: str | None = None) -> str | None:
        value = await self._r.getex(key, ex=refresh_seconds)
        if value is not None and group:
            await self._touch(group, key, refresh_seconds, None)
        return value.decode() if value is not None else None

    async def put(self, key: str, value: str, ttl_seconds: int, group: str | None = None, keep: int = 1) -> None:
        await self._r.set(key, value, ex=ttl_seconds)
        if group:
            await self._touch(group, key, ttl_seconds, max(keep, 1))
