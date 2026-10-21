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

    async def test_configured_client_decode_failure_is_a_miss_not_an_outage(self):
        from app.cache.runtime_status import RedisRuntimeStatus
        redis=AsyncMock()
        redis.get.side_effect=UnicodeDecodeError('utf-8',b'\xff',0,1,'invalid start byte')
        status=RedisRuntimeStatus.online(1)
        self.assertIsNone(await RedisCache(redis,status).get('snapshot'))
        self.assertTrue(status.available)

    async def test_connection_errors_still_propagate_and_mark_unavailable(self):
        from app.cache.runtime_status import RedisRuntimeStatus
        redis=AsyncMock();redis.get.side_effect=ConnectionError('offline')
        status=RedisRuntimeStatus.online(1)
        with self.assertRaises(ConnectionError):await RedisCache(redis,status).get('snapshot')
        self.assertFalse(status.available)
