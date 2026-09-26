from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request

from app.chatbot.memory import ConversationMemory
from app.chatbot.service import ChatbotService
from app.chatbot.gemini_provider import GeminiProvider
from app.chatbot.sql_tools import ChatbotSqlTools
from app.schemas.chat import ChatRequest, ChatResponse, ChatSuggestion
from app.schemas.dashboard import DashboardSnapshot, ReplayStatus
from app.services.reconciliation_report import ReconciliationReportService

router = APIRouter(prefix="/api")


@router.get("/health")
async def health(request: Request) -> dict:
    cache_ping = getattr(request.app.state.cache, "ping", None)
    if callable(cache_ping):
        await cache_ping()
    websocket_metrics = await request.app.state.websocket_manager.metrics()
    engine_metrics = await request.app.state.engine.metrics()
    repository = request.app.state.repository
    persistence = await repository.diagnostics() if repository else await request.app.state.engine._provider.diagnostics()
    redis_status = getattr(request.app.state, "redis_status", None)
    redis_payload = redis_status.to_dict() if redis_status else {
        "redis_configured": request.app.state.cache.name == "redis",
        "redis_available": request.app.state.cache.name == "redis",
        "redis_reason": "available" if request.app.state.cache.name == "redis" else "not_configured",
        "redis_latency_ms": None,
    }
    return {
        "status": "ok",
        "app": request.app.state.settings.app_name,
        "cache_backend": "redis" if redis_payload.get("redis_available") else "memory",
        **redis_payload,
        "websocket_clients": request.app.state.websocket_manager.count,
        "websocket": websocket_metrics,
        "engine": engine_metrics,
        "persistence": persistence,
    }


@router.get("/supervision/snapshot", response_model=DashboardSnapshot)
@router.get("/analytics/summary", response_model=DashboardSnapshot, include_in_schema=False)
async def supervision_snapshot(
    request: Request,
    period: str | None = Query(default=None, pattern="^(today|yesterday|7d|30d|quarter|year)$"),
) -> dict:
    try:
        return await request.app.state.engine.synchronized_snapshot(period=period)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/supervision/status", response_model=ReplayStatus)
async def supervision_status(request: Request) -> dict:
    return (await request.app.state.engine.synchronized_snapshot())["replay_status"]


@router.get("/supervision/reconciliation-report")
async def supervision_reconciliation_report(
    request: Request,
    dates: list[date] = Query(..., min_length=1, max_length=14),
) -> dict:
    if not request.app.state.settings.uses_sqlserver_replay:
        raise HTTPException(status_code=422, detail="Reconciliation reports require SQL Server replay mode.")
    return await ReconciliationReportService(request.app.state.settings).reconcile_dates(dates)


@router.post("/replay/start", response_model=ReplayStatus)
async def replay_start(request: Request) -> dict:
    await request.app.state.engine.start()
    return (await request.app.state.engine.synchronized_snapshot())["replay_status"]


@router.post("/replay/pause", response_model=ReplayStatus)
async def replay_pause(request: Request) -> dict:
    await request.app.state.engine.pause()
    return (await request.app.state.engine.synchronized_snapshot())["replay_status"]


@router.post("/replay/resume", response_model=ReplayStatus)
async def replay_resume(request: Request) -> dict:
    await request.app.state.engine.resume()
    return (await request.app.state.engine.synchronized_snapshot())["replay_status"]


@router.post("/replay/reset", response_model=ReplayStatus)
async def replay_reset(request: Request) -> dict:
    await request.app.state.engine.reset()
    return (await request.app.state.engine.synchronized_snapshot())["replay_status"]


@router.post("/replay/fast-forward", response_model=ReplayStatus)
async def replay_fast_forward(request: Request, enabled: bool = Query(...)) -> dict:
    return await request.app.state.engine.set_fast_forward(enabled)


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, payload: ChatRequest) -> ChatResponse:
    tools = ChatbotSqlTools(
        engine=request.app.state.engine,
        provider=getattr(request.app.state, "replay_provider", None) or getattr(request.app.state.engine, "_provider", None),
    )
    if not getattr(request.app.state, "chat_memory", None):
        request.app.state.chat_memory = ConversationMemory()
    if not getattr(request.app.state, "gemini_provider", None):
        request.app.state.gemini_provider = GeminiProvider(request.app.state.settings)
    return await ChatbotService(
        tools,
        knowledge_base=getattr(request.app.state, "chat_knowledge_base", None),
        memory=getattr(request.app.state, "chat_memory", None),
        settings=request.app.state.settings,
        gemini_provider=getattr(request.app.state, "gemini_provider", None),
    ).answer(payload)


@router.get("/chat/suggestions", response_model=list[ChatSuggestion])
async def chat_suggestions(language: str = Query(default="fr", pattern="^(fr|en)$")) -> list[ChatSuggestion]:
    return [ChatSuggestion(label=item) for item in ChatbotService.suggestions(language)]
