"""The shipped .env.example must load as-is (blank values = defaults) and keep every safety default."""
from __future__ import annotations

import pathlib

from app.core.config import Settings

EXAMPLE = pathlib.Path(__file__).resolve().parents[3] / ".env.example"


def test_env_example_loads_with_safe_defaults(monkeypatch):
    for key in ("APP_ENV", "INTEGRATION_MODE", "TEST_MODE", "GOOGLE_ADS_WRITES_ENABLED", "AI_PROVIDER", "AUTH_PROVIDER",
                "AUTO_NEGATIVES_MODE", "GOOGLE_ADS_MODE", "EMERGENCY_BRAKE_WRITE_MODE", "GROQ_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=str(EXAMPLE))
    assert s.INTEGRATION_MODE == "mock"
    assert all(s.mode_for(n) == "mock" for n in ("GOOGLE_ADS", "KEYWORD_RESEARCH", "PLACES", "PAGESPEED", "NOTIFIER"))
    assert s.GOOGLE_ADS_WRITES_ENABLED is False
    assert s.AUTH_PROVIDER == "mock" and s.AI_PROVIDER == "mock" and s.GROQ_ENABLED is False
    assert s.AUTO_NEGATIVES_MODE == "shadow" and s.EMERGENCY_BRAKE_WRITE_MODE == "simulate"


def test_env_example_contains_no_secrets():
    text = EXAMPLE.read_text()
    for key in ("GROQ_API_KEY", "GOOGLE_ADS_CLIENT_SECRET", "ANTHROPIC_API_KEY", "SMTP_PASSWORD",
                "GOOGLE_PLACES_API_KEY", "PAGESPEED_API_KEY", "TOKEN_ENCRYPTION_KEY", "APP_SECRET_KEY", "GOOGLE_ADS_REFRESH_TOKEN"):
        line = next(ln for ln in text.splitlines() if ln.startswith(f"{key}="))
        assert line == f"{key}=", f"{key} must be blank in .env.example"
    assert "supabase.com" not in "".join(ln for ln in text.splitlines() if not ln.startswith("#"))
