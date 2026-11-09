"""Real requests to real services. See conftest.py for how results are reported."""
from __future__ import annotations

import datetime as dt

import httpx
import pytest

from live_tests.conftest import blocked, blocker, not_configured, step_status


# ------------------------------------------------------------------ needs NO user credentials: negative paths against the real APIs
def test_groq_rejects_an_invalid_key_with_a_401_mapped_to_an_auth_error():
    r = httpx.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": "Bearer gsk_invalid_key_for_live_test"},
                   json={"model": "openai/gpt-oss-120b", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}, timeout=20)
    assert r.status_code == 401


def test_google_token_endpoint_rejects_a_bad_refresh_token():
    r = httpx.post("https://oauth2.googleapis.com/token", data={"client_id": "0.apps.googleusercontent.com", "client_secret": "x",
                                                                "refresh_token": "1//bad", "grant_type": "refresh_token"}, timeout=20)
    assert r.status_code in (400, 401) and r.json().get("error") in ("invalid_client", "invalid_grant", "unauthorized_client")


def test_pagespeed_real_request_answers():
    r = httpx.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed", params={"url": "https://example.com", "strategy": "mobile", "category": "performance"}, timeout=60)
    if r.status_code == 429:
        blocked("PageSpeed keyless quota exhausted; save a PageSpeed API key on the Integrations page")
    assert r.status_code == 200 and "lighthouseResult" in r.json()


# ------------------------------------------------------------------ credentials saved on the Integrations page
def test_google_ads_oauth_refresh_and_accessible_customers(integration):
    res = integration("google_ads")
    assert step_status(res, "OAuth") == "PASSED", res["steps"]
    why = blocker(res)
    if why:
        blocked(why)
    assert res["ok"], [s for s in res["steps"] if s["status"] == "FAILED"]


def test_google_ads_account_read_and_campaign_data(integration):
    res = integration("google_ads")
    if step_status(res, "OAuth") != "PASSED":
        pytest.fail(f"OAuth failed: {[s for s in res['steps'] if s['status'] == 'FAILED']}")
    from app.adapters import registry
    ads = registry.get("google_ads")
    from app.adapters.base import PermissionDenied
    from app.services import integrations_service as svc
    v = svc.resolve("google_ads")
    cid = v.get("customer_id") or ""
    if not cid:
        not_configured("Google Ads", "(no client customer ID saved)")
    try:
        customer = ads.get_customer(cid)
        camps = ads.list_campaigns(cid)
        end = dt.date.today() - dt.timedelta(days=1)
        metrics = ads.get_metrics(cid, end - dt.timedelta(days=6), end)
        terms = ads.get_search_terms(cid, end - dt.timedelta(days=29), end)
    except PermissionDenied as exc:
        if "Explorer access" in exc.message:
            blocked("the Google Cloud project only has Test access to the Google Ads API (apply for Explorer access)")
        raise
    assert customer["customer_id"] == cid.replace("-", "")
    assert isinstance(camps, list) and isinstance(metrics, list) and isinstance(terms, list)
    for m in metrics:  # real rows only: every metric is a number the API returned
        assert m["clicks"] >= 0 and m["impressions"] >= m["clicks"] and m["cost_micros"] >= 0


@pytest.mark.parametrize("provider,target", [("groq", "primary"), ("groq", "challenger"), ("claude", "all"), ("places", "all"),
                                              ("pagespeed", "all"), ("ga4", "all"), ("gbp", "all"), ("keyword_planner", "all"), ("smtp", "all"),
                                              ("database", "all"), ("redis", "all"), ("crawler", "all")])
def test_provider_connection(integration, provider, target):
    res = integration(provider, target)
    why = blocker(res)
    if why and not res["ok"]:
        blocked(why)
    assert res["ok"], [s for s in res["steps"] if s["status"] == "FAILED"]
