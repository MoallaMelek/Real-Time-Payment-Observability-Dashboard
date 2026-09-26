from __future__ import annotations

import re

from app.chatbot.adaptive_response_policy import AdaptiveResponsePolicy
from app.chatbot.business_analysis import BusinessAnalysis
from app.chatbot.business_understanding import BusinessUnderstanding


class AnswerComposer:
    """Renders BusinessAnalysis into a natural answer without analyzing facts."""

    def compose(
        self,
        *,
        understanding: BusinessUnderstanding | None,
        analysis: BusinessAnalysis | None,
        policy: AdaptiveResponsePolicy | None = None,
        language: str,
    ) -> tuple[str, list[str], str] | None:
        if understanding is None or analysis is None:
            return None
        if not analysis.facts:
            return None
        if policy and policy.depth == "factual":
            answer = self._factual_answer(understanding, analysis, language)
            return answer, [], self._summary(analysis)
        if policy and policy.depth == "analytical" and understanding.action == "compare":
            return self._comparison_answer(analysis), ["Afficher les commerçants qui expliquent l'écart"], self._summary(analysis)
        if policy and policy.depth == "analytical" and understanding.action == "rank" and understanding.object == "merchant" and not understanding.context.get("is_follow_up"):
            return self._ranking_answer(analysis, understanding), ["Afficher le détail du commerçant"], self._summary(analysis)
        intro = self._intro(understanding, language)
        parts = [intro, self._section("Résultat", analysis.facts)]
        include_interpretation = policy.include_interpretation if policy else True
        include_limits = policy.include_limits if policy else True
        include_recommendations = policy.include_recommendations if policy else True
        technical_terms_allowed = policy.technical_terms_allowed if policy else False
        if include_interpretation and analysis.insights:
            parts.append(self._section("Lecture métier", analysis.insights))
        if include_interpretation and analysis.interpretation:
            parts.append(self._section("Interprétation", analysis.interpretation))
        if include_interpretation and analysis.possible_causes:
            parts.append(self._section("Causes possibles", analysis.possible_causes))
        if include_interpretation and analysis.anomalies:
            parts.append(self._section("Points d'attention", analysis.anomalies))
        if include_limits and analysis.limitations:
            parts.append(self._section("Limite", analysis.limitations))
        if technical_terms_allowed:
            parts.append(f"Confiance : {analysis.confidence_label} ({analysis.confidence:.2f}), sur la base de {', '.join(analysis.evidence_basis) or 'preuves disponibles'}.")
        if include_recommendations and analysis.recommendations:
            parts.append(self._section("Suite logique", analysis.recommendations[:2]))
        answer = "\n\n".join(part for part in parts if part)
        return answer, analysis.recommendations[:3] or ["Comparer avec une autre période ?"], self._summary(analysis)

    @staticmethod
    def _intro(understanding: BusinessUnderstanding, language: str) -> str:
        metric = understanding.metric or "les indicateurs"
        obj = understanding.object or understanding.dimension or "le dashboard"
        if language == "en":
            return f"I analyzed {metric} for {obj}."
        if understanding.context.get("is_follow_up"):
            return f"Je reprends le contexte précédent : j'ai compris que vous voulez analyser {metric} sur {obj}."
        return f"J'ai compris la demande et j'ai analysé {metric} sur {obj}."

    @staticmethod
    def _section(title: str, items: list[str]) -> str:
        if not items:
            return ""
        if len(items) == 1:
            return f"{title} : {items[0]}"
        return f"{title} :\n" + "\n".join(f"- {item}" for item in items)

    @classmethod
    def _factual_answer(cls, understanding: BusinessUnderstanding, analysis: BusinessAnalysis, language: str) -> str:
        facts = analysis.facts
        if understanding.capability == "multi_period_metric":
            return " ".join(fact if fact.endswith(".") else f"{fact}." for fact in facts)
        metric_subject = str(understanding.context.get("metric_subject") or "")
        metric = understanding.metric or ""
        obj = understanding.object or ""
        if metric_subject == "transactions_lentes" or metric == "slow_count":
            count = cls._first_matching(facts, ("transactions lentes",))
            avg = cls._first_matching(facts, ("temps moyen",))
            if count and avg:
                return f"{count.rstrip('.')} ({avg.rstrip('.')})."
            return count or facts[0]
        if metric_subject == "fraud_timeouts" or metric == "fraud_timeout_count":
            return cls._first_matching(facts, ("timeouts anti-fraude",)) or facts[0]
        if obj == "tpe" or metric == "distinct_count":
            fact = cls._first_matching(facts, ("tpe distincts", "tpe"))
            return cls._shorten_count(fact, "TPE distincts") if fact else facts[0]
        if metric_subject == "non_completed" or metric == "non_completed_count":
            count = cls._first_matching(facts, ("transactions non abouties",))
            rate = cls._first_matching(facts, ("taux de transactions non abouties",))
            if count and rate:
                percent = cls._first_percent(rate)
                return f"{count.rstrip('.')} ({percent})." if percent else count
            return count or rate or facts[0]
        if metric in {"refusal_rate", "refused_count"} or understanding.filters.get("status") == "refused":
            return cls._first_matching(facts, ("refus",)) or facts[0]
        if metric == "total" and obj == "transaction":
            fact = cls._first_matching(facts, ("transactions observ", "transactions"))
            return cls._shorten_count(fact, "transactions") if fact else facts[0]
        return facts[0]

    @classmethod
    def _comparison_answer(cls, analysis: BusinessAnalysis) -> str:
        facts = analysis.facts
        if len(facts) >= 3:
            conclusion = "Les deux périodes sont stables."
            delta = cls._first_signed_number(facts[2])
            if delta is not None and delta > 0:
                conclusion = "La première période est au-dessus."
            elif delta is not None and delta < 0:
                conclusion = "La première période est en dessous."
            return f"{facts[0]} {facts[1]} {facts[2]} {conclusion} Je peux ensuite afficher les commerçants qui expliquent l'écart."
        return " ".join(facts)

    @classmethod
    def _ranking_answer(cls, analysis: BusinessAnalysis, understanding: BusinessUnderstanding) -> str:
        first = cls._first_matching(analysis.facts, ("premier élément", "premier element"))
        period = understanding.period or {}
        period_hint = ""
        if isinstance(period, dict) and period.get("start_date"):
            start = period.get("start_date")
            end = period.get("end_date") or start
            period_hint = f" Sur {start}." if start == end else f" Sur {start} à {end}."
        if first:
            return f"{first}{period_hint} Je peux afficher le détail si besoin."
        return f"{analysis.facts[0]}{period_hint} Je peux afficher le détail si besoin."

    @staticmethod
    def _first_matching(items: list[str], needles: tuple[str, ...]) -> str | None:
        for item in items:
            lowered = item.lower()
            if any(needle in lowered for needle in needles):
                return item
        return None

    @staticmethod
    def _first_percent(value: str) -> str | None:
        match = re.search(r"\d+(?:[.,]\d+)?\s*%", value)
        return match.group(0) if match else None

    @staticmethod
    def _first_signed_number(value: str) -> float | None:
        match = re.search(r"-?\d+(?:[.,]\d+)?", value)
        return float(match.group(0).replace(",", ".")) if match else None

    @staticmethod
    def _shorten_count(value: str | None, label: str) -> str:
        if not value:
            return ""
        match = re.search(r"\b\d[\d\s]*\b", value)
        if not match:
            return value
        return f"{match.group(0).strip()} {label}."

    @staticmethod
    def _summary(analysis: BusinessAnalysis) -> str:
        return (
            f"BusinessAnalysis produced {len(analysis.facts)} fact(s), "
            f"{len(analysis.insights)} insight(s), confidence={analysis.confidence_label}."
        )
