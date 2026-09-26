from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.chatbot.business_query_parser import BusinessQuery, BusinessQueryParser
from app.chatbot.followup_resolver import FollowUpResolution
from app.chatbot.memory import ConversationState
from app.chatbot.message_normalizer import MessageNormalizer
from app.chatbot.router import ChatIntent
from app.chatbot.temporal_resolver import TemporalRange, TemporalResolver


@dataclass(frozen=True, slots=True)
class BusinessQueryPatch:
    action: str | None = None
    object: str | None = None
    metric: str | None = None
    dimension: str | None = None
    filters: dict[str, Any] | None = None
    period: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None
    metric_subject: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "object": self.object,
            "metric": self.metric,
            "dimension": self.dimension,
            "filters": self.filters or {},
            "period": self.period,
            "comparison": self.comparison,
            "metric_subject": self.metric_subject,
        }


@dataclass(frozen=True, slots=True)
class BusinessUnderstanding:
    type: str
    conversation_kind: str
    knowledge_family: str
    intent: str
    action: str | None
    object: str | None
    metric: str | None
    capability: str | None
    dimension: str | None
    filters: dict[str, Any]
    period: dict[str, Any] | None
    comparison: dict[str, Any] | None
    context: dict[str, Any]
    requires_sql: bool
    requires_rag: bool
    requires_code_search: bool
    confidence: float
    business_query: BusinessQuery
    patch: BusinessQueryPatch | None = None
    follow_up: FollowUpResolution | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "conversation_kind": self.conversation_kind,
            "knowledge_family": self.knowledge_family,
            "intent": self.intent,
            "action": self.action,
            "object": self.object,
            "metric": self.metric,
            "capability": self.capability,
            "dimension": self.dimension,
            "filters": self.filters,
            "period": self.period,
            "comparison": self.comparison,
            "context": self.context,
            "requires_sql": self.requires_sql,
            "requires_rag": self.requires_rag,
            "requires_code_search": self.requires_code_search,
            "confidence": self.confidence,
            "business_query": self.business_query.to_dict(),
            "patch": self.patch.to_dict() if self.patch else None,
            "follow_up": self._follow_up_to_dict(self.follow_up),
        }

    @staticmethod
    def _follow_up_to_dict(resolved: FollowUpResolution | None) -> dict[str, Any] | None:
        if not resolved:
            return None
        return {
            "axis": resolved.axis,
            "period": resolved.period,
            "compare_left": resolved.compare_left,
            "compare_right": resolved.compare_right,
            "temporal": resolved.temporal.to_dict() if resolved.temporal else None,
            "metric_subject": resolved.metric_subject,
            "contextual": resolved.contextual,
        }


