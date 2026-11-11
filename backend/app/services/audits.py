"""Audit engine: website (4a), Google Business Profile (4b), competitors (4c), keyword research (4d),
plus the read-only 90-day account audit. 4a-4c run in parallel; 4d uses their outputs."""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.adapters import registry
from app.services.keyword_labels import label_keyword
from app.adapters.base import AdapterError
from app.core import clock
from app.core.errors import IntegrationNotConfigured
from app.core.util import from_micros, normalize_term
from app.db.session import session_scope
from app.models import Audit, Business, SourceType
from app.services import accounts_service, ai_service, clients_service, crawler_service

TRUST_WORDS = ["licensed", "insured", "certified", "warranty", "guarantee", "years", "since", "review", "award",
               "family owned", "board-certified", "no fee"]

logger = logging.getLogger(__name__)


def _slug_match(service: str, page: dict) -> bool:
    n = normalize_term(service)
    hay = " ".join([normalize_term(page.get("title", "")), *[normalize_term(h) for h in page.get("h1", [])],
                    normalize_term(urlparse(page["url"]).path.replace("-", " "))])
    return n in hay or all(w in hay for w in n.split())


def content_score(page: dict, service: str, city: str) -> dict:
    text = (page.get("text_excerpt") or "").lower()
    depth = min(30, int(page.get("word_count", 0) / 20))
    cta = (10 if page.get("tel_links") else 0) + (10 if page.get("forms") else 0) + \
          (5 if any(normalize_term(service) in normalize_term(h) for h in page.get("h1", [])) else 0)
    trust = min(20, 5 * sum(1 for w in TRUST_WORDS if w in text))
    area = 15 if city and city.lower() in text else 0
    mobile = (5 if page.get("has_viewport") else 0) + (5 if page.get("phones") else 0)
    total = depth + cta + trust + area + mobile
    notes = []
    if depth < 15:
        notes.append("thin content — add detail, pricing guidance and FAQs")
    if cta < 20:
        notes.append("weak call to action above the fold (phone button + form)")
    if trust < 10:
        notes.append("few trust signals (licences, reviews, years in business)")
    if not area:
        notes.append("service-area wording missing")
    return {"url": page["url"], "service": service, "score": total,
            "breakdown": {"depth": depth, "cta_above_fold": cta, "trust": trust, "service_area": area, "mobile_contact": mobile},
            "notes": notes, "method": "deterministic DOM heuristics (mobile above-the-fold is a proxy, see KNOWN_ISSUES)"}


def page_signals(pages: list[dict], base_url: str) -> dict:
    """Deterministic, evidence-backed signals from the crawled pages (used by the website & landing page audit)."""
    text = " ".join((p.get("text_excerpt") or "").lower() for p in pages)
    links = [u.lower() for p in pages for u in p.get("internal_links", [])]
    heads = " ".join(h.lower() for p in pages for h in (p.get("h2") or []) + (p.get("h3") or []))
    return {
        "https": base_url.lower().startswith("https://"),
        "has_faq": "faq" in text or "frequently asked" in text or "?" in heads,
        "has_privacy_link": any("privacy" in u for u in links), "has_terms_link": any("terms" in u for u in links),
        "has_pricing_or_offers": bool(re.search(r"\$\s?\d|free estimate|free quote|free consultation|financing|discount|% off|coupon", text)),
        "has_testimonials": any(w in text for w in ("testimonial", "reviews", "5-star", "five star", "what our customers")),
        "has_credentials": any(w in text for w in ("licensed", "insured", "certified", "accredited", "bbb")),
        "has_process_steps": any(w in text for w in ("how it works", "our process", "step 1", "what to expect")),
        "phones_on_site": sorted({ph for p in pages for ph in p.get("phones", [])})[:10],
        "pages_with_tel_link": sum(1 for p in pages if p.get("tel_links")), "pages_with_form": sum(1 for p in pages if p.get("forms")),
        "pages_with_viewport": sum(1 for p in pages if p.get("has_viewport")), "pages": len(pages),
        "site_text_sample": text[:4000],
    }


