"""Tavily web search (https://tavily.com). Optional and metered: each basic search costs 1 API credit and each advanced search 2,
according to Tavily's published pricing. Results are search snippets, not verified facts; callers record them as web evidence."""
from __future__ import annotations

import httpx

from app.adapters.base import InvalidResponse
from app.adapters.research.http import classify, json_of, request

BASE = "https://api.tavily.com"
CREDITS = {"basic": 1, "advanced": 2}


class TavilyAdapter:
    name = "tavily"
    source = "LIVE"

    def __init__(self, api_key: str, client: httpx.Client | None = None, *, depth: str = "basic"):
        self._key = api_key
        self.client = client or httpx.Client(timeout=30)
        self.depth = depth if depth in CREDITS else "basic"

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

    def search(self, query: str, *, max_results: int = 5, depth: str | None = None, topic: str = "general") -> dict:
        depth = depth if depth in CREDITS else self.depth
        body = {"query": query[:400], "search_depth": depth, "max_results": max(1, min(max_results, 10)), "topic": topic,
                "include_answer": False, "include_raw_content": False, "include_usage": True}
        resp = request(self.client, "POST", f"{BASE}/search", "Tavily", json=body, headers=self._auth())
        classify(resp, "Tavily", quota_statuses=(432, 433))
        data = json_of(resp, "Tavily")
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise InvalidResponse("Tavily answered without a results list", adapter=self.name)
        results = []
        for r in data["results"]:
            if not isinstance(r, dict) or not r.get("url"):
                continue
            results.append({"url": r["url"], "title": (r.get("title") or "")[:300], "content": (r.get("content") or "")[:1500],
                            "score": r.get("score") if isinstance(r.get("score"), (int, float)) else None})
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        credits = usage.get("credits") if isinstance(usage.get("credits"), (int, float)) else CREDITS[depth]
        return {"query": query, "depth": depth, "results": results, "credits": credits,
                "credits_source": "reported by Tavily" if "credits" in usage else "Tavily published price for this search depth"}

    def usage(self) -> dict:
        resp = request(self.client, "GET", f"{BASE}/usage", "Tavily", headers=self._auth())
        classify(resp, "Tavily", quota_statuses=(432, 433))
        data = json_of(resp, "Tavily")
        if not isinstance(data, dict):
            raise InvalidResponse("Tavily usage answered in an unexpected format", adapter=self.name)
        key = data.get("key") if isinstance(data.get("key"), dict) else {}
        return {"used": key.get("usage"), "limit": key.get("limit")}
