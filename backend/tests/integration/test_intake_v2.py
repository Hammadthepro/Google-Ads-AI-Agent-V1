"""Intake V2: conditional Google Business Profile URL, optional budget, optional weekly conversion target, mandatory competitor name +
website, and campaign preparation that does not need a linked Google Ads account (deployment still does)."""
from __future__ import annotations

import pytest

from app.db.session import session_scope
from app.models import Business, Client, Competitor
from tests.conftest import ADMIN, SUPER, Api
from tests.integration.test_client_profile_and_audit_report import GOOD


def _validate(api: Api, **over):
    return api.post("/client-profiles/validate", {"profile": {**GOOD, "website": "validate-only-v2.example", **over}}).json()


# ------------------------------------------------------------------ Google Business Profile
def test_gbp_yes_requires_a_valid_link_and_no_clears_it():
    api = Api(ADMIN)
    assert _validate(api, has_gbp=True, gbp_url="")["errors"]["gbp_url"].startswith("Paste")
    for bad in ("not a url", "https://example.com/page", "https://evil.example/maps", "ftp://maps.google.com/x", "https://google.com/about"):
        assert "gbp_url" in _validate(api, has_gbp=True, gbp_url=bad)["errors"], bad
    for good in ("https://maps.app.goo.gl/AbC123", "https://www.google.com/maps/place/Acme+Plumbing/@30.2,-97.7,17z",
                 "https://g.page/acme-plumbing", "https://maps.google.com/?cid=123", "https://business.google.com/n/123/profile"):
        assert _validate(api, has_gbp=True, gbp_url=good)["valid"], good
    # "No": the field is not required, and anything typed earlier is ignored
    r = _validate(api, has_gbp=False, gbp_url="not even a link")
    assert r["valid"] and r["normalized"]["gbp_url"] == ""


def test_gbp_link_is_saved_cleared_and_available_on_edit():
    api = Api(ADMIN)
    r = api.post("/client-profiles?audit=none", {"profile": {**GOOD, "website": "gbp-v2-create.example", "name": "GBP V2 Create",
                                                            "gbp_url": "https://g.page/gbp-v2"}})
    assert r.status_code == 200, r.text
    cid = r.json()["client_id"]
    form = api.get(f"/client-profiles/{cid}").json()
    assert form["has_gbp"] is True and form["gbp_url"] == "https://g.page/gbp-v2"
    form["has_gbp"] = False
    r = api.put(f"/client-profiles/{cid}", {"profile": form})
    assert r.status_code == 200, r.text
    again = api.get(f"/client-profiles/{cid}").json()
    assert again["has_gbp"] is False and again["gbp_url"] == ""
    with session_scope() as db:
        assert db.query(Business).filter_by(client_id=cid).one().gbp_url == ""  # cleared in the database, not just hidden
    # switching back to Yes requires the link again
    again["has_gbp"] = True
    assert api.put(f"/client-profiles/{cid}", {"profile": again}).status_code == 422


# ------------------------------------------------------------------ budget and weekly target
def test_monthly_budget_is_optional_but_validated_when_supplied():
    api = Api(ADMIN)
    assert _validate(api, monthly_budget="")["valid"] and _validate(api, monthly_budget=None)["valid"]
    assert "monthly_budget" in _validate(api, monthly_budget="lots")["errors"]
    assert "monthly_budget" in _validate(api, monthly_budget="-5")["errors"]


@pytest.mark.parametrize("value,ok", [("", True), (None, True), ("15", True), (15, True), ("7.5", True),
                                      ("0", False), ("-3", False), ("abc", False), ("nan", False), ("1e9", False)])
def test_weekly_conversion_target_is_optional_and_validated(value, ok):
    api = Api(ADMIN)
    r = _validate(api, weekly_conversion_target=value)
    assert ("weekly_conversion_target" not in r["errors"]) is ok, r["errors"]


