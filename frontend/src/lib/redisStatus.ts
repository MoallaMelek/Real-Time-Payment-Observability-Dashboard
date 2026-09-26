import type { ReplayStatus } from "../types/dashboard";

export type RedisStatusKey =
  | "redisOnlineDetail"
  | "redisNotConfiguredDetail"
  | "redisConnectionFailedDetail"
  | "redisMemoryDetail";

export function redisStatusKey(replayStatus: Pick<ReplayStatus, "redis_available" | "redis_reason" | "cache_backend">): RedisStatusKey {
  if (replayStatus.redis_available) return "redisOnlineDetail";
  if (replayStatus.redis_reason === "not_configured") return "redisNotConfiguredDetail";
  if (replayStatus.redis_reason === "connection_failed") return "redisConnectionFailedDetail";
  return "redisMemoryDetail";
}

export function redisStatusClass(replayStatus: Pick<ReplayStatus, "redis_available" | "redis_reason">): "is-online" | "is-warn" | "is-offline" {
  if (replayStatus.redis_available) return "is-online";
  return replayStatus.redis_reason === "not_configured" ? "is-warn" : "is-offline";
}
