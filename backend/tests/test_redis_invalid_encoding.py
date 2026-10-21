import unittest
from unittest.mock import AsyncMock
from app.cache.redis_cache import RedisCache

class RedisEncodingTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_utf8_is_a_cache_miss(self):
        redis = AsyncMock()
        redis.get.return_value = b'\xff'
        self.assertIsNone(await RedisCache(redis).get('snapshot'))

    async def test_valid_utf8_json_survives(self):
        redis = AsyncMock()
        redis.get.return_value = b'{"total": 0}'
        self.assertEqual(await RedisCache(redis).get('snapshot'), {'total': 0})
