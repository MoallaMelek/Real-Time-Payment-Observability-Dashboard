import asyncio
import os
import unittest

from app.chatbot.sql_tools import ChatbotSqlTools, TRANSACTION_STATUS_EXPR
from app.config import Settings
from app.services.sqlserver_replay_provider import SqlServerReplayProvider


@unittest.skipUnless(os.getenv("RUN_SQL_INTEGRATION") == "1", "Set RUN_SQL_INTEGRATION=1 to query portfolio_demo.")
class ChatbotSqlIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = SqlServerReplayProvider(Settings())
        self.provider.configure_period_sync("today")
        self.tools = ChatbotSqlTools(engine=None, provider=self.provider)

    def raw_one(self, query: str, *params: object) -> tuple:
        connection = self.provider.open_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(query, *params)
            row = cursor.fetchone()
            cursor.close()
            return tuple(row)
        finally:
            connection.close()

    def raw_rows(self, query: str, *params: object) -> list[dict]:
        connection = self.provider.open_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(query, *params)
            columns = [column[0] for column in cursor.description]
            rows = [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
            cursor.close()
            return rows
        finally:
            connection.close()

    def test_transactions_between_dates_matches_raw_sql(self) -> None:
        raw = self.raw_one(
            f"""
            SELECT COUNT_BIG(*),
                   SUM(CASE WHEN {TRANSACTION_STATUS_EXPR} LIKE N'%refus%' OR {TRANSACTION_STATUS_EXPR} LIKE N'%non autor%' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN {TRANSACTION_STATUS_EXPR} LIKE N'%non about%' THEN 1 ELSE 0 END)
            FROM dbo.demo_transactions
            WHERE transaction_date BETWEEN ? AND ?;
            """,
            "260512",
            "260512",
        )
        result = asyncio.run(self.tools.get_transactions_between_dates("2026-05-12", "2026-05-12"))
        self.assertEqual(result["source"], "sql")
        self.assertEqual(result["transactions"], int(raw[0] or 0))
        self.assertEqual(result["refused"], int(raw[1] or 0))
        self.assertEqual(result["non_completed"], int(raw[2] or 0))

    def test_rankings_and_search_match_raw_sql_row_counts(self) -> None:
        top_merchants = asyncio.run(self.tools.get_top_merchants("today", 5, "2026-05-12", "2026-05-12"))
        top_tpe = asyncio.run(self.tools.get_top_tpe("today", 5, "2026-05-12", "2026-05-12"))
        incidents = asyncio.run(self.tools.get_incidents_by_hour("today", "2026-05-12", "2026-05-12"))
        merchant = asyncio.run(self.tools.search_merchant("Urban Basket", 5))

        self.assertEqual(top_merchants["source"], "sql")
        self.assertEqual(top_tpe["source"], "sql")
        self.assertEqual(incidents["source"], "sql")
        self.assertEqual(merchant["source"], "sql")
        self.assertGreater(top_merchants["rows_count"], 0)
        self.assertGreater(top_tpe["rows_count"], 0)
        self.assertGreater(incidents["rows_count"], 0)
        self.assertGreater(merchant["rows_count"], 0)


if __name__ == "__main__":
    unittest.main()
