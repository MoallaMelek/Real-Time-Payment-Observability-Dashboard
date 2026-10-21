import unittest
from app.services.event_bus import InMemoryEventBus
from app.analytics.aggregator import AnalyticsAggregator
from app.services.transaction_engine import TransactionEngine
from app.services.kpi_cache import InMemoryKpiCache
from app.models.events import TransactionEvent
from tests.helpers import make_transaction, RecordingWebSocketManager

class MixedSubscriberTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_subscribers_take_precedence_over_single_fallback(self):
        bus = InMemoryEventBus()
        singles, batches = [], []
        async def single(event): singles.append(event)
        async def batch(events): batches.append(list(events))
        bus.subscribe('transaction', single)
        bus.subscribe_batch('transaction', batch)
        await bus.publish_transaction_batch([1,2])
        self.assertEqual(singles,[])
        self.assertEqual(batches,[[1,2]])
        self.assertEqual(bus.metrics()['transaction']['delivered'],2)

    async def test_engine_unique_batch_does_not_inflate_duplicate_counts(self):
        aggregator=AnalyticsAggregator();bus=InMemoryEventBus()
        TransactionEngine(aggregator,RecordingWebSocketManager(),InMemoryKpiCache(),event_bus=bus)
        events=[TransactionEvent.from_transaction(make_transaction(identifier)) for identifier in ('UNIQUE-A','UNIQUE-B')]
        await bus.publish_transaction_batch(events)
        metrics=await aggregator.actual_period_metrics()
        self.assertEqual(metrics.total_transactions,2)
        self.assertEqual(metrics.duplicates_ignored,0)
        self.assertEqual(bus.metrics()['transaction']['delivered'],2)
