"""Conversion tracking gate: GREEN (verified) / AMBER (configured, unverified) / RED (missing, broken, untrusted).

A detected tag only proves installation. GREEN additionally requires conversions actually recorded in Google Ads.
An admin override behaves like AMBER and never authorises conversion-based automation.
"""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import registry
from app.adapters.base import AdapterError
from app.core import clock
from app.core.errors import Forbidden, NotFound, ValidationFailed
from app.models import Business, Campaign, TrackingGateResult, TrackingOverride, User
from app.services import accounts_service, activity, auth_service, crawler_service, fixes_service, settings_service

LEAD_FORM_TYPES = {"WEBPAGE", "WEBPAGE_ONCLICK", "UPLOAD_CLICKS"}
CALL_TYPES = {"AD_CALL", "WEBSITE_CALL", "CLICK_TO_CALL", "CALL_FROM_ADS"}
GA_TYPES = {"GOOGLE_ANALYTICS_4_CUSTOM", "GOOGLE_ANALYTICS_4_PURCHASE", "UNIVERSAL_ANALYTICS_GOAL"}


def _check(key, label, status, critical, evidence, fix=""):
    return {"key": key, "label": label, "status": status, "critical": critical, "evidence": evidence, "fix": fix}


def active_override(db: Session, business_id: str) -> TrackingOverride | None:
    return db.scalar(select(TrackingOverride).where(TrackingOverride.business_id == business_id,
                                                    TrackingOverride.cleared_at.is_(None)))


def latest(db: Session, business_id: str) -> TrackingGateResult | None:
    return db.scalar(select(TrackingGateResult).where(TrackingGateResult.business_id == business_id)
                     .order_by(TrackingGateResult.evaluated_at.desc()))


def effective_state(db: Session, business_id: str) -> str:
    r = latest(db, business_id)
    return r.effective_state if r else "RED"


