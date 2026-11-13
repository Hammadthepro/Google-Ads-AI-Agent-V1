"""Web research used by the agent only when it adds something the other sources lack (Web Search, then Web Fetch).

Every result is stored as web evidence (query, provider, URL, title, snippet, retrieval time, purpose) with confidence
"unverified_snippet": a search snippet is never treated as a verified fact about a business. Searches are cached for 7 days,
identical queries are never repeated, and each audit has a configurable search budget. Without Tavily the agent carries on.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from app.services import research_events, web_fetch, web_search

DIRECTORIES = ("yelp.", "angi.", "angieslist", "yellowpages", "bbb.org", "facebook.", "google.", "thumbtack", "homeadvisor", "nextdoor",
               "reddit.", "wikipedia.", "youtube.", "linkedin.", "instagram.", "x.com", "twitter.", "tripadvisor", "mapquest", "porch.com",
               "houzz.", "expertise.com", "threebestrated", "manta.com", "chamberofcommerce", "superpages", "bark.com", "networx")


def available() -> tuple[bool, str]:
    names = web_search.available()
    if not names:
        return False, "No web search provider is configured (Tavily or Brave Search); web search was not used."
    return True, ""


def search(query: str, purpose: str, *, stage: str = "", max_results: int = 5) -> dict:
    """Web Search through the configured provider(s); see app.services.web_search. Never raises for provider problems."""
    return web_search.search(query, purpose, stage=stage, max_results=max_results)


def host(u: str) -> str:
    h = (urlparse(u if "//" in u else f"https://{u}").hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def discover_competitors(ctx: dict, *, stage: str = "competitors") -> dict:
    """Candidate competitors from web results when fewer than three were supplied. Candidates are suggestions, never auto-added."""
    if len(ctx.get("competitors") or []) >= 3:
        return {"used": False, "reason": "Three or more competitors were supplied; no web search needed.", "candidates": []}
    what = ctx.get("category") or ctx.get("business_type") or (ctx.get("service_names") or [""])[0]
    where = ctx.get("city") or ""
    if not what:
        return {"used": False, "reason": "No business category or service to search for.", "candidates": []}
    res = search(f"{what} {where}".strip(), "competitor_discovery", stage=stage, max_results=8)
    if not res["ok"]:
        return {"used": False, "reason": res["reason"], "candidates": []}
    own = host(ctx.get("website_url") or "")
    known = {host(c.get("website_url") or "") for c in ctx.get("competitors") or []}
    cands, seen = [], set()
    for r in res["results"]:
        h = host(r["url"])
        if not h or h == own or h in known or h in seen or any(d in h for d in DIRECTORIES):
            continue
        seen.add(h)
        name = re.split(r"\s[|–—-]\s|\s\|\s", r["title"])[0].strip() or h
        u = urlparse(r["url"])
        origin = f"{u.scheme or 'https'}://{u.hostname}"  # the site exactly as the search result gave it (keeps www.); `h` is only for de-duplication
        cands.append({"name": name[:120], "website_url": origin, "evidence": r, "status": "suggested — not verified"})
    cands = cands[:6]
    for c in cands[:3]:  # Web Fetch: open the candidate's home page to see whether it is a real, reachable business site
        page = web_fetch.fetch(c["website_url"], stage=stage, purpose="check a competitor found by web search")
        c["page"] = {k: page.get(k) for k in ("ok", "status", "final_url", "title", "h1", "word_count", "error")}
        c["status"] = ("suggested — site opened, not verified as a competitor" if page.get("ok") else
                       f"suggested — site could not be opened ({page.get('error')})")
    for c in cands:
        research_events.emit("competitor_identified", c["name"], stage=stage, provider=res["provider"], url=c["website_url"],
                             basis="web search result" + ("; site opened" if (c.get("page") or {}).get("ok") else ""))
    return {"used": True, "reason": "", "provider": res["provider"], "fallback_from": res.get("fallback_from"), "candidates": cands,
            "query": res["results"][0]["query"] if res["results"] else ""}


QUESTION = re.compile(r"\b(how (?:much|long|to|do|does|can)|what (?:is|are|does)|why|when should|which|best|cost|price|near me)\b[^.?!]{3,90}[?.]?", re.I)


def customer_language(service: str, city: str, *, stage: str = "keywords") -> dict:
    """Customer questions and wording around one service, used to validate keyword intent. Phrases come from real result text."""
    res = search(f"{service} {city} cost questions".strip(), "keyword_intent", stage=stage, max_results=5)
    if not res["ok"]:
        return {"used": False, "reason": res["reason"], "phrases": [], "evidence": []}
    phrases, seen = [], set()
    for r in res["results"]:
        for m in QUESTION.finditer(f"{r['title']}. {r['content']}"):
            t = " ".join(m.group(0).lower().split()).strip(" .?")
            if service.lower().split()[0] in t and 3 <= len(t.split()) <= 9 and t not in seen:
                seen.add(t)
                phrases.append({"phrase": t, "url": r["url"]})
    return {"used": True, "reason": "", "provider": res["provider"], "phrases": phrases[:12], "evidence": res["results"]}


def local_market(ctx: dict, *, stage: str = "geo") -> dict:
    what = ctx.get("category") or ctx.get("business_type") or ""
    where = ctx.get("city") or ""
    if not (what and where):
        return {"used": False, "reason": "Business category or city missing.", "evidence": []}
    res = search(f"{what} market demand {where}", "local_market", stage=stage, max_results=5)
    return {"used": res["ok"], "reason": res["reason"], "evidence": res["results"]}
