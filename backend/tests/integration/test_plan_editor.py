"""Structured plan editing: versions, original-vs-final diff, new exact-version approval."""
from __future__ import annotations

from tests.conftest import ADMIN, Api


def _build(admin):
    c = admin.post("/clients", {"name": "Editor Test Co", "owner_contact": "o@x.example"}).json()["id"]
    b = admin.post(f"/clients/{c}/businesses", {
        "name": "Editor Biz", "business_type": "home_services", "website_url": "https://www.northsideplumbing.example/",
        "phone": "(555) 201-4400", "address": "1 Main St, Columbus, OH", "lead_form_url": "https://www.northsideplumbing.example/contact",
        "thank_you_url": "https://www.northsideplumbing.example/thank-you", "verified_claims": ["Licensed & Insured"],
        "services": [{"name": "Drain Cleaning", "landing_page_url": "https://www.northsideplumbing.example/drain-cleaning"},
                     {"name": "Water Heater Repair", "landing_page_url": "https://www.northsideplumbing.example/water-heater-repair"}]}).json()["id"]
    acct = next(a for a in admin.post("/ads-accounts/discover").json() if a["customer_id"] == "1110000004")
    admin.post(f"/ads-accounts/{acct['id']}/link", {"client_id": c})
    t = admin.post("/tasks?sync=true", {"task_type": "BUILD_CAMPAIGN", "client_id": c, "business_id": b,
                                        "fields": {"monthly_budget": 6000, "weekly_target": 6, "services": ["Drain Cleaning", "Water Heater Repair"]}}).json()
    if t["status"] == "AWAITING_BUDGET_DECISION":
        t = admin.post(f"/tasks/{t['id']}/budget-decision?sync=true", {"decision": "ACCEPT_RECOMMENDED"}).json()
    assert t["status"] == "AWAITING_APPROVAL", t
    return t


def test_edit_creates_new_version_new_approval_and_diff():
    admin = Api(ADMIN)
    t = _build(admin)
    a1 = admin.get(f"/approvals/{t['approval_id']}").json()
    plan1 = admin.get(f"/plans/{a1['context']['plan_id']}").json()
    assert plan1["version"] == 1 and plan1["competition"]["mode"] == "single" and plan1["original"] == plan1["draft"]
    group = plan1["draft"]["ad_groups"][0]["name"]
    r = admin.post(f"/approvals/{a1['id']}/plan-edit", {"note": "client call", "edits": [
        {"op": "add_keyword", "ad_group": group, "text": "same day drain unclogging", "match_type": "PHRASE"},
        {"op": "add_negative", "text": "rental"},
        {"op": "rename_campaign", "value": "Edited | Drain & Heater | Search"}]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["plan_version"] == 2 and out["approval_id"] != a1["id"] and len(out["changes"]) == 3
    old = admin.get(f"/approvals/{a1['id']}").json()
    new = admin.get(f"/approvals/{out['approval_id']}").json()
    assert old["status"] == "INVALIDATED" and new["status"] == "PENDING_APPROVAL" and new["payload_hash"] != old["payload_hash"]
    names = [o["fields"].get("name") for o in new["payload"]["operations"] if o["resource"] == "campaign"]
    assert names == ["Edited | Drain & Heater | Search"]
    # the old version can no longer be approved
    assert admin.post(f"/approvals/{a1['id']}/approve", {"payload_hash": old["payload_hash"]}).status_code in (409, 422, 423)
    d = admin.get(f"/plans/{out['plan_id']}/diff").json()
    assert d["changed"] and d["negatives"]["added"] == ["rental"]
    assert any("same day drain unclogging" in k for g in d["ad_groups"] for k in g["keywords_added"])
    assert d["campaign"][0]["field"] == "campaign_name"
    versions = admin.get(f"/plans/{out['plan_id']}/versions").json()
    assert [v["version"] for v in versions] == [1, 2] and versions[1]["is_current"]
    assert admin.get(f"/plans/{plan1['id']}").json()["draft"] == plan1["draft"]  # history is immutable


def test_invalid_edits_are_rejected_and_broad_match_is_refused():
    admin = Api(ADMIN)
    t = _build(admin)
    aid = t["approval_id"]
    group = admin.get(f"/plans/{admin.get(f'/approvals/{aid}').json()['context']['plan_id']}").json()["draft"]["ad_groups"][0]["name"]
    for edit in ({"op": "add_keyword", "ad_group": group, "text": "drain", "match_type": "BROAD"},
                 {"op": "add_keyword", "ad_group": "Nope", "text": "x", "match_type": "EXACT"},
                 {"op": "set_bidding", "value": "TARGET_CPA"}, {"op": "delete_everything"},
                 {"op": "set_landing_page", "ad_group": group, "url": "javascript:alert(1)"}):
        assert admin.post(f"/approvals/{aid}/plan-edit", {"edits": [edit]}).status_code == 422, edit
    assert admin.post(f"/approvals/{aid}/plan-edit", {"edits": []}).status_code == 422
    assert admin.get(f"/approvals/{aid}").json()["status"] == "PENDING_APPROVAL"  # failed edits change nothing


def test_edit_producing_policy_violation_fails_validation_not_silently():
    admin = Api(ADMIN)
    t = _build(admin)
    aid = t["approval_id"]
    plan = admin.get(f"/plans/{admin.get(f'/approvals/{aid}').json()['context']['plan_id']}").json()
    group = plan["draft"]["ad_groups"][0]["name"]
    r = admin.post(f"/approvals/{aid}/plan-edit", {"edits": [{"op": "edit_ad", "ad_group": group, "index": 0,
                     "headlines": ["Best Plumber Ever!", "Call Now 555-123-4567", "A" * 40]}]})
    assert r.status_code == 200 and r.json()["validation_passed"] is False
    new = admin.get(f"/approvals/{r.json()['approval_id']}").json()
    assert new["status"] == "VALIDATION_FAILED"
    assert admin.post(f"/approvals/{new['id']}/approve", {"payload_hash": new["payload_hash"]}).status_code in (409, 422, 423)
