"""SERP research through SerpApi, for the client's own services and target location.

One planner for every SerpApi request made during research:
  * query planning   — Google searches for "<service> <city>" (organic, sponsored ads, related searches/questions, local pack), Google
                       Autocomplete for each service, one Google Maps search when the business location can be geocoded, and (only when switched
                       on) the Ads Transparency Center for competitor domains;
  * quota            — before a batch, the free Account API is read; the batch is cut to what the plan's remaining searches (minus the
                       configured reserve) and the per-audit budget allow, highest-value queries first; whatever cannot run is reported by name;
  * caching          — identical requests are answered from provider_cache (7 days for results, 14 for autocomplete, 90 for locations) and are
                       marked `served_from_cache` with their ORIGINAL retrieval time; they are never shown as newly retrieved;
  * provenance       — every finding carries the query, engine, location, device, retrieval time, SerpApi search id and source URL;
  * invalidation     — queries are built from the client's current services, city and settings, so changed inputs produce new queries.

Policy: these are observations of what Google showed. Ranks are not search volume, sponsored ads are not CPCs, ad copy is not evidence that
an advertiser performs well, and suggestions carry no volume. Without SerpApi (or without quota) research continues with the other sources.
"""
from __future__ import annotations

import time
from datetime import timedelta

from app.adapters import registry
from app.adapters.base import AdapterError
from app.core.util import normalize_term
from app.services import integrations_service, provider_calls, research_events

PROVIDER = "serpapi"
DIRECTORIES = ("yelp.", "angi.", "angieslist", "yellowpages", "bbb.org", "facebook.", "google.", "thumbtack", "homeadvisor", "nextdoor",
               "reddit.", "wikipedia.", "youtube.", "linkedin.", "instagram.", "x.com", "twitter.", "tripadvisor", "mapquest", "porch.com",
               "houzz.", "expertise.com", "threebestrated", "manta.com", "chamberofcommerce", "superpages", "bark.com", "networx", "amazon.")
TTL = {"google": timedelta(days=7), "google_maps": timedelta(days=7), "google_autocomplete": timedelta(days=14),
       "google_ads_transparency_center": timedelta(days=7)}
PRIORITY = {"google": 0, "google_autocomplete": 1, "google_maps": 2, "google_ads_transparency_center": 3}
CAPABILITY = {"google": "organic results, sponsored ads, related searches and questions", "google_autocomplete": "autocomplete suggestions",
              "google_maps": "Google Maps local businesses", "google_ads_transparency_center": "Ads Transparency Center creatives"}
_account_memo: dict[str, tuple[float, dict]] = {}


def adapter():
    return registry.optional(PROVIDER)


def settings() -> dict:
    return integrations_service.resolve(PROVIDER)


def available() -> tuple[bool, str]:
    if adapter() is None:
        return False, "SerpApi is not configured or is switched off; Google results research was not used."
    return True, ""


def _int(v, default: int) -> int:
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return default


def account(force: bool = False) -> dict | None:
    """Plan and searches left from the free Account API (memoised for 5 minutes per process). None when it cannot be read."""
    a = adapter()
    if a is None:
        return None
    hit = _account_memo.get("acc")
    if hit and not force and time.monotonic() - hit[0] < 300:
        return hit[1]
    try:
        acc = a.account()
    except AdapterError:
        return None
    _account_memo["acc"] = (time.monotonic(), acc)
    return acc


def allowance(needed: int) -> tuple[int, str, dict | None]:
    """How many of `needed` live searches may run now, and why fewer (plan quota, reserve, per-audit budget)."""
    s = settings()
    per_audit = _int(s.get("max_searches_per_audit"), 12)
    spent = provider_calls.spent_this_audit(PROVIDER)
    left_budget = max(0, per_audit - spent) if research_events.current_report_id() else per_audit
    acc = account()
    reserve = _int(s.get("reserve_searches"), 0)
    quota_left = None
    if acc and isinstance(acc.get("total_searches_left"), (int, float)):
        quota_left = max(0, int(acc["total_searches_left"]) - reserve)
    allowed = min(needed, left_budget, quota_left if quota_left is not None else needed)
    reasons = []
    if quota_left is not None and quota_left < needed:
        reasons.append(f"the SerpApi plan has {acc['total_searches_left']} search(es) left" + (f" and {reserve} are kept in reserve" if reserve else ""))
    if left_budget < needed:
        reasons.append(f"the per-audit budget allows {left_budget} more search(es) ({spent} of {per_audit} used)")
    if acc is None:
        reasons.append("the remaining quota could not be read (Account API unavailable); the per-audit budget still applies")
    return allowed, "; ".join(reasons), acc


