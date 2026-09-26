from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Tableau de bord de supervision"
    environment: str = "development"
    backend_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    data_source: str = "mock"
    mock_engine_enabled: bool = True
    mock_tx_interval_seconds: float = Field(default=0.8, gt=0)
    mock_tx_max_batch_size: int = Field(default=4, ge=1, le=20)
    sqlserver_host: str = "."
    sqlserver_database: str = "portfolio_demo"
    sqlserver_driver: str = "ODBC Driver 17 for SQL Server"
    sqlserver_trust_certificate: bool = True
    sqlserver_encrypt: bool = True
    sqlserver_auth: str = "windows"
    replay_batch_size: int = Field(default=100, ge=1, le=5000)
    replay_interval_seconds: float = Field(default=0.5, gt=0)
    fast_forward_batch_size: int = Field(default=250, ge=1, le=5000)
    fast_forward_fetch_batch_size: int = Field(default=5000, ge=1, le=5000)
    fast_forward_interval_seconds: float = Field(default=0.1, ge=0.1)
    fraud_timeout_threshold_ms: float = Field(default=5000, gt=0)
    amount_scale: float = Field(default=1000, gt=0)
    amount_unit: str = "TND"
    redis_url: str | None = "redis://127.0.0.1:6379/0"
    database_url: str = "sqlite:///./data/payments.db"
    websocket_auth_token: str | None = None
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = Field(default=180, ge=1)
    chatbot_llm_provider: str = "local"
    chatbot_llm_enabled: bool = True
    local_llm_base_url: str = "http://localhost:11434"
    local_llm_model: str = "qwen2.5:7b-instruct"
    local_llm_timeout_seconds: int = Field(default=20, ge=1, le=120)
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    gemini_enabled: bool = False
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_timeout_seconds: float = Field(default=6, ge=1, le=60)
    gemini_max_retries: int = Field(default=1, ge=0, le=5)
    gemini_temperature: float = Field(default=0.2, ge=0, le=1)
    gemini_max_output_tokens: int = Field(default=1024, ge=128, le=8192)
    gemini_rollout_percent: int = Field(default=100, ge=0, le=100)
    gemini_fallback_enabled: bool = False
    gemini_fail_closed: bool = False
    chatbot_response_cache_ttl_seconds: int = Field(default=60, ge=0, le=3600)
    chatbot_openai_requests_per_minute: int = Field(default=20, ge=1, le=120)
    chatbot_tool_calls_per_minute: int = Field(default=120, ge=1, le=1000)
    chatbot_audit_enabled: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]

    @property
    def uses_sqlserver_replay(self) -> bool:
        return self.data_source.strip().lower() == "sqlserver_replay"


@lru_cache
def get_settings() -> Settings:
    return Settings()
