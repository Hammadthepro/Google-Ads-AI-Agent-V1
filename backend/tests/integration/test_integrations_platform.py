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
SECRET = "client-secret-xyz"  # fake


@pytest.fixture(autouse=True)
def _reset_integrations():
    from app.services import mcc_service
    with session_scope() as db:  # the seeded fake world selected a manager; these tests start with none chosen, then restore it
        ws = mcc_service.default_workspace(db)
        saved_manager, ws.google_ads_manager_customer_id = ws.google_ads_manager_customer_id, ""
    svc.invalidate()
    yield
    with session_scope() as db:
        mcc_service.default_workspace(db).google_ads_manager_customer_id = saved_manager
    with session_scope() as db:
        db.query(IntegrationConfig).delete()
        from app.services import settings_service
        settings_service.set_value(db, "operating_mode", "mock", None)
    svc.invalidate()


SENT_HEADERS: list[dict] = []


def _google_handler(*, token_status=200, ads_status=200, manager=False, client_error=None, test_account=False, mcc_is_manager=True, mcc_status="ENABLED"):
    """Fake Google endpoints. `client_error` = an authorizationError code returned for the client account query."""
    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        SENT_HEADERS.append({k.lower(): v for k, v in req.headers.items()})
        if u.startswith("https://oauth2.googleapis.com/token"):
            if token_status != 200:
                return httpx.Response(token_status, json={"error": "invalid_grant", "error_description": "Token has been revoked"})
            return httpx.Response(200, json={"access_token": "ya29.fake", "expires_in": 3600, "scope": "adwords"})
        if "listAccessibleCustomers" in u:
            if ads_status == 403:
                return httpx.Response(403, json={"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "Google Ads API has not been used in project 123 before or it is disabled.",
                                                           "details": [{"reason": "SERVICE_DISABLED"}]}})
            if ads_status != 200:
                return httpx.Response(ads_status, json={"error": {"message": "boom"}})
            return httpx.Response(200, json={"resourceNames": ["customers/1112223334", "customers/5556667778", "customers/9998887776"]})
        if "googleAds:search" in u:
            assert "pageSize" not in json.loads(req.content), "pageSize is not supported by googleAds:search"
            cust = u.split("/customers/")[1].split("/")[0]
            q = json.loads(req.content)["query"]
            if client_error and cust == "1112223334":
                return httpx.Response(403, json={"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "The caller does not have permission",
                                                           "details": [{"errors": [{"errorCode": {"authorizationError": client_error}}]}]}})
            if "FROM customer_client" in q:
                if cust != "5556667778":
                    return httpx.Response(403, json={"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "no",
                                                               "details": [{"errors": [{"errorCode": {"authorizationError": "USER_PERMISSION_DENIED"}}]}]}})
                return httpx.Response(200, json={"results": [{"customerClient": {"id": "5556667778", "level": "0", "manager": True, "status": mcc_status}},
                                                              {"customerClient": {"id": "1112223334", "level": "1", "manager": False, "status": "ENABLED"}},
                                                              {"customerClient": {"id": "9998887776", "level": "1", "manager": False, "status": "CLOSED"}}]})
            if "FROM customer" in q:
                is_mgr = (cust == "5556667778" and mcc_is_manager) or manager
                return httpx.Response(200, json={"results": [{"customer": {"id": cust, "descriptiveName": "Manager Co" if is_mgr else "Acme", "manager": is_mgr,
                                                                           "status": mcc_status if cust == "5556667778" else "ENABLED",
                                                                           "testAccount": test_account, "currencyCode": "USD", "timeZone": "America/Chicago"}}]})
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
    svc.save(db, "google_ads", settings={"client_id": "609437209123-abc.apps.googleusercontent.com", **extra},
             secrets={"client_secret": SECRET, "refresh_token": "1//0refresh-abc-def-ghi"}, user_id=None)


def test_secrets_are_encrypted_and_masked():
    with session_scope() as db:
        _save_ads(db)
        row = db.query(IntegrationConfig).filter_by(provider="google_ads").one()
        assert SECRET not in row.secrets_ciphertext and row.secrets_ciphertext
        assert "customer_id" not in row.settings  # retired: client accounts are discovered from the manager
    sup = Api(SUPER)
    body = sup.get("/integrations").text
    assert SECRET not in body and "1//0refresh" not in body
    ads = sup.get("/integrations/google_ads").json()
    assert ads["secrets"]["client_secret"]["saved"] and len(ads["secrets"]["client_secret"]["fingerprint"]) >= 8
    assert "value" not in ads["secrets"]["client_secret"] and "developer_token" not in ads["secrets"]


