"""Retry Draft: a failed AI drafting stage is retried alone from the task's stored research, once per click, safely under concurrency,
and never creates anything in Google Ads. Also: a task run records its live events, so the build workspace follows real work."""
from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from app.adapters import registry
from app.adapters.base import RateLimited
from app.core.errors import Conflict
from app.db.session import SessionLocal, session_scope
from app.models import AIRun, Approval, Audit, FeasibilityResult, Operation, Task, User
from app.services import tasks_service
from tests.conftest import ADMIN, Api
from tests.fakes.ai import MockAIProvider

DRAFTS = ("DRAFT_CAMPAIGN", "DRAFT_AD_GROUP", "DRAFT_AD")


class Flaky(MockAIProvider):
    """The test AI, except that drafting fails while `down` is set (as Groq does when the plan's per-minute limit is hit)."""
    down = True

    def complete(self, purpose, system, user, *, model_role="draft"):
        if purpose in DRAFTS and Flaky.down:
            raise RateLimited("Groq rejected the request as too large for m's per-minute token limit on this plan (HTTP 413).",
                              adapter=self.name, details={"too_large": True, "status": 413})
        return super().complete(purpose, system, user, model_role=model_role)


@pytest.fixture
def flaky():
    Flaky.down = True
    registry.override("ai", Flaky())
    registry.override("claude_ai", Flaky(variant="b"))
    yield Flaky


@pytest.fixture(scope="module")
def business():
    admin = Api(ADMIN)
    r = admin.post("/clients", {"name": "Retry Draft Plumbing", "owner_contact": "owner@retry.example", "backup_contact": "b@retry.example",
                                "tag_access_contact": "web@retry.example"})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    r = admin.post(f"/clients/{cid}/businesses", {
        "name": "Northside Plumbing (retry)", "business_type": "home_services", "website_url": "https://www.northsideplumbing.example/",
        "phone": "(555) 201-4400", "address": "1200 Example Ave, Columbus, OH", "lead_form_url": "https://www.northsideplumbing.example/contact",
        "thank_you_url": "https://www.northsideplumbing.example/thank-you", "verified_claims": ["Licensed & Insured"],
        "services": [{"name": "Drain Cleaning", "landing_page_url": "https://www.northsideplumbing.example/drain-cleaning"},
                     {"name": "Water Heater Repair", "landing_page_url": "https://www.northsideplumbing.example/water-heater-repair"}],
        "competitors": [{"name": "Rapid Rooter Columbus", "website_url": "https://www.rapidrooter.example/"}]})
    assert r.status_code == 200, r.text
    return cid, r.json()["id"]


def _failed_build(business) -> dict:
    cid, bid = business
    r = Api(ADMIN).post("/tasks?sync=true", {"task_type": "BUILD_CAMPAIGN", "client_id": cid, "business_id": bid,
                                             "fields": {"monthly_budget": 3000, "weekly_target": 1, "services": ["Drain Cleaning", "Water Heater Repair"]}})
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["status"] == "FAILED", (t["status"], t["error"])
    return t


def _counts(task_id: str) -> dict:
    with session_scope() as db:
        return {"audits": db.query(Audit).filter(Audit.task_id == task_id).count(),
                "feasibility": db.query(FeasibilityResult).filter(FeasibilityResult.task_id == task_id).count(),
                "research_ai": db.query(AIRun).filter(AIRun.task_id == task_id, AIRun.purpose.notin_(DRAFTS + ("CHALLENGE", "EXPLAIN", "FIX_ADS"))).count()}


def test_failed_draft_records_provider_details_and_offers_a_retry(flaky, business):
    t = _failed_build(business)
    assert "Neither AI produced a valid draft" in t["error"] and "HTTP 413" in t["error"]
    a = t["draft_attempts"]
    assert len(a) == 1 and a[0]["attempt"] == 1 and a[0]["kind"] == "initial" and a[0]["status"] == "failed"
    assert {c["provider"].split(":")[0] for c in a[0]["calls"]} == {"internal", "claude"}
    assert all(not c["ok"] and "HTTP 413" in c["error"] and c["model"] == "" for c in a[0]["calls"])
    d = Api(ADMIN).get(f"/tasks/{t['id']}").json()
    assert d["draft_retry"]["available"] is True and d["draft_retry"]["researched_at"]


