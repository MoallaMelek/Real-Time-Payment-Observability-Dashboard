from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app.chatbot.business_understanding import BusinessUnderstanding
from app.chatbot.memory import ConversationState
from app.chatbot.message_normalizer import MessageNormalizer
from app.chatbot.router import ChatIntent


@dataclass(frozen=True, slots=True)
class ConversationStrategy:
    goal: str
    dialogue_mode: str
    tone: str
    detail_level: str
    audience: str
    response_type: str
    needs_tools: bool
    technical: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "dialogue_mode": self.dialogue_mode,
            "tone": self.tone,
            "detail_level": self.detail_level,
            "audience": self.audience,
            "response_type": self.response_type,
            "needs_tools": self.needs_tools,
            "technical": self.technical,
        }


class ConversationStrategyEngine:
    """Chooses how to help before any tool is selected."""

    CONCERN = ("inquiet", "inquiete", "inquiète", "inquietude", "peur", "risque", "probleme", "panne", "alerte", "grave", "anormal", "difficulte", "difficulté")
    DIAGNOSE = ("normal", "tout va bien", "etat", "situation", "semble", "verifie", "check")
    DRILL_DOWN = ("montre", "montrer", "detail", "détail", "ce qui t'inquiete", "ce qui t inquiète", "creuse", "approfondis")
    SIMPLIFY = ("simplement", "simple", "vulgarise", "explique moi simplement", "explique-moi simplement")
    SUMMARY = ("que retiens", "resume", "résume", "bilan", "synthese", "synthèse", "journee", "journée")
    DECISION = ("surveiller", "priorite", "priorité", "recommande", "decision", "décision", "quoi faire")
    TECHNICAL = ("technique", "architecture", "code", "sql", "redis", "eventbus", "event bus", "implementation")
    CONTINUE = ("continue", "pareil", "idem", "encore", "suite")
    WHY = ("pourquoi", "why")

    @classmethod
    def decide(
        cls,
        message: str,
        understanding: BusinessUnderstanding,
        state: ConversationState | None,
        intent: ChatIntent,
    ) -> ConversationStrategy:
        text = MessageNormalizer.normalize_text(message).strip(" ?!.")
        technical = cls._matches(text, cls.TECHNICAL) or understanding.conversation_kind in {"code", "architecture"}

        if understanding.conversation_kind == "code":
            return cls._strategy("technical_answer", "show", "direct", "detailed", "technical", "code", True, True)
        if technical and understanding.conversation_kind == "architecture":
            return cls._strategy("explain_system", "teach", "clear", "medium", "technical", "architecture", True, True)
        if understanding.conversation_kind == "opinion":
            return cls._strategy("opinion", "advise", "prudent", "medium", "general", "business", True, technical)

        if understanding.context.get("is_follow_up"):
            if cls._matches(text, cls.WHY):
                return cls._strategy("explain_previous_answer", "explain", "patient", "medium", "general", "business", True, technical)
            if cls._matches(text, cls.DRILL_DOWN) or cls._matches(text, cls.CONTINUE):
                return cls._strategy("drill_down", "investigate", "focused", "medium", "general", "business", True, technical)

        if cls._matches(text, cls.DRILL_DOWN) and (state and state.last_business_query or cls._matches(text, cls.CONCERN)):
            return cls._strategy("drill_down", "investigate", "focused", "medium", "general", "business", True, technical)
        explicit_dimension = understanding.dimension or understanding.object in {"merchant", "tpe", "affiliation"}
        if cls._matches(text, cls.CONCERN) and not explicit_dimension:
            return cls._strategy("reassure_and_investigate", "diagnose", "reassuring", "medium", "general", "business", True, technical)
        if cls._matches(text, cls.DIAGNOSE):
            return cls._strategy("diagnose", "diagnose", "balanced", "medium", "general", "business", True, technical)
        if cls._matches(text, cls.SIMPLIFY):
            return cls._strategy("simplify", "teach", "simple", "short", "general", "business", bool(state and state.last_business_query), technical)
        if cls._matches(text, cls.SUMMARY):
            return cls._strategy("executive_summary", "summarize", "calm", "medium", "general", "business", True, technical)
        if cls._matches(text, cls.DECISION):
            return cls._strategy("decision_support", "advise", "prudent", "medium", "general", "business", True, technical)
        if understanding.conversation_kind == "conversation":
            return cls._strategy(understanding.intent, "social", "friendly", "short", "general", "conversation", False, technical)
        if understanding.action == "compare":
            return cls._strategy("compare", "compare", "analytical", "medium", "general", "business", True, technical)
        if understanding.conversation_kind == "business":
            return cls._strategy("answer_business_question", "answer", "clear", "detailed", "general", "business", True, technical)
        return cls._strategy("clarify", "clarify", "friendly", "short", "general", "business", False, technical)

    @staticmethod
    def _matches(text: str, cues: tuple[str, ...]) -> bool:
        for cue in cues:
            if " " in cue:
                if cue in text:
                    return True
                continue
            if re.search(rf"\b{re.escape(cue)}\b", text):
                return True
        return False

    @staticmethod
    def _strategy(
        goal: str,
        dialogue_mode: str,
        tone: str,
        detail_level: str,
        audience: str,
        response_type: str,
        needs_tools: bool,
        technical: bool,
    ) -> ConversationStrategy:
        return ConversationStrategy(
            goal=goal,
            dialogue_mode=dialogue_mode,
            tone=tone,
            detail_level=detail_level,
            audience=audience,
            response_type=response_type,
            needs_tools=needs_tools,
            technical=technical,
        )
