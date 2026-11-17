"""DataForSEO: credentials stored encrypted and never returned, the connection test (free User Data API), authentication failures, every
endpoint's documented response parsed exactly, location and language handling, the 12-per-minute Google Ads throttle, bounded retries,
per-keyword caching, competitor verification by real search overlap, missing credentials, and no fabricated data when it fails.

Responses are fixtures in the documented v3 shapes (docs.dataforseo.com/v3) served through httpx.MockTransport: tests only, no network."""
from __future__ import annotations

import base64
import json
import time
import uuid

import httpx
import pytest

from app.adapters import registry
from app.adapters.base import AuthError, InvalidInput, QuotaExceeded, RateLimited, ServiceUnavailable
from app.adapters.research.dataforseo import DataForSEOAdapter
from app.core.errors import IntegrationNotConfigured
from app.db.session import session_scope
from app.services import dataforseo_research as d4s
from app.services import integrations_service as svc
from app.services import provider_calls
from tests.conftest import SUPER, Api

LOGIN, PASSWORD = "agency@example.com", "d4s-Secret-Passw0rd-xyz"


def env(result, *, status=20000, message="Ok.", cost=0.05, task_status=None, task_message=None):
    """A DataForSEO v3 response envelope (version, status_code, cost, tasks[{status_code, cost, result}])."""
    return {"version": "0.1.20261101", "status_code": status, "status_message": message, "time": "0.5 sec.", "cost": cost, "tasks_count": 1,
            "tasks_error": 0 if (task_status or 20000) == 20000 else 1,
            "tasks": [{"id": uuid.uuid4().hex, "status_code": task_status or 20000, "status_message": task_message or "Ok.", "time": "0.4 sec.",
                       "cost": cost, "result_count": len(result or []), "path": ["v3"], "data": {}, "result": result}]}


def ads_item(kw, vol, cpc=None, comp="MEDIUM", idx=40, low=1.2, high=4.5, loc=2840, lang="en"):
    return {"keyword": kw, "spell": None, "location_code": loc, "language_code": lang, "search_partners": False, "competition": comp,
            "competition_index": idx, "search_volume": vol, "low_top_of_page_bid": low, "high_top_of_page_bid": high, "cpc": cpc,
            "monthly_searches": [{"year": 2026, "month": 10, "search_volume": vol}, {"year": 2026, "month": 9, "search_volume": vol}] if vol else None}


def kinfo(vol, cpc, level="HIGH"):
    return {"se_type": "google", "last_updated_time": "2026-11-01 08:00:00 +00:00", "competition": 0.62, "competition_level": level, "cpc": cpc,
            "search_volume": vol, "low_top_of_page_bid": 2.1, "high_top_of_page_bid": 7.9,
            "monthly_searches": [{"year": 2026, "month": 10, "search_volume": vol}]}


