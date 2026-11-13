"""SerpApi: adapter (account, google, ads, maps, autocomplete, transparency), connection test without spending a search, secure storage,
the research planner (location, caching, duplicate prevention, quota, budget, provenance) and its place in keyword/competitor research.
Every SerpApi response here is a recorded-shape fixture served through httpx.MockTransport; nothing touches the network."""
from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.adapters import registry
from app.adapters.base import AdapterError
from app.adapters.research.serpapi import SerpApiAdapter
from app.db.session import session_scope
from app.models import Workspace
from app.services import integrations_service as svc
from app.services import provider_calls, serp_research
from tests.conftest import SUPER, Api

KEY = "serpkey_0123456789abcdef0123456789abcdef"


class FakeSerp:
    """Answers like SerpApi for the documented endpoints and records every request (path + params)."""

    def __init__(self, *, left=250, fail=None):
        self.calls, self.left, self.fail = [], left, fail

    def __call__(self, req: httpx.Request) -> httpx.Response:
        q = {k: v[0] for k, v in parse_qs(urlparse(str(req.url)).query).items()}
        self.calls.append((req.url.path, q))
        if self.fail:
            return self.fail(req, q)
        if req.url.path == "/account.json":
            return httpx.Response(200, json={"account_id": "acc_1", "api_key": q.get("api_key"), "account_email": "owner@example.com",
                                             "plan_name": "Developer", "searches_per_month": 5000, "plan_searches_left": self.left,
                                             "extra_credits": 0, "total_searches_left": self.left, "this_month_usage": 5000 - self.left,
                                             "account_rate_limit_per_hour": 1000, "this_hour_searches": 3})
        if req.url.path == "/locations.json":
            assert "api_key" not in q  # the Locations API needs no key; it is never sent there
            city = q["q"].split(",")[0].title()
            return httpx.Response(200, json=[{"name": city, "canonical_name": f"{city},Ohio,United States", "country_code": "US",
                                              "target_type": "City"}])
        assert req.url.path == "/search.json" and q["api_key"] == KEY and q["output"] == "json"
        self.left -= 1
        eng = q["engine"]
        md = {"search_metadata": {"id": f"s_{len(self.calls)}", "status": "Success", "created_at": "2026-11-13 10:00:00 UTC"}}
        if eng == "google":
            term = q["q"]
            return httpx.Response(200, json={**md, "search_parameters": {"location_used": q.get("location")},
                "ads": [{"position": 1, "block_position": "top", "title": f"Fast {term} | RivalCo", "link": "https://www.rivalco.example/landing",
                         "displayed_link": "www.rivalco.example", "description": "Same-day service. Call now.", "sitelinks": [{"title": "Pricing"}]}],
                "organic_results": [{"position": 1, "title": "Best Plumbers Near You - Yelp", "link": "https://www.yelp.com/x"},
                                    {"position": 2, "title": "Northside Plumbing", "link": "https://www.northsideplumbing.example/"},
                                    {"position": 3, "title": "AcmePipes – drains", "link": "https://acmepipes.example/drains", "snippet": "Drain experts"}],
                "related_searches": [{"query": f"{term} cost"}, {"query": f"emergency {term}"}],
                "related_questions": [{"question": f"How much does {term} cost?", "link": "https://help.example/cost"}],
                "local_results": {"places": [{"position": 1, "title": "Local Pipe Pros", "rating": 4.8, "reviews": 120, "address": "1 Main St"}]},
                "serpapi_pagination": {"next": f"https://serpapi.com/search.json?engine=google&q=x&start=10&api_key={KEY}"}})
        if eng == "google_autocomplete":
            return httpx.Response(200, json={**md, "suggestions": [{"value": f"{q['q']} near me"}, {"value": f"{q['q']} prices"}]})
        if eng == "google_maps":
            return httpx.Response(200, json={**md, "local_results": [{"position": 1, "title": "Map Plumbing", "address": "2 Elm St", "phone": "555",
                                                                       "website": "https://mapplumbing.example", "rating": 4.5, "reviews": 9,
                                                                       "gps_coordinates": {"latitude": 39.96, "longitude": -82.99}}],
                                              "serpapi_pagination": {"next": "https://serpapi.com/search.json?start=20"}})
        if eng == "google_ads_transparency_center":
            return httpx.Response(200, json={**md, "ad_creatives": [{"advertiser": "RivalCo", "ad_creative_id": "CR1", "format": "text",
                                                                      "first_shown": 1700000000, "last_shown": 1730000000, "total_days_shown": 300}]})
        return httpx.Response(400, json={"error": "Unsupported engine"})


