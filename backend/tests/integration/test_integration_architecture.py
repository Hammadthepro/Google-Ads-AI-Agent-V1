"""Integration management architecture: provenance and precedence, dependencies, discovery, system integrations, isolation, permissions.

HTTP to Google/Anthropic/SMTP is replaced by stand-in transports ONLY inside these tests; the application code paths are the real ones.
Checks against the real services are in backend/live_tests.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.adapters import registry
from app.core.config import reset_settings_cache
from app.core.errors import IntegrationNotConfigured
from app.db.session import session_scope
from app.models import ActivityLog, IntegrationConfig, Workspace
from app.services import integrations_service as svc
from tests.conftest import ADMIN, ANALYST, MANAGER, SUPER, Api
from tests.integration.test_integrations_platform import SECRET, _client, _google_handler, _save_ads  # noqa: F401 (shared stand-in Google)


@pytest.fixture(autouse=True)
def _clean():
    from app.services import mcc_service
    with session_scope() as db:  # the seeded fake world selected a manager; these tests choose their own
        ws = mcc_service.default_workspace(db)
        saved_manager, ws.google_ads_manager_customer_id = ws.google_ads_manager_customer_id, ""
    svc.invalidate()
    yield
    with session_scope() as db:
        mcc_service.default_workspace(db).google_ads_manager_customer_id = saved_manager
    with session_scope() as db:
        db.query(IntegrationConfig).delete()
    svc.invalidate()


# --------------------------------------------------------------------------- precedence and provenance (the reported "Missing: client_id" family)
def _card(sup, p="google_ads"):
    return sup.get(f"/integrations/{p}").json()


def test_reported_setup_secret_and_token_from_environment_client_id_typed_in_the_dashboard(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_CLIENT_SECRET", "S" * 24)
    monkeypatch.setenv("GOOGLE_ADS_REFRESH_TOKEN", "1//" + "r" * 40)
    monkeypatch.delenv("GOOGLE_ADS_CLIENT_ID", raising=False)
    reset_settings_cache()
    svc.invalidate()
    try:
        sup = Api(SUPER)
        c = _card(sup)
        assert c["missing"] == ["client_id"] and c["display"]["label"] == "Not configured"
        assert c["settings"]["client_id"]["source"] == "missing" and c["settings"]["client_id"]["required"] is True
        assert {k: v["source"] for k, v in c["secrets"].items()} == {"client_secret": "environment", "refresh_token": "environment"}
        r = sup.c.put("/api/v1/integrations/google_ads", json={"settings": {"client_id": "609437209123-abc.apps.googleusercontent.com"}, "secrets": {}}, headers=sup._h())
        assert r.status_code == 200
        c = _card(sup)
        assert c["missing"] == [] and c["settings"]["client_id"]["source"] == "saved" and c["display"]["label"] == "Not tested"
        assert c["secrets"]["client_secret"]["source"] == "environment"  # environment secrets are untouched by the save
        assert "S" * 24 not in json.dumps(c) and "1//" + "r" * 10 not in json.dumps(c)
    finally:
        monkeypatch.undo()
        reset_settings_cache()
        svc.invalidate()


def test_precedence_saved_beats_environment_and_blanks_never_override(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_CLIENT_ID", "111-env.apps.googleusercontent.com")
    reset_settings_cache()
    try:
        with session_scope() as db:
            assert svc.effective("google_ads", None)["client_id"] == "111-env.apps.googleusercontent.com"  # environment only
            svc.save(db, "google_ads", settings={"client_id": "222-db.apps.googleusercontent.com"}, secrets={}, user_id=None)
            assert svc.effective("google_ads", svc._row(db, "google_ads"))["client_id"] == "222-db.apps.googleusercontent.com"  # database wins
            svc.save(db, "google_ads", settings={"client_id": ""}, secrets={}, user_id=None)  # a blank value for a REQUIRED setting…
            assert svc.effective("google_ads", svc._row(db, "google_ads"))["client_id"] == "222-db.apps.googleusercontent.com"  # …never wipes the saved one
        monkeypatch.setenv("GOOGLE_ADS_CLIENT_ID", "")  # an EMPTY environment variable…
        reset_settings_cache()
        with session_scope() as db:
            assert svc.effective("google_ads", svc._row(db, "google_ads"))["client_id"] == "222-db.apps.googleusercontent.com"  # …never overrides the database
            assert svc.missing("google_ads", svc._row(db, "google_ads")) == ["client_secret", "refresh_token"]  # and no misleading client_id gap
    finally:
        monkeypatch.undo()
        reset_settings_cache()


def test_credentials_that_cannot_be_decrypted_are_reported_not_silently_missing(monkeypatch):
    with session_scope() as db:
        _save_ads(db)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Zm9yLXRlc3RpbmctYS1kaWZmZXJlbnQta2V5LTAwMDAwMDA=")
    reset_settings_cache()
    try:
        card = _card(Api(SUPER))
        assert card["credentials_unreadable"] is True and "client_secret" in card["missing"]
    finally:
        monkeypatch.undo()
        reset_settings_cache()


def test_default_manager_that_is_a_client_account_is_rejected_with_a_clear_reason():
    with session_scope() as db:
        _save_ads(db, login_customer_id="111-222-3334")  # 1112223334 is a client account in the stand-in Google
        res = svc.test_connection(db, "google_ads", http=_client(_google_handler()))
        hier = res["subtests"]["hierarchy"]
        assert hier["status"] == "FAILED" and "not a manager" in hier["detail"] and "Choose a manager" in hier["hint"]


def test_default_manager_that_this_google_user_cannot_reach_is_reported_before_any_request_fails_obscurely():
    with session_scope() as db:
        _save_ads(db, login_customer_id="999-888-7777")
        res = svc.test_connection(db, "google_ads", http=_client(_google_handler()))
        hier = res["subtests"]["hierarchy"]
        assert hier["status"] == "FAILED" and "not among the 3 account(s)" in hier["detail"] and "directly accessible" in hier["hint"]


# --------------------------------------------------------------------------- dependencies
def test_ga4_is_blocked_until_the_shared_oauth_client_and_their_own_authorisation_exist():
    sup = Api(SUPER)
    for p in ("ga4",):
        c = _card(sup, p)
        assert c["display"]["label"] == "Not configured"  # nothing present
        keys = {d["key"]: d["ok"] for d in c["dependencies"]}
        assert keys == {"oauth_client": False, "authorisation": False}
    with session_scope() as db:
        _save_ads(db)
        svc.save(db, "ga4", settings={"property_id": "42"}, secrets={"refresh_token": "1//0ga4"}, user_id=None)
    c = _card(sup, "ga4")
    assert all(d["ok"] for d in c["dependencies"]) and c["display"]["label"] == "Not tested"
    with session_scope() as db:  # remove the shared client: the dependent integration is blocked, even though its own token exists
        row = svc._row(db, "google_ads")
        row.settings = {}
    svc.invalidate()
    c = _card(sup, "ga4")
    assert c["display"]["label"] == "Blocked by dependency" and c["blocked_by"][0]["key"] == "oauth_client" and c["blocked_by"][0]["action"] == "google_ads"


def test_ga4_requires_a_selected_property_and_tests_both_apis():
    calls = []

    def handler(req):
        calls.append(str(req.url))
        if "oauth2.googleapis.com" in str(req.url):
            return httpx.Response(200, json={"access_token": "ya29.fake"})
        if "accountSummaries" in str(req.url):
            return httpx.Response(200, json={"accountSummaries": [{"displayName": "Acme", "propertySummaries": [{"property": "properties/42", "displayName": "Site"}]}]})
        if "runReport" in str(req.url):
            return httpx.Response(200, json={"rows": [{}]})
        return httpx.Response(404)
    with session_scope() as db:
        _save_ads(db)
        svc.save(db, "ga4", settings={}, secrets={"refresh_token": "1//0ga4"}, user_id=None)
        assert svc.missing("ga4", svc._row(db, "ga4")) == ["property_id"]
        opts = svc.options(db, "ga4", http=_client(handler))
        assert opts["ok"] and opts["properties"] == [{"property": "42", "name": "Site", "account": "Acme"}]
        svc.save(db, "ga4", settings={"property_id": "999"}, secrets={}, user_id=None)
        bad = svc.test_connection(db, "ga4", http=_client(handler))
        assert not bad["connected"] and "not visible" in " ".join(s["detail"] for s in bad["steps"])  # a typed ID that the user cannot read
        svc.save(db, "ga4", settings={"property_id": "properties/42"}, secrets={}, user_id=None)
        good = svc.test_connection(db, "ga4", http=_client(handler))
        assert good["connected"] and any("analyticsdata" in c for c in calls)  # the Data API was really called


def test_keyword_planner_dependency_chain_is_explicit():
    with session_scope() as db:
        from app.services import mcc_service
        ws = mcc_service.default_workspace(db)
        saved = ws.google_ads_manager_customer_id
        ws.google_ads_manager_customer_id = ""
        db.query(__import__("app.models", fromlist=["GoogleAdsAccount"]).GoogleAdsAccount).update({"status": "CLOSED"})
        card = svc.serialize(db, "keyword_planner")
        deps = {d["key"]: d["ok"] for d in card["dependencies"]}
        assert deps["google_ads"] is False and deps["manager"] is False and deps["research_customer"] is False
        assert card["display"]["label"] in ("Not configured", "Blocked by dependency")
        db.query(__import__("app.models", fromlist=["GoogleAdsAccount"]).GoogleAdsAccount).update({"status": "ENABLED"})
        ws.google_ads_manager_customer_id = saved


# --------------------------------------------------------------------------- Claude
def test_claude_models_come_from_the_api_and_the_test_makes_a_real_completion():
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path))
        if req.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "claude-opus-5-5"}, {"id": "claude-sonnet-5-5"}]})
        if req.url.path == "/v1/messages":
            return httpx.Response(200, json={"content": [{"type": "text", "text": "OK"}], "usage": {"input_tokens": 9, "output_tokens": 2}})
        return httpx.Response(404)
    with session_scope() as db:
        svc.save(db, "claude", settings={"model": "claude-opus-5-5"}, secrets={"api_key": "sk-ant-FAKE-KEY-0123456789"}, user_id=None)
        assert svc.options(db, "claude", http=_client(handler))["models"] == ["claude-opus-5-5", "claude-sonnet-5-5"]
        res = svc.test_connection(db, "claude", http=_client(handler))
        assert res["connected"] and ("POST", "/v1/messages") in seen and "9 in / 2 out" in res["steps"][-1]["detail"]
        svc.save(db, "claude", settings={"model": "claude-made-up"}, secrets={}, user_id=None)
        bad = svc.test_connection(db, "claude", http=_client(handler))
        assert not bad["connected"] and "not in the list" in bad["steps"][-1]["detail"]
        usage = svc.serialize(db, "claude")["info"]["usage"]
        assert set(usage) >= {"runs", "tokens_in", "tokens_out", "cost_note"}


def test_dual_ai_falls_back_to_the_single_available_draft_when_claude_is_not_configured(monkeypatch):
    from app.services import competition
    from tests.integration.test_ai_competition import _run
    real_get = registry.get

    def get(name):
        if name == "claude_ai":
            raise IntegrationNotConfigured("claude", "Claude (Anthropic)", missing=["api_key"])
        return real_get(name)
    monkeypatch.setattr(registry, "get", get)
    res, _ = _run()
    c = res["competition"]
    assert c["mode"] == "single" and "Claude (Anthropic) integration is not configured" in c["errors"]["claude"]
    assert c["providers"]["claude"]["source"] == "NONE" and c["providers"]["internal"]["model"]  # no pretend second opinion
    assert "second AI unavailable" in c["degraded_reason"] or "not configured" in c["degraded_reason"]
    monkeypatch.setattr(registry, "get", lambda name: (_ for _ in ()).throw(IntegrationNotConfigured(name, name)))
    with pytest.raises(IntegrationNotConfigured):
        competition._providers()  # both missing: nothing is faked


# --------------------------------------------------------------------------- SMTP (database-backed, encrypted)
class _SMTP:
    log: list = []

    def __init__(self, host, port, timeout=0, context=None):
        self.host, self.port = host, port
        _SMTP.log.append(("connect", host, port))

    def ehlo(self): return (250, b"ok")
    def starttls(self, context=None): _SMTP.log.append(("starttls",)); return (220, b"go")
    def login(self, u, p): _SMTP.log.append(("login", u, p)); return (235, b"ok")
    def send_message(self, msg): _SMTP.log.append(("send", msg["To"], msg["From"]))
    def quit(self): return (221, b"bye")


def test_smtp_is_configured_in_the_dashboard_encrypted_masked_tested_and_used_for_alerts(monkeypatch):
    import smtplib
    _SMTP.log = []
    monkeypatch.setattr(smtplib, "SMTP", _SMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _SMTP)
    sup = Api(SUPER)
    body = {"settings": {"host": "smtp.example.test", "port": "587", "security": "starttls", "username": "alerts", "from_email": "alerts@example.test", "from_name": "Ads Agent"},
            "secrets": {"password": "pw-SECRET-123456"}}
    assert sup.c.put("/api/v1/integrations/smtp", json=body, headers=sup._h()).status_code == 200
    with session_scope() as db:
        assert "pw-SECRET" not in db.query(IntegrationConfig).filter_by(provider="smtp").one().secrets_ciphertext  # encrypted at rest
    card = _card(sup, "smtp")
    assert "pw-SECRET" not in json.dumps(card) and card["secrets"]["password"]["source"] == "saved" and card["deployment_managed"] is False
    assert sup.c.put("/api/v1/integrations/smtp", json={"settings": {"security": "plaintext"}}, headers=sup._h()).status_code == 422
    t = sup.post("/integrations/smtp/test").json()
    assert t["connected"] and ("starttls",) in _SMTP.log and ("login", "alerts", "pw-SECRET-123456") in _SMTP.log
    sent = sup.post("/integrations/smtp/test-email", {"to": "admin@example.test"}).json()
    assert sent == {"sent": True, "to": "admin@example.test"} and any(e[0] == "send" and "Ads Agent" in e[2] for e in _SMTP.log)
    assert sup.post("/integrations/smtp/test-email", {"to": "not-an-email"}).status_code == 422
    registry.reset()
    n = registry.notifier()
    assert n.name == "EmailNotifier"  # configured in the database → alerts are emailed
    assert n.send(recipients=["ops@example.test"], subject="x", body="y") == "SENT"
    assert "pw-SECRET" not in json.dumps(sup.get("/activity").json())


def test_without_smtp_alerts_stay_in_the_app_and_say_so():
    registry.reset()
    n = registry.notifier()
    assert n.name == "InAppNotifier" and n.send(recipients=["ops@example.test"], subject="x", body="y") == "NOT_EMAILED"


# --------------------------------------------------------------------------- system integrations
def test_database_and_redis_checks_are_real_and_the_url_is_never_shown():
    sup = Api(SUPER)
    db_res = sup.post("/integrations/database/test").json()
    names = [s["name"] for s in db_res["steps"]]
    assert db_res["connected"] and {"Query the database", "Write and read back", "Schema version", "Connection pool"} <= set(names)
    redis_res = sup.post("/integrations/redis/test").json()
    assert redis_res["steps"][0]["name"] == "PING" and any(s["name"] == "Broker queue" for s in redis_res["steps"]) and any(s["name"] == "Worker heartbeat" for s in redis_res["steps"])
    text = json.dumps([_card(sup, "database"), _card(sup, "redis"), db_res, redis_res])
    assert "postgresql://" not in text and "redis://" not in text and "app:app" not in text
    for p in ("database", "redis", "google_login"):
        assert _card(sup, p)["deployment_managed"] is True
    assert sup.c.put("/api/v1/integrations/database", json={"settings": {}}, headers=sup._h()).status_code == 422


def test_crawler_test_proves_the_ssrf_guard_and_fetches_a_public_page(monkeypatch):
    from types import SimpleNamespace as NS

    from app.adapters.crawler import LiveCrawlerAdapter
    monkeypatch.setattr(LiveCrawlerAdapter, "fetch", lambda self, url: NS(status=200))
    with session_scope() as db:
        res = svc.test_connection(db, "crawler")
        assert res["connected"] and res["steps"][0]["name"] == "SSRF guard" and "169.254.169.254" in res["steps"][0]["detail"]
        assert svc.serialize(db, "crawler")["settings"] == {} and svc.serialize(db, "crawler")["secrets"] == {}  # no unnecessary fields


# --------------------------------------------------------------------------- isolation, permissions, audit
def test_credentials_are_isolated_per_workspace():
    with session_scope() as db:
        other = Workspace(name="Other team", slug="other")
        db.add(other)
        db.flush()
        other_id = other.id
        svc.save(db, "groq", settings={"model": "model-A"}, secrets={"api_key": "gsk_workspaceA_key_123456"}, user_id=None)
    svc.invalidate()
    with session_scope() as db:
        assert svc.effective("groq", svc._row(db, "groq"))["model"] == "model-A"
        with svc.use_workspace(other_id):
            assert svc._row(db, "groq") is None  # the other workspace sees nothing of A's credentials
            assert svc.resolve("groq").get("api_key") in (None, "") and "model" not in {k for k, v in svc.resolve("groq").items() if v and k == "model" and v == "model-A"}
            svc.save(db, "groq", settings={"model": "model-B"}, secrets={"api_key": "gsk_workspaceB_key_654321"}, user_id=None)
        assert svc.effective("groq", svc._row(db, "groq"))["model"] == "model-A"  # saving in B did not touch A
        assert db.query(IntegrationConfig).filter_by(provider="groq").count() == 2
        with svc.use_workspace(other_id):
            assert svc.effective("groq", svc._row(db, "groq"))["model"] == "model-B"
    with session_scope() as db:
        db.query(IntegrationConfig).delete()
        db.query(Workspace).filter_by(slug="other").delete()


@pytest.mark.parametrize("who", [ADMIN, MANAGER, ANALYST])
def test_only_super_admin_can_change_test_enable_or_disconnect_any_integration(who):
    u, sup = Api(who), Api(SUPER)
    for p in svc.PROVIDERS:
        assert u.c.put(f"/api/v1/integrations/{p}", json={"settings": {}}, headers=u._h()).status_code == 403, p
        assert u.post(f"/integrations/{p}/test").status_code == 403, p
        assert u.post(f"/integrations/{p}/disconnect").status_code == 403, p
        assert u.post(f"/integrations/{p}/enable", {"enabled": True}).status_code == 403, p
        assert u.get(f"/integrations/{p}/options").status_code in (403, 404, 422), p
    assert u.post("/integrations/smtp/test-email", {"to": "a@b.co"}).status_code == 403
    assert u.get("/integrations").status_code == 200  # read-only masked view
    assert sup.get("/integrations").status_code == 200


def test_every_response_is_free_of_plaintext_secrets_and_changes_are_audited_without_values():
    secrets = {"google_ads": {"client_secret": "CS-plain-0001", "refresh_token": "1//RT-plain-0002"}, "groq": {"api_key": "gsk_plain_0003_abcdefgh"},
               "claude": {"api_key": "sk-ant-plain-0004-abcdefgh"}, "places": {"api_key": "AIza-plain-0005-abcdefghij"}, "smtp": {"password": "pw-plain-0006"}}
    sup = Api(SUPER)
    for p, sec in secrets.items():
        assert sup.c.put(f"/api/v1/integrations/{p}", json={"settings": {}, "secrets": sec}, headers=sup._h()).status_code == 200
    blobs = [sup.get("/integrations").text, sup.get("/integrations/health").text, sup.get("/activity").text] + [sup.get(f"/integrations/{p}").text for p in svc.PROVIDERS]
    for blob in blobs:
        for sec in secrets.values():
            for v in sec.values():
                assert v not in blob
    with session_scope() as db:
        logged = db.query(ActivityLog).filter(ActivityLog.action == "integrations.configured").all()
        assert len(logged) >= 5 and all("plain" not in json.dumps(a.details) for a in logged)
        assert {a.entity_id for a in logged} >= set(secrets) and all(a.actor_email for a in logged)


def test_blank_secret_fields_keep_the_existing_secret_and_survive_restart():
    sup = Api(SUPER)
    sup.c.put("/api/v1/integrations/groq", json={"settings": {"model": "m1"}, "secrets": {"api_key": "gsk_keep_me_0123456789"}}, headers=sup._h())
    first = _card(sup, "groq")["secrets"]["api_key"]["fingerprint"]
    sup.c.put("/api/v1/integrations/groq", json={"settings": {"model": "m2"}, "secrets": {"api_key": ""}}, headers=sup._h())
    again = _card(sup, "groq")
    assert again["secrets"]["api_key"]["fingerprint"] == first and again["settings"]["model"]["value"] == "m2"
    svc.invalidate()  # a restart drops every in-memory cache; the database is the source of truth
    registry.reset()
    assert _card(sup, "groq")["secrets"]["api_key"]["fingerprint"] == first


def test_untouched_safety_defaults():
    from app.core.config import get_settings
    s = get_settings()
    assert s.GOOGLE_ADS_WRITES_ENABLED is False and s.AUTO_NEGATIVES_MODE == "shadow" and s.EMERGENCY_BRAKE_WRITE_MODE == "simulate"
