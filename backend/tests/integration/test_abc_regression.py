"""Regression for the ABC Home & Commercial Services campaign: DataForSEO answered 40104 (account not verified) to every request, Keyword
Planner was refused for the Cloud project's Explorer access level, 0 of 662 keywords were measured, google.com was listed as a
competitor, keywords for other cities were kept, and the report showed dashes for the location and the ad schedule.

Every provider here is a test double in the documented response shapes: nothing reaches the network, and no metric is invented."""
from __future__ import annotations

import uuid

import httpx

from app.adapters import registry
from app.adapters.base import PermissionDenied
from app.db.session import session_scope
from app.services import campaign_settings, competitor_verify
from app.services import keyword_classify as kc
from tests.conftest import ADMIN, Api
from tests.integration.test_dataforseo import FakeD4S, adapter, env


# ------------------------------------------------------------------ keyword classification (units)
AREAS = kc.area_terms({"city": "Columbus", "address": "1200 Example Ave, Columbus, OH", "service_areas": [{"label": "Dublin, OH"}], "states": ["OH"]})
SERVICES = ["Drain Cleaning", "Water Heater Repair"]


def cls(kw, label="SERVICE", service="Drain Cleaning", **kw_):
    return kc.classify(kw, label=label, service=service, services=SERVICES, excluded=kw_.get("excluded", ["Septic Tank Pumping"]), areas=AREAS,
                       brand={"northside"}, competitors={"rapid rooter columbus"}, measured=kw_.get("measured", False))


def test_keywords_for_other_cities_states_and_countries_are_rejected_with_the_reason():
    for kw in ("drain cleaning pittsburgh", "drain cleaning texas", "drain cleaning london", "drain cleaning nj", "drain cleaning los angeles"):
        c = cls(kw)
        assert c["class"] == "irrelevant_location" and c["selection_status"] == "rejected", kw
        assert "not one of the client's service areas" in c["rejection_reason"]
    for kw in ("drain cleaning columbus", "drain cleaning dublin", "drain cleaning ohio", "drain cleaning near me"):
        c = cls(kw)
        assert c["location_relevance"] == "in_area" and c["selection_status"] == "eligible", kw


def test_each_class_is_assigned_with_its_reason():
    expect = {
        "drain cleaning jobs": "job_seeking", "how to unclog a drain yourself": "diy_educational", "what is hydro jetting": "informational",
        "septic tank pumping columbus": "irrelevant_service", "rapid rooter columbus reviews": "competitor_navigation",
        "yelp drain cleaning": "competitor_navigation", "northside plumbing phone": "competitor_navigation",
        "emergency drain cleaning": "transactional", "drain cleaning cost": "high_intent_commercial", "drain cleaning columbus": "local_service",
    }
    for kw, want in expect.items():
        c = cls(kw)
        assert c["class"] == want, (kw, c["class"], c["class_reason"])
        assert c["class_reason"] and c["class_label"] == kc.CLASSES[want]
    assert cls("drain cleaning training", service="Drain Cleaning")["class"] == "job_seeking"
    dog = kc.classify("dog training columbus", label="SERVICE", service="Dog Training", services=["Dog Training"], excluded=[], areas=AREAS,
                      brand=set(), competitors=set(), measured=False)
    assert dog["class"] == "local_service"  # a service name is never mistaken for a job search


def test_measurement_status_is_reported_never_guessed_and_near_duplicates_share_a_key():
    assert cls("drain cleaning cost")["measurement_status"] == "unmeasured"
    assert cls("drain cleaning cost", measured=True)["measurement_status"] == "measured"
    assert kc.near_key("drain cleaning services columbus") == kc.near_key("columbus drain cleaning service")
    assert kc.pre_reject("drain cleaning pittsburgh", excluded=[], areas=AREAS, competitors=set()) == "outside the service area"
    assert kc.pre_reject("drain cleaning columbus", excluded=[], areas=AREAS, competitors=set()) is None


