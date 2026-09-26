from __future__ import annotations

import re

from app.chatbot.business_analysis import BusinessAnalysis
from app.chatbot.adaptive_response_policy import AdaptiveResponsePolicy
from app.chatbot.conversation_strategy import ConversationStrategy
from app.chatbot.memory import ConversationState


class BusinessCommunicationEngine:
    """Turns business analysis into plain-language conversation."""

    NATURAL_GOALS = {
        "reassure_and_investigate",
        "diagnose",
        "drill_down",
        "simplify",
        "executive_summary",
        "decision_support",
        "explain_previous_answer",
    }

    INTERNAL_WORDS = ("KPI", "metric", "snapshot", "projection", "confidence", "tool", "reasoning", "evidence")

    @classmethod
    def compose(
        cls,
        *,
        analysis: BusinessAnalysis | None,
        strategy: ConversationStrategy | None,
        state: ConversationState | None,
        language: str,
        policy: AdaptiveResponsePolicy | None = None,
    ) -> tuple[str, list[str], str] | None:
        if analysis is None or strategy is None:
            return None
        if strategy.technical or strategy.goal not in cls.NATURAL_GOALS:
            return None
        if language == "en":
            return cls._compose_en(analysis, strategy, state)
        return cls._compose_fr(analysis, strategy, state)

    @classmethod
    def _compose_fr(
        cls,
        analysis: BusinessAnalysis,
        strategy: ConversationStrategy,
        state: ConversationState | None,
    ) -> tuple[str, list[str], str]:
        volume = cls._first_number(analysis.facts, "transactions")
        refusal = cls._first_percent(analysis.facts, "refus")
        success = cls._first_percent(analysis.facts, "succès") or cls._first_percent(analysis.facts, "succes")
        top = cls._first_after(analysis.facts, "Premier élément :")

        if strategy.goal == "simplify":
            lines = [
                "Dit simplement : je regarde si les paiements passent normalement, puis je cherche où les refus se concentrent.",
                "Quand les refus restent limités, ce n'est pas forcément un incident général.",
                "Ce qui compte ensuite, c'est de voir s'ils viennent surtout de quelques commerçants, de quelques terminaux ou d'un moment précis.",
            ]
        elif strategy.goal == "drill_down":
            focus = f"Le premier point visible est {top}." if top else "Je vais chercher les zones où le problème se concentre."
            lines = [
                "Oui. Je vais te montrer ce qui mérite attention en priorité.",
                focus,
                "L'idée n'est pas de conclure trop vite, mais d'isoler les endroits où les refus semblent se regrouper.",
            ]
        elif strategy.goal == "decision_support":
            lines = [
                "Je surveillerais d'abord les concentrations plutôt que le volume global.",
                "En pratique : les commerçants les plus touchés, les terminaux qui reviennent souvent, puis les horaires où les refus montent.",
                "Si le même signal reste présent sur plusieurs périodes, il devient prioritaire.",
            ]
        elif strategy.goal == "executive_summary":
            lines = [
                "Ce que je retiens : l'activité existe et la majorité des paiements semblent passer correctement.",
                cls._volume_sentence(volume),
                cls._refusal_sentence(refusal, success),
                "Le point utile maintenant est de vérifier si les refus sont dispersés ou concentrés sur quelques acteurs.",
            ]
        elif strategy.goal == "explain_previous_answer":
            lines = [
                "Parce qu'un taux global ne suffit pas à lui seul pour conclure.",
                "Un petit nombre de refus peut être normal s'il est dispersé.",
                "En revanche, si ces refus se concentrent sur les mêmes commerçants, terminaux ou horaires, là il faut creuser.",
            ]
        else:
            prefix = "Je comprends ton inquiétude." if strategy.goal == "reassure_and_investigate" else "À première vue, voici ce que je vois."
            lines = [
                prefix,
                cls._volume_sentence(volume),
                cls._refusal_sentence(refusal, success),
                "À ce stade, je ne vois pas de signal suffisant pour parler d'incident majeur.",
                "Le bon réflexe est de chercher si certains commerçants, terminaux ou horaires concentrent davantage les refus.",
            ]

        answer = "\n\n".join(line for line in lines if line)
        answer = cls._remove_internal_words(answer)
        follow_up = cls._followups_fr(strategy, state)
        return answer, follow_up, f"BusinessCommunicationEngine adapted tone={strategy.tone}, goal={strategy.goal}."

    @classmethod
    def _compose_en(
        cls,
        analysis: BusinessAnalysis,
        strategy: ConversationStrategy,
        state: ConversationState | None,
    ) -> tuple[str, list[str], str]:
        lines = [
            "At first glance, the situation looks manageable.",
            "Most payments appear to be accepted, and the useful next step is to see whether refusals are concentrated around specific merchants, terminals or hours.",
            "I would avoid jumping to a major-incident conclusion until that concentration is checked.",
        ]
        answer = cls._remove_internal_words("\n\n".join(lines))
        return answer, ["Show the merchants?", "Compare with yesterday?"], f"BusinessCommunicationEngine adapted tone={strategy.tone}, goal={strategy.goal}."

    @staticmethod
    def _volume_sentence(volume: str | None) -> str:
        if volume and volume.strip() != "0":
            return f"J'observe {volume} paiements sur la période regardée."
        return "Je regarde d'abord le niveau général d'activité."

    @staticmethod
    def _refusal_sentence(refusal: str | None, success: str | None) -> str:
        if success:
            return f"La très grande majorité semble acceptée : environ {success} de réussite."
        if refusal:
            return f"Il y a des refus, autour de {refusal}, mais ce niveau doit surtout être lu avec sa répartition."
        return "Je cherche ensuite si les refus restent limités ou s'ils se regroupent quelque part."

    @staticmethod
    def _first_number(items: list[str], label: str) -> str | None:
        for item in items:
            if label in item.lower():
                match = re.search(r"\b\d[\d\s]*\b", item)
                if match:
                    return match.group(0).strip()
        return None

    @staticmethod
    def _first_percent(items: list[str], label: str) -> str | None:
        for item in items:
            if label in item.lower():
                segment = item.lower().split(label, 1)[1]
                match = re.search(r"\d+(?:[.,]\d+)?\s*%", segment)
                if match:
                    return match.group(0).replace(".", ",")
        return None

    @staticmethod
    def _first_after(items: list[str], marker: str) -> str | None:
        for item in items:
            if marker in item:
                value = item.split(marker, 1)[1].strip()
                return value.split(", avec", 1)[0].strip()
        return None

    @classmethod
    def _remove_internal_words(cls, answer: str) -> str:
        cleaned = answer
        for word in cls.INTERNAL_WORDS:
            cleaned = cleaned.replace(word, "élément")
            cleaned = cleaned.replace(word.lower(), "élément")
        return cleaned

    @staticmethod
    def _followups_fr(strategy: ConversationStrategy, state: ConversationState | None) -> list[str]:
        if strategy.goal in {"reassure_and_investigate", "diagnose", "executive_summary"}:
            return ["Montre-moi les commerçants concernés", "Compare avec hier"]
        if strategy.goal == "drill_down":
            return ["Et les TPE ?", "Pourquoi eux ?"]
        if strategy.goal == "decision_support":
            return ["Prioriser les commerçants", "Voir les horaires"]
        if strategy.goal == "simplify":
            return ["Montre un exemple", "Revenir au détail"]
        return ["Continuer", "Comparer avec hier"]
