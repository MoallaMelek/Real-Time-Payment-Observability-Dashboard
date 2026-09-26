import unittest
from datetime import date, time

from app.config import Settings
from app.analytics.aggregator import AnalyticsAggregator
from app.services.sqlserver_replay_provider import SqlServerReplayProvider

BASE_COLUMNS = [
    ("transaction_id",), ("transaction_status",), ("merchant_id",), ("store_id",),
    ("merchant_terminal_id",), ("terminal_id",), ("merchant_name",), ("response_code",), ("Amount",),
    ("transaction_date",), ("transaction_time",), ("processing_time_ms",),
    ("risk_processing_time_ms",), ("risk_decision",), ("transfer_issued",),
]
ENRICHMENT_COLUMNS = [
    ("demo_terminal_directory_id",), ("demo_terminal_directory_merchant_id",),
    ("demo_terminal_directory_magasin_id",), ("merchant_name",), ("terminal_status",),
    ("softpos",), ("connectivity",),
]


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.description: list[tuple[str]] = []
        self.rows: list[tuple] = []

    def execute(self, query: str, *params: object) -> "FakeCursor":
        self.connection.executed.append((query, params))
        if "AS total_transactions" in query:
            self.description = [
                ("total_transactions",), ("total_amount",), ("refused",), ("non_completed",),
                ("active_terminals",), ("invalid_datetime_rows",), ("invalid_amount_rows",),
            ]
            self.rows = [self.connection.expected_row]
        elif "AS raw_status" in query:
            self.description = [("raw_status",), ("status_count",)]
            self.rows = self.connection.status_rows
        elif "FROM dbo.demo_transactions" in query:
            self.description = BASE_COLUMNS
            self.rows = self.connection.base_rows
        elif "demo_terminal_directory_merchant_id IN" in query:
            self.description = ENRICHMENT_COLUMNS
            self.rows = self.connection.merchant_rows
        elif "demo_terminal_directory_magasin_id IN" in query:
            self.description = ENRICHMENT_COLUMNS
            self.rows = self.connection.magasin_rows
        elif "FROM dbo.demo_transfers" in query:
            self.description = [("nb_virements",), ("montant_total_virements",)]
            self.rows = [self.connection.transfer_row] if self.connection.transfer_row else []
        return self

    def fetchone(self) -> tuple[int] | None:
        return self.rows[0] if self.rows else (1,)

    def fetchall(self) -> list[tuple]:
        return self.rows

    def close(self) -> None:
        return None


class FakeConnection:
    def __init__(
        self,
        base_rows: list[tuple],
        merchant_rows: list[tuple] | None = None,
        magasin_rows: list[tuple] | None = None,
        transfer_row: tuple[int, object] | None = None,
        expected_row: tuple[object, ...] | None = None,
        status_rows: list[tuple[str, int]] | None = None,
    ) -> None:
        self.base_rows = base_rows
        self.merchant_rows = merchant_rows or []
        self.magasin_rows = magasin_rows or []
        self.transfer_row = transfer_row
        self.expected_row = expected_row or (0, 0, 0, 0, 0, 0, 0)
        self.status_rows = status_rows or []
        self.executed: list[tuple[str, tuple]] = []
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def close(self) -> None:
        self.closed = True


def base_transaction(transaction_id: str = "TX-100", amount: str = "12500") -> tuple:
    return (
        transaction_id, "Transaction non autorisée", "42", "8", "TM-7", "SN-100", "Le vrai commerçant", "051", amount,
        date(2026, 6, 18), time(11, 30, 4), 1250, 40, None, 0,
    )


class SqlServerReplayProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_unique_base_rows_then_enriches_with_merchant_priority(self) -> None:
        connections: list[FakeConnection] = []

        def factory(_connection_string: str) -> FakeConnection:
            connection = FakeConnection(
                [base_transaction()],
                merchant_rows=[("DIR-1", 42, 999, "Boutique Zen", "ACTIVE", 0, "online")],
                magasin_rows=[("DIR-2", 999, 8, "Fallback Magasin", "INACTIVE", 1, "offline")],
            )
            connections.append(connection)
            return connection

        provider = SqlServerReplayProvider(Settings(replay_batch_size=500), connection_factory=factory)
        provider._period_window = provider._period_window_for("today", date(2026, 6, 18))
        transactions = await provider.fetch_next_batch()

        self.assertEqual(len(transactions), 1)
        transaction = transactions[0]
        self.assertEqual(transaction.transaction_id, "TX-100")
        self.assertTrue(transaction.is_refused)
        self.assertTrue(transaction.is_slow)
        self.assertEqual(transaction.merchant, "Le vrai commerçant")
        self.assertEqual(transaction.amount, 12.5)
        self.assertIn("2026-06-18", provider.cursor_label)

        queries = [query for query, _params in connections[0].executed]
        base_query = next(query for query in queries if "FROM dbo.demo_transactions" in query)
        self.assertIn("SELECT TOP (500)", base_query)
        self.assertNotIn("JOIN dbo.demo_terminal_directory", base_query)
        for forbidden in ("RIB", "CardMask", "CardBin", "PAN", "Track2", "EncryptedPIN", "CardholderName", "msisdn", "OTP"):
            self.assertNotIn(forbidden, base_query)

        payload = transaction.to_dict()
        self.assertEqual(set(payload), {"id", "terminal_id", "merchant", "merchant_id", "amount", "timestamp", "status", "response_code", "processing_time_ms", "fraud_time_ms"})

    async def test_falls_back_to_discrete_merchant_id_label_when_name_is_missing(self) -> None:
        def factory(_connection_string: str) -> FakeConnection:
            row = list(base_transaction())
            row[6] = None
            return FakeConnection([tuple(row)])

        provider = SqlServerReplayProvider(Settings(replay_batch_size=500), connection_factory=factory)
        provider._period_window = provider._period_window_for("today", date(2026, 6, 18))
        transactions = await provider.fetch_next_batch()

        self.assertEqual(transactions[0].merchant, "Commerçant #42")

    async def test_deduplicates_a_defensive_duplicate_base_id(self) -> None:
        def factory(_connection_string: str) -> FakeConnection:
            return FakeConnection([base_transaction("TX-DUP"), base_transaction("TX-DUP")])

        provider = SqlServerReplayProvider(Settings(replay_batch_size=500), connection_factory=factory)
        provider._period_window = provider._period_window_for("today", date(2026, 6, 18))
        with self.assertLogs("app.services.sqlserver_replay_provider", "WARNING"):
            transactions = await provider.fetch_next_batch()
        self.assertEqual([transaction.transaction_id for transaction in transactions], ["TX-DUP"])

    async def test_normalizes_amounts_with_the_configured_scale(self) -> None:
        def factory(_connection_string: str) -> FakeConnection:
            return FakeConnection([base_transaction()])

        provider = SqlServerReplayProvider(Settings(replay_batch_size=500, amount_scale=1000), connection_factory=factory)
        provider._period_window = provider._period_window_for("today", date(2026, 6, 18))
        transactions = await provider.fetch_next_batch()

        self.assertEqual(transactions[0].amount, 12.5)
        self.assertEqual(provider.amount_metadata, {"amount_unit": "TND"})

    async def test_accepts_a_larger_fast_forward_batch_without_changing_normalization(self) -> None:
        connections: list[FakeConnection] = []

        def factory(_connection_string: str) -> FakeConnection:
            connection = FakeConnection([base_transaction()])
            connections.append(connection)
            return connection

        provider = SqlServerReplayProvider(Settings(replay_batch_size=500), connection_factory=factory)
        provider._period_window = provider._period_window_for("today", date(2026, 6, 18))
        transactions = await provider.fetch_next_batch(batch_size=5000)

        self.assertEqual(transactions[0].amount, 12.5)
        base_query = next(query for query, _params in connections[0].executed if "FROM dbo.demo_transactions" in query)
        self.assertIn("SELECT TOP (5000)", base_query)

    async def test_aggregates_sql_millimes_as_tnd_before_building_snapshot(self) -> None:
        def factory(_connection_string: str) -> FakeConnection:
            return FakeConnection([base_transaction("TX-1", "125000"), base_transaction("TX-2", "50000")])

        provider = SqlServerReplayProvider(Settings(amount_scale=1000), connection_factory=factory)
        provider._period_window = provider._period_window_for("today", date(2026, 6, 18))
        transactions = await provider.fetch_next_batch()
        aggregator = AnalyticsAggregator()
        await aggregator.record_batch(transactions)
        snapshot = await aggregator.snapshot()

        self.assertEqual([transaction.amount for transaction in transactions], [125.0, 50.0])
        self.assertEqual(snapshot["kpis"]["total_amount"], 175.0)
        self.assertEqual(snapshot["kpis"]["amount_unit"], "TND")

    async def test_normalizes_transfer_amounts_from_millimes_to_tnd(self) -> None:
        def factory(_connection_string: str) -> FakeConnection:
            return FakeConnection([], transfer_row=(2, 342370))

        provider = SqlServerReplayProvider(Settings(amount_scale=1000, amount_unit="TND"), connection_factory=factory)
        provider._period_window = provider._period_window_for("today", date(2026, 6, 18))

        amount, count = await provider.transfer_summary()

        self.assertEqual(count, 2)
        self.assertEqual(amount, 342.37)

    async def test_expected_period_metrics_use_predefined_aggregate_queries(self) -> None:
        connections: list[FakeConnection] = []

        def factory(_connection_string: str) -> FakeConnection:
            connection = FakeConnection(
                [],
                expected_row=(3, 175000, 1, 1, 2, 0, 1),
                status_rows=[
                    ("Transaction autorisee", 1),
                    ("Transaction non autorisee", 1),
                    ("EtatMystere", 1),
                ],
            )
            connections.append(connection)
            return connection

        provider = SqlServerReplayProvider(Settings(amount_scale=1000), connection_factory=factory)
        provider._period_window = provider._period_window_for("today", date(2026, 6, 18))

        metrics = await provider.expected_period_metrics()

        self.assertEqual(metrics.total_transactions, 3)
        self.assertEqual(metrics.total_amount, 175.0)
        self.assertEqual(metrics.refused, 1)
        self.assertEqual(metrics.active_terminals, 2)
        self.assertEqual(metrics.invalid_amount_rows, 1)
        self.assertEqual(metrics.status_distribution_raw["EtatMystere"], 1)
        self.assertEqual(metrics.status_classification["unknown"], 1)
        queries = [query for query, _params in connections[0].executed]
        self.assertTrue(any("COUNT_BIG(*) AS total_transactions" in query for query in queries))
        self.assertTrue(any("GROUP BY COALESCE" in query for query in queries))
        self.assertTrue(all("SELECT *" not in query for query in queries))

    async def test_connection_check_uses_windows_authentication_string(self) -> None:
        captured: list[str] = []

        def factory(connection_string: str) -> FakeConnection:
            captured.append(connection_string)
            return FakeConnection([])

        provider = SqlServerReplayProvider(Settings(sqlserver_host="."), connection_factory=factory)
        await provider.connect()
        self.assertTrue(provider.available)
        self.assertIn("Trusted_Connection=yes", captured[0])
        self.assertIn("Encrypt=yes", captured[0])
        self.assertIn("DATABASE=portfolio_demo", captured[0])

    def test_parses_compact_demo_date_and_time_fields(self) -> None:
        self.assertEqual(SqlServerReplayProvider._as_date("240402").isoformat(), "2024-04-02")
        self.assertEqual(SqlServerReplayProvider._as_time("160119").isoformat(), "16:01:19")

    def test_calculates_windows_from_the_maximum_available_date(self) -> None:
        reference = date(2026, 5, 12)
        expected = {
            "today": (date(2026, 5, 12), date(2026, 5, 12)),
            "yesterday": (date(2026, 5, 11), date(2026, 5, 11)),
            "7d": (date(2026, 5, 6), date(2026, 5, 12)),
            "30d": (date(2026, 4, 13), date(2026, 5, 12)),
            "quarter": (date(2026, 4, 1), date(2026, 5, 12)),
            "year": (date(2026, 1, 1), date(2026, 5, 12)),
        }
        for period, dates in expected.items():
            window = SqlServerReplayProvider._period_window_for(period, reference)
            self.assertEqual((window.start_date, window.end_date), dates)
