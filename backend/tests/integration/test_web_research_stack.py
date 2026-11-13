"""Web Search (Tavily -> Brave fallback), Web Fetch, and the Keyword Planner approval failure.

Provider HTTP is mocked (httpx.MockTransport) or replaced by test doubles; nothing here contacts a real provider."""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy import delete, select

from app.adapters import registry
from app.adapters.base import AdapterTimeout, AuthError, PermissionDenied, QuotaExceeded, RateLimited
from app.adapters.crawler import FetchResult
from app.adapters.research.brave import BraveSearchAdapter
from app.db.session import session_scope
from app.models import IntegrationConfig, ResearchEvent
from app.services import audits, integrations_service as svc, research_events, web_fetch, web_research, web_search
from tests.conftest import SUPER, Api
from tests.integration.test_integrations_platform import _client, _save_ads


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- Brave adapter
def test_brave_parses_results_and_sends_the_token_only_in_its_header():
    seen = {}

    def h(req):
        seen.update(url=str(req.url), token=req.headers.get("x-subscription-token"))
        return httpx.Response(200, json={"web": {"results": [{"url": "https://a.example/", "title": "A", "description": "snippet"}, {"title": "no url"}]}})
    r = BraveSearchAdapter("BSA-test", client(h)).search("plumber austin", max_results=3, country="us")
    assert [x["url"] for x in r["results"]] == ["https://a.example/"] and r["results"][0]["content"] == "snippet"
    assert seen["token"] == "BSA-test" and "BSA-test" not in seen["url"] and "country=US" in seen["url"]


@pytest.mark.parametrize("status,error", [(401, AuthError), (402, QuotaExceeded), (429, RateLimited)])
def test_brave_failures_are_classified(status, error):
    with pytest.raises(error):
        BraveSearchAdapter("k", client(lambda r: httpx.Response(status, json={}))).search("x")


def test_brave_timeout_is_a_timeout():
    def h(req):
        raise httpx.ConnectTimeout("slow", request=req)
    with pytest.raises(AdapterTimeout):
        BraveSearchAdapter("k", client(h)).search("x")


# --------------------------------------------------------------------------- web search: provider order and visible fallback
class FakeSearch:
    def __init__(self, name, fail=None, results=None):
        self.name, self.source, self.depth, self.fail, self.calls = name, "MOCK", "basic", fail, 0
        self.results = results or [{"url": f"https://{name}.example/", "title": f"{name} result", "content": "", "score": None}]

    def search(self, query, *, max_results=5, **kw):
        self.calls += 1
        if self.fail:
            raise self.fail
        return {"query": query, "results": self.results, "credits": 1, "credits_source": "test"}


@pytest.fixture
def providers():
    made = {}

    def install(**kw):
        for name, adapter in kw.items():
            registry.override(name, adapter)
            made[name] = adapter
    yield install
    for name in made:
        registry._overrides.pop(name, None)


def test_no_web_search_provider_means_no_web_search_and_says_so():
    out = web_search.search("plumber austin", "test")
    assert out["ok"] is False and out["provider"] is None and "No web search provider is configured" in out["reason"]


def test_tavily_is_used_first(providers):
    providers(tavily=FakeSearch("tavily"), brave_search=FakeSearch("brave"))
    out = web_search.search("unique query tavily first", "test")
    assert out["provider"] == "tavily" and out["results"][0]["provider"] == "tavily" and out["fallback_from"] is None


def test_when_tavily_fails_brave_answers_and_the_fallback_is_visible(providers):
    tav, brave = FakeSearch("tavily", fail=RateLimited("busy", adapter="tavily")), FakeSearch("brave")
    providers(tavily=tav, brave_search=brave)
    rid = "wsf" + "0" * 29
    with research_events.bind(rid):
        out = web_search.search("unique query fallback", "test", stage="competitors")
    assert out["ok"] and out["provider"] == "brave_search" and out["fallback_from"] == ["tavily"]
    assert out["attempts"][0] == {"provider": "tavily", "ok": False, "error_kind": "rate_limited", "message": "busy"}
    with session_scope() as db:
        kinds = [e.kind for e in db.scalars(select(ResearchEvent).where(ResearchEvent.run_id == rid).order_by(ResearchEvent.seq))]
        db.execute(delete(ResearchEvent).where(ResearchEvent.run_id == rid))
    assert "provider_failed" in kinds and "web_result" in kinds


