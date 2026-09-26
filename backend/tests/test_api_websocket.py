import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from app.main import payments_websocket
from app.schemas.dashboard import DashboardSnapshot
from app.schemas.websocket import EventsSocketMessage, SnapshotSocketMessage
from app.websocket.manager import WebSocketManager


def snapshot() -> dict:
    return {
        "state_version": 2,
        "kpis": {
            "refusal_rate": 5.0,
            "slow_transactions": 1,
            "slow_transactions_available": True,
            "fraud_timeouts": 0,
            "fraud_timeout_available": True,
            "non_completed_transactions": 0,
            "global_risk_score": 15,
            "total_amount": 100.0,
            "amount_unit": "TND",
            "total_transactions": 2,
            "success_rate": 95.0,
            "active_terminals": 1,
            "total_transfers_amount": 0.0,
            "transfers_count": 0,
            "affiliations_remaining": None,
            "average_processing_time_ms": 320.0,
            "avg_processing_time_available": True,
        },
        "incidents_by_hour": [],
        "fraud_trend_7_days": [],
        "response_time_distribution": [],
        "status_distribution": [],
        "active_alerts": [],
        "live_events": [],
        "top_anomalies": [],
        "top_tpe": [],
        "top_merchants": [],
        "replay_status": {
            "mode": "sqlserver_replay",
            "label": "SQL Server replay mode from historical data",
            "running": True,
            "paused": False,
            "processed_transactions": 2,
            "batches_processed": 1,
            "batch_size": 500,
            "interval_seconds": 5.0,
            "current_speed_tx_per_sec": 100.0,
            "current_batch_size": 500,
            "current_interval_ms": 5000,
            "fast_forward_enabled": False,
            "source_available": True,
        },
    }


class FakeEngine:
    def __init__(self) -> None:
        self.refresh_calls = 0
        self.paused = False
        self.last_period: str | None = None
        self.fast_forward_enabled = False

    async def synchronized_snapshot(self, period: str | None = None) -> dict:
        self.last_period = period
        value = snapshot()
        value["replay_status"]["paused"] = self.paused
        value["replay_status"]["fast_forward_enabled"] = self.fast_forward_enabled
        return value

    async def refresh_snapshot_cache(self) -> None:
        self.refresh_calls += 1

    async def metrics(self) -> dict:
        return {"supervisor_running": True, "source": "sqlserver_replay"}

    async def start(self) -> None:
        self.paused = False

    async def pause(self) -> None:
        self.paused = True

    async def resume(self) -> None:
        self.paused = False

    async def reset(self) -> None:
        self.paused = False

    async def set_fast_forward(self, enabled: bool) -> dict:
        self.fast_forward_enabled = enabled
        value = await self.synchronized_snapshot()
        return value["replay_status"]

    @staticmethod
    def snapshot_payload(value: dict) -> dict:
        validated = DashboardSnapshot.model_validate(value)
        return SnapshotSocketMessage(state_version=validated.state_version, emitted_at="2026-06-15T08:30:00+00:00", snapshot=validated).model_dump(mode="json")


class FakeRepository:
    async def diagnostics(self) -> dict:
        return {"backend": "sqlserver_replay", "available": True}


class ApiAndWebSocketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = FastAPI()
        self.app.include_router(router)
        self.app.add_api_websocket_route("/ws/payments", payments_websocket)
        self.app.state.settings = SimpleNamespace(app_name="Test App", websocket_auth_token=None)
        self.app.state.cache = SimpleNamespace(name="memory")
        self.app.state.engine = FakeEngine()
        self.app.state.repository = FakeRepository()
        self.app.state.websocket_manager = WebSocketManager()
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)

    def test_supervision_api_and_controls(self) -> None:
        summary = self.client.get("/api/supervision/snapshot")
        status = self.client.get("/api/supervision/status")
        health = self.client.get("/api/health")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["kpis"]["global_risk_score"], 15)
        self.assertEqual(summary.json()["kpis"]["amount_unit"], "TND")
        self.assertNotIn("amount_unit_confirmed", summary.json()["kpis"])
        self.assertEqual(status.status_code, 200)
        self.assertEqual(health.status_code, 200)
        filtered = self.client.get("/api/supervision/snapshot?period=7d")
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(self.app.state.engine.last_period, "7d")

        self.assertTrue(self.client.post("/api/replay/pause").json()["paused"])
        self.assertFalse(self.client.post("/api/replay/resume").json()["paused"])
        self.assertEqual(self.client.post("/api/replay/reset").status_code, 200)
        self.assertEqual(self.client.post("/api/replay/start").status_code, 200)
        fast = self.client.post("/api/replay/fast-forward?enabled=true")
        self.assertEqual(fast.status_code, 200)
        self.assertTrue(fast.json()["fast_forward_enabled"])

    def test_websocket_snapshot_ping_and_update_contracts(self) -> None:
        with self.client.websocket_connect("/ws/payments") as websocket:
            initial = websocket.receive_json()
            SnapshotSocketMessage.model_validate(initial)
            self.assertEqual(initial["state_version"], 2)

            event_payload = EventsSocketMessage(
                state_version=3,
                emitted_at="2026-06-15T08:30:00+00:00",
                events=[{"type": "supervision_update", "snapshot": DashboardSnapshot.model_validate({**snapshot(), "state_version": 3})}],
            ).model_dump(mode="json")
            self.client.portal.call(self.app.state.websocket_manager.broadcast, event_payload)
            EventsSocketMessage.model_validate(websocket.receive_json())

            websocket.send_json({"type": "ping"})
            self.assertEqual(websocket.receive_json()["type"], "pong")
