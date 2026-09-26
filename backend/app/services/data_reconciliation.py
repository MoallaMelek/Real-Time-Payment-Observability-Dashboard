"""Data quality and reconciliation helpers for SQL replay snapshots.

The amount tolerance is deliberately small and explicit: 0.001 TND.  It only
absorbs millime-to-TND rounding noise and must not hide material differences.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Literal

logger_name = "app.services.data_reconciliation"
logger = logging.getLogger(logger_name)

ReconciliationStatus = Literal["pending", "validated", "warning", "invalid", "unavailable"]
MetricCategory = Literal["source_fact", "derived_metric", "simulated_projection"]
StatusClass = Literal["success", "refused", "non_completed", "reversed", "unknown"]

AMOUNT_TOLERANCE_TND = 0.001


@dataclass(frozen=True, slots=True)
class ExpectedPeriodMetrics:
    total_transactions: int
    total_amount: float
    refused: int
    non_completed: int
    active_terminals: int
    invalid_datetime_rows: int = 0
    invalid_amount_rows: int = 0
    status_distribution_raw: dict[str, int] = field(default_factory=dict)
    status_classification: dict[StatusClass, int] = field(
        default_factory=lambda: {
            "success": 0,
            "refused": 0,
            "non_completed": 0,
            "reversed": 0,
            "unknown": 0,
        }
    )
    unknown_status_values: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ActualPeriodMetrics:
    total_transactions: int
    total_amount: float
    refused: int
    non_completed: int
    active_terminals: int
    success: int
    processing_time_count: int
    status_distribution_total: int
    response_time_distribution_total: int
    duplicates_ignored: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_sql_status(raw_value: Any) -> StatusClass:
    value = str(raw_value or "").strip().lower()
    if not value:
        return "unknown"
    if "non autor" in value or "refus" in value:
        return "refused"
    if "non about" in value or "attente" in value or "instance" in value:
        return "non_completed"
    if "annul" in value or "reverse" in value:
        return "reversed"
    if any(token in value for token in ("autor", "approuv", "approved", "accept", "succes", "succès", "ok")):
        return "success"
    return "unknown"


def classify_status_distribution(raw_distribution: dict[str, int]) -> tuple[dict[StatusClass, int], dict[str, int]]:
    classified: dict[StatusClass, int] = {
        "success": 0,
        "refused": 0,
        "non_completed": 0,
        "reversed": 0,
        "unknown": 0,
    }
    unknown: dict[str, int] = {}
    for raw, count in raw_distribution.items():
        status_class = classify_sql_status(raw)
        classified[status_class] += int(count)
        if status_class == "unknown":
            unknown[raw] = int(count)
    return classified, unknown


def metric_metadata() -> dict[str, MetricCategory]:
    return {
        "total_transactions": "source_fact",
        "total_amount": "source_fact",
        "refused": "source_fact",
        "non_completed_transactions": "source_fact",
        "active_terminals": "source_fact",
        "transfers_count": "source_fact",
        "total_transfers_amount": "source_fact",
        "refusal_rate": "derived_metric",
        "success_rate": "derived_metric",
        "average_processing_time_ms": "derived_metric",
        "slow_transactions": "derived_metric",
        "fraud_timeouts": "derived_metric",
        "global_risk_score": "derived_metric",
        "top_anomalies": "derived_metric",
        "affiliations_remaining": "simulated_projection",
        "affiliations_reservees": "simulated_projection",
        "affiliations_affectees": "simulated_projection",
    }


class DataReconciliationService:
    def reconcile(
        self,
        *,
        expected: ExpectedPeriodMetrics | None,
        actual: ActualPeriodMetrics,
        replay_complete: bool,
        event_bus: dict[str, Any] | None = None,
        snapshot: dict[str, Any] | None = None,
        unavailable_reason: str | None = None,
    ) -> dict[str, Any]:
        if expected is None:
            return self._report(
                status="unavailable",
                expected=None,
                actual=actual,
                differences={},
                invariants=[],
                event_bus=event_bus,
                unavailable_reason=unavailable_reason,
            )

        differences = self._differences(expected, actual)
        invariants = self._validate_invariants(snapshot or {}, actual)
        event_issues = self._event_bus_issues(event_bus)
        material_differences = {
            key: value
            for key, value in differences.items()
            if not value["within_tolerance"] and value["difference"] != 0
        }
        amount_warning = (
            differences["total_amount"]["difference"] != 0
            and differences["total_amount"]["within_tolerance"]
        )
        has_warning = amount_warning or expected.status_classification.get("unknown", 0) > 0
        invalid_reasons = [item for item in invariants if not item["valid"]] + event_issues
        for item in invalid_reasons:
            logger.error("data_quality_invariant_failed name=%s", item["name"])

        if not replay_complete:
            status: ReconciliationStatus = "pending"
        elif invalid_reasons or material_differences:
            status = "invalid"
        elif has_warning:
            status = "warning"
        else:
            status = "validated"

        return self._report(
            status=status,
            expected=expected,
            actual=actual,
            differences=differences,
            invariants=invariants,
            event_bus=event_bus,
            unavailable_reason=None,
        )

    @staticmethod
    def _differences(expected: ExpectedPeriodMetrics, actual: ActualPeriodMetrics) -> dict[str, dict[str, Any]]:
        specs = {
            "total_transactions": (expected.total_transactions, actual.total_transactions, 0.0),
            "total_amount": (expected.total_amount, actual.total_amount, AMOUNT_TOLERANCE_TND),
            "refused": (expected.refused, actual.refused, 0.0),
            "non_completed": (expected.non_completed, actual.non_completed, 0.0),
            "active_terminals": (expected.active_terminals, actual.active_terminals, 0.0),
        }
        return {
            key: {
                "expected": expected_value,
                "actual": actual_value,
                "difference": round(actual_value - expected_value, 6),
                "tolerance": tolerance,
                "within_tolerance": abs(actual_value - expected_value) <= tolerance,
            }
            for key, (expected_value, actual_value, tolerance) in specs.items()
        }

    @staticmethod
    def _validate_invariants(snapshot: dict[str, Any], actual: ActualPeriodMetrics) -> list[dict[str, Any]]:
        kpis = snapshot.get("kpis", {})
        status_total = sum(int(item.get("value") or 0) for item in snapshot.get("status_distribution", []))
        response_total = sum(int(item.get("value") or 0) for item in snapshot.get("response_time_distribution", []))
        affiliations_total = kpis.get("affiliations_total")
        affiliations_remaining = kpis.get("affiliations_remaining")
        affiliations_sum_available = affiliations_total is not None and affiliations_remaining is not None
        checks = [
            ("total_transactions_non_negative", actual.total_transactions >= 0),
            ("total_amount_non_negative", actual.total_amount >= 0),
            ("refused_not_greater_than_total", actual.refused <= actual.total_transactions),
            ("non_completed_not_greater_than_total", actual.non_completed <= actual.total_transactions),
            ("success_not_greater_than_total", actual.success <= actual.total_transactions),
            ("active_terminals_not_greater_than_total", actual.active_terminals <= actual.total_transactions),
            ("refusal_rate_between_0_and_100", 0 <= float(kpis.get("refusal_rate", 0)) <= 100),
            ("success_rate_between_0_and_100", 0 <= float(kpis.get("success_rate", 0)) <= 100),
            ("status_distribution_matches_total", status_total == actual.total_transactions),
            ("response_distribution_matches_processing_count", response_total == actual.processing_time_count),
            (
                "affiliation_projection_balances_available_stock",
                not affiliations_sum_available
                or int(affiliations_remaining or 0)
                + int(kpis.get("affiliations_reservees") or 0)
                + int(kpis.get("affiliations_affectees") or 0)
                == int(affiliations_total or 0),
            ),
        ]
        return [{"name": name, "valid": bool(valid)} for name, valid in checks]

    @staticmethod
    def _event_bus_issues(event_bus: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not event_bus:
            return []
        transaction = event_bus.get("transaction", {})
        issues = []
        if int(transaction.get("handler_errors") or 0) > 0:
            issues.append({"name": "event_bus_handler_errors", "valid": False})
        if int(transaction.get("published") or 0) != int(transaction.get("delivered_to_aggregator") or 0):
            issues.append({"name": "event_bus_delivery_mismatch", "valid": False})
        return issues

    @staticmethod
    def _report(
        *,
        status: ReconciliationStatus,
        expected: ExpectedPeriodMetrics | None,
        actual: ActualPeriodMetrics,
        differences: dict[str, Any],
        invariants: list[dict[str, Any]],
        event_bus: dict[str, Any] | None,
        unavailable_reason: str | None,
    ) -> dict[str, Any]:
        expected_total = expected.total_transactions if expected else None
        completion_rate = (
            round(actual.total_transactions / expected_total * 100, 2)
            if expected_total
            else (100.0 if expected_total == 0 else 0.0)
        )
        return {
            "status": status,
            "reconciliation_status": status,
            "expected_transactions": expected_total,
            "processed_transactions": actual.total_transactions,
            "completion_rate": completion_rate,
            "last_reconciled_at": datetime.now(timezone.utc).isoformat(),
            "differences": differences,
            "amount_tolerance_tnd": AMOUNT_TOLERANCE_TND,
            "expected": expected.to_dict() if expected else None,
            "actual": actual.to_dict(),
            "invariants": invariants,
            "event_bus": event_bus or {},
            "unknown_status_values": expected.unknown_status_values if expected else {},
            "metric_metadata": metric_metadata(),
            "unavailable_reason": unavailable_reason,
        }
