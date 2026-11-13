"""Central configuration. Real integrations are NEVER inferred from credential presence."""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[3]


def env_files() -> tuple[str, ...]:
    """Where .env is looked up. Later files override earlier ones; real environment variables override all files.

    1. <repo root>/.env   (what the docs, create-env.sh and Codespaces create — works wherever the backend is started from)
    2. ./.env             (the current directory, e.g. backend/.env)
    GAA_ENV_FILE=<path> uses only that file; GAA_ENV_FILE=none loads no file (the test suite does this).
    """
    override = os.environ.get("GAA_ENV_FILE")
    if override is not None:
        return () if override.strip().lower() in ("", "none") else (override,)
    return (str(REPO_ROOT / ".env"), ".env")


_ID_FIELDS = ("GOOGLE_ADS_LOGIN_CUSTOMER_ID",)


class Settings(BaseSettings):
    # blank values (e.g. "GOOGLE_ADS_MODE=" in .env.example) mean "use the default"
    model_config = SettingsConfigDict(env_file=env_files(), env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True)

    APP_ENV: Literal["development", "test", "staging", "production"] = "development"
    LOG_LEVEL: str = "INFO"

    # Safety
    GOOGLE_ADS_WRITES_ENABLED: bool = False
    AUTO_NEGATIVES_MODE: Literal["shadow", "active"] = "shadow"
    AUTO_NEGATIVES_DAILY_LIMIT: int = 25
    EMERGENCY_BRAKE_WRITE_MODE: Literal["simulate", "disabled", "real"] = "simulate"

    # "dev" = email-only local login for users that already exist (refused in production); "google" = Google sign-in.
    AUTH_PROVIDER: Literal["dev", "google"] = "dev"

    # Infrastructure
    DATABASE_URL: str = "postgresql+psycopg://app:app@localhost:5432/google_ads_agent"
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_TASK_ALWAYS_EAGER: bool = False
    APP_SECRET_KEY: str = "dev-insecure-secret-change-me"
    TOKEN_ENCRYPTION_KEY: str = ""
    CORS_ORIGINS: str = "http://localhost:5173"
    SESSION_COOKIE_SECURE: bool = False
    SESSION_TTL_HOURS: int = 12
    RATE_LIMIT_PER_MINUTE: int = 300
    AUTH_RATE_LIMIT_PER_MINUTE: int = 30

    # Login
    BOOTSTRAP_ADMIN_EMAIL: str = ""
    ALLOWED_LOGIN_DOMAINS: str = ""
    # Google *sign-in* (Identity Services ID-token flow) needs ONLY the client ID. It is NOT the Google Ads OAuth client.
    GOOGLE_CLIENT_ID: str = ""

    # Google Ads
    GOOGLE_ADS_CLIENT_ID: str = ""
    GOOGLE_ADS_CLIENT_SECRET: str = ""
    GOOGLE_ADS_REDIRECT_URI: str = ""
    GOOGLE_ADS_REFRESH_TOKEN: str = ""
    GOOGLE_ADS_LOGIN_CUSTOMER_ID: str = ""

    GOOGLE_PLACES_API_KEY: str = ""
    PAGESPEED_API_KEY: str = ""
    TAVILY_API_KEY: str = ""
    BRAVE_SEARCH_API_KEY: str = ""
    GEOAPIFY_API_KEY: str = ""

    # AI
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = ""
    GROQ_CHALLENGER_MODEL: str = ""
    ANTHROPIC_API_KEY: str = ""  # optional fallback for the Claude integration (model is set on the Integrations page)

    # Email alerts (sent only when SMTP_HOST is set)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "alerts@localhost"

    # Crawler
    CRAWLER_MAX_PAGES: int = 40
    CRAWLER_MAX_DEPTH: int = 2
    CRAWLER_TIMEOUT_SECONDS: float = 10.0

    @field_validator("*", mode="before")
    @classmethod
    def _clean_strings(cls, v):
        """Trim whitespace and a single pair of wrapping quotes: `KEY = "abc" ` must behave like `KEY=abc`."""
        if isinstance(v, str):
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1].strip()
        return v

    @field_validator("AUTH_PROVIDER", mode="before")
    @classmethod
    def _legacy_auth_provider(cls, v):
        return "dev" if isinstance(v, str) and v.strip().lower() == "mock" else v  # old .env files

    @field_validator(*_ID_FIELDS, mode="after")
    @classmethod
    def _customer_ids(cls, v: str) -> str:
        """Google Ads customer IDs are 10 digits; `123-456-7890` and `1234567890` are the same ID. Invalid values are kept
        as typed (so `check-env` can name them) but never half-cleaned."""
        digits = re.sub(r"[\s-]", "", v or "")
        return digits if digits.isdigit() else v

    @field_validator("BOOTSTRAP_ADMIN_EMAIL", mode="after")
    @classmethod
    def _lower_email(cls, v: str) -> str:
        return v.lower()

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        if self.APP_ENV == "production":
            problems = []
            if self.AUTH_PROVIDER == "dev":
                problems.append("AUTH_PROVIDER=dev is not allowed in production (use google)")
            if self.APP_SECRET_KEY.startswith("dev-") or len(self.APP_SECRET_KEY) < 32:
                problems.append("APP_SECRET_KEY must be a strong secret in production")
            if problems:
                raise ValueError("; ".join(problems))
        if self.AUTH_PROVIDER == "google" and not self.GOOGLE_CLIENT_ID:
            raise ValueError("AUTH_PROVIDER=google requires GOOGLE_CLIENT_ID (nobody could sign in otherwise)")
        return self

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
            "google_ads_writes_enabled": self.GOOGLE_ADS_WRITES_ENABLED,
            "auto_negatives_mode": self.AUTO_NEGATIVES_MODE,
            "emergency_brake_write_mode": self.EMERGENCY_BRAKE_WRITE_MODE,
            "auth_provider": self.AUTH_PROVIDER,
            "groq_model_configured": bool(self.GROQ_MODEL),
            "groq_key_configured": bool(self.GROQ_API_KEY),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings_override: dict = {}


def reset_settings_cache() -> None:
    get_settings.cache_clear()
    try:  # resolved integration credentials may embed environment fallbacks
        from app.services import integrations_service
        integrations_service._cache.clear()
    except Exception:  # noqa: BLE001 - not importable during early bootstrap
        pass

