"""Adapter contract tests. The same expectations run against mocks now and are reused for real adapters
(with fake transports here; with real credentials during Phase 16 integration)."""
from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from app.adapters.base import AuthError, InvalidInput, NotFoundError, RateLimited
from app.adapters.google_ads.mock import MockGoogleAdsAdapter
from app.adapters.keyword_research import MockKeywordResearchAdapter
from app.adapters.pagespeed import MockPageSpeedAdapter, RealPageSpeedAdapter
from app.adapters.places import MockGooglePlacesAdapter, RealGooglePlacesAdapter
from app.core import clock
from tests.conftest import inject


# ------------------------------------------------------------------ Google Ads
def test_google_ads_mock_contract():
    a = MockGoogleAdsAdapter()
    custs = a.list_accessible_customers()
    assert {"customer_id", "descriptive_name", "is_manager"} <= set(custs[0])
    cid = "1110000001"
    assert a.get_account_hierarchy("1000000000")
    camps = a.list_campaigns(cid)
    assert camps and all(c["resource_name"].startswith(f"customers/{cid}/campaigns/") for c in camps)
    groups = a.list_ad_groups(cid, camps[0]["resource_name"])
    assert groups and a.list_keywords(cid, groups[0]["resource_name"]) and a.list_ads(cid, groups[0]["resource_name"])
    end = clock.today() - timedelta(days=1)
    m = a.get_metrics(cid, end - timedelta(days=6), end)
    assert m and {"cost_micros", "clicks", "conversions", "search_budget_lost_impression_share"} <= set(m[0])
    assert a.get_search_terms(cid, end - timedelta(days=29), end)
    assert isinstance(a.get_call_records(cid, end - timedelta(days=6), end), list)
    assert isinstance(a.get_change_events(cid, (clock.now() - timedelta(days=30)).isoformat()), list)
    assert isinstance(a.get_recommendations(cid), list)
    spend = a.get_today_spend(cid)
    assert spend and {"cost_micros", "daily_budget_micros"} <= set(spend[0])
    with pytest.raises(NotFoundError):
        a.get_customer("9999999999")


def test_google_ads_mock_atomic_mutate_and_validate_only():
    a = MockGoogleAdsAdapter()
    cid = "1110000004"
    ops = [{"resource": "campaign_budget", "op": "create", "temp_id": "-1", "fields": {"name": "contract budget", "amount_micros": 10_000_000}},
           {"resource": "campaign", "op": "create", "temp_id": "-2", "fields": {"name": "Contract test campaign", "status": "PAUSED",
            "advertising_channel_type": "SEARCH", "campaign_budget": "temp:-1"}},
           {"resource": "ad_group", "op": "create", "fields": {"name": "AG", "campaign": "temp:-2", "status": "PAUSED"}}]
    assert a.mutate(cid, ops, validate_only=True)["validate_only"] is True
    assert not [c for c in a.list_campaigns(cid) if c["name"] == "Contract test campaign"]
    bad = ops + [{"resource": "ad_group_ad", "op": "create", "fields": {"ad_group": "temp:-2", "ad": {"responsive_search_ad": {"headlines": ["x"]}}}}]
    with pytest.raises(InvalidInput):
        a.mutate(cid, bad)
    assert not [c for c in a.list_campaigns(cid) if c["name"] == "Contract test campaign"]  # all-or-nothing
    res = a.mutate(cid, ops)
    assert len(res["results"]) == 3 and res["source"] == "MOCK"
    camp = a.get_resource(cid, res["results"][1]["resource_name"])
    assert camp["status"] == "PAUSED"
    with pytest.raises(InvalidInput):
        a.mutate(cid, ops)  # duplicate campaign name
    with pytest.raises(InvalidInput):
        a.mutate(cid, [{"resource": "campaign", "op": "remove", "resource_name": camp["resource_name"]}])


@pytest.mark.parametrize("failure,exc", [("auth_failure", AuthError), ("rate_limited", RateLimited), ("not_found", NotFoundError)])
def test_google_ads_mock_failure_injection(failure, exc):
    inject("google_ads", failure, method="list_resources", times=1)
    with pytest.raises(exc):
        MockGoogleAdsAdapter().list_campaigns("1110000001")
    assert MockGoogleAdsAdapter().list_campaigns("1110000001")  # one-shot


def test_partial_and_empty_data():
    inject("google_ads", "partial_data", method="get_metrics")
    end = clock.today() - timedelta(days=1)
    rows = MockGoogleAdsAdapter().get_metrics("1110000001", end - timedelta(days=9), end)
    assert rows and all(r["search_impression_share"] is None for r in rows)
    inject("google_ads", "empty", method="list_accessible_customers")
    assert MockGoogleAdsAdapter().list_accessible_customers() == []


def test_conversion_lag_refresh_changes_recent_days():
    a = MockGoogleAdsAdapter()
    end = clock.today() - timedelta(days=1)
    recent = a.get_metrics("1110000003", end, end)
    from app.mock_world.simulator import lag_factor
    assert lag_factor(1) < 1 and lag_factor(5) == 1
    assert all(r["conversions"] >= 0 for r in recent)


# ------------------------------------------------------------------ Places / PageSpeed / keyword research
def test_places_contract_mock():
    p = MockGooglePlacesAdapter()
    found = p.find_business("Northside Plumbing", "https://www.northsideplumbing.example/")
    assert {"rating", "user_rating_count", "opening_hours", "primary_category", "photo_count", "reviews", "phone", "website"} <= set(found)
    assert found["simulated"] is True
    assert p.text_search("zzzz nothing here") == []


