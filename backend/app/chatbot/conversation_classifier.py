from __future__ import annotations

from dataclasses import dataclass
import re

from app.chatbot.message_normalizer import normalize_for_matching


ConversationCategory = str


INDIVIDUAL_SENSITIVE_TERMS = {
    "carte",
    "numero carte",
    "numéro carte",
    "card number",
    "pan",
    "rib",
    "iban",
    "otp",
    "pin",
    "identite client",
    "identité client",
    "client identity",
    "cle secrete",
    "clé secrète",
    "secret key",
    "token",
}

EXPLICIT_CODE_TERMS = {
    "fichier",
    "file",
    "code",
    "fonction",
    "function",
    "classe",
    "class",
    "route technique",
    "implementation",
    "implémentation",
    "backend",
    "frontend",
    "ou est defini",
    "où est défini",
    "where is defined",
    "source code",
}

BUSINESS_DIAGNOSTIC_TERMS = {
    "paiement",
    "paiements",
    "payment",
    "payments",
    "activite",
    "activité",
    "probleme",
    "problème",
    "inquiet",
    "inquiete",
    "inquiète",
    "inquietude",
    "inquiétude",
    "normal",
    "anormal",
    "panne",
    "incident",
    "difficulte",
    "difficulté",
    "commercant",
    "commerçant",
    "merchant",
    "transaction",
    "transactions",
    "journee",
    "journée",
    "refus",
    "tpe",
    "terminal",
}

BUSINESS_DATA_TERMS = {
    "combien",
    "nombre",
    "total",
    "taux",
    "pourcentage",
    "top",
    "classement",
    "compare",
    "comparaison",
    "transaction",
    "transactions",
    "refus",
    "paiement",
    "paiements",
    "commercant",
    "commerçant",
    "merchant",
    "tpe",
    "terminal",
    "affiliation",
    "stock",
}

DATA_REQUEST_CUES = {
    "combien",
    "nombre",
    "total",
    "taux",
    "pourcentage",
    "top",
    "classement",
    "compare",
    "comparaison",
}

ARCHITECTURE_TERMS = {
    "architecture",
    "rapide",
    "rapidite",
    "rapidité",
    "performance",
    "beaucoup de transactions",
    "reste rapide",
    "redis",
    "cache",
    "eventbus",
    "event bus",
    "replay",
    "websocket",
    "sql server",
    "dashboard",
    "tableau de bord",
}

SOCIAL_INTENTS = {"greeting", "small_talk", "help", "help_request", "thanks", "capabilities"}
BUSINESS_INTENTS = {
    "kpi_question",
    "anomaly_question",
    "merchant_question",
    "affiliation_question",
    "comparison_question",
    "follow_up_question",
    "status_distribution",
    "incidents_by_hour",
    "top_tpe",
    "merchant_details",
}


@dataclass(frozen=True, slots=True)
class ConversationClassification:
    category: ConversationCategory
    explicit_code: bool = False
    sensitive_individual_data: bool = False
    business_diagnostic: bool = False
    business_data_query: bool = False
    architecture: bool = False
    social: bool = False

    def to_dict(self) -> dict[str, bool | str]:
        return {
            "category": self.category,
            "explicit_code": self.explicit_code,
            "sensitive_individual_data": self.sensitive_individual_data,
            "business_diagnostic": self.business_diagnostic,
            "business_data_query": self.business_data_query,
            "architecture": self.architecture,
            "social": self.social,
        }


class ConversationClassifier:
    """Single priority policy for conversation categories."""

    @classmethod
    def classify(cls, message: str, analysis_intent: str | None = None) -> ConversationClassification:
        text = normalize_for_matching(message)
        explicit_code = cls.contains_any(text, EXPLICIT_CODE_TERMS)
        sensitive = cls.contains_any(text, INDIVIDUAL_SENSITIVE_TERMS)
        business_diagnostic = cls.contains_any(text, BUSINESS_DIAGNOSTIC_TERMS)
        business_data = cls.contains_any(text, BUSINESS_DATA_TERMS) or analysis_intent in BUSINESS_INTENTS
        explicit_data_request = cls.contains_any(text, DATA_REQUEST_CUES)
        architecture = cls.contains_any(text, ARCHITECTURE_TERMS) or analysis_intent == "architecture_question"
        social = analysis_intent in SOCIAL_INTENTS

        if explicit_code:
            return ConversationClassification("technical_code", explicit_code=True)
        if sensitive:
            return ConversationClassification("sensitive_individual_data", sensitive_individual_data=True)
        if architecture and not explicit_data_request:
            return ConversationClassification("architecture", architecture=True)
        if business_data and explicit_data_request:
            return ConversationClassification("business_data_query", business_data_query=True)
        if business_diagnostic:
            return ConversationClassification(
                "business_diagnostic",
                business_diagnostic=True,
                business_data_query=business_data,
            )
        if business_data:
            return ConversationClassification("business_data_query", business_data_query=True)
        if social:
            return ConversationClassification("social", social=True)
        return ConversationClassification("unknown")

    @classmethod
    def contains_any(cls, text: str, terms: set[str]) -> bool:
        return any(cls.contains_term(text, normalize_for_matching(term)) for term in terms)

    @staticmethod
    def contains_term(text: str, term: str) -> bool:
        if not term:
            return False
        if " " in term:
            return term in text
        return re.search(rf"\b{re.escape(term)}\b", text) is not None
