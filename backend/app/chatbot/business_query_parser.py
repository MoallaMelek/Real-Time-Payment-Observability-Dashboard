from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.chatbot.router import ChatIntent, normalize_text
from app.chatbot.temporal_resolver import TemporalRange


@dataclass(frozen=True, slots=True)
class BusinessQuery:
    action: str | None
    object: str | None
    metric: str | None
    dimension: str | None
    period: dict[str, Any] | None
    filters: dict[str, Any]
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "object": self.object,
            "entity": self.object,
            "metric": self.metric,
            "dimension": self.dimension,
            "period": self.period,
            "aggregation": "rank" if self.action == "rank" else f"group_by_{self.dimension}" if self.dimension else "aggregate" if self.action in {"count", "rate"} else None,
            "filters": self.filters,
            "confidence": self.confidence,
        }


class BusinessQueryParser:
    """Stable semantic parser for dashboard business questions."""

    @classmethod
    def parse(cls, message: str, intent: ChatIntent, temporal: TemporalRange | None) -> BusinessQuery:
        text = normalize_text(message)
        action = cls._action(text, intent)
        business_object = cls._object(text, intent)
        metric = cls._metric(text, action, business_object)
        dimension = cls._dimension(text, intent, business_object, action)
        filters = cls._filters(text)
        confidence = cls._confidence(action, business_object, metric, dimension, temporal, text)
        return BusinessQuery(
            action=action,
            object=business_object,
            metric=metric,
            dimension=dimension,
            period=temporal.to_dict() if temporal else None,
            filters=filters,
            confidence=confidence,
        )

    @staticmethod
    def _action(text: str, intent: ChatIntent) -> str | None:
        if intent.intent in {"code_question", "code_request"}:
            return "code"
        data_cue = any(token in text for token in ("taux", "pourcentage", "%", "combien", "nombre", "total", "transaction", "transactions", "refus", "fraude", "fraudes", "fraud"))
        if (intent.intent == "opinion_question" or any(token in text for token in ("avis", "opinion", "pense quoi", "credible", "soutenance"))) and not data_cue:
            return "opinion"
        if any(token in text for token in ("compare", "comparaison", "evolution", "évolution", "difference", "différence", " vs ", " versus ")):
            return "compare"
        if any(token in text for token in ("top", "classement", "plus eleves", "plus élevés", "plus frequents", "plus fréquents")):
            return "rank"
        if any(token in text for token in ("taux", "pourcentage", "%")):
            return "rate"
        if any(token in text for token in ("combien", "nombre", "total", "observes", "observés", "compte", "count")):
            return "count"
        if intent.intent == "top_tpe":
            return "rank"
        if intent.intent == "incidents_by_hour":
            return "count"
        if any(token in text for token in ("explique", "pourquoi", "c'est quoi", "definition", "définition", "comment")):
            return "explain"
        if intent.intent == "merchant_details":
            return "search"
        if intent.intent in {"greeting", "small_talk", "help", "help_request", "thanks", "capabilities"}:
            return "help"
        return None

    @staticmethod
    def _object(text: str, intent: ChatIntent) -> str | None:
        if any(token in text for token in ("redis", "eventbus", "event bus", "replay", "architecture", "dashboard", "tableau de bord", "websocket")):
            return "architecture" if "dashboard" not in text and "tableau de bord" not in text else "dashboard"
        if any(token in text for token in ("transaction", "transactions", "trx")):
            return "transaction"
        if any(token in text for token in ("tpe", "terminal", "terminaux", "serie", "série", "serial")):
            return "tpe"
        if any(token in text for token in ("commercant", "commerçant", "merchant")):
            return "merchant"
        if any(token in text for token in ("anomal", "risque", "risk")):
            return "anomaly"
        if any(token in text for token in ("affiliation", "affiliations", "stock")):
            return "affiliation"
        if any(token in text for token in ("incident", "incidents")):
            return "incident"
        if intent.intent in {"kpi_question", "kpi_summary"}:
            return "transaction"
        if intent.intent in {"top_tpe"}:
            return "tpe"
        if intent.intent in {"merchant_question", "top_merchants"}:
            return "merchant"
        if intent.intent in {"anomaly_question", "top_anomalies"}:
            return "anomaly"
        if intent.intent == "incidents_by_hour":
            return "incident"
        return None

    @staticmethod
    def _metric(text: str, action: str | None, business_object: str | None) -> str | None:
        if "taux" in text and "refus" in text:
            return "refusal_rate"
        if "taux" in text and any(token in text for token in ("succes", "succès", "success")):
            return "success_rate"
        if "taux" in text and any(token in text for token in ("autorise", "autorisé", "autorises", "autorisés")) and "non autor" not in text:
            return "authorization_rate"
        if any(token in text for token in ("autorise", "autorisé", "autorises", "autorisés")) and "non autor" not in text:
            return "authorized_count"
        if "non about" in text:
            return "non_completed_count"
        if "fraude" in text or "fraudes" in text or "fraud" in text:
            return "fraud_timeout_count"
        if "lent" in text or "slow" in text:
            return "slow_count"
        if "refus" in text or "non autor" in text:
            return "refused_count" if action == "count" else "refusal_rate" if action == "rate" else "refused_count"
        if action == "count" and business_object in {"tpe", "merchant"}:
            return "distinct_count"
        if action == "count":
            return "total"
        if action == "rate":
            return "refusal_rate"
        return None

    @staticmethod
    def _dimension(text: str, intent: ChatIntent, business_object: str | None, action: str | None) -> str | None:
        if "par heure" in text or "heure" in text or intent.intent == "incidents_by_hour":
            return "hour"
        if "par statut" in text or "status" in text or "statut" in text:
            return "status"
        if "par region" in text or "région" in text or "region" in text:
            return "region"
        if "par commerc" in text or "par merchant" in text or (action == "rank" and business_object == "merchant"):
            return "merchant"
        if "par tpe" in text or "par terminal" in text or (action == "rank" and business_object == "tpe"):
            return "tpe"
        return None

    @staticmethod
    def _filters(text: str) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if "non about" in text:
            filters["status"] = "non_completed"
        elif "refus" in text or "non autor" in text:
            filters["status"] = "refused"
        if "lent" in text or "slow" in text:
            filters["performance"] = "slow"
        if "fraude" in text or "fraudes" in text or "fraud" in text:
            filters["risk_signal"] = "fraud_timeout"
        return filters

    @staticmethod
    def _confidence(
        action: str | None,
        business_object: str | None,
        metric: str | None,
        dimension: str | None,
        temporal: TemporalRange | None,
        text: str,
    ) -> str:
        if action in {"help", "code", "opinion"}:
            return "high"
        if action in {"explain"} and business_object in {"architecture", "dashboard"}:
            return "high"
        if action and business_object and (metric or dimension) and temporal:
            return "high"
        if action and business_object and (metric or dimension):
            return "medium"
        if any(token in text for token in ("donne moi", "renseigne moi", "peux tu", "je veux", "j'aimerais")) and not business_object:
            return "low"
        return "low"
