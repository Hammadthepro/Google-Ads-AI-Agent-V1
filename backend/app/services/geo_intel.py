"""Geographic and market intelligence without Google Places.

Order: geocode the business (Geoapify when configured, else OpenStreetMap Nominatim) -> nearby businesses of the same category
(OpenStreetMap Overpass, plus Geoapify Places when configured) -> deduplicate across sources -> match the client and the supplied
competitors -> market density. Ratings, reviews, hours and operating status are not available from these sources and are reported as
unavailable; nothing is filled in.
"""
from __future__ import annotations

import math
from datetime import timedelta

from app.adapters import registry
from app.adapters.base import AdapterError
from app.adapters.research.geo import geoapify_category_for, osm_tags_for
from app.core.util import normalize_term
from app.services import provider_calls, research_events
from app.services.web_research import host

LIMITATIONS = [
    "OpenStreetMap is community-edited: many small service businesses are missing or out of date, so counts are a lower bound.",
    "No ratings, reviews, opening status or review counts are available from OpenStreetMap or Geoapify; those checks need Google Places or a manual look.",
]


def _km(a: dict, b: dict) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _geocode(query: str, stage: str) -> tuple[dict | None, list[dict]]:
    problems = []
    for name in ("geoapify", "openstreetmap"):
        adapter = registry.optional(name)
        if adapter is None:
            continue
        try:
            loc = provider_calls.call(name, "geocode", {"q": normalize_term(query)}, lambda a=adapter: a.geocode(query), ttl=timedelta(days=90))
        except AdapterError as exc:
            problems.append({"provider": name, "kind": exc.kind, "message": exc.message})
            research_events.emit("provider_failed", f"{name}: geocoding failed ({exc.kind})", stage=stage, level="warn", provider=name, error_kind=exc.kind)
            continue
        if loc:
            return loc, problems
    return None, problems


def dedupe(pois: list[dict]) -> list[dict]:
    """Same business from two sources = same normalised name within 200 m, or the same website host. Sources are merged, not dropped."""
    out: list[dict] = []
    for p in pois:
        name, h = normalize_term(p["name"]), host(p.get("website", ""))
        match = next((q for q in out if (h and h == host(q.get("website", ""))) or (normalize_term(q["name"]) == name and _km(p, q) < 0.2)), None)
        if match is None:
            out.append({**p, "sources": [{"source": p["source"], "ref": p["source_ref"], "attribution": p["attribution"]}]})
            continue
        match["sources"].append({"source": p["source"], "ref": p["source_ref"], "attribution": p["attribution"]})
        for k in ("website", "phone", "address"):
            if not match.get(k) and p.get(k):
                match[k] = p[k]
    return out