# ------------------------------------------------------------------ competitor verification (units)
def test_search_engines_directories_social_and_marketplaces_are_rejected_and_suggestions_are_not_verified():
    ctx = {"website_url": "https://www.northsideplumbing.example/", "city": "Columbus", "service_names": SERVICES,
           "competitors": [{"name": "Rapid Rooter Columbus", "website_url": "https://www.rapidrooter.example/"},
                           {"name": "Google", "website_url": "https://www.google.com/"}]}
    result = {
        "competitors": [{"name": "Rapid Rooter Columbus", "website_url": "https://www.rapidrooter.example/", "services": ["Drain Cleaning"], "crawl_source": "LIVE"}],
        "web_candidates": [{"name": "AI Suggested Plumbers", "website_url": "https://aisuggested.example", "evidence": {"provider": "tavily"}}],
        "serp": {"organic_competitors": [{"domain": "localdrains.example", "title": "Local Drains", "appearances": [
                     {"query": "drain cleaning columbus", "location": "Columbus, Ohio", "position": 2, "retrieved_at": "2026-11-16T10:00:00Z"},
                     {"query": "drain cleaning near me", "location": "Columbus, Ohio", "position": 4, "retrieved_at": "2026-11-16T10:00:00Z"}]},
                     {"domain": "yelp.com", "title": "Yelp", "appearances": [{"query": "drain cleaning columbus", "position": 1}]}],
                 "sponsored_ads": [{"domain": "google.com", "query": "drain cleaning columbus", "title": "Google Local Services"},
                                   {"domain": "adsonly.example", "query": "drain cleaning", "title": "Drains fast", "location": "United States"}],
                 "local_businesses": []},
    }
    out = competitor_verify.build(ctx, result)
    by = {r["domain"]: r for r in out["records"]}
    assert by["google.com"]["status"] == "Rejected" and "search engine" in by["google.com"]["rejection_reason"]
    assert by["google.com"]["classification"] == "client_provided"  # a client-provided entry is shown, and still rejected with the reason
    assert by["yelp.com"]["status"] == "Rejected" and "directory" in by["yelp.com"]["rejection_reason"]
    assert by["aisuggested.example"]["status"] == "Discovered"  # one AI/web suggestion verifies nothing
    assert by["rapidrooter.example"]["status"] == "Website Verified" and by["rapidrooter.example"]["service_evidence"]
    assert by["localdrains.example"]["status"] == "Market Verified" and by["localdrains.example"]["geo_overlap_evidence"]
    assert by["adsonly.example"]["status"] == "SERP Verified"
    c = out["counts"]
    assert c["rejected"] == 2 and c["verified"] == 3 and c["discovered"] == len(out["records"]) and c["client_provided"] == 2
    for r in out["records"]:  # every record has the fields the report shows
        assert {"domain", "url", "discovery_sources", "queries", "serp_evidence", "website_accessible", "service_evidence",
                "geo_overlap_evidence", "classification", "status", "rejection_reason", "timestamp"} <= set(r)


# ------------------------------------------------------------------ campaign settings (units)
class _Biz:
    def __init__(self, address="1200 Example Ave, Columbus, OH", hours=None, radius=15):
        self.address, self.business_hours, self.default_radius_miles = address, hours or {}, radius


def test_missing_location_and_schedule_are_blocking_issues_not_blanks():
    st = campaign_settings.resolve(_Biz(address="", hours={}), {})
    codes = {i["code"] for i in st["issues"]}
    assert codes == {"NO_TARGET_LOCATION", "NO_AD_SCHEDULE"} and all(i["blocking"] for i in st["issues"])
    assert st["language"]["label"] == "English" and st["location_targeting_mode"]["positive"] == "PRESENCE"
    ok = campaign_settings.resolve(_Biz(hours={"mon-fri": "07:00-19:00", "sat": "8am-4pm", "sun": "closed"}), {})
    assert not ok["issues"] and len(ok["ad_schedule"]["entries"]) == 6 and "15 mile radius" in ok["location"]["label"]
    ops = campaign_settings.criteria_ops("temp:1", ok)
    assert [o["fields"]["type"] for o in ops].count("AD_SCHEDULE") == 6 and ops[0]["fields"]["language"]["language_constant"] == "languageConstants/1000"
    explicit = campaign_settings.resolve(_Biz(hours={}), {"ad_schedule": "all_hours"})
    assert not explicit["issues"] and explicit["ad_schedule"]["mode"] == "all_hours"


# ------------------------------------------------------------------ the whole ABC pipeline
class ExplorerPlanner:
    """Keyword Planner refused by Google for the Cloud project's Explorer access level (the real adapter's error, verbatim shape)."""
    source = "LIVE"

    def __init__(self, inner):
        self.inner = inner

    def generate_ideas(self, *a, **k):
        raise PermissionDenied("Google refused this method for the Cloud project's Explorer API access level.", adapter="keyword_planner",
                               details={"reason": "DEVELOPER_TOKEN_NOT_APPROVED", "requires_approval": True, "cause": "project_access"})

    def __getattr__(self, n):
        return getattr(self.inner, n)


