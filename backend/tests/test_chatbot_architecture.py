import unittest
from datetime import date
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from app.chatbot.adaptive_response_policy import AdaptiveResponsePolicyEngine
from app.chatbot.business_analysis import BusinessAnalysisEngine
from app.chatbot.conversation_classifier import ConversationClassifier
from app.chatbot.business_understanding import BusinessUnderstandingEngine
from app.chatbot.memory import ConversationMemory
from app.chatbot.reasoning import ReasoningEngine
from app.chatbot.router import detect_intent
from app.chatbot.temporal_resolver import TemporalResolver
from app.chatbot.tool_selector import ToolSelector
from app.chatbot.sql_tools import FORBIDDEN_FIELDS, QUERY_REGISTRY
from tests.test_chatbot import FakeEngine, FakeReplayProvider, FakeSqlConnection


class ChatbotArchitectureTests(unittest.TestCase):
    def _client_with_provider(self, provider: FakeReplayProvider | None = None) -> tuple[TestClient, FastAPI]:
        app = FastAPI()
        app.include_router(router)
        app.state.engine = FakeEngine()
        app.state.replay_provider = provider
        app.state.cache = SimpleNamespace(name="redis")
        app.state.settings = SimpleNamespace(
            app_name="Test App",
            chatbot_llm_provider="fallback",
            chatbot_response_cache_ttl_seconds=60,
            chatbot_tool_calls_per_minute=10000,
            chatbot_audit_enabled=False,
        )
        app.state.chat_memory = ConversationMemory()
        return TestClient(app), app

    def test_business_understanding_produces_canonical_structure(self) -> None:
        message = "Quel est le taux de refus des transactions entre le 9 mai et le 11 mai par commerçant ?"
        intent = detect_intent(message)
        temporal = TemporalResolver.resolve(message, reference_date=date(2026, 5, 12))

        understanding = BusinessUnderstandingEngine.understand(message, intent, temporal)
        payload = understanding.to_dict()

        self.assertEqual(payload["type"], "business_query")
        self.assertEqual(payload["conversation_kind"], "business")
        self.assertEqual(payload["intent"], "analyse")
        self.assertEqual(payload["action"], "rate")
        self.assertEqual(payload["object"], "transaction")
        self.assertEqual(payload["metric"], "refusal_rate")
        self.assertEqual(payload["dimension"], "merchant")
        self.assertEqual(payload["period"]["start_date"], "2026-05-09")
        self.assertTrue(payload["requires_sql"])
        self.assertFalse(payload["requires_rag"])
        self.assertFalse(payload["requires_code_search"])
        self.assertGreaterEqual(payload["confidence"], 0.9)

    def test_tool_selector_maps_business_query_without_text_rules(self) -> None:
        message = "top TPE le 12 mai"
        intent = detect_intent(message)
        temporal = TemporalResolver.resolve(message, reference_date=date(2026, 5, 12))
        understanding = BusinessUnderstandingEngine.understand(message, intent, temporal)

        plan = ToolSelector.from_business_query(understanding.business_query, "today")

        self.assertEqual([call.name for call in plan], ["get_top_tpe"])
        self.assertEqual(plan[0].arguments["start_date"], "2026-05-12")
        self.assertEqual(plan[0].arguments["business_query"]["dimension"], "tpe")

    def test_business_request_overrides_help_intent_for_tpe_count(self) -> None:
        message = "donne moi le nombre de tpe observés entre 7 mai et 9 mai"
        intent = detect_intent(message)
        temporal = TemporalResolver.resolve(message, reference_date=date(2026, 5, 12))

        understanding = BusinessUnderstandingEngine.understand(message, intent, temporal)
        plan = ToolSelector.from_business_query(understanding.business_query, "today")

        self.assertEqual(intent.intent, "top_tpe")
        self.assertEqual(understanding.conversation_kind, "business")
        self.assertEqual(understanding.action, "count")
        self.assertEqual(understanding.object, "tpe")
        self.assertTrue(understanding.requires_sql)
        self.assertEqual([call.name for call in plan], ["get_tpe_count_by_period"])

    def test_unified_conversation_classifier_categories(self) -> None:
        cases = [
            ("bonjour", "social"),
            ("Je suis inquiet pour les paiements aujourd'hui", "business_diagnostic"),
            ("Combien de transactions le 7 mai ?", "business_data_query"),
            ("Pourquoi Redis est utilisé ?", "architecture"),
            ("Où est défini le code de la route chatbot ?", "technical_code"),
            ("Donne-moi les IBAN des clients", "sensitive_individual_data"),
        ]
        for message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(ConversationClassifier.classify(message, detect_intent(message).intent).category, expected)

    def test_adaptive_response_policy_detects_fact_analysis_and_expertise_depths(self) -> None:
        factual_intent = detect_intent("Combien de transactions le 7 mai ?")
        factual_temporal = TemporalResolver.resolve("Combien de transactions le 7 mai ?", reference_date=date(2026, 5, 12))
        factual_understanding = BusinessUnderstandingEngine.understand("Combien de transactions le 7 mai ?", factual_intent, factual_temporal)
        factual_strategy = SimpleNamespace(goal="answer_business_question", technical=False)

        analysis_policy = AdaptiveResponsePolicyEngine.decide(
            "J'ai l'impression qu'il y a un problème sur les paiements aujourd'hui",
            SimpleNamespace(action=None, conversation_kind="business"),
            SimpleNamespace(goal="reassure_and_investigate", technical=False),
        )
        expert_policy = AdaptiveResponsePolicyEngine.decide(
            "Que recommandes-tu de surveiller ?",
            SimpleNamespace(action=None, conversation_kind="business"),
            SimpleNamespace(goal="decision_support", technical=False),
        )

        self.assertEqual(AdaptiveResponsePolicyEngine.decide("Combien de transactions le 7 mai ?", factual_understanding, factual_strategy).depth, "factual")
        self.assertEqual(analysis_policy.depth, "analytical")
        self.assertEqual(expert_policy.depth, "expert")

    def test_conversation_state_tracks_last_business_query_tool_and_result(self) -> None:
        memory = ConversationMemory()
        session_id = memory.ensure_session("state-demo")
        business_query = {
            "metric": "refusal_rate",
            "dimension": "merchant",
            "filters": {"status": "refused"},
            "period": {"period_key": "today"},
        }

        memory.update_state(
            session_id,
            business_query=business_query,
            tool="get_top_merchants",
            evidence={"tools": ["get_top_merchants"], "has_sql": True},
            result_summary={"rows_count": 5},
        )
        state = memory.state(session_id)

        self.assertEqual(state.last_business_query, business_query)
        self.assertEqual(state.last_period, "today")
        self.assertIsNone(state.last_action)
        self.assertIsNone(state.last_object)
        self.assertEqual(state.last_metric, "refusal_rate")
        self.assertIsNone(state.last_capability)
        self.assertEqual(state.last_dimension, "merchant")
        self.assertEqual(state.last_filters, {"status": "refused"})
        self.assertEqual(state.last_tool, "get_top_merchants")
        self.assertEqual(state.last_evidence, {"tools": ["get_top_merchants"], "has_sql": True})
        self.assertEqual(state.last_result_summary, {"rows_count": 5})
        self.assertEqual(state.last_business_topic, "refusal_rate")
        self.assertIsNone(state.active_context["action"])
        self.assertIsNone(state.active_context["object"])
        self.assertIsNone(state.active_context["capability"])
        self.assertEqual(state.active_context["dimension"], "merchant")
        self.assertEqual(len(state.recent_outputs), 1)
        self.assertEqual(state.recent_outputs[0]["tool"], "get_top_merchants")

    def test_reasoning_engine_uses_evidence_bundle_not_raw_text(self) -> None:
        bundle = ReasoningEngine.build_evidence_bundle(
            {"get_transactions_between_dates": {"source": "sql", "data_available": True, "transactions": 10}},
            [{"name": "get_transactions_between_dates", "arguments": {}, "purpose": "controlled SQL"}],
        )
        result = ReasoningEngine.reason(None, bundle)

        self.assertTrue(bundle.has_sql)
        self.assertEqual(bundle.tools, ["get_transactions_between_dates"])
        self.assertEqual(result.confidence, "high")

    def test_conversation_state_preserves_action_object_and_capability(self) -> None:
        memory = ConversationMemory()
        session_id = memory.ensure_session("structured-state")

        memory.update_state(
            session_id,
            business_query={
                "action": "count",
                "object": "tpe",
                "metric": "distinct_count",
                "capability": "tpe_count",
                "dimension": None,
                "filters": {},
                "period": {"period_key": "today"},
            },
        )
        state = memory.state(session_id)

        self.assertEqual(state.last_action, "count")
        self.assertEqual(state.last_object, "tpe")
        self.assertEqual(state.last_capability, "tpe_count")
        self.assertEqual(state.active_context["action"], "count")
        self.assertEqual(state.active_context["object"], "tpe")
        self.assertEqual(state.active_context["capability"], "tpe_count")

    def test_business_analysis_engine_interprets_kpi_without_inventing(self) -> None:
        evidence = {
            "get_transactions_between_dates": {
                "source": "sql",
                "data_available": True,
                "start_date": "2026-05-12",
                "end_date": "2026-05-12",
                "transactions": 6400,
                "refused": 52,
                "non_completed": 0,
                "refusal_rate": 0.84,
            }
        }
        bundle = ReasoningEngine.build_evidence_bundle(evidence, [{"name": "get_transactions_between_dates"}])

        analysis = BusinessAnalysisEngine.analyze(
            understanding=None,
            state=None,
            bundle=bundle,
            tool_results=evidence,
            reasoning=ReasoningEngine.reason(None, bundle),
        )

        joined = " ".join(analysis.facts + analysis.interpretation + analysis.recommendations)
        self.assertIn("6400 transactions", joined)
        self.assertIn("52 refus", joined)
        self.assertIn("0.84 %", joined)
        self.assertIn("ne suffit pas à identifier une cause", joined)
        self.assertIn("Comparer cette période", joined)
        self.assertNotIn("moyenne", joined.lower())
        self.assertEqual(analysis.confidence_label, "élevé")

    def test_business_analysis_engine_comparison_calculates_evolution_and_anomaly_hint(self) -> None:
        evidence = {
            "compare_date_ranges": {
                "source": "sql",
                "data_available": True,
                "left": {"label": "janvier", "transactions": 100, "refusal_rate": 4.0},
                "right": {"label": "février", "transactions": 50, "refusal_rate": 2.0},
                "delta": {"transactions": 50, "refusal_rate": 2.0},
            }
        }
        bundle = ReasoningEngine.build_evidence_bundle(evidence, [{"name": "compare_date_ranges"}])

        analysis = BusinessAnalysisEngine.analyze(
            understanding=None,
            state=None,
            bundle=bundle,
            tool_results=evidence,
            reasoning=ReasoningEngine.reason(None, bundle),
        )

        self.assertTrue(any("100.0 %" in item for item in analysis.insights))
        self.assertTrue(analysis.anomalies)
        self.assertTrue(any("commerçants" in item for item in analysis.recommendations))
        self.assertEqual(analysis.confidence_label, "élevé")

    def test_business_analysis_engine_handles_insufficient_data_with_low_confidence(self) -> None:
        bundle = ReasoningEngine.build_evidence_bundle({}, [])

        analysis = BusinessAnalysisEngine.analyze(
            understanding=None,
            state=None,
            bundle=bundle,
            tool_results={},
            reasoning=ReasoningEngine.reason(None, bundle),
        )

        self.assertEqual(analysis.confidence_label, "faible")
        self.assertTrue(analysis.limitations)
        self.assertTrue(analysis.recommendations)

    def test_analyze_knowledge_accepts_malformed_shapes_without_crashing(self) -> None:
        cases = [
            [{"text": "Redis garde un état partagé."}],
            {"text": "EventBus diffuse les événements."},
            "Le replay historique alimente le dashboard.",
            None,
            {"unexpected": object()},
            [None, 42, {"content": "Documentation exploitable."}],
        ]
        for chunks in cases:
            with self.subTest(chunks=type(chunks).__name__):
                facts: list[str] = []
                insights: list[str] = []
                interpretation: list[str] = []
                recommendations: list[str] = []
                limitations: list[str] = []

                BusinessAnalysisEngine._analyze_knowledge(
                    chunks,
                    facts,
                    insights,
                    interpretation,
                    recommendations,
                    limitations,
                )

                self.assertTrue(facts or limitations)

    def test_chat_endpoint_exposes_understanding_and_updates_structured_state(self) -> None:
        client, app = self._client_with_provider(
            FakeReplayProvider(
            FakeSqlConnection(
                rows=[("Meridian Market", 102, 0, 1)],
                description=[("merchant_name",), ("transactions",), ("refused",), ("non_completed",)],
            )
            )
        )

        response = client.post(
            "/api/chat",
            json={"message": "top commerçants le 12 mai", "conversation_id": "state-api"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("understanding", payload["data"])
        self.assertIn("evidence_bundle", payload["data"])
        self.assertIn("reasoning_result", payload["data"])
        self.assertEqual(payload["data"]["understanding"]["action"], "rank")
        state = app.state.chat_memory.state("state-api")
        self.assertEqual(state.last_tool, "get_top_merchants")
        self.assertEqual(state.last_dimension, "merchant")
        self.assertEqual(state.last_business_topic, "merchant")

    def test_natural_answer_exposes_understanding_result_and_interpretation(self) -> None:
        client, _ = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(99, 7, 3))))

        response = client.post("/api/chat", json={"message": "analyse-moi les refus le 12 mai"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("J'ai compris", payload["answer"])
        self.assertIn("Résultat", payload["answer"])
        self.assertIn("Interprétation", payload["answer"])
        self.assertIn("Limite", payload["answer"])
        self.assertIn("reasoning_result", payload["data"])
        self.assertEqual(payload["data"]["reasoning_result"]["confidence"], "high")

    def test_factual_question_answers_only_requested_fact(self) -> None:
        client, _ = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(10, 2, 1))))

        response = client.post("/api/chat", json={"message": "Combien de transactions le 7 mai ?"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("get_transactions_between_dates", payload["tools_used"])
        self.assertEqual(payload["data"]["response_policy"]["depth"], "factual")
        self.assertEqual(payload["answer"], "10 transactions.")
        for unexpected in ("Résultat", "Interprétation", "Limite", "Confiance", "KPI", "snapshot", "tool", "evidence"):
            self.assertNotIn(unexpected, payload["answer"])

    def test_adjacent_explicit_dates_are_counted_as_one_sql_window(self) -> None:
        connection = FakeSqlConnection(row=(10000, 520, 11))
        client, _ = self._client_with_provider(FakeReplayProvider(connection))

        response = client.post("/api/chat", json={"message": "combien de transactions le 7 et le 8 avril ensemble"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["tools_used"], ["get_transactions_between_dates"])
        data = payload["data"]["get_transactions_between_dates"]
        self.assertEqual(data["start_date"], "2026-04-07")
        self.assertEqual(data["end_date"], "2026-04-08")
        self.assertEqual(payload["answer"], "10000 transactions.")
        self.assertNotIn("Redis KPI Snapshot", str(payload["sources"]))

    def test_followup_merchants_reuses_explicit_two_day_context(self) -> None:
        connection = FakeSqlConnection(
            row=(10000, 520, 11),
            rows=[("Cedar Shop", 900, 40, 2)],
            description=[("merchant_name",), ("transactions",), ("refused",), ("non_completed",)],
        )
        client, _ = self._client_with_provider(FakeReplayProvider(connection))
        conversation_id = "april-two-days"

        client.post(
            "/api/chat",
            json={"message": "combien de transactions le 7 et le 8 avril ensemble", "conversation_id": conversation_id},
        )
        response = client.post(
            "/api/chat",
            json={"message": "Montre-moi les commerçants concernés", "conversation_id": conversation_id},
        )

        payload = response.json()
        self.assertEqual(payload["tools_used"], ["get_top_merchants"])
        tool = payload["tool_calls"][0]
        self.assertEqual(tool["arguments"]["start_date"], "2026-04-07")
        self.assertEqual(tool["arguments"]["end_date"], "2026-04-08")
        self.assertIn("Cedar Shop", payload["answer"])

    def test_compare_with_yesterday_after_explicit_window_uses_neighbor_range(self) -> None:
        client, _ = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(10, 2, 1))))
        conversation_id = "april-window-compare"

        client.post(
            "/api/chat",
            json={"message": "combien de transactions le 7 et le 8 avril ensemble", "conversation_id": conversation_id},
        )
        response = client.post("/api/chat", json={"message": "Compare avec hier", "conversation_id": conversation_id})

        payload = response.json()
        self.assertEqual(payload["tools_used"], ["compare_date_ranges"])
        data = payload["data"]["compare_date_ranges"]
        self.assertEqual(data["left"]["start_date"], "2026-04-07")
        self.assertEqual(data["left"]["end_date"], "2026-04-08")
        self.assertEqual(data["right"]["start_date"], "2026-04-05")
        self.assertEqual(data["right"]["end_date"], "2026-04-06")

    def test_frustration_recovery_preserves_context_without_generic_clarification(self) -> None:
        client, _ = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(10, 2, 1))))
        conversation_id = "frustration-recovery"

        client.post("/api/chat", json={"message": "combien de transactions le 7 mai", "conversation_id": conversation_id})
        response = client.post("/api/chat", json={"message": "tu m'emmerdes", "conversation_id": conversation_id})

        payload = response.json()
        self.assertEqual(payload["tools_used"], ["conversation_operation"])
        self.assertEqual(payload["data"]["planner"]["conversation_operation"], "frustration_recovery")
        self.assertIn("Je reprends proprement", payload["answer"])
        self.assertNotIn("vous parlez des transactions", payload["answer"])

    def test_anomaly_count_returns_explicit_unsupported_capability(self) -> None:
        client, _ = self._client_with_provider()

        response = client.post("/api/chat", json={"message": "combien d'anomalies aujourd'hui ?"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["tools_used"], ["unsupported_capability"])
        self.assertEqual(payload["data"]["planner"]["capability"], "unsupported_anomaly_count")
        self.assertNotIn("get_top_anomalies", payload["tools_used"])
        self.assertNotIn("get_kpi_summary", payload["tools_used"])
        self.assertIn("n'est actuellement pas exposée", payload["answer"])
        self.assertIn("nombre d'anomalies", payload["answer"])

    def test_slow_transaction_count_uses_snapshot_kpi_when_available(self) -> None:
        client, _ = self._client_with_provider()

        response = client.post("/api/chat", json={"message": "combien de transactions lentes aujourd'hui ?"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["tools_used"], ["get_kpi_summary"])
        self.assertEqual(payload["data"]["planner"]["capability"], "slow_transaction_count")
        self.assertNotIn("get_transactions_between_dates", payload["tools_used"])
        self.assertIn("transactions lentes", payload["answer"].lower())
        self.assertIn("3", payload["answer"])

    def test_dashboard_performance_question_routes_to_architecture_not_refusals(self) -> None:
        client, _ = self._client_with_provider()

        response = client.post("/api/chat", json={"message": "Pourquoi le dashboard reste rapide avec beaucoup de transactions ?"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "architecture_question")
        self.assertIn("search_redis", payload["tools_used"])
        self.assertNotIn("get_refusal_analysis", payload["tools_used"])
        self.assertIn("ne relit pas toute la base SQL", payload["answer"])
        self.assertIn("Redis/WebSocket", payload["answer"])
        self.assertNotIn("taux de refus", payload["answer"].lower())

    def test_explicit_two_date_refusal_comparison_uses_date_ranges(self) -> None:
        connection = FakeSqlConnection(row=(10, 2, 1))
        client, _ = self._client_with_provider(FakeReplayProvider(connection))

        response = client.post("/api/chat", json={"message": "compare le 7 mai avec 9 mai en termes de taux de refus"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("compare_date_ranges", payload["tools_used"])
        self.assertNotIn("compare_periods", payload["tools_used"])
        data = payload["data"]["compare_date_ranges"]
        self.assertEqual(data["left"]["start_date"], "2026-05-07")
        self.assertEqual(data["right"]["start_date"], "2026-05-09")
        self.assertNotIn("today", str(payload["tool_calls"]))
        self.assertNotIn("7d", str(payload["tool_calls"]))
        self.assertIn("07/05/2026", payload["answer"])
        self.assertIn("09/05/2026", payload["answer"])

    def test_architecture_question_after_transaction_count_ignores_business_context(self) -> None:
        client, _ = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(10, 2, 1))))
        client.post("/api/chat", json={"message": "combien de transactions le 7 mai", "conversation_id": "arch-after-count"})

        response = client.post(
            "/api/chat",
            json={"message": "Pourquoi le dashboard reste rapide avec beaucoup de transactions ?", "conversation_id": "arch-after-count"},
        )

        payload = response.json()
        self.assertEqual(payload["intent"], "architecture_question")
        self.assertIn("search_redis", payload["tools_used"])
        self.assertNotIn("get_refusal_analysis", payload["tools_used"])
        self.assertIn("ne relit pas toute la base SQL", payload["answer"])

    def test_neighbor_period_followup_uses_last_real_business_period(self) -> None:
        client, _ = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(10, 2, 1))))
        client.post("/api/chat", json={"message": "combien de transactions le 7 mai", "conversation_id": "neighbor-demo"})
        client.post(
            "/api/chat",
            json={"message": "Pourquoi le dashboard reste rapide avec beaucoup de transactions ?", "conversation_id": "neighbor-demo"},
        )

        response = client.post(
            "/api/chat",
            json={"message": "Comparer cette période avec une période voisine", "conversation_id": "neighbor-demo"},
        )

        payload = response.json()
        self.assertIn("compare_date_ranges", payload["tools_used"])
        data = payload["data"]["compare_date_ranges"]
        self.assertEqual(data["left"]["start_date"], "2026-05-07")
        self.assertEqual(data["right"]["start_date"], "2026-05-06")
        self.assertNotIn("compare_periods", payload["tools_used"])

    def test_top_merchant_today_is_concise(self) -> None:
        connection = FakeSqlConnection(
            rows=[("Adam Market", 80, 9, 1)],
            description=[("merchant_name",), ("transactions",), ("refused",), ("non_completed",)],
        )
        client, _ = self._client_with_provider(FakeReplayProvider(connection))

        response = client.post("/api/chat", json={"message": "le top merchant aujourd'hui"})

        payload = response.json()
        self.assertIn("get_top_merchants", payload["tools_used"])
        self.assertIn("Adam Market", payload["answer"])
        self.assertIn("détail", payload["answer"])
        for unexpected in ("Lecture métier", "Interprétation", "Limite", "Suite logique"):
            self.assertNotIn(unexpected, payload["answer"])

    def test_multi_period_transaction_count_keeps_both_periods_in_requested_order(self) -> None:
        client, _ = self._client_with_provider()

        today_first = client.post("/api/chat", json={"message": "combien de transactions aujourd'hui et hier"}).json()
        self.assertEqual(today_first["tools_used"], ["multi_period_metric"])
        self.assertEqual(today_first["data"]["planner"]["capability"], "multi_period_metric")
        self.assertEqual([item["period"] for item in today_first["data"]["multi_period_metric"]["values"]], ["today", "yesterday"])
        self.assertIn("735 transactions", today_first["answer"])
        self.assertIn("500 transactions", today_first["answer"])
        self.assertNotIn("get_kpi_summary", today_first["tools_used"])

        yesterday_first = client.post("/api/chat", json={"message": "combien de transactions hier et aujourd'hui"}).json()
        self.assertEqual(yesterday_first["tools_used"], ["multi_period_metric"])
        self.assertEqual([item["period"] for item in yesterday_first["data"]["multi_period_metric"]["values"]], ["yesterday", "today"])
        self.assertTrue(yesterday_first["answer"].find("500 transactions") < yesterday_first["answer"].find("735 transactions"))

    def test_multi_period_tpe_count_keeps_both_periods_in_requested_order(self) -> None:
        client, _ = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(10, 2, 1))))

        today_first = client.post("/api/chat", json={"message": "combien de TPE aujourd'hui et hier"}).json()
        self.assertEqual(today_first["tools_used"], ["multi_period_metric"])
        self.assertEqual(today_first["data"]["planner"]["capability"], "multi_period_metric")
        self.assertEqual(today_first["data"]["multi_period_metric"]["metric_subject"], "tpe")
        self.assertEqual([item["period"] for item in today_first["data"]["multi_period_metric"]["values"]], ["today", "yesterday"])
        self.assertEqual([item["active_tpe"] for item in today_first["data"]["multi_period_metric"]["values"]], [10, 10])
        self.assertIn("10 TPE distincts", today_first["answer"])
        self.assertNotIn("get_kpi_summary", today_first["tools_used"])

        yesterday_first = client.post("/api/chat", json={"message": "combien de TPE hier et aujourd'hui"}).json()
        self.assertEqual(yesterday_first["tools_used"], ["multi_period_metric"])
        self.assertEqual([item["period"] for item in yesterday_first["data"]["multi_period_metric"]["values"]], ["yesterday", "today"])
        self.assertEqual([item["active_tpe"] for item in yesterday_first["data"]["multi_period_metric"]["values"]], [10, 10])
        self.assertNotIn("get_kpi_summary", yesterday_first["tools_used"])

    def test_opinion_followup_uses_last_structured_refusal_result(self) -> None:
        client, app = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(5312, 579, 0))))
        conversation_id = "opinion-from-last-result"

        first = client.post(
            "/api/chat",
            json={
                "message": "quel est votre avis sur le taux de refus des transactions et leur nombre le 16 avril",
                "conversation_id": conversation_id,
            },
        ).json()

        self.assertIn("get_transactions_between_dates", first["tools_used"])
        self.assertEqual(first["data"]["planner"]["capability"], "transaction_refusal_metric")
        self.assertIn("579 refus", first["answer"])
        self.assertIn("10.90 %", first["answer"])
        state = app.state.chat_memory.state(conversation_id)
        self.assertEqual(state.last_result_metric, "refusal_rate")
        self.assertEqual(state.last_numeric_values["rate"], 10.9)
        self.assertEqual(state.last_numeric_values["count"], 579)

        second = client.post(
            "/api/chat",
            json={"message": "cela est-il considéré élevé ?", "conversation_id": conversation_id},
        ).json()

        self.assertEqual(second["tools_used"], ["conversation_operation"])
        self.assertIn("10.90 %", second["answer"])
        self.assertIn("579 refus", second["answer"])
        self.assertNotIn("get_kpi_summary", second["tools_used"])
        self.assertTrue(second["data"]["conversation_operation"]["used_memory_only"])

    def test_contextual_opinion_variants_use_last_result_without_kpi_reload(self) -> None:
        variants = [
            "est-ce élevé ?",
            "est-ce bas ?",
            "cela est-il considéré élevé ?",
            "pourquoi ?",
            "qu'en pensez-vous ?",
            "est-ce inquiétant ?",
            "que recommanderiez-vous ?",
        ]
        for index, message in enumerate(variants):
            with self.subTest(message=message):
                client, _ = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(5312, 579, 0))))
                conversation_id = f"opinion-variant-{index}"
                client.post(
                    "/api/chat",
                    json={
                        "message": "quel est votre avis sur le taux de refus des transactions et leur nombre le 16 avril",
                        "conversation_id": conversation_id,
                    },
                )

                payload = client.post("/api/chat", json={"message": message, "conversation_id": conversation_id}).json()

                self.assertEqual(payload["tools_used"], ["conversation_operation"])
                self.assertEqual(payload["data"]["planner"]["capability"], "analytical_context_opinion")
                self.assertEqual(payload["data"]["planner"]["conversation_operation"], "opinion_on_previous_result")
                self.assertEqual(payload["data"]["conversation_operation"]["operation"], "opinion_on_previous_result")
                self.assertTrue(payload["data"]["conversation_operation"]["used_memory_only"])
                self.assertNotIn("get_kpi_summary", payload["tools_used"])
                self.assertIn("10.90 %", payload["answer"])
                self.assertIn("579 refus", payload["answer"])

    def test_contextual_normal_and_acceptable_opinions_are_nuanced_from_memory(self) -> None:
        variants = ["est-ce normal ?", "est-ce acceptable ?"]
        for index, message in enumerate(variants):
            with self.subTest(message=message):
                client, _ = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(5312, 579, 0))))
                conversation_id = f"opinion-normal-{index}"
                client.post(
                    "/api/chat",
                    json={"message": "quel est le taux de refus le 16 avril", "conversation_id": conversation_id},
                )

                payload = client.post("/api/chat", json={"message": message, "conversation_id": conversation_id}).json()

                self.assertEqual(payload["tools_used"], ["conversation_operation"])
                self.assertTrue(payload["data"]["conversation_operation"]["used_memory_only"])
                self.assertIn("10.90 %", payload["answer"])
                self.assertIn("579 refus", payload["answer"])
                self.assertIn("pas que c'est pleinement normal", payload["answer"])
                self.assertNotIn("get_kpi_summary", payload["tools_used"])

    def test_analytical_context_and_comparison_context_are_persisted(self) -> None:
        client, app = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(5312, 579, 0))))

        first = client.post(
            "/api/chat",
            json={"message": "quel est le taux de refus le 16 avril", "conversation_id": "analytical-context"},
        ).json()
        state = app.state.chat_memory.state("analytical-context")
        self.assertIn("get_transactions_between_dates", first["tools_used"])
        self.assertIsNotNone(state.current_analysis_context)
        self.assertEqual(state.current_analysis_context.rate, 10.9)
        self.assertEqual(state.current_analysis_context.count, 579)
        self.assertEqual(state.current_analysis_context.source, "sql")

        comparison = client.post(
            "/api/chat",
            json={"message": "compare aujourd'hui et hier", "conversation_id": "analytical-context"},
        ).json()
        state = app.state.chat_memory.state("analytical-context")
        self.assertIn("compare_periods", comparison["tools_used"])
        self.assertIsNotNone(state.comparison_context)
        self.assertEqual(state.comparison_context.left_period, "today")
        self.assertEqual(state.comparison_context.right_period, "yesterday")
        self.assertEqual(state.comparison_context.winner, "left")
        self.assertEqual(state.comparison_context.difference, 235)

    def test_comparison_follow_up_uses_memorized_winner_without_tools(self) -> None:
        client, _ = self._client_with_provider()
        conversation_id = "comparison-winner-memory"

        comparison = client.post(
            "/api/chat",
            json={"message": "compare aujourd'hui et hier", "conversation_id": conversation_id},
        ).json()
        self.assertIn("compare_periods", comparison["tools_used"])

        winner = client.post(
            "/api/chat",
            json={"message": "lequel est le plus élevé ?", "conversation_id": conversation_id},
        ).json()
        self.assertEqual(winner["tools_used"], ["conversation_operation"])
        self.assertEqual(winner["data"]["planner"]["conversation_operation"], "comparison_follow_up")
        self.assertIn("today est le plus élevé", winner["answer"])
        self.assertIn("235 transactions", winner["answer"])
        self.assertTrue(winner["data"]["conversation_operation"]["used_memory_only"])

    def test_summary_and_meta_phrases_do_not_launch_sql_or_snapshot(self) -> None:
        client, app = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(5312, 579, 0))))
        conversation_id = "meta-memory-only"

        client.post(
            "/api/chat",
            json={"message": "quel est le taux de refus le 16 avril", "conversation_id": conversation_id},
        )
        summary = client.post(
            "/api/chat",
            json={"message": "résume en une phrase", "conversation_id": conversation_id},
        ).json()
        self.assertEqual(summary["tools_used"], ["conversation_operation"])
        self.assertEqual(summary["data"]["planner"]["conversation_operation"], "summarize")
        self.assertNotIn("get_transactions_between_dates", summary["tools_used"])

        reset = client.post(
            "/api/chat",
            json={"message": "oublions cette journée", "conversation_id": conversation_id},
        ).json()
        self.assertEqual(reset["tools_used"], ["conversation_operation"])
        self.assertEqual(reset["data"]["planner"]["conversation_operation"], "reset_context")
        state = app.state.chat_memory.state(conversation_id)
        self.assertIsNone(state.current_analysis_context)
        self.assertIsNone(state.comparison_context)
        self.assertIsNone(state.last_object)

        topic = client.post(
            "/api/chat",
            json={"message": "parlons des anomalies maintenant", "conversation_id": conversation_id},
        ).json()
        self.assertEqual(topic["tools_used"], ["conversation_operation"])
        self.assertEqual(topic["data"]["planner"]["conversation_operation"], "set_topic")
        self.assertEqual(app.state.chat_memory.state(conversation_id).last_business_topic, "anomalies")

    def test_explicit_subject_change_overwrites_previous_analytical_context(self) -> None:
        client, app = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(5312, 579, 0))))
        conversation_id = "subject-change"

        client.post(
            "/api/chat",
            json={"message": "combien de transactions le 16 avril", "conversation_id": conversation_id},
        )
        tpe = client.post(
            "/api/chat",
            json={"message": "combien de TPE aujourd'hui", "conversation_id": conversation_id},
        ).json()

        self.assertIn("get_tpe_count_by_period", tpe["tools_used"])
        state = app.state.chat_memory.state(conversation_id)
        self.assertEqual(state.last_object, "tpe")
        self.assertEqual(state.last_result_metric, "distinct_count")
        self.assertEqual(state.current_analysis_context.metric, "distinct_count")
        self.assertNotEqual(state.current_analysis_context.metric, "total")

    def test_contextual_transaction_subject_and_opinion_reference_previous_result(self) -> None:
        client, _ = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(10, 2, 1))))
        conversation_id = "transaction-subject-context"

        client.post("/api/chat", json={"message": "ok alors 15 mars", "conversation_id": conversation_id})
        subject = client.post(
            "/api/chat",
            json={"message": "je parle des transactions", "conversation_id": conversation_id},
        ).json()
        self.assertIn("get_transactions_between_dates", subject["tools_used"])
        self.assertEqual(subject["data"]["planner"]["metric_subject"], "transactions")

        opinion = client.post(
            "/api/chat",
            json={"message": "qu'en pensez-vous de cela", "conversation_id": conversation_id},
        ).json()
        self.assertEqual(opinion["tools_used"], ["conversation_operation"])
        self.assertTrue(opinion["data"]["conversation_operation"]["used_memory_only"])
        self.assertNotIn("get_kpi_summary", opinion["tools_used"])

    def test_requested_edge_routes_do_not_fallback_to_incoherent_kpis(self) -> None:
        client, _ = self._client_with_provider()

        anomalies = client.post("/api/chat", json={"message": "combien d'anomalies hier"}).json()
        self.assertEqual(anomalies["tools_used"], ["unsupported_capability"])
        self.assertEqual(anomalies["data"]["planner"]["capability"], "unsupported_anomaly_count")
        self.assertNotIn("get_kpi_summary", anomalies["tools_used"])

        incidents = client.post("/api/chat", json={"message": "analyser les incidents par heure"}).json()
        self.assertIn("get_incidents_by_hour", incidents["tools_used"])
        self.assertNotIn("get_kpi_summary", incidents["tools_used"])

    def test_follow_up_uses_conversation_state_in_visible_answer(self) -> None:
        connection = FakeSqlConnection(
            row=(99, 7, 3),
            rows=[("Adam Market", 80, 9, 1)],
            description=[("merchant_name",), ("transactions",), ("refused",), ("non_completed",)],
        )
        client, app = self._client_with_provider(FakeReplayProvider(connection))
        client.post("/api/chat", json={"message": "analyse-moi les refus le 12 mai", "conversation_id": "follow-visible"})

        response = client.post("/api/chat", json={"message": "et les commerçants", "conversation_id": "follow-visible"})

        payload = response.json()
        self.assertIn("get_top_merchants", payload["tools_used"])
        self.assertIn("Je reprends le contexte précédent", payload["answer"])
        self.assertIn("Adam Market", payload["answer"])
        self.assertTrue(payload["data"]["canonical_understanding"]["context"]["is_follow_up"])
        self.assertEqual(app.state.chat_memory.state("follow-visible").last_tool, "get_top_merchants")

    def test_follow_up_keeps_tpe_count_capability(self) -> None:
        client, app = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(10, 2, 1))))

        first = client.post(
            "/api/chat",
            json={"message": "combien de TPE aujourd'hui", "conversation_id": "tpe-follow-up"},
        )
        self.assertEqual(first.status_code, 200)

        second = client.post(
            "/api/chat",
            json={"message": "et hier", "conversation_id": "tpe-follow-up"},
        )
        self.assertEqual(second.status_code, 200)
        payload = second.json()
        self.assertIn("get_tpe_count_by_period", payload["tools_used"])
        self.assertNotIn("get_top_tpe", payload["tools_used"])
        self.assertEqual(payload["data"]["planner"]["capability"], "tpe_count")
        state = app.state.chat_memory.state("tpe-follow-up")
        self.assertEqual(state.last_action, "count")
        self.assertEqual(state.last_object, "tpe")
        self.assertEqual(state.last_capability, "tpe_count")

    def test_combine_two_previous_results_uses_memory_only(self) -> None:
        connection = FakeSqlConnection(
            row=(10, 2, 1),
            rows=[("Meridian Market", 102, 0, 1)],
            description=[("merchant_name",), ("transactions",), ("refused",), ("non_completed",)],
        )
        client, app = self._client_with_provider(FakeReplayProvider(connection))

        first = client.post("/api/chat", json={"message": "combien de transactions le 7 mai", "conversation_id": "combine-demo"})
        self.assertEqual(first.status_code, 200)
        second = client.post("/api/chat", json={"message": "top commerçants le 12 mai", "conversation_id": "combine-demo"})
        self.assertEqual(second.status_code, 200)

        response = client.post("/api/chat", json={"message": "combine les deux", "conversation_id": "combine-demo"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["tools_used"], ["conversation_operation"])
        self.assertEqual(payload["data"]["planner"]["question_type"], "conversation_operation")
        self.assertEqual(payload["data"]["planner"]["conversation_operation"], "combine")
        self.assertIn("10 transactions", payload["answer"])
        self.assertIn("Meridian Market", payload["answer"])
        self.assertTrue(payload["data"]["conversation_operation"]["used_memory_only"])
        self.assertGreaterEqual(len(app.state.chat_memory.state("combine-demo").recent_outputs), 3)

    def test_compare_previous_results_uses_memory_only(self) -> None:
        client, app = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(10, 2, 1))))

        first = client.post("/api/chat", json={"message": "combien de transactions le 7 mai", "conversation_id": "compare-memory"})
        self.assertEqual(first.status_code, 200)
        app.state.replay_provider = FakeReplayProvider(FakeSqlConnection(row=(20, 4, 1)))
        second = client.post("/api/chat", json={"message": "combien de transactions le 8 mai", "conversation_id": "compare-memory"})
        self.assertEqual(second.status_code, 200)

        response = client.post("/api/chat", json={"message": "compare", "conversation_id": "compare-memory"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["tools_used"], ["conversation_operation"])
        self.assertEqual(payload["data"]["planner"]["conversation_operation"], "compare")
        self.assertIn("L'écart observé", payload["answer"])
        self.assertTrue(payload["data"]["conversation_operation"]["used_memory_only"])
        self.assertNotIn("get_transactions_between_dates", payload["tools_used"])

        winner = client.post("/api/chat", json={"message": "lequel est le plus élevé ?", "conversation_id": "compare-memory"}).json()
        self.assertEqual(winner["tools_used"], ["conversation_operation"])
        self.assertEqual(winner["data"]["planner"]["conversation_operation"], "comparison_follow_up")
        self.assertIn("2026-05-08 à 2026-05-08 est le plus élevé", winner["answer"])
        self.assertIn("10 transactions", winner["answer"])

    def test_change_topic_clears_previous_business_signature(self) -> None:
        client, app = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(10, 2, 1))))
        conversation_id = "change-topic-clear"

        client.post("/api/chat", json={"message": "combien de transactions le 7 mai", "conversation_id": conversation_id})
        payload = client.post("/api/chat", json={"message": "changeons de sujet", "conversation_id": conversation_id}).json()

        state = app.state.chat_memory.state(conversation_id)
        self.assertEqual(payload["tools_used"], ["conversation_operation"])
        self.assertEqual(payload["data"]["planner"]["conversation_operation"], "change_topic")
        self.assertIsNone(state.last_object)
        self.assertIsNone(state.last_metric)
        self.assertIsNone(state.current_analysis_context)
        self.assertIsNone(state.comparison_context)

    def test_month_comparison_uses_controlled_sql_and_reasoned_answer(self) -> None:
        client, _ = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(42, 6, 1))))

        response = client.post("/api/chat", json={"message": "compare janvier et février"})

        payload = response.json()
        self.assertIn("compare_date_ranges", payload["tools_used"])
        self.assertIn("janvier", payload["answer"])
        self.assertIn("fevrier", payload["answer"])
        self.assertIn("L'écart", payload["answer"])
        self.assertTrue(payload["data"]["evidence_bundle"]["has_sql"])

    def test_architecture_answer_uses_rag_without_code_leak(self) -> None:
        client, _ = self._client_with_provider()

        response = client.post("/api/chat", json={"message": "explique Redis dans cette architecture"})

        payload = response.json()
        self.assertIn("search_documentation", payload["tools_used"])
        self.assertIn("J'ai compris", payload["answer"])
        self.assertIn("Redis", payload["answer"])
        self.assertIn("RAG documentaire local", payload["answer"])
        self.assertNotIn("```", payload["answer"])
        self.assertNotIn("from app.", payload["answer"])

    def test_business_answer_does_not_leak_code(self) -> None:
        connection = FakeSqlConnection(
            rows=[("Meridian Market", 102, 0, 1)],
            description=[("merchant_name",), ("transactions",), ("refused",), ("non_completed",)],
        )
        client, _ = self._client_with_provider(FakeReplayProvider(connection))

        response = client.post("/api/chat", json={"message": "quels commerçants posent problème le 12 mai"})

        payload = response.json()
        self.assertIn("get_top_merchants", payload["tools_used"])
        self.assertIn("Meridian Market", payload["answer"])
        self.assertNotIn("SELECT", payload["answer"])
        self.assertNotIn("```", payload["answer"])
        self.assertNotIn(".tsx", payload["answer"])

    def test_conversation_strategy_diagnoses_implicit_concern(self) -> None:
        client, _ = self._client_with_provider()

        response = client.post("/api/chat", json={"message": "Je suis inquiet.", "conversation_id": "concern-demo"})

        payload = response.json()
        self.assertIn("get_kpi_summary", payload["tools_used"])
        self.assertIn("get_top_anomalies", payload["tools_used"])
        self.assertEqual(payload["data"]["conversation_strategy"]["goal"], "reassure_and_investigate")
        self.assertIn("Je comprends ton inquiétude", payload["answer"])
        for internal_word in ("KPI", "snapshot", "confidence", "tool", "reasoning", "evidence"):
            self.assertNotIn(internal_word, payload["answer"])

    def test_conversation_strategy_explains_previous_answer(self) -> None:
        client, _ = self._client_with_provider()
        client.post("/api/chat", json={"message": "Je suis inquiet.", "conversation_id": "why-demo"})

        response = client.post("/api/chat", json={"message": "Pourquoi ?", "conversation_id": "why-demo"})

        payload = response.json()
        self.assertEqual(payload["data"]["conversation_strategy"]["goal"], "explain_previous_answer")
        self.assertIn("get_refusal_analysis", payload["tools_used"])
        self.assertIn("Parce qu", payload["answer"])
        self.assertNotIn("KPI", payload["answer"])

    def test_conversation_strategy_drills_down_from_previous_context(self) -> None:
        client, _ = self._client_with_provider()
        client.post("/api/chat", json={"message": "Je suis inquiet.", "conversation_id": "drill-demo"})

        response = client.post(
            "/api/chat",
            json={"message": "Tu peux me montrer ce qui t'inquiète ?", "conversation_id": "drill-demo"},
        )

        payload = response.json()
        self.assertEqual(payload["data"]["conversation_strategy"]["goal"], "drill_down")
        self.assertIn("get_top_anomalies", payload["tools_used"])
        self.assertIn("mérite attention", payload["answer"])

    def test_business_diagnostic_phrases_do_not_route_to_non_business_intents(self) -> None:
        client, _ = self._client_with_provider()
        cases = [
            ("Je suis inquiet...", "reassure_and_investigate"),
            ("Peut-on dire qu'il y a une panne aujourd'hui ?", "reassure_and_investigate"),
            ("Tu peux me montrer ce qui t'inquiète ?", "drill_down"),
            ("J'ai l'impression qu'il y a un problème sur les paiements aujourd'hui", "reassure_and_investigate"),
        ]
        forbidden_intents = {"greeting", "help", "capabilities", "confidentiality", "code_question"}
        for message, expected_goal in cases:
            with self.subTest(message=message):
                payload = client.post("/api/chat", json={"message": message}).json()

                self.assertNotIn(payload["intent"], forbidden_intents)
                self.assertNotIn("project_code_search", payload["tools_used"])
                self.assertNotEqual(payload["tools_used"], ["greeting"])
                self.assertEqual(payload["data"]["conversation_strategy"]["goal"], expected_goal)
                self.assertTrue(
                    {"get_kpi_summary", "get_refusal_analysis", "get_top_anomalies"}.intersection(payload["tools_used"])
                )

    def test_guardrail_allows_aggregated_sensitive_term_analysis(self) -> None:
        client, _ = self._client_with_provider()

        response = client.post("/api/chat", json={"message": "Combien de transactions avec email manquant aujourd'hui ?"})

        payload = response.json()
        self.assertFalse(payload["data"].get("refused_sensitive_request", False))
        self.assertIn("get_kpi_summary", payload["tools_used"])
        self.assertEqual(payload["data"]["conversation_strategy"]["goal"], "answer_business_question")

    def test_non_regression_existing_business_and_architecture_routes(self) -> None:
        tpe_client, _ = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(10, 2, 1))))
        tpe = tpe_client.post("/api/chat", json={"message": "donne moi le nombre de tpe observés entre 7 mai et 9 mai"}).json()
        self.assertIn("get_tpe_count_by_period", tpe["tools_used"])
        self.assertEqual(tpe["data"]["get_tpe_count_by_period"]["start_date"], "2026-05-07")
        self.assertEqual(tpe["data"]["get_tpe_count_by_period"]["end_date"], "2026-05-09")
        self.assertIn("10 TPE distincts", tpe["answer"])

        non_completed_client, _ = self._client_with_provider(FakeReplayProvider(FakeSqlConnection(row=(20, 3, 5))))
        non_completed = non_completed_client.post("/api/chat", json={"message": "donne moi le taux des transactions non abouties le 9 mai et leur nombre"}).json()
        self.assertIn("get_transactions_between_dates", non_completed["tools_used"])
        self.assertEqual(non_completed["data"]["get_transactions_between_dates"]["metric_subject"], "non_completed")
        self.assertIn("25.0 %", non_completed["answer"])

        merchant_connection = FakeSqlConnection(
            rows=[("Meridian Market", 102, 0, 1)],
            description=[("merchant_name",), ("transactions",), ("refused",), ("non_completed",)],
        )
        merchant_client, _ = self._client_with_provider(FakeReplayProvider(merchant_connection))
        merchants = merchant_client.post("/api/chat", json={"message": "top commerçants le 12 mai"}).json()
        self.assertIn("get_top_merchants", merchants["tools_used"])
        self.assertIn("Meridian Market", merchants["answer"])

        compare_client, _ = self._client_with_provider()
        comparison = compare_client.post("/api/chat", json={"message": "Compare aujourd'hui vs hier"}).json()
        self.assertIn("compare_periods", comparison["tools_used"])

        redis = compare_client.post("/api/chat", json={"message": "Pourquoi Redis est utilisé ?"}).json()
        self.assertIn("search_documentation", redis["tools_used"])
        self.assertIn("Redis", redis["answer"])

        eventbus = compare_client.post("/api/chat", json={"message": "Explique EventBus"}).json()
        self.assertIn("search_documentation", eventbus["tools_used"])
        self.assertIn("EventBus", eventbus["answer"])

    def test_sql_registry_stays_whitelisted_and_non_sensitive(self) -> None:
        dangerous = ("delete", "update", "insert", "drop", "alter", "select *")
        for name, query in QUERY_REGISTRY.items():
            lowered = query.lower()
            self.assertFalse(any(token in lowered for token in dangerous), name)
            selected_block = lowered.split(" from ", 1)[0]
            for field in FORBIDDEN_FIELDS:
                self.assertNotIn(field.lower(), selected_block, name)


if __name__ == "__main__":
    unittest.main()
