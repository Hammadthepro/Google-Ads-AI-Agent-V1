"""Research provider adapters against controlled HTTP responses (httpx.MockTransport). No request leaves the machine.

They check the contract that matters: real fields are parsed, missing fields stay missing, and every provider failure becomes the
right error class (auth vs permission vs quota vs rate limit vs timeout vs invalid response vs unsupported)."""
from __future__ import annotations

import httpx
import pytest

from app.adapters.base import (AdapterTimeout, AuthError, InvalidResponse, PermissionDenied, QuotaExceeded, RateLimited, ServiceUnavailable,
                               Unsupported)
from app.adapters.research.geo import GeoapifyAdapter, OSMAdapter, geoapify_category_for, osm_tags_for
from app.adapters.research.suggest import SuggestionAdapter
from app.adapters.research.tavily import TavilyAdapter


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_google_and_duckduckgo_suggestions_are_parsed_and_deduplicated():
    seen = []

    def h(req):
        seen.append(req.url)
        return httpx.Response(200, json=["plumber", ["Plumber near me", "plumber  near me", "plumber cost"]])
    a = SuggestionAdapter(client(h), min_interval=0)
    assert a.suggest("google", "plumber") == ["plumber near me", "plumber cost"]
    assert a.suggest("duckduckgo", "plumber")[0] == "plumber near me"
    assert seen[0].host == "suggestqueries.google.com" and seen[0].params["q"] == "plumber" and seen[1].host == "duckduckgo.com"


def test_empty_bing_answer_is_reported_as_unsupported_not_as_zero_suggestions():
    a = SuggestionAdapter(client(lambda req: httpx.Response(204)), min_interval=0)
    with pytest.raises(Unsupported):
        a.suggest("bing", "plumber")


@pytest.mark.parametrize("status,error", [(401, AuthError), (403, PermissionDenied), (429, RateLimited), (503, ServiceUnavailable),
                                          (432, QuotaExceeded), (433, QuotaExceeded)])
def test_tavily_failures_are_classified(status, error):
    a = TavilyAdapter("tvly-test", client(lambda req: httpx.Response(status, json={"detail": "x"})))
    with pytest.raises(error):
        a.search("plumbers austin")


def test_tavily_parses_results_and_credits_and_sends_the_key_only_in_the_header():
    got = {}

    def h(req):
        got["auth"], got["body"] = req.headers.get("authorization"), req.content.decode()
        return httpx.Response(200, json={"results": [{"url": "https://a.example/", "title": "A | Plumbing", "content": "x" * 3000, "score": 0.9},
                                                     {"title": "no url"}], "usage": {"credits": 1}})
    r = TavilyAdapter("tvly-test", client(h)).search("plumbers austin", max_results=3)
    assert got["auth"] == "Bearer tvly-test" and "tvly-test" not in got["body"]
    assert [x["url"] for x in r["results"]] == ["https://a.example/"] and len(r["results"][0]["content"]) == 1500
    assert r["credits"] == 1 and r["credits_source"] == "reported by Tavily"


def test_tavily_without_results_list_is_an_invalid_response():
    with pytest.raises(InvalidResponse):
        TavilyAdapter("k", client(lambda req: httpx.Response(200, json={"error": "?"}))).search("x")


def test_network_timeout_is_a_timeout_not_an_auth_error():
    def h(req):
        raise httpx.ReadTimeout("slow", request=req)
    with pytest.raises(AdapterTimeout):
        TavilyAdapter("k", client(h)).search("x")


def test_overpass_keeps_only_named_places_and_never_invents_fields():
    body = {"elements": [
        {"type": "node", "id": 1, "lat": 30.27, "lon": -97.74, "tags": {"name": "Acme Plumbing", "craft": "plumber", "phone": "+1 512 555 0100"}},
        {"type": "way", "id": 2, "center": {"lat": 30.28, "lon": -97.75}, "tags": {"name": "Beta Pipes", "craft": "plumber"}},
        {"type": "node", "id": 3, "lat": 30.29, "lon": -97.76, "tags": {"craft": "plumber"}},  # unnamed: dropped
    ]}
    sent = {}

    def h(req):
        sent["q"] = req.content.decode()
        return httpx.Response(200, json=body)
    pois = OSMAdapter(client(h)).nearby(30.27, -97.74, 5000, [("craft", "plumber")])
    assert [p["name"] for p in pois] == ["Acme Plumbing", "Beta Pipes"]
    assert pois[0]["phone"] == "+1 512 555 0100" and "phone" not in pois[1] and "website" not in pois[1]
    assert all("rating" not in p and "reviews" not in p for p in pois)
    assert pois[1]["source_ref"] == "way/2" and pois[0]["attribution"].startswith("© OpenStreetMap")
    assert "around%3A5000" in sent["q"] or "around:5000" in sent["q"]


def test_unknown_business_type_has_no_map_category():
    assert osm_tags_for("Emergency Plumbing Services") == [("craft", "plumber")]
    assert osm_tags_for("quantum consulting") == [] and geoapify_category_for("quantum consulting") == ""
    with pytest.raises(Unsupported):
        OSMAdapter(client(lambda r: httpx.Response(200, json={"elements": []}))).nearby(1, 1, 1000, [])


def test_nominatim_geocoding_and_empty_result():
    a = OSMAdapter(client(lambda req: httpx.Response(200, json=[{"lat": "30.26", "lon": "-97.74", "display_name": "Austin, Texas", "osm_type": "relation", "osm_id": 1}])))
    g = a.geocode("Austin, Texas")
    assert g["lat"] == 30.26 and g["source"] == "openstreetmap_nominatim"
    assert OSMAdapter(client(lambda req: httpx.Response(200, json=[]))).geocode("nowhere") is None


def test_geoapify_auth_failure_and_places_parsing():
    with pytest.raises(AuthError):
        GeoapifyAdapter("bad", client(lambda req: httpx.Response(401, json={"message": "Invalid apiKey"}))).geocode("Austin")
    feats = {"features": [{"properties": {"name": "Smile Dental", "lat": 30.2, "lon": -97.7, "place_id": "p1", "formatted": "1 Main St",
                                          "contact": {"phone": "555"}}},
                          {"properties": {"lat": 30.2, "lon": -97.7}}]}
    pois = GeoapifyAdapter("k", client(lambda req: httpx.Response(200, json=feats))).nearby(30.2, -97.7, 3000, "healthcare.dentist")
    assert len(pois) == 1 and pois[0]["phone"] == "555" and pois[0]["source"] == "geoapify_places"
