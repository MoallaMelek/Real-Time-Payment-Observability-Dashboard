import unittest
from datetime import datetime, timedelta, timezone

from app.analytics.aggregator import AnalyticsAggregator
from app.analytics.risk_alert_engine import RiskAlertEngine
from app.schemas.dashboard import DashboardSnapshot
from tests.helpers import make_transaction


class AnalyticsAggregatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_supervision_snapshot_calculates_operational_kpis(self) -> None:
        aggregator = AnalyticsAggregator()
        transactions = [
            make_transaction("TX-1", amount=100, processing_time_ms=250),
            make_transaction("TX-2", amount=50, status="declined", source_status="Transaction non autorisée", response_code="051"),
            make_transaction("TX-3", amount=25, status="pending", source_status="Transaction non aboutie", processing_time_ms=1400),
        ]

        await aggregator.record_batch(transactions)
        await aggregator.detect_alerts(transactions)
        snapshot = DashboardSnapshot.model_validate(await aggregator.snapshot())

        self.assertEqual(snapshot.state_version, 3)
        self.assertEqual(snapshot.kpis.total_transactions, 3)
        self.assertEqual(snapshot.kpis.total_amount, 175)
        self.assertEqual(snapshot.kpis.slow_transactions, 1)
        self.assertEqual(snapshot.kpis.non_completed_transactions, 1)
        self.assertAlmostEqual(snapshot.kpis.refusal_rate, 33.33, places=2)
        self.assertGreater(snapshot.kpis.global_risk_score, 0)
        self.assertEqual(snapshot.kpis.affiliations_remaining, None)
        self.assertTrue(snapshot.live_events)

    async def test_rebuild_and_risk_score_are_deterministic(self) -> None:
        aggregator = AnalyticsAggregator()
        transactions = [
            make_transaction("TX-1", status="declined", source_status="Transaction non autorisée", response_code="091"),
            make_transaction("TX-2", processing_time_ms=1500, fraud_time_ms=6000, response_code="999"),
        ]
        await aggregator.rebuild(transactions)
        snapshot = DashboardSnapshot.model_validate(await aggregator.snapshot())

        self.assertEqual(snapshot.state_version, 2)
        self.assertEqual(snapshot.kpis.refusal_rate, 50)
        self.assertEqual(snapshot.kpis.slow_transactions, 1)
        self.assertEqual(snapshot.kpis.fraud_timeouts, 1)
        self.assertEqual(
            RiskAlertEngine.score(
                total=2,
                refused=1,
                non_completed=0,
                slow=1,
                fraud_timeouts=1,
                abnormal_responses=1,
                traffic_drop_percent=0,
            ),
            snapshot.kpis.global_risk_score,
        )

    async def test_duplicate_ids_are_never_counted_twice(self) -> None:
        aggregator = AnalyticsAggregator()
        transaction = make_transaction("TX-DEDUP", amount=100)
        with self.assertLogs("app.analytics.aggregator", "WARNING"):
            accepted = await aggregator.record_batch([transaction, transaction])
        await aggregator.detect_alerts(accepted)
        snapshot = DashboardSnapshot.model_validate(await aggregator.snapshot())

        self.assertEqual(len(accepted), 1)
        self.assertEqual(snapshot.state_version, 1)
        self.assertEqual(snapshot.kpis.total_transactions, 1)
        self.assertEqual(snapshot.kpis.total_amount, 100)

    async def test_snapshot_exposes_normalized_tnd_amounts(self) -> None:
        aggregator = AnalyticsAggregator()
        await aggregator.record_batch([make_transaction("TX-AMOUNT", amount=12.5)])
        snapshot = DashboardSnapshot.model_validate(await aggregator.snapshot())

        self.assertEqual(snapshot.kpis.total_amount, 12.5)
        self.assertEqual(snapshot.kpis.amount_unit, "TND")
        self.assertEqual(snapshot.top_merchants[0].merchant_name, "Cedar Shop")
        self.assertFalse(hasattr(snapshot.top_merchants[0], "amount"))
        self.assertNotIn("amount_unit_confirmed", snapshot.model_dump()["kpis"])

    async def test_missing_processing_and_fraud_data_are_not_reported_as_zero(self) -> None:
        aggregator = AnalyticsAggregator()
        transactions = [
            make_transaction("TX-NO-SIGNAL-1", processing_time_ms=None, fraud_time_ms=None, fraud_decision=None),
            make_transaction("TX-NO-SIGNAL-2", processing_time_ms=None, fraud_time_ms=None, fraud_decision=None),
        ]

        accepted = await aggregator.record_batch(transactions)
        await aggregator.detect_alerts(accepted)
        snapshot = DashboardSnapshot.model_validate(await aggregator.snapshot())

        self.assertFalse(snapshot.kpis.avg_processing_time_available)
        self.assertFalse(snapshot.kpis.slow_transactions_available)
        self.assertFalse(snapshot.kpis.fraud_timeout_available)
        self.assertIsNone(snapshot.kpis.average_processing_time_ms)
        self.assertIsNone(snapshot.kpis.slow_transactions)
        self.assertIsNone(snapshot.kpis.fraud_timeouts)
        self.assertEqual(snapshot.response_time_distribution, [])
        self.assertTrue(all(point.slow is None for point in snapshot.fraud_trend_7_days))
        self.assertTrue(all(point.fraud_timeouts is None for point in snapshot.fraud_trend_7_days))

    async def test_affiliation_projection_uses_observed_merchant_behaviour(self) -> None:
        async def run_projection() -> dict:
            aggregator = AnalyticsAggregator()
            await aggregator.set_affiliation_summary(
                {
                    "affiliations_total": 1000,
                    "affiliations_libres": 550,
                    "affiliations_reservees": 250,
                    "affiliations_affectees": 200,
                    "affiliation_demo_data": True,
                }
            )
            start = datetime(2026, 5, 1, 8, tzinfo=timezone.utc)
            transactions = [
                make_transaction(
                    f"TX-ACTIVE-{index}",
                    amount=220 + index,
                    merchant="Marchand actif",
                    merchant_id="1001",
                    terminal_id=f"TPE-ACTIVE-{index % 4}",
                    timestamp=start + timedelta(minutes=index),
                )
                for index in range(180)
            ]
            transactions.extend(
                make_transaction(
                    f"TX-QUIET-{index}",
                    amount=12,
                    merchant="Marchand discret",
                    merchant_id="2002",
                    terminal_id="TPE-QUIET-1",
                    timestamp=start + timedelta(minutes=200 + index * 30),
                )
                for index in range(12)
            )

            await aggregator.record_batch(transactions)
            return await aggregator.snapshot()

        first = await run_projection()
        second = await run_projection()

        self.assertEqual(first["kpis"], second["kpis"])
        self.assertLess(first["kpis"]["affiliations_remaining"], 550)
        self.assertGreater(first["kpis"]["affiliations_reservees"] + first["kpis"]["affiliations_affectees"], 450)
        self.assertGreaterEqual(first["kpis"]["affiliations_remaining"], 350)
