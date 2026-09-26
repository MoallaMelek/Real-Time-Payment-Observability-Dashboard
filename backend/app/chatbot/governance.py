from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
from time import monotonic
from typing import Any

from app.schemas.chat import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _CacheEntry:
    expires_at: float
    payload: dict[str, Any]


class ChatbotResponseCache:
    def __init__(self) -> None:
        self._entries: dict[str, _CacheEntry] = {}

    def key_for(self, request: ChatRequest) -> str:
        raw = "|".join(
            [
                (request.session_id or request.conversation_id or "anonymous").strip().lower(),
                str(request.period or "today"),
                str(request.language or "fr"),
                " ".join(request.message.strip().lower().split()),
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> ChatResponse | None:
        entry = self._entries.get(key)
        if not entry:
            return None
        if entry.expires_at < monotonic():
            self._entries.pop(key, None)
            return None
        return ChatResponse.model_validate(entry.payload)

    def set(self, key: str, response: ChatResponse, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        self._entries[key] = _CacheEntry(
            expires_at=monotonic() + ttl_seconds,
            payload=response.model_dump(mode="json"),
        )


class MinuteRateLimiter:
    def __init__(self) -> None:
        self._events: dict[str, list[float]] = {}

    def allow(self, key: str, limit: int) -> bool:
        now = monotonic()
        window_start = now - 60
        bucket = [item for item in self._events.get(key, []) if item >= window_start]
        if len(bucket) >= limit:
            self._events[key] = bucket
            return False
        bucket.append(now)
        self._events[key] = bucket
        return True


def audit_chatbot_event(event: str, **fields: Any) -> None:
    logger.info("chatbot_audit event=%s fields=%s", event, fields)


response_cache = ChatbotResponseCache()
openai_rate_limiter = MinuteRateLimiter()
tool_rate_limiter = MinuteRateLimiter()
