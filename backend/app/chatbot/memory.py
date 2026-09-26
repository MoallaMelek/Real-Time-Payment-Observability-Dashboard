from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    role: str
    content: str
    timestamp: datetime


@dataclass(slots=True)
class AnalyticalContext:
    analysis_subject: str | None = None
    analysis_scope: str | None = None
    analysis_type: str | None = None
    analysis_result: dict[str, Any] | None = None
    primary_value: Any = None
    secondary_values: dict[str, Any] = field(default_factory=dict)
    derived_values: dict[str, Any] = field(default_factory=dict)
    comparison_result: dict[str, Any] | None = None
    period: str | None = None
    entity: str | None = None
    metric: str | None = None
    count: int | float | None = None
    rate: int | float | None = None
    conclusion: str | None = None
    recommendation: str | None = None
    confidence: str | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_subject": self.analysis_subject,
            "analysis_scope": self.analysis_scope,
            "analysis_type": self.analysis_type,
            "analysis_result": self.analysis_result,
            "primary_value": self.primary_value,
            "secondary_values": self.secondary_values,
            "derived_values": self.derived_values,
            "comparison_result": self.comparison_result,
            "period": self.period,
            "entity": self.entity,
            "metric": self.metric,
            "count": self.count,
            "rate": self.rate,
            "conclusion": self.conclusion,
            "recommendation": self.recommendation,
            "confidence": self.confidence,
            "source": self.source,
        }


@dataclass(slots=True)
class ComparisonContext:
    left_period: str | None = None
    right_period: str | None = None
    left_value: int | float | None = None
    right_value: int | float | None = None
    difference: int | float | None = None
    percentage_difference: int | float | None = None
    winner: str | None = None
    summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_period": self.left_period,
            "right_period": self.right_period,
            "left_value": self.left_value,
            "right_value": self.right_value,
            "difference": self.difference,
            "percentage_difference": self.percentage_difference,
            "winner": self.winner,
            "summary": self.summary,
        }


@dataclass(slots=True)
class ConversationState:
    last_business_query: dict[str, Any] | None = None
    last_period: str | None = None
    last_action: str | None = None
    last_object: str | None = None
    last_metric: str | None = None
    last_capability: str | None = None
    last_dimension: str | None = None
    last_filters: dict[str, Any] = field(default_factory=dict)
    last_comparison: dict[str, Any] | None = None
    last_tool: str | None = None
    last_evidence: dict[str, Any] | None = None
    last_result_summary: dict[str, Any] | None = None
    last_numeric_values: dict[str, Any] = field(default_factory=dict)
    last_result_values: dict[str, Any] = field(default_factory=dict)
    last_result_period: str | None = None
    last_result_metric: str | None = None
    last_result_interpretation: str | None = None
    last_business_topic: str | None = None
    active_context: dict[str, Any] = field(default_factory=dict)
    current_analysis_context: AnalyticalContext | None = None
    comparison_context: ComparisonContext | None = None
    recent_outputs: list[dict[str, Any]] = field(default_factory=list)


