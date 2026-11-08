"""E2E-A: new client -> simulated campaign created and verified PAUSED.
E2E-B: the campaign cannot be enabled without the required ENABLE approval."""
from __future__ import annotations

from tests.conftest import ADMIN, MANAGER, SUPER, Api


def _new_client_with_business(admin: Api) -> tuple[str, str]:
    r = admin.post("/clients", {"name": "E2E Test Plumbing Co", "owner_contact": "owner@e2e.example", "backup_contact": "backup@e2e.example",
                                "tag_access_contact": "web@e2e.example"})
    assert r.status_code == 200, r.text
    client_id = r.json()["id"]
    # reuse a fixture website so the crawler works in fixture mode; link the seeded simulated account
    r = admin.post(f"/clients/{client_id}/businesses", {
        "name": "Northside Plumbing (E2E)", "business_type": "home_services", "website_url": "https://www.northsideplumbing.example/",
        "phone": "(555) 201-4400", "address": "1200 Example Ave, Columbus, OH", "lead_form_url": "https://www.northsideplumbing.example/contact",
        "thank_you_url": "https://www.northsideplumbing.example/thank-you", "verified_claims": ["Licensed & Insured", "Same-Day Service"],
        "brand_rules": ["Avoid: cheapest"],
        "services": [{"name": "Drain Cleaning", "landing_page_url": "https://www.northsideplumbing.example/drain-cleaning"},
                     {"name": "Water Heater Repair", "landing_page_url": "https://www.northsideplumbing.example/water-heater-repair"},
                     {"name": "Septic Tank Pumping", "is_excluded": True}],
        "competitors": [{"name": "Rapid Rooter Columbus", "website_url": "https://www.rapidrooter.example/"}]})
    assert r.status_code == 200, r.text
    business_id = r.json()["id"]
    accounts = admin.post("/ads-accounts/discover").json()
    acct = next(a for a in accounts if a["customer_id"] == "1110000004")
    assert acct["client_id"] is None
    r = admin.post(f"/ads-accounts/{acct['id']}/link", {"client_id": client_id})
    assert r.status_code == 200 and r.json()["client_id"] == client_id
    return client_id, business_id, acct


