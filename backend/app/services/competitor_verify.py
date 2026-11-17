"""Competitor verification records: one record per competitor domain, from every research source, with the evidence that supports
(or rejects) it. Nothing is called verified without evidence: an AI or web-search suggestion alone stays `Discovered`.

Statuses, strongest first:
  Market Verified   — seen in Google results for the client's services AND evidence of the same geographic market
  Website Verified  — the competitor's website was opened and shows one of the client's services
  SERP Verified     — ranks or advertises in Google results for the client's service searches (SerpApi or DataForSEO overlap)
  Pending Verification — client-provided, or found with some evidence that is not yet enough
  Discovered        — named by one source only (web search, AI), nothing checked
  Rejected          — not a competitor (search engine, directory, social network, marketplace, the client itself), with the reason
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from app.core.util import normalize_term

STATUSES = ("Market Verified", "Website Verified", "SERP Verified", "Pending Verification", "Discovered", "Rejected")
VERIFIED = ("Market Verified", "Website Verified", "SERP Verified")

# Search engines, directories, review sites, social networks, marketplaces and reference sites: never a competitor.
EXCLUDED = {
    "search engine": ("google.", "bing.com", "yahoo.", "duckduckgo.com", "ask.com", "aol.com", "baidu.com", "yandex.", "ecosia.org", "brave.com"),
    "directory / review site": ("yelp.", "angi.", "angieslist", "yellowpages", "bbb.org", "thumbtack", "homeadvisor", "nextdoor", "tripadvisor",
                                "mapquest", "porch.com", "houzz.", "expertise.com", "threebestrated", "manta.com", "chamberofcommerce",
                                "superpages", "bark.com", "networx", "trustpilot", "angieslist", "buildzoom", "fixr.com", "homeguide",
                                "citysearch", "local.com", "merchantcircle", "birdeye", "zocdoc", "healthgrades", "avvo.com", "findlaw",
                                "justia", "lawyers.com", "martindale", "clutch.co", "upcity", "designrush", "goodfirms", "glassdoor", "indeed."),
    "social network / video": ("facebook.", "instagram.", "linkedin.", "x.com", "twitter.", "tiktok.", "youtube.", "pinterest.", "reddit.",
                               "quora.com", "medium.com", "threads.net", "snapchat."),
    "marketplace / retailer": ("amazon.", "ebay.", "etsy.", "walmart.", "homedepot.", "lowes.", "craigslist.", "taskrabbit", "fiverr.",
                               "upwork.", "alibaba.", "costco.", "target.com", "bestbuy."),
    "reference / maps": ("wikipedia.", "apple.com", "waze.com", "gov", "edu"),
}


def domain(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    h = urlparse(u if "//" in u else f"https://{u}").hostname or ""
    return h.lower().removeprefix("www.")


def excluded_reason(d: str) -> str | None:
    d = (d or "").lower()
    if not d:
        return None
    for kind, pats in EXCLUDED.items():
        for p in pats:
            if p in ("gov", "edu"):
                hit = d.endswith("." + p)
            elif p.endswith("."):            # a brand on any TLD or subdomain: google.com, maps.google.co.uk
                hit = d.startswith(p) or f".{p}" in f".{d}"
            elif "." in p:                   # one exact domain (and its subdomains)
                hit = d == p or d.endswith("." + p)
            else:                            # a brand name inside the domain: thumbtack, homeadvisor
                hit = p in d
            if hit:
                return (f"{kind} ({d}): a public institution, not a business competitor" if p in ("gov", "edu")
                        else f"{kind} ({d}): not a business competing for the client's customers")
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build(ctx: dict, result: dict) -> dict:
    """Records for every competitor domain the competitor research saw. `result` is competitor_audit's output."""
    own = domain(ctx.get("website_url") or "")
    services = [s for s in ctx.get("service_names") or []]
    svc_n = [normalize_term(s) for s in services]
    places = [normalize_term(p) for p in [ctx.get("city") or "", *[(a.get("label") if isinstance(a, dict) else str(a)) or ""
                                                                   for a in ctx.get("service_areas") or []]] if p]
    recs: dict[str, dict] = {}

    def rec(url: str, name: str, source: str, classification: str) -> dict | None:
        d = domain(url)
        if not d:
            return None
        r = recs.get(d)
        if r is None:
            r = recs[d] = {"domain": d, "name": name or d, "url": url if "//" in (url or "") else f"https://{d}",
                           "classification": classification, "discovery_sources": [], "queries": [], "locations": [], "serp_evidence": [],
                           "services_found": [], "service_evidence": [], "website_accessible": None, "geo_overlap_evidence": [],
                           "dataforseo": None, "status": "Discovered", "rejection_reason": None, "evidence_note": "", "timestamp": None}
        if classification == "client_provided":
            r["classification"] = "client_provided"
            r["name"] = name or r["name"]
        if source not in r["discovery_sources"]:
            r["discovery_sources"].append(source)
        return r

    for c in ctx.get("competitors") or []:
        rec(c.get("website_url") or "", c.get("name") or "", "client profile", "client_provided")
    for c in result.get("competitors") or []:  # client competitors whose websites were crawled
        r = rec(c.get("website_url") or "", c.get("name") or "", "client profile", "client_provided")
        if r is None:
            continue
        r["website_accessible"] = bool(c.get("crawl_source")) and c.get("services") is not None
        found = c.get("services") or []
        r["services_found"] = found
        match = [s for s in services if any(normalize_term(s) in normalize_term(f) or normalize_term(f) in normalize_term(s) for f in found)]
        if match:
            r["service_evidence"].append({"source": "website crawl", "detail": f"service pages for {', '.join(match[:4])}"})
    for c in result.get("web_candidates") or []:
        ev = c.get("evidence") or {}
        src = (ev.get("provider") or "web search") if isinstance(ev, dict) else "web search"
        r = rec(c.get("website_url") or "", c.get("name") or "", f"web research ({src})" if "serpapi" != src else "Google results (SerpApi)",
                "discovered")
        if r is None:
            continue
        page = c.get("page") or {}
        if page:
            r["website_accessible"] = bool(page.get("ok"))
            text = normalize_term(" ".join([page.get("title") or "", *(page.get("h1") or [])]))
            match = [s for s, n in zip(services, svc_n) if n and n in text]
            if match:
                r["service_evidence"].append({"source": "website opened", "detail": f"page title/heading names {', '.join(match[:3])}"})
        if isinstance(ev, dict) and ev.get("provider") == "serpapi" and ev.get("query"):
            r["serp_evidence"].append({"provider": "serpapi", "query": ev.get("query"), "location": ev.get("location"),
                                       "position": ev.get("position"), "ad_title": ev.get("ad_title"), "retrieved_at": ev.get("retrieved_at")})
    serp = result.get("serp") or {}
    for o in serp.get("organic_competitors") or []:
        r = rec(f"https://{o['domain']}", o.get("title") or o["domain"], "Google results (SerpApi)", "discovered")
        if r is None:
            continue
        for a in o.get("appearances") or []:
            r["serp_evidence"].append({"provider": "serpapi", "query": a.get("query"), "location": a.get("location"), "position": a.get("position"),
                                       "retrieved_at": a.get("retrieved_at")})
    for ad in serp.get("sponsored_ads") or []:
        if not ad.get("domain") or ad.get("is_client"):
            continue
        r = rec(f"https://{ad['domain']}", ad["domain"], "Google ads (SerpApi)", "discovered")
        if r is not None:
            r["serp_evidence"].append({"provider": "serpapi", "query": ad.get("query"), "location": ad.get("location"), "ad_title": ad.get("title"),
                                       "retrieved_at": ad.get("retrieved_at")})
    for lp in serp.get("local_businesses") or []:
        d = domain(lp.get("website") or "")
        if d and d in recs:
            addr = lp.get("address")
            recs[d]["geo_overlap_evidence"].append({"source": lp.get("source_engine") or "google local",
                                                    "detail": f"listed locally at {addr}" if addr else "listed in local results"})
    for c in ((result.get("dataforseo") or {}).get("competitors") or []):
        r = rec(f"https://{c['domain']}", c.get("name") or c["domain"], "DataForSEO", "discovered")
        if r is None:
            continue
        r["dataforseo"] = {k: c.get(k) for k in ("verified", "shared_keywords", "serp_keywords", "location", "last_updated")}
        if c.get("verified"):
            r["serp_evidence"].append({"provider": "dataforseo", "query": f"{c.get('shared_keywords') or 0} shared ranking keyword(s), "
                                       f"{c.get('serp_keywords') or 0} target keyword(s) ranked", "location": c.get("location"),
                                       "retrieved_at": c.get("last_updated")})
    for r in recs.values():
        _decide(r, own, places)
    out = sorted(recs.values(), key=lambda r: (STATUSES.index(r["status"]), r["classification"] != "client_provided", r["domain"]))
    return {"records": out, "counts": counts(out), "statuses": list(STATUSES),
            "policy": "A competitor is verified only on evidence: Google results for the client's services (SERP), its website showing the "
                      "client's services (Website), and both plus a shared geographic market (Market). AI or web-search suggestions alone "
                      "are never verified. Search engines, directories, social networks and marketplaces are rejected."}


