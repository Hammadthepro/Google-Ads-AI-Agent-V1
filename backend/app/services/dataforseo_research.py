"""DataForSEO in the research workflow: keyword ideas and metrics, and competitor discovery verified by real search overlap.

It complements, never replaces, the other sources: Keyword Planner stays the first source of Google keyword metrics; SerpApi stays the
source of what Google showed for a query; suggestions and the websites stay the free discovery sources. DataForSEO adds:

  * keyword ideas: Keywords For Keywords (seeds = the client's services, max 20 per request) and Keywords For Site (the client's domain,
    and verified competitors' domains);
  * keyword metrics (Google Ads data via DataForSEO: search volume, monthly searches, CPC, competition level and index, top-of-page bids)
    for every candidate that has none yet, in batches of up to 1,000, for the target location and language;
  * competitor discovery and verification: Competitors Domain (domains sharing search visibility with the client), SERP Competitors
    (domains ranking for the target keywords), Domain Intersection (keywords the client shares with a candidate) and Ranked Keywords
    (a verified competitor's own keywords).

Every metric keeps its source endpoint and retrieval time. Each keyword's metrics are cached by (endpoint, keyword, location, language), so
the same keyword is not bought twice while the cache is fresh. A failing step is recorded and reported; whatever other providers already
returned is kept. Nothing is estimated here: a value DataForSEO did not return stays None.
"""
from __future__ import annotations

from datetime import timedelta

from app.adapters import registry
from app.adapters.base import AdapterError
from app.adapters.research.dataforseo import MAX_SEARCH_VOLUME, MAX_SEEDS, domain_of, valid_keyword
from app.core.errors import IntegrationNotConfigured
from app.core.util import normalize_term
from app.services import provider_calls, research_events

PROVIDER = "dataforseo"
METRIC_TTL = timedelta(days=30)        # Google Ads keyword metrics are monthly figures
IDEA_TTL = timedelta(days=14)
LABS_TTL = timedelta(days=14)
LOCATIONS_TTL = timedelta(days=30)
GENERIC_DOMAINS = {"wikipedia.org", "youtube.com", "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "reddit.com",
                   "quora.com", "pinterest.com", "amazon.com", "google.com", "yelp.com", "yellowpages.com", "bbb.org", "angi.com", "thumbtack.com",
                   "indeed.com", "glassdoor.com", "tiktok.com", "medium.com", "nextdoor.com", "mapquest.com", "apple.com"}
# a candidate is a VERIFIED competitor only on real overlap evidence, never because it appeared in one generic result
MIN_SHARED_KEYWORDS = 5          # Competitors Domain: keywords both domains rank for
MIN_SERP_KEYWORDS = 2            # SERP Competitors: the domain ranks for at least this many of the target keywords
MIN_INTERSECTION = 3             # Domain Intersection: shared ranking keywords found

US_STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado", "CT": "Connecticut",
             "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
             "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
             "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
             "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
             "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
             "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
             "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming"}


def adapter_or_reason():
    """(adapter, None) or (None, reason). Missing credentials are a clear integration error, never a silent fallback."""
    try:
        return registry.get(PROVIDER), None
    except IntegrationNotConfigured as exc:
        return None, exc.message


def _settings() -> dict:
    from app.services import integrations_service
    return integrations_service.resolve(PROVIDER) or {}