def test_when_every_provider_fails_the_result_is_a_failure_not_an_empty_success(providers):
    providers(tavily=FakeSearch("tavily", fail=QuotaExceeded("no credits", adapter="tavily")),
              brave_search=FakeSearch("brave", fail=AuthError("bad key", adapter="brave_search")))
    out = web_search.search("unique query all fail", "test")
    assert out["ok"] is False and out["results"] == [] and "Tavily quota_exceeded" in out["reason"] and "Brave Search auth_failure" in out["reason"]


# --------------------------------------------------------------------------- web fetch
def test_web_fetch_reads_one_known_page():
    page = web_fetch.fetch("https://www.northsideplumbing.example/")
    assert page["ok"] and page["status"] == 200 and page["title"] and page["word_count"] > 0 and page["text_excerpt"]


def test_web_fetch_reports_failures_and_unsupported_content_instead_of_content(monkeypatch):
    missing = web_fetch.fetch("https://unknown-host.example/")
    assert missing["ok"] is False and missing["error"]
    crawler = registry.get("crawler")
    monkeypatch.setattr(crawler, "fetch", lambda url: FetchResult(url, url, 200, text="%PDF", content_type="application/pdf"))
    pdf = web_fetch.fetch("https://www.northsideplumbing.example/brochure.pdf")
    assert pdf["ok"] is False and "Unsupported content type" in pdf["error"]
    monkeypatch.setattr(crawler, "fetch", lambda url: FetchResult(url, url, 503, text="down"))
    down = web_fetch.fetch("https://www.northsideplumbing.example/")
    assert down["ok"] is False and down["status"] == 503


def test_the_ssrf_guard_refuses_private_addresses_before_any_request():
    from app.adapters.crawler import SSRFBlocked, check_url_safe
    for url in ("http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8000/", "http://10.0.0.5/"):
        with pytest.raises(SSRFBlocked):
            check_url_safe(url)


def test_search_then_fetch_competitors_found_on_the_web(providers):
    hit = [{"url": "https://www.rapidrooter.example/", "title": "Rapid Rooter | Columbus", "content": "", "score": 1},
           {"url": "https://www.yelp.com/search?x", "title": "Best plumbers", "content": "", "score": 1},
           {"url": "https://ghost-plumbers.example/", "title": "Ghost Plumbers - Columbus", "content": "", "score": 1}]
    providers(tavily=FakeSearch("tavily", results=hit))
    out = web_research.discover_competitors({"competitors": [], "category": "Plumber", "city": "Columbus web-fetch-test", "website_url": "https://northside.example"})
    by = {c["name"]: c for c in out["candidates"]}
    assert set(by) == {"Rapid Rooter", "Ghost Plumbers"} and out["provider"] == "tavily"  # the directory was dropped
    assert by["Rapid Rooter"]["page"]["ok"] is True and by["Rapid Rooter"]["page"]["title"] and "site opened" in by["Rapid Rooter"]["status"]
    assert by["Ghost Plumbers"]["page"]["ok"] is False and "could not be opened" in by["Ghost Plumbers"]["status"]
    assert all(c["status"].startswith("suggested") for c in out["candidates"])  # never presented as verified


# --------------------------------------------------------------------------- Keyword Planner: Explorer access
EXPLORER = {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "The caller does not have permission",
                      "details": [{"errors": [{"errorCode": {"authorizationError": "DEVELOPER_TOKEN_NOT_APPROVED"},
                                               "message": "This method is not allowed for use with explorer access. Please apply for basic or standard access."}]}]}}


def test_keyword_planner_explorer_access_is_reported_as_an_approval_requirement():
    def handler(req):
        if "oauth2.googleapis.com" in str(req.url):
            return httpx.Response(200, json={"access_token": "ya29.fake", "scope": "adwords"})
        return httpx.Response(403, json=EXPLORER)
    with session_scope() as db:
        _save_ads(db)
        svc.save(db, "keyword_planner", settings={"research_customer_id": "123-456-7890"}, secrets={}, user_id=None)
        res = svc.test_connection(db, "keyword_planner", http=_client(handler))
        step = res["steps"][-1]
        assert not res["connected"] and res["status"] == "REQUIRES_APPROVAL" and "Basic or Standard" in step["hint"]
        assert "cannot access this account" not in step["hint"]  # the old, wrong diagnosis
        s = res["summary"]
        assert s["contacted"] and s["authenticated"] is True and s["requires_approval"] is True and s["capability_available"] is False
        card = svc.serialize(db, "keyword_planner")
        assert card["display"]["label"] in ("Requires approval", "Blocked by dependency")
        db.execute(delete(IntegrationConfig).where(IntegrationConfig.provider == "keyword_planner"))


