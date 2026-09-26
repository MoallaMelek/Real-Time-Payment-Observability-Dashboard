from __future__ import annotations


class AnswerSanitizer:
    CODE_MARKERS = (
        "useState",
        "useEffect",
        "<div",
        "<span",
        "const ",
        "interface ",
        "export ",
        "import ",
        "=>",
        "React.",
        "def ",
        "class ",
        "async def",
        "from app.",
        "SELECT ",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "```",
        ".tsx",
        ".jsx",
    )

    @classmethod
    def sanitize(cls, answer: str, *, response_type: str = "business", code_allowed: bool = False, language: str = "fr") -> str:
        if code_allowed or response_type == "code":
            return answer
        if not cls.contains_code(answer):
            return answer
        clean_lines = [
            line
            for line in answer.splitlines()
            if not cls.contains_code(line)
            and not line.strip().startswith(("{", "}", "return (", ");", "SELECT", "FROM", "WHERE"))
        ]
        cleaned = "\n".join(line for line in clean_lines if line.strip()).strip()
        if cleaned and not cls.contains_code(cleaned):
            return cleaned
        if language == "en":
            return (
                "I will explain it without exposing source code: this point belongs to the dashboard implementation. "
                "The useful takeaway is that the assistant relies on controlled KPI evidence, Redis, EventBus, historical replay and project documentation."
            )
        return (
            "Je vais l'expliquer simplement sans afficher de code source : ce point relève de l'implémentation du dashboard. "
            "L'essentiel est que l'assistant s'appuie sur des KPI contrôlés, Redis, EventBus, le replay historique et la documentation projet."
        )

    @classmethod
    def contains_code(cls, text: str) -> bool:
        return any(marker in text for marker in cls.CODE_MARKERS)
