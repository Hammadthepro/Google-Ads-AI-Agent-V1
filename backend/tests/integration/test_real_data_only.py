"""The application runs on real integrations only: no mock selection, no silent fallback, honest data freshness.

Tests marked `real_registry` run WITHOUT the test fakes, so the real configuration gate is exercised.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import select

from app.adapters import registry
from app.adapters.base import AuthError, RateLimited
from app.adapters.google_ads.real import RealGoogleAdsAdapter
from app.core.errors import IntegrationNotConfigured
from app.db.session import session_scope
from app.models import Client, GoogleAdsAccount, JobRun, PerformanceSnapshot
from app.services import integrations_service as svc
from app.services import monitoring
from tests.conftest import SUPER, Api

APP = Path(__file__).resolve().parents[2] / "app"


# --------------------------------------------------------------------------- the registry never builds a mock
@pytest.mark.real_registry
@pytest.mark.parametrize("name,provider", [("google_ads", "google_ads"), ("ai", "ai_main"), ("challenger_ai", "ai_challenger"),
                                           ("places", "places"),
                                           ("keyword_research", "google_ads")])
def test_unconfigured_integrations_raise_instead_of_falling_back(name, provider):
    with pytest.raises(IntegrationNotConfigured) as exc:
        registry.get(name)
    assert exc.value.provider == provider
    assert "Please configure and test your connection on the Integrations page" in exc.value.message


@pytest.mark.real_registry
def test_credential_free_integrations_are_still_real():
    assert registry.get("crawler").source == "LIVE" and registry.get("crawler").name == "LiveCrawlerAdapter"
    assert registry.get("pagespeed").source == "LIVE"
    assert registry.get("notifier").name == "InAppNotifier"  # no SMTP: in-app only, and it says so


@pytest.mark.real_registry
def test_api_returns_a_clear_424_not_fake_data_when_google_ads_is_missing():
    sup = Api(SUPER)
    r = sup.post("/ads-accounts/discover")  # a Google Ads dependent endpoint
    assert r.status_code == 424
    err = r.json()["error"]
    assert err["code"] == "integration_not_configured" and err["details"]["provider"] == "google_ads"
    assert err["message"] == "Google Ads API integration is not configured. Please configure and test your connection on the Integrations page."


@pytest.mark.real_registry
def test_disabled_integration_is_not_silently_replaced():
    with session_scope() as db:
        svc.save(db, "groq", settings={"model": "m1"}, secrets={"api_key": "gsk_testkey12345"}, user_id=None)
    svc.invalidate()
    assert registry.get("ai").source == "LIVE"
    with session_scope() as db:
        svc._row(db, "groq").status = "DISABLED"
    svc.invalidate()
    try:
        with pytest.raises(IntegrationNotConfigured) as exc:
            registry.get("ai")
        assert exc.value.details["disabled"] is True
    finally:
        with session_scope() as db:
            svc.disconnect(db, "groq", None)
            svc._row(db, "groq").settings = {}  # disconnect keeps non-secret settings; leave no trace for other tests


def test_no_mock_code_or_fake_imports_in_the_application_package():
    assert not [p for p in APP.rglob("*.py") if p.name.startswith("mock") or "mock_world" in p.parts]
    offenders = []
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name.startswith(("Mock", "Fixture", "Fake")):
                offenders.append((path.name, node.name))
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            offenders += [(path.name, n) for n in names if n.startswith(("tests", "app.mock_world"))]
    # MockFailureInjection & friends are legacy table models kept only so old databases still migrate; nothing reads them at runtime.
    legacy = {n for n in ("MockCustomer", "MockResource", "MockMetricDaily", "MockSearchTerm", "MockCallRecord", "MockChangeEvent", "MockFailureInjection")}
    assert [o for o in offenders if o[1] not in legacy] == [], offenders


# --------------------------------------------------------------------------- real adapter: retries, safe errors
class _Boom(Exception):
    pass


def _ads_with(search_stream):
    class Svc:
        pass
    svc_ = Svc()
    svc_.search_stream = search_stream
    client = NS(get_service=lambda name: svc_)
    ads = RealGoogleAdsAdapter(client_factory=lambda: client)
    ads._sleep = lambda s: sleeps.append(s)
    return ads


sleeps: list[float] = []


def test_reads_back_off_exponentially_on_rate_limits_then_succeed():
    sleeps.clear()
    calls = {"n": 0}

    def stream(customer_id, query):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Boom("RESOURCE_EXHAUSTED: Too many requests")
        return [NS(results=[NS(customer=NS(id=1, descriptive_name="Acme", currency_code="USD", time_zone="UTC", manager=False, auto_tagging_enabled=True))])]
    out = _ads_with(stream).get_customer("111-222-3333")
    assert out["descriptive_name"] == "Acme" and calls["n"] == 3 and sleeps == [1.0, 2.0]


def test_rate_limit_that_never_clears_raises_a_safe_message():
    sleeps.clear()

    def stream(customer_id, query):
        raise _Boom("RESOURCE_EXHAUSTED: quota detail with internal ids 8f3a")
    with pytest.raises(RateLimited) as exc:
        _ads_with(stream).get_customer("1112223333")
    assert sleeps == [1.0, 2.0, 4.0]
    assert "8f3a" not in exc.value.message and "quota detail" not in exc.value.message  # technical text stays in the log


def test_expired_authorization_is_reported_on_the_integrations_page():
    with session_scope() as db:
        svc.save(db, "google_ads", settings={"client_id": "1-a.apps.googleusercontent.com"}, secrets={"client_secret": "s", "refresh_token": "1//r"}, user_id=None)
        svc._row(db, "google_ads").status = "CONNECTED"

    def stream(customer_id, query):
        raise _Boom("invalid_grant: Token has been expired or revoked.")
    try:
        with pytest.raises(AuthError) as exc:
            _ads_with(stream).get_customer("1112223333")
        assert "Reconnect Google Ads" in exc.value.message and "invalid_grant" not in exc.value.message
        with session_scope() as db:
            card = svc.serialize(db, "google_ads")
            assert card["status"] == "AUTHORIZATION_EXPIRED" and card["display"]["label"] == "Connection failed"
    finally:
        with session_scope() as db:
            svc.disconnect(db, "google_ads", None)


def test_test_access_only_project_gets_the_exact_fix_not_a_generic_error():
    def stream(customer_id, query):
        raise _Boom("PERMISSION_DENIED: CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION")
    from app.adapters.base import PermissionDenied
    with pytest.raises(PermissionDenied) as exc:
        _ads_with(stream).get_customer("1112223333")
    assert "not a test account" in exc.value.message


def test_adapter_errors_reach_the_user_as_safe_messages():
    from fastapi.testclient import TestClient

    from app.main import create_app
    app = create_app()

    @app.get("/boom-auth")
    def boom_auth():
        raise AuthError("secret-token-abc leaked in vendor text", adapter="X")
    r = TestClient(app, raise_server_exceptions=False).get("/boom-auth")
    assert r.status_code == 424 and "secret-token-abc" not in r.text and r.json()["error"]["code"] == "authorization_failed"


# --------------------------------------------------------------------------- data freshness: current / historical / missing
def _client_with_account(db):
    return next(c for c in db.scalars(select(Client)) if db.scalar(select(GoogleAdsAccount.id).where(GoogleAdsAccount.client_id == c.id)))


@pytest.mark.real_registry
def test_data_status_says_not_configured_and_daily_sync_is_skipped_not_failed(db):
    c = _client_with_account(db)
    st = monitoring.data_status(db, c.id)
    assert st["state"] == "not_configured" and "Integrations page" in st["message"]
    res = monitoring.run_daily_all(db)
    assert "skipped" in res
    assert db.scalar(select(JobRun).where(JobRun.status == "NOT_CONFIGURED")) is not None


def test_data_status_marks_data_historical_when_a_refresh_fails_and_keeps_the_original_timestamp(db):
    c = _client_with_account(db)
    acct = db.scalar(select(GoogleAdsAccount).where(GoogleAdsAccount.client_id == c.id))
    monitoring.daily_sync(db, acct)
    db.commit()
    ok = monitoring.data_status(db, c.id)
    assert ok["state"] == "data_available" and ok["retrieved_at"]
    from tests.conftest import inject
    inject("google_ads", "outage", "get_metrics", times=1)
    before = db.scalar(select(PerformanceSnapshot.id).limit(1))
    res = monitoring.daily_sync(db, acct)
    db.commit()
    assert res["status"] == "FAILED"
    st = monitoring.data_status(db, c.id)
    assert st["state"] == "historical" and st["retrieved_at"] == ok["retrieved_at"]  # original retrieval time, not the failed attempt
    assert "latest refresh failed" in st["message"]
    assert db.get(PerformanceSnapshot, before) is not None  # previously retrieved data is preserved
    again = monitoring.daily_sync(db, acct)  # retry succeeds -> current again
    db.commit()
    assert again["status"] == "SUCCEEDED" and monitoring.data_status(db, c.id)["state"] == "data_available"


def test_auth_failure_during_sync_is_recorded_with_a_specific_status(db):
    from tests.conftest import inject
    c = _client_with_account(db)
    acct = db.scalar(select(GoogleAdsAccount).where(GoogleAdsAccount.client_id == c.id))
    inject("google_ads", "auth_failure", "get_metrics", times=1)
    assert monitoring.daily_sync(db, acct)["status"] == "AUTH_FAILED"
    db.commit()
    monitoring.daily_sync(db, acct)
    db.commit()


# --------------------------------------------------------------------------- demo-data purge
def test_purge_demo_data_is_a_dry_run_by_default_and_never_matches_real_clients(db):
    from app.services import demo_purge
    from tests.fakes.mock_world import seed  # noqa: F401 - the seeded world IS demo data
    rep = demo_purge.purge(db, apply=False)
    assert rep["applied"] is False and rep["demo_clients"], "the test database holds the fixture world"
    real = Client(name="Real Plumbing Co")
    db.add(real)
    db.flush()
    assert real.id not in demo_purge.demo_client_ids(db)
    db.rollback()


# --------------------------------------------------------------------------- a full audit with NOTHING configured stays honest
@pytest.mark.real_registry
def test_audit_report_completes_honestly_when_integrations_are_missing():
    """Only the credential-free crawler/pagespeed are present (as test doubles, to avoid network). Google Ads, Places and the
    AI providers are not configured: the report must complete, mark those checks unavailable with the actionable message,
    and never invent ratings, keyword volumes or AI-derived findings."""
    from tests.fakes.crawler import FixtureCrawlerAdapter
    from tests.fakes.pagespeed import MockPageSpeedAdapter
    registry.override("crawler", FixtureCrawlerAdapter())
    registry.override("pagespeed", MockPageSpeedAdapter())
    admin = Api(SUPER)
    client = next(c for c in admin.get("/clients").json() if c["name"] == "Northside Home Services")
    r = admin.post(f"/clients/{client['id']}/audit-reports?sync=true&business_id={client['businesses'][0]['id']}")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["status"] == "COMPLETED", rep.get("error")
    findings = [i for sec in rep["sections"].values() for i in sec["findings"]]
    unavailable = [i for i in findings if i["status"] == "UNAVAILABLE"]
    assert unavailable, "missing integrations must show up as unavailable checks"
    assert any("Integrations page" in (i.get("detail") or i.get("evidence_text") or str(i)) for i in unavailable)
    gbp = rep["sections"]["gbp"]["findings"]
    assert not any(i["status"] == "PASS" and "rating" in i["check"].lower() for i in gbp), "no review/rating result without Google Places"
    kw = rep["sections"]["keywords"]["report"]
    assert not any((k.get("volume") or 0) > 0 for k in (kw.get("rows") or kw.get("keywords") or [])), "no invented search volumes"