def test_runtime_explorer_refusal_is_not_misreported_as_missing_account_access():
    from app.adapters.google_ads.real import RealGoogleAdsAdapter
    err = RealGoogleAdsAdapter(client_factory=lambda: None)._map_error(Exception("authorization_error: DEVELOPER_TOKEN_NOT_APPROVED This method is not allowed for use with explorer access."))
    assert isinstance(err, PermissionDenied) and err.details["requires_approval"] and "Basic or Standard" in err.message


def test_keyword_research_continues_without_keyword_planner_and_says_why(monkeypatch):
    class Refused:
        name, source, calls = "kp", "LIVE", 0

        def generate_ideas(self, *a, **kw):
            Refused.calls += 1
            raise PermissionDenied("Google refused this method for the Cloud project's Explorer API access level.", adapter="kp",
                                   details={"requires_approval": True})
    registry.override("keyword_research", Refused())
    try:
        ctx = {"business_name": "Northside Plumbing", "city": "Austin", "service_names": ["Drain Cleaning", "Water Heater Repair"], "client_id": None,
               "competitors": [], "service_areas": [], "website_url": "https://northside.example", "excluded": [],
               "services": [{"name": "Drain Cleaning", "landing_page_url": ""}, {"name": "Water Heater Repair", "landing_page_url": ""}]}
        with session_scope() as db:
            out = audits.keyword_research(db, ctx, {}, {}, None)
    finally:
        from tests.fakes.keyword_research import MockKeywordResearchAdapter
        registry.override("keyword_research", MockKeywordResearchAdapter())
    kp = out["keyword_planner"]
    assert kp["available"] is False and kp["failed"] and "Explorer" in kp["reason"] and Refused.calls == 1  # not repeated per service
    assert out["keywords"] and "Keyword Planner" not in out["sources_used"]
    assert all(k["avg_monthly_searches"] is None and k["metrics"]["avg_monthly_searches"]["basis"] == "unavailable" for k in out["keywords"])
    assert all(s["source"] != "keyword_planner" for k in out["keywords"] for s in k["sources"])


# --------------------------------------------------------------------------- integrations page
def test_research_providers_are_listed_with_truthful_states():
    items = {i["provider"]: i for i in Api(SUPER).get("/integrations").json()["integrations"]}
    for p in ("tavily", "brave_search", "search_suggestions", "openstreetmap", "geoapify", "web_fetch", "crawler", "keyword_planner"):
        assert p in items, p
    assert items["tavily"]["group"] == items["brave_search"]["group"] == "Research"
    assert items["brave_search"]["display"]["label"] == "Not configured" and items["brave_search"]["missing"] == ["api_key"]
    assert items["web_fetch"]["display"]["config"] == "No credentials needed" and items["web_fetch"]["display"]["label"] != "Connected" or items["web_fetch"]["last_success_at"]
    assert "not a web search" in items["crawler"]["description"]


def test_a_429_from_the_provider_is_shown_as_rate_limited(monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "BSA-not-real")
    from app.core.config import reset_settings_cache
    reset_settings_cache()
    try:
        with session_scope() as db:
            out = svc.test_connection(db, "brave_search", http=client(lambda r: httpx.Response(429, json={})))
            assert out["summary"]["rate_limited"] and svc.serialize(db, "brave_search")["display"]["label"] == "Rate limited"
            db.execute(delete(IntegrationConfig).where(IntegrationConfig.provider == "brave_search"))
    finally:
        monkeypatch.delenv("BRAVE_SEARCH_API_KEY")
        reset_settings_cache()


def test_one_dead_suggestion_source_does_not_fail_the_others():
    def h(req):
        if "bing" in str(req.url):
            return httpx.Response(204)
        return httpx.Response(200, json=["plumber", ["plumber near me", "plumber cost"]])
    with session_scope() as db:
        svc.save(db, "search_suggestions", settings={"sources": "google,duckduckgo,bing"}, secrets={}, user_id=None)
        out = svc.test_connection(db, "search_suggestions", http=client(h))
        assert out["connected"] and out["status"] == "CONNECTED"
        subs = {k: v["status"] for k, v in out["subtests"].items()}
        assert subs == {"google": "PASSED", "duckduckgo": "PASSED", "bing": "SKIPPED"}
        db.execute(delete(IntegrationConfig).where(IntegrationConfig.provider == "search_suggestions"))
