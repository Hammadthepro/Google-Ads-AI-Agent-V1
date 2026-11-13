"""AI campaign planning: drafting AI, independent challenger AI, human-plan comparison, and the deterministic
payload builder that converts a draft into exact Google Ads operations (always PAUSED-first)."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import registry
from app.core.util import normalize_term, to_micros
from app.models import Ad, AdGroup, Business, Campaign, CampaignPlan, GoogleAdsAccount, PlaybookRule, Task
from app.services import ai_service, clients_service, competition

SHARED_LIST_NAME = "GAA account negatives"
PATTERN_NEGATIVES = ["jobs", "diy", "free"]


def playbook_rules(db: Session, business: Business) -> list[PlaybookRule]:
    rules = db.scalars(select(PlaybookRule).where(PlaybookRule.is_active.is_(True)))
    return [r for r in rules if r.scope == "GLOBAL" or (r.scope == "BUSINESS_TYPE" and r.scope_value == business.business_type)
            or (r.scope == "CLIENT" and r.scope_value == business.client_id)]


def _common_payload(db, business: Business, measurement_ctx: dict) -> dict:
    rules = playbook_rules(db, business)
    return {"business_name": business.name, "website": business.website_url, "city": clients_service.city_of(business),
            "phone": business.phone, "radius_miles": business.default_radius_miles,
            "verified_claims": business.verified_claims or [], "brand_rules": business.brand_rules or [],
            "excluded_services": clients_service.excluded_services(business),
            "measurement": measurement_ctx,
            "playbook_rules": [r.text for r in rules], "_rule_ids": [r.id for r in rules]}


PER_SERVICE, MAX_IRRELEVANT = 25, 40


def compact_keywords(table: list[dict]) -> dict:
    """The researched keywords in the shape the AI needs: per service, [keyword, monthly searches] pairs strongest first (a volume is
    given only where a source reported one; null means unknown, never zero), plus the terms to exclude. Each keyword appears once:
    the earlier format repeated every keyword again in a separate volume map, which alone cost ~20% of the draft request."""
    by_service: dict[str, list[list]] = {}
    irrelevant: list[str] = []
    ranked = sorted(table, key=lambda r: (-(r.get("avg_monthly_searches") or 0), -(r.get("relevance_score") or 0)))
    for r in ranked:
        kw = r.get("keyword")
        if not kw:
            continue
        if r.get("label") == "IRRELEVANT":
            if len(irrelevant) < MAX_IRRELEVANT:
                irrelevant.append(kw)
            continue
        if not r.get("selected"):
            continue
        group = by_service.setdefault(r.get("service") or "Generic", [])
        if len(group) < PER_SERVICE:
            group.append([kw, r.get("avg_monthly_searches")])
    return {"keywords_by_service": by_service, "irrelevant_terms": irrelevant}


def draft_services(services: list[dict]) -> list[dict]:
    """Each service once, with the one landing page the ad group uses (the task's service rows repeat the URL under two keys)."""
    return [{"name": x["name"], "landing_page": x.get("landing_page") or x.get("landing_page_url") or ""} for x in services]


def draft(db: Session, task: Task, business: Business, *, scope: str, services: list[dict], keyword_table: list[dict],
          feasibility: dict | None, measurement_ctx: dict, extra: dict | None = None) -> CampaignPlan:
    payload = _common_payload(db, business, measurement_ctx)
    rule_ids = payload.pop("_rule_ids")
    payload.update({"services": draft_services(services), "monthly_budget": (task.fields or {}).get("monthly_budget"),
                    "feasibility": {k: (feasibility or {}).get("summary", {}).get(k) for k in
                                    ("status", "expected_weekly_conversions", "weekly_target", "allowed_cost_per_lead")},
                    "keyword_table": compact_keywords(keyword_table),
                    "note": task.note or "", **(extra or {})})
    purpose = {"CAMPAIGN": "DRAFT_CAMPAIGN", "AD_GROUP": "DRAFT_AD_GROUP", "AD": "DRAFT_AD"}[scope]
    if scope == "AD_GROUP":
        payload["service"] = payload["services"][0]
    if scope == "AD":
        payload["service"] = services[0]["name"]
    if scope in ("CAMPAIGN", "AD_GROUP"):
        ctx = {"verified_claims": business.verified_claims or [], "excluded_services": clients_service.excluded_services(business),
               "measurement": measurement_ctx, "feasibility": payload["feasibility"]}
        res = competition.run(db, scope=scope, payload=payload, challenge_ctx=ctx, table=keyword_table,
                              verified_claims=business.verified_claims or [], conversion_bidding=measurement_ctx.get("conversion_bidding_supported"),
                              task_id=task.id)
        plan = CampaignPlan(task_id=task.id, client_id=task.client_id, business_id=business.id, scope=scope, draft=res["draft"],
                            original=res["draft"], competition=res["competition"], challenger=res["challenger"],
                            draft_ai_run_id=res["draft_run_id"], challenger_ai_run_id=res["challenger_run_id"],
                            playbook_rule_ids=rule_ids, status="CHALLENGED",
                            source_type=("LIVE" if any(p["source"] == "LIVE" for p in res["competition"]["providers"].values()) else "NONE"))
        db.add(plan)
        db.flush()
        return plan
    parsed, run = ai_service.generate(db, purpose, payload, task_id=task.id)
    plan = CampaignPlan(task_id=task.id, client_id=task.client_id, business_id=business.id, scope=scope,
                        draft=parsed.model_dump(), original=parsed.model_dump(), draft_ai_run_id=run.id, playbook_rule_ids=rule_ids,
                        source_type=registry.get("ai").source)
    db.add(plan)
    db.flush()
    return plan


def challenge(db: Session, plan: CampaignPlan, business: Business, feasibility: dict | None, measurement_ctx: dict) -> dict:
    if plan.competition:  # the AI competition already cross-challenged both drafts
        return plan.challenger
    payload = {"draft": plan.draft, "verified_claims": business.verified_claims or [],
               "excluded_services": clients_service.excluded_services(business), "measurement": measurement_ctx,
               "feasibility": {k: (feasibility or {}).get("summary", {}).get(k) for k in
                               ("status", "expected_weekly_conversions", "weekly_target")}}
    parsed, run = ai_service.generate(db, "CHALLENGE", payload, task_id=plan.task_id, model_role="challenger")
    plan.challenger = parsed.model_dump()
    plan.challenger_ai_run_id = run.id
    plan.status = "CHALLENGED"
    return plan.challenger


# ------------------------------------------------------------------ human plan comparison
def parse_human_plan(text: str) -> dict:
    plan: dict = {"ad_groups": [], "keywords": [], "negatives": []}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        key, val = [x.strip() for x in line.split(":", 1)]
        k = key.lower()
        items = [v.strip().strip('"') for v in re.split(r"[,;]", val) if v.strip()]
        if "budget" in k:
            m = re.search(r"[\d,.]+", val)
            if m:
                plan["monthly_budget"] = float(m.group().replace(",", ""))
        elif "bid" in k:
            plan["bidding_strategy"] = "MAXIMIZE_CLICKS" if "click" in val.lower() else "MAXIMIZE_CONVERSIONS"
        elif "ad group" in k:
            plan["ad_groups"] += items
        elif "negative" in k:
            plan["negatives"] += items
        elif "keyword" in k:
            plan["keywords"] += items
    return plan


def compare_human_plan(db: Session, plan: CampaignPlan, human_text: str, form_budget: float | None) -> dict:
    human = parse_human_plan(human_text)
    d = plan.draft
    groups = d.get("ad_groups", [])
    ai_groups = {normalize_term(g["name"]): g["name"] for g in groups}
    ai_kws = {normalize_term(k["text"]) for g in groups for k in g.get("keywords", [])}
    ai_negs = {normalize_term(n) for n in d.get("campaign_negatives", [])} | {normalize_term(n) for g in groups for n in g.get("negatives", [])}
    agreed, ai_only, human_only, diffs = [], [], [], []

    def cmp_set(topic, hum: list[str], ai_set: set[str]):
        hs = {normalize_term(x) for x in hum}
        for x in sorted(hs & ai_set):
            agreed.append(f"{topic}: {x}")
        for x in sorted(hs - ai_set):
            human_only.append(f"{topic}: {x}")
        if hum:
            for x in sorted(ai_set - hs)[:15]:
                ai_only.append(f"{topic}: {x}")

    cmp_set("ad group", human["ad_groups"], set(ai_groups))
    cmp_set("keyword", human["keywords"], ai_kws)
    cmp_set("negative", human["negatives"], ai_negs)
    if "bidding_strategy" in human:
        if human["bidding_strategy"] == d.get("bidding_strategy"):
            agreed.append(f"bidding: {human['bidding_strategy']}")
        else:
            diffs.append({"topic": "bidding_strategy", "human": human["bidding_strategy"], "ai": d.get("bidding_strategy"),
                          "ai_reason": "Maximize Conversions is the default; Maximize Clicks is used only when the account has no conversion action recording."})
    if "monthly_budget" in human and form_budget and abs(human["monthly_budget"] - form_budget) > 0.5:
        diffs.append({"topic": "monthly_budget", "human": human["monthly_budget"], "ai": form_budget,
                      "ai_reason": "The task form budget was used for feasibility."})
    resolution = {"disagreements": [], "summary": ""}
    if diffs:
        parsed, _ = ai_service.generate(db, "RESOLVE_HUMAN_PLAN", {"differences": diffs}, task_id=plan.task_id)
        resolution = parsed.model_dump()
    # Human decisions are applied by default — AI never silently overwrites them.
    merged = dict(d)
    applied = []
    if human.get("bidding_strategy"):
        merged["bidding_strategy"] = human["bidding_strategy"]
        applied.append("bidding strategy from human plan")
    if groups:
        extra_kw = [k for k in human["keywords"] if normalize_term(k) not in ai_kws]
        for k in extra_kw:
            target = next((g for g in groups if normalize_term(g["service"]).split()[0] in normalize_term(k)), groups[0])
            target.setdefault("keywords", []).append({"text": k.lower(), "match_type": "PHRASE"})
            applied.append(f"human keyword '{k}' added to {target['name']} (phrase)")
        merged["ad_groups"] = groups
        merged["campaign_negatives"] = sorted(set(d.get("campaign_negatives", [])) | {n.lower() for n in human["negatives"]})
    result = {"human_plan": human, "agreed": agreed, "ai_only": ai_only, "human_only": human_only,
              "disagreements": resolution.get("disagreements") or diffs, "resolution_summary": resolution.get("summary", ""),
              "applied_human_decisions": applied, "human_budget": human.get("monthly_budget")}
    plan.human_comparison = result
    plan.draft = merged
    return result


# ------------------------------------------------------------------ payload builder (deterministic)
class _Temp:
    def __init__(self):
        self.n = 0

    def next(self) -> str:
        self.n -= 1
        return str(self.n)


def _ad_op(ad_group_ref: str, ad: dict, final_url: str, temp: _Temp | None = None) -> dict:
    op = {"resource": "ad_group_ad", "op": "create",
          "fields": {"ad_group": ad_group_ref, "status": "PAUSED",
                     "ad": {"final_urls": [final_url], "responsive_search_ad": {
                         "headlines": ad["headlines"], "descriptions": ad["descriptions"],
                         "path1": ad.get("path1", ""), "path2": ad.get("path2", "")}}}}
    if temp:
        op["temp_id"] = temp.next()
    return op


def _kw_ops(ad_group_ref: str, keywords: list[dict], negatives: list[str]) -> list[dict]:
    ops = []
    for k in keywords:
        ops.append({"resource": "ad_group_criterion", "op": "create",
                    "fields": {"ad_group": ad_group_ref, "status": "PAUSED", "type": "KEYWORD",
                               "keyword": {"text": k["text"].lower().strip(), "match_type": k["match_type"]}}})
    for n in negatives:
        ops.append({"resource": "ad_group_criterion", "op": "create",
                    "fields": {"ad_group": ad_group_ref, "type": "KEYWORD", "negative": True,
                               "keyword": {"text": n.lower().strip(), "match_type": "EXACT"}}})
    return ops


def location_ops(campaign_ref: str, business: Business, fields: dict) -> list[dict]:
    zips = fields.get("zip_codes") or []
    radius = float(fields.get("radius_miles") or business.default_radius_miles or 20)
    if zips:
        geos = registry.get("keyword_research").suggest_geo_targets(zips)
        return [{"resource": "campaign_criterion", "op": "create",
                 "fields": {"campaign": campaign_ref, "type": "LOCATION",
                            "location": {"geo_target_constant": g["geo_target_constant"]}, "zip": g["zip"]}} for g in geos]
    return [{"resource": "campaign_criterion", "op": "create",
             "fields": {"campaign": campaign_ref, "type": "PROXIMITY",
                        "proximity": {"radius": radius, "radius_units": "MILES",
                                      "address": {"street_address": business.address, "country_code": "US"}}}}]


def build_campaign_payload(db: Session, task: Task, business: Business, account: GoogleAdsAccount | None, draft: dict) -> dict:
    t = _Temp()
    fields = task.fields or {}
    monthly = float(fields.get("monthly_budget") or 0)
    daily_micros = to_micros(round(monthly / 30.4, 2))
    b_ref, c_ref = t.next(), t.next()
    # Preparation does not need an account. Without one, duplicate-name and shared-list checks happen when the draft is bound to one.
    existing_names = {c.get("name") for c in registry.get("google_ads").list_campaigns(account.customer_id)} if account else set()
    name, n = draft["campaign_name"], 2
    while name in existing_names:  # Google Ads requires unique campaign names; renaming is recorded on the card
        name, n = f"{draft['campaign_name']} #{n}", n + 1
    renamed = name != draft["campaign_name"]
    draft = {**draft, "campaign_name": name}
    bidding = ({"maximize_conversions": {}} if draft["bidding_strategy"] == "MAXIMIZE_CONVERSIONS"
               else {"target_spend": {"cpc_bid_ceiling_micros": to_micros(float(fields.get("max_cpc") or 15))}})
    ops = [
        {"resource": "campaign_budget", "op": "create", "temp_id": b_ref,
         "fields": {"name": f"{draft['campaign_name']} budget", "amount_micros": daily_micros,
                    "delivery_method": "STANDARD", "explicitly_shared": False}},
        {"resource": "campaign", "op": "create", "temp_id": c_ref,
         "fields": {"name": draft["campaign_name"], "advertising_channel_type": "SEARCH", "status": "PAUSED",
                    "campaign_budget": f"temp:{b_ref}", **bidding,
                    "network_settings": {"target_google_search": True, "target_search_network": False,
                                         "target_content_network": False, "target_partner_search_network": False},
                    "geo_target_type_setting": {"positive_geo_target_type": "PRESENCE", "negative_geo_target_type": "PRESENCE"},
                    "contains_eu_political_advertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
                    "weekly_conversion_target": float(fields.get("weekly_target") or 0)}},
    ]
    ops += location_ops(f"temp:{c_ref}", business, fields)
    negs = [n for n in draft.get("campaign_negatives", []) if n.lower() not in PATTERN_NEGATIVES]
    for n in negs:
        ops.append({"resource": "campaign_criterion", "op": "create",
                    "fields": {"campaign": f"temp:{c_ref}", "type": "KEYWORD", "negative": True,
                               "keyword": {"text": n.lower(), "match_type": "EXACT"}}})
    existing_list = [s for s in registry.get("google_ads").list_resources(account.customer_id, "shared_set")
                     if s.get("name") == SHARED_LIST_NAME] if account else []
    if existing_list:
        list_ref = existing_list[0]["resource_name"]
    else:
        s_ref = t.next()
        list_ref = f"temp:{s_ref}"
        ops.append({"resource": "shared_set", "op": "create", "temp_id": s_ref,
                    "fields": {"name": SHARED_LIST_NAME, "type": "NEGATIVE_KEYWORDS"}})
        for p in PATTERN_NEGATIVES:
            ops.append({"resource": "shared_criterion", "op": "create",
                        "fields": {"shared_set": list_ref, "type": "KEYWORD", "keyword": {"text": p, "match_type": "PHRASE"}}})
    ops.append({"resource": "campaign_shared_set", "op": "create", "fields": {"campaign": f"temp:{c_ref}", "shared_set": list_ref}})
    for g in draft["ad_groups"]:
        g_ref = t.next()
        ops.append({"resource": "ad_group", "op": "create", "temp_id": g_ref,
                    "fields": {"name": g["name"], "campaign": f"temp:{c_ref}", "status": "PAUSED", "type": "SEARCH_STANDARD",
                               "service": g["service"]}})
        ops += _kw_ops(f"temp:{g_ref}", g.get("keywords", []), g.get("negatives", []))
        for ad in g.get("ads", []):
            ops.append(_ad_op(f"temp:{g_ref}", ad, g["landing_page"]))
    if draft.get("use_call_asset") and business.phone:
        a_ref = t.next()
        ops += [{"resource": "asset", "op": "create", "temp_id": a_ref,
                 "fields": {"type": "CALL", "call_asset": {"country_code": "US", "phone_number": business.phone,
                                                           "ad_schedule_targets": business.business_hours or {}}}},
                {"resource": "campaign_asset", "op": "create",
                 "fields": {"campaign": f"temp:{c_ref}", "asset": f"temp:{a_ref}", "field_type": "CALL"}}]
    for sl in draft.get("sitelinks", [])[:4]:
        a_ref = t.next()
        ops += [{"resource": "asset", "op": "create", "temp_id": a_ref,
                 "fields": {"type": "SITELINK", "sitelink_asset": {"link_text": sl["text"][:25]}, "final_urls": [sl["url"]]}},
                {"resource": "campaign_asset", "op": "create",
                 "fields": {"campaign": f"temp:{c_ref}", "asset": f"temp:{a_ref}", "field_type": "SITELINK"}}]
    for co in draft.get("callouts", [])[:4]:
        a_ref = t.next()
        ops += [{"resource": "asset", "op": "create", "temp_id": a_ref, "fields": {"type": "CALLOUT", "callout_asset": {"callout_text": co[:25]}}},
                {"resource": "campaign_asset", "op": "create",
                 "fields": {"campaign": f"temp:{c_ref}", "asset": f"temp:{a_ref}", "field_type": "CALLOUT"}}]
    return {"kind": "BUILD_CAMPAIGN", "customer_id": account.customer_id if account else None, "operations": ops,
            "summary": {"campaign_name": draft["campaign_name"], "daily_budget": daily_micros / 1e6, "monthly_budget": monthly,
                        "bidding_strategy": draft["bidding_strategy"], "ad_groups": [g["name"] for g in draft["ad_groups"]],
                        "keywords": sum(len(g.get("keywords", [])) for g in draft["ad_groups"]),
                        "negatives": len(negs) + len(PATTERN_NEGATIVES), "status_on_create": "PAUSED",
                        "network": "Google Search only (Search Partners and Display off)", "geo_target_type": "PRESENCE",
                        "deployment_account": account.customer_id if account else "not linked yet (draft only)",
                        "locations": fields.get("zip_codes") or f"{fields.get('radius_miles') or business.default_radius_miles} mile radius",
                        **({"renamed_to_avoid_duplicate": True} if renamed else {})},
            "internal": {"weekly_conversion_target": float(fields.get("weekly_target") or 0), "monthly_budget": monthly,
                         "business_id": business.id, "services": [g["service"] for g in draft["ad_groups"]]},
            "draft_ref": {"campaign_name": draft["campaign_name"]}}


def build_ad_group_payload(account: GoogleAdsAccount, campaign: Campaign, group: dict) -> dict:
    t = _Temp()
    g_ref = t.next()
    ops = [{"resource": "ad_group", "op": "create", "temp_id": g_ref,
            "fields": {"name": group["name"], "campaign": campaign.external_resource_name, "status": "PAUSED",
                       "type": "SEARCH_STANDARD", "service": group["service"]}}]
    ops += _kw_ops(f"temp:{g_ref}", group.get("keywords", []), group.get("negatives", []))
    for ad in group.get("ads", []):
        ops.append(_ad_op(f"temp:{g_ref}", ad, group["landing_page"]))
    return {"kind": "ADD_AD_GROUP", "customer_id": account.customer_id, "operations": ops,
            "summary": {"campaign": campaign.name, "ad_group": group["name"], "keywords": len(group.get("keywords", [])),
                        "status_on_create": "PAUSED"}, "internal": {}}


def build_ad_payload(account: GoogleAdsAccount, ad_group: AdGroup, ad: dict, final_url: str, kind="ADD_AD",
                     replaces: Ad | None = None) -> dict:
    payload = {"kind": kind, "customer_id": account.customer_id, "operations": [_ad_op(ad_group.external_resource_name, ad, final_url)],
               "summary": {"ad_group": ad_group.name, "headlines": len(ad["headlines"]), "descriptions": len(ad["descriptions"]),
                           "status_on_create": "PAUSED"}, "internal": {}}
    if replaces:
        payload["internal"]["replaces_ad"] = replaces.external_resource_name
        payload["summary"]["replaces"] = replaces.external_resource_name
    return payload
