"""Client profile form (create/edit/validate, unique client codes) and the five-part evidence-based audit report."""
from __future__ import annotations

import pytest

from app.services import client_profile as cp
from tests.conftest import ADMIN, ANALYST, MANAGER, Api

GOOD = {"name": "Acme Plumbing Test", "category": "plumber", "phone": "(512) 555-0100", "email": "leads@acme-test.example",
        "website": "acme-test-plumbing.example", "lead_form_url": "/contact", "thank_you_url": "/thank-you", "gtm_id": "GTM-ABC1234",
        "has_gbp": True, "services": ["Drain Cleaning", "Water Heater Repair"], "selling_points": ["Licensed & insured", "Same-day service", "Upfront pricing"],
        "service_areas": ["Austin, TX 78701", "Round Rock"], "states": ["Texas"], "targeting_mode": "AREAS", "monthly_budget": "1.5k",
        "competitors": [{"name": "Ace Plumbing", "website": "aceplumbing.com", "maps_url": ""}], "notes": "Call after 5pm"}


def _create(api, profile=None, audit="none"):
    r = api.post(f"/client-profiles?audit={audit}", {"profile": {**GOOD, **(profile or {})}})
    return r


def test_normalizers():
    assert cp.parse_budget("1.5k") == 1500 and cp.parse_budget("$1,500") == 1500 and cp.parse_budget("abc") is None
    assert cp.normalize_phone("+1 512 555 0100") == "(512) 555-0100" and cp.normalize_phone("123") is None
    assert cp.normalize_phone("+44 20 7946 0958") == "+442079460958"
    assert cp.normalize_url("business.com") == "https://business.com/" and cp.normalize_url("/contact", "https://b.com") == "https://b.com/contact"
    assert cp.parse_area("Austin, TX 78701") == {"label": "Austin, TX 78701", "city": "Austin", "state": "TX", "zip": "78701"}
    assert cp.parse_area("78701")["zip"] == "78701" and cp.parse_area("Austin, Texas")["state"] == "TX" and cp.parse_area("  ,") is None


def test_field_errors_as_you_type():
    admin = Api(ADMIN)
    r = admin.post("/client-profiles/validate", {"profile": {"name": "", "phone": "12", "website": "nodot", "monthly_budget": "lots", "gtm_id": "zzz",
                                                            "services": [], "service_areas": [], "targeting_mode": None}})
    errs = r.json()["errors"]
    assert not r.json()["valid"]
    assert {"name", "category", "phone", "website", "has_gbp", "services", "service_areas", "targeting_mode", "monthly_budget", "gtm_id"} <= set(errs)
    assert admin.post("/client-profiles/validate", {"profile": GOOD}).json()["valid"]


def test_create_assigns_unique_client_code_and_normalizes():
    admin = Api(ADMIN)
    a = _create(admin).json()
    assert a["client_code"].startswith("CL-") and len(a["client_code"]) == 9
    p = admin.get(f"/client-profiles/{a['client_id']}").json()
    assert p["phone"] == "(512) 555-0100" and p["website"] == "https://acme-test-plumbing.example/" and p["monthly_budget"] == 1500
    assert p["thank_you_url"] == "https://acme-test-plumbing.example/thank-you" and p["states"] == ["TX"] and p["service_areas"][0] == "Austin, TX 78701"
    b = _create(admin, {"name": "Second Co", "website": "second-co-test.example"}).json()
    assert b["client_code"] != a["client_code"]
    client = admin.get(f"/clients/{a['client_id']}").json()
    assert client["client_code"] == a["client_code"] and client["monthly_budget_usd"] == 1500
    # the same website cannot belong to two clients (prevents mixing data)
    dup = _create(admin)
    assert dup.status_code == 422 and "already belongs to client" in dup.json()["error"]["details"]["fields"]["website"]


