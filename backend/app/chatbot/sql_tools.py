from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
import logging
from typing import Any

from app.services.sqlserver_replay_provider import PeriodKey, PeriodWindow, VALID_PERIODS

logger = logging.getLogger(__name__)

MAX_LIMIT = 20
TRANSACTION_TABLE = "dbo.demo_transactions"
TRANSACTION_DATE_COLUMN = "transaction_date"
TRANSACTION_STATUS_EXPR = "LOWER(COALESCE(TRY_CONVERT(NVARCHAR(255), transaction_status), N''))"
FORBIDDEN_FIELDS = {
    "RIB",
    "PAN",
    "CardMask",
    "CardBin",
    "Track2",
    "PIN",
    "EncryptedPIN",
    "OTP",
    "password",
    "BDK",
    "IPEK",
    "TMK",
    "WK",
    "ZMK",
    "CardholderName",
    "email",
    "msisdn",
}

QUERY_REGISTRY = {
    "top_merchants": """
        SELECT TOP ({limit})
            COALESCE(NULLIF(LTRIM(RTRIM(merchant_name)), ''), CONCAT('Commerçant #', CAST(merchant_id AS VARCHAR(50)))) AS merchant_name,
            COUNT_BIG(*) AS transactions,
            SUM(CASE WHEN {status_expr} LIKE N'%refus%' OR {status_expr} LIKE N'%non autor%' THEN 1 ELSE 0 END) AS refused,
            SUM(CASE WHEN {status_expr} LIKE N'%non about%' THEN 1 ELSE 0 END) AS non_completed
        FROM dbo.demo_transactions
        WHERE transaction_date BETWEEN ? AND ?
        GROUP BY COALESCE(NULLIF(LTRIM(RTRIM(merchant_name)), ''), CONCAT('Commerçant #', CAST(merchant_id AS VARCHAR(50))))
        ORDER BY transactions DESC, refused DESC;
    """,
    "top_tpe": """
        SELECT TOP ({limit})
            COALESCE(NULLIF(LTRIM(RTRIM(terminal_id)), ''), NULLIF(LTRIM(RTRIM(merchant_terminal_id)), ''), 'N/A') AS terminal_id,
            COALESCE(NULLIF(LTRIM(RTRIM(merchant_name)), ''), CONCAT('Commerçant #', CAST(merchant_id AS VARCHAR(50)))) AS merchant_name,
            COUNT_BIG(*) AS transactions,
            SUM(CASE WHEN {status_expr} LIKE N'%refus%' OR {status_expr} LIKE N'%non autor%' THEN 1 ELSE 0 END) AS refused,
            SUM(CASE WHEN TRY_CONVERT(float, processing_time_ms) > 1000 THEN 1 ELSE 0 END) AS slow
        FROM dbo.demo_transactions
        WHERE transaction_date BETWEEN ? AND ?
        GROUP BY COALESCE(NULLIF(LTRIM(RTRIM(terminal_id)), ''), NULLIF(LTRIM(RTRIM(merchant_terminal_id)), ''), 'N/A'),
                 COALESCE(NULLIF(LTRIM(RTRIM(merchant_name)), ''), CONCAT('Commerçant #', CAST(merchant_id AS VARCHAR(50))))
        ORDER BY refused DESC, transactions DESC;
    """,
    "incidents_by_hour": """
        SELECT
            LEFT(transaction_time, 2) AS hour,
            COUNT_BIG(*) AS transactions,
            SUM(CASE WHEN {status_expr} LIKE N'%refus%' OR {status_expr} LIKE N'%non autor%' THEN 1 ELSE 0 END) AS refused
        FROM dbo.demo_transactions
        WHERE transaction_date BETWEEN ? AND ?
          AND transaction_time IS NOT NULL
        GROUP BY LEFT(transaction_time, 2)
        ORDER BY hour;
    """,
    "search_merchant": """
        SELECT TOP ({limit})
            CAST(merchant_id AS VARCHAR(50)) AS merchant_id,
            COALESCE(NULLIF(LTRIM(RTRIM(merchant_name)), ''), CONCAT('Commerçant #', CAST(merchant_id AS VARCHAR(50)))) AS merchant_name,
            COUNT_BIG(*) AS transactions
        FROM dbo.demo_transactions
        WHERE (merchant_name LIKE ? OR CAST(merchant_id AS VARCHAR(50)) LIKE ?)
        GROUP BY CAST(merchant_id AS VARCHAR(50)),
                 COALESCE(NULLIF(LTRIM(RTRIM(merchant_name)), ''), CONCAT('Commerçant #', CAST(merchant_id AS VARCHAR(50))))
        ORDER BY transactions DESC;
    """,
}


