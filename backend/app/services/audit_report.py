"""Five-part, evidence-based Google Ads audit built on the existing audit pipeline (services/audits.py).

  1 Client & business · 2 Website & landing pages · 3 Google Business Profile · 4 Competitors · 5 Keywords

Every finding carries:
  evidence  VERIFIED (confirmed from a source) | ESTIMATED (tool/indicator based) | AI_GENERATED (hypothesis to validate)
            | UNAVAILABLE (could not be accessed) | NOT_APPLICABLE
  status    PASS | WARN | FAIL | INFO | UNAVAILABLE | NA
  source + date, and `simulated` when the underlying data came from a mock adapter.
Scores are computed from findings only (weight × credit); UNAVAILABLE / N/A / INFO checks are excluded from the
denominator and reported as coverage, never silently counted as passes or failures. Nothing here is fabricated:
a metric the sources did not return is labelled unavailable.
"""
from __future__ import annotations

import hashlib
import re
import threading
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import registry
from app.adapters.base import AdapterError
from app.core import clock
from app.core.errors import Conflict, NotFound
from app.core.util import canonical_json, normalize_term
from app.db.session import SessionLocal, session_scope
from app.models import AuditReport, Business, Client
from app.services import audits, clients_service, crawler_service

SECTIONS = [("client", "Client & business"), ("website", "Website & landing pages"), ("gbp", "Google Business Profile"),
            ("competitors", "Competitor analysis"), ("keywords", "Keyword research")]
CREDIT = {"PASS": 1.0, "WARN": 0.5, "FAIL": 0.0}
SEVERITY_ORDER = ["critical", "high", "medium", "low"]
INTENT_WORDS = {
    "informational": ("how to", "how do", "what is", "why ", "diy", "guide", "tutorial", "tips", "vs ", "meaning", "causes"),
    "transactional": ("near me", "quote", "hire", "emergency", "same day", "24 hour", "24/7", "book", "schedule", "appointment",
                      "repair", "replacement", "installation", "install", "company", "contractor", "service", "provider", "cost", "price", "estimate"),
    "commercial": ("best", "top", "reviews", "affordable", "cheap", "compare", "cost of"),
}
JUNK = ("jobs", "job", "career", "careers", "hiring", "salary", "diy", "free", "course", "courses", "training", "school", "certification",
        "how to", "tutorial", "definition", "wikipedia", "reddit", "youtube", "template")
MODIFIERS = ["near me", "emergency", "cost", "company", "service", "provider", "professional", "same day", "{city}"]


def _today() -> str:
    return clock.today().isoformat()


def digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")[-10:]


class Collector:
    def __init__(self, section: str, simulated_default: bool):
        self.section, self.items, self.sim = section, [], simulated_default

    def add(self, fid: str, area: str, check: str, status: str, evidence: str, finding: str, *, source: str = "", weight: int = 1,
            rec: str = "", needs: str | None = None, sim: bool | None = None, data: dict | None = None) -> None:
        sev = None
        if status in ("FAIL", "WARN"):
            sev = ("critical" if weight >= 3 else "high" if weight == 2 else "medium") if status == "FAIL" else \
                  ("high" if weight >= 3 else "medium" if weight == 2 else "low")
        self.items.append({"id": f"{self.section}.{fid}", "section": self.section, "area": area, "check": check, "status": status,
                           "evidence": evidence, "finding": finding, "source": source, "date": _today(), "weight": weight,
                           "severity": sev, "recommendation": rec, "needs": needs,
                           "simulated": self.sim if sim is None else sim, **({"data": data} if data else {})})

    def unavailable(self, fid, area, check, why, *, needs="manual", rec=""):
        self.add(fid, area, check, "UNAVAILABLE", "UNAVAILABLE", why, weight=1, needs=needs, rec=rec, sim=False)

    def na(self, fid, area, check, why):
        self.add(fid, area, check, "NA", "NOT_APPLICABLE", why, sim=False)


def score(items: list[dict]) -> dict:
    app = [i for i in items if i["status"] in CREDIT]
    unav = [i for i in items if i["status"] == "UNAVAILABLE"]
    total = sum(i["weight"] for i in app)
    got = sum(i["weight"] * CREDIT[i["status"]] for i in app)
    return {"score": round(100 * got / total) if total else None, "checks_scored": len(app), "checks_unavailable": len(unav),
            "checks_not_applicable": sum(1 for i in items if i["status"] == "NA"),
            "coverage": round(len(app) / (len(app) + len(unav)), 2) if (app or unav) else None,
            "passed": sum(1 for i in app if i["status"] == "PASS"), "warned": sum(1 for i in app if i["status"] == "WARN"),
            "failed": sum(1 for i in app if i["status"] == "FAIL")}


SCORING_CRITERIA = ("Each applicable check has a weight (1–3) and earns full credit for PASS, half for WARN and none for FAIL. "
                    "A section score is earned weight ÷ total applicable weight × 100. Checks that are UNAVAILABLE (data could not be "
                    "accessed), NOT APPLICABLE to this business, or purely informational are excluded from the score and shown as "
                    "coverage instead. The overall score is the weighted average of the section scores that could be calculated "
                    "(client 25%, website 25%, business profile 15%, competitors 15%, keywords 20%).")
OVERALL_WEIGHTS = {"client": 25, "website": 25, "gbp": 15, "competitors": 15, "keywords": 20}


