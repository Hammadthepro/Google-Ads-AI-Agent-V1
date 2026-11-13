"""Provider-independent keyword discovery.

Sources, all optional and independent (one failing never stops the others):
  * search suggestions (Google Autocomplete, DuckDuckGo, Bing) for each service seed, seed + location and question seeds;
  * the client's own website (titles and headings of the pages crawled in the website audit);
  * competitor websites (service headings found by the competitor audit);
  * first-party Google Ads search terms already synced for the client's linked account (with their real clicks/conversions);
  * web search results (Tavily, else Brave Search) for customer questions and wording, only when configured and within the audit's budget;
  * Keyword Planner ideas, when that integration works (merged in by the caller with their real metrics).

Metrics policy: suggestion and web sources carry NO search volume, CPC or competition. Every keyword carries `metrics`, where each metric
records value, source, retrieval time, metric type, confidence and basis (observed | unavailable). `relevance_score` is this app's own
0-100 score of fit with the business; it is not search volume and is labelled as such everywhere.
"""
from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import registry
from app.adapters.base import AdapterError
from app.core.util import normalize_term
from app.models import SearchTermRow
from app.services import provider_calls, research_events, web_research

TRANSACTIONAL = ("near me", "emergency", "same day", "24 hour", "24/7", "hire", "book", "quote", "quotes", "estimate", "service", "services",
                 "company", "companies", "contractor", "repair", "install", "installation", "replacement", "cost", "price", "prices", "cheap",
                 "affordable", "best", "top", "local", "appointment", "consultation", "free estimate")
INFORMATIONAL = ("how to", "how do", "how does", "what is", "what are", "why", "diy", "guide", "tutorial", "ideas", "meaning", "definition",
                 "vs", "versus", "reddit", "youtube", "pdf", "course", "training", "jobs", "salary", "career")
QUESTION_SEEDS = ("how much does {s} cost", "{s} cost")
METRICS = ("avg_monthly_searches", "competition", "low_top_of_page_bid_micros", "high_top_of_page_bid_micros")
METRIC_TYPE = {"avg_monthly_searches": "search_volume", "competition": "competition", "low_top_of_page_bid_micros": "top_of_page_bid_low",
               "high_top_of_page_bid_micros": "top_of_page_bid_high"}
SOURCE_LABEL = {"suggest:google": "Google Autocomplete", "suggest:duckduckgo": "DuckDuckGo suggestions", "suggest:bing": "Bing suggestions",
                "client_website": "Client website", "competitor_website": "Competitor website", "search_terms": "Google Ads search terms",
                "web_search": "Web search results", "keyword_planner": "Keyword Planner"}


def intent(kw: str, brand_words: set[str] = frozenset(), competitor_names: set[str] = frozenset()) -> str:
    n = f" {normalize_term(kw)} "
    if competitor_names and any(c and f" {c} " in n for c in competitor_names):
        return "navigational"
    if brand_words and any(f" {b} " in n for b in brand_words):
        return "navigational"
    if any(f" {w} " in n for w in INFORMATIONAL) or n.strip().endswith("?"):
        return "informational"
    if any(f" {w} " in n for w in ("hire", "book", "quote", "quotes", "estimate", "appointment", "near me", "emergency", "same day", "24 hour")):
        return "transactional"
    if any(f" {w} " in n for w in TRANSACTIONAL):
        return "commercial"
    return "commercial" if len(n.split()) >= 2 else "informational"


def unavailable_metrics(reason: str) -> dict:
    return {m: {"value": None, "source": None, "retrieved_at": None, "metric_type": METRIC_TYPE[m], "confidence": None, "basis": "unavailable",
                "note": reason} for m in METRICS}


def planner_metrics(row: dict) -> dict:
    out = {}
    for m in METRICS:
        v = row.get(m)
        out[m] = ({"value": v, "source": "google_keyword_planner", "retrieved_at": row.get("retrieved_at"), "metric_type": METRIC_TYPE[m],
                   "confidence": "google_estimate", "basis": "observed"} if v is not None else
                  {"value": None, "source": "google_keyword_planner", "retrieved_at": row.get("retrieved_at"), "metric_type": METRIC_TYPE[m],
                   "confidence": None, "basis": "unavailable", "note": "Keyword Planner returned no value for this metric."})
    return out