# ------------------------------------------------------------------------------------------ location
def resolve_location(ad, ctx: dict, stage: str = "keywords") -> dict:
    """The Google Ads location for keyword data: the business's city when DataForSEO lists it, else the configured default location.
    Labs endpoints use the configured country-level default (they cover countries, not cities)."""
    s = _settings()
    default = s.get("location_name") or ad.location_name
    country_iso = (s.get("country_iso") or "us").lower()
    language = (s.get("language_code") or ad.language_code).lower()
    out = {"keywords": {"location_name": default, "language_code": language, "basis": "configured default location"},
           "labs": {"location_name": s.get("labs_location_name") or default, "language_code": language, "basis": "configured default location"}}
    areas = [*(ctx.get("service_areas") or [])]
    city = (areas[0].get("city") if areas and isinstance(areas[0], dict) else None) or ctx.get("city")
    state = (areas[0].get("state") if areas and isinstance(areas[0], dict) else None) or ((ctx.get("states") or [None])[0])
    if not city:
        return out
    state_name = US_STATES.get((state or "").upper(), state or "") if country_iso == "us" else (state or "")
    try:
        locs = provider_calls.call(PROVIDER, "locations", {"country": country_iso}, lambda: ad.locations(country_iso), ttl=LOCATIONS_TTL)
    except AdapterError as exc:
        research_events.emit("provider_failed", f"DataForSEO location list unavailable ({exc.message}); using {default}", stage=stage,
                             level="warn", provider=PROVIDER, error_kind=exc.kind)
        return out
    want = normalize_term(city)
    hits = [x for x in locs if x.get("location_type") == "City" and normalize_term((x.get("location_name") or "").split(",")[0]) == want
            and (not state_name or normalize_term(state_name) in normalize_term(x.get("location_name") or ""))]
    if len(hits) == 1:
        out["keywords"] = {"location_code": hits[0]["location_code"], "location_name": hits[0]["location_name"], "language_code": language,
                           "basis": "business city matched in DataForSEO's Google Ads locations"}
    return out


def _where_args(where: dict) -> dict:
    return {k: where[k] for k in ("location_code", "location_name", "language_code") if where.get(k) is not None} if not where.get("location_code") \
        else {"location_code": where["location_code"], "language_code": where["language_code"]}


def _where_key(where: dict) -> dict:
    return {"location": where.get("location_code") or where.get("location_name"), "language": where.get("language_code")}


# ------------------------------------------------------------------------------------------ keyword metrics
def keyword_metrics(ad, keywords: list[str], where: dict, *, stage: str = "keywords") -> dict:
    """Search volume and bids for these keywords (Google Ads via DataForSEO). Each keyword's answer is cached on its own; only keywords
    without a fresh cached answer are sent, in batches of up to 1,000. Returns {"rows": {normalized keyword: row}, "sent", "cached",
    "cost_usd", "errors"}; a row's `cached` says whether it came from the cache."""
    kws = list(dict.fromkeys(" ".join(k.lower().split()) for k in keywords if valid_keyword(k)))
    wk = _where_key(where)
    reqs = {k: {"keyword": k, **wk} for k in kws}
    hits = provider_calls.cached_items(PROVIDER, "search_volume", list(reqs.values()))
    rows: dict[str, dict] = {}
    for k, r in reqs.items():
        h = hits.get(provider_calls.cache_key({"op": "search_volume", **r}))
        if h is not None:
            rows[normalize_term(k)] = {**(h["value"] or {}), "cached": True, "cached_at": h["cached_at"]}
    todo = [k for k in kws if normalize_term(k) not in rows]
    out = {"rows": rows, "sent": 0, "cached": len(rows), "cost_usd": 0.0, "errors": [], "where": where}
    if rows:
        provider_calls.log_cached(PROVIDER, "search_volume")
    for i in range(0, len(todo), MAX_SEARCH_VOLUME):
        batch = todo[i:i + MAX_SEARCH_VOLUME]
        try:
            res = ad.search_volume(batch, **_where_args(where))
        except AdapterError as exc:
            provider_calls.log_failure(PROVIDER, "search_volume", exc)
            out["errors"].append({"step": "search_volume", "kind": exc.kind, "message": exc.message})
            research_events.emit("provider_failed", f"DataForSEO search volume failed: {exc.message}. Metrics from other sources are kept.",
                                 stage=stage, level="warn", provider=PROVIDER, error_kind=exc.kind)
            break
        got = {normalize_term(x["keyword"] or ""): x for x in res["items"] if x.get("keyword")}
        store = []
        for k in batch:
            row = got.get(normalize_term(k)) or {"keyword": k, "search_volume": None, "cpc": None, "competition_level": None,
                                                  "competition_index": None, "low_top_of_page_bid": None, "high_top_of_page_bid": None,
                                                  "monthly_searches": None, "source": "dataforseo:search_volume", "retrieved_at": res["retrieved_at"],
                                                  "no_data": True}
            row = {**row, "location": where.get("location_name") or where.get("location_code"), "language": where.get("language_code")}
            store.append((reqs[k], row))
            rows[normalize_term(k)] = {**row, "cached": False, "cached_at": None}
        provider_calls.store_items(PROVIDER, "search_volume", store, ttl=METRIC_TTL, cost=res["cost"], sent=1)
        out["sent"] += 1
        out["cost_usd"] += res["cost"]
        research_events.emit("keyword_discovery", f"DataForSEO search volume for {len(batch)} keyword(s) "
                             f"({sum(1 for k in batch if (got.get(normalize_term(k)) or {}).get('search_volume') is not None)} with data)",
                             stage=stage, provider=PROVIDER, count=len(batch), seed="search_volume", examples=batch[:5])
    return out


