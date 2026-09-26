from typing import Any, Literal

from pydantic import BaseModel, Field


ChatPeriod = Literal["today", "yesterday", "7d", "30d", "quarter", "year"]
ChatLanguage = Literal["fr", "en"]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=800)
    period: ChatPeriod | None = None
    language: ChatLanguage | None = None
    session_id: str | None = Field(default=None, max_length=80)
    conversation_id: str | None = Field(default=None, max_length=80)


class ChatSource(BaseModel):
    type: Literal["snapshot", "sql", "docs", "security"]
    label: str


class ChatResponse(BaseModel):
    answer: str
    intent: str
    period: ChatPeriod
    session_id: str | None = None
    conversation_id: str | None = None
    sources: list[ChatSource] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    follow_up: list[str] = Field(default_factory=list)
    plan: list[str] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    reasoning_summary: str | None = None
    provider: Literal["local", "fallback", "openai", "gemini", "local_fallback"] = "fallback"
    tools_used: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    detected_period: ChatPeriod | None = None
    confidence: Literal["high", "medium", "low"] = "medium"


class ChatSuggestion(BaseModel):
    label: str
