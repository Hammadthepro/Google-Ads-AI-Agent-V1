"""The ten-module intelligence engine: ONE persistent, evidence-bearing record of what the agent knows about a business.

The modules are the logical stages of a single pipeline, not pages. Each module keeps versioned runs; every finding carries its own
provenance. Downstream stages (campaign strategy, generation, monitoring, optimisation) read this record instead of re-deriving it.

Rules enforced here
* Unavailable data is ``UNAVAILABLE``/``unknown`` - never zero. A failed source is ``FAILED``/``WAITING_FOR_INTEGRATION`` - never ``COMPLETED``.
* Client-provided, externally researched, API-derived, third-party-estimated, forecast and AI-inferred information stay separate
  (``origin`` + ``verification``). An estimate or forecast is never promoted to a verified fact.
* A run is reused while its inputs (and the runs it depends on) are unchanged; a changed input makes it - and everything downstream of it - stale.
* Every specification check has an entry per module (``coverage``): evaluated, or honestly ``not_assessed``.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import NotFound, ValidationFailed
from app.core.util import canonical_json
from app.models import (AuditReport, Business, Client, FeasibilityResult, IntelligenceRun, PerformanceSnapshot)
from app.services import accounts_service, clients_service, finance, measurement

STATUSES = ("NOT_STARTED", "IN_PROGRESS", "WAITING_FOR_CLIENT", "WAITING_FOR_INTEGRATION", "PARTIALLY_COMPLETED", "COMPLETED",
            "COMPLETED_WITH_WARNINGS", "BLOCKED", "FAILED", "NOT_APPLICABLE")
RESULTS = ("PASS", "WARN", "FAIL", "INFO", "UNAVAILABLE", "NA")
SEVERITIES = ("critical", "high", "medium", "low", "info")
VERIFICATION = ("verified", "client_provided", "third_party_estimate", "forecast", "inferred", "assumption", "unknown")
ORIGINS = ("client_provided", "externally_researched", "google_ads_api", "third_party_estimate", "ai_assumption", "ai_recommendation")
CONFIDENCE = {"verified": 0.9, "client_provided": 0.7, "third_party_estimate": 0.5, "forecast": 0.5, "inferred": 0.5, "assumption": 0.3, "unknown": 0.0}
DECISIONS = {"ready": "Ready", "ready_with_conditions": "Ready with conditions", "blocked": "Not ready",
             "not_commercially_viable": "Not commercially viable"}

# ------------------------------------------------------------------------------------------------ the specification, as data
# (check key, label). Every audit item and additional check named in the specification appears exactly once.
_M = {}
_M["business_intelligence"] = ("Business intelligence", [], [
    ("business_understanding", "Business model, industry, products and services"), ("business_objectives", "Business objectives (leads, sales, calls, bookings)"),
    ("service_offerings", "Every service/product, priority and profitability"), ("target_audience", "Relevant customer types, needs and characteristics"),
    ("customer_pain_points", "Problems customers want solved"), ("usp", "Unique selling proposition"), ("pricing", "Prices, packages, discounts and offers"),
    ("profitability", "Gross margins, acquisition cost, average order value"), ("sales_process", "How leads become paying customers"),
    ("capacity", "How many customers or leads the business can handle"), ("service_coverage", "Where the business can actually serve customers"),
    ("restrictions", "Products or services that cannot or should not be advertised"),
    ("most_profitable_services", "Most profitable services or products"), ("highest_demand_services", "Services with the highest customer demand"),
    ("client_priority_services", "Services the client wants to prioritise"), ("lead_vs_qualified_lead", "Difference between a lead and a qualified lead"),
    ("acceptable_acquisition_cost", "Acceptable acquisition costs"), ("offer_competitiveness", "Whether the offer is commercially competitive"),
    ("fulfilment_ability", "Ability to fulfil generated demand"), ("client_vs_website_conflicts", "Conflicts between client information and the website"),
    ("missing_material_information", "Missing information that materially affects advertising decisions")])
_M["website"] = ("Website and landing pages", ["business_intelligence"], [
    ("website_structure", "Pages, navigation and service categories"), ("content_relevance", "Content matches actual products and services"),
    ("service_pages", "Dedicated pages for individual services"), ("seed_keywords", "Service names, products and relevant phrases"),
    ("landing_page_suitability", "Best destination for each service/product"), ("keyword_to_page_mapping", "Which page matches each keyword theme"),
    ("page_speed", "Mobile and desktop performance"), ("core_web_vitals", "LCP, INP and CLS where available"), ("mobile_responsiveness", "Mobile usability and layout"),
    ("conversion_elements", "Forms, phone numbers, booking buttons and CTAs"), ("trust_signals", "Reviews, testimonials, certifications, credentials"),
    ("content_quality", "Clarity, uniqueness, accuracy and usefulness"), ("technical_accessibility", "HTTP status, redirects, crawlability, indexing signals"),
    ("security", "HTTPS and functional destination"), ("user_experience", "Navigation, readability, friction and accessibility"),
    ("contact_functionality", "Forms, phone links and email links"),
    ("dedicated_page_per_service", "Every important service has its own relevant page"), ("offer_visible", "Advertised offering immediately visible"),
    ("pricing_or_quote", "Pricing explained or a clear way to get a quote"), ("unnecessary_steps", "No unnecessary steps between landing and conversion"),
    ("geography_supported", "Website supports the advertised locations"), ("ad_promise_match", "Landing page matches the promise made in the ad"),
    ("broken_links_redirects", "Broken links, errors and redirect problems"), ("phone_forms_functional", "Phone numbers and forms functional"),
    ("differentiators_communicated", "Differentiators communicated"), ("keyword_theme_relevance", "Content relevant to the intended keyword theme"),
    ("mobile_usable", "Destination usable on mobile"), ("enough_info_for_conversion", "Enough information to support the conversion"),
    ("destination_policy", "Technical or policy-related destination issues"), ("landing_page_scores", "Suitability score per service page")])
_M["competitors"] = ("Market and competitor intelligence", ["business_intelligence", "website"], [
    ("competitor_discovery", "Direct and indirect competitors"), ("competitor_websites", "Services, landing pages and positioning"),
    ("semrush_domain_overview", "SEMrush: estimated organic and paid visibility"), ("semrush_advertising_research", "SEMrush: paid keyword estimates and ad examples"),
    ("semrush_keyword_gap", "SEMrush: competitor keyword opportunities"), ("competitor_landing_pages", "Competitor landing pages where observable"),
    ("competitor_offers", "Discounts, pricing, guarantees and promotions"), ("ad_messaging", "Headlines, CTAs and selling propositions"),
    ("customer_reviews", "Common complaints and customer expectations"), ("google_business_profile", "Public ratings, reviews, services and locations"),
    ("local_competition", "Competitors in relevant local searches"), ("market_gaps", "Underserved services, locations and customer needs"),
    ("auction_insights", "Google Ads Auction Insights for eligible campaigns")])
_M["search_intent"] = ("Customer and search intent", ["business_intelligence", "website", "competitors"], [
    ("customer_problems", "Customer problems and motivations"), ("search_terminology", "Search terminology"), ("alternative_phrases", "Alternative phrases"),
    ("transactional_intent", "Transactional intent"), ("informational_intent", "Informational intent"), ("commercial_investigation", "Commercial investigation"),
    ("local_vs_nonlocal", "Local and non-local intent"), ("emergency_vs_non", "Emergency and non-emergency intent"), ("brand_vs_nonbrand", "Brand and non-brand searches"),
    ("price_vs_quality", "Price-sensitive and quality-oriented customers"), ("customer_segments", "Customer segments"),
    ("intent_by_variation", "Intent behind keyword variations"), ("keyword_intent_classification", "Intent classification per keyword and group")])
_M["keywords"] = ("Keyword research and analysis", ["website", "competitors", "search_intent"], [
    ("src_client_services", "Source: client-provided services"), ("src_website", "Source: website content and service pages"), ("src_seed_keywords", "Source: extracted seed keywords"),
    ("src_keyword_planner", "Source: Google Keyword Planner API"), ("src_semrush_magic", "Source: SEMrush Keyword Magic Tool"), ("src_semrush_competitors", "Source: SEMrush competitor keywords"),
    ("src_google_suggest", "Source: Google Search suggestions and related searches"), ("src_customer_language", "Source: customer language"),
    ("src_location_variations", "Source: location-based variations"), ("src_brand_names", "Source: brand and product names"),
    ("src_existing_keywords", "Source: existing Google Ads keywords"), ("src_search_terms", "Source: existing search-term reports"),
    ("src_account_history", "Source: historical account performance"),
    ("metric_avg_searches", "Average monthly searches"), ("metric_trend", "Search volume trend"), ("metric_competition", "Keyword Planner competition"),
    ("metric_competition_index", "Competition index"), ("metric_bid_low", "Low top-of-page bid"), ("metric_bid_high", "High top-of-page bid"),
    ("metric_estimated_cpc", "Estimated CPC (planning estimate)"), ("metric_intent", "Search intent"), ("metric_location_relevance", "Location relevance"),
    ("metric_source", "Keyword source"),
    ("classification", "Primary/secondary/high-intent/commercial/location/brand/competitor/informational/irrelevant"), ("filtering", "Relevance, intent, commercial potential, CPC, location, landing page"),
    ("grouping", "Groups by service, intent, need, landing page, semantics"), ("negative_keywords", "Irrelevant searches before launch and during monitoring"),
    ("cpc_financial_calcs", "CPC, conversions, CPA, max affordable CPC/CPL"), ("forecasts", "Keyword Planner forecasts"),
    ("keyword_planner_independence", "Keyword Planner usable from intake without a deployment account")])
_M["geography"] = ("Geographic and market feasibility", ["business_intelligence", "keywords"], [
    ("target_countries", "Countries where customers can be served"), ("target_regions", "Relevant states/regions"), ("cities_zips", "Specific cities and ZIP codes"),
    ("radius_targeting", "Practical radius from the business location"), ("service_availability", "Whether the client operates in those locations"),
    ("search_demand_by_location", "Keyword demand by location"), ("competition_by_location", "Competitive intensity by location"), ("language", "Customer search language"),
    ("local_search_trends", "Location-specific search behaviour"), ("excluded_locations", "Areas where the business cannot serve customers"),
    ("demand_differences", "Demand differences between cities"), ("seasonal_demand", "Seasonal demand"), ("local_regulations", "Local regulations and restrictions"),
    ("hours_availability", "24/7 or specific hours"), ("travel_constraints", "Travel or service constraints"), ("geographic_intent", "Geographic differences in intent"),
    ("location_option", "Presence vs Presence-or-interest location setting")])
_M["finance"] = ("Budget and financial feasibility", ["business_intelligence", "keywords", "geography"], [
    ("weekly_conversion_target", "Weekly conversion target"), ("monthly_budget", "Monthly advertising budget, if supplied"), ("max_daily_spend", "Maximum acceptable daily spend"),
    ("target_cpa", "Target CPA"), ("target_cpl", "Target CPL"), ("target_cac", "Target CAC"), ("avg_customer_value", "Average customer value"),
    ("avg_profit_per_customer", "Average profit per customer"), ("lead_to_customer_rate", "Lead-to-customer conversion rate"),
    ("landing_page_cvr", "Expected landing-page conversion rate"), ("estimated_cpc_range", "Estimated CPC range"), ("expected_impressions_clicks", "Expected impressions and clicks"),
    ("seasonal_demand", "Seasonal demand"), ("keyword_competition", "Keyword competition"), ("business_capacity", "Business capacity"),
    ("historical_performance", "Historical campaign performance"), ("three_scenarios", "Conservative, expected and optimistic scenarios"),
    ("affordability", "Maximum affordable CPC/CPL versus estimated CPC"), ("budget_recommendation", "Recommended daily and monthly budget with assumptions"),
    ("budget_sufficiency", "Can the budget realistically compete"), ("sensitivity", "Which assumptions matter most")])
_M["measurement"] = ("Conversion measurement", ["website"], [
    ("ads_conversion_tracking", "Google Ads conversion tracking configured"), ("google_tag", "Google tag installed correctly"), ("google_tag_manager", "Tag Manager configured, if used"),
    ("ga4", "Correct GA4 property connected"), ("form_submissions", "Successful form submissions tracked"), ("phone_calls", "Phone calls tracked"),
    ("purchase_tracking", "Transactions and values recorded"), ("booking_tracking", "Completed bookings measured"), ("duplicate_conversions", "No double counting"),
    ("conversion_values", "Accurate conversion values"), ("primary_conversions", "Genuine business outcomes used for bidding"), ("secondary_conversions", "Supporting actions observation-only"),
    ("attribution", "Attribution setup"), ("consent_privacy", "Consent and privacy requirements"), ("crm_integration", "Leads trackable through the sales pipeline"),
    ("offline_conversions", "Qualified and converted leads sent back to Google Ads"), ("measurement_readiness", "Configured / detected / verified / generating data / reliable")])
_M["account_policy"] = ("Google Ads account and policy", ["measurement"], [
    ("account_connection", "Correct Google Ads customer account"), ("account_status", "Enabled and eligible"), ("billing", "Valid payment setup where accessible"),
    ("account_access", "Appropriate permissions"), ("existing_campaigns", "Active, paused and removed campaigns"), ("historical_performance", "Impressions, clicks, CPC, conversions"),
    ("existing_keywords", "Previously successful and unsuccessful keywords"), ("search_terms", "Actual searches triggering previous ads"), ("negative_keywords", "Existing exclusions"),
    ("conversion_history", "Historical conversion data and quality"), ("policy_status", "Disapproved ads and policy restrictions"), ("account_limitations", "Serving restrictions and warnings"),
    ("experiments", "Existing experiments"), ("campaign_settings", "Campaign types and settings"), ("ad_groups_ads_assets", "Ad groups, ads and assets"),
    ("budget_bidding_config", "Budget configuration and bidding strategies"), ("geo_language_settings", "Geographic and language settings"),
    ("conversion_goals", "Conversion goals"), ("device_performance", "Device performance"), ("change_history", "Change history"),
    ("optimisation_decisions", "Existing optimisation decisions"), ("industry_policy", "Industry-specific policies, restricted services, claims, trademarks")])
_M["strategy"] = ("Campaign strategy and readiness", ["business_intelligence", "website", "competitors", "search_intent", "keywords", "geography", "finance", "measurement", "account_policy"], [
    ("campaign_objective", "Business outcome to optimise for"), ("campaign_structure", "Number of campaigns and their purposes"), ("service_prioritization", "Which services deserve priority"),
    ("ad_group_structure", "Keyword themes and groups"), ("keyword_strategy", "Selected keywords and match types"), ("negative_keyword_strategy", "Initial exclusions"),
    ("landing_pages", "Final URL for each ad group"), ("geographic_targeting", "Locations and exclusions"), ("language_targeting", "Appropriate languages"),
    ("budget_allocation", "Daily budget per campaign"), ("bidding_strategy", "Recommended strategy and rationale"), ("conversion_goals_strategy", "Actions used for optimisation"),
    ("ad_strategy", "Messaging, offers and USPs"), ("ad_assets", "Sitelinks, callouts, snippets, calls"), ("schedule", "Ad schedule where justified"),
    ("launch_requirements", "Outstanding blockers and approvals"), ("monitoring_requirements", "Metrics and conditions to monitor"),
    ("readiness_decision", "Ready / Ready with conditions / Not ready / Not commercially viable")])

MODULE_KEYS = list(_M)  # pipeline order
MODULES = {k: {"key": k, "number": i + 1, "title": v[0], "depends_on": v[1], "checks": [{"key": c, "label": lbl} for c, lbl in v[2]]}
           for i, (k, v) in enumerate(_M.items())}
DOWNSTREAM = {k: [m for m in MODULE_KEYS if k in MODULES[m]["depends_on"]] for k in MODULE_KEYS}


def spec_check_count() -> int:
    return sum(len(m["checks"]) for m in MODULES.values())


# ------------------------------------------------------------------------------------------------ findings
class _Seq:
    def __init__(self, module: str):
        self.module, self.n = module, 0

    def next(self) -> str:
        self.n += 1
        return f"FND-{MODULES[self.module]['number']:02d}-{self.n:04d}"


def make_finding(seq: _Seq, check: str, subject: str, text: str, *, result: str, origin: str, verification: str, source_type: str,
                 severity: str = "info", source_reference: str = "", retrieved_at: str | None = None, geography: str = "",
                 confidence: float | None = None, evidence: list | None = None, contradicting: list | None = None,
                 limitations: list | None = None, recommendation: str = "", reason: str = "", value=None) -> dict:
    """The one constructor for a finding. Refuses combinations that would blur provenance."""
    module = seq.module
    if check not in {c["key"] for c in MODULES[module]["checks"]}:
        raise ValueError(f"{check!r} is not a specified check of module {module}")
    if result not in RESULTS or severity not in SEVERITIES or verification not in VERIFICATION or origin not in ORIGINS:
        raise ValueError("finding has an unknown result, severity, verification or origin")
    if verification == "verified" and not (source_type and (source_reference or origin in ("client_provided", "google_ads_api"))):
        raise ValueError("a verified finding needs a source")
    if origin in ("ai_assumption", "ai_recommendation") and verification == "verified":
        raise ValueError("an AI assumption or recommendation can never be 'verified'")
    if origin == "third_party_estimate" and verification == "verified":
        raise ValueError("a third-party estimate is not a verified fact")
    conf = CONFIDENCE[verification] if confidence is None else confidence
    if not 0 <= conf <= 1:
        raise ValueError("confidence must be between 0 and 1")
    return {"finding_id": seq.next(), "module": module, "category": check, "subject": subject, "finding": text, "result": result, "severity": severity,
            "origin": origin, "verification": verification, "source_type": source_type, "source_reference": source_reference,
            "retrieved_at": retrieved_at or clock.now().isoformat(), "geography": geography, "confidence": conf,
            "evidence": evidence or [], "contradicting_evidence": contradicting or [], "limitations": limitations or [],
            "recommendation": recommendation, "reason": reason or recommendation, "value": value}


# ------------------------------------------------------------------------------------------------ inputs and staleness
def _jsonable(x):
    return x.isoformat() if isinstance(x, datetime) else x


def account_data_version(db: Session, acct) -> str | None:
    """When the account's performance data was last retrieved (None without an account or data)."""
    if acct is None:
        return None
    t = db.scalar(select(func.max(PerformanceSnapshot.fetched_at)).where(PerformanceSnapshot.google_ads_account_id == acct.id))
    return t.isoformat() if t else None


