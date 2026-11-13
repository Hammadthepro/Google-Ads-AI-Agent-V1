"""SerpApi (https://serpapi.com): structured JSON of public Google results.

Endpoints used (as documented by SerpApi and its official Python client `serpapi`):
  GET https://serpapi.com/account.json      Account API: plan, monthly allowance, searches left. Does not use a search credit.
  GET https://serpapi.com/locations.json    Locations API: canonical Google location names for `location`.
  GET https://serpapi.com/search.json       Search API, with `engine`:
        google                          organic results, sponsored ads (`ads`), related searches, related questions, local pack,
                                        pagination through `start` / `serpapi_pagination.next`; parameters q, location, gl, hl, google_domain, device
        google_maps                     local businesses; parameters q, ll ("@lat,lng,zoom"), type=search, start, hl, gl
        google_autocomplete             Google's query suggestions; parameters q, gl, hl
        google_ads_transparency_center  ads an advertiser ran (Ads Transparency Center); OFF by default in this app (see the integration card)

Errors (SerpApi status codes): 401 invalid key, 400 missing/invalid parameter, 429 hourly throughput limit OR the account ran out of
searches; a JSON `error` can also come with HTTP 200 (e.g. "Google hasn't returned any results for this query." = an empty result).

What these results are: what Google showed for one query, place, device and time. A rank is not search volume, a sponsored ad is not a
CPC or proof that the advertiser performs, and an autocomplete suggestion has no volume. The key is sent as the `api_key` query parameter
(SerpApi's authentication), never logged, and removed from every URL this adapter returns.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from app.adapters.base import AuthError, InvalidInput, InvalidResponse, QuotaExceeded, RateLimited
from app.adapters.research.http import classify, json_of, request
from app.core import clock

BASE = "https://serpapi.com"
DEVICES = ("desktop", "mobile", "tablet")
NO_RESULTS = re.compile(r"hasn'?t returned any results", re.I)
OUT_OF_SEARCHES = re.compile(r"run out of searches|no more searches|searches? (?:left|remaining)|plan.*(?:limit|exceeded)", re.I)
ACCOUNT_FIELDS = ("plan_id", "plan_name", "plan_monthly_price", "searches_per_month", "plan_searches_left", "extra_credits",
                  "total_searches_left", "this_month_usage", "this_hour_searches", "last_hour_searches", "account_rate_limit_per_hour",
                  "account_status")


def _strip_key(url: str | None) -> str:
    """Remove api_key from a URL SerpApi returned (e.g. serpapi_pagination.next), so it can be stored and shown."""
    if not url:
        return ""
    return re.sub(r"([?&])api_key=[^&]*&?", r"\1", url).rstrip("?&")


def host(u: str) -> str:
    h = (urlparse(u if "//" in (u or "") else f"https://{u}").hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


class SerpApiAdapter:
    name = "serpapi"
    source = "LIVE"

    def __init__(self, api_key: str, client: httpx.Client | None = None, *, gl: str = "us", hl: str = "en", device: str = "desktop"):
        self._key = api_key
        self.client = client or httpx.Client(timeout=45)
        self.gl, self.hl = (gl or "us").lower(), (hl or "en").lower()
        self.device = device if device in DEVICES else "desktop"

    def __repr__(self) -> str:
        return "<SerpApiAdapter>"

    # ------------------------------------------------------------------------------------------ transport
    def _get(self, path: str, params: dict, *, auth: bool = True):
        q = {k: v for k, v in params.items() if v not in (None, "")}
        if auth:
            q["api_key"] = self._key
        resp = request(self.client, "GET", f"{BASE}{path}", "SerpApi", params=q)
        body = None
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except ValueError:
                body = None
            msg = str((body or {}).get("error") or "")[:300] if isinstance(body, dict) else ""
            if resp.status_code == 401:
                raise AuthError("SerpApi rejected the API key (HTTP 401)" + (f": {msg}" if msg else ""), adapter=self.name,
                                details={"status": 401, "error_kind": "auth_failure"})
            if resp.status_code == 429:
                if OUT_OF_SEARCHES.search(msg):
                    raise QuotaExceeded(f"SerpApi: the account has no searches left ({msg})", adapter=self.name,
                                        details={"status": 429, "error_kind": "quota_exceeded"})
                raise RateLimited(f"SerpApi: hourly throughput limit reached (HTTP 429){': ' + msg if msg else ''}", adapter=self.name,
                                  details={"status": 429, "error_kind": "rate_limited"})
            if resp.status_code == 400:
                raise InvalidInput(f"SerpApi rejected the request (HTTP 400){': ' + msg if msg else ''}", adapter=self.name,
                                   details={"status": 400, "error_kind": "invalid_input"})
            classify(resp, "SerpApi")
        data = json_of(resp, "SerpApi")
        if not isinstance(data, (dict, list)):
            raise InvalidResponse("SerpApi answered in an unexpected format", adapter=self.name)
        return data

    def _search(self, params: dict) -> tuple[dict, bool]:
        """(data, empty). A JSON `error` saying Google returned nothing is an empty result, any other `error` is a failure."""
        data = self._get("/search.json", {**params, "output": "json"})
        if not isinstance(data, dict):
            raise InvalidResponse("SerpApi search answered in an unexpected format", adapter=self.name)
        err = data.get("error")
        if err:
            if NO_RESULTS.search(str(err)):
                return data, True
            if OUT_OF_SEARCHES.search(str(err)):
                raise QuotaExceeded(f"SerpApi: {str(err)[:200]}", adapter=self.name, details={"error_kind": "quota_exceeded"})
            raise InvalidResponse(f"SerpApi: {str(err)[:200]}", adapter=self.name, details={"error_kind": "provider_error"})
        return data, False

    @staticmethod
    def _meta(data: dict, engine: str, params: dict) -> dict:
        md = data.get("search_metadata") or {}
        return {"engine": engine, "params": {k: v for k, v in params.items() if k != "api_key"}, "search_id": md.get("id"),
                "provider_created_at": md.get("created_at"), "status": md.get("status"), "retrieved_at": clock.real_now().isoformat(),
                "provider": "serpapi"}

    # ------------------------------------------------------------------------------------------ account / locations (no search credit)
    def account(self) -> dict:
        data = self._get("/account.json", {})
        if not isinstance(data, dict):
            raise InvalidResponse("SerpApi account answered in an unexpected format", adapter=self.name)
        if data.get("error"):
            raise AuthError(f"SerpApi: {str(data['error'])[:200]}", adapter=self.name, details={"error_kind": "auth_failure"})
        # account_email, account_id and api_key are deliberately NOT kept
        return {k: data.get(k) for k in ACCOUNT_FIELDS if k in data}

    def locations(self, q: str, limit: int = 3) -> list[dict]:
        data = self._get("/locations.json", {"q": q[:120], "limit": max(1, min(limit, 10))}, auth=False)
        if not isinstance(data, list):
            return []
        return [{"name": x.get("name"), "canonical_name": x.get("canonical_name"), "country_code": x.get("country_code"),
                 "target_type": x.get("target_type"), "gps": x.get("gps")} for x in data if isinstance(x, dict) and x.get("canonical_name")]

    # ------------------------------------------------------------------------------------------ engines
    def google(self, q: str, *, location: str = "", gl: str | None = None, hl: str | None = None, device: str | None = None,
               start: int = 0, google_domain: str = "google.com") -> dict:
        params = {"engine": "google", "q": q[:400], "location": location or None, "gl": (gl or self.gl), "hl": (hl or self.hl),
                  "device": device if device in DEVICES else self.device, "google_domain": google_domain, "start": start or None}
        data, empty = self._search(params)
        organic = [{"position": r.get("position"), "title": (r.get("title") or "")[:300], "link": r.get("link"), "domain": host(r.get("link") or ""),
                    "displayed_link": r.get("displayed_link"), "snippet": (r.get("snippet") or "")[:500], "source": r.get("source")}
                   for r in data.get("organic_results") or [] if isinstance(r, dict) and r.get("link")]
        ads = [{"position": a.get("position"), "block_position": a.get("block_position"), "title": (a.get("title") or "")[:300],
                "link": a.get("link"), "domain": host(a.get("link") or a.get("displayed_link") or ""), "displayed_link": a.get("displayed_link"),
                "description": (a.get("description") or "")[:500], "source": a.get("source"),
                "sitelinks": [s.get("title") for s in (a.get("sitelinks") or []) if isinstance(s, dict) and s.get("title")][:6]
                if isinstance(a.get("sitelinks"), list) else []}
               for a in data.get("ads") or [] if isinstance(a, dict) and (a.get("link") or a.get("displayed_link"))]
        related = [r.get("query") for r in data.get("related_searches") or [] if isinstance(r, dict) and r.get("query")]
        questions = [{"question": r.get("question"), "link": r.get("link"), "title": r.get("title")}
                     for r in data.get("related_questions") or [] if isinstance(r, dict) and r.get("question")]
        lr = data.get("local_results")
        places = lr.get("places") if isinstance(lr, dict) else lr if isinstance(lr, list) else []
        local = [{"position": p.get("position"), "title": p.get("title"), "rating": p.get("rating"), "reviews": p.get("reviews"),
                  "address": p.get("address"), "type": p.get("type"), "place_id": p.get("place_id"),
                  "website": (p.get("links") or {}).get("website") if isinstance(p.get("links"), dict) else p.get("website")}
                 for p in places or [] if isinstance(p, dict) and p.get("title")]
        pag = data.get("serpapi_pagination") or {}
        return {**self._meta(data, "google", params), "query": q, "location": location, "empty": empty, "organic": organic, "ads": ads,
                "related_searches": related, "related_questions": questions, "local_results": local,
                "next_page": bool(pag.get("next")), "next_start": (start or 0) + 10 if pag.get("next") else None,
                "searched_location": (data.get("search_parameters") or {}).get("location_used") or location or None}

    def maps(self, q: str, *, ll: str, start: int = 0, gl: str | None = None, hl: str | None = None) -> dict:
        if not re.fullmatch(r"@-?\d+(\.\d+)?,-?\d+(\.\d+)?,\d+(\.\d+)?z", ll or ""):
            raise InvalidInput("Google Maps search needs a map position like @30.2672,-97.7431,14z", adapter=self.name,
                               details={"error_kind": "invalid_input"})
        params = {"engine": "google_maps", "q": q[:200], "ll": ll, "type": "search", "start": start or None, "gl": gl or self.gl, "hl": hl or self.hl}
        data, empty = self._search(params)
        places = [{"position": p.get("position"), "title": p.get("title"), "address": p.get("address"), "phone": p.get("phone"),
                   "website": p.get("website"), "rating": p.get("rating"), "reviews": p.get("reviews"), "type": p.get("type"),
                   "types": p.get("types") if isinstance(p.get("types"), list) else None, "gps": p.get("gps_coordinates"),
                   "place_id": p.get("place_id")}
                  for p in data.get("local_results") or [] if isinstance(p, dict) and p.get("title")]
        pag = data.get("serpapi_pagination") or {}
        return {**self._meta(data, "google_maps", params), "query": q, "ll": ll, "empty": empty, "places": places,
                "next_page": bool(pag.get("next")), "next_start": (start or 0) + 20 if pag.get("next") else None}

    def autocomplete(self, q: str, *, gl: str | None = None, hl: str | None = None) -> dict:
        params = {"engine": "google_autocomplete", "q": q[:200], "gl": gl or self.gl, "hl": hl or self.hl}
        data, empty = self._search(params)
        sugg = [s.get("value") for s in data.get("suggestions") or [] if isinstance(s, dict) and s.get("value")]
        return {**self._meta(data, "google_autocomplete", params), "query": q, "empty": empty, "suggestions": sugg}

    def ads_transparency(self, text: str, *, region: str = "") -> dict:
        """Ads an advertiser has run (Google Ads Transparency Center). `text` is the advertiser's domain or name."""
        params = {"engine": "google_ads_transparency_center", "text": text[:120], "region": region or None}
        data, empty = self._search(params)
        creatives = [{"advertiser": c.get("advertiser"), "advertiser_id": c.get("advertiser_id"), "ad_creative_id": c.get("ad_creative_id"),
                      "format": c.get("format"), "target_domain": c.get("target_domain"), "first_shown": c.get("first_shown"),
                      "last_shown": c.get("last_shown"), "total_days_shown": c.get("total_days_shown"), "details_link": c.get("details_link")}
                     for c in data.get("ad_creatives") or [] if isinstance(c, dict)]
        return {**self._meta(data, "google_ads_transparency_center", params), "query": text, "empty": empty, "creatives": creatives[:40]}
