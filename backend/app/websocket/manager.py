import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from time import perf_counter
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _QueuedMessage:
    payload: str
    queued_at: float


@dataclass(slots=True)
class _ClientState:
    websocket: WebSocket
    queue: asyncio.Queue[_QueuedMessage] = field(default_factory=lambda: asyncio.Queue(maxsize=1))
    writer: asyncio.Task[None] | None = None
    messages_sent: int = 0
    messages_replaced: int = 0
    send_latency_ms: float = 0.0
    queue_latency_ms: float = 0.0
    stale: bool = False


class WebSocketManager:
    def __init__(self) -> None:
        self._clients: dict[WebSocket, _ClientState] = {}
        self._lock = asyncio.Lock()
        self._messages_sent = 0
        self._messages_replaced = 0
        self._stale_connections = 0
        self._last_broadcast_at: str | None = None
        self._max_send_latency_ms = 0.0
        self._max_queue_latency_ms = 0.0

    @property
    def count(self) -> int:
        return len(self._clients)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        state = _ClientState(websocket=websocket)
        state.writer = asyncio.create_task(self._writer(state), name="dashboard-websocket-writer")
        async with self._lock:
            self._clients[websocket] = state

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            state = self._clients.pop(websocket, None)
        if state and state.writer:
            state.writer.cancel()
            try:
                await state.writer
            except asyncio.CancelledError:
                pass

    async def broadcast(self, payload: dict[str, Any]) -> None:
        serialized = json.dumps(payload, separators=(",", ":"), default=str)
        queued_at = perf_counter()
        async with self._lock:
            states = list(self._clients.values())
            for state in states:
                if state.websocket.client_state != WebSocketState.CONNECTED:
                    state.stale = True
                    continue
                if state.queue.full():
                    try:
                        state.queue.get_nowait()
                        state.queue.task_done()
                    except asyncio.QueueEmpty:
                        pass
                    state.messages_replaced += 1
                    self._messages_replaced += 1
                state.queue.put_nowait(_QueuedMessage(payload=serialized, queued_at=queued_at))
            self._last_broadcast_at = datetime.now(timezone.utc).isoformat()
            stale = [state.websocket for state in states if state.stale]
            for websocket in stale:
                self._clients.pop(websocket, None)
            self._stale_connections += len(stale)

    async def _writer(self, state: _ClientState) -> None:
        while True:
            message = await state.queue.get()
            queue_latency = (perf_counter() - message.queued_at) * 1000
            started = perf_counter()
            try:
                if state.websocket.client_state != WebSocketState.CONNECTED:
                    state.stale = True
                    return
                await asyncio.wait_for(state.websocket.send_text(message.payload), timeout=3)
                send_latency = (perf_counter() - started) * 1000
                async with self._lock:
                    state.messages_sent += 1
                    state.queue_latency_ms = round(queue_latency, 2)
                    state.send_latency_ms = round(send_latency, 2)
                    self._messages_sent += 1
                    self._max_queue_latency_ms = max(self._max_queue_latency_ms, queue_latency)
                    self._max_send_latency_ms = max(self._max_send_latency_ms, send_latency)
            except Exception as exc:
                state.stale = True
                logger.debug("websocket_send_failed error=%s", type(exc).__name__)
                return
            finally:
                state.queue.task_done()

    async def metrics(self) -> dict[str, Any]:
        async with self._lock:
            queue_depths = [state.queue.qsize() for state in self._clients.values()]
            slow_clients = sum(1 for depth in queue_depths if depth > 0)
            return {
                "connected_clients": len(self._clients),
                "messages_sent": self._messages_sent,
                "messages_replaced": self._messages_replaced,
                "stale_connections": self._stale_connections,
                "last_broadcast_at": self._last_broadcast_at,
                "queue_depth": max(queue_depths, default=0),
                "slow_client_count": slow_clients,
                "websocket_queue_latency_ms": round(self._max_queue_latency_ms, 2),
                "websocket_send_latency_ms": round(self._max_send_latency_ms, 2),
            }