def test_blank_secret_keeps_existing_and_unknown_fields_rejected():
    sup = Api(SUPER)
    with session_scope() as db:
        _save_ads(db)
    r = sup.c.put("/api/v1/integrations/google_ads", json={"settings": {"login_customer_id": "999-888-7777"}, "secrets": {"client_secret": ""}},
                  headers=sup._h())
    assert r.status_code == 200 and r.json()["secrets"]["client_secret"]["saved"]
    assert sup.c.put("/api/v1/integrations/google_ads", json={"secrets": {"developer_token": "x"}}, headers=sup._h()).status_code in (400, 422)  # retired
    bad = sup.c.put("/api/v1/integrations/google_ads", json={"settings": {"nope": "x"}}, headers=sup._h())
    assert bad.status_code in (400, 422)
    bad = sup.c.put("/api/v1/integrations/google_ads", json={"settings": {"login_customer_id": "12"}}, headers=sup._h())
    assert bad.status_code in (400, 422)
    old = sup.c.put("/api/v1/integrations/google_ads", json={"settings": {"customer_id": "1112223334"}}, headers=sup._h())
    assert old.status_code in (400, 422)  # GOOGLE_ADS_CUSTOMER_ID / customer_id no longer exists


def test_google_ads_connects_after_an_enabled_manager_is_found_and_sends_no_developer_token():
    SENT_HEADERS.clear()
    with session_scope() as db:
        _save_ads(db)  # no manager and no client account configured anywhere
        res = svc.test_connection(db, "google_ads", http=_client(_google_handler()))
        assert res["ok"] and res["connected"] and res["status"] == "CONNECTED", [x for x in res["steps"] if x["status"] == "FAILED"]
        st = {k: v["status"] for k, v in res["subtests"].items()}
        assert st == {"auth": "PASSED", "cloud_access": "PASSED", "manager": "PASSED", "hierarchy": "SKIPPED"}
        assert "ya29.fake" not in json.dumps(res)
        s = svc.serialize(db, "google_ads")
        assert s["display"]["label"] == "Connected" and s["info"]["cloud_project_number"] == "609437209123"
        assert [m["customer_id"] for m in s["info"]["managers"]] == ["5556667778"]
    assert SENT_HEADERS and not any("developer-token" in h for h in SENT_HEADERS)  # sunset 2026-09-09: never sent


def test_hierarchy_step_uses_the_selected_manager_as_login_customer_id():
    SENT_HEADERS.clear()
    from app.services import mcc_service
    with session_scope() as db:
        _save_ads(db)
        mcc_service.default_workspace(db).google_ads_manager_customer_id = "5556667778"
    svc.invalidate()
    with session_scope() as db:
        res = svc.test_connection(db, "google_ads", http=_client(_google_handler()))
        assert res["connected"] and res["subtests"]["hierarchy"]["status"] == "PASSED"
        assert "1 account" in res["subtests"]["hierarchy"]["detail"] or "2 account" in res["subtests"]["hierarchy"]["detail"]
        assert svc.serialize(db, "google_ads")["info"]["hierarchy"]["manager"] == "5556667778"
        mcc_service.default_workspace(db).google_ads_manager_customer_id = ""
    assert any(h.get("login-customer-id") == "5556667778" for h in SENT_HEADERS)


def test_manager_and_hierarchy_can_be_tested_separately():
    with session_scope() as db:
        _save_ads(db)
        svc.test_connection(db, "google_ads", http=_client(_google_handler()))
        r = svc.test_connection(db, "google_ads", http=_client(_google_handler()), target="manager")
        assert {k: v["status"] for k, v in r["subtests"].items()}["manager"] == "PASSED" and r["connected"] is True
        r = svc.test_connection(db, "google_ads", http=_client(_google_handler()), target="hierarchy")
        assert r["subtests"]["hierarchy"]["status"] == "SKIPPED"
        with pytest.raises(Exception):
            svc.test_connection(db, "google_ads", http=_client(_google_handler()), target="client")  # the single-client test is gone


