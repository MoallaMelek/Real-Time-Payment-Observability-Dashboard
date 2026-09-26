import unittest
from datetime import datetime, timezone

from app.analytics.aggregator import AnalyticsAggregator
from app.models.events import TransactionEvent
from app.cache.redis_cache import RedisCache
from app.services.event_bus import InMemoryEventBus
from app.services.kpi_cache import InMemoryKpiCache
from app.services.replay_producer import ReplayProducer
from app.services.transaction_engine import TransactionEngine
from tests.helpers import make_transaction
from tests.helpers import RecordingWebSocketManager


class FakeProvider:
    def __init__(self) -> None:
        self.requests: list[int] = []

    async def fetch_next_batch(self, batch_size: int) -> list:
        self.requests.append(batch_size)
        return [make_transaction("TX-EVENT", amount=12.5)]


class ReplayStatusProvider:
    available = True
    last_error = None
    cursor_label = "historical cursor"
    period_window = None


class ReplacementProducer:
    """A stand-in for a future live producer using the same event contract."""

    def __init__(self, event_bus: InMemoryEventBus) -> None:
        self._event_bus = event_bus
        self.requested_batch_sizes: list[int] = []

    async def read_next_batch(self, batch_size: int) -> list[TransactionEvent]:
        self.requested_batch_sizes.append(batch_size)
        return [
            TransactionEvent.from_transaction(
                make_transaction("TX-REPLACEMENT", amount=99.0, timestamp=datetime.now(timezone.utc))
            )
        ]

    async def publish_events(self, events: list[TransactionEvent]) -> None:
        for event in events:
            await self._event_bus.publish_transaction_event(event)


class CountingKpiCache(InMemoryKpiCache):
    def __init__(self) -> None:
        super().__init__()
        self.store_calls = 0
        self.load_calls = 0

    async def store_snapshot(self, snapshot: dict) -> None:
        self.store_calls += 1
        await super().store_snapshot(snapshot)

    async def load_snapshot(self) -> dict | None:
        self.load_calls += 1
        return await super().load_snapshot()


class FakeRedis:
    def __init__(self, value: str | None) -> None:
        self.value = value

    async def get(self, key: str) -> str | None:
        return self.value

    async def set(self, key: str, payload: str, ex: int | None = None) -> None:
        self.value = payload

    async def delete(self, key: str) -> None:
        self.value = None

    async def aclose(self) -> None:
        return None


class EventPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_producer_publishes_one_transaction_event_per_transaction(self) -> None:
        bus = InMemoryEventBus()
        provider = FakeProvider()
        producer = ReplayProducer(provider, bus)  # type: ignore[arg-type]
        received: list[TransactionEvent] = []

        async def capture(event: TransactionEvent) -> None:
            received.append(event)

        bus.subscribe("transaction", capture)
        events = await producer.produce_next_batch(500)

        self.assertEqual(provider.requests, [500])
        self.assertEqual(len(events), 1)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].transaction_id, "TX-EVENT")
        self.assertEqual(received[0].amount_tnd, 12.5)
        self.assertEqual(received[0].source, "sqlserver_replay")
        self.assertTrue(received[0].replay)

    async def test_event_bus_keeps_publishing_after_handler_failure(self) -> None:
        bus = InMemoryEventBus()
        delivered: list[str] = []

        async def failing_handler(_event: TransactionEvent) -> None:
            raise RuntimeError("handler failed")

        async def healthy_handler(event: TransactionEvent) -> None:
            delivered.append(event.transaction_id)

        bus.subscribe("transaction", failing_handler)
        bus.subscribe("transaction", healthy_handler)

        await bus.publish_transaction_event(TransactionEvent.from_transaction(make_transaction("TX-RESILIENT")))

        metrics = bus.metrics()["transaction"]
        self.assertEqual(delivered, ["TX-RESILIENT"])
        self.assertEqual(metrics["published"], 1)
        self.assertEqual(metrics["delivered"], 1)
        self.assertEqual(metrics["handler_errors"], 1)

    async def test_aggregator_consumes_transaction_events_incrementally(self) -> None:
        bus = InMemoryEventBus()
        aggregator = AnalyticsAggregator()
        bus.subscribe("transaction", aggregator.consume_transaction_event)
        event = TransactionEvent.from_transaction(make_transaction("TX-AGG", amount=42.5))

        await bus.publish_transaction_event(event)
        consumed = await aggregator.drain_consumed_event_batch()
        snapshot = await aggregator.snapshot()

        self.assertEqual([transaction.transaction_id for transaction in consumed], ["TX-AGG"])
        self.assertEqual(snapshot["kpis"]["total_transactions"], 1)
        self.assertEqual(snapshot["kpis"]["total_amount"], 42.5)

    async def test_event_bus_publishes_transaction_batch_in_order(self) -> None:
        bus = InMemoryEventBus()
        received: list[str] = []

        async def capture(events: list[TransactionEvent]) -> None:
            received.extend(event.transaction_id for event in events)

        bus.subscribe_batch("transaction", capture)
        events = [TransactionEvent.from_transaction(make_transaction(f"TX-BATCH-{index}")) for index in range(500)]

        await bus.publish_transaction_batch(events)

        self.assertEqual(received, [f"TX-BATCH-{index}" for index in range(500)])
        metrics = bus.metrics()["transaction"]
        self.assertEqual(metrics["published"], 500)
        self.assertEqual(metrics["delivered"], 500)
        self.assertEqual(metrics["handler_errors"], 0)

    async def test_aggregator_batch_deduplicates_in_one_pass(self) -> None:
        aggregator = AnalyticsAggregator()
        events = [
            TransactionEvent.from_transaction(make_transaction("TX-BATCH-DEDUP", amount=10)),
            TransactionEvent.from_transaction(make_transaction("TX-BATCH-DEDUP", amount=99)),
            TransactionEvent.from_transaction(make_transaction("TX-BATCH-UNIQUE", amount=5)),
        ]

        with self.assertLogs("app.analytics.aggregator", "WARNING"):
            await aggregator.consume_transaction_batch(events)
        snapshot = await aggregator.snapshot()
        metrics = await aggregator.actual_period_metrics()

        self.assertEqual(snapshot["kpis"]["total_transactions"], 2)
        self.assertEqual(snapshot["kpis"]["total_amount"], 15)
        self.assertEqual(metrics.duplicates_ignored, 1)

    async def test_unit_and_batch_paths_produce_equivalent_kpis(self) -> None:
        transactions = [
            make_transaction("TX-EQ-1", amount=10),
            make_transaction("TX-EQ-2", amount=20, source_status="Transaction non autorisée", status="declined"),
            make_transaction("TX-EQ-3", amount=30, source_status="Transaction non aboutie", status="pending"),
        ]
        unit = AnalyticsAggregator()
        batch = AnalyticsAggregator()
        for transaction in transactions:
            await unit.consume_transaction_event(TransactionEvent.from_transaction(transaction))
        await batch.consume_transaction_batch([TransactionEvent.from_transaction(transaction) for transaction in transactions])

        self.assertEqual((await unit.snapshot())["kpis"], (await batch.snapshot())["kpis"])

    async def test_kpi_cache_stores_and_restores_the_snapshot(self) -> None:
        cache = InMemoryKpiCache()
        snapshot = {"state_version": 7, "kpis": {"total_transactions": 42}}

        await cache.store_snapshot(snapshot)

        self.assertEqual(await cache.load_snapshot(), snapshot)

    async def test_redis_cache_invalid_json_is_a_cache_miss(self) -> None:
        cache = RedisCache(FakeRedis("{broken json"))  # type: ignore[arg-type]

        self.assertIsNone(await cache.get("dashboard:supervision:snapshot"))

    async def test_websocket_broadcast_matches_cache_without_redis_reread(self) -> None:
        manager = RecordingWebSocketManager()
        cache = CountingKpiCache()
        engine = TransactionEngine(AnalyticsAggregator(), manager, cache)

        await engine.pause()

        cached = await cache.load_snapshot()
        payload_snapshot = manager.payloads[-1]["events"][0]["snapshot"]
        self.assertEqual(payload_snapshot["state_version"], cached["state_version"])
        self.assertEqual(payload_snapshot["replay_status"]["replay_run_id"], cached["replay_status"]["replay_run_id"])
        self.assertEqual(cache.store_calls, 1)
        self.assertEqual(cache.load_calls, 1)

    async def test_dashboard_pipeline_accepts_a_replacement_producer(self) -> None:
        bus = InMemoryEventBus()
        producer = ReplacementProducer(bus)
        cache = InMemoryKpiCache()
        engine = TransactionEngine(
            AnalyticsAggregator(),
            RecordingWebSocketManager(),
            cache,
            replay_provider=ReplayStatusProvider(),  # type: ignore[arg-type]
            event_bus=bus,
            replay_producer=producer,
            max_batch_size=10,
        )

        await engine._run_iteration()
        snapshot = await engine.synchronized_snapshot()

        self.assertEqual(producer.requested_batch_sizes, [10])
        self.assertEqual(snapshot["kpis"]["total_transactions"], 1)
        self.assertEqual(snapshot["kpis"]["total_amount"], 99.0)

    async def test_cached_snapshot_and_websocket_payload_share_data_quality_report(self) -> None:
        manager = RecordingWebSocketManager()
        cache = InMemoryKpiCache()
        engine = TransactionEngine(AnalyticsAggregator(), manager, cache)

        await engine.pause()
        cached = await cache.load_snapshot()
        payload_report = manager.payloads[-1]["events"][0]["snapshot"]["data_quality"]

        self.assertEqual(cached["data_quality"]["status"], payload_report["status"])
        self.assertEqual(cached["data_quality"]["processed_transactions"], payload_report["processed_transactions"])
