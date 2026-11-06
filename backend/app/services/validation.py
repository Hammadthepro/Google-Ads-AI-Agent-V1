"""Deterministic validation engine (step 7). Passing validation does NOT mean approved or launched."""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.adapters import registry
from app.adapters.base import AdapterError
from app.adapters.google_ads.mock import DESCRIPTION_MAX, HEADLINE_MAX, PATH_MAX, policy_issues_for_text
from app.core.util import normalize_term, payload_hash
from app.models import Business, ValidationRun
from app.services import crawler_service, settings_service, tracking_gate

CLAIM_PATTERNS = {
    "#1": r"#\s?1\b|number one", "best": r"\bbest\b", "top rated": r"top[- ]rated", "guarantee": r"guarantee",
    "licensed": r"\blicensed\b", "insured": r"\binsured\b", "certified": r"certified", "award": r"award",
    "same-day": r"same[- ]day", "24/7": r"24/7|24 hours", "no fee": r"no fee", "free": r"\bfree\b",
    "family owned": r"family[- ]owned", "years": r"\d+\+?\s+years|since \d{4}", "warranty": r"warrant",
    "cheapest": r"cheapest|lowest price", "discount": r"\d+%|\$\d+",
}
FIXABLE = {"AD_LENGTH", "UNVERIFIED_CLAIM", "POLICY", "BRAND_RULE", "EXCLUDED_SERVICE_IN_AD"}


def _err(code, message, target="", phrase="", severity="error"):
    return {"code": code, "message": message, "target": target, "phrase": phrase, "fixable": code in FIXABLE, "severity": severity}


def banned_phrases(business: Business) -> list[str]:
    out = []
    for rule in business.brand_rules or []:
        m = re.match(r"\s*(avoid|never say|do not use|don't use|banned)\s*:\s*(.+)", rule, re.I)
        if m:
            out += [p.strip().lower() for p in m.group(2).split(",") if p.strip()]
    return out


def claim_violations(text: str, verified: list[str]) -> list[str]:
    vtext = " ".join(verified).lower()
    hits = []
    for name, pattern in CLAIM_PATTERNS.items():
        if re.search(pattern, text, re.I) and not re.search(pattern, vtext, re.I):
            hits.append(name)
    return hits


