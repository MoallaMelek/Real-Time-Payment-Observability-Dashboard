from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.chatbot.business_understanding import BusinessUnderstanding
from app.chatbot.memory import ConversationState


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    tools: list[str]
    has_sql: bool
    has_rag: bool
    has_snapshot: bool
    limitations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tools": self.tools,
            "has_sql": self.has_sql,
            "has_rag": self.has_rag,
            "has_snapshot": self.has_snapshot,
            "limitations": self.limitations,
        }


@dataclass(frozen=True, slots=True)
class ReasoningResult:
    summary: str
    confidence: str
    limitations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "confidence": self.confidence,
            "limitations": self.limitations,
        }


class ReasoningEngine:
    """Reason over canonical understanding and collected evidence only."""

    @classmethod
    def build_evidence_bundle(cls, evidence: dict[str, Any], tool_calls: list[dict[str, Any]]) -> EvidenceBundle:
        tools = [item.get("name") for item in tool_calls if item.get("name") and item.get("name") != "tool_rate_limited"]
        limitations: list[str] = []
        for tool in tools:
            value = evidence.get(str(tool))
            if isinstance(value, dict):
                if value.get("source") == "sql_unavailable":
                    limitations.append(f"{tool}: SQL Server unavailable")
                if value.get("source") == "sql_error":
                    limitations.append(f"{tool}: controlled SQL error")
                if value.get("data_available") is False:
                    limitations.append(f"{tool}: no data available")
        return EvidenceBundle(
            tools=[str(tool) for tool in tools],
            has_sql=any(str(tool).startswith("get_transactions") or str(tool) in {"get_top_merchants", "get_top_tpe", "get_incidents_by_hour", "get_tpe_count_by_period", "search_merchant", "compare_date_ranges"} for tool in tools),
            has_rag=any(str(tool).startswith("search_") or str(tool).startswith("explain_") for tool in tools),
            has_snapshot=any(str(tool) in {"get_kpi_summary", "get_kpi_by_period", "get_dashboard_snapshot", "get_affiliation_summary", "get_top_anomalies"} for tool in tools),
            limitations=limitations,
        )

    @classmethod
    def reason(
        cls,
        understanding: BusinessUnderstanding | None,
        bundle: EvidenceBundle,
        state: ConversationState | None = None,
    ) -> ReasoningResult:
        if not bundle.tools:
            return ReasoningResult("No tool evidence was collected; clarification or fallback response is required.", "low", bundle.limitations)
        if understanding and understanding.requires_sql and not bundle.has_sql:
            limitations = [*bundle.limitations, "Requested business data requires SQL evidence but no SQL tool was selected."]
            return ReasoningResult("Business question lacks the expected SQL evidence.", "low", limitations)
        if bundle.limitations:
            return ReasoningResult("Answer is constrained by available evidence and explicit tool limitations.", "medium", bundle.limitations)
        context_note = " Follow-up context was available." if state and state.last_business_query else ""
        return ReasoningResult(f"Answer can be grounded in collected tool evidence.{context_note}", "high", [])
