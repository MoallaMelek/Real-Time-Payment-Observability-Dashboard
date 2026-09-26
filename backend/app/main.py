from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.analytics.aggregator import AnalyticsAggregator
from app.api.routes import router as api_router
from app.cache.factory import create_cache_setup
from app.chatbot.knowledge_base import KnowledgeBase
from app.chatbot.memory import ConversationMemory
from app.config import get_settings
from app.repositories.transaction_repository import TransactionRepository
from app.schemas.websocket import ErrorSocketMessage, PongSocketMessage
from app.security.rate_limit import SimpleRateLimitMiddleware
from app.services.sqlserver_replay_provider import SqlServerReplayProvider
from app.services.kpi_cache import InMemoryKpiCache, RedisKpiCache
from app.services.transaction_engine import TransactionEngine
from app.websocket.manager import WebSocketManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    cache_setup = await create_cache_setup(settings.redis_url)
    cache_backend = cache_setup.backend
    redis_status = cache_setup.redis_status
    kpi_cache = RedisKpiCache(cache_backend, redis_status) if cache_backend.name == "redis" else InMemoryKpiCache()
    if cache_backend.name != "redis":
        await cache_backend.close()
    repository = None
    replay_provider = None
    if settings.uses_sqlserver_replay:
        replay_provider = SqlServerReplayProvider(settings)
    else:
        repository = TransactionRepository(settings.database_url)
        await repository.connect()
    aggregator = AnalyticsAggregator(fraud_timeout_threshold_ms=settings.fraud_timeout_threshold_ms)
    websocket_manager = WebSocketManager()
    engine = TransactionEngine(
        aggregator=aggregator,
        websocket_manager=websocket_manager,
        kpi_cache=kpi_cache,
        redis_status=redis_status,
        repository=repository,
        replay_provider=replay_provider,
        interval_seconds=settings.replay_interval_seconds if replay_provider else settings.mock_tx_interval_seconds,
        max_batch_size=settings.replay_batch_size if replay_provider else settings.mock_tx_max_batch_size,
        fast_forward_interval_seconds=settings.fast_forward_interval_seconds,
        fast_forward_batch_size=settings.fast_forward_batch_size,
        fast_forward_fetch_batch_size=settings.fast_forward_fetch_batch_size,
    )

    app.state.settings = settings
    app.state.cache = kpi_cache
    app.state.redis_status = redis_status
    app.state.repository = repository
    app.state.replay_provider = replay_provider
    app.state.aggregator = aggregator
    app.state.websocket_manager = websocket_manager
    app.state.engine = engine
    app.state.chat_memory = ConversationMemory()
    app.state.chat_knowledge_base = KnowledgeBase()

    try:
        await engine.seed()
    except Exception as exc:
        logger.exception("initial_replay_seed_failed_api_degraded")
        await engine.mark_source_unavailable(exc)
    await engine.start()

    yield

    await engine.stop()
    await kpi_cache.close()
    if repository:
        await repository.close()


app = FastAPI(title="Payment Observability Dashboard API", version="0.2.0", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    SimpleRateLimitMiddleware,
    enabled=settings.rate_limit_enabled,
    requests_per_minute=settings.rate_limit_requests_per_minute,
)
app.include_router(api_router)


@app.websocket("/ws/payments")
async def payments_websocket(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    token = websocket.query_params.get("token")
    if settings.websocket_auth_token and token != settings.websocket_auth_token:
        await websocket.close(code=1008)
        return

    manager: WebSocketManager = websocket.app.state.websocket_manager
    engine: TransactionEngine = websocket.app.state.engine
    await manager.connect(websocket)
    try:
        snapshot = await engine.synchronized_snapshot()
        await websocket.send_json(engine.snapshot_payload(snapshot))
        while True:
            raw_message = await websocket.receive_text()
            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError:
                message = {"type": raw_message}

            if message.get("type") == "ping":
                await websocket.send_json(
                    PongSocketMessage(emitted_at=datetime.now(timezone.utc)).model_dump(mode="json")
                )
            else:
                await websocket.send_json(
                    ErrorSocketMessage(
                        emitted_at=datetime.now(timezone.utc),
                        detail="Unsupported websocket message type",
                    ).model_dump(mode="json")
                )
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("websocket_connection_failed")
    finally:
        await manager.disconnect(websocket)
        await engine.refresh_snapshot_cache()