def account_facts(db: Session, acct) -> dict | None:
    """Measured facts about the linked account from the synced data. None when nothing has been retrieved (then everything stays Unavailable)."""
    from app.models import AdGroup, Ad, Campaign, ChangeEvent, Keyword, NegativeKeyword, SearchTermRow
    when = account_data_version(db, acct)
    if not when:
        return None
    end = clock.today() - timedelta(days=1)

    def tot(days):
        r = db.execute(select(func.count(func.distinct(PerformanceSnapshot.date)), func.sum(PerformanceSnapshot.impressions), func.sum(PerformanceSnapshot.clicks),
                              func.sum(PerformanceSnapshot.cost_micros), func.sum(PerformanceSnapshot.conversions)).where(
            PerformanceSnapshot.google_ads_account_id == acct.id, PerformanceSnapshot.level == "CAMPAIGN",
            PerformanceSnapshot.date >= end - timedelta(days=days - 1))).one()
        n, imp, clk, cost, conv = r
        clk, cost, conv = int(clk or 0), float(cost or 0) / 1e6, float(conv or 0)
        return {"days_with_data": int(n or 0), "impressions": int(imp or 0), "clicks": clk, "cost": round(cost, 2), "conversions": round(conv, 1),
                "cpc": round(cost / clk, 2) if clk else None, "cvr": round(conv / clk, 4) if clk else None, "cpl": round(cost / conv, 2) if conv else None}
    camps = list(db.scalars(select(Campaign).where(Campaign.google_ads_account_id == acct.id)))
    cids = [c.id for c in camps]
    groups = list(db.scalars(select(AdGroup).where(AdGroup.campaign_id.in_(cids or ["-"]))))
    gids = [g.id for g in groups]
    kws = list(db.scalars(select(Keyword).where(Keyword.ad_group_id.in_(gids or ["-"]))))
    terms = list(db.scalars(select(SearchTermRow).where(SearchTermRow.google_ads_account_id == acct.id, SearchTermRow.window_days == 30)))
    since = clock.now() - timedelta(days=30)
    changes = list(db.scalars(select(ChangeEvent).where(ChangeEvent.google_ads_account_id == acct.id, ChangeEvent.changed_at >= since)))
    return {"retrieved_at": when, "customer_id": acct.customer_id, "d30": tot(30), "d90": tot(90),
            "campaigns": {"total": len(camps), "by_status": _count_by(camps, "status"), "by_type": _count_by(camps, "campaign_type"), "by_bidding": _count_by(camps, "bidding_strategy"),
                          "monthly_budget": round(sum(c.monthly_budget_micros for c in camps if c.status == "ENABLED") / 1e6, 2)},
            "structure": {"ad_groups": len(groups), "ads": db.scalar(select(func.count()).select_from(Ad).where(Ad.ad_group_id.in_(gids or ["-"]))), "keywords": len(kws)},
            "keywords": {"enabled": sum(1 for k in kws if k.status == "ENABLED"), "match_types": _count_by(kws, "match_type")},
            "search_terms": {"terms": len(terms), "cost": round(sum(t.cost_micros for t in terms) / 1e6, 2),
                             "no_conversion_cost": round(sum(t.cost_micros for t in terms if not t.conversions and not t.conversions_90d) / 1e6, 2)},
            "negatives": db.scalar(select(func.count()).select_from(NegativeKeyword).where(NegativeKeyword.google_ads_account_id == acct.id)),
            "changes": {"last_30_days": len(changes), "outside_app": sum(1 for c in changes if c.made_outside_app)}}