# ------------------------------------------------------------------------------------------ keyword ideas
def keyword_ideas(ad, seeds: list[str], site: str | None, where: dict, *, stage: str = "keywords", max_seed_requests: int = 2) -> dict:
    """Keywords For Keywords (seeds, 20 per request) and Keywords For Site (the client's domain). Each idea keeps its origin:
    the request's seeds (the API does not say which seed produced an idea) or the source domain."""
    out = {"ideas": [], "errors": [], "sent": 0, "cost_usd": 0.0}
    clean = list(dict.fromkeys(" ".join(s.lower().split()) for s in seeds if valid_keyword(s)))
    for i in range(0, min(len(clean), MAX_SEEDS * max_seed_requests), MAX_SEEDS):
        batch = clean[i:i + MAX_SEEDS]
        try:
            res, meta = provider_calls.call(PROVIDER, "keywords_for_keywords", {"seeds": batch, **_where_key(where)},
                                            lambda b=batch: ad.keywords_for_keywords(b, **_where_args(where)), ttl=IDEA_TTL,
                                            cost=lambda v: v.get("cost") or 0, meta=True)
        except AdapterError as exc:
            out["errors"].append({"step": "keywords_for_keywords", "kind": exc.kind, "message": exc.message})
            research_events.emit("provider_failed", f"DataForSEO Keywords For Keywords failed: {exc.message}", stage=stage, level="warn",
                                 provider=PROVIDER, error_kind=exc.kind)
            break
        out["sent"] += 0 if meta["cached"] else 1
        out["cost_usd"] += 0 if meta["cached"] else res.get("cost") or 0
        for x in res["items"]:
            out["ideas"].append({**x, "origin": {"endpoint": "keywords_for_keywords", "seeds": batch}, "cached": meta["cached"]})
        research_events.emit("keyword_discovery", f"{len(res['items'])} DataForSEO keyword idea(s) from {len(batch)} seed(s)"
                             + (" (from cache)" if meta["cached"] else ""), stage=stage, provider=PROVIDER, count=len(res["items"]),
                             seed=", ".join(batch[:3]), examples=[x["keyword"] for x in res["items"][:5]])
    if site and domain_of(site):
        d = domain_of(site)
        try:
            res, meta = provider_calls.call(PROVIDER, "keywords_for_site", {"target": d, **_where_key(where)},
                                            lambda: ad.keywords_for_site(d, target_type="site", **_where_args(where)), ttl=IDEA_TTL,
                                            cost=lambda v: v.get("cost") or 0, meta=True)
            out["sent"] += 0 if meta["cached"] else 1
            out["cost_usd"] += 0 if meta["cached"] else res.get("cost") or 0
            for x in res["items"]:
                out["ideas"].append({**x, "origin": {"endpoint": "keywords_for_site", "source_domain": d}, "cached": meta["cached"]})
            research_events.emit("keyword_discovery", f"{len(res['items'])} DataForSEO keyword idea(s) for {d}" + (" (from cache)" if meta["cached"] else ""),
                                 stage=stage, provider=PROVIDER, count=len(res["items"]), seed=d, examples=[x["keyword"] for x in res["items"][:5]])
        except AdapterError as exc:
            out["errors"].append({"step": "keywords_for_site", "kind": exc.kind, "message": exc.message})
            research_events.emit("provider_failed", f"DataForSEO Keywords For Site failed for {d}: {exc.message}", stage=stage, level="warn",
                                 provider=PROVIDER, error_kind=exc.kind)
    return out


