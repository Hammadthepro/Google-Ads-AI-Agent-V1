"""Integrations platform: encrypted credential store, stepwise REAL tests (fake transport), OAuth, modes.

No test here contacts a real provider; transports are fakes, so these prove our request/diagnostic logic only.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.db.session import session_scope
from app.models import IntegrationConfig
from app.services import integrations_service as svc
from tests.conftest import Api

SUPER = "superadmin@napollo.example"
SECRET = "dev-token-SECRET-123456"


@pytest.fixture(autouse=True)
def _reset_integrations():
    yield
    with session_scope() as db:
        db.query(IntegrationConfig).delete()
        from app.services import settings_service
        settings_service.set_value(db, "operating_mode", "mock", None)
    svc.invalidate()


def _google_handler(*, token_status=200, ads_status=200, manager=False):
    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if u.startswith("https://oauth2.googleapis.com/token"):
            if token_status != 200:
                return httpx.Response(token_status, json={"error": "invalid_grant", "error_description": "Token has been revoked"})
            return httpx.Response(200, json={"access_token": "ya29.fake", "expires_in": 3600, "scope": "adwords"})
        if "listAccessibleCustomers" in u:
            if ads_status != 200:
                return httpx.Response(ads_status, json={"error": {"message": "developer token not approved"}})
            return httpx.Response(200, json={"resourceNames": ["customers/1112223334", "customers/5556667778"]})
        if "googleAds:search" in u:
            q = json.loads(req.content)["query"]
            if "FROM customer" in q:
                return httpx.Response(200, json={"results": [{"customer": {"id": "1112223334", "descriptiveName": "Acme", "manager": manager,
                                                                           "currencyCode": "USD", "timeZone": "America/Chicago"}}]})
            return httpx.Response(200, json={"results": [{"campaign": {"id": "1"}}]})
        if "accountSummaries" in u:
            return httpx.Response(200, json={"accountSummaries": [{"displayName": "Acme", "propertySummaries": [{"property": "properties/42", "displayName": "Site"}]}]})
        if "runReport" in u:
            return httpx.Response(200, json={"rows": []})
        return httpx.Response(404)
    return handler


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _save_ads(db, **extra):
    svc.save(db, "google_ads", settings={"client_id": "cid.apps.googleusercontent.com", "customer_id": "111-222-3334", **extra},
             secrets={"developer_token": SECRET, "client_secret": "client-secret-xyz", "refresh_token": "1//0refresh-abc-def-ghi"}, user_id=None)


def test_secrets_are_encrypted_and_masked():
    with session_scope() as db:
        _save_ads(db)
        row = db.query(IntegrationConfig).filter_by(provider="google_ads").one()
        assert SECRET not in row.secrets_ciphertext and row.secrets_ciphertext
        assert row.settings["customer_id"] == "1112223334"
    sup = Api(SUPER)
    body = sup.get("/integrations").text
    assert SECRET not in body and "client-secret-xyz" not in body and "1//0refresh" not in body
    ads = sup.get("/integrations/google_ads").json()
    assert ads["secrets"]["developer_token"]["saved"] and len(ads["secrets"]["developer_token"]["fingerprint"]) >= 8
    assert "value" not in ads["secrets"]["developer_token"]


def test_blank_secret_keeps_existing_and_unknown_fields_rejected():
    sup = Api(SUPER)
    with session_scope() as db:
        _save_ads(db)
    r = sup.c.put("/api/v1/integrations/google_ads", json={"settings": {"login_customer_id": "999-888-7777"}, "secrets": {"developer_token": ""}},
                  headers=sup._h())
    assert r.status_code == 200 and r.json()["secrets"]["developer_token"]["saved"]
    bad = sup.c.put("/api/v1/integrations/google_ads", json={"settings": {"nope": "x"}}, headers=sup._h())
    assert bad.status_code == 422 or bad.status_code == 400
    bad = sup.c.put("/api/v1/integrations/google_ads", json={"settings": {"customer_id": "12"}}, headers=sup._h())
    assert bad.status_code in (400, 422)


def test_google_ads_connection_steps_succeed_with_fake_transport():
    with session_scope() as db:
        _save_ads(db)
        res = svc.test_connection(db, "google_ads", http=_client(_google_handler()))
        assert res["ok"] and res["status"] == "CONNECTED"
        names = [s["name"] for s in res["steps"]]
        assert names == ["Credentials present", "Refresh OAuth access token", "List accessible customers", "Customer accessible",
                         "Read account information", "Read campaigns"]
        assert all(s["status"] == "PASSED" for s in res["steps"])
        row = db.query(IntegrationConfig).filter_by(provider="google_ads").one()
        assert row.connected_account == "Acme" and row.account_id == "1112223334"
        assert "ya29.fake" not in json.dumps(res)


def test_google_ads_failure_stops_at_failed_step_and_is_redacted():
    with session_scope() as db:
        _save_ads(db)
        res = svc.test_connection(db, "google_ads", http=_client(_google_handler(token_status=400)))
        assert not res["ok"] and res["status"] == "AUTHORIZATION_EXPIRED"
        failed = [s for s in res["steps"] if s["status"] == "FAILED"]
        assert failed[0]["name"] == "Refresh OAuth access token" and "reconnect" in failed[0]["hint"].lower()
        assert not any(s["name"] == "List accessible customers" for s in res["steps"])
        res = svc.test_connection(db, "google_ads", http=_client(_google_handler(ads_status=403)))
        assert res["status"] == "CONNECTION_FAILED" and "developer token" in json.dumps(res["steps"]).lower()
        with pytest.raises(Exception):
            svc.set_enabled(db, "google_ads", True, None)  # cannot enable after a failed test


def test_missing_credentials_report_configuration_required():
    with session_scope() as db:
        svc.save(db, "google_ads", settings={"client_id": "x"}, secrets={}, user_id=None)
        res = svc.test_connection(db, "google_ads", http=_client(_google_handler()))
        assert res["status"] == "CONFIGURATION_REQUIRED" and "developer_token" in res["steps"][0]["detail"]


def test_modes_require_a_passing_connection_and_writes_need_both_keys(monkeypatch):
    sup = Api(SUPER)
    assert sup.get("/integrations/mode").json()["mode"] == "mock"
    r = sup.c.put("/api/v1/integrations/mode", json={"mode": "read_only"}, headers=sup._h())
    assert r.status_code in (400, 422) and "Test Connection" in r.text
    with session_scope() as db:
        _save_ads(db)
        svc.test_connection(db, "google_ads", http=_client(_google_handler()))
        svc.set_enabled(db, "google_ads", True, None)
    r = sup.c.put("/api/v1/integrations/mode", json={"mode": "read_only"}, headers=sup._h())
    assert r.status_code == 200
    m = r.json()
    assert m["mode"] == "read_only" and m["real_reads"] and not m["real_writes_possible"]
    from app.adapters import registry
    assert registry.get("google_ads").source == "LIVE"
    r = sup.c.put("/api/v1/integrations/mode", json={"mode": "execution"}, headers=sup._h())
    assert r.status_code in (400, 422) and "GOOGLE_ADS_WRITES_ENABLED" in r.text  # key 1 off in tests
    # disconnecting Google Ads drops back to the safe mode
    sup.post("/integrations/google_ads/disconnect")
    assert sup.get("/integrations/mode").json()["mode"] == "mock"
    assert registry.get("google_ads").source == "MOCK"


def test_ga4_and_groq_and_claude_steps():
    with session_scope() as db:
        _save_ads(db)
        svc.save(db, "ga4", settings={"property_id": "42"}, secrets={"refresh_token": "1//0ga4refresh"}, user_id=None)
        res = svc.test_connection(db, "ga4", http=_client(_google_handler()))
        assert res["ok"] and [s["name"] for s in res["steps"]][-1] == "Run report"

        svc.save(db, "groq", settings={"model": "llama-x"}, secrets={"api_key": "gsk_abcdefghijk"}, user_id=None)
        groq = _client(lambda req: httpx.Response(200, json={"data": [{"id": "llama-x"}, {"id": "other"}]}) if "groq.com" in str(req.url)
                       else httpx.Response(404))
        assert svc.test_connection(db, "groq", http=groq)["ok"]
        svc.save(db, "groq", settings={"model": "missing-model"}, secrets={}, user_id=None)
        res = svc.test_connection(db, "groq", http=groq)
        assert not res["ok"] and res["steps"][-1]["name"] == "Model available"

        svc.save(db, "claude", settings={"model": "claude-opus-5-5"}, secrets={"api_key": "sk-ant-fake"}, user_id=None)

        def claude(req):
            assert req.headers["x-api-key"] == "sk-ant-fake" and "anthropic-version" in req.headers
            return httpx.Response(200, json={"data": [{"id": "claude-opus-5-5"}]})
        assert svc.test_connection(db, "claude", http=_client(claude))["ok"]
        bad = _client(lambda req: httpx.Response(401, json={"error": {"type": "authentication_error", "message": "invalid x-api-key"}}))
        res = svc.test_connection(db, "claude", http=bad)
        assert not res["ok"] and res["status"] == "CONNECTION_FAILED"


def test_oauth_state_is_single_use_user_bound_and_pkce():
    with session_scope() as db:
        svc.save(db, "google_ads", settings={"client_id": "cid"}, secrets={"client_secret": "csecret"}, user_id=None)
        out = svc.oauth_start(db, ["google_ads", "ga4"], "user1")
        url = out["authorization_url"]
        assert "code_challenge_method=S256" in url and "access_type=offline" in url and "csecret" not in url
        assert "analytics.readonly" in url and "adwords" in url
        state = url.split("state=")[1].split("&")[0]

        def token(req):
            body = dict(x.split("=") for x in req.content.decode().split("&"))
            assert body["grant_type"] == "authorization_code" and body["code_verifier"]
            return httpx.Response(200, json={"refresh_token": "1//0newrefresh", "scope": "https://www.googleapis.com/auth/adwords"})
        with pytest.raises(Exception):
            svc.oauth_callback(db, state, "code", "someone-else", http=_client(token))
        res = svc.oauth_callback(db, state, "code", "user1", http=_client(token))
        assert res["saved"] == ["google_ads"]  # ga4 scope was not granted → not saved
        with pytest.raises(Exception):
            svc.oauth_callback(db, state, "code", "user1", http=_client(token))  # replay
        assert svc.serialize(db, "google_ads")["secrets"]["refresh_token"]["saved"]


def test_only_super_admin_can_manage_integrations():
    for email in ("admin@napollo.example", "manager@napollo.example", "analyst@napollo.example"):
        a = Api(email)
        assert a.c.put("/api/v1/integrations/groq", json={"secrets": {"api_key": "x"}}, headers=a._h()).status_code == 403
        assert a.post("/integrations/google_ads/test").status_code == 403
    assert Api("analyst@napollo.example").get("/integrations/health").status_code == 200


def test_health_and_test_all_never_fake_success():
    sup = Api(SUPER)
    h = sup.get("/integrations/health").json()
    assert {i["provider"] for i in h["items"]} >= {"google_ads", "ga4", "gbp", "keyword_planner", "groq", "claude", "pagespeed", "places", "crawler"}
    assert all(i["status"] != "CONNECTED" for i in h["items"] if i["provider"] in ("google_ads", "groq", "claude", "ga4"))
    r = sup.post("/integrations/test-all").json()
    ads = next(x for x in r["results"] if x["provider"] == "google_ads")
    assert ads.get("skipped") and ads["status"] != "CONNECTED"


def test_saving_credentials_refuses_the_public_development_key(monkeypatch):
    """With no TOKEN_ENCRYPTION_KEY and the built-in dev APP_SECRET_KEY, 'encrypting' credentials would be meaningless."""
    from app.core.config import reset_settings_cache
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "")
    monkeypatch.setenv("APP_SECRET_KEY", "dev-insecure-secret-change-me")
    reset_settings_cache()
    try:
        sup = Api(SUPER)
        r = sup.c.put("/api/v1/integrations/groq", json={"secrets": {"api_key": "gsk_x"}}, headers=sup._h())
        assert r.status_code in (400, 422) and "TOKEN_ENCRYPTION_KEY" in r.text
        assert sup.c.put("/api/v1/integrations/groq", json={"settings": {"model": "llama-3.3-70b-versatile"}}, headers=sup._h()).status_code == 200  # non-secret settings are fine
        monkeypatch.setenv("APP_SECRET_KEY", "r" * 48)
        reset_settings_cache()
        assert sup.c.put("/api/v1/integrations/groq", json={"secrets": {"api_key": "gsk_x"}}, headers=sup._h()).status_code == 200
    finally:
        monkeypatch.undo()
        reset_settings_cache()


def test_env_customer_ids_are_normalised_for_real_requests(monkeypatch):
    from app.core.config import reset_settings_cache
    monkeypatch.setenv("GOOGLE_ADS_CUSTOMER_ID", "123-456-7890")
    monkeypatch.setenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "987-654-3210")
    reset_settings_cache()
    try:
        vals = svc.effective("google_ads", None)
        assert vals["customer_id"] == "1234567890" and vals["login_customer_id"] == "9876543210"
    finally:
        monkeypatch.undo()
        reset_settings_cache()