class FakeD4S:
    """Answers like DataForSEO for the documented endpoints and records (method, path, body, auth)."""

    def __init__(self, *, fail=None, volumes=None):
        self.calls, self.fail, self.volumes = [], fail, volumes or {}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/v3/")
        body = json.loads(req.content) if req.content else None
        self.calls.append((req.method, path, body, req.headers.get("authorization")))
        if self.fail:
            r = self.fail(path, body, len(self.calls))
            if r is not None:
                return r
        if path == "appendix/user_data":
            return httpx.Response(200, json=env([{"login": LOGIN, "timezone": "UTC", "money": {"total": 50.0, "balance": 37.25},
                                                  "rates": {"limits": {"minute": {"total": 2000}}}}], cost=0))
        if path.startswith("keywords_data/google_ads/locations/"):
            return httpx.Response(200, json=env([{"location_code": 1025197, "location_name": "Harrisburg,Pennsylvania,United States",
                                                  "location_code_parent": 21170, "country_iso_code": "US", "location_type": "City"},
                                                 {"location_code": 21170, "location_name": "Pennsylvania,United States", "location_type": "State",
                                                  "country_iso_code": "US"}], cost=0))
        t = body[0]
        if path == "keywords_data/google_ads/search_volume/live":
            res = [ads_item(k, self.volumes.get(k, 100 + i), cpc=round(2.0 + i / 10, 2), loc=t.get("location_code", 2840))
                   for i, k in enumerate(t["keywords"]) if not k.startswith("nodata")]
            return httpx.Response(200, json=env(res, cost=0.075))
        if path == "keywords_data/google_ads/keywords_for_keywords/live":
            return httpx.Response(200, json=env([ads_item(f"{t['keywords'][0]} services", 320, cpc=3.4), ads_item(f"best {t['keywords'][0]}", 90, cpc=2.2)]))
        if path == "keywords_data/google_ads/keywords_for_site/live":
            return httpx.Response(200, json=env([ads_item(f"{t['target'].split('.')[0]} reviews", 50, cpc=1.1)]))
        if path == "dataforseo_labs/google/competitors_domain/live":
            items = [{"se_type": "google", "domain": "rivalseo.example", "avg_position": 8.2, "sum_position": 400, "intersections": 47,
                      "full_domain_metrics": {"organic": {"pos_1": 3, "count": 900, "etv": 1200.5, "estimated_paid_traffic_cost": 3400.0},
                                              "paid": {"pos_1": 2, "count": 15, "etv": 80.0}},
                      "metrics": {"organic": {"count": 47, "etv": 210.0}, "paid": {"count": 4, "etv": 12.0}}},
                     {"se_type": "google", "domain": "tinyoverlap.example", "avg_position": 40, "intersections": 2,
                      "full_domain_metrics": {"organic": {"count": 30, "etv": 5.0}}, "metrics": {"organic": {"count": 2, "etv": 0.5}}},
                     {"se_type": "google", "domain": "yelp.com", "avg_position": 3, "intersections": 400, "full_domain_metrics": {}, "metrics": {}}]
            return httpx.Response(200, json=env([{"se_type": "google", "target": t["target"], "location_code": 2840, "language_code": "en",
                                                  "total_count": 3, "items_count": 3, "items": items}], cost=0.0101))
        if path == "dataforseo_labs/google/serp_competitors/live":
            items = [{"se_type": "google", "domain": "serpranker.example", "avg_position": 4, "median_position": 3, "rating": 190, "etv": 66.1,
                      "keywords_count": 3, "visibility": 0.9, "relevant_serp_items": 3, "keywords_positions": {t["keywords"][0]: [2], t["keywords"][1]: [5]}},
                     {"se_type": "google", "domain": "oneoff.example", "avg_position": 9, "median_position": 9, "rating": 91, "etv": 1.0,
                      "keywords_count": 1, "visibility": 0.2, "relevant_serp_items": 1, "keywords_positions": {t["keywords"][0]: [9]}}]
            return httpx.Response(200, json=env([{"se_type": "google", "seed_keywords": t["keywords"], "location_code": 2840, "language_code": "en",
                                                  "total_count": 2, "items_count": 2, "items": items}], cost=0.0101))
        if path == "dataforseo_labs/google/domain_intersection/live":
            n = 6 if t["target2"] == "rivalseo.example" else 1
            items = [{"se_type": "google", "keyword_data": {"se_type": "google", "keyword": f"seo audit {i}", "location_code": 2840, "language_code": "en",
                                                            "keyword_info": kinfo(200 + i, 5.5)},
                      "first_domain_serp_element": {"se_type": "google", "type": "organic", "rank_group": 4, "rank_absolute": 5, "domain": t["target1"],
                                                    "url": f"https://{t['target1']}/seo"},
                      "second_domain_serp_element": {"se_type": "google", "type": "organic", "rank_group": 2, "rank_absolute": 2, "domain": t["target2"],
                                                     "url": f"https://{t['target2']}/seo"}} for i in range(n)]
            return httpx.Response(200, json=env([{"se_type": "google", "location_code": 2840, "language_code": "en", "total_count": n,
                                                  "items_count": n, "items": items}], cost=0.0101))
        if path == "dataforseo_labs/google/keyword_overview/live":
            items = [{"se_type": "google", "keyword": k, "location_code": 2840, "language_code": "en", "keyword_info": kinfo(500, 6.25)} for k in t["keywords"]]
            return httpx.Response(200, json=env([{"location_code": 2840, "language_code": "en", "items_count": len(items), "items": items}]))
        if path == "dataforseo_labs/google/ranked_keywords/live":
            items = [{"se_type": "google", "keyword_data": {"keyword": f"{t['target'].split('.')[0]} pricing", "keyword_info": kinfo(140, 4.0)},
                      "ranked_serp_element": {"se_type": "google", "serp_item": {"type": "organic", "rank_absolute": 3, "url": f"https://{t['target']}/pricing"}}}]
            return httpx.Response(200, json=env([{"se_type": "google", "target": t["target"], "items_count": 1, "items": items}]))
        return httpx.Response(404, json={"status_code": 40400, "status_message": "Not Found."})


