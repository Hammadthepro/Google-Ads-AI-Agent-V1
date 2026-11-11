"""The tracking gate's four states are decided by EVIDENCE: missing evidence is UNKNOWN (never RED), only confirmed problems are RED.
Preparation is stopped only by RED; live deployment needs verified tracking (GREEN) or an explicit admin authorisation."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.session import session_scope
from app.models import Business, FixTrackerItem, TrackingGateResult
from app.services import tracking_gate, validation
from tests.conftest import ADMIN, SUPER, Api
from tests.integration.test_intake_v2 import _fixture_client

DENTAL_SITE = "https://www.brightsmiledental.example/"  # fixture site with no Google tag at all
STATES = {"GREEN", "YELLOW", "RED", "UNKNOWN"}


def _biz(api, name):
    """A seeded business with any admin override left by other tests cleared, so its state is judged on evidence alone."""
    b = next(b for b in api.get("/businesses").json() if b["name"] == name)
    with session_scope() as db:
        from app.models import TrackingOverride
        from app.core import clock
        for ov in db.scalars(select(TrackingOverride).where(TrackingOverride.business_id == b["id"], TrackingOverride.cleared_at.is_(None))):
            ov.cleared_at, ov.cleared_reason = clock.now(), "test isolation"
        # other tests move the simulated clock forward and leave future-dated results behind; they would shadow a fresh assessment
        db.query(TrackingGateResult).filter(TrackingGateResult.business_id == b["id"], TrackingGateResult.evaluated_at > clock.now()).delete()
    return b


def _gate(api, business_id):
    r = api.post(f"/businesses/{business_id}/tracking-gate/run")
    assert r.status_code == 200, r.text
    return r.json()


def _no_account_business(api, name, website=None):
    cid, bid = _fixture_client(api, name)
    if website:
        with session_scope() as db:
            b = db.get(Business, bid)
            b.website_url, b.lead_form_url, b.thank_you_url = website, website + "contact", website + "thank-you"  # every page the gate reads is on that site
    return cid, bid


# ------------------------------------------------------------------------------------------------ 1-4: missing evidence is UNKNOWN
def test_no_linked_account_is_unknown_and_creates_no_client_work():
    api = Api(ADMIN)
    _, bid = _no_account_business(api, "Gate Unknown Co")
    g = _gate(api, bid)
    assert g["state"] == "UNKNOWN" and g["effective_state"] == "UNKNOWN" and g["state"] in STATES
    assert g["blocks_preparation"] is False and g["deployment_ready"] is False and g["automation_allowed"] is False
    assert not any(c["critical"] for c in g["checks"]) and not any(c["status"] == "FAIL" and c["key"] in ("account_linked", "form_conversion", "google_tag") for c in g["checks"])
    acct = next(c for c in g["checks"] if c["key"] == "account_linked")
    assert acct["status"] == "UNKNOWN" and acct["assessable"] is False
    assert any("Link the client's Google Ads account" in o["what"] for o in g["outstanding"])  # what is still to verify is listed
    with session_scope() as db:  # missing evidence must not turn into tasks demanded from the client
        assert db.scalar(select(func.count()).select_from(FixTrackerItem).where(FixTrackerItem.business_id == bid, FixTrackerItem.source == "tracking_gate")) == 0


def test_unknown_never_becomes_green_by_itself_even_with_a_tag_detected():
    api = Api(ADMIN)
    _, bid = _no_account_business(api, "Gate Not Green Co")
    first, second = _gate(api, bid), _gate(api, bid)
    tag = next(c for c in first["checks"] if c["key"] == "google_tag")
    assert tag["status"] == "PASS"  # the site has the tag...
    assert first["state"] == second["state"] == "UNKNOWN"  # ...but without an account nothing can be VERIFIED
    assert first["automation_allowed"] is False


def test_a_site_without_a_tag_is_unknown_not_red_until_an_account_says_which_tag_should_exist():
    api = Api(ADMIN)
    _, bid = _no_account_business(api, "Gate Not Red Co", DENTAL_SITE)
    g = _gate(api, bid)
    tag = next(c for c in g["checks"] if c["key"] == "google_tag")
    assert tag["status"] == "UNKNOWN" and tag["critical"] is False and "not yet a failure" in tag["evidence"]
    assert g["state"] == "UNKNOWN" and g["blocks_preparation"] is False


def test_a_stale_cached_red_cannot_leak_through_old_rules_or_an_account_change():
    api = Api(ADMIN)
    _, bid = _no_account_business(api, "Gate Stale Co")
    with session_scope() as db:  # a RED row written by the OLD rules: no "assessable" evidence flags, taken with no account
        biz = db.get(Business, bid)
        db.add(TrackingGateResult(business_id=bid, google_ads_account_id=None, state="RED", effective_state="RED", override_active=False,
                                  checks=[{"key": "account_linked", "label": "Google Ads account linked", "status": "FAIL", "critical": True, "evidence": "No account linked", "fix": "Link it"}],
                                  fix_list=[], evaluated_at=__import__("app.core.clock", fromlist=["now"]).now(), source_type="NONE"))
        db.flush()
        a = tracking_gate.assessment(db, bid)
        assert a["stale"] and a["state"] == "UNKNOWN" and a["effective_state"] == "UNKNOWN" and a["previous_state"] == "RED"
        assert tracking_gate.effective_state(db, bid) == "UNKNOWN" and tracking_gate.current_result(db, biz) is None
    got = api.get(f"/businesses/{bid}/tracking-gate").json()
    assert got["state"] == "UNKNOWN" and got["stale"] is True and "must be re-run" in got["stale_reason"] and got["previous_state"] == "RED" and got["blocks_preparation"] is False
    mods = {m["module"]: m for m in api.get(f"/clients/{_client_of(api, bid)}/intelligence").json()["modules"]}
    assert not any(f["result"] == "FAIL" for f in mods["tracking"]["findings"])  # the record never shows the cached verdict


def _client_of(api, bid):
    return next(b for b in api.get("/businesses").json() if b["id"] == bid)["client_id"]


# ------------------------------------------------------------------------------------------------ 5-7: evidence decides RED / YELLOW / GREEN
def test_confirmed_broken_tracking_is_red():
    api = Api(ADMIN)
    g = _gate(api, _biz(api, "Brightsmile Dental")["id"])  # linked account, readable, no tag and no working conversion
    assert g["state"] == "RED" and g["blocks_preparation"] is True
    failed = [c for c in g["checks"] if c["status"] == "FAIL" and c["critical"]]
    assert failed and all(c["assessable"] for c in failed)  # every RED reason is a check whose evidence could be read
    assert any(o["status"] == "FAIL" for o in g["outstanding"])


def test_partially_configured_tracking_is_yellow():
    api = Api(ADMIN)
    g = _gate(api, _biz(api, "Northside Heating & Air")["id"])
    assert g["state"] == "YELLOW" and g["blocks_preparation"] is False and g["deployment_ready"] is False
    assert not any(c["critical"] for c in g["checks"]) and any(c["status"] in ("WARN", "FAIL", "UNKNOWN") for c in g["checks"])


def test_verified_tracking_is_green():
    api = Api(ADMIN)
    g = _gate(api, _biz(api, "Northside Plumbing")["id"])
    assert g["state"] == "GREEN" and g["deployment_ready"] is True and g["automation_allowed"] is True
    assert all(c["status"] == "PASS" or c["key"] in ("gtm",) for c in g["checks"])


def test_old_amber_rows_read_as_yellow_and_unrecognised_values_as_unknown():
    assert tracking_gate.normalize("AMBER") == "YELLOW" and tracking_gate.normalize(None) == "UNKNOWN" and tracking_gate.normalize("???") == "UNKNOWN"
    assert [tracking_gate.normalize(s) for s in ("GREEN", "YELLOW", "RED", "UNKNOWN")] == ["GREEN", "YELLOW", "RED", "UNKNOWN"]
    base = [{"status": "PASS", "critical": False}]
    assert tracking_gate.classify(base, account_readable=True, recorded_pass=True) == "GREEN"
    assert tracking_gate.classify(base, account_readable=True, recorded_pass=False) == "YELLOW"
    assert tracking_gate.classify(base, account_readable=False, recorded_pass=True) == "UNKNOWN"
    assert tracking_gate.classify([{"status": "UNKNOWN", "critical": False}], account_readable=True, recorded_pass=True) == "YELLOW"  # an unknown check is never GREEN
    assert tracking_gate.classify([{"status": "UNKNOWN", "critical": True}], account_readable=False, recorded_pass=False) == "UNKNOWN"  # and an unknown is never RED
    assert tracking_gate.classify([{"status": "FAIL", "critical": True}], account_readable=False, recorded_pass=False) == "RED"


# ------------------------------------------------------------------------------------------------ preparation is not blocked by UNKNOWN
def test_unknown_tracking_does_not_block_preparation_and_the_warning_lists_what_to_verify():
    api = Api(ADMIN)
    cid, bid = _no_account_business(api, "Gate Prepare Co")
    r = api.post("/tasks?sync=true", {"task_type": "BUILD_CAMPAIGN", "client_id": cid, "business_id": bid, "fields": {"weekly_target": 5}})
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["status"] == "AWAITING_APPROVAL", (t["status"], t["error"])
    step = next(s for s in t["steps"] if s["name"] == "Conversion tracking gate")
    assert step["status"] == "DONE" and "Tracking UNKNOWN" in step["summary"] and "preparation continues" in step["summary"] and "Still to verify before deployment" in step["summary"]
    ap = api.get(f"/approvals/{t['approval_id']}").json()
    assert ap["status"] == "PENDING_APPROVAL"
    w = next(x for x in ap["validation"]["warnings"] if x["code"] == "TRACKING_UNKNOWN")
    assert "does not stop drafting" in w["message"] and "Before deployment:" in w["message"]
    assert not any(e["code"].startswith("TRACKING") for e in ap["validation"]["errors"])
    assert ap["context"]["tracking"]["state"] == "UNKNOWN"
    rec = api.get(f"/clients/{cid}/intelligence").json()
    assert rec["decision"] != "blocked"
    tr = next(m for m in rec["modules"] if m["module"] == "tracking")
    assert tr["status"] in ("WAITING_FOR_INTEGRATION", "NOT_STARTED") and not any(f["result"] == "FAIL" for f in tr["findings"])


def test_confirmed_red_still_blocks_preparation():
    api = Api(ADMIN)
    d = _biz(api, "Brightsmile Dental")
    r = api.post("/tasks?sync=true", {"task_type": "BUILD_CAMPAIGN", "client_id": d["client_id"], "business_id": d["id"],
                                      "fields": {"monthly_budget": 2000, "weekly_target": 4, "services": ["Emergency Dentist"]}})
    assert r.json()["status"] == "BLOCKED_TRACKING" and "confirmed by evidence" in r.json()["error"]


# ------------------------------------------------------------------------------------------------ 8: deployment stays strict
def _deploy_payload(kind="BUILD_CAMPAIGN"):
    return {"kind": kind, "customer_id": "1110000004", "operations": [{"resource": "campaign", "op": "create", "temp_id": "-1",
                                                                          "fields": {"name": "Gate test", "status": "PAUSED", "advertising_channel_type": "SEARCH"}}], "summary": {}}


@pytest.mark.parametrize("name,state,prep_error,deploy_error", [
    ("Northside Plumbing", "GREEN", False, False),
    ("Northside Heating & Air", "YELLOW", False, True),
    ("Brightsmile Dental", "RED", True, True)])
def test_the_same_state_is_judged_differently_for_preparation_and_for_deployment(name, state, prep_error, deploy_error):
    api = Api(ADMIN)
    bid = _biz(api, name)["id"]
    assert _gate(api, bid)["effective_state"] == state
    with session_scope() as db:
        biz = db.get(Business, bid)
        for kind in ("BUILD_CAMPAIGN", "ENABLE"):
            prep = validation.validate(db, _deploy_payload(kind), biz, run_adapter=False, check_landing_pages=False)
            dep = validation.validate(db, _deploy_payload(kind), biz, run_adapter=False, check_landing_pages=False, deployment=True)
            assert any(e["code"].startswith("TRACKING") for e in prep.errors) is prep_error, (name, kind, prep.errors)
            assert any(e["code"].startswith("TRACKING") for e in dep.errors) is deploy_error, (name, kind, dep.errors)
            if state == "YELLOW":
                assert any(w["code"] == "TRACKING_YELLOW" and "Outstanding" in w["message"] for w in prep.warnings)


def test_unknown_blocks_live_deployment_unless_an_admin_explicitly_authorises_it():
    api = Api(ADMIN)
    _, bid = _no_account_business(api, "Gate Deploy Unknown Co")
    _gate(api, bid)
    with session_scope() as db:
        biz = db.get(Business, bid)
        prep = validation.validate(db, _deploy_payload(), biz, run_adapter=False, check_landing_pages=False)
        dep = validation.validate(db, _deploy_payload(), biz, run_adapter=False, check_landing_pages=False, deployment=True)
        assert not any(e["code"].startswith("TRACKING") for e in prep.errors) and any(w["code"] == "TRACKING_UNKNOWN" for w in prep.warnings)
        err = next(e for e in dep.errors if e["code"] == "TRACKING_NOT_VERIFIED")
        assert "verified conversion tracking (GREEN) or an explicit admin authorisation" in err["message"] and "UNKNOWN" in err["message"]
    assert Api("analyst@napollo.example").post(f"/businesses/{bid}/tracking-gate/override", {"reason": "I would like to launch anyway please"}).status_code in (403, 404)
    r = api.post(f"/businesses/{bid}/tracking-gate/override", {"reason": "Client confirmed their tag manager setup in writing; launching paused"})
    assert r.status_code == 200 and r.json()["effective_state"] == "YELLOW"  # the authorisation never makes tracking look better than YELLOW
    with session_scope() as db:
        dep = validation.validate(db, _deploy_payload(), db.get(Business, bid), run_adapter=False, check_landing_pages=False, deployment=True)
        assert not any(e["code"].startswith("TRACKING") for e in dep.errors) and any(w["code"] == "TRACKING" and "override" in w["message"] for w in dep.warnings)
    assert api.post(f"/businesses/{bid}/tracking-gate/override", {"reason": "Another reason that is long enough"}).status_code == 200  # idempotent


def test_an_override_does_not_bypass_approval_or_account_binding():
    api, approver = Api(ADMIN), Api(SUPER)
    cid, bid = _no_account_business(api, "Gate Safety Co")
    t = api.post("/tasks?sync=true", {"task_type": "BUILD_CAMPAIGN", "client_id": cid, "business_id": bid, "fields": {"weekly_target": 5}}).json()
    assert t["status"] == "AWAITING_APPROVAL"
    assert api.post(f"/businesses/{bid}/tracking-gate/override", {"reason": "Explicitly authorised for the test of account binding"}).status_code == 200
    ap = api.get(f"/approvals/{t['approval_id']}").json()
    assert approver.post(f"/approvals/{ap['id']}/execute").status_code == 423  # still needs approval
    assert approver.post(f"/approvals/{ap['id']}/approve", {"payload_hash": ap["payload_hash"]}).status_code == 200
    r = approver.post(f"/approvals/{ap['id']}/execute")
    assert r.status_code == 423 and "no Google Ads account" in r.text  # tracking authorisation does not supply a target account
    s = __import__("app.core.config", fromlist=["get_settings"]).get_settings()
    assert s.GOOGLE_ADS_WRITES_ENABLED is False and s.AUTO_NEGATIVES_MODE == "shadow"


# ------------------------------------------------------------------------------------------------ 9: linking reassesses
def test_linking_an_account_reassesses_tracking_and_unlinking_makes_it_unknown_again():
    api = Api(ADMIN)
    cid, bid = _no_account_business(api, "Gate Link Co")
    before = _gate(api, bid)
    assert before["state"] == "UNKNOWN"
    with session_scope() as db:
        n0 = db.scalar(select(func.count()).select_from(TrackingGateResult).where(TrackingGateResult.business_id == bid))
    acct = next(a for a in api.post("/ads-accounts/discover").json() if a["customer_id"] == "1110000004")
    assert api.post(f"/ads-accounts/{acct['id']}/link", {"client_id": cid}).status_code == 200
    try:
        with session_scope() as db:
            n1 = db.scalar(select(func.count()).select_from(TrackingGateResult).where(TrackingGateResult.business_id == bid))
            latest = tracking_gate.latest(db, bid)
            assert n1 == n0 + 1 and latest.google_ads_account_id == acct["id"]  # reassessed at once, against the account
        after = api.get(f"/businesses/{bid}/tracking-gate").json()
        assert after["stale"] is False and after["state"] in ("GREEN", "YELLOW", "RED")  # real evidence now exists: no longer UNKNOWN
        assert next(c for c in after["checks"] if c["key"] == "form_conversion")["assessable"] is True
        assert next((c for c in after["checks"] if c["key"] == "account_linked"), None) is None
    finally:
        assert api.post(f"/ads-accounts/{acct['id']}/link", {"client_id": None}).status_code == 200
    gone = api.get(f"/businesses/{bid}/tracking-gate").json()
    assert gone["state"] == "UNKNOWN" and gone["stale"] is True and "account link changed" in gone["stale_reason"]  # the old verdict is not reused