# ======================================================================================== section 1
def audit_client(biz: Business, client: Client, ctx: dict, kw: dict, website: dict) -> list[dict]:
    c = Collector("client", simulated_default=False)
    src = f"Client intake form ({client.client_code})"
    active = [s.name for s in clients_service.active_services(biz)]
    excluded = clients_service.excluded_services(biz)
    c.add("services", "A. Business understanding", "Primary products and services identified", "PASS" if active else "FAIL", "VERIFIED",
          f"{len(active)} service(s): {', '.join(active) or 'none'}", source=src, weight=3, rec="Add the services the client sells.")
    c.add("promote", "A. Business understanding", "Services the client wants to promote", "PASS" if active else "FAIL", "VERIFIED",
          "All listed services are treated as promotable (each becomes its own ad group).", source=src, weight=1)
    c.unavailable("profitable", "A. Business understanding", "Most profitable services identified",
                  "The form does not capture profitability per service.", needs="client",
                  rec="Ask the client which services earn the most profit so budget can favour them.")
    c.add("excluded", "A. Business understanding", "Services that must not be advertised", "PASS" if excluded else "INFO", "VERIFIED",
          f"Excluded: {', '.join(excluded)}" if excluded else "No excluded services recorded — confirm with the client that every listed service may be advertised.",
          source=src, needs=None if excluded else "client")
    c.unavailable("audience", "A. Business understanding", "Target audience and ideal customer", "Not captured by the intake form.", needs="client",
                  rec="Ask who the ideal customer is (homeowner/business, age, need).")
    areas = biz.service_areas or []
    c.add("locations", "A. Business understanding", "Target locations defined", "PASS" if areas else "FAIL", "VERIFIED",
          f"{len(areas)} area(s): {', '.join(a['label'] for a in areas[:6])}; states: {', '.join(biz.states) or 'nationwide'}; mode: {biz.targeting_mode}",
          source=src, weight=3, rec="Add at least one service area.")
    if biz.targeting_mode == "AREAS" and not biz.states and any(a.get("city") for a in areas):
        c.add("states", "A. Business understanding", "State(s) limit location targeting", "WARN", "VERIFIED",
              "No state is set, so same-named cities in other states could be targeted.", source=src, weight=1,
              rec="Add the state(s) so ads do not show in same-named cities elsewhere.")
    c.add("usp", "A. Business understanding", "Unique selling points (key selling points) provided",
          "PASS" if len(biz.verified_claims or []) >= 3 else "WARN" if biz.verified_claims else "FAIL", "VERIFIED",
          f"{len(biz.verified_claims or [])} selling point(s): {'; '.join((biz.verified_claims or [])[:5])}", source=src, weight=2,
          rec="Collect at least 3 verifiable selling points; ads may only use these claims.")
    sig = (website or {}).get("signals") or {}
    if sig:
        c.add("pricing", "A. Business understanding", "Pricing, packages or offers visible on the website",
              "PASS" if sig.get("has_pricing_or_offers") else "WARN", "ESTIMATED",
              "Offer/pricing wording found on crawled pages." if sig.get("has_pricing_or_offers") else "No pricing, estimate or offer wording found on the crawled pages.",
              source=ctx["website_url"], weight=1, sim=(website or {}).get("source") == "MOCK",
              rec="Ask the client about current offers (free estimate, financing, discounts) to use in ads and on landing pages.")
    c.add("model", "A. Business understanding", "Business model identified", "INFO", "ESTIMATED",
          f"Local service business (inferred from category '{biz.category}' and {len(areas)} service area(s)); B2C/B2B mix not confirmed.",
          source=src, needs="client", sim=False)
    c.add("objective", "A. Business understanding", "Primary business objective", "PASS", "VERIFIED",
          "Lead generation (calls and form leads) — fixed for Google Search campaigns in this app.", source="App configuration", sim=False)
    c.add("budget", "A. Business understanding", "Monthly advertising budget provided", "PASS" if biz.monthly_budget_usd > 0 else "FAIL", "VERIFIED",
          f"${biz.monthly_budget_usd:,.0f}/month" if biz.monthly_budget_usd else "No budget.", source=src, weight=3, rec="Set a monthly budget.")
    c.add("conversion_path", "A. Business understanding", "Lead handling path (phone and/or form) defined",
          "PASS" if biz.phone and (biz.lead_form_url or biz.email) else "WARN" if biz.phone or biz.lead_form_url else "FAIL", "VERIFIED",
          f"Phone: {biz.phone or '—'} · Lead form: {biz.lead_form_url or '—'} · Email: {biz.email or '—'}", source=src, weight=3,
          rec="Provide a phone number and a lead form page so both call and form leads can be measured.")
    c.add("thank_you", "A. Business understanding", "Thank-you page set (needed to count form leads)", "PASS" if biz.thank_you_url else "WARN", "VERIFIED",
          biz.thank_you_url or "No thank-you page: the launch checklist will flag form-lead tracking.", source=src, weight=2,
          rec="Create a thank-you page shown after the form is submitted.")
    for fid, label in (("sales_process", "Sales process and how leads are handled"), ("seasonality", "Seasonal demand and peak periods"),
                       ("capacity", "Operating capacity and service availability"), ("not_served", "Geographic areas the business does not serve"),
                       ("growth", "Most important business goals and growth opportunities")):
        c.unavailable(fid, "A. Business understanding", label, "Not captured by the intake form.", needs="client",
                      rec=f"Ask the client: {label.lower()}.")
    for fid, label in (("aov", "Average order / service value"), ("margin", "Estimated gross profit margin"), ("cac", "Customer acquisition cost"),
                       ("cpl", "Acceptable cost per lead"), ("cpa", "Acceptable cost per acquisition"), ("lead_to_customer", "Lead-to-customer conversion rate"),
                       ("ltv", "Customer lifetime value"), ("repeat", "Repeat purchase / recurring potential")):
        c.unavailable(fid, "B. Business economics", label, "Not provided. Economics are needed to judge profitability; none are estimated here.",
                      needs="client", rec=f"Collect from the client: {label.lower()}.")
    rows = (kw or {}).get("keywords", [])
    bids = [(r["low_top_of_page_bid_micros"] + r["high_top_of_page_bid_micros"]) / 2e6 for r in rows
            if r.get("selected") and r.get("high_top_of_page_bid_micros")]
    if biz.monthly_budget_usd and bids:
        avg = sum(bids) / len(bids)
        clicks = biz.monthly_budget_usd / avg
        c.add("budget_realism", "B. Business economics", "Budget realistic for the market (estimate)",
              "PASS" if clicks >= 100 else "WARN" if clicks >= 40 else "FAIL", "ESTIMATED",
              f"${biz.monthly_budget_usd:,.0f}/month at an average top-of-page bid of ~${avg:,.2f} buys roughly {clicks:,.0f} clicks/month "
              f"(selected keywords, Keyword Planner {(kw or {}).get('source', 'MOCK')}). An estimate, not a forecast.",
              source="Keyword Planner top-of-page bids", weight=3, sim=(kw or {}).get("source") == "MOCK",
              rec="Below ~100 clicks/month, learning and optimisation are unreliable: raise the budget or narrow services/areas.")
    else:
        c.unavailable("budget_realism", "B. Business economics", "Budget realistic for the market (estimate)", "No keyword bid data was returned.", needs="manual")
    c.add("note_volume", "B. Business economics", "High search volume is not the same as value", "INFO", "VERIFIED",
          "Keywords are prioritised by service relevance and intent first; volume is only a tie-breaker. Per-service profitability is unknown until the client provides it.",
          source="Audit method", sim=False)
    return c.items