def adapter(fake, **kw) -> DataForSEOAdapter:
    return DataForSEOAdapter(LOGIN, PASSWORD, httpx.Client(transport=httpx.MockTransport(fake)), min_interval=kw.pop("min_interval", 0),
                             sleep=kw.pop("sleep", lambda s: None), **kw)


# ------------------------------------------------------------------ credentials, connection test, card
def test_credentials_are_encrypted_never_returned_and_the_connection_test_is_free():
    with session_scope() as db:
        svc.save(db, "dataforseo", settings={"location_name": "United States"}, secrets={"login": LOGIN, "password": PASSWORD}, user_id=None)
    svc.invalidate()
    fake = FakeD4S()
    try:
        with session_scope() as db:
            row = svc._row(db, "dataforseo")
            assert row.secrets_ciphertext and PASSWORD not in row.secrets_ciphertext and LOGIN not in row.secrets_ciphertext
            res = svc.test_connection(db, "dataforseo", http=httpx.Client(transport=httpx.MockTransport(fake)))
            assert res["connected"] is True and [c[1] for c in fake.calls] == ["appendix/user_data", "keywords_data/google_ads/locations/us"]  # nothing paid
            assert fake.calls[0][3] == "Basic " + base64.b64encode(f"{LOGIN}:{PASSWORD}".encode()).decode()  # HTTP Basic, as documented
            item = svc.serialize(db, "dataforseo")
            assert item["info"]["account"]["balance"] == 37.25 and item["label"] == "DataForSEO"
            assert item["secrets"]["login"]["saved"] and item["secrets"]["password"]["saved"]
            assert PASSWORD not in json.dumps(item) and LOGIN not in json.dumps(item)  # neither credential is ever returned
            assert LOGIN not in json.dumps(res) and PASSWORD not in json.dumps(res)
        body = Api(SUPER).get("/integrations").text
        assert PASSWORD not in body and LOGIN not in body and "DataForSEO" in body
        assert svc.resolve("dataforseo")["password"] == PASSWORD  # the backend can read it back for requests
    finally:
        with session_scope() as db:
            svc.disconnect(db, "dataforseo", None)
        svc.invalidate()
    assert not svc.resolve("dataforseo").get("password")  # disconnect removes the credentials


def test_no_environment_variables_are_needed_or_read():
    assert "dataforseo" not in svc._ENV
    assert set(svc.PROVIDERS["dataforseo"]["secrets"]) == {"login", "password"} and svc.PROVIDERS["dataforseo"]["required"] == ["login", "password"]