class Bag:
    """Candidate keywords with every source that produced them."""

    def __init__(self):
        self.items: dict[str, dict] = {}

    def add(self, kw: str, source: str, *, service: str | None = None, detail: str = "", evidence_url: str = "") -> bool:
        k = normalize_term(kw)
        if not k or len(k) < 3 or len(k.split()) > 8:
            return False
        item = self.items.setdefault(k, {"keyword": " ".join(kw.lower().split()), "sources": [], "service_hint": service})
        if not any(s["source"] == source and s.get("detail") == detail for s in item["sources"]):
            item["sources"].append({"source": source, "label": SOURCE_LABEL.get(source.split(":")[0] if source.startswith("competitor") else source, source),
                                    **({"detail": detail} if detail else {}), **({"url": evidence_url} if evidence_url else {})})
        item["service_hint"] = item["service_hint"] or service
        return True


def _suggest(bag: Bag, ctx: dict, stage: str) -> dict:
    report = {"used": [], "failed": {}, "calls": 0}
    try:
        adapter = registry.get("search_suggestions")
    except Exception as exc:  # noqa: BLE001 - switched off on the Integrations page
        research_events.emit("provider_unavailable", "Search suggestions are switched off", stage=stage, level="warn", provider="search_suggestions")
        report["failed"]["*"] = getattr(exc, "message", str(exc))
        return report
    city = (ctx.get("city") or "").lower()
    for svc in (ctx.get("service_names") or [])[:8]:
        s = svc.lower()
        seeds = [s, f"{s} {city}".strip(), f"{s} near"] + [q.format(s=s) for q in QUESTION_SEEDS]
        for src in adapter.enabled:
            if src in report["failed"]:
                continue
            for seed in dict.fromkeys(seeds):
                try:
                    got = provider_calls.call(f"suggest_{src}", "suggest", {"q": seed}, lambda src=src, seed=seed: adapter.suggest(src, seed),
                                              ttl=timedelta(days=14))
                except AdapterError as exc:
                    report["failed"][src] = f"{exc.kind}: {exc.message}"
                    research_events.emit("provider_failed" if exc.kind != "unsupported" else "provider_unavailable",
                                         f"{SOURCE_LABEL[f'suggest:{src}']}: {exc.message}", stage=stage, level="warn", provider=src, error_kind=exc.kind)
                    break
                report["calls"] += 1
                added = [g for g in got if bag.add(g, f"suggest:{src}", service=svc, detail=f"seed: {seed}")]
                if src not in report["used"]:
                    report["used"].append(src)
                if added:
                    research_events.emit("keyword_discovery", f"{len(added)} suggestion(s) for “{seed}”", stage=stage, provider=src,
                                         seed=seed, examples=added[:5], count=len(added))
    return report


def _website_terms(bag: Bag, ctx: dict, website: dict, competitors: dict, stage: str) -> dict:
    services = [s.lower() for s in ctx.get("service_names") or []]
    n_site = 0
    for p in (website or {}).get("page_inventory", [])[:40]:
        for text in [p.get("title") or "", *(p.get("h1") or [])][:3]:
            t = re.split(r"\s[|–—-]\s", text)[0].strip()
            svc = next((s for s in services if s.split()[0] in t.lower()), None)
            if svc and 2 <= len(t.split()) <= 7 and bag.add(t, "client_website", service=svc, detail=p.get("url", ""), evidence_url=p.get("url", "")):
                n_site += 1
    for seed in (website or {}).get("keyword_seeds", [])[:30]:
        if bag.add(seed, "client_website", detail="website keyword seed"):
            n_site += 1
    n_comp = 0
    for c in (competitors or {}).get("competitors", []):
        for svc_name in (c.get("services") or [])[:12]:
            if bag.add(svc_name, f"competitor_website:{c['name']}", detail=c.get("website_url", ""), evidence_url=c.get("website_url", "")):
                n_comp += 1
    if n_site or n_comp:
        research_events.emit("keyword_discovery", f"{n_site} term(s) from the client website, {n_comp} from competitor websites", stage=stage,
                             provider="crawler", count=n_site + n_comp)
    return {"client_website": n_site, "competitor_websites": n_comp}