def research(ctx: dict, *, stage: str = "geo") -> dict:
    research_events.emit("stage_started", "Geographic & market research", stage=stage)
    query = ctx.get("address") or ", ".join(x for x in (ctx.get("city"), *(ctx.get("states") or [])[:1]) if x)
    if not query:
        research_events.emit("stage_skipped", "No address or city in the business profile", stage=stage, level="warn")
        return {"available": False, "reason": "No address or city in the business profile.", "limitations": LIMITATIONS}
    center, problems = _geocode(query, stage)
    if center is None:
        reason = ("No geocoding provider answered: " + "; ".join(f"{p['provider']} {p['kind']}" for p in problems)) if problems \
            else "No geocoding provider is enabled (OpenStreetMap is switched off and Geoapify is not configured)."
        research_events.emit("stage_failed", reason, stage=stage, level="warn")
        return {"available": False, "reason": reason, "provider_errors": problems, "limitations": LIMITATIONS}
    research_events.emit("location_identified", center["display_name"][:200] or query, stage=stage, lat=center["lat"], lon=center["lon"],
                         provider=center["source"], role="business")
    radius_m = int(min(max(float(ctx.get("radius_miles") or 10), 1), 30) * 1609)
    type_text = " ".join(x for x in (ctx.get("category"), ctx.get("business_type"), *(ctx.get("service_names") or [])) if x)
    tags, gcat = osm_tags_for(type_text), geoapify_category_for(type_text)
    pois, sources_used = [], []
    osm = registry.optional("openstreetmap")
    if osm is not None and tags:
        try:
            got = provider_calls.call("openstreetmap", "nearby", {"lat": round(center["lat"], 3), "lon": round(center["lon"], 3), "r": radius_m, "tags": tags},
                                      lambda: osm.nearby(center["lat"], center["lon"], radius_m, tags), ttl=timedelta(days=14))
            pois += got
            sources_used.append({"provider": "openstreetmap_overpass", "results": len(got), "category": ", ".join(f"{k}={v}" for k, v in tags)})
        except AdapterError as exc:
            problems.append({"provider": "openstreetmap", "kind": exc.kind, "message": exc.message})
            research_events.emit("provider_failed", f"OpenStreetMap Overpass: {exc.kind}", stage=stage, level="warn", provider="openstreetmap", error_kind=exc.kind)
    geo = registry.optional("geoapify")
    if geo is not None and gcat:
        try:
            got = provider_calls.call("geoapify", "nearby", {"lat": round(center["lat"], 3), "lon": round(center["lon"], 3), "r": radius_m, "c": gcat},
                                      lambda: geo.nearby(center["lat"], center["lon"], radius_m, gcat), ttl=timedelta(days=14))
            pois += got
            sources_used.append({"provider": "geoapify_places", "results": len(got), "category": gcat})
        except AdapterError as exc:
            problems.append({"provider": "geoapify", "kind": exc.kind, "message": exc.message})
            research_events.emit("provider_failed", f"Geoapify: {exc.kind}", stage=stage, level="warn", provider="geoapify", error_kind=exc.kind)
    if not tags and not gcat:
        problems.append({"provider": "category", "kind": "unsupported", "message": f"No map category is known for '{type_text[:80]}'."})
    merged = dedupe(pois)
    own_host, own_name = host(ctx.get("website_url") or ""), normalize_term(ctx.get("business_name") or "")
    supplied = {host(c.get("website_url") or ""): c["name"] for c in ctx.get("competitors") or [] if c.get("website_url")}
    supplied_names = {normalize_term(c["name"]): c["name"] for c in ctx.get("competitors") or []}
    client, nearby = None, []
    for p in merged:
        p["distance_km"] = round(_km(center, p), 2)
        h = host(p.get("website", ""))
        if (own_host and h == own_host) or (own_name and normalize_term(p["name"]) == own_name):
            client = p
            continue
        p["supplied_competitor"] = supplied.get(h) or supplied_names.get(normalize_term(p["name"]))
        nearby.append(p)
    nearby.sort(key=lambda p: p["distance_km"])
    for p in nearby[:25]:
        research_events.emit("competitor_identified", p["name"], stage=stage, provider=p["sources"][0]["source"], lat=p["lat"], lon=p["lon"],
                             distance_km=p["distance_km"], basis="map listing in the same category")
    rings = {f"within_{k}km": sum(1 for p in nearby if p["distance_km"] <= k) for k in (2, 5, 10, 25) if k * 1000 <= radius_m * 1.01 or k == 2}
    out = {"available": True, "center": center, "radius_m": radius_m, "category_tags": [f"{k}={v}" for k, v in tags], "geoapify_category": gcat,
           "client_listing": client, "nearby": nearby[:60], "density": {"total": len(nearby), **rings}, "sources": sources_used,
           "provider_errors": problems, "limitations": LIMITATIONS,
           "attribution": sorted({s["attribution"] for p in merged for s in p["sources"]} | ({center["attribution"]} if center.get("attribution") else set()))}
    research_events.emit("research_stored", f"{len(nearby)} nearby business(es) in the same category", stage=stage, count=len(nearby))
    research_events.emit("stage_completed", "Geographic & market research", stage=stage)
    return out