def test_a_wrong_login_is_reported_as_an_authentication_failure():
    def deny(path, body, n):
        return httpx.Response(401, json={"status_code": 40100, "status_message": "You are not authorized to access this resource."})
    with pytest.raises(AuthError) as e:
        adapter(FakeD4S(fail=deny)).user_data()
    assert e.value.kind == "auth_failure" and PASSWORD not in e.value.message
    with session_scope() as db:
        svc.save(db, "dataforseo", settings={}, secrets={"login": LOGIN, "password": "wrong-password"}, user_id=None)
    svc.invalidate()
    try:
        with session_scope() as db:
            res = svc.test_connection(db, "dataforseo", http=httpx.Client(transport=httpx.MockTransport(FakeD4S(fail=deny))))
            assert res["connected"] is False and "wrong-password" not in json.dumps(res)
    finally:
        with session_scope() as db:
            svc.disconnect(db, "dataforseo", None)
        svc.invalidate()


def test_missing_credentials_are_a_clear_integration_error_never_a_fallback():
    svc.invalidate()
    registry._overrides.pop("dataforseo", None)
    with pytest.raises(IntegrationNotConfigured):
        registry.get("dataforseo")
    ad, why = d4s.adapter_or_reason()
    assert ad is None and "DataForSEO" in why
    with pytest.raises(AuthError):
        DataForSEOAdapter(LOGIN, "")


# ------------------------------------------------------------------ endpoints parsed exactly
def test_search_volume_returns_every_documented_metric_with_location_language_and_time():
    fake = FakeD4S()
    out = adapter(fake).search_volume(["SEO Agency", "seo agency", "ppc management"], location_code=1025197, language_code="EN")
    m, path, body, _ = fake.calls[0]
    assert path == "keywords_data/google_ads/search_volume/live" and body == [{"keywords": ["seo agency", "ppc management"],
                                                                                "location_code": 1025197, "language_code": "en"}]
    r = out["items"][0]
    assert (r["keyword"], r["search_volume"], r["cpc"], r["competition_level"], r["competition_index"]) == ("seo agency", 100, 2.0, "MEDIUM", 40)
    assert (r["low_top_of_page_bid"], r["high_top_of_page_bid"], r["location_code"], r["language_code"]) == (1.2, 4.5, 1025197, "en")
    assert r["monthly_searches"][0] == {"year": 2026, "month": 10, "search_volume": 100} and r["retrieved_at"] and out["cost"] == 0.075
    with pytest.raises(InvalidInput):
        adapter(fake).search_volume([f"kw {i}" for i in range(1001)])


def test_keywords_for_keywords_keeps_its_seeds_and_accepts_at_most_twenty():
    fake = FakeD4S()
    out = adapter(fake).keywords_for_keywords(["seo", "ppc"], location_name="United States")
    assert fake.calls[0][2] == [{"keywords": ["seo", "ppc"], "location_name": "United States", "language_code": "en"}]
    assert out["items"][0]["keyword"] == "seo services" and out["items"][0]["seeds"] == ["seo", "ppc"] and out["items"][0]["cpc"] == 3.4
    with pytest.raises(InvalidInput):
        adapter(fake).keywords_for_keywords([f"seed {i}" for i in range(21)])


def test_keywords_for_site_keeps_the_source_domain():
    fake = FakeD4S()
    out = adapter(fake).keywords_for_site("https://www.napollo.example/services")
    assert fake.calls[0][2][0]["target"] == "napollo.example" and fake.calls[0][2][0]["target_type"] == "site"
    assert out["items"][0]["source_domain"] == "napollo.example" and out["items"][0]["search_volume"] == 50


