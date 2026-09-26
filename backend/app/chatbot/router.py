from dataclasses import dataclass
import re

from app.chatbot.conversation_classifier import ConversationClassifier
from app.chatbot.intent_analyzer import IntentAnalyzer
from app.chatbot.message_normalizer import normalize_for_matching
from app.chatbot.temporal_resolver import TemporalResolver
from app.schemas.chat import ChatLanguage, ChatPeriod


Intent = str

VALID_PERIODS: set[ChatPeriod] = {"today", "yesterday", "7d", "30d", "quarter", "year"}


@dataclass(frozen=True, slots=True)
class ChatIntent:
    intent: Intent
    period: ChatPeriod
    language: ChatLanguage
    merchant_query: str | None = None
    sensitive: bool = False


def normalize_text(value: str) -> str:
    return normalize_for_matching(value)


def detect_language(message: str, explicit: str | None = None) -> ChatLanguage:
    if explicit in {"fr", "en"}:
        return explicit  # type: ignore[return-value]
    text = normalize_text(message)
    english_markers = {"what", "why", "explain", "how", "which", "today", "merchant", "rate", "used"}
    french_markers = {"quel", "pourquoi", "explique", "combien", "aujourd", "commercant", "taux", "utilise"}
    en_score = sum(marker in text for marker in english_markers)
    fr_score = sum(marker in text for marker in french_markers)
    return "en" if en_score > fr_score else "fr"


def extract_period(message: str, fallback: ChatPeriod | None = None) -> ChatPeriod:
    return TemporalResolver.period_key(message, fallback)


def is_sensitive_request(message: str) -> bool:
    return ConversationClassifier.classify(message).sensitive_individual_data


def is_explicit_code_request(message: str) -> bool:
    return ConversationClassifier.classify(message).explicit_code


def is_business_diagnostic_request(message: str) -> bool:
    return ConversationClassifier.classify(message).business_diagnostic


