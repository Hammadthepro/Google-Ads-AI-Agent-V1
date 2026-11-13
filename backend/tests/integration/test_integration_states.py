"""Integration states must be truthful: no test is recorded without credentials, a test says whether the provider was contacted and
whether authentication worked, keyless providers are not shown as 'credentials present', and sign-in is not a health check."""
from __future__ import annotations

import httpx
from sqlalchemy import select

from app.db.session import session_scope
from app.models import IntegrationConfig
from app.services import integrations_service as svc
from tests.conftest import SUPER, Api


def _card(name):
    return next(i for i in Api(SUPER).get("/integrations").json()["integrations"] if i["provider"] == name)


def test_testing_tavily_without_a_key_records_no_test_and_says_why():
    with session_scope() as db:
        out = svc.test_connection(db, "tavily", http=httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError("no request")))))
        assert out["attempted"] is False and out["summary"]["contacted"] is False and out["tested_at"] is None
        assert "TAVILY_API_KEY" in out["steps"][0]["hint"]
        assert db.scalar(select(IntegrationConfig).where(IntegrationConfig.provider == "tavily")) is None  # nothing persisted
    c = _card("tavily")
    assert c["display"]["label"] == "Not configured" and c["last_test_at"] is None and c["missing"] == ["api_key"]


def test_a_rejected_key_is_reported_as_contacted_but_not_authenticated(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-not-a-real-key")
    from app.core.config import reset_settings_cache
    reset_settings_cache()
    try:
        with session_scope() as db:
            out = svc.test_connection(db, "tavily", http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"detail": "bad key"}))))
            s = out["summary"]
            assert s["credentials_present"] and s["contacted"] and s["requests"] == 1 and s["authenticated"] is False
            assert s["capability_available"] is False and "auth_failure" in s["failure_reason"]
            card = svc.serialize(db, "tavily")
            assert card["display"]["label"] == "Connection failed" and card["test"]["contacted"] and card["last_success_at"] is None
            db.delete(db.scalar(select(IntegrationConfig).where(IntegrationConfig.provider == "tavily")))
    finally:
        monkeypatch.delenv("TAVILY_API_KEY")
        reset_settings_cache()


def test_geoapify_is_a_registered_provider_that_is_simply_not_configured():
    c = _card("geoapify")
    assert c["display"]["label"] == "Not configured" and c["missing"] == ["api_key"] and not c["secrets"]["api_key"]["saved"]
    from app.adapters import registry
    assert registry.optional("geoapify") is None  # nothing borrows another provider's key


def test_openstreetmap_needs_no_credentials_and_is_only_connected_after_a_real_test():
    c = _card("openstreetmap")
    assert c["display"]["config"] == "No credentials needed" and c["display"]["label"] in ("Configured, not tested", "Connected", "Connection failed")
    assert c["provider"] != "geoapify" and _card("geoapify")["display"]["config"] == "Not configured"  # separate providers, separate states


def test_google_sign_in_is_never_shown_as_connected():
    c = _card("google_login")
    assert c["display"]["testable"] is False and c["display"]["label"] != "Connected"
