import unittest
from unittest.mock import patch
from app.cache.memory_cache import MemoryCache

class CacheExpiryTests(unittest.IsolatedAsyncioTestCase):
    async def test_zero_and_negative_ttl_expire(self):
        cache = MemoryCache()
        for ttl in (0, -1):
            await cache.set('key', 'value', ttl)
            self.assertIsNone(await cache.get('key'))

    async def test_positive_ttl_expires_at_boundary_and_none_persists(self):
        cache = MemoryCache()
        with patch('app.cache.memory_cache.time.monotonic', return_value=100):
            await cache.set('timed', 0, 2)
            await cache.set('persistent', False)
        with patch('app.cache.memory_cache.time.monotonic', return_value=102):
            self.assertIsNone(await cache.get('timed'))
            self.assertIs(await cache.get('persistent'), False)