# ------------------------------------------------------------------------------------------ competitors
def _labs(ad, op: str, request: dict, fn, errors: list, stage: str):
    try:
        res, meta = provider_calls.call(PROVIDER, op, request, fn, ttl=LABS_TTL, cost=lambda v: v.get("cost") or 0, meta=True)
        return res, meta
    except AdapterError as exc:
        errors.append({"step": op, "kind": exc.kind, "message": exc.message})
        research_events.emit("provider_failed", f"DataForSEO {op.replace('_', ' ')} failed: {exc.message}. Other competitor evidence is kept.",
                             stage=stage, level="warn", provider=PROVIDER, error_kind=exc.kind)
        return None, None


def competitor_research(ad, ctx: dict, target_keywords: list[str], candidates: list[dict], where: dict, *, stage: str = "competitors",
                        max_intersections: int = 3, max_ranked: int = 2) -> dict:
    """Discover competitor domains and verify them with search-overlap evidence.

    candidates: domains already seen by other research (client profile, website, SerpApi), each {"domain", "name", "source"}.
    Verification needs at least one of: >= MIN_SHARED_KEYWORDS shared ranking keywords (Competitors Domain), ranking for
    >= MIN_SERP_KEYWORDS of the target keywords (SERP Competitors), or >= MIN_INTERSECTION shared keywords (Domain Intersection).
    A domain seen only once in a generic result stays an unverified candidate."""
    target = domain_of(ctx.get("website_url") or "")
    errors: list = []
    found: dict[str, dict] = {}
    loc = where.get("location_name") or where.get("location_code")

    def rec(domain: str, source: str, name: str = "") -> dict:
        d = domain_of(domain)
        r = found.setdefault(d, {"domain": d, "name": name or d, "sources": [], "target_domain": target, "shared_keywords": None,
                                 "serp_keywords": None, "intersection_keywords": [], "organic": None, "paid": None, "shared_organic": None,
                                 "shared_paid": None, "serp_visibility": None, "relevant_keywords": [], "location": loc,
                                 "language": where.get("language_code"), "evidence": [], "last_updated": None})
        if source not in r["sources"]:
            r["sources"].append(source)
        if name and r["name"] == d:
            r["name"] = name
        return r
    for c in candidates:
        if c.get("domain") and domain_of(c["domain"]) not in GENERIC_DOMAINS and domain_of(c["domain"]) != target:
            rec(c["domain"], c.get("source") or "candidate", c.get("name") or "")
    # B. domains with overlapping search visibility
    if target:
        res, meta = _labs(ad, "competitors_domain", {"target": target, **_where_key(where)},
                          lambda: ad.competitors_domain(target, **_where_args(where)), errors, stage)
        for x in (res or {}).get("items", []):
            d = domain_of(x.get("domain") or "")
            if not d or d == target or d in GENERIC_DOMAINS:
                continue
            r = rec(d, "dataforseo:competitors_domain")
            r.update(shared_keywords=x.get("shared_keywords"), organic=x.get("organic"), paid=x.get("paid"), shared_organic=x.get("shared_organic"),
                     shared_paid=x.get("shared_paid"), last_updated=res["retrieved_at"])
            if (x.get("shared_keywords") or 0) >= MIN_SHARED_KEYWORDS:
                r["evidence"].append(f"Ranks for {x['shared_keywords']} of the same keywords as {target} (DataForSEO Competitors Domain)")
    # C. domains ranking for the target keywords
    kws = [k for k in dict.fromkeys(target_keywords) if valid_keyword(k)][:50]
    if kws:
        res, meta = _labs(ad, "serp_competitors", {"keywords": kws, **_where_key(where)},
                          lambda: ad.serp_competitors(kws, **_where_args(where)), errors, stage)
        for x in (res or {}).get("items", []):
            d = domain_of(x.get("domain") or "")
            if not d or d == target or d in GENERIC_DOMAINS:
                continue
            r = rec(d, "dataforseo:serp_competitors")
            r.update(serp_keywords=x.get("keywords_count"), serp_visibility={"avg_position": x.get("avg_position"), "median_position": x.get("median_position"),
                     "visibility": x.get("visibility"), "etv": x.get("etv"), "rating": x.get("rating")}, last_updated=res["retrieved_at"])
            r["relevant_keywords"] = sorted(set(r["relevant_keywords"]) | set((x.get("keywords_positions") or {}).keys()))[:30]
            if (x.get("keywords_count") or 0) >= MIN_SERP_KEYWORDS:
                r["evidence"].append(f"Ranks for {x['keywords_count']} of {len(kws)} target keyword(s) (DataForSEO SERP Competitors)")
    # D. shared keywords with the strongest candidates (client-provided first, then by overlap)
    order = sorted(found.values(), key=lambda r: (not any(s.startswith("client") for s in r["sources"]),
                                                  -((r["shared_keywords"] or 0) + 10 * (r["serp_keywords"] or 0))))
    if target:
        for r in order[:max_intersections]:
            res, meta = _labs(ad, "domain_intersection", {"target1": target, "target2": r["domain"], **_where_key(where)},
                              lambda d=r["domain"]: ad.domain_intersection(target, d, **_where_args(where)), errors, stage)
            if res is None:
                continue
            r["intersection_keywords"] = [{"keyword": x["keyword"], "search_volume": x.get("search_volume"), "cpc": x.get("cpc"),
                                           "competition_level": x.get("competition_level"),
                                           "client_rank": (x.get("target1_serp") or {}).get("rank_absolute"),
                                           "competitor_rank": (x.get("target2_serp") or {}).get("rank_absolute")}
                                          for x in res["items"] if x.get("keyword")][:50]
            r["last_updated"] = res["retrieved_at"]
            if len(r["intersection_keywords"]) >= MIN_INTERSECTION:
                r["evidence"].append(f"Shares {len(r['intersection_keywords'])} ranking keyword(s) with {target} (DataForSEO Domain Intersection)")
    for r in found.values():
        r["verified"] = bool(r["evidence"])
        r["status"] = "verified" if r["verified"] else "candidate"
    # E. keyword opportunities from the verified competitors' own rankings
    verified = [r for r in sorted(found.values(), key=lambda r: -(r["shared_keywords"] or 0)) if r["verified"]]
    for r in verified[:max_ranked]:
        res, meta = _labs(ad, "ranked_keywords", {"target": r["domain"], **_where_key(where)},
                          lambda d=r["domain"]: ad.ranked_keywords(d, **_where_args(where)), errors, stage)
        if res is None:
            continue
        r["ranked_keywords"] = [{"keyword": x["keyword"], "search_volume": x.get("search_volume"), "cpc": x.get("cpc"),
                                 "competition_level": x.get("competition_level"), "rank": (x.get("serp") or {}).get("rank_absolute"),
                                 "url": (x.get("serp") or {}).get("url")} for x in res["items"] if x.get("keyword")][:100]
    for r in found.values():
        if r["verified"] or any(s.startswith("dataforseo") for s in r["sources"]):
            research_events.emit("competitor_identified", r["name"], stage=stage, provider=PROVIDER, url=f"https://{r['domain']}",
                                 basis=("; ".join(r["evidence"]) if r["evidence"] else "candidate: no search-overlap evidence yet"),
                                 verified=r["verified"])
    return {"used": True, "target_domain": target, "where": where, "competitors": sorted(found.values(), key=lambda r: (not r["verified"],
            -(r["shared_keywords"] or 0), -(r["serp_keywords"] or 0))), "errors": errors,
            "verification_rule": f"Verified only with search-overlap evidence: >= {MIN_SHARED_KEYWORDS} shared ranking keywords, or ranking for "
                                 f">= {MIN_SERP_KEYWORDS} target keywords, or >= {MIN_INTERSECTION} shared keywords in Domain Intersection."}