def _adapter(fake: FakeSerp) -> SerpApiAdapter:
    return SerpApiAdapter(KEY, httpx.Client(transport=httpx.MockTransport(fake)))


# ------------------------------------------------------------------ adapter
def test_account_api_proves_the_key_without_a_search_and_keeps_no_personal_or_secret_fields():
    fake = FakeSerp(left=4321)
    acc = _adapter(fake).account()
    assert [c[0] for c in fake.calls] == ["/account.json"] and fake.calls[0][1]["api_key"] == KEY
    assert acc["plan_name"] == "Developer" and acc["searches_per_month"] == 5000 and acc["total_searches_left"] == 4321
    assert "api_key" not in acc and "account_email" not in acc and "account_id" not in acc
    assert KEY not in repr(_adapter(fake))


def test_google_search_normalises_organic_ads_related_questions_local_and_pagination():
    fake = FakeSerp()
    r = _adapter(fake).google("drain cleaning columbus", location="Columbus,Ohio,United States", gl="us", hl="en", device="mobile", start=10)
    sent = fake.calls[-1][1]
    assert sent["engine"] == "google" and sent["location"] == "Columbus,Ohio,United States" and sent["gl"] == "us" and sent["hl"] == "en"
    assert sent["device"] == "mobile" and sent["start"] == "10"
    assert [o["domain"] for o in r["organic"]] == ["yelp.com", "northsideplumbing.example", "acmepipes.example"]
    assert r["ads"][0] == {"position": 1, "block_position": "top", "title": "Fast drain cleaning columbus | RivalCo", "link": "https://www.rivalco.example/landing",
                           "domain": "rivalco.example", "displayed_link": "www.rivalco.example", "description": "Same-day service. Call now.",
                           "source": None, "sitelinks": ["Pricing"]}
    assert r["related_searches"] == ["drain cleaning columbus cost", "emergency drain cleaning columbus"]
    assert r["related_questions"][0]["question"].startswith("How much") and r["local_results"][0]["title"] == "Local Pipe Pros"
    assert r["next_page"] is True and r["next_start"] == 20 and r["search_id"] and r["retrieved_at"] and r["provider"] == "serpapi"
    assert "api_key" not in r["params"] and KEY not in str(r)


def test_maps_autocomplete_and_transparency_engines():
    fake = FakeSerp()
    a = _adapter(fake)
    m = a.maps("plumber", ll="@39.9612,-82.9988,13z", start=20)
    assert fake.calls[-1][1]["engine"] == "google_maps" and fake.calls[-1][1]["ll"] == "@39.9612,-82.9988,13z" and fake.calls[-1][1]["type"] == "search"
    assert m["places"][0]["website"] == "https://mapplumbing.example" and m["next_start"] == 40
    with pytest.raises(AdapterError) as e:
        a.maps("plumber", ll="Columbus")
    assert e.value.details["error_kind"] == "invalid_input"
    ac = a.autocomplete("drain cleaning")
    assert fake.calls[-1][1]["engine"] == "google_autocomplete" and ac["suggestions"] == ["drain cleaning near me", "drain cleaning prices"]
    t = a.ads_transparency("rivalco.example")
    assert fake.calls[-1][1]["engine"] == "google_ads_transparency_center" and fake.calls[-1][1]["text"] == "rivalco.example"
    assert t["creatives"][0]["ad_creative_id"] == "CR1"