def test_abc_build_reports_verification_block_explorer_access_and_blockers_without_inventing_metrics():
    def verify_first(path, body, n):
        if path.startswith(("keywords_data/google_ads/search_volume", "keywords_data/google_ads/keywords_for", "dataforseo_labs/")):
            return httpx.Response(200, json=env(None, status=40104, message="Please verify your account before using the API."))
        return None
    fake = FakeD4S(fail=verify_first)
    registry.override("dataforseo", adapter(fake))
    registry.override("keyword_research", ExplorerPlanner(registry.get("keyword_research")))
    try:
        api = Api(ADMIN)
        name = f"ABC Home & Commercial {uuid.uuid4().hex[:4]}"
        r = api.post("/clients", {"name": name, "owner_contact": "o@abc.example", "backup_contact": "b@abc.example", "tag_access_contact": "w@abc.example"})
        cid = r.json()["id"]
        r = api.post(f"/clients/{cid}/businesses", {  # no business hours recorded: the schedule must be a blocker
            "name": name, "business_type": "home_services", "website_url": "https://www.northsideplumbing.example/", "phone": "(555) 201-4400",
            "address": "1200 Example Ave, Columbus, OH", "lead_form_url": "https://www.northsideplumbing.example/contact",
            "thank_you_url": "https://www.northsideplumbing.example/thank-you", "verified_claims": ["Licensed & Insured"],
            "services": [{"name": "Drain Cleaning", "landing_page_url": "https://www.northsideplumbing.example/drain-cleaning"}],
            "competitors": [{"name": "Rapid Rooter Columbus", "website_url": "https://www.rapidrooter.example/"}]})
        bid = r.json()["id"]
        t = api.post("/tasks?sync=true", {"task_type": "BUILD_CAMPAIGN", "client_id": cid, "business_id": bid, "fields": {"monthly_budget": 3000}}).json()
        assert t["status"] == "VALIDATION_FAILED", (t["status"], t["error"])
        from app.models import Audit, ValidationRun
        with session_scope() as db:
            kw = db.query(Audit).filter(Audit.task_id == t["id"], Audit.audit_type == "KEYWORD").one().result
            comp = db.query(Audit).filter(Audit.task_id == t["id"], Audit.audit_type == "COMPETITOR").one().result
            vr = db.query(ValidationRun).filter(ValidationRun.task_id == t["id"]).order_by(ValidationRun.created_at.desc()).first()
            codes = {e["code"] for e in vr.errors}
        assert "NO_AD_SCHEDULE" in codes  # a missing schedule blocks approval; it is never a dash
        # provider states: the real reasons, not "connected"
        assert kw["keyword_planner"]["available"] is False and kw["keyword_planner"]["access_level"] == "explorer"
        assert kw["dataforseo"]["metrics"]["blocked_by"] == "account_verification_required"
        sv_calls = [c for c in fake.calls if c[1].startswith("keywords_data/google_ads/search_volume")]
        assert len(sv_calls) <= 1  # the run stopped asking once DataForSEO said the account is not verified
        # no metric was invented: every row without a provider number is unmeasured
        for r in kw["keywords"]:
            assert r["avg_monthly_searches"] is None and r["cpc"] is None and r["measurement_status"] == "unmeasured"
            assert r["keyword_class"] in kc.CLASSES and r["selection_status"] in ("eligible", "research_only", "rejected")
            if r["selection_status"] == "rejected":
                assert r["rejection_reason"] and not r["selected"]
        m = kw["measurement"]
        assert m["eligible_measured"] == 0 and m["eligible_unmeasured"] == m["eligible"] and m["unmeasured_reasons"]
        # competitors: verification records, nothing generic listed as a competitor
        recs = comp["verification"]["records"]
        assert all(not competitor_verify.excluded_reason(r["domain"]) or r["status"] == "Rejected" for r in recs)
        assert any(r["classification"] == "client_provided" for r in recs)
        # the report: settings, coverage, classification and verification are all there
        a = api.get(f"/plans/{t['plan_id']}/appendix").json()
        assert {i["code"] for i in a["settings"]["issues"]} == {"NO_AD_SCHEDULE"} and a["settings"]["location"]["label"]
        assert a["keywords"]["keyword_planner"]["access_level"] == "explorer"
        assert a["keywords"]["dataforseo"]["metrics"]["blocked_by"] == "account_verification_required"
        assert a["keywords"]["measurement_coverage"]["eligible_measured"] == 0 and len(a["keywords"]["rows"]) == len(kw["keywords"])
        assert a["competitor_verification"]["counts"]["discovered"] >= 1 and a["source_ledger"]["counts"]["discovered"] >= 1
        assert any(e["provider"] in ("dataforseo", "keyword_planner") for e in a["provider_errors"])
    finally:
        registry._overrides.pop("dataforseo", None)
        registry._overrides.pop("keyword_research", None)
