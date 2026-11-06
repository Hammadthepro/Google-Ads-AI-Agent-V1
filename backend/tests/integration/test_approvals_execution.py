"""#19 validation failure, #20 approval workflow, #21 invalidation after payload change, #22-#25 mock execution,
paused-first, read-back verification, reconciliation after timeouts, SEC-W real-write guard, kill switches."""
from __future__ import annotations

import copy

import pytest
from sqlalchemy import select

from app.adapters import registry
from app.adapters.base import WritesDisabled
from app.adapters.google_ads.real import RealGoogleAdsAdapter
from app.core.errors import SafetyBlocked
from app.models import Approval, Business, Campaign, GoogleAdsAccount, MockResource
from app.services import validation
from tests.conftest import ADMIN, BACKUP, MANAGER, SUPER, Api, inject


def _campaign(api, name):
    clients = api.get("/clients").json()
    for c in clients:
        for camp in api.get(f"/clients/{c['id']}/structure").json()["campaigns"]:
            if camp["name"] == name:
                return c["id"], camp
    raise AssertionError(name)


def _budget_task(mgr, client_id, camp, monthly):
    r = mgr.post("/tasks?sync=true", {"task_type": "CHANGE_BUDGET", "client_id": client_id, "campaign_id": camp["id"],
                                      "fields": {"monthly_budget": monthly}})
    assert r.status_code == 200, r.text
    return r.json()


def test_validation_failures_are_reported(db):
    b = db.scalar(select(Business).where(Business.name == "Northside Plumbing"))
    payload = {"kind": "ADD_AD_GROUP", "customer_id": "1110000001", "operations": [
        {"resource": "ad_group", "op": "create", "temp_id": "-1", "fields": {"name": "Septic", "campaign": "customers/1110000001/campaigns/10006", "status": "ENABLED"}},
        {"resource": "ad_group_criterion", "op": "create", "fields": {"ad_group": "temp:-1", "status": "PAUSED", "type": "KEYWORD",
                                                                      "keyword": {"text": "septic tank pumping", "match_type": "BROAD"}}},
        {"resource": "ad_group_ad", "op": "create", "fields": {"ad_group": "temp:-1", "status": "PAUSED", "ad": {
            "final_urls": ["https://www.summitinjurylaw.example/personal-injury"],
            "responsive_search_ad": {"headlines": ["Best Septic Service In The Whole City!", "Call Now", "CHEAPEST"],
                                     "descriptions": ["Septic tank pumping, guaranteed lowest price", "Call (555) 201-4400 today"]}}}},
        {"resource": "campaign", "op": "remove", "resource_name": "customers/1110000001/campaigns/10006"}]}
    v = validation.validate(db, payload, b)
    codes = {e["code"] for e in v.errors}
    assert not v.passed
    assert {"NOT_PAUSED", "MATCH_TYPE", "EXCLUDED_SERVICE", "AD_LENGTH", "POLICY", "UNVERIFIED_CLAIM", "BRAND_RULE",
            "LANDING_PAGE", "DELETE_FORBIDDEN", "EXCLUDED_SERVICE_IN_AD"} <= codes, codes
    db.rollback()


def test_approval_workflow_approve_reject_request_changes():
    admin, mgr = Api(ADMIN), Api(MANAGER)
    cid, camp = _campaign(admin, "Plumbing | Drain Cleaning & Water Heaters | Search")
    t = _budget_task(mgr, cid, camp, camp["monthly_budget"] + 100)
    appr = admin.get(f"/approvals/{t['approval_id']}").json()
    for key in ("current_state", "proposed_state", "reason", "expected_effect", "risks", "validation", "ai_explanation", "requested_by_email",
                "payload_hash", "context"):
        assert key in appr
    assert appr["requested_by_email"] == MANAGER and appr["status"] == "PENDING_APPROVAL"
    assert admin.post(f"/approvals/{appr['id']}/reject", {"reason": ""}).status_code == 422
    r = admin.post(f"/approvals/{appr['id']}/request-changes", {"reason": "Increase smaller, +$50"})
    assert r.json()["status"] == "CHANGES_REQUESTED"
    assert admin.post(f"/approvals/{appr['id']}/approve", {"payload_hash": appr["payload_hash"]}).status_code == 409
    t2 = _budget_task(mgr, cid, camp, camp["monthly_budget"] + 50)
    a2 = admin.get(f"/approvals/{t2['approval_id']}").json()
    r = admin.post(f"/approvals/{a2['id']}/reject", {"reason": "Not this month"})
    assert r.json()["status"] == "REJECTED" and r.json()["decided_by_email"] == ADMIN
    events = admin.get(f"/approvals/{a2['id']}").json()["events"]
    assert [e["event"] for e in events] == ["CREATED", "REJECTED"]