def evaluate(db: Session, business: Business, actor: User | None = None) -> TrackingGateResult:
    ads = registry.get("google_ads")
    account = accounts_service.account_for_business(db, business)
    host = (urlparse(business.website_url).hostname or "").lower()
    checks: list[dict] = []
    source = ads.source
    if not account:
        checks.append(_check("account_linked", "Google Ads account linked", "FAIL", True, "No account linked to this client",
                             "Link the client's Google Ads account."))
    else:
        try:
            cust = ads.get_customer(account.customer_id)
            on = cust.get("auto_tagging_enabled")
            checks.append(_check("auto_tagging", "Auto-tagging enabled", "PASS" if on else "FAIL", True,
                                 f"customer.auto_tagging_enabled = {on}", "" if on else "Turn on auto-tagging in Google Ads account settings."))
        except AdapterError as exc:
            checks.append(_check("auto_tagging", "Auto-tagging enabled", "UNKNOWN", True, f"Could not read account: {exc.kind}",
                                 "Fix the Google Ads connection, then re-run."))

    # --- website tag detection (likely installation only)
    pages = [u for u in {business.website_url, business.lead_form_url, business.thank_you_url} if u]
    tags, gtm, fetched, thank_you_ok = set(), set(), 0, None
    for url in pages:
        res = registry.get("crawler").fetch(url)
        if url == business.thank_you_url:
            thank_you_ok = res.ok
        if res.ok:
            fetched += 1
            parsed = crawler_service.parse_page(res.text, url)
            tags |= set(parsed["google_tags"])
            gtm |= set(parsed["gtm_containers"])
    aw = sorted(t for t in tags if t.startswith("AW-"))
    if fetched == 0:
        checks.append(_check("google_tag", "Google tag on website", "UNKNOWN", True, "Website could not be fetched",
                             "Make the website reachable for the audit."))
    elif aw:
        checks.append(_check("google_tag", "Google tag on website", "PASS", True,
                             f"Google Ads tag detected ({', '.join(aw)}) on {fetched} page(s). Detection proves installation, not that conversions fire."))
    else:
        found = ", ".join(sorted(tags)) or "none"
        checks.append(_check("google_tag", "Google tag on website", "FAIL", True,
                             f"No Google Ads (AW-) tag found. Tags seen: {found}",
                             "Install the Google Ads tag (or configure it in Google Tag Manager) on every page."))
    checks.append(_check("gtm", "Google Tag Manager", "PASS" if gtm else "WARN", False,
                         f"Containers: {', '.join(sorted(gtm))}" if gtm else "No GTM container detected (optional)."))
    if business.thank_you_url:
        checks.append(_check("thank_you_page", "Thank-you page reachable", "PASS" if thank_you_ok else "FAIL", False,
                             business.thank_you_url, "" if thank_you_ok else "Restore the thank-you page used for form conversions."))

    # --- conversion actions
    actions = []
    if account:
        try:
            all_actions = [a for a in ads.list_conversion_actions(account.customer_id) if a.get("status", "ENABLED") == "ENABLED"]
            actions = [a for a in all_actions if not a.get("business_host") or a.get("business_host") == host]
        except AdapterError as exc:
            checks.append(_check("conversion_actions", "Conversion actions readable", "UNKNOWN", True, exc.kind))
    lead_actions = [a for a in actions if a.get("type") in LEAD_FORM_TYPES and a.get("primary_for_goal", True)]
    call_actions = [a for a in actions if a.get("type") in CALL_TYPES]
    ga_actions = [a for a in actions if a.get("type") in GA_TYPES and a.get("primary_for_goal")]
    checks.append(_check("form_conversion", "Form conversion action exists", "PASS" if lead_actions else "FAIL", True,
                         ", ".join(a["name"] for a in lead_actions) or "No primary website lead conversion",
                         "" if lead_actions else "Create a primary 'Submit lead form' conversion action on the thank-you page."))
    if business.phone:
        checks.append(_check("call_conversion", "Call conversion action exists", "PASS" if call_actions else "FAIL", False,
                             ", ".join(a["name"] for a in call_actions) or "No call conversion action",
                             "" if call_actions else "Create a call conversion (calls from ads / website calls)."))
        min_len = settings_service.get_value("tracking.call_min_seconds", 60, db)
        bad_len = [a for a in call_actions if not a.get("phone_call_duration_seconds")
                   or int(a["phone_call_duration_seconds"]) < int(min_len)]
        if call_actions:
            checks.append(_check("call_min_length", f"Minimum call length ≥ {min_len}s", "FAIL" if bad_len else "PASS", False,
                                 ", ".join(f"{a['name']}: {a.get('phone_call_duration_seconds') or 'not set'}s" for a in call_actions),
                                 f"Set call length to at least {min_len} seconds so short calls are not counted." if bad_len else ""))
    many = [a for a in lead_actions + call_actions if a.get("counting_type") == "MANY_PER_CLICK"]
    checks.append(_check("counted_once", "Leads counted once", "FAIL" if many else "PASS", True,
                         ", ".join(f"{a['name']} counts every conversion" for a in many) or "All lead actions count one per click",
                         "Change counting to 'One' for lead conversions." if many else ""))
    dup = bool(ga_actions and lead_actions)
    checks.append(_check("duplicates", "No duplicate lead counting", "FAIL" if dup else "PASS", True,
                         (f"Imported {', '.join(a['name'] for a in ga_actions)} is primary alongside the Google Ads tag — same lead counted twice"
                          if dup else "No duplicate primary lead actions"),
                         "Set the imported Google Analytics conversion to secondary." if dup else ""))

    # --- recorded conversions (existing accounts)
    camps = list(db.scalars(select(Campaign).where(Campaign.business_id == business.id, Campaign.campaign_type == "SEARCH")))
    need = int(settings_service.get_value("tracking.green_min_conversions_30d", 3, db))
    recorded_status = "UNKNOWN"
    if account and camps:
        end = clock.today() - timedelta(days=1)
        ids = {c.external_id for c in camps}
        try:
            rows = [r for r in ads.get_metrics(account.customer_id, end - timedelta(days=29), end) if r["campaign_id"] in ids]
            conv = round(sum(r["conversions"] or 0 for r in rows), 1)
            clicks = sum(r["clicks"] for r in rows)
            recent = [r for r in rows if r["date"] >= (end - timedelta(days=6)).isoformat()]
            recent_conv = sum(r["conversions"] or 0 for r in recent)
            recent_clicks = sum(r["clicks"] for r in recent)
            if conv >= need and not (recent_clicks >= 60 and recent_conv == 0):
                recorded_status = "PASS"
                ev = f"{conv} conversions from {clicks} clicks in the last 30 days"
            elif (clicks >= 100 and conv == 0) or (recent_clicks >= 60 and recent_conv == 0):
                recorded_status = "FAIL_CRITICAL"
                ev = (f"{clicks} clicks, {conv} conversions in 30 days; last 7 days {recent_clicks} clicks / {recent_conv} conversions "
                      "— tracking appears broken")
            else:
                recorded_status = "FAIL"
                ev = f"Only {conv} conversions recorded in 30 days (need {need} to verify)"
        except AdapterError as exc:
            ev = f"Could not read conversions: {exc.kind}"
    else:
        ev = "New account/business: no lead history yet — submit a test lead to verify."
    checks.append(_check("recorded_conversions", f"Leads recorded in Google Ads (≥{need} in 30 days)",
                         {"PASS": "PASS", "FAIL_CRITICAL": "FAIL", "FAIL": "WARN"}.get(recorded_status, "UNKNOWN"),
                         recorded_status == "FAIL_CRITICAL", ev,
                         "" if recorded_status == "PASS" else "Submit a test lead and confirm it appears in Google Ads."))

    critical_fail = any(c["critical"] and c["status"] in ("FAIL", "UNKNOWN") for c in checks)
    if critical_fail:
        state = "RED"
    elif recorded_status != "PASS" or any(c["status"] == "FAIL" for c in checks):
        state = "AMBER"
    else:
        state = "GREEN"
    ov = active_override(db, business.id)
    effective = "AMBER" if (state == "RED" and ov) else state
    fix_list = [{"check": c["key"], "fix": c["fix"], "owner": business.client.tag_access_contact or "website manager"}
                for c in checks if c["fix"] and c["status"] in ("FAIL", "UNKNOWN", "WARN")]
    result = TrackingGateResult(business_id=business.id, google_ads_account_id=account.id if account else None, state=state,
                                effective_state=effective, override_active=bool(ov), checks=checks, fix_list=fix_list,
                                evaluated_at=clock.now(), source_type=source)
    db.add(result)
    for f in fix_list:
        if state == "RED" or f["check"] in ("call_min_length", "thank_you_page"):
            fixes_service.upsert(db, business.client_id, business.id, issue=f["fix"], category="tracking",
                                 priority="HIGH" if state == "RED" else "MEDIUM", owner=f["owner"], source="tracking_gate",
                                 dedupe_key=f"tracking:{business.id}:{f['check']}", evidence="Tracking gate check: " + f["check"])
    # resolve tracking fixes that now pass
    for c in checks:
        if c["status"] == "PASS":
            fixes_service.resolve_by_key(db, f"tracking:{business.id}:{c['key']}", "Re-check passed")
    if ov and state != "RED":
        ov.cleared_at = clock.now()
        ov.cleared_reason = f"Tracking re-check returned {state}"
    activity.log(db, "tracking_gate.evaluated", actor=actor, entity="business", entity_id=business.id,
                 client_id=business.client_id, details={"state": state, "effective": effective}, integration_mode=source)
    db.flush()
    return result


