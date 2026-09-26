"""Controlled reconciliation reports for explicit portfolio_demo dates."""

from datetime import date
from typing import Any
from uuid import uuid4

from app.analytics.aggregator import AnalyticsAggregator
from app.config import Settings
from app.services.data_reconciliation import DataReconciliationService
from app.services.event_bus import InMemoryEventBus
from app.services.replay_producer import ReplayProducer
from app.services.sqlserver_replay_provider import SqlServerReplayProvider


class ReconciliationReportService:
    """Replay explicit days through the normal event pipeline in isolation."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._reconciliation = DataReconciliationService()

    async def reconcile_dates(self, dates: list[date], batch_size: int | None = None) -> dict[str, Any]:
        replay_run_id = uuid4().hex
        rows = [
            await self._reconcile_date(item, batch_size or self._settings.fast_forward_fetch_batch_size, replay_run_id)
            for item in dates
        ]
        return {
            "replay_run_id": replay_run_id,
            "dates": [item.isoformat() for item in dates],
            "rows": rows,
        }

    async def _reconcile_date(self, target_date: date, batch_size: int, replay_run_id: str) -> dict[str, Any]:
        provider = SqlServerReplayProvider(self._settings)
        aggregator = AnalyticsAggregator(fraud_timeout_threshold_ms=self._settings.fraud_timeout_threshold_ms)
        bus = InMemoryEventBus()
        bus.subscribe("transaction", aggregator.consume_transaction_event)
        producer = ReplayProducer(provider, bus)
        try:
            await provider.connect()
            await provider.configure_date(target_date)
            expected = await provider.expected_period_metrics()
            await aggregator.set_amount_unit(provider.amount_metadata["amount_unit"])

            while True:
                events = await producer.produce_next_batch(batch_size)
                if not events:
                    break
                await aggregator.drain_consumed_event_batch()

            actual = await aggregator.actual_period_metrics()
            snapshot = await aggregator.snapshot()
            event_bus = bus.metrics()
            transaction_metrics = dict(event_bus.get("transaction", {}))
            transaction_metrics["delivered_to_aggregator"] = transaction_metrics.get("delivered", 0)
            transaction_metrics["accepted_after_deduplication"] = actual.total_transactions
            transaction_metrics["duplicates_ignored"] = actual.duplicates_ignored
            event_bus["transaction"] = transaction_metrics
            report = self._reconciliation.reconcile(
                expected=expected,
                actual=actual,
                replay_complete=True,
                event_bus=event_bus,
                snapshot=snapshot,
            )
            return {
                "replay_run_id": replay_run_id,
                "period": target_date.isoformat(),
                "sql_expected": expected.total_transactions,
                "aggregator": actual.total_transactions,
                "difference": report["differences"]["total_transactions"]["difference"],
                "status": report["status"],
                "refused_expected": expected.refused,
                "refused_actual": actual.refused,
                "non_completed_expected": expected.non_completed,
                "non_completed_actual": actual.non_completed,
                "active_terminals_expected": expected.active_terminals,
                "active_terminals_actual": actual.active_terminals,
                "total_amount_expected": expected.total_amount,
                "total_amount_actual": actual.total_amount,
                "unknown_status_values": report["unknown_status_values"],
                "data_quality": report,
            }
        except Exception as exc:
            return {
                "replay_run_id": replay_run_id,
                "period": target_date.isoformat(),
                "sql_expected": None,
                "aggregator": None,
                "difference": None,
                "status": "unavailable",
                "error": f"{type(exc).__name__}: {exc}",
            }
        finally:
            await provider.close()