def test_e2e_new_client_to_paused_campaign_then_enable_requires_approval():
    sup, admin, mgr = Api(SUPER), Api(ADMIN), Api(MANAGER)
    client_id, business_id, acct = _new_client_with_business(admin)
    # manager is not assigned to the new client yet
    r = mgr.post("/tasks?sync=true", {"task_type": "BUILD_CAMPAIGN", "client_id": client_id, "business_id": business_id,
                                      "fields": {"monthly_budget": 1500, "weekly_target": 5}})
    assert r.status_code == 403
    users = sup.get("/users").json()
    mgr_user = next(u for u in users if u["email"] == MANAGER)
    sup.patch(f"/users/{mgr_user['id']}", {"client_ids": mgr_user["client_ids"] + [client_id]})
    mgr = Api(MANAGER)
    r = mgr.post("/tasks?sync=true", {"task_type": "BUILD_CAMPAIGN", "client_id": client_id, "business_id": business_id,
                                      "fields": {"monthly_budget": 1500, "weekly_target": 5, "services": ["Drain Cleaning", "Water Heater Repair"]},
                                      "human_plan": "Bidding: Maximize Conversions\nKeywords: sewer drain cleaning\nNegatives: plumbing school",
                                      "note": "E2E build"})
    assert r.status_code == 200, r.text
    task = r.json()
    assert task["status"] == "AWAITING_APPROVAL", task
    names = [s["name"] for s in task["steps"]]
    for step in ("Conversion tracking gate", "Audits (website, GBP, competitors)", "Keyword research", "Overlap check", "Feasibility",
                 "AI draft", "AI challenger", "Human plan comparison", "Validation", "Approval request"):
        assert step in names
    validation_step = next(s for s in task["steps"] if s["name"] == "Validation")
    assert "Passed" in validation_step["summary"]
    # the internal AI's unverified '#1' claim is removed by the rule-based consolidator before validation even runs

    appr = admin.get(f"/approvals/{task['approval_id']}").json()
    assert appr["status"] == "PENDING_APPROVAL"
    ops = appr["payload"]["operations"]
    assert all(o["fields"]["status"] == "PAUSED" for o in ops if o["op"] == "create" and o["resource"] in ("campaign", "ad_group", "ad_group_ad"))
    assert not any(k["fields"].get("keyword", {}).get("match_type") == "BROAD" for k in ops if k["resource"] == "ad_group_criterion")
    camp_op = next(o for o in ops if o["resource"] == "campaign")
    assert camp_op["fields"]["network_settings"]["target_search_network"] is False
    assert camp_op["fields"]["geo_target_type_setting"]["positive_geo_target_type"] == "PRESENCE"
    assert appr["context"]["challenger"]["verdict"] in ("APPROVE", "REVISE")
    draft_heads = [h for g in appr["context"]["draft"]["ad_groups"] for a in g["ads"] for h in a["headlines"]]
    assert not any("#1" in h or "Top Rated" in h for h in draft_heads)
    assert appr["context"]["human_comparison"]["applied_human_decisions"]

    # nothing can execute before approval
    r = admin.post(f"/approvals/{appr['id']}/execute")
    assert r.status_code == 423, r.text
    # approve the exact version reviewed
    r = admin.post(f"/approvals/{appr['id']}/approve", {"payload_hash": appr["payload_hash"]})
    assert r.status_code == 200, r.text
    r = admin.post(f"/approvals/{appr['id']}/execute")
    assert r.status_code == 200, r.text
    op = r.json()
    assert op["state"] == "SUCCEEDED" and op["verification_passed"] is True, op
    assert op["integration_mode"] == "MOCK" and op["adapter_name"] == "MockGoogleAdsAdapter"

    # read back from the (simulated) account: everything PAUSED
    structure = admin.get(f"/clients/{client_id}/structure").json()
    created = [c for c in structure["campaigns"] if c["created_by_app"]]
    assert len(created) == 1
    camp = created[0]
    assert camp["status"] == "PAUSED"
    assert all(g["status"] == "PAUSED" for g in camp["ad_groups"])
    assert all(a["status"] == "PAUSED" for g in camp["ad_groups"] for a in g["ads"])
    assert camp["monthly_budget"] == 1500 and abs(camp["daily_budget"] - round(1500 / 30.4, 2)) < 0.01

    # E2E-B: an ENABLE approval exists, is pending, and enabling without approving it is blocked
    pend = admin.get(f"/approvals?client_id={client_id}&status=PENDING_APPROVAL").json()
    enable = next(a for a in pend if a["approval_type"] == "ENABLE")
    r = admin.post(f"/approvals/{enable['id']}/execute")
    assert r.status_code == 423
    assert "APPROVED" in r.text
    still = admin.get(f"/clients/{client_id}/structure").json()
    assert [c for c in still["campaigns"] if c["created_by_app"]][0]["status"] == "PAUSED"
    # analyst cannot approve; the requester-separation rule is respected; admin approves -> enabled
    ana = Api("analyst@napollo.example")
    assert ana.post(f"/approvals/{enable['id']}/approve", {"payload_hash": enable["payload_hash"]}).status_code == 403
    r = admin.post(f"/approvals/{enable['id']}/approve", {"payload_hash": enable["payload_hash"]})
    assert r.status_code == 200, r.text
    r = admin.post(f"/approvals/{enable['id']}/execute")
    assert r.status_code == 200 and r.json()["state"] == "SUCCEEDED", r.text
    final = admin.get(f"/clients/{client_id}/structure").json()
    camp = [c for c in final["campaigns"] if c["created_by_app"]][0]
    assert camp["status"] == "ENABLED"
    task = admin.get(f"/tasks/{task['id']}").json()
    assert task["status"] == "ENABLED"