def test_places_real_adapter_with_fake_transport(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")
    from app.core.config import reset_settings_cache
    reset_settings_cache()

    def handler(req: httpx.Request):
        assert req.headers["X-Goog-Api-Key"] == "test-key" and "X-Goog-FieldMask" in req.headers
        if req.url.path.endswith("searchText"):
            return httpx.Response(200, json={"places": [{"id": "abc", "displayName": {"text": "Biz"}, "websiteUri": "https://biz.example"}]})
        if req.url.path.endswith("/places/abc"):
            return httpx.Response(200, json={"id": "abc", "displayName": {"text": "Biz"}, "rating": 4.5, "userRatingCount": 10,
                                             "reviews": [{"rating": 5, "text": {"text": "good"}}]})
        return httpx.Response(429)
    try:
        real = RealGooglePlacesAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
        d = real.find_business("Biz", "https://biz.example")
        assert d["rating"] == 4.5 and d["simulated"] is False and d["reviews"][0]["simulated"] is False
        with pytest.raises(RateLimited):
            real.place_details("other")
    finally:
        reset_settings_cache()


def test_pagespeed_contract_mock_and_real():
    m = MockPageSpeedAdapter().run("https://www.brightsmiledental.example/", "mobile")
    assert 0 <= m["performance_score"] <= 100 and m["performance_score"] < 50 and m["opportunities"]
    body = {"lighthouseResult": {"categories": {"performance": {"score": 0.62}},
                                 "audits": {"largest-contentful-paint": {"numericValue": 3100},
                                            "unused-javascript": {"title": "Reduce JS", "details": {"type": "opportunity", "overallSavingsMs": 900}}}}}
    real = RealPageSpeedAdapter(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))))
    r = real.run("https://x.example/", "mobile")
    assert r["performance_score"] == 62 and r["core_web_vitals"]["lcp_seconds"] == 3.1 and r["opportunities"][0]["id"] == "unused-javascript"
    inject("pagespeed", "outage")
    from app.adapters.base import ServiceUnavailable
    with pytest.raises(ServiceUnavailable):
        MockPageSpeedAdapter().run("https://x.example/", "mobile")


def test_keyword_research_contract():
    ideas = MockKeywordResearchAdapter().generate_ideas("1110000001", ["drain cleaning"], city="Columbus")
    assert ideas and {"keyword", "avg_monthly_searches", "competition", "low_top_of_page_bid_micros", "high_top_of_page_bid_micros"} <= set(ideas[0])
    assert ideas == MockKeywordResearchAdapter().generate_ideas("1110000001", ["drain cleaning"], city="Columbus")  # deterministic
    geos = MockKeywordResearchAdapter().suggest_geo_targets(["43215"])
    assert geos[0]["geo_target_constant"].startswith("geoTargetConstants/")


# ------------------------------------------------------------------ real-adapter pieces with fake Google clients
class _FakeAdsClient:
    """Minimal stand-in for google.ads.googleads.client.GoogleAdsClient (no network)."""

    def __init__(self, service):
        self._service = service

    def get_service(self, name):
        return self._service

    def get_type(self, name):
        class _Names:
            def __init__(self):
                self.names = []
        class _Req:
            def __init__(self):
                self.location_names = _Names()
        return _Req()


def test_real_geo_target_lookup_prefers_postal_codes():
    from types import SimpleNamespace as NS
    from app.adapters.google_ads.real import RealGoogleAdsAdapter
    from app.adapters.keyword_research import GoogleKeywordPlannerAdapter

    class Svc:
        def suggest_geo_target_constants(self, request):
            assert request.location_names.names == ["43215", "43004"] and request.country_code == "US"
            g = lambda rn, name, t: NS(resource_name=rn, canonical_name=name, target_type=t)  # noqa: E731
            return NS(geo_target_constant_suggestions=[
                NS(search_term="43215", geo_target_constant=g("geoTargetConstants/1023640", "Columbus,Ohio", "City")),
                NS(search_term="43215", geo_target_constant=g("geoTargetConstants/9019312", "43215,Ohio", "Postal Code")),
                NS(search_term="43004", geo_target_constant=g("geoTargetConstants/9019101", "43004,Ohio", "Postal Code"))])
    ads = RealGoogleAdsAdapter(client_factory=lambda: _FakeAdsClient(Svc()))
    out = GoogleKeywordPlannerAdapter(ads).suggest_geo_targets(["43215", "43004"])
    assert [o["geo_target_constant"] for o in out] == ["geoTargetConstants/9019312", "geoTargetConstants/9019101"]
    assert all(o["simulated"] is False for o in out)


def test_real_geo_target_lookup_reports_unknown_zip():
    from types import SimpleNamespace as NS
    from app.adapters.base import AdapterError
    from app.adapters.google_ads.real import RealGoogleAdsAdapter
    from app.adapters.keyword_research import GoogleKeywordPlannerAdapter

    class Svc:
        def suggest_geo_target_constants(self, request):
            return NS(geo_target_constant_suggestions=[])
    ads = RealGoogleAdsAdapter(client_factory=lambda: _FakeAdsClient(Svc()))
    with pytest.raises(AdapterError):
        GoogleKeywordPlannerAdapter(ads).suggest_geo_targets(["00000"])


def test_keyword_mirror_stores_resource_name_used_by_pause_proposals(db):
    from sqlalchemy import select
    from app.models import Keyword
    kws = list(db.scalars(select(Keyword).limit(20)))
    assert kws and all(k.external_resource_name.endswith(f"/{k.external_id}") for k in kws)
