"""DataForSEO API v3 (https://api.dataforseo.com/v3/): Google Ads keyword data and DataForSEO Labs competitor research.

Built from the official v3 documentation (docs.dataforseo.com/v3). Endpoints, all "Live" (one task per request, POST body = [task]):

  Google Ads Keywords Data — at most 12 requests per minute per account across these Live endpoints (throttled here):
    POST keywords_data/google_ads/search_volume/live          keywords[] (max 1000, each <= 80 chars and <= 10 words), location, language
    POST keywords_data/google_ads/keywords_for_keywords/live  keywords[] (max 20 seeds), location, language
    POST keywords_data/google_ads/keywords_for_site/live      target (domain or URL), target_type site | page, location, language
    Result items: keyword, location_code, language_code, search_partners, competition (LOW | MEDIUM | HIGH | null), competition_index
    (0-100 | null), search_volume, low_top_of_page_bid, high_top_of_page_bid, cpc (USD), monthly_searches[{year, month, search_volume}].
    keywords_for_keywords does NOT say which seed produced an idea: the seeds of the request are kept with every idea.

  DataForSEO Labs (location AND language required):
    POST dataforseo_labs/google/competitors_domain/live   target domain -> items[{domain, avg_position, intersections, metrics{organic,paid},
                                                          full_domain_metrics{organic, paid}}]  (metrics = on the keywords shared with target)
    POST dataforseo_labs/google/serp_competitors/live     keywords[] (max 200) -> items[{domain, avg_position, median_position, rating, etv,
                                                          keywords_count, visibility, relevant_serp_items, keywords_positions}]
    POST dataforseo_labs/google/domain_intersection/live  target1, target2, intersections=true -> items[{keyword_data{keyword, keyword_info},
                                                          first_domain_serp_element, second_domain_serp_element}]
    POST dataforseo_labs/google/keyword_overview/live     keywords[] (max 700) -> items[{keyword, keyword_info{search_volume, cpc,
                                                          competition (0-1), competition_level, low/high_top_of_page_bid, monthly_searches,
                                                          last_updated_time}, ...}]
    POST dataforseo_labs/google/ranked_keywords/live      target -> items[{keyword_data, ranked_serp_element{serp_item{type, rank_absolute, url}}}]

  Free (not charged): GET appendix/user_data (login, balance, rate limits) and GET keywords_data/google_ads/locations/$country.

Authentication is HTTP Basic with the saved API login and password. The password is never logged, returned or stored anywhere but the
encrypted integration record. Status codes come from the official appendix: 20000 ok; 40102 no results (an empty answer, not an error);
40100/40104 auth; 40200/40210 payment/funds; 40203 cost limit; 40202/40209 rate limits; 5xxxx server-side. Transient failures (rate limit,
5xxxx, 40103 "resubmit", timeouts, HTTP 429/5xx) are retried a bounded number of times; everything else is raised with its code.
Metrics a response does not contain are returned as None: nothing is estimated or filled in here.
"""
from __future__ import annotations

import time
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from app.adapters.base import (AccountNotReady, AdapterError, AdapterTimeout, AuthError, InsufficientFunds, InvalidInput, InvalidResponse,
                               NotFoundError, PermissionDenied, QuotaExceeded, RateLimited, ServiceUnavailable)
from app.adapters.research.http import request, throttle
from app.core import clock

BASE = "https://api.dataforseo.com/v3"
PROVIDER = "dataforseo"
GOOGLE_ADS_PER_MINUTE = 12                      # documented account limit for the Google Ads Live endpoints
GOOGLE_ADS_MIN_INTERVAL = 60.0 / GOOGLE_ADS_PER_MINUTE
MAX_SEARCH_VOLUME = 1000                        # keywords per search_volume request
MAX_SEEDS = 20                                  # seeds per keywords_for_keywords request
MAX_SERP_COMPETITOR_KEYWORDS = 200
MAX_OVERVIEW_KEYWORDS = 700
MAX_KEYWORD_CHARS, MAX_KEYWORD_WORDS = 80, 10

