"""Integrations dashboard: honest statuses (Connected only after a real request), system components, read-only cards, secrets."""
from __future__ import annotations

import json

import httpx
import pytest

from app.db.session import session_scope
from app.models import IntegrationConfig
from app.services import integrations_service as svc
from tests.conftest import Api
from tests.integration.test_integrations_platform import SECRET, _client, _google_handler, _save_ads  # noqa: F401

SUPER = "superadmin@napollo.example"
ANALYST = "analyst@napollo.example"


@pytest.fixture(autouse=True)
def _reset():
    yield
    with session_scope() as db:
        db.query(IntegrationConfig).delete()
    svc.invalidate()


def by(items, key):
    return next(i for i in items if i["provider"] == key)


def test_every_implemented_integration_is_listed_with_the_six_statuses():
    sup = Api(SUPER)
    items = sup.get("/integrations").json()["integrations"]
    assert {i["provider"] for i in items} >= {"google_ads", "groq", "pagespeed", "claude", "places", "ga4", "gbp", "keyword_planner", "crawler",
                                               "smtp", "database", "redis", "google_login"}
    allowed = {"Configured", "Not configured", "Connected", "Connection failed", "Not tested", "Disabled"}
    for i in items:
        assert i["display"]["config"] in {"Configured", "Not configured"} and i["display"]["label"] in allowed, i["provider"]
    assert by(items, "google_ads")["display"]["label"] == "Not configured" and by(items, "groq")["display"]["label"] == "Not configured"


def test_configured_is_not_connected_until_a_real_request_succeeds(monkeypatch):
    monkeypatch.setenv("PAGESPEED_API_KEY", "AIzaFAKEfakeFAKEfakeFAKEfakeFAKE12345")
    from app.core.config import reset_settings_cache
    reset_settings_cache()
    try:
        sup = Api(SUPER)
        ps = by(sup.get("/integrations").json()["integrations"], "pagespeed")
        assert ps["display"] == {"config": "Configured", "connection": "Not tested", "label": "Not tested", "testable": True}  # a key exists ≠ connected
        assert ps["secrets"]["api_key"]["saved"] and "AIza" not in json.dumps(ps)
        with session_scope() as db:
            ok = svc.test_connection(db, "pagespeed", http=_client(lambda r: httpx.Response(200, json={"lighthouseResult": {"categories": {"performance": {"score": 0.73}}}})))
            assert ok["connected"] and "73/100" in ok["steps"][-1]["detail"]
            assert svc.serialize(db, "pagespeed")["display"]["label"] == "Connected"
            svc.test_connection(db, "pagespeed", http=_client(lambda r: httpx.Response(429, json={"error": {"message": "quota"}})))
            s = svc.serialize(db, "pagespeed")
            assert s["display"]["label"] == "Connection failed" and "Quota" in s["last_test_steps"][-1]["hint"]
            svc.test_connection(db, "pagespeed", http=_client(lambda r: httpx.Response(200, json={"lighthouseResult": {"categories": {"performance": {"score": 0.5}}}})))
            svc.set_enabled(db, "pagespeed", False, None)
            assert svc.serialize(db, "pagespeed")["display"]["label"] == "Disabled"
            svc.set_enabled(db, "pagespeed", True, None)
            assert svc.serialize(db, "pagespeed")["display"]["label"] == "Connected"
    finally:
        monkeypatch.undo()
        reset_settings_cache()


def test_crawler_in_fixture_mode_is_never_reported_connected():
    with session_scope() as db:
        res = svc.test_connection(db, "crawler", http=_client(lambda r: httpx.Response(404)))
        assert res["connected"] is False and res["subtests"]["fetch"]["status"] == "SKIPPED"
        assert svc.serialize(db, "crawler")["display"]["label"] == "Not tested"


