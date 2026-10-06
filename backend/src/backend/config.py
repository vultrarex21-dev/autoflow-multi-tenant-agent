"""Agent service settings."""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")

    # OpenRouter / LLM Config
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENAI_MODEL: str = "anthropic/claude-3.5-sonnet"

    # Database Config
    DATABASE_URL: str = "sqlite:///autoflow.db"

    # n8n Webhook Urls
    N8N_GMAIL_WEBHOOK_URL: str = ""
    N8N_LINKEDIN_WEBHOOK_URL: str = ""

@lru_cache
def get_settings() -> AgentSettings:
    return AgentSettings()

settings: AgentSettings = get_settings()
