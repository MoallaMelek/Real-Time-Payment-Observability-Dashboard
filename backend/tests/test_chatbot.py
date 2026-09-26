import asyncio
import unittest
from datetime import date
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from app.chatbot.business_query_parser import BusinessQueryParser
from app.chatbot.router import detect_intent
from app.chatbot.temporal_resolver import TemporalResolver
from app.chatbot.sql_tools import ChatbotSqlTools, FORBIDDEN_FIELDS, QUERY_REGISTRY
from app.services.sqlserver_replay_provider import PeriodWindow


def chat_snapshot() -> dict:
    return {
        "state_version": 7,
        "kpis": {
            "refusal_rate": 5.7,
            "slow_transactions": 3,
            "slow_transactions_available": True,
            "fraud_timeouts": 1,
            "fraud_timeout_available": True,
            "non_completed_transactions": 2,
            "global_risk_score": 22,
            "total_amount": 1200.0,
            "amount_unit": "TND",
            "total_transactions": 735,
            "success_rate": 94.0,
            "active_terminals": 48,
            "total_transfers_amount": 100.0,
            "transfers_count": 4,
            "transfer_period_available": True,
            "affiliations_remaining": 468,
            "affiliations_total": 1000,
            "affiliations_reservees": 286,
            "affiliations_affectees": 246,
            "affiliation_demo_data": True,
            "average_processing_time_ms": 320.0,
            "avg_processing_time_available": True,
        },
        "incidents_by_hour": [{"hour": "10", "transactions": 120, "refused": 9}],
        "fraud_trend_7_days": [],
        "response_time_distribution": [],
        "status_distribution": [{"label": "Autorisées", "value": 690}, {"label": "Refusées", "value": 42}],
        "active_alerts": [],
        "live_events": [],
        "top_anomalies": [{"name": "Adam Market", "merchant_name": "Adam Market", "refused": 7, "timeouts": 1, "slow": 2, "risk_score": 68, "severity": "high"}],
        "top_tpe": [{"terminal_id": "0183099019", "merchant_name": "Adam Market", "merchant": "Adam Market", "transactions": 40, "refused": 6, "timeouts": 1, "risk_score": 66, "severity": "high"}],
        "top_merchants": [{"name": "Adam Market", "merchant_name": "Adam Market", "transactions": 80, "refused": 9, "non_completed": 1, "risk_score": 70, "severity": "high", "success_rate": 90.0}],
        "replay_status": {
            "mode": "sqlserver_replay",
            "label": "SQL Server replay mode from historical data",
            "running": True,
            "paused": False,
            "processed_transactions": 735,
            "batches_processed": 2,
            "batch_size": 500,
            "interval_seconds": 5.0,
            "current_speed_tx_per_sec": 100.0,
            "current_batch_size": 500,
            "current_interval_ms": 5000,
            "fast_forward_enabled": False,
            "source_available": True,
            "cache_backend": "redis",
            "redis_configured": True,
            "redis_available": True,
            "redis_reason": "available",
            "redis_latency_ms": 1.2,
        },
    }


class FakeEngine:
    async def synchronized_snapshot(self, period: str | None = None) -> dict:
        value = chat_snapshot()
        value["replay_status"]["period"] = period or "today"
        if period == "yesterday":
            value["kpis"]["total_transactions"] = 500
            value["kpis"]["refusal_rate"] = 6.0
            value["kpis"]["success_rate"] = 93.0
            value["replay_status"]["processed_transactions"] = 500
        return value

    async def metrics(self) -> dict:
        return {"supervisor_running": True}


class FakeSqlCursor:
    def __init__(self, connection: "FakeSqlConnection") -> None:
        self.connection = connection
        self.timeout = 0
        self.description = connection.description

    def execute(self, query: str, *params: object) -> "FakeSqlCursor":
        self.connection.executed.append((query, params))
        if self.connection.fail_execute:
            raise RuntimeError("bad date column")
        return self

    def fetchone(self) -> tuple[int, int, int]:
        return self.connection.row

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.connection.rows

    def close(self) -> None:
        return None


class FakeSqlCursorWithoutTimeout:
    __slots__ = ("connection", "description")

    def __init__(self, connection: "FakeSqlConnection") -> None:
        self.connection = connection
        self.description = connection.description

    def execute(self, query: str, *params: object) -> "FakeSqlCursorWithoutTimeout":
        self.connection.executed.append((query, params))
        if self.connection.fail_execute:
            raise RuntimeError("bad date column")
        return self

    def fetchone(self) -> tuple[int, int, int]:
        return self.connection.row

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.connection.rows

    def close(self) -> None:
        return None


