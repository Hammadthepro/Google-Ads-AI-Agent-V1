"""Campaign draft lifecycle: regenerate and revise with instructions from the stored research (no research is run again), immutable
versions with a parent and a reason, the new approval superseding the old one, restore an earlier version as a new one, compare
versions, and a failed revision that leaves the current version and approval untouched."""
from __future__ import annotations

import pytest

from app.db.session import session_scope
from app.models import Approval, CampaignPlan
from app.services import competition, planner
from tests.conftest import ADMIN, Api
from tests.integration.test_intake_v2 import _fixture_client


def _build(api: Api, name: str) -> dict:
    cid, bid = _fixture_client(api, name, weekly_target=4)
    r = api.post("/tasks?sync=true", {"task_type": "BUILD_CAMPAIGN", "client_id": cid, "business_id": bid, "fields": {"monthly_budget": 3000}})
    t = r.json()
    assert t["status"] == "AWAITING_APPROVAL", (t["status"], t["error"])
    return t


@pytest.fixture
def seen(monkeypatch):
    calls = []
    real = competition.run

    def spy(db, **kw):
        calls.append(kw["payload"].get("revision"))
        return real(db, **kw)
    monkeypatch.setattr(competition, "run", spy)
    return calls


def test_revise_with_instructions_makes_a_new_version_from_stored_research_and_supersedes_the_approval(seen):
    api = Api(ADMIN)
    t = _build(api, "Versions Co")
    assert api.get(f"/tasks/{t['id']}").json()["revision"]["available"] is True
    steps_before = len(t["steps"])
    r = api.post(f"/tasks/{t['id']}/revise-draft?sync=true", {"instructions": "Lead with same-day emergency service; drop the generic ad group."})
    assert r.status_code == 200, r.text
    t2 = r.json()
    assert t2["status"] == "AWAITING_APPROVAL" and t2["plan_id"] != t["plan_id"] and t2["approval_id"] != t["approval_id"]
    assert seen[-1]["instructions"].startswith("Lead with same-day") and seen[-1]["previous"]["version"] == 1  # the AI got the instructions
    new_steps = [s["name"] for s in t2["steps"][steps_before:]]
    assert new_steps[0].startswith("AI draft (revision, v2)") and not any("Keyword research" in n or "Audits" in n for n in new_steps)
    with session_scope() as db:
        v1, v2 = db.get(CampaignPlan, t["plan_id"]), db.get(CampaignPlan, t2["plan_id"])
        assert (v2.version, v2.parent_plan_id, v1.status) == (2, v1.id, "SUPERSEDED") and "same-day emergency" in v2.change_summary
        a1, a2 = db.get(Approval, t["approval_id"]), db.get(Approval, t2["approval_id"])
        assert a1.status == "INVALIDATED" and a1.superseded_by_id == a2.id and a2.supersedes_id == a1.id
        assert a2.payload_version == a1.payload_version + 1 and a2.status in ("PENDING_APPROVAL", "VALIDATION_FAILED")
    attempts = t2["fields"]["draft_attempts"]
    assert attempts[-1]["kind"] == "revision" and attempts[-1]["status"] == "succeeded"

    # regenerate (no instructions), then compare and restore v1 as a new version
    t3 = api.post(f"/tasks/{t['id']}/revise-draft?sync=true", {"instructions": ""}).json()
    assert seen[-1]["instructions"].startswith("Regenerate")
    vs = api.get(f"/plans/{t3['plan_id']}/versions").json()
    assert [v["version"] for v in vs] == [1, 2, 3] and [v["is_current"] for v in vs] == [False, False, True]
    with session_scope() as db:  # (the test AI is deterministic: give v1 a real difference to restore)
        v1 = db.get(CampaignPlan, t["plan_id"])
        v1.draft = {**v1.draft, "campaign_name": v1.draft["campaign_name"] + " (first draft)"}
    d = api.get(f"/plans/{t3['plan_id']}/diff?against={t['plan_id']}").json()
    assert d["against"] == t["plan_id"] and d["version"] == 3
    r = api.post(f"/approvals/{t3['approval_id']}/restore-version", {"plan_id": t["plan_id"], "note": "client prefers the first draft"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["plan_version"] == 4 and out["changes"] == ["Restored version 1"]
    with session_scope() as db:
        v4, v1 = db.get(CampaignPlan, out["plan_id"]), db.get(CampaignPlan, t["plan_id"])
        assert v4.draft == v1.draft and v4.status == "RESTORED" and "first draft" in v4.change_summary
        assert db.get(Approval, t3["approval_id"]).status == "INVALIDATED"
    assert api.post(f"/approvals/{out['approval_id']}/restore-version", {"plan_id": out["plan_id"]}).status_code == 409  # already current
    r = api.post(f"/approvals/{out['approval_id']}/restore-version", {"plan_id": t["plan_id"]})
    assert r.status_code == 409 and "identical" in r.text


def test_a_failed_revision_changes_nothing(monkeypatch):
    api = Api(ADMIN)
    t = _build(api, "Versions Fail Co")

    def boom(*a, **k):
        raise RuntimeError("provider exploded")
    monkeypatch.setattr(planner, "draft", boom)
    t2 = api.post(f"/tasks/{t['id']}/revise-draft?sync=true", {"instructions": "shorter headlines"}).json()
    assert t2["status"] == "AWAITING_APPROVAL" and t2["plan_id"] == t["plan_id"] and t2["approval_id"] == t["approval_id"]
    assert "revision failed and nothing changed" in t2["error"]
    assert t2["fields"]["draft_attempts"][-1]["status"] == "failed"
    with session_scope() as db:
        assert db.get(Approval, t["approval_id"]).status in ("PENDING_APPROVAL", "VALIDATION_FAILED")
        assert db.get(CampaignPlan, t["plan_id"]).status != "SUPERSEDED"


def test_revision_input_is_bounded_and_only_one_runs_at_a_time():
    api = Api(ADMIN)
    t = _build(api, "Versions Guard Co")
    assert api.post(f"/tasks/{t['id']}/revise-draft", {"instructions": "x" * 2001}).status_code == 422
    with session_scope() as db:
        from app.models import Task
        db.get(Task, t["id"]).status = "RETRYING_DRAFT"
    assert api.post(f"/tasks/{t['id']}/revise-draft", {"instructions": "again"}).status_code == 409
    with session_scope() as db:
        from app.models import Task
        db.get(Task, t["id"]).status = "AWAITING_APPROVAL"