def test_no_enabled_manager_means_not_connected_with_a_clear_reason():
    for kw in ({"mcc_is_manager": False}, {"mcc_status": "CLOSED"}):
        with session_scope() as db:
            _save_ads(db)
            res = svc.test_connection(db, "google_ads", http=_client(_google_handler(**kw)))
            assert not res["connected"] and res["subtests"]["manager"]["status"] == "FAILED"
            assert "enabled manager" in res["subtests"]["manager"]["detail"] and "ENABLED manager" in res["subtests"]["manager"]["hint"]
            assert svc.serialize(db, "google_ads")["display"]["label"] == "Connection failed"


def test_a_test_only_project_is_reported_per_account_without_blocking_manager_discovery():
    with session_scope() as db:
        _save_ads(db)
        res = svc.test_connection(db, "google_ads", http=_client(_google_handler(client_error="CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION")))
        assert res["connected"]  # an enabled manager exists; one non-test client account is simply not readable by a Test-access project
        checked = {c["customer_id"]: c for c in svc.serialize(db, "google_ads")["settings"] and db.query(IntegrationConfig).filter_by(provider="google_ads").one().discovered["accounts_checked"]}
        assert checked["1112223334"]["error"] == "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION"
        assert svc.serialize(db, "google_ads")["info"]["api_access"]["level"] == "test"
        res = svc.test_connection(db, "google_ads", http=_client(_google_handler(ads_status=403)))
        assert res["subtests"]["cloud_access"]["status"] == "FAILED" and "Enable it" in res["subtests"]["cloud_access"]["hint"]
        assert svc.serialize(db, "google_ads")["info"]["api_access"]["api_enabled"] is False


def test_google_ads_failure_stops_at_failed_step_and_is_redacted():
    with session_scope() as db:
        _save_ads(db)
        res = svc.test_connection(db, "google_ads", http=_client(_google_handler(token_status=400)))
        assert not res["ok"] and res["status"] == "AUTHORIZATION_EXPIRED"
        failed = [s for s in res["steps"] if s["status"] == "FAILED"]
        assert failed[0]["name"].startswith("OAuth authentication") and "reconnect" in failed[0]["hint"].lower()
        assert not any("accessible" in s["name"].lower() for s in res["steps"])
        with pytest.raises(Exception):
            svc.set_enabled(db, "google_ads", True, None)  # cannot enable after a failed test


def test_missing_credentials_report_configuration_required():
    with session_scope() as db:
        svc.save(db, "google_ads", settings={"client_id": "x"}, secrets={}, user_id=None)
        res = svc.test_connection(db, "google_ads", http=_client(_google_handler()))
        assert res["status"] == "CONFIGURATION_REQUIRED" and "client_secret" in res["steps"][0]["detail"] and "developer" not in res["steps"][0]["detail"].lower().replace("no developer", "")
        assert svc.serialize(db, "google_ads")["display"]["label"] == "Not configured"


@pytest.mark.real_registry
def test_modes_require_a_passing_connection_and_writes_need_both_keys(monkeypatch):
    from app.adapters import registry
    from app.core.errors import IntegrationNotConfigured
    sup = Api(SUPER)
    assert sup.get("/integrations/mode").json()["mode"] == "read_only"  # default: real account, read-only. There is no mock mode.
    with pytest.raises(IntegrationNotConfigured) as exc:
        registry.get("google_ads")  # nothing configured -> a clear error, never simulated data
    assert "Integrations page" in exc.value.message
    r = sup.c.put("/api/v1/integrations/mode", json={"mode": "execution"}, headers=sup._h())
    assert r.status_code in (400, 422) and "Test Connection" in r.text
    with session_scope() as db:
        _save_ads(db)
        svc.test_connection(db, "google_ads", http=_client(_google_handler()))
        svc.set_enabled(db, "google_ads", True, None)
    assert registry.get("google_ads").source == "LIVE"
    r = sup.c.put("/api/v1/integrations/mode", json={"mode": "read_only"}, headers=sup._h())
    assert r.status_code == 200
    m = r.json()
    assert m["mode"] == "read_only" and m["real_reads"] and not m["real_writes_possible"]
    r = sup.c.put("/api/v1/integrations/mode", json={"mode": "execution"}, headers=sup._h())
    assert r.status_code in (400, 422) and "GOOGLE_ADS_WRITES_ENABLED" in r.text  # key 1 off in tests
    # disconnecting Google Ads drops back to the safe mode
    sup.post("/integrations/google_ads/disconnect")
    assert sup.get("/integrations/mode").json()["mode"] == "read_only"
    with pytest.raises(IntegrationNotConfigured):
        registry.get("google_ads")