def test_edit_preserves_hidden_data_and_reports_changes():
    admin = Api(ADMIN)
    a = _create(admin, {"name": "Edit Me Co", "website": "edit-me-test.example"}).json()
    cid = a["client_id"]
    biz = admin.get(f"/businesses/{a['business_id']}").json()
    svcs = biz["services"] + [{"name": "Septic Pumping", "is_excluded": True}]
    admin.patch(f"/businesses/{a['business_id']}", {"services": [{**s, "landing_page_url": "https://edit-me-test.example/drain" if s["name"] == "Drain Cleaning" else ""} for s in svcs]})
    form = admin.get(f"/client-profiles/{cid}").json()
    form["monthly_budget"] = "2,000"
    form["services"] = [*form["services"], "Leak Detection"]
    r = admin.c.put(f"/api/v1/client-profiles/{cid}", json={"profile": form}, headers=admin._h())
    assert r.status_code == 200, r.text
    assert set(r.json()["changed"]) == {"monthly_budget", "services"}
    biz = admin.get(f"/businesses/{a['business_id']}").json()
    names = {s["name"]: s for s in biz["services"]}
    assert names["Septic Pumping"]["is_excluded"] is True  # the "do not advertise" list survives an edit
    assert names["Drain Cleaning"]["landing_page_url"].endswith("/drain") and "Leak Detection" in names
    assert admin.get(f"/clients/{cid}").json()["client_code"] == a["client_code"]  # ID never changes
    bad = admin.c.put(f"/api/v1/client-profiles/{cid}", json={"profile": {**form, "phone": "x"}}, headers=admin._h())
    assert bad.status_code == 422 and "phone" in bad.json()["error"]["details"]["fields"]


def test_permissions_on_profiles():
    ana = Api(ANALYST)
    assert _create(ana).status_code == 403
    admin = Api(ADMIN)
    a = _create(admin, {"name": "Private Co", "website": "private-co-test.example"}).json()
    mgr = Api(MANAGER)  # manager is not assigned to the new client
    assert mgr.get(f"/client-profiles/{a['client_id']}").status_code == 403
    assert mgr.get(f"/clients/{a['client_id']}/audit-reports").status_code == 403


@pytest.fixture(scope="module")
def northside():
    admin = Api(ADMIN)
    client = next(c for c in admin.get("/clients").json() if c["name"] == "Northside Home Services")
    r = admin.post(f"/clients/{client['id']}/audit-reports?sync=true&business_id={client['businesses'][0]['id']}")
    assert r.status_code == 200, r.text
    return admin, client, r.json()


def test_audit_report_has_five_sections_scores_and_evidence(northside):
    admin, client, rep = northside
    assert rep["status"] == "COMPLETED" and rep["version"] >= 1
    assert set(rep["sections"]) == {"client", "website", "gbp", "competitors", "keywords"}
    ev = {"VERIFIED", "ESTIMATED", "AI_GENERATED", "UNAVAILABLE", "NOT_APPLICABLE"}
    allf = [f for s in rep["sections"].values() for f in s["findings"]]
    assert len(allf) > 80
    assert all(f["evidence"] in ev and f["date"] and f["status"] in {"PASS", "WARN", "FAIL", "INFO", "UNAVAILABLE", "NA"} for f in allf)
    assert all(f["recommendation"] for f in allf if f["status"] == "FAIL" and f["weight"] >= 2)
    assert all(isinstance(rep["scores"][k]["score"], int) for k in ("client", "website", "gbp", "competitors", "keywords", "overall"))
    assert all(0 <= rep["scores"][k]["score"] <= 100 for k in rep["scores"])
    assert "UNAVAILABLE" in {f["evidence"] for f in allf}  # economics etc. are reported as unavailable, not invented
    eco = [f for f in rep["sections"]["client"]["findings"] if f["area"].startswith("B.") and f["id"].split(".")[1] in ("aov", "margin", "ltv")]
    assert eco and all(f["status"] == "UNAVAILABLE" and f["needs"] == "client" for f in eco)
    assert any(f["simulated"] for f in allf)  # mock adapters are labelled as simulated
    r = rep["recommendations"]
    assert {"critical", "high", "medium", "low", "needs_client_clarification", "manual_verification", "next_steps"} <= set(r)
    assert r["needs_client_clarification"] and r["manual_verification"]
    assert len(rep["summary"]) >= 15 and "13_readiness" in rep["summary"]


def test_scoring_excludes_unavailable_and_na():
    from app.services.audit_report import score
    items = [{"status": "PASS", "weight": 3}, {"status": "FAIL", "weight": 1}, {"status": "UNAVAILABLE", "weight": 3}, {"status": "NA", "weight": 3}, {"status": "INFO", "weight": 3}]
    s = score(items)
    assert s["score"] == 75 and s["checks_scored"] == 2 and s["checks_unavailable"] == 1 and s["checks_not_applicable"] == 1 and s["coverage"] == 0.67
    assert score([{"status": "NA", "weight": 1}])["score"] is None


