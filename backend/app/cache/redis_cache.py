import json
import logging
from time import perf_counter
from typing import Any

from redis.asyncio import Redis

from app.cache.runtime_status import RedisRuntimeStatus

logger = logging.getLogger(__name__)


class RedisCache:
    name = "redis"

    def __init__(self, redis: Redis, status: RedisRuntimeStatus | None = None) -> None:
        self._redis = redis
        self.status = status

    async def get(self, key: str) -> Any | None:
        started = perf_counter()
        try:
            value = await self._redis.get(key)
            if self.status:
                self.status.mark_available(round((perf_counter() - started) * 1000, 2))
        except Exception:
            if self.status:
                self.status.mark_connection_failed()
            raise
        if value is None:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            logger.warning("redis_cache_invalid_json key=%s", key)
            return None

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        payload = json.dumps(value, default=str)
        started = perf_counter()
        try:
            if ttl_seconds:
                await self._redis.set(key, payload, ex=ttl_seconds)
            else:
                await self._redis.set(key, payload)
            if self.status:
                self.status.mark_available(round((perf_counter() - started) * 1000, 2))
        except Exception:
            if self.status:
                self.status.mark_connection_failed()
            raise

    async def delete(self, key: str) -> None:
        started = perf_counter()
        try:
            await self._redis.delete(key)
            if self.status:
                self.status.mark_available(round((perf_counter() - started) * 1000, 2))
        except Exception:
            if self.status:
                self.status.mark_connection_failed()
            raise

    async def ping(self) -> bool:
        started = perf_counter()
        await self._redis.ping()
        if self.status:
            self.status.mark_available(round((perf_counter() - started) * 1000, 2))
        return True

    async def close(self) -> None:
        await self._redis.aclose()
