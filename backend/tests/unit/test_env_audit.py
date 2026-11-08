"""Environment audit: the registry matches the code, .env.example and Settings; the rules behave; no value is ever printed."""
from __future__ import annotations

import glob
import json
import re
from pathlib import Path

import pytest

from app.core import env_audit
from app.core.config import REPO_ROOT, Settings, env_files
from app.core.env_audit import BY_NAME, EXTERNAL, REGISTRY, RETIRED, audit

APP = Path(__file__).resolve().parents[2] / "app"
EXAMPLE = REPO_ROOT / ".env.example"
NOT_SETTINGS = {"COMPOSE_DATABASE_URL"}  # read by docker-compose.yml, not by the backend
DYNAMIC = {"CORS_ORIGINS", "ALLOWED_LOGIN_DOMAINS"}  # read through Settings properties (cors_origins / allowed_login_domains)
FAKE = {"GROQ_API_KEY": "gsk_FAKE_SECRET_VALUE_1234567890", "GOOGLE_ADS_CLIENT_SECRET": "GOCSPX-fake-secret-value-abcdef",
        "GOOGLE_ADS_REFRESH_TOKEN": "1//0fake-refresh-token-value-zzzzzz", "GOOGLE_ADS_DEVELOPER_TOKEN": "fakeDevTokenValue123456"}


def names_in_example() -> list[str]:
    return [l.split("=", 1)[0] for l in EXAMPLE.read_text().splitlines() if l and not l.startswith("#") and "=" in l]


def test_registry_matches_settings_and_example():
    fields = set(Settings.model_fields)
    reg = set(BY_NAME)
    assert reg - NOT_SETTINGS == fields, f"in registry only: {reg - NOT_SETTINGS - fields}; in Settings only: {fields - reg}"
    ex = names_in_example()
    assert sorted(ex) == sorted(reg), f"example vs registry: {set(ex) ^ reg}"
    assert len(ex) == len(set(ex)), "duplicate keys in .env.example"
    assert not (set(ex) & set(RETIRED))


def test_example_contains_no_secrets_only_safe_defaults():
    vals = dict(l.split("=", 1) for l in EXAMPLE.read_text().splitlines() if l and not l.startswith("#") and "=" in l)
    for v in REGISTRY:
        if v["secret"] and v["name"] != "DATABASE_URL":
            assert vals[v["name"]] == "", v["name"]
    assert vals["GOOGLE_ADS_WRITES_ENABLED"] == "false" and vals["INTEGRATION_MODE"] == "mock" and vals["AUTH_PROVIDER"] == "mock"
    assert vals["EMERGENCY_BRAKE_WRITE_MODE"] == "simulate" and vals["AUTO_NEGATIVES_MODE"] == "shadow" and vals["GROQ_ENABLED"] == "false"
    assert "app:app@localhost" in vals["DATABASE_URL"]
    Settings(_env_file=str(EXAMPLE))  # the example itself must load


def test_every_environment_read_is_declared():
    declared = set(BY_NAME) | set(EXTERNAL)
    for f in glob.glob(str(APP / "**/*.py"), recursive=True):
        for name in re.findall(r"os\.environ(?:\.get)?\(?\[?[\"']([A-Z][A-Z0-9_]+)[\"']", Path(f).read_text()):
            assert name in declared, f"{f} reads undeclared env var {name}"


def test_every_setting_is_actually_used():
    code = {f: Path(f).read_text() for f in glob.glob(str(APP / "**/*.py"), recursive=True) if not f.endswith("core/config.py") and "env_audit" not in f}
    for name in Settings.model_fields:
        if name in DYNAMIC:
            continue
        pat = rf"\.{name}\b|[\"']{name}[\"']"
        mode = name[:-5] if name.endswith("_MODE") else None
        used = any(re.search(pat, t) or (mode and re.search(rf"mode_for\([\"']{mode}[\"']\)", t)) for t in code.values())
        assert used, f"Settings.{name} is declared but never read — remove it or use it"
    for name, why in RETIRED.items():
        assert name not in Settings.model_fields, f"{name} is retired ({why[:40]}…) but still declared"


def test_settings_cleanup_and_normalisation(monkeypatch):
    for k in ("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "GOOGLE_ADS_CUSTOMER_ID", "BOOTSTRAP_ADMIN_EMAIL", "GROQ_MODEL"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None, GOOGLE_ADS_LOGIN_CUSTOMER_ID=" 123-456-7890 ", GOOGLE_ADS_CUSTOMER_ID="987 654 3210",
                 BOOTSTRAP_ADMIN_EMAIL=" Admin@Example.COM ", GROQ_MODEL='"llama-3.3-70b-versatile"')
    assert s.GOOGLE_ADS_LOGIN_CUSTOMER_ID == "1234567890" and s.GOOGLE_ADS_CUSTOMER_ID == "9876543210"
    assert s.BOOTSTRAP_ADMIN_EMAIL == "admin@example.com" and s.GROQ_MODEL == "llama-3.3-70b-versatile"
    assert Settings(_env_file=None, GOOGLE_ADS_CUSTOMER_ID="12-ab").GOOGLE_ADS_CUSTOMER_ID == "12-ab"  # invalid stays visible to check-env
    with pytest.raises(ValueError, match="GOOGLE_CLIENT_ID"):
        Settings(_env_file=None, AUTH_PROVIDER="google", GOOGLE_CLIENT_ID="")
    assert Settings(_env_file=None, AUTH_PROVIDER="google", GOOGLE_CLIENT_ID="x.apps.googleusercontent.com").AUTH_PROVIDER == "google"


