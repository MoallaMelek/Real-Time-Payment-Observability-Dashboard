from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.chatbot.message_normalizer import MessageNormalizer


@dataclass(frozen=True, slots=True)
class AdaptiveResponsePolicy:
    depth: str
    include_interpretation: bool
    include_limits: bool
    include_recommendations: bool
    technical_terms_allowed: bool

    def to_dict(self) -> dict[str, bool | str]:
        return {
            "depth": self.depth,
            "include_interpretation": self.include_interpretation,
            "include_limits": self.include_limits,
            "include_recommendations": self.include_recommendations,
            "technical_terms_allowed": self.technical_terms_allowed,
        }


class AdaptiveResponsePolicyEngine:
    """Decides how much detail the answer should contain."""

    FACTUAL_CUES = ("combien", "nombre", "total", "quel est", "donne moi le taux", "donne-moi le taux")
    ANALYTICAL_CUES = ("analyse", "diagnostic", "probleme", "panne", "inquiet", "normal", "pourquoi", "compare", "comparaison", "montre", "top")
    EXPERT_CUES = ("recommande", "decision", "décision", "priorite", "priorité", "synthese", "synthèse", "bilan", "avis", "architecture")
    TECHNICAL_CUES = ("technique", "code", "sql", "redis", "eventbus", "backend", "frontend", "implementation")

    @classmethod
    def decide(cls, message: str, understanding: Any | None, strategy: Any | None) -> AdaptiveResponsePolicy:
        text = MessageNormalizer.normalize_text(message)
        technical = bool(getattr(strategy, "technical", False)) or cls._matches(text, cls.TECHNICAL_CUES)
        goal = getattr(strategy, "goal", "")
        action = getattr(understanding, "action", None)
        conversation_kind = getattr(understanding, "conversation_kind", None)

        if technical or conversation_kind in {"code", "architecture"}:
            return AdaptiveResponsePolicy("expert", True, True, True, True)
        if goal in {"decision_support", "executive_summary", "opinion"} or cls._matches(text, cls.EXPERT_CUES):
            return AdaptiveResponsePolicy("expert", True, True, True, False)
        if cls._is_factual(text, action, goal):
            return AdaptiveResponsePolicy("factual", False, False, False, False)
        if goal in {"reassure_and_investigate", "diagnose", "drill_down", "explain_previous_answer", "compare"} or cls._matches(text, cls.ANALYTICAL_CUES):
            return AdaptiveResponsePolicy("analytical", True, True, True, False)
        return AdaptiveResponsePolicy("analytical", True, False, False, False)

    @classmethod
    def _is_factual(cls, text: str, action: str | None, goal: str) -> bool:
        if goal != "answer_business_question":
            return False
        if action not in {"count", "rate"}:
            return False
        if cls._matches(text, cls.ANALYTICAL_CUES) or cls._matches(text, cls.EXPERT_CUES):
            return False
        return cls._matches(text, cls.FACTUAL_CUES)

    @staticmethod
    def _matches(text: str, cues: tuple[str, ...]) -> bool:
        return any(cue in text for cue in cues)