OK, NO_RESULTS = 20000, 40102
AUTH = {40100}                  # login or password wrong
NOT_VERIFIED = {40104}          # credentials right, but the account must be verified before the API can be used
FUNDS = {40200, 40210}          # payment required / insufficient funds
COST_LIMIT = {40203}            # the account's own cost limit was reached
DENIED = {40201, 40207}
RATE = {40202, 40209, 40205, 40206}
NOT_FOUND = {40400, 40401, 40402}
INVALID = {40000, 40006, 40501, 40502, 40503, 50100}
TRANSIENT = {40202, 40209, 40101, 40103, 50000, 50301, 50303, 50401}


def domain_of(target: str) -> str:
    """'https://www.example.com/x' -> 'example.com' (the Labs endpoints want a bare domain)."""
    t = (target or "").strip()
    h = (urlparse(t if "//" in t else f"https://{t}").hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def valid_keyword(k: str) -> bool:
    k = " ".join((k or "").split())
    return bool(k) and len(k) <= MAX_KEYWORD_CHARS and len(k.split()) <= MAX_KEYWORD_WORDS


def _error(code: int, message: str, status: int | None = None) -> AdapterError:
    details = {"dataforseo_status": code, **({"status": status} if status else {})}
    msg = f"DataForSEO: {message} (status {code})"
    if code in AUTH:
        return AuthError(msg, adapter=PROVIDER, details={**details, "error_kind": "auth_failure"})
    if code in NOT_VERIFIED:
        return AccountNotReady(f"DataForSEO: the account must be verified before the API can be used — complete verification at "
                               f"app.dataforseo.com (status {code}). The login and password were accepted.", adapter=PROVIDER,
                               details={**details, "error_kind": "account_verification_required", "blocking": True})
    if code in FUNDS:
        return InsufficientFunds(msg + " — add funds in the DataForSEO dashboard", adapter=PROVIDER, details={**details, "blocking": True})
    if code in COST_LIMIT:
        return QuotaExceeded(msg + " — the account's own cost limit was reached (app.dataforseo.com/api-settings)", adapter=PROVIDER,
                             details={**details, "blocking": True})
    if code in DENIED:
        return PermissionDenied(msg, adapter=PROVIDER, details=details)
    if code in RATE:
        return RateLimited(msg, adapter=PROVIDER, details=details)
    if code in NOT_FOUND:
        return NotFoundError(msg, adapter=PROVIDER, details=details)
    if code in INVALID or 40000 <= code < 50000:
        return InvalidInput(msg, adapter=PROVIDER, details=details)
    return ServiceUnavailable(msg, adapter=PROVIDER, details=details)


class DataForSEOAdapter:
    name = PROVIDER
    source = "LIVE"

    def __init__(self, login: str, password: str, client: httpx.Client | None = None, *, location_name: str = "United States",
                 language_code: str = "en", retries: int = 2, backoff: tuple[float, ...] = (3.0, 10.0), sleep: Callable[[float], None] = time.sleep,
                 min_interval: float = GOOGLE_ADS_MIN_INTERVAL):
        if not login or not password:
            raise AuthError("DataForSEO API login and password are required", adapter=PROVIDER, details={"error_kind": "auth_failure"})
        self._auth = (login, password)
        self.client = client or httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0))
        self.location_name = location_name or "United States"
        self.language_code = (language_code or "en").lower()
        self.retries, self.backoff, self.sleep, self.min_interval = max(0, retries), backoff, sleep, min_interval

    def __repr__(self) -> str:  # never shows the credentials
        return "<DataForSEOAdapter>"

    # ------------------------------------------------------------------------------------------ transport
    def _send(self, method: str, path: str, body: list | None = None) -> dict:
        resp = request(self.client, method, f"{BASE}/{path}", "DataForSEO", auth=self._auth, **({"json": body} if body is not None else {}))
        if resp.status_code == 401:
            try:  # DataForSEO can answer a not-yet-verified account with HTTP 401 and status 40104: that is not a wrong password
                if int((resp.json() or {}).get("status_code") or 0) in NOT_VERIFIED:
                    raise _error(int(resp.json()["status_code"]), str(resp.json().get("status_message") or ""))
            except ValueError:
                pass
            raise AuthError("DataForSEO rejected the API login or password (HTTP 401)", adapter=PROVIDER,
                            details={"status": 401, "error_kind": "auth_failure"})
        if resp.status_code == 402:
            raise InsufficientFunds("DataForSEO: payment required, the account balance is too low (HTTP 402)", adapter=PROVIDER,
                                    details={"status": 402, "blocking": True})
        if resp.status_code == 429:
            raise RateLimited("DataForSEO: too many requests (HTTP 429)", adapter=PROVIDER, details={"status": 429})
        if resp.status_code >= 500:
            raise ServiceUnavailable(f"DataForSEO: server error (HTTP {resp.status_code})", adapter=PROVIDER, details={"status": resp.status_code})
        try:
            data = resp.json()
        except ValueError:
            raise InvalidResponse(f"DataForSEO returned a non-JSON response (HTTP {resp.status_code})", adapter=PROVIDER,
                                  details={"status": resp.status_code}) from None
        if not isinstance(data, dict):
            raise InvalidResponse("DataForSEO returned an unexpected response shape", adapter=PROVIDER)
        code = int(data.get("status_code") or 0)
        if code != OK:
            raise _error(code, str(data.get("status_message") or "request failed"), resp.status_code)
        return data

    def _with_retries(self, fn: Callable[[], Any]) -> Any:
        for attempt in range(self.retries + 1):
            try:
                return fn()
            except (RateLimited, ServiceUnavailable, AdapterTimeout) as exc:
                code = exc.details.get("dataforseo_status")
                transient = code is None or code in TRANSIENT
                if not transient or attempt >= self.retries:
                    raise
                wait = self.backoff[min(attempt, len(self.backoff) - 1)]
                if isinstance(exc, RateLimited):
                    wait = max(wait, self.min_interval * 2)
                self.sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    def _task(self, path: str, task: dict, *, google_ads: bool) -> dict:
        """POST one Live task; returns {"result": [...], "cost": float, "task_id": str, "retrieved_at": iso}. A 40102 task is an empty result."""
        def go():
            if google_ads:
                throttle("dataforseo_google_ads", self.min_interval)
            data = self._send("POST", path, [task])
            tasks = data.get("tasks")
            if not isinstance(tasks, list) or not tasks:
                raise InvalidResponse("DataForSEO returned no task in the response", adapter=PROVIDER)
            t = tasks[0]
            tcode = int(t.get("status_code") or 0)
            if tcode == NO_RESULTS:
                return {"result": [], "cost": float(t.get("cost") or data.get("cost") or 0), "task_id": t.get("id"), "no_results": True}
            if tcode != OK:
                raise _error(tcode, str(t.get("status_message") or "task failed"))
            result = t.get("result")
            if result is not None and not isinstance(result, list):
                raise InvalidResponse("DataForSEO task result is not a list", adapter=PROVIDER)
            return {"result": result or [], "cost": float(t.get("cost") or 0), "task_id": t.get("id")}
        out = self._with_retries(go)
        out["retrieved_at"] = clock.real_now().isoformat()
        return out

    def _where(self, location_name: str | None = None, location_code: int | None = None, language_code: str | None = None) -> dict:
        loc = {"location_code": int(location_code)} if location_code else {"location_name": location_name or self.location_name}
        return {**loc, "language_code": (language_code or self.language_code).lower()}

    # ------------------------------------------------------------------------------------------ free account endpoints
    def user_data(self) -> dict:
        """GET appendix/user_data (not charged): proves the credentials and returns the balance."""
        data = self._with_retries(lambda: self._send("GET", "appendix/user_data"))
        t = (data.get("tasks") or [{}])[0]
        if int(t.get("status_code") or 0) != OK:
            raise _error(int(t.get("status_code") or 0), str(t.get("status_message") or "user_data failed"))
        r = (t.get("result") or [{}])[0] or {}
        money = r.get("money") or {}
        limits = ((r.get("rates") or {}).get("limits") or {}).get("minute") or {}
        return {"login": r.get("login"), "balance": money.get("balance"), "total_deposited": money.get("total"),
                "minute_limits": {k: v for k, v in limits.items() if isinstance(v, (int, float))} or None}

    def research_access(self) -> dict:
        """Proves the account can use the research API, not just sign in: the Google Ads locations list (not charged) answers only for an
        account allowed to use the Keywords Data API. An unverified account is refused here (40104)."""
        locs = self.locations("us")
        return {"ok": True, "locations": len(locs)}

    def locations(self, country_iso: str) -> list[dict]:
        """GET keywords_data/google_ads/locations/$country (not charged)."""
        def go():
            data = self._send("GET", f"keywords_data/google_ads/locations/{country_iso.lower()}")
            t = (data.get("tasks") or [{}])[0]
            if int(t.get("status_code") or 0) != OK:
                raise _error(int(t.get("status_code") or 0), str(t.get("status_message") or "locations failed"))
            return [{"location_code": x.get("location_code"), "location_name": x.get("location_name"), "location_type": x.get("location_type"),
                     "country_iso_code": x.get("country_iso_code")} for x in (t.get("result") or []) if isinstance(x, dict)]
        return self._with_retries(go)

    # ------------------------------------------------------------------------------------------ Google Ads keyword data
    @staticmethod
    def _ads_row(x: dict, *, retrieved_at: str, endpoint: str) -> dict:
        monthly = [{"year": m.get("year"), "month": m.get("month"), "search_volume": m.get("search_volume")}
                   for m in (x.get("monthly_searches") or []) if isinstance(m, dict)]
        return {"keyword": x.get("keyword"), "spell": x.get("spell"), "location_code": x.get("location_code"), "language_code": x.get("language_code"),
                "search_partners": x.get("search_partners"), "search_volume": x.get("search_volume"), "monthly_searches": monthly or None,
                "cpc": x.get("cpc"), "competition_level": x.get("competition"), "competition_index": x.get("competition_index"),
                "low_top_of_page_bid": x.get("low_top_of_page_bid"), "high_top_of_page_bid": x.get("high_top_of_page_bid"),
                "source": f"dataforseo:{endpoint}", "retrieved_at": retrieved_at}

    def search_volume(self, keywords: list[str], *, location_name: str | None = None, location_code: int | None = None,
                      language_code: str | None = None) -> dict:
        kws = list(dict.fromkeys(" ".join(k.lower().split()) for k in keywords if valid_keyword(k)))
        if not kws:
            raise InvalidInput("No valid keywords (each must be 1-10 words and at most 80 characters)", adapter=PROVIDER)
        if len(kws) > MAX_SEARCH_VOLUME:
            raise InvalidInput(f"search_volume accepts at most {MAX_SEARCH_VOLUME} keywords per request", adapter=PROVIDER)
        where = self._where(location_name, location_code, language_code)
        out = self._task("keywords_data/google_ads/search_volume/live", {"keywords": kws, **where}, google_ads=True)
        return {**out, "where": where, "items": [self._ads_row(x, retrieved_at=out["retrieved_at"], endpoint="search_volume")
                                                 for x in out["result"] if isinstance(x, dict)]}

    def keywords_for_keywords(self, seeds: list[str], *, location_name: str | None = None, location_code: int | None = None,
                              language_code: str | None = None) -> dict:
        s = list(dict.fromkeys(" ".join(k.lower().split()) for k in seeds if valid_keyword(k)))
        if not s:
            raise InvalidInput("No valid seed keywords", adapter=PROVIDER)
        if len(s) > MAX_SEEDS:
            raise InvalidInput(f"keywords_for_keywords accepts at most {MAX_SEEDS} seed keywords per request", adapter=PROVIDER)
        where = self._where(location_name, location_code, language_code)
        out = self._task("keywords_data/google_ads/keywords_for_keywords/live", {"keywords": s, **where}, google_ads=True)
        items = [{**self._ads_row(x, retrieved_at=out["retrieved_at"], endpoint="keywords_for_keywords"), "seeds": s}
                 for x in out["result"] if isinstance(x, dict)]
        return {**out, "where": where, "seeds": s, "items": items}

    def keywords_for_site(self, target: str, *, target_type: str = "site", location_name: str | None = None, location_code: int | None = None,
                          language_code: str | None = None) -> dict:
        if target_type not in ("site", "page"):
            raise InvalidInput("target_type must be site or page", adapter=PROVIDER)
        t = domain_of(target) if target_type == "site" else target.strip()
        if not t:
            raise InvalidInput("A target website is required", adapter=PROVIDER)
        where = self._where(location_name, location_code, language_code)
        out = self._task("keywords_data/google_ads/keywords_for_site/live", {"target": t, "target_type": target_type, **where}, google_ads=True)
        items = [{**self._ads_row(x, retrieved_at=out["retrieved_at"], endpoint="keywords_for_site"), "source_domain": t}
                 for x in out["result"] if isinstance(x, dict)]
        return {**out, "where": where, "target": t, "items": items}

    # ------------------------------------------------------------------------------------------ DataForSEO Labs
    @staticmethod
    def _labs_items(out: dict) -> tuple[list[dict], dict]:
        r = (out["result"] or [{}])[0] or {}
        items = r.get("items") or []
        if not isinstance(items, list):
            raise InvalidResponse("DataForSEO Labs items is not a list", adapter=PROVIDER)
        return [i for i in items if isinstance(i, dict)], {"total_count": r.get("total_count"), "items_count": r.get("items_count"),
                                                           "location_code": r.get("location_code"), "language_code": r.get("language_code")}

    @staticmethod
    def _keyword_info(ki: dict | None) -> dict:
        ki = ki or {}
        monthly = [{"year": m.get("year"), "month": m.get("month"), "search_volume": m.get("search_volume")}
                   for m in (ki.get("monthly_searches") or []) if isinstance(m, dict)]
        return {"search_volume": ki.get("search_volume"), "cpc": ki.get("cpc"), "competition": ki.get("competition"),
                "competition_level": ki.get("competition_level"), "low_top_of_page_bid": ki.get("low_top_of_page_bid"),
                "high_top_of_page_bid": ki.get("high_top_of_page_bid"), "monthly_searches": monthly or None,
                "data_updated_at": ki.get("last_updated_time")}

    @staticmethod
    def _vis(m: dict | None) -> dict | None:
        if not isinstance(m, dict):
            return None
        return {"count": m.get("count"), "etv": m.get("etv"), "pos_1": m.get("pos_1"), "pos_2_3": m.get("pos_2_3"), "pos_4_10": m.get("pos_4_10"),
                "estimated_paid_traffic_cost": m.get("estimated_paid_traffic_cost")}

    def competitors_domain(self, target: str, *, location_name: str | None = None, location_code: int | None = None,
                           language_code: str | None = None, limit: int = 20) -> dict:
        t = domain_of(target)
        if not t:
            raise InvalidInput("A target domain is required", adapter=PROVIDER)
        where = self._where(location_name, location_code, language_code)
        out = self._task("dataforseo_labs/google/competitors_domain/live",
                         {"target": t, **where, "limit": max(1, min(limit, 1000)), "exclude_top_domains": True}, google_ads=False)
        items, meta = self._labs_items(out)
        rows = []
        for i in items:
            m, full = i.get("metrics") or {}, i.get("full_domain_metrics") or {}
            rows.append({"domain": i.get("domain"), "avg_position": i.get("avg_position"), "shared_keywords": i.get("intersections"),
                         "shared_organic": self._vis(m.get("organic")), "shared_paid": self._vis(m.get("paid")),
                         "organic": self._vis(full.get("organic")), "paid": self._vis(full.get("paid"))})
        return {**out, "where": where, "target": t, "meta": meta, "items": rows}

    def serp_competitors(self, keywords: list[str], *, location_name: str | None = None, location_code: int | None = None,
                         language_code: str | None = None, limit: int = 20) -> dict:
        kws = list(dict.fromkeys(" ".join(k.lower().split()) for k in keywords if valid_keyword(k)))[:MAX_SERP_COMPETITOR_KEYWORDS]
        if not kws:
            raise InvalidInput("No valid keywords", adapter=PROVIDER)
        where = self._where(location_name, location_code, language_code)
        out = self._task("dataforseo_labs/google/serp_competitors/live", {"keywords": kws, **where, "limit": max(1, min(limit, 1000))},
                         google_ads=False)
        items, meta = self._labs_items(out)
        rows = [{"domain": i.get("domain"), "avg_position": i.get("avg_position"), "median_position": i.get("median_position"),
                 "rating": i.get("rating"), "etv": i.get("etv"), "keywords_count": i.get("keywords_count"), "visibility": i.get("visibility"),
                 "relevant_serp_items": i.get("relevant_serp_items"),
                 "keywords_positions": i.get("keywords_positions") if isinstance(i.get("keywords_positions"), dict) else None} for i in items]
        return {**out, "where": where, "keywords": kws, "meta": meta, "items": rows}

    @staticmethod
    def _serp_el(e: dict | None) -> dict | None:
        if not isinstance(e, dict):
            return None
        return {"type": e.get("type"), "rank_group": e.get("rank_group"), "rank_absolute": e.get("rank_absolute"), "url": e.get("url"),
                "domain": e.get("domain")}

    def domain_intersection(self, target1: str, target2: str, *, location_name: str | None = None, location_code: int | None = None,
                            language_code: str | None = None, limit: int = 100) -> dict:
        a, b = domain_of(target1), domain_of(target2)
        if not a or not b or a == b:
            raise InvalidInput("Two different domains are required", adapter=PROVIDER)
        where = self._where(location_name, location_code, language_code)
        out = self._task("dataforseo_labs/google/domain_intersection/live",
                         {"target1": a, "target2": b, **where, "intersections": True, "limit": max(1, min(limit, 1000))}, google_ads=False)
        items, meta = self._labs_items(out)
        rows = []
        for i in items:
            kd = i.get("keyword_data") or {}
            rows.append({"keyword": kd.get("keyword"), **self._keyword_info(kd.get("keyword_info")),
                         "target1_serp": self._serp_el(i.get("first_domain_serp_element")),
                         "target2_serp": self._serp_el(i.get("second_domain_serp_element"))})
        return {**out, "where": where, "target1": a, "target2": b, "meta": meta, "items": rows}

    def keyword_overview(self, keywords: list[str], *, location_name: str | None = None, location_code: int | None = None,
                         language_code: str | None = None) -> dict:
        kws = list(dict.fromkeys(" ".join(k.lower().split()) for k in keywords if valid_keyword(k)))
        if not kws:
            raise InvalidInput("No valid keywords", adapter=PROVIDER)
        if len(kws) > MAX_OVERVIEW_KEYWORDS:
            raise InvalidInput(f"keyword_overview accepts at most {MAX_OVERVIEW_KEYWORDS} keywords per request", adapter=PROVIDER)
        where = self._where(location_name, location_code, language_code)
        out = self._task("dataforseo_labs/google/keyword_overview/live", {"keywords": kws, **where}, google_ads=False)
        items, meta = self._labs_items(out)
        rows = [{"keyword": i.get("keyword"), **self._keyword_info(i.get("keyword_info")), "source": "dataforseo:keyword_overview",
                 "retrieved_at": out["retrieved_at"]} for i in items]
        return {**out, "where": where, "meta": meta, "items": rows}

    def ranked_keywords(self, target: str, *, location_name: str | None = None, location_code: int | None = None,
                        language_code: str | None = None, limit: int = 100) -> dict:
        t = domain_of(target)
        if not t:
            raise InvalidInput("A target domain is required", adapter=PROVIDER)
        where = self._where(location_name, location_code, language_code)
        out = self._task("dataforseo_labs/google/ranked_keywords/live", {"target": t, **where, "limit": max(1, min(limit, 1000))}, google_ads=False)
        items, meta = self._labs_items(out)
        rows = []
        for i in items:
            kd = i.get("keyword_data") or {}
            se = ((i.get("ranked_serp_element") or {}).get("serp_item")) or {}
            rows.append({"keyword": kd.get("keyword"), **self._keyword_info(kd.get("keyword_info")),
                         "serp": {"type": se.get("type"), "rank_absolute": se.get("rank_absolute"), "url": se.get("url")} if se else None})
        return {**out, "where": where, "target": t, "meta": meta, "items": rows}