def test_weekly_target_and_missing_budget_are_stored_on_the_client():
    api = Api(ADMIN)
    r = api.post("/client-profiles?audit=none", {"profile": {**GOOD, "website": "wk-v2.example", "name": "Weekly V2", "monthly_budget": "",
                                                            "weekly_conversion_target": "12"}})
    assert r.status_code == 200, r.text
    form = api.get(f"/client-profiles/{r.json()['client_id']}").json()
    assert form["weekly_conversion_target"] == 12 and form["monthly_budget"] == ""
    assert api.get(f"/clients/{r.json()['client_id']}").json()["weekly_conversion_target"] == 12


# ------------------------------------------------------------------ competitors
def test_competitor_needs_name_and_website():
    api = Api(ADMIN)
    r = _validate(api, competitors=[{"name": "Only Name", "website": "", "maps_url": ""}])
    assert r["errors"] == {"competitors.0.website": "Competitor website is required"}
    r = _validate(api, competitors=[{"name": "", "website": "rival.com", "maps_url": ""}])
    assert r["errors"] == {"competitors.0": "Competitor name is required"}
    r = _validate(api, competitors=[{"name": "Bad", "website": "no spaces allowed.com", "maps_url": ""}])
    assert "competitors.0.website" in r["errors"]
    r = _validate(api, competitors=[{"name": "Ok", "website": "rival.com", "maps_url": ""}, {"name": "", "website": "", "maps_url": ""}])
    assert r["valid"] and len(r["normalized"]["competitors"]) == 1  # a completely empty row is ignored, never saved half-filled


def test_many_competitors_are_saved_and_returned():
    api = Api(ADMIN)
    comps = [{"name": f"Rival {i}", "website": f"rival{i}.example", "maps_url": ""} for i in range(5)]
    r = api.post("/client-profiles?audit=none", {"profile": {**GOOD, "website": "many-v2.example", "name": "Many Rivals", "competitors": comps}})
    assert r.status_code == 200, r.text
    got = api.get(f"/client-profiles/{r.json()['client_id']}").json()["competitors"]
    assert [c["name"] for c in got] == [c["name"] for c in comps] and all(c["website"].startswith("https://rival") for c in got)
    too_many = [{"name": f"R{i}", "website": f"r{i}.example", "maps_url": ""} for i in range(11)]
    assert "competitors" in _validate(api, competitors=too_many)["errors"]


def test_legacy_competitor_without_website_still_loads_and_must_be_completed_on_save():
    api = Api(ADMIN)
    r = api.post("/client-profiles?audit=none", {"profile": {**GOOD, "website": "legacy-v2.example", "name": "Legacy V2"}})
    cid = r.json()["client_id"]
    with session_scope() as db:
        b = db.query(Business).filter_by(client_id=cid).one()
        db.add(Competitor(business_id=b.id, name="Legacy Rival", website_url=""))
    form = api.get(f"/client-profiles/{cid}").json()  # reading never fails on old data
    legacy = next(c for c in form["competitors"] if c["name"] == "Legacy Rival")
    assert legacy["website"] == ""
    res = api.put(f"/client-profiles/{cid}", {"profile": form})  # saving asks for the missing website, field by field
    assert res.status_code == 422
    idx = form["competitors"].index(legacy)
    assert f"competitors.{idx}.website" in res.json()["error"]["details"]["fields"]


