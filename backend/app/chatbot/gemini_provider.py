from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import logging
import random
import time
from typing import Any, Protocol

from app.config import Settings

logger = logging.getLogger(__name__)


class GeminiProviderError(RuntimeError):
    retryable = False


class GeminiTimeoutError(GeminiProviderError):
    retryable = True


class GeminiRateLimitError(GeminiProviderError):
    retryable = True


class GeminiTransientError(GeminiProviderError):
    retryable = True


class GeminiInvalidResponseError(GeminiProviderError):
    retryable = False


class LLMProvider(Protocol):
    name: str

    @property
    def enabled(self) -> bool: ...

    async def generate_decision(self, system: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def compose_answer(self, system: str, payload: dict[str, Any]) -> str: ...

    async def health_check(self) -> bool: ...


@dataclass(slots=True)
class GeminiMetrics:
    requests_total: int = 0
    success_total: int = 0
    fallback_total: int = 0
    timeout_total: int = 0
    rate_limit_total: int = 0
    invalid_response_total: int = 0
    latency_ms: list[float] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    capability_selected: str | None = None
    provider_used: str = "local_fallback"
    circuit_breaker_state: str = "closed"

    def observe_latency(self, started: float) -> None:
        self.latency_ms.append(round((time.perf_counter() - started) * 1000, 2))
        if len(self.latency_ms) > 100:
            self.latency_ms.pop(0)

    def to_dict(self) -> dict[str, Any]:
        avg_latency = sum(self.latency_ms) / len(self.latency_ms) if self.latency_ms else 0.0
        return {
            "gemini_requests_total": self.requests_total,
            "gemini_success_total": self.success_total,
            "gemini_fallback_total": self.fallback_total,
            "gemini_timeout_total": self.timeout_total,
            "gemini_rate_limit_total": self.rate_limit_total,
            "gemini_invalid_response_total": self.invalid_response_total,
            "gemini_latency_ms": round(avg_latency, 2),
            "gemini_input_tokens": self.input_tokens,
            "gemini_output_tokens": self.output_tokens,
            "capability_selected": self.capability_selected,
            "provider_used": self.provider_used,
            "circuit_breaker_state": self.circuit_breaker_state,
        }


class GeminiCircuitBreaker:
    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 45.0) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if time.monotonic() - self._opened_at >= self._cooldown_seconds:
            return "half_open"
        return "open"

    def allow_request(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._failure_threshold:
            self._opened_at = time.monotonic()


class GeminiProvider:
    name = "gemini"
    _client_cache: dict[tuple[str, str], Any] = {}

    def __init__(self, settings: Settings, metrics: GeminiMetrics | None = None) -> None:
        self._api_key = str(getattr(settings, "gemini_api_key", "") or "")
        self._model = str(getattr(settings, "gemini_model", "gemini-2.5-flash") or "gemini-2.5-flash")
        self._enabled = bool(getattr(settings, "gemini_enabled", False) and self._api_key)
        self._timeout = float(getattr(settings, "gemini_timeout_seconds", 6) or 6)
        self._max_retries = int(getattr(settings, "gemini_max_retries", 1) or 1)
        self._temperature = float(getattr(settings, "gemini_temperature", 0.2) or 0.2)
        self._max_output_tokens = int(getattr(settings, "gemini_max_output_tokens", 1024) or 1024)
        self.fallback_enabled = bool(getattr(settings, "gemini_fallback_enabled", True))
        # Deprecated compatibility flag. User-facing chatbot answers must never fail closed.
        self.fail_closed = bool(getattr(settings, "gemini_fail_closed", False))
        rollout_percent = int(getattr(settings, "gemini_rollout_percent", 100) or 0)
        rollout_enabled = rollout_percent >= 100 or random.uniform(0, 100) < max(0, rollout_percent)
        self._enabled = self._enabled and rollout_enabled
        self.metrics = metrics or GeminiMetrics()
        self.circuit_breaker = GeminiCircuitBreaker()
        self._client = self._get_client() if self._enabled else None

    @property
    def enabled(self) -> bool:
        return self._enabled and self._client is not None

    def diagnostic(self) -> dict[str, Any]:
        return {
            "gemini_enabled": self._enabled,
            "gemini_configured": bool(self._api_key),
            "gemini_available": self.enabled and self.circuit_breaker.allow_request(),
            "model": self._model,
            "fallback_available": self.fallback_enabled,
        }

    async def generate_decision(self, system: str, payload: dict[str, Any]) -> dict[str, Any]:
        text = await self._generate(system, payload)
        parsed = _extract_json(text)
        if parsed is None:
            self.metrics.invalid_response_total += 1
            raise GeminiInvalidResponseError("Gemini returned invalid JSON")
        return parsed

    async def compose_answer(self, system: str, payload: dict[str, Any]) -> str:
        text = await self._generate(system, payload)
        if not text or not text.strip():
            self.metrics.invalid_response_total += 1
            raise GeminiInvalidResponseError("Gemini returned an empty answer")
        return text.strip()

    async def health_check(self) -> bool:
        if not self.enabled or not self.circuit_breaker.allow_request():
            return False
        try:
            await self._generate("Return ok.", {"health_check": True})
            return True
        except GeminiProviderError:
            return False

    async def _generate(self, system: str, payload: dict[str, Any]) -> str:
        if not self.enabled:
            raise GeminiProviderError("Gemini disabled")
        if not self.circuit_breaker.allow_request():
            raise GeminiTransientError("Gemini circuit breaker open")

        last_error: GeminiProviderError | None = None
        attempts = max(1, self._max_retries + 1)
        for attempt in range(attempts):
            started = time.perf_counter()
            self.metrics.requests_total += 1
            self.metrics.circuit_breaker_state = self.circuit_breaker.state
            try:
                text, usage = await asyncio.wait_for(
                    asyncio.to_thread(self._generate_sync, system, payload),
                    timeout=self._timeout,
                )
                self.metrics.observe_latency(started)
                self.metrics.input_tokens += int(usage.get("input_tokens") or 0)
                self.metrics.output_tokens += int(usage.get("output_tokens") or 0)
                self.metrics.success_total += 1
                self.metrics.provider_used = "gemini"
                self.circuit_breaker.record_success()
                return text
            except asyncio.TimeoutError as exc:
                self.metrics.timeout_total += 1
                last_error = GeminiTimeoutError("Gemini timeout")
                self.circuit_breaker.record_failure()
            except GeminiRateLimitError as exc:
                self.metrics.rate_limit_total += 1
                last_error = exc
                self.circuit_breaker.record_failure()
            except GeminiProviderError as exc:
                last_error = exc
                self.circuit_breaker.record_failure()
            if not last_error.retryable or attempt >= attempts - 1:
                break
            await asyncio.sleep((0.15 * (2 ** attempt)) + random.uniform(0, 0.08))

        self.metrics.fallback_total += 1
        self.metrics.provider_used = "local_fallback"
        self.metrics.circuit_breaker_state = self.circuit_breaker.state
        raise last_error or GeminiProviderError("Gemini unavailable")

    def _generate_sync(self, system: str, payload: dict[str, Any]) -> tuple[str, dict[str, int]]:
        if self._client is None:
            raise GeminiProviderError("Gemini client missing")
        contents = [
            {"role": "user", "parts": [{"text": json.dumps(payload, ensure_ascii=False)}]},
        ]
        config = {
            "system_instruction": system,
            "temperature": self._temperature,
            "max_output_tokens": self._max_output_tokens,
            "automatic_function_calling": {"disable": True},
        }
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            status = str(getattr(exc, "code", "") or getattr(exc, "status_code", "") or exc)
            if "429" in status:
                raise GeminiRateLimitError("Gemini rate limit") from exc
            if any(code in status for code in ("500", "503", "UNAVAILABLE", "DEADLINE")):
                raise GeminiTransientError("Gemini transient error") from exc
            raise GeminiProviderError(f"Gemini request failed: {type(exc).__name__}") from exc

        text = str(getattr(response, "text", "") or "").strip()
        usage = getattr(response, "usage_metadata", None)
        return text, {
            "input_tokens": int(getattr(usage, "prompt_token_count", 0) or 0),
            "output_tokens": int(getattr(usage, "candidates_token_count", 0) or 0),
        }

    def _get_client(self) -> Any | None:
        cache_key = (self._api_key[-8:], self._model)
        if cache_key in self._client_cache:
            return self._client_cache[cache_key]
        try:
            from google import genai
        except Exception:
            logger.warning("google_genai_sdk_unavailable_gemini_disabled")
            return None
        client = genai.Client(api_key=self._api_key)
        self._client_cache[cache_key] = client
        return client


def _extract_json(content: str | None) -> dict[str, Any] | None:
    if not content:
        return None
    try:
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            payload = json.loads(content[start : end + 1])
            return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        logger.warning("gemini_json_parse_failed")
    return None