def test_retry_reuses_research_runs_only_drafting_onward_and_creates_nothing_in_google_ads(flaky, business):
    t = _failed_build(business)
    before, steps_before = _counts(t["id"]), len(t["steps"])
    flaky.down = False  # the provider has recovered
    r = Api(ADMIN).post(f"/tasks/{t['id']}/retry-draft?sync=true")
    assert r.status_code == 200, r.text
    t2 = r.json()
    assert t2["status"] == "AWAITING_APPROVAL", t2["error"]
    assert _counts(t["id"]) == before  # no audit, keyword research or feasibility was run again
    new_steps = [s["name"] for s in t2["steps"][steps_before:]]
    assert new_steps[0] == "AI draft (retry, attempt 2)" and "Keyword research" not in new_steps and "Feasibility" not in new_steps
    assert new_steps[-1] == "Approval request"
    assert t2["steps"][:steps_before] == t["steps"]  # history is preserved
    a = t2["draft_attempts"]
    assert [x["status"] for x in a] == ["failed", "succeeded"] and a[1]["kind"] == "retry" and a[1]["plan_id"] == t2["plan_id"]
    assert a[1]["requested_by"] and a[1]["requested_at"] and a[1]["finished_at"] and any(c["ok"] for c in a[1]["calls"])
    with session_scope() as db:
        appr = db.get(Approval, t2["approval_id"])
        assert appr.status in ("PENDING_APPROVAL", "VALIDATION_FAILED") and appr.task_id == t["id"]
        assert db.query(Operation).filter(Operation.approval_id == appr.id).count() == 0  # nothing executed: approval still required
    assert Api(ADMIN).get(f"/tasks/{t['id']}").json()["draft_retry"]["available"] is False


def test_a_retry_that_fails_again_says_why_and_can_be_retried_later(flaky, business):
    t = _failed_build(business)
    r = Api(ADMIN).post(f"/tasks/{t['id']}/retry-draft?sync=true")
    assert r.status_code == 200, r.text
    t2 = r.json()
    assert t2["status"] == "FAILED" and "HTTP 413" in t2["error"]
    assert [x["status"] for x in t2["draft_attempts"]] == ["failed", "failed"]
    assert t2["draft_retry"]["available"] is True  # an explicit click may try again; nothing retries on its own
    flaky.down = False
    t3 = Api(ADMIN).post(f"/tasks/{t['id']}/retry-draft?sync=true").json()
    assert t3["status"] == "AWAITING_APPROVAL" and [x["attempt"] for x in t3["draft_attempts"]] == [1, 2, 3]


def test_repeated_clicks_start_one_retry(flaky, business):
    t = _failed_build(business)
    with SessionLocal() as db:
        actor = db.query(User).filter(User.email == ADMIN).one()
        tasks_service.retry_draft(db, actor, t["id"])
        db.commit()
        with pytest.raises(Conflict):
            tasks_service.retry_draft(db, actor, t["id"])
    r = Api(ADMIN).post(f"/tasks/{t['id']}/retry-draft")
    assert r.status_code == 409
    with session_scope() as db:
        task = db.get(Task, t["id"])
        assert task.status == "RETRYING_DRAFT" and [a["status"] for a in task.fields["draft_attempts"]] == ["failed", "queued"]
    assert tasks_service.run_retry_draft(t["id"]) == "FAILED"
    assert tasks_service.run_retry_draft(t["id"]) == "skipped"  # a duplicate dispatch does nothing


