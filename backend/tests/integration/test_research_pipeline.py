"""Live research events, provider caching/budgets, keyword intelligence without Keyword Planner, and geo intelligence without Places."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import delete, func, select

from app.adapters import registry
from app.adapters.base import RateLimited
from app.core.errors import IntegrationNotConfigured
from app.db.session import session_scope
from app.models import AuditReport, ProviderCall, ResearchEvent
from app.services import geo_intel, keyword_intel, provider_calls, research_events, web_research
from tests.conftest import ADMIN, Api


# --------------------------------------------------------------------------- events
def test_events_are_ordered_persisted_and_follow_worker_threads():
    rid = "evt" + "0" * 29
    with session_scope() as db:
        db.execute(delete(ResearchEvent).where(ResearchEvent.audit_report_id == rid))
    research_events.forget(rid)
    research_events.emit("note", "outside any binding")  # no binding: nothing recorded, nothing raised
    with research_events.bind(rid, client_id=None):
        research_events.emit("audit_started", "start")
        with ThreadPoolExecutor(4) as pool:
            for i in range(8):
                pool.submit(research_events.in_stage("website", lambda i=i: research_events.emit("url_crawling", f"https://x.example/{i}", url=f"https://x.example/{i}")))
        research_events.emit("audit_completed", "end")
    with session_scope() as db:
        evs = research_events.list_events(db, rid)
        later = research_events.list_events(db, rid, after=evs[3]["seq"])
    assert [e["seq"] for e in evs] == list(range(1, 11))
    assert evs[0]["kind"] == "audit_started" and evs[-1]["kind"] == "audit_completed"
    assert {e["stage"] for e in evs if e["kind"] == "url_crawling"} == {"website"}
    assert later[0]["seq"] == evs[3]["seq"] + 1  # a client that refreshes asks for everything after what it has


def test_unknown_event_kind_is_a_programming_error():
    with research_events.bind("x" * 32), pytest.raises(ValueError):
        research_events.emit("made_up_kind", "nope")


def test_audit_run_records_real_events_and_the_api_replays_them():
    api = Api(ADMIN)
    client = next(c for c in api.get("/clients").json() if c["name"] == "Northside Home Services")
    rep = api.post(f"/clients/{client['id']}/audit-reports?sync=true").json()
    assert rep["status"] == "COMPLETED", rep.get("error")
    page = api.get(f"/audit-reports/{rep['id']}/events").json()
    kinds = [e["kind"] for e in page["events"]]
    assert page["done"] and kinds[0] == "audit_started" and kinds[-1] == "audit_completed"
    for k in ("stage_started", "stage_completed", "url_crawling", "page_extracted", "competitor_identified", "keyword_discovery", "research_stored",
              "evidence_validated", "location_identified"):
        assert k in kinds, k
    crawled = [e["data"]["url"] for e in page["events"] if e["kind"] == "url_crawling"]
    assert crawled and all(u.startswith("http") for u in crawled)  # real URLs the crawler fetched (fixture crawler in tests)
    tail = api.get(f"/audit-reports/{rep['id']}/events?after={page['events'][5]['seq']}").json()
    assert tail["events"][0]["seq"] == page["events"][6]["seq"]


def test_events_are_only_visible_through_the_client():
    from tests.conftest import ANALYST
    api = Api(ADMIN)
    with session_scope() as db:
        rid = db.scalar(select(AuditReport.id).order_by(AuditReport.started_at.desc()).limit(1))
    assert api.get(f"/audit-reports/{rid}/events").status_code == 200
    assert Api(ANALYST).get("/audit-reports/" + "z" * 32 + "/events").status_code == 404


# --------------------------------------------------------------------------- provider cache & budget
def test_identical_requests_are_served_from_cache_and_logged():
    calls = []
    q = {"q": f"cache-{threading.get_ident()}"}
    for _ in range(3):
        v = provider_calls.call("unit_test_provider", "op", q, lambda: calls.append(1) or {"ok": True})
        assert v == {"ok": True}
    assert len(calls) == 1
    u = provider_calls.usage("unit_test_provider")
    assert u["requests_sent"] >= 1 and u["served_from_cache"] >= 2


def test_failures_are_recorded_and_reraised_not_cached():
    def boom():
        raise RateLimited("slow down", adapter="unit_test_failing")
    for _ in range(2):
        with pytest.raises(RateLimited):
            provider_calls.call("unit_test_failing", "op", {"q": "x"}, boom)
    with session_scope() as db:
        n = db.scalar(select(func.count(ProviderCall.id)).where(ProviderCall.provider == "unit_test_failing", ProviderCall.error_kind == "rate_limited"))
    assert n >= 2
    assert provider_calls.usage("unit_test_failing")["last_error"]["kind"] == "rate_limited"


def test_per_audit_budget_stops_calls_without_contacting_the_provider():
    rid = "bud" + "0" * 29
    sent = []
    with research_events.bind(rid):
        for i in range(2):
            provider_calls.call("unit_test_metered", "search", {"q": f"b{i}-{rid}"}, lambda: sent.append(1) or {}, budget=2)
        from app.adapters.base import BudgetExhausted
        with pytest.raises(BudgetExhausted):
            provider_calls.call("unit_test_metered", "search", {"q": f"b3-{rid}"}, lambda: sent.append(1) or {}, budget=2)
    assert len(sent) == 2


# --------------------------------------------------------------------------- keyword intelligence
CTX = {"business_name": "Northside Plumbing", "city": "Austin", "service_names": ["Drain Cleaning", "Water Heater Repair"], "client_id": None,
       "competitors": [], "service_areas": [], "website_url": "https://northside.example"}


def test_keyword_discovery_uses_free_sources_with_provenance_and_no_metrics():
    with session_scope() as db:
        out = keyword_intel.discover(db, CTX, {"page_inventory": [{"url": "https://northside.example/drain", "title": "Drain Cleaning in Austin | Northside", "h1": ["Drain Cleaning in Austin"]}]},
                                     {"competitors": [{"name": "Rival", "website_url": "https://rival.example", "services": ["Hydro Jetting"]}]})
    cands = out["candidates"]
    assert "drain cleaning near me" in cands
    srcs = {s["source"] for c in cands.values() for s in c["sources"]}
    assert {"suggest:google", "suggest:duckduckgo", "client_website", "competitor_website:Rival", "client_profile"} <= srcs
    assert out["report"]["search_terms"]["available"] is False and out["report"]["web"]["used"] is False
    m = keyword_intel.unavailable_metrics("no source")
    assert all(v["value"] is None and v["basis"] == "unavailable" for v in m.values())


def test_one_failing_suggestion_source_does_not_stop_the_others():
    fake = registry.get("search_suggestions")
    fake.fail["google"] = RateLimited("blocked", adapter="google")
    try:
        with session_scope() as db:
            out = keyword_intel.discover(db, {**CTX, "service_names": ["Sump Pump Installation"]}, {}, {})
    finally:
        fake.fail.clear()
    rep = out["report"]["suggestions"]
    assert "google" in rep["failed"] and "duckduckgo" in rep["used"]
    assert any(s["source"] == "suggest:duckduckgo" for c in out["candidates"].values() for s in c["sources"])


@pytest.mark.parametrize("kw,expected", [("emergency plumber near me", "transactional"), ("drain cleaning cost", "commercial"),
                                         ("how to unclog a drain", "informational"), ("plumber jobs", "informational")])
def test_intent_classification(kw, expected):
    assert keyword_intel.intent(kw) == expected


def test_relevance_score_is_not_search_volume_and_rewards_agreement():
    one, _ = keyword_intel.relevance("drain cleaning austin", label="SERVICE", kw_intent="commercial", sources=[{"source": "suggest:google"}],
                                     services=["Drain Cleaning"], places=["Austin"])
    two, why = keyword_intel.relevance("drain cleaning austin", label="SERVICE", kw_intent="commercial",
                                       sources=[{"source": "suggest:google"}, {"source": "client_website"}, {"source": "search_terms"}],
                                       services=["Drain Cleaning"], places=["Austin"])
    assert 0 < one < two <= 100 and "already searched by real customers of this account" in why
    assert keyword_intel.relevance("x", label="IRRELEVANT", kw_intent="commercial", sources=[], services=[], places=[])[0] == 0


def test_keyword_research_without_keyword_planner_still_produces_a_strategy(monkeypatch):
    from app.services import audits

    def not_configured(name):
        if name == "keyword_research":
            raise IntegrationNotConfigured("keyword_planner", "Keyword Planner", missing=["research_customer_id"])
        return original(name)
    original = registry.get
    monkeypatch.setattr(registry, "get", not_configured)
    ctx = {**CTX, "services": [{"name": s, "landing_page_url": ""} for s in CTX["service_names"]], "excluded": []}
    with session_scope() as db:
        out = audits.keyword_research(db, ctx, {}, {}, None)
    assert out["keywords"] and out["keyword_planner"]["available"] is False
    assert all(k["avg_monthly_searches"] is None and k["metrics"]["avg_monthly_searches"]["basis"] == "unavailable" for k in out["keywords"])
    assert any(k["selected"] and k["selection_basis"] == "relevance and intent (no volume data)" for k in out["keywords"])
    assert out["clusters"] and "Keyword Planner" not in out["sources_used"]
    assert out["not_configured"] == "keyword_planner" and out["errors"]  # the gap is reported, research continued


# --------------------------------------------------------------------------- geo intelligence
GEO_CTX = {"address": "100 Congress Ave, Austin, TX", "city": "Austin", "category": "Plumber", "business_type": "plumbing",
           "service_names": ["Drain Cleaning"], "radius_miles": 10, "website_url": "https://northside.example", "business_name": "Northside Plumbing",
           "competitors": [{"name": "Rival Plumbing", "website_url": "https://rival.example"}]}


def _poi(name, lat, lon, source="openstreetmap_overpass", **kw):
    return {"name": name, "lat": lat, "lon": lon, "source": source, "source_ref": f"node/{name}", "category": "craft=plumber",
            "attribution": "© OpenStreetMap contributors (ODbL 1.0)", **kw}


def test_geo_research_dedupes_matches_the_client_and_never_invents_ratings():
    osm = registry.get("openstreetmap")
    osm.pois = [_poi("Northside Plumbing", 30.2672, -97.7431, website="https://northside.example"),
                _poi("Rival Plumbing", 30.30, -97.74, website="https://www.rival.example/"),
                _poi("Acme Pipes", 30.28, -97.75), _poi("Acme Pipes", 30.2801, -97.7501, source="geoapify_places", phone="555")]
    try:
        out = geo_intel.research(GEO_CTX)
    finally:
        osm.pois = []
    assert out["available"] and out["client_listing"]["name"] == "Northside Plumbing"
    names = [p["name"] for p in out["nearby"]]
    assert names.count("Acme Pipes") == 1 and len(next(p for p in out["nearby"] if p["name"] == "Acme Pipes")["sources"]) == 2
    assert next(p for p in out["nearby"] if p["name"] == "Rival Plumbing")["supplied_competitor"] == "Rival Plumbing"
    assert all("rating" not in p and "reviews" not in p for p in out["nearby"]) and out["density"]["total"] == 2
    assert any("ratings" in lim for lim in out["limitations"])


def test_geo_provider_failure_is_reported_and_research_continues():
    osm = registry.get("openstreetmap")
    osm.fail_nearby = RateLimited("Overpass busy", adapter="openstreetmap")
    try:
        out = geo_intel.research({**GEO_CTX, "radius_miles": 13})  # a new request (an identical one is correctly served from the cache)
    finally:
        osm.fail_nearby = None
    assert out["available"] and out["nearby"] == [] and out["provider_errors"][0]["kind"] == "rate_limited"


def test_web_research_is_skipped_honestly_without_tavily():
    ok, why = web_research.available()
    assert ok is False and "No web search provider is configured" in why
    assert web_research.discover_competitors({"competitors": [], "category": "Plumber", "city": "Austin"})["used"] is False


def test_tavily_competitor_discovery_filters_directories_and_marks_candidates_unverified(monkeypatch):
    class FakeTavily:
        name, source, depth = "tavily", "MOCK", "basic"

        def search(self, query, *, max_results=5, depth=None, topic="general"):
            return {"query": query, "results": [{"url": "https://www.yelp.com/search?x", "title": "Best 10 Plumbers", "content": "", "score": 1},
                                                {"url": "https://bluewave-plumbing.example/", "title": "BlueWave Plumbing | Austin TX", "content": "", "score": 0.8},
                                                {"url": "https://northside.example/", "title": "Northside", "content": "", "score": 0.7}],
                    "credits": 1, "credits_source": "test"}
    registry.override("tavily", FakeTavily())
    try:
        out = web_research.discover_competitors({"competitors": [], "category": "Plumber", "city": "Austin Unit", "website_url": "https://northside.example"})
    finally:
        registry._overrides.pop("tavily", None)
    assert [c["name"] for c in out["candidates"]] == ["BlueWave Plumbing"]
    assert out["candidates"][0]["status"].startswith("suggested") and out["candidates"][0]["evidence"]["confidence"] == "unverified_snippet"


def test_research_provider_cards_never_return_secrets():
    listing = Api(ADMIN).get("/integrations").json()
    cards = {c["provider"]: c for c in listing["integrations"]}
    for p in ("tavily", "openstreetmap", "geoapify", "search_suggestions"):
        assert p in cards
    assert "tvly-" not in str(listing)