def test_env_file_lookup(monkeypatch, tmp_path):
    monkeypatch.delenv("GAA_ENV_FILE", raising=False)
    files = env_files()
    assert str(REPO_ROOT / ".env") in files and ".env" in files  # the repo-root .env works wherever the backend is started
    monkeypatch.setenv("GAA_ENV_FILE", "none")
    assert env_files() == ()
    f = tmp_path / "x.env"
    monkeypatch.setenv("GAA_ENV_FILE", str(f))
    assert env_files() == (str(f),)


def run(values: dict, **kw) -> dict:
    return audit(values, **kw)


def lv(rep, var, level):
    return [p for p in rep["problems"] if p["var"] == var and p["level"] == level]


def test_rules_google_signin_vs_ads_oauth():
    r = run({"AUTH_PROVIDER": "google"})
    assert lv(r, "GOOGLE_CLIENT_ID", "error") and lv(r, "BOOTSTRAP_ADMIN_EMAIL", "warn")
    r = run({"AUTH_PROVIDER": "mock", "GOOGLE_CLIENT_ID": "abc.apps.googleusercontent.com"})
    assert lv(r, "GOOGLE_CLIENT_ID", "info") and not r["counts"]["error"]  # ignored in mock mode
    assert lv(run({"GOOGLE_CLIENT_ID": "12345"}), "GOOGLE_CLIENT_ID", "error")  # wrong shape
    same = "abc.apps.googleusercontent.com"
    assert lv(run({"AUTH_PROVIDER": "google", "GOOGLE_CLIENT_ID": same, "GOOGLE_ADS_CLIENT_ID": same, "BOOTSTRAP_ADMIN_EMAIL": "a@b.co"}), "GOOGLE_ADS_CLIENT_ID", "warn")
    r = run({"GOOGLE_CLIENT_SECRET": "x", "GOOGLE_REDIRECT_URI": "https://x/y", "KEYWORD_RESEARCH_MODE": "real"})
    assert {p["var"] for p in r["problems"] if "Not used" in p["message"]} == set(RETIRED)
    assert lv(run({"ALLOWED_LOGIN_DOMAINS": "@example.com"}), "ALLOWED_LOGIN_DOMAINS", "error")


def test_rules_google_ads_ids():
    ok = {"GOOGLE_ADS_LOGIN_CUSTOMER_ID": "123-456-7890", "GOOGLE_ADS_CUSTOMER_ID": "987-654-3210"}
    assert not [p for p in run(ok)["problems"] if p["level"] in ("error", "warn") and "CUSTOMER_ID" in p["var"]]
    dup = run({"GOOGLE_ADS_LOGIN_CUSTOMER_ID": "1234567890", "GOOGLE_ADS_CUSTOMER_ID": "123-456-7890"})
    assert lv(dup, "GOOGLE_ADS_CUSTOMER_ID", "warn") and "manager" in lv(dup, "GOOGLE_ADS_CUSTOMER_ID", "warn")[0]["message"]
    assert lv(run({"GOOGLE_ADS_LOGIN_CUSTOMER_ID": "12345"}), "GOOGLE_ADS_LOGIN_CUSTOMER_ID", "error")
    partly = run({"GOOGLE_ADS_CLIENT_ID": "a.apps.googleusercontent.com", "GOOGLE_ADS_CLIENT_SECRET": "s"})
    assert lv(partly, "GOOGLE_ADS_DEVELOPER_TOKEN", "warn") and lv(partly, "GOOGLE_ADS_REFRESH_TOKEN", "warn")
    assert lv(run({"GOOGLE_ADS_MODE": "real"}), "GOOGLE_ADS_MODE", "error")


def test_rules_groq():
    assert lv(run({"AI_PROVIDER": "groq"}), "GROQ_ENABLED", "error")
    r = run({"GROQ_ENABLED": "true", "AI_PROVIDER": "mock", "GROQ_API_KEY": "gsk_x"})
    assert lv(r, "AI_PROVIDER", "warn") and lv(r, "GROQ_MODEL", "error")  # key alone does nothing: a model is required
    r = run({"GROQ_ENABLED": "true", "AI_PROVIDER": "groq", "GROQ_MODEL": "llama-3.3-70b-versatile"})
    assert lv(r, "GROQ_API_KEY", "error") and not lv(r, "GROQ_MODEL", "error")
    good = run({"GROQ_ENABLED": "true", "AI_PROVIDER": "groq", "GROQ_API_KEY": "gsk_abc", "GROQ_MODEL": "llama-3.3-70b-versatile", "GROQ_CHALLENGER_MODEL": "openai/gpt-oss-120b"})
    assert good["counts"]["error"] == 0 and not lv(good, "GROQ_CHALLENGER_MODEL", "warn") and not lv(good, "GROQ_CHALLENGER_MODEL", "info")
    assert lv(run({"GROQ_MODEL": "whisper-large-v3"}), "GROQ_MODEL", "error")  # speech model cannot write plans
    assert lv(run({"GROQ_MODEL": "llama-9000"}), "GROQ_MODEL", "warn")
    assert lv(run({"GROQ_MODEL": "llama-3.3-70b-versatile", "GROQ_CHALLENGER_MODEL": "llama-3.3-70b-versatile"}), "GROQ_CHALLENGER_MODEL", "info")


