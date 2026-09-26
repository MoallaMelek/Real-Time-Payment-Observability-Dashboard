from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import re

from app.chatbot.message_normalizer import MessageNormalizer
from app.schemas.chat import ChatPeriod


@dataclass(frozen=True, slots=True)
class TemporalRange:
    start_date: str
    end_date: str
    granularity: str
    source: str
    period_key: ChatPeriod | None = None
    label_fr: str = ""
    label_en: str = ""

    def __post_init__(self) -> None:
        if self.label_fr and self.label_en:
            return
        start = date.fromisoformat(self.start_date)
        end = date.fromisoformat(self.end_date)
        if start == end:
            label_fr = start.strftime("%d/%m/%Y")
            label_en = start.strftime("%Y-%m-%d")
        else:
            label_fr = f"{start.strftime('%d/%m/%Y')} au {end.strftime('%d/%m/%Y')}"
            label_en = f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"
        object.__setattr__(self, "label_fr", self.label_fr or label_fr)
        object.__setattr__(self, "label_en", self.label_en or label_en)

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


class TemporalResolver:
    MONTHS = {
        "janvier": 1,
        "fevrier": 2,
        "février": 2,
        "mars": 3,
        "avril": 4,
        "mai": 5,
        "juin": 6,
        "juillet": 7,
        "aout": 8,
        "août": 8,
        "septembre": 9,
        "octobre": 10,
        "novembre": 11,
        "decembre": 12,
        "décembre": 12,
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    NUMBER_WORDS = {
        "un": 1,
        "une": 1,
        "deux": 2,
        "trois": 3,
        "quatre": 4,
        "cinq": 5,
        "six": 6,
        "sept": 7,
        "huit": 8,
        "neuf": 9,
        "dix": 10,
        "premier": 1,
        "1er": 1,
        "deuxieme": 2,
        "deuxième": 2,
        "second": 2,
        "troisieme": 3,
        "troisième": 3,
        "quatrieme": 4,
        "quatrième": 4,
    }

    @classmethod
    def resolve(
        cls,
        message: str,
        *,
        reference_date: date | None = None,
        memory: TemporalRange | None = None,
    ) -> TemporalRange | None:
        reference = reference_date or date.today()
        text = MessageNormalizer.normalize_text(message)

        explicit = cls._explicit_range(text, reference) or cls._explicit_single_date(text, reference) or cls._explicit_month(text, reference) or cls._explicit_quarter(text, reference)
        if explicit:
            return explicit

        relative = cls._relative(text, reference)
        if relative:
            return relative

        if cls._looks_like_memory_followup(text) and memory:
            return TemporalRange(memory.start_date, memory.end_date, memory.granularity, "memory", memory.period_key)
        return None

    @classmethod
    def resolve_many(
        cls,
        message: str,
        *,
        reference_date: date | None = None,
    ) -> list[TemporalRange]:
        reference = reference_date or date.today()
        text = MessageNormalizer.normalize_text(message)
        matches: list[tuple[int, TemporalRange]] = []
        patterns: tuple[tuple[str, date, ChatPeriod | None, str], ...] = (
            (r"\b(aujourd'hui|today)\b", reference, "today", "aujourd'hui"),
            (r"\b(hier|yesterday)\b", reference - timedelta(days=1), "yesterday", "hier"),
            (r"\b(avant-hier|avant hier)\b", reference - timedelta(days=2), None, "avant-hier"),
            (r"\b(demain|tomorrow)\b", reference + timedelta(days=1), None, "demain"),
        )
        for pattern, day, period_key, label in patterns:
            for match in re.finditer(pattern, text):
                resolved = cls._one_day(day, "relative", period_key)
                object.__setattr__(resolved, "label_fr", label)
                object.__setattr__(resolved, "label_en", "today" if period_key == "today" else "yesterday" if period_key == "yesterday" else resolved.label_en)
                matches.append((match.start(), resolved))
        matches.sort(key=lambda item: item[0])
        unique: list[TemporalRange] = []
        seen: set[tuple[str, str, str | None]] = set()
        for _, temporal in matches:
            key = (temporal.start_date, temporal.end_date, temporal.period_key)
            if key in seen:
                continue
            seen.add(key)
            unique.append(temporal)
        return unique if len(unique) > 1 else []

    @classmethod
    def period_key(cls, message: str, fallback: ChatPeriod | None = None, reference_date: date | None = None) -> ChatPeriod:
        resolved = cls.resolve(message, reference_date=reference_date)
        return resolved.period_key if resolved and resolved.period_key else fallback or "today"

    @classmethod
    def explicit_comparison_dates(cls, message: str, reference_date: date | None = None) -> tuple[TemporalRange, TemporalRange] | None:
        reference = reference_date or date.today()
        text = MessageNormalizer.normalize_text(message)
        if not re.search(r"\b(compare|comparaison|comparer|vs|versus|avec|par rapport)\b", text):
            return None
        separators = (" avec ", " vs ", " versus ", " par rapport a ", " par rapport ")
        for separator in separators:
            if separator not in text:
                continue
            left_fragment, right_fragment = text.split(separator, 1)
            left = cls._parse_date_fragment(left_fragment, reference)
            right = cls._parse_date_fragment(right_fragment, reference, default_month=left.month if left else None)
            if left and right:
                return cls._one_day(left, "explicit"), cls._one_day(right, "explicit")
        month_names = "|".join(cls.MONTHS)
        pattern = rf"\b(\d{{1,2}})\s+({month_names})(?:\s+\d{{4}})?\b"
        matches = list(re.finditer(pattern, text))
        if len(matches) >= 2:
            left = cls._parse_date_fragment(matches[0].group(0), reference)
            right = cls._parse_date_fragment(matches[1].group(0), reference, default_month=left.month if left else None)
            if left and right:
                return cls._one_day(left, "explicit"), cls._one_day(right, "explicit")
        month_ranges = cls._explicit_comparison_months(text, reference)
        if month_ranges:
            return month_ranges
        return None

    @classmethod
    def _relative(cls, text: str, reference: date) -> TemporalRange | None:
        if re.search(r"\b(aujourd'hui|today)\b", text):
            return cls._one_day(reference, "relative", "today")
        if "avant-hier" in text or "avant hier" in text:
            return cls._one_day(reference - timedelta(days=2), "relative")
        if re.search(r"\b(hier|yesterday)\b", text):
            return cls._one_day(reference - timedelta(days=1), "relative", "yesterday")
        if re.search(r"\b(demain|tomorrow)\b", text):
            return cls._one_day(reference + timedelta(days=1), "relative")

        match = re.search(r"\b(?:les\s*)?(\d+|trois|sept|trente)\s+derniers?\s+jours?\b", text)
        if match:
            days = cls._number(match.group(1))
            if days:
                start = reference - timedelta(days=days - 1)
                return TemporalRange(start.isoformat(), reference.isoformat(), "range", "relative", "7d" if days == 7 else "30d" if days == 30 else None)
        match = re.search(r"\b(7|30)\s*(jours|days)\b", text)
        if match:
            days = int(match.group(1))
            start = reference - timedelta(days=days - 1)
            return TemporalRange(start.isoformat(), reference.isoformat(), "range", "relative", "7d" if days == 7 else "30d")

        if "cette semaine" in text or "this week" in text:
            start = reference - timedelta(days=reference.weekday())
            return TemporalRange(start.isoformat(), reference.isoformat(), "week", "relative", "7d")
        if "semaine derniere" in text or "last week" in text:
            end = reference - timedelta(days=reference.weekday() + 1)
            start = end - timedelta(days=6)
            return TemporalRange(start.isoformat(), end.isoformat(), "week", "relative")
        if "ce mois" in text or "this month" in text:
            start = reference.replace(day=1)
            return TemporalRange(start.isoformat(), reference.isoformat(), "month", "relative")
        if "mois dernier" in text or "last month" in text:
            first_this_month = reference.replace(day=1)
            end = first_this_month - timedelta(days=1)
            start = end.replace(day=1)
            return TemporalRange(start.isoformat(), end.isoformat(), "month", "relative")
        if "cette annee" in text or "this year" in text:
            start = reference.replace(month=1, day=1)
            return TemporalRange(start.isoformat(), reference.isoformat(), "year", "relative", "year")
        if "annee derniere" in text or "last year" in text:
            year = reference.year - 1
            return TemporalRange(date(year, 1, 1).isoformat(), date(year, 12, 31).isoformat(), "year", "relative")
        if "annee complete" in text or "full year" in text:
            year_match = re.search(r"\b(20\d{2}|19\d{2})\b", text)
            year = int(year_match.group(1)) if year_match else reference.year
            return TemporalRange(date(year, 1, 1).isoformat(), date(year, 12, 31).isoformat(), "year", "explicit" if year_match else "relative", "year" if year == reference.year else None)
        if text.strip(" ?!.") in {"trimestre", "ce trimestre", "quarter"}:
            quarter = (reference.month - 1) // 3 + 1
            return cls._quarter(reference.year, quarter, "relative", "quarter")
        return None

    @classmethod
    def _explicit_range(cls, text: str, reference: date) -> TemporalRange | None:
        month_names = "|".join(cls.MONTHS)
        match = re.search(
            rf"\bentre\s+(?:le\s+)?(\d{{1,2}})\s+({month_names})(?:\s+(\d{{4}}))?\s+(?:et|jusqu[' ]?a)\s+(?:le\s+)?(\d{{1,2}})(?:\s+({month_names}))?(?:\s+(\d{{4}}))?\b",
            text,
        )
        if match:
            start_month = cls.MONTHS[match.group(2)]
            end_month = cls.MONTHS[match.group(5)] if match.group(5) else start_month
            start_year = int(match.group(3) or match.group(6) or reference.year)
            end_year = int(match.group(6) or start_year)
            return cls._range(date(start_year, start_month, int(match.group(1))), date(end_year, end_month, int(match.group(4))), "range", "explicit")

        match = re.search(
            rf"\b(?:le|les|des)?\s*(\d{{1,2}})\s*(?:,|\s+et\s+|\s+and\s+|&|\+)\s*(?:le\s+)?(\d{{1,2}})\s+({month_names})(?:\s+(\d{{4}}))?\b",
            text,
        )
        if match:
            month = cls.MONTHS[match.group(3)]
            year = int(match.group(4) or reference.year)
            start = date(year, month, int(match.group(1)))
            end = date(year, month, int(match.group(2)))
            return cls._range(start, end, "range", "explicit")

        match = re.search(rf"\b(?:entre|between|de)\s+(.+?)\s+(?:et|and|jusqu[' ]?a|a)\s+(.+?)\b", text)
        if match:
            left = cls._parse_date_fragment(match.group(1), reference)
            right = cls._parse_date_fragment(match.group(2), reference, default_month=left.month if left else None)
            if left and right:
                return cls._range(left, right, "range", "explicit")

        match = re.search(rf"\bdu\s+(\d{{1,2}})\s+(?:au|a|jusqu[' ]?a)\s+(\d{{1,2}})\s+({month_names})(?:\s+(\d{{4}}))?\b", text)
        if match:
            month = cls.MONTHS[match.group(3)]
            year = int(match.group(4) or reference.year)
            return cls._range(date(year, month, int(match.group(1))), date(year, month, int(match.group(2))), "range", "explicit")

        match = re.search(rf"\bdu\s+(\d{{1,2}})\s+({month_names})(?:\s+(\d{{4}}))?\s+(?:au|a|jusqu[' ]?a)\s+(\d{{1,2}})(?:\s+({month_names}))?(?:\s+(\d{{4}}))?\b", text)
        if match:
            start_month = cls.MONTHS[match.group(2)]
            end_month = cls.MONTHS[match.group(5)] if match.group(5) else start_month
            start_year = int(match.group(3) or match.group(6) or reference.year)
            end_year = int(match.group(6) or start_year)
            return cls._range(date(start_year, start_month, int(match.group(1))), date(end_year, end_month, int(match.group(4))), "range", "explicit")

        match = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\s*(?:au|a|à|-|et)\s*(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", text)
        if match:
            start = cls._date_from_numeric(match.group(1), match.group(2), match.group(3), reference.year)
            end = cls._date_from_numeric(match.group(4), match.group(5), match.group(6), start.year)
            return cls._range(start, end, "range", "explicit")
        return None

    @classmethod
    def _explicit_single_date(cls, text: str, reference: date) -> TemporalRange | None:
        match = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", text)
        if match:
            day = cls._date_from_numeric(match.group(1), match.group(2), match.group(3), reference.year)
            return cls._one_day(day, "explicit")

        parsed = cls._parse_date_fragment(text, reference)
        if parsed:
            return cls._one_day(parsed, "explicit")
        return None

    @classmethod
    def _explicit_month(cls, text: str, reference: date) -> TemporalRange | None:
        for month_name, month in cls.MONTHS.items():
            if re.search(rf"\b{re.escape(month_name)}\b", text):
                year_match = re.search(r"\b(20\d{2}|19\d{2})\b", text)
                year = int(year_match.group(1)) if year_match else reference.year
                start = date(year, month, 1)
                end = cls._month_end(year, month)
                return TemporalRange(start.isoformat(), end.isoformat(), "month", "explicit")
        return None

    @classmethod
    def _explicit_comparison_months(cls, text: str, reference: date) -> tuple[TemporalRange, TemporalRange] | None:
        month_names = {MessageNormalizer.normalize_text(name): value for name, value in cls.MONTHS.items()}
        pattern = r"\b(" + "|".join(re.escape(name) for name in sorted(month_names, key=len, reverse=True)) + r")\b"
        found: list[tuple[str, int]] = []
        for match in re.finditer(pattern, text):
            name = match.group(1)
            month = month_names.get(name)
            if month and month not in [item[1] for item in found]:
                found.append((name, month))
        if len(found) < 2:
            return None
        year_match = re.search(r"\b(20\d{2}|19\d{2})\b", text)
        year = int(year_match.group(1)) if year_match else reference.year

        def month_range(label: str, month: int) -> TemporalRange:
            start = date(year, month, 1)
            end = cls._month_end(year, month)
            return TemporalRange(start.isoformat(), end.isoformat(), "month", "explicit", None, label_fr=label, label_en=label)

        left_name, left_month = found[0]
        right_name, right_month = found[1]
        return month_range(left_name, left_month), month_range(right_name, right_month)

    @classmethod
    def _explicit_quarter(cls, text: str, reference: date) -> TemporalRange | None:
        if "trimestre" not in text and "quarter" not in text:
            return None
        quarter = None
        for word, value in cls.NUMBER_WORDS.items():
            if re.search(rf"\b{re.escape(word)}\b", text):
                quarter = value
                break
        number_match = re.search(r"\b([1-4])(?:er|e)?\s+trimestre\b", text)
        if number_match:
            quarter = int(number_match.group(1))
        if not quarter:
            return None
        year_match = re.search(r"\b(20\d{2}|19\d{2})\b", text)
        return cls._quarter(int(year_match.group(1)) if year_match else reference.year, quarter, "explicit")

    @classmethod
    def _parse_date_fragment(cls, fragment: str, reference: date, default_month: int | None = None) -> date | None:
        month_names = "|".join(cls.MONTHS)
        match = re.search(rf"\b(?:le\s+)?(\d{{1,2}})\s+({month_names})(?:\s+(\d{{4}}))?\b", fragment)
        if match:
            return date(int(match.group(3) or reference.year), cls.MONTHS[match.group(2)], int(match.group(1)))
        match = re.search(r"\b(?:le\s+)?(\d{1,2})(?:\s+(\d{4}))?\b", fragment)
        if match and default_month:
            return date(int(match.group(2) or reference.year), default_month, int(match.group(1)))
        return None

    @staticmethod
    def _date_from_numeric(day: str, month: str, year: str | None, default_year: int) -> date:
        parsed_year = default_year if not year else int(year) if len(year) == 4 else 2000 + int(year)
        return date(parsed_year, int(month), int(day))

    @staticmethod
    def _one_day(day: date, source: str, period_key: ChatPeriod | None = None) -> TemporalRange:
        return TemporalRange(day.isoformat(), day.isoformat(), "day", source, period_key)

    @staticmethod
    def _range(start: date, end: date, granularity: str, source: str) -> TemporalRange:
        if end < start:
            start, end = end, start
        return TemporalRange(start.isoformat(), end.isoformat(), granularity, source)

    @classmethod
    def _quarter(cls, year: int, quarter: int, source: str, period_key: ChatPeriod | None = None) -> TemporalRange:
        safe_quarter = max(1, min(quarter, 4))
        start_month = (safe_quarter - 1) * 3 + 1
        start = date(year, start_month, 1)
        end_month = start_month + 2
        return TemporalRange(start.isoformat(), cls._month_end(year, end_month).isoformat(), "quarter", source, period_key)

    @staticmethod
    def _month_end(year: int, month: int) -> date:
        if month == 12:
            return date(year, 12, 31)
        return date(year, month + 1, 1) - timedelta(days=1)

    @classmethod
    def _number(cls, value: str) -> int | None:
        return int(value) if value.isdigit() else cls.NUMBER_WORDS.get(value)

    @staticmethod
    def _looks_like_memory_followup(text: str) -> bool:
        return text.strip(" ?!.") in {"et la meme periode", "meme periode", "compare", "pourquoi"}
