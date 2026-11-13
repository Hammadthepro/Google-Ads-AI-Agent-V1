"""Brave Search API (https://brave.com/search/api/). Optional fallback web search with its own independent index.

Pricing at the time of writing: metered (about $5 per 1,000 requests) with a monthly credit of about $5 that needs a payment method on
file; it is not an unconditional free plan. Results are titles, URLs and snippets, with country targeting."""
from __future__ import annotations

import httpx

from app.adapters.base import InvalidResponse
from app.adapters.research.http import classify, json_of, request

BASE = "https://api.search.brave.com/res/v1/web/search"


class BraveSearchAdapter:
    name = "brave_search"
    source = "LIVE"

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self._key = api_key
        self.client = client or httpx.Client(timeout=20)

    def search(self, query: str, *, max_results: int = 5, country: str = "US") -> dict:
        params = {"q": query[:400], "count": max(1, min(max_results, 20)), "country": (country or "US").upper(), "safesearch": "moderate"}
        resp = request(self.client, "GET", BASE, "Brave Search", params=params,
                       headers={"X-Subscription-Token": self._key, "Accept": "application/json"})
        classify(resp, "Brave Search", quota_statuses=(402,))
        data = json_of(resp, "Brave Search")
        web = data.get("web") if isinstance(data, dict) else None
        if web is not None and not isinstance(web, dict):
            raise InvalidResponse("Brave answered with an unexpected web block", adapter=self.name)
        results = [{"url": r["url"], "title": (r.get("title") or "")[:300], "content": (r.get("description") or "")[:1500], "score": None}
                   for r in ((web or {}).get("results") or []) if isinstance(r, dict) and r.get("url")]
        return {"query": query, "results": results, "credits": 1, "credits_source": "one metered request"}
