"""The shipped .env.example must load as-is (blank values = defaults) and keep every safety default."""
from __future__ import annotations

import pathlib

from app.core.config import Settings

EXAMPLE = pathlib.Path(__file__).resolve().parents[3] / ".env.example"


def test_env_example_loads_with_safe_defaults(monkeypatch):
    for key in ("APP_ENV", "GOOGLE_ADS_WRITES_ENABLED", "AUTH_PROVIDER", "AUTO_NEGATIVES_MODE", "EMERGENCY_BRAKE_WRITE_MODE"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=str(EXAMPLE))
    assert s.GOOGLE_ADS_WRITES_ENABLED is False
    assert s.AUTH_PROVIDER == "dev"
    assert s.AUTO_NEGATIVES_MODE == "shadow" and s.EMERGENCY_BRAKE_WRITE_MODE == "simulate"


def test_env_example_contains_no_secrets():
    text = EXAMPLE.read_text()
    for key in ("GROQ_API_KEY", "GOOGLE_ADS_CLIENT_SECRET", "ANTHROPIC_API_KEY", "SMTP_PASSWORD",
                "GOOGLE_PLACES_API_KEY", "PAGESPEED_API_KEY", "TOKEN_ENCRYPTION_KEY", "APP_SECRET_KEY", "GOOGLE_ADS_REFRESH_TOKEN"):
        line = next(ln for ln in text.splitlines() if ln.startswith(f"{key}="))
        assert line == f"{key}=", f"{key} must be blank in .env.example"
    assert "supabase.com" not in "".join(ln for ln in text.splitlines() if not ln.startswith("#"))