def test_concurrent_retries_start_exactly_one(flaky, business):
    t = _failed_build(business)
    gate, results = threading.Barrier(4), []

    def click():
        with SessionLocal() as db:
            actor = db.query(User).filter(User.email == ADMIN).one()
            gate.wait()
            try:
                tasks_service.retry_draft(db, actor, t["id"])
                db.commit()
                results.append("started")
            except Conflict:
                db.rollback()
                results.append("conflict")
    threads = [threading.Thread(target=click) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(results) == ["conflict", "conflict", "conflict", "started"]
    with session_scope() as db:
        assert [a["status"] for a in db.get(Task, t["id"]).fields["draft_attempts"]] == ["failed", "queued"]


def test_missing_research_is_explained_and_blocks_the_retry(flaky, business):
    t = _failed_build(business)
    with session_scope() as db:
        db.query(Audit).filter(Audit.task_id == t["id"], Audit.audit_type == "KEYWORD").delete()
    d = Api(ADMIN).get(f"/tasks/{t['id']}").json()
    assert d["draft_retry"]["available"] is False and "keyword research stored for this task is missing" in d["draft_retry"]["reason"]
    r = Api(ADMIN).post(f"/tasks/{t['id']}/retry-draft?sync=true")
    assert r.status_code == 422 and "Run the build again" in r.text
    with session_scope() as db:
        assert db.get(Task, t["id"]).status == "FAILED"  # unchanged


def test_stale_research_says_what_must_be_refreshed(flaky, business):
    t = _failed_build(business)
    with session_scope() as db:
        for a in db.query(Audit).filter(Audit.task_id == t["id"]):
            a.created_at = a.created_at - timedelta(days=tasks_service.RESEARCH_MAX_AGE_DAYS + 6)
    reason = Api(ADMIN).get(f"/tasks/{t['id']}").json()["draft_retry"]["reason"]
    assert "days old" in reason and "must be refreshed" in reason


def test_only_a_failed_drafting_stage_can_be_retried(flaky, business):
    flaky.down = False
    cid, bid = business
    t = Api(ADMIN).post("/tasks?sync=true", {"task_type": "BUILD_CAMPAIGN", "client_id": cid, "business_id": bid,
                                             "fields": {"monthly_budget": 3000, "weekly_target": 1}}).json()
    assert t["status"] == "AWAITING_APPROVAL"
    r = Api(ADMIN).post(f"/tasks/{t['id']}/retry-draft?sync=true")
    assert r.status_code == 422


# ------------------------------------------------------------------ live events of a task run
def _events(task_id: str) -> list[dict]:
    out, after = [], 0
    while True:
        r = Api(ADMIN).get(f"/tasks/{task_id}/events?after={after}").json()
        out += r["events"]
        after = r["last_seq"]
        if r["done"]:
            return out


def test_a_build_records_real_events_for_every_stage(flaky, business):
    flaky.down = False
    cid, bid = business
    t = Api(ADMIN).post("/tasks?sync=true", {"task_type": "BUILD_CAMPAIGN", "client_id": cid, "business_id": bid,
                                             "fields": {"monthly_budget": 3000, "weekly_target": 1}}).json()
    ev = _events(t["id"])
    started = {e["stage"] for e in ev if e["kind"] == "stage_started"}
    assert {"account", "measurement", "research", "website", "gbp", "competitors", "keywords", "overlap", "feasibility",
            "intelligence", "draft", "approval"} <= started
    feas = next(e for e in ev if e["kind"] == "metric_computed" and e["data"].get("metric") == "feasibility")
    assert feas["data"]["estimate"] is True and feas["data"]["monthly_budget"] == 3000
    plan = next(e for e in ev if e["kind"] == "plan_stored")
    assert plan["data"]["plan_id"] == t["plan_id"] and plan["data"]["ad_groups"]
    assert any(e["kind"] == "ai_request" and e["stage"] == "draft" for e in ev)
    assert any(e["kind"] == "page_extracted" for e in ev)  # the crawler's own events arrive through the task
    assert ev[-1]["kind"] == "audit_completed" and [e["seq"] for e in ev] == sorted({e["seq"] for e in ev})
    # the keyword audit created by the task shows its own activity too
    with session_scope() as db:
        kid = db.query(Audit).filter(Audit.task_id == t["id"], Audit.audit_type == "KEYWORD").one().id
    assert Api(ADMIN).get(f"/audits/{kid}/events").json()["events"]


def test_a_failed_draft_is_shown_as_failed_with_the_provider_reason(flaky, business):
    t = _failed_build(business)
    ev = _events(t["id"])
    failed = next(e for e in ev if e["kind"] == "stage_failed" and e["stage"] == "draft")
    assert "HTTP 413" in failed["data"]["summary"]
    assert any(e["kind"] == "ai_failed" and e["data"].get("error_kind") == "rate_limited" for e in ev)
    assert ev[-1]["kind"] == "audit_failed"
    r = Api(ADMIN).get(f"/tasks/{t['id']}/events?after={ev[-1]['seq']}").json()
    assert r["events"] == [] and r["done"] is True and r["last_seq"] == ev[-1]["seq"]
