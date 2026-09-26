"""Snapshot cache abstraction, ready for an eventual Redis implementation."""

import asyncio
from typing import Protocol

from app.cache.base import CacheBackend
from app.cache.runtime_status import RedisRuntimeStatus
from app.schemas.data import DashboardSnapshotData

SNAPSHOT_KEY = "dashboard:supervision:snapshot"


class KpiCache(Protocol):
    name: str

    async def store_snapshot(self, snapshot: DashboardSnapshotData) -> None: ...

    async def load_snapshot(self) -> DashboardSnapshotData | None: ...

    async def close(self) -> None: ...


class InMemoryKpiCache:
    name = "memory"

    def __init__(self) -> None:
        self._snapshot: DashboardSnapshotData | None = None
        self._lock = asyncio.Lock()

    async def store_snapshot(self, snapshot: DashboardSnapshotData) -> None:
        async with self._lock:
            self._snapshot = snapshot

    async def load_snapshot(self) -> DashboardSnapshotData | None:
        async with self._lock:
            return self._snapshot

    async def close(self) -> None:
        return None


class RedisKpiCache:
    """Adapter over the existing Redis-ready cache backend."""

    name = "redis"

    def __init__(self, backend: CacheBackend, status: RedisRuntimeStatus | None = None) -> None:
        self._backend = backend
        self._fallback = InMemoryKpiCache()
        self.status = status

    async def store_snapshot(self, snapshot: DashboardSnapshotData) -> None:
        await self._fallback.store_snapshot(snapshot)
        try:
            await self._backend.set(SNAPSHOT_KEY, snapshot, ttl_seconds=30)
        except Exception:
            if self.status:
                self.status.mark_connection_failed()

    async def load_snapshot(self) -> DashboardSnapshotData | None:
        try:
            value = await self._backend.get(SNAPSHOT_KEY)
            return value if value is not None else await self._fallback.load_snapshot()
        except Exception:
            if self.status:
                self.status.mark_connection_failed()
            return await self._fallback.load_snapshot()

    async def ping(self) -> bool:
        ping = getattr(self._backend, "ping", None)
        if not callable(ping):
            return False
        try:
            return bool(await ping())
        except Exception:
            if self.status:
                self.status.mark_connection_failed()
            return False

    async def close(self) -> None:
        await self._backend.close()
