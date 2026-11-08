"""Intake validation, budget recommendation + team decision, client-level weekly targets."""
from __future__ import annotations

from app.services import feasibility
from tests.conftest import ADMIN, MANAGER, SUPER, Api


def _setup(admin, **biz):
    c = admin.post("/clients", {"name": "Intake Test Co", "owner_contact": "o@x.example"}).json()["id"]
    base = {"name": "Intake Biz", "business_type": "home_services", "website_url": "https://www.northsideplumbing.example/",
            "phone": "(555) 201-4400", "address": "1 Main St, Columbus, OH", "verified_claims": ["Licensed & Insured"],
            "services": [{"name": "Drain Cleaning", "landing_page_url": "https://www.northsideplumbing.example/drain-cleaning"}]}
    base.update(biz)
    b = admin.post(f"/clients/{c}/businesses", base).json()["id"]
    acct = next(a for a in admin.post("/ads-accounts/discover").json() if a["customer_id"] == "1110000004")
    r = admin.post(f"/ads-accounts/{acct['id']}/link", {"client_id": c})  # the sandbox account is re-linked to the newest test client
    assert r.status_code == 200, r.text
    return c, b


def _validate(admin, c, b, fields):
    return admin.post("/intake/validate", {"client_id": c, "business_id": b, "fields": fields}).json()


def test_blockers_questions_and_warnings():
    admin = Api(ADMIN)
    c, b = _setup(admin, phone="", lead_form_url="", gbp_url="")
    rep = _validate(admin, c, b, {"monthly_budget": 100, "weekly_target": 5, "services": ["Drain Cleaning", "Roof Repair"]})
    codes = {i["code"] for i in rep["issues"]}
    assert rep["status"] == "BLOCKED"
    assert {"NO_CONVERSION_PATH", "BUDGET_TOO_LOW", "UNKNOWN_SERVICE", "NO_GBP", "NO_HOURS"} <= codes
    r = admin.post("/tasks", {"task_type": "BUILD_CAMPAIGN", "client_id": c, "business_id": b,
                              "fields": {"monthly_budget": 100, "weekly_target": 5, "services": ["Drain Cleaning"]}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "intake_blocked"


def test_implausible_target_needs_a_written_clarification():
    admin = Api(ADMIN)
    c, b = _setup(admin)
    fields = {"monthly_budget": 600, "weekly_target": 40, "services": ["Drain Cleaning"]}
    rep = _validate(admin, c, b, fields)
    assert rep["status"] == "NEEDS_CLARIFICATION" and any(i["code"] == "IMPLAUSIBLE_CPL" for i in rep["issues"])
    r = admin.post("/tasks", {"task_type": "BUILD_CAMPAIGN", "client_id": c, "business_id": b, "fields": fields})
    assert r.status_code == 422 and r.json()["error"]["code"] == "clarification_required"
    fields["clarifications"] = {"IMPLAUSIBLE_CPL": "Client confirmed: low-ticket service with cheap clicks"}
    assert _validate(admin, c, b, fields)["status"] == "OK"
    r = admin.post("/tasks", {"task_type": "BUILD_CAMPAIGN", "client_id": c, "business_id": b, "fields": fields})
    assert r.status_code == 200 and r.json()["intake"]["status"] == "OK"


def test_client_level_weekly_target_and_report():
    sup = Api(SUPER)
    client = next(c for c in sup.get("/clients").json() if c["name"] == "Northside Home Services")
    assert client["weekly_conversion_target"] > 0  # backfilled from campaign targets by migration/seed
    r = sup.patch(f"/clients/{client['id']}", {"weekly_conversion_target": 21, "monthly_budget_usd": 5000})
    assert r.status_code == 200 and r.json()["weekly_conversion_target"] == 21
    assert sup.patch(f"/clients/{client['id']}", {"weekly_conversion_target": -1}).status_code == 422
    rep = sup.post(f"/reports/generate/{client['id']}").json()
    totals = rep["metrics"]["totals"]
    assert totals["client_target"] == 21 and totals["client_gap"] == round(totals["conversions"] - 21, 1)
    assert "campaign" in str(rep["metrics"]["campaigns"][0].keys()).lower() or rep["metrics"]["campaigns"][0]["target"] is not None


def test_recommendation_and_adjustment_options_are_deterministic():
    r = feasibility.calculate(monthly_budget=1000, weekly_target=20, cpc_range=(9, 7, 5), cvr_range=(0.03, 0.06, 0.09))
    assert r["status"] == "UNREALISTIC"
    rec = r["recommended_monthly_budget"]
    assert rec >= r["budget_needed_for_target_monthly"] and rec % 50 == 0
    opts = {o["option"]: o for o in r["adjustment_options"]}
    assert {"RAISE_BUDGET", "LOWER_TARGET", "NARROW_SCOPE"} <= set(opts)
    assert opts["LOWER_TARGET"]["weekly_target"] == r["expected_weekly_conversions"]
    again = feasibility.calculate(monthly_budget=rec, weekly_target=20, cpc_range=(9, 7, 5), cvr_range=(0.03, 0.06, 0.09))
    assert again["status"] in ("FEASIBLE", "PARTIALLY_FEASIBLE")  # accepting the recommendation actually helps
    ok = feasibility.calculate(monthly_budget=9000, weekly_target=5, cpc_range=(9, 7, 5), cvr_range=(0.03, 0.06, 0.09))
    assert ok["status"] == "FEASIBLE" and ok["adjustment_options"] == []


def test_budget_decision_only_when_waiting_and_by_authorized_user():
    mgr = Api(MANAGER)
    admin = Api(ADMIN)
    c, b = _setup(admin)
    t = admin.post("/tasks?sync=true", {"task_type": "BUILD_CAMPAIGN", "client_id": c, "business_id": b,
                                        "fields": {"monthly_budget": 400, "weekly_target": 9, "services": ["Drain Cleaning"]}}).json()
    assert t["status"] in ("AWAITING_BUDGET_DECISION", "AWAITING_APPROVAL")
    if t["status"] == "AWAITING_BUDGET_DECISION":
        assert mgr.post(f"/tasks/{t['id']}/budget-decision", {"decision": "ACCEPT_RECOMMENDED"}).status_code == 403  # not assigned
        bad = admin.post(f"/tasks/{t['id']}/budget-decision", {"decision": "ADJUST"})
        assert bad.status_code == 422
        ok = admin.post(f"/tasks/{t['id']}/budget-decision?sync=true", {"decision": "ADJUST", "weekly_target": 2, "note": "lower target"})
        assert ok.status_code == 200 and ok.json()["fields"]["weekly_target"] == 2
    again = admin.post(f"/tasks/{t['id']}/budget-decision", {"decision": "KEEP_CURRENT", "note": "x" * 20})
    assert again.status_code == 422  # no longer waiting