@pytest.mark.parametrize("status,body,kind", [
    (401, {"error": "Invalid API key. Your API key should be here: https://serpapi.com/manage-api-key"}, "auth_failure"),
    (429, {"error": "Your account has run out of searches."}, "quota_exceeded"),
    (429, {"error": "Your account has exceeded the hourly throughput limit."}, "rate_limited"),
    (400, {"error": "Missing query `q` parameter."}, "invalid_input"),
])
def test_provider_errors_have_precise_kinds_and_never_contain_the_key(status, body, kind):
    a = _adapter(FakeSerp(fail=lambda req, q: httpx.Response(status, json=body)))
    with pytest.raises(AdapterError) as e:
        a.google("x")
    assert (e.value.details.get("error_kind") or e.value.kind) == kind and KEY not in e.value.message


def test_an_empty_google_result_is_empty_not_an_error_and_other_json_errors_fail():
    a = _adapter(FakeSerp(fail=lambda req, q: httpx.Response(200, json={"error": "Google hasn't returned any results for this query."})))
    r = a.google("zzzz qqq")
    assert r["empty"] is True and r["organic"] == []
    a = _adapter(FakeSerp(fail=lambda req, q: httpx.Response(200, json={"error": "Something went wrong"})))
    with pytest.raises(AdapterError):
        a.google("x")


# ------------------------------------------------------------------ integration card, test, storage
def test_connection_test_uses_the_account_api_and_the_live_search_only_on_request():
    with session_scope() as db:
        svc.save(db, "serpapi", settings={"country": "us"}, secrets={"api_key": KEY}, user_id=None)
    svc.invalidate()
    fake = FakeSerp(left=1200)
    try:
        with session_scope() as db:
            res = svc.test_connection(db, "serpapi", http=httpx.Client(transport=httpx.MockTransport(fake)))
            assert res["connected"] is True and {c[0] for c in fake.calls} == {"/account.json"}  # no search credit used
            item = svc.serialize(db, "serpapi")
            assert item["info"]["account"]["total_searches_left"] == 1200 and item["info"]["account"]["plan_name"] == "Developer"
            assert KEY not in str(item) and "owner@example.com" not in str(item)
            row = svc._row(db, "serpapi")
            assert row.secrets_ciphertext and KEY not in row.secrets_ciphertext  # encrypted at rest
            live = svc.test_connection(db, "serpapi", http=httpx.Client(transport=httpx.MockTransport(fake)), target="search")
            assert live["ok"] and fake.calls[-1][0] == "/search.json"
        body = Api(SUPER).get("/integrations").text
        assert KEY not in body
    finally:
        with session_scope() as db:
            svc.disconnect(db, "serpapi", None)
        svc.invalidate()


def test_serpapi_credentials_are_isolated_per_workspace():
    with session_scope() as db:
        w = Workspace(name="SerpApi tenant", slug=f"serp-{uuid.uuid4().hex[:8]}")
        db.add(w)
        db.flush()
        wid = w.id
    try:
        with svc.use_workspace(wid):
            with session_scope() as db:
                svc.save(db, "serpapi", settings={}, secrets={"api_key": KEY}, user_id=None)
            svc.invalidate()
            assert svc.resolve("serpapi").get("api_key") == KEY
        svc.invalidate()
        assert svc.resolve("serpapi").get("api_key") != KEY
    finally:  # leave no second tenant's credentials behind (later migration round-trip tests need one row per provider)
        from app.models import IntegrationConfig
        with session_scope() as db:
            db.query(IntegrationConfig).filter(IntegrationConfig.workspace_id == wid).delete()
            db.query(Workspace).filter(Workspace.id == wid).delete()
        svc.invalidate()


# ------------------------------------------------------------------ research planner
def _ctx(city="Columbus, OH", tag=None):
    tag = tag or uuid.uuid4().hex[:6]
    return {"city": city, "address": "", "website_url": "https://www.northsideplumbing.example/", "category": "plumber",
            "service_names": [f"drain cleaning {tag}", f"water heater repair {tag}"], "competitors": [{"name": "RivalCo", "website_url": "https://rivalco.example"}]}


