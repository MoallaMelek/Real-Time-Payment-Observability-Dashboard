"""Small event bus abstraction used by the replay demonstration.

The in-memory implementation preserves event order.  A broker-backed adapter
can later implement the same interface without changing producers or consumers.
"""

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
from typing import Any, Literal, Protocol

from app.models.events import TransactionEvent

logger = logging.getLogger(__name__)

EventTopic = Literal["transaction", "alert"]
EventHandler = Callable[[Any], Awaitable[None]]
EventBatchHandler = Callable[[list[Any]], Awaitable[None]]


@dataclass(slots=True)
class EventBusMetrics:
    published: int = 0
    delivered: int = 0
    handler_errors: int = 0


class EventBus(Protocol):
    """Transport contract shared by the replay and a future broker adapter."""

    def subscribe(self, topic: EventTopic, handler: EventHandler) -> Callable[[], None]: ...

    def subscribe_batch(self, topic: EventTopic, handler: EventBatchHandler) -> Callable[[], None]: ...

    async def publish_transaction_event(self, event: TransactionEvent) -> None: ...

    async def publish_transaction_batch(self, events: list[TransactionEvent]) -> None: ...

    async def publish_alert_event(self, event: dict[str, Any]) -> None: ...


class InMemoryEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[EventTopic, list[EventHandler]] = defaultdict(list)
        self._batch_subscribers: dict[EventTopic, list[EventBatchHandler]] = defaultdict(list)
        self._metrics: dict[EventTopic, EventBusMetrics] = defaultdict(EventBusMetrics)
        self._locks: dict[EventTopic, asyncio.Lock] = defaultdict(asyncio.Lock)

    def subscribe(self, topic: EventTopic, handler: EventHandler) -> Callable[[], None]:
        self._subscribers[topic].append(handler)

        def unsubscribe() -> None:
            if handler in self._subscribers[topic]:
                self._subscribers[topic].remove(handler)

        return unsubscribe

    def subscribe_batch(self, topic: EventTopic, handler: EventBatchHandler) -> Callable[[], None]:
        self._batch_subscribers[topic].append(handler)

        def unsubscribe() -> None:
            if handler in self._batch_subscribers[topic]:
                self._batch_subscribers[topic].remove(handler)

        return unsubscribe

    async def publish_transaction_event(self, event: TransactionEvent) -> None:
        await self._publish("transaction", event)

    async def publish_transaction_batch(self, events: list[TransactionEvent]) -> None:
        await self._publish_batch("transaction", events)

    async def publish_alert_event(self, event: dict[str, Any]) -> None:
        await self._publish("alert", event)

    async def _publish(self, topic: EventTopic, event: Any) -> None:
        async with self._locks[topic]:
            subscribers = tuple(self._subscribers[topic])
            self._metrics[topic].published += 1
            for handler in subscribers:
                try:
                    await handler(event)
                    self._metrics[topic].delivered += 1
                except Exception:
                    self._metrics[topic].handler_errors += 1
                    logger.exception(
                        "event_bus_handler_failed topic=%s handler=%s",
                        topic,
                        getattr(handler, "__qualname__", type(handler).__name__),
                    )

    async def _publish_batch(self, topic: EventTopic, events: list[Any]) -> None:
        if not events:
            return
        async with self._locks[topic]:
            batch_subscribers = tuple(self._batch_subscribers[topic])
            subscribers = tuple(self._subscribers[topic])
            self._metrics[topic].published += len(events)
            if batch_subscribers:
                for handler in batch_subscribers:
                    try:
                        await handler(events)
                        self._metrics[topic].delivered += len(events)
                    except Exception:
                        self._metrics[topic].handler_errors += 1
                        logger.exception(
                            "event_bus_batch_handler_failed topic=%s handler=%s",
                            topic,
                            getattr(handler, "__qualname__", type(handler).__name__),
                        )
                return

            for event in events:
                for handler in subscribers:
                    try:
                        await handler(event)
                        self._metrics[topic].delivered += 1
                    except Exception:
                        self._metrics[topic].handler_errors += 1
                        logger.exception(
                            "event_bus_handler_failed topic=%s handler=%s",
                            topic,
                            getattr(handler, "__qualname__", type(handler).__name__),
                        )

    def metrics(self) -> dict[str, dict[str, int]]:
        return {
            topic: {
                "published": metrics.published,
                "delivered": metrics.delivered,
                "handler_errors": metrics.handler_errors,
                "subscribers": len(self._subscribers[topic]),
                "batch_subscribers": len(self._batch_subscribers[topic]),
            }
            for topic, metrics in self._metrics.items()
        }
