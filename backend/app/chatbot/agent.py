from __future__ import annotations

from datetime import date
import json
from time import perf_counter
from typing import Any

from app.chatbot.answer_composer import AnswerComposer
from app.chatbot.answer_sanitizer import AnswerSanitizer
from app.chatbot.adaptive_response_policy import AdaptiveResponsePolicy, AdaptiveResponsePolicyEngine
from app.chatbot.business_analysis import BusinessAnalysis, BusinessAnalysisEngine
from app.chatbot.business_communication import BusinessCommunicationEngine
from app.chatbot.business_understanding import BusinessUnderstandingEngine
from app.chatbot.conversation_operation import ConversationOperation, ConversationOperationEngine
from app.chatbot.conversation_strategy import ConversationStrategy, ConversationStrategyEngine
from app.chatbot.governance import audit_chatbot_event, tool_rate_limiter
from app.chatbot.knowledge_base import KnowledgeBase
from app.chatbot.llm_provider import OptionalLlmClient
from app.chatbot.memory import ConversationMemory, ConversationState
from app.chatbot.project_code_search import ProjectCodeSearchTool
from app.chatbot.reasoning import ReasoningEngine
from app.chatbot.response_builder import ResponseBuilder, period_label, source
from app.chatbot.router import ChatIntent, detect_intent, normalize_text
from app.chatbot.sql_tools import ChatbotSqlTools
from app.chatbot.temporal_resolver import TemporalRange, TemporalResolver
from app.chatbot.tool_catalog import ALLOWED_TOOL_NAMES, TOOL_CATALOG
from app.chatbot.tool_selector import ToolCall, ToolSelector
from app.schemas.chat import ChatRequest, ChatResponse, ChatSource


