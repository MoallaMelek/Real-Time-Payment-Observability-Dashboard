"""Produces event-stream records from the bounded SQL Server replay source."""

from typing import Protocol

from app.models.events import TransactionEvent
from app.services.event_bus import EventBus
from app.services.sqlserver_replay_provider import SqlServerReplayProvider


class TransactionProducer(Protocol):
    """Producer boundary to keep future live sources out of the dashboard."""

    async def read_next_batch(self, batch_size: int) -> list[TransactionEvent]: ...

    async def publish_events(self, events: list[TransactionEvent]) -> None: ...


class ReplayProducer:
    """Translate SQL replay rows into events without knowing KPI or UI logic."""

    def __init__(self, provider: SqlServerReplayProvider, event_bus: EventBus) -> None:
        self._provider = provider
        self._event_bus = event_bus

    async def read_next_batch(self, batch_size: int) -> list[TransactionEvent]:
        transactions = await self._provider.fetch_next_batch(batch_size=batch_size)
        return [TransactionEvent.from_transaction(transaction) for transaction in transactions]

    async def publish_events(self, events: list[TransactionEvent]) -> None:
        publish_batch = getattr(self._event_bus, "publish_transaction_batch", None)
        if callable(publish_batch):
            await publish_batch(events)
            return
        for event in events:
            await self._event_bus.publish_transaction_event(event)

    async def produce_next_batch(self, batch_size: int) -> list[TransactionEvent]:
        events = await self.read_next_batch(batch_size)
        await self.publish_events(events)
        return events
