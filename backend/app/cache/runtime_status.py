from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Literal


RedisReason = Literal["available", "not_configured", "connection_failed"]


@dataclass(slots=True)
class RedisRuntimeStatus:
    configured: bool
    available: bool
    reason: RedisReason
    latency_ms: float | None = None
    checked_at: float = 0.0

    @classmethod
    def not_configured(cls) -> "RedisRuntimeStatus":
        return cls(configured=False, available=False, reason="not_configured", checked_at=time.time())

    @classmethod
    def connection_failed(cls, latency_ms: float | None = None) -> "RedisRuntimeStatus":
        return cls(configured=True, available=False, reason="connection_failed", latency_ms=latency_ms, checked_at=time.time())

    @classmethod
    def online(cls, latency_ms: float) -> "RedisRuntimeStatus":
        return cls(configured=True, available=True, reason="available", latency_ms=latency_ms, checked_at=time.time())

    def mark_connection_failed(self, latency_ms: float | None = None) -> None:
        self.configured = True
        self.available = False
        self.reason = "connection_failed"
        self.latency_ms = latency_ms
        self.checked_at = time.time()

    def mark_available(self, latency_ms: float) -> None:
        self.configured = True
        self.available = True
        self.reason = "available"
        self.latency_ms = latency_ms
        self.checked_at = time.time()

    def to_dict(self) -> dict[str, object]:
        return {
            "redis_configured": self.configured,
            "redis_available": self.available,
            "redis_reason": self.reason,
            "redis_latency_ms": self.latency_ms,
            "redis_checked_at": self.checked_at,
        }
