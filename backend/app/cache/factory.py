import logging
from dataclasses import dataclass
from time import perf_counter

from redis.asyncio import Redis

from app.cache.base import CacheBackend
from app.cache.memory_cache import MemoryCache
from app.cache.redis_cache import RedisCache
from app.cache.runtime_status import RedisRuntimeStatus

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CacheSetup:
    backend: CacheBackend
    redis_status: RedisRuntimeStatus


async def create_cache_setup(redis_url: str | None) -> CacheSetup:
    if not redis_url or not redis_url.strip():
        return CacheSetup(MemoryCache(), RedisRuntimeStatus.not_configured())

    redis: Redis | None = None
    started = perf_counter()
    try:
        redis = Redis.from_url(
            redis_url.strip(),
            decode_responses=True,
            socket_connect_timeout=1.5,
            socket_timeout=1.5,
            health_check_interval=30,
        )
        await redis.ping()
        latency_ms = round((perf_counter() - started) * 1000, 2)
        status = RedisRuntimeStatus.online(latency_ms)
        return CacheSetup(RedisCache(redis, status), status)
    except Exception as exc:
        latency_ms = round((perf_counter() - started) * 1000, 2)
        if redis is not None:
            await redis.aclose()
        logger.warning("redis_unavailable_falling_back_to_memory reason=connection_failed error=%s", type(exc).__name__)
        return CacheSetup(MemoryCache(), RedisRuntimeStatus.connection_failed(latency_ms))


async def create_cache(redis_url: str | None) -> CacheBackend:
    return (await create_cache_setup(redis_url)).backend