# ------------------------------------------------------------------ 4a website
def website_audit(ctx: dict, task_id: str | None) -> dict:
    url = ctx["website_url"]
    if not url:
        return {"error": "No website URL in the business profile", "source": "NONE"}
    crawl = crawler_service.crawl(url)
    pages = crawl["pages"]
    seo_issues, titles = [], {}
    for p in pages:
        if not p["title"]:
            seo_issues.append({"url": p["url"], "issue": "Missing title tag"})
        elif p["title"] in titles:
            seo_issues.append({"url": p["url"], "issue": f"Duplicate title (also on {titles[p['title']]})"})
        titles.setdefault(p["title"], p["url"])
        if not p["meta_description"]:
            seo_issues.append({"url": p["url"], "issue": "Missing meta description"})
        if len(p["h1"]) == 0:
            seo_issues.append({"url": p["url"], "issue": "Missing H1"})
        elif len(p["h1"]) > 1:
            seo_issues.append({"url": p["url"], "issue": "Multiple H1 headings"})
    for e in crawl["errors"]:
        seo_issues.append({"url": e["url"], "issue": f"Page error {e['status'] or e['error']}"})
    with session_scope() as db:
        extraction, _ = ai_service.generate(db, "EXTRACT_SERVICES", {
            "business_name": ctx["business_name"], "known_services": ctx["service_names"],
            "pages": [{"url": p["url"], "title": p["title"], "h1": (p["h1"] or [""])[0], "h2": p["h2"][:8],
                       "text": p["text_excerpt"][:900]} for p in pages[:20]]}, task_id=task_id)
    site_services = [s.model_dump() for s in extraction.services]
    form_services = ctx["service_names"]
    page_for = {}
    for svc in form_services:
        match = next((p for p in pages if _slug_match(svc, p) and p["url"].rstrip("/") != url.rstrip("/")), None)
        if match:
            page_for[svc] = match["url"]
    services_without_page = [s for s in form_services if s not in page_for]
    not_in_form = [s["name"] for s in site_services
                   if normalize_term(s["name"]) not in {normalize_term(x) for x in form_services + ctx["excluded"]}]
    thin = [p["url"] for p in pages if p["url"] in page_for.values() and p["word_count"] < 250]
    scores = [content_score(next(p for p in pages if p["url"] == u), s, ctx["city"]) for s, u in page_for.items()]
    ps = {}
    for strategy in ("mobile", "desktop"):
        try:
            ps[strategy] = registry.get("pagespeed").run(url, strategy)
        except AdapterError as exc:
            ps[strategy] = {"error": exc.kind, "message": exc.message}
    tags = sorted({t for p in pages for t in p["google_tags"]})
    seeds = sorted({*form_services, *[s["name"] for s in site_services]} - set(ctx["excluded"]))
    signals = page_signals(pages, url)
    claims_unverified = [c for c in extraction.claims
                         if not any(normalize_term(v) in normalize_term(c) for v in ctx["verified_claims"])]
    return {
        "page_inventory": [{"url": p["url"], "title": p["title"], "h1": p["h1"], "words": p["word_count"],
                            "forms": p["forms"], "phones": p["phones"], "depth": p["depth"]} for p in pages],
        "robots_found": crawl["robots_found"], "sitemap_urls": len(crawl["sitemap_urls"]),
        "blocked_by_robots": crawl["blocked_by_robots"], "crawl_errors": crawl["errors"],
        "service_inventory": site_services, "service_areas": extraction.service_areas,
        "claims_found": extraction.claims, "claims_not_in_verified_list": claims_unverified,
        "brand_words": extraction.brand_words,
        "services_not_in_profile": not_in_form, "services_without_page": services_without_page,
        "landing_page_map": page_for, "thin_pages": thin, "seo_issues": seo_issues, "content_scores": scores,
        "pagespeed": ps, "google_tags_detected": tags, "gtm_detected": sorted({g for p in pages for g in p["gtm_containers"]}),
        "schema_types": sorted({str(s.get("@type")) for p in pages for s in p["schema"] if isinstance(s, dict)}),
        "keyword_seeds": seeds, "signals": signals, "crawled_url": url,
        "landing_page_recommendations": [f"Use {u} for '{s}' ads" for s, u in page_for.items()] +
                                        [f"Create a dedicated page for '{s}' (ads would land on the homepage)" for s in services_without_page],
        "tag_note": "Tag detection shows likely installation only; it does not prove conversions are recorded.",
        "source": SourceType.combine(crawl["source"], registry.get("pagespeed").source),
    }


