"""Bounded SQL Server reader for historical transaction replay.

The provider never selects cardholder, account, PIN or authentication fields,
and never materialises the full ``dbo.demo_transactions`` table in application memory.
"""

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import logging
from typing import Any, Callable

from app.config import Settings
from app.models.transaction import NormalizedTransaction, PaymentTransaction
from app.services.data_reconciliation import ExpectedPeriodMetrics, classify_status_distribution
from app.services.money import millimes_to_tnd

logger = logging.getLogger(__name__)

PeriodKey = str
VALID_PERIODS = {"today", "yesterday", "7d", "30d", "quarter", "year"}


@dataclass(frozen=True, slots=True)
class PeriodWindow:
    period: PeriodKey
    reference_date: date
    start_date: date
    end_date: date

    @property
    def start_yymmdd(self) -> str:
        return self.start_date.strftime("%y%m%d")

    @property
    def end_yymmdd(self) -> str:
        return self.end_date.strftime("%y%m%d")

    @property
    def end_exclusive(self) -> datetime:
        return datetime.combine(self.end_date + timedelta(days=1), time.min)


@dataclass(slots=True)
class ReplayCursor:
    # portfolio_demo stores these fields as compact strings (YYMMDD / HHMMSS).  Keep
    # the source representation in the cursor so SQL Server comparisons remain
    # lexical and deterministic for that historical schema.
    date_value: date | str = "000000"
    time_value: time | str = "000000"
    transaction_id: Any = 0

    def label(self) -> str:
        date_label = self.date_value.isoformat() if isinstance(self.date_value, date) else self.date_value
        time_label = self.time_value.isoformat() if isinstance(self.time_value, time) else self.time_value
        return f"{date_label} {time_label} / {self.transaction_id}"


