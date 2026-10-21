import unittest
from app.services.event_bus import InMemoryEventBus

class MixedSubscriberTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_and_individual_subscribers_both_receive_events(self):
        bus = InMemoryEventBus()
        singles, batches = [], []
        async def single(event): singles.append(event)
        async def batch(events): batches.append(list(events))
        bus.subscribe('transaction', single)
        bus.subscribe_batch('transaction', batch)
        await bus.publish_transaction_batch([1,2])
        self.assertEqual(singles,[1,2])
        self.assertEqual(batches,[[1,2]])
        self.assertEqual(bus.metrics()['transaction']['delivered'],4)