def test_labs_endpoints_parse_competitors_intersections_overview_and_rankings():
    fake = FakeD4S()
    ad = adapter(fake)
    comp = ad.competitors_domain("https://www.napollo.example/")
    assert comp["items"][0]["domain"] == "rivalseo.example" and comp["items"][0]["shared_keywords"] == 47
    assert comp["items"][0]["organic"]["etv"] == 1200.5 and comp["items"][0]["paid"]["count"] == 15 and comp["items"][0]["shared_organic"]["count"] == 47
    serp = ad.serp_competitors(["seo agency", "seo services"])
    assert serp["items"][0]["keywords_count"] == 3 and serp["items"][0]["keywords_positions"]["seo agency"] == [2]
    inter = ad.domain_intersection("napollo.example", "https://rivalseo.example")
    assert fake.calls[-1][2][0]["intersections"] is True and fake.calls[-1][2][0]["target2"] == "rivalseo.example"
    k = inter["items"][0]
    assert (k["keyword"], k["search_volume"], k["cpc"], k["competition"], k["competition_level"]) == ("seo audit 0", 200, 5.5, 0.62, "HIGH")
    assert k["target1_serp"]["rank_absolute"] == 5 and k["target2_serp"]["rank_absolute"] == 2 and k["data_updated_at"].startswith("2026-11-01")
    ov = ad.keyword_overview(["seo agency"])
    assert ov["items"][0]["search_volume"] == 500 and ov["items"][0]["cpc"] == 6.25
    rk = ad.ranked_keywords("rivalseo.example")
    assert rk["items"][0]["keyword"] == "rivalseo pricing" and rk["items"][0]["serp"]["rank_absolute"] == 3
    for _, path, body, _ in fake.calls:
        assert body[0]["language_code"] == "en" and ("location_name" in body[0] or "location_code" in body[0])  # Labs requires both


def test_location_resolves_to_the_business_city_and_labs_stay_country_level():
    from app.models import ProviderCache
    with session_scope() as db:  # (test database) start without a cached location list, so the free locations call is observed
        db.query(ProviderCache).filter(ProviderCache.provider == "dataforseo").delete()
    fake = FakeD4S()
    registry.override("dataforseo", adapter(fake))
    ctx = {"city": "Harrisburg", "service_areas": [{"label": "Harrisburg, PA", "city": "Harrisburg", "state": "PA"}], "states": ["PA"]}
    where = d4s.resolve_location(registry.get("dataforseo"), ctx)
    assert where["keywords"]["location_code"] == 1025197 and where["keywords"]["location_name"] == "Harrisburg,Pennsylvania,United States"
    assert where["labs"]["location_name"] == "United States" and where["labs"]["language_code"] == "en"
    assert fake.calls[0][1] == "keywords_data/google_ads/locations/us"
    assert d4s.resolve_location(registry.get("dataforseo"), {"city": "Nowhere"})["keywords"]["basis"] == "configured default location"
    registry._overrides.pop("dataforseo", None)


# ------------------------------------------------------------------ errors, retries, rate limit
def test_documented_status_codes_become_clear_errors():
    def code(c, http=200):
        return lambda path, body, n: httpx.Response(http, json=env(None, task_status=c, task_message="msg") if http == 200 else {"status_code": c})
    with pytest.raises(QuotaExceeded):
        adapter(FakeD4S(fail=code(40210))).search_volume(["seo"])          # insufficient funds
    with pytest.raises(InvalidInput):
        adapter(FakeD4S(fail=code(40501))).search_volume(["seo"])          # invalid field
    with pytest.raises(QuotaExceeded):
        adapter(FakeD4S(fail=lambda p, b, n: httpx.Response(402, json={}))).search_volume(["seo"])
    assert adapter(FakeD4S(fail=code(40102))).search_volume(["seo"])["items"] == []  # "no search results" is an empty answer


