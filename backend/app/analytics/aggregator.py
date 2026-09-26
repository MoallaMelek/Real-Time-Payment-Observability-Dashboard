import asyncio
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import logging
from typing import Any

from app.analytics.risk_alert_engine import RiskAlertEngine
from app.models.events import TransactionEvent
from app.models.transaction import PaymentTransaction
from app.schemas.data import AlertMetricData, DashboardSnapshotData
from app.services.data_reconciliation import ActualPeriodMetrics, metric_metadata

logger = logging.getLogger(__name__)


@dataclass
class MerchantAffiliationProfile:
    """Observed merchant behaviour used to project affiliation lifecycle changes."""

    transactions: int = 0
    amount: float = 0.0
    terminals: set[str] = field(default_factory=set)
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    last_stock_action_at: datetime | None = None
    inactive_released_at: datetime | None = None
    reserved_projection: int = 0
    affected_projection: int = 0


class AnalyticsAggregator:
    """In-memory operational projection fed one replay batch at a time."""

    def __init__(self, recent_limit: int = 50, fraud_timeout_threshold_ms: float = 5000) -> None:
        self._lock = asyncio.Lock()
        self._recent_limit = recent_limit
        self._fraud_timeout_threshold_ms = fraud_timeout_threshold_ms
        self._risk_engine = RiskAlertEngine()
        self._reset_state()

    def _reset_state(self) -> None:
        self._recent: deque[PaymentTransaction] = deque(maxlen=self._recent_limit)
        self._event_batch: deque[PaymentTransaction] = deque()
        self._seen_transaction_ids: set[str] = set()
        self._alerts: deque[AlertMetricData] = deque(maxlen=30)
        self._live_events: deque[dict[str, Any]] = deque(maxlen=60)
        self._state_version = 0
        self._total_transactions = 0
        self._total_amount = 0.0
        self._success = 0
        self._refused = 0
        self._non_completed = 0
        self._slow = 0
        self._fraud_timeouts = 0
        self._abnormal_responses = 0
        self._processing_total = 0.0
        self._processing_count = 0
        self._fraud_time_count = 0
        self._fraud_decision_count = 0
        self._response_time_counts = {"< 500 ms": 0, "500–1000 ms": 0, "> 1000 ms": 0}
        self._active_terminals: set[str] = set()
        self._status_counts: Counter[str] = Counter()
        self._hours: dict[datetime, dict[str, int]] = defaultdict(lambda: {"transactions": 0, "refused": 0})
        self._days: dict[datetime, dict[str, int]] = defaultdict(
            lambda: {"refused": 0, "slow": 0, "fraud_timeouts": 0}
        )
        self._merchant_stats: dict[str, dict[str, float]] = defaultdict(self._metric_bucket)
        self._terminal_stats: dict[str, dict[str, float]] = defaultdict(self._metric_bucket)
        self._terminal_merchants: dict[str, str] = {}
        self._batch_sizes: deque[int] = deque(maxlen=10)
        self._traffic_drop_percent = 0.0
        self._transfers_amount = 0.0
        self._transfers_count = 0
        self._affiliations_total = 0
        self._affiliations_libres: int | None = None
        self._affiliations_reservees = 0
        self._affiliations_affectees = 0
        self._affiliation_demo_data = False
        self._affiliation_profiles: dict[str, MerchantAffiliationProfile] = {}
        self._amount_unit = "TND"
        self._replay_status: dict[str, Any] = self._empty_replay_status()
        self._latest_timestamp: datetime | None = None
        self._duplicates_ignored = 0

    @staticmethod
    def _empty_replay_status() -> dict[str, Any]:
        return {
            "mode": "sqlserver_replay",
            "label": "SQL Server replay mode from historical data",
            "running": False,
            "paused": False,
            "processed_transactions": 0,
            "batches_processed": 0,
            "batch_size": 0,
            "interval_seconds": 0.0,
            "current_speed_tx_per_sec": 0.0,
            "current_batch_size": 0,
            "current_interval_ms": 0,
            "fast_forward_enabled": False,
            "cursor": None,
            "last_batch_at": None,
            "source_available": True,
            "detail": "En attente du replay SQL Server.",
            "period": None,
            "reference_date": None,
            "start_date": None,
            "end_date": None,
            "transfer_period_available": False,
        }

    @staticmethod
    def _metric_bucket() -> dict[str, float]:
        return {
            "transactions": 0,
            "amount": 0.0,
            "success": 0,
            "refused": 0,
            "non_completed": 0,
            "slow": 0,
            "timeouts": 0,
            "abnormal": 0,
        }

    async def reset(self) -> None:
        async with self._lock:
            self._reset_state()

    async def rebuild(self, transactions: list[PaymentTransaction]) -> None:
        """Compatibility helper for the small local SQLite/mock test source."""
        async with self._lock:
            self._reset_state()
            for tx in self._new_transactions(transactions):
                self._record(tx)

    async def record_batch(self, transactions: list[PaymentTransaction]) -> list[PaymentTransaction]:
        async with self._lock:
            accepted = self._new_transactions(transactions)
            for tx in accepted:
                self._record(tx)
            return accepted

    async def consume_transaction_event(self, event: TransactionEvent) -> None:
        """EventBus consumer: update incremental projection from one event."""
        transaction = event.to_payment_transaction()
        async with self._lock:
            accepted = self._new_transactions([transaction])
            for item in accepted:
                self._record(item)
                self._event_batch.append(item)

    async def consume_transaction_batch(self, events: list[TransactionEvent]) -> None:
        """EventBus batch consumer: update projection with one lock acquisition."""
        transactions = [event.to_payment_transaction() for event in events]
        async with self._lock:
            accepted = self._new_transactions(transactions)
            for item in accepted:
                self._record(item)
                self._event_batch.append(item)

    async def drain_consumed_event_batch(self) -> list[PaymentTransaction]:
        async with self._lock:
            batch = list(self._event_batch)
            self._event_batch.clear()
            return batch

    def _new_transactions(self, transactions: list[PaymentTransaction]) -> list[PaymentTransaction]:
        accepted: list[PaymentTransaction] = []
        duplicates = 0
        for tx in transactions:
            if tx.transaction_id in self._seen_transaction_ids:
                duplicates += 1
                self._duplicates_ignored += 1
                continue
            self._seen_transaction_ids.add(tx.transaction_id)
            accepted.append(tx)
        if duplicates:
            logger.warning("aggregator_duplicate_transaction_ids_skipped count=%s", duplicates)
        return accepted

    async def evaluate_alerts(self, transactions: list[PaymentTransaction]) -> list[AlertMetricData]:
        async with self._lock:
            baseline = sum(self._batch_sizes) / len(self._batch_sizes) if self._batch_sizes else 0
            self._traffic_drop_percent = max(0.0, (baseline - len(transactions)) / baseline * 100) if baseline else 0.0
            alerts = self._risk_engine.evaluate_batch(
                transactions,
                self._merchant_stats,
                self._terminal_stats,
                self._traffic_drop_percent,
                self._fraud_timeout_threshold_ms,
            )
            self._batch_sizes.append(len(transactions))
            return alerts

    async def consume_alert_event(self, alert: AlertMetricData) -> None:
        async with self._lock:
            self._alerts.appendleft(alert)

    async def detect_alerts(self, transactions: list[PaymentTransaction]) -> list[AlertMetricData]:
        """Compatibility helper for direct batch callers outside EventBus."""
        alerts = await self.evaluate_alerts(transactions)
        for alert in reversed(alerts):
            await self.consume_alert_event(alert)
        return alerts

    async def set_transfer_summary(self, amount: float, count: int) -> None:
        async with self._lock:
            self._transfers_amount = max(0.0, amount)
            self._transfers_count = max(0, count)

    async def set_amount_unit(self, unit: str) -> None:
        async with self._lock:
            self._amount_unit = unit

    async def set_affiliation_summary(self, summary: dict[str, Any]) -> None:
        async with self._lock:
            self._affiliations_total = max(0, int(summary.get("affiliations_total") or 0))
            libres = summary.get("affiliations_libres")
            self._affiliations_libres = int(libres) if libres is not None else None
            self._affiliations_reservees = max(0, int(summary.get("affiliations_reservees") or 0))
            self._affiliations_affectees = max(0, int(summary.get("affiliations_affectees") or 0))
            self._affiliation_demo_data = bool(summary.get("affiliation_demo_data"))

    async def set_replay_status(self, status: dict[str, Any]) -> None:
        async with self._lock:
            self._replay_status = {**self._empty_replay_status(), **status}

    async def snapshot(self) -> DashboardSnapshotData:
        async with self._lock:
            return self._build_snapshot()

    async def actual_period_metrics(self) -> ActualPeriodMetrics:
        async with self._lock:
            return ActualPeriodMetrics(
                total_transactions=self._total_transactions,
                total_amount=round(self._total_amount, 3),
                refused=self._refused,
                non_completed=self._non_completed,
                active_terminals=len(self._active_terminals),
                success=self._success,
                processing_time_count=self._processing_count,
                status_distribution_total=sum(self._status_counts.values()),
                response_time_distribution_total=sum(self._response_time_counts.values()),
                duplicates_ignored=self._duplicates_ignored,
            )

    def _record(self, tx: PaymentTransaction) -> None:
        self._state_version += 1
        self._total_transactions += 1
        self._total_amount += tx.amount
        self._recent.appendleft(tx)
        self._active_terminals.add(tx.terminal_id)
        self._terminal_merchants[tx.terminal_id] = tx.merchant
        self._latest_timestamp = max(self._latest_timestamp, tx.timestamp) if self._latest_timestamp else tx.timestamp

        self._status_counts[self._status_label(tx)] += 1
        hour = tx.timestamp.replace(minute=0, second=0, microsecond=0)
        self._hours[hour]["transactions"] += 1
        day = tx.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)

        if tx.is_success:
            self._success += 1
        if tx.is_refused:
            self._refused += 1
            self._hours[hour]["refused"] += 1
            self._days[day]["refused"] += 1
        if tx.is_non_completed:
            self._non_completed += 1
        if tx.is_slow:
            self._slow += 1
            self._days[day]["slow"] += 1
        if tx.is_fraud_timeout_at(self._fraud_timeout_threshold_ms):
            self._fraud_timeouts += 1
            self._days[day]["fraud_timeouts"] += 1
        if tx.has_abnormal_response:
            self._abnormal_responses += 1
        if tx.processing_time_ms is not None and tx.processing_time_ms >= 0:
            self._processing_total += tx.processing_time_ms
            self._processing_count += 1
            if tx.processing_time_ms < 500:
                self._response_time_counts["< 500 ms"] += 1
            elif tx.processing_time_ms <= 1000:
                self._response_time_counts["500–1000 ms"] += 1
            else:
                self._response_time_counts["> 1000 ms"] += 1
        if tx.fraud_time_ms is not None:
            self._fraud_time_count += 1
        if tx.fraud_decision is not None:
            self._fraud_decision_count += 1

        for bucket in (self._merchant_stats[tx.merchant], self._terminal_stats[tx.terminal_id]):
            bucket["transactions"] += 1
            bucket["amount"] += tx.amount
            bucket["success"] += int(tx.is_success)
            bucket["refused"] += int(tx.is_refused)
            bucket["non_completed"] += int(tx.is_non_completed)
            bucket["slow"] += int(tx.is_slow)
            bucket["timeouts"] += int(tx.is_fraud_timeout)
            bucket["abnormal"] += int(tx.has_abnormal_response)

        self._advance_affiliation_stock(tx)
        self._live_events.appendleft(self._event_for(tx))

    def _advance_affiliation_stock(self, tx: PaymentTransaction) -> None:
        if self._affiliations_libres is None or self._affiliations_total <= 0:
            return

        merchant_key = tx.merchant_id or tx.merchant
        profile = self._affiliation_profiles.setdefault(merchant_key, MerchantAffiliationProfile())
        previous_last_seen = profile.last_seen

        profile.transactions += 1
        profile.amount += max(0.0, tx.amount)
        profile.terminals.add(tx.terminal_id)
        profile.first_seen = min(profile.first_seen, tx.timestamp) if profile.first_seen else tx.timestamp
        profile.last_seen = max(profile.last_seen, tx.timestamp) if profile.last_seen else tx.timestamp

        # Deterministic operational simulation:
        # - active/high-volume merchants reserve more stock;
        # - multi-terminal and frequent merchants convert reservations faster;
        # - merchants with a long observed gap release a projected allocation.
        self._release_inactive_affiliations(tx.timestamp)
        reserve_probability = self._merchant_reservation_probability(profile, tx)
        if self._deterministic_chance("reserve", merchant_key, tx.transaction_id, reserve_probability):
            self._reserve_affiliation(profile, tx.timestamp)

        affect_probability = self._merchant_affectation_probability(profile, tx)
        if self._deterministic_chance("affect", merchant_key, tx.transaction_id, affect_probability):
            self._affect_reserved_affiliation(profile, tx.timestamp)

        if previous_last_seen and tx.timestamp - previous_last_seen >= timedelta(days=3):
            self._release_completed_affiliation(profile, tx.timestamp)

    def _merchant_reservation_probability(self, profile: MerchantAffiliationProfile, tx: PaymentTransaction) -> float:
        if profile.transactions < 3:
            return 0.0
        merchant_count = max(1, len(self._affiliation_profiles))
        average_transactions = max(1.0, self._total_transactions / merchant_count)
        average_amount = max(1.0, self._total_amount / merchant_count)
        frequency_score = min(1.0, profile.transactions / (average_transactions * 2.5))
        volume_score = min(1.0, profile.amount / (average_amount * 2.5))
        terminal_score = min(1.0, len(profile.terminals) / 4)
        recency_score = 1.0
        if profile.last_stock_action_at:
            hours_since_action = max(0.0, (tx.timestamp - profile.last_stock_action_at).total_seconds() / 3600)
            recency_score = min(1.0, hours_since_action / 12)
        return min(0.24, 0.01 + frequency_score * 0.08 + volume_score * 0.06 + terminal_score * 0.06 + recency_score * 0.03)

    def _merchant_affectation_probability(self, profile: MerchantAffiliationProfile, tx: PaymentTransaction) -> float:
        if profile.reserved_projection <= 0 or profile.transactions < 8:
            return 0.0
        merchant_count = max(1, len(self._affiliation_profiles))
        average_transactions = max(1.0, self._total_transactions / merchant_count)
        frequency_score = min(1.0, profile.transactions / (average_transactions * 3))
        terminal_score = min(1.0, len(profile.terminals) / 3)
        success_score = 1.0 if tx.is_success else 0.35
        delay_score = 1.0
        if profile.last_stock_action_at:
            hours_since_action = max(0.0, (tx.timestamp - profile.last_stock_action_at).total_seconds() / 3600)
            delay_score = min(1.0, hours_since_action / 6)
        return min(0.18, 0.01 + frequency_score * 0.06 + terminal_score * 0.05 + success_score * 0.04 + delay_score * 0.04)

    @staticmethod
    def _deterministic_chance(action: str, merchant_key: str, transaction_id: str, probability: float) -> bool:
        if probability <= 0:
            return False
        digest = hashlib.blake2b(
            f"{action}:{merchant_key}:{transaction_id}".encode("utf-8"),
            digest_size=8,
        ).digest()
        bucket = int.from_bytes(digest, "big") / float(2**64 - 1)
        return bucket < probability

    def _reserve_affiliation(self, profile: MerchantAffiliationProfile, timestamp: datetime) -> None:
        if self._affiliations_libres is None or self._affiliations_libres <= 0:
            return
        operational_floor = max(1, int(self._affiliations_total * 0.35))
        if self._affiliations_libres <= operational_floor:
            return
        self._affiliations_libres -= 1
        self._affiliations_reservees += 1
        profile.reserved_projection += 1
        profile.last_stock_action_at = timestamp

    def _affect_reserved_affiliation(self, profile: MerchantAffiliationProfile, timestamp: datetime) -> None:
        if self._affiliations_reservees <= 0 or profile.reserved_projection <= 0:
            return
        self._affiliations_reservees -= 1
        self._affiliations_affectees += 1
        profile.reserved_projection -= 1
        profile.affected_projection += 1
        profile.last_stock_action_at = timestamp

    def _release_completed_affiliation(self, profile: MerchantAffiliationProfile, timestamp: datetime) -> None:
        if self._affiliations_libres is None or self._affiliations_affectees <= 0 or profile.affected_projection <= 0:
            return
        self._affiliations_affectees -= 1
        self._affiliations_libres += 1
        profile.affected_projection -= 1
        profile.last_stock_action_at = timestamp
        profile.inactive_released_at = timestamp

    def _release_inactive_affiliations(self, replay_timestamp: datetime) -> None:
        for profile in self._affiliation_profiles.values():
            if profile.last_seen is None or profile.affected_projection <= 0:
                continue
            if replay_timestamp - profile.last_seen < timedelta(days=2):
                continue
            if profile.inactive_released_at and replay_timestamp - profile.inactive_released_at < timedelta(days=1):
                continue
            self._release_completed_affiliation(profile, replay_timestamp)

    @staticmethod
    def _status_label(tx: PaymentTransaction) -> str:
        if tx.is_refused:
            return "Refusées"
        if tx.is_non_completed:
            return "Non abouties"
        if tx.payment_status == "reversed":
            return "Annulées"
        if tx.is_success:
            return "Autorisées"
        return "En instance"

    def _event_for(self, tx: PaymentTransaction) -> dict[str, Any]:
        if tx.is_fraud_timeout_at(self._fraud_timeout_threshold_ms):
            severity, title = "critical", "Timeout anti-fraude"
        elif tx.is_refused:
            severity, title = "warning", "Transaction refusée"
        elif tx.is_non_completed:
            severity, title = "warning", "Transaction non aboutie"
        elif tx.is_slow:
            severity, title = "warning", "Transaction lente"
        else:
            severity, title = "info", "Transaction autorisée"
        details = []
        if tx.response_code:
            details.append(f"code {tx.response_code}")
        if tx.processing_time_ms is not None:
            details.append(f"{tx.processing_time_ms:.0f} ms")
        return {
            "id": f"event:{tx.transaction_id}",
            "timestamp": tx.timestamp.isoformat(),
            "severity": severity,
            "title": title,
            "message": " — ".join(details) or (tx.source_status or tx.payment_status),
            "merchant_name": tx.merchant,
            "merchant": tx.merchant,
            "terminal_id": tx.terminal_id,
        }

    def _build_snapshot(self) -> DashboardSnapshotData:
        total = self._total_transactions
        processing_available = self._processing_count > 0
        fraud_available = self._fraud_time_count > 0 or self._fraud_decision_count > 0
        refusal_rate = self._refused / total * 100 if total else 0.0
        success_rate = self._success / total * 100 if total else 0.0
        risk = self._risk_engine.score(
            total=total,
            refused=self._refused,
            non_completed=self._non_completed,
            slow=self._slow,
            fraud_timeouts=self._fraud_timeouts,
            abnormal_responses=self._abnormal_responses,
            traffic_drop_percent=self._traffic_drop_percent,
        )
        return {
            "state_version": self._state_version,
            "kpis": {
                "refusal_rate": round(refusal_rate, 2),
                "slow_transactions": self._slow if processing_available else None,
                "slow_transactions_available": processing_available,
                "fraud_timeouts": self._fraud_timeouts if fraud_available else None,
                "fraud_timeout_available": fraud_available,
                "non_completed_transactions": self._non_completed,
                "global_risk_score": risk,
                "total_amount": round(self._total_amount, 3),
                "amount_unit": self._amount_unit,
                "total_transactions": total,
                "success_rate": round(success_rate, 2),
                "active_terminals": len(self._active_terminals),
                "total_transfers_amount": round(self._transfers_amount, 3),
                "transfers_count": self._transfers_count,
                "transfer_period_available": bool(self._replay_status.get("transfer_period_available")),
                "affiliations_remaining": self._affiliations_libres,
                "affiliations_total": self._affiliations_total,
                "affiliations_reservees": self._affiliations_reservees,
                "affiliations_affectees": self._affiliations_affectees,
                "affiliation_demo_data": self._affiliation_demo_data,
                "average_processing_time_ms": round(self._processing_total / self._processing_count, 1)
                if processing_available
                else None,
                "avg_processing_time_available": processing_available,
            },
            "incidents_by_hour": self._hour_series(),
            "fraud_trend_7_days": self._fraud_trend(),
            "response_time_distribution": self._response_distribution(),
            "status_distribution": self._status_distribution(),
            "active_alerts": list(self._alerts)[:8],
            "live_events": list(self._live_events)[:40],
            "top_anomalies": self._top_anomalies(),
            "top_tpe": self._top_tpe(),
            "top_merchants": self._top_merchants(),
            "replay_status": self._replay_status,
            "metric_metadata": metric_metadata(),
        }

    def _hour_series(self) -> list[dict[str, int | str]]:
        reference = (self._latest_timestamp or datetime.now(timezone.utc)).replace(minute=0, second=0, microsecond=0)
        return [
            {
                "hour": (reference - timedelta(hours=offset)).strftime("%Hh"),
                "transactions": self._hours[reference - timedelta(hours=offset)]["transactions"],
                "refused": self._hours[reference - timedelta(hours=offset)]["refused"],
            }
            for offset in range(10, -1, -1)
        ]

    def _fraud_trend(self) -> list[dict[str, int | str]]:
        reference = (self._latest_timestamp or datetime.now(timezone.utc)).replace(hour=0, minute=0, second=0, microsecond=0)
        processing_available = self._processing_count > 0
        fraud_available = self._fraud_time_count > 0 or self._fraud_decision_count > 0
        rows: list[dict[str, int | str]] = []
        for offset in range(6, -1, -1):
            day = reference - timedelta(days=offset)
            value = self._days[day]
            rows.append(
                {
                    "day": "Auj." if offset == 0 else day.strftime("%d/%m"),
                    "refused": value["refused"],
                    "slow": value["slow"] if processing_available else None,
                    "fraud_timeouts": value["fraud_timeouts"] if fraud_available else None,
                }
            )
        return rows

    def _response_distribution(self) -> list[dict[str, int | str]]:
        if self._processing_count == 0:
            return []
        return [
            {"label": "< 500 ms", "value": self._response_time_counts["< 500 ms"], "color": "#00d99c"},
            {"label": "500–1000 ms", "value": self._response_time_counts["500–1000 ms"], "color": "#f59e0b"},
            {"label": "> 1000 ms", "value": self._response_time_counts["> 1000 ms"], "color": "#ff4d6d"},
        ]

    def _status_distribution(self) -> list[dict[str, int | str]]:
        colors = {
            "Autorisées": "#00d99c",
            "Refusées": "#ff4d6d",
            "Annulées": "#f59e0b",
            "Non abouties": "#77839a",
            "En instance": "#3b82f6",
        }
        return [
            {"label": label, "value": self._status_counts[label], "color": colors[label]}
            for label in colors
        ]

    def _score_bucket(self, stats: dict[str, float]) -> int:
        return self._risk_engine.score(
            total=int(stats["transactions"]),
            refused=int(stats["refused"]),
            non_completed=int(stats["non_completed"]),
            slow=int(stats["slow"]),
            fraud_timeouts=int(stats["timeouts"]),
            abnormal_responses=int(stats["abnormal"]),
            traffic_drop_percent=0,
        )

    @staticmethod
    def _severity(score: int) -> str:
        return "high" if score >= 60 else "medium" if score >= 30 else "low"

    def _top_anomalies(self) -> list[dict[str, Any]]:
        processing_available = self._processing_count > 0
        fraud_available = self._fraud_time_count > 0 or self._fraud_decision_count > 0
        rows = []
        for name, stats in self._merchant_stats.items():
            score = self._score_bucket(stats)
            rows.append(
                {
                    "name": name,
                    "merchant_name": name,
                    "refused": int(stats["refused"]),
                    "timeouts": int(stats["timeouts"]) if fraud_available else None,
                    "slow": int(stats["slow"]) if processing_available else None,
                    "risk_score": score,
                    "severity": self._severity(score),
                }
            )
        return sorted(rows, key=lambda row: (row["risk_score"], row["refused"]), reverse=True)[:10]

    def _top_tpe(self) -> list[dict[str, Any]]:
        fraud_available = self._fraud_time_count > 0 or self._fraud_decision_count > 0
        rows = []
        for terminal_id, stats in self._terminal_stats.items():
            score = self._score_bucket(stats)
            rows.append(
                {
                    "terminal_id": terminal_id,
                    "merchant_name": self._terminal_merchants.get(terminal_id, "—"),
                    "merchant": self._terminal_merchants.get(terminal_id, "—"),
                    "transactions": int(stats["transactions"]),
                    "refused": int(stats["refused"]),
                    "timeouts": int(stats["timeouts"]) if fraud_available else None,
                    "risk_score": score,
                    "severity": self._severity(score),
                }
            )
        return sorted(rows, key=lambda row: (row["risk_score"], row["transactions"]), reverse=True)[:10]

    def _top_merchants(self) -> list[dict[str, Any]]:
        rows = []
        for name, stats in self._merchant_stats.items():
            transactions = int(stats["transactions"])
            success_rate = stats["success"] / transactions * 100 if transactions else 0
            score = self._score_bucket(stats)
            rows.append(
                {
                    "name": name,
                    "merchant_name": name,
                    "transactions": transactions,
                    "refused": int(stats["refused"]),
                    "non_completed": int(stats["non_completed"]),
                    "risk_score": score,
                    "severity": self._severity(score),
                    "success_rate": round(success_rate, 1),
                }
            )
        return sorted(rows, key=lambda row: (row["transactions"], row["risk_score"]), reverse=True)[:10]
