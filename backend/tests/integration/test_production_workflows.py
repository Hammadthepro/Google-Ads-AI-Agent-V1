"""The user-facing workflows, end to end through the API: acting on Challenger findings (apply as a new version, dismiss with a reason,
ask for a revision, re-review), re-run actions that state exactly what runs again, approved-but-unpublished campaigns as parents of a
new ad group (a new version, never a second campaign), the Research & Evidence Appendix of each version, and remaining token budget."""
from __future__ import annotations

from app.db.session import session_scope
from app.models import Approval, CampaignPlan, ChallengerResolution, Task
from tests.conftest import ADMIN, SUPER, Api
from tests.integration.test_intake_v2 import _fixture_client


def _build(api: Api, name: str):
    cid, bid = _fixture_client(api, name, weekly_target=4)
    t = api.post("/tasks?sync=true", {"task_type": "BUILD_CAMPAIGN", "client_id": cid, "business_id": bid, "fields": {"monthly_budget": 3000}}).json()
    assert t["status"] == "AWAITING_APPROVAL", (t["status"], t["error"])
    return cid, bid, t


def _plant_objection(task: dict, objection: dict) -> str:
    """Give the current version a Challenger report (the test AI's reviewer is deterministic, so the objection is set directly)."""
    with session_scope() as db:
        p = db.get(CampaignPlan, task["plan_id"])
        p.challenger = {"performed": True, "verdict": "REVISE", "objections": [objection], "agreements": [], "limitations": []}
        return p.draft["ad_groups"][0]["keywords"][0]["text"]


def _first_kw(task):
    with session_scope() as db:
        g = db.get(CampaignPlan, task["plan_id"]).draft["ad_groups"][0]
        return g["name"], g["keywords"][0]


