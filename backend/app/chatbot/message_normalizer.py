from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True, slots=True)
class NormalizedMessage:
    original: str
    text: str


class MessageNormalizer:
    WORD_REPLACEMENTS = {
        "bnjr": "bonjour",
        "bjr": "bonjour",
        "slt": "salut",
        "cc": "bonjour",
        "cmb": "combien",
        "cb": "combien",
        "combaen": "combien",
        "trsnction": "transaction",
        "trsnctions": "transactions",
        "transction": "transaction",
        "transctions": "transactions",
        "trx": "transactions",
        "auj": "aujourd'hui",
        "ajd": "aujourd'hui",
        "mtn": "maintenant",
        "pk": "pourquoi",
        "pq": "pourquoi",
        "prq": "pourquoi",
        "ff": "fast forward",
        "evtbus": "eventbus",
        "eventbus": "eventbus",
        "replay": "replay",
        "redis": "redis",
        "commercant": "commercant",
        "commercants": "commercants",
        "resume": "resume",
    }

    PHRASE_REPLACEMENTS = (
        ("event bus", "eventbus"),
        ("fast foward", "fast forward"),
        ("fast-forward", "fast forward"),
        ("temps reel", "temps reel"),
        ("aujourd hui", "aujourd'hui"),
        ("aujourdhui", "aujourd'hui"),
        ("avant-hier", "avant hier"),
        ("m aider", "m'aider"),
    )

    @classmethod
    def normalize(cls, message: str) -> NormalizedMessage:
        text = cls.normalize_text(message)
        return NormalizedMessage(original=message, text=text)

    @classmethod
    def normalize_text(cls, value: str) -> str:
        lowered = value.strip().lower().replace("’", "'").replace("`", "'")
        lowered = unicodedata.normalize("NFKD", lowered)
        lowered = "".join(char for char in lowered if not unicodedata.combining(char))
        lowered = re.sub(r"\s+", " ", lowered)
        for source, target in cls.PHRASE_REPLACEMENTS:
            lowered = lowered.replace(source, target)
        words = re.findall(r"[\w']+|[^\w\s]", lowered, flags=re.UNICODE)
        normalized_words = [cls.WORD_REPLACEMENTS.get(word, word) for word in words]
        text = " ".join(normalized_words)
        text = re.sub(r"\s+([?.!,;:])", r"\1", text)
        text = re.sub(r"\s*([/-])\s*", r"\1", text)
        return re.sub(r"\s+", " ", text).strip()


def normalize_for_matching(value: str) -> str:
    return MessageNormalizer.normalize_text(value)