class ConversationMemory:
    def __init__(self, max_turns: int = 12) -> None:
        self._max_turns = max_turns
        self._sessions: dict[str, deque[ConversationTurn]] = defaultdict(lambda: deque(maxlen=max_turns))
        self._states: dict[str, ConversationState] = defaultdict(ConversationState)

    def ensure_session(self, session_id: str | None = None) -> str:
        if session_id and session_id.strip():
            return session_id.strip()[:80]
        return f"chat-{uuid4().hex[:16]}"

    def add(self, session_id: str, role: str, content: str) -> None:
        self._sessions[session_id].append(
            ConversationTurn(role=role, content=content[:2500], timestamp=datetime.now(timezone.utc))
        )

    def history(self, session_id: str) -> list[ConversationTurn]:
        return list(self._sessions.get(session_id, []))

    def compact_context(self, session_id: str) -> str:
        turns = self.history(session_id)[-self._max_turns :]
        return "\n".join(f"{turn.role}: {turn.content}" for turn in turns)

    def state(self, session_id: str) -> ConversationState:
        return self._states[session_id]

    def update_state(
        self,
        session_id: str,
        *,
        business_query: dict[str, Any] | None = None,
        tool: str | None = None,
        evidence: dict[str, Any] | None = None,
        result_summary: dict[str, Any] | None = None,
        answer: str | None = None,
    ) -> None:
        state = self._states[session_id]
        if business_query:
            previous_topic = state.last_business_topic
            state.last_business_query = business_query
            period = business_query.get("period")
            state.last_period = period.get("period_key") if isinstance(period, dict) else None
            state.last_action = business_query.get("action")
            state.last_object = business_query.get("object") or business_query.get("entity")
            state.last_metric = business_query.get("metric")
            state.last_capability = business_query.get("capability")
            state.last_dimension = business_query.get("dimension")
            state.last_filters = dict(business_query.get("filters") or {})
            comparison = business_query.get("comparison")
            state.last_comparison = comparison if isinstance(comparison, dict) else None
            state.last_business_topic = self._business_topic(business_query)
            query_context = business_query.get("context") if isinstance(business_query.get("context"), dict) else {}
            is_follow_up = bool(query_context.get("is_follow_up"))
            topic_changed = bool(query_context.get("topic_changed"))
            if previous_topic and state.last_business_topic and previous_topic != state.last_business_topic and (topic_changed or not is_follow_up):
                state.current_analysis_context = None
                state.comparison_context = None
                state.last_result_summary = None
                state.last_numeric_values = {}
                state.last_result_values = {}
                state.last_result_period = None
                state.last_result_metric = None
                state.last_result_interpretation = None
            state.active_context = {
                "period": state.last_period,
                "action": state.last_action,
                "object": state.last_object,
                "metric": state.last_metric,
                "capability": state.last_capability,
                "dimension": state.last_dimension,
                "filters": state.last_filters,
                "topic": state.last_business_topic,
            }
        if tool:
            state.last_tool = tool
        if evidence:
            state.last_evidence = evidence
        if result_summary:
            operation = str(result_summary.get("operation") or result_summary.get("kind") or "")
            if operation in {"reset_context", "clear_context"}:
                self.clear_analysis_context(session_id)
                return
            if operation in {"change_topic", "set_topic"}:
                self._clear_business_context(state)
                if operation == "set_topic":
                    period = result_summary.get("period")
                    obj = result_summary.get("object")
                    metric = result_summary.get("metric")
                    subject = result_summary.get("metric_subject")
                    state.last_period = str(period) if period else state.last_period
                    state.last_object = str(obj) if obj else state.last_object
                    state.last_metric = str(metric) if metric else state.last_metric
                    state.last_business_topic = str(subject or obj or metric) if subject or obj or metric else state.last_business_topic
                    state.active_context = {
                        "period": state.last_period,
                        "object": state.last_object,
                        "metric": state.last_metric,
                        "topic": state.last_business_topic,
                    }
            state.last_result_summary = result_summary
            numeric_values = self._numeric_values(result_summary)
            if numeric_values or result_summary.get("values"):
                state.last_numeric_values = numeric_values
                raw_values = result_summary.get("result_values")
                state.last_result_values = dict(raw_values) if isinstance(raw_values, dict) else state.last_numeric_values
                state.last_result_period = result_summary.get("period") or state.last_period
                state.last_result_metric = result_summary.get("metric") or state.last_metric
                state.last_result_interpretation = result_summary.get("interpretation")
            analysis_context = self._analysis_context_from_summary(state, result_summary, tool)
            if analysis_context:
                state.current_analysis_context = analysis_context
            comparison_context = self._comparison_context_from_summary(result_summary)
            if comparison_context:
                state.comparison_context = comparison_context
        output_frame = self._recent_output_frame(state, tool, result_summary, answer)
        if output_frame:
            state.recent_outputs = [*state.recent_outputs[-3:], output_frame]

    def clear_analysis_context(self, session_id: str) -> None:
        state = self._states[session_id]
        state.last_business_query = None
        state.last_period = None
        state.last_action = None
        state.last_object = None
        state.last_metric = None
        state.last_capability = None
        state.last_dimension = None
        state.last_filters = {}
        state.last_comparison = None
        state.last_tool = None
        state.last_evidence = None
        state.last_result_summary = None
        state.last_numeric_values = {}
        state.last_result_values = {}
        state.last_result_period = None
        state.last_result_metric = None
        state.last_result_interpretation = None
        state.last_business_topic = None
        state.active_context = {}
        state.current_analysis_context = None
        state.comparison_context = None
        state.recent_outputs = []

    @staticmethod
    def _clear_business_context(state: ConversationState) -> None:
        state.last_business_query = None
        state.last_period = None
        state.last_action = None
        state.last_object = None
        state.last_metric = None
        state.last_capability = None
        state.last_dimension = None
        state.last_filters = {}
        state.last_comparison = None
        state.last_tool = None
        state.last_evidence = None
        state.last_result_summary = None
        state.last_numeric_values = {}
        state.last_result_values = {}
        state.last_result_period = None
        state.last_result_metric = None
        state.last_result_interpretation = None
        state.last_business_topic = None
        state.active_context = {}
        state.current_analysis_context = None
        state.comparison_context = None

    @staticmethod
    def _business_topic(business_query: dict[str, Any]) -> str | None:
        obj = business_query.get("object") or business_query.get("entity")
        metric = business_query.get("metric")
        if obj and metric:
            return f"{obj}:{metric}"
        return obj or metric or business_query.get("dimension") or business_query.get("action")

    @staticmethod
    def _recent_output_frame(
        state: ConversationState,
        tool: str | None,
        result_summary: dict[str, Any] | None,
        answer: str | None,
    ) -> dict[str, Any] | None:
        if not tool and not result_summary and not answer:
            return None
        excerpt = (answer or "").strip()
        return {
            "tool": tool or state.last_tool,
            "answer_excerpt": excerpt[:320],
            "result_summary": dict(result_summary or state.last_result_summary or {}),
            "primary_value": summary_value(result_summary or state.last_result_summary or {}, "primary_value"),
            "secondary_value": summary_value(result_summary or state.last_result_summary or {}, "secondary_value"),
            "rate": summary_value(result_summary or state.last_result_summary or {}, "rate"),
            "count": summary_value(result_summary or state.last_result_summary or {}, "count"),
            "label": (result_summary or state.last_result_summary or {}).get("label")
            or (result_summary or state.last_result_summary or {}).get("value_label"),
            "source": (result_summary or state.last_result_summary or {}).get("source") or tool or state.last_tool,
            "business_query": dict(state.last_business_query or {}),
            "capability": state.last_capability,
            "period": (result_summary or state.last_result_summary or {}).get("period") or state.last_period,
        }

    @staticmethod
    def _numeric_values(result_summary: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in result_summary.items()
            if isinstance(value, (int, float)) and key in {"primary_value", "secondary_value", "rate", "count", "transactions", "refused"}
        }

    @classmethod
    def _analysis_context_from_summary(
        cls,
        state: ConversationState,
        result_summary: dict[str, Any],
        tool: str | None,
    ) -> AnalyticalContext | None:
        if result_summary.get("data_available") is False:
            return None
        source_values = result_summary.get("source_values") if isinstance(result_summary.get("source_values"), dict) else {}
        derived_values = result_summary.get("derived_values") if isinstance(result_summary.get("derived_values"), dict) else {}
        secondary_values = result_summary.get("secondary_values") if isinstance(result_summary.get("secondary_values"), dict) else {}
        raw_values = result_summary.get("result_values") if isinstance(result_summary.get("result_values"), dict) else {}
        if not source_values and raw_values:
            source_values = raw_values
        rate = cls._number(result_summary.get("rate") or source_values.get("refusal_rate") or derived_values.get("refusal_rate"))
        count = cls._number(result_summary.get("count") or result_summary.get("secondary_value") or source_values.get("refused"))
        primary = result_summary.get("primary_value")
        if primary is None and source_values:
            primary = source_values.get("transactions") or source_values.get("active_tpe")
        if primary is None and count is None and rate is None and not source_values and not derived_values:
            return None
        metric = str(result_summary.get("metric") or state.last_result_metric or state.last_metric or "").strip() or None
        period = str(result_summary.get("period") or state.last_result_period or state.last_period or "").strip() or None
        subject = state.last_business_topic or metric or result_summary.get("kind")
        return AnalyticalContext(
            analysis_subject=subject,
            analysis_scope=period,
            analysis_type=str(result_summary.get("kind") or state.last_action or "").strip() or None,
            analysis_result=dict(result_summary),
            primary_value=primary,
            secondary_values={**secondary_values, **{k: v for k, v in raw_values.items() if k not in source_values}},
            derived_values=derived_values,
            comparison_result=result_summary.get("comparison_result") if isinstance(result_summary.get("comparison_result"), dict) else None,
            period=period,
            entity=state.last_object,
            metric=metric,
            count=count,
            rate=rate,
            conclusion=result_summary.get("conclusion"),
            recommendation=result_summary.get("recommendation"),
            confidence=result_summary.get("confidence"),
            source=result_summary.get("source") or tool or state.last_tool,
        )

    @classmethod
    def _comparison_context_from_summary(cls, result_summary: dict[str, Any]) -> ComparisonContext | None:
        comparison = result_summary.get("comparison_context")
        if isinstance(comparison, dict):
            return ComparisonContext(
                left_period=comparison.get("left_period"),
                right_period=comparison.get("right_period"),
                left_value=cls._number(comparison.get("left_value")),
                right_value=cls._number(comparison.get("right_value")),
                difference=cls._number(comparison.get("difference")),
                percentage_difference=cls._number(comparison.get("percentage_difference")),
                winner=comparison.get("winner"),
                summary=comparison.get("summary"),
            )
        return None

    @staticmethod
    def _number(value: Any) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        return None


def summary_value(summary: dict[str, Any], key: str) -> Any:
    value = summary.get(key)
    return value if isinstance(value, (int, float)) else None