def location_for(ctx: dict) -> tuple[str, str]:
    """A SerpApi canonical location for the client's city (free Locations API, cached 90 days). ("", reason) when none is found."""
    a = adapter()
    city = (ctx.get("city") or "").strip()
    if not city:
        return "", "No city in the client profile; searches are not localised beyond the country."
    try:
        locs = provider_calls.call("serpapi_locations", "locations", {"q": normalize_term(city)}, lambda: a.locations(city, limit=3),
                                   ttl=timedelta(days=90))
    except AdapterError as exc:
        return "", f"Location lookup failed ({exc.kind}); searches use the country only."
    if not locs:
        return "", f"SerpApi has no location matching “{city}”; searches use the country only."
    return locs[0]["canonical_name"], ""


def _run(engine: str, req: dict, fn, *, purpose: str, stage: str) -> dict:
    s = settings()
    try:
        value, meta = provider_calls.call(PROVIDER, engine, req, fn, ttl=TTL[engine], cost=1,
                                          budget=_int(s.get("max_searches_per_audit"), 12), meta=True)
    except AdapterError as exc:
        research_events.emit("provider_failed" if exc.kind not in ("budget_exhausted", "quota_exceeded") else "provider_unavailable",
                             f"SerpApi ({engine}): {exc.message}", stage=stage, level="warn", provider=PROVIDER, error_kind=exc.kind,
                             engine=engine, query=req.get("q"))
        return {"ok": False, "engine": engine, "query": req.get("q"), "error_kind": exc.kind, "error": exc.message, "purpose": purpose}
    out = {**value, "ok": True, "purpose": purpose, "served_from_cache": meta["cached"], "cached_at": meta.get("cached_at")}
    research_events.emit("serp_query", f"{'Cached' if meta['cached'] else 'Searched'} Google ({engine.replace('_', ' ')}): {req.get('q')}",
                         stage=stage, provider=PROVIDER, engine=engine, query=req.get("q"), location=req.get("location") or req.get("ll"),
                         served_from_cache=meta["cached"], retrieved_at=value.get("retrieved_at"), purpose=purpose)
    return out


def plan(ctx: dict, parts: tuple[str, ...], location: str, ll: str | None) -> list[dict]:
    s = settings()
    svcs = [x for x in (ctx.get("service_names") or []) if x][:3]
    city = (ctx.get("city") or "").split(",")[0].strip()
    out: list[dict] = []
    if "google" in parts:
        for svc in svcs:
            q = f"{svc} {city}".strip().lower()
            out.append({"engine": "google", "q": q, "purpose": f"competitors, ads and intent for “{svc}”", "service": svc,
                        "req": {"q": q, "location": location, "gl": s.get("country") or "us", "hl": s.get("language") or "en",
                                "device": s.get("device") or "desktop"}})
    if "autocomplete" in parts:
        for svc in svcs:
            q = svc.lower()
            out.append({"engine": "google_autocomplete", "q": q, "purpose": f"autocomplete for “{svc}”", "service": svc,
                        "req": {"q": q, "gl": s.get("country") or "us", "hl": s.get("language") or "en"}})
    if "maps" in parts and ll:
        cat = (ctx.get("category") or "").strip()
        # a business-type code such as "home_services" is not something people search for: use a real category, else the first service
        what = (cat if cat and "_" not in cat else (svcs[0] if svcs else "")).lower()
        if what:
            out.append({"engine": "google_maps", "q": what, "purpose": "local businesses on Google Maps", "service": None,
                        "req": {"q": what, "ll": ll, "gl": s.get("country") or "us", "hl": s.get("language") or "en"}})
    if "transparency" in parts and (s.get("ads_transparency") or "off") == "on":
        for c in (ctx.get("competitors") or [])[:3]:
            dom = c.get("website_url") and _host(c["website_url"])
            if dom:
                out.append({"engine": "google_ads_transparency_center", "q": dom, "purpose": f"ads run by {c.get('name') or dom}", "service": None,
                            "req": {"text": dom, "q": dom}})
    seen, uniq = set(), []
    for p in out:  # duplicate-query prevention inside one plan
        k = (p["engine"], tuple(sorted(p["req"].items())))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return sorted(uniq, key=lambda p: PRIORITY[p["engine"]])


