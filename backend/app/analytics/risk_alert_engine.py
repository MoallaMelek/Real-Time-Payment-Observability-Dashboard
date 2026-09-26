"""Deterministic and explainable supervision-risk rules."""

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.models.transaction import PaymentTransaction
from app.schemas.data import AlertMetricData


class RiskAlertEngine:
    """Small rule engine: every point in the global score is traceable."""

    @staticmethod
    def score(
        *,
        total: int,
        refused: int,
        non_completed: int,
        slow: int,
        fraud_timeouts: int,
        abnormal_responses: int,
        traffic_drop_percent: float,
    ) -> int:
        if total <= 0:
            return 0
        refusal_rate = refused / total * 100
        non_completed_rate = non_completed / total * 100
        slow_rate = slow / total * 100
        fraud_rate = fraud_timeouts / total * 100
        abnormal_rate = abnormal_responses / total * 100
        value = (
            min(30, refusal_rate * 3)
            + min(20, non_completed_rate * 4)
            + min(15, slow_rate * 1.5)
            + min(15, fraud_rate * 3)
            + min(10, abnormal_rate * 1.5)
            + min(10, max(0, traffic_drop_percent) / 4)
        )
        return max(0, min(100, round(value)))

    def evaluate_batch(
        self,
        transactions: list[PaymentTransaction],
        merchant_stats: dict[str, dict[str, float]],
        terminal_stats: dict[str, dict[str, float]],
        traffic_drop_percent: float,
        fraud_timeout_threshold_ms: float = 5000,
    ) -> list[AlertMetricData]:
        if not transactions:
            return []

        now = datetime.now(timezone.utc).isoformat()
        alerts: list[AlertMetricData] = []
        batch_refusal_rate = sum(tx.is_refused for tx in transactions) / len(transactions) * 100
        if batch_refusal_rate >= 10:
            alerts.append(
                self._alert(
                    "high_refusal_rate",
                    "critical" if batch_refusal_rate >= 20 else "warning",
                    "Taux de refus élevé",
                    f"{batch_refusal_rate:.1f}% des transactions du dernier lot sont refusées.",
                    now,
                )
            )

        if traffic_drop_percent >= 40:
            alerts.append(
                self._alert(
                    "traffic_drop",
                    "warning",
                    "Chute de trafic",
                    f"Le dernier lot est inférieur de {traffic_drop_percent:.0f}% à la fenêtre glissante.",
                    now,
                )
            )

        for tx in transactions:
            if tx.is_fraud_timeout_at(fraud_timeout_threshold_ms):
                alerts.append(
                    self._alert(
                        "fraud_timeout",
                        "critical",
                        "Timeout anti-fraude",
                        f"Décision anti-fraude lente ou suspecte sur {tx.terminal_id}.",
                        now,
                        tx,
                    )
                )
            elif tx.is_slow:
                alerts.append(
                    self._alert(
                        "slow_transaction",
                        "warning",
                        "Transaction lente détectée",
                        f"Traitement à {tx.processing_time_ms:.0f} ms sur {tx.terminal_id}.",
                        now,
                        tx,
                    )
                )

        impacted_merchants = Counter(tx.merchant for tx in transactions)
        for merchant in impacted_merchants:
            stats = merchant_stats[merchant]
            transactions_count = max(1, int(stats["transactions"]))
            failure_rate = (stats["refused"] + stats["non_completed"]) / transactions_count * 100
            if transactions_count >= 3 and failure_rate >= 20:
                alerts.append(
                    self._alert(
                        "suspect_merchant",
                        "critical" if failure_rate >= 40 else "warning",
                        "Commerçant suspect détecté",
                        f"{merchant}: {failure_rate:.0f}% de refus ou transactions non abouties.",
                        now,
                    )
                )

        impacted_terminals = Counter(tx.terminal_id for tx in transactions)
        for terminal_id in impacted_terminals:
            stats = terminal_stats[terminal_id]
            transactions_count = max(1, int(stats["transactions"]))
            failure_rate = (stats["refused"] + stats["non_completed"]) / transactions_count * 100
            if transactions_count >= 3 and failure_rate >= 30:
                alerts.append(
                    self._alert(
                        "suspect_terminal",
                        "warning",
                        "TPE suspect détecté",
                        f"{terminal_id}: {failure_rate:.0f}% d'incidents opérationnels.",
                        now,
                        terminal_id=terminal_id,
                    )
                )

        # Repeated alerts from a large replay batch make the feed unreadable.
        return self._deduplicate(alerts)[:8]

    @staticmethod
    def _alert(
        alert_type: str,
        severity: str,
        title: str,
        message: str,
        timestamp: str,
        tx: PaymentTransaction | None = None,
        terminal_id: str | None = None,
    ) -> AlertMetricData:
        return {
            "id": f"{alert_type}:{tx.transaction_id if tx else terminal_id or title}:{timestamp}",
            "timestamp": timestamp,
            "type": alert_type,
            "severity": severity,  # type: ignore[typeddict-item]
            "title": title,
            "message": message,
            "merchant_name": tx.merchant if tx else None,
            "merchant_id": tx.merchant_id if tx else None,
            "terminal_id": tx.terminal_id if tx else terminal_id,
        }

    @staticmethod
    def _deduplicate(alerts: list[AlertMetricData]) -> list[AlertMetricData]:
        seen: set[tuple[str, str | None, str | None]] = set()
        unique: list[AlertMetricData] = []
        for alert in alerts:
            key = (alert["type"], alert["merchant_id"], alert["terminal_id"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(alert)
        return unique