class ChatbotSqlTools:
    """Whitelisted data tools. User text is never converted to executable SQL."""

    def __init__(self, engine: Any, provider: Any | None = None, health_provider: Any | None = None) -> None:
        self._engine = engine
        self._provider = provider
        self._health_provider = health_provider

    async def get_current_snapshot(self, period: PeriodKey | None = None) -> dict[str, Any]:
        return await self._engine.synchronized_snapshot(period=period)

    async def get_kpi_summary(self, period: PeriodKey | None = None) -> dict[str, Any]:
        snapshot = await self.get_current_snapshot(period)
        return {"snapshot": snapshot, **snapshot.get("kpis", {})}

    async def get_dashboard_snapshot(self, period: PeriodKey | None = None) -> dict[str, Any]:
        return await self.get_current_snapshot(period)

    async def get_transactions_last_n_days(self, days: int) -> dict[str, Any]:
        safe_days = max(1, min(int(days or 1), 30))
        if self._can_query_sql():
            return await asyncio.to_thread(self._transactions_last_n_days_sync, safe_days)
        return self._sql_unavailable_result("", "", "provider_missing", {"days": safe_days})

    async def get_transactions_between_dates(self, start_date: str, end_date: str) -> dict[str, Any]:
        if self._can_query_sql():
            return await asyncio.to_thread(self._transactions_between_dates_sync, start_date, end_date)
        return self._sql_unavailable_result(start_date, end_date, "provider_missing")

    async def get_tpe_count_by_period(self, start_date: str, end_date: str) -> dict[str, Any]:
        if self._can_query_sql():
            return await asyncio.to_thread(self._tpe_count_between_dates_sync, start_date, end_date)
        return self._sql_unavailable_result(start_date, end_date, "provider_missing", {"metric": "distinct_count", "object": "tpe"})

    async def compare_periods(self, left: PeriodKey, right: PeriodKey) -> dict[str, Any]:
        if left not in VALID_PERIODS:
            left = "today"
        if right not in VALID_PERIODS:
            right = "yesterday"
        left_summary = await self.get_kpi_summary(left)
        right_summary = await self.get_kpi_summary(right)
        return {
            "left": self._small_kpi(left, left_summary),
            "right": self._small_kpi(right, right_summary),
            "delta": {
                "transactions": int(left_summary.get("total_transactions") or 0) - int(right_summary.get("total_transactions") or 0),
                "refusal_rate": round(float(left_summary.get("refusal_rate") or 0) - float(right_summary.get("refusal_rate") or 0), 2),
                "success_rate": round(float(left_summary.get("success_rate") or 0) - float(right_summary.get("success_rate") or 0), 2),
            },
        }

    async def compare_date_ranges(
        self,
        left_start: str,
        left_end: str,
        right_start: str,
        right_end: str,
        left_label: str = "left",
        right_label: str = "right",
    ) -> dict[str, Any]:
        if not self._can_query_sql():
            return {
                "source": "sql_unavailable",
                "source_quality": "connection_unavailable",
                "data_available": False,
                "message": "Connexion SQL Server indisponible.",
                "left": {"label": left_label, "start_date": left_start, "end_date": left_end},
                "right": {"label": right_label, "start_date": right_start, "end_date": right_end},
                "delta": {},
            }
        return await asyncio.to_thread(
            self._compare_date_ranges_sync,
            left_start,
            left_end,
            right_start,
            right_end,
            left_label,
            right_label,
        )

    async def get_top_merchants(self, period: PeriodKey | None = None, limit: int = 5, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
        if self._can_query_sql():
            return await self._run_period_query("top_merchants", period, limit, start_date, end_date)
        return self._sql_table_unavailable_result("get_top_merchants", "provider_missing")

    async def get_top_anomalies(self, period: PeriodKey | None = None, limit: int = 5) -> list[dict[str, Any]]:
        snapshot = await self.get_current_snapshot(period)
        return list(snapshot.get("top_anomalies", []))[: self._limit(limit)]

    async def get_top_tpe(self, period: PeriodKey | None = None, limit: int = 5, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
        if self._can_query_sql():
            return await self._run_period_query("top_tpe", period, limit, start_date, end_date)
        return self._sql_table_unavailable_result("get_top_tpe", "provider_missing")

    async def get_status_distribution(self, period: PeriodKey | None = None) -> list[dict[str, Any]]:
        snapshot = await self.get_current_snapshot(period)
        return list(snapshot.get("status_distribution", []))

    async def get_incidents_by_hour(self, period: PeriodKey | None = None, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
        if self._can_query_sql():
            return await self._run_period_query("incidents_by_hour", period, MAX_LIMIT, start_date, end_date)
        return self._sql_table_unavailable_result("get_incidents_by_hour", "provider_missing")

    async def get_hourly_incidents(self, period: PeriodKey | None = None, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
        return await self.get_incidents_by_hour(period, start_date, end_date)

    async def get_affiliation_summary(self) -> dict[str, Any]:
        snapshot = await self.get_current_snapshot(None)
        kpis = snapshot.get("kpis", {})
        return {
            "affiliations_remaining": kpis.get("affiliations_remaining"),
            "affiliations_total": kpis.get("affiliations_total", 0),
            "affiliations_reservees": kpis.get("affiliations_reservees", 0),
            "affiliations_affectees": kpis.get("affiliations_affectees", 0),
            "projection": True,
        }

    async def get_affiliations(self) -> dict[str, Any]:
        return await self.get_affiliation_summary()

    async def get_refusal_analysis(self, period: PeriodKey | None = None) -> dict[str, Any]:
        return {
            "kpi": await self.get_kpi_summary(period),
            "hourly_incidents": await self.get_incidents_by_hour(period),
            "top_anomalies": await self.get_top_anomalies(period, 5),
            "top_tpe": await self.get_top_tpe(period, 5),
        }

    async def get_success_analysis(self, period: PeriodKey | None = None) -> dict[str, Any]:
        kpis = await self.get_kpi_summary(period)
        total = int(kpis.get("total_transactions") or 0)
        return {
            "total_transactions": total,
            "success_rate": kpis.get("success_rate"),
            "non_completed_transactions": kpis.get("non_completed_transactions"),
            "approved_estimate": round(total * float(kpis.get("success_rate") or 0) / 100),
            "period": period or "today",
        }

    async def get_risk_summary(self, period: PeriodKey | None = None) -> dict[str, Any]:
        snapshot = await self.get_current_snapshot(period)
        return {
            "global_risk_score": snapshot.get("kpis", {}).get("global_risk_score"),
            "top_anomalies": list(snapshot.get("top_anomalies", []))[:5],
            "active_alerts": list(snapshot.get("active_alerts", []))[:5],
            "period": period or "today",
        }

    async def get_merchant_details(self, merchant_name_or_id: str, period: PeriodKey | None = None) -> dict[str, Any]:
        snapshot = await self.get_current_snapshot(period)
        query = merchant_name_or_id.lower()
        rows = [
            row for row in snapshot.get("top_merchants", [])
            if query in str(row.get("merchant_name") or row.get("name") or "").lower()
        ]
        anomalies = [
            row for row in snapshot.get("top_anomalies", [])
            if query in str(row.get("merchant_name") or row.get("name") or "").lower()
        ]
        return {"matches": rows[:5], "anomalies": anomalies[:5]}

    async def search_merchant(self, query: str, limit: int = 5) -> dict[str, Any]:
        if self._can_query_sql():
            return await asyncio.to_thread(self._run_search_merchant, query, self._limit(limit))
        return self._sql_table_unavailable_result("search_merchant", "provider_missing")

    async def explain_metric(self, metric_name: str) -> dict[str, str]:
        metric = metric_name.lower()
        definitions = {
            "refusal_rate": "refused transactions / total transactions * 100",
            "success_rate": "approved transactions / total transactions * 100",
            "affiliations": "initial SQL stock plus deterministic business projection during replay",
            "active_terminals": "distinct terminal identifiers observed in the replayed period",
        }
        return {"metric": metric_name, "definition": definitions.get(metric, "Metric definition is not available.")}

    def _can_query_sql(self) -> bool:
        return bool(
            self._provider
            and hasattr(self._provider, "open_connection")
            and hasattr(self._provider, "period_window_for")
            and hasattr(self._provider, "reference_date")
        )

    async def _run_period_query(self, query_name: str, period: PeriodKey | None, limit: int, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(self._run_period_query_sync, query_name, period or "today", self._limit(limit), start_date, end_date)

    def _run_period_query_sync(self, query_name: str, period: PeriodKey, limit: int, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
        if period not in VALID_PERIODS:
            period = "today"
        query_template = QUERY_REGISTRY[query_name]
        query = query_template.format(limit=limit, status_expr=TRANSACTION_STATUS_EXPR)
        self._assert_safe_query(query)
        connection = None
        window = None
        start_param = None
        end_param = None
        try:
            window = self._window_from_dates(start_date, end_date) if start_date and end_date else self._period_window(period)
            start_param = window.start_yymmdd
            end_param = window.end_yymmdd
            connection = self._provider.open_connection()
            cursor = connection.cursor()
            self._set_cursor_timeout(cursor, 5)
            cursor.execute(query, start_param, end_param)
            columns = [column[0] for column in cursor.description]
            rows = [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
            cursor.close()
            return self._sql_rows_result(query_name, rows, start_param, end_param, window)
        except Exception as exc:
            logger.warning(
                "chatbot_sql_tool_failed tool=%s table=%s date_column=%s start_param=%s end_param=%s error=%s",
                query_name,
                TRANSACTION_TABLE,
                TRANSACTION_DATE_COLUMN,
                start_param,
                end_param,
                f"{type(exc).__name__}: {exc}",
                exc_info=True,
            )
            return self._sql_table_error_result(query_name, f"{type(exc).__name__}: {exc}", start_param, end_param)
        finally:
            if connection is not None:
                connection.close()

    def _run_search_merchant(self, query: str, limit: int) -> dict[str, Any]:
        sql = QUERY_REGISTRY["search_merchant"].format(limit=limit)
        self._assert_safe_query(sql)
        connection = None
        like = f"%{query[:80]}%"
        try:
            connection = self._provider.open_connection()
            cursor = connection.cursor()
            self._set_cursor_timeout(cursor, 5)
            cursor.execute(sql, like, like)
            columns = [column[0] for column in cursor.description]
            rows = [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
            cursor.close()
            result = self._sql_rows_result("search_merchant", rows, None, None, None)
            result["query"] = query[:80]
            result["params"] = [like, like]
            return result
        except Exception as exc:
            logger.warning("chatbot_search_merchant_failed query=%s error=%s", query[:80], f"{type(exc).__name__}: {exc}", exc_info=True)
            return self._sql_table_error_result("search_merchant", f"{type(exc).__name__}: {exc}", None, None)
        finally:
            if connection is not None:
                connection.close()

    def _transactions_last_n_days_sync(self, days: int) -> dict[str, Any]:
        reference = self._reference_date()
        start = reference - timedelta(days=days - 1)
        return self._transactions_between_window(start, reference, {"days": days})

    def _transactions_between_dates_sync(self, start_date: str, end_date: str) -> dict[str, Any]:
        try:
            start = date.fromisoformat(start_date[:10])
            end = date.fromisoformat(end_date[:10])
        except ValueError:
            return {
                "start_date": start_date,
                "end_date": end_date,
                "transactions": 0,
                "refused": 0,
                "non_completed": 0,
                "refusal_rate": 0,
                "source": "invalid_date",
                "source_quality": "invalid",
                "data_available": False,
                "message": "Période invalide ou mal comprise.",
                "diagnostic": self._sql_diagnostic(start_date, end_date, exception="invalid_date"),
            }
        if end < start:
            start, end = end, start
        if (end - start).days > 366:
            end = start + timedelta(days=366)
        return self._transactions_between_window(start, end, {})

    def _compare_date_ranges_sync(
        self,
        left_start: str,
        left_end: str,
        right_start: str,
        right_end: str,
        left_label: str,
        right_label: str,
    ) -> dict[str, Any]:
        try:
            left_start_date = date.fromisoformat(left_start[:10])
            left_end_date = date.fromisoformat(left_end[:10])
            right_start_date = date.fromisoformat(right_start[:10])
            right_end_date = date.fromisoformat(right_end[:10])
        except ValueError:
            return {"source": "invalid_date", "data_available": False, "message": "Période invalide ou mal comprise.", "left": {}, "right": {}, "delta": {}}
        left = self._transactions_between_window(left_start_date, left_end_date, {})
        right = self._transactions_between_window(right_start_date, right_end_date, {})
        left_summary = self._range_summary(left_label, left)
        right_summary = self._range_summary(right_label, right)
        return {
            "source": "sql" if left.get("source") == "sql" and right.get("source") == "sql" else left.get("source") or right.get("source"),
            "source_quality": "controlled_sql",
            "data_available": bool(left.get("data_available") or right.get("data_available")),
            "left": left_summary,
            "right": right_summary,
            "delta": {
                "transactions": int(left_summary.get("transactions") or 0) - int(right_summary.get("transactions") or 0),
                "refused": int(left_summary.get("refused") or 0) - int(right_summary.get("refused") or 0),
                "refusal_rate": round(float(left_summary.get("refusal_rate") or 0) - float(right_summary.get("refusal_rate") or 0), 2),
            },
        }

    @staticmethod
    def _range_summary(label: str, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "label": label,
            "start_date": data.get("start_date"),
            "end_date": data.get("end_date"),
            "transactions": int(data.get("transactions") or 0),
            "refused": int(data.get("refused") or 0),
            "non_completed": int(data.get("non_completed") or 0),
            "authorized_count": int(data.get("authorized_count") or 0),
            "completed_success_count": int(data.get("completed_success_count") or 0),
            "refusal_rate": float(data.get("refusal_rate") or 0),
            "success_rate": float(data.get("success_rate") or 0),
            "data_available": bool(data.get("data_available")),
        }

    def _tpe_count_between_dates_sync(self, start_date: str, end_date: str) -> dict[str, Any]:
        try:
            start = date.fromisoformat(start_date[:10])
            end = date.fromisoformat(end_date[:10])
        except ValueError:
            return self._sql_error_result(start_date, end_date, "invalid_date", "", "", {"metric": "distinct_count", "object": "tpe"})
        if end < start:
            start, end = end, start
        query = """
            SELECT COUNT(DISTINCT COALESCE(NULLIF(LTRIM(RTRIM(terminal_id)), ''), NULLIF(LTRIM(RTRIM(merchant_terminal_id)), ''))) AS active_tpe
            FROM dbo.demo_transactions
            WHERE transaction_date BETWEEN ? AND ?
              AND COALESCE(NULLIF(LTRIM(RTRIM(terminal_id)), ''), NULLIF(LTRIM(RTRIM(merchant_terminal_id)), '')) IS NOT NULL;
        """
        self._assert_safe_query(query)
        connection = None
        start_yymmdd = start.strftime("%y%m%d")
        end_yymmdd = end.strftime("%y%m%d")
        try:
            connection = self._provider.open_connection()
            cursor = connection.cursor()
            self._set_cursor_timeout(cursor, 5)
            cursor.execute(query, start_yymmdd, end_yymmdd)
            row = cursor.fetchone()
            cursor.close()
            active_tpe = int(row[0] or 0) if row else 0
            return {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "metric": "distinct_count",
                "object": "tpe",
                "active_tpe": active_tpe,
                "transactions": active_tpe,
                "source": "sql",
                "source_quality": "controlled_sql",
                "data_available": active_tpe > 0,
                "message": "TPE trouvés pour cette période." if active_tpe else "Aucun TPE trouvé pour cette période.",
                "diagnostic": self._sql_diagnostic(
                    start.isoformat(),
                    end.isoformat(),
                    start_param=start_yymmdd,
                    end_param=end_yymmdd,
                    rows_count=active_tpe,
                ),
            }
        except Exception as exc:
            logger.warning(
                "chatbot_tpe_count_failed tool=get_tpe_count_by_period table=%s date_column=%s start_date=%s end_date=%s start_param=%s end_param=%s error=%s",
                TRANSACTION_TABLE,
                TRANSACTION_DATE_COLUMN,
                start.isoformat(),
                end.isoformat(),
                start_yymmdd,
                end_yymmdd,
                f"{type(exc).__name__}: {exc}",
                exc_info=True,
            )
            return self._sql_error_result(start.isoformat(), end.isoformat(), f"{type(exc).__name__}: {exc}", start_yymmdd, end_yymmdd, {"metric": "distinct_count", "object": "tpe"})
        finally:
            if connection is not None:
                connection.close()

    def _transactions_between_window(self, start: date, end: date, extra: dict[str, Any]) -> dict[str, Any]:
        query = """
            SELECT
                COUNT_BIG(*) AS transactions,
                SUM(CASE WHEN {status_expr} LIKE N'%refus%' OR {status_expr} LIKE N'%non autor%' THEN 1 ELSE 0 END) AS refused,
                SUM(CASE WHEN {status_expr} LIKE N'%non about%' THEN 1 ELSE 0 END) AS non_completed
            FROM dbo.demo_transactions
            WHERE transaction_date BETWEEN ? AND ?;
        """.format(status_expr=TRANSACTION_STATUS_EXPR)
        self._assert_safe_query(query)
        connection = None
        start_yymmdd = start.strftime("%y%m%d")
        end_yymmdd = end.strftime("%y%m%d")
        try:
            connection = self._provider.open_connection()
        except Exception as exc:
            logger.warning(
                "chatbot_sql_connection_unavailable tool=get_transactions_between_dates table=%s date_column=%s start_date=%s end_date=%s start_param=%s end_param=%s error=%s",
                TRANSACTION_TABLE,
                TRANSACTION_DATE_COLUMN,
                start.isoformat(),
                end.isoformat(),
                start_yymmdd,
                end_yymmdd,
                f"{type(exc).__name__}: {exc}",
                exc_info=True,
            )
            return self._sql_unavailable_result(start.isoformat(), end.isoformat(), f"{type(exc).__name__}: {exc}", extra)

        try:
            cursor = connection.cursor()
            self._set_cursor_timeout(cursor, 5)
            cursor.execute(query, start_yymmdd, end_yymmdd)
            row = cursor.fetchone()
            cursor.close()
            transactions = int(row[0] or 0) if row else 0
            refused = int(row[1] or 0) if row else 0
            non_completed = int(row[2] or 0) if row else 0
            authorized_count = max(0, transactions - refused)
            completed_success_count = max(0, transactions - refused - non_completed)
            return {
                **extra,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "transactions": transactions,
                "refused": refused,
                "non_completed": non_completed,
                "authorized_count": authorized_count,
                "completed_success_count": completed_success_count,
                "refusal_rate": round(refused / transactions * 100, 2) if transactions else 0,
                "authorization_rate": round(authorized_count / transactions * 100, 2) if transactions else 0,
                "success_rate": round(completed_success_count / transactions * 100, 2) if transactions else 0,
                "source": "sql",
                "source_quality": "controlled_sql",
                "data_available": transactions > 0,
                "message": "Pas de transaction trouvée pour cette période." if transactions == 0 else "Transactions trouvées pour cette période.",
                "diagnostic": self._sql_diagnostic(
                    start.isoformat(),
                    end.isoformat(),
                    start_param=start_yymmdd,
                    end_param=end_yymmdd,
                    rows_count=transactions,
                ),
            }
        except Exception as exc:
            logger.warning(
                "chatbot_transaction_count_failed tool=get_transactions_between_dates table=%s date_column=%s start_date=%s end_date=%s start_param=%s end_param=%s error=%s",
                TRANSACTION_TABLE,
                TRANSACTION_DATE_COLUMN,
                start.isoformat(),
                end.isoformat(),
                start_yymmdd,
                end_yymmdd,
                f"{type(exc).__name__}: {exc}",
                exc_info=True,
            )
            return self._sql_error_result(start.isoformat(), end.isoformat(), f"{type(exc).__name__}: {exc}", start_yymmdd, end_yymmdd, extra)
        finally:
            if connection is not None:
                connection.close()

    def _sql_unavailable_result(self, start_date: str, end_date: str, reason: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            **(extra or {}),
            "start_date": start_date,
            "end_date": end_date,
            "transactions": 0,
            "refused": 0,
            "non_completed": 0,
            "refusal_rate": 0,
            "source": "sql_unavailable",
            "source_quality": "connection_unavailable",
            "data_available": False,
            "message": "Connexion SQL Server indisponible.",
            "diagnostic": self._sql_diagnostic(start_date, end_date, exception=reason),
        }

    def _sql_error_result(self, start_date: str, end_date: str, error: str, start_param: str, end_param: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            **(extra or {}),
            "start_date": start_date,
            "end_date": end_date,
            "transactions": 0,
            "refused": 0,
            "non_completed": 0,
            "refusal_rate": 0,
            "source": "sql_error",
            "source_quality": "controlled_sql_error",
            "data_available": False,
            "message": "Erreur interne de l'outil SQL contrôlé.",
            "diagnostic": self._sql_diagnostic(start_date, end_date, start_param=start_param, end_param=end_param, exception=error),
        }

    @staticmethod
    def _sql_diagnostic(
        start_date: str,
        end_date: str,
        *,
        start_param: str | None = None,
        end_param: str | None = None,
        rows_count: int | None = None,
        exception: str | None = None,
    ) -> dict[str, Any]:
        return {
            "tool": "get_transactions_between_dates",
            "table": TRANSACTION_TABLE,
            "date_column": TRANSACTION_DATE_COLUMN,
            "start_date": start_date,
            "end_date": end_date,
            "start_param": start_param,
            "end_param": end_param,
            "exception": exception,
            "rows_count": rows_count,
        }

    def _window_from_dates(self, start_date: str, end_date: str) -> PeriodWindow:
        start = date.fromisoformat(start_date[:10])
        end = date.fromisoformat(end_date[:10])
        if end < start:
            start, end = end, start
        return PeriodWindow("custom", end, start, end)  # type: ignore[arg-type]

    @staticmethod
    def _set_cursor_timeout(cursor: Any, seconds: int) -> None:
        try:
            setattr(cursor, "timeout", seconds)
        except (AttributeError, TypeError):
            logger.debug("chatbot_sql_cursor_timeout_unsupported cursor=%s", type(cursor).__name__)

    @staticmethod
    def _sql_rows_result(tool: str, rows: list[dict[str, Any]], start_param: str | None, end_param: str | None, window: PeriodWindow | None) -> dict[str, Any]:
        return {
            "rows": rows,
            "rows_count": len(rows),
            "source": "sql",
            "source_quality": "controlled_sql",
            "data_available": bool(rows),
            "message": "Données SQL trouvées." if rows else "Aucune donnée SQL trouvée pour cette période.",
            "diagnostic": {
                "tool": tool,
                "table": TRANSACTION_TABLE,
                "date_column": TRANSACTION_DATE_COLUMN,
                "start_date": window.start_date.isoformat() if window else None,
                "end_date": window.end_date.isoformat() if window else None,
                "start_param": start_param,
                "end_param": end_param,
                "exception": None,
                "rows_count": len(rows),
            },
        }

    @staticmethod
    def _sql_table_error_result(tool: str, error: str, start_param: str | None, end_param: str | None) -> dict[str, Any]:
        return {
            "rows": [],
            "rows_count": 0,
            "source": "sql_error",
            "source_quality": "controlled_sql_error",
            "data_available": False,
            "message": "Erreur interne de l'outil SQL contrôlé.",
            "diagnostic": {
                "tool": tool,
                "table": TRANSACTION_TABLE,
                "date_column": TRANSACTION_DATE_COLUMN,
                "start_param": start_param,
                "end_param": end_param,
                "exception": error,
                "rows_count": None,
            },
        }

    @staticmethod
    def _sql_table_unavailable_result(tool: str, reason: str) -> dict[str, Any]:
        return {
            "rows": [],
            "rows_count": 0,
            "source": "sql_unavailable",
            "source_quality": "connection_unavailable",
            "data_available": False,
            "message": "Connexion SQL Server indisponible.",
            "diagnostic": {
                "tool": tool,
                "table": TRANSACTION_TABLE,
                "date_column": TRANSACTION_DATE_COLUMN,
                "exception": reason,
                "rows_count": None,
            },
        }

    def _reference_date(self) -> date:
        return self._provider.reference_date("today")

    def _period_window(self, period: PeriodKey) -> PeriodWindow:
        return self._provider.period_window_for(period, self._provider.reference_date(period))

    @staticmethod
    def _limit(limit: int) -> int:
        return max(1, min(int(limit or 5), MAX_LIMIT))

    @staticmethod
    def _small_kpi(period: str, summary: dict[str, Any]) -> dict[str, Any]:
        return {
            "period": period,
            "transactions": summary.get("total_transactions", 0),
            "refusal_rate": summary.get("refusal_rate", 0),
            "success_rate": summary.get("success_rate", 0),
            "non_completed": summary.get("non_completed_transactions", 0),
        }

    @staticmethod
    def _assert_safe_query(query: str) -> None:
        lowered = query.lower()
        forbidden_sql = (" delete ", " update ", " insert ", " drop ", " alter ", " merge ", " truncate ", " select *")
        if any(token in f" {lowered} " for token in forbidden_sql):
            raise ValueError("Unsafe SQL tool query.")
        selected_block = lowered.split(" from ", 1)[0]
        if any(field.lower() in selected_block for field in FORBIDDEN_FIELDS):
            raise ValueError("Sensitive field selected by SQL tool.")
