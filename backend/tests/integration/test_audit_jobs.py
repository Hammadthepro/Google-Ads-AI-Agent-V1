"""Audit lifecycle and keyword-research orchestration regressions:
* keyword research never starts website / presence / competitor audits without an explicit choice (and confirmation for fresh runs);
* only the requested audits run; every audit ends COMPLETED / FAILED / SKIPPED with times, a source and a summary or the real error;
* a caller rollback or a dead process cannot leave an audit RUNNING; abandoned jobs are failed with the reason;
* every audit has its own live events, and its detail payload works in every state.
"""
from __future__ import annotations

import time
from datetime import timedelta

from sqlalchemy import func, select

from app.core import clock
from app.db.session import session_scope
from app.models import AgentConversation, Audit, AuditReport, Business, Client
from app.services import audit_jobs, audits
from tests.conftest import ADMIN, Api

BIZ = "Northside Plumbing"


def _biz_id() -> str:
    with session_scope() as db:
        return db.scalar(select(Business.id).where(Business.name == BIZ))


def _count(**where) -> int:
    with session_scope() as db:
        q = select(func.count(Audit.id))
        for k, v in where.items():
            q = q.where(getattr(Audit, k) == v)
        return db.scalar(q)


def test_keyword_research_without_a_choice_starts_nothing_and_explains_the_sources():
    api, bid = Api(ADMIN), _biz_id()
    before = _count(business_id=bid)
    r = api.post("/audits", {"business_id": bid, "audit_type": "KEYWORD"})
    assert r.status_code == 409
    body = r.json()["error"] if "error" in r.json() else r.json()
    details = body.get("details") or {}
    assert details.get("choice_required") is True and {s["type"] for s in details["sources"]["sources"]} == {"WEBSITE", "GBP", "COMPETITOR"}
    assert _count(business_id=bid) == before  # no job was created


def test_fresh_audits_need_confirmation_and_only_the_chosen_sources_run():
    api, bid = Api(ADMIN), _biz_id()
    before = _count(business_id=bid)
    r = api.post("/audits", {"business_id": bid, "audit_type": "KEYWORD", "keyword_mode": "refresh", "refresh": ["WEBSITE"]})
    assert r.status_code == 409 and _count(business_id=bid) == before
    rows = api.post("/audits", {"business_id": bid, "audit_type": "KEYWORD", "keyword_mode": "refresh", "refresh": ["WEBSITE"], "confirm": True}).json()
    assert [a["audit_type"] for a in rows] == ["WEBSITE", "KEYWORD"]  # no GBP or competitor audit was started
    assert _count(business_id=bid) == before + 2
    kw = rows[-1]
    assert kw["status"] == "COMPLETED" and kw["result"]["inputs"]["WEBSITE"]["fresh"] is True
    assert kw["result"]["inputs"]["COMPETITOR"] is None or kw["result"]["inputs"]["COMPETITOR"]["audit_id"]  # reused, never re-run


def test_reuse_runs_only_keyword_research_on_existing_audits():
    api, bid = Api(ADMIN), _biz_id()
    api.post("/audits", {"business_id": bid, "audit_type": "ALL"})
    src = api.get(f"/businesses/{bid}/keyword-research/sources").json()
    assert {s["state"] for s in src["sources"]} <= {"usable", "outdated", "insufficient", "missing", "running"}
    before = {t: _count(business_id=bid, audit_type=t) for t in ("WEBSITE", "GBP", "COMPETITOR")}
    rows = api.post("/audits", {"business_id": bid, "audit_type": "KEYWORD", "keyword_mode": "reuse"}).json()
    assert [a["audit_type"] for a in rows] == ["KEYWORD"]
    assert {t: _count(business_id=bid, audit_type=t) for t in before} == before
    used = rows[0]["result"]["inputs"]
    assert used["WEBSITE"]["audit_id"] == next(s["audit_id"] for s in src["sources"] if s["type"] == "WEBSITE")