def test_transient_failures_are_retried_a_bounded_number_of_times():
    sleeps = []

    def flaky(path, body, n):
        return httpx.Response(200, json=env(None, task_status=40202, task_message="rate limit")) if n == 1 else None
    fake = FakeD4S(fail=flaky)
    out = adapter(fake, sleep=sleeps.append, retries=2).search_volume(["seo"])
    assert out["items"][0]["keyword"] == "seo" and len(fake.calls) == 2 and len(sleeps) == 1
    down = FakeD4S(fail=lambda p, b, n: httpx.Response(200, json=env(None, task_status=50000, task_message="internal error")))
    with pytest.raises(ServiceUnavailable):
        adapter(down, retries=2).search_volume(["seo"])
    assert len(down.calls) == 3  # 1 try + 2 retries, then it stops
    bad = FakeD4S(fail=lambda p, b, n: httpx.Response(200, json=env(None, task_status=40501, task_message="invalid field")))
    with pytest.raises(InvalidInput):
        adapter(bad, retries=2).search_volume(["seo"])
    assert len(bad.calls) == 1  # a request error is never retried
    rl = FakeD4S(fail=lambda p, b, n: httpx.Response(429, json={}))
    with pytest.raises(RateLimited):
        adapter(rl, retries=1).search_volume(["seo"])
    assert len(rl.calls) == 2


def test_google_ads_endpoints_are_throttled_and_labs_are_not():
    from app.adapters.research import http as rhttp
    rhttp._last.pop("dataforseo_google_ads", None)
    fake = FakeD4S()
    ad = adapter(fake, min_interval=0.15)
    t0 = time.monotonic()
    for k in ("a1", "a2", "a3"):
        ad.search_volume([k])
    assert time.monotonic() - t0 >= 0.29  # at most one Google Ads Live request per interval (12 per minute in production)
    t1 = time.monotonic()
    for _ in range(3):
        ad.keyword_overview(["seo"])
    assert time.monotonic() - t1 < 0.15
    assert DataForSEOAdapter(LOGIN, PASSWORD).min_interval == 5.0  # 60 s / 12 requests


# ------------------------------------------------------------------ research service: caching, competitors, no fabrication
def test_each_keyword_is_bought_once_and_batches_are_split_at_1000():
    fake = FakeD4S()
    ad = adapter(fake)
    where = {"location_name": "United States", "language_code": "en"}
    tag = uuid.uuid4().hex[:6]
    first = d4s.keyword_metrics(ad, [f"seo {tag}", f"ppc {tag}", f"nodata {tag}"], where)
    assert first["sent"] == 1 and first["rows"][f"seo {tag}"]["search_volume"] == 100 and first["rows"][f"nodata {tag}"]["search_volume"] is None
    again = d4s.keyword_metrics(ad, [f"seo {tag}", f"ppc {tag}", f"nodata {tag}", f"web {tag}"], where)
    assert again["sent"] == 1 and fake.calls[-1][2][0]["keywords"] == [f"web {tag}"]  # only the new keyword is paid for
    assert again["rows"][f"seo {tag}"]["cached"] is True and again["rows"][f"web {tag}"]["cached"] is False
    big = d4s.keyword_metrics(ad, [f"k{i} {tag}" for i in range(1500)], where)
    assert big["sent"] == 2 and [len(c[2][0]["keywords"]) for c in fake.calls[-2:]] == [1000, 500]
    usage = provider_calls.usage("dataforseo")
    assert usage["requests_sent"] >= 4 and usage["cost_units"] > 0  # cost recorded from the responses


