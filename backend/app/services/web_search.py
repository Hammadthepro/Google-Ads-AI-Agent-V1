"""Web Search: find pages the app does not know yet (URLs, titles, snippets) through a search API.

Providers, in order: Tavily (primary; free monthly credits), then Brave Search (fallback; metered with a monthly credit). Only providers
that are configured and enabled are used. When the first one fails, the next is tried and the result says so (`provider`, `attempts`),
and every failure is recorded as a research event and in the usage ledger. Nothing here fetches pages: that is Web Fetch.
Search-engine result pages are never scraped."""
from __future__ import annotations

from datetime import timedelta

from app.adapters import registry
from app.adapters.base import AdapterError
from app.core import clock
from app.services import integrations_service, provider_calls, research_events

PROVIDERS = ("tavily", "brave_search")
LABEL = {"tavily": "Tavily", "brave_search": "Brave Search"}


def _budget(provider: str) -> int:
    try:
        return max(0, int(integrations_service.resolve(provider).get("max_searches_per_audit") or 8))
    except (TypeError, ValueError):
        return 8


def available() -> list[str]:
    return [p for p in PROVIDERS if registry.optional(p) is not None]


def search(query: str, purpose: str, *, stage: str = "", max_results: int = 5, country: str = "US") -> dict:
    """{"ok", "provider", "results": [evidence], "attempts": [{provider, ok, error_kind, message}], "reason"}. Never raises for provider problems."""
    attempts: list[dict] = []
    configured = available()
    if not configured:
        return {"ok": False, "provider": None, "results": [], "attempts": [],
                "reason": "No web search provider is configured (Tavily or Brave Search). Web search was not used."}
    for name in configured:
        adapter = registry.optional(name)
        research_events.emit("source_discovered", f"Web search ({LABEL[name]}): {query}", stage=stage, provider=name, query=query, purpose=purpose)
        req = {"q": query.lower().strip(), "n": max_results, "c": country, **({"depth": adapter.depth} if name == "tavily" else {})}
        try:
            res = provider_calls.call(name, "search", req,
                                      (lambda a=adapter: a.search(query, max_results=max_results)) if name == "tavily"
                                      else (lambda a=adapter: a.search(query, max_results=max_results, country=country)),
                                      ttl=timedelta(days=7), cost=lambda v: v.get("credits", 0), budget=_budget(name))
        except AdapterError as exc:
            attempts.append({"provider": name, "ok": False, "error_kind": exc.kind, "message": exc.message})
            research_events.emit("provider_failed" if exc.kind != "budget_exhausted" else "provider_unavailable",
                                 f"{LABEL[name]}: {exc.message}" + ("; trying the next web search provider" if name != configured[-1] else ""),
                                 stage=stage, level="warn", provider=name, error_kind=exc.kind)
            continue
        attempts.append({"provider": name, "ok": True})
        now = clock.now().isoformat()
        out = [{"query": query, "provider": name, "url": r["url"], "title": r["title"], "content": r["content"], "retrieved_at": now,
                "purpose": purpose, "confidence": "unverified_snippet"} for r in res["results"]]
        for r in out:
            research_events.emit("web_result", r["title"] or r["url"], stage=stage, provider=name, url=r["url"], purpose=purpose)
        return {"ok": True, "provider": name, "results": out, "attempts": attempts, "reason": "",
                "fallback_from": [a["provider"] for a in attempts if not a["ok"]] or None}
    return {"ok": False, "provider": None, "results": [], "attempts": attempts,
            "reason": "Every configured web search provider failed: " + "; ".join(f"{LABEL[a['provider']]} {a['error_kind']}" for a in attempts)}