def _host(u: str) -> str:
    from app.adapters.research.serpapi import host
    return host(u)


def _call_for(a, p: dict):
    r = p["req"]
    if p["engine"] == "google":
        return lambda: a.google(r["q"], location=r.get("location") or "", gl=r.get("gl"), hl=r.get("hl"), device=r.get("device"))
    if p["engine"] == "google_autocomplete":
        return lambda: a.autocomplete(r["q"], gl=r.get("gl"), hl=r.get("hl"))
    if p["engine"] == "google_maps":
        return lambda: a.maps(r["q"], ll=r["ll"], gl=r.get("gl"), hl=r.get("hl"))
    return lambda: a.ads_transparency(r["text"])


def research(ctx: dict, *, stage: str, parts: tuple[str, ...] = ("google", "autocomplete", "maps", "transparency")) -> dict:
    """Run the planned SerpApi queries for this client and return normalised, attributed findings. Never raises for provider problems."""
    ok, why = available()
    if not ok:
        return {"used": False, "reason": why, "queries": [], "unavailable": [{"capability": "SerpApi research", "reason": why}]}
    a = adapter()
    location, loc_note = location_for(ctx) if "google" in parts else ("", "")
    ll, ll_note = None, ""
    if "maps" in parts:
        from app.services import geo_intel
        where = ctx.get("address") or ctx.get("city") or ""
        point, _ = geo_intel._geocode(where, stage) if where else (None, [])
        if point:
            ll = f"@{point['lat']:.4f},{point['lon']:.4f},13z"
        else:
            ll_note = "The business location could not be geocoded, so Google Maps research was not run."
    planned = plan(ctx, parts, location, ll)
    cached = [p for p in planned if provider_calls.is_cached(PROVIDER, p["engine"], p["req"])]
    live = [p for p in planned if p not in cached]
    allowed, limit_reason, acc = allowance(len(live)) if live else (0, "", account())
    run_now, skipped = cached + live[:allowed], live[allowed:]  # cached answers cost nothing and always run
    unavailable = [{"capability": CAPABILITY[p["engine"]], "query": p["q"], "reason": limit_reason or "budget"} for p in skipped]
    if ll_note:
        unavailable.append({"capability": CAPABILITY["google_maps"], "reason": ll_note})
    if skipped:
        research_events.emit("provider_unavailable", f"SerpApi: {len(skipped)} planned search(es) not run — {limit_reason}", stage=stage, level="warn",
                             provider=PROVIDER, skipped=[p["q"] for p in skipped][:10])
    results = [_run(p["engine"], {**p["req"]}, _call_for(a, p), purpose=p["purpose"], stage=stage) | {"service": p["service"]} for p in run_now]
    out = aggregate(ctx, results, stage=stage)
    return {"used": True, "reason": "", "location": location, "location_note": loc_note, "queries": [
        {k: r.get(k) for k in ("engine", "query", "location", "ll", "retrieved_at", "served_from_cache", "cached_at", "search_id", "ok", "error",
                                "error_kind", "purpose", "service", "empty")} for r in results],
        **out, "unavailable": unavailable,
        "quota": {"plan_searches_left": (acc or {}).get("total_searches_left"), "plan_name": (acc or {}).get("plan_name"),
                  "planned": len(planned), "run": len(run_now), "from_cache": len(cached), "skipped": len(skipped)}}