def test_competitors_are_verified_only_on_search_overlap():
    fake = FakeD4S()
    ad = adapter(fake)
    ctx = {"website_url": "https://www.napollo.example/", "city": "Harrisburg"}
    cands = [{"domain": "https://www.rivalseo.example/", "name": "Rival SEO", "source": "client profile"},
             {"domain": "https://onlyonce.example/", "name": "Only Once", "source": "web research (serpapi)"}]
    out = d4s.competitor_research(ad, ctx, [f"seo {uuid.uuid4().hex[:6]}", "seo services"], cands,
                                  {"location_name": "United States", "language_code": "en"})
    by = {c["domain"]: c for c in out["competitors"]}
    r = by["rivalseo.example"]
    assert r["verified"] and r["shared_keywords"] == 47 and len(r["intersection_keywords"]) == 6 and r["organic"]["etv"] == 1200.5
    assert {"client profile", "dataforseo:competitors_domain"} <= set(r["sources"]) and r["target_domain"] == "napollo.example"
    assert r["location"] == "United States" and r["last_updated"] and r["ranked_keywords"][0]["keyword"] == "rivalseo pricing"
    assert by["serpranker.example"]["verified"] and by["serpranker.example"]["serp_keywords"] == 3
    assert not by["onlyonce.example"]["verified"] and by["onlyonce.example"]["status"] == "candidate"   # seen once in a result: not verified
    assert not by["oneoff.example"]["verified"] and not by["tinyoverlap.example"]["verified"]           # too little overlap
    assert "yelp.com" not in by                                                                        # directories are not competitors


def test_a_failing_provider_is_reported_and_nothing_is_fabricated():
    fake = FakeD4S(fail=lambda p, b, n: httpx.Response(200, json=env(None, task_status=40210, task_message="Insufficient funds.")))
    ad = adapter(fake)
    tag = uuid.uuid4().hex[:6]
    met = d4s.keyword_metrics(ad, [f"seo {tag}"], {"location_name": "United States", "language_code": "en"})
    assert met["rows"] == {} and met["errors"][0]["kind"] == "insufficient_funds"  # no row, no invented number
    site = f"https://fresh-{tag}.example"  # (a domain no earlier request cached: a cached answer would correctly be reused)
    comp = d4s.competitor_research(ad, {"website_url": site}, [f"seo {tag}"],
                                   [{"domain": "rivalseo.example", "name": "Rival", "source": "client profile"}],
                                   {"location_name": "United States", "language_code": "en"})
    assert comp["errors"] and all(not c["verified"] for c in comp["competitors"])  # client candidate kept, but not called verified
    assert all(e.get("skipped") for e in comp["errors"])  # a blocking failure stops later requests of the run: nothing more was sent
    from app.models import ProviderCall
    with session_scope() as db:  # the refusal is in the usage ledger (newest-row order depends on the test clock, so check the row itself)
        assert db.query(ProviderCall).filter(ProviderCall.provider == "dataforseo", ProviderCall.ok.is_(False),
                                             ProviderCall.error_kind == "insufficient_funds").count() >= 1
    assert provider_calls.usage("dataforseo")["last_error"] is not None


def test_unverified_account_40104_is_not_an_auth_error_and_blocks_research():
    """The ABC regression: login accepted, but the account must be verified (40104). The connection test must not report Connected,
    the label must say why, and the run must stop after the first refused request."""
    from app.adapters.base import AccountNotReady
    def fail(path, body, n):
        if path.startswith("keywords_data/") or path.startswith("dataforseo_labs/"):
            return httpx.Response(200, json=env(None, status=40104, message="Please verify your account before using the API."))
        return None
    fake = FakeD4S(fail=fail)
    ad = adapter(fake)
    with pytest.raises(AccountNotReady) as ei:
        ad.search_volume(["seo"], location_name="United States", language_code="en")
    assert ei.value.kind == "account_verification_required" and "verified" in ei.value.message and "40104" in ei.value.message
    assert not isinstance(ei.value, AuthError)
    fake.calls.clear()
    tag = uuid.uuid4().hex[:6]
    met = d4s.keyword_metrics(ad, [f"a {tag}", f"b {tag}"] + [f"kw{i} {tag}" for i in range(1200)],
                              {"location_name": "United States", "language_code": "en"})
    assert met["rows"] == {} and met["errors"][0]["kind"] == "account_verification_required"
    sent = [c for c in fake.calls if c[1].startswith("keywords_data/google_ads/search_volume")]
    assert len(sent) == 1  # 1202 keywords would be 2 batches: the second is not sent once the account is known to be unverified
    assert met["unmeasured"] == 1202  # every keyword is reported as unmeasured; none is given a number


