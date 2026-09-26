from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from app.chatbot.message_normalizer import MessageNormalizer


@dataclass(frozen=True, slots=True)
class CodeSearchResult:
    path: str
    excerpt: str
    score: int


class ProjectCodeSearchTool:
    CODE_ROOTS = ("backend/app", "frontend/src", "scripts", "backend/tests")
    MAX_EXCERPT_LINES = 28

    @classmethod
    def search(cls, query: str, root: Path | None = None, limit: int = 3) -> list[dict[str, str | int]]:
        project_root = root or Path(__file__).resolve().parents[3]
        normalized = MessageNormalizer.normalize_text(query)
        terms = cls._terms_for_query(normalized)
        results: list[CodeSearchResult] = []
        for folder in cls.CODE_ROOTS:
            base = project_root / folder
            if not base.exists():
                continue
            for path in base.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in {".py", ".ts", ".tsx", ".js", ".jsx", ".ps1"}:
                    continue
                if any(part in {".venv", "__pycache__", "node_modules", "dist"} for part in path.parts):
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                score = cls._score(path, text, terms)
                if score:
                    results.append(CodeSearchResult(path.relative_to(project_root).as_posix(), cls._excerpt(text, terms), score))
        results.sort(key=lambda item: item.score, reverse=True)
        return [{"path": item.path, "excerpt": item.excerpt, "score": item.score} for item in results[:limit]]

    @classmethod
    def _terms_for_query(cls, normalized: str) -> set[str]:
        terms = {term for term in re.split(r"\W+", normalized) if len(term) >= 3}
        if "composant chat" in normalized or "component chat" in normalized:
            terms.update({"chatassistant", "chatassistant.tsx", "sendchatmessage"})
        if "route" in normalized and "chat" in normalized:
            terms.update({"api/chat", "chat(", "routes.py"})
        if "redis" in normalized:
            terms.update({"redis", "rediskpicache", "redis_cache"})
        if "eventbus" in normalized or "event bus" in normalized:
            terms.update({"eventbus", "inmemoryeventbus", "event_bus"})
        if "llm" in normalized or "provider" in normalized:
            terms.update({"llm_provider", "optionalllmclient", "chatbot_llm_provider"})
        if "test" in normalized and "chat" in normalized:
            terms.update({"test_chatbot", "chatbottests"})
        return terms

    @staticmethod
    def _score(path: Path, text: str, terms: set[str]) -> int:
        haystack_path = MessageNormalizer.normalize_text(path.name)
        haystack_text = MessageNormalizer.normalize_text(text[:20000])
        score = sum(8 for term in terms if term in haystack_path)
        score += sum(1 for term in terms if term in haystack_text)
        if path.name in {"ChatAssistant.tsx", "routes.py", "llm_provider.py", "event_bus.py", "redis_cache.py", "test_chatbot.py"}:
            score += 8
        return score

    @classmethod
    def _excerpt(cls, text: str, terms: set[str]) -> str:
        lines = text.splitlines()
        start = 0
        normalized_lines = [MessageNormalizer.normalize_text(line) for line in lines]
        for index, line in enumerate(normalized_lines):
            if any(term in line for term in terms):
                start = max(0, index - 3)
                break
        selected = lines[start : start + cls.MAX_EXCERPT_LINES]
        return "\n".join(selected).strip()
