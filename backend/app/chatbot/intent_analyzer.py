from __future__ import annotations

from dataclasses import dataclass
import re

from app.chatbot.message_normalizer import MessageNormalizer


@dataclass(frozen=True, slots=True)
class IntentAnalysis:
    intent: str
    normalized_text: str
    score: float


class IntentAnalyzer:
    INTENTS: dict[str, tuple[str, ...]] = {
        "greeting": ("bonjour", "salut", "hello", "hi", "bonsoir", "hey", "coucou", "yo", "bjr", "bnjr", "slt"),
        "small_talk": ("comment vas tu", "comment vas-tu", "ca va", "tu vas bien", "how are you"),
        "help": ("aide", "aide moi", "tu peux m'aider", "peux tu m'aider", "help", "assistance"),
        "thanks": ("merci", "thanks", "super", "parfait", "ok merci", "cool", "top"),
        "capabilities": ("que peux tu faire", "tu fais quoi", "capacites", "capabilities", "comment tu peux aider"),
        "code_question": ("montre le code", "montre-moi le code", "show code", "code du composant", "source code"),
        "opinion_question": ("avis", "opinion", "pense quoi", "penses quoi", "credible", "soutenance", "ameliorer l'ui"),
        "comparison_question": ("compare", "comparaison", "vs", "versus", "par rapport"),
        "follow_up_question": ("et hier", "pourquoi", "et les anomalies", "et les commercants", "compare avec hier"),
        "kpi_question": ("kpi", "combien", "transaction", "transactions", "taux", "resume", "summary", "refus", "succes"),
        "anomaly_question": ("anomalie", "anomalies", "anomaly", "risk", "risque", "incident"),
        "merchant_question": ("commercant", "commercants", "merchant", "merchants"),
        "affiliation_question": ("affiliation", "affiliations", "stock"),
        "architecture_question": ("redis", "eventbus", "event bus", "replay", "architecture", "fast forward", "temps reel", "limites", "limite"),
    }

    EXACT_PHRASE_INTENTS = {
        "greeting": ("bonjour", "salut", "hello", "hi", "bonsoir", "hey", "coucou", "yo"),
        "small_talk": ("comment vas tu", "comment vas-tu", "ca va", "tu vas bien", "how are you"),
        "help": ("aide moi", "tu peux m'aider", "peux tu m'aider", "help", "assistance"),
        "thanks": ("merci", "merci beaucoup", "thanks", "ok merci"),
        "follow_up_question": ("et hier", "pourquoi", "et les anomalies", "et les commercants", "compare avec hier"),
    }

    @classmethod
    def analyze(cls, message: str) -> IntentAnalysis:
        normalized = MessageNormalizer.normalize_text(message)
        clean = normalized.strip(" ?!.")
        for intent, phrases in cls.EXACT_PHRASE_INTENTS.items():
            if clean in phrases:
                return IntentAnalysis(intent, normalized, 1.0)

        words = [word for word in re.findall(r"\b[\w']+\b", normalized) if len(word) >= 3]
        detected: list[tuple[str, float]] = []
        for intent, keywords in cls.INTENTS.items():
            score = 0.0
            for keyword in keywords:
                normalized_keyword = MessageNormalizer.normalize_text(keyword)
                if normalized_keyword in normalized:
                    score += 2.0
            for word in words:
                _, similarity = cls.fuzzy_match(word, keywords)
                if similarity >= 0.68:
                    score += similarity
            if score > 0:
                detected.append((intent, score))

        if not detected:
            return IntentAnalysis("out_of_scope", normalized, 0.0)
        detected.sort(key=lambda item: item[1], reverse=True)
        intent, score = detected[0]
        return IntentAnalysis(intent, normalized, min(score, 1.0))

    @classmethod
    def fuzzy_match(cls, word: str, candidates: tuple[str, ...], threshold: float = 0.68) -> tuple[str | None, float]:
        best_match: str | None = None
        best_score = 0.0
        normalized_word = MessageNormalizer.normalize_text(word)
        for candidate in candidates:
            normalized_candidate = MessageNormalizer.normalize_text(candidate)
            if normalized_word == normalized_candidate:
                return candidate, 1.0
            if normalized_word in normalized_candidate or normalized_candidate in normalized_word:
                score = min(1.0, max(len(normalized_word), len(normalized_candidate)) / (len(normalized_word) + len(normalized_candidate)) * 1.5)
            else:
                max_len = max(len(normalized_word), len(normalized_candidate))
                score = 1 - (cls.levenshtein_distance(normalized_word, normalized_candidate) / max_len) if max_len else 0.0
            if score >= threshold and score > best_score:
                best_match = candidate
                best_score = score
        return best_match, best_score

    @staticmethod
    def levenshtein_distance(left: str, right: str) -> int:
        if len(left) < len(right):
            return IntentAnalyzer.levenshtein_distance(right, left)
        if not right:
            return len(left)
        previous_row = range(len(right) + 1)
        for i, left_char in enumerate(left):
            current_row = [i + 1]
            for j, right_char in enumerate(right):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (left_char != right_char)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
        return previous_row[-1]