def override(db: Session, actor: User, business: Business, reason: str) -> TrackingOverride:
    auth_service.require(actor, "tracking.override")
    allowed = settings_service.get_value("tracking.override_roles", ["SUPER_ADMIN", "ADMIN"], db)
    if actor.role not in allowed:
        raise Forbidden("Your role is not allowed to override the tracking gate")
    if len((reason or "").strip()) < 15:
        raise ValidationFailed("A written reason (at least 15 characters) is required for an override")
    current = latest(db, business.id)
    if not current or current.state != "RED":
        raise ValidationFailed("Override is only available when the tracking gate is RED")
    ov = active_override(db, business.id)
    if ov:
        return ov
    ov = TrackingOverride(business_id=business.id, admin_user_id=actor.id, admin_email=actor.email, reason=reason.strip())
    db.add(ov)
    current.effective_state = "AMBER"
    current.override_active = True
    activity.log(db, "tracking_gate.override", actor=actor, entity="business", entity_id=business.id,
                 client_id=business.client_id, details={"reason": reason})
    db.flush()
    return ov


def serialize(r: TrackingGateResult | None) -> dict | None:
    if not r:
        return None
    return {"id": r.id, "business_id": r.business_id, "state": r.state, "effective_state": r.effective_state,
            "override_active": r.override_active, "checks": r.checks, "fix_list": r.fix_list,
            "evaluated_at": r.evaluated_at.isoformat(), "source_type": r.source_type,
            "automation_allowed": r.state == "GREEN" and not r.override_active}


def get_business_or_404(db: Session, business_id: str) -> Business:
    b = db.get(Business, business_id)
    if not b:
        raise NotFound("Business not found")
    return b