# ------------------------------------------------------------------ account-free preparation, account-bound deployment
def _fixture_client(api: Api, name: str, weekly_target: float = 0) -> tuple[str, str]:
    """A client whose website the fixture crawler knows, with NO Google Ads account linked and no budget."""
    r = api.post("/clients", {"name": name, "owner_contact": "o@v2.example", "backup_contact": "b@v2.example", "tag_access_contact": "w@v2.example"})
    cid = r.json()["id"]
    r = api.post(f"/clients/{cid}/businesses", {
        "name": name, "business_type": "home_services", "website_url": "https://www.northsideplumbing.example/", "phone": "(555) 201-4400",
        "address": "1200 Example Ave, Columbus, OH", "lead_form_url": "https://www.northsideplumbing.example/contact",
        "thank_you_url": "https://www.northsideplumbing.example/thank-you", "verified_claims": ["Licensed & Insured"],
        "services": [{"name": "Drain Cleaning", "landing_page_url": "https://www.northsideplumbing.example/drain-cleaning"}],
        "competitors": [{"name": "Rapid Rooter Columbus", "website_url": "https://www.rapidrooter.example/"}]})
    assert r.status_code == 200, r.text
    if weekly_target:
        with session_scope() as db:
            db.get(Client, cid).weekly_conversion_target = weekly_target
    return cid, r.json()["id"]


def test_campaign_draft_is_prepared_without_account_or_budget_and_cannot_be_deployed():
    api = Api(ADMIN)
    cid, bid = _fixture_client(api, "No Account Co", weekly_target=6)
    assert api.get(f"/clients/{cid}/structure").json()["campaigns"] == []
    r = api.post("/tasks?sync=true", {"task_type": "BUILD_CAMPAIGN", "client_id": cid, "business_id": bid, "fields": {}, "note": "prep only"})
    assert r.status_code == 200, r.text
    task = r.json()
    assert task["status"] == "AWAITING_APPROVAL", (task["status"], task["error"])
    assert task["fields"]["monthly_budget"] > 0 and task["fields"]["budget_source"].startswith("recommended")  # recommended, not typed
    assert task["fields"]["weekly_target"] == 6 and task["fields"]["weekly_target_source"] == "client"
    load = next(s for s in task["steps"] if s["name"] == "Load account")
    assert "without account data" in load["summary"]
    assert any(i["code"] == "NO_ADS_ACCOUNT" and i["severity"] == "WARNING" for i in task["intake"]["issues"])  # visible, not blocking
    appr = api.get(f"/approvals/{task['approval_id']}").json()
    assert appr["payload"]["customer_id"] is None and not appr.get("google_ads_account_id")
    assert appr["payload"]["summary"]["status_on_create"] == "PAUSED"
    # the plan can be approved, but nothing is sent to Google without a target account
    approver = Api(SUPER)  # the requester cannot approve their own request
    assert approver.post(f"/approvals/{appr['id']}/approve", {"payload_hash": appr["payload_hash"]}).status_code == 200
    r = approver.post(f"/approvals/{appr['id']}/execute")
    assert r.status_code == 423 and "no Google Ads account" in r.text
    assert api.get(f"/clients/{cid}/structure").json()["campaigns"] == []


def test_budget_is_recommended_from_a_target_or_from_the_learning_threshold():
    from app.services import feasibility
    # target-driven: the weekly target times the expected cost per lead times weeks per month, rounded up to $50
    budget, src = feasibility.recommend_budget(expected_cpc=5.0, expected_cvr=0.05, weekly_target=10)
    assert src == "recommended_from_weekly_target" and budget == 4350.0  # CPL $100: 10 leads/week * $100 * 4.345 weeks = $4,345, rounded up to the next $50
    budget, src = feasibility.recommend_budget(expected_cpc=5.0, expected_cvr=0.05, weekly_target=0)
    assert src == "recommended_from_learning_threshold" and budget == 3000.0  # 30 conversions * $100
    assert feasibility.recommend_budget(0, 0.05, 5) == (0.0, "unavailable")


def test_audit_only_still_needs_an_account():
    api = Api(ADMIN)
    cid, bid = _fixture_client(api, "Audit No Account")
    r = api.post("/tasks?sync=true", {"task_type": "AUDIT_ONLY", "client_id": cid, "business_id": bid, "fields": {}})
    assert r.status_code == 422 and "No Google Ads account" in r.text
