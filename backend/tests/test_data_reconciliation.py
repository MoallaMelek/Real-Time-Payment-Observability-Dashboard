import unittest

from app.services.data_reconciliation import (
    ActualPeriodMetrics,
    DataReconciliationService,
    ExpectedPeriodMetrics,
    classify_status_distribution,
)


def expected(**overrides: object) -> ExpectedPeriodMetrics:
    values = {
        "total_transactions": 3,
        "total_amount": 175.0,
        "refused": 1,
        "non_completed": 1,
        "active_terminals": 2,
        "status_distribution_raw": {
            "Transaction autorisee": 1,
            "Transaction non autorisee": 1,
            "Transaction non aboutie": 1,
        },
    }
    values.update(overrides)
    if "status_classification" not in values or "unknown_status_values" not in values:
        classified, unknown = classify_status_distribution(values["status_distribution_raw"])  # type: ignore[arg-type]
        values.setdefault("status_classification", classified)
        values.setdefault("unknown_status_values", unknown)
    return ExpectedPeriodMetrics(**values)  # type: ignore[arg-type]


def actual(**overrides: object) -> ActualPeriodMetrics:
    values = {
        "total_transactions": 3,
        "total_amount": 175.0,
        "refused": 1,
        "non_completed": 1,
        "active_terminals": 2,
        "success": 1,
        "processing_time_count": 3,
        "status_distribution_total": 3,
        "response_time_distribution_total": 3,
        "duplicates_ignored": 0,
    }
    values.update(overrides)
    return ActualPeriodMetrics(**values)  # type: ignore[arg-type]


def snapshot_for(metrics: ActualPeriodMetrics) -> dict:
    return {
        "kpis": {
            "total_transactions": metrics.total_transactions,
            "total_amount": metrics.total_amount,
            "refusal_rate": metrics.refused / metrics.total_transactions * 100 if metrics.total_transactions else 0,
            "success_rate": metrics.success / metrics.total_transactions * 100 if metrics.total_transactions else 0,
            "affiliations_total": 10,
            "affiliations_remaining": 4,
            "affiliations_reservees": 3,
            "affiliations_affectees": 3,
        },
        "status_distribution": [{"label": "x", "value": metrics.status_distribution_total}],
        "response_time_distribution": [{"label": "x", "value": metrics.response_time_distribution_total}],
    }


class DataReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = DataReconciliationService()

    def reconcile(self, expected_metrics: ExpectedPeriodMetrics | None = None, actual_metrics: ActualPeriodMetrics | None = None, **kwargs: object) -> dict:
        metrics = actual_metrics or actual()
        return self.service.reconcile(
            expected=expected_metrics if expected_metrics is not None else expected(),
            actual=metrics,
            replay_complete=kwargs.pop("replay_complete", True),  # type: ignore[arg-type]
            event_bus=kwargs.pop(
                "event_bus",
                {"transaction": {"published": metrics.total_transactions, "delivered_to_aggregator": metrics.total_transactions, "handler_errors": 0}},
            ),  # type: ignore[arg-type]
            snapshot=kwargs.pop("snapshot", snapshot_for(metrics)),  # type: ignore[arg-type]
            unavailable_reason=kwargs.pop("unavailable_reason", None),  # type: ignore[arg-type]
        )

    def test_reconciliation_without_difference_is_validated(self) -> None:
        report = self.reconcile()
        self.assertEqual(report["status"], "validated")
        self.assertTrue(all(item["within_tolerance"] for item in report["differences"].values()))

    def test_missing_transaction_is_invalid(self) -> None:
        report = self.reconcile(actual_metrics=actual(total_transactions=2, status_distribution_total=2, processing_time_count=2, response_time_distribution_total=2))
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["differences"]["total_transactions"]["difference"], -1)

    def test_duplicated_transaction_is_invalid_and_visible(self) -> None:
        report = self.reconcile(actual_metrics=actual(total_transactions=4, status_distribution_total=4, processing_time_count=4, response_time_distribution_total=4, duplicates_ignored=1))
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["actual"]["duplicates_ignored"], 1)

    def test_amount_difference_uses_explicit_tnd_tolerance(self) -> None:
        warning = self.reconcile(actual_metrics=actual(total_amount=175.0005))
        invalid = self.reconcile(actual_metrics=actual(total_amount=176.0))
        self.assertEqual(warning["status"], "warning")
        self.assertEqual(warning["amount_tolerance_tnd"], 0.001)
        self.assertEqual(invalid["status"], "invalid")

    def test_refused_and_active_terminal_differences_are_invalid(self) -> None:
        self.assertEqual(self.reconcile(actual_metrics=actual(refused=0))["status"], "invalid")
        self.assertEqual(self.reconcile(actual_metrics=actual(active_terminals=1))["status"], "invalid")

    def test_unknown_sql_status_is_not_silently_successful(self) -> None:
        raw = {"Transaction autorisee": 2, "EtatMystere": 1}
        classified, unknown = classify_status_distribution(raw)
        report = self.reconcile(expected_metrics=expected(status_distribution_raw=raw, status_classification=classified, unknown_status_values=unknown))
        self.assertEqual(classified["success"], 2)
        self.assertEqual(classified["unknown"], 1)
        self.assertEqual(report["status"], "warning")
        self.assertEqual(report["unknown_status_values"], {"EtatMystere": 1})

    def test_replay_incomplete_is_pending(self) -> None:
        report = self.reconcile(actual_metrics=actual(total_transactions=1, status_distribution_total=1, processing_time_count=1, response_time_distribution_total=1), replay_complete=False)
        self.assertEqual(report["status"], "pending")
        self.assertEqual(report["processed_transactions"], 1)
        self.assertEqual(report["expected_transactions"], 3)

    def test_event_bus_error_invalidates_quality(self) -> None:
        report = self.reconcile(event_bus={"transaction": {"published": 3, "delivered_to_aggregator": 2, "handler_errors": 1}})
        self.assertEqual(report["status"], "invalid")

    def test_distribution_invariants_invalidate_quality(self) -> None:
        metrics = actual()
        bad_snapshot = snapshot_for(metrics)
        bad_snapshot["status_distribution"] = [{"label": "x", "value": 2}]
        report = self.reconcile(actual_metrics=metrics, snapshot=bad_snapshot)
        self.assertEqual(report["status"], "invalid")
        failed = [item["name"] for item in report["invariants"] if not item["valid"]]
        self.assertIn("status_distribution_matches_total", failed)

    def test_unavailable_source_never_validates(self) -> None:
        report = self.service.reconcile(
            expected=None,
            actual=actual(),
            replay_complete=True,
            snapshot=snapshot_for(actual()),
            unavailable_reason="SQL unavailable",
        )
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["unavailable_reason"], "SQL unavailable")

    def test_affiliations_are_marked_as_simulated_projection(self) -> None:
        report = self.reconcile()
        self.assertEqual(report["metric_metadata"]["affiliations_remaining"], "simulated_projection")
        self.assertEqual(report["metric_metadata"]["global_risk_score"], "derived_metric")
        self.assertEqual(report["metric_metadata"]["total_transactions"], "source_fact")