def _decide(r: dict, own: str, places: list[str]) -> None:
    r["timestamp"] = next((e.get("retrieved_at") for e in r["serp_evidence"] if e.get("retrieved_at")), None) or _now()
    for e in r["serp_evidence"]:
        if e.get("query"):
            r["queries"].append(e["query"])
        if e.get("location"):
            r["locations"].append(e["location"])
    r["queries"] = list(dict.fromkeys(r["queries"]))[:10]
    r["locations"] = list(dict.fromkeys(r["locations"]))[:5]
    if r["domain"] == own:
        r.update(status="Rejected", rejection_reason="this is the client's own website")
        return
    why = excluded_reason(r["domain"])
    if why:
        r.update(status="Rejected", rejection_reason=why)
        return
    if r["website_accessible"] is False and not r["serp_evidence"]:
        r["evidence_note"] = "website could not be opened"
    serp_ok = bool(r["serp_evidence"])
    site_ok = bool(r["website_accessible"]) and bool(r["service_evidence"])
    geo = r["geo_overlap_evidence"] or [{"source": "SERP location", "detail": loc} for loc in r["locations"]
                                         if any(p and p in normalize_term(loc) for p in places)]
    r["geo_overlap_evidence"] = geo
    if serp_ok and geo and (site_ok or len(r["serp_evidence"]) >= 2):
        r["status"] = "Market Verified"
    elif site_ok:
        r["status"] = "Website Verified"
    elif serp_ok:
        r["status"] = "SERP Verified"
    elif r["classification"] == "client_provided" or r["website_accessible"]:
        r["status"] = "Pending Verification"
        r["evidence_note"] = r["evidence_note"] or ("supplied by the client; not yet confirmed by search or website evidence"
                                                    if r["classification"] == "client_provided" else "website opened; services not confirmed")
    else:
        r["status"] = "Discovered"
        r["evidence_note"] = "named by one source; nothing checked"


def counts(records: list[dict]) -> dict:
    """Evidence counts (a record can count in several: a Market Verified competitor also has SERP evidence)."""
    live = [r for r in records if r["status"] != "Rejected"]
    by = {s: sum(1 for r in records if r["status"] == s) for s in STATUSES}
    return {"discovered": len(records), "serp_verified": sum(1 for r in live if r["serp_evidence"]),
            "website_verified": sum(1 for r in live if r["website_accessible"] and r["service_evidence"]),
            "market_verified": by["Market Verified"], "verified": sum(by[s] for s in VERIFIED),
            "pending": by["Pending Verification"] + by["Discovered"], "rejected": by["Rejected"],
            "client_provided": sum(1 for r in records if r["classification"] == "client_provided"), "by_status": by}
