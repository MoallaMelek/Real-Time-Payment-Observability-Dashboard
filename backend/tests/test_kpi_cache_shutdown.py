import unittest
from app.services.kpi_cache import InMemoryKpiCache

class KpiCacheShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_releases_snapshot_and_is_repeatable(self):
        cache = InMemoryKpiCache()
        await cache.store_snapshot({'sample': 1})
        await cache.close()
        await cache.close()
        self.assertIsNone(await cache.load_snapshot())
