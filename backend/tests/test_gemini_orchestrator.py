import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from app.chatbot.gemini_provider import (
    GeminiCircuitBreaker,
    GeminiInvalidResponseError,
    GeminiMetrics,
    GeminiProvider,
    GeminiRateLimitError,
    GeminiTimeoutError,
    GeminiTransientError,
)
from app.chatbot.knowledge_base import KnowledgeBase
from app.chatbot.memory import ConversationMemory
from app.chatbot.service import ChatbotService
from app.chatbot.sql_tools import ChatbotSqlTools
from tests.test_chatbot import FakeEngine, FakeReplayProvider, FakeSqlConnection


@dataclass
class FakeGeminiProvider:
    decisions: list[dict[str, Any] | Exception]
    answers: list[str]
    enabled_value: bool = True
    fallback_enabled: bool = True

    def __post_init__(self) -> None:
        self.name = "gemini"
        self.metrics = GeminiMetrics()
        self.decision_payloads: list[dict[str, Any]] = []
        self.compose_payloads: list[dict[str, Any]] = []

    @property
    def enabled(self) -> bool:
        return self.enabled_value

    async def generate_decision(self, system: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.decision_payloads.append(payload)
        item = self.decisions.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def compose_answer(self, system: str, payload: dict[str, Any]) -> str:
        self.compose_payloads.append(payload)
        return self.answers.pop(0) if self.answers else "Réponse Gemini contrôlée."

    async def health_check(self) -> bool:
        return self.enabled


def settings(**overrides: Any) -> SimpleNamespace:
    base = {
        "chatbot_llm_provider": "fallback",
        "chatbot_response_cache_ttl_seconds": 60,
        "chatbot_tool_calls_per_minute": 10000,
        "chatbot_audit_enabled": False,
        "gemini_enabled": True,
        "gemini_api_key": "fake",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def service(provider: FakeGeminiProvider, connection: FakeSqlConnection | None = None) -> ChatbotService:
    engine = FakeEngine()
    tools = ChatbotSqlTools(engine=engine, provider=FakeReplayProvider(connection or FakeSqlConnection()))
    return ChatbotService(
        tools,
        knowledge_base=KnowledgeBase(),
        memory=ConversationMemory(),
        settings=settings(),
        gemini_provider=provider,
    )


def strict_service(provider: FakeGeminiProvider, connection: FakeSqlConnection | None = None) -> ChatbotService:
    engine = FakeEngine()
    tools = ChatbotSqlTools(engine=engine, provider=FakeReplayProvider(connection or FakeSqlConnection()))
    provider.fail_closed = True
    return ChatbotService(
        tools,
        knowledge_base=KnowledgeBase(),
        memory=ConversationMemory(),
        settings=settings(gemini_fail_closed=True),
        gemini_provider=provider,
    )


class GeminiOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_gemini_disabled_uses_local_fallback_contract(self) -> None:
        provider = FakeGeminiProvider([], [], enabled_value=False)
        response = await service(provider).answer(SimpleNamespace(message="Quel est le taux de refus aujourd'hui ?", period=None, language=None, session_id=None, conversation_id=None))

        self.assertEqual(response.provider, "fallback")
        self.assertEqual(provider.decision_payloads, [])

    async def test_gemini_only_mode_does_not_use_local_fallback_when_disabled(self) -> None:
        provider = FakeGeminiProvider([], [], enabled_value=False, fallback_enabled=False)
        response = await service(provider).answer(SimpleNamespace(message="Quel est le taux de refus aujourd'hui ?", period=None, language="fr", session_id=None, conversation_id=None))

        self.assertEqual(response.provider, "gemini")
        self.assertEqual(response.intent, "gemini_unavailable")
        self.assertEqual(response.tools_used, [])
        self.assertEqual(response.data["provider_used"], "gemini_unavailable")
        self.assertIn("Gemini est indisponible", response.answer)
        self.assertEqual(provider.decision_payloads, [])

    async def test_gemini_strict_mode_still_uses_local_fallback(self) -> None:
        provider = FakeGeminiProvider([], [], enabled_value=False)
        response = await strict_service(provider).answer(SimpleNamespace(message="Quel est le taux de refus aujourd'hui ?", period=None, language="fr", session_id=None, conversation_id=None))

        self.assertEqual(response.provider, "fallback")
        self.assertNotEqual(response.intent, "gemini_unavailable")
        self.assertEqual(response.data["provider_used"], "local_fallback")
        self.assertEqual(provider.decision_payloads, [])

    async def test_bonsoir_never_exposes_gemini_error_when_disabled(self) -> None:
        provider = FakeGeminiProvider([], [], enabled_value=False)
        response = await strict_service(provider).answer(SimpleNamespace(message="bonsoir", period=None, language="fr", session_id=None, conversation_id=None))

        self.assertEqual(response.provider, "fallback")
        self.assertEqual(response.intent, "greeting")
        self.assertIn("Bonsoir", response.answer)
        forbidden = ("Gemini", "ValueError", "provider_used", "gemini_unavailable")
        self.assertFalse(any(token in response.answer for token in forbidden))

    async def test_valid_gemini_sql_decision_uses_controlled_sql_tool(self) -> None:
        provider = FakeGeminiProvider(
            decisions=[{
                "knowledge_family": "sql_metric",
                "capability": "transaction_volume",
                "metric": "total",
                "requires_tool": True,
                "tool_name": "get_transactions_between_dates",
                "tool_arguments": {"start_date": "2026-04-16", "end_date": "2026-04-16"},
                "response_depth": "factual",
                "confidence": "high",
            }],
            answers=["Le 16 avril 2026, il y a eu 10 transactions."],
        )
        response = await service(provider).answer(SimpleNamespace(message="Combien de transactions le 16 avril ?", period=None, language="fr", session_id=None, conversation_id=None))

        self.assertEqual(response.provider, "gemini")
        self.assertIn("10 transactions", response.answer)
        self.assertEqual(response.tools_used, ["get_transactions_between_dates"])
        self.assertEqual(len(provider.compose_payloads), 1)
        self.assertEqual(provider.compose_payloads[0]["tool_results"]["get_transactions_between_dates"]["transactions"], 10)
        self.assertEqual(provider.compose_payloads[0]["response_contract"]["final_answer_author"], "gemini_only")
        self.assertIn("kpi_families", provider.compose_payloads[0]["dashboard_briefing"])
        self.assertEqual(provider.decision_payloads, [])
        self.assertEqual(response.data["gemini_metrics"]["provider_used"], "gemini")

    async def test_gemini_cannot_downgrade_explicit_dates_to_snapshot(self) -> None:
        provider = FakeGeminiProvider(
            decisions=[{
                "knowledge_family": "general_conversation",
                "capability": "transaction_volume",
                "metric": "total",
                "requires_tool": False,
                "tool_name": "get_kpi_summary",
                "tool_arguments": {"period": "today"},
                "response_depth": "factual",
                "confidence": "medium",
            }],
            answers=["Les 7 et 8 avril 2026 ensemble totalisent 10000 transactions."],
        )
        response = await service(provider, FakeSqlConnection(row=(10000, 520, 11))).answer(
            SimpleNamespace(
                message="combien de transactions le 7 et le 8 avril ensemble",
                period=None,
                language="fr",
                session_id=None,
                conversation_id=None,
            )
        )

        self.assertEqual(response.provider, "gemini")
        self.assertEqual(response.tools_used, ["get_transactions_between_dates"])
        tool_result = provider.compose_payloads[0]["tool_results"]["get_transactions_between_dates"]
        self.assertEqual(tool_result["start_date"], "2026-04-07")
        self.assertEqual(tool_result["end_date"], "2026-04-08")
        self.assertEqual(tool_result["transactions"], 10000)

    async def test_timeout_falls_back_to_local_pipeline(self) -> None:
        provider = FakeGeminiProvider([GeminiTimeoutError("timeout")], [])
        response = await service(provider).answer(SimpleNamespace(message="Combien de transactions aujourd'hui ?", period=None, language=None, session_id=None, conversation_id=None))

        self.assertEqual(response.provider, "fallback")
        self.assertEqual(response.data["provider_used"], "local_fallback")
        self.assertNotEqual(response.tools_used, ["execute_sql"])

    async def test_gemini_greeting_uses_single_composition_call(self) -> None:
        provider = FakeGeminiProvider(
            decisions=[{"knowledge_family": "general_conversation", "requires_tool": False, "response_depth": "brief"}],
            answers=["Bonjour, je peux t'aider sur les KPI TPE."],
        )
        response = await service(provider).answer(SimpleNamespace(message="Bonjour", period=None, language="fr", session_id=None, conversation_id=None))

        self.assertEqual(response.provider, "gemini")
        self.assertEqual(response.answer, "Bonjour, je peux t'aider sur les KPI TPE.")
        self.assertEqual(provider.decision_payloads, [])
        self.assertEqual(len(provider.compose_payloads), 1)

    async def test_gemini_conversation_capability_is_not_rejected(self) -> None:
        provider = FakeGeminiProvider(
            decisions=[{"knowledge_family": "general_conversation", "capability": "greeting", "requires_tool": False, "response_depth": "brief"}],
            answers=["Bonjour, je suis prêt à analyser le dashboard."],
        )
        response = await service(provider).answer(SimpleNamespace(message="bonjour", period=None, language="fr", session_id=None, conversation_id=None))

        self.assertEqual(response.provider, "gemini")
        self.assertEqual(response.intent, "greeting")
        self.assertEqual(response.tools_used, ["greeting"])
        self.assertIn("Bonjour", response.answer)
        self.assertEqual(len(provider.compose_payloads), 1)

    async def test_runtime_redis_question_uses_runtime_status_tool(self) -> None:
        provider = FakeGeminiProvider(
            decisions=[{"knowledge_family": "runtime_status", "capability": "redis_runtime_status", "requires_tool": True, "response_depth": "factual"}],
            answers=["Redis est en ligne et le cache partagé est actif."],
        )
        response = await service(provider).answer(SimpleNamespace(message="Pourquoi Redis est offline ?", period=None, language="fr", session_id=None, conversation_id=None))

        self.assertEqual(response.provider, "gemini")
        self.assertIn("get_redis_runtime_status", response.tools_used)
        self.assertLessEqual(len(provider.decision_payloads) + len(provider.compose_payloads), 2)

    async def test_gemini_unsupported_slow_metric_is_remapped_to_snapshot_kpi(self) -> None:
        provider = FakeGeminiProvider(
            decisions=[{
                "knowledge_family": "sql_metric",
                "capability": "unsupported_slow_transaction_count",
                "metric": "slow_count",
                "object": "transaction",
                "requires_tool": True,
                "tool_name": "unsupported_capability",
                "response_depth": "factual",
            }],
            answers=["Le snapshot indique 3 transactions lentes aujourd'hui."],
        )
        response = await service(provider).answer(SimpleNamespace(message="combien de transactions lentes aujourd'hui ?", period=None, language="fr", session_id=None, conversation_id=None))

        self.assertEqual(response.provider, "gemini")
        self.assertEqual(response.tools_used, ["get_kpi_summary"])
        self.assertEqual(provider.compose_payloads[0]["tool_results"]["get_kpi_summary"]["slow_transactions"], 3)
        self.assertIn("3 transactions lentes", response.answer)

    async def test_gemini_governed_historical_fraud_count_uses_unsupported_evidence(self) -> None:
        provider = FakeGeminiProvider(
            decisions=[],
            answers=["Le comptage historique des fraudes n'est pas expose par les outils disponibles."],
        )
        response = await service(provider).answer(
            SimpleNamespace(
                message="combien de fraudes entre le 7 et 11 avril",
                period=None,
                language="fr",
                session_id=None,
                conversation_id=None,
            )
        )

        self.assertEqual(response.provider, "gemini")
        self.assertEqual(provider.decision_payloads, [])
        self.assertEqual(response.tools_used, ["unsupported_capability"])
        result = provider.compose_payloads[0]["tool_results"]["unsupported_capability"]
        self.assertEqual(result["capability"], "unsupported_historical_fraud_timeout_count")
        self.assertEqual(result["requested_metric"], "fraud_timeout_count")
        self.assertEqual(result["temporal"]["start_date"], "2026-04-07")
        self.assertEqual(result["temporal"]["end_date"], "2026-04-11")

    async def test_gemini_governed_historical_slow_transactions_uses_sql_evidence(self) -> None:
        provider = FakeGeminiProvider(
            decisions=[],
            answers=["Le comptage historique SQL ne donne pas le nombre de transactions lentes."],
        )
        response = await service(provider, FakeSqlConnection(row=(321, 18, 2))).answer(
            SimpleNamespace(
                message="combien de transactions lentes entre le 7 et 11 avril",
                period=None,
                language="fr",
                session_id=None,
                conversation_id=None,
            )
        )

        self.assertEqual(response.provider, "gemini")
        self.assertEqual(provider.decision_payloads, [])
        self.assertEqual(response.tools_used, ["get_transactions_between_dates"])
        result = provider.compose_payloads[0]["tool_results"]["get_transactions_between_dates"]
        self.assertEqual(result["transactions"], 321)
        self.assertEqual(result["metric_subject"], "transactions_lentes")
        self.assertFalse(result["requested_metric_available"])

    async def test_invalid_decision_is_repaired_once(self) -> None:
        provider = FakeGeminiProvider(
            decisions=[
                {"knowledge_family": "sql_metric", "tool_name": "execute_sql"},
                {"knowledge_family": "runtime_status", "capability": "redis_runtime_status", "requires_tool": True},
            ],
            answers=["Redis est en ligne."],
        )
        response = await service(provider).answer(SimpleNamespace(message="Statut Redis ?", period=None, language="fr", session_id=None, conversation_id=None))

        self.assertEqual(response.provider, "gemini")
        self.assertEqual(len(provider.decision_payloads), 2)
        self.assertIn("get_redis_runtime_status", response.tools_used)

    async def test_context_contains_previous_memory_on_second_turn(self) -> None:
        provider = FakeGeminiProvider(
            decisions=[
                {"knowledge_family": "sql_metric", "capability": "transaction_volume", "metric": "total", "requires_tool": True, "tool_name": "get_transactions_between_dates", "tool_arguments": {"start_date": "2026-04-16", "end_date": "2026-04-16"}},
                {"knowledge_family": "contextual_opinion", "requires_tool": False, "response_depth": "analytical"},
            ],
            answers=["Oui, c'est un volume à surveiller."],
        )
        chat = service(provider)
        session_id = "gemini-memory"
        await chat.answer(SimpleNamespace(message="Combien de transactions le 16 avril ?", period=None, language="fr", session_id=session_id, conversation_id=None))
        response = await chat.answer(SimpleNamespace(message="Est-ce élevé ?", period=None, language="fr", session_id=session_id, conversation_id=None))

        self.assertEqual(response.provider, "gemini")
        second_context = provider.compose_payloads[-1]["conversation_context"]
        self.assertTrue(second_context["recent_turns"])
        self.assertTrue(second_context["last_result_summary"] or second_context["analytical_context"])

    async def test_invented_sql_tool_is_rejected_then_local_refuses_free_sql(self) -> None:
        provider = FakeGeminiProvider(
            decisions=[{"knowledge_family": "sql_metric", "requires_tool": True, "tool_name": "execute_sql", "tool_arguments": {"query": "SELECT * FROM cards"}}],
            answers=[],
        )
        response = await service(provider).answer(SimpleNamespace(message="Execute ce SQL: SELECT * FROM cards", period=None, language="fr", session_id=None, conversation_id=None))

        self.assertEqual(response.provider, "fallback")
        self.assertNotIn("SELECT * FROM cards", response.answer)

    async def test_architecture_question_uses_business_rag_then_gemini_composes(self) -> None:
        provider = FakeGeminiProvider(
            decisions=[{"knowledge_family": "architecture", "capability": "architecture_explanation", "requires_tool": True, "response_depth": "analytical"}],
            answers=["Redis sert de cache partagé pour éviter de recalculer les KPI à chaque affichage."],
        )
        response = await service(provider).answer(SimpleNamespace(message="Pourquoi Redis est utilisé ?", period=None, language="fr", session_id=None, conversation_id=None))

        self.assertEqual(response.provider, "gemini")
        self.assertIn("Redis", response.answer)
        self.assertIn("search_documentation", response.tools_used)

    async def test_code_question_uses_code_rag(self) -> None:
        provider = FakeGeminiProvider(
            decisions=[{"knowledge_family": "code", "requires_tool": True, "response_depth": "analytical"}],
            answers=["Le calcul est localisé dans les composants et services du projet."],
        )
        response = await service(provider).answer(SimpleNamespace(message="Où est calculé le taux de refus dans le code ?", period=None, language="fr", session_id=None, conversation_id=None))

        self.assertEqual(response.provider, "gemini")
        self.assertIn("project_code_search", response.tools_used)

    async def test_sensitive_values_are_masked_before_gemini(self) -> None:
        provider = FakeGeminiProvider(
            decisions=[{"knowledge_family": "general_conversation", "requires_tool": False, "response_depth": "brief"}],
            answers=["Je peux aider avec des données agrégées."],
        )
        await service(provider).answer(SimpleNamespace(message="Ma carte 4111111111111111 a un souci", period=None, language="fr", session_id=None, conversation_id=None))

        serialized = str(provider.decision_payloads[-1])
        self.assertIn("[masked_card]", serialized)
        self.assertNotIn("4111111111111111", serialized)

    async def test_invalid_empty_gemini_answer_falls_back(self) -> None:
        provider = FakeGeminiProvider(
            decisions=[{"knowledge_family": "architecture", "capability": "architecture_explanation", "requires_tool": True}],
            answers=[""],
        )
        response = await service(provider).answer(SimpleNamespace(message="Explique le replay", period=None, language="fr", session_id=None, conversation_id=None))

        self.assertEqual(response.provider, "fallback")

    async def test_invalid_tool_argument_falls_back(self) -> None:
        provider = FakeGeminiProvider(
            decisions=[{
                "knowledge_family": "sql_metric",
                "requires_tool": True,
                "tool_name": "get_transactions_between_dates",
                "tool_arguments": {"start_date": "not-a-date", "end_date": "2026-04-16"},
            }],
            answers=[],
        )
        response = await service(provider).answer(SimpleNamespace(message="Combien de transactions ?", period=None, language="fr", session_id=None, conversation_id=None))

        self.assertEqual(response.provider, "fallback")

    async def test_invalid_gemini_json_falls_back(self) -> None:
        provider = FakeGeminiProvider([GeminiInvalidResponseError("bad json")], [])
        response = await service(provider).answer(SimpleNamespace(message="Pourquoi Redis ?", period=None, language="fr", session_id=None, conversation_id=None))

        self.assertEqual(response.provider, "fallback")

    async def test_answer_sanitizer_remains_last_layer(self) -> None:
        provider = FakeGeminiProvider(
            decisions=[{"knowledge_family": "architecture", "capability": "architecture_explanation", "requires_tool": True}],
            answers=["Voici du SQL: SELECT * FROM secret_table"],
        )
        response = await service(provider).answer(SimpleNamespace(message="Explique l'architecture", period=None, language="fr", session_id=None, conversation_id=None))

        self.assertNotIn("SELECT", response.answer)


class GeminiProviderRetryTests(unittest.IsolatedAsyncioTestCase):
    def provider_with_script(self, script: list[object], max_retries: int) -> GeminiProvider:
        provider = GeminiProvider.__new__(GeminiProvider)
        provider._enabled = True
        provider._client = object()
        provider._timeout = 1
        provider._max_retries = max_retries
        provider.metrics = GeminiMetrics()
        provider.circuit_breaker = GeminiCircuitBreaker(failure_threshold=5, cooldown_seconds=1)
        calls = {"count": 0}

        def generate_sync(system: str, payload: dict[str, Any]) -> tuple[str, dict[str, int]]:
            item = script[calls["count"]]
            calls["count"] += 1
            if isinstance(item, Exception):
                raise item
            return str(item), {"input_tokens": 3, "output_tokens": 2}

        provider._generate_sync = generate_sync
        provider.calls = calls
        return provider

    async def test_rate_limit_retries_once_then_succeeds(self) -> None:
        provider = self.provider_with_script([GeminiRateLimitError("429"), "ok"], max_retries=1)

        answer = await provider.compose_answer("system", {"message": "hello"})

        self.assertEqual(answer, "ok")
        self.assertEqual(provider.calls["count"], 2)
        self.assertEqual(provider.metrics.rate_limit_total, 1)

    async def test_transient_503_retries_then_raises_for_fallback(self) -> None:
        provider = self.provider_with_script([GeminiTransientError("503"), GeminiTransientError("503")], max_retries=1)

        with self.assertRaises(GeminiTransientError):
            await provider.compose_answer("system", {"message": "hello"})

        self.assertEqual(provider.calls["count"], 2)
        self.assertEqual(provider.metrics.fallback_total, 1)

    def test_generate_sync_disables_automatic_function_calling(self) -> None:
        captured: dict[str, Any] = {}

        class Models:
            def generate_content(self, **kwargs: Any) -> Any:
                captured.update(kwargs)
                return SimpleNamespace(text="ok", usage_metadata=SimpleNamespace(prompt_token_count=1, candidates_token_count=1))

        provider = GeminiProvider.__new__(GeminiProvider)
        provider._client = SimpleNamespace(models=Models())
        provider._model = "gemini-2.5-flash"
        provider._temperature = 0.2
        provider._max_output_tokens = 128

        text, usage = provider._generate_sync("system", {"message": "hello"})

        self.assertEqual(text, "ok")
        self.assertTrue(captured["config"]["automatic_function_calling"]["disable"])
        self.assertEqual(usage["input_tokens"], 1)