def test_approval_bound_to_exact_payload_version(db):
    admin, mgr = Api(ADMIN), Api(MANAGER)
    cid, camp = _campaign(admin, "Plumbing | Drain Cleaning & Water Heaters | Search")
    t = _budget_task(mgr, cid, camp, camp["monthly_budget"] + 60)
    appr = admin.get(f"/approvals/{t['approval_id']}").json()
    # stale/forged hash rejected
    assert admin.post(f"/approvals/{appr['id']}/approve", {"payload_hash": "0" * 64}).status_code == 409
    # inline edit -> new version; old one invalidated
    new_payload = copy.deepcopy(appr["payload"])
    new_payload["operations"][0]["fields"]["amount_micros"] += 1_000_000
    r = mgr.put(f"/approvals/{appr['id']}/payload", {"payload": new_payload})
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["payload_version"] == 2 and new["supersedes_id"] == appr["id"] and new["payload_hash"] != appr["payload_hash"]
    old = admin.get(f"/approvals/{appr['id']}").json()
    assert old["status"] == "INVALIDATED" and old["superseded_by_id"] == new["id"]
    assert admin.post(f"/approvals/{appr['id']}/approve", {"payload_hash": appr["payload_hash"]}).status_code == 409
    # approve v2, then tamper with the stored payload -> execution refuses
    assert admin.post(f"/approvals/{new['id']}/approve", {"payload_hash": new["payload_hash"]}).status_code == 200
    a = db.get(Approval, new["id"])
    tampered = copy.deepcopy(a.payload)
    tampered["operations"][0]["fields"]["amount_micros"] = 999_000_000
    a.payload = tampered
    db.commit()
    r = admin.post(f"/approvals/{new['id']}/execute")
    assert r.status_code == 423 and "hash" in r.text.lower()


def test_self_approval_blocked_and_expiry_escalates(db):
    admin = Api(ADMIN)
    cid, camp = _campaign(admin, "Plumbing | Drain Cleaning & Water Heaters | Search")
    t = _budget_task(admin, cid, camp, camp["monthly_budget"] + 70)
    appr = admin.get(f"/approvals/{t['approval_id']}").json()
    r = admin.post(f"/approvals/{appr['id']}/approve", {"payload_hash": appr["payload_hash"]})
    assert r.status_code == 403 and "separation of duties" in r.text
    from datetime import timedelta
    from app.core import clock
    from app.services import approvals
    a = db.get(Approval, appr["id"])
    a.expires_at = clock.now() - timedelta(hours=1)
    db.commit()
    approvals.expiry_sweep(db)
    db.commit()
    db.refresh(a)
    assert a.status == "EXPIRED" and a.superseded_by_id
    succ = db.get(Approval, a.superseded_by_id)
    backup_id = db.scalar(select(__import__("app.models", fromlist=["User"]).User.id).where(
        __import__("app.models", fromlist=["User"]).User.email == BACKUP))
    assert succ.status == "PENDING_APPROVAL" and succ.escalated_to_id == backup_id and succ.payload_hash == a.payload_hash
    assert any("escalated" in x["subject"].lower() for x in admin.get("/alerts").json())