class FakeSqlConnection:
    def __init__(
        self,
        row: tuple[int, int, int] = (10, 2, 1),
        fail_execute: bool = False,
        rows: list[tuple[object, ...]] | None = None,
        description: list[tuple[str]] | None = None,
        cursor_without_timeout: bool = False,
    ) -> None:
        self.row = row
        self.fail_execute = fail_execute
        self.rows = rows or []
        self.description = description or [("value",)]
        self.cursor_without_timeout = cursor_without_timeout
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False

    def cursor(self) -> FakeSqlCursor | FakeSqlCursorWithoutTimeout:
        if self.cursor_without_timeout:
            return FakeSqlCursorWithoutTimeout(self)
        return FakeSqlCursor(self)

    def close(self) -> None:
        self.closed = True


class FakeReplayProvider:
    def __init__(self, connection: FakeSqlConnection | None = None, fail_connect: bool = False) -> None:
        self.connection = connection or FakeSqlConnection()
        self.fail_connect = fail_connect
        self.period_window = PeriodWindow("today", date(2026, 6, 30), date(2026, 6, 30), date(2026, 6, 30))

    def open_connection(self) -> FakeSqlConnection:
        if self.fail_connect:
            raise RuntimeError("server unreachable")
        return self.connection

    def period_window_for(self, period: str, reference_date: date) -> PeriodWindow:
        return PeriodWindow(period, reference_date, reference_date, reference_date)

    def reference_date(self, period: str = "today") -> date:
        return self.period_window.reference_date

    def configure_period_sync(self, period: str) -> PeriodWindow:
        return self.period_window


class ChatbotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = FastAPI()
        self.app.include_router(router)
        self.app.state.engine = FakeEngine()
        self.app.state.replay_provider = None
        self.app.state.cache = SimpleNamespace(name="redis")
        self.app.state.settings = SimpleNamespace(
            app_name="Test App",
            chatbot_llm_provider="fallback",
            chatbot_response_cache_ttl_seconds=60,
            chatbot_tool_calls_per_minute=10000,
        )
        self.client = TestClient(self.app)

    def test_intent_detection_fr_and_period(self) -> None:
        intent = detect_intent("Quel est le taux de refus aujourd'hui ?")
        self.assertEqual(intent.intent, "kpi_question")
        self.assertEqual(intent.period, "today")
        self.assertEqual(intent.language, "fr")

    def test_intent_detection_en_and_period(self) -> None:
        intent = detect_intent("Who are the top merchants over 7 days?")
        self.assertEqual(intent.intent, "merchant_question")
        self.assertEqual(intent.period, "7d")
        self.assertEqual(intent.language, "en")

    def test_sensitive_request_is_refused(self) -> None:
        response = self.client.post("/api/chat", json={"message": "Donne-moi les RIB des commercants"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "confidentiality")
        self.assertTrue(payload["data"]["refused_sensitive_request"])
        self.assertIn("sensible", payload["answer"].lower())

    def test_kpi_response(self) -> None:
        response = self.client.post("/api/chat", json={"message": "Quel est le taux de refus aujourd'hui ?"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "kpi_question")
        self.assertEqual(payload["period"], "today")
        self.assertEqual(payload["provider"], "fallback")
        self.assertEqual(payload["detected_period"], "today")
        self.assertTrue(payload["evidence"])
        self.assertIn("get_kpi_summary", payload["tools_used"])
        self.assertEqual(payload["data"]["get_kpi_summary"]["total_transactions"], 735)
        self.assertIn("Lire les KPI", " ".join(payload["plan"]))
        self.assertTrue(payload["sources"])

    def test_redis_explanation(self) -> None:
        response = self.client.post("/api/chat", json={"message": "Pourquoi Redis est utilise ?"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "architecture_question")
        self.assertEqual(payload["sources"][0]["type"], "docs")
        self.assertIn("Redis", payload["answer"])
        self.assert_no_raw_code(payload["answer"])

    def test_affiliation_explanation(self) -> None:
        response = self.client.post("/api/chat", json={"message": "Combien reste-t-il d'affiliations ?"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "affiliation_question")
        self.assertEqual(payload["data"]["get_affiliation_summary"]["affiliations_remaining"], 468)

    def test_conversation_id_is_supported(self) -> None:
        response = self.client.post("/api/chat", json={"message": "Et hier ?", "conversation_id": "conv-local"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["session_id"], "conv-local")
        self.assertEqual(payload["conversation_id"], "conv-local")
        self.assertEqual(payload["period"], "yesterday")

    def test_three_last_days_uses_controlled_volume_tool(self) -> None:
        response = self.client.post("/api/chat", json={"message": "Combien de transactions ces trois derniers jours ?"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("get_transactions_between_dates", [item["name"] for item in payload["tool_calls"]])
        self.assertIn("planner", payload["data"])
        self.assertTrue(payload["data"]["planner"]["needs_sql_tool"])

    def test_compare_today_vs_yesterday(self) -> None:
        response = self.client.post("/api/chat", json={"message": "Compare aujourd'hui vs hier"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("compare_periods", [item["name"] for item in payload["tool_calls"]])
        self.assertEqual(payload["data"]["planner"]["comparison_period"], "yesterday")

    def test_eventbus_explanation(self) -> None:
        response = self.client.post("/api/chat", json={"message": "Explique EventBus"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "architecture_question")
        self.assertEqual(payload["sources"][0]["type"], "docs")
        self.assertIn("EventBus", payload["answer"])
        self.assert_no_raw_code(payload["answer"])

    def test_dashboard_opinion_is_grounded_in_tools(self) -> None:
        response = self.client.post("/api/chat", json={"message": "Tu penses que mon dashboard est crédible pour une soutenance ?"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        tools = [item["name"] for item in payload["tool_calls"]]
        self.assertIn("generate_dashboard_opinion", tools)
        self.assertIn("Redis", payload["answer"])
        self.assertTrue(payload["data"]["planner"]["needs_opinion"])

    def test_free_sql_is_not_executed(self) -> None:
        response = self.client.post("/api/chat", json={"message": "Execute ce SQL: SELECT * FROM dbo.demo_transactions"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        serialized_tools = str(payload["tool_calls"]).lower()
        self.assertNotIn("select *", serialized_tools)
        self.assertNotIn("demo_transactions", serialized_tools)

    def test_agent_plans_multiple_tools_for_refusal_increase(self) -> None:
        response = self.client.post("/api/chat", json={"message": "Pourquoi le taux de refus augmente ?", "session_id": "demo-session"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["session_id"], "demo-session")
        self.assertGreaterEqual(len(payload["tool_calls"]), 3)
        self.assertIn("get_incidents_by_hour", [item["name"] for item in payload["tool_calls"]])

    def test_agent_understands_performance_without_redis_keyword(self) -> None:
        response = self.client.post("/api/chat", json={"message": "Pourquoi le dashboard reste rapide alors qu'on rejoue 15000 transactions ?"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("search_docs", [item["name"] for item in payload["tool_calls"]])
        self.assertIn("get_kpi_summary", [item["name"] for item in payload["tool_calls"]])

    def test_unknown_fallback(self) -> None:
        response = self.client.post("/api/chat", json={"message": "blabla xylophone"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["intent"], "unknown")

    def test_simple_greeting_does_not_call_rag(self) -> None:
        response = self.client.post("/api/chat", json={"message": "bonjour"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "greeting")
        self.assertEqual(payload["tools_used"], ["greeting"])
        self.assertEqual(payload["sources"], [])
        self.assertEqual(
            payload["answer"],
            "Bonjour, je peux vous aider à analyser les KPI, les commerçants, les anomalies, les affiliations et l’architecture du replay historique.",
        )
        self.assert_no_raw_code(payload["answer"])

    def test_help_greeting_does_not_call_rag(self) -> None:
        response = self.client.post("/api/chat", json={"message": "tu peux m’aider ?"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "help")
        self.assertEqual(payload["tools_used"], ["greeting"])
        self.assert_no_raw_code(payload["answer"])

    def test_typos_and_abbreviations_are_normalized(self) -> None:
        response = self.client.post("/api/chat", json={"message": "combaen trsnction ajd"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "kpi_question")
        self.assertEqual(payload["period"], "today")
        self.assertIn("get_kpi_summary", [item["name"] for item in payload["tool_calls"]])

        response = self.client.post("/api/chat", json={"message": "cmb transactions hier"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "kpi_question")
        self.assertEqual(payload["period"], "yesterday")

    def test_small_talk_does_not_call_rag_or_sql(self) -> None:
        response = self.client.post("/api/chat", json={"message": "comment vas tu"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "small_talk")
        self.assertEqual(payload["tools_used"], ["greeting"])
        self.assertIn("Je vais bien", payload["answer"])
        self.assert_no_raw_code(payload["answer"])

    def test_followup_compare_uses_conversation_memory(self) -> None:
        first = self.client.post(
            "/api/chat",
            json={
                "message": "Combien de transactions ces trois derniers jours ?",
                "conversation_id": "memory-demo",
            },
        )
        self.assertEqual(first.status_code, 200)
        second = self.client.post(
            "/api/chat",
            json={"message": "compare avec hier", "conversation_id": "memory-demo"},
        )
        self.assertEqual(second.status_code, 200)
        payload = second.json()
        tools = [item["name"] for item in payload["tool_calls"]]
        self.assertIn("compare_periods", tools)
        self.assertEqual(payload["data"]["planner"]["question_type"], "follow_up_question")

    def test_followup_anomalies_and_why_use_memory(self) -> None:
        first = self.client.post(
            "/api/chat",
            json={"message": "Combien de transactions aujourd'hui ?", "conversation_id": "follow-demo"},
        )
        self.assertEqual(first.status_code, 200)
        anomalies = self.client.post(
            "/api/chat",
            json={"message": "et les anomalies ?", "conversation_id": "follow-demo"},
        )
        self.assertEqual(anomalies.status_code, 200)
        self.assertIn("get_top_anomalies", [item["name"] for item in anomalies.json()["tool_calls"]])

        why = self.client.post(
            "/api/chat",
            json={"message": "pourquoi ?", "conversation_id": "follow-demo"},
        )
        self.assertEqual(why.status_code, 200)
        self.assertNotEqual(why.json()["tools_used"], ["clarify_question"])

    def test_architecture_and_opinion_answers_do_not_leak_source_code(self) -> None:
        messages = [
            "explique Redis simplement",
            "pourquoi EventBus est utile ?",
            "explique-moi le replay historique",
            "c’est quoi la limite de cette architecture ?",
            "tu penses que mon dashboard est crédible ?",
        ]
        for message in messages:
            with self.subTest(message=message):
                response = self.client.post("/api/chat", json={"message": message})
                self.assertEqual(response.status_code, 200)
                self.assert_no_raw_code(response.json()["answer"])

    def test_explicit_code_request_is_allowed(self) -> None:
        response = self.client.post("/api/chat", json={"message": "montre-moi le code du composant chat"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "code_question")
        self.assertIn("project_code_search", [item["name"] for item in payload["tool_calls"]])
        self.assertIn("frontend/src/components/ChatAssistant.tsx", payload["answer"])

    def test_explicit_date_without_sql_does_not_fallback_to_today_snapshot(self) -> None:
        response = self.client.post("/api/chat", json={"message": "Combien de transactions le 2 février ?"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("get_transactions_between_dates", [item["name"] for item in payload["tool_calls"]])
        self.assertEqual(payload["data"]["get_transactions_between_dates"]["source"], "sql_unavailable")
        self.assertFalse(payload["data"]["get_transactions_between_dates"]["data_available"])
        self.assertIn("Connexion SQL Server indisponible", payload["answer"])

    def test_explicit_refusal_rate_ranges_use_controlled_sql_dates(self) -> None:
        cases = [
            ("taux de refus entre 15 mars et 25 mars", "260315", "260325"),
            ("taux de refus entre 1 juin et 4 juin", "260601", "260604"),
            ("taux de refus entre 2 mai jusqu’à 5 mai", "260502", "260505"),
            ("taux de refus entre 9 mai jusqu’à 11 mai", "260509", "260511"),
            ("taux de refus le 12 mai", "260512", "260512"),
        ]
        for message, start_param, end_param in cases:
            with self.subTest(message=message):
                connection = FakeSqlConnection(row=(10, 2, 1))
                self.app.state.replay_provider = FakeReplayProvider(connection)
                response = self.client.post("/api/chat", json={"message": message})
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                data = payload["data"]["get_transactions_between_dates"]
                self.assertEqual(data["source"], "sql")
                self.assertEqual(data["transactions"], 10)
                self.assertEqual(data["refused"], 2)
                self.assertEqual(data["authorized_count"], 8)
                self.assertEqual(data["completed_success_count"], 7)
                self.assertEqual(data["refusal_rate"], 20.0)
                self.assertEqual(data["success_rate"], 70.0)
                self.assertEqual(data["diagnostic"]["table"], "dbo.demo_transactions")
                self.assertEqual(data["diagnostic"]["date_column"], "transaction_date")
                self.assertEqual(data["diagnostic"]["start_param"], start_param)
                self.assertEqual(data["diagnostic"]["end_param"], end_param)
                self.assertIn("10 transactions", payload["answer"])
                self.assertIn("2 refus", payload["answer"])

    def test_sql_connection_unavailable_message_is_specific(self) -> None:
        self.app.state.replay_provider = FakeReplayProvider(fail_connect=True)
        response = self.client.post("/api/chat", json={"message": "taux de refus le 12 mai"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        data = payload["data"]["get_transactions_between_dates"]
        self.assertEqual(data["source"], "sql_unavailable")
        self.assertEqual(data["message"], "Connexion SQL Server indisponible.")
        self.assertIn("Connexion SQL Server indisponible", payload["answer"])

    def test_sql_accessible_with_zero_rows_has_specific_message(self) -> None:
        self.app.state.replay_provider = FakeReplayProvider(FakeSqlConnection(row=(0, 0, 0)))
        response = self.client.post("/api/chat", json={"message": "taux de refus le 12 mai"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        data = payload["data"]["get_transactions_between_dates"]
        self.assertEqual(data["source"], "sql")
        self.assertFalse(data["data_available"])
        self.assertEqual(data["message"], "Pas de transaction trouvée pour cette période.")
        self.assertIn("Pas de transaction trouvée", payload["answer"])

    def test_sql_mapping_error_is_not_reported_as_unavailable(self) -> None:
        self.app.state.replay_provider = FakeReplayProvider(FakeSqlConnection(fail_execute=True))
        response = self.client.post("/api/chat", json={"message": "taux de refus le 12 mai"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        data = payload["data"]["get_transactions_between_dates"]
        self.assertEqual(data["source"], "sql_error")
        self.assertIn("bad date column", data["diagnostic"]["exception"])
        self.assertNotIn("Connexion SQL Server indisponible", payload["answer"])

    def test_final_chatbot_answer_is_not_cached(self) -> None:
        session_id = "no-final-cache"
        message = "taux de refus le 12 mai"
        first = self.client.post("/api/chat", json={"message": message, "session_id": session_id})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["data"]["get_transactions_between_dates"]["source"], "sql_unavailable")

        self.app.state.replay_provider = FakeReplayProvider(FakeSqlConnection(row=(99, 7, 3)))
        second = self.client.post("/api/chat", json={"message": message, "session_id": session_id})
        self.assertEqual(second.status_code, 200)
        payload = second.json()
        data = payload["data"]["get_transactions_between_dates"]
        self.assertEqual(data["source"], "sql")
        self.assertEqual(data["transactions"], 99)
        self.assertIn("99 transactions", payload["answer"])

    def test_pyodbc_cursor_without_timeout_attribute_still_executes(self) -> None:
        connection = FakeSqlConnection(row=(12, 3, 1), cursor_without_timeout=True)
        tools = ChatbotSqlTools(FakeEngine(), FakeReplayProvider(connection))
        data = asyncio.run(tools.get_transactions_between_dates("2026-05-12", "2026-05-12"))
        self.assertEqual(data["source"], "sql")
        self.assertEqual(data["transactions"], 12)
        self.assertEqual(data["refused"], 3)
        self.assertEqual(data["authorized_count"], 9)
        self.assertEqual(data["completed_success_count"], 8)
        self.assertEqual(data["diagnostic"]["start_param"], "260512")

    def test_sql_ranking_error_is_not_silent_empty_rows(self) -> None:
        connection = FakeSqlConnection(fail_execute=True)
        tools = ChatbotSqlTools(FakeEngine(), FakeReplayProvider(connection))
        data = asyncio.run(tools.get_top_merchants("today", 5, "2026-05-12", "2026-05-12"))
        self.assertEqual(data["source"], "sql_error")
        self.assertEqual(data["rows"], [])
        self.assertIn("bad date column", data["diagnostic"]["exception"])

    def test_top_merchants_explicit_date_uses_top_tool(self) -> None:
        connection = FakeSqlConnection(
            rows=[("Meridian Market", 102, 0, 1)],
            description=[("merchant_name",), ("transactions",), ("refused",), ("non_completed",)],
        )
        self.app.state.replay_provider = FakeReplayProvider(connection)
        response = self.client.post("/api/chat", json={"message": "top commerçants le 12 mai"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("get_top_merchants", [item["name"] for item in payload["tool_calls"]])
        self.assertNotIn("get_transactions_between_dates", [item["name"] for item in payload["tool_calls"]])
        data = payload["data"]["get_top_merchants"]
        self.assertEqual(data["source"], "sql")
        self.assertEqual(data["rows_count"], 1)
        self.assertEqual(data["diagnostic"]["start_param"], "260512")
        self.assertIn("2026-05-12", payload["answer"])
        self.assertIn("Meridian Market", payload["answer"])

    def test_structured_planner_extracts_metric_entity_time_dimension(self) -> None:
        connection = FakeSqlConnection(
            rows=[("Meridian Market", 102, 0, 1)],
            description=[("merchant_name",), ("transactions",), ("refused",), ("non_completed",)],
        )
        self.app.state.replay_provider = FakeReplayProvider(connection)
        response = self.client.post(
            "/api/chat",
            json={"message": "Quel est le taux de refus des transactions entre le 9 mai et le 11 mai par commerçant ?"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("get_top_merchants", [item["name"] for item in payload["tool_calls"]])
        self.assertNotIn("get_transactions_between_dates", [item["name"] for item in payload["tool_calls"]])
        understanding = payload["data"]["planner"]["query_understanding"]
        self.assertEqual(understanding["metric"], "refusal_rate")
        self.assertEqual(understanding["entity"], "transaction")
        self.assertEqual(understanding["dimension"], "merchant")
        self.assertEqual(understanding["aggregation"], "group_by_merchant")
        self.assertEqual(understanding["period"]["start_date"], f"{date.today().year}-05-09")
        self.assertEqual(understanding["period"]["end_date"], f"{date.today().year}-05-11")
        self.assertEqual(payload["data"]["get_top_merchants"]["diagnostic"]["start_param"], f"{str(date.today().year)[2:]}0509")

    def test_business_query_parser_structure_matrix(self) -> None:
        year = date.today().year
        cases = [
            ("nombre total de transactions le 8 mai", "count", "transaction", "total", None, f"{year}-05-08"),
            ("nombre de TPE observés le 9 mai", "count", "tpe", "distinct_count", None, f"{year}-05-09"),
            ("top TPE le 9 mai", "rank", "tpe", None, "tpe", f"{year}-05-09"),
            ("top commerçants le 12 mai", "rank", "merchant", None, "merchant", f"{year}-05-12"),
            ("taux de refus le 12 mai", "rate", "transaction", "refusal_rate", None, f"{year}-05-12"),
            ("taux de succès le 12 mai", "rate", "transaction", "success_rate", None, f"{year}-05-12"),
            ("combien de transactions autorisées le 12 mai", "count", "transaction", "authorized_count", None, f"{year}-05-12"),
            ("incidents par heure le 12 mai", "count", "incident", "total", "hour", f"{year}-05-12"),
            ("compare les transactions", "compare", "transaction", None, None, None),
            ("explique Redis simplement", "explain", "architecture", None, None, None),
            ("tu peux m'aider ?", "help", None, None, None, None),
        ]
        for message, action, obj, metric, dimension, start_date in cases:
            with self.subTest(message=message):
                intent = detect_intent(message)
                temporal = TemporalResolver.resolve(message, reference_date=date(year, 5, 12))
                parsed = BusinessQueryParser.parse(message, intent, temporal).to_dict()
                self.assertEqual(parsed["action"], action)
                self.assertEqual(parsed["object"], obj)
                self.assertEqual(parsed["metric"], metric)
                self.assertEqual(parsed["dimension"], dimension)
                if start_date:
                    self.assertEqual(parsed["period"]["start_date"], start_date)

    def test_business_query_tool_selector_matrix(self) -> None:
        cases = [
            ("nombre total de transactions le 8 mai", "get_transactions_between_dates"),
            ("nombre de TPE observés le 9 mai", "get_tpe_count_by_period"),
            ("top TPE le 9 mai", "get_top_tpe"),
            ("top commerçants le 12 mai", "get_top_merchants"),
            ("taux de refus le 12 mai", "get_transactions_between_dates"),
            ("taux de succès le 12 mai", "get_transactions_between_dates"),
            ("combien de transactions autorisées le 12 mai", "get_transactions_between_dates"),
            ("incidents par heure le 12 mai", "get_incidents_by_hour"),
        ]
        for message, expected_tool in cases:
            with self.subTest(message=message):
                self.app.state.replay_provider = FakeReplayProvider(FakeSqlConnection(
                    row=(10, 2, 1),
                    rows=[("A", 1, 0, 0)],
                    description=[("merchant_name",), ("transactions",), ("refused",), ("non_completed",)],
                ))
                response = self.client.post("/api/chat", json={"message": message})
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertIn(expected_tool, [item["name"] for item in payload["tool_calls"]])
                business_query = payload["data"]["planner"]["business_query"]
                self.assertIn(business_query["action"], {"count", "rank", "rate"})

    def test_answer_is_built_from_collected_evidence(self) -> None:
        self.app.state.replay_provider = FakeReplayProvider(FakeSqlConnection(row=(99, 7, 3)))
        response = self.client.post("/api/chat", json={"message": "taux de refus le 12 mai"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["data"]["evidence_collector"]["mode"], "evidence_first")
        self.assertEqual(payload["data"]["evidence_collector"]["tools_collected"], ["get_transactions_between_dates"])
        self.assertEqual(payload["data"]["get_transactions_between_dates"]["transactions"], 99)
        self.assertIn("99 transactions", payload["answer"])
        self.assertIn("7 refus", payload["answer"])

    def test_top_tpe_explicit_date_uses_tpe_tool(self) -> None:
        connection = FakeSqlConnection(
            rows=[("0182779017", "OTHMANI MAJID", 18, 4, 0)],
            description=[("terminal_id",), ("merchant_name",), ("transactions",), ("refused",), ("slow",)],
        )
        self.app.state.replay_provider = FakeReplayProvider(connection)
        response = self.client.post("/api/chat", json={"message": "top TPE le 12 mai"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("get_top_tpe", [item["name"] for item in payload["tool_calls"]])
        self.assertNotIn("get_transactions_between_dates", [item["name"] for item in payload["tool_calls"]])
        data = payload["data"]["get_top_tpe"]
        self.assertEqual(data["source"], "sql")
        self.assertEqual(data["diagnostic"]["start_param"], "260512")
        self.assertIn("2026-05-12", payload["answer"])
        self.assertIn("0182779017", payload["answer"])

    def test_incidents_by_hour_explicit_date_uses_incident_tool(self) -> None:
        connection = FakeSqlConnection(
            rows=[("10", 120, 9)],
            description=[("hour",), ("transactions",), ("refused",)],
        )
        self.app.state.replay_provider = FakeReplayProvider(connection)
        response = self.client.post("/api/chat", json={"message": "incidents par heure le 12 mai"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("get_incidents_by_hour", [item["name"] for item in payload["tool_calls"]])
        self.assertNotIn("get_transactions_between_dates", [item["name"] for item in payload["tool_calls"]])
        data = payload["data"]["get_incidents_by_hour"]
        self.assertEqual(data["source"], "sql")
        self.assertEqual(data["diagnostic"]["start_param"], "260512")
        self.assertIn("2026-05-12", payload["answer"])
        self.assertIn("10h", payload["answer"])

    def test_non_completed_rate_question_uses_non_completed_count(self) -> None:
        connection = FakeSqlConnection(row=(20, 3, 5))
        self.app.state.replay_provider = FakeReplayProvider(connection)
        response = self.client.post(
            "/api/chat",
            json={"message": "donne moi le taux des transactions non abouties le 9 mai et leur nombre"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        data = payload["data"]["get_transactions_between_dates"]
        self.assertEqual(data["source"], "sql")
        self.assertEqual(data["metric_subject"], "non_completed")
        self.assertEqual(data["transactions"], 20)
        self.assertEqual(data["non_completed"], 5)
        self.assertEqual(data["diagnostic"]["start_param"], "260509")
        self.assertIn("5 transactions non abouties", payload["answer"])
        self.assertIn("25.0 %", payload["answer"])
        executed_query = connection.executed[0][0]
        self.assertIn("TRY_CONVERT(NVARCHAR(255), transaction_status)", executed_query)

    def test_followup_new_date_reuses_last_metric_subject(self) -> None:
        conversation_id = "ctx-slow-then-date"
        year = date.today().year
        first = self.client.post(
            "/api/chat",
            json={"message": "combien de transactions lentes le 18 janvier", "conversation_id": conversation_id},
        )
        self.assertEqual(first.status_code, 200)

        second = self.client.post(
            "/api/chat",
            json={"message": "ok alors 15 mars", "conversation_id": conversation_id},
        )
        self.assertEqual(second.status_code, 200)
        payload = second.json()
        tool = self._tool_call(payload, "get_transactions_between_dates")
        self.assertEqual(tool["arguments"]["start_date"], f"{year}-03-15")
        self.assertEqual(tool["arguments"]["end_date"], f"{year}-03-15")
        self.assertEqual(tool["arguments"]["metric_subject"], "transactions_lentes")
        self.assertEqual(payload["data"]["planner"]["metric_subject"], "transactions_lentes")

    def test_followup_new_subject_reuses_last_explicit_date(self) -> None:
        conversation_id = "ctx-date-then-slow"
        year = date.today().year
        first = self.client.post(
            "/api/chat",
            json={"message": "ok alors 15 mars", "conversation_id": conversation_id},
        )
        self.assertEqual(first.status_code, 200)

        second = self.client.post(
            "/api/chat",
            json={"message": "je parle des transactions lentes", "conversation_id": conversation_id},
        )
        self.assertEqual(second.status_code, 200)
        payload = second.json()
        tool = self._tool_call(payload, "get_transactions_between_dates")
        self.assertEqual(tool["arguments"]["start_date"], f"{year}-03-15")
        self.assertEqual(tool["arguments"]["metric_subject"], "transactions_lentes")

    def test_followup_new_subject_reuses_last_explicit_period(self) -> None:
        conversation_id = "ctx-anomalies-then-merchants"
        year = date.today().year
        first = self.client.post(
            "/api/chat",
            json={"message": "anomalies le 2 février", "conversation_id": conversation_id},
        )
        self.assertEqual(first.status_code, 200)

        second = self.client.post(
            "/api/chat",
            json={"message": "et les commerçants", "conversation_id": conversation_id},
        )
        self.assertEqual(second.status_code, 200)
        payload = second.json()
        tool = self._tool_call(payload, "get_top_merchants")
        self.assertEqual(tool["arguments"]["start_date"], f"{year}-02-02")
        self.assertEqual(tool["arguments"]["end_date"], f"{year}-02-02")
        self.assertEqual(tool["arguments"]["metric_subject"], "merchants")

    def test_followup_new_subject_reuses_last_relative_period(self) -> None:
        conversation_id = "ctx-yesterday-then-anomalies"
        first = self.client.post(
            "/api/chat",
            json={"message": "transactions hier", "conversation_id": conversation_id},
        )
        self.assertEqual(first.status_code, 200)

        second = self.client.post(
            "/api/chat",
            json={"message": "et les anomalies", "conversation_id": conversation_id},
        )
        self.assertEqual(second.status_code, 200)
        payload = second.json()
        tool = self._tool_call(payload, "get_top_anomalies")
        self.assertEqual(tool["arguments"]["period"], "yesterday")
        self.assertEqual(tool["arguments"]["metric_subject"], "anomalies")

    def test_thanks_and_capabilities_are_direct(self) -> None:
        for message, expected_intent in (("merci", "thanks"), ("que peux tu faire", "capabilities")):
            with self.subTest(message=message):
                response = self.client.post("/api/chat", json={"message": message})
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["intent"], expected_intent)
                self.assertEqual(payload["tools_used"], ["greeting"])
                self.assert_no_raw_code(payload["answer"])

    def test_confidence_guard_clarifies_ambiguous_question(self) -> None:
        response = self.client.post("/api/chat", json={"message": "blabla xylophone"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "unknown")
        self.assertEqual(payload["tools_used"], ["clarify_question"])
        self.assertIn("besoin de préciser", payload["answer"])

    def test_chat_suggestions(self) -> None:
        response = self.client.get("/api/chat/suggestions?language=en")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Why is Redis used?", [item["label"] for item in response.json()])

    def test_sql_tools_do_not_select_sensitive_fields_or_dangerous_sql(self) -> None:
        dangerous = ("delete", "update", "insert", "drop", "alter", "select *")
        for name, query in QUERY_REGISTRY.items():
            lowered = query.lower()
            self.assertFalse(any(token in lowered for token in dangerous), name)
            selected_block = lowered.split(" from ", 1)[0]
            for field in FORBIDDEN_FIELDS:
                self.assertNotIn(field.lower(), selected_block, name)

    def assert_no_raw_code(self, answer: str) -> None:
        forbidden = ("useState", "useEffect", "<div", "const ", "interface ", "export ", "import ", "TSX", "JSX")
        for marker in forbidden:
            self.assertNotIn(marker, answer)

    def _tool_call(self, payload: dict, name: str) -> dict:
        for item in payload["tool_calls"]:
            if item["name"] == name:
                return item
        self.fail(f"Tool {name} not found in {payload['tool_calls']}")