def _search_terms(db: Session, bag: Bag, ctx: dict, stage: str) -> dict:
    client_id = ctx.get("client_id")
    if not client_id:
        return {"available": False, "reason": "No client ID in context."}
    rows = list(db.scalars(select(SearchTermRow).where(SearchTermRow.client_id == client_id).order_by(SearchTermRow.clicks.desc()).limit(300)))
    if not rows:
        return {"available": False, "reason": "No synced Google Ads search terms for this client (no linked account history)."}
    first_party = {}
    for r in rows:
        bag.add(r.term, "search_terms", detail=f"{r.clicks} clicks, {r.conversions:g} conversions in {r.window_days} days")
        k = normalize_term(r.term)
        prev = first_party.get(k, {"clicks": 0, "impressions": 0, "conversions": 0.0, "cost_micros": 0, "window_days": r.window_days,
                                   "retrieved_at": r.fetched_at.isoformat() if r.fetched_at else None})
        prev["clicks"] += r.clicks
        prev["impressions"] += r.impressions
        prev["conversions"] += r.conversions
        prev["cost_micros"] += r.cost_micros
        first_party[k] = prev
    research_events.emit("keyword_discovery", f"{len(first_party)} search term(s) from the client's Google Ads history", stage=stage,
                         provider="google_ads", count=len(first_party))
    return {"available": True, "terms": len(first_party), "metrics": first_party}


def _web(bag: Bag, ctx: dict, stage: str) -> dict:
    ok, why = web_research.available()
    if not ok:
        return {"used": False, "reason": why}
    out = {"used": True, "services": {}, "evidence": []}
    for svc in (ctx.get("service_names") or [])[:4]:
        res = web_research.customer_language(svc, ctx.get("city") or "", stage=stage)
        if not res["used"]:
            out["services"][svc] = res["reason"]
            if res.get("reason", "").startswith("The per-audit budget"):
                break
            continue
        n = sum(1 for ph in res["phrases"] if bag.add(ph["phrase"], "web_search", service=svc, detail=f"phrase in a {res.get('provider') or 'web'} search result",
                                                      evidence_url=ph["url"]))
        out["services"][svc] = f"{n} phrase(s)"
        out["evidence"] += res["evidence"]
    return out


def discover(db: Session, ctx: dict, website: dict, competitors: dict, *, stage: str = "keywords") -> dict:
    """Candidate keywords from every free source, with provenance. Labelling, scoring and grouping happen in `enrich`."""
    bag = Bag()
    for svc in ctx.get("service_names") or []:
        bag.add(svc, "client_profile", service=svc, detail="service in the client profile")
        if ctx.get("city"):
            bag.add(f"{svc} {ctx['city']}", "client_profile", service=svc, detail="service + location")
    report = {"suggestions": _suggest(bag, ctx, stage), "websites": _website_terms(bag, ctx, website, competitors, stage),
              "search_terms": _search_terms(db, bag, ctx, stage), "web": _web(bag, ctx, stage)}
    return {"candidates": bag.items, "report": report}


def relevance(kw: str, *, label: str, kw_intent: str, sources: list[dict], services: list[str], places: list[str]) -> tuple[int, list[str]]:
    """This app's 0-100 fit score. NOT search volume: it combines label, intent, location and how many independent sources agree."""
    n = f" {normalize_term(kw)} "
    score, why = 0, []
    if label == "SERVICE":
        score += 45; why.append("names a service the client offers")
    elif label == "GENERIC":
        score += 25; why.append("generic category term")
    else:
        return 0, ["irrelevant or excluded service"]
    if kw_intent == "transactional":
        score += 25; why.append("transactional intent")
    elif kw_intent == "commercial":
        score += 15; why.append("commercial intent")
    elif kw_intent == "informational":
        score -= 20; why.append("informational intent")
    if any(p and f" {normalize_term(p)} " in n for p in places) or " near me " in n:
        score += 10; why.append("local modifier")
    kinds = {s["source"].split(":")[0] for s in sources}
    if len(kinds) >= 2:
        score += min(15, 5 * len(kinds)); why.append(f"{len(kinds)} independent sources")
    if "search_terms" in kinds:
        score += 10; why.append("already searched by real customers of this account")
    return max(0, min(100, score)), why