@pytest.fixture
def serp():
    fake = FakeSerp(left=500)
    serp_research._account_memo.clear()
    registry.override("serpapi", _adapter(fake))
    yield fake
    serp_research._account_memo.clear()


def test_research_is_localised_attributed_and_never_counts_cached_answers_as_new(serp):
    ctx = _ctx()
    first = serp_research.research(ctx, stage="keywords", parts=("google", "autocomplete"))
    searches = [c for c in serp.calls if c[0] == "/search.json"]
    assert len(searches) == 4 and all(c[1]["location"] == "Columbus,Ohio,United States" for c in searches if c[1]["engine"] == "google")
    assert first["location"] == "Columbus,Ohio,United States" and all(not q["served_from_cache"] for q in first["queries"])
    assert {"rivalco.example"} == set(first["advertisers"]) and first["sponsored_ads"][0]["query"].startswith("drain cleaning")
    assert all(a["note"].startswith("Public ad copy") for a in first["sponsored_ads"])
    comp = {c["domain"] for c in first["organic_competitors"]}
    assert comp == {"acmepipes.example"}  # the client's own site and directories are excluded
    ev = first["organic_competitors"][0]["appearances"][0]
    assert ev["retrieved_at"] and ev["search_id"] and ev["location"] == "Columbus,Ohio,United States" and ev["provider"] == "serpapi"
    n = len(serp.calls)
    again = serp_research.research(ctx, stage="keywords", parts=("google", "autocomplete"))
    assert len([c for c in serp.calls[n:] if c[0] == "/search.json"]) == 0  # duplicate queries are served from the cache
    assert all(q["served_from_cache"] and q["cached_at"] for q in again["queries"])
    assert again["queries"][0]["retrieved_at"] == first["queries"][0]["retrieved_at"]  # the ORIGINAL retrieval time, not "now"


def test_quota_is_checked_before_a_batch_and_what_cannot_run_is_named(serp):
    serp.left = 1
    out = serp_research.research(_ctx(), stage="keywords", parts=("google", "autocomplete"))
    assert len([c for c in serp.calls if c[0] == "/search.json"]) == 1 and out["quota"]["skipped"] == 3
    assert out["unavailable"] and "search(es) left" in out["unavailable"][0]["reason"]
    assert out["queries"][0]["engine"] == "google"  # highest-value queries first


def test_changed_business_inputs_produce_new_queries_and_clients_stay_separate(serp):
    a = serp_research.research(_ctx("Columbus, OH", "aa1"), stage="keywords", parts=("google",))
    b = serp_research.research(_ctx("Dayton, OH", "aa1"), stage="keywords", parts=("google",))
    assert {q["location"] for q in a["queries"]} == {"Columbus,Ohio,United States"}
    assert {q["location"] for q in b["queries"]} == {"Dayton,Ohio,United States"}
    assert not any(q["served_from_cache"] for q in b["queries"])  # another place is a different request, never reused


def test_without_serpapi_research_continues_and_says_why():
    registry._overrides.pop("serpapi", None)
    registry.reset()
    out = serp_research.research(_ctx(), stage="keywords")
    assert out["used"] is False and "not configured" in out["reason"]
    assert serp_research.evidence_summary(out) is None  # no search -> nothing a model could cite


def test_ads_transparency_runs_only_when_switched_on(serp):
    out = serp_research.research(_ctx(), stage="competitors", parts=("transparency",))
    assert out["queries"] == []
    with session_scope() as db:
        svc.save(db, "serpapi", settings={"ads_transparency": "on"}, secrets={}, user_id=None)
    svc.invalidate()
    registry.override("serpapi", _adapter(serp))
    try:
        out = serp_research.research(_ctx(), stage="competitors", parts=("transparency",))
        assert out["queries"][0]["engine"] == "google_ads_transparency_center" and out["ad_creatives"][0]["ad_creative_id"] == "CR1"
    finally:
        with session_scope() as db:
            svc._row(db, "serpapi").settings = {}
        svc.invalidate()