def _count_by(rows, attr: str) -> dict:
    out: dict = {}
    for r in rows:
        k = getattr(r, attr) or "UNKNOWN"
        out[k] = out.get(k, 0) + 1
    return out


def module_inputs(db: Session, biz: Business, client: Client, module: str) -> dict:
    """Only what this module's conclusions depend on: editing anything else must not invalidate it."""
    svc = sorted(s.name for s in clients_service.active_services(biz))
    excluded = sorted(clients_service.excluded_services(biz))
    comps = sorted((c.name, c.website_url) for c in biz.competitors)
    areas = [a.get("label") for a in (biz.service_areas or [])]
    acct = accounts_service.account_for_business(db, biz)
    adata = account_data_version(db, acct)  # newer synced Google Ads data makes the modules that read it stale
    rep = _latest_report(db, biz)
    rid = rep.id if rep else None  # a newer research report makes the modules built on it stale
    feas = db.scalar(select(FeasibilityResult.id).where(FeasibilityResult.business_id == biz.id).order_by(FeasibilityResult.created_at.desc()))
    base = {
        "business_intelligence": {"name": biz.name, "category": biz.category, "services": svc, "excluded": excluded, "claims": sorted(biz.verified_claims or []),
                                  "notes": biz.notes, "weekly": client.weekly_conversion_target, "budget": biz.monthly_budget_usd, "phone": biz.phone, "report": rid,
                                  "email": biz.email, "form": biz.lead_form_url, "areas": areas},
        "website": {"website": biz.website_url, "form": biz.lead_form_url, "thanks": biz.thank_you_url, "services": svc,
                    "pages": sorted((s.name, s.landing_page_url) for s in biz.services), "areas": areas, "report": rid},
        "competitors": {"competitors": comps, "gbp": biz.gbp_url, "has_gbp": biz.has_gbp, "category": biz.category, "areas": areas, "ads_text": biz.competitor_ads_text, "report": rid},
        "search_intent": {"services": svc, "areas": areas, "category": biz.category, "report": rid},
        "keywords": {"services": svc, "excluded": excluded, "areas": areas, "website": biz.website_url, "competitors": comps, "report": rid, "account_data": adata},
        "geography": {"areas": areas, "states": sorted(biz.states or []), "mode": biz.targeting_mode, "radius": biz.default_radius_miles,
                      "hours": biz.business_hours, "excluded": excluded},
        "finance": {"budget": biz.monthly_budget_usd, "weekly": client.weekly_conversion_target, "services": svc, "feasibility": feas, "account_data": adata},
        "measurement": {"audit": (lambda m: m.id if m else None)(measurement.current(db, biz)), "website": biz.website_url, "thanks": biz.thank_you_url, "gtm": biz.gtm_id, "phone": biz.phone, "account": acct.id if acct else None},
        "account_policy": {"account": acct.id if acct else None, "customer": acct.customer_id if acct else None, "account_data": adata},
        "strategy": {"services": svc, "weekly": client.weekly_conversion_target},
    }
    return base[module]


def current_run(db: Session, business_id: str, module: str) -> IntelligenceRun | None:
    return db.scalar(select(IntelligenceRun).where(IntelligenceRun.business_id == business_id, IntelligenceRun.module == module,
                                                   IntelligenceRun.is_current.is_(True)))


def input_hash(db: Session, biz: Business, client: Client, module: str) -> str:
    """Own inputs + the input hashes of the runs this module consumes, so a change cascades downstream."""
    deps = {d: (current_run(db, biz.id, d).input_hash if current_run(db, biz.id, d) else None) for d in MODULES[module]["depends_on"]}
    return hashlib.sha256(canonical_json({"own": module_inputs(db, biz, client, module), "deps": deps}).encode()).hexdigest()


def stale_modules(db: Session, biz: Business, client: Client) -> list[str]:
    """Modules whose stored run no longer matches the current inputs (or that have never run). Computed in pipeline order so that a stale
    upstream module marks everything downstream stale without needing it to be re-run first."""
    stale, hypothetical = [], {}
    for m in MODULE_KEYS:
        run = current_run(db, biz.id, m)
        deps = {d: hypothetical.get(d) for d in MODULES[m]["depends_on"]}
        h = hashlib.sha256(canonical_json({"own": module_inputs(db, biz, client, m), "deps": deps}).encode()).hexdigest()
        hypothetical[m] = h
        if run is None or run.input_hash != h or run.status in ("FAILED", "NOT_STARTED"):
            stale.append(m)
    return stale


# ------------------------------------------------------------------------------------------------ evaluators
def _latest_report(db: Session, biz: Business) -> AuditReport | None:
    return db.scalar(select(AuditReport).where(AuditReport.business_id == biz.id, AuditReport.status == "COMPLETED").order_by(AuditReport.version.desc()))


def _report_items(rep: AuditReport | None, *sections: str) -> list[dict]:
    return [i for s in sections for i in ((rep.sections or {}).get(s, {}).get("findings", []) if rep else [])]


# Existing five-part report finding id -> specified check(s). The report already holds the evidence; this lifts it into the ten modules.
REPORT_MAP: dict[str, dict[str, list[str]]] = {
    "business_intelligence": {
        "business_understanding": ["client.services", "client.model", "client.promote"], "business_objectives": ["client.objective", "client.growth"],
        "service_offerings": ["client.services", "client.promote"], "target_audience": ["client.audience"], "usp": ["client.usp"], "pricing": ["client.pricing"],
        "profitability": ["client.margin", "client.aov", "client.ltv", "client.repeat"], "sales_process": ["client.sales_process", "client.conversion_path"],
        "capacity": ["client.capacity"], "service_coverage": ["client.locations", "client.not_served"], "restrictions": ["client.excluded"],
        "most_profitable_services": ["client.profitable"], "acceptable_acquisition_cost": ["client.cpl", "client.cac", "client.cpa"],
        "client_vs_website_conflicts": ["website.consistency", "website.nap_phone"], "customer_pain_points": ["competitors.pos.market"],
        "offer_competitiveness": ["competitors.pos.offers"]},
    "website": {
        "website_structure": ["website.pages"], "content_relevance": ["website.consistency", "website.geo_content"], "service_pages": ["website.service_pages"],
        "seed_keywords": ["keywords.sources"], "landing_page_suitability": ["website.lp_map", "website.content_quality"], "keyword_to_page_mapping": ["keywords.mapping", "website.lp_map"],
        "page_speed": ["website.speed_mobile", "website.speed_desktop"], "core_web_vitals": ["website.cwv_lcp_seconds", "website.cwv_inp_ms", "website.cwv_cls"],
        "mobile_responsiveness": ["website.mobile_meta"], "conversion_elements": ["website.cta", "website.forms", "website.tel"],
        "trust_signals": ["website.credentials", "website.testimonials", "website.privacy"], "content_quality": ["website.content_quality", "website.thin", "website.editorial"],
        "technical_accessibility": ["website.broken", "website.manual_tech"], "security": ["website.https"], "user_experience": ["website.popups", "website.cta_fold"],
        "contact_functionality": ["website.tel", "website.forms", "website.lead_form_reach"], "dedicated_page_per_service": ["website.service_pages"],
        "offer_visible": ["client.pricing"], "pricing_or_quote": ["client.pricing"], "unnecessary_steps": ["website.form_fields"], "geography_supported": ["website.geo_content"],
        "ad_promise_match": ["website.ad_match"], "broken_links_redirects": ["website.broken", "website.manual_tech"], "phone_forms_functional": ["website.broken_buttons", "website.forms"],
        "differentiators_communicated": ["competitors.pos.usp"], "keyword_theme_relevance": ["website.content_quality"], "mobile_usable": ["website.mobile_meta", "website.speed_mobile"],
        "enough_info_for_conversion": ["website.faq", "website.process"], "destination_policy": ["website.ads_policy", "website.claims"]},
    "competitors": {
        "competitor_discovery": ["competitors.provided", "competitors.discovered"], "competitor_websites": ["competitors.site", "competitors.content"],
        "competitor_offers": ["competitors.pos.offers"], "ad_messaging": ["competitors.ads"], "customer_reviews": ["competitors.gbp"], "google_business_profile": ["gbp."],
        "local_competition": ["competitors.advertisers"], "market_gaps": ["competitors.lacks", "competitors.pos.market"]},
    "keywords": {
        "src_client_services": ["keywords.cover"], "src_keyword_planner": ["keywords.metrics", "keywords.sources"], "metric_avg_searches": ["keywords.metrics"],
        "metric_bid_low": ["keywords.metrics"], "metric_bid_high": ["keywords.metrics"], "metric_estimated_cpc": ["keywords.avg_cpc"], "metric_trend": ["keywords.trends"],
        "grouping": ["keywords.cover", "keywords.overlap", "keywords.mapping"], "negative_keywords": ["keywords.negatives"], "filtering": ["keywords.high"]},
}


def _origin_for(item: dict) -> tuple[str, str]:
    """(origin, verification) of an existing report finding, from where it came from. Estimates stay estimates."""
    sid, ev = item["id"], item.get("evidence")
    if ev == "UNAVAILABLE":
        return "ai_assumption", "unknown"
    if sid.startswith("client."):
        return ("externally_researched", "inferred") if sid == "client.pricing" else ("client_provided", "client_provided")
    if sid.startswith(("competitors.", "gbp.")):
        return ("third_party_estimate", "third_party_estimate") if ev == "ESTIMATED" else ("externally_researched", "verified")
    if sid.startswith("keywords."):
        return ("google_ads_api", "forecast") if "bid" in item.get("check", "").lower() or ev == "ESTIMATED" else ("google_ads_api", "verified")
    return ("externally_researched", "inferred") if ev == "ESTIMATED" else ("externally_researched", "verified")


# The audit report's "critical" is a scoring weight. For READINESS only a destination that cannot receive traffic is critical on its own;
# everything else the report calls critical is a high-severity condition to resolve, not a reason to refuse to prepare a draft.
CRITICAL_REPORT_IDS = {"website.https", "website.lead_form_reach"}


