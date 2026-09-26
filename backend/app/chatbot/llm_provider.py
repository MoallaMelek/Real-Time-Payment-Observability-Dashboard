from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Any, Protocol

from app.chatbot.governance import openai_rate_limiter
from app.config import Settings

logger = logging.getLogger(__name__)


class LlmProvider(Protocol):
    name: str

    @property
    def enabled(self) -> bool: ...

    async def complete_json(self, system: str, user: str) -> dict[str, Any] | None: ...

    async def synthesize(self, system: str, user: str) -> str | None: ...


class FallbackRuleBasedProvider:
    name = "fallback"

    @property
    def enabled(self) -> bool:
        return False

    async def complete_json(self, system: str, user: str) -> dict[str, Any] | None:
        return None

    async def synthesize(self, system: str, user: str) -> str | None:
        return None


class LocalLLMProvider:
    name = "local"

    def __init__(self, settings: Settings) -> None:
        self._enabled = bool(getattr(settings, "chatbot_llm_enabled", True))
        self._base_url = str(getattr(settings, "local_llm_base_url", "http://localhost:11434")).rstrip("/")
        self._model = str(getattr(settings, "local_llm_model", "qwen2.5:7b-instruct"))
        self._timeout = int(getattr(settings, "local_llm_timeout_seconds", 20) or 20)

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def complete_json(self, system: str, user: str) -> dict[str, Any] | None:
        content = await self._complete(system, user, temperature=0.1)
        return _extract_json(content)

    async def synthesize(self, system: str, user: str) -> str | None:
        return await self._complete(system, user, temperature=0.2)

    async def _complete(self, system: str, user: str, temperature: float) -> str | None:
        if not self._enabled:
            return None
        return await asyncio.to_thread(self._complete_sync, system, user, temperature)

    def _complete_sync(self, system: str, user: str, temperature: float) -> str | None:
        payload = {
            "model": self._model,
            "stream": False,
            "options": {"temperature": temperature},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        request = urllib.request.Request(
            f"{self._base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            return body.get("message", {}).get("content")
        except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
            logger.warning("local_llm_unavailable_falling_back_to_rules provider=ollama model=%s error=%s", self._model, type(exc).__name__)
            return None


class OpenAIProvider:
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        self._api_key = getattr(settings, "openai_api_key", None)
        provider = str(getattr(settings, "chatbot_llm_provider", "local")).lower()
        self._enabled = bool(getattr(settings, "chatbot_llm_enabled", True) and provider == "openai" and self._api_key)
        self._model = getattr(settings, "openai_model", "gpt-4.1-mini")
        self._base_url = getattr(settings, "openai_base_url", "https://api.openai.com/v1").rstrip("/")
        self._requests_per_minute = int(getattr(settings, "chatbot_openai_requests_per_minute", 20) or 20)

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def complete_json(self, system: str, user: str) -> dict[str, Any] | None:
        content = await self._complete(system, user, temperature=0.1)
        return _extract_json(content)

    async def synthesize(self, system: str, user: str) -> str | None:
        return await self._complete(system, user, temperature=0.25)

    async def _complete(self, system: str, user: str, temperature: float) -> str | None:
        if not self._enabled:
            return None
        return await asyncio.to_thread(self._complete_sync, system, user, temperature)

    def _complete_sync(self, system: str, user: str, temperature: float) -> str | None:
        if not self._api_key:
            return None
        if not openai_rate_limiter.allow("openai_chatbot", self._requests_per_minute):
            logger.warning("chatbot_openai_rate_limited limit_per_minute=%s", self._requests_per_minute)
            return None
        payload = {
            "model": self._model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = json.loads(response.read().decode("utf-8"))
            return body.get("choices", [{}])[0].get("message", {}).get("content")
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError) as exc:
            logger.warning("chatbot_openai_unavailable_falling_back_to_rules error=%s", type(exc).__name__)
            return None


class ChatbotLlmClient:
    """Provider router: local Ollama by default, OpenAI only when explicitly selected."""

    def __init__(self, settings: Settings) -> None:
        selected = str(getattr(settings, "chatbot_llm_provider", "fallback")).lower()
        if selected == "openai":
            self._provider: LlmProvider = OpenAIProvider(settings)
        elif selected == "fallback":
            self._provider = FallbackRuleBasedProvider()
        else:
            self._provider = LocalLLMProvider(settings)
        self._last_provider = self._provider.name if self._provider.enabled else "fallback"

    @property
    def enabled(self) -> bool:
        return self._provider.enabled

    @property
    def provider_name(self) -> str:
        return self._last_provider

    async def complete_json(self, system: str, user: str) -> dict[str, Any] | None:
        payload = await self._provider.complete_json(system, user)
        self._last_provider = self._provider.name if payload else "fallback"
        return payload

    async def synthesize(self, system: str, user: str) -> str | None:
        answer = await self._provider.synthesize(system, user)
        self._last_provider = self._provider.name if answer else "fallback"
        return answer


OptionalLlmClient = ChatbotLlmClient


def _extract_json(content: str | None) -> dict[str, Any] | None:
    if not content:
        return None
    try:
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            return json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        logger.warning("chatbot_llm_json_parse_failed")
    return None
