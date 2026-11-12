"""Tavily-backed web research used by the agent only when it adds something the other sources lack.

Every result is stored as web evidence (query, provider, URL, title, snippet, retrieval time, purpose) with confidence
"unverified_snippet": a search snippet is never treated as a verified fact about a business. Searches are cached for 7 days,
identical queries are never repeated, and each audit has a configurable search budget. Without Tavily the agent carries on.
"""
from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import urlparse

from app.adapters import registry
from app.adapters.base import AdapterError
from app.core import clock
from app.services import integrations_service, provider_calls, research_events

DIRECTORIES = ("yelp.", "angi.", "angieslist", "yellowpages", "bbb.org", "facebook.", "google.", "thumbtack", "homeadvisor", "nextdoor",
               "reddit.", "wikipedia.", "youtube.", "linkedin.", "instagram.", "x.com", "twitter.", "tripadvisor", "mapquest", "porch.com",
               "houzz.", "expertise.com", "threebestrated", "manta.com", "chamberofcommerce", "superpages", "bark.com", "networx")


def available() -> tuple[bool, str]:
    adapter = registry.optional("tavily")
    if adapter is None:
        return False, "Tavily is not configured; web search was not used."
    return True, ""


def _budget() -> int:
    try:
        return max(0, int(integrations_service.resolve("tavily").get("max_searches_per_audit") or 8))
    except (TypeError, ValueError):
        return 8


def search(query: str, purpose: str, *, stage: str = "", max_results: int = 5) -> dict:
    """{"ok", "results": [evidence...], "reason"} — never raises for provider problems; they are recorded and reported."""
    adapter = registry.optional("tavily")
    if adapter is None:
        return {"ok": False, "results": [], "reason": "Tavily is not configured"}
    research_events.emit("source_discovered", f"Web search: {query}", stage=stage, provider="tavily", query=query, purpose=purpose)
    try:
        res = provider_calls.call("tavily", "search", {"q": query.lower().strip(), "n": max_results, "depth": adapter.depth},
                                  lambda: adapter.search(query, max_results=max_results), ttl=timedelta(days=7),
                                  cost=lambda v: v.get("credits", 0), budget=_budget())
    except AdapterError as exc:
        research_events.emit("provider_failed" if exc.kind != "budget_exhausted" else "provider_unavailable",
                             f"Tavily: {exc.message}", stage=stage, level="warn", provider="tavily", error_kind=exc.kind)
        return {"ok": False, "results": [], "reason": exc.message, "error_kind": exc.kind}
    now = clock.now().isoformat()
    out = []
    for r in res["results"]:
        ev = {"query": query, "provider": "tavily", "url": r["url"], "title": r["title"], "content": r["content"], "retrieved_at": now,
              "purpose": purpose, "confidence": "unverified_snippet"}
        out.append(ev)
        research_events.emit("web_result", r["title"] or r["url"], stage=stage, provider="tavily", url=r["url"], purpose=purpose)
    return {"ok": True, "results": out, "reason": ""}


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
        cands.append({"name": name[:120], "website_url": f"https://{h}", "evidence": r, "status": "suggested — not verified"})
        research_events.emit("competitor_identified", name[:120], stage=stage, provider="tavily", url=f"https://{h}", basis="web search result")
    return {"used": True, "reason": "", "candidates": cands[:6], "query": res["results"][0]["query"] if res["results"] else ""}


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
    return {"used": True, "reason": "", "phrases": phrases[:12], "evidence": res["results"]}


def local_market(ctx: dict, *, stage: str = "geo") -> dict:
    what = ctx.get("category") or ctx.get("business_type") or ""
    where = ctx.get("city") or ""
    if not (what and where):
        return {"used": False, "reason": "Business category or city missing.", "evidence": []}
    res = search(f"{what} market demand {where}", "local_market", stage=stage, max_results=5)
    return {"used": res["ok"], "reason": res["reason"], "evidence": res["results"]}
