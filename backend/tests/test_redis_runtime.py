import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.cache.factory import create_cache_setup
from app.cache.redis_cache import RedisCache
from app.cache.runtime_status import RedisRuntimeStatus
from app.services.kpi_cache import RedisKpiCache


class FakeRedisClient:
    def __init__(self, fail_ping: bool = False) -> None:
        self.fail_ping = fail_ping
        self.closed = False
        self.values: dict[str, str] = {}

    async def ping(self) -> bool:
        if self.fail_ping:
            raise ConnectionError("down")
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)

    async def aclose(self) -> None:
        self.closed = True


class RedisRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_absent_url_uses_memory_with_not_configured_reason(self) -> None:
        setup = await create_cache_setup("")

        self.assertEqual(setup.backend.name, "memory")
        self.assertFalse(setup.redis_status.configured)
        self.assertFalse(setup.redis_status.available)
        self.assertEqual(setup.redis_status.reason, "not_configured")

    async def test_successful_ping_uses_redis_cache(self) -> None:
        fake = FakeRedisClient()
        with patch("app.cache.factory.Redis.from_url", return_value=fake):
            setup = await create_cache_setup("redis://localhost:6379/0")

        self.assertEqual(setup.backend.name, "redis")
        self.assertTrue(setup.redis_status.configured)
        self.assertTrue(setup.redis_status.available)
        self.assertEqual(setup.redis_status.reason, "available")

    async def test_configured_but_unreachable_falls_back_to_memory(self) -> None:
        fake = FakeRedisClient(fail_ping=True)
        with patch("app.cache.factory.Redis.from_url", return_value=fake):
            setup = await create_cache_setup("redis://localhost:6379/0")

        self.assertEqual(setup.backend.name, "memory")
        self.assertTrue(setup.redis_status.configured)
        self.assertFalse(setup.redis_status.available)
        self.assertEqual(setup.redis_status.reason, "connection_failed")
        self.assertTrue(fake.closed)

    async def test_redis_kpi_cache_keeps_memory_fallback_when_redis_write_fails(self) -> None:
        backend = SimpleNamespace(name="redis", set=AsyncMock(side_effect=ConnectionError("down")), get=AsyncMock(side_effect=ConnectionError("down")), close=AsyncMock())
        status = RedisRuntimeStatus.online(1.2)
        cache = RedisKpiCache(backend, status)  # type: ignore[arg-type]
        snapshot = {"state_version": 1, "kpis": {"total_transactions": 10}}

        await cache.store_snapshot(snapshot)  # type: ignore[arg-type]
        loaded = await cache.load_snapshot()

        self.assertEqual(loaded, snapshot)
        self.assertFalse(status.available)
        self.assertEqual(status.reason, "connection_failed")

    async def test_redis_cache_ping_updates_latency_status(self) -> None:
        status = RedisRuntimeStatus.connection_failed()
        cache = RedisCache(FakeRedisClient(), status)  # type: ignore[arg-type]

        self.assertTrue(await cache.ping())
        self.assertTrue(status.available)
        self.assertEqual(status.reason, "available")
        self.assertIsNotNone(status.latency_ms)