def test_apply_a_challenger_suggestion_creates_a_new_version_and_records_it():
    api = Api(ADMIN)
    _, _, t = _build(api, "Resolve Co")
    group, kw = _first_kw(t)
    _plant_objection(t, {"category": "WEAK_INTENT", "severity": "MEDIUM", "target": kw["text"],
                         "message": f"Keyword '{kw['text']}' is overly restrictive and may get no volume.",
                         "suggestion": f"Remove the keyword '{kw['text']}' and rely on standard phrase and exact match.", "evidence": "no volume", "fix": []})
    f = api.get(f"/tasks/{t['id']}/challenger").json()
    item = f["findings"][0]
    assert item["status"] == "open" and item["fix_source"] == "rules"
    assert item["changes"][0]["original"].endswith(kw["text"]) and item["changes"][0]["replacement"] == "removed"
    assert group in item["component"]
    r = api.post(f"/tasks/{t['id']}/challenger/{item['key']}/resolve", {"action": "apply"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resolution"] == "applied" and body["findings"]["plan_version"] == 2
    with session_scope() as db:
        new = db.get(CampaignPlan, body["plan_id"])
        assert all(k["text"] != kw["text"] for g in new.draft["ad_groups"] for k in g["keywords"])
        assert db.get(CampaignPlan, t["plan_id"]).draft != new.draft  # the previous version is preserved unchanged
        assert db.get(Approval, t["approval_id"]).status == "INVALIDATED"
        rec = db.query(ChallengerResolution).filter_by(task_id=t["id"]).one()
        assert rec.action == "applied" and rec.result_plan_id == new.id and rec.edits
    assert body["findings"]["findings"][0]["status"] == "applied"  # the concern is shown as resolved on the new version
    assert body["findings"]["reviewed_plan_version"] == 1 and body["findings"]["plan_version"] == 2  # never claims v2 was reviewed


def test_dismiss_needs_a_reason_and_stays_in_history_and_approved_versions_need_confirmation():
    api, sup = Api(ADMIN), Api(SUPER)
    _, _, t = _build(api, "Dismiss Co")
    _, kw = _first_kw(t)
    _plant_objection(t, {"category": "BUDGET", "severity": "LOW", "target": "budget", "message": "Budget may be thin.", "suggestion": "Consider more.",
                         "evidence": "", "fix": []})
    key = api.get(f"/tasks/{t['id']}/challenger").json()["findings"][0]["key"]
    assert api.post(f"/tasks/{t['id']}/challenger/{key}/resolve", {"action": "apply"}).status_code == 422  # nothing to apply automatically
    assert api.post(f"/tasks/{t['id']}/challenger/{key}/resolve", {"action": "dismiss"}).status_code == 422
    r = api.post(f"/tasks/{t['id']}/challenger/{key}/resolve", {"action": "dismiss", "reason": "Client confirmed the budget in writing"})
    assert r.status_code == 200 and r.json()["findings"]["findings"][0]["status"] == "dismissed"
    assert r.json()["findings"]["history"][0]["reason"] == "Client confirmed the budget in writing"
    # an approved version is never changed silently
    appr = api.get(f"/approvals/{t['approval_id']}").json()
    if appr["status"] == "PENDING_APPROVAL":
        assert sup.post(f"/approvals/{appr['id']}/approve", {"payload_hash": appr["payload_hash"]}).status_code == 200
        _plant_objection(t, {"category": "WEAK_INTENT", "severity": "HIGH", "target": kw["text"], "message": f"'{kw['text']}' is weak",
                             "suggestion": f"Remove '{kw['text']}'", "evidence": "", "fix": []})
        key2 = api.get(f"/tasks/{t['id']}/challenger").json()["findings"][0]["key"]
        r = api.post(f"/tasks/{t['id']}/challenger/{key2}/resolve", {"action": "apply"})
        assert r.status_code == 409 and "approved" in r.text
        r = api.post(f"/tasks/{t['id']}/challenger/{key2}/resolve", {"action": "apply", "confirm_supersede": True})
        assert r.status_code == 200, r.text
        with session_scope() as db:
            assert db.get(Approval, appr["id"]).status == "INVALIDATED"


def test_actions_say_what_reruns_and_each_calls_a_real_operation():
    api = Api(ADMIN)
    _, _, t = _build(api, "Actions Co")
    acts = {a["key"]: a for a in api.get(f"/tasks/{t['id']}").json()["actions"]}
    assert {"regenerate", "revise", "rerun_feasibility", "refresh_research", "rerun_challenger"} <= set(acts)
    assert acts["rerun_feasibility"]["stages"][0] == "Feasibility" and "Keyword research" in acts["rerun_feasibility"]["reuses"]
    assert acts["refresh_research"]["reuses"] == []
    assert api.post(f"/tasks/{t['id']}/rerun", {"action": "resume"}).status_code == 422  # not interrupted: not offered, refused
    r = api.post(f"/tasks/{t['id']}/rerun", {"action": "rerun_challenger"})
    assert r.status_code == 200, r.text
    with session_scope() as db:
        assert db.get(CampaignPlan, t["plan_id"]).challenger.get("reviewed_version") == 1
    r = api.post(f"/tasks/{t['id']}/rerun?sync=true", {"action": "rerun_feasibility"})
    assert r.status_code == 200, r.text
    t2 = api.get(f"/tasks/{t['id']}").json()
    assert t2["status"] == "AWAITING_APPROVAL" and t2["plan_id"] != t["plan_id"]
    names = [s["name"] for s in t2["steps"][len(t["steps"]):]]
    assert names[0] == "Feasibility" and not any(n.startswith("Keyword research") or n.startswith("Audits") for n in names)
    with session_scope() as db:
        assert "feasibility" in db.get(CampaignPlan, t2["plan_id"]).change_summary.lower()


def test_resume_is_offered_only_for_an_interrupted_run():
    api = Api(ADMIN)
    _, _, t = _build(api, "Resume Co")
    from datetime import timedelta
    from app.core import clock
    with session_scope() as db:
        task = db.get(Task, t["id"])
        task.status = "RUNNING"
        task.steps = [s for s in task.steps if s.get("key") in ("load", "measurement", "audits", "keywords")]
    assert "resume" not in {a["key"] for a in api.get(f"/tasks/{t['id']}").json()["actions"]}  # just updated: still active
    with session_scope() as db:
        from app.models import ResearchEvent
        old = clock.real_now() - timedelta(minutes=30)
        db.query(ResearchEvent).filter(ResearchEvent.run_id == t["id"]).update({"created_at": old})
        db.query(Task).filter(Task.id == t["id"]).update({"updated_at": old})
    act = next(a for a in api.get(f"/tasks/{t['id']}").json()["actions"] if a["key"] == "resume")
    assert act["stages"][0] == "Overlap check" and "Keyword research" in act["reuses"]
    assert api.post(f"/tasks/{t['id']}/rerun?sync=true", {"action": "resume"}).status_code == 200
    assert api.get(f"/tasks/{t['id']}").json()["status"] == "AWAITING_APPROVAL"


def test_an_approved_unpublished_campaign_is_selectable_and_a_new_ad_group_becomes_its_next_version():
    api, sup = Api(ADMIN), Api(SUPER)
    cid, bid, t = _build(api, "Expand Co")
    appr = api.get(f"/approvals/{t['approval_id']}").json()
    if appr["status"] == "PENDING_APPROVAL":
        assert sup.post(f"/approvals/{appr['id']}/approve", {"payload_hash": appr["payload_hash"]}).status_code == 200
    opts = api.get(f"/clients/{cid}/campaign-options").json()["campaigns"]
    plan = next(o for o in opts if o["kind"] == "plan")
    assert plan["id"] == t["plan_id"] and plan["published"] is False and plan["publication"].startswith("Not published")
    assert plan["approval_status"] in ("APPROVED", "VALIDATION_FAILED") and plan["can_add_ad_group"]
    body = {"task_type": "ADD_AD_GROUP", "client_id": cid, "business_id": bid,
            "fields": {"parent_plan_id": plan["id"], "service": "Drain Cleaning", "landing_page": "https://www.northsideplumbing.example/drain-cleaning"}}
    if plan["approval_status"] == "APPROVED":
        r = api.post("/tasks?sync=true", body)
        assert r.status_code == 409 and "approved" in r.text
        body["fields"]["confirm_supersede"] = True
    r = api.post("/tasks?sync=true", body)
    assert r.status_code == 200, r.text
    t2 = r.json()
    assert t2["status"] in ("AWAITING_APPROVAL", "VALIDATION_FAILED"), (t2["status"], t2["error"])
    with session_scope() as db:
        merged = db.get(CampaignPlan, t2["fields"]["merged_plan_id"])
        parent = db.get(CampaignPlan, t["plan_id"])
        assert merged.scope == "CAMPAIGN" and merged.task_id == t["id"] and merged.version == parent.version + 1
        assert len(merged.draft["ad_groups"]) == len(parent.draft["ad_groups"]) + 1 and merged.parent_plan_id == parent.id
        assert db.get(Approval, t2["approval_id"]).payload["kind"] == "BUILD_CAMPAIGN"  # one campaign, rebuilt; never a second one
        assert db.get(Approval, t["approval_id"]).status == "INVALIDATED"
        assert db.get(Task, t["id"]).plan_id == merged.id
    opts = api.get(f"/clients/{cid}/campaign-options").json()["campaigns"]
    assert [o["id"] for o in opts if o["kind"] == "plan"] == [merged.id]  # the current version only, never duplicated


def test_the_appendix_is_the_persisted_research_of_that_exact_version():
    api = Api(ADMIN)
    _, _, t = _build(api, "Appendix Co")
    a = api.get(f"/plans/{t['plan_id']}/appendix").json()
    assert a["captured_with_version"] is True and a["plan_version"] == 1
    assert a["sources"] and all(s["provider"] and s["retrieved_at"] for s in a["sources"])
    kw = a["keywords"]
    assert kw["total"] >= len(kw["selected"]) and all("measurement" in k for k in kw["selected"])
    for k in kw["selected"] + kw["rejected"]:
        if k["search_volume"] is None:
            assert k["volume_period"] is None and k["unavailable_reason"]  # never an invented number
        assert k["avg_cpc"] is None and "not average CPC" in k["avg_cpc_note"]
    assert any(c["origin"] == "client-provided" for c in a["competitors"])
    for c in a["competitors"]:
        assert c["website_inspected"] == bool(c["landing_pages_reviewed"])  # never claimed without a retrieval
    assert a["feasibility"]["is_estimate"] is True and a["methodology"]["events_recorded"] > 0
    with session_scope() as db:  # the same appendix comes back for an edited version (it carries its source's evidence)
        stored = db.get(CampaignPlan, t["plan_id"]).evidence
    assert stored["captured_at"] == a["captured_at"]


def test_remaining_token_budget_is_reported_per_client():
    sup = Api(SUPER)
    r = sup.get("/ai/roles/main/budget")
    assert r.status_code == 200 and {"caps", "clients", "fields"} <= set(r.json())
    assert Api(ADMIN).get("/ai/roles/main/budget").status_code in (200, 403)
