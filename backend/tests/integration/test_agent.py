"""The conversational agent: understanding, planning against the intelligence record (run / reuse / skip), reuse of valid research,
staleness detection, clarification, confirmation before anything consequential, resumable runs, access control."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.adapters import registry
from app.db.session import session_scope
from app.models import AgentRun, AuditReport, Competitor, FeasibilityResult, IntelligenceRun, Task
from app.services import agent_service as agent
from tests.conftest import ADMIN, ANALYST, MANAGER, SUPER, Api
from tests.integration.test_intake_v2 import _fixture_client


def _count(model, **where) -> int:
    with session_scope() as db:
        q = select(func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return db.scalar(q)


def _open(api: Api, cid: str | None) -> dict:
    r = api.post("/agent/conversations", {"client_id": cid})
    assert r.status_code == 200, r.text
    return r.json()


def _say(api: Api, conv: str, text: str, **kw) -> dict:
    r = api.post(f"/agent/conversations/{conv}/messages?sync=true", {"text": text, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _last_run(t: dict) -> dict:
    return list(t["runs"].values())[-1]


@pytest.fixture(scope="module")
def world():
    api = Api(ADMIN)
    cid, bid = _fixture_client(api, "Agent Co", weekly_target=6)
    return api, cid, bid


# ------------------------------------------------------------------------------------------------ the thread
def test_one_persistent_thread_per_client_with_a_greeting_and_suggestions(world):
    api, cid, _ = world
    a, b = _open(api, cid), _open(api, cid)
    assert a["id"] == b["id"] and a["client_id"] == cid
    assert a["messages"][0]["role"] == "agent" and "Nothing is changed in Google Ads without your explicit approval" in a["messages"][0]["content"]
    assert any("Audit" in s for s in a["messages"][0]["data"]["suggestions"])
    assert api.post("/agent/conversations", {"client_id": cid, "new": True}).json()["id"] != a["id"]


# ------------------------------------------------------------------------------------------------ run, reuse, detect staleness
def test_audit_request_runs_missing_stages_then_reuses_them_and_reruns_only_after_a_change(world):
    api, cid, bid = world
    conv = _open(api, cid)["id"]
    t = _say(api, conv, "Audit this new client's business and website")
    run = _last_run(t)
    assert run["intent"] == "audit_business" and run["interpreted_by"] == "rules"
    assert [(s["key"], s["action"]) for s in run["plan"]] == [("research", "run"), ("modules", "run")]
    assert run["plan"][0]["reason"] == "No completed research exists yet" and all(s["status"] == "DONE" for s in run["plan"])
    assert run["status"] in ("COMPLETED", "COMPLETED_WITH_BLOCKERS")
    res = run["result"]
    assert res["sections"] and res["decision"] in ("ready", "ready_with_conditions", "blocked", "not_commercially_viable")
    item = next(i for s in res["sections"] for i in s["items"])
    assert {"source", "retrieved_at", "confidence", "verification", "origin"} <= set(item)  # evidence travels with every statement
    assert any("No Google Ads account is linked" in w for w in res["warnings"]) and res["account_linked"] is False
    reports, runs = _count(AuditReport, business_id=bid), _count(IntelligenceRun, business_id=bid)

    t2 = _say(api, conv, "Audit this client's business and website again")
    run2 = _last_run(t2)
    assert [(s["key"], s["action"]) for s in run2["plan"]] == [("research", "reuse"), ("modules", "reuse")]  # nothing valid is repeated
    assert "still matches the client's information" in run2["plan"][0]["reason"]
    assert _count(AuditReport, business_id=bid) == reports and _count(IntelligenceRun, business_id=bid) == runs
    assert run2["result"]["reused"]

    with session_scope() as db:  # the client's information changes
        db.add(Competitor(business_id=bid, name="Agent Rival", website_url="https://agentrival.example/"))
    t3 = _say(api, conv, "audit the website")
    run3 = _last_run(t3)
    assert run3["plan"][0]["action"] == "run" and "changed" in run3["plan"][0]["reason"]
    assert _count(AuditReport, business_id=bid) == reports + 1


def test_research_older_than_the_limit_is_refreshed(world):
    api, cid, bid = world
    conv = _open(api, cid, )["id"]
    _say(api, conv, "audit")  # make sure research exists and is current
    with session_scope() as db:
        r = db.scalar(select(AuditReport).where(AuditReport.business_id == bid).order_by(AuditReport.version.desc()))
        from datetime import timedelta
        r.finished_at = r.finished_at - timedelta(days=45)
    run = _last_run(_say(api, conv, "audit the business"))
    assert run["plan"][0]["action"] == "run" and "45 days old" in run["plan"][0]["reason"]


def test_status_is_read_only_and_names_what_is_out_of_date(world):
    api, cid, bid = world
    conv = _open(api, cid)["id"]
    with session_scope() as db:
        db.add(Competitor(business_id=bid, name="Another Rival", website_url="https://another.example/"))
    before = (_count(AuditReport, business_id=bid), _count(IntelligenceRun, business_id=bid))
    run = _last_run(_say(api, conv, "What is out of date?"))
    assert run["intent"] == "status" and run["plan"] == [] and run["status"] == "COMPLETED"
    assert "competitors" in run["result"]["stale_modules"]
    assert any("changed" in w for w in run["result"]["warnings"])
    assert (_count(AuditReport, business_id=bid), _count(IntelligenceRun, business_id=bid)) == before  # asking never runs anything


def test_budget_request_recalculates_and_updates_the_financial_stage(world):
    api, cid, bid = world
    conv = _open(api, cid)["id"]
    _say(api, conv, "audit")
    feas_before = _count(FeasibilityResult, business_id=bid)
    run = _last_run(_say(api, conv, "Recalculate the recommended budget"))
    assert run["intent"] == "recalculate_budget" and run["plan"][2]["key"] == "feasibility"
    assert _count(FeasibilityResult, business_id=bid) == feas_before + 1
    nums = run["result"]["sections"][0]
    assert nums["type"] == "numbers" and "estimate" in nums["title"].lower() and any(i["label"] == "Monthly" and i["value"].startswith("$") for i in nums["items"])
    assert "Planning arithmetic" in nums["note"]
    fin = api.get(f"/clients/{cid}/intelligence").json()
    assert next(m for m in fin["modules"] if m["module"] == "finance")["version"] >= 2


def test_keyword_request_shows_unavailable_metrics_and_names_the_missing_prerequisite(world, monkeypatch):
    api, cid, _ = world
    conv = _open(api, cid)["id"]
    run = _last_run(_say(api, conv, "Find profitable keywords for these services"))
    assert run["intent"] == "keyword_research" and run["result"]["sections"][0]["type"] == "table"
    monkeypatch.delitem(registry._overrides, "keyword_research")  # Keyword Planner not configured (no research customer)
    run = _last_run(_say(api, conv, "keywords"))
    assert any("Keyword Planner is not configured" in b["what"] and "research_customer_id" in b["needs"] for b in run["result"]["blockers"])


def test_strategy_request_reports_the_readiness_decision_with_conditions(world):
    api, cid, _ = world
    conv = _open(api, cid)["id"]
    run = _last_run(_say(api, conv, "Prepare a campaign strategy using the latest research"))
    assert run["intent"] == "prepare_strategy"
    res = run["result"]
    assert res["decision_label"] in ("Ready", "Ready with conditions", "Not ready", "Not commercially viable") and "Readiness:" in res["headline"]
    assert any("No Google Ads account is linked" in c for c in res.get("conditions", []))
    assert res["next_actions"][0]["say"] == "Create a campaign draft"


# ------------------------------------------------------------------------------------------------ understanding and clarification
def test_ties_go_to_the_ai_and_unreadable_requests_get_a_question():
    api = Api(ADMIN)
    cid, _ = _fixture_client(api, "Clarify Co")
    conv = _open(api, cid)["id"]
    t = _say(api, conv, "audit keywords")  # two intents tie on the rules
    assert _last_run(t)["interpreted_by"] == "ai"
    t = _say(api, conv, "zzz qqq")
    last = t["messages"][-1]
    assert last["kind"] == "clarification" and last["data"]["type"] == "intent" and len(last["data"]["options"]) >= 2
    pick = last["data"]["options"][1]["value"]
    t = api.post(f"/agent/conversations/{conv}/messages?sync=true", {"text": "", "choice": {"value": pick}}).json()
    assert _last_run(t)["interpreted_by"] == "selection" and _last_run(t)["intent"] == pick


def test_a_conversation_without_a_client_asks_which_one_and_remembers_the_answer():
    api = Api(ADMIN)
    cid, _ = _fixture_client(api, "Unique Pelican Plumbing")
    conv = _open(api, None)["id"]
    t = _say(api, conv, "What is out of date?")
    q = t["messages"][-1]
    assert q["kind"] == "clarification" and q["data"]["type"] == "client" and q["data"]["options"]
    t = _say(api, conv, "", choice={"value": cid})
    assert t["client_id"] == cid and _last_run(t)["intent"] == "status"  # the original request continued, no need to repeat it
    conv2 = _open(api, None)["id"]
    t = _say(api, conv2, "status for unique pelican plumbing")  # a client named in the sentence needs no question
    assert t["client_id"] == cid


# ------------------------------------------------------------------------------------------------ nothing consequential without confirmation
def test_creating_a_draft_needs_explicit_confirmation_and_only_requests_approval(world):
    api, cid, bid = world
    conv = _open(api, cid)["id"]
    tasks_before = _count(Task, business_id=bid)
    t = _say(api, conv, "Create a campaign draft")
    run = _last_run(t)
    assert run["status"] == "WAITING_FOR_CONFIRMATION" and t["pending"]["kind"] == "confirm"
    conf = run["result"]["confirmation"]
    assert "Nothing is created in Google Ads" in conf["describes"] and "approval REQUEST" in conf["describes"]
    assert _count(Task, business_id=bid) == tasks_before  # asking created nothing
    assert api.post(f"/agent/runs/{run['id']}/confirm", {"approve": False}).json()["status"] == "CANCELLED"
    assert _count(Task, business_id=bid) == tasks_before
    t = _say(api, conv, "Create a campaign draft")
    run = _last_run(t)
    out = api.post(f"/agent/runs/{run['id']}/confirm", {"approve": True})
    assert out.status_code == 200, out.text
    assert _count(Task, business_id=bid) == tasks_before + 1
    assert "Nothing was sent to Google Ads" in out.json()["message"]
    assert api.post(f"/agent/runs/{run['id']}/confirm", {"approve": True}).status_code == 409  # a confirmation works once


def test_a_role_without_permission_cannot_ask_for_or_confirm_a_draft(world):
    api, cid, _ = world
    sup = Api(SUPER)
    ana = next(u for u in sup.get("/users").json() if u["email"] == ANALYST)
    sup.patch(f"/users/{ana['id']}", {"client_ids": ana["client_ids"] + [cid]})
    a = Api(ANALYST)
    conv = _open(a, cid)["id"]
    r = a.post(f"/agent/conversations/{conv}/messages?sync=true", {"text": "Create a campaign draft"})
    assert r.status_code == 403
    assert _last_run(_say(a, conv, "What is out of date?"))["status"] == "COMPLETED"  # read-only requests are fine


# ------------------------------------------------------------------------------------------------ interruption and resume
def test_a_failed_run_resumes_from_the_unfinished_stage_without_repeating_finished_ones(world, monkeypatch):
    api = Api(ADMIN)
    cid, bid = _fixture_client(api, "Resume Co")
    conv = _open(api, cid)["id"]
    real = agent.STEPS["modules"]

    def boom(c):
        raise RuntimeError("worker died")
    monkeypatch.setitem(agent.STEPS, "modules", boom)
    t = _say(api, conv, "audit")
    run = _last_run(t)
    assert run["status"] == "FAILED" and run["resumable"] and [s["status"] for s in run["plan"]] == ["DONE", "FAILED"]
    assert "worker died" in run["error"] and t["messages"][-1]["kind"] == "error"
    reports = _count(AuditReport, business_id=bid)
    assert reports == 1
    monkeypatch.setitem(agent.STEPS, "modules", real)
    t = api.post(f"/agent/runs/{run['id']}/resume?sync=true").json()
    run = _last_run(t)
    assert run["status"] in ("COMPLETED", "COMPLETED_WITH_BLOCKERS") and [s["status"] for s in run["plan"]] == ["DONE", "DONE"]
    assert _count(AuditReport, business_id=bid) == reports  # the finished research stage was not run again
    assert api.post(f"/agent/runs/{run['id']}/resume").status_code == 409  # a finished run cannot be resumed


def test_a_run_whose_worker_vanished_becomes_interrupted_and_can_be_resumed(world):
    api = Api(ADMIN)
    cid, bid = _fixture_client(api, "Vanish Co")
    conv = _open(api, cid)["id"]
    t = _say(api, conv, "audit")
    rid = _last_run(t)["id"]
    from datetime import timedelta

    from app.core import clock
    with session_scope() as db:  # simulate a crash mid-run
        r = db.get(AgentRun, rid)
        r.status, r.heartbeat_at = "RUNNING", clock.now() - timedelta(minutes=agent.STALE_RUN_MINUTES + 1)
        plan = [dict(s) for s in r.plan]
        plan[1]["status"] = "RUNNING"
        r.plan = plan
    got = api.get(f"/agent/conversations/{conv}").json()
    assert got["runs"][rid]["status"] == "INTERRUPTED" and got["runs"][rid]["resumable"] and not got["busy"]
    t = api.post(f"/agent/runs/{rid}/resume?sync=true").json()
    assert t["runs"][rid]["status"] in ("COMPLETED", "COMPLETED_WITH_BLOCKERS")


def test_a_live_run_blocks_a_second_request_in_the_same_thread(world):
    api, cid, _ = world
    conv = _open(api, cid)["id"]
    t = _say(api, conv, "What is out of date?")
    rid = _last_run(t)["id"]
    from app.core import clock
    with session_scope() as db:
        r = db.get(AgentRun, rid)
        r.status, r.heartbeat_at = "RUNNING", clock.now()
    assert api.post(f"/agent/conversations/{conv}/messages?sync=true", {"text": "status"}).status_code == 409
    with session_scope() as db:
        db.get(AgentRun, rid).status = "COMPLETED"


# ------------------------------------------------------------------------------------------------ access
def test_conversations_are_private_and_respect_client_access(world):
    api, cid, _ = world
    conv = _open(api, cid)["id"]
    other = Api(SUPER)
    assert other.get(f"/agent/conversations/{conv}").status_code == 200  # a super admin may review
    mgr = Api(MANAGER)  # not assigned to this client
    assert mgr.post("/agent/conversations", {"client_id": cid}).status_code == 403
    assert mgr.get(f"/agent/conversations/{conv}").status_code == 404
    assert Api(ADMIN).get("/agent/conversations/doesnotexist").status_code == 404
    assert api.post(f"/agent/conversations/{conv}/messages", {"text": "x" * 2001}).status_code == 422
    assert api.post(f"/agent/conversations/{conv}/messages", {"text": "  "}).status_code == 422


def test_a_queued_run_counts_as_busy_so_the_page_keeps_polling_until_it_finishes(world):
    """Found in a real-browser check: a run that was queued but not started yet reported 'not busy', so the page never refreshed."""
    api, cid, _ = world
    conv = _open(api, cid)["id"]
    r = api.post(f"/agent/conversations/{conv}/messages", {"text": "What is out of date?"})  # no sync: the run is dispatched to a background thread
    assert r.status_code == 200
    import time
    deadline = time.time() + 30
    t = r.json()
    while time.time() < deadline and t["busy"]:
        time.sleep(0.3)
        t = api.get(f"/agent/conversations/{conv}").json()
    assert not t["busy"] and _last_run(t)["status"] == "COMPLETED"
    # a planned run (dispatch never happened) is busy, blocks a second request, and becomes interruptible once stale
    with session_scope() as db:
        run = db.get(AgentRun, _last_run(t)["id"])
        run.status = "PLANNED"
    t = api.get(f"/agent/conversations/{conv}").json()
    assert t["busy"] is True
    assert api.post(f"/agent/conversations/{conv}/messages?sync=true", {"text": "status"}).status_code == 409
    from datetime import timedelta

    from app.core import clock
    with session_scope() as db:
        db.get(AgentRun, run.id).heartbeat_at = clock.now() - timedelta(minutes=agent.STALE_RUN_MINUTES + 1)
    t = api.get(f"/agent/conversations/{conv}").json()
    assert t["busy"] is False and t["runs"][run.id]["status"] == "INTERRUPTED"