def test_database_and_redis_are_real_checks():
    sup = Api(SUPER)
    for p in ("database", "redis"):
        r = sup.post(f"/integrations/{p}/test").json()
        assert r["connected"] is True and r["integration"]["display"]["label"] == "Connected", p
    db_card = by(sup.get("/integrations").json()["integrations"], "database")
    assert db_card["readonly"] and db_card["info"]["host"] and "password" not in json.dumps(db_card).lower()
    assert sup.c.put("/api/v1/integrations/database", json={"settings": {}}, headers=sup._h()).status_code in (400, 422)  # env-only
    assert sup.post("/integrations/redis/disconnect").status_code in (400, 422)


def test_google_sign_in_card_is_read_only_and_cannot_be_tested():
    sup = Api(SUPER)
    g = by(sup.get("/integrations").json()["integrations"], "google_login")
    assert g["readonly"] and g["display"]["testable"] is False and g["info"]["auth_provider"] == "mock"
    r = sup.post("/integrations/google_login/test")
    assert r.status_code in (400, 422) and "signing in" in r.text


def test_smtp_test_is_a_real_connection(monkeypatch):
    import smtplib

    class FakeSMTP:
        def __init__(self, host, port, timeout=0): self.host = host
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): return (250, b"ok")
        def has_extn(self, name): return True
        def starttls(self): return (220, b"go")
        def login(self, u, p): raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
    from app.core.config import reset_settings_cache
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p-fake-secret")
    reset_settings_cache()
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    try:
        with session_scope() as db:
            res = svc.test_connection(db, "smtp")
            assert not res["connected"] and any("SMTPAuthenticationError" in s["detail"] for s in res["steps"])
            assert "p-fake-secret" not in json.dumps(res)
    finally:
        monkeypatch.undo()
        reset_settings_cache()


def test_keyword_planner_runs_a_real_readonly_request_and_explains_access_errors():
    def handler(req):
        if "oauth2.googleapis.com" in str(req.url):
            return httpx.Response(200, json={"access_token": "ya29.fake", "scope": "adwords"})
        assert "developer-token" not in {k.lower() for k in req.headers}
        if str(req.url).endswith(":generateKeywordIdeas"):
            return httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED", "message": "x", "details": [{"errors": [{"errorCode": {"authorizationError": "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION"}}]}]}})
        return httpx.Response(404)
    with session_scope() as db:
        _save_ads(db)
        res = svc.test_connection(db, "keyword_planner", http=_client(handler))
        assert not res["connected"] and "Test access" in res["steps"][-1]["hint"]
        ok = svc.test_connection(db, "keyword_planner", http=_client(lambda r: httpx.Response(200, json={"access_token": "t"}) if "oauth2" in str(r.url)
                                                                       else httpx.Response(200, json={"results": [{"text": "plumber near me"}]})))
        assert ok["connected"] and svc.serialize(db, "keyword_planner")["display"]["label"] == "Connected"


def test_test_endpoint_accepts_targets_and_stores_results_for_the_dashboard(monkeypatch):
    sup = Api(SUPER)
    r = sup.post("/integrations/google_ads/test?target=nonsense")
    assert r.status_code in (400, 422)
    r = sup.post("/integrations/google_ads/test?target=client").json()  # nothing configured: honest failure, not success
    assert r["connected"] is False and r["integration"]["display"]["label"] == "Not configured"
    assert Api(ANALYST).post("/integrations/google_ads/test").status_code == 403
    assert Api(ANALYST).get("/integrations").status_code == 200


def test_responses_and_activity_log_never_contain_secrets():
    with session_scope() as db:
        _save_ads(db)
        svc.test_connection(db, "google_ads", http=_client(_google_handler()))
    sup = Api(SUPER)
    blobs = [sup.get("/integrations").text, sup.get("/integrations/health").text, sup.get("/integrations/google_ads").text, sup.get("/activity").text]
    for blob in blobs:
        for secret in (SECRET, "1//0refresh-abc-def-ghi", "ya29.fake"):
            assert secret not in blob