class BusinessUnderstandingEngine:
    """Canonical understanding layer for language, business meaning and memory fusion."""

    @classmethod
    def understand(
        cls,
        message: str,
        intent: ChatIntent,
        temporal: TemporalRange | None,
        state: ConversationState | None = None,
    ) -> BusinessUnderstanding:
        business_query = BusinessQueryParser.parse(message, intent, temporal)
        business_query = cls._promote_explanation(message, business_query)
        patch = cls._patch_from_state(message, business_query, temporal, state, intent.period, intent)
        follow_up = cls._follow_up_from_patch(patch, intent.period)
        business_query = cls._apply_patch(business_query, patch)
        reference_date = date.fromisoformat(temporal.start_date) if temporal and temporal.period_key == "today" else None
        periods = [item.to_dict() for item in TemporalResolver.resolve_many(message, reference_date=reference_date)]
        comparison = patch.comparison if patch and patch.comparison else cls._comparison(message, temporal)
        knowledge_family = cls._knowledge_family(business_query, intent, comparison)
        capability = cls._capability_for(message, business_query, knowledge_family, comparison, periods)
        topic = cls._topic_for(business_query)
        previous_topic = state.last_business_topic if state else None
        context = {
            "is_follow_up": bool(patch or follow_up),
            "topic_changed": bool(previous_topic and topic and previous_topic != topic),
            "active_context": state.active_context if state else None,
            "last_business_topic": previous_topic,
            "last_period": state.last_period if state else None,
            "last_action": state.last_action if state else None,
            "last_object": state.last_object if state else None,
            "last_metric": state.last_metric if state else None,
            "last_capability": state.last_capability if state else None,
            "last_dimension": state.last_dimension if state else None,
            "last_tool": state.last_tool if state else None,
            "metric_subject": patch.metric_subject if patch else cls._subject_from_query(business_query),
            "knowledge_query": cls._knowledge_query(message, business_query, intent),
            "periods": periods,
        }
        conversation_kind = cls._conversation_kind(business_query, intent)
        return BusinessUnderstanding(
            type="business_query" if conversation_kind == "business" else conversation_kind,
            conversation_kind=conversation_kind,
            knowledge_family=knowledge_family,
            intent=cls._canonical_intent(business_query, intent),
            action=business_query.action,
            object=business_query.object,
            metric=business_query.metric,
            capability=capability,
            dimension=business_query.dimension,
            filters=business_query.filters,
            period=business_query.period,
            comparison=comparison,
            context=context,
            requires_sql=cls._requires_sql(business_query),
            requires_rag=cls._requires_rag(business_query),
            requires_code_search=business_query.action == "code",
            confidence=cls._numeric_confidence(business_query.confidence),
            business_query=business_query,
            patch=patch,
            follow_up=follow_up,
        )

    @staticmethod
    def resolve_temporal(message: str, reference_date: Any, memory: TemporalRange | None = None) -> TemporalRange | None:
        return TemporalResolver.resolve(message, reference_date=reference_date, memory=memory)

    @staticmethod
    def _numeric_confidence(value: str) -> float:
        return {"high": 0.92, "medium": 0.68, "low": 0.32}.get(value, 0.3)

    @staticmethod
    def _conversation_kind(query: BusinessQuery, intent: ChatIntent) -> str:
        if query.action == "code" or intent.intent in {"code_question", "code_request"}:
            return "code"
        if query.action == "opinion":
            return "opinion"
        if query.action == "explain" and query.object == "transaction" and not query.metric:
            return "architecture"
        if query.action in {"rank", "count", "rate", "compare", "search"}:
            return "business"
        if query.action == "help" or intent.intent in {"greeting", "small_talk", "help", "help_request", "thanks", "capabilities"}:
            return "conversation"
        if query.object in {"architecture", "dashboard"} and query.action == "explain":
            return "architecture"
        if intent.intent in {"confidentiality"}:
            return "security"
        return "business"

    @staticmethod
    def _canonical_intent(query: BusinessQuery, intent: ChatIntent) -> str:
        if intent.intent in {"greeting", "small_talk", "help", "help_request", "thanks", "capabilities"}:
            return intent.intent
        if query.action in {"rank", "count", "rate", "compare", "search"}:
            return "analyse"
        if query.action == "explain":
            return "explain"
        return query.action or intent.intent

    @staticmethod
    def _requires_sql(query: BusinessQuery) -> bool:
        period = query.period or {}
        source = str(period.get("source") or "")
        granularity = str(period.get("granularity") or "")
        if query.dimension in {"merchant", "tpe", "hour"}:
            return True
        if query.action == "rank" and query.object in {"merchant", "tpe"}:
            return True
        if query.object in {"tpe", "incident"} and query.action in {"count", "rate"}:
            return True
        if query.action == "compare":
            return source == "explicit" or granularity in {"range", "month", "quarter", "year"}
        if query.action in {"count", "rate"} and query.object == "transaction":
            return source == "explicit" or granularity in {"range", "month", "quarter", "year"}
        return False

    @staticmethod
    def _requires_rag(query: BusinessQuery) -> bool:
        return query.action in {"explain", "opinion"} or query.object in {"architecture", "dashboard"}

    @staticmethod
    def _topic_for(query: BusinessQuery) -> str | None:
        if query.object and query.metric:
            return f"{query.object}:{query.metric}"
        return query.object or query.dimension or query.action

    @staticmethod
    def _comparison(message: str, temporal: TemporalRange | None) -> dict[str, Any] | None:
        normalized = message.lower()
        if not any(token in normalized for token in ("compare", "compar", " vs ", "versus", "par rapport")):
            return None
        explicit_dates = TemporalResolver.explicit_comparison_dates(message)
        if explicit_dates:
            left, right = explicit_dates
            return {
                "requested": True,
                "type": "date_ranges",
                "left": left.to_dict(),
                "right": right.to_dict(),
            }
        return {
            "requested": True,
            "period": temporal.to_dict() if temporal else None,
        }

    @classmethod
    def _patch_from_state(
        cls,
        message: str,
        query: BusinessQuery,
        temporal: TemporalRange | None,
        state: ConversationState | None,
        default_period: str,
        intent: ChatIntent,
    ) -> BusinessQueryPatch | None:
        if state is None or state.last_business_query is None:
            return None
        if query.object in {"architecture", "dashboard"} or intent.intent == "architecture_question":
            return None
        clean = MessageNormalizer.normalize_text(message).strip(" ?!.")
        explicit_comparison = TemporalResolver.explicit_comparison_dates(message)
        if query.action == "compare" and explicit_comparison:
            return None
        current_subject = cls._metric_subject(message)
        last_subject = cls._subject_from_state(state)
        last_temporal = cls._temporal_from_state(state)
        effective_temporal = temporal or last_temporal
        if "compar" in clean and "periode voisine" in clean and last_temporal:
            neighbor = cls._neighbor_before(last_temporal)
            return BusinessQueryPatch(
                action="compare",
                comparison={"requested": True, "type": "date_ranges", "left": last_temporal.to_dict(), "right": neighbor.to_dict()},
                metric_subject=last_subject,
            )

        if clean in {"compare avec hier", "compare hier", "vs hier"} and last_temporal and last_temporal.source != "relative":
            neighbor = cls._neighbor_before(last_temporal)
            return BusinessQueryPatch(
                action="compare",
                comparison={"requested": True, "type": "date_ranges", "left": last_temporal.to_dict(), "right": neighbor.to_dict()},
                metric_subject=last_subject,
            )

        if clean in {"compare avec hier", "compare hier", "vs hier"}:
            return BusinessQueryPatch(
                action="compare",
                comparison={"left": default_period, "right": "yesterday"},
                metric_subject=last_subject,
            )
        if clean in {"compare avec la periode precedente", "compare periode precedente"}:
            right = "yesterday" if default_period == "today" else "today"
            return BusinessQueryPatch(
                action="compare",
                comparison={"left": default_period, "right": right},
                metric_subject=last_subject,
            )

        if current_subject or (temporal and last_subject):
            subject = current_subject or last_subject
            if subject:
                if subject == last_subject and cls._state_has_business_signature(state):
                    return BusinessQueryPatch(
                        action=state.last_action or query.action,
                        object=state.last_object or query.object,
                        metric=state.last_metric or query.metric,
                        dimension=state.last_dimension if state.last_dimension is not None else query.dimension,
                        filters=dict(state.last_filters or query.filters),
                        period=effective_temporal.to_dict() if effective_temporal else None,
                        metric_subject=subject,
                    )
                return BusinessQueryPatch(
                    action=cls._action_for_subject(subject, query.action),
                    object=cls._object_for_subject(subject, query.object),
                    metric=cls._metric_for_subject(subject, query.metric),
                    dimension=cls._dimension_for_subject(subject, query.dimension),
                    filters=cls._filters_for_subject(subject, query.filters),
                    period=effective_temporal.to_dict() if effective_temporal else None,
                    metric_subject=subject,
                )

        if clean in {"et hier", "hier", "yesterday", "and yesterday"}:
            temporal_payload = temporal.to_dict() if temporal else None
            subject = last_subject or "kpi"
            if cls._state_has_business_signature(state):
                return BusinessQueryPatch(
                    action=state.last_action or query.action,
                    object=state.last_object or query.object,
                    metric=state.last_metric or query.metric,
                    dimension=state.last_dimension if state.last_dimension is not None else query.dimension,
                    filters=dict(state.last_filters or query.filters),
                    period=temporal_payload,
                    metric_subject=subject,
                )
            return BusinessQueryPatch(
                action=cls._action_for_subject(subject, query.action),
                object=cls._object_for_subject(subject, query.object),
                metric=cls._metric_for_subject(subject, query.metric),
                dimension=cls._dimension_for_subject(subject, query.dimension),
                period=temporal_payload,
                metric_subject=subject,
            )
        if clean in {"et les anomalies", "anomalies"}:
            return BusinessQueryPatch(action="rank", object="anomaly", dimension="merchant", period=effective_temporal.to_dict() if effective_temporal else None, metric_subject="anomalies")
        if clean in {"et les commercants", "et les commerçants", "commercants"}:
            return BusinessQueryPatch(action="rank", object="merchant", dimension="merchant", period=effective_temporal.to_dict() if effective_temporal else None, metric_subject="merchants")
        if clean in {"pourquoi", "pourquoi ca", "pourquoi ça"}:
            subject = last_subject or cls._subject_from_query(query) or "why"
            return BusinessQueryPatch(
                action="explain",
                object=state.last_object if cls._state_has_business_signature(state) else cls._object_for_subject(subject, query.object),
                metric=state.last_metric if cls._state_has_business_signature(state) else query.metric,
                filters=dict(state.last_filters or query.filters) if cls._state_has_business_signature(state) else query.filters,
                period=effective_temporal.to_dict() if effective_temporal else None,
                metric_subject="why",
            )
        return None

    @staticmethod
    def _neighbor_before(period: TemporalRange) -> TemporalRange:
        from datetime import date, timedelta

        start = date.fromisoformat(period.start_date)
        end = date.fromisoformat(period.end_date)
        days = max(1, (end - start).days + 1)
        right_end = start - timedelta(days=1)
        right_start = right_end - timedelta(days=days - 1)
        return TemporalRange(right_start.isoformat(), right_end.isoformat(), period.granularity, "derived_neighbor")

    @classmethod
    def _apply_patch(cls, query: BusinessQuery, patch: BusinessQueryPatch | None) -> BusinessQuery:
        if not patch:
            return query
        return BusinessQuery(
            action=patch.action or query.action,
            object=patch.object or query.object,
            metric=patch.metric or query.metric,
            dimension=patch.dimension if patch.dimension is not None else query.dimension,
            period=patch.period or query.period,
            filters={**query.filters, **(patch.filters or {})},
            confidence="high" if patch.period or patch.comparison or patch.metric_subject else query.confidence,
        )

    @staticmethod
    def _promote_explanation(message: str, query: BusinessQuery) -> BusinessQuery:
        normalized = message.lower()
        asks_why = any(token in normalized for token in ("pourquoi", "why", "augmente", "increase", "hausse"))
        is_refusal = query.metric in {"refusal_rate", "refused_count"} or query.filters.get("status") == "refused"
        if asks_why and is_refusal:
            return BusinessQuery(
                action="explain",
                object=query.object,
                metric=query.metric,
                dimension=query.dimension,
                period=query.period,
                filters=query.filters,
                confidence="high",
            )
        return query

    @staticmethod
    def _follow_up_from_patch(patch: BusinessQueryPatch | None, default_period: str) -> FollowUpResolution | None:
        if not patch:
            return None
        temporal = BusinessUnderstandingEngine._temporal_from_payload(patch.period)
        if patch.comparison:
            return FollowUpResolution(
                axis="comparison",
                period=default_period,
                compare_left=str(patch.comparison.get("left") or default_period),
                compare_right=str(patch.comparison.get("right") or "yesterday"),
                temporal=temporal,
                metric_subject=patch.metric_subject,
                contextual=True,
            )
        if patch.metric_subject:
            return FollowUpResolution(
                axis=patch.metric_subject,
                period=BusinessUnderstandingEngine._period_from_temporal(temporal, default_period),
                temporal=temporal,
                metric_subject=patch.metric_subject,
                contextual=True,
            )
        return None

    @staticmethod
    def _temporal_from_state(state: ConversationState) -> TemporalRange | None:
        query = state.last_business_query or {}
        period = query.get("period") if isinstance(query, dict) else None
        return BusinessUnderstandingEngine._temporal_from_payload(period if isinstance(period, dict) else None)

    @staticmethod
    def _temporal_from_payload(period: dict[str, Any] | None) -> TemporalRange | None:
        if not period:
            return None
        start = period.get("start_date")
        end = period.get("end_date")
        granularity = period.get("granularity") or "day"
        source = period.get("source") or "memory"
        if not start or not end:
            return None
        return TemporalRange(str(start), str(end), str(granularity), str(source), period.get("period_key"))

    @staticmethod
    def _period_from_temporal(temporal: TemporalRange | None, default_period: str) -> str:
        return temporal.period_key if temporal and temporal.period_key else default_period

    @staticmethod
    def _state_has_business_signature(state: ConversationState | None) -> bool:
        return bool(state and state.last_action and (state.last_object or state.last_metric or state.last_dimension))

    @staticmethod
    def _has_explicit_temporal_window(period: dict[str, Any] | None) -> bool:
        if not isinstance(period, dict):
            return False
        if not (period.get("start_date") and period.get("end_date")):
            return False
        return str(period.get("source") or "") != "relative"

    @classmethod
    def _subject_from_state(cls, state: ConversationState) -> str | None:
        query = state.last_business_query or {}
        if state.last_metric == "slow_count" or state.last_filters.get("performance") == "slow":
            return "transactions_lentes"
        if state.last_metric == "fraud_timeout_count" or state.last_filters.get("risk_signal") == "fraud_timeout":
            return "fraud_timeouts"
        if state.last_metric == "non_completed_count" or state.last_filters.get("status") == "non_completed":
            return "non_completed"
        if state.last_metric in {"refusal_rate", "refused_count"} or state.last_filters.get("status") == "refused":
            return "refus"
        if state.last_dimension == "merchant" or state.last_object == "merchant" or query.get("object") == "merchant":
            return "merchants"
        if state.last_dimension == "tpe" or state.last_object == "tpe" or query.get("object") == "tpe":
            return "tpe"
        if state.last_object in {"anomaly", "incident"} or query.get("object") in {"anomaly", "incident"}:
            return "anomalies"
        if state.last_object == "affiliation" or query.get("object") == "affiliation":
            return "affiliations"
        if state.last_object == "transaction" or query.get("object") == "transaction":
            return "transactions"
        return None

    @staticmethod
    def _subject_from_query(query: BusinessQuery) -> str | None:
        if query.metric == "slow_count" or query.filters.get("performance") == "slow":
            return "transactions_lentes"
        if query.metric == "fraud_timeout_count" or query.filters.get("risk_signal") == "fraud_timeout":
            return "fraud_timeouts"
        if query.metric == "non_completed_count" or query.filters.get("status") == "non_completed":
            return "non_completed"
        if query.metric in {"refusal_rate", "refused_count"} or query.filters.get("status") == "refused":
            return "refus"
        if query.dimension == "merchant" or query.object == "merchant":
            return "merchants"
        if query.dimension == "tpe" or query.object == "tpe":
            return "tpe"
        if query.object in {"anomaly", "incident"}:
            return "anomalies"
        if query.object == "affiliation":
            return "affiliations"
        if query.object == "transaction":
            return "transactions"
        return None

    @staticmethod
    def _metric_subject(message: str) -> str | None:
        normalized = MessageNormalizer.normalize_text(message)
        if ("transaction" in normalized and ("lente" in normalized or "slow" in normalized)) or "transactions lentes" in normalized:
            return "transactions_lentes"
        if "fraude" in normalized or "fraudes" in normalized or "fraud" in normalized:
            return "fraud_timeouts"
        if "anomal" in normalized or "incident" in normalized:
            return "anomalies"
        if "commerc" in normalized or "merchant" in normalized:
            return "merchants"
        if "affiliation" in normalized or "stock" in normalized:
            return "affiliations"
        if "non about" in normalized or "non-about" in normalized or "non completed" in normalized or "pending" in normalized:
            return "non_completed"
        if "refus" in normalized or "declin" in normalized:
            return "refus"
        if "succes" in normalized or "success" in normalized:
            return "success"
        if "transaction" in normalized or "transactions" in normalized or "trx" in normalized:
            return "transactions"
        if "kpi" in normalized or "taux" in normalized or "resume" in normalized:
            return "kpi"
        return None

    @staticmethod
    def _action_for_subject(subject: str, fallback: str | None) -> str | None:
        if subject in {"merchants", "tpe", "anomalies"}:
            return "rank"
        if subject in {"refus", "success", "non_completed", "transactions_lentes", "transactions", "kpi"}:
            return "count"
        return fallback

    @staticmethod
    def _object_for_subject(subject: str, fallback: str | None) -> str | None:
        return {
            "merchants": "merchant",
            "tpe": "tpe",
            "anomalies": "anomaly",
            "affiliations": "affiliation",
            "transactions": "transaction",
            "transactions_lentes": "transaction",
            "fraud_timeouts": "transaction",
            "non_completed": "transaction",
            "refus": "transaction",
            "success": "transaction",
            "kpi": "transaction",
        }.get(subject, fallback)

    @staticmethod
    def _metric_for_subject(subject: str, fallback: str | None) -> str | None:
        return {
            "transactions_lentes": "slow_count",
            "fraud_timeouts": "fraud_timeout_count",
            "non_completed": "non_completed_count",
            "refus": "refused_count",
            "success": "success_rate",
            "transactions": "total",
            "kpi": "total",
        }.get(subject, fallback)

    @staticmethod
    def _dimension_for_subject(subject: str, fallback: str | None) -> str | None:
        return {"merchants": "merchant", "tpe": "tpe", "anomalies": "merchant"}.get(subject, fallback)

    @staticmethod
    def _filters_for_subject(subject: str, fallback: dict[str, Any]) -> dict[str, Any]:
        filters = dict(fallback or {})
        if subject == "transactions_lentes":
            filters["performance"] = "slow"
        if subject == "non_completed":
            filters["status"] = "non_completed"
        if subject == "refus":
            filters["status"] = "refused"
        return filters

    @staticmethod
    def _knowledge_query(message: str, query: BusinessQuery, intent: ChatIntent) -> str:
        normalized = message.lower()
        if any(token in normalized for token in ("rapide", "fast", "performance", "15000", "15 000", "beaucoup de transactions")):
            return "Redis EventBus Fast Forward performance replay"
        if query.action == "opinion":
            return "architecture Redis EventBus replay limites chatbot dashboard"
        if query.object == "architecture":
            return f"{query.object or ''} {query.metric or ''} {intent.intent}".strip()
        if query.action == "code":
            return message[:240]
        return f"{query.action or ''} {query.object or ''} {query.metric or ''} {query.dimension or ''}".strip()

    @staticmethod
    def _knowledge_family(query: BusinessQuery, intent: ChatIntent, comparison: dict[str, Any] | None) -> str:
        if intent.intent == "confidentiality":
            return "security"
        if query.action == "code" or intent.intent in {"code_question", "code_request"}:
            return "code"
        if query.action == "opinion":
            return "business_analysis"
        if query.action == "help" or intent.intent in {"greeting", "small_talk", "help", "help_request", "thanks", "capabilities"}:
            return "conversation"
        if comparison:
            return "sql_metrics"
        if query.object in {"architecture", "dashboard"} and query.action == "explain":
            return "architecture"
        if query.action == "explain" and (query.metric in {"refusal_rate", "refused_count"} or query.filters.get("status") == "refused"):
            return "business_analysis"
        if query.action in {"count", "rate", "rank", "compare", "search"}:
            return "sql_metrics"
        if query.action == "explain":
            return "business_rag"
        return "business_analysis"

    @classmethod
    def _capability_for(
        cls,
        message: str,
        query: BusinessQuery,
        knowledge_family: str,
        comparison: dict[str, Any] | None,
        periods: list[dict[str, Any]] | None = None,
    ) -> str | None:
        normalized = MessageNormalizer.normalize_text(message)
        if periods and len(periods) > 1 and query.action in {"count", "rate"}:
            return "multi_period_metric"
        if comparison and comparison.get("type") == "date_ranges":
            return "compare_explicit_date_ranges"
        if knowledge_family == "conversation":
            return query.action or "conversation"
        if knowledge_family == "security":
            return "sensitive_guardrail"
        if knowledge_family == "code":
            return "code_search"
        if knowledge_family == "architecture":
            if "reste rapide" in normalized or "beaucoup de transactions" in normalized or "performance" in normalized:
                return "architecture_performance"
            if "fast forward" in normalized or "avance rapide" in normalized:
                return "architecture_fast_forward"
            return "architecture_explanation"
        if query.action == "opinion":
            return "dashboard_opinion"
        if query.action == "compare":
            return "compare_periods"
        if query.action == "explain" and query.metric in {"refusal_rate", "refused_count"}:
            return "refusal_diagnosis"
        if query.metric == "slow_count" and query.action in {"count", "rate"}:
            return "slow_transaction_count" if cls._has_explicit_temporal_window(query.period) else "unsupported_slow_transaction_count"
        if query.metric == "fraud_timeout_count" and query.action in {"count", "rate"}:
            return "unsupported_historical_fraud_timeout_count" if cls._has_explicit_temporal_window(query.period) else "fraud_timeout_count"
        if query.object == "anomaly" and query.action == "count":
            return "unsupported_anomaly_count"
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
        if query.metric in {"refusal_rate", "refused_count"}:
            return "transaction_refusal_metric"
        if query.object == "transaction" and query.action in {"count", "rate"}:
            return "transaction_volume"
        if knowledge_family == "business_rag":
            return "business_documentation"
        return None


CognitiveEngine = BusinessUnderstandingEngine
