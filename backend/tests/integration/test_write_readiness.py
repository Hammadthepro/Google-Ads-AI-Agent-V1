"""Real-write authorization model: every key is visible, each is controlled separately, and nothing here writes to Google Ads."""
from __future__ import annotations

from sqlalchemy import select

from app.adapters.google_ads import real as real_adapter
from app.core.config import get_settings
from app.db.session import session_scope
from app.models import ActivityLog, GoogleAdsAccount
from app.services import settings_service, write_readiness
from tests.conftest import ADMIN, SUPER, Api


def _account(manager=False):
    with session_scope() as db:
        a = db.scalar(select(GoogleAdsAccount).where(GoogleAdsAccount.is_manager.is_(manager), GoogleAdsAccount.status == "ENABLED").limit(1))
        return a.id if a else None


def test_readiness_lists_every_key_and_is_not_ready_by_default():
    out = Api(ADMIN).get("/google-ads/write-readiness").json()
    keys = [c["key"] for c in out["checks"]]
    assert keys == ["deployment_permit", "connection", "mode", "super_admin", "kill_switches", "account", "google_access"]
    assert out["ready"] is False and "Real Google Ads writes are off" in out["summary"]
    assert next(c for c in out["checks"] if c["key"] == "deployment_permit")["status"] == "missing"
    assert next(c for c in out["checks"] if c["key"] == "google_access")["status"] == "unverified"  # only Google can confirm it
    assert all(c["action"] for c in out["checks"] if c["status"] == "missing")


def test_only_a_super_admin_can_allow_an_account_and_needs_a_reason():
    aid = _account()
    assert Api(ADMIN).c.put(f"/api/v1/ads-accounts/{aid}/write-allowlist", json={"allowed": True, "reason": "x" * 30},
                            headers=Api(ADMIN)._h()).status_code == 403
    sup = Api(SUPER)
    assert sup.c.put(f"/api/v1/ads-accounts/{aid}/write-allowlist", json={"allowed": True, "reason": "short"}, headers=sup._h()).status_code == 422
    r = sup.c.put(f"/api/v1/ads-accounts/{aid}/write-allowlist", json={"allowed": True, "reason": "Approved by the client for the spring launch"},
                  headers=sup._h())
    try:
        assert r.status_code == 200 and r.json()["writes_allowed"] is True
        chk = next(c for c in sup.get(f"/google-ads/write-readiness?account_id={aid}").json()["checks"] if c["key"] == "account")
        assert chk["status"] == "met"
        with session_scope() as db:
            assert db.scalar(select(ActivityLog).where(ActivityLog.action == "ads_account.write_allowlist", ActivityLog.entity_id == aid))
    finally:
        sup.c.put(f"/api/v1/ads-accounts/{aid}/write-allowlist", json={"allowed": False}, headers=sup._h())


def test_a_manager_account_can_never_be_allowed_for_writes():
    mid = _account(manager=True)
    if mid is None:
        return
    sup = Api(SUPER)
    r = sup.c.put(f"/api/v1/ads-accounts/{mid}/write-allowlist", json={"allowed": True, "reason": "x" * 30}, headers=sup._h())
    assert r.status_code == 422


def test_read_only_mode_blocks_the_real_adapter_even_with_every_other_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "GOOGLE_ADS_WRITES_ENABLED", True)
    with session_scope() as db:
        before = settings_service.get_value("real_writes_authorized", False, db)
        settings_service.set_value(db, "real_writes_authorized", True, None)
    try:
        ok, why = real_adapter.real_writes_permitted()
        assert ok is False and "read-only mode" in why
    finally:
        with session_scope() as db:
            settings_service.set_value(db, "real_writes_authorized", before, None)


def test_readiness_never_calls_google():
    class Boom:
        def __getattr__(self, name):
            raise AssertionError("readiness must not touch the Google Ads adapter")
    from app.adapters import registry
    registry.override("google_ads", Boom())
    try:
        with session_scope() as db:
            out = write_readiness.readiness(db, None)
        assert out["ready"] is False
    finally:
        from tests.fakes.google_ads import MockGoogleAdsAdapter
        registry.override("google_ads", MockGoogleAdsAdapter())
