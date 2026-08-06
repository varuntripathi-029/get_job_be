"""Application settings loaded from environment / .env."""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Providers that can back the classifier or extractor role. groq, xai and
# openai share the OpenAI chat-completions protocol; gemini and anthropic
# use their own SDKs.
PROVIDERS = ("gemini", "openai", "anthropic", "groq", "xai")
OPENAI_PROTOCOL_PROVIDERS = ("openai", "groq", "xai")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "HireSignal"
    environment: str = "development"
    debug: bool = True
    # NoDecode stops pydantic-settings from JSON-parsing this, so the plain
    # comma-separated form in .env reaches the validator below.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # Database
    database_url: str = "postgresql+asyncpg://hiresignal:hiresignal@localhost:5432/hiresignal"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Auth — Google OAuth 2.0
    google_client_id: str = ""
    google_client_secret: str = ""
    jwt_secret_key: str = "change-me-to-a-random-string"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # LLM — provider is chosen per role so the cheap classifier and the
    # expensive extractor can sit on different vendors.
    gemini_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    # Groq and xAI both speak the OpenAI chat-completions protocol, so all
    # three share one client and differ only by base URL.
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    xai_api_key: str = ""
    xai_base_url: str = "https://api.x.ai/v1"

    classifier_provider: str = "groq"
    classifier_model: str = "llama-3.1-8b-instant"
    extractor_provider: str = "groq"
    extractor_model: str = "openai/gpt-oss-120b"
    llm_max_input_chars: int = 15000

    @field_validator("classifier_provider", "extractor_provider")
    @classmethod
    def _known_provider(cls, v: str) -> str:
        if v not in PROVIDERS:
            raise ValueError(f"provider must be one of {sorted(PROVIDERS)}, got {v!r}")
        return v

    def api_key_for(self, provider: str) -> str:
        return {
            "gemini": self.gemini_api_key,
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "groq": self.groq_api_key,
            "xai": self.xai_api_key,
        }[provider]

    def base_url_for(self, provider: str) -> str | None:
        """Base URL for OpenAI-protocol providers; None means the SDK default."""
        return {
            "groq": self.groq_base_url,
            "xai": self.xai_base_url,
        }.get(provider)

    @property
    def missing_llm_keys(self) -> list[str]:
        """Providers referenced by a role but with no key configured."""
        return sorted(
            {
                provider
                for provider in (self.classifier_provider, self.extractor_provider)
                if not self.api_key_for(provider)
            }
        )

    # News APIs (all optional — features degrade gracefully when unset)
    newsapi_key: str = ""
    gnews_api_key: str = ""
    serpapi_key: str = ""
    github_token: str = ""

    # Email
    resend_api_key: str = ""
    from_email: str = "noreply@yourdomain.com"

    # Crawler
    crawler_user_agent: str = "HireSignal/1.0 (+https://yourdomain.com/bot)"
    crawler_timeout_seconds: int = 30
    playwright_max_concurrent: int = 2
    playwright_timeout_seconds: int = 60
    rate_limit_seconds_per_domain: float = 5.0
    crawl_log_retention_days: int = 10
    scheduler_batch_size: int = 10

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, v: object) -> object:
        """Allow CORS_ORIGINS to be a comma-separated string in .env."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