def validate(db: Session, payload: dict, business: Business, *, existing_keywords: dict[str, list[str]] | None = None,
             task_id: str | None = None, approval_id: str | None = None, check_landing_pages: bool = True,
             run_adapter: bool = True, budget_context: dict | None = None) -> ValidationRun:
    errors, warnings = [], []
    ops = payload.get("operations", [])
    kind = payload.get("kind", "")
    internal_only = kind in ("CHANGE_TARGET",)
    if not payload.get("customer_id"):
        errors.append(_err("REQUIRED", "customer_id is required"))
    if not ops and not internal_only:
        errors.append(_err("REQUIRED", "No operations to validate"))
    verified = business.verified_claims or []
    banned = banned_phrases(business)
    excluded = [normalize_term(s.name) for s in business.services if s.is_excluded]

    group_names: dict[str, str] = {}
    keywords_by_group: dict[str, list[tuple[str, str]]] = {}
    final_urls: set[str] = set()
    has_location = False
    for op in ops:
        r, f = op.get("resource"), op.get("fields") or {}
        if op.get("op") == "remove":
            errors.append(_err("DELETE_FORBIDDEN", "Remove operations are never permitted", r))
        if op.get("op") == "create" and r in ("campaign", "ad_group", "ad_group_ad") and f.get("status") != "PAUSED":
            errors.append(_err("NOT_PAUSED", f"New {r} must be created PAUSED", f.get("name", r)))
        if op.get("op") == "create" and r == "ad_group_criterion" and not f.get("negative") and f.get("status") != "PAUSED":
            errors.append(_err("NOT_PAUSED", "New keywords must be created PAUSED", (f.get("keyword") or {}).get("text", "")))
        if r == "campaign" and op.get("op") == "create":
            if not f.get("name"):
                errors.append(_err("REQUIRED", "Campaign name is required"))
            if f.get("advertising_channel_type") != "SEARCH":
                errors.append(_err("CAMPAIGN_TYPE", "Only Search campaigns can be created in V1"))
            ns = f.get("network_settings") or {}
            if not ns.get("target_google_search") or ns.get("target_search_network") or ns.get("target_content_network"):
                errors.append(_err("NETWORK", "Campaign must target Google Search only (Search Partners and Display off)"))
            if (f.get("geo_target_type_setting") or {}).get("positive_geo_target_type") != "PRESENCE":
                errors.append(_err("GEO_TYPE", "Location option must be PRESENCE"))
            if "maximize_conversions" not in f and "target_spend" not in f:
                errors.append(_err("BIDDING", "A bidding strategy is required"))
            if "target_spend" in f:
                warnings.append(_err("BIDDING", "Maximize Clicks (CPC cap) is an exception reserved for RED tracking with override", severity="warning"))
        if r == "campaign_budget":
            amount = int(f.get("amount_micros", 0))
            if amount <= 0:
                errors.append(_err("BUDGET", "Daily budget must be greater than zero"))
            monthly = (payload.get("internal") or {}).get("monthly_budget")
            if monthly and op.get("op") == "create" and abs(amount / 1e6 - monthly / 30.4) > 0.05:
                errors.append(_err("BUDGET", f"Daily budget {amount / 1e6:.2f} does not match monthly {monthly}/30.4"))
        if r == "campaign_criterion" and f.get("type") in ("LOCATION", "PROXIMITY"):
            has_location = True
        if r == "ad_group":
            group_names[op.get("temp_id") or f.get("name", "")] = f.get("name", "")
        if r == "ad_group_criterion" and f.get("type") == "KEYWORD" and not f.get("negative"):
            kw = f.get("keyword") or {}
            text, mt = kw.get("text", ""), kw.get("match_type")
            gkey = f.get("ad_group", "").replace("temp:", "")
            if mt == "BROAD":
                errors.append(_err("MATCH_TYPE", f"Broad match is not allowed in main ad groups in V1: '{text}'", text))
            if len(text) > 80 or len(text.split()) > 10:
                errors.append(_err("KEYWORD_LENGTH", f"Keyword too long: '{text}'", text))
            for ex in excluded:
                if ex and ex in normalize_term(text):
                    errors.append(_err("EXCLUDED_SERVICE", f"Keyword '{text}' targets an excluded service", text))
            if (normalize_term(text), mt) in keywords_by_group.get(gkey, []):
                errors.append(_err("DUPLICATE_KEYWORD", f"Duplicate keyword '{text}' ({mt}) in the same ad group", text))
            keywords_by_group.setdefault(gkey, []).append((normalize_term(text), mt))
        if r == "ad_group_ad" and "ad" in f:  # content rules only when ad content is created/changed
            ad = f.get("ad") or {}
            rsa = ad.get("responsive_search_ad") or {}
            heads, descs = rsa.get("headlines") or [], rsa.get("descriptions") or []
            final_urls.update(ad.get("final_urls") or [])
            if not 3 <= len(heads) <= 15:
                errors.append(_err("AD_LENGTH", f"Responsive search ads need 3-15 headlines (has {len(heads)})"))
            if not 2 <= len(descs) <= 4:
                errors.append(_err("AD_LENGTH", f"Responsive search ads need 2-4 descriptions (has {len(descs)})"))
            for txt, kind_, limit in [(h, "headline", HEADLINE_MAX) for h in heads] + [(d, "description", DESCRIPTION_MAX) for d in descs]:
                if len(txt) > limit:
                    errors.append(_err("AD_LENGTH", f"{kind_.capitalize()} over {limit} characters: '{txt}'", txt, txt))
                for issue in policy_issues_for_text(txt, kind_):
                    errors.append(_err("POLICY", issue, txt, txt))
                for claim in claim_violations(txt, verified):
                    errors.append(_err("UNVERIFIED_CLAIM", f"'{txt}' makes an unverified claim ({claim})", txt, txt))
                for b in banned:
                    if b in txt.lower():
                        errors.append(_err("BRAND_RULE", f"'{txt}' uses a banned phrase ('{b}')", txt, txt))
                for ex in excluded:
                    if ex and ex in normalize_term(txt):
                        errors.append(_err("EXCLUDED_SERVICE_IN_AD", f"'{txt}' mentions an excluded service", txt, txt))
            for p in ("path1", "path2"):
                if len(rsa.get(p) or "") > PATH_MAX:
                    errors.append(_err("AD_LENGTH", f"{p} over {PATH_MAX} characters", rsa.get(p)))
            if len(set(h.lower() for h in heads)) != len(heads):
                warnings.append(_err("AD_COPY", "Duplicate headlines in one ad", severity="warning"))
    if kind == "BUILD_CAMPAIGN" and not has_location:
        errors.append(_err("LOCATIONS", "At least one location target is required"))
    # overlap between ad groups (warning) and with existing campaign keywords (error)
    owners: dict[tuple, list[str]] = {}
    for g, kws in keywords_by_group.items():
        for k in kws:
            owners.setdefault(k, []).append(group_names.get(g, g))
    for k, gs in owners.items():
        if len(set(gs)) > 1:
            warnings.append(_err("KEYWORD_OVERLAP", f"'{k[0]}' ({k[1]}) appears in ad groups {sorted(set(gs))}", k[0], severity="warning"))
    for g, kws in (existing_keywords or {}).items():
        existing = {normalize_term(x) for x in kws}
        for gk, new in keywords_by_group.items():
            for text, _mt in new:
                if text in existing:
                    errors.append(_err("DUPLICATE_RESOURCE", f"Keyword '{text}' already exists in ad group '{g}'", text))
    # landing pages
    if check_landing_pages:
        for url in sorted(final_urls):
            res = crawler_service.check_url(url)
            if not res["ok"]:
                errors.append(_err("LANDING_PAGE", f"Landing page {url} is not available ({res['status'] or res['error']})", url))
    # duplicate campaign / ad group names in the account
    ads = registry.get("google_ads")
    try:
        existing_names = {c.get("name") for c in ads.list_campaigns(payload.get("customer_id", ""))} if payload.get("customer_id") else set()
    except AdapterError:
        existing_names = set()
    for op in ops:
        if op.get("resource") == "campaign" and op.get("op") == "create" and (op.get("fields") or {}).get("name") in existing_names:
            errors.append(_err("DUPLICATE_RESOURCE", f"A campaign named '{op['fields']['name']}' already exists"))
    # tracking state
    state = tracking_gate.effective_state(db, business.id)
    latest = tracking_gate.latest(db, business.id)
    if kind in ("BUILD_CAMPAIGN", "ADD_AD_GROUP", "ADD_KEYWORDS", "ENABLE"):
        if state == "RED":
            errors.append(_err("TRACKING", "Tracking gate is RED — fix tracking or obtain an admin override"))
        elif latest and latest.override_active:
            warnings.append(_err("TRACKING", "Tracking override active: launch allowed, no conversion-based automation", severity="warning"))
        elif state == "AMBER":
            warnings.append(_err("TRACKING", "Tracking AMBER: launch requires approval; no conversion-based automation", severity="warning"))
    # budget change thresholds
    if budget_context:
        cur, new = budget_context.get("current_monthly", 0), budget_context.get("new_monthly", 0)
        if new <= 0:
            errors.append(_err("BUDGET", "New budget must be greater than zero"))
        pct = float(settings_service.get_value("approvals.budget_super_admin_pct", 20, db))
        abs_lim = float(settings_service.get_value("approvals.budget_super_admin_abs_usd", 500, db))
        if cur and (new - cur) > 0 and ((new - cur) / cur * 100 > pct or new - cur > abs_lim):
            warnings.append(_err("BUDGET_THRESHOLD", f"Budget increase {cur:.0f} → {new:.0f} exceeds the threshold; Super Admin approval required",
                                 severity="warning"))
    # Google-side validate-only (mock simulates; real uses validate_only=true, which creates nothing)
    adapter_result: dict = {}
    if run_adapter and ops and not errors:
        try:
            adapter_result = ads.mutate(payload["customer_id"], ops, validate_only=True)
        except AdapterError as exc:
            adapter_result = exc.to_dict()
            for e in (exc.details or {}).get("errors", [{"message": exc.message}]):
                errors.append(_err("ADAPTER_VALIDATION", f"{ads.name}: {e.get('message')}", e.get("resource", "")))
    run = ValidationRun(task_id=task_id, approval_id=approval_id, payload_hash=payload_hash(payload), passed=not errors,
                        errors=errors, warnings=warnings, adapter_result=adapter_result)
    db.add(run)
    db.flush()
    return run


def serialize(v: ValidationRun | None) -> dict | None:
    if not v:
        return None
    return {"id": v.id, "passed": v.passed, "errors": v.errors, "warnings": v.warnings, "adapter_result": v.adapter_result,
            "payload_hash": v.payload_hash, "ai_fix_attempts": v.ai_fix_attempts, "created_at": v.created_at.isoformat(),
            "note": "Validation passing does not mean the change is approved or launched."}