def test_budget_increase_over_threshold_requires_super_admin():
    admin, mgr, sup = Api(ADMIN), Api(MANAGER), Api(SUPER)
    cid, camp = _campaign(admin, "Plumbing | Emergency | Search")
    t = _budget_task(mgr, cid, camp, camp["monthly_budget"] * 2)
    appr = admin.get(f"/approvals/{t['approval_id']}").json()
    assert appr["context"]["requires_super_admin"] is True
    assert admin.post(f"/approvals/{appr['id']}/approve", {"payload_hash": appr["payload_hash"]}).status_code == 403
    assert sup.post(f"/approvals/{appr['id']}/approve", {"payload_hash": appr["payload_hash"]}).status_code == 200


def _approved_build(admin, mgr, service="Emergency Plumbing", budget=900):
    plumb = next(b for b in admin.get("/businesses").json() if b["name"] == "Northside Plumbing")
    r = mgr.post("/tasks?sync=true", {"task_type": "BUILD_CAMPAIGN", "client_id": plumb["client_id"], "business_id": plumb["id"],
                                      "fields": {"monthly_budget": budget, "weekly_target": 2, "services": [service]}})
    t = r.json()
    assert t["status"] == "AWAITING_APPROVAL", t
    appr = admin.get(f"/approvals/{t['approval_id']}").json()
    assert admin.post(f"/approvals/{appr['id']}/approve", {"payload_hash": appr["payload_hash"]}).status_code == 200
    return appr


def _count_campaigns(db, name):
    db.expire_all()
    return len([r for r in db.scalars(select(MockResource).where(MockResource.resource_type == "campaign"))
                if r.data.get("name") == name])


def test_timeout_after_commit_is_reconciled_not_retried(db):
    admin, mgr = Api(ADMIN), Api(MANAGER)
    appr = _approved_build(admin, mgr, "Emergency Plumbing", 910)
    name = appr["payload"]["operations"][1]["fields"]["name"]
    inject("google_ads", "timeout_after_commit", method="mutate", times=1)
    r = admin.post(f"/approvals/{appr['id']}/execute")
    assert r.status_code == 200, r.text
    op = r.json()
    assert op["state"] == "SUCCEEDED" and op["reconciled_at"] and op["external_response"].get("reconciled") is True
    assert op["verification_passed"] is True
    assert _count_campaigns(db, name) == 1  # created exactly once — no blind retry
    again = admin.post(f"/approvals/{appr['id']}/execute").json()  # idempotent
    assert again["id"] == op["id"] and _count_campaigns(db, name) == 1


def test_timeout_before_commit_reconciles_as_not_applied_then_resubmits(db):
    admin, mgr = Api(ADMIN), Api(MANAGER)
    appr = _approved_build(admin, mgr, "Water Heater Repair", 920)
    name = appr["payload"]["operations"][1]["fields"]["name"]
    inject("google_ads", "timeout", method="mutate", entity=":write", times=1)
    r = admin.post(f"/approvals/{appr['id']}/execute").json()
    assert r["state"] == "NOT_APPLIED" and _count_campaigns(db, name) == 0
    db.expire_all()
    assert db.get(Approval, appr["id"]).status == "EXECUTION_FAILED"
    # reconciliation proved nothing exists -> a fresh attempt on the same approval is safe (approval re-set by admin)
    a = db.get(Approval, appr["id"])
    a.status = "APPROVED"
    db.commit()
    r2 = admin.post(f"/approvals/{appr['id']}/execute").json()
    assert r2["state"] == "SUCCEEDED" and r2["attempts"] == 2 and _count_campaigns(db, name) == 1