def _from_report(seq: _Seq, check: str, item: dict, rep: AuditReport) -> dict:
    origin, verification = _origin_for(item)
    status = item["status"]
    result = {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL", "INFO": "INFO", "UNAVAILABLE": "UNAVAILABLE", "NA": "NA"}.get(status, "INFO")
    sev = item.get("severity") or ("info" if result in ("PASS", "INFO", "NA", "UNAVAILABLE") else "medium")
    if sev == "critical" and item["id"] not in CRITICAL_REPORT_IDS:
        sev = "high"
    lim = []
    if item.get("simulated"):
        lim.append("Produced from a simulated source: not evidence about this business.")
    if result == "UNAVAILABLE":
        lim.append(item.get("finding") or "Data unavailable")
        if item.get("needs"):
            lim.append(f"Needs: {item['needs']}")
    return make_finding(seq, check, item.get("check", check), item.get("finding", ""), result=result, severity=sev, origin=origin, verification=verification,
                        source_type=("client_intake" if origin == "client_provided" else "audit_report"),
                        source_reference=item.get("source") or f"audit report v{rep.version}", retrieved_at=(rep.finished_at.isoformat() if rep.finished_at else None),
                        evidence=[item.get("evidence_text") or str(item.get("evidence", ""))] if item.get("evidence") not in (None, "UNAVAILABLE") else [],
                        limitations=lim, recommendation=item.get("rec") or item.get("recommendation", ""), value={"report_finding_id": item["id"]})


def _not_assessed(seq: _Seq, check: str, why: str) -> dict:
    return make_finding(seq, check, check, "Not assessed.", result="UNAVAILABLE", origin="ai_assumption", verification="unknown", source_type="none",
                        limitations=[why], recommendation="")


def _match(items: list[dict], prefixes: list[str]) -> list[dict]:
    return [i for i in items if any(i["id"] == p or i["id"].startswith(p if p.endswith(".") else p + ".") for p in prefixes)]


class Ctx:
    """Everything an evaluator may read. Read-only: no evaluator calls an external API or writes outside its own run."""
    def __init__(self, db: Session, biz: Business, client: Client, research: bool = False, user_id: str | None = None):
        self.db, self.biz, self.client, self.research, self.user_id = db, biz, client, research, user_id
        self.report = _latest_report(db, biz)
        self.account = accounts_service.account_for_business(db, biz)
        self.measurement = measurement.current(db, biz)  # None when never audited or outdated
        self.feas = db.scalar(select(FeasibilityResult).where(FeasibilityResult.business_id == biz.id).order_by(FeasibilityResult.created_at.desc()))
        from app.models import Audit
        aud = db.scalar(select(Audit).where(Audit.business_id == biz.id, Audit.audit_type == "KEYWORD", Audit.status == "COMPLETED").order_by(Audit.created_at.desc()))
        self.kw_rows: list[dict] = (aud.result or {}).get("keywords", []) if aud else []     # selected/label/service/volume/bids (Keyword Planner)
        kwrep = ((self.report.sections or {}).get("keywords", {}).get("report") or {}) if self.report else {}
        self.intent_rows: list[dict] = kwrep.get("rows", [])                                  # keyword + intent per row (audit report)
        self.geography = "; ".join(a.get("label", "") for a in (biz.service_areas or [])[:3])


def _run_dict(findings, coverage, **extra) -> dict:
    return {"findings": findings, "coverage": coverage, **{k: extra.get(k, []) for k in
            ("missing_information", "contradictions", "risks", "recommendations", "client_actions", "research_performed", "data_sources")},
            "decision": extra.get("decision", ""), "summary": extra.get("summary", ""), "source_report_id": extra.get("source_report_id")}


def _mapped(module: str, ctx: Ctx, seq: _Seq, sections: tuple[str, ...]) -> tuple[list[dict], dict, list[str]]:
    """Findings lifted from the audit report for this module, plus the coverage map. Returns (findings, coverage, check keys left to evaluators)."""
    items = _report_items(ctx.report, *sections)
    findings, coverage, left = [], {}, []
    for chk in MODULES[module]["checks"]:
        ids = REPORT_MAP.get(module, {}).get(chk["key"], [])
        hit = _match(items, ids) if ids and ctx.report else []
        if hit:
            findings += [_from_report(seq, chk["key"], i, ctx.report) for i in hit]
            coverage[chk["key"]] = "evaluated"
        else:
            left.append(chk["key"])
    return findings, coverage, left


def _finish(module: str, ctx: Ctx, seq: _Seq, findings: list, coverage: dict, left: list[str], why_missing: dict[str, str] | None = None, **extra) -> dict:
    for k in left:
        if k not in coverage:
            findings.append(_not_assessed(seq, k, (why_missing or {}).get(k) or "No automated evaluator or data source for this check yet."))
            coverage[k] = "not_assessed"
    bad = [f for f in findings if f["result"] == "FAIL"]
    miss = [{"check": f["category"], "what": f["subject"], "needs": next((lim for lim in f["limitations"] if lim.startswith("Needs:")), "")} for f in findings if f["result"] == "UNAVAILABLE"]
    recs = [{"finding_id": f["finding_id"], "recommendation": f["recommendation"], "reason": f["reason"], "severity": f["severity"]} for f in findings if f["recommendation"] and f["result"] in ("FAIL", "WARN")]
    risks = [{"finding_id": f["finding_id"], "risk": f["finding"], "severity": f["severity"]} for f in findings if f["result"] in ("FAIL", "WARN") and f["severity"] in ("critical", "high")]
    r = _run_dict(findings, coverage, missing_information=extra.pop("missing_information", miss), recommendations=recs, risks=risks,
                  source_report_id=ctx.report.id if ctx.report else None, **extra)
    n_eval = sum(1 for v in coverage.values() if v == "evaluated")
    if bad and any(f["severity"] == "critical" for f in bad):
        status = "BLOCKED"
    elif n_eval == 0:
        status = "WAITING_FOR_INTEGRATION" if module in ("account_policy",) else "NOT_STARTED"
    elif n_eval < max(1, len(coverage) // 2):
        status = "PARTIALLY_COMPLETED"
    elif bad or any(f["result"] in ("WARN",) for f in findings) or any(v == "not_assessed" for v in coverage.values()):
        status = "COMPLETED_WITH_WARNINGS"
    else:
        status = "COMPLETED"
    r["status"] = extra.get("status") or status
    return r


def ev_business_intelligence(ctx: Ctx, seq: _Seq) -> dict:
    f, cov, left = _mapped("business_intelligence", ctx, seq, ("client",))
    cl = ctx.client
    if "highest_demand_services" in left and ctx.kw_rows:
        by = {}
        for r in ctx.kw_rows:
            if r.get("service") and r.get("avg_monthly_searches") is not None:
                by[r["service"]] = by.get(r["service"], 0) + r["avg_monthly_searches"]
        if by:
            top = sorted(by.items(), key=lambda kv: -kv[1])
            f.append(make_finding(seq, "highest_demand_services", "Search demand by service", "Keyword Planner demand by service: " + ", ".join(f"{k} {int(v):,}/mo" for k, v in top),
                                  result="INFO", origin="google_ads_api", verification="forecast", source_type="keyword_planner", source_reference="audit report keyword table",
                                  limitations=["Demand is not profitability: the highest-volume service is not automatically the best opportunity."], value=dict(top)))
            cov["highest_demand_services"] = "evaluated"
    if "client_priority_services" in left:
        f.append(make_finding(seq, "client_priority_services", "Client priority", "The intake form does not capture a priority order; all active services are treated equally.",
                              result="UNAVAILABLE", origin="client_provided", verification="unknown", source_type="client_intake", limitations=["Needs: client"]))
        cov["client_priority_services"] = "evaluated"
    if "lead_vs_qualified_lead" in left:
        f.append(make_finding(seq, "lead_vs_qualified_lead", "Qualified lead definition", "No definition of a qualified lead has been supplied.", result="UNAVAILABLE",
                              origin="client_provided", verification="unknown", source_type="client_intake", limitations=["Needs: client"],
                              recommendation="Ask the client what makes a lead qualified (service, area, budget, timing)."))
        cov["lead_vs_qualified_lead"] = "evaluated"
    if "fulfilment_ability" in left:
        f.append(make_finding(seq, "fulfilment_ability", "Fulfilment", "Capacity to fulfil demand is not recorded." if not cl.weekly_conversion_target else
                              f"Client target of {cl.weekly_conversion_target:g} conversions/week is recorded, but capacity to serve them is not.", result="UNAVAILABLE",
                              origin="client_provided", verification="unknown", source_type="client_intake", limitations=["Needs: client"]))
        cov["fulfilment_ability"] = "evaluated"
    if "missing_material_information" in left:
        miss = [k for k, v in cov.items() if v == "evaluated" and any(x["category"] == k and x["result"] == "UNAVAILABLE" for x in f)]
        f.append(make_finding(seq, "missing_material_information", "Missing information", f"{len(miss)} business-intelligence item(s) are unknown: {', '.join(miss[:8])}" if miss else "No material gaps recorded.",
                              result="WARN" if miss else "PASS", severity="medium" if miss else "info", origin="ai_recommendation", verification="inferred", source_type="intelligence_engine",
                              recommendation="Collect these from the client; none is invented." if miss else ""))
        cov["missing_material_information"] = "evaluated"
    contradictions = [{"finding_id": x["finding_id"], "detail": x["finding"]} for x in f if x["category"] == "client_vs_website_conflicts" and x["result"] in ("WARN", "FAIL")]
    r = _finish("business_intelligence", ctx, seq, f, cov, left, contradictions=contradictions,
                research_performed=["Client intake review", "Website consistency comparison" if ctx.report else "(no audit run yet)"],
                data_sources=["client intake form"] + (["website crawl"] if ctx.report else []))
    r["client_actions"] = [{"ask": x["recommendation"] or x["subject"], "finding_id": x["finding_id"]} for x in f if "Needs: client" in " ".join(x["limitations"])][:15]
    return r


def ev_website(ctx: Ctx, seq: _Seq) -> dict:
    f, cov, left = _mapped("website", ctx, seq, ("website", "client", "competitors", "keywords"))
    scores = []
    if ctx.report:
        for s in (ctx.report.sections or {}).get("website", {}).get("findings", []):
            if s["id"] in ("website.lp_map", "website.content_quality"):
                scores.append({"item": s["check"], "status": s["status"], "evidence": s.get("finding", "")})
        if scores:
            f.append(make_finding(seq, "landing_page_scores", "Landing-page suitability", "; ".join(f"{s['item']}: {s['status']}" for s in scores), result="INFO",
                                  origin="externally_researched", verification="inferred", source_type="audit_report", source_reference=f"audit report v{ctx.report.version}",
                                  evidence=[s["evidence"] for s in scores], limitations=["Per-page numeric scoring is not produced yet; statuses come from the audit checks."]))
            cov["landing_page_scores"] = "evaluated"
    left = [k for k in left if k != "landing_page_scores" or "landing_page_scores" not in cov]
    return _finish("website", ctx, seq, f, cov, left, research_performed=["Website crawl", "PageSpeed Insights"] if ctx.report else [],
                   data_sources=["website crawler", "PageSpeed Insights"] if ctx.report else [])


def ev_competitors(ctx: Ctx, seq: _Seq) -> dict:
    f, cov, left = _mapped("competitors", ctx, seq, ("competitors", "gbp"))
    why = {"semrush_domain_overview": "SEMrush is not connected. Its figures would be third-party estimates, never Google Ads data.",
           "semrush_advertising_research": "SEMrush is not connected.", "semrush_keyword_gap": "SEMrush is not connected.",
           "competitor_landing_pages": "Competitor paid landing pages are observable only through SERP/SEMrush research, which has not been run.",
           "auction_insights": "Auction Insights needs a linked account with eligible campaigns and enough data; it is not a pre-launch tool."}
    if ctx.biz.gbp_url and "google_business_profile" in left:
        f.append(make_finding(seq, "google_business_profile", "Supplied profile link", "A Google Business Profile link was supplied at intake and is available for public-profile research; it has not been read yet.",
                              result="INFO", origin="client_provided", verification="client_provided", source_type="client_intake", source_reference=ctx.biz.gbp_url,
                              limitations=["Profile data is read from public sources, not from the Business Profile API."]))
        cov["google_business_profile"] = "evaluated"
    return _finish("competitors", ctx, seq, f, cov, left, why, research_performed=["Competitor website crawl", "Places lookup"] if ctx.report else [],
                   data_sources=["competitor websites", "Google Places"] if ctx.report else [])


def ev_search_intent(ctx: Ctx, seq: _Seq) -> dict:
    f, cov, left = [], {}, [c["key"] for c in MODULES["search_intent"]["checks"]]
    if ctx.intent_rows:
        counts: dict[str, int] = {}
        examples: dict[str, list[str]] = {}
        for r in ctx.intent_rows:
            it = r.get("intent") or "unclassified"
            counts[it] = counts.get(it, 0) + 1
            examples.setdefault(it, []).append(r["keyword"])
        n = sum(counts.values())

        def add(chk, text, key, **kw):
            f.append(make_finding(seq, chk, chk, text, result=kw.get("result", "INFO"), origin="ai_recommendation", verification="inferred", source_type="keyword_intent_rules",
                                  source_reference="audit report keyword table", value={"count": counts.get(key, 0), "examples": examples.get(key, [])[:8]},
                                  limitations=["Rule-based classification of keyword text. It is an analytical label, not a Google Ads match type."]))
            cov[chk] = "evaluated"
        f.append(make_finding(seq, "keyword_intent_classification", "Every researched keyword", f"{n} keyword(s) classified: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())),
                              result="INFO", origin="ai_recommendation", verification="inferred", source_type="keyword_intent_rules", source_reference="audit report keyword table",
                              value={"counts": counts}, limitations=["Rule-based classification of keyword text."]))
        cov["keyword_intent_classification"] = "evaluated"
        add("transactional_intent", f"{counts.get('transactional', 0)} transactional keyword(s)", "transactional")
        add("informational_intent", f"{counts.get('informational', 0)} informational keyword(s) (candidates for exclusion unless the objective is awareness)", "informational")
        add("commercial_investigation", f"{counts.get('commercial_investigation', 0)} commercial-investigation keyword(s)", "commercial_investigation")
        add("brand_vs_nonbrand", f"{counts.get('brand', 0)} brand and {counts.get('competitor', 0)} competitor keyword(s)", "brand")
        near = [r["keyword"] for r in ctx.intent_rows if r.get("intent") == "local" or "near me" in r["keyword"].lower()]
        f.append(make_finding(seq, "local_vs_nonlocal", "Local intent", f"{len(near)} keyword(s) with explicit local intent ('near me').", result="INFO", origin="ai_recommendation",
                              verification="inferred", source_type="keyword_intent_rules", value={"examples": near[:8]}))
        cov["local_vs_nonlocal"] = "evaluated"
        emerg = [r["keyword"] for r in ctx.intent_rows if any(w in r["keyword"].lower() for w in ("emergency", "24 hour", "24/7", "same day", "urgent"))]
        f.append(make_finding(seq, "emergency_vs_non", "Emergency intent", f"{len(emerg)} keyword(s) signal urgency.", result="INFO", origin="ai_recommendation",
                              verification="inferred", source_type="keyword_intent_rules", value={"examples": emerg[:8]},
                              limitations=["Only advertise emergency service if the client can answer 24/7: check business hours."]))
        cov["emergency_vs_non"] = "evaluated"
    why = {k: "Needs customer research (reviews, client interviews, search suggestions) that has not been run." for k in left}
    return _finish("search_intent", ctx, seq, f, cov, [k for k in left if k not in cov], why,
                   research_performed=["Rule-based intent classification of researched keywords"] if ctx.intent_rows else [],
                   data_sources=["Keyword Planner results (audit report)"] if ctx.intent_rows else [])


def ev_keywords(ctx: Ctx, seq: _Seq) -> dict:
    f, cov, left = _mapped("keywords", ctx, seq, ("keywords",))
    rows = ctx.kw_rows
    if rows:
        sel = [r for r in rows if r.get("selected")]
        have = [r for r in rows if r.get("avg_monthly_searches") is not None]
        f.append(make_finding(seq, "classification", "Keyword classification", f"{len(rows)} researched, {len(sel)} selected; labels: " +
                              ", ".join(f"{lab} {sum(1 for r in rows if r.get('label') == lab)}" for lab in sorted({r.get('label') for r in rows if r.get('label')})),
                              result="INFO", origin="google_ads_api", verification="verified", source_type="keyword_planner", source_reference="audit report keyword table",
                              value={"researched": len(rows), "selected": len(sel)}))
        cov["classification"] = "evaluated"
        f.append(make_finding(seq, "metric_avg_searches", "Metric availability", f"Average monthly searches returned for {len(have)} of {len(rows)} keyword(s); the rest are unavailable, not zero.",
                              result="PASS" if len(have) == len(rows) else "WARN", severity="low", origin="google_ads_api", verification="verified", source_type="keyword_planner",
                              source_reference="audit report keyword table", limitations=["Keyword Planner estimates; averages can include close variants."]))
        cov["metric_avg_searches"] = "evaluated"
    facts = account_facts(ctx.db, ctx.account) if ctx.account else None
    if facts:
        ref = f"synced Google Ads data, customer {facts['customer_id']}"
        for chk, text in (("src_existing_keywords", f"{facts['keywords']['enabled']} enabled keyword(s) already in the account"),
                          ("src_search_terms", f"{facts['search_terms']['terms']} search term(s) from the last 30 days"),
                          ("src_account_history", f"90-day history: {facts['d90']['clicks']:,} clicks, {facts['d90']['conversions']:g} conversions")):
            f.append(make_finding(seq, chk, chk, text, result="INFO", origin="google_ads_api", verification="verified", source_type="google_ads_api", source_reference=ref,
                                  retrieved_at=facts["retrieved_at"], geography=ctx.geography))
            cov[chk] = "evaluated"
    why = {"src_semrush_magic": "SEMrush is not connected.", "src_semrush_competitors": "SEMrush is not connected.", "src_google_suggest": "Search suggestions have not been collected.",
           "src_existing_keywords": "Needs a linked Google Ads account.", "src_search_terms": "Needs a linked Google Ads account with serving history.",
           "src_account_history": "Needs a linked Google Ads account with history.", "forecasts": "Keyword Planner forecasts have not been requested for this plan.",
           "keyword_planner_independence": ""}
    f.append(make_finding(seq, "keyword_planner_independence", "Research customer", "By design, keyword research runs under the separately configured research customer and does not need this client's deployment account.",
                          result="PASS", origin="ai_recommendation", verification="inferred", source_type="application_design", source_reference="integrations: keyword_planner.research_customer_id"))
    cov["keyword_planner_independence"] = "evaluated"
    left = [k for k in left if k != "keyword_planner_independence"]
    return _finish("keywords", ctx, seq, f, cov, left, why, research_performed=["Keyword Planner ideas and metrics"] if rows else [],
                   data_sources=["Google Keyword Planner"] if rows else [])


def ev_geography(ctx: Ctx, seq: _Seq) -> dict:
    biz, f, cov = ctx.biz, [], {}

    def add(chk, text, *, result="INFO", origin="client_provided", verification="client_provided", severity="info", ref="client intake", **kw):
        f.append(make_finding(seq, chk, chk, text, result=result, severity=severity, origin=origin, verification=verification, source_type="client_intake" if origin == "client_provided" else "intelligence_engine",
                              source_reference=ref, geography=ctx.geography, **kw))
        cov[chk] = "evaluated"
    areas = biz.service_areas or []
    states = biz.states or []
    add("target_countries", "United States" + ("" if states else " (nationwide: no state limit set)"), origin="ai_assumption", verification="assumption",
        limitations=["Country is assumed from US states/ZIP codes in the intake; confirm if the business serves other countries."])
    add("target_regions", ", ".join(states) if states else "No state is set: same-named cities in other states could be targeted.", result="PASS" if states else "WARN",
        severity="low" if not states else "info", recommendation="" if states else "Add the state(s) so ads do not show in same-named cities elsewhere.")
    add("cities_zips", f"{len(areas)} area(s): " + ", ".join(a.get("label", "") for a in areas[:8]) if areas else "No service area defined.", result="PASS" if areas else "FAIL",
        severity="info" if areas else "high", recommendation="" if areas else "Add at least one city or ZIP code.")
    add("radius_targeting", f"Mode {biz.targeting_mode}" + (f", {biz.default_radius_miles:g}-mile radius around the first area" if biz.targeting_mode == "RADIUS" else ""),
        result="WARN" if biz.targeting_mode == "RADIUS" and (biz.default_radius_miles or 0) > 50 else "INFO",
        severity="medium" if biz.targeting_mode == "RADIUS" and (biz.default_radius_miles or 0) > 50 else "info",
        recommendation="A radius above 50 miles is wide for a local service: confirm the business really serves it." if biz.targeting_mode == "RADIUS" and (biz.default_radius_miles or 0) > 50 else "")
    add("service_availability", "Coverage is stated by the client; it is not independently verified.", verification="client_provided", limitations=["Cross-check with website service-area pages and the business profile."])
    add("excluded_locations", "No excluded locations recorded." , result="UNAVAILABLE", verification="unknown", limitations=["Needs: client"], recommendation="Ask which areas the business cannot serve.")
    add("hours_availability", "Business hours recorded." if biz.business_hours else "No business hours: call assets and emergency messaging cannot be limited to open hours.",
        result="PASS" if biz.business_hours else "WARN", severity="low" if not biz.business_hours else "info",
        recommendation="" if biz.business_hours else "Record opening hours before advertising emergency or call-based services.")
    mode_text = ("Recommend 'Presence' (people physically in the targeted area): the business serves customers at their location, so people only interested in the area are less likely to be buyers."
                 if biz.business_type in ("home_services", "dental", "legal", "default") else "Evaluate Presence vs Presence-or-interest for this business type.")
    add("location_option", mode_text, origin="ai_recommendation", verification="inferred", ref="Google Ads location options",
        reason="Local service businesses lose budget on out-of-area interest traffic.", limitations=["A recommendation from business type, not a measured result. Confirm with the client."])
    add("language", "English assumed; the website language was not independently detected.", origin="ai_assumption", verification="assumption", limitations=["Confirm the customers' search language."])
    why = {"search_demand_by_location": "Needs location-level Keyword Planner metrics (not requested yet).", "competition_by_location": "Needs local SERP/auction research.",
           "local_search_trends": "Needs location-level trend data.", "demand_differences": "Needs per-city Keyword Planner metrics.", "seasonal_demand": "Needs monthly search history per location.",
           "local_regulations": "Needs the client to confirm licensing/regulatory limits.", "travel_constraints": "Needs the client to confirm travel limits.",
           "geographic_intent": "Needs location-level query research."}
    left = [c["key"] for c in MODULES["geography"]["checks"] if c["key"] not in cov]
    return _finish("geography", ctx, seq, f, cov, left, why, research_performed=["Intake geography review"], data_sources=["client intake"])


def _assumptions(ctx: Ctx) -> finance.Assumptions:
    a = finance.Assumptions()
    if ctx.biz.monthly_budget_usd:
        a.monthly_budget = finance.Input(ctx.biz.monthly_budget_usd, "client", "supplied at intake")
    bids = [((r.get("low_top_of_page_bid_micros") or 0) + (r.get("high_top_of_page_bid_micros") or 0)) / 2e6 for r in ctx.kw_rows
            if r.get("selected") and r.get("high_top_of_page_bid_micros")]
    if ctx.feas and (ctx.feas.inputs or {}).get("cpc_range"):
        inp = ctx.feas.inputs
        a.expected_cpc = finance.Input(inp["cpc_range"][1], "estimated" if inp.get("basis") != "account_history_90d" else "measured", f"basis: {inp.get('basis')}")
        a.landing_page_cvr = finance.Input(inp["cvr_range"][1], "measured" if inp.get("basis") == "account_history_90d" else "assumed", f"basis: {inp.get('basis')}")
    elif bids:
        import statistics
        a.expected_cpc = finance.Input(statistics.median(bids), "estimated", "median of Keyword Planner top-of-page bid midpoints")
    return a


def ev_finance(ctx: Ctx, seq: _Seq) -> dict:
    f, cov = [], {}
    a = _assumptions(ctx)
    cl = ctx.client

    def add(chk, text, *, result="INFO", origin="ai_recommendation", verification="inferred", source_type="financial_engine", **kw):
        f.append(make_finding(seq, chk, chk, text, result=result, origin=origin, verification=verification, source_type=source_type, geography=ctx.geography, **kw))
        cov[chk] = "evaluated"
    add("weekly_conversion_target", f"{cl.weekly_conversion_target:g} conversions/week (client-provided)" if cl.weekly_conversion_target else "Not supplied (optional).",
        result="INFO" if cl.weekly_conversion_target else "UNAVAILABLE", origin="client_provided", verification="client_provided" if cl.weekly_conversion_target else "unknown",
        source_type="client_intake", limitations=[] if cl.weekly_conversion_target else ["Needs: client (optional)"])
    add("monthly_budget", f"${ctx.biz.monthly_budget_usd:,.0f}/month (client-supplied)" if ctx.biz.monthly_budget_usd else "Not supplied: a budget is recommended from the analysis.",
        origin="client_provided", verification="client_provided" if ctx.biz.monthly_budget_usd else "unknown", source_type="client_intake")
    if a.expected_cpc.value is not None:
        add("estimated_cpc_range", f"Planning CPC ${a.expected_cpc.value:.2f} ({a.expected_cpc.note}). A top-of-page bid estimate, not an actual CPC.", origin="google_ads_api",
            verification="forecast" if a.expected_cpc.source == "estimated" else "verified", source_type="keyword_planner")
    else:
        add("estimated_cpc_range", "No CPC estimate yet: Keyword Planner metrics are unavailable.", result="UNAVAILABLE", origin="google_ads_api", verification="unknown",
            source_type="keyword_planner", limitations=["Needs: Keyword Planner research (configure the research customer)"])
    rec = ctx.feas.summary if ctx.feas else None
    if rec:
        add("budget_recommendation", f"Recommended ${rec.get('recommended_monthly_budget') or rec.get('monthly_budget'):,.0f}/month (~${(rec.get('daily_budget') or 0):,.2f}/day); status {ctx.feas.status}. "
            f"Expected {rec.get('expected_weekly_conversions')} leads/week (range {rec.get('realistic_weekly_range')}). ESTIMATE.", origin="ai_recommendation", verification="forecast",
            source_type="feasibility_engine", source_reference=f"feasibility result {ctx.feas.id}", limitations=["Planning arithmetic from stated assumptions, not a promise."],
            value={"daily": rec.get("daily_budget"), "monthly": rec.get("recommended_monthly_budget") or rec.get("monthly_budget")})
        add("three_scenarios", "; ".join(f"{k}: {v.get('weekly_conversions')} leads/wk @ ${v.get('cost_per_lead')} CPL" for k, v in (ctx.feas.scenarios or {}).items()),
            origin="ai_recommendation", verification="forecast", source_type="feasibility_engine", value=ctx.feas.scenarios)
    else:
        add("budget_recommendation", "Not produced yet: run a campaign preparation task to run feasibility.", result="UNAVAILABLE", verification="unknown", limitations=["Needs: preparation task"])
    if a.expected_cpc.value is not None and a.monthly_budget.value:
        out = finance.analyse(a)
        add("affordability", "; ".join(out["affordability"]["reasons"]), result="INFO", value=out["affordability"], limitations=["Needs client target CAC and lead-to-customer rate for a real affordability verdict."]
            if out["affordability"]["verdict"] == "unknown" else [])
        add("sensitivity", f"Largest driver: {out['sensitivity']['largest_driver']}" if out["sensitivity"]["largest_driver"] else "Needs revenue inputs.", value=out["sensitivity"]["rows"][:6])
        add("budget_sufficiency", "; ".join(c["detail"] for c in out["sufficiency"]["checks"]) or "Cannot assess.", result="INFO" if out["sufficiency"]["sufficient"] else "WARN",
            severity="medium" if not out["sufficiency"]["sufficient"] else "info", value=out["sufficiency"])
    facts = account_facts(ctx.db, ctx.account) if ctx.account else None
    if facts and facts["d90"]["days_with_data"]:
        d = facts["d90"]
        f.append(make_finding(seq, "historical_performance", "Measured 90-day performance", f"90 days: ${d['cost']:,.2f} spent, {d['clicks']:,} clicks, {d['conversions']:g} conversions"
                              + (f", CPC ${d['cpc']:.2f}" if d["cpc"] else "") + (f", conversion rate {d['cvr']:.1%}" if d["cvr"] else "") + ". Measured actuals: the best basis for CPC and conversion-rate assumptions.",
                              result="INFO", origin="google_ads_api", verification="verified", source_type="google_ads_api",
                              source_reference=f"synced Google Ads data, customer {facts['customer_id']}", retrieved_at=facts["retrieved_at"], value=d))
        cov["historical_performance"] = "evaluated"
    why = {"max_daily_spend": "Not collected at intake.", "target_cpa": "Not collected at intake.", "target_cpl": "Not collected at intake.", "target_cac": "Needs: client",
           "avg_customer_value": "Needs: client", "avg_profit_per_customer": "Needs: client", "lead_to_customer_rate": "Needs: client", "landing_page_cvr": "Measured after launch; planning value is an assumption.",
           "expected_impressions_clicks": "Needs a Keyword Planner forecast.", "seasonal_demand": "Needs monthly search history.", "keyword_competition": "See keyword module.",
           "business_capacity": "Needs: client", "historical_performance": "Needs a linked account with history."}
    left = [c["key"] for c in MODULES["finance"]["checks"] if c["key"] not in cov]
    return _finish("finance", ctx, seq, f, cov, left, why, research_performed=["Deterministic financial engine"], data_sources=["client intake", "Keyword Planner", "feasibility engine"])


# measurement-audit finding key -> specified check(s) of this stage
MEASUREMENT_MAP = {"conversion_actions": ["ads_conversion_tracking"], "account": ["ads_conversion_tracking"], "auto_tagging": ["ads_conversion_tracking"],
                   "google_tag": ["google_tag", "google_tag_manager"], "primary_conversion": ["primary_conversions", "form_submissions"], "thank_you_page": ["form_submissions"],
                   "call_conversion": ["phone_calls"], "counting": ["duplicate_conversions"], "duplicates": ["duplicate_conversions"], "conversion_values": ["conversion_values"],
                   "attribution": ["attribution"], "enhanced_conversions": ["offline_conversions"], "recorded_conversions": ["measurement_readiness"]}
_M_RESULT = {"ok": "PASS", "issue": "FAIL", "attention": "WARN", "not_assessable": "UNAVAILABLE"}


def ev_measurement(ctx: Ctx, seq: _Seq) -> dict:
    """The measurement audit's findings, lifted into this stage. There is no overall state: each finding stands on its own evidence."""
    f, cov = [], {}
    ma = ctx.measurement
    if ma is None and ctx.research:  # reads the website (and the account when linked); only when research is requested
        ma = ctx.measurement = measurement.audit(ctx.db, ctx.biz, None)
    keys = [c["key"] for c in MODULES["measurement"]["checks"]]
    if ma is None:
        return _finish("measurement", ctx, seq, f, cov, keys, {k: "The measurement audit has not been run (or predates the current account link)." for k in keys},
                       status="NOT_STARTED", summary="No current measurement audit.")
    when, ref = ma.evaluated_at.isoformat(), f"measurement audit {ma.evaluated_at.date()}"
    for x in ma.findings:
        for chk in MEASUREMENT_MAP.get(x["key"], []):
            res = _M_RESULT[x["result"]]
            site = x["key"] in ("google_tag", "thank_you_page")
            f.append(make_finding(seq, chk, x["inspected"], f"{x['actual']}." + (f" {x['impact']}" if x["impact"] else ""), result=res,
                                  severity=(x["severity"] or "medium") if res == "FAIL" else ("low" if res == "WARN" else "info"),
                                  origin="externally_researched" if site else "google_ads_api", verification="verified" if res != "UNAVAILABLE" else "unknown",
                                  source_type="measurement_audit", source_reference=ref, retrieved_at=when, evidence=[x["evidence"]], recommendation=x["action"],
                                  limitations=[f"Needs: {x['dependency']}"] if x["dependency"] else [],
                                  value={"measurement_key": x["key"], "action_type": x["action_type"]}))
            cov[chk] = "evaluated"
    ok, why = measurement.supports_conversion_bidding(ma.facts)
    f.append(make_finding(seq, "primary_conversions", "Conversion-based bidding", why, result={True: "PASS", False: "WARN", None: "UNAVAILABLE"}[ok],
                          severity="medium" if ok is False else "info", origin="ai_recommendation", verification="inferred" if ok is not None else "unknown",
                          source_type="measurement_audit", source_reference=ref, retrieved_at=when, value={"conversion_bidding_supported": ok, "facts": ma.facts},
                          recommendation="Use click-based bidding with a CPC ceiling until a conversion action records leads." if ok is False else ""))
    cov["primary_conversions"] = "evaluated"
    why_not = {k: "Not read by the measurement audit." for k in ("ga4", "purchase_tracking", "booking_tracking", "secondary_conversions", "consent_privacy", "crm_integration")}
    left = [k for k in keys if k not in cov]
    assessable = any(x["result"] != "not_assessable" for x in ma.findings if x["key"] not in ("google_tag", "thank_you_page"))
    return _finish("measurement", ctx, seq, f, cov, left, why_not, research_performed=["Conversion measurement audit"], data_sources=["website", "Google Ads (when linked)"],
                   **({} if assessable else {"status": "WAITING_FOR_INTEGRATION", "summary": "Account-side measurement cannot be read without a linked Google Ads account. Nothing is blocked by this."}))


def ev_account_policy(ctx: Ctx, seq: _Seq) -> dict:
    left = [c["key"] for c in MODULES["account_policy"]["checks"]]
    if ctx.account is None:
        why = {k: "Unavailable: no Google Ads account is linked to this client. Link an account to run the account audit." for k in left}
        return _finish("account_policy", ctx, seq, [], {}, left, why, status="WAITING_FOR_INTEGRATION",
                       summary="No Google Ads account linked. This is not a blocker for preparation; historical performance is Unavailable, not zero.")
    a, f, cov = ctx.account, [], {}
    f.append(make_finding(seq, "account_connection", f"Customer {a.customer_id}", f"Linked Google Ads account {a.customer_id}.",
                          result="PASS", origin="google_ads_api", verification="verified", source_type="google_ads_api", source_reference=f"customer {a.customer_id}"))
    cov["account_connection"] = "evaluated"
    status = getattr(a, "status", "UNKNOWN")
    f.append(make_finding(seq, "account_status", "Account status", f"Account status: {status}.", result="PASS" if status == "ENABLED" else "WARN",
                          severity="info" if status == "ENABLED" else "high", origin="google_ads_api", verification="verified", source_type="google_ads_api", source_reference=f"customer {a.customer_id}",
                          recommendation="" if status == "ENABLED" else "Resolve the account status before deployment."))
    cov["account_status"] = "evaluated"
    facts = account_facts(ctx.db, a)
    if facts:
        ref, when = f"synced Google Ads data, customer {a.customer_id}", facts["retrieved_at"]

        def fact(chk, text, result="INFO", **kw):
            f.append(make_finding(seq, chk, chk, text, result=result, origin="google_ads_api", verification="verified", source_type="google_ads_api",
                                  source_reference=ref, retrieved_at=when, geography=ctx.geography, **kw))
            cov[chk] = "evaluated"
        d30, d90 = facts["d30"], facts["d90"]
        fact("historical_performance", f"Last 30 days ({d30['days_with_data']} days with data): {d30['impressions']:,} impressions, {d30['clicks']:,} clicks, ${d30['cost']:,.2f}, "
             f"{d30['conversions']:g} conversions" + (f", CPC ${d30['cpc']:.2f}" if d30["cpc"] else "") + (f", cost per lead ${d30['cpl']:.2f}" if d30["cpl"] else "") +
             f". Last 90 days: ${d90['cost']:,.2f}, {d90['conversions']:g} conversions.", value={"d30": d30, "d90": d90})
        cp = facts["campaigns"]
        fact("existing_campaigns", f"{cp['total']} campaign(s): " + ", ".join(f"{v} {k.lower()}" for k, v in cp["by_status"].items()) + "; types " + ", ".join(cp["by_type"]), value=cp)
        fact("campaign_settings", "Campaign types: " + ", ".join(f"{k} {v}" for k, v in cp["by_type"].items()), value=cp["by_type"])
        fact("budget_bidding_config", f"Enabled monthly budget ${cp['monthly_budget']:,.0f}; bidding: " + ", ".join(f"{k} {v}" for k, v in cp["by_bidding"].items()), value=cp["by_bidding"])
        st = facts["structure"]
        fact("ad_groups_ads_assets", f"{st['ad_groups']} ad group(s), {st['ads']} ad(s), {st['keywords']} keyword(s). Assets (sitelinks, callouts) are not synced.",
             limitations=["Asset-level data is not synced."])
        kw = facts["keywords"]
        fact("existing_keywords", f"{kw['enabled']} enabled keyword(s); match types " + ", ".join(f"{k} {v}" for k, v in kw["match_types"].items()), value=kw)
        sx = facts["search_terms"]
        fact("search_terms", f"{sx['terms']} search term(s) in 30 days costing ${sx['cost']:,.2f}; ${sx['no_conversion_cost']:,.2f} on terms with no conversion in 90 days.",
             result="WARN" if sx["no_conversion_cost"] > 0.2 * max(sx["cost"], 1) else "INFO", severity="medium" if sx["no_conversion_cost"] > 0.2 * max(sx["cost"], 1) else "info",
             recommendation="Review the non-converting terms for exclusion." if sx["no_conversion_cost"] > 0.2 * max(sx["cost"], 1) else "", value=sx)
        fact("negative_keywords", f"{facts['negatives']} negative keyword(s) are in place.", value=facts["negatives"])
        fact("conversion_history", f"{d90['conversions']:g} conversions in 90 days" + (f" at ${d90['cpl']:.2f} each" if d90["cpl"] else "") +
             ".", value=d90)
        ch = facts["changes"]
        fact("change_history", f"{ch['last_30_days']} account change(s) in 30 days, {ch['outside_app']} made outside this app.",
             result="WARN" if ch["outside_app"] else "INFO", severity="low" if ch["outside_app"] else "info",
             recommendation="Review changes made outside the app before optimising." if ch["outside_app"] else "", value=ch)
    from app.models import Audit
    aud = ctx.db.scalar(select(Audit).where(Audit.business_id == ctx.biz.id, Audit.audit_type == "ACCOUNT", Audit.status == "COMPLETED").order_by(Audit.created_at.desc()))
    if aud:
        res = aud.result or {}
        f.append(make_finding(seq, "historical_performance", "90-day account audit", aud.summary or "Account audit completed.", result="WARN" if res.get("findings") else "PASS",
                              severity="medium", origin="google_ads_api", verification="verified", source_type="google_ads_api", retrieved_at=aud.finished_at.isoformat() if aud.finished_at else None,
                              source_reference=f"account audit {aud.id}", evidence=list(res.get("findings", []))[:10]))
        cov["historical_performance"] = "evaluated"
        cov["existing_campaigns"] = "evaluated"
        f.append(make_finding(seq, "existing_campaigns", "Campaign inventory", f"{len(res.get('campaigns', []))} campaign(s) read over {res.get('window_days', 90)} days.", result="INFO",
                              origin="google_ads_api", verification="verified", source_type="google_ads_api", source_reference=f"account audit {aud.id}"))
    why = {k: "Not read yet: run the read-only account audit (Audit-only task) for this account." for k in left}
    left = [k for k in left if k not in cov]
    return _finish("account_policy", ctx, seq, f, cov, left, why, research_performed=["Linked-account status"] + (["Read-only 90-day account audit"] if aud else []), data_sources=["Google Ads API"])


def ev_strategy(ctx: Ctx, seq: _Seq, others: dict[str, dict]) -> dict:
    """Combines the other nine modules into the readiness decision. Runs last."""
    f, cov = [], {}
    all_f = [x for r in others.values() for x in r["findings"]]
    crit = [x for x in all_f if x["result"] == "FAIL" and x["severity"] == "critical"]
    high = [x for x in all_f if x["result"] in ("FAIL", "WARN") and x["severity"] == "high"]
    fin = {x["category"]: x for x in others.get("finance", {}).get("findings", [])}
    aff = (fin.get("affordability") or {}).get("value") or {}
    not_viable = bool(aff.get("verdict") == "over_limit")
    unfinished = [m for m, r in others.items() if r["status"] in ("NOT_STARTED", "FAILED")]
    conditions = [x["finding"][:160] for x in high][:10] + [f"{MODULES[m]['title']} has not been completed" for m in unfinished]
    if ctx.account is None:
        conditions.append("No Google Ads account is linked: the draft can be prepared and validated, but deployment needs an account.")
    m_issues = [x for x in others.get("measurement", {}).get("findings", []) if x["result"] == "FAIL"]
    if m_issues:  # listed for the reader; measurement never decides readiness by itself
        conditions.append(f"{len(m_issues)} conversion measurement issue(s) to resolve: " + "; ".join(x["subject"] for x in m_issues[:3]))
    if crit:
        decision = "blocked"
    elif not_viable:
        decision = "not_commercially_viable"
    elif conditions:
        decision = "ready_with_conditions"
    else:
        decision = "ready"
    reasons = ([f"Critical: {x['finding'][:140]}" for x in crit] or ([f"Estimated CPC exceeds the maximum affordable CPC: {'; '.join(aff.get('reasons', []))}"] if not_viable else [])) or conditions or ["All modules completed without critical or high-severity issues."]
    f.append(make_finding(seq, "readiness_decision", "Readiness", f"{DECISIONS[decision]}. " + " | ".join(reasons[:5]), result="PASS" if decision == "ready" else ("FAIL" if decision in ("blocked", "not_commercially_viable") else "WARN"),
                          severity="info" if decision == "ready" else ("critical" if decision == "blocked" else "high"), origin="ai_recommendation", verification="inferred",
                          source_type="intelligence_engine", source_reference="module findings", value={"decision": decision, "conditions": conditions, "blockers": [x["finding_id"] for x in crit]},
                          limitations=["'Ready for campaign generation' is not authorisation to publish: creation, activation and spending are separate approvals."]))
    cov["readiness_decision"] = "evaluated"
    rows = ctx.kw_rows
    sel = [r for r in rows if r.get("selected")]
    if sel:
        groups = {}
        for r in sel:
            groups.setdefault(r.get("ad_group") or r.get("service") or "Generic", []).append(r["keyword"])
        f.append(make_finding(seq, "ad_group_structure", "Proposed ad groups", f"{len(groups)} ad group(s): " + "; ".join(f"{k} ({len(v)} kw)" for k, v in groups.items()), result="INFO", origin="ai_recommendation",
                              verification="inferred", source_type="intelligence_engine", source_reference="selected keywords", value=groups,
                              reason="One ad group per service keeps keywords, ads and landing page on the same theme."))
        cov["ad_group_structure"] = "evaluated"
        f.append(make_finding(seq, "keyword_strategy", "Match types", "EXACT and PHRASE in main ad groups; BROAD only in an approved test ad group once conversions are recorded reliably.", result="INFO", origin="ai_recommendation",
                              verification="inferred", source_type="intelligence_engine", reason="Broad match needs conversion data and negative coverage to be safe."))
        cov["keyword_strategy"] = "evaluated"
    ok, why = measurement.supports_conversion_bidding(ctx.measurement.facts if ctx.measurement else None)
    f.append(make_finding(seq, "bidding_strategy", "Bidding", ("Maximize Conversions. " if ok is not False else "Maximize Clicks with a CPC ceiling for now. ") + why,
                          result="INFO", origin="ai_recommendation", verification="inferred", source_type="intelligence_engine",
                          reason="Conversion-based bidding needs a conversion action that records the outcome it should optimise toward."))
    cov["bidding_strategy"] = "evaluated"
    if fin.get("budget_recommendation", {}).get("value"):
        f.append(make_finding(seq, "budget_allocation", "Budget", fin["budget_recommendation"]["finding"], result="INFO", origin="ai_recommendation", verification="forecast",
                              source_type="financial_engine", value=fin["budget_recommendation"]["value"], limitations=["Estimate: never a guaranteed result."]))
        cov["budget_allocation"] = "evaluated"
    f.append(make_finding(seq, "launch_requirements", "Launch requirements", "; ".join(conditions) if conditions else "None outstanding. Creation, activation and spend still need their own approvals.",
                          result="WARN" if conditions else "PASS", severity="medium" if conditions else "info", origin="ai_recommendation", verification="inferred", source_type="intelligence_engine"))
    cov["launch_requirements"] = "evaluated"
    why = {"campaign_objective": "Lead generation (calls and form leads) is fixed for Google Search campaigns in this app.", "campaign_structure": "Produced when a draft is generated.",
           "ad_assets": "Produced when a draft is generated.", "schedule": "Needs business hours and historical performance.", "monitoring_requirements": "Defined by the monitoring engine per campaign."}
    left = [c["key"] for c in MODULES["strategy"]["checks"] if c["key"] not in cov]
    r = _finish("strategy", ctx, seq, f, cov, left, why, decision=decision, research_performed=["Cross-module consistency review"], data_sources=["the nine other modules"],
                summary=DECISIONS[decision])
    r["status"] = "BLOCKED" if decision == "blocked" else ("COMPLETED" if decision == "ready" else "COMPLETED_WITH_WARNINGS")
    r["client_actions"] = [{"ask": c} for c in conditions][:10]
    return r


EVALUATORS = {"business_intelligence": ev_business_intelligence, "website": ev_website, "competitors": ev_competitors, "search_intent": ev_search_intent,
              "keywords": ev_keywords, "geography": ev_geography, "finance": ev_finance, "measurement": ev_measurement, "account_policy": ev_account_policy}


# ------------------------------------------------------------------------------------------------ orchestration
def _store(db: Session, biz: Business, module: str, data: dict, h: str, user_id: str | None, started) -> IntelligenceRun:
    prev = current_run(db, biz.id, module)
    if prev:
        prev.is_current = False
    run = IntelligenceRun(client_id=biz.client_id, business_id=biz.id, module=module, version=(prev.version + 1) if prev else 1, input_hash=h, is_current=True,
                          status=data.pop("status"), started_at=started, finished_at=clock.now(), created_by_id=user_id, error="", **data)
    db.add(run)
    db.flush()
    return run


def refresh(db: Session, biz: Business, client: Client, user_id: str | None = None, *, modules: list[str] | None = None, force: bool = False,
            research: bool = False) -> dict:
    """Bring the record up to date. Only stale modules (changed inputs, changed upstream run, never run, or failed) are evaluated unless ``force``;
    valid research is reused. ``modules`` restricts the stage(s); downstream modules that depend on a re-run stage are re-run too.
    ``research=True`` runs the five-part audit first when there is none (the only step that contacts external services)."""
    unknown = [m for m in (modules or []) if m not in MODULES]
    if unknown:
        raise ValidationFailed(f"Unknown module(s): {', '.join(unknown)}", details={"modules": MODULE_KEYS})
    if research and _latest_report(db, biz) is None:
        from app.services import audit_report
        audit_report.launch(db, biz, client, user_id, sync=True)
        db.expire_all()
    todo = set(modules) if modules else set(MODULE_KEYS)
    if modules:
        for m in list(todo):  # an explicit stage re-run cascades to what depends on it
            todo |= set(_closure(m))
    stale = set(stale_modules(db, biz, client))
    ran, reused = [], []
    order = [m for m in MODULE_KEYS if m in todo]
    ctx = Ctx(db, biz, client, research, user_id)
    results: dict[str, dict] = {}
    for m in MODULE_KEYS:
        if m not in todo:
            prev = current_run(db, biz.id, m)
            if prev:
                results[m] = {"findings": prev.findings, "status": prev.status}
            continue
        run = current_run(db, biz.id, m)
        explicit = bool(modules and m in todo)
        if run is not None and not force and not explicit and m not in stale:
            reused.append(m)
            results[m] = {"findings": run.findings, "status": run.status}
            continue
        started, seq = clock.now(), _Seq(m)
        try:
            data = EVALUATORS[m](ctx, seq) if m != "strategy" else ev_strategy(ctx, seq, results)
        except Exception as exc:  # noqa: BLE001 - a failed evaluation is recorded as FAILED, never as completed
            data = _run_dict([], {}, summary=f"{type(exc).__name__}: {str(exc)[:300]}") | {"status": "FAILED"}
        run = _store(db, biz, m, data, input_hash(db, biz, client, m), user_id, started)
        results[m] = {"findings": run.findings, "status": run.status}
        ran.append(m)
    return {"ran": ran, "reused": reused, "order": order}


def _closure(module: str) -> list[str]:
    out, stack = [], list(DOWNSTREAM[module])
    while stack:
        m = stack.pop()
        if m not in out:
            out.append(m)
            stack += DOWNSTREAM[m]
    return out


def serialize_run(run: IntelligenceRun | None, module: str, stale: bool = False) -> dict:
    meta = MODULES[module]
    if run is None:
        return {"module": module, "number": meta["number"], "title": meta["title"], "status": "NOT_STARTED", "stale": True, "version": 0, "findings": [], "coverage": {},
                "checks_total": len(meta["checks"]), "checks_evaluated": 0, "depends_on": meta["depends_on"], "decision": "", "summary": "",
                "missing_information": [], "contradictions": [], "risks": [], "recommendations": [], "client_actions": [], "research_performed": [],
                "data_sources": [], "retrieved_at": None, "source_report_id": None}
    ev = sum(1 for v in (run.coverage or {}).values() if v == "evaluated")
    return {"module": module, "number": meta["number"], "title": meta["title"], "status": run.status, "stale": stale, "version": run.version, "run_id": run.id,
            "decision": run.decision, "summary": run.summary, "findings": run.findings, "coverage": run.coverage, "checks_total": len(meta["checks"]), "checks_evaluated": ev,
            "missing_information": run.missing_information, "contradictions": run.contradictions, "risks": run.risks, "recommendations": run.recommendations,
            "client_actions": run.client_actions, "research_performed": run.research_performed, "data_sources": run.data_sources, "depends_on": meta["depends_on"],
            "retrieved_at": run.finished_at.isoformat() if run.finished_at else None, "source_report_id": run.source_report_id}


def report(db: Session, biz: Business, client: Client) -> dict:
    stale = set(stale_modules(db, biz, client))
    mods = [serialize_run(current_run(db, biz.id, m), m, m in stale) for m in MODULE_KEYS]
    strat = next(m for m in mods if m["module"] == "strategy")
    decision = strat["decision"] or ""
    return {"business_id": biz.id, "client_id": client.id, "client_code": client.client_code, "modules": mods, "stale_modules": sorted(stale, key=MODULE_KEYS.index),
            "decision": decision, "decision_label": DECISIONS.get(decision, "Not yet assessed"),
            "decision_reason": next((f["finding"] for f in strat["findings"] if f["category"] == "readiness_decision"), ""),
            "account_linked": accounts_service.account_for_business(db, biz) is not None, "research": research_status(db, biz, client),
            "totals": {"checks_specified": spec_check_count(), "checks_evaluated": sum(m["checks_evaluated"] for m in mods)},
            "legend": {"origin": list(ORIGINS), "verification": list(VERIFICATION), "confidence": CONFIDENCE,
                       "note": "Confidence is categorical, set by how a finding was obtained (verified 0.9, client-provided 0.7, estimate/inference 0.5, assumption 0.3, unknown 0). It is not a statistical probability."},
            "reuse_note": "Unchanged modules are reused; editing an input marks only the affected module and the modules that depend on it as stale."}


def research_status(db: Session, biz: Business, client: Client) -> dict:
    """Is the external-research audit the modules are built on still current? (Used by the agent to decide whether to research again.)"""
    from app.services import audit_report
    rep = _latest_report(db, biz)
    if rep is None:
        return {"exists": False, "up_to_date": False, "reason": "No completed research yet"}
    changed = rep.input_hash != audit_report._hash(audit_report.snapshot(biz, client))
    when = rep.finished_at or rep.created_at
    age = (clock.now() - when).days
    return {"exists": True, "report_id": rep.id, "version": rep.version, "date": when.isoformat(), "age_days": age, "client_info_changed": changed,
            "up_to_date": not changed, "reason": "Client information changed since this research" if changed else "Matches the client's current information"}


def history(db: Session, biz: Business, module: str) -> list[dict]:
    if module not in MODULES:
        raise NotFound("Unknown module")
    rows = db.scalars(select(IntelligenceRun).where(IntelligenceRun.business_id == biz.id, IntelligenceRun.module == module).order_by(IntelligenceRun.version.desc())).all()
    return [{"run_id": r.id, "version": r.version, "status": r.status, "is_current": r.is_current, "decision": r.decision, "findings": len(r.findings or []),
             "finished_at": r.finished_at.isoformat() if r.finished_at else None, "input_hash": r.input_hash[:12]} for r in rows]