class DataAnalystAgent:
    def __init__(
        self,
        tools: ChatbotSqlTools,
        knowledge_base: KnowledgeBase,
        memory: ConversationMemory,
        llm: OptionalLlmClient | None = None,
        tool_calls_per_minute: int = 120,
        audit_enabled: bool = True,
    ) -> None:
        self._tools = tools
        self._kb = knowledge_base
        self._memory = memory
        self._llm = llm
        self._builder = ResponseBuilder()
        self._composer = AnswerComposer()
        self._communication = BusinessCommunicationEngine()
        self._analysis = BusinessAnalysisEngine()
        self._understanding = BusinessUnderstandingEngine()
        self._conversation_operation = ConversationOperationEngine()
        self._strategy = ConversationStrategyEngine()
        self._response_policy = AdaptiveResponsePolicyEngine()
        self._tool_selector = ToolSelector()
        self._reasoning = ReasoningEngine()
        self._tool_calls_per_minute = tool_calls_per_minute
        self._audit_enabled = audit_enabled

    async def answer(self, request: ChatRequest) -> ChatResponse:
        started = perf_counter()
        session_id = self._memory.ensure_session(request.session_id or request.conversation_id)
        intent = detect_intent(request.message, request.period, request.language)
        history = self._memory.compact_context(session_id)
        conversation_state = self._memory.state(session_id)
        self._memory.add(session_id, "user", request.message)
        temporal = self._resolve_temporal(request.message, history)
        canonical_understanding = self._understanding.understand(request.message, intent, temporal, conversation_state)
        conversation_operation = self._conversation_operation.resolve(request.message, canonical_understanding, conversation_state)
        conversation_strategy = self._strategy.decide(request.message, canonical_understanding, conversation_state, intent)
        response_policy = self._response_policy.decide(request.message, canonical_understanding, conversation_strategy)
        self._last_understanding = canonical_understanding
        self._last_conversation_operation = conversation_operation
        self._last_strategy = conversation_strategy
        self._last_response_policy = response_policy

        if intent.sensitive and self._should_refuse_sensitive(canonical_understanding):
            answer, follow_up = self._builder.security_refusal(intent)
            self._memory.add(session_id, "assistant", answer)
            response = ChatResponse(
                answer=answer,
                intent=intent.intent,
                period=intent.period,
                session_id=session_id,
                conversation_id=session_id,
                sources=[source("security", "Sensitive data policy")],
                data={"refused_sensitive_request": True},
                follow_up=follow_up,
                plan=["Detect sensitive request", "Refuse protected data", "Offer aggregated alternative"],
                tool_calls=[],
                reasoning_summary="Sensitive-data guardrail triggered before any data access.",
                provider="fallback",
                tools_used=[],
                evidence=[self._evidence("reasoning", "sensitive_guardrail", "refused", True, intent.period, "high")],
                detected_period=intent.period,
                confidence="high",
            )
            self._audit(request, response, perf_counter() - started)
            return response

        plan = await self._plan(request, intent, history, conversation_state, canonical_understanding, conversation_strategy, conversation_operation)
        evidence, tool_calls, sources = await self._execute_plan(plan, intent)
        evidence["planner"] = self._structured_plan(request, intent, plan, canonical_understanding, conversation_operation)
        if canonical_understanding:
            evidence["canonical_understanding"] = canonical_understanding.to_dict()
            evidence["understanding"] = canonical_understanding.to_dict()
        if conversation_strategy:
            evidence["conversation_strategy"] = conversation_strategy.to_dict()
        evidence["response_policy"] = response_policy.to_dict()
        understanding = self._business_query_from_plan(plan)
        if understanding and "understanding" not in evidence:
            evidence["understanding"] = understanding
        evidence_bundle = self._reasoning.build_evidence_bundle(evidence, tool_calls)
        reasoning_result = self._reasoning.reason(canonical_understanding, evidence_bundle, conversation_state)
        evidence["evidence_bundle"] = evidence_bundle.to_dict()
        evidence["reasoning_result"] = reasoning_result.to_dict()
        business_analysis = self._analysis.analyze(
            understanding=canonical_understanding,
            state=conversation_state,
            bundle=evidence_bundle,
            tool_results=evidence,
            knowledge=self._knowledge_from_evidence(evidence),
            reasoning=reasoning_result,
        )
        evidence["business_analysis"] = business_analysis.to_dict()
        temporal = self._temporal_from_plan(plan)
        if temporal:
            evidence["temporal"] = temporal
        evidence["evidence_collector"] = self._collect_evidence_summary(evidence, tool_calls)
        answer, follow_up, reasoning_summary = await self._synthesize(
            request,
            intent,
            history,
            plan,
            evidence,
            conversation_state,
            evidence_bundle,
            reasoning_result,
            business_analysis,
            conversation_strategy,
            response_policy,
        )
        answer = self._sanitize_final_answer(
            answer,
            response_type=self._response_type(canonical_understanding, intent),
            code_allowed=self._is_code_request(request.message, intent),
        )
        self._memory.add(session_id, "assistant", answer)
        evidence_items = self._evidence_records(evidence, intent)

        response = ChatResponse(
            answer=answer,
            intent=intent.intent,
            period=intent.period,
            session_id=session_id,
            conversation_id=session_id,
            sources=sources,
            data=evidence,
            follow_up=follow_up,
            plan=[call.purpose for call in plan],
            tool_calls=tool_calls,
            reasoning_summary=reasoning_summary,
            provider=self._llm.provider_name if self._llm else "fallback",
            tools_used=[item["name"] for item in tool_calls if item.get("name") != "tool_rate_limited"],
            evidence=evidence_items,
            detected_period=intent.period,
            confidence=self._confidence(evidence_items),
        )
        state_business_query = (
            canonical_understanding.to_dict()
            if canonical_understanding and canonical_understanding.conversation_kind == "business"
            else understanding if canonical_understanding is None else None
        )
        plan_business_query = self._business_query_from_plan(plan)
        if state_business_query and plan_business_query:
            state_business_query = {
                **state_business_query,
                "action": plan_business_query.get("action", state_business_query.get("action")),
                "object": plan_business_query.get("object", state_business_query.get("object")),
                "metric": plan_business_query.get("metric", state_business_query.get("metric")),
                "dimension": plan_business_query.get("dimension", state_business_query.get("dimension")),
                "filters": plan_business_query.get("filters", state_business_query.get("filters") or {}),
                "period": plan_business_query.get("period", state_business_query.get("period")),
                "business_query": plan_business_query,
            }
        self._memory.update_state(
            session_id,
            business_query=state_business_query,
            tool=next((item.get("name") for item in tool_calls if item.get("name") != "tool_rate_limited"), None),
            evidence=evidence_bundle.to_dict(),
            result_summary=self._result_summary(evidence, tool_calls, intent),
            answer=answer,
        )
        self._audit(request, response, perf_counter() - started)
        return response

    async def _plan(
        self,
        request: ChatRequest,
        intent: ChatIntent,
        history: str,
        state: ConversationState | None = None,
        understanding: Any | None = None,
        strategy: ConversationStrategy | None = None,
        operation: ConversationOperation | None = None,
    ) -> list[ToolCall]:
        if understanding is None:
            temporal = self._resolve_temporal(request.message, history)
            understanding = self._understanding.understand(request.message, intent, temporal, state)
        if operation is None:
            operation = self._conversation_operation.resolve(request.message, understanding, state)
        if strategy is None:
            strategy = self._strategy.decide(request.message, understanding, state, intent)
        self._last_understanding = understanding
        self._last_conversation_operation = operation
        self._last_strategy = strategy
        social_plan = self._simple_social_plan(request, intent)
        if social_plan:
            return social_plan
        plan = self._tool_selector.from_understanding(understanding, intent.period, strategy, state, operation)
        override_plan = self._conversation_plan_override(request.message, intent, understanding, plan)
        if override_plan:
            return override_plan
        if plan:
            return plan
        legacy_plan = self._heuristic_plan(request, intent, history)
        if legacy_plan:
            return legacy_plan
        llm_plan = await self._llm_plan(request, intent, history)
        if llm_plan:
            return llm_plan
        return plan or [ToolCall("clarify_question", {}, "Demander une precision avant d'utiliser les outils")]

    def _simple_social_plan(self, request: ChatRequest, intent: ChatIntent) -> list[ToolCall]:
        clean_text = normalize_text(request.message).strip(" ?!.")
        if clean_text in {"bonjour", "salut", "hello", "hi"}:
            return [ToolCall("greeting", {"kind": "greeting"}, "Repondre naturellement sans RAG ni SQL")]
        if clean_text == "bonsoir":
            return [ToolCall("greeting", {"kind": "bonsoir"}, "Saluer et presenter le perimetre d'aide")]
        if clean_text in {"comment vas-tu", "comment vas tu", "ca va", "tu vas bien", "how are you"}:
            return [ToolCall("greeting", {"kind": "small_talk"}, "Repondre au small talk sans outil data")]
        if clean_text in {"tu peux m'aider", "tu peux m aider", "peux tu m'aider", "peux tu m aider", "aide moi", "help", "help me"}:
            return [ToolCall("greeting", {"kind": "help"}, "Expliquer le perimetre d'aide sans RAG ni SQL")]
        if clean_text in {"merci", "merci beaucoup", "thanks", "ok merci"}:
            return [ToolCall("greeting", {"kind": "thanks"}, "Repondre naturellement sans RAG ni SQL")]
        if clean_text in {"que peux tu faire", "tu fais quoi", "capacites", "capabilities", "comment tu peux aider"}:
            return [ToolCall("greeting", {"kind": "capabilities"}, "Repondre naturellement sans RAG ni SQL")]
        return []

    def _conversation_plan_override(
        self,
        message: str,
        intent: ChatIntent,
        understanding: Any | None,
        plan: list[ToolCall],
    ) -> list[ToolCall]:
        normalized = normalize_text(message)
        tool_names = [call.name for call in plan]
        if "compare" in normalized or "compar" in normalized:
            if "hier" in normalized and tool_names == ["multi_period_metric"]:
                return [ToolCall("compare_periods", {"left": "today", "right": "yesterday"}, "Comparer aujourd'hui et hier")]
        if (
            getattr(understanding, "action", None) == "count"
            and (getattr(understanding, "object", None) == "tpe" or "tpe" in normalized)
            and (not plan or tool_names == ["get_kpi_summary"])
        ):
            temporal = getattr(understanding, "period", None)
            if not isinstance(temporal, dict) or not temporal.get("start_date") or not temporal.get("end_date"):
                window = self._period_window(intent.period)
                temporal = {
                    "start_date": window["start_date"],
                    "end_date": window["end_date"],
                    "period_key": intent.period,
                    "granularity": "day",
                    "source": "period_window",
                }
            query = getattr(understanding, "business_query", None)
            business_query = query.to_dict() if query is not None else None
            if business_query is not None:
                business_query = {
                    **business_query,
                    "action": "count",
                    "object": "tpe",
                    "metric": "distinct_count",
                    "dimension": None,
                    "filters": {},
                    "period": temporal,
                }
            return [
                ToolCall(
                    "get_tpe_count_by_period",
                    {
                        "period": str(temporal.get("period_key") or intent.period),
                        "start_date": temporal.get("start_date"),
                        "end_date": temporal.get("end_date"),
                        "temporal": temporal,
                        **({"business_query": business_query} if business_query else {}),
                    },
                    "Compter les TPE distincts sur la période dashboard résolue",
                )
            ]
        return []

    async def _llm_plan(self, request: ChatRequest, intent: ChatIntent, history: str) -> list[ToolCall] | None:
        if not self._llm or not self._llm.enabled:
            return None
        system = (
            "You are a banking dashboard data analyst planner. Return JSON only. "
            "You may choose only the provided backend tools. "
            "Never generate SQL. Never request sensitive fields. Plan 1 to 5 tool calls."
        )
        user = json.dumps(
            {
                "question": request.message,
                "normalized_question": normalize_text(request.message),
                "intent_hint": intent.intent,
                "period": intent.period,
                "language": intent.language,
                "conversation_history": history,
                "tool_catalog": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                        "example": tool.example,
                        "output": tool.output,
                        "source": tool.source,
                    }
                    for tool in TOOL_CATALOG
                ],
                "output_schema": {"steps": [{"tool": "tool_name", "arguments": {}, "purpose": "why this tool is needed"}]},
            },
            ensure_ascii=False,
        )
        payload = await self._llm.complete_json(system, user)
        if not payload:
            return None
        steps = payload.get("steps") or [
            {"tool": name, "arguments": {"period": intent.period}, "purpose": f"Use secure tool {name}"}
            for name in payload.get("required_tools", [])
        ]
        calls: list[ToolCall] = []
        for step in steps[:5]:
            name = str(step.get("tool") or "")
            if name not in ALLOWED_TOOL_NAMES:
                continue
            args = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
            args.setdefault("period", intent.period)
            calls.append(ToolCall(name=name, arguments=args, purpose=str(step.get("purpose") or name)))
        return calls or None

    def _heuristic_plan(self, request: ChatRequest, intent: ChatIntent, history: str) -> list[ToolCall]:
        """compatibility fallback only.

        Normal routing must come from BusinessUnderstanding -> ConversationOperation
        -> ToolSelector. This fallback is reached only when that path produced no
        executable plan, and should stay limited to legacy intent aliases.
        """
        current_text = normalize_text(request.message)
        period = intent.period
        clean_text = current_text.strip(" ?!.")
        if clean_text == "bonsoir":
            return [ToolCall("greeting", {"kind": "bonsoir"}, "Saluer et presenter le perimetre d'aide")]
        if intent.intent in {"greeting", "small_talk", "help", "help_request", "thanks", "capabilities"}:
            return [ToolCall("greeting", {"kind": intent.intent}, "Repondre naturellement sans RAG ni SQL")]
        if clean_text in {"bonjour", "salut", "hello", "hi"}:
            return [ToolCall("greeting", {"kind": "greeting"}, "Saluer et presenter le perimetre d'aide")]
        if clean_text in {"comment vas-tu", "comment vas tu", "ca va", "tu vas bien", "how are you"}:
            return [ToolCall("greeting", {"kind": "small_talk"}, "Repondre au small talk sans outil data")]
        if clean_text in {"tu peux m'aider", "tu peux m aider", "peux tu m'aider", "peux tu m aider", "aide moi", "help", "help me"}:
            return [ToolCall("greeting", {"kind": "help"}, "Expliquer le perimetre d'aide sans RAG ni SQL")]
        if intent.intent in {"code_request", "code_question"}:
            return [ToolCall("project_code_search", {"query": request.message, "limit": 3}, "Rechercher le code explicitement demande dans le projet")]
        if any(token in current_text for token in ("select ", "select*", " from ", "drop ", "delete ", "update ", "insert ", "execute ce sql", "execute sql")):
            return [
                ToolCall("search_project_documentation", {"query": "SQL security policy controlled tools no free SQL"}, "Refuser le SQL libre et expliquer les outils controles")
            ]
        days = self._extract_last_n_days(current_text)
        if days:
            return [ToolCall("get_transaction_volume", {"days": days}, f"Compter les transactions des {days} derniers jours via SQL controle")]
        if intent.intent in {"kpi_summary", "kpi_question"}:
            return [ToolCall("get_kpi_summary", {"period": period}, "Lire les KPI courants")]
        if intent.intent in {"top_merchants", "merchant_question"}:
            return [
                ToolCall("get_top_merchants", {"period": period, "limit": 5}, "Classer les commercants par volume et refus"),
                ToolCall("get_kpi_summary", {"period": period}, "Contextualiser les tops avec le volume global"),
            ]
        if intent.intent in {"top_anomalies", "anomaly_question"}:
            return [
                ToolCall("get_top_anomalies", {"period": period, "limit": 5}, "Identifier les entites les plus risquees"),
                ToolCall("get_incidents_by_hour", {"period": period}, "Ajouter le contexte temporel des incidents"),
            ]
        if intent.intent == "top_tpe":
            return [ToolCall("get_top_tpe", {"period": period, "limit": 5}, "Identifier les TPE les plus incidentes")]
        if intent.intent == "merchant_details":
            query = intent.merchant_query or request.message
            return [
                ToolCall("search_merchant", {"query": query, "limit": 5}, "Retrouver le commercant demande"),
                ToolCall("get_merchant_details", {"merchant_name_or_id": query, "period": period}, "Analyser son profil risque dans la projection"),
                ToolCall("get_top_anomalies", {"period": period, "limit": 5}, "Comparer avec le top anomalies"),
            ]
        if intent.intent in {"affiliation", "affiliation_question"}:
            return [
                ToolCall("get_affiliation_summary", {}, "Lire le stock d'affiliations projete"),
                ToolCall("search_documentation", {"query": "affiliations projection comportement commercants"}, "Expliquer pourquoi ce stock est une projection metier"),
            ]
        if intent.intent in {"status_distribution", "incidents_by_hour"}:
            tool = "get_status_distribution" if intent.intent == "status_distribution" else "get_incidents_by_hour"
            return [ToolCall(tool, {"period": period}, "Analyser la distribution demandee")]
        if intent.intent.startswith("explain_") or intent.intent in {"limitations", "confidentiality", "architecture_question"}:
            return [ToolCall("search_documentation", {"query": request.message}, "Chercher les informations projet pertinentes avant de repondre")]
        return [ToolCall("clarify_question", {}, "Demander une precision avant d'utiliser les outils")]

    async def _execute_plan(
        self,
        plan: list[ToolCall],
        intent: ChatIntent,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[ChatSource]]:
        evidence: dict[str, Any] = {}
        tool_calls: list[dict[str, Any]] = []
        source_map: dict[tuple[str, str], ChatSource] = {}
        for call in plan[:6]:
            if call.name not in ALLOWED_TOOL_NAMES:
                continue
            if not tool_rate_limiter.allow("chatbot_tools", self._tool_calls_per_minute):
                evidence["tool_rate_limited"] = {"limit_per_minute": self._tool_calls_per_minute}
                tool_calls.append({"name": "tool_rate_limited", "arguments": {}, "purpose": "Tool-call rate limit reached"})
                break
            result = await self._execute_tool(call, intent)
            evidence[call.name] = result
            tool_calls.append({"name": call.name, "arguments": call.arguments, "purpose": call.purpose})
            for item in self._sources_for_tool(call.name):
                source_map[(item.type, item.label)] = item
        return evidence, tool_calls, list(source_map.values())

    async def _execute_tool(self, call: ToolCall, intent: ChatIntent) -> Any:
        args = dict(call.arguments)
        period = args.get("period") or intent.period
        if call.name == "greeting":
            return {"ok": True, "kind": args.get("kind") or intent.intent}
        if call.name == "clarify_question":
            return {"ok": True}
        if call.name == "conversation_operation":
            return {
                "operation": args.get("operation"),
                "answer": args.get("answer"),
                "follow_up": list(args.get("follow_up") or []),
                "summary": args.get("summary"),
                "used_memory_only": bool(args.get("used_memory_only", True)),
                "referenced_outputs": int(args.get("referenced_outputs") or 0),
                "period": args.get("period"),
                "object": args.get("object"),
                "metric": args.get("metric"),
                "metric_subject": args.get("metric_subject"),
                "comparison_context": args.get("comparison_context") if isinstance(args.get("comparison_context"), dict) else None,
            }
        if call.name == "unsupported_capability":
            return {
                "capability": args.get("capability"),
                "requested": args.get("requested"),
                "requested_metric": args.get("requested_metric"),
                "metric_subject": args.get("metric_subject"),
                "alternatives": list(args.get("alternatives") or []),
                "period": args.get("period") or period,
                **({"temporal": args.get("temporal")} if isinstance(args.get("temporal"), dict) else {}),
            }
        if call.name == "multi_period_metric":
            return await self._multi_period_metric(args, intent)
        if call.name == "project_code_search":
            return ProjectCodeSearchTool.search(str(args.get("query") or ""), limit=int(args.get("limit") or 3))
        if call.name in {"get_current_snapshot", "get_dashboard_snapshot"}:
            return await self._tools.get_dashboard_snapshot(period)
        if call.name in {"get_kpi_by_period", "get_kpi_summary"}:
            result = await self._tools.get_kpi_summary(period)
            if "metric_subject" in args:
                result["metric_subject"] = args["metric_subject"]
            if "business_query" in args:
                result["business_query"] = args["business_query"]
                result["query_understanding"] = args["business_query"]
            return result
        if call.name in {"get_transactions_last_n_days", "get_transaction_volume"}:
            return await self._tools.get_transactions_last_n_days(int(args.get("days") or 1))
        if call.name == "get_transactions_between_dates":
            result = await self._tools.get_transactions_between_dates(str(args.get("start_date") or ""), str(args.get("end_date") or ""))
            if "temporal" in args:
                result["temporal"] = args["temporal"]
            if "business_query" in args:
                result["business_query"] = args["business_query"]
                result["query_understanding"] = args["business_query"]
            if "metric_subject" in args:
                result["metric_subject"] = args["metric_subject"]
                if args["metric_subject"] == "transactions_lentes":
                    result["requested_metric"] = "transactions_lentes"
                    result["requested_metric_available"] = False
                    result["metric_limitation"] = "Le comptage historique SQL contrôlé retourne le volume/refus/non-abouties, mais pas le nombre de transactions lentes."
            return result
        if call.name == "get_tpe_count_by_period":
            result = await self._tools.get_tpe_count_by_period(str(args.get("start_date") or ""), str(args.get("end_date") or ""))
            if "temporal" in args:
                result["temporal"] = args["temporal"]
            if "business_query" in args:
                result["business_query"] = args["business_query"]
                result["query_understanding"] = args["business_query"]
            return result
        if call.name == "compare_periods":
            return await self._tools.compare_periods(str(args.get("left") or "today"), str(args.get("right") or "yesterday"))
        if call.name == "compare_date_ranges":
            result = await self._tools.compare_date_ranges(
                str(args.get("left_start") or ""),
                str(args.get("left_end") or ""),
                str(args.get("right_start") or ""),
                str(args.get("right_end") or ""),
                str(args.get("left_label") or "left"),
                str(args.get("right_label") or "right"),
            )
            if "business_query" in args:
                result["business_query"] = args["business_query"]
                result["query_understanding"] = args["business_query"]
            return result
        if call.name == "get_top_merchants":
            return await self._tools.get_top_merchants(period, int(args.get("limit") or 5), args.get("start_date"), args.get("end_date"))
        if call.name == "get_top_anomalies":
            return await self._tools.get_top_anomalies(period, int(args.get("limit") or 5))
        if call.name == "get_top_tpe":
            return await self._tools.get_top_tpe(period, int(args.get("limit") or 5), args.get("start_date"), args.get("end_date"))
        if call.name == "get_status_distribution":
            return await self._tools.get_status_distribution(period)
        if call.name == "get_incidents_by_hour":
            return await self._tools.get_incidents_by_hour(period, args.get("start_date"), args.get("end_date"))
        if call.name == "get_hourly_incidents":
            return await self._tools.get_hourly_incidents(period, args.get("start_date"), args.get("end_date"))
        if call.name in {"get_affiliation_summary", "get_affiliation_stock"}:
            return await self._tools.get_affiliation_summary()
        if call.name == "get_affiliations":
            return await self._tools.get_affiliations()
        if call.name == "get_refusal_analysis":
            return await self._tools.get_refusal_analysis(period)
        if call.name == "get_success_analysis":
            return await self._tools.get_success_analysis(period)
        if call.name in {"get_risk_summary", "get_risk_alerts"}:
            return await self._tools.get_risk_summary(period)
        if call.name == "get_redis_runtime_status":
            snapshot = await self._tools.get_dashboard_snapshot(period)
            replay = snapshot.get("replay_status", {})
            return {
                "redis_configured": replay.get("redis_configured"),
                "redis_available": replay.get("redis_available"),
                "cache_backend": replay.get("cache_backend"),
                "redis_reason": replay.get("redis_reason"),
                "redis_latency_ms": replay.get("redis_latency_ms"),
                "redis_url_hint": "redis://localhost:6379/0",
                "replay_run_id": replay.get("replay_run_id"),
            }
        if call.name == "get_data_quality_status":
            snapshot = await self._tools.get_dashboard_snapshot(period)
            return snapshot.get("data_quality") or {
                "reconciliation_status": snapshot.get("replay_status", {}).get("reconciliation_status"),
                "completion_rate": snapshot.get("replay_status", {}).get("completion_rate"),
            }
        if call.name in {"get_replay_status", "get_period_status"}:
            snapshot = await self._tools.get_dashboard_snapshot(period)
            return snapshot.get("replay_status", {})
        if call.name == "get_dashboard_metadata":
            return {
                "architecture": "event-driven",
                "cache": "RedisKpiCache with InMemory fallback",
                "event_bus": "in-memory EventBus",
                "data_source": "SQL Server portfolio_demo replay",
                "sensitive_data_policy": "aggregated data only; no free SQL",
            }
        if call.name == "get_merchant_details":
            return await self._tools.get_merchant_details(str(args.get("merchant_name_or_id") or ""), period)
        if call.name == "search_merchant":
            return await self._tools.search_merchant(str(args.get("query") or ""), int(args.get("limit") or 5))
        if call.name == "explain_metric":
            return await self._tools.explain_metric(str(args.get("metric_name") or ""))
        if call.name in {
            "search_docs",
            "search_documentation",
            "search_project_documentation",
            "search_dashboard_explanations",
            "search_architecture",
            "search_redis",
            "search_eventbus",
            "explain_dashboard_component",
            "explain_redis_architecture",
            "explain_eventbus_architecture",
            "explain_replay_architecture",
        }:
            return [{"title": chunk.title, "text": chunk.text, "tags": chunk.tags} for chunk in self._kb.search(str(args.get("query") or ""), 3)]
        if call.name == "generate_dashboard_opinion":
            snapshot = await self._tools.get_dashboard_snapshot(period)
            return self._dashboard_opinion(snapshot)
        return None

    @staticmethod
    def _sources_for_tool(name: str) -> list[ChatSource]:
        if name in {
            "get_current_snapshot",
            "get_dashboard_snapshot",
            "get_kpi_by_period",
            "get_kpi_summary",
            "get_affiliation_summary",
            "get_affiliation_stock",
            "get_affiliations",
            "get_status_distribution",
            "get_top_anomalies",
            "get_merchant_details",
            "get_refusal_analysis",
            "get_success_analysis",
            "get_risk_summary",
            "get_risk_alerts",
            "get_replay_status",
            "get_redis_runtime_status",
            "get_data_quality_status",
            "get_period_status",
            "compare_periods",
        }:
            return [source("snapshot", "Redis KPI Snapshot")]
        if name in {"get_top_merchants", "get_top_tpe", "get_incidents_by_hour", "get_hourly_incidents", "search_merchant", "get_transactions_last_n_days", "get_transaction_volume", "get_transactions_between_dates", "get_tpe_count_by_period", "compare_date_ranges"}:
            return [source("snapshot", "Dashboard projection"), source("sql", "portfolio_demo / dbo.demo_transactions")]
        if name in {"conversation_operation", "unsupported_capability", "clarify_question", "greeting"}:
            return []
        if name == "multi_period_metric":
            return [source("snapshot", "Redis KPI Snapshot")]
        if name.startswith("search_") or name.startswith("explain_") or name in {"explain_metric", "get_dashboard_metadata", "generate_dashboard_opinion"}:
            return [source("docs", "Local project knowledge base")]
        return []

    async def _synthesize(
        self,
        request: ChatRequest,
        intent: ChatIntent,
        history: str,
        plan: list[ToolCall],
        evidence: dict[str, Any],
        state: ConversationState | None,
        evidence_bundle: Any,
        reasoning_result: Any,
        business_analysis: BusinessAnalysis | None,
        conversation_strategy: ConversationStrategy | None = None,
        response_policy: AdaptiveResponsePolicy | None = None,
    ) -> tuple[str, list[str], str]:
        if "conversation_operation" in evidence:
            payload = evidence["conversation_operation"] if isinstance(evidence["conversation_operation"], dict) else {}
            return (
                str(payload.get("answer") or "Je reprends le contexte précédent depuis la mémoire conversationnelle."),
                list(payload.get("follow_up") or []),
                str(payload.get("summary") or "Conversation operation answered from memory only."),
            )
        communicated = self._communication.compose(
            analysis=business_analysis,
            strategy=conversation_strategy,
            policy=response_policy,
            state=state,
            language=intent.language,
        )
        if communicated:
            return communicated
        if conversation_strategy and conversation_strategy.goal == "explain_system" and any(key in evidence for key in ("search_redis", "search_docs")):
            return self._synthesize_performance_answer(intent, evidence)
        composed = self._composer.compose(
            understanding=getattr(self, "_last_understanding", None),
            analysis=business_analysis,
            policy=response_policy,
            language=intent.language,
        )
        if composed:
            return composed
        llm_answer = await self._llm_synthesize(request, intent, history, plan, evidence)
        if llm_answer:
            return llm_answer, self._followups(intent), "LLM synthesized the answer from validated tool evidence."
        return self._local_synthesize(intent, evidence)

    async def _llm_synthesize(
        self,
        request: ChatRequest,
        intent: ChatIntent,
        history: str,
        plan: list[ToolCall],
        evidence: dict[str, Any],
    ) -> str | None:
        if not self._llm or not self._llm.enabled:
            return None
        system = (
            "You are an AI data analyst for a banking TPE dashboard. "
            "Answer naturally using only the provided tool evidence. "
            "Use an evidence-first style: do not add facts that are absent from the evidence payload. "
            "Mention the period, say when data is replay/projection, refuse sensitive data, and never invent values."
        )
        user = json.dumps(
            {
                "question": request.message,
                "language": intent.language,
                "period": intent.period,
                "history": history,
                "plan": [call.purpose for call in plan],
                "evidence": evidence,
            },
            ensure_ascii=False,
            default=str,
        )
        return await self._llm.synthesize(system, user)

    def _local_synthesize(self, intent: ChatIntent, evidence: dict[str, Any]) -> tuple[str, list[str], str]:
        if "greeting" in evidence:
            kind = evidence["greeting"].get("kind") if isinstance(evidence["greeting"], dict) else intent.intent
            if kind == "small_talk":
                answer, follow = self._builder.small_talk(intent)
                return answer, follow, "Small talk handled without RAG, SQL or code snippets."
            if kind in {"help", "help_request"}:
                answer, follow = self._builder.help_request(intent)
                return answer, follow, "Help request handled without RAG, SQL or code snippets."
            if kind == "thanks":
                answer, follow = self._builder.thanks(intent)
                return answer, follow, "Thanks handled without RAG, SQL or code snippets."
            if kind == "capabilities":
                answer, follow = self._builder.capabilities(intent)
                return answer, follow, "Capabilities handled without RAG, SQL or code snippets."
            if kind == "bonsoir":
                return (
                    "Bonsoir ! Comment puis-je vous aider concernant les transactions, les KPI, les commerçants, les TPE, les anomalies ou l'architecture du dashboard ?",
                    ["Voir le taux de refus ?", "Expliquer Redis ?"],
                    "Evening greeting handled without RAG, SQL or code snippets.",
                )
            answer, follow = self._builder.greeting(intent)
            return answer, follow, "Greeting handled without RAG, SQL or code snippets."
        if "conversation_operation" in evidence:
            payload = evidence["conversation_operation"] if isinstance(evidence["conversation_operation"], dict) else {}
            return (
                str(payload.get("answer") or "Je reprends le contexte précédent depuis la mémoire conversationnelle."),
                list(payload.get("follow_up") or []),
                str(payload.get("summary") or "Conversation operation answered from memory only."),
            )
        if "unsupported_capability" in evidence:
            return self._synthesize_unsupported_capability(intent, evidence["unsupported_capability"])
        if "multi_period_metric" in evidence:
            return self._synthesize_multi_period_metric(intent, evidence["multi_period_metric"])
        if "clarify_question" in evidence:
            answer, follow = self._builder.clarification(intent)
            return answer, follow, "ConfidenceGuard asked a clarification instead of inventing an answer."
        if "get_refusal_analysis" in evidence:
            return self._synthesize_refusal_diagnosis(intent, evidence["get_refusal_analysis"], evidence.get("compare_periods"))
        if "compare_date_ranges" in evidence:
            return self._synthesize_comparison(intent, evidence["compare_date_ranges"], evidence.get("get_top_anomalies"))
        if "compare_periods" in evidence:
            return self._synthesize_comparison(intent, evidence["compare_periods"], evidence.get("get_top_anomalies"))
        if "generate_dashboard_opinion" in evidence:
            return self._synthesize_dashboard_opinion(intent, evidence["generate_dashboard_opinion"])
        transaction_volume = evidence.get("get_transaction_volume") or evidence.get("get_transactions_last_n_days") or evidence.get("get_transactions_between_dates")
        if transaction_volume:
            return self._synthesize_last_days(intent, transaction_volume)
        if "get_tpe_count_by_period" in evidence:
            return self._synthesize_tpe_count(intent, evidence["get_tpe_count_by_period"])
        kpi_data = evidence.get("get_kpi_summary") or evidence.get("get_kpi_by_period")
        if kpi_data and any(key in evidence for key in ("search_redis", "search_eventbus")):
            return self._synthesize_performance_answer(intent, evidence)
        affiliation_data = evidence.get("get_affiliations") or evidence.get("get_affiliation_summary")
        if affiliation_data:
            answer, follow = self._builder.affiliation(intent, affiliation_data)
            if any(key.startswith("search_") for key in evidence):
                answer += " Cette explication s'appuie aussi sur les regles documentees de projection du stock." if intent.language == "fr" else " This also uses the documented stock projection rules."
            return answer, follow, "Combined affiliation snapshot with documentation."
        if "get_top_merchants" in evidence:
            rows, error_answer = self._rows_or_sql_error(intent, evidence["get_top_merchants"])
            if error_answer:
                return error_answer
            answer, follow = self._builder.rows(intent, "Top merchants" if intent.language == "en" else "Top commercants", rows, "merchant")
            answer = self._replace_with_sql_period_label(answer, intent, evidence["get_top_merchants"])
            if "get_kpi_summary" in evidence:
                total = evidence["get_kpi_summary"].get("total_transactions")
                if total:
                    answer += f"\nContexte: {total} transactions rejouees sur la periode." if intent.language == "fr" else f"\nContext: {total} transactions replayed in the period."
            return answer, follow, "Combined merchant ranking with KPI context."
        if "get_top_anomalies" in evidence:
            answer, follow = self._builder.rows(intent, "Top anomalies", evidence["get_top_anomalies"], "merchant")
            incidents = self._extract_rows(evidence.get("get_incidents_by_hour"))
            if incidents:
                peak = max(incidents, key=lambda row: int(row.get("refused") or 0))
                answer += f"\nPic horaire observe: {peak.get('hour')}h avec {peak.get('refused')} refus." if intent.language == "fr" else f"\nObserved hourly peak: {peak.get('hour')}h with {peak.get('refused')} refusals."
            return answer, follow, "Compared anomaly ranking with incident distribution."
        if "get_top_tpe" in evidence:
            rows, error_answer = self._rows_or_sql_error(intent, evidence["get_top_tpe"])
            if error_answer:
                return error_answer
            answer, follow = self._builder.rows(intent, "Top TPE", rows, "terminal")
            answer = self._replace_with_sql_period_label(answer, intent, evidence["get_top_tpe"])
            return answer, follow, "Used terminal ranking tool."
        if "get_incidents_by_hour" in evidence:
            rows, error_answer = self._rows_or_sql_error(intent, evidence["get_incidents_by_hour"])
            if error_answer:
                return error_answer
            answer, follow = self._builder.incidents(intent, rows)
            answer = self._replace_with_sql_period_label(answer, intent, evidence["get_incidents_by_hour"])
            return answer, follow, "Used hourly incident distribution."
        if "project_code_search" in evidence:
            rows = evidence.get("project_code_search") or []
            if not rows:
                return "Je n'ai pas trouvé d'extrait de code pertinent dans le projet.", ["Chercher la route /api/chat ?", "Chercher le provider LLM ?"], "Project code search returned no match."
            first = rows[0]
            answer = f"Fichier : {first.get('path')}\n\n```text\n{first.get('excerpt')}\n```"
            return answer, ["Où est la route /api/chat ?", "Où est le provider LLM ?"], "Returned short project code excerpt for an explicit code question."
        search_key = next((key for key in evidence if key.startswith("search_")), None)
        if search_key:
            chunks = evidence.get(search_key) or []
            text_chunks = [
                type("KnowledgeLike", (), {"title": item.get("title"), "text": item.get("text"), "tags": item.get("tags", [])})
                for item in chunks
            ]
            answer, follow = self._builder.docs(intent, text_chunks)
            return answer, follow, "Used local RAG documentation."
        if kpi_data:
            answer, follow = self._builder.kpi_summary(intent, kpi_data)
            return answer, follow, "Used KPI snapshot as fallback context."
        answer, follow = self._builder.unknown(intent)
        return answer, follow, "No relevant tool evidence was available."

    async def _multi_period_metric(self, args: dict[str, Any], intent: ChatIntent) -> dict[str, Any]:
        periods = [item for item in list(args.get("periods") or []) if isinstance(item, dict)]
        metric = str(args.get("metric") or "total")
        metric_subject = str(args.get("metric_subject") or "")
        business_query = args.get("business_query") if isinstance(args.get("business_query"), dict) else {}
        object_name = str(business_query.get("object") or business_query.get("entity") or "")
        values: list[dict[str, Any]] = []
        for item in periods[:4]:
            period_key = str(item.get("period_key") or "")
            if period_key not in {"today", "yesterday", "7d", "30d", "quarter", "year"}:
                continue
            if metric_subject == "tpe" or object_name == "tpe":
                tpe = await self._tools.get_tpe_count_by_period(str(item.get("start_date") or ""), str(item.get("end_date") or ""))
                values.append(
                    {
                        "period": period_key,
                        "label": item.get("label_fr") or period_key,
                        "start_date": item.get("start_date"),
                        "end_date": item.get("end_date"),
                        "active_tpe": int(tpe.get("active_tpe") or tpe.get("transactions") or 0),
                        "source": tpe.get("source") or "sql",
                        "data_available": tpe.get("data_available"),
                    }
                )
                continue
            kpi = await self._tools.get_kpi_summary(period_key)
            transactions = int(kpi.get("total_transactions") or 0)
            refusal_rate = float(kpi.get("refusal_rate") or 0)
            refused = self._refused_count_from_kpi(kpi, transactions, refusal_rate)
            values.append(
                {
                    "period": period_key,
                    "label": item.get("label_fr") or period_key,
                    "start_date": item.get("start_date"),
                    "end_date": item.get("end_date"),
                    "transactions": transactions,
                    "refused": refused,
                    "refusal_rate": refusal_rate,
                    "source": "snapshot",
                }
            )
        return {
            "metric": metric,
            "metric_subject": metric_subject,
            "values": values,
            "business_query": business_query or args.get("business_query"),
            "data_available": bool(values),
        }

    @staticmethod
    def _refused_count_from_kpi(kpi: dict[str, Any], transactions: int, refusal_rate: float) -> int:
        status_rows = kpi.get("snapshot", {}).get("status_distribution") if isinstance(kpi.get("snapshot"), dict) else None
        if isinstance(status_rows, list):
            for row in status_rows:
                if isinstance(row, dict) and "refus" in str(row.get("label") or "").lower():
                    return int(row.get("value") or 0)
        return round(transactions * refusal_rate / 100)

    @staticmethod
    def _is_code_request(message: str, intent: ChatIntent) -> bool:
        text = normalize_text(message)
        return intent.intent in {"code_request", "code_question"} or any(token in text for token in ("montre le code", "montre-moi le code", "show code", "code du composant", "source code"))

    def _rows_or_sql_error(self, intent: ChatIntent, value: Any) -> tuple[list[dict[str, Any]], tuple[str, list[str], str] | None]:
        if not isinstance(value, dict):
            return list(value or []), None
        source_name = value.get("source")
        if source_name == "sql_unavailable":
            if intent.language == "en":
                return [], ("SQL Server connection is unavailable.", ["Show current KPI?"], "SQL Server connection unavailable for SQL ranking tool.")
            return [], ("Connexion SQL Server indisponible.", ["Voir les KPI courants ?"], "Connexion SQL Server indisponible pour l'outil SQL.")
        if source_name == "sql_error":
            if intent.language == "en":
                return [], ("The controlled SQL tool encountered an internal query error. The backend logs contain the real SQL exception.", ["Show current KPI?"], "Controlled SQL ranking tool failed.")
            return [], ("Erreur interne de l'outil SQL contrôlé. L'exception réelle est disponible dans les logs backend.", ["Voir les KPI courants ?"], "Erreur SQL controlee avec exception detaillee dans les logs backend.")
        return self._extract_rows(value), None

    def _synthesize_unsupported_capability(self, intent: ChatIntent, value: Any) -> tuple[str, list[str], str]:
        payload = value if isinstance(value, dict) else {}
        requested = str(payload.get("requested") or "cette métrique")
        alternatives = [str(item) for item in list(payload.get("alternatives") or []) if str(item).strip()]
        if intent.language == "en":
            answer = (
                f"I understand that you are looking for {requested}. "
                "This metric is not currently exposed by the available tools, so I prefer to answer explicitly rather than return an unrelated KPI."
            )
            follow = alternatives or ["Ask for another available metric", "Analyze a related signal"]
            return answer, follow[:2], "Capability matrix returned an explicit unsupported capability."
        answer = (
            f"Je comprends que vous recherchez {requested}. "
            "Cette métrique n'est actuellement pas exposée par les outils disponibles. "
            "Je préfère donc vous répondre explicitement plutôt que de retourner un KPI sans rapport."
        )
        return answer, alternatives[:2] or ["Poser une autre question métier", "Analyser un indicateur disponible"], "Capability matrix returned an explicit unsupported capability."

    def _synthesize_tpe_count(self, intent: ChatIntent, data: dict[str, Any]) -> tuple[str, list[str], str]:
        source_name = data.get("source")
        start = data.get("start_date", "?")
        end = data.get("end_date", "?")
        if source_name == "sql_unavailable":
            return "Connexion SQL Server indisponible.", ["Voir les KPI courants ?"], "Connexion SQL Server indisponible pour le comptage TPE."
        if source_name == "sql_error":
            return "Erreur interne de l'outil SQL contrôlé. L'exception réelle est disponible dans les logs backend.", ["Voir les KPI courants ?"], "Erreur SQL controlee avec exception detaillee dans les logs backend."
        active_tpe = int(data.get("active_tpe") or 0)
        if active_tpe == 0:
            return f"Aucun TPE trouvé pour cette période ({start} à {end}).", ["Essayer une autre période ?", "Voir les KPI courants ?"], "Comptage SQL controle des TPE retourne zero."
        return (
            f"Sur la période {start} à {end}, l'outil SQL contrôlé a compté {active_tpe} TPE distincts observés.",
            ["Voir le top TPE ?", "Comparer avec une autre période ?"],
            "Comptage SQL controle des TPE distincts.",
        )

    def _synthesize_multi_period_metric(self, intent: ChatIntent, data: dict[str, Any]) -> tuple[str, list[str], str]:
        values = [item for item in list(data.get("values") or []) if isinstance(item, dict)]
        metric = str(data.get("metric") or "total")
        metric_subject = str(data.get("metric_subject") or "")
        if not values:
            return "Je n'ai pas pu lire les périodes demandées.", ["Préciser la période ?"], "No multi-period values were available."
        parts: list[str] = []
        for item in values:
            label = str(item.get("label") or item.get("period") or "période")
            if metric_subject == "tpe":
                parts.append(f"{label} : {int(item.get('active_tpe') or 0)} TPE distincts")
            elif metric in {"refusal_rate", "refused_count"}:
                parts.append(f"{label} : {item.get('refused', 0)} refus ({float(item.get('refusal_rate') or 0):.2f} %)")
            else:
                parts.append(f"{label} : {int(item.get('transactions') or 0)} transactions")
        answer = "; ".join(parts) + "."
        return answer, ["Comparer ces deux périodes ?", "Voir les commerçants concernés ?"], "Returned each requested period separately from the multi-period metric evidence."

    @staticmethod
    def _extract_rows(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, dict):
            rows = value.get("rows")
            return list(rows) if isinstance(rows, list) else []
        return list(value or [])

    @staticmethod
    def _replace_with_sql_period_label(answer: str, intent: ChatIntent, value: Any) -> str:
        if not isinstance(value, dict):
            return answer
        diagnostic = value.get("diagnostic") if isinstance(value.get("diagnostic"), dict) else {}
        start = diagnostic.get("start_date")
        end = diagnostic.get("end_date")
        if not start or not end:
            return answer
        label = str(start) if start == end else f"{start} à {end}" if intent.language == "fr" else f"{start} to {end}"
        current_label = period_label(intent)
        return (
            answer.replace(f"({current_label})", f"({label})")
            .replace(f"Sur {intent.period}", f"Sur {label}")
            .replace(f"For {intent.period}", f"For {label}")
        )

    @staticmethod
    def _sanitize_final_answer(answer: str, response_type: str = "business", code_allowed: bool = False) -> str:
        return AnswerSanitizer.sanitize(answer, response_type=response_type, code_allowed=code_allowed)

    @staticmethod
    def _should_refuse_sensitive(understanding: Any) -> bool:
        query = getattr(understanding, "business_query", None)
        if query and query.action in {"count", "rate", "rank", "compare"}:
            return False
        return True

    @staticmethod
    def _response_type(understanding: Any, intent: ChatIntent) -> str:
        kind = getattr(understanding, "conversation_kind", None)
        if kind in {"business", "code", "architecture", "security"}:
            return str(kind)
        if intent.sensitive or intent.intent == "confidentiality":
            return "security"
        if intent.intent in {"code_request", "code_question"}:
            return "code"
        if intent.intent == "architecture_question":
            return "architecture"
        return "business"

    @staticmethod
    def _structured_plan(
        request: ChatRequest,
        intent: ChatIntent,
        plan: list[ToolCall],
        understanding: Any | None,
        operation: ConversationOperation | None,
    ) -> dict[str, Any]:
        tool_names = [call.name for call in plan]
        needs_sql = any(
            name in {"get_top_merchants", "get_top_tpe", "get_incidents_by_hour", "get_transactions_last_n_days", "get_transaction_volume", "get_transactions_between_dates", "get_tpe_count_by_period", "compare_date_ranges"}
            for name in tool_names
        )
        needs_rag = any(name.startswith("search_") or name.startswith("explain_") for name in tool_names)
        needs_redis = any(
            name in {"get_dashboard_snapshot", "get_current_snapshot", "get_kpi_summary", "get_kpi_by_period", "multi_period_metric", "get_affiliation_summary", "get_affiliation_stock"}
            for name in tool_names
        )
        normalized = normalize_text(request.message)
        knowledge_family = getattr(understanding, "knowledge_family", None)
        capability = operation.capability if operation is not None else getattr(understanding, "capability", None)
        if capability == "unsupported_slow_transaction_count" and "get_kpi_summary" in tool_names:
            capability = "slow_transaction_count"
        question_type = "architecture" if knowledge_family == "architecture" else "data"
        if operation is not None:
            question_type = "conversation_operation"
        elif intent.intent in {"follow_up_question"} or normalized.strip(" ?!.") in {"et hier", "pourquoi", "compare avec hier", "et les anomalies", "et les commercants"}:
            question_type = "follow_up_question"
        elif intent.intent in {"greeting", "small_talk", "help", "help_request", "thanks", "capabilities"}:
            question_type = intent.intent
        elif intent.intent == "unknown" and "clarify_question" in tool_names:
            question_type = "out_of_scope"
        elif intent.intent == "unknown":
            question_type = "follow_up_question"
        elif intent.intent in {"code_request", "code_question"}:
            question_type = "code_question"
        elif any(token in normalized for token in ("avis", "opinion", "credible", "crédible", "pense", "soutenance", "ameliorer l'ui", "ameliorer ui")):
            question_type = "opinion"
        elif needs_rag and (needs_sql or needs_redis):
            question_type = "mixed"
        return {
            "question_type": question_type,
            "intent": intent.intent,
            "period": intent.period,
            "knowledge_family": knowledge_family,
            "capability": capability,
            "conversation_operation": operation.kind if operation is not None else None,
            "comparison_period": "yesterday" if "compare_periods" in tool_names else None,
            "temporal": DataAnalystAgent._temporal_from_plan(plan),
            "periods": DataAnalystAgent._periods_from_plan(plan),
            "metric_subject": DataAnalystAgent._metric_subject_from_plan(plan),
            "business_query": DataAnalystAgent._business_query_from_plan(plan) or (understanding.business_query.to_dict() if getattr(understanding, "business_query", None) else None),
            "query_understanding": DataAnalystAgent._business_query_from_plan(plan) or (understanding.business_query.to_dict() if getattr(understanding, "business_query", None) else None),
            "required_tools": tool_names,
            "needs_sql_tool": needs_sql,
            "needs_redis": needs_redis,
            "needs_rag": needs_rag,
            "needs_opinion": question_type == "opinion",
            "reasoning_steps": [call.purpose for call in plan],
        }

    @staticmethod
    def _collect_evidence_summary(evidence: dict[str, Any], tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
        collected = [item.get("name") for item in tool_calls if item.get("name") and item.get("name") != "tool_rate_limited"]
        return {
            "mode": "evidence_first",
            "tools_collected": collected,
            "answer_policy": "final answer must use collected evidence only",
            "has_tool_evidence": bool(collected),
        }

    @staticmethod
    def _knowledge_from_evidence(evidence: dict[str, Any]) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        for key, value in evidence.items():
            if not (key.startswith("search_") or key.startswith("explain_")):
                continue
            if isinstance(value, list):
                chunks.extend(item for item in value if isinstance(item, dict))
        return chunks

    @staticmethod
    def _result_summary(evidence: dict[str, Any], tool_calls: list[dict[str, Any]], intent: ChatIntent) -> dict[str, Any]:
        tool = next((item.get("name") for item in tool_calls if item.get("name") and item.get("name") != "tool_rate_limited"), None)
        if not tool:
            return {"tool": None, "available": False}
        value = evidence.get(str(tool))
        if tool == "conversation_operation" and isinstance(value, dict):
            return {
                "tool": tool,
                "kind": value.get("operation"),
                "operation": value.get("operation"),
                "detail": value.get("summary"),
                "period": value.get("period"),
                "object": value.get("object"),
                "metric": value.get("metric"),
                "metric_subject": value.get("metric_subject"),
                "comparison_context": value.get("comparison_context") if isinstance(value.get("comparison_context"), dict) else None,
                "data_available": True,
            }
        if tool == "unsupported_capability" and isinstance(value, dict):
            return {
                "tool": tool,
                "kind": "unsupported",
                "detail": value.get("requested"),
                "data_available": False,
            }
        if tool == "get_transactions_between_dates" and isinstance(value, dict):
            query = value.get("business_query") if isinstance(value.get("business_query"), dict) else {}
            query_metric = query.get("metric")
            metric_subject = str(value.get("metric_subject") or "")
            metric = str(query_metric or ("refusal_rate" if metric_subject == "refus" else "non_completed_count" if metric_subject == "non_completed" else metric_subject or "total"))
            total = int(value.get("transactions") or 0)
            refused = int(value.get("refused") or 0)
            non_completed = int(value.get("non_completed") or 0)
            authorized_count = int(value.get("authorized_count") or max(0, total - refused))
            completed_success_count = int(value.get("completed_success_count") or max(0, total - refused - non_completed))
            refusal_rate = float(value.get("refusal_rate") or 0)
            success_rate = float(value.get("success_rate") or (completed_success_count / total * 100 if total else 0))
            non_completed_rate = round(non_completed / total * 100, 2) if total else 0.0
            if metric in {"success_rate", "authorized_count", "authorization_rate"}:
                count = authorized_count
                rate = success_rate
            else:
                count = non_completed if metric_subject == "non_completed" else refused
                rate = non_completed_rate if metric_subject == "non_completed" else refusal_rate
            return {
                "tool": tool,
                "kind": "count",
                "value_label": "transactions",
                "primary_value": total,
                "secondary_value": count,
                "count": count,
                "rate": rate,
                "metric": metric,
                "source_values": {
                    "transactions": total,
                    "refused": refused,
                    "non_completed": non_completed,
                    "authorized_count": authorized_count,
                    "completed_success_count": completed_success_count,
                },
                "derived_values": {
                    "refusal_rate": refusal_rate,
                    "authorization_rate": round(authorized_count / total * 100, 2) if total else 0,
                    "success_rate": success_rate,
                    **({"non_completed_rate": non_completed_rate} if metric_subject == "non_completed" else {}),
                },
                "result_values": {
                    "transactions": total,
                    "refused": refused,
                    "non_completed": non_completed,
                    "authorized_count": authorized_count,
                    "completed_success_count": completed_success_count,
                    "refusal_rate": refusal_rate,
                    "success_rate": success_rate,
                    **({"non_completed_rate": non_completed_rate} if metric_subject == "non_completed" else {}),
                },
                "detail": f"entre {value.get('start_date')} et {value.get('end_date')}",
                "period": f"{value.get('start_date')} à {value.get('end_date')}",
                "source": value.get("source"),
                "interpretation": "refusal_rate" if metric in {"refusal_rate", "refused_count", "refus"} else "count",
                "data_available": value.get("data_available"),
            }
        if tool == "multi_period_metric" and isinstance(value, dict):
            values = [item for item in list(value.get("values") or []) if isinstance(item, dict)]
            first = values[0] if values else {}
            metric_subject = str(value.get("metric_subject") or "")
            return {
                "tool": tool,
                "kind": "multi_period_metric",
                "value_label": "TPE distincts" if metric_subject == "tpe" else "transactions",
                "primary_value": int((first.get("active_tpe") if metric_subject == "tpe" else first.get("transactions")) or 0),
                "metric": value.get("metric") or "total",
                "values": values,
                "source_values": {"values": values},
                "period": " / ".join(str(item.get("label") or item.get("period")) for item in values),
                "source": "snapshot",
                "data_available": bool(values),
            }
        if tool == "get_tpe_count_by_period" and isinstance(value, dict):
            return {
                "tool": tool,
                "kind": "count",
                "value_label": "TPE distincts",
                "primary_value": int(value.get("active_tpe") or value.get("transactions") or 0),
                "source_values": {
                    "active_tpe": int(value.get("active_tpe") or value.get("transactions") or 0),
                },
                "detail": f"entre {value.get('start_date')} et {value.get('end_date')}",
                "period": f"{value.get('start_date')} à {value.get('end_date')}",
                "data_available": value.get("data_available"),
            }
        if tool in {"compare_periods", "compare_date_ranges"} and isinstance(value, dict):
            delta = value.get("delta") if isinstance(value.get("delta"), dict) else {}
            left = value.get("left") if isinstance(value.get("left"), dict) else {}
            right = value.get("right") if isinstance(value.get("right"), dict) else {}
            left_value = DataAnalystAgent._comparison_numeric_value(left)
            right_value = DataAnalystAgent._comparison_numeric_value(right)
            difference = DataAnalystAgent._numeric_difference(left_value, right_value)
            winner = DataAnalystAgent._comparison_winner(left, right, left_value, right_value)
            percentage_difference = DataAnalystAgent._percentage_difference(left_value, right_value)
            left_label = left.get("label") or left.get("period")
            right_label = right.get("label") or right.get("period")
            summary = (
                f"{left_label} est plus élevé que {right_label}."
                if winner == "left"
                else f"{right_label} est plus élevé que {left_label}."
                if winner == "right"
                else f"{left_label} et {right_label} sont au même niveau."
            )
            return {
                "tool": tool,
                "kind": "comparison",
                "value_label": "transactions",
                "primary_value": left_value,
                "secondary_value": right_value,
                "source_values": {
                    "left": left,
                    "right": right,
                    "left_value": left_value,
                    "right_value": right_value,
                },
                "derived_values": {
                    "transactions_difference": difference,
                    "refusal_rate_difference": delta.get("refusal_rate"),
                    "success_rate_difference": delta.get("success_rate"),
                    "percentage_difference": percentage_difference,
                },
                "comparison_context": {
                    "left_period": str(left_label or ""),
                    "right_period": str(right_label or ""),
                    "left_value": left_value,
                    "right_value": right_value,
                    "difference": difference,
                    "percentage_difference": percentage_difference,
                    "winner": winner,
                    "summary": summary,
                },
                "comparison_result": {"left": left, "right": right, "delta": delta},
                "conclusion": summary,
                "detail": f"{left.get('label') or left.get('period')} vs {right.get('label') or right.get('period')}",
                "period": intent.period,
                "data_available": True,
            }
        if tool in {"get_top_merchants", "get_top_tpe"} and isinstance(value, dict):
            rows = DataAnalystAgent._extract_rows(value)
            top = rows[0] if rows else {}
            return {
                "tool": tool,
                "kind": "ranking",
                "value_label": "élément(s)",
                "primary_value": int(value.get("rows_count") or len(rows)),
                "detail": str(top.get("merchant_name") or top.get("terminal_id") or top.get("name") or "").strip(),
                "period": intent.period,
                "data_available": value.get("data_available"),
            }
        if isinstance(value, dict):
            return {
                "tool": tool,
                "source": value.get("source"),
                "rows_count": value.get("rows_count"),
                "transactions": value.get("transactions"),
                "data_available": value.get("data_available"),
            }
        if isinstance(value, list):
            return {"tool": tool, "rows_count": len(value), "data_available": bool(value)}
        return {"tool": tool, "available": value is not None}

    @staticmethod
    def _comparison_numeric_value(item: dict[str, Any]) -> float | None:
        for key in ("transactions", "active_tpe", "value", "count"):
            value = item.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        return None

    @staticmethod
    def _numeric_difference(left: float | None, right: float | None) -> float | None:
        if left is None or right is None:
            return None
        return left - right

    @staticmethod
    def _percentage_difference(left: float | None, right: float | None) -> float | None:
        if left is None or right in {None, 0}:
            return None
        return round((left - right) / right * 100, 2)

    @staticmethod
    def _comparison_winner(left: dict[str, Any], right: dict[str, Any], left_value: float | None, right_value: float | None) -> str | None:
        if left_value is None or right_value is None:
            return None
        if left_value > right_value:
            return "left"
        if right_value > left_value:
            return "right"
        return "tie"

    def _resolve_temporal(self, message: str, history: str) -> TemporalRange | None:
        memory = self._last_temporal_from_history(history)
        return TemporalResolver.resolve(message, reference_date=self._reference_date(), memory=memory)

    def _last_temporal_from_history(self, history: str) -> TemporalRange | None:
        for line in reversed(history.splitlines()):
            if not line.startswith("user:"):
                continue
            resolved = TemporalResolver.resolve(line[5:].strip(), reference_date=self._reference_date())
            if resolved:
                return resolved
        return None

    def _reference_date(self) -> date:
        provider = getattr(self._tools, "_provider", None)
        current = getattr(provider, "period_window", None)
        if current and getattr(current, "reference_date", None):
            return current.reference_date
        return date.today()

    def _period_window(self, period: str) -> dict[str, str]:
        provider = getattr(self._tools, "_provider", None)
        reference = self._reference_date()
        resolver = getattr(provider, "period_window_for", None)
        if callable(resolver):
            window = resolver(period, reference)
            start = getattr(window, "start_date", reference)
            end = getattr(window, "end_date", reference)
            return {"start_date": start.isoformat(), "end_date": end.isoformat()}
        return {"start_date": reference.isoformat(), "end_date": reference.isoformat()}

    @staticmethod
    def _temporal_from_plan(plan: list[ToolCall]) -> dict[str, Any] | None:
        for call in plan:
            temporal = call.arguments.get("temporal")
            if isinstance(temporal, dict):
                return temporal
        return None

    @staticmethod
    def _periods_from_plan(plan: list[ToolCall]) -> list[dict[str, Any]]:
        for call in plan:
            periods = call.arguments.get("periods")
            if isinstance(periods, list):
                return [item for item in periods if isinstance(item, dict)]
        return []

    @staticmethod
    def _metric_subject_from_plan(plan: list[ToolCall]) -> str | None:
        for call in plan:
            subject = call.arguments.get("metric_subject")
            if isinstance(subject, str) and subject:
                return subject
        return None

    @staticmethod
    def _business_query_from_plan(plan: list[ToolCall]) -> dict[str, Any] | None:
        for call in plan:
            query = call.arguments.get("business_query") or call.arguments.get("query_understanding")
            if isinstance(query, dict):
                return query
        return None

    def _evidence_records(self, evidence: dict[str, Any], intent: ChatIntent) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for tool, value in evidence.items():
            if tool == "planner":
                continue
            if tool in {"get_kpi_summary", "get_kpi_by_period"} and isinstance(value, dict):
                records.append(self._evidence("redis", tool, "total_transactions", value.get("total_transactions"), intent.period, "high"))
                records.append(self._evidence("redis", tool, "refusal_rate", value.get("refusal_rate"), intent.period, "high"))
            elif tool in {"get_transactions_last_n_days", "get_transaction_volume", "get_transactions_between_dates", "get_tpe_count_by_period"} and isinstance(value, dict):
                confidence = "high" if value.get("source") == "sql" else "medium"
                records.append(self._evidence("sql_tool", tool, "transactions", value.get("transactions"), intent.period, confidence))
            elif tool == "multi_period_metric" and isinstance(value, dict):
                records.append(self._evidence("redis", tool, "values", value.get("values"), intent.period, "high"))
            elif tool == "compare_periods" and isinstance(value, dict):
                records.append(self._evidence("reasoning", tool, "delta", value.get("delta"), intent.period, "high"))
            elif tool.startswith("search_") or tool.startswith("explain_"):
                records.append(self._evidence("rag", tool, "chunks", len(value or []), intent.period, "medium"))
            elif tool in {"get_affiliation_summary", "get_affiliations", "get_affiliation_stock"} and isinstance(value, dict):
                records.append(self._evidence("redis", tool, "affiliations_remaining", value.get("affiliations_remaining"), intent.period, "high"))
            elif tool in {"get_risk_summary", "get_risk_alerts"} and isinstance(value, dict):
                records.append(self._evidence("redis", tool, "global_risk_score", value.get("global_risk_score"), intent.period, "high"))
            else:
                records.append(self._evidence("reasoning", tool, "available", value is not None, intent.period, "medium"))
        return records

    @staticmethod
    def _evidence(source_name: str, tool: str, metric: str, value: Any, period: str, confidence: str) -> dict[str, Any]:
        return {
            "source": source_name,
            "tool": tool,
            "metric": metric,
            "value": value,
            "period": period,
            "confidence": confidence,
        }

    @staticmethod
    def _confidence(records: list[dict[str, Any]]) -> str:
        if records and all(item.get("confidence") == "high" for item in records):
            return "high"
        if any(item.get("confidence") == "low" for item in records):
            return "low"
        return "medium"

    def _audit(self, request: ChatRequest, response: ChatResponse, elapsed_seconds: float) -> None:
        if not self._audit_enabled:
            return
        audit_chatbot_event(
            "answered",
            session_id=response.session_id,
            intent=response.intent,
            period=response.period,
            language=request.language,
            tools=[item.get("name") for item in response.tool_calls],
            refused=bool(response.data.get("refused_sensitive_request")),
            elapsed_ms=round(elapsed_seconds * 1000, 2),
        )

    @staticmethod
    def _dashboard_opinion(snapshot: dict[str, Any]) -> dict[str, Any]:
        kpis = snapshot.get("kpis", {})
        replay = snapshot.get("replay_status", {})
        return {
            "credible": True,
            "strengths": [
                "architecture event-driven avec EventBus",
                "cache Redis partage pour le snapshot KPI",
                "WebSocket pour la diffusion live",
                "replay historique portfolio_demo reproductible",
                "outils SQL controles et donnees sensibles exclues",
            ],
            "limits": [
                "le replay reste une projection historique, pas un flux production temps reel",
                "l'assistant depend d'Ollama local pour la synthese LLM gratuite",
                "les KPI indisponibles doivent rester explicitement marques comme N/A",
            ],
            "priority": "stabiliser l'assistant Data Analyst local, puis renforcer les graphes et les explications d'anomalies",
            "kpi_context": {
                "total_transactions": kpis.get("total_transactions"),
                "refusal_rate": kpis.get("refusal_rate"),
                "global_risk_score": kpis.get("global_risk_score"),
                "period": replay.get("period", "today"),
            },
        }

    def _synthesize_dashboard_opinion(self, intent: ChatIntent, opinion: dict[str, Any]) -> tuple[str, list[str], str]:
        kpi = opinion.get("kpi_context", {})
        if intent.language == "en":
            answer = (
                "Yes, the dashboard is credible for a defense because it demonstrates an event-driven architecture, Redis snapshot caching, WebSocket updates, "
                "and a reproducible portfolio_demo historical replay. "
                f"The current evidence includes {kpi.get('total_transactions', 'N/A')} replayed transactions, refusal rate {kpi.get('refusal_rate', 'N/A')}%, "
                f"and global risk score {kpi.get('global_risk_score', 'N/A')}. "
                "The limits are clear: this is a historical replay projection, the local LLM depends on Ollama availability, and unavailable KPI must stay marked as unavailable. "
                f"Priority: {opinion.get('priority')}."
            )
            return answer, ["Show the architecture?", "Analyze anomalies?"], "Generated an opinion grounded in KPI, architecture and known limits."
        answer = (
            "Oui, ton dashboard est credible pour une soutenance, surtout parce qu'il ne se limite pas a afficher des cartes KPI : "
            "il montre une architecture event-driven avec EventBus, un cache Redis partage, des mises a jour WebSocket et un replay historique portfolio_demo reproductible. "
            f"Les preuves courantes sont {kpi.get('total_transactions', 'N/A')} transactions rejouees, un taux de refus de {kpi.get('refusal_rate', 'N/A')} % "
            f"et un score de risque global de {kpi.get('global_risk_score', 'N/A')}. "
            "Les limites a assumer clairement : le replay reste une projection historique, le LLM local depend d'Ollama, et les KPI indisponibles doivent rester indiques comme indisponibles. "
            f"Ma recommandation prioritaire : {opinion.get('priority')}."
        )
        return answer, ["Montrer l'architecture ?", "Analyser les anomalies ?"], "Avis argumente a partir des KPI, de l'architecture et des limites connues."

    def _synthesize_refusal_diagnosis(self, intent: ChatIntent, evidence: dict[str, Any], comparison: dict[str, Any] | None = None) -> tuple[str, list[str], str]:
        kpi = evidence.get("kpi", evidence)
        total = int(kpi.get("total_transactions") or 0)
        refusal = float(kpi.get("refusal_rate") or 0)
        refused_count = round(total * refusal / 100)
        incidents = self._extract_rows(evidence.get("hourly_incidents"))
        anomalies = evidence.get("top_anomalies") or []
        tpe = self._extract_rows(evidence.get("top_tpe"))
        peak = max(incidents, key=lambda row: int(row.get("refused") or 0)) if incidents else {}
        top_anomaly = anomalies[0] if anomalies else {}
        top_tpe = tpe[0] if tpe else {}
        if intent.language == "en":
            answer = (
                f"For {intent.period}, the replay shows a {refusal:.1f}% refusal rate ({refused_count} refused out of {total} transactions). "
                f"The likely explanation is concentration rather than a generic issue: the peak hour is {peak.get('hour', 'N/A')}h with {peak.get('refused', 'N/A')} refusals, "
                f"the riskiest merchant is {top_anomaly.get('merchant_name') or top_anomaly.get('name', 'N/A')} with risk score {top_anomaly.get('risk_score', 'N/A')}, "
                f"and the most incident terminal observed is {top_tpe.get('terminal_id', 'N/A')}. This is based on the replay projection, not a fresh production stream."
            )
            return answer, ["Compare with 7 days?", "Show top TPE?"], "Correlated KPI, hourly incidents, merchant anomalies and TPE concentration."
        answer = (
            f"Sur {intent.period}, le replay montre un taux de refus de {refusal:.1f} % ({refused_count} refus sur {total} transactions). "
            f"L'explication la plus probable est une concentration des incidents plutot qu'une hausse uniforme : le pic horaire est {peak.get('hour', 'N/A')}h avec {peak.get('refused', 'N/A')} refus, "
            f"le commercant le plus risque est {top_anomaly.get('merchant_name') or top_anomaly.get('name', 'N/A')} avec un score {top_anomaly.get('risk_score', 'N/A')}, "
            f"et le TPE le plus incident observe est {top_tpe.get('terminal_id', 'N/A')}."
        )
        if comparison:
            delta = comparison.get("delta", {})
            answer += f" Par rapport a {comparison.get('right', {}).get('period', 'la periode comparee')}, l'ecart de taux de refus est de {delta.get('refusal_rate', 0)} point(s)."
        answer += " Cette analyse vient de la projection de replay, pas d'un flux de production temps reel."
        return answer, ["Comparer avec 7 jours ?", "Voir les top TPE ?"], "Correlation entre KPI, incidents horaires, anomalies commercants et concentration TPE."

    def _synthesize_performance_answer(self, intent: ChatIntent, evidence: dict[str, Any]) -> tuple[str, list[str], str]:
        if intent.language == "en":
            answer = (
                "The dashboard stays fast because it does not reread the whole SQL database on every screen refresh. "
                "Transactions are replayed in batches, aggregated by the backend, then the UI receives already computed indicators through Redis/WebSocket. "
                "That avoids recalculating every detail each time."
            )
            return answer, ["Explain Redis?", "Explain EventBus?"], "Linked performance question to replay batching, EventBus, Aggregator and Redis cache."
        answer = (
            "Le dashboard reste rapide parce qu'il ne relit pas toute la base SQL à chaque affichage. "
            "Les transactions sont rejouées par lots, agrégées côté backend, puis le dashboard reçoit seulement des indicateurs déjà calculés via Redis/WebSocket. "
            "Cela évite de recalculer tous les détails à chaque rafraîchissement."
        )
        return answer, ["Expliquer Redis ?", "Expliquer EventBus ?"], "Question performance reliee au batching, EventBus, Aggregator et cache Redis."

    @staticmethod
    def _extract_last_n_days(text: str) -> int | None:
        import re

        match = re.search(r"(\d+)\s*(derniers?|last)?\s*(jours?|days?)", text)
        if match:
            return max(1, min(int(match.group(1)), 30))
        if "trois derniers jours" in text or "three last days" in text or "last three days" in text:
            return 3
        if "sept derniers jours" in text or "cette semaine" in text or "semaine ecoulee" in text or "last seven days" in text:
            return 7
        return None

    def _synthesize_last_days(self, intent: ChatIntent, data: dict[str, Any]) -> tuple[str, list[str], str]:
        days = data.get("days", "?")
        transactions = data.get("transactions", 0)
        start = data.get("start_date", "?")
        end = data.get("end_date", "?")
        refused = data.get("refused", 0)
        non_completed = data.get("non_completed", 0)
        refusal_rate = data.get("refusal_rate", 0)
        non_completed_rate = round(int(non_completed or 0) / int(transactions or 1) * 100, 2) if int(transactions or 0) else 0
        metric_subject = data.get("metric_subject")
        source_name = data.get("source")
        if source_name == "sql_unavailable":
            if intent.language == "en":
                return "SQL Server connection is unavailable.", ["Try the current replay period?", "Show current KPI?"], "SQL Server connection unavailable for the controlled SQL tool."
            return "Connexion SQL Server indisponible.", ["Essayer la période courante du replay ?", "Voir les KPI courants ?"], "Connexion SQL Server indisponible pour l'outil SQL controle."
        if source_name == "sql_error":
            if intent.language == "en":
                return "The controlled SQL tool encountered an internal query error. The backend logs contain the real SQL exception.", ["Show current KPI?", "Try another period?"], "Controlled SQL tool failed with an internal SQL error."
            return "Erreur interne de l'outil SQL contrôlé. L'exception réelle est disponible dans les logs backend.", ["Voir les KPI courants ?", "Essayer une autre période ?"], "Erreur SQL controlee avec exception detaillee dans les logs backend."
        if source_name == "invalid_date":
            if intent.language == "en":
                return f"I could not parse the requested period ({start} to {end}).", ["Try another period?", "Show current KPI?"], "Invalid explicit temporal range."
            return f"Je n'ai pas pu interpréter correctement la période demandée ({start} à {end}).", ["Essayer une autre période ?", "Voir les KPI courants ?"], "Periode explicite invalide."
        if source_name == "sql" and int(transactions or 0) == 0:
            if intent.language == "en":
                return f"No transaction was found for this period ({start} to {end}).", ["Try another period?", "Show current KPI?"], "Controlled SQL returned zero transaction for the requested period."
            return f"Pas de transaction trouvée pour cette période ({start} à {end}).", ["Essayer une autre période ?", "Voir les KPI courants ?"], "SQL controle retourne zero transaction pour la periode demandee."
        if intent.language == "en":
            if metric_subject == "non_completed":
                answer = f"For the period {start} to {end}, the controlled SQL tool counted {non_completed} non-completed transactions out of {transactions} total transactions ({non_completed_rate}%)."
                return answer, ["Compare with refusals?", "Show current KPI?"], "Used controlled SQL non-completed transaction count."
            if days == "?":
                answer = f"For the period {start} to {end}, the controlled SQL tool counted {transactions} transactions, including {refused} refusals ({refusal_rate}%)."
            else:
                answer = f"Over the last {days} days ({start} to {end}), the controlled SQL tool counted {transactions} transactions, including {refused} refusals ({refusal_rate}%)."
            return answer, ["Compare with the previous period?", "Show top merchants?"], "Used controlled SQL last-N-days transaction count."
        if metric_subject == "non_completed":
            answer = f"Sur la période {start} à {end}, l'outil SQL contrôlé a compté {non_completed} transactions non abouties sur {transactions} transactions au total, soit {non_completed_rate} %."
            return answer, ["Comparer avec les refus ?", "Voir les KPI courants ?"], "Comptage SQL controle des transactions non abouties."
        if days == "?":
            answer = f"Sur la période {start} à {end}, l'outil SQL contrôlé a compté {transactions} transactions, dont {refused} refus ({refusal_rate} %)."
        else:
            answer = f"Sur les {days} derniers jours ({start} a {end}), l'outil SQL controle a compte {transactions} transactions, dont {refused} refus ({refusal_rate} %)."
        return answer, ["Comparer avec la periode precedente ?", "Voir les top commercants ?"], "Comptage SQL controle sur les N derniers jours."

    def _synthesize_comparison(self, intent: ChatIntent, data: dict[str, Any], anomalies: Any = None) -> tuple[str, list[str], str]:
        left = data.get("left", {})
        right = data.get("right", {})
        delta = data.get("delta", {})
        if intent.language == "en":
            answer = (
                f"Comparison {left.get('label') or left.get('period')} vs {right.get('label') or right.get('period')}: transactions delta {delta.get('transactions')}, "
                f"refusal-rate delta {delta.get('refusal_rate')} point(s), success-rate delta {delta.get('success_rate')} point(s)."
            )
            return answer, ["Show root causes?", "Compare merchants?"], "Compared two dashboard periods with KPI deltas."
        left_label = left.get("label") or left.get("period")
        right_label = right.get("label") or right.get("period")
        left_rate = left.get("refusal_rate", "N/A")
        right_rate = right.get("refusal_rate", "N/A")
        rate_delta = delta.get("refusal_rate", 0)
        conclusion = "Le taux de refus est plus élevé sur la première période." if float(rate_delta or 0) > 0 else "Le taux de refus est plus faible sur la première période." if float(rate_delta or 0) < 0 else "Les deux périodes sont stables sur le taux de refus."
        answer = (
            f"{left_label} : {left_rate} % de refus. "
            f"{right_label} : {right_rate} % de refus. "
            f"Écart : {rate_delta} point(s). {conclusion} Je peux ensuite afficher les commerçants qui expliquent l'écart."
        )
        if anomalies:
            first = anomalies[0] if isinstance(anomalies, list) and anomalies else {}
            answer += f" Le premier signal a surveiller est {first.get('merchant_name') or first.get('name', 'N/A')}."
        return answer, ["Voir les causes ?", "Comparer les commercants ?"], "Comparaison multi-periodes avec deltas KPI."

    @staticmethod
    def _followups(intent: ChatIntent) -> list[str]:
        if intent.language == "en":
            return ["Show top merchants?", "Explain the architecture?"]
        return ["Voir les top commercants ?", "Expliquer l'architecture ?"]