def test_read_back_verification_detects_mismatch(db, monkeypatch):
    admin, mgr = Api(ADMIN), Api(MANAGER)
    appr = _approved_build(admin, mgr, "Drain Cleaning", 930)
    ads = registry.get("google_ads")
    orig = ads.get_resource

    def lying(cid, rn):
        res = orig(cid, rn)
        if res and res.get("resource_type") == "campaign":
            res = {**res, "status": "ENABLED"}
        return res
    monkeypatch.setattr(ads, "get_resource", lying)
    op = admin.post(f"/approvals/{appr['id']}/execute").json()
    assert op["state"] == "SUCCEEDED" and op["verification_passed"] is False
    assert any("expected PAUSED" in d["problem"] for d in op["verification_diff"])
    pend = admin.get("/approvals?status=PENDING_APPROVAL").json()
    assert not any(p["approval_type"] == "ENABLE" and appr["title"] in p["title"] for p in pend)  # no enable offered


def test_kill_switch_blocks_execution():
    admin, mgr, sup = Api(ADMIN), Api(MANAGER), Api(SUPER)
    cid, camp = _campaign(admin, "Plumbing | Drain Cleaning & Water Heaters | Search")
    t = _budget_task(mgr, cid, camp, camp["monthly_budget"] + 10)
    appr = admin.get(f"/approvals/{t['approval_id']}").json()
    admin.post(f"/approvals/{appr['id']}/approve", {"payload_hash": appr["payload_hash"]})
    assert sup.post("/kill-switches", {"scope": "GLOBAL", "is_on": True, "reason": "test"}).status_code == 200
    try:
        r = admin.post(f"/approvals/{appr['id']}/execute")
        assert r.status_code == 423 and "Kill switch" in r.text
    finally:
        sup.post("/kill-switches", {"scope": "GLOBAL", "is_on": False})
    assert admin.post(f"/approvals/{appr['id']}/execute").json()["state"] == "SUCCEEDED"


def test_sec_w_real_adapter_cannot_write_while_disabled(db):
    """With the REAL adapter loaded, GOOGLE_ADS_WRITES_ENABLED=false blocks writes in the WriteGuard AND inside the adapter."""
    class NoClient:
        def __call__(self):
            raise AssertionError("the real Google Ads client must never be created for a blocked write")
    real = RealGoogleAdsAdapter(client_factory=NoClient())
    with pytest.raises(WritesDisabled):
        real.mutate("1110000001", [{"resource": "campaign", "op": "update", "resource_name": "x", "fields": {"status": "ENABLED"}}])
    registry.override("google_ads", real)
    from app.services import safety
    acct = db.scalar(select(GoogleAdsAccount).where(GoogleAdsAccount.customer_id == "1110000001"))
    with pytest.raises(SafetyBlocked) as exc:
        safety.check(db, policy="AUTO_NEGATIVE", client=None, account=acct)
    reasons = " ".join(exc.value.details["reasons"])
    assert "GOOGLE_ADS_WRITES_ENABLED is false" in reasons and "not authorized real writes" in reasons and "allowlist" in reasons


def test_enable_requires_verified_parent(db):
    admin = Api(ADMIN)
    pend = [a for a in admin.get("/approvals?status=PENDING_APPROVAL").json() if a["approval_type"] == "ENABLE"]
    if not pend:
        pytest.skip("no pending enable approval in this run")
    a = db.get(Approval, pend[0]["id"])
    a.parent_operation_id = None
    db.commit()
    sup = Api(SUPER)
    sup.post(f"/approvals/{a.id}/approve", {"payload_hash": a.payload_hash})
    r = admin.post(f"/approvals/{a.id}/execute")
    assert r.status_code == 423 and "verified" in r.text


def test_analyst_cannot_execute_and_manager_cannot_execute():
    admin = Api(ADMIN)
    any_approved = admin.get("/approvals").json()[0]
    assert Api("analyst@napollo.example").post(f"/approvals/{any_approved['id']}/execute").status_code == 403
    assert Api(MANAGER).post(f"/approvals/{any_approved['id']}/execute").status_code == 403


def test_campaign_created_paused_in_db_mirror(db):
    created = [c for c in db.scalars(select(Campaign).where(Campaign.created_by_app.is_(True)))]
    assert created
    assert all(c.integration_mode == "MOCK" for c in created)