def aggregate(ctx: dict, results: list[dict], *, stage: str) -> dict:
    """Competitor domains, public ads, intent phrases and local businesses, each with its query, location, time and URL."""
    own = _host(ctx.get("website_url") or "")
    known = {_host(c.get("website_url") or "") for c in ctx.get("competitors") or []}
    organic, ads, related, questions, autocomplete, local, creatives = {}, [], [], [], [], [], []

    def prov(r: dict) -> dict:
        return {"query": r.get("query"), "engine": r.get("engine"), "location": r.get("location") or r.get("ll") or None,
                "retrieved_at": r.get("retrieved_at"), "served_from_cache": r.get("served_from_cache"), "search_id": r.get("search_id"),
                "provider": PROVIDER}
    for r in results:
        if not r.get("ok"):
            continue
        p = prov(r)
        if r["engine"] == "google":
            for o in r.get("organic") or []:
                d = o["domain"]
                if not d or d == own or any(x in d for x in DIRECTORIES):
                    continue
                e = organic.setdefault(d, {"domain": d, "title": o["title"], "url": o["link"], "best_position": o["position"], "appearances": [],
                                           "already_known": d in known})
                e["best_position"] = min(x for x in (e["best_position"], o["position"]) if x is not None) if o["position"] is not None else e["best_position"]
                e["appearances"].append({**p, "position": o["position"], "url": o["link"]})
            for ad in r.get("ads") or []:
                ads.append({**ad, **p, "is_client": ad["domain"] == own,
                            "note": "Public ad copy as shown on Google; not a bid, CPC or measure of performance."})
            related += [{"phrase": q, **p} for q in r.get("related_searches") or []]
            questions += [{"question": q["question"], "url": q.get("link"), **p} for q in r.get("related_questions") or []]
            local += [{**lp, **p, "source_engine": "google local pack"} for lp in r.get("local_results") or []]
        elif r["engine"] == "google_autocomplete":
            autocomplete += [{"phrase": sgt, **p} for sgt in r.get("suggestions") or []]
        elif r["engine"] == "google_maps":
            local += [{**pl, **p, "source_engine": "google maps"} for pl in r.get("places") or []]
        elif r["engine"] == "google_ads_transparency_center":
            creatives += [{**c, **p} for c in r.get("creatives") or []]
    comp = sorted(organic.values(), key=lambda e: (e["best_position"] or 99, -len(e["appearances"])))
    for e in comp[:8]:
        research_events.emit("competitor_identified", e["title"] or e["domain"], stage=stage, provider=PROVIDER, url=e["url"],
                             basis=f"Google organic result #{e['best_position']} for “{e['appearances'][0]['query']}”")
    for ad in ads[:8]:
        research_events.emit("sponsored_ad", f"{ad['domain'] or ad.get('displayed_link')}: {ad['title']}", stage=stage, provider=PROVIDER,
                             url=ad.get("link"), query=ad["query"], block_position=ad.get("block_position"), description=ad.get("description"))
    for lp in local[:8]:
        research_events.emit("local_business", lp["title"], stage=stage, provider=PROVIDER, rating=lp.get("rating"), reviews=lp.get("reviews"),
                             address=lp.get("address"), source=lp["source_engine"])
    if related or autocomplete or questions:
        research_events.emit("keyword_discovery", f"{len(related)} related search(es), {len(questions)} question(s), {len(autocomplete)} autocomplete "
                             "suggestion(s) from Google via SerpApi", stage=stage, provider=PROVIDER,
                             examples=[x["phrase"] for x in (related + autocomplete)][:5], count=len(related) + len(autocomplete) + len(questions))
    return {"organic_competitors": comp[:15], "sponsored_ads": ads[:30], "related_searches": related[:40], "related_questions": questions[:30],
            "autocomplete": autocomplete[:60], "local_businesses": local[:30], "ad_creatives": creatives[:40],
            "advertisers": sorted({a["domain"] for a in ads if a.get("domain") and not a.get("is_client")})}


def evidence_summary(serp: dict | None) -> dict | None:
    """A compact, attributed digest for the Main AI and the Challenger AI. None when no search was performed (so no model can imply one)."""
    if not serp or not serp.get("used") or not any(q.get("ok") for q in serp.get("queries") or []):
        return None
    return {"provider": "SerpApi (public Google results)", "location": serp.get("location") or None,
            "queries": [{"q": q["query"], "engine": q["engine"], "retrieved_at": q.get("retrieved_at"), "from_cache": q.get("served_from_cache")}
                        for q in serp["queries"] if q.get("ok")][:8],
            "organic_competitors": [{"domain": c["domain"], "best_position": c["best_position"], "url": c["url"]} for c in serp.get("organic_competitors", [])[:6]],
            "advertisers_seen": serp.get("advertisers", [])[:8],
            "public_ad_copy": [{"domain": a["domain"], "title": a["title"], "description": a.get("description", "")[:160], "url": a.get("link")}
                               for a in serp.get("sponsored_ads", []) if not a.get("is_client")][:5],
            "related_searches": [r["phrase"] for r in serp.get("related_searches", [])][:10],
            "questions": [r["question"] for r in serp.get("related_questions", [])][:6],
            "note": "Observed Google results for the listed queries and place. Ranks are not search volume; ads are not CPCs or proof of performance."}
