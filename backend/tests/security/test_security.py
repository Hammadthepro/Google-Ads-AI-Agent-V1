"""Security tests: SSRF guard, secret exposure, layering contract, production guards, headers, rate limits, write guard."""
from __future__ import annotations

import ast
import pathlib
import socket

import httpx
import pytest

from app.adapters.crawler import LiveCrawlerAdapter, SSRFBlocked, check_url_safe
from tests.conftest import ADMIN, SUPER, Api

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def _resolver(mapping):
    def r(host, port):
        ip = mapping.get(host, "93.184.216.34")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
    return r


@pytest.mark.parametrize("url", ["http://localhost/", "http://127.0.0.1/", "http://169.254.169.254/latest/meta-data",
                                 "http://10.0.0.5/", "http://192.168.1.1/", "file:///etc/passwd", "ftp://example.com/",
                                 "http://user:pass@example.com/", "http://example.com:6379/", "http://metadata.google.internal/",
                                 "http://[::1]/"])
def test_ssrf_blocked(url):
    with pytest.raises(SSRFBlocked):
        check_url_safe(url, _resolver({"localhost": "127.0.0.1", "[::1]": "::1"}))


def test_ssrf_dns_rebinding_to_private_blocked():
    with pytest.raises(SSRFBlocked):
        check_url_safe("https://evil.example/", _resolver({"evil.example": "10.1.2.3"}))
    check_url_safe("https://good.example/", _resolver({"good.example": "93.184.216.34"}))


def test_live_crawler_blocks_redirect_to_private_address():
    def handler(request: httpx.Request):
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://internal.example/admin"})
        return httpx.Response(200, text="secret internal page")
    crawler = LiveCrawlerAdapter(transport=httpx.MockTransport(handler),
                                 resolver=_resolver({"public.example": "93.184.216.34", "internal.example": "10.0.0.9"}))
    res = crawler.fetch("https://public.example/")
    assert not res.ok and "Blocked" in res.error and res.redirects == ["http://internal.example/admin"]


def test_layering_contract_business_logic_never_imports_vendor_sdks_concrete_adapters_or_test_fakes():
    forbidden = ("groq", "google.ads", "google.oauth2", "app.adapters.google_ads.real", "tests", "app.mock_world")
    bad = []
    for path in list((APP / "services").glob("*.py")) + list((APP / "api").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            bad += [(path.name, n) for n in names if n.startswith(forbidden)]
    assert bad == [], bad


def test_secrets_never_returned_by_api(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_TESTSECRET1234567890")
    monkeypatch.setenv("GOOGLE_ADS_CLIENT_SECRET", "adssecret-SECRET-abc")
    from app.core.config import reset_settings_cache
    reset_settings_cache()
    try:
        sup = Api(SUPER)
        for path in ("/integrations", "/settings", "/auth/me", "/auth/config", "/dashboard"):
            body = sup.get(path).text
            assert "gsk_TESTSECRET" not in body and "adssecret-SECRET" not in body, path
        groq = next(i for i in sup.get("/integrations").json()["integrations"] if i["provider"] == "groq")
        assert groq["secrets"]["api_key"]["saved"] is True and groq["secrets"]["api_key"]["source"] == "environment"
    finally:
        reset_settings_cache()


def test_production_config_guards():
    from app.core.config import Settings
    with pytest.raises(ValueError):
        Settings(APP_ENV="production", AUTH_PROVIDER="dev", GOOGLE_CLIENT_ID="a.apps.googleusercontent.com", APP_SECRET_KEY="x" * 40)
    with pytest.raises(ValueError):
        Settings(APP_ENV="production", AUTH_PROVIDER="google", GOOGLE_CLIENT_ID="a.apps.googleusercontent.com", APP_SECRET_KEY="dev-short")
    with pytest.raises(ValueError):
        Settings(AUTH_PROVIDER="google")  # nobody could sign in without a client id


def test_security_headers_and_no_store():
    r = Api(ADMIN).get("/auth/me")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Cache-Control"] == "no-store"
    assert "X-Request-ID" in r.headers


def test_csrf_required_for_state_changes():
    api = Api(ADMIN)
    r = api.c.post("/api/v1/clients", json={"name": "No CSRF"})
    assert r.status_code == 403 and "CSRF" in r.text
    r = api.c.post("/api/v1/clients", json={"name": "Bad CSRF"}, headers={"X-CSRF-Token": "forged"})
    assert r.status_code == 403


def test_auth_rate_limit():
    from app.main import app
    limiter = app.state.limiter
    key = "auth:testclient"
    for _ in range(31):
        limiter.hit(key, 30)
    assert limiter.hit(key, 30) is False


def test_session_cookie_is_httponly():
    from app.main import app
    from fastapi.testclient import TestClient
    r = TestClient(app).post("/api/v1/auth/dev-login", json={"email": ADMIN})
    cookies = r.headers.get_list("set-cookie")
    sess = next(c for c in cookies if c.startswith("gaa_session="))
    assert "HttpOnly" in sess and "samesite=lax" in sess.lower()


def test_sql_injection_like_input_is_inert():
    api = Api(ADMIN)
    r = api.get("/activity?action=' OR 1=1 --")
    assert r.status_code == 200 and r.json() == []