def test_connection_test_with_unverified_account_is_not_connected():
    def fail(path, body, n):
        if path.startswith("keywords_data/"):
            return httpx.Response(401, json=env(None, status=40104, message="Please verify your account."))
        return None
    with session_scope() as db:
        svc.save(db, "dataforseo", settings={"location_name": "United States"}, secrets={"login": LOGIN, "password": PASSWORD}, user_id=None)
    svc.invalidate()
    with session_scope() as db:
        res = svc.test_connection(db, "dataforseo", http=httpx.Client(transport=httpx.MockTransport(FakeD4S(fail=fail))))
        assert res["connected"] is False  # login accepted, research refused: never "connected"
        item = svc.serialize(db, "dataforseo")
        assert item["info"]["research_block"]["kind"] == "account_verification_required"
        assert "verification" in json.dumps(item).lower() and LOGIN not in json.dumps(item) and PASSWORD not in json.dumps(item)


def test_research_uses_dataforseo_metrics_with_provenance_end_to_end():
    """A real build with DataForSEO configured: its ideas and metrics enter the keyword table with source and time, Keyword Planner's
    values are never overwritten, and the AI receives [keyword, volume, CPC] only as reported."""
    from tests.conftest import ADMIN
    from tests.integration.test_intake_v2 import _fixture_client
    fake = FakeD4S()
    registry.override("dataforseo", adapter(fake))
    try:
        api = Api(ADMIN)
        cid, bid = _fixture_client(api, f"D4S Co {uuid.uuid4().hex[:4]}", weekly_target=3)
        t = api.post("/tasks?sync=true", {"task_type": "BUILD_CAMPAIGN", "client_id": cid, "business_id": bid, "fields": {"monthly_budget": 3000}}).json()
        assert t["status"] in ("AWAITING_APPROVAL", "VALIDATION_FAILED"), (t["status"], t["error"])
        from app.models import Audit
        with session_scope() as db:
            kw = db.query(Audit).filter(Audit.task_id == t["id"], Audit.audit_type == "KEYWORD").one().result
            comp = db.query(Audit).filter(Audit.task_id == t["id"], Audit.audit_type == "COMPETITOR").one().result
        assert kw["dataforseo"]["used"] is True and kw["dataforseo"]["ideas"] > 0
        d4s_rows = [r for r in kw["keywords"] if (r["metrics"]["avg_monthly_searches"].get("source") or "").startswith("dataforseo")]
        assert d4s_rows
        for r in d4s_rows:
            m = r["metrics"]["avg_monthly_searches"]
            assert m["retrieved_at"] and m["basis"] in ("observed", "unavailable") and r["metrics"]["cpc"]["source"].startswith("dataforseo")
            assert (m["value"] is None) == (r["avg_monthly_searches"] is None)
        planner_rows = [r for r in kw["keywords"] if any(s["source"] == "keyword_planner" for s in r["sources"])]
        assert all((r["metrics"]["avg_monthly_searches"].get("source") or "") != "dataforseo:search_volume" for r in planner_rows)
        assert any(s["source"].startswith("dataforseo") for r in kw["keywords"] for s in r["sources"])
        assert comp["dataforseo"]["used"] is True and any(c["verified"] for c in comp["dataforseo"]["competitors"])
        app_paths = {c[1] for c in fake.calls}
        assert {"keywords_data/google_ads/search_volume/live", "dataforseo_labs/google/competitors_domain/live"} <= app_paths
        a = api.get(f"/plans/{t['plan_id']}/appendix").json()
        assert any(k["measurement"].startswith("DataForSEO") for k in a["keywords"]["selected"] + a["keywords"]["rejected"])
    finally:
        registry._overrides.pop("dataforseo", None)
