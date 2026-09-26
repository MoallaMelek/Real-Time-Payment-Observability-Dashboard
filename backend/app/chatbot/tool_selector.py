from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.chatbot.business_query_parser import BusinessQuery
from app.chatbot.conversation_operation import ConversationOperation
from app.chatbot.followup_resolver import FollowUpResolution


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    purpose: str


@dataclass(frozen=True, slots=True)
class CapabilityRoute:
    capability: str
    knowledge_family: str
    tool_family: str
    expected_output: str
    response_depth: str
    action: tuple[str, ...] | None = None
    object: tuple[str, ...] | None = None
    metric: tuple[str, ...] | None = None
    dimension: tuple[str, ...] | None = None

    def matches(self, query: BusinessQuery, knowledge_family: str, capability: str | None) -> bool:
        if capability and capability != self.capability:
            return False
        if knowledge_family != self.knowledge_family:
            return False
        if self.action and query.action not in self.action:
            return False
        if self.object and query.object not in self.object:
            return False
        if self.metric and query.metric not in self.metric:
            return False
        if self.dimension and query.dimension not in self.dimension:
            return False
        return True


class ToolSelector:
    """Deterministic selector from canonical business structures to tool calls."""

    CAPABILITY_MATRIX: tuple[CapabilityRoute, ...] = (
        CapabilityRoute("dashboard_opinion", "business_analysis", "hybrid", "opinion", "expert", action=("opinion",)),
        CapabilityRoute("unsupported_anomaly_count", "sql_metrics", "unsupported", "unsupported", "factual", action=("count",), object=("anomaly",)),
        CapabilityRoute("unsupported_slow_transaction_count", "sql_metrics", "unsupported", "unsupported", "factual", action=("count", "rate"), object=("transaction",), metric=("slow_count",)),
        CapabilityRoute("unsupported_historical_fraud_timeout_count", "sql_metrics", "unsupported", "unsupported", "factual", action=("count", "rate"), object=("transaction",), metric=("fraud_timeout_count",)),
        CapabilityRoute("fraud_timeout_count", "sql_metrics", "snapshot", "fraud_timeouts", "factual", action=("count", "rate"), object=("transaction",), metric=("fraud_timeout_count",)),
        CapabilityRoute("transaction_success_metric", "sql_metrics", "snapshot_or_sql", "success_metric", "factual", action=("count", "rate"), object=("transaction",), metric=("success_rate", "authorized_count", "authorization_rate")),
        CapabilityRoute("multi_period_metric", "sql_metrics", "snapshot_or_sql", "multi_period_metric", "factual", action=("count", "rate"), object=("transaction", "tpe")),
        CapabilityRoute("transaction_volume", "sql_metrics", "snapshot_or_sql", "count", "factual", action=("count", "rate"), object=("transaction",)),
        CapabilityRoute("transaction_refusal_metric", "sql_metrics", "snapshot_or_sql", "refusal_metric", "factual", action=("count", "rate"), object=("transaction",), metric=("refusal_rate", "refused_count")),
        CapabilityRoute("slow_transaction_count", "sql_metrics", "sql", "slow_transactions", "factual", action=("count", "rate"), object=("transaction",), metric=("slow_count",)),
        CapabilityRoute("non_completed_transaction_count", "sql_metrics", "snapshot_or_sql", "non_completed_transactions", "factual", action=("count", "rate"), object=("transaction",), metric=("non_completed_count",)),
        CapabilityRoute("tpe_count", "sql_metrics", "sql", "distinct_tpe_count", "factual", action=("count",), object=("tpe",)),
        CapabilityRoute("merchant_ranking", "sql_metrics", "sql", "merchant_ranking", "analytical", action=("rank", "rate", "count"), object=("merchant", "transaction"), dimension=("merchant",)),
        CapabilityRoute("tpe_ranking", "sql_metrics", "sql", "tpe_ranking", "analytical", action=("rank", "count"), object=("tpe",), dimension=("tpe",)),
        CapabilityRoute("anomaly_ranking", "sql_metrics", "snapshot", "anomaly_ranking", "analytical", action=("rank",), object=("anomaly",)),
        CapabilityRoute("incidents_by_hour", "sql_metrics", "sql", "hourly_incidents", "analytical", action=("count",), object=("incident",), dimension=("hour",)),
        CapabilityRoute("compare_periods", "sql_metrics", "snapshot", "period_comparison", "analytical", action=("compare",)),
        CapabilityRoute("refusal_diagnosis", "business_analysis", "hybrid", "refusal_diagnosis", "analytical", action=("explain",)),
        CapabilityRoute("affiliation_summary", "sql_metrics", "snapshot_and_docs", "affiliation_projection", "analytical", object=("affiliation",)),
        CapabilityRoute("architecture_explanation", "architecture", "docs", "architecture_explanation", "analytical"),
        CapabilityRoute("architecture_fast_forward", "architecture", "docs", "fast_forward_explanation", "analytical"),
        CapabilityRoute("architecture_performance", "architecture", "hybrid", "performance_explanation", "analytical"),
        CapabilityRoute("business_documentation", "business_rag", "docs", "documentation_answer", "analytical", action=("explain",)),
        CapabilityRoute("redis_runtime_status", "runtime_status", "runtime", "redis_runtime_status", "factual"),
        CapabilityRoute("replay_status", "runtime_status", "runtime", "replay_status", "factual"),
        CapabilityRoute("data_quality_status", "runtime_status", "runtime", "data_quality_status", "factual"),
    )

    @classmethod
    def from_understanding(
        cls,
        understanding: Any,
        period: str,
        strategy: Any | None = None,
        state: Any | None = None,
        operation: ConversationOperation | None = None,
    ) -> list[ToolCall]:
        if operation is not None:
            return cls.from_operation(operation)
        conversation_kind = getattr(understanding, "conversation_kind", None)
        knowledge_family = getattr(understanding, "knowledge_family", None) or "business_analysis"
        capability = getattr(understanding, "capability", None)
        context = getattr(understanding, "context", {}) or {}
        comparison = getattr(understanding, "comparison", None) or {}
        comparison_plan = cls._from_explicit_comparison(comparison, getattr(understanding, "business_query", None))
        if comparison_plan:
            return comparison_plan

        if conversation_kind == "conversation":
            return [ToolCall("greeting", {"kind": getattr(understanding, "intent", "greeting")}, "Repondre naturellement sans RAG ni SQL")]
        if conversation_kind == "code" or knowledge_family == "code":
            query = context.get("knowledge_query") or "project code"
            return [ToolCall("project_code_search", {"query": query, "limit": 3}, "Rechercher le code explicitement demande dans le projet")]
        if knowledge_family == "architecture":
            return cls._plan_for_capability(capability, period, context=context, query=getattr(understanding, "business_query", None))

        strategy_plan = cls._from_strategy(understanding, period, strategy, state)
        query = getattr(understanding, "business_query", None)
        if (
            query is not None
            and knowledge_family == "sql_metrics"
            and context.get("is_follow_up")
            and (getattr(query, "object", None) in {"merchant", "tpe"} or getattr(query, "dimension", None) in {"merchant", "tpe"})
        ):
            plan = cls._plan_for_query(query, period, knowledge_family=knowledge_family, capability=capability, context=context)
            if plan:
                return plan
        if strategy_plan:
            return strategy_plan

        if query is not None:
            plan = cls._plan_for_query(query, period, knowledge_family=knowledge_family, capability=capability, context=context)
            if plan:
                return plan

        follow_up = getattr(understanding, "follow_up", None)
        if follow_up is not None:
            plan = cls.from_followup(follow_up)
            if plan:
                return plan

        if conversation_kind == "opinion":
            return cls._plan_for_capability("dashboard_opinion", period, context=context, query=query)
        if conversation_kind == "architecture":
            return cls._plan_for_capability("architecture_explanation", period, context=context, query=query)
        return []

    @classmethod
    def from_business_query(cls, query: BusinessQuery, period: str) -> list[ToolCall]:
        knowledge_family = cls._knowledge_family_from_query(query)
        capability = cls._capability_from_query(query)
        return cls._plan_for_query(query, period, knowledge_family=knowledge_family, capability=capability, context={})

    @staticmethod
    def from_operation(operation: ConversationOperation) -> list[ToolCall]:
        return [ToolCall("conversation_operation", dict(operation.payload), operation.purpose)]

    @classmethod
    def from_followup(cls, resolved: FollowUpResolution) -> list[ToolCall]:
        if resolved.compare_left and resolved.compare_right:
            left = resolved.compare_left
            right = resolved.compare_right
            if left == right:
                left, right = "today", "yesterday"
            return [
                ToolCall("compare_periods", {"left": left, "right": right}, "Comparer deux periodes resolues par le suivi conversationnel"),
                ToolCall("get_top_anomalies", {"period": left, "limit": 5}, "Identifier les anomalies qui expliquent l'ecart"),
            ]

        temporal = resolved.temporal
        temporal_payload = temporal.to_dict() if temporal else {}
        period = resolved.period
        subject = resolved.metric_subject or resolved.axis
        if subject == "merchants":
            return [
                ToolCall(
                    "get_top_merchants",
                    {
                        "period": period,
                        "limit": 5,
                        **({"start_date": temporal.start_date, "end_date": temporal.end_date} if temporal else {}),
                        **({"temporal": temporal_payload} if temporal_payload else {}),
                        "metric_subject": subject,
                    },
                    "Poursuivre l'analyse sur les commercants en conservant la periode",
                )
            ]
        if subject == "tpe":
            return [
                ToolCall(
                    "get_top_tpe",
                    {
                        "period": period,
                        "limit": 5,
                        **({"start_date": temporal.start_date, "end_date": temporal.end_date} if temporal else {}),
                        **({"temporal": temporal_payload} if temporal_payload else {}),
                        "metric_subject": subject,
                    },
                    "Poursuivre l'analyse sur les TPE en conservant la periode",
                )
            ]
        if subject == "anomalies":
            return [
                ToolCall(
                    "get_top_anomalies",
                    {
                        "period": period,
                        "limit": 5,
                        **({"temporal": temporal_payload} if temporal_payload else {}),
                        "metric_subject": subject,
                    },
                    "Poursuivre l'analyse sur les anomalies en conservant la periode",
                )
            ]
        if subject == "why":
            return [ToolCall("get_refusal_analysis", {"period": period}, "Expliquer la reponse precedente avec les preuves disponibles")]
        if temporal:
            return [
                ToolCall(
                    "get_transactions_between_dates",
                    {
                        "start_date": temporal.start_date,
                        "end_date": temporal.end_date,
                        "temporal": temporal_payload,
                        "metric_subject": subject,
                    },
                    "Reconstruire la requete metier a partir du suivi conversationnel",
                )
            ]
        return []

    @classmethod
    def _plan_for_query(
        cls,
        query: BusinessQuery,
        period: str,
        *,
        knowledge_family: str,
        capability: str | None,
        context: dict[str, Any],
    ) -> list[ToolCall]:
        route = cls._route_for(query, knowledge_family, capability)
        if not route:
            return []
        return cls._build_route_plan(route, query, period, context)

    @classmethod
    def _route_for(cls, query: BusinessQuery, knowledge_family: str, capability: str | None) -> CapabilityRoute | None:
        for route in cls.CAPABILITY_MATRIX:
            if route.matches(query, knowledge_family, capability):
                return route
        return None

    @classmethod
    def _build_route_plan(
        cls,
        route: CapabilityRoute,
        query: BusinessQuery,
        period: str,
        context: dict[str, Any],
    ) -> list[ToolCall]:
        capability = route.capability
        if capability == "dashboard_opinion":
            return cls._plan_for_capability(capability, period, context=context, query=query)
        if capability in {"architecture_explanation", "architecture_fast_forward", "architecture_performance", "business_documentation"}:
            return cls._plan_for_capability(capability, period, context=context, query=query)
        if capability == "compare_periods":
            temporal = query.period or {}
            if temporal.get("source") == "explicit" and temporal.get("start_date") and temporal.get("end_date"):
                return [
                    ToolCall(
                        "compare_date_ranges",
                        {
                            "left_start": temporal.get("start_date"),
                            "left_end": temporal.get("end_date"),
                            "right_start": temporal.get("start_date"),
                            "right_end": temporal.get("end_date"),
                            "left_label": temporal.get("label_fr") or temporal.get("start_date"),
                            "right_label": temporal.get("label_fr") or temporal.get("end_date"),
                            "business_query": query.to_dict(),
                        },
                        "Comparer deux plages explicites deja resolues",
                    )
                ]
            return [
                ToolCall("compare_periods", {"left": "today", "right": "yesterday", **({"temporal": temporal} if temporal else {})}, "Comparer aujourd'hui et hier"),
                ToolCall("get_top_anomalies", {"period": "today", "limit": 5, **({"temporal": temporal} if temporal else {})}, "Identifier les anomalies qui expliquent l'ecart"),
            ]
        if capability == "unsupported_slow_transaction_count":
            capability = "slow_transaction_count"
        if capability == "fraud_timeout_count":
            return cls._fraud_timeout_plan(query, period, context)
        if capability.startswith("unsupported_"):
            return cls._unsupported_plan(capability, query, period)
        if capability in {"transaction_volume", "transaction_refusal_metric", "transaction_success_metric", "non_completed_transaction_count"}:
            return cls._transaction_metric_plan(query, period, context)
        if capability == "multi_period_metric":
            return cls._multi_period_metric_plan(query, period, context)
        if capability == "slow_transaction_count":
            return cls._slow_transaction_plan(query, period, context)
        if capability == "tpe_count":
            return cls._tpe_count_plan(query, period)
        if capability == "merchant_ranking":
            return cls._merchant_plan(query, period, context)
        if capability == "tpe_ranking":
            return cls._tpe_plan(query, period, context)
        if capability == "anomaly_ranking":
            return cls._anomaly_plan(query, period, context)
        if capability == "incidents_by_hour":
            return cls._incidents_plan(query, period)
        if capability == "refusal_diagnosis":
            return [
                ToolCall("get_refusal_analysis", {"period": cls._period_key(query.period, period)}, "Analyser KPI, incidents horaires, anomalies et TPE autour des refus"),
                ToolCall("compare_periods", {"left": "today", "right": "yesterday"}, "Comparer aujourd'hui avec hier"),
                ToolCall("get_incidents_by_hour", {"period": cls._period_key(query.period, period)}, "Verifier la concentration horaire des refus"),
            ]
        if capability == "affiliation_summary":
            return [
                ToolCall("get_affiliation_summary", {}, "Lire le stock d'affiliations projete"),
                ToolCall("search_documentation", {"query": "affiliations projection comportement commercants"}, "Expliquer pourquoi ce stock est une projection metier"),
            ]
        return []

    @classmethod
    def _plan_for_capability(
        cls,
        capability: str | None,
        period: str,
        *,
        context: dict[str, Any],
        query: BusinessQuery | None,
    ) -> list[ToolCall]:
        knowledge_query = context.get("knowledge_query") or "architecture dashboard"
        if capability == "dashboard_opinion":
            return [
                ToolCall("get_kpi_summary", {"period": period}, "Evaluer l'etat courant du dashboard"),
                ToolCall("search_project_documentation", {"query": "architecture Redis EventBus replay limites chatbot dashboard"}, "Mobiliser les preuves d'architecture"),
                ToolCall("generate_dashboard_opinion", {"period": period}, "Produire un avis argumente base sur les evidences"),
            ]
        if capability == "architecture_performance":
            return [
                ToolCall("get_kpi_summary", {"period": period}, "Mesurer le volume actuellement rejoue"),
                ToolCall("search_redis", {"query": knowledge_query}, "Relier la performance a Redis, EventBus et au replay incremental"),
                ToolCall("search_docs", {"query": knowledge_query}, "Conserver l'alias documentaire historique"),
            ]
        if capability in {"architecture_explanation", "architecture_fast_forward", "business_documentation"}:
            return [ToolCall("search_documentation", {"query": knowledge_query}, "Chercher les informations projet pertinentes avant de repondre")]
        if capability == "code_search":
            return [ToolCall("project_code_search", {"query": context.get("knowledge_query") or "project code", "limit": 3}, "Rechercher le code explicitement demande dans le projet")]
        if capability == "unsupported_slow_transaction_count":
            capability = "slow_transaction_count"
        if capability and capability.startswith("unsupported_"):
            return cls._unsupported_plan(capability, query, period) if query is not None else []
        if query is not None:
            return cls._plan_for_query(query, period, knowledge_family=cls._knowledge_family_from_query(query), capability=capability, context=context)
        return []

    @classmethod
    def _transaction_metric_plan(cls, query: BusinessQuery, period: str, context: dict[str, Any]) -> list[ToolCall]:
        temporal = query.period or {}
        metric_subject = context.get("metric_subject")
        period_key = cls._period_key(temporal, period)
        if temporal.get("source") == "explicit" or (
            temporal.get("start_date") and temporal.get("end_date") and temporal.get("granularity") in {"range", "week", "month", "quarter", "year"}
        ):
            return [
                ToolCall(
                    "get_transactions_between_dates",
                    {
                        "start_date": temporal.get("start_date"),
                        "end_date": temporal.get("end_date"),
                        "temporal": temporal,
                        "business_query": query.to_dict(),
                        **({"metric_subject": metric_subject} if metric_subject else {}),
                    },
                    "Compter les transactions sur la periode resolue",
                )
            ]
        if metric_subject in {"transactions_lentes", "non_completed"} and temporal.get("start_date") and temporal.get("end_date"):
            return [
                ToolCall(
                    "get_transactions_between_dates",
                    {
                        "start_date": temporal.get("start_date"),
                        "end_date": temporal.get("end_date"),
                        "temporal": temporal,
                        "business_query": query.to_dict(),
                        "metric_subject": metric_subject,
                    },
                    "Conserver la metrique metier precise sur la periode memorisee",
                )
            ]
        return [ToolCall("get_kpi_summary", {"period": period_key, **({"temporal": temporal} if temporal else {})}, "Lire les KPI courants")]

    @classmethod
    def _multi_period_metric_plan(cls, query: BusinessQuery, period: str, context: dict[str, Any]) -> list[ToolCall]:
        periods = [item for item in list(context.get("periods") or []) if isinstance(item, dict)]
        metric_subject = context.get("metric_subject")
        return [
            ToolCall(
                "multi_period_metric",
                {
                    "periods": periods,
                    "period": period,
                    "metric": query.metric or "total",
                    "metric_subject": metric_subject or "transactions",
                    "business_query": query.to_dict(),
                },
                "Lire la même métrique pour plusieurs périodes sans écraser les résultats",
            )
        ]

    @classmethod
    def _tpe_count_plan(cls, query: BusinessQuery, period: str) -> list[ToolCall]:
        temporal = query.period or {}
        start_date = temporal.get("start_date")
        end_date = temporal.get("end_date")
        if start_date and end_date:
            return [
                ToolCall(
                    "get_tpe_count_by_period",
                    {
                        "period": cls._period_key(temporal, period),
                        "start_date": start_date,
                        "end_date": end_date,
                        "temporal": temporal,
                        "business_query": query.to_dict(),
                    },
                    "Compter les TPE distincts sur la periode resolue",
                )
            ]
        return []

    @classmethod
    def _merchant_plan(cls, query: BusinessQuery, period: str, context: dict[str, Any]) -> list[ToolCall]:
        temporal = query.period or {}
        args = {
            "period": cls._period_key(temporal, period),
            "limit": 5,
            **({"start_date": temporal.get("start_date"), "end_date": temporal.get("end_date")} if temporal.get("start_date") and temporal.get("end_date") else {}),
            **({"temporal": temporal} if temporal else {}),
            **({"metric_subject": context.get("metric_subject")} if context.get("metric_subject") else {}),
            "business_query": query.to_dict(),
        }
        plan = [ToolCall("get_top_merchants", args, "Classer les commercants sur la capacite identifiee")]
        if not temporal.get("source") == "explicit":
            plan.append(ToolCall("get_kpi_summary", {"period": cls._period_key(temporal, period)}, "Contextualiser le classement avec le volume global"))
        return plan

    @classmethod
    def _tpe_plan(cls, query: BusinessQuery, period: str, context: dict[str, Any]) -> list[ToolCall]:
        temporal = query.period or {}
        return [
            ToolCall(
                "get_top_tpe",
                {
                    "period": cls._period_key(temporal, period),
                    "limit": 5,
                    **({"start_date": temporal.get("start_date"), "end_date": temporal.get("end_date")} if temporal.get("start_date") and temporal.get("end_date") else {}),
                    **({"temporal": temporal} if temporal else {}),
                    **({"metric_subject": context.get("metric_subject")} if context.get("metric_subject") else {}),
                    "business_query": query.to_dict(),
                },
                "Identifier les TPE les plus concernes",
            )
        ]

    @classmethod
    def _anomaly_plan(cls, query: BusinessQuery, period: str, context: dict[str, Any]) -> list[ToolCall]:
        temporal = query.period or {}
        return [
            ToolCall(
                "get_top_anomalies",
                {
                    "period": cls._period_key(temporal, period),
                    "limit": 5,
                    **({"temporal": temporal} if temporal else {}),
                    **({"metric_subject": context.get("metric_subject")} if context.get("metric_subject") else {}),
                    "business_query": query.to_dict(),
                },
                "Identifier les entites les plus risquees sur la capacite demandee",
            ),
            ToolCall("get_incidents_by_hour", {"period": cls._period_key(temporal, period)}, "Ajouter le contexte temporel des incidents"),
        ]

    @classmethod
    def _incidents_plan(cls, query: BusinessQuery, period: str) -> list[ToolCall]:
        temporal = query.period or {}
        return [
            ToolCall(
                "get_incidents_by_hour",
                {
                    "period": cls._period_key(temporal, period),
                    **({"start_date": temporal.get("start_date"), "end_date": temporal.get("end_date")} if temporal.get("start_date") and temporal.get("end_date") else {}),
                    **({"temporal": temporal} if temporal else {}),
                    "business_query": query.to_dict(),
                },
                "Analyser la distribution horaire demandee",
            )
        ]

    @classmethod
    def _from_strategy(cls, understanding: Any, period: str, strategy: Any | None, state: Any | None) -> list[ToolCall]:
        if strategy is None or not getattr(strategy, "needs_tools", False):
            return []
        if getattr(understanding, "knowledge_family", None) == "sql_metrics" and getattr(strategy, "goal", "") == "answer_business_question":
            return []
        goal = getattr(strategy, "goal", "")
        if goal in {"reassure_and_investigate", "diagnose", "executive_summary", "decision_support"}:
            if getattr(understanding, "object", None) == "tpe" or getattr(understanding, "dimension", None) == "tpe":
                return [
                    ToolCall("get_kpi_summary", {"period": period}, "Evaluer la situation generale avant de repondre"),
                    ToolCall("get_refusal_analysis", {"period": period}, "Analyser les refus et leurs causes probables"),
                    ToolCall("get_top_tpe", {"period": period, "limit": 5}, "Identifier les TPE les plus concernes"),
                ]
            if getattr(understanding, "object", None) == "merchant" or getattr(understanding, "dimension", None) == "merchant":
                return [
                    ToolCall("get_kpi_summary", {"period": period}, "Evaluer la situation generale avant de repondre"),
                    ToolCall("get_refusal_analysis", {"period": period}, "Analyser les refus et leurs causes probables"),
                    ToolCall("get_top_merchants", {"period": period, "limit": 5}, "Identifier les commercants les plus concernes"),
                ]
            return [
                ToolCall("get_kpi_summary", {"period": period}, "Evaluer la situation generale avant de repondre"),
                ToolCall("get_refusal_analysis", {"period": period}, "Analyser les refus et leurs causes probables"),
                ToolCall("compare_periods", {"left": period, "right": "yesterday"}, "Comparer avec hier pour verifier si le signal augmente"),
                ToolCall("get_top_anomalies", {"period": period, "limit": 5}, "Identifier les points qui meritent attention"),
            ]
        if goal == "drill_down":
            last_dimension = getattr(state, "last_dimension", None)
            if last_dimension == "tpe":
                return [ToolCall("get_top_tpe", {"period": period, "limit": 5}, "Approfondir le contexte precedent sur les TPE")]
            if last_dimension == "merchant":
                return [ToolCall("get_top_merchants", {"period": period, "limit": 5}, "Approfondir le contexte precedent sur les commercants")]
            return [
                ToolCall("get_top_anomalies", {"period": period, "limit": 5}, "Montrer les points les plus preoccupants"),
                ToolCall("get_top_merchants", {"period": period, "limit": 5}, "Identifier les commercants concernes"),
            ]
        if goal == "explain_previous_answer":
            return [ToolCall("get_refusal_analysis", {"period": period}, "Expliquer le signal precedent avec les dimensions disponibles")]
        if goal == "simplify":
            return [] if getattr(state, "last_tool", None) else [ToolCall("get_kpi_summary", {"period": period}, "Donner une explication simple a partir de la situation generale")]
        return []

    @staticmethod
    def _from_explicit_comparison(comparison: dict[str, Any], query: BusinessQuery | None = None) -> list[ToolCall]:
        if comparison.get("type") != "date_ranges":
            return []
        left = comparison.get("left") if isinstance(comparison.get("left"), dict) else {}
        right = comparison.get("right") if isinstance(comparison.get("right"), dict) else {}
        if not left.get("start_date") or not right.get("start_date"):
            return []
        return [
            ToolCall(
                "compare_date_ranges",
                {
                    "left_start": left.get("start_date"),
                    "left_end": left.get("end_date") or left.get("start_date"),
                    "right_start": right.get("start_date"),
                    "right_end": right.get("end_date") or right.get("start_date"),
                    "left_label": left.get("label_fr") or left.get("start_date"),
                    "right_label": right.get("label_fr") or right.get("start_date"),
                    **({"business_query": query.to_dict()} if query else {}),
                },
                "Comparer deux dates explicites via SQL controle",
            )
        ]

    @staticmethod
    def _knowledge_family_from_query(query: BusinessQuery) -> str:
        if query.action == "opinion":
            return "business_analysis"
        if query.object in {"architecture", "dashboard"} and query.action == "explain":
            return "architecture"
        if query.action == "code":
            return "code"
        if query.action == "help":
            return "conversation"
        if query.action == "explain":
            return "business_rag"
        return "sql_metrics"

    @staticmethod
    def _capability_from_query(query: BusinessQuery) -> str | None:
        if query.action == "opinion":
            return "dashboard_opinion"
        if query.action == "compare":
            return "compare_periods"
        if query.metric == "slow_count" and query.action in {"count", "rate"}:
            return "slow_transaction_count"
        if query.metric == "fraud_timeout_count" and query.action in {"count", "rate"}:
            return "fraud_timeout_count"
        if query.object == "anomaly" and query.action == "count":
            return "unsupported_anomaly_count"
        if query.action == "explain" and query.object in {"architecture", "dashboard"}:
            return "architecture_explanation"
        if query.action == "explain":
            return "business_documentation"
        if query.object == "affiliation":
            return "affiliation_summary"
        if query.object == "tpe" and query.action == "count":
            return "tpe_count"
        if query.object == "tpe" or query.dimension == "tpe":
            return "tpe_ranking"
        if query.object == "merchant" or query.dimension == "merchant":
            return "merchant_ranking"
        if query.object == "anomaly":
            return "anomaly_ranking"
        if query.dimension == "hour" or query.object == "incident":
            return "incidents_by_hour"
        if query.metric == "non_completed_count":
            return "non_completed_transaction_count"
        if query.metric in {"success_rate", "authorized_count", "authorization_rate"}:
            return "transaction_success_metric"
        if query.metric in {"refusal_rate", "refused_count"}:
            return "transaction_refusal_metric"
        if query.object == "transaction":
            return "transaction_volume"
        return None

    @staticmethod
    def _unsupported_plan(capability: str, query: BusinessQuery, period: str) -> list[ToolCall]:
        requested = ToolSelector._unsupported_requested_label(capability, query)
        return [
            ToolCall(
                "unsupported_capability",
                {
                    "capability": capability,
                    "requested": requested,
                    "requested_metric": query.metric,
                    "metric_subject": ToolSelector._unsupported_metric_subject(capability, query),
                    "period": ToolSelector._period_key(query.period, period),
                    **({"temporal": query.period} if query.period else {}),
                    "alternatives": ToolSelector._unsupported_alternatives(capability),
                },
                "Répondre de manière contrôlée quand aucune capacité outillée cohérente n'existe",
            )
        ]

    @staticmethod
    def _unsupported_metric_subject(capability: str, query: BusinessQuery) -> str | None:
        if capability == "unsupported_historical_fraud_timeout_count" or query.metric == "fraud_timeout_count":
            return "fraud_timeouts"
        if capability == "unsupported_slow_transaction_count" or query.metric == "slow_count":
            return "transactions_lentes"
        return None

    @staticmethod
    def _unsupported_requested_label(capability: str, query: BusinessQuery) -> str:
        if capability == "unsupported_anomaly_count":
            return "le nombre d'anomalies"
        if capability == "unsupported_slow_transaction_count":
            return "le nombre de transactions lentes"
        if capability == "unsupported_historical_fraud_timeout_count":
            return "le nombre de fraudes ou timeouts anti-fraude sur période historique"
        metric = query.metric or "cette métrique"
        obj = query.object or "cet objet"
        return f"{metric} sur {obj}"

    @staticmethod
    def _unsupported_alternatives(capability: str) -> list[str]:
        if capability == "unsupported_anomaly_count":
            return ["Afficher les anomalies les plus visibles", "Analyser les incidents par heure"]
        if capability == "unsupported_slow_transaction_count":
            return ["Fournir le volume total des transactions", "Analyser les anomalies associées"]
        if capability == "unsupported_historical_fraud_timeout_count":
            return ["Analyser les transactions refusées sur cette période", "Voir les anomalies les plus visibles"]
        return ["Poser une autre question métier", "Analyser un indicateur disponible"]

    @classmethod
    def _fraud_timeout_plan(cls, query: BusinessQuery, period: str, context: dict[str, Any]) -> list[ToolCall]:
        temporal = query.period or {}
        if temporal.get("start_date") and temporal.get("end_date") and temporal.get("source") != "relative":
            return cls._unsupported_plan("unsupported_historical_fraud_timeout_count", query, period)
        return [
            ToolCall(
                "get_kpi_summary",
                {
                    "period": cls._period_key(temporal, period),
                    "business_query": query.to_dict(),
                    "metric_subject": context.get("metric_subject") or "fraud_timeouts",
                },
                "Lire le KPI timeouts anti-fraude depuis le snapshot de supervision",
            )
        ]

    @classmethod
    def _slow_transaction_plan(cls, query: BusinessQuery, period: str, context: dict[str, Any]) -> list[ToolCall]:
        temporal = query.period or {}
        if temporal.get("start_date") and temporal.get("end_date") and temporal.get("source") != "relative":
            return [
                ToolCall(
                    "get_transactions_between_dates",
                    {
                        "start_date": temporal.get("start_date"),
                        "end_date": temporal.get("end_date"),
                        "temporal": temporal,
                        "business_query": query.to_dict(),
                        "metric_subject": context.get("metric_subject") or "transactions_lentes",
                    },
                    "Reconstruire la requête métier des transactions lentes sur la période explicite",
                )
            ]
        return [
            ToolCall(
                "get_kpi_summary",
                {
                    "period": cls._period_key(temporal, period),
                    "business_query": query.to_dict(),
                    "metric_subject": context.get("metric_subject") or "transactions_lentes",
                },
                "Lire le KPI transactions lentes depuis le snapshot de supervision",
            )
        ]

    @staticmethod
    def _period_key(temporal: dict[str, Any] | None, fallback: str) -> str:
        if not isinstance(temporal, dict):
            return fallback
        return str(temporal.get("period_key") or fallback)