class SqlServerReplayProvider:
    """Read ``demo_transactions`` in ordered batches using Windows Authentication."""

    name = "sqlserver_replay"

    def __init__(
        self,
        settings: Settings,
        connection_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._connection_factory = connection_factory
        self._cursor = ReplayCursor()
        self._period_window: PeriodWindow | None = None
        self._available = False
        self._last_error: str | None = None

    @property
    def cursor_label(self) -> str:
        return self._cursor.label()

    @property
    def available(self) -> bool:
        return self._available

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def period_window(self) -> PeriodWindow | None:
        return self._period_window

    @property
    def amount_metadata(self) -> dict[str, Any]:
        return {
            "amount_unit": self._settings.amount_unit,
        }

    async def connect(self) -> None:
        await asyncio.to_thread(self._check_connection)

    async def close(self) -> None:
        return None

    async def reset(self) -> None:
        self._cursor = ReplayCursor()

    async def configure_period(self, period: PeriodKey) -> PeriodWindow:
        return await asyncio.to_thread(self._configure_period, period)

    async def configure_date(self, target_date: date) -> PeriodWindow:
        return await asyncio.to_thread(self._configure_date, target_date)

    def configure_period_sync(self, period: PeriodKey) -> PeriodWindow:
        """Synchronous variant for controlled tools already running in a worker thread."""
        return self._configure_period(period)

    def reference_date(self, fallback_period: PeriodKey = "today") -> date:
        if self._period_window is None:
            return self.configure_period_sync(fallback_period).reference_date
        return self._period_window.reference_date

    @staticmethod
    def period_window_for(period: PeriodKey, reference_date: date) -> PeriodWindow:
        return SqlServerReplayProvider._period_window_for(period, reference_date)

    def open_connection(self) -> Any:
        """Open a bounded SQL Server connection for whitelisted diagnostic tools."""
        return self._open_connection()

    async def fetch_next_batch(self, batch_size: int | None = None) -> list[PaymentTransaction]:
        return await asyncio.to_thread(self._fetch_next_batch, batch_size)

    async def transfer_summary(self) -> tuple[float, int]:
        return await asyncio.to_thread(self._transfer_summary, self._require_period_window())

    async def expected_period_metrics(self) -> ExpectedPeriodMetrics:
        return await asyncio.to_thread(self._expected_period_metrics, self._require_period_window())

    async def get_affiliation_summary(self, period: PeriodKey | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(self._affiliation_summary)

    async def diagnostics(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "database": self._settings.sqlserver_database,
            "host": self._settings.sqlserver_host,
            "cursor": self.cursor_label,
            "available": self._available,
            "last_error": self._last_error,
            "period": self._period_window.period if self._period_window else None,
            "reference_date": self._period_window.reference_date.isoformat() if self._period_window else None,
            "start_date": self._period_window.start_date.isoformat() if self._period_window else None,
            "end_date": self._period_window.end_date.isoformat() if self._period_window else None,
        }

    def _configure_period(self, period: PeriodKey) -> PeriodWindow:
        if period not in VALID_PERIODS:
            raise ValueError(f"Unsupported period: {period}")
        connection = None
        try:
            connection = self._open_connection()
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT MAX(transaction_date)
                FROM dbo.demo_transactions
                WHERE transaction_date IS NOT NULL
                  AND LEN(LTRIM(RTRIM(transaction_date))) = 6
                  AND transaction_date NOT LIKE '%[^0-9]%';
                """
            )
            row = cursor.fetchone()
            cursor.close()
            if not row or row[0] is None:
                raise RuntimeError("No valid transaction_date values are available.")
            reference_date = self._as_date(row[0])
            window = self._period_window_for(period, reference_date)
            self._period_window = window
            self._cursor = ReplayCursor()
            self._available = True
            self._last_error = None
            return window
        except Exception as exc:
            self._available = False
            self._last_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(f"SQL Server period configuration failed: {self._last_error}") from exc
        finally:
            if connection is not None:
                connection.close()

    def _configure_date(self, target_date: date) -> PeriodWindow:
        window = PeriodWindow(
            period=f"date:{target_date.isoformat()}",
            reference_date=target_date,
            start_date=target_date,
            end_date=target_date,
        )
        self._period_window = window
        self._cursor = ReplayCursor()
        self._available = True
        self._last_error = None
        return window

    @staticmethod
    def _period_window_for(period: PeriodKey, reference_date: date) -> PeriodWindow:
        if period == "today":
            start_date = end_date = reference_date
        elif period == "yesterday":
            start_date = end_date = reference_date - timedelta(days=1)
        elif period == "7d":
            start_date, end_date = reference_date - timedelta(days=6), reference_date
        elif period == "30d":
            start_date, end_date = reference_date - timedelta(days=29), reference_date
        elif period == "quarter":
            quarter_month = (reference_date.month - 1) // 3 * 3 + 1
            start_date, end_date = date(reference_date.year, quarter_month, 1), reference_date
        elif period == "year":
            start_date, end_date = date(reference_date.year, 1, 1), reference_date
        else:  # guarded by configure_period; retained for direct unit testing
            raise ValueError(f"Unsupported period: {period}")
        return PeriodWindow(period, reference_date, start_date, end_date)

    def _check_connection(self) -> None:
        connection = None
        try:
            connection = self._open_connection()
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            self._available = True
            self._last_error = None
        except Exception as exc:
            self._available = False
            self._last_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(f"SQL Server replay connection failed: {self._last_error}") from exc
        finally:
            if connection is not None:
                connection.close()

    def _fetch_next_batch(self, requested_batch_size: int | None = None) -> list[PaymentTransaction]:
        # TOP cannot be reliably parameterized across every ODBC/SQL Server
        # combination. The configuration validator limits it and we interpolate
        # that validated integer only; all cursor values remain parameters.
        batch_size = int(requested_batch_size or self._settings.replay_batch_size)
        if not 1 <= batch_size <= 5000:
            raise ValueError("REPLAY_BATCH_SIZE must be between 1 and 5000.")
        window = self._require_period_window()
        query = f"""
        SELECT TOP ({batch_size})
            t.transaction_id,
            t.transaction_status,
            t.merchant_id,
            t.store_id,
            t.merchant_terminal_id,
            t.terminal_id,
            t.merchant_name,
            t.response_code,
            t.Amount,
            t.transaction_date,
            t.transaction_time,
            t.processing_time_ms,
            t.risk_processing_time_ms,
            t.risk_decision,
            t.transfer_issued
        FROM dbo.demo_transactions AS t
        WHERE
            t.transaction_date BETWEEN ? AND ?
            AND (
                t.transaction_date > ?
                OR (t.transaction_date = ? AND t.transaction_time > ?)
                OR (
                    t.transaction_date = ?
                    AND t.transaction_time = ?
                    AND t.transaction_id > ?
                )
            )
        ORDER BY t.transaction_date, t.transaction_time, t.transaction_id;
        """
        connection = None
        try:
            connection = self._open_connection()
            cursor = connection.cursor()
            cursor.execute(
                query,
                window.start_yymmdd,
                window.end_yymmdd,
                self._cursor.date_value,
                self._cursor.date_value,
                self._cursor.time_value,
                self._cursor.date_value,
                self._cursor.time_value,
                self._cursor.transaction_id,
            )
            columns = [column[0] for column in cursor.description]
            rows = [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
            cursor.close()
            rows = self._deduplicate_rows(rows)
            enrichments = self._fetch_enrichments(connection, rows)
            transactions = [
                self._normalize(row, enrichments.get(str(row.get("transaction_id")))).to_payment_transaction()
                for row in rows
            ]
            if rows:
                last = rows[-1]
                self._cursor = ReplayCursor(
                    last.get("transaction_date"),
                    last.get("transaction_time"),
                    last.get("transaction_id"),
                )
            self._available = True
            self._last_error = None
            return transactions
        except Exception as exc:
            self._available = False
            self._last_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(f"SQL Server replay batch failed: {self._last_error}") from exc
        finally:
            if connection is not None:
                connection.close()

    def _fetch_enrichments(self, connection: Any, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Select at most one safe terminal row per transaction without joins.

        Merchant matching has precedence.  The lowest primary key is used when
        a directory key maps to more than one row, making the fallback stable.
        """
        merchant_lookup = self._enrichment_lookup(
            connection,
            "demo_terminal_directory_merchant_id",
            {value for row in rows if (value := self._as_optional_int(row.get("merchant_id"))) is not None},
        )
        magasin_lookup = self._enrichment_lookup(
            connection,
            "demo_terminal_directory_magasin_id",
            {value for row in rows if (value := self._as_optional_int(row.get("store_id"))) is not None},
        )

        selected: dict[str, dict[str, Any]] = {}
        for row in rows:
            merchant_id = self._as_optional_int(row.get("merchant_id"))
            magasin_id = self._as_optional_int(row.get("store_id"))
            enrichment = merchant_lookup.get(merchant_id) if merchant_id is not None else None
            if enrichment is None and magasin_id is not None:
                enrichment = magasin_lookup.get(magasin_id)
            if enrichment is not None:
                selected[str(row.get("transaction_id"))] = enrichment
        return selected

    @staticmethod
    def _deduplicate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        duplicates = 0
        for row in rows:
            transaction_id = str(row.get("transaction_id"))
            if transaction_id in seen:
                duplicates += 1
                continue
            seen.add(transaction_id)
            unique.append(row)
        if duplicates:
            logger.warning("sqlserver_replay_duplicate_batch_ids_deduplicated count=%s", duplicates)
        return unique

    def _enrichment_lookup(self, connection: Any, key_column: str, values: set[int]) -> dict[int, dict[str, Any]]:
        if not values:
            return {}
        if key_column not in {"demo_terminal_directory_merchant_id", "demo_terminal_directory_magasin_id"}:
            raise ValueError("Unsupported terminal enrichment key.")

        lookup: dict[int, dict[str, Any]] = {}
        # SQL Server limits a statement to 2,100 parameters.  Fast-forward
        # batches can contain 5,000 different IDs, so lookups are chunked.
        ordered_values = sorted(values)
        for offset in range(0, len(ordered_values), 2000):
            chunk = ordered_values[offset : offset + 2000]
            placeholders = ", ".join("?" for _ in chunk)
            cursor = connection.cursor()
            cursor.execute(
                f"""
                SELECT
                    demo_terminal_directory_id,
                    demo_terminal_directory_merchant_id,
                    demo_terminal_directory_magasin_id,
                    demo_terminal_directory_name AS merchant_name,
                    demo_terminal_directory_status AS terminal_status,
                    demo_terminal_directory_Softpos AS softpos,
                    gw_terminal_connectivity AS connectivity
                FROM dbo.demo_terminal_directory
                WHERE {key_column} IN ({placeholders})
                ORDER BY demo_terminal_directory_id ASC;
                """,
                *chunk,
            )
            columns = [column[0] for column in cursor.description]
            rows = [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
            cursor.close()
            for row in rows:
                key = self._as_optional_int(row.get(key_column))
                if key is not None:
                    lookup.setdefault(key, row)
        return lookup

    def _transfer_summary(self, window: PeriodWindow) -> tuple[float, int]:
        connection = None
        try:
            connection = self._open_connection()
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT
                    COUNT(*) AS nb_virements,
                    SUM(COALESCE(
                        TRY_CAST(Amount AS FLOAT),
                        TRY_CAST(REPLACE(REPLACE(REPLACE(CAST(Amount AS VARCHAR(64)), CHAR(160), ''), ' ', ''), ',', '.') AS FLOAT)
                    ))
                        AS montant_total_virements
                FROM dbo.demo_transfers
                WHERE TRY_CONVERT(datetime2, [Date], 120) >= ?
                  AND TRY_CONVERT(datetime2, [Date], 120) < ?;
                """
                ,
                datetime.combine(window.start_date, time.min),
                window.end_exclusive,
            )
            row = cursor.fetchone()
            cursor.close()
            if not row:
                return 0.0, 0
            values = list(row)
            return millimes_to_tnd(self._as_float(values[1] if len(values) > 1 else 0), self._settings.amount_scale), int(values[0] or 0)
        finally:
            if connection is not None:
                connection.close()

    def _expected_period_metrics(self, window: PeriodWindow) -> ExpectedPeriodMetrics:
        connection = None
        try:
            connection = self._open_connection()
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT
                    COUNT_BIG(*) AS total_transactions,
                    SUM(COALESCE(
                        TRY_CAST(Amount AS FLOAT),
                        TRY_CAST(REPLACE(REPLACE(REPLACE(CAST(Amount AS VARCHAR(64)), CHAR(160), ''), ' ', ''), ',', '.') AS FLOAT)
                    )) AS total_amount,
                    SUM(CASE WHEN LOWER(LTRIM(RTRIM(COALESCE(transaction_status, '')))) LIKE '%non autor%'
                              OR LOWER(LTRIM(RTRIM(COALESCE(transaction_status, '')))) LIKE '%refus%'
                             THEN 1 ELSE 0 END) AS refused,
                    SUM(CASE WHEN LOWER(LTRIM(RTRIM(COALESCE(transaction_status, '')))) LIKE '%non about%'
                              OR LOWER(LTRIM(RTRIM(COALESCE(transaction_status, '')))) LIKE '%attente%'
                              OR LOWER(LTRIM(RTRIM(COALESCE(transaction_status, '')))) LIKE '%instance%'
                             THEN 1 ELSE 0 END) AS non_completed,
                    COUNT(DISTINCT COALESCE(NULLIF(LTRIM(RTRIM(CAST(terminal_id AS VARCHAR(64)))), ''),
                                            NULLIF(LTRIM(RTRIM(CAST(merchant_terminal_id AS VARCHAR(64)))), '')))
                        AS active_terminals,
                    SUM(CASE WHEN transaction_date IS NULL
                              OR LEN(LTRIM(RTRIM(CAST(transaction_date AS VARCHAR(32))))) <> 6
                              OR LTRIM(RTRIM(CAST(transaction_date AS VARCHAR(32)))) LIKE '%[^0-9]%'
                              OR transaction_time IS NULL
                              OR LEN(LTRIM(RTRIM(CAST(transaction_time AS VARCHAR(32))))) <> 6
                              OR LTRIM(RTRIM(CAST(transaction_time AS VARCHAR(32)))) LIKE '%[^0-9]%'
                              OR TRY_CONVERT(date, LTRIM(RTRIM(CAST(transaction_date AS VARCHAR(32)))), 12) IS NULL
                              OR TRY_CONVERT(time, STUFF(STUFF(LTRIM(RTRIM(CAST(transaction_time AS VARCHAR(32)))), 3, 0, ':'), 6, 0, ':')) IS NULL
                             THEN 1 ELSE 0 END) AS invalid_datetime_rows,
                    SUM(CASE WHEN Amount IS NULL
                              OR COALESCE(
                                  TRY_CAST(Amount AS FLOAT),
                                  TRY_CAST(REPLACE(REPLACE(REPLACE(CAST(Amount AS VARCHAR(64)), CHAR(160), ''), ' ', ''), ',', '.') AS FLOAT)
                              ) IS NULL
                             THEN 1 ELSE 0 END) AS invalid_amount_rows
                FROM dbo.demo_transactions
                WHERE transaction_date BETWEEN ? AND ?;
                """,
                window.start_yymmdd,
                window.end_yymmdd,
            )
            row = cursor.fetchone()
            cursor.close()

            status_cursor = connection.cursor()
            status_cursor.execute(
                """
                SELECT
                    COALESCE(NULLIF(LTRIM(RTRIM(CAST(transaction_status AS NVARCHAR(128)))), ''), N'<empty>')
                        AS raw_status,
                    COUNT_BIG(*) AS status_count
                FROM dbo.demo_transactions
                WHERE transaction_date BETWEEN ? AND ?
                GROUP BY COALESCE(NULLIF(LTRIM(RTRIM(CAST(transaction_status AS NVARCHAR(128)))), ''), N'<empty>');
                """,
                window.start_yymmdd,
                window.end_yymmdd,
            )
            raw_distribution = {str(status): int(count or 0) for status, count in status_cursor.fetchall()}
            status_cursor.close()
            classified, unknown = classify_status_distribution(raw_distribution)
            values = list(row or [])
            return ExpectedPeriodMetrics(
                total_transactions=int(values[0] or 0) if len(values) > 0 else 0,
                total_amount=millimes_to_tnd(self._as_float(values[1] if len(values) > 1 else 0), self._settings.amount_scale),
                refused=int(values[2] or 0) if len(values) > 2 else 0,
                non_completed=int(values[3] or 0) if len(values) > 3 else 0,
                active_terminals=int(values[4] or 0) if len(values) > 4 else 0,
                invalid_datetime_rows=int(values[5] or 0) if len(values) > 5 else 0,
                invalid_amount_rows=int(values[6] or 0) if len(values) > 6 else 0,
                status_distribution_raw=raw_distribution,
                status_classification=classified,
                unknown_status_values=unknown,
            )
        finally:
            if connection is not None:
                connection.close()

    def _affiliation_summary(self) -> dict[str, Any]:
        connection = None
        empty_summary = {
            "affiliations_total": 0,
            "affiliations_libres": None,
            "affiliations_reservees": 0,
            "affiliations_affectees": 0,
            "affiliation_demo_data": False,
        }
        try:
            connection = self._open_connection()
            cursor = connection.cursor()
            cursor.execute(
                """
                IF OBJECT_ID(N'dbo.demo_affiliation_inventory', N'U') IS NULL
                BEGIN
                    SELECT
                        CAST(0 AS BIGINT) AS affiliations_total,
                        CAST(NULL AS BIGINT) AS affiliations_libres,
                        CAST(0 AS BIGINT) AS affiliations_reservees,
                        CAST(0 AS BIGINT) AS affiliations_affectees,
                        CAST(0 AS BIT) AS affiliation_demo_data;
                    RETURN;
                END;

                SELECT
                    COUNT_BIG(*) AS affiliations_total,
                    SUM(CASE WHEN LOWER(LTRIM(RTRIM(status))) = 'libre' THEN 1 ELSE 0 END) AS affiliations_libres,
                    SUM(CASE WHEN LOWER(LTRIM(RTRIM(status))) IN ('reserve', 'réservé', 'reservee') THEN 1 ELSE 0 END) AS affiliations_reservees,
                    SUM(CASE WHEN LOWER(LTRIM(RTRIM(status))) = 'affecte' THEN 1 ELSE 0 END) AS affiliations_affectees,
                    CAST(
                        CASE WHEN EXISTS (
                            SELECT 1
                            FROM sys.extended_properties
                            WHERE major_id = OBJECT_ID(N'dbo.demo_affiliation_inventory')
                              AND minor_id = 0
                              AND name = N'PortfolioDemoData'
                              AND LOWER(CAST(value AS NVARCHAR(20))) = N'true'
                        ) THEN 1 ELSE 0 END
                    AS BIT) AS affiliation_demo_data
                FROM dbo.demo_affiliation_inventory;
                """
            )
            row = cursor.fetchone()
            cursor.close()
            if not row:
                return empty_summary
            values = list(row)
            total = int(values[0] or 0)
            return {
                "affiliations_total": total,
                "affiliations_libres": int(values[1]) if total and values[1] is not None else None,
                "affiliations_reservees": int(values[2] or 0),
                "affiliations_affectees": int(values[3] or 0),
                "affiliation_demo_data": bool(values[4]),
            }
        finally:
            if connection is not None:
                connection.close()

    def _open_connection(self) -> Any:
        connection_string = self._connection_string()
        if self._connection_factory:
            return self._connection_factory(connection_string)
        try:
            import pyodbc
        except ImportError as exc:  # pragma: no cover - exercised by deployment
            raise RuntimeError("pyodbc is required for DATA_SOURCE=sqlserver_replay.") from exc
        return pyodbc.connect(connection_string, timeout=8, autocommit=True)

    def _connection_string(self) -> str:
        parts = [
            f"DRIVER={{{self._settings.sqlserver_driver}}}",
            f"SERVER={self._settings.sqlserver_host}",
            f"DATABASE={self._settings.sqlserver_database}",
        ]
        if self._settings.sqlserver_auth.lower() == "windows":
            parts.append("Trusted_Connection=yes")
        parts.append(f"Encrypt={'yes' if self._settings.sqlserver_encrypt else 'no'}")
        if self._settings.sqlserver_trust_certificate:
            parts.append("TrustServerCertificate=yes")
        return ";".join(parts) + ";"

    def _require_period_window(self) -> PeriodWindow:
        if self._period_window is None:
            raise RuntimeError("Configure a replay period before fetching SQL Server transactions.")
        return self._period_window

    def _normalize(self, row: dict[str, Any], enrichment: dict[str, Any] | None = None) -> NormalizedTransaction:
        timestamp = datetime.combine(
            self._as_date(row.get("transaction_date")),
            self._as_time(row.get("transaction_time")),
            tzinfo=timezone.utc,
        )
        return NormalizedTransaction(
            id=str(row.get("transaction_id")),
            timestamp=timestamp,
            merchant_id=self._as_optional_str(row.get("merchant_id")),
            magasin_id=self._as_optional_str(row.get("store_id")),
            terminal_id=self._as_optional_str(row.get("terminal_id")),
            terminal_merchant_id=self._as_optional_str(row.get("merchant_terminal_id")),
            status=self._as_optional_str(row.get("transaction_status")) or "Inconnu",
            response_code=self._as_optional_str(row.get("response_code")),
            amount=millimes_to_tnd(self._as_float(row.get("Amount")), self._settings.amount_scale),
            processing_time_ms=self._as_optional_float(row.get("processing_time_ms")),
            fraud_time_ms=self._as_optional_float(row.get("risk_processing_time_ms")),
            fraud_decision=self._as_optional_str(row.get("risk_decision")),
            merchant_name=self._as_optional_str(row.get("merchant_name"))
            or (self._as_optional_str(enrichment.get("merchant_name")) if enrichment else None),
            terminal_status=self._as_optional_str(enrichment.get("terminal_status")) if enrichment else None,
            softpos=self._as_optional_bool(enrichment.get("softpos")) if enrichment else None,
            connectivity=self._as_optional_str(enrichment.get("connectivity")) if enrichment else None,
            virement_emis=bool(self._as_optional_bool(row.get("transfer_issued"))),
        )

    @staticmethod
    def _as_date(value: Any) -> date:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            compact = value.strip()
            if compact.isdigit() and len(compact) == 6:
                return datetime.strptime(compact, "%y%m%d").date()
            if compact.isdigit() and len(compact) == 8:
                return datetime.strptime(compact, "%Y%m%d").date()
            return datetime.fromisoformat(compact).date()
        raise ValueError("transaction_date is missing or invalid.")

    @staticmethod
    def _as_time(value: Any) -> time:
        if isinstance(value, datetime):
            return value.time().replace(tzinfo=None)
        if isinstance(value, time):
            return value.replace(tzinfo=None)
        if isinstance(value, str):
            compact = value.strip()
            if compact.isdigit() and len(compact) == 6:
                return datetime.strptime(compact, "%H%M%S").time()
            return time.fromisoformat(compact)
        raise ValueError("transaction_time is missing or invalid.")

    @staticmethod
    def _as_optional_str(value: Any) -> str | None:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @staticmethod
    def _as_optional_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_float(value: Any) -> float:
        if value is None:
            return 0.0
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, str):
            return float(value.strip().replace("\xa0", "").replace(" ", "").replace(",", "."))
        return float(value)

    @classmethod
    def _as_optional_float(cls, value: Any) -> float | None:
        return None if value is None or str(value).strip() == "" else cls._as_float(value)

    @staticmethod
    def _as_optional_bool(value: Any) -> bool | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "oui", "y"}
        return bool(value)
