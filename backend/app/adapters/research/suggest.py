"""Search-suggestion sources for keyword discovery.

These endpoints return the phrases people type, nothing else: no search volume, no CPC, no competition. They are public, keyless and
unofficial (Google's and Bing's suggestion endpoints are not documented APIs), so they can change or stop at any time; every call is
throttled, cached by the caller, and a source that stops answering is reported as unavailable rather than guessed around.
"""
from __future__ import annotations

import httpx

from app.adapters.base import InvalidResponse, Unsupported
from app.adapters.research.http import classify, json_of, request, throttle

SOURCES = ("google", "duckduckgo", "bing")
_ENDPOINTS = {
    "google": ("https://suggestqueries.google.com/complete/search", lambda q, hl, gl: {"client": "firefox", "hl": hl, "gl": gl, "q": q}),
    "duckduckgo": ("https://duckduckgo.com/ac/", lambda q, hl, gl: {"q": q, "type": "list", "kl": f"{gl}-{hl}"}),
    "bing": ("https://api.bing.com/osjson.aspx", lambda q, hl, gl: {"query": q, "market": f"{hl}-{gl.upper()}"}),
}
LABELS = {"google": "Google Autocomplete", "duckduckgo": "DuckDuckGo suggestions", "bing": "Bing suggestions"}


class SuggestionAdapter:
    """One instance serves all sources; `suggest(source, query)` returns the suggestion phrases in the order the source gave them."""

    name = "search_suggestions"
    source = "LIVE"

    def __init__(self, client: httpx.Client | None = None, *, enabled: tuple[str, ...] = SOURCES, min_interval: float = 0.35):
        self.client = client or httpx.Client(timeout=10, follow_redirects=True)
        self.enabled = tuple(s for s in enabled if s in SOURCES)
        self.min_interval = min_interval

    def suggest(self, source: str, query: str, *, hl: str = "en", gl: str = "us") -> list[str]:
        if source not in _ENDPOINTS:
            raise Unsupported(f"Unknown suggestion source {source}", adapter=self.name)
        url, params = _ENDPOINTS[source]
        throttle(f"suggest:{source}", self.min_interval)
        resp = request(self.client, "GET", url, LABELS[source], params=params(query, hl, gl))
        if resp.status_code == 204 or not resp.content:
            raise Unsupported(f"{LABELS[source]} returned no content for this request", adapter=self.name, details={"status": resp.status_code})
        classify(resp, LABELS[source])
        body = json_of(resp, LABELS[source])
        if not (isinstance(body, list) and len(body) >= 2 and isinstance(body[1], list)):
            raise InvalidResponse(f"{LABELS[source]} answered in an unexpected format", adapter=self.name)
        out, seen = [], set()
        for s in body[1]:
            t = " ".join(str(s).split()).lower()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out[:20]