def test_keyword_report_never_fabricates_metrics(northside):
    admin, client, rep = northside
    kw = rep["sections"]["keywords"]["report"]
    assert {"keyword", "service", "intent", "monthly_searches", "avg_cpc", "competition", "top_of_page_bid", "match_type", "landing_page", "priority", "source"} <= set(kw["columns"])
    hyp = [r for r in kw["rows"] if r["evidence"] == "AI_GENERATED"]
    assert hyp and all(r["monthly_searches"] is None and r["top_of_page_bid"] is None and r["competition"] is None for r in hyp)
    assert all(r["avg_cpc"] is None for r in kw["rows"])  # Keyword Planner does not return average CPC
    assert {r["priority"] for r in kw["rows"]} <= {"HIGH", "MEDIUM", "LOW", "EXCLUDED"}
    assert not any(r["match_type"] == "BROAD" for r in kw["rows"]) and kw["negatives"]
    csv = admin.get(f"/audit-reports/{rep['id']}/keywords.csv")
    assert csv.status_code == 200 and csv.text.splitlines()[0].startswith("Keyword,Service,Intent") and "unavailable" in csv.text


def test_history_versions_comparison_and_profile_change_detection():
    admin = Api(ADMIN)
    a = _create(admin, {"name": "History Co", "website": "history-co-test.example"}).json()
    cid = a["client_id"]
    r1 = admin.post(f"/clients/{cid}/audit-reports?sync=true").json()
    assert r1["version"] == 1 and r1["changes"] == {"first_audit": True}
    listing = admin.get(f"/clients/{cid}/audit-reports").json()
    assert len(listing) == 1 and listing[0]["profile_changed_since"] is False
    form = admin.get(f"/client-profiles/{cid}").json()
    form["selling_points"] = ["Licensed & insured"]
    form["monthly_budget"] = 3000
    admin.c.put(f"/api/v1/client-profiles/{cid}", json={"profile": form}, headers=admin._h())
    assert admin.get(f"/clients/{cid}/audit-reports").json()[0]["profile_changed_since"] is True  # old audit flagged as stale
    r2 = admin.post(f"/clients/{cid}/audit-reports?sync=true").json()
    assert r2["version"] == 2 and r2["previous_report_id"] == r1["id"]
    assert r2["changes"]["previous_version"] == 1 and any(i["id"] == "client.usp" for i in r2["changes"]["worse"])
    assert {x["version"] for x in admin.get(f"/clients/{cid}/audit-reports").json()} == {1, 2}
    old = admin.get(f"/audit-reports/{r1['id']}").json()
    assert old["input_snapshot"]["selling_points"] != r2["input_snapshot"]["selling_points"]  # old findings keep the data they were based on
    assert old["sections"]["client"]["findings"] == r1["sections"]["client"]["findings"]


def test_reports_never_leak_between_clients():
    admin, mgr = Api(ADMIN), Api(MANAGER)
    a = _create(admin, {"name": "Isolated Co", "website": "isolated-co-test.example"}).json()
    rep = admin.post(f"/clients/{a['client_id']}/audit-reports?sync=true").json()
    assert mgr.get(f"/audit-reports/{rep['id']}").status_code == 403
    assert mgr.get(f"/audit-reports/{rep['id']}/keywords.csv").status_code == 403
    other = next(c for c in admin.get("/clients").json() if c["name"] == "Brightsmile Dental Group")
    ids = {x["client_id"] for x in admin.get(f"/clients/{other['id']}/audit-reports").json()}
    assert ids <= {other["id"]}
    r = admin.post(f"/clients/{a['client_id']}/audit-reports")
    assert r.status_code == 200 and admin.post(f"/clients/{a['client_id']}/audit-reports").status_code in (200, 409)


def test_fresh_report_is_not_flagged_as_stale():
    """Regression: 20.0 vs 20 after JSON storage made a brand-new audit look 'edited since'."""
    admin = Api(ADMIN)
    r = _create(admin, {"name": "Fresh Co", "website": "fresh-co-test.example"}, audit="sync").json()
    reports = admin.get(f"/clients/{r['client_id']}/audit-reports").json()
    assert len(reports) == 1 and reports[0]["status"] == "COMPLETED" and reports[0]["profile_changed_since"] is False
    full = admin.get(f"/audit-reports/{reports[0]['id']}").json()
    assert full["profile_changed_since"] is False
