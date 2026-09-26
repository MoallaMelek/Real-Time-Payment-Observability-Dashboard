from app.chatbot.agent import DataAnalystAgent
from app.chatbot.gemini_orchestrator import GeminiPrincipalOrchestrator
from app.chatbot.gemini_provider import GeminiProvider, LLMProvider
from app.chatbot.knowledge_base import KnowledgeBase
from app.chatbot.llm_provider import OptionalLlmClient
from app.chatbot.memory import ConversationMemory
from app.chatbot.sql_tools import ChatbotSqlTools
from app.config import Settings, get_settings
from app.schemas.chat import ChatRequest, ChatResponse


class ChatbotService:
    def __init__(
        self,
        tools: ChatbotSqlTools,
        knowledge_base: KnowledgeBase | None = None,
        memory: ConversationMemory | None = None,
        settings: Settings | None = None,
        gemini_provider: LLMProvider | None = None,
    ) -> None:
        resolved_settings = settings or get_settings()
        self._settings = resolved_settings
        resolved_memory = memory or ConversationMemory()
        self._agent = DataAnalystAgent(
            tools=tools,
            knowledge_base=knowledge_base or KnowledgeBase(),
            memory=resolved_memory,
            llm=OptionalLlmClient(resolved_settings),
            tool_calls_per_minute=getattr(resolved_settings, "chatbot_tool_calls_per_minute", 120),
            audit_enabled=getattr(resolved_settings, "chatbot_audit_enabled", True),
        )
        self._orchestrator = GeminiPrincipalOrchestrator(
            local_agent=self._agent,
            provider=gemini_provider or GeminiProvider(resolved_settings),
            memory=resolved_memory,
        )

    async def answer(self, request: ChatRequest) -> ChatResponse:
        return await self._orchestrator.answer(request)

    @staticmethod
    def suggestions(language: str = "fr") -> list[str]:
        if language == "en":
            return [
                "Why does the dashboard stay fast with many replayed transactions?",
                "Why is the refusal rate increasing?",
                "Why is Redis used?",
                "How many affiliations remain?",
                "Explain Fast Forward.",
            ]
        return [
            "Pourquoi le dashboard reste rapide avec beaucoup de transactions ?",
            "Pourquoi le taux de refus augmente ?",
            "Pourquoi Redis est utilise ?",
            "Combien reste-t-il d'affiliations ?",
            "Explique le Fast Forward.",
        ]