def test_one_requested_audit_runs_alone():
    api, bid = Api(ADMIN), _biz_id()
    before = {t: _count(business_id=bid, audit_type=t) for t in ("WEBSITE", "GBP", "COMPETITOR")}
    rows = api.post("/audits", {"business_id": bid, "audit_type": "WEBSITE"}).json()
    assert [a["audit_type"] for a in rows] == ["WEBSITE"] and rows[0]["status"] == "COMPLETED"
    assert _count(business_id=bid, audit_type="GBP") == before["GBP"] and _count(business_id=bid, audit_type="COMPETITOR") == before["COMPETITOR"]
    a = rows[0]
    assert a["finished_at"] and a["source_type"] != "NONE" and a["summary"]


def test_a_crashing_audit_is_failed_with_its_error_and_never_left_running(monkeypatch):
    def boom(ctx, task_id):
        raise RuntimeError("crawler exploded")
    monkeypatch.setattr(audits, "website_audit", boom)
    rows = Api(ADMIN).post("/audits", {"business_id": _biz_id(), "audit_type": "WEBSITE"}).json()
    a = rows[0]
    assert a["status"] == "FAILED" and "crawler exploded" in a["error"] and a["finished_at"]
    detail = Api(ADMIN).get(f"/audits/{a['id']}").json()
    assert detail["retryable"] is True and detail["business"]["name"] == BIZ


def test_final_statuses_survive_a_caller_rollback():
    with session_scope() as db:
        biz = db.get(Business, _biz_id())
        par = audits.run_parallel(db, biz, task_id=None, actor_id=None)
        db.rollback()  # e.g. a later step of the caller failed
    with session_scope() as db:
        st = {db.get(Audit, i).status for i in par["audit_ids"].values()}
    assert st <= {"COMPLETED", "FAILED", "SKIPPED"}


def test_abandoned_jobs_are_failed_with_the_reason_and_live_ones_are_left_alone():
    with session_scope() as db:
        biz = db.get(Business, _biz_id())
        old = Audit(client_id=biz.client_id, business_id=biz.id, audit_type="GBP", status="RUNNING", started_at=clock.real_now() - timedelta(hours=2))
        queued = Audit(client_id=biz.client_id, business_id=biz.id, audit_type="COMPETITOR", status="QUEUED", started_at=clock.real_now() - timedelta(hours=1))
        fresh = Audit(client_id=biz.client_id, business_id=biz.id, audit_type="WEBSITE", status="RUNNING", started_at=clock.real_now())
        db.add_all([old, queued, fresh])
        db.flush()
        ids = old.id, queued.id, fresh.id
    listing = Api(ADMIN).get("/audits").json()
    by = {a["id"]: a for a in listing}
    assert by[ids[0]]["status"] == "FAILED" and "Interrupted" in by[ids[0]]["error"] and by[ids[0]]["finished_at"]
    assert by[ids[1]]["status"] == "FAILED"
    assert by[ids[2]]["status"] == "RUNNING"  # still within its time: not touched
    with session_scope() as db:
        db.get(Audit, ids[2]).status = "FAILED"


def test_abandoned_report_runs_are_failed_too():
    with session_scope() as db:
        biz = db.get(Business, _biz_id())
        stale = AuditReport(client_id=biz.client_id, business_id=biz.id, version=999, status="RUNNING",
                            steps=[{"key": "website", "name": "x", "status": "RUNNING", "summary": ""}], input_snapshot={}, input_hash="x",
                            started_at=clock.real_now() - timedelta(hours=3), source_type="NONE")
        db.add(stale)
        db.flush()
        sid = stale.id
        assert audit_jobs.recover_stale(db) >= 1
    with session_scope() as db:
        r = db.get(AuditReport, sid)
        assert r.status == "FAILED" and r.steps[0]["status"] == "FAILED"
        db.delete(r)