def test_ga4_and_groq_and_claude_steps():
    with session_scope() as db:
        _save_ads(db)
        svc.save(db, "ga4", settings={"property_id": "42"}, secrets={"refresh_token": "1//0ga4refresh"}, user_id=None)
        res = svc.test_connection(db, "ga4", http=_client(_google_handler()))
        assert res["ok"] and [s["name"] for s in res["steps"]][-1] == "Run report"

        svc.save(db, "groq", settings={"model": "llama-x", "challenger_model": "other"}, secrets={"api_key": "gsk_abcdefghijk"}, user_id=None)
        chat_calls = []

        def groq_handler(req):
            if req.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [{"id": "llama-x"}, {"id": "other"}]})
            body = json.loads(req.content)
            chat_calls.append(body["model"])
            assert body["response_format"] == {"type": "json_object"}  # the mode the app actually uses
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"total_tokens": 20}})
        groq = _client(groq_handler)
        res = svc.test_connection(db, "groq", http=groq)
        assert res["connected"] and chat_calls == ["llama-x", "other"]
        assert {k: v["status"] for k, v in res["subtests"].items()} == {"auth": "PASSED", "primary": "PASSED", "challenger": "PASSED"}
        # primary-only run keeps the stored challenger result
        chat_calls.clear()
        r2 = svc.test_connection(db, "groq", http=groq, target="primary")
        assert chat_calls == ["llama-x"] and r2["connected"] and svc.serialize(db, "groq")["display"]["label"] == "Connected"
        # a model that is not in the account's list fails with a useful message
        svc.save(db, "groq", settings={"model": "missing-model"}, secrets={}, user_id=None)
        res = svc.test_connection(db, "groq", http=groq)
        assert not res["connected"] and res["subtests"]["primary"]["status"] == "FAILED" and "not in your Groq model list" in res["subtests"]["primary"]["detail"]
        # 200 but not JSON → fail (JSON mode is required)
        svc.save(db, "groq", settings={"model": "llama-x"}, secrets={}, user_id=None)
        bad = _client(lambda req: httpx.Response(200, json={"data": [{"id": "llama-x"}, {"id": "other"}]}) if req.url.path.endswith("/models")
                      else httpx.Response(200, json={"choices": [{"message": {"content": "sure! here you go"}}]}))
        res = svc.test_connection(db, "groq", http=bad, target="primary")
        assert res["subtests"]["primary"]["status"] == "FAILED" and "valid JSON" in res["subtests"]["primary"]["detail"]
        # 429 and timeouts are reported, not hidden
        limited = _client(lambda req: httpx.Response(200, json={"data": [{"id": "llama-x"}]}) if req.url.path.endswith("/models")
                          else httpx.Response(429, json={"error": {"message": "rate limit"}}))
        res = svc.test_connection(db, "groq", http=limited, target="primary")
        assert res["subtests"]["primary"]["status"] == "FAILED" and "Rate limit" in res["subtests"]["primary"]["hint"]

        def boom(req):
            if req.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [{"id": "llama-x"}]})
            raise httpx.ReadTimeout("slow")
        res = svc.test_connection(db, "groq", http=_client(boom), target="primary")
        assert "Timed out" in res["subtests"]["primary"]["detail"]
        svc.save(db, "groq", settings={"model": "llama-x", "challenger_model": ""}, secrets={}, user_id=None)
        res = svc.test_connection(db, "groq", http=groq)
        assert res["subtests"]["challenger"]["status"] == "SKIPPED" and res["connected"]  # blank challenger falls back to the primary

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
    monkeypatch.setenv("GOOGLE_ADS_CUSTOMER_ID", "123-456-7890")  # retired: must be ignored
    monkeypatch.setenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "987-654-3210")
    reset_settings_cache()
    try:
        vals = svc.effective("google_ads", None)
        assert "customer_id" not in vals and vals["login_customer_id"] == "9876543210"
    finally:
        monkeypatch.undo()
        reset_settings_cache()