def detect_intent(message: str, period: ChatPeriod | None = None, language: str | None = None) -> ChatIntent:
    analysis = IntentAnalyzer.analyze(message)
    text = analysis.normalized_text
    detected_language = detect_language(message, language)
    detected_period = extract_period(message, period)
    classification = ConversationClassifier.classify(message, analysis.intent)
    if classification.category == "technical_code":
        return ChatIntent("code_question", detected_period, detected_language)
    if classification.category == "sensitive_individual_data":
        return ChatIntent("confidentiality", detected_period, detected_language, sensitive=True)
    if classification.category == "architecture":
        return ChatIntent("architecture_question", detected_period, detected_language)
    if classification.category == "business_diagnostic":
        if "tpe" in text or "terminal" in text or "serie" in text or "serial" in text:
            return ChatIntent("top_tpe", detected_period, detected_language)
        if "commerc" in text or "merchant" in text:
            merchant = _extract_merchant_hint(text)
            if merchant and not any(token in text for token in ("top", "plus", "most")):
                return ChatIntent("merchant_details", detected_period, detected_language, merchant)
            return ChatIntent("merchant_question", detected_period, detected_language)
        if any(token in text for token in ("panne", "probleme", "problème", "inquiet", "normal", "anormal", "incident", "difficulte", "difficulté")):
            return ChatIntent("anomaly_question", detected_period, detected_language)
        return ChatIntent("kpi_question", detected_period, detected_language)
    if classification.category == "business_data_query":
        if analysis.intent in {"comparison_question", "follow_up_question"}:
            return ChatIntent(analysis.intent, detected_period, detected_language)
        if "tpe" in text or "terminal" in text or "serie" in text or "serial" in text:
            return ChatIntent("top_tpe", detected_period, detected_language)
        if "commerc" in text or "merchant" in text:
            merchant = _extract_merchant_hint(text)
            if merchant and not any(token in text for token in ("top", "plus", "most")):
                return ChatIntent("merchant_details", detected_period, detected_language, merchant)
            return ChatIntent("merchant_question", detected_period, detected_language)
        if "affiliation" in text or "stock" in text:
            return ChatIntent("affiliation_question", detected_period, detected_language)
        if "statut" in text or "status distribution" in text or "repartition" in text:
            return ChatIntent("status_distribution", detected_period, detected_language)
        if "heure" in text or "hour" in text or "incident" in text:
            return ChatIntent("incidents_by_hour", detected_period, detected_language)
        if "anomal" in text or "risque" in text or "risk" in text or "high risk" in text:
            merchant = _extract_merchant_hint(text)
            return ChatIntent("merchant_details" if merchant else "anomaly_question", detected_period, detected_language, merchant)
        return ChatIntent("kpi_question", detected_period, detected_language)

    if analysis.intent == "code_question" and classification.category == "technical_code":
        return ChatIntent("code_question", detected_period, detected_language)
    if analysis.intent == "greeting":
        return ChatIntent("greeting", detected_period, detected_language)
    if analysis.intent == "small_talk":
        return ChatIntent("small_talk", detected_period, detected_language)
    if analysis.intent == "help":
        return ChatIntent("help", detected_period, detected_language)
    if "tpe" in text or "terminal" in text or "serie" in text or "serial" in text:
        return ChatIntent("top_tpe", detected_period, detected_language)
    if analysis.intent == "thanks":
        return ChatIntent("thanks", detected_period, detected_language)
    if analysis.intent == "capabilities":
        return ChatIntent("capabilities", detected_period, detected_language)

    if analysis.intent == "opinion_question":
        return ChatIntent("opinion_question", detected_period, detected_language)
    if any(token in text for token in ("redis", "cache")):
        return ChatIntent("architecture_question", detected_period, detected_language)
    if "eventbus" in text or "event bus" in text:
        return ChatIntent("architecture_question", detected_period, detected_language)
    if "fast forward" in text or "avance rapide" in text or "acceler" in text:
        return ChatIntent("architecture_question", detected_period, detected_language)
    if "live replay" in text or "temps reel" in text or "real time" in text or "replay" in text:
        return ChatIntent("architecture_question", detected_period, detected_language)
    if any(token in text for token in ("confidential", "sensible", "securite", "security")):
        return ChatIntent("confidentiality", detected_period, detected_language)
    if any(token in text for token in ("limite", "limitation", "n/a", "indisponible", "unavailable")):
        return ChatIntent("architecture_question", detected_period, detected_language)
    if analysis.intent in {"comparison_question", "follow_up_question"}:
        return ChatIntent(analysis.intent, detected_period, detected_language)

    if analysis.intent == "affiliation_question":
        return ChatIntent("affiliation_question", detected_period, detected_language)
    if "statut" in text or "status distribution" in text or "repartition" in text:
        return ChatIntent("status_distribution", detected_period, detected_language)
    if "heure" in text or "hour" in text or "incident" in text:
        return ChatIntent("incidents_by_hour", detected_period, detected_language)
    if "anomal" in text or "risque" in text or "risk" in text or "high risk" in text:
        merchant = _extract_merchant_hint(text)
        return ChatIntent("merchant_details" if merchant else "anomaly_question", detected_period, detected_language, merchant)
    if "commerc" in text or "merchant" in text:
        merchant = _extract_merchant_hint(text)
        if merchant and not any(token in text for token in ("top", "plus", "most")):
            return ChatIntent("merchant_details", detected_period, detected_language, merchant)
        return ChatIntent("merchant_question", detected_period, detected_language)
    if analysis.intent in {"kpi_question", "anomaly_question", "merchant_question", "architecture_question"}:
        return ChatIntent(analysis.intent, detected_period, detected_language)
    if any(token in text for token in ("architecture", "pourquoi", "why", "comment", "how", "definition", "calcul")):
        return ChatIntent("architecture_question", detected_period, detected_language)
    return ChatIntent("unknown", detected_period, detected_language)


def _extract_merchant_hint(text: str) -> str | None:
    patterns = [
        r"commercant\s+([\w\s'-]{3,60})",
        r"merchant\s+([\w\s'-]{3,60})",
        r"pour\s+([\w\s'-]{3,60})",
        r"for\s+([\w\s'-]{3,60})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = match.group(1).strip(" ?.!:")
            if value and value not in {"le plus risque", "most risky", "top merchants"}:
                return value
    return None
