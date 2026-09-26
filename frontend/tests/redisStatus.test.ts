import assert from "node:assert/strict";

import { redisStatusClass, redisStatusKey } from "../src/lib/redisStatus.ts";

assert.equal(redisStatusKey({ redis_available: true, redis_reason: "available", cache_backend: "redis" }), "redisOnlineDetail");
assert.equal(redisStatusClass({ redis_available: true, redis_reason: "available" }), "is-online");

assert.equal(redisStatusKey({ redis_available: false, redis_reason: "not_configured", cache_backend: "memory" }), "redisNotConfiguredDetail");
assert.equal(redisStatusClass({ redis_available: false, redis_reason: "not_configured" }), "is-warn");

assert.equal(redisStatusKey({ redis_available: false, redis_reason: "connection_failed", cache_backend: "memory" }), "redisConnectionFailedDetail");
assert.equal(redisStatusClass({ redis_available: false, redis_reason: "connection_failed" }), "is-offline");

assert.equal(redisStatusKey({ redis_available: false, cache_backend: "memory" }), "redisMemoryDetail");

console.log("redis status tests passed");