def test_rules_safety_and_production():
    assert lv(run({"GOOGLE_ADS_WRITES_ENABLED": "true"}), "GOOGLE_ADS_WRITES_ENABLED", "warn")
    prod = run({"APP_ENV": "production"})
    assert {p["var"] for p in prod["problems"] if p["level"] == "error"} >= {"APP_SECRET_KEY", "TOKEN_ENCRYPTION_KEY", "AUTH_PROVIDER", "TEST_MODE", "SESSION_COOKIE_SECURE"}
    hardened = run({"APP_ENV": "production", "APP_SECRET_KEY": "k" * 48, "TOKEN_ENCRYPTION_KEY": "9ZKj5vNq0y3uW8cFhB2tXsL1mP6dRaQe4oYiTnVgHwE=", "AUTH_PROVIDER": "google",
                    "GOOGLE_CLIENT_ID": "a.apps.googleusercontent.com", "BOOTSTRAP_ADMIN_EMAIL": "a@b.co", "TEST_MODE": "false", "SESSION_COOKIE_SECURE": "true",
                    "CORS_ORIGINS": "https://app.example.com"})
    assert hardened["counts"]["error"] == 0
    assert lv(run({"TOKEN_ENCRYPTION_KEY": "not-a-key"}), "TOKEN_ENCRYPTION_KEY", "error")
    assert lv(run({"CORS_ORIGINS": "https://app.example.com/"}), "CORS_ORIGINS", "error")
    assert lv(run({"GROQ_API_KEY": "<your key>"}), "GROQ_API_KEY", "error")  # placeholder
    assert lv(run({"AUTO_NEGATIVES_MODE": "yolo"}), "AUTO_NEGATIVES_MODE", "error")
    assert lv(run({"APP_ENVV": "x"}), "APP_ENVV", "warn")
    assert "Did you mean APP_ENV" in lv(run({"APP_ENVV": "x"}), "APP_ENVV", "warn")[0]["message"]
    assert lv(run({"DATABASE_URL": "postgresql+psycopg://u:p@db.x.supabase.co:5432/postgres"}), "DATABASE_URL", "warn")  # sslmode


def test_duplicates_and_file_parsing(tmp_path):
    f = tmp_path / ".env"
    f.write_text("APP_ENV=development\nGROQ_MODEL=a\n# comment\nGROQ_MODEL = 'b'\nexport MOCK_SEED=7 # note\n")
    values, files, dups = env_audit.load(str(f), environ={})
    assert values["GROQ_MODEL"] == "b" and values["MOCK_SEED"] == "7" and dups == ["GROQ_MODEL"]
    assert lv(audit(values, files=files, duplicates=dups), "GROQ_MODEL", "error")


def test_secret_values_never_appear_in_any_output(tmp_path, capsys):
    f = tmp_path / ".env"
    f.write_text("\n".join(f"{k}={v}" for k, v in FAKE.items()) + "\nAI_PROVIDER=groq\nGROQ_ENABLED=true\nGROQ_MODEL=llama-3.3-70b-versatile\n")
    for kw in ({}, {"as_json": True}, {"brief": True}):
        env_audit.run(str(f), file_only=True, **kw)
        out = capsys.readouterr().out
        for secret in FAKE.values():
            assert secret not in out and secret[:12] not in out
    rep = audit(env_audit.load(str(f), environ={})[0])
    assert all(isinstance(r["configured"], str) and r["configured"] in ("set", "blank", "default") for r in rep["rows"])
    assert "gsk_FAKE" not in json.dumps(rep)


def test_exit_codes_and_json(tmp_path, capsys):
    bad, good = tmp_path / "bad.env", tmp_path / "good.env"
    bad.write_text("AUTH_PROVIDER=google\n")
    good.write_text("APP_ENV=development\nAUTH_PROVIDER=mock\n")
    assert env_audit.run(str(bad), file_only=True) == 1
    capsys.readouterr()
    assert env_audit.run(str(good), as_json=True, file_only=True) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert env_audit.run(str(good), strict=True, file_only=True) == 1  # --strict also fails on warnings (DATABASE_URL blank)


def test_retired_variables_are_not_silently_accepted():
    rep = run({"GOOGLE_CLIENT_SECRET": "x"})
    assert lv(rep, "GOOGLE_CLIENT_SECRET", "warn") and "ID tokens" in lv(rep, "GOOGLE_CLIENT_SECRET", "warn")[0]["message"]