def test_evidence_for_the_models_is_attributed_and_says_what_it_is_not(serp):
    ev = serp_research.evidence_summary(serp_research.research(_ctx(), stage="keywords", parts=("google",)))
    assert ev["queries"][0]["retrieved_at"] and ev["location"] and ev["advertisers_seen"] == ["rivalco.example"]
    assert ev["public_ad_copy"][0]["url"].startswith("https://www.rivalco.example") and "not search volume" in ev["note"]


# ------------------------------------------------------------------ keyword research: one process, every metric with its source
def test_keyword_research_merges_serp_phrases_without_inventing_metrics(serp):
    from app.services import audits
    from app.models import Business
    from sqlalchemy import select
    with session_scope() as db:
        biz = db.scalar(select(Business).where(Business.name == "Northside Plumbing"))
        ctx = audits.build_context(db, biz)
        res = audits.keyword_research(db, ctx, {}, {}, None)
        rows = res["keywords"]
        serp_rows = [r for r in rows if any(s["source"].startswith("serpapi_") for s in r["sources"])]
        assert serp_rows, "related searches / autocomplete phrases reach the keyword table"
        for r in serp_rows:
            if not any(s["source"] == "keyword_planner" for s in r["sources"]):
                assert r["avg_monthly_searches"] is None and r["metrics"]["avg_monthly_searches"]["basis"] == "unavailable"
        planner_rows = [r for r in rows if r["metrics"]["avg_monthly_searches"]["source"] == "google_keyword_planner" and r["avg_monthly_searches"] is not None]
        assert planner_rows  # volumes still come only from Keyword Planner
        observed = [r for r in rows if r.get("serp_observation")]
        assert all("not a CPC" in r["serp_observation"]["note"] for r in observed)
        assert res["discovery"]["serp"]["used"] is True
        db.rollback()


# ------------------------------------------------------------------ Google Ads diagnostics
@pytest.mark.parametrize("text,cause", [
    ("GoogleAdsException ... authorization_error: DEVELOPER_TOKEN_PROHIBITED", "project_access"),
    ("... request_error ... SERVICE_DISABLED Google Ads API has not been used in project 123", "project_access"),
    ("authorization_error: DEVELOPER_TOKEN_NOT_APPROVED explorer access", "project_access"),
    ("authorization_error: USER_PERMISSION_DENIED", "account_access"),
    ("PERMISSION_DENIED: The caller does not have permission", "unspecified"),
])
def test_project_authorization_failures_are_not_blamed_on_the_customer_account(text, cause):
    from app.adapters.google_ads.real import RealGoogleAdsAdapter
    a = RealGoogleAdsAdapter.__new__(RealGoogleAdsAdapter)
    err = a._map_error(Exception(text))
    assert err.details["cause"] == cause
    if cause != "account_access":
        assert "cannot access this Google Ads account" not in err.message


def test_research_customer_is_platform_level_and_never_a_client_account():
    from app.adapters.keyword_research import GoogleKeywordPlannerAdapter
    with session_scope() as db:
        svc.save(db, "keyword_planner", settings={"research_customer_id": "123-456-7890"}, secrets={}, user_id=None)
    svc.invalidate()
    try:
        kp = GoogleKeywordPlannerAdapter.__new__(GoogleKeywordPlannerAdapter)
        assert kp._customer("9998887776") == "1234567890"  # a client's deployment account passed in is ignored
    finally:
        with session_scope() as db:
            svc._row(db, "keyword_planner").settings = {}
        svc.invalidate()


def test_provider_usage_ledger_records_live_and_cached_requests(serp):
    serp_research.research(_ctx(), stage="keywords", parts=("autocomplete",))
    u = provider_calls.usage("serpapi")
    assert u["requests_sent"] >= 2


def test_maps_never_searches_for_a_business_type_code(serp):
    ctx = {**_ctx(), "category": "home_services"}
    plan = serp_research.plan(ctx, ("maps",), "", "@39.9612,-82.9988,13z")
    assert plan[0]["q"] == ctx["service_names"][0].lower()