def test_each_audit_has_its_own_live_events():
    rows = Api(ADMIN).post("/audits", {"business_id": _biz_id(), "audit_type": "WEBSITE"}).json()
    ev = Api(ADMIN).get(f"/audits/{rows[0]['id']}/events").json()
    kinds = [e["kind"] for e in ev["events"]]
    assert ev["done"] and kinds[0] == "stage_started" and "url_crawling" in kinds and "page_extracted" in kinds and kinds[-1] == "stage_completed"
    assert all(e["stage"] == "website" for e in ev["events"])
    more = Api(ADMIN).get(f"/audits/{rows[0]['id']}/events?after={ev['last_seq']}").json()
    assert more["events"] == []


def test_background_runs_return_queued_audits_that_finish():
    api = Api(ADMIN)
    rows = api.post("/audits", {"business_id": _biz_id(), "audit_type": "COMPETITOR", "background": True}).json()
    assert rows[0]["status"] in ("QUEUED", "RUNNING") and rows[0]["result"] == {}
    q = api.get(f"/audits/{rows[0]['id']}").json()
    assert q["business"]["name"] == BIZ  # the detail payload is complete while it is still queued
    for _ in range(60):
        q = api.get(f"/audits/{rows[0]['id']}").json()
        if q["status"] not in ("QUEUED", "RUNNING"):
            break
        time.sleep(0.5)
    assert q["status"] in ("COMPLETED", "SKIPPED", "FAILED") and q["finished_at"]


def test_a_second_run_of_the_same_audit_is_refused_while_one_is_active():
    api, bid = Api(ADMIN), _biz_id()
    with session_scope() as db:
        biz = db.get(Business, bid)
        a = Audit(client_id=biz.client_id, business_id=bid, audit_type="GBP", status="RUNNING", started_at=clock.real_now())
        db.add(a)
        db.flush()
        aid = a.id
    try:
        r = api.post("/audits", {"business_id": bid, "audit_type": "GBP"})
        assert r.status_code == 409
    finally:
        with session_scope() as db:
            db.get(Audit, aid).status = "FAILED"


def test_the_agent_asks_before_keyword_research_starts_new_research(monkeypatch):
    from app.services import agent_service
    real = agent_service.research_decision
    monkeypatch.setattr(agent_service, "research_decision",
                        lambda db, biz, client, force=False: ("run", "You asked for fresh research") if force else ("run", "No completed research exists yet"))
    api = Api(ADMIN)
    with session_scope() as db:
        cid = db.scalar(select(Client.id).where(Client.name == "Summit Legal Partners"))
        reports = db.scalar(select(func.count(AuditReport.id)).where(AuditReport.client_id == cid))
    conv = api.post("/agent/conversations", {"client_id": cid}).json()["id"]
    t = api.post(f"/agent/conversations/{conv}/messages?sync=true", {"text": "research keywords"}).json()
    last = t["messages"][-1]
    if last["kind"] == "clarification" and last["data"]["type"] == "business":
        t = api.post(f"/agent/conversations/{conv}/messages?sync=true", {"text": "", "choice": {"value": last["data"]["options"][0]["value"]}}).json()
        last = t["messages"][-1]
    assert last["kind"] == "clarification", last
    if True:  # research would run: the agent must ask, and must not have started anything
        assert last["data"]["type"] == "research_choice" and {o["value"] for o in last["data"]["options"]} == {"reuse", "fresh"}
        with session_scope() as db:
            assert db.scalar(select(func.count(AuditReport.id)).where(AuditReport.client_id == cid)) == reports
        t = api.post(f"/agent/conversations/{conv}/messages?sync=true", {"text": "", "choice": {"value": "reuse"}}).json()
        run = list(t["runs"].values())[-1]
        step = next(s for s in run["plan"] if s["key"] == "research")
        assert step["action"] == "skip" and "You chose to use the available data" in step["reason"]
        with session_scope() as db:
            assert db.scalar(select(func.count(AuditReport.id)).where(AuditReport.client_id == cid)) == reports
            assert "research_choice" not in (db.get(AgentConversation, conv).context or {})
    assert real is not None