# ------------------------------------------------------------------ 4b GBP
def gbp_audit(ctx: dict) -> dict:
    places = registry.get("places")
    try:
        client = places.find_business(ctx["business_name"], ctx["website_url"], ctx["address"])
    except AdapterError as exc:
        return {"error": f"Places lookup failed: {exc.kind}", "source": places.source}
    comps = []
    for c in ctx["competitors"]:
        try:
            comps.append(places.find_business(c["name"], c["website_url"]))
        except AdapterError:
            comps.append(None)
    findings = []
    valid = [c for c in comps if c]
    if client and valid:
        med = sorted(c["user_rating_count"] for c in valid)[len(valid) // 2]
        if client["user_rating_count"] < med:
            findings.append(f"Review gap: {client['user_rating_count']} reviews vs competitor median {med}")
        if (client.get("rating") or 0) < max(c.get("rating") or 0 for c in valid) - 0.3:
            findings.append(f"Rating {client.get('rating')} trails the best competitor ({max(c.get('rating') or 0 for c in valid)})")
        if client.get("photo_count", 0) < min(c.get("photo_count", 0) for c in valid):
            findings.append("Fewer photos than every competitor (Places returns a sample of photos)")
    if client and not (client.get("opening_hours") or {}).get("weekday_text"):
        findings.append("Opening hours missing on the listing")
    if not client:
        findings.append("Business listing not found on Google Maps")
    return {"client": client, "competitors": comps, "findings": findings,
            "note": "Posts, Q&A and services lists have no public API for competitors — not audited (decision D-06).",
            "source": places.source}


# ------------------------------------------------------------------ 4c competitors
def competitor_audit(ctx: dict, task_id: str | None) -> dict:
    if not ctx["competitors"]:
        return {"skipped": True, "reason": "No competitors in the business profile (decision D-05)", "source": "NONE"}
    out, client_services = [], {normalize_term(s) for s in ctx["service_names"]}
    try:
        kw_adapter = registry.get("keyword_research")
    except IntegrationNotConfigured as exc:
        kw_adapter, kw_unavailable = None, exc.message
    for c in ctx["competitors"]:
        entry = {"name": c["name"], "website_url": c["website_url"]}
        if c["website_url"]:
            crawl = crawler_service.crawl(c["website_url"], max_pages=8, max_depth=1)
            # service pages only: skip the home page and brand headings ("X — trusted in City")
            svc_pages = [p for p in crawl["pages"] if p["h1"] and " in " in p["h1"][0] and urlparse(p["url"]).path not in ("", "/")
                         and "—" not in p["h1"][0] and normalize_term(c["name"]) not in normalize_term(p["h1"][0])]
            entry["services"] = sorted({p["h1"][0].split(" in ")[0] for p in svc_pages})
            entry["content_scores"] = [content_score(p, p["h1"][0].split(" in ")[0], ctx["city"]) for p in svc_pages[:4]]
            entry["offers_on_site"] = sorted({s for p in crawl["pages"] for s in re.findall(r"[^.]*(?:\$\d+|free|24/7|warranty|no fee)[^.]*\.",
                                                                                                p["text_excerpt"], re.I)})[:6]
            entry["services_client_lacks"] = [s for s in entry["services"] if normalize_term(s) not in client_services]
            if kw_adapter is None:
                entry["keyword_ideas"], entry["keyword_error"] = [], kw_unavailable
            else:
                try:
                    ideas = kw_adapter.generate_ideas("", entry["services"][:3], page_url=c["website_url"], city=ctx["city"])
                    entry["keyword_ideas"] = [i["keyword"] for i in sorted(ideas, key=lambda x: -(x.get("avg_monthly_searches") or 0))[:15]]
                except AdapterError as exc:
                    entry["keyword_ideas"], entry["keyword_error"] = [], exc.kind
            entry["crawl_source"] = crawl["source"]
        out.append(entry)
    ads_analysis = None
    if ctx["competitor_ads_text"].strip():
        with session_scope() as db:
            parsed, _ = ai_service.generate(db, "EXTRACT_COMPETITOR_ADS", {"ads_text": ctx["competitor_ads_text"][:6000]},
                                            task_id=task_id)
        ads_analysis = {**parsed.model_dump(), "verification": "Pasted by the team — not verified as currently running."}
    return {"competitors": out, "pasted_ads_analysis": ads_analysis,
            "keyword_sources": ["Keyword Planner URL seeds (" + kw_adapter.source + ")"] if kw_adapter else [],
            "missing_sources": ["Paid competitor bidding data (Semrush or similar) not connected"],
            "source": SourceType.combine(registry.get("crawler").source, kw_adapter.source if kw_adapter else None)}


# ------------------------------------------------------------------ 4d keyword research
def keyword_research(db: Session, ctx: dict, website: dict, competitors: dict, task_id: str | None,
                     services: list[dict] | None = None, existing_keywords: dict[str, list[str]] | None = None,
                     daily_budget: float | None = None) -> dict:
    try:
        kw = registry.get("keyword_research")
    except IntegrationNotConfigured as exc:  # Keyword Planner rides on the Google Ads connection: say so, invent nothing
        return {"keywords": [], "clusters": {}, "negative_ideas": [], "overlaps": [], "unavailable": True, "not_configured": exc.provider,
                "errors": [{"service": "*", "error": exc.message}], "source": SourceType.NONE,
                "match_type_policy": "EXACT + PHRASE in main ad groups; BROAD only in an approved test ad group once conversions are recorded reliably."}
    services = services or ctx["services"]
    names = [s["name"] for s in services]
    lp_map = website.get("landing_page_map", {}) if website else {}
    rows, errors = [], []
    for s in services:
        seeds = [s["name"]] + [x for x in (website or {}).get("keyword_seeds", []) if normalize_term(s["name"]) in normalize_term(x)][:2]
        try:
            ideas = kw.generate_ideas("", seeds,  # research runs under the configured research customer, never a client account
                                       page_url=s.get("landing_page_url") or lp_map.get(s["name"], ""),
                                      city=ctx["city"])
        except AdapterError as exc:
            errors.append({"service": s["name"], "error": exc.kind})
            continue
        rows += ideas
    for comp in (competitors or {}).get("competitors", []):
        for k in comp.get("keyword_ideas", [])[:5]:
            rows.append({"keyword": k, "avg_monthly_searches": None, "competition": None, "low_top_of_page_bid_micros": None,
                         "high_top_of_page_bid_micros": None, "seed": f"competitor:{comp['name']}", "source": kw.source,
                         "metrics_unavailable": ["avg_monthly_searches", "competition", "low_top_of_page_bid_micros", "high_top_of_page_bid_micros"]})
    dedup = {}
    for r in rows:
        dedup.setdefault(normalize_term(r["keyword"]), r)
    unsure = []
    for key, r in dedup.items():
        label, svc, why = label_keyword(r["keyword"], names, ctx["excluded"])
        r.update({"label": label, "service": svc, "label_reason": why, "labelled_by": "rules"})
        if why == "partial service match":
            unsure.append(r["keyword"])
    if unsure:
        try:
            parsed, _ = ai_service.generate(db, "LABEL_KEYWORDS", {"keywords": unsure[:80], "services": names,
                                                                 "excluded_services": ctx["excluded"]}, task_id=task_id)
            for lab in parsed.labels:
                r = dedup.get(normalize_term(lab.keyword))
                if r and (lab.service in names or lab.label != "SERVICE"):
                    r.update({"label": lab.label, "service": lab.service if lab.label == "SERVICE" else None,
                              "label_reason": lab.reason or "AI label", "labelled_by": "ai"})
        except (ai_service.AIOutputInvalid, IntegrationNotConfigured) as exc:
            errors.append({"service": "*", "error": f"AI labelling unavailable: {exc.message} Rule-based labels were kept."})
    max_bid = (daily_budget / 5) if daily_budget else None
    table = []
    for r in dedup.values():
        high = (r.get("high_top_of_page_bid_micros") or 0) / 1e6
        affordable = max_bid is None or high == 0 or high <= max_bid
        keep = r["label"] != "IRRELEVANT" and ((r.get("avg_monthly_searches") or 0) >= 20 or r["seed"].startswith("competitor")) and affordable
        table.append({"keyword": r["keyword"], "label": r["label"], "service": r.get("service"),
                      "ad_group": r.get("service") if r["label"] == "SERVICE" else ("Generic" if r["label"] == "GENERIC" else None),
                      "match_types": ["EXACT", "PHRASE"] if r["label"] != "IRRELEVANT" else [],
                      "avg_monthly_searches": r.get("avg_monthly_searches"), "competition": r.get("competition"),
                      "low_top_of_page_bid_micros": r.get("low_top_of_page_bid_micros"),
                      "high_top_of_page_bid_micros": r.get("high_top_of_page_bid_micros"),
                      "bid_range": f"${from_micros(r.get('low_top_of_page_bid_micros')):.2f}–${high:.2f}" if high else "n/a",
                      "affordable": affordable, "selected": keep, "label_reason": r.get("label_reason"),
                      "labelled_by": r.get("labelled_by"), "seed": r.get("seed")})
    table.sort(key=lambda x: (x["label"] != "SERVICE", -(x["avg_monthly_searches"] or 0)))
    clusters = {}
    for t in table:
        if t["selected"] and t["ad_group"]:
            clusters.setdefault(t["ad_group"], []).append(t["keyword"])
    negatives = sorted({t["keyword"] for t in table if t["label"] == "IRRELEVANT"})
    overlaps = overlap_check(clusters, existing_keywords or {})
    return {"keywords": table, "clusters": clusters, "negative_ideas": negatives, "overlaps": overlaps, "errors": errors,
            "match_type_policy": "EXACT + PHRASE in main ad groups; BROAD only in an approved test ad group once conversions are recorded reliably.",
            "source": SourceType.combine(kw.source)}


def overlap_check(groups: dict[str, list[str]], existing: dict[str, list[str]]) -> list[dict]:
    """Same normalised keyword in two ad groups, or a phrase keyword that contains another group's keyword."""
    owners: dict[str, list[str]] = {}
    for g, kws in {**{f"(existing) {k}": v for k, v in existing.items()}, **groups}.items():
        for k in kws:
            owners.setdefault(normalize_term(k), []).append(g)
    out = [{"keyword": k, "ad_groups": sorted(set(v)), "type": "DUPLICATE"} for k, v in owners.items() if len(set(v)) > 1]
    items = [(normalize_term(k), g) for g, kws in groups.items() for k in kws]
    for a, ga in items:
        for b, gb in items:
            if ga != gb and a != b and len(a.split()) >= 2 and f" {a} " in f" {b} ":
                out.append({"keyword": b, "ad_groups": [ga, gb], "type": "PHRASE_CONTAINS", "detail": f"'{a}' (phrase) in {ga} can match '{b}'"})
    return out[:50]


# ------------------------------------------------------------------ orchestration
def build_context(db: Session, business: Business) -> dict:
    acct = accounts_service.account_for_business(db, business)
    return {"business_id": business.id, "business_name": business.name, "website_url": business.website_url,
            "address": business.address, "city": clients_service.city_of(business), "phone": business.phone,
            "service_names": [s.name for s in clients_service.active_services(business)],
            "services": [{"name": s.name, "landing_page_url": s.landing_page_url} for s in clients_service.active_services(business)],
            "excluded": clients_service.excluded_services(business), "verified_claims": business.verified_claims or [],
            "competitors": [{"name": c.name, "website_url": c.website_url} for c in business.competitors],
            "competitor_ads_text": business.competitor_ads_text or "", "customer_id": acct.customer_id if acct else "",
            "radius_miles": business.default_radius_miles, "business_type": business.business_type,
            "category": business.category or business.business_type, "service_areas": business.service_areas or [],
            "states": business.states or [], "has_gbp": business.has_gbp, "gtm_id": business.gtm_id,
            "email": business.email, "monthly_budget": business.monthly_budget_usd,
            "lead_form_url": business.lead_form_url, "thank_you_url": business.thank_you_url}


def run_parallel(db: Session, business: Business, *, task_id: str | None, actor_id: str | None,
                 focus_service: str | None = None, on_done=None) -> dict:
    ctx = build_context(db, business)
    if focus_service:
        ctx["focus_service"] = focus_service
    rows = {t: Audit(client_id=business.client_id, business_id=business.id, task_id=task_id, audit_type=t,
                     status="RUNNING", created_by_id=actor_id) for t in ("WEBSITE", "GBP", "COMPETITOR")}
    for r in rows.values():
        db.add(r)
    db.commit()  # audits visible as RUNNING while they execute
    jobs = {"WEBSITE": lambda: website_audit(ctx, task_id), "GBP": lambda: gbp_audit(ctx),
            "COMPETITOR": lambda: competitor_audit(ctx, task_id)}
    results = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {k: pool.submit(fn) for k, fn in jobs.items()}
        for k, f in futures.items():
            try:
                results[k] = f.result()
                rows[k].status = "FAILED" if results[k].get("error") else "COMPLETED"
                rows[k].error = results[k].get("error", "")
            except IntegrationNotConfigured as exc:  # a missing integration is a clear, actionable message, not a crash
                results[k] = {"error": exc.message, "not_configured": exc.provider, "source": SourceType.NONE}
                rows[k].status, rows[k].error = "FAILED", exc.message
            except Exception as exc:  # noqa: BLE001 — one failing audit must not stop the others
                logger.exception("audit %s failed", k)
                results[k] = {"error": f"{type(exc).__name__}: {str(exc)[:300]}", "source": SourceType.NONE}
                rows[k].status, rows[k].error = "FAILED", results[k]["error"]
            rows[k].result = results[k]
            rows[k].source_type = results[k].get("source", "NONE")
            rows[k].finished_at = clock.now()
            rows[k].summary = _summary(k, results[k])
            if on_done:
                on_done(k, results[k], rows[k].summary)
    db.flush()
    return {"context": ctx, "website": results["WEBSITE"], "gbp": results["GBP"], "competitor": results["COMPETITOR"],
            "audit_ids": {k: r.id for k, r in rows.items()}}


def save_keyword_audit(db: Session, business: Business, task_id: str | None, actor_id: str | None, result: dict) -> Audit:
    row = Audit(client_id=business.client_id, business_id=business.id, task_id=task_id, audit_type="KEYWORD",
                status="COMPLETED", result=result, summary=_summary("KEYWORD", result), finished_at=clock.now(),
                created_by_id=actor_id, source_type=result.get("source", "NONE"))
    db.add(row)
    db.flush()
    return row


def _summary(kind: str, r: dict) -> str:
    if r.get("error"):
        return f"Failed: {r['error']}"
    if kind == "WEBSITE":
        return (f"{len(r['page_inventory'])} pages crawled, {len(r['seo_issues'])} SEO issues, "
                f"{len(r['services_without_page'])} service(s) without a page, mobile speed "
                f"{(r['pagespeed'].get('mobile') or {}).get('performance_score', 'n/a')}")
    if kind == "GBP":
        c = r.get("client") or {}
        return f"Rating {c.get('rating', 'n/a')} from {c.get('user_rating_count', 0)} reviews; {len(r['findings'])} finding(s)"
    if kind == "COMPETITOR":
        return "Skipped — no competitors" if r.get("skipped") else f"{len(r['competitors'])} competitor(s) analysed"
    if kind == "KEYWORD":
        return f"{sum(1 for k in r['keywords'] if k['selected'])} keywords selected in {len(r['clusters'])} cluster(s), {len(r['negative_ideas'])} negative ideas, {len(r['overlaps'])} overlap(s)"
    return ""


def account_audit(db: Session, business: Business, task_id: str | None, actor_id: str | None) -> Audit:
    """Read-only 90-day account audit (Audit only task). Never writes."""
    ads = registry.get("google_ads")  # IntegrationNotConfigured propagates: an account audit cannot exist without Google Ads
    acct = accounts_service.account_for_business(db, business)
    result: dict = {"findings": []}
    if acct:
        end = clock.today() - timedelta(days=1)
        try:
            camps = {c["id"]: c for c in ads.list_campaigns(acct.customer_id)}
            metrics = ads.get_metrics(acct.customer_id, end - timedelta(days=89), end)
            terms = ads.get_search_terms(acct.customer_id, end - timedelta(days=89), end)
            per = {}
            for m in metrics:
                p = per.setdefault(m["campaign_id"], {"clicks": 0, "cost": 0, "conversions": 0, "blis": [], "rlis": []})
                p["clicks"] += m["clicks"]
                p["cost"] += m["cost_micros"] / 1e6
                p["conversions"] += m["conversions"] or 0
                p["blis"].append(m["search_budget_lost_impression_share"] or 0)
                p["rlis"].append(m["search_rank_lost_impression_share"] or 0)
            summary = []
            for cid_, p in per.items():
                c = camps.get(cid_, {})
                blis = sum(p["blis"]) / len(p["blis"]) if p["blis"] else 0
                rlis = sum(p["rlis"]) / len(p["rlis"]) if p["rlis"] else 0
                summary.append({"campaign": c.get("name", cid_), "type": c.get("advertising_channel_type"), "status": c.get("status"),
                                "clicks": p["clicks"], "cost": round(p["cost"], 2), "conversions": round(p["conversions"], 1),
                                "cost_per_conversion": round(p["cost"] / p["conversions"], 2) if p["conversions"] else None,
                                "budget_lost_is": round(blis, 3), "rank_lost_is": round(rlis, 3),
                                "read_only": c.get("advertising_channel_type") != "SEARCH"})
                if p["cost"] > 300 and p["conversions"] == 0:
                    result["findings"].append(f"{c.get('name')}: ${p['cost']:,.0f} spent with zero conversions in 90 days")
                if blis > 0.2:
                    result["findings"].append(f"{c.get('name')}: losing {blis:.0%} impression share to budget")
                if rlis > 0.4:
                    result["findings"].append(f"{c.get('name')}: losing {rlis:.0%} impression share to ad rank")
            wasted = [t for t in terms if t["clicks"] >= 10 and t["conversions"] == 0]
            result.update({"campaigns": summary, "wasted_terms": sorted(wasted, key=lambda t: -t["cost_micros"])[:25],
                           "wasted_spend": round(sum(t["cost_micros"] for t in wasted) / 1e6, 2), "window_days": 90})
        except AdapterError as exc:
            result["error"] = exc.kind
    else:
        result["error"] = "No Google Ads account linked"
    row = Audit(client_id=business.client_id, business_id=business.id, task_id=task_id, audit_type="ACCOUNT",
                status="FAILED" if result.get("error") else "COMPLETED", result=result,
                summary=f"{len(result.get('findings', []))} finding(s); wasted spend ${result.get('wasted_spend', 0):,.2f} (90 days)",
                finished_at=clock.now(), created_by_id=actor_id, source_type=ads.source)
    db.add(row)
    db.flush()
    return row


def serialize(a: Audit) -> dict:
    return {"id": a.id, "client_id": a.client_id, "business_id": a.business_id, "task_id": a.task_id, "audit_type": a.audit_type,
            "status": a.status, "summary": a.summary, "error": a.error, "result": a.result, "source_type": a.source_type,
            "started_at": a.started_at.isoformat(), "finished_at": a.finished_at.isoformat() if a.finished_at else None}
