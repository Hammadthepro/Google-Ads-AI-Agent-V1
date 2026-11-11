"""Conversion measurement is audited as findings with evidence. There is no status, gate or override, and nothing is blocked by it;
operations that genuinely depend on conversion data ask one specific question and explain the consequence."""
from __future__ import annotations

import pathlib
import re

from sqlalchemy import func, select

from app.db.session import session_scope
from app.models import Business, Campaign, FixTrackerItem, MeasurementAudit
from app.services import measurement, optimization
from tests.conftest import ADMIN, MANAGER, SUPER, Api
from tests.integration.test_intake_v2 import _fixture_client

ROOT = pathlib.Path(__file__).resolve().parents[3]
FINDING_KEYS = {"key", "inspected", "result", "actual", "evidence", "severity", "impact", "action", "action_type", "dependency"}


def _biz(api, name):
    return next(b for b in api.get("/businesses").json() if b["name"] == name)


def _run(api, bid):
    r = api.post(f"/businesses/{bid}/measurement/run")
    assert r.status_code == 200, r.text
    return r.json()


def _by(audit):
    return {f["key"]: f for f in audit["findings"]}


# ------------------------------------------------------------------------------------------------ the old workflow is gone
def test_no_tracking_status_workflow_remains_anywhere_in_the_product():
    banned = re.compile(r"tracking[_-]gate|TrackingGate|TrackingOverride|tracking_state|effective_state|override_active|BLOCKED_TRACKING|tracking\.override"
                        r"|\bAMBER\b|\bYELLOW\b|[\"']GREEN[\"']|[\"']RED[\"']|TRACKING_UNKNOWN|TRACKING_NOT_VERIFIED")
    hits = []
    for base, pattern in ((ROOT / "backend" / "app", "*.py"), (ROOT / "frontend" / "src", "*.ts*")):
        for p in base.rglob(pattern):
            if "/src/test/" in str(p):  # tests may name the retired words in order to assert their absence
                continue
            for i, line in enumerate(p.read_text().splitlines(), 1):
                if banned.search(line):
                    hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()[:90]}")
    assert not hits, "\n".join(hits)


def test_the_old_endpoints_permission_and_task_status_do_not_exist():
    api = Api(SUPER)
    bid = _biz(api, "Northside Plumbing")["id"]
    for path in (f"/businesses/{bid}/tracking-gate", f"/businesses/{bid}/tracking-gate/run", f"/businesses/{bid}/tracking-gate/override"):
        assert api.get(path).status_code in (404, 405) and api.post(path, {"reason": "a long enough reason to pass validation"}).status_code in (404, 405)
    assert "tracking.override" not in api.get("/auth/me").json()["user"]["permissions"]
    b = api.get(f"/businesses/{bid}").json()
    assert "tracking" not in b and "override" not in b and "state" not in b["measurement"]
    dash = api.get("/dashboard").json()
    assert all("tracking" not in c for c in dash.get("clients", dash if isinstance(dash, list) else []))


# ------------------------------------------------------------------------------------------------ findings, each on its own evidence
def test_without_an_account_account_side_checks_are_not_assessable_and_nothing_is_a_problem():
    api = Api(ADMIN)
    _, bid = _fixture_client(api, "Measure No Account Co")
    a = _run(api, bid)
    assert set(a) >= {"findings", "facts", "counts", "conversion_bidding"} and not ({"state", "status", "effective_state", "fix_list"} & set(a))
    assert all(FINDING_KEYS <= set(f) for f in a["findings"])
    f = _by(a)
    for key in ("account", "conversion_actions", "primary_conversion", "counting", "conversion_values", "attribution", "recorded_conversions"):
        assert f[key]["result"] == "not_assessable" and f[key]["severity"] is None and f[key]["dependency"], key
    assert a["counts"]["issue"] == 0  # missing evidence is never reported as a problem
    assert f["google_tag"]["result"] == "ok"  # what CAN be read (the website) is reported as found
    assert a["conversion_bidding"]["supported"] is None and "not known" in a["conversion_bidding"]["reason"]
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(FixTrackerItem).where(FixTrackerItem.business_id == bid, FixTrackerItem.source == "measurement_audit")) == 0


def test_confirmed_problems_carry_evidence_impact_and_a_corrective_action():
    api = Api(ADMIN)
    d = _biz(api, "Brightsmile Dental")
    a = _run(api, d["id"])
    f = _by(a)
    for key in ("google_tag", "counting", "duplicates", "recorded_conversions"):
        x = f[key]
        assert x["result"] == "issue" and x["severity"] in ("high", "medium") and x["evidence"] and x["impact"] and x["action"] and x["action_type"] == "needs_approval", key
    assert "counted twice" in f["duplicates"]["impact"] and "Conversions stopped" in f["recorded_conversions"]["evidence"]
    assert a["facts"]["counting_reliable"] is False and a["facts"]["suspected_outage"] is True
    assert a["conversion_bidding"]["supported"] is False and "stopped being recorded" in a["conversion_bidding"]["reason"]
    assert a["findings"][0]["result"] == "issue"  # problems first
    fixes = api.get(f"/fixes?client_id={d['client_id']}").json()
    assert any(x["category"] == "measurement" for x in fixes)  # client work only for confirmed problems


