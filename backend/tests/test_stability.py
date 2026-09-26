import unittest
from datetime import date

from app.analytics.aggregator import AnalyticsAggregator
from app.models.transaction import PaymentTransaction
from app.services.kpi_cache import InMemoryKpiCache
from app.services.transaction_engine import TransactionEngine
from app.services.sqlserver_replay_provider import PeriodWindow
from tests.helpers import RecordingWebSocketManager, make_transaction


class FakeReplayProvider:
    available = True
    last_error = None
    cursor_label = "2026-06-15 08:30:00 / TX-1"
    amount_metadata = {
        "amount_unit": "TND",
    }

    def __init__(self, batches: list[list[PaymentTransaction]]) -> None:
        self.batches = batches
        self.connect_calls = 0
        self.batch_requests: list[int | None] = []
        self.period_window = PeriodWindow("today", date(2026, 6, 15), date(2026, 6, 15), date(2026, 6, 15))

    async def connect(self) -> None:
        self.connect_calls += 1

    async def close(self) -> None:
        return None

    async def reset(self) -> None:
        return None

    async def configure_period(self, period: str) -> PeriodWindow:
        self.period_window = PeriodWindow(period, date(2026, 6, 15), date(2026, 6, 15), date(2026, 6, 15))
        return self.period_window

    async def transfer_summary(self) -> tuple[float, int]:
        return 342.37, 2

    async def get_affiliation_summary(self, period: str | None = None) -> dict:
        return {
            "affiliations_total": 1000,
            "affiliations_libres": 550,
            "affiliations_reservees": 250,
            "affiliations_affectees": 200,
            "affiliation_demo_data": True,
        }

    async def fetch_next_batch(self, batch_size: int | None = None) -> list[PaymentTransaction]:
        self.batch_requests.append(batch_size)
        return self.batches.pop(0) if self.batches else []


class ReplayEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_consumes_a_bounded_batch_and_broadcasts_snapshot_update(self) -> None:
        manager = RecordingWebSocketManager()
        provider = FakeReplayProvider([[make_transaction("TX-1"), make_transaction("TX-2", status="declined", source_status="Transaction non autorisée")]])
        cache = InMemoryKpiCache()
        engine = TransactionEngine(
            AnalyticsAggregator(),
            manager,
            cache,
            replay_provider=provider,  # type: ignore[arg-type]
            interval_seconds=5,
            max_batch_size=500,
        )

        await engine.seed()
        await engine._run_iteration()
        snapshot = await engine.synchronized_snapshot()

        self.assertEqual(provider.connect_calls, 1)
        self.assertEqual(snapshot["kpis"]["total_transactions"], 2)
        self.assertEqual(snapshot["kpis"]["total_transfers_amount"], 342.37)
        self.assertLessEqual(snapshot["kpis"]["affiliations_remaining"], 550)
        self.assertEqual(
            snapshot["kpis"]["affiliations_total"],
            snapshot["kpis"]["affiliations_remaining"]
            + snapshot["kpis"]["affiliations_reservees"]
            + snapshot["kpis"]["affiliations_affectees"],
        )
        self.assertTrue(snapshot["kpis"]["affiliation_demo_data"])
        self.assertEqual(snapshot["replay_status"]["processed_transactions"], 2)
        self.assertEqual(manager.payloads[-1]["events"][0]["type"], "supervision_update")
        cached_snapshot = await cache.load_snapshot()
        self.assertEqual(cached_snapshot["state_version"], 2)
        websocket_snapshot = manager.payloads[-1]["events"][0]["snapshot"]
        self.assertEqual(websocket_snapshot["state_version"], cached_snapshot["state_version"])
        self.assertEqual(websocket_snapshot["kpis"]["total_transactions"], cached_snapshot["kpis"]["total_transactions"])

    async def test_fast_forward_changes_cadence_without_resetting_cursor_or_projection(self) -> None:
        provider = FakeReplayProvider([
            [make_transaction("TX-1")],
            [make_transaction("TX-2")],
            [make_transaction("TX-3")],
        ])
        manager = RecordingWebSocketManager()
        engine = TransactionEngine(
            AnalyticsAggregator(),
            manager,
            InMemoryKpiCache(),
            replay_provider=provider,  # type: ignore[arg-type]
            interval_seconds=5,
            max_batch_size=500,
            fast_forward_interval_seconds=0.1,
            fast_forward_batch_size=500,
        )

        await engine.seed()
        fast_status = await engine.set_fast_forward(True)
        await engine._run_iteration()
        normal_status = await engine.set_fast_forward(False)
        await engine._run_iteration()
        snapshot = await engine.synchronized_snapshot()

        self.assertTrue(fast_status["fast_forward_enabled"])
        self.assertEqual(fast_status["current_batch_size"], 500)
        self.assertEqual(fast_status["current_interval_ms"], 100)
        self.assertEqual(fast_status["current_speed_tx_per_sec"], 5000)
        self.assertFalse(normal_status["fast_forward_enabled"])
        self.assertEqual(provider.batch_requests, [500, 5000, 500])
        self.assertEqual(snapshot["kpis"]["total_transactions"], 3)
        self.assertEqual(snapshot["replay_status"]["processed_transactions"], 3)
        self.assertEqual(snapshot["replay_status"]["cursor"], provider.cursor_label)
        self.assertTrue(any(payload["type"] == "snapshot" for payload in manager.payloads))

    async def test_fast_forward_is_preserved_when_changing_period(self) -> None:
        provider = FakeReplayProvider([[make_transaction("TX-TODAY")], [make_transaction("TX-7D")]])
        engine = TransactionEngine(
            AnalyticsAggregator(),
            RecordingWebSocketManager(),
            InMemoryKpiCache(),
            replay_provider=provider,  # type: ignore[arg-type]
            fast_forward_batch_size=500,
        )

        await engine.seed()
        await engine.set_fast_forward(True)
        snapshot = await engine.synchronized_snapshot("7d")

        self.assertEqual(provider.period_window.period, "7d")
        self.assertEqual(provider.batch_requests, [500, 5000])
        self.assertTrue(snapshot["replay_status"]["fast_forward_enabled"])
        self.assertEqual(snapshot["replay_status"]["processed_transactions"], 1)

    async def test_fast_forward_prefetch_is_emitted_in_visible_sub_batches(self) -> None:
        provider = FakeReplayProvider([
            [make_transaction("TX-SEED")],
            [make_transaction(f"TX-FAST-{index}") for index in range(1, 6)],
        ])
        engine = TransactionEngine(
            AnalyticsAggregator(),
            RecordingWebSocketManager(),
            InMemoryKpiCache(),
            replay_provider=provider,  # type: ignore[arg-type]
            max_batch_size=1,
            fast_forward_batch_size=2,
            fast_forward_fetch_batch_size=5,
        )

        await engine.seed()
        await engine.set_fast_forward(True)
        await engine._run_iteration()
        after_first_step = await engine.synchronized_snapshot()
        await engine._run_iteration()
        after_second_step = await engine.synchronized_snapshot()
        await engine._run_iteration()
        after_third_step = await engine.synchronized_snapshot()

        self.assertEqual(provider.batch_requests, [1, 5])
        self.assertEqual(after_first_step["replay_status"]["processed_transactions"], 3)
        self.assertEqual(after_second_step["replay_status"]["processed_transactions"], 5)
        self.assertEqual(after_third_step["replay_status"]["processed_transactions"], 6)

    async def test_replay_reports_completion_after_the_last_fast_forward_batch(self) -> None:
        provider = FakeReplayProvider([[make_transaction("TX-LAST")], []])
        engine = TransactionEngine(
            AnalyticsAggregator(),
            RecordingWebSocketManager(),
            InMemoryKpiCache(),
            replay_provider=provider,  # type: ignore[arg-type]
        )

        await engine.seed()
        await engine.set_fast_forward(True)
        await engine._run_iteration()
        snapshot = await engine.synchronized_snapshot()

        self.assertTrue(snapshot["replay_status"]["fast_forward_enabled"])
        self.assertEqual(snapshot["replay_status"]["processed_transactions"], 1)
        self.assertEqual(snapshot["replay_status"]["detail"], "Fin des données historiques atteinte.")

    async def test_reset_clears_replay_projection_without_touching_the_source(self) -> None:
        manager = RecordingWebSocketManager()
        provider = FakeReplayProvider([[make_transaction("TX-1")]])
        engine = TransactionEngine(AnalyticsAggregator(), manager, InMemoryKpiCache(), replay_provider=provider)  # type: ignore[arg-type]
        await engine.seed()
        await engine._run_iteration()
        await engine.reset()
        snapshot = await engine.synchronized_snapshot()

        self.assertEqual(snapshot["state_version"], 0)
        self.assertEqual(snapshot["kpis"]["total_transactions"], 0)
        self.assertEqual(snapshot["replay_status"]["processed_transactions"], 0)
