import asyncio
import json
import unittest

from starlette.websockets import WebSocketState

from app.websocket.manager import WebSocketManager


class SlowWebSocket:
    client_state = WebSocketState.CONNECTED

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.release = asyncio.Event()

    async def accept(self) -> None:
        return None

    async def send_text(self, payload: str) -> None:
        await self.release.wait()
        self.sent.append(json.loads(payload))


class WebSocketManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_client_gets_latest_snapshot_without_blocking_broadcast(self) -> None:
        manager = WebSocketManager()
        websocket = SlowWebSocket()
        await manager.connect(websocket)  # type: ignore[arg-type]

        await asyncio.wait_for(manager.broadcast({"state_version": 1}), timeout=0.05)
        await asyncio.wait_for(manager.broadcast({"state_version": 2}), timeout=0.05)
        metrics = await manager.metrics()
        self.assertEqual(metrics["messages_replaced"], 1)
        self.assertEqual(metrics["queue_depth"], 1)

        websocket.release.set()
        await asyncio.sleep(0.05)

        self.assertEqual([payload["state_version"] for payload in websocket.sent], [2])
        self.assertEqual((await manager.metrics())["messages_sent"], 1)
        await manager.disconnect(websocket)  # type: ignore[arg-type]
