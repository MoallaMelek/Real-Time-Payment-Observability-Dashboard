# Redis local setup

Redis is the shared snapshot cache for the dashboard. The local default is:

```env
REDIS_URL=redis://localhost:6379/0
```

If `REDIS_URL` is empty, the backend starts with the memory cache and reports:

```text
cache_backend=memory
redis_available=false
redis_reason=not_configured
```

If `REDIS_URL` is configured but Redis does not respond, the backend keeps
running with memory fallback and reports:

```text
cache_backend=memory
redis_available=false
redis_reason=connection_failed
```

## Start Redis

```powershell
.\scripts\start-redis.ps1
```

Equivalent Docker Compose commands:

```powershell
docker compose up -d redis
docker compose ps
docker compose logs redis
docker exec payment-observability-dashboard-redis redis-cli ping
```

Expected result:

```text
PONG
```

## Start the local stack

```powershell
.\scripts\run-local-stack.ps1
```

Startup order:

1. Redis
2. Backend
3. Frontend

## Runtime status

The backend exposes Redis status in `/api/health` and in
`snapshot.replay_status`:

- `redis_configured`
- `redis_available`
- `cache_backend`
- `redis_reason`
- `redis_latency_ms`

The React header displays those backend values directly; it does not infer Redis
state in the browser.

## Recovery note

If Redis becomes unavailable after startup, snapshot writes keep a memory fallback
copy and the status becomes `connection_failed`. If Redis was not reachable at
startup, the current cache wiring uses the memory backend; start Redis and
restart the backend to switch the active cache back to Redis.
