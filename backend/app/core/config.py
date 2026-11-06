"""Central configuration. Real integrations are NEVER inferred from credential presence."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Mode = Literal["mock", "real"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_ENV: Literal["development", "test", "staging", "production"] = "development"
    INTEGRATION_MODE: Mode = "mock"
    TEST_MODE: bool = True
    LOG_LEVEL: str = "INFO"

    # Safety
    GOOGLE_ADS_WRITES_ENABLED: bool = False
    AUTO_NEGATIVES_MODE: Literal["shadow", "active"] = "shadow"
    AUTO_NEGATIVES_DAILY_LIMIT: int = 25
    EMERGENCY_BRAKE_WRITE_MODE: Literal["simulate", "disabled", "real"] = "simulate"

    # Per-integration modes (blank -> INTEGRATION_MODE)
    GOOGLE_ADS_MODE: Mode | None = None
    KEYWORD_RESEARCH_MODE: Mode | None = None
    PLACES_MODE: Mode | None = None
    PAGESPEED_MODE: Mode | None = None
    CRAWLER_MODE: Literal["fixture", "live"] = "fixture"
    NOTIFIER_MODE: Mode | None = None
    AUTH_PROVIDER: Literal["mock", "google"] = "mock"
    AI_PROVIDER: Literal["mock", "groq"] = "mock"

    # Infrastructure
    DATABASE_URL: str = "postgresql+psycopg://app:app@localhost:5432/google_ads_agent"
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_TASK_ALWAYS_EAGER: bool = False
    APP_SECRET_KEY: str = "dev-insecure-secret-change-me"
    TOKEN_ENCRYPTION_KEY: str = ""
    CORS_ORIGINS: str = "http://localhost:5173"
    SESSION_COOKIE_SECURE: bool = False
    SESSION_TTL_HOURS: int = 12

    # Login
    BOOTSTRAP_ADMIN_EMAIL: str = ""
    ALLOWED_LOGIN_DOMAINS: str = ""
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = ""

    # Google Ads
    GOOGLE_ADS_DEVELOPER_TOKEN: str = ""
    GOOGLE_ADS_CLIENT_ID: str = ""
    GOOGLE_ADS_CLIENT_SECRET: str = ""
    GOOGLE_ADS_REDIRECT_URI: str = ""
    GOOGLE_ADS_REFRESH_TOKEN: str = ""
    GOOGLE_ADS_LOGIN_CUSTOMER_ID: str = ""
    GOOGLE_ADS_CUSTOMER_ID: str = ""

    GOOGLE_PLACES_API_KEY: str = ""
    PAGESPEED_API_KEY: str = ""

    # AI
    GROQ_ENABLED: bool = False
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = ""
    GROQ_CHALLENGER_MODEL: str = ""

    # Mock / crawler
    MOCK_SEED: int = 42
    CRAWLER_MAX_PAGES: int = 40
    CRAWLER_MAX_DEPTH: int = 2
    CRAWLER_TIMEOUT_SECONDS: float = 10.0

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        if self.APP_ENV == "production":
            problems = []
            if self.AUTH_PROVIDER == "mock":
                problems.append("AUTH_PROVIDER=mock is not allowed in production")
            if self.TEST_MODE:
                problems.append("TEST_MODE=true is not allowed in production")
            if self.APP_SECRET_KEY.startswith("dev-") or len(self.APP_SECRET_KEY) < 32:
                problems.append("APP_SECRET_KEY must be a strong secret in production")
            if problems:
                raise ValueError("; ".join(problems))
        if self.AI_PROVIDER == "groq" and not self.GROQ_ENABLED:
            raise ValueError("AI_PROVIDER=groq requires GROQ_ENABLED=true")
        return self

    def mode_for(self, name: str) -> Mode:
        value = getattr(self, f"{name}_MODE", None)
        return value or self.INTEGRATION_MODE

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def allowed_login_domains(self) -> list[str]:
        return [d.strip().lower() for d in self.ALLOWED_LOGIN_DOMAINS.split(",") if d.strip()]

    def public_summary(self) -> dict:
        """Safe, secret-free view of the configuration."""
        return {
            "app_env": self.APP_ENV,
            "integration_mode": self.INTEGRATION_MODE,
            "test_mode": self.TEST_MODE,
            "google_ads_writes_enabled": self.GOOGLE_ADS_WRITES_ENABLED,
            "auto_negatives_mode": self.AUTO_NEGATIVES_MODE,
            "emergency_brake_write_mode": self.EMERGENCY_BRAKE_WRITE_MODE,
            "auth_provider": self.AUTH_PROVIDER,
            "ai_provider": self.AI_PROVIDER,
            "crawler_mode": self.CRAWLER_MODE,
            "modes": {n: self.mode_for(n) for n in ("GOOGLE_ADS", "KEYWORD_RESEARCH", "PLACES", "PAGESPEED", "NOTIFIER")},
            "groq_model_configured": bool(self.GROQ_MODEL),
            "groq_key_configured": bool(self.GROQ_API_KEY),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings_override: dict = {}


def reset_settings_cache() -> None:
    get_settings.cache_clear()