def test_working_measurement_reports_what_was_found_and_what_could_not_be_read():
    api = Api(ADMIN)
    a = _run(api, _biz(api, "Northside Plumbing")["id"])
    f = _by(a)
    assert f["conversion_actions"]["result"] == "ok" and f["primary_conversion"]["result"] == "ok" and f["recorded_conversions"]["result"] == "ok"
    assert "conversions from" in f["recorded_conversions"]["actual"] and a["counts"]["issue"] == 0
    assert a["conversion_bidding"]["supported"] is True
    assert f["enhanced_conversions"]["result"] == "not_assessable"  # said honestly, not passed
    with session_scope() as db:
        ok, why = measurement.conversion_data_reliable(db, db.get(Business, a["business_id"]))
        assert ok and "counted once" in why


def test_too_few_conversions_is_attention_with_its_reason_not_a_verdict():
    api = Api(ADMIN)
    a = _run(api, _biz(api, "Northside Heating & Air")["id"])
    f = _by(a)
    assert f["recorded_conversions"]["result"] in ("attention", "not_assessable") and f["recorded_conversions"]["severity"] is None
    with session_scope() as db:
        ok, why = measurement.conversion_data_reliable(db, db.get(Business, a["business_id"]))
        assert ok is False and why


# ------------------------------------------------------------------------------------------------ nothing is blocked; specific dependencies are explained
def test_measurement_problems_do_not_block_preparation_and_bidding_follows_the_specific_fact():
    api, mgr = Api(ADMIN), Api(MANAGER)
    d = _biz(api, "Brightsmile Dental")
    r = mgr.post("/tasks?sync=true", {"task_type": "BUILD_CAMPAIGN", "client_id": d["client_id"], "business_id": d["id"],
                                      "fields": {"monthly_budget": 2000, "weekly_target": 4, "services": ["Emergency Dentist"]}})
    t = r.json()
    assert t["status"] == "AWAITING_APPROVAL", (t["status"], t["error"])
    step = next(s for s in t["steps"] if s["name"] == "Conversion measurement audit")
    assert step["status"] == "DONE" and "issue(s)" in step["summary"]
    ap = api.get(f"/approvals/{t['approval_id']}").json()
    assert ap["status"] == "PENDING_APPROVAL" and not any("TRACK" in e["code"] for e in ap["validation"]["errors"] + ap["validation"]["warnings"])
    camp = next(o for o in ap["payload"]["operations"] if o["resource"] == "campaign")
    assert "target_spend" in camp["fields"] and "maximize_conversions" not in camp["fields"]  # nothing is recording, so not conversion bidding
    assert ap["context"]["measurement"]["conversion_bidding"]["supported"] is False
    assert any(o["category"] == "MEASUREMENT" for o in ap["context"]["challenger"]["objections"])  # raised as a finding, not as a gate


def test_recommendations_judged_by_conversions_are_held_for_a_named_data_reason():
    api = Api(ADMIN)
    with session_scope() as db:
        d = db.scalar(select(Business).where(Business.name == "Brightsmile Dental"))
        c = db.scalar(select(Campaign).where(Campaign.business_id == d.id, Campaign.campaign_type == "SEARCH"))
        chk = next(x for x in optimization.enough_data(db, c)["checks"] if x["key"] == "conversion_data")
        assert chk["passed"] is False and chk["label"] == "Conversions are recorded and counted once" and chk["value"]
        assert all(x["key"] != "tracking" for x in optimization.enough_data(db, c)["checks"])
        plain = optimization.enough_data(db, c, needs_conversions=False)
        assert all(x["key"] != "conversion_data" for x in plain["checks"])  # advice that does not use conversions is not held by it
    r = api.post("/experiments", {"campaign_id": c.id, "variable": "MATCH_TYPE", "hypothesis": "Broad match finds more leads", "treatment": {"match_type": "BROAD"}})
    assert r.status_code == 422 and "judged by conversions" in r.text


def test_linking_an_account_reaudits_and_an_outdated_audit_is_not_shown_as_current():
    api = Api(ADMIN)
    cid, bid = _fixture_client(api, "Measure Link Co")
    _run(api, bid)
    acct = next(a for a in api.post("/ads-accounts/discover").json() if a["customer_id"] == "1110000004")
    with session_scope() as db:
        n0 = db.scalar(select(func.count()).select_from(MeasurementAudit).where(MeasurementAudit.business_id == bid))
    assert api.post(f"/ads-accounts/{acct['id']}/link", {"client_id": cid}).status_code == 200
    try:
        got = api.get(f"/businesses/{bid}/measurement").json()
        with session_scope() as db:
            assert db.scalar(select(func.count()).select_from(MeasurementAudit).where(MeasurementAudit.business_id == bid)) == n0 + 1
        assert got["account_assessed"] is True and _by(got)["conversion_actions"]["result"] != "not_assessable" and "outdated" not in got
    finally:
        api.post(f"/ads-accounts/{acct['id']}/link", {"client_id": None})
    gone = api.get(f"/businesses/{bid}/measurement").json()
    assert gone.get("outdated") is None or gone["findings"] == [] or gone["account_assessed"] is False  # re-audited without the account, or flagged outdated