# ======================================================================================== section 2
def audit_website(biz: Business, ctx: dict, w: dict) -> list[dict]:
    c = Collector("website", simulated_default=(w or {}).get("source") != "LIVE")
    if not w or w.get("error"):
        c.unavailable("site", "A. Content", "Website could be audited", (w or {}).get("error", "No website data"), needs="manual",
                      rec="Check the website URL and run the audit again.")
        return c.items
    url, sig = ctx["website_url"], w.get("signals", {})
    pages = w["page_inventory"]
    c.add("pages", "A. Content", "Pages crawled", "PASS" if len(pages) >= 5 else "WARN", "VERIFIED",
          f"{len(pages)} pages crawled (robots.txt {'found' if w['robots_found'] else 'missing'}, sitemap URLs: {w['sitemap_urls']}).",
          source=url, weight=1, rec="A very small site gives ads little to land on; plan dedicated service pages.")
    n = len(ctx["service_names"])
    miss = w["services_without_page"]
    c.add("service_pages", "A. Content", "Each important service has its own dedicated page", "PASS" if not miss else "FAIL" if len(miss) == n else "WARN",
          "VERIFIED", "Every service has a page." if not miss else f"No dedicated page for: {', '.join(miss)}", source=url, weight=3,
          rec="Create a dedicated landing page per service; ads for these services would otherwise land on the home page.")
    thin = w["thin_pages"]
    c.add("thin", "A. Content", "Service descriptions are detailed (≥250 words)", "PASS" if not thin else "WARN", "VERIFIED",
          "All service pages have enough copy." if not thin else f"Thin service pages: {', '.join(thin[:4])}", source=url, weight=2,
          rec="Expand thin pages with benefits, pricing guidance, FAQs and trust signals.")
    cnt = Counter(i["issue"].split(" (")[0] for i in w["seo_issues"])
    for key, label, wt in (("Missing title tag", "Page titles present", 2), ("Missing meta description", "Meta descriptions present", 1),
                           ("Missing H1", "Each page has one H1", 2), ("Multiple H1 headings", "Headings structured (single H1)", 1)):
        c.add("seo." + key.lower().replace(" ", "_"), "A. Content", label, "PASS" if not cnt.get(key) else "WARN", "VERIFIED",
              "No issues." if not cnt.get(key) else f"{cnt[key]} page(s): {key}", source=url, weight=wt, rec=f"Fix: {key.lower()} on the listed pages.")
    scores = w.get("content_scores", [])
    if scores:
        avg = sum(s_["score"] for s_ in scores) / len(scores)
        c.add("content_quality", "A. Content", "Service page content quality (depth, CTA, trust, service area)",
              "PASS" if avg >= 70 else "WARN" if avg >= 50 else "FAIL", "ESTIMATED",
              f"Average heuristic score {avg:.0f}/100 across {len(scores)} service page(s). Weakest: " +
              "; ".join(f"{s_['url'].rstrip('/').split('/')[-1]} {s_['score']}" for s_ in sorted(scores, key=lambda x: x["score"])[:2]),
              source=url, weight=2, rec="Work through each page's notes (thin content, weak CTA, few trust signals, missing area wording).",
              data={"pages": scores})
        area_ok = sum(1 for s_ in scores if s_["breakdown"]["service_area"])
        c.add("geo_content", "A. Content", "Content mentions the targeted location", "PASS" if area_ok == len(scores) else "WARN", "ESTIMATED",
              f"{area_ok} of {len(scores)} service pages mention '{ctx['city']}'.", source=url, weight=1, rec="Add the service area to headings and copy.")
    c.add("faq", "A. Content", "FAQs available", "PASS" if sig.get("has_faq") else "WARN", "ESTIMATED",
          "FAQ content detected." if sig.get("has_faq") else "No FAQ content detected on the crawled pages.", source=url, weight=1,
          rec="Add FAQs to the main service pages to answer objections.")
    c.add("process", "A. Content", "Business process explained", "PASS" if sig.get("has_process_steps") else "WARN", "ESTIMATED",
          "Process wording found." if sig.get("has_process_steps") else "No 'how it works / our process' wording found.", source=url, weight=1,
          rec="Explain what happens after the customer calls (steps, timing).")
    nf = w["services_not_in_profile"]
    c.add("consistency", "A. Content", "Website offerings match the client profile", "PASS" if not nf else "WARN", "ESTIMATED",
          "No extra services found on the site." if not nf else f"Site mentions services not in the profile: {', '.join(nf[:5])}", source=url, weight=1,
          needs="client" if nf else None, rec="Confirm with the client whether these services should be advertised.", sim=w.get("source") != "LIVE")
    c.unavailable("editorial", "A. Content", "Originality, grammar, objection handling, outdated pages, audience focus",
                  "Needs human reading; the audit does not judge writing quality.", needs="manual",
                  rec="Have a person review the key service pages for originality, accuracy and tone.")
    # landing page experience
    tel, form, vp, np_ = sig.get("pages_with_tel_link", 0), sig.get("pages_with_form", 0), sig.get("pages_with_viewport", 0), max(sig.get("pages", 1), 1)
    lp = w.get("landing_page_map", {})
    c.add("lp_map", "B. Landing pages", "Each ad group has a matching landing page", "PASS" if lp and not miss else "WARN" if lp else "FAIL", "VERIFIED",
          "; ".join(f"{k} → {v}" for k, v in lp.items()) or "No service-specific landing pages found.", source=url, weight=3,
          rec="Map every service to a dedicated page before building ads.")
    cta = [s_ for s_ in scores if s_["breakdown"]["cta_above_fold"] >= 20]
    if scores:
        c.add("cta", "B. Landing pages", "Clear CTA (phone button and form) on landing pages", "PASS" if len(cta) == len(scores) else "WARN" if cta else "FAIL",
              "ESTIMATED", f"{len(cta)} of {len(scores)} landing pages have both a click-to-call link and a form (DOM heuristic; above-the-fold is not rendered).",
              source=url, weight=3, rec="Put a click-to-call button and a short form near the top of each landing page.")
    c.add("tel", "B. Landing pages", "Phone numbers are clickable on mobile", "PASS" if tel >= max(1, np_ // 2) else "WARN" if tel else "FAIL", "VERIFIED",
          f"{tel} of {np_} crawled pages contain a tel: link.", source=url, weight=2, rec="Make phone numbers tel: links.")
    c.add("forms", "B. Landing pages", "Contact forms present", "PASS" if form else "FAIL", "VERIFIED",
          f"{form} of {np_} crawled pages contain a form.", source=url, weight=2, rec="Add a lead form to the landing pages.")
    c.add("mobile_meta", "B. Landing pages", "Mobile viewport configured", "PASS" if vp == np_ else "WARN" if vp else "FAIL", "VERIFIED",
          f"{vp} of {np_} pages declare a viewport.", source=url, weight=2, rec="Add a responsive viewport meta tag.")
    for fid, label in (("popups", "Intrusive pop-ups or distractions"), ("form_fields", "Forms ask only for necessary information"),
                       ("ad_match", "Landing page matches the ad's promise and search intent"), ("cta_fold", "CTA visible without excessive scrolling"),
                       ("broken_buttons", "Broken buttons or forms")):
        c.unavailable(fid, "B. Landing pages", label, "Needs a rendered visit or the final ad copy; checked by a person or at the campaign draft stage.", needs="manual")
    # technical
    c.add("https", "C. Technical & speed", "HTTPS/SSL", "PASS" if sig.get("https") else "FAIL", "VERIFIED", f"Site URL is {'HTTPS' if sig.get('https') else 'not HTTPS'}.",
          source=url, weight=3, rec="Serve the whole site over HTTPS.")
    for strat, wt in (("mobile", 3), ("desktop", 2)):
        ps = w["pagespeed"].get(strat, {})
        if ps.get("error") or "performance_score" not in ps:
            c.unavailable(f"speed_{strat}", "C. Technical & speed", f"{strat.title()} page speed", f"PageSpeed unavailable ({ps.get('error', 'no data')}).", needs="manual")
            continue
        sc = ps["performance_score"]
        c.add(f"speed_{strat}", "C. Technical & speed", f"{strat.title()} page speed (PageSpeed Insights)", "PASS" if sc >= 70 else "WARN" if sc >= 50 else "FAIL",
              "VERIFIED", f"Performance score {sc}/100.", source="Google PageSpeed Insights", weight=wt, sim=w["pagespeed"].get(strat, {}).get("source") != "LIVE",
              rec="Compress images, remove unused scripts/widgets, defer render-blocking resources.", data={"opportunities": ps.get("opportunities", [])[:5]})
    cwv = (w["pagespeed"].get("mobile") or {}).get("core_web_vitals")
    if cwv:
        for key, label, ok, bad in (("lcp_seconds", "LCP", 2.5, 4.0), ("inp_ms", "INP", 200, 500), ("cls", "CLS", 0.1, 0.25)):
            v = cwv.get(key)
            if v is None:
                c.unavailable("cwv_" + key, "C. Technical & speed", f"Core Web Vital {label}", "Not returned.", needs="manual")
            else:
                c.add("cwv_" + key, "C. Technical & speed", f"Core Web Vital {label} (mobile)", "PASS" if v <= ok else "WARN" if v <= bad else "FAIL", "VERIFIED",
                      f"{label} = {v}", source="Google PageSpeed Insights", weight=2, sim=w["pagespeed"]["mobile"].get("source") != "LIVE",
                      rec=f"Improve {label} (good ≤ {ok}).")
    errs = w["crawl_errors"]
    c.add("broken", "C. Technical & speed", "No broken pages (4xx/5xx)", "PASS" if not errs else "FAIL", "VERIFIED",
          "No crawl errors." if not errs else f"{len(errs)} error(s): " + "; ".join(f"{e['url']} ({e.get('status') or e.get('error')})" for e in errs[:3]),
          source=url, weight=2, rec="Fix or redirect broken pages before sending ad traffic.")
    tags, gtm = w["google_tags_detected"], w["gtm_detected"]
    given = ctx.get("gtm_id") or ""
    detected = {*tags, *gtm}
    if given:
        c.add("tag", "C. Technical & speed", "Provided Google tag / GTM ID found on the site", "PASS" if given in detected else "FAIL", "VERIFIED",
              f"{given} {'found' if given in detected else 'NOT found'} on the crawled pages (detected: {', '.join(sorted(detected)) or 'none'}).",
              source=url, weight=3, rec="Confirm the tag is installed on every page; detection shows installation only, not that conversions fire.")
    else:
        c.add("tag", "C. Technical & speed", "Google tag installed", "PASS" if detected else "WARN", "VERIFIED",
              f"Detected: {', '.join(sorted(detected))}" if detected else "No Google tag or GTM container detected.", source=url, weight=2,
              rec="Install the Google tag / GTM and set up conversion tracking.")
    for fid, label, u in (("lead_form_reach", "Lead form page reachable", ctx.get("lead_form_url")), ("thank_you_reach", "Thank-you page reachable", ctx.get("thank_you_url"))):
        if u:
            r = crawler_service.check_url(u)
            c.add(fid, "C. Technical & speed", label, "PASS" if r["ok"] else "FAIL", "VERIFIED", f"{u} → HTTP {r['status'] or r['error']}", source=u,
                  weight=2, sim=r["source"] != "LIVE", rec="Fix the page or the URL in the client profile.")
    c.unavailable("manual_tech", "C. Technical & speed", "Redirect chains, cross-browser rendering, working form submission, third-party script weight, accessibility",
                  "Needs browser-level testing (e.g. Lighthouse, GTmetrix, a test form submission).", needs="manual",
                  rec="Run Lighthouse/GTmetrix and submit a test lead.")
    # trust
    c.add("privacy", "D. Trust & compliance", "Privacy policy available", "PASS" if sig.get("has_privacy_link") else "WARN", "VERIFIED",
          "Privacy link found." if sig.get("has_privacy_link") else "No privacy-policy link found in crawled pages.", source=url, weight=2,
          rec="Add a privacy policy (required for Google Ads data collection).")
    c.add("terms", "D. Trust & compliance", "Terms and conditions (where applicable)", "PASS" if sig.get("has_terms_link") else "INFO", "VERIFIED",
          "Terms link found." if sig.get("has_terms_link") else "No terms link found; may not be needed for this business.", source=url)
    ph = {digits(p) for p in sig.get("phones_on_site", [])}
    c.add("nap_phone", "D. Trust & compliance", "Phone number on site matches the profile", "PASS" if digits(biz.phone) in ph else "WARN" if ph else "FAIL", "VERIFIED",
          f"Profile {biz.phone}; on site: {', '.join(sig.get('phones_on_site', [])[:3]) or 'none found'}", source=url, weight=2,
          rec="Make the website phone match the number used in ads and the Business Profile.")
    city_txt = (ctx.get("city") or "").lower()
    c.add("address", "D. Trust & compliance", "Business location displayed", "PASS" if city_txt and city_txt in sig.get("site_text_sample", "") else "WARN", "ESTIMATED",
          "Location wording found." if city_txt and city_txt in sig.get("site_text_sample", "") else "City not found in the sampled page text.", source=url, weight=1,
          rec="Show the business address or service area clearly.")
    unv = w["claims_not_in_verified_list"]
    c.add("claims", "D. Trust & compliance", "No unsupported claims or guarantees", "PASS" if not unv else "WARN", "ESTIMATED",
          "All claims found are in the verified list." if not unv else f"Claims on the site not verified by the client: {'; '.join(unv[:3])}", source=url, weight=2,
          needs="client" if unv else None, rec="Ask the client to confirm or remove these claims; ads will not use them.", sim=w.get("source") != "LIVE")
    c.add("credentials", "D. Trust & compliance", "Credentials / licences / certifications shown", "PASS" if sig.get("has_credentials") else "WARN", "ESTIMATED",
          "Credential wording found." if sig.get("has_credentials") else "No licence/insurance/certification wording found.", source=url, weight=1,
          rec="Show licences, insurance and certifications on service pages.")
    c.add("testimonials", "D. Trust & compliance", "Testimonials/reviews displayed", "PASS" if sig.get("has_testimonials") else "WARN", "ESTIMATED",
          "Testimonial/review wording found." if sig.get("has_testimonials") else "No testimonial wording found.", source=url, weight=1,
          rec="Add real customer testimonials and a link to Google reviews.")
    c.unavailable("ads_policy", "D. Trust & compliance", "Compliance with Google Ads policies and local law, accuracy of prices/promotions",
                  "Needs human review against https://support.google.com/adspolicy/answer/6008942", needs="manual")
    return c.items


# ======================================================================================== section 3
def audit_gbp(biz: Business, ctx: dict, g: dict) -> list[dict]:
    c = Collector("gbp", simulated_default=(g or {}).get("source") != "LIVE")
    if biz.has_gbp is False:
        c.add("none", "A. Profile", "Business has a Google Business Profile", "WARN", "VERIFIED",
              "The client reported no Google Business Profile.", source="Client intake form", weight=2, sim=False,
              rec="Create and verify a Business Profile: it is free, drives local calls/directions, and enables location assets in Google Ads.")
        for fid, label in (("claim", "Profile claimed and verified"), ("categories", "Categories"), ("reviews", "Reviews and reputation"),
                           ("assets", "Location assets in Google Ads")):
            c.na(fid, "A. Profile", label, "No profile exists yet; becomes applicable once one is created.")
        return c.items
    if not g or g.get("error") or not g.get("client"):
        c.add("found", "A. Profile", "Profile found on Google Maps", "FAIL", "UNAVAILABLE" if (g or {}).get("error") else "VERIFIED",
              (g or {}).get("error") or "No matching listing found by name, website and location.", source="Google Places", weight=3,
              rec="Confirm the profile exists and matches the business name/phone/website exactly; the audit looks it up by those.", needs="client", sim=(g or {}).get("source") != "LIVE")
        return c.items
    cl, comps = g["client"], [x for x in g.get("competitors", []) if x]
    src = "Google Places"
    c.add("found", "A. Profile", "Profile found on Google Maps", "PASS", "VERIFIED", f"{cl.get('name')} — {cl.get('address')}", source=src, weight=3)
    c.add("name", "A. Profile", "Business name matches", "PASS" if normalize_term(cl.get("name", "")) == normalize_term(biz.name) else "WARN", "VERIFIED",
          f"Profile '{cl.get('name')}' vs client '{biz.name}'", source=src, weight=2, rec="Use the exact legal/trading name on the profile.")
    c.add("website", "A. Profile", "Website URL matches", "PASS" if cl.get("website") and urlhost(cl["website"]) == urlhost(biz.website_url) else "WARN", "VERIFIED",
          f"Profile: {cl.get('website') or '—'}", source=src, weight=2, rec="Point the profile website at the site used for ads.")
    c.add("phone", "A. Profile", "Phone matches", "PASS" if digits(cl.get("phone", "")) == digits(biz.phone) else "WARN", "VERIFIED",
          f"Profile {cl.get('phone') or '—'} vs client {biz.phone}", source=src, weight=2, rec="Align the phone number everywhere (NAP consistency).")
    c.add("address", "A. Profile", "Address present", "PASS" if cl.get("address") else "FAIL", "VERIFIED", cl.get("address") or "none", source=src, weight=1)
    hours = (cl.get("opening_hours") or {}).get("weekday_text")
    c.add("hours", "A. Profile", "Business hours set", "PASS" if hours else "FAIL", "VERIFIED", "Hours listed." if hours else "No hours on the listing.", source=src, weight=2,
          rec="Add accurate business hours (also needed to limit the call asset to open hours).")
    pc = (cl.get("primary_category") or "").lower()
    cat_words = set(normalize_term(biz.category or "").split())
    c.add("category", "A. Profile", "Primary category relevant", "PASS" if pc and (cat_words & set(normalize_term(pc).split())) else "WARN" if pc else "FAIL", "ESTIMATED",
          f"Primary category '{cl.get('primary_category') or '—'}' vs client category '{biz.category}'.", source=src, weight=2,
          rec="Choose the most specific accurate primary category; do not add irrelevant categories to chase keywords.")
    rating, n = cl.get("rating") or 0, cl.get("user_rating_count", 0)
    c.add("rating", "C. Reviews", "Average rating", "PASS" if rating >= 4.2 else "WARN" if rating >= 3.8 else "FAIL", "VERIFIED", f"{rating} from {n} reviews", source=src, weight=2,
          rec="Respond to negative reviews and ask happy customers for reviews.")
    if comps:
        med = sorted(x.get("user_rating_count", 0) for x in comps)[len(comps) // 2]
        c.add("review_volume", "C. Reviews", "Review count vs competitors", "PASS" if n >= med else "WARN" if n >= 0.5 * med else "FAIL", "VERIFIED",
              f"{n} reviews vs competitor median {med}", source=src, weight=2, rec=f"Aim for ~{max(0, med - n)} more reviews to reach the competitor median.")
    revs = cl.get("reviews") or []
    old = sum(1 for r in revs if "year" in (r.get("relative_time") or ""))
    if revs:
        c.add("recency", "C. Reviews", "Recent reviews", "PASS" if old < len(revs) / 2 else "WARN", "ESTIMATED",
              f"{len(revs) - old} of {len(revs)} sampled reviews are from the past year (Google returns a sample).", source=src, weight=1)
        low = [r for r in revs if (r.get("rating") or 5) <= 2]
        c.add("complaints", "C. Reviews", "Common complaints", "INFO", "VERIFIED", f"{len(low)} low-rated review(s) in the sample" +
              (": " + " | ".join((r.get("text") or "")[:90] for r in low[:2]) if low else "."), source=src)
    else:
        c.unavailable("recency", "C. Reviews", "Review recency and themes", "Review text was not returned.", needs="manual")
    photos = cl.get("photo_count", 0)
    c.add("photos", "B. Optimisation", "Photos uploaded", "PASS" if photos >= 10 else "WARN" if photos else "FAIL", "ESTIMATED",
          f"{photos} photo(s) returned (Google returns a sample, not the full count).", source=src, weight=1, rec="Upload real photos of work, team, premises and logo.")
    for fid, label in (("claimed", "Profile claimed and verified"), ("secondary", "Secondary categories"), ("description", "Business description"),
                       ("services_list", "Services/products listed"), ("posts_qa", "Posts and Q&A"), ("attributes", "Attributes, logo, special/holiday hours"),
                       ("responses", "Owner responses to reviews"), ("duplicates", "Duplicate or conflicting profiles"), ("eligible", "Eligibility")):
        c.unavailable(fid, "A. Profile" if fid in ("claimed", "secondary", "description", "services_list", "duplicates", "eligible") else "B/C. Optimisation & reviews", label,
                      "Not available from Google Places; needs the Business Profile API (approval-gated) or a manual look.", needs="manual")
    c.add("assets", "D. Google Ads integration", "Eligible for location assets", "PASS" if cl.get("address") else "WARN", "ESTIMATED",
          "A physical address is listed, so a location asset is likely possible once the profile is linked in Google Ads." if cl.get("address") else "No address listed.",
          source=src, weight=1, rec="Link the Business Profile in Google Ads (requires access to both accounts) to enable calls, directions and location extensions.")
    return c.items


def urlhost(u: str) -> str:
    from app.services.client_profile import host_of
    return host_of(u)


# ======================================================================================== section 4
def audit_competitors(biz: Business, ctx: dict, comp: dict, g: dict, w: dict) -> tuple[list[dict], list[dict]]:
    c = Collector("competitors", simulated_default=(comp or {}).get("source") != "LIVE")
    suggested: list[dict] = []
    provided = ctx["competitors"]
    if not provided:
        c.add("provided", "A. Identification", "Competitors provided by the client", "WARN", "VERIFIED", "None provided.", source="Client intake form", weight=2,
              sim=False, rec="Ask the client for their top 3 competitors.", needs="client")
    else:
        c.add("provided", "A. Identification", "Competitors provided by the client", "PASS" if len(provided) >= 2 else "WARN", "VERIFIED",
              f"{len(provided)} of up to 3: {', '.join(x['name'] for x in provided)}", source="Client intake form", weight=1, sim=False)
    # additional direct competitors from the Places search (verified listings, not rankings)
    try:
        found = registry.get("places").text_search(f"{ctx['category']} in {ctx['city']}")
        have = {normalize_term(x["name"]) for x in provided} | {normalize_term(biz.name)}
        suggested = [{"name": f["name"], "website": f.get("website", ""), "place_id": f.get("place_id", "")} for f in found if normalize_term(f["name"]) not in have][:7]
        c.add("discovered", "A. Identification", "Additional local competitors discovered", "INFO", "VERIFIED",
              f"{len(suggested)} other '{ctx['category']}' listing(s) near {ctx['city']}: {', '.join(s_['name'] for s_ in suggested[:5]) or 'none'}",
              source="Google Places text search", sim=registry.get("places").source != "LIVE")
    except AdapterError as exc:
        c.unavailable("discovered", "A. Identification", "Additional local competitors discovered", f"Places search failed ({exc.kind}).", needs="manual")
    c.unavailable("advertisers", "A. Identification", "Who is advertising on Google Search / ranking organically",
                  "Needs the Google Ads Transparency Center (https://adstransparency.google.com) or a paid tool (Semrush/Ahrefs); neither is connected.", needs="manual",
                  rec="Check the Ads Transparency Center for each competitor and record observed ads.")
    rows = (comp or {}).get("competitors", []) if comp and not comp.get("skipped") else []
    gcomps = (g or {}).get("competitors") or []
    client_scores = [s_["score"] for s_ in (w or {}).get("content_scores", [])]
    client_avg = sum(client_scores) / len(client_scores) if client_scores else None
    for i, r in enumerate(rows):
        name = r["name"]
        gp = gcomps[i] if i < len(gcomps) and gcomps[i] else None
        if r.get("website_url"):
            c.add(f"site.{i}", "C. Website", f"{name}: service-focused website", "INFO", "VERIFIED",
                  f"Services found: {', '.join(r.get('services', [])[:6]) or 'none identified'}; offers on site: {'; '.join(r.get('offers_on_site', [])[:2]) or 'none found'}",
                  source=r["website_url"], sim=r.get("crawl_source") != "LIVE")
            cs = [s_["score"] for s_ in r.get("content_scores", [])]
            if cs and client_avg is not None:
                avg = sum(cs) / len(cs)
                c.add(f"content.{i}", "C. Website", f"{name}: landing page quality vs the client", "PASS" if client_avg >= avg else "WARN", "ESTIMATED",
                      f"Competitor service pages average {avg:.0f}/100 vs client {client_avg:.0f}/100 (same DOM heuristic).", source=r["website_url"], weight=2,
                      rec=f"Close the gap to {name}'s service pages (depth, CTA, trust signals).")
            lacks = r.get("services_client_lacks", [])
            if lacks:
                c.add(f"lacks.{i}", "E. Positioning", f"{name}: services the client does not list", "WARN", "VERIFIED", ", ".join(lacks[:5]), source=r["website_url"], weight=1,
                      needs="client", rec="Ask the client whether they offer these; if so, add pages and ad groups.")
        else:
            c.unavailable(f"site.{i}", "C. Website", f"{name}: website analysed", "No website provided.", needs="client")
        if gp:
            c.add(f"gbp.{i}", "D. Business Profile", f"{name}: rating and reviews", "INFO", "VERIFIED",
                  f"{gp.get('rating')} from {gp.get('user_rating_count', 0)} reviews; hours {'listed' if (gp.get('opening_hours') or {}).get('weekday_text') else 'missing'}",
                  source="Google Places", sim=(g or {}).get("source") != "LIVE")
        else:
            c.unavailable(f"gbp.{i}", "D. Business Profile", f"{name}: Business Profile found", "No listing found (add the Google Maps link for an exact match).", needs="client")
        if r.get("keyword_ideas"):
            c.add(f"kw.{i}", "B. Google Ads", f"{name}: keyword opportunities (estimated)", "INFO", "ESTIMATED",
                  f"{len(r['keyword_ideas'])} ideas from Keyword Planner URL seeding, e.g. {', '.join(r['keyword_ideas'][:4])}. These are NOT verified records of the competitor's targeting.",
                  source="Keyword Planner (URL seeds)", sim=r.get("crawl_source") != "LIVE")
    ads = (comp or {}).get("pasted_ads_analysis")
    if ads:
        c.add("ads", "B. Google Ads", "Competitor ad messaging (pasted by the team)", "INFO", "ESTIMATED",
              f"Angles: {', '.join(ads.get('angles', [])) or '—'}; offers: {'; '.join(ads.get('offers', [])[:3]) or '—'}. {ads.get('verification', '')}",
              source="Team-pasted ad text, analysed by AI", sim=False)
    else:
        c.unavailable("ads", "B. Google Ads", "Competitor headlines, descriptions, offers and assets",
                      "No ad text was provided and observed ads cannot be fetched automatically.", needs="manual",
                      rec="Paste competitor ad copy from the Ads Transparency Center into the business profile (competitor ads) and re-run.")
    # positioning (E): derived only from the evidence above
    cl = (g or {}).get("client") or {}
    gv = [x for x in gcomps if x]
    if cl and gv:
        best = max(gv, key=lambda x: (x.get("rating") or 0, x.get("user_rating_count", 0)))
        if (cl.get("user_rating_count", 0) < best.get("user_rating_count", 0)):
            c.add("pos.reviews", "E. Positioning", "Social proof gap", "WARN", "VERIFIED",
                  f"Strongest competitor has {best.get('user_rating_count')} reviews vs the client's {cl.get('user_rating_count', 0)}.", source="Google Places", weight=2,
                  sim=(g or {}).get("source") != "LIVE", rec="Differentiate on reviews: run a review-request campaign; until then avoid 'most trusted' style claims.")
    offers = sorted({o for r in rows for o in r.get("offers_on_site", [])})[:4]
    if offers:
        c.add("pos.offers", "E. Positioning", "Competitor offers the client could answer", "WARN", "ESTIMATED", "; ".join(o.strip()[:90] for o in offers), source="Competitor websites",
              weight=1, rec="Consider an equivalent offer (free estimate, same-day, warranty) if the client can honour it.", needs="client")
    c.add("pos.usp", "E. Positioning", "Client USPs available to differentiate",
          "PASS" if len(biz.verified_claims or []) >= 3 else "WARN", "VERIFIED", f"{len(biz.verified_claims or [])} verified selling point(s).", source="Client intake form", weight=2,
          sim=False, rec="Differentiate ads on verified selling points that competitors do not state.")
    c.unavailable("pos.market", "E. Positioning", "Market messaging gaps and customer pain points", "Needs human judgement across competitor ads and reviews.", needs="manual")
    return c.items, suggested


# ======================================================================================== section 5
def intent_of(kw: str, brand_words: set[str], comp_names: set[str]) -> str:
    k = normalize_term(kw)
    if comp_names and any(n and n in k for n in comp_names):
        return "competitor"
    if brand_words and any(b in k.split() for b in brand_words):
        return "brand"
    if any(j in k for j in ("how to", "how do", "what is", "diy", "tutorial", "guide")):
        return "informational"
    if "near me" in k or "nearby" in k:
        return "local"
    if any(w in k for w in INTENT_WORDS["transactional"]):
        return "transactional"
    if any(w in k for w in INTENT_WORDS["commercial"]):
        return "commercial_investigation"
    return "service_specific"


def audit_keywords(biz: Business, ctx: dict, kw: dict, w: dict, comp: dict) -> tuple[list[dict], dict]:
    c = Collector("keywords", simulated_default=(kw or {}).get("source") != "LIVE")
    src = f"Keyword Planner ({(kw or {}).get('source', 'MOCK')})"
    sim = (kw or {}).get("source") != "LIVE"
    rows = (kw or {}).get("keywords", [])
    lp = (w or {}).get("landing_page_map", {}) or {}
    brand = {x for x in normalize_term(biz.name).split() if len(x) > 3}
    comp_names = {normalize_term(x["name"]) for x in ctx["competitors"]}
    city = ctx["city"]
    loc = ", ".join(a["label"] for a in (biz.service_areas or [])[:3]) or city
    report = []
    selected_terms = set()
    for r in rows:
        intent = intent_of(r["keyword"], brand, comp_names)
        vol = r.get("avg_monthly_searches")
        has_metrics = bool(vol) or bool(r.get("high_top_of_page_bid_micros"))
        irrelevant = r["label"] == "IRRELEVANT"
        if irrelevant or intent in ("informational", "navigational"):
            prio, why = "EXCLUDED", "Irrelevant or informational intent" if not irrelevant else r.get("label_reason", "Irrelevant")
        elif intent in ("brand", "competitor"):
            prio, why = "LOW", f"{intent.title()} term: run separately, not in main lead-gen ad groups"
        elif r["label"] == "SERVICE" and intent in ("transactional", "local", "service_specific") and (vol or 0) >= 20 and r.get("affordable", True):
            prio, why = "HIGH", "Matches a service with commercial/transactional intent and measurable volume"
        elif r["label"] in ("SERVICE", "GENERIC") and (vol or 0) >= 20:
            prio, why = "MEDIUM", "Relevant with moderate commercial potential"
        else:
            prio, why = "LOW", "Relevant but low volume, lower intent or not affordable at this budget"
        if prio != "EXCLUDED":
            selected_terms.add(normalize_term(r["keyword"]))
        high = (r.get("high_top_of_page_bid_micros") or 0) / 1e6
        low = (r.get("low_top_of_page_bid_micros") or 0) / 1e6
        report.append({"keyword": r["keyword"], "service": r.get("service") or ("Generic" if r["label"] == "GENERIC" else ""), "intent": intent,
                       "monthly_searches": vol if has_metrics else None, "avg_cpc": None, "competition": r.get("competition") if has_metrics else None,
                       "top_of_page_bid": f"${low:.2f}–${high:.2f}" if high else None, "target_location": loc,
                       "match_type": "EXACT + PHRASE" if prio in ("HIGH", "MEDIUM") else ("PHRASE" if prio == "LOW" else "—"),
                       "landing_page": lp.get(r.get("service") or "", "") or next((s_["landing_page_url"] for s_ in ctx["services"] if s_["name"] == r.get("service")), ""),
                       "priority": prio, "negative_recommendation": "Add as negative" if prio == "EXCLUDED" else "",
                       "source": r.get("seed", "")[:40] if str(r.get("seed", "")).startswith("competitor") else "Keyword Planner idea", "evidence": "ESTIMATED",
                       "simulated": sim, "notes": why})
    # unvalidated variations (hypotheses): never given metrics
    have = {normalize_term(x["keyword"]) for x in report}
    for s_ in ctx["service_names"]:
        n = 0
        for m in MODIFIERS:
            k = f"{s_.lower()} {m.format(city=city.lower())}".strip()
            if normalize_term(k) in have or n >= 8:
                continue
            n += 1
            have.add(normalize_term(k))
            report.append({"keyword": k, "service": s_, "intent": intent_of(k, brand, comp_names), "monthly_searches": None, "avg_cpc": None,
                           "competition": None, "top_of_page_bid": None, "target_location": loc, "match_type": "PHRASE",
                           "landing_page": lp.get(s_, ""), "priority": "LOW", "negative_recommendation": "", "source": "Rule-based variation (service + modifier)",
                           "evidence": "AI_GENERATED", "simulated": False,
                           "notes": "Hypothesis: not returned by Keyword Planner, so volume/CPC/competition are unavailable. Validate before use."})
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "EXCLUDED": 3}
    report.sort(key=lambda x: (order[x["priority"]], x["service"], -(x["monthly_searches"] or 0)))
    counts = Counter(x["priority"] for x in report)
    # negatives
    negs = []
    for r in report:
        if r["priority"] == "EXCLUDED":
            junk = next((j for j in JUNK if j in normalize_term(r["keyword"])), None)
            negs.append({"term": junk or r["keyword"], "match_type": "PHRASE" if junk and " " not in junk else "EXACT", "reason": r["notes"]})
    base = [{"term": j, "match_type": "PHRASE", "reason": "Standard junk intent (jobs, DIY, free, training)"} for j in ("jobs", "careers", "diy", "free", "salary", "course", "training")]
    seen, neg_final = set(), []
    for n in base + negs:
        if n["term"] not in seen:
            seen.add(n["term"])
            neg_final.append(n)
    conflicts = [{"negative": n["term"], "blocks": r["keyword"]} for n in neg_final for r in report
                 if r["priority"] in ("HIGH", "MEDIUM") and n["match_type"] == "PHRASE" and f" {normalize_term(n['term'])} " in f" {normalize_term(r['keyword'])} "]
    # findings
    for s_ in ctx["service_names"]:
        n_sel = sum(1 for r in report if r["service"] == s_ and r["priority"] in ("HIGH", "MEDIUM"))
        c.add(f"cover.{normalize_term(s_).replace(' ', '_')}", "E. Grouping", f"'{s_}' has enough relevant keywords", "PASS" if n_sel >= 4 else "WARN" if n_sel >= 1 else "FAIL",
              "ESTIMATED", f"{n_sel} high/medium priority keyword(s).", source=src, weight=3, sim=sim,
              rec=f"Research more '{s_}' terms (problem-based, location) or check the service name.")
    high = counts.get("HIGH", 0)
    c.add("high", "G. Prioritisation", "High-priority keywords available", "PASS" if high >= 5 else "WARN" if high else "FAIL", "ESTIMATED",
          f"{high} high · {counts.get('MEDIUM', 0)} medium · {counts.get('LOW', 0)} low · {counts.get('EXCLUDED', 0)} excluded.", source=src, weight=3, sim=sim)
    metrics = sum(1 for r in report if r["monthly_searches"] is not None)
    c.add("metrics", "C. Evaluation", "Volume/bid data available for the keywords", "PASS" if metrics >= 0.5 * max(len(report), 1) else "WARN", "VERIFIED" if not sim else "ESTIMATED",
          f"{metrics} of {len(report)} keywords have Keyword Planner metrics; the rest are labelled unavailable, never estimated.", source=src, weight=2, sim=sim,
          rec="Connect Google Ads (read-only) to pull real Keyword Planner data." if sim else "")
    c.unavailable("avg_cpc", "C. Evaluation", "Average CPC", "Keyword Planner returns top-of-page bids, not average CPC; actual CPC appears only after campaigns run.", needs="manual")
    ov = (kw or {}).get("overlaps", [])
    c.add("overlap", "E. Grouping", "No keyword overlap between ad groups", "PASS" if not ov else "WARN", "VERIFIED", f"{len(ov)} overlap(s)." if ov else "None.", source="Audit rules", weight=1,
          sim=False, rec="Keep each keyword in its most relevant ad group.")
    c.add("negatives", "F. Negatives", "Initial negative keyword list created", "PASS" if neg_final else "FAIL", "AI_GENERATED",
          f"{len(neg_final)} negative term(s) recommended; {len(conflicts)} conflict(s) with wanted keywords.", source="Rules + irrelevant keyword labels", weight=2, sim=False,
          rec="Review the list; add job-seeker, DIY, free and unrelated-service terms.")
    if conflicts:
        c.add("neg_conflict", "F. Negatives", "Negatives that would block wanted keywords", "FAIL", "VERIFIED", "; ".join(f"'{x['negative']}' blocks '{x['blocks']}'" for x in conflicts[:3]),
              source="Audit rules", weight=3, sim=False, rec="Change those negatives to exact match or remove them.")
    mapped = sum(1 for s_ in ctx["service_names"] if lp.get(s_))
    c.add("mapping", "E. Grouping", "Keywords mapped to dedicated landing pages", "PASS" if mapped == len(ctx["service_names"]) else "WARN" if mapped else "FAIL", "VERIFIED",
          f"{mapped} of {len(ctx['service_names'])} services have a dedicated landing page.", source="Website audit", weight=2, sim=False, rec="Create the missing service pages.")
    c.add("sources", "B. Discovery", "Research sources used", "INFO", "VERIFIED",
          "Used: client service list, website headings, competitor websites, Keyword Planner ideas" + (", pasted competitor ads" if ctx["competitor_ads_text"].strip() else "") +
          ". Not connected: Google Autocomplete/Related searches/People Also Ask, Google Trends, Semrush/Ahrefs, forums, existing search-terms report (needs account history).",
          source="Audit configuration", sim=False)
    c.unavailable("trends", "B. Discovery", "Seasonal trends, autocomplete, People Also Ask, third-party databases", "Sources not connected to this app.", needs="manual",
                  rec="Check Google Trends and autocomplete manually for seasonality and customer phrasing.")
    c.add("ai_note", "B. Discovery", "AI used only for labelling and variations", "INFO", "AI_GENERATED",
          "AI/rules classify intent and propose variations; every metric comes from Keyword Planner or is marked unavailable.", source="Audit method", sim=False)
    return c.items, {"columns": ["keyword", "service", "intent", "monthly_searches", "avg_cpc", "competition", "top_of_page_bid", "target_location",
                                 "match_type", "landing_page", "priority", "negative_recommendation", "source", "evidence", "notes"],
                     "rows": report, "negatives": neg_final, "negative_conflicts": conflicts, "counts": dict(counts)}


# ======================================================================================== scoring, recommendations, summary
def recommendations(all_items: list[dict]) -> dict:
    rec = {s: [] for s in SEVERITY_ORDER}
    for i in sorted(all_items, key=lambda x: -x["weight"]):
        if i["status"] in ("FAIL", "WARN") and i["recommendation"]:
            rec[i["severity"]].append({"finding": i["id"], "section": i["section"], "text": i["recommendation"], "because": i["finding"][:200], "evidence": i["evidence"]})
    needs_client = [{"finding": i["id"], "section": i["section"], "text": i["check"], "hint": i["recommendation"] or i["finding"]} for i in all_items
                    if i.get("needs") == "client" and i["status"] in ("UNAVAILABLE", "WARN", "INFO", "FAIL")]
    manual = [{"finding": i["id"], "section": i["section"], "text": i["check"], "hint": i["finding"]} for i in all_items
              if i.get("needs") == "manual" and i["status"] == "UNAVAILABLE"]
    plan = [x["text"] for x in rec["critical"][:3] + rec["high"][:4]]
    if needs_client:
        plan.append(f"Send the client the {len(needs_client)} open question(s) listed under 'needs client clarification'.")
    if manual:
        plan.append(f"Complete the {len(manual)} manual verification item(s) before building campaigns.")
    return {**rec, "needs_client_clarification": needs_client, "manual_verification": manual, "next_steps": plan[:10]}


def readiness(overall: int | None) -> str:
    if overall is None:
        return "Not enough data to assess readiness."
    return ("Ready to plan campaigns" if overall >= 75 else "Almost ready — fix the high-priority items first" if overall >= 55 else "Not ready — critical gaps must be fixed before building campaigns")


def build_summary(biz: Business, client: Client, ctx: dict, sections: dict, scores: dict, recs: dict, kwrep: dict, w: dict, g: dict, comp: dict, suggested: list) -> dict:
    items = {k: v["findings"] for k, v in sections.items()}
    fails = lambda sec: [i for i in items[sec] if i["status"] in ("FAIL", "WARN")]  # noqa: E731
    cnt = kwrep["counts"]
    top = [r for r in kwrep["rows"] if r["priority"] == "HIGH"][:8]
    cl = (g or {}).get("client") or {}
    return {
        "1_business_understanding": f"{biz.name} ({client.client_code}) is a {biz.category or 'local'} business serving {', '.join(a['label'] for a in (biz.service_areas or [])[:5]) or 'unspecified areas'} "
                                    f"with {len(ctx['service_names'])} service(s): {', '.join(ctx['service_names'])}. Budget ${biz.monthly_budget_usd:,.0f}/month; objective: lead generation. "
                                    f"Economics, audience, capacity and seasonality are not yet known (see clarification list).",
        "2_online_presence": f"Website {biz.website_url} ({(w or {}).get('signals', {}).get('pages', 0)} pages crawled); "
                             f"Business Profile: {'none reported' if biz.has_gbp is False else (str(cl.get('rating')) + '★ from ' + str(cl.get('user_rating_count', 0)) + ' reviews') if cl else 'not found/unavailable'}; "
                             f"{len(ctx['competitors'])} competitor(s) provided.",
        "3_website_weaknesses": [f"{i['check']}: {i['finding'][:140]}" for i in fails("website") if i["weight"] >= 2][:8],
        "4_gbp_weaknesses": [f"{i['check']}: {i['finding'][:140]}" for i in fails("gbp")][:8],
        "5_competitors": [f"{i['check']}: {i['finding'][:140]}" for i in items["competitors"] if i["status"] in ("WARN", "PASS") and i["id"].split(".")[1] in ("content", "lacks", "reviews", "offers", "usp")][:8],
        "6_keyword_opportunities": [f"{r['keyword']} ({r['service']}, {r['intent']}, vol {r['monthly_searches'] if r['monthly_searches'] is not None else 'n/a'})" for r in top]
                                   or ["No high-priority keywords with metrics were found."],
        "7_negative_keywords": [f"{n['term']} [{n['match_type']}]" for n in kwrep["negatives"][:15]],
        "8_services_to_advertise": [s_ for s_ in ctx["service_names"] if sum(1 for r in kwrep["rows"] if r["service"] == s_ and r["priority"] in ("HIGH", "MEDIUM")) >= 1] or ctx["service_names"],
        "9_geo_targeting": f"{biz.targeting_mode.replace('AREAS', 'Specific areas').replace('RADIUS', 'Radius around location')}: " +
                           f"{', '.join(a['label'] for a in (biz.service_areas or [])[:6])}" + (f"; states {', '.join(biz.states)}" if biz.states else "; no state limit (add one)"),
        "10_landing_pages": [f"{k} → {v}" for k, v in ((w or {}).get("landing_page_map") or {}).items()] + [f"Create a page for '{s_}'" for s_ in (w or {}).get("services_without_page", [])],
        "11_advertising_opportunities": [f"{cnt.get('HIGH', 0)} high-priority and {cnt.get('MEDIUM', 0)} medium-priority keywords across {len(ctx['service_names'])} service(s)"] +
                                        [x["text"] for x in recs["low"][:3]] + ([f"{len(suggested)} additional local competitors identified for the Ads Transparency check"] if suggested else []),
        "12_risks_and_limitations": [f"{scores[k]['checks_unavailable']} check(s) in '{dict(SECTIONS)[k]}' could not be verified" for k in scores if k != "overall" and scores[k] and scores[k].get("checks_unavailable")][:6] +
                                    (["Part of this audit used simulated (mock) data — connect real integrations for live evidence"] if any(i.get("simulated") for v in items.values() for i in v) else []) +
                                    ["Economics (margin, lead value, CPL targets) are unknown, so profitability cannot be judged"],
        "13_readiness": f"{readiness(scores['overall']['score'])} (overall {scores['overall']['score']}/100)",
        "14_action_plan": recs["next_steps"],
        "method_note": SCORING_CRITERIA,
    }


# ======================================================================================== orchestration
def snapshot(biz: Business, client: Client) -> dict:
    from app.services import client_profile
    return client_profile.to_form(client, biz)


def _hash(d: dict) -> str:
    d = {k: v for k, v in d.items() if k not in ("updated_at", "created_at")}
    return hashlib.sha256(canonical_json(d).encode()).hexdigest()


def start(db: Session, biz: Business, client: Client, user_id: str | None) -> AuditReport:
    running = db.scalar(select(AuditReport).where(AuditReport.business_id == biz.id, AuditReport.status == "RUNNING"))
    if running:
        raise Conflict("An audit is already running for this business", details={"report_id": running.id})
    prev = db.scalar(select(AuditReport).where(AuditReport.business_id == biz.id).order_by(AuditReport.version.desc()))
    snap = snapshot(biz, client)
    rep = AuditReport(client_id=client.id, business_id=biz.id, version=(prev.version + 1) if prev else 1, previous_report_id=prev.id if prev else None,
                      status="RUNNING", steps=[{"key": k, "name": n, "status": "PENDING", "summary": ""} for k, n in
                                               [("client", "1 · Client & business"), ("website", "2 · Website & landing pages"), ("gbp", "3 · Google Business Profile"),
                                                ("competitors", "4 · Competitor analysis"), ("keywords", "5 · Keyword research"), ("report", "Scoring, recommendations & summary")]],
                      input_snapshot=snap, input_hash=_hash(snap), created_by_id=user_id, source_type="MOCK")
    db.add(rep)
    db.flush()
    return rep


def _step(report_id: str, key: str, status: str, summary: str = "") -> None:
    with session_scope() as db:
        r = db.get(AuditReport, report_id)
        r.steps = [({**s, "status": status, "summary": summary, **({"at": clock.now().isoformat()} if status != "RUNNING" else {})} if s["key"] == key else s) for s in r.steps]


def run(report_id: str) -> str:
    """Runs all five audits, stores findings against the client's ID, scores, compares with the previous report."""
    db = SessionLocal()
    try:
        rep = db.get(AuditReport, report_id)
        biz, client = db.get(Business, rep.business_id), db.get(Client, rep.client_id)
        ctx = audits.build_context(db, biz)
        try:
            _step(rep.id, "client", "RUNNING")
            _step(rep.id, "website", "RUNNING")
            _step(rep.id, "gbp", "RUNNING")
            _step(rep.id, "competitors", "RUNNING")
            done = {}

            def on_done(kind, res, summary):
                key = {"WEBSITE": "website", "GBP": "gbp", "COMPETITOR": "competitors"}[kind]
                done[key] = res
                _step(rep.id, key, "FAILED" if res.get("error") else "DONE", summary)
            par = audits.run_parallel(db, biz, task_id=None, actor_id=rep.created_by_id, on_done=on_done)
            w, g, comp = par["website"], par["gbp"], par["competitor"]
            _step(rep.id, "keywords", "RUNNING")
            budget = (biz.monthly_budget_usd or 0) / 30.4 or None
            kw = audits.keyword_research(db, ctx, w if not w.get("error") else {}, comp, None, daily_budget=budget)
            audits.save_keyword_audit(db, biz, None, rep.created_by_id, kw)
            _step(rep.id, "keywords", "DONE", audits._summary("KEYWORD", kw))
            db.commit()
            sections = {}
            f1 = audit_client(biz, client, ctx, kw, w)
            _step(rep.id, "client", "DONE", f"{len(f1)} checks")
            f2 = audit_website(biz, ctx, w)
            f3 = audit_gbp(biz, ctx, g)
            f4, suggested = audit_competitors(biz, ctx, comp, g, w)
            f5, kwrep = audit_keywords(biz, ctx, kw, w, comp)
            for key, items in (("client", f1), ("website", f2), ("gbp", f3), ("competitors", f4), ("keywords", f5)):
                sections[key] = {"title": dict(SECTIONS)[key], "findings": items, **score(items)}
            sections["competitors"]["suggested_competitors"] = suggested
            sections["keywords"]["report"] = kwrep
            scores = {k: {kk: v[kk] for kk in ("score", "checks_scored", "checks_unavailable", "checks_not_applicable", "coverage")} for k, v in sections.items()}
            have = {k: v["score"] for k, v in scores.items() if v["score"] is not None}
            tw = sum(OVERALL_WEIGHTS[k] for k in have)
            scores["overall"] = {"score": round(sum(OVERALL_WEIGHTS[k] * s_ for k, s_ in have.items()) / tw) if tw else None,
                                 "sections_scored": sorted(have), "criteria": SCORING_CRITERIA, "label": readiness(round(sum(OVERALL_WEIGHTS[k] * s_ for k, s_ in have.items()) / tw) if tw else None)}
            all_items = [i for v in sections.values() for i in v["findings"]]
            recs = recommendations(all_items)
            summary = build_summary(biz, client, ctx, sections, scores, recs, kwrep, w, g, comp, suggested)
            prev = db.get(AuditReport, rep.previous_report_id) if rep.previous_report_id else None
            changes = compare(prev, all_items, scores) if prev and prev.status == "COMPLETED" else {"first_audit": True}
            rep.sections, rep.scores, rep.recommendations, rep.summary, rep.changes = sections, scores, recs, summary, changes
            rep.source_type = "LIVE" if all(not i.get("simulated") for i in all_items if i["status"] in CREDIT) else ("MIXED" if any(not i.get("simulated") for i in all_items if i["status"] in CREDIT) else "MOCK")
            rep.status, rep.finished_at = "COMPLETED", clock.now()
            db.commit()
            _step(rep.id, "report", "DONE", f"Overall readiness {scores['overall']['score']}/100")
        except Exception as exc:  # noqa: BLE001 - record, never leave a report RUNNING
            db.rollback()
            rep = db.get(AuditReport, report_id)
            rep.status, rep.error, rep.finished_at = "FAILED", f"{type(exc).__name__}: {str(exc)[:400]}", clock.now()
            db.commit()
        return rep.status
    finally:
        db.close()


def compare(prev: AuditReport, items: list[dict], scores: dict) -> dict:
    old = {i["id"]: i for v in (prev.sections or {}).values() for i in v.get("findings", [])}
    new = {i["id"]: i for i in items}
    rank = {"FAIL": 0, "WARN": 1, "PASS": 2}
    out = {"compared_with": prev.id, "previous_version": prev.version, "previous_date": prev.finished_at.isoformat() if prev.finished_at else None,
           "new": [], "resolved": [], "worse": [], "unchanged": 0,
           "score_change": {k: (None if scores[k]["score"] is None or (prev.scores.get(k) or {}).get("score") is None else scores[k]["score"] - prev.scores[k]["score"]) for k in scores}}
    for fid, i in new.items():
        o = old.get(fid)
        if i["status"] not in rank:
            continue
        if o is None or o["status"] not in rank:
            if i["status"] != "PASS":
                out["new"].append({"id": fid, "check": i["check"], "status": i["status"]})
        elif rank[i["status"]] > rank[o["status"]]:
            out["resolved"].append({"id": fid, "check": i["check"], "from": o["status"], "to": i["status"]})
        elif rank[i["status"]] < rank[o["status"]]:
            out["worse"].append({"id": fid, "check": i["check"], "from": o["status"], "to": i["status"]})
        else:
            out["unchanged"] += 1
    return out


def launch(db: Session, biz: Business, client: Client, user_id: str | None, *, sync: bool = False) -> AuditReport:
    rep = start(db, biz, client, user_id)
    db.commit()
    if sync:
        run(rep.id)
        db.expire_all()
        return db.get(AuditReport, rep.id)
    threading.Thread(target=run, args=(rep.id,), daemon=True).start()
    return rep


def serialize(r: AuditReport, *, full: bool = True, current_profile_hash: str | None = None) -> dict:
    d = {"id": r.id, "client_id": r.client_id, "business_id": r.business_id, "version": r.version, "status": r.status, "steps": r.steps,
         "started_at": r.started_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None, "error": r.error,
         "source_type": r.source_type, "previous_report_id": r.previous_report_id,
         "scores": r.scores, "profile_changed_since": (current_profile_hash is not None and r.input_hash != current_profile_hash)}
    if full:
        d.update(sections=r.sections, recommendations=r.recommendations, summary=r.summary, changes=r.changes, input_snapshot=r.input_snapshot)
    return d


def keyword_csv(r: AuditReport) -> str:
    import csv
    import io
    rep = (r.sections or {}).get("keywords", {}).get("report", {})
    buf = io.StringIO()
    wtr = csv.writer(buf)
    cols = rep.get("columns", [])
    wtr.writerow([c.replace("_", " ").title() for c in cols])
    metric = ("monthly_searches", "competition", "top_of_page_bid", "avg_cpc")
    for row in rep.get("rows", []):
        wtr.writerow([(("unavailable" if c in metric else "") if row.get(c) is None else row[c]) for c in cols])
    return buf.getvalue()


def get_for_user(db: Session, report_id: str) -> AuditReport:
    r = db.get(AuditReport, report_id)
    if not r:
        raise NotFound("Audit report not found")
    return r
