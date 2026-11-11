"""Conversion tracking gate. Four states, and the difference between them is EVIDENCE:

* GREEN   - verified: nothing failed or is unknown, and conversions are actually recorded in Google Ads.
* YELLOW  - tracking exists but its configuration or verification is incomplete (e.g. no leads recorded yet).
* RED     - evidence CONFIRMS tracking is broken, invalid or misconfigured (a check that could be read failed).
* UNKNOWN - tracking cannot be assessed: required account access or data is unavailable (no linked account, integration not configured,
            account unreadable, never assessed, or the assessment predates the current account link).

Missing evidence is never a negative finding: it can produce UNKNOWN (or a non-critical "unknown" check), never RED. UNKNOWN does not become
GREEN or RED by itself. A detected tag only proves installation. An admin override (any state but GREEN) is the explicit authorisation for deployment; it behaves like YELLOW and never authorises
conversion-based automation. Preparation is blocked only by RED; live deployment needs more (see validation.validate(deployment=True)).
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

STATES = ("GREEN", "YELLOW", "RED", "UNKNOWN")


def normalize(state: str | None) -> str:
    """Stored rows written before the rename say AMBER; anything unrecognised is UNKNOWN, never RED."""
    return {"AMBER": "YELLOW"}.get(state or "", state if state in STATES else "UNKNOWN")


LEAD_FORM_TYPES = {"WEBPAGE", "WEBPAGE_ONCLICK", "UPLOAD_CLICKS"}
CALL_TYPES = {"AD_CALL", "WEBSITE_CALL", "CLICK_TO_CALL", "CALL_FROM_ADS"}
GA_TYPES = {"GOOGLE_ANALYTICS_4_CUSTOM", "GOOGLE_ANALYTICS_4_PURCHASE", "UNIVERSAL_ANALYTICS_GOAL"}


def _check(key, label, status, critical, evidence, fix=""):
    """status PASS / WARN / FAIL / UNKNOWN. ``critical`` only matters for FAIL (confirmed evidence). ``assessable`` records whether the
    evidence needed for this check was available: rows without it were written by the old rules and are treated as stale."""
    return {"key": key, "label": label, "status": status, "critical": bool(critical and status == "FAIL"), "evidence": evidence, "fix": fix,
            "assessable": status != "UNKNOWN"}


def active_override(db: Session, business_id: str) -> TrackingOverride | None:
    return db.scalar(select(TrackingOverride).where(TrackingOverride.business_id == business_id,
                                                    TrackingOverride.cleared_at.is_(None)))


def latest(db: Session, business_id: str) -> TrackingGateResult | None:
    return db.scalar(select(TrackingGateResult).where(TrackingGateResult.business_id == business_id)
                     .order_by(TrackingGateResult.evaluated_at.desc(), TrackingGateResult.created_at.desc()))


def assessment(db: Session, business_id: str) -> dict:
    """The CURRENT tracking assessment. A stored result is only trusted while it still describes this business's account situation:
    never assessed, written by the old rules, or taken before the account link changed -> UNKNOWN (reassess), never a cached RED."""
    r = latest(db, business_id)
    if r is None:
        return {"result": None, "state": "UNKNOWN", "effective_state": "UNKNOWN", "stale": False, "reason": "Tracking has not been assessed yet"}
    biz = db.get(Business, business_id)
    acct = accounts_service.account_for_business(db, biz) if biz else None
    cur = acct.id if (acct is not None and registry.optional("google_ads") is not None) else None
    if any("assessable" not in c for c in (r.checks or [])):
        why = "The last assessment used rules that treated missing evidence as a failure: it must be re-run"
    elif (r.google_ads_account_id or None) != cur:
        why = "The Google Ads account link changed since tracking was assessed: reassess"
    else:
        return {"result": r, "state": normalize(r.state), "effective_state": normalize(r.effective_state), "stale": False, "reason": ""}
    return {"result": None, "state": "UNKNOWN", "effective_state": "UNKNOWN", "stale": True, "reason": why, "previous_state": normalize(r.state)}


def effective_state(db: Session, business_id: str) -> str:
    return assessment(db, business_id)["effective_state"]


def current_result(db: Session, business: Business) -> TrackingGateResult | None:
    """The stored result only if it is still current (see ``assessment``)."""
    return assessment(db, business.id)["result"]


def classify(checks: list[dict], *, account_readable: bool, recorded_pass: bool) -> str:
    if any(c["critical"] and c["status"] == "FAIL" for c in checks):
        return "RED"                       # confirmed by evidence that could be read
    if not account_readable:
        return "UNKNOWN"                   # cannot assess: no account / integration / unreadable
    if recorded_pass and not any(c["status"] in ("FAIL", "UNKNOWN") for c in checks):
        return "GREEN"
    return "YELLOW"


def evaluate(db: Session, business: Business, actor: User | None = None) -> TrackingGateResult:
    ads = registry.optional("google_ads")
    account = accounts_service.account_for_business(db, business)
    if ads is None:  # Google Ads integration not configured: nothing can be read from Google, exactly as with no linked account
        account = None
    host = (urlparse(business.website_url).hostname or "").lower()
    checks: list[dict] = []
    source = ads.source if ads else "NONE"
    account_readable = account is not None  # False when the account (or what we need from it) cannot be read: the state is then UNKNOWN
    if not account:
        # Not critical: a campaign can be prepared without an account. Without one the tracking can never be VERIFIED (so never GREEN),
        # and deployment requires an account anyway.
        checks.append(_check("account_linked", "Google Ads account linked", "UNKNOWN", False,
                             "No account linked: conversions cannot be verified in Google Ads yet",
                             "Link the client's Google Ads account before deployment to verify conversion tracking."))
    else:
        try:
            cust = ads.get_customer(account.customer_id)
            on = cust.get("auto_tagging_enabled")
            checks.append(_check("auto_tagging", "Auto-tagging enabled", "PASS" if on else "FAIL", True,
                                 f"customer.auto_tagging_enabled = {on}", "" if on else "Turn on auto-tagging in Google Ads account settings."))
        except AdapterError as exc:
            account_readable = False
            checks.append(_check("auto_tagging", "Auto-tagging enabled", "UNKNOWN", False, f"Could not read account: {exc.kind}",
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
            account_readable = False
            checks.append(_check("conversion_actions", "Conversion actions readable", "UNKNOWN", False, exc.kind,
                                 "Fix the Google Ads connection, then re-run."))
    lead_actions = [a for a in actions if a.get("type") in LEAD_FORM_TYPES and a.get("primary_for_goal", True)]
    call_actions = [a for a in actions if a.get("type") in CALL_TYPES]
    ga_actions = [a for a in actions if a.get("type") in GA_TYPES and a.get("primary_for_goal")]
    if not account:
        # Conversion actions live in the Google Ads account: with none linked they are unknown (never "passed", never a hard failure).
        for key, label in (("form_conversion", "Form conversion action exists"), ("call_conversion", "Call conversion action exists"),
                           ("counted_once", "Leads counted once"), ("duplicates", "No duplicate lead counting")):
            if key == "call_conversion" and not business.phone:
                continue
            checks.append(_check(key, label, "UNKNOWN", False, "Conversion actions cannot be read until a Google Ads account is linked"))
    else:
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
            sum(r["conversions"] or 0 for r in recent)
            sum(r["clicks"] for r in recent)
            cutoff = (end - timedelta(days=6)).isoformat()
            broken_campaigns = []
            for cid_ in ids:
                c_rows = [r for r in rows if r["campaign_id"] == cid_]
                c_recent = [r for r in c_rows if r["date"] >= cutoff]
                if (sum(r["clicks"] for r in c_recent) >= 30 and sum(r["conversions"] or 0 for r in c_recent) == 0
                        and sum(r["conversions"] or 0 for r in c_rows if r["date"] < cutoff) >= 2):
                    broken_campaigns.append(cid_)
            broken_recently = bool(broken_campaigns)
            if conv >= need and not broken_recently:
                recorded_status = "PASS"
                ev = f"{conv} conversions from {clicks} clicks in the last 30 days"
            elif (clicks >= 100 and conv == 0) or broken_recently:
                recorded_status = "FAIL_CRITICAL"
                names = {c.external_id: c.name for c in camps}
                ev = (f"{clicks} clicks, {conv} conversions in 30 days; conversions stopped in the last 7 days despite clicks in "
                      f"{', '.join(names.get(x, x) for x in broken_campaigns) or 'all campaigns'} — tracking appears broken")
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

    # --- the Google tag check needs the account facts: an AW- tag is the account's conversion tag, so its absence is only evidence of a
    #     problem once an account exists to say which tag should be there; and recorded conversions prove the tag works whatever the crawl saw.
    if fetched == 0:
        tag = _check("google_tag", "Google tag on website", "UNKNOWN", False, "Website could not be fetched", "Make the website reachable for the audit.")
    elif aw:
        tag = _check("google_tag", "Google tag on website", "PASS", True,
                     f"Google Ads tag detected ({', '.join(aw)}) on {fetched} page(s). Detection proves installation, not that conversions fire.")
    elif recorded_status == "PASS":
        tag = _check("google_tag", "Google tag on website", "WARN", False, "No AW- tag was detected, but conversions are being recorded, so the tag works (likely loaded by Tag Manager or a script).")
    elif gtm:
        tag = _check("google_tag", "Google tag on website", "WARN", False, f"No AW- tag in the page source; Tag Manager is present ({', '.join(sorted(gtm))}) and may fire it. Cannot be confirmed from the page source.",
                     "Confirm in Tag Manager that the Google Ads conversion tag fires.")
    elif account:
        tag = _check("google_tag", "Google tag on website", "FAIL", True, f"No Google tag or Tag Manager found on {fetched} reachable page(s). Tags seen: {', '.join(sorted(tags)) or 'none'}",
                     "Install the Google Ads tag (or configure it in Google Tag Manager) on every page.")
    else:
        tag = _check("google_tag", "Google tag on website", "UNKNOWN", False,
                     "No Google tag found yet. The tag to install comes from the Google Ads conversion action, which needs a linked account, so this is not yet a failure.",
                     "Once the account is linked, install its conversion tag (or configure it in Tag Manager).")
    checks.insert(next((i for i, c in enumerate(checks) if c["key"] == "gtm"), len(checks)), tag)
    state = classify(checks, account_readable=account_readable and account is not None, recorded_pass=recorded_status == "PASS")
    ov = active_override(db, business.id)
    effective = "YELLOW" if (state in ("RED", "UNKNOWN") and ov) else state  # an override never makes tracking look better than YELLOW
    fix_list = [{"check": c["key"], "fix": c["fix"], "owner": business.client.tag_access_contact or "website manager"}
                for c in checks if c["fix"] and c["status"] in ("FAIL", "UNKNOWN", "WARN")]
    result = TrackingGateResult(business_id=business.id, google_ads_account_id=account.id if account else None, state=state,
                                effective_state=effective, override_active=bool(ov), checks=checks, fix_list=fix_list,
                                evaluated_at=clock.now(), source_type=source)
    db.add(result)
    for f in fix_list:  # client work is requested only for CONFIRMED problems (and two minor checks), never for missing evidence
        if state == "RED" or f["check"] in ("call_min_length", "thank_you_page"):
            fixes_service.upsert(db, business.client_id, business.id, issue=f["fix"], category="tracking",
                                 priority="HIGH" if state == "RED" else "MEDIUM", owner=f["owner"], source="tracking_gate",
                                 dedupe_key=f"tracking:{business.id}:{f['check']}", evidence="Tracking gate check: " + f["check"])
    # resolve tracking fixes that now pass
    for c in checks:
        if c["status"] == "PASS":
            fixes_service.resolve_by_key(db, f"tracking:{business.id}:{c['key']}", "Re-check passed")
    if ov and state == "GREEN":
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
    if assessment(db, business.id)["state"] == "GREEN":
        raise ValidationFailed("Tracking is already verified (GREEN): there is nothing to override")
    current = latest(db, business.id)
    if current is None:
        raise ValidationFailed("Assess tracking first: there is nothing to override yet")
    ov = active_override(db, business.id)
    if ov:
        return ov
    ov = TrackingOverride(business_id=business.id, admin_user_id=actor.id, admin_email=actor.email, reason=reason.strip())
    db.add(ov)
    current.effective_state = "YELLOW"
    current.override_active = True
    activity.log(db, "tracking_gate.override", actor=actor, entity="business", entity_id=business.id,
                 client_id=business.client_id, details={"reason": reason})
    db.flush()
    return ov


def outstanding(r: TrackingGateResult | None) -> list[dict]:
    """What still has to be verified or fixed, from the stored checks (confirmed failures first, then unknowns, then warnings)."""
    order = {"FAIL": 0, "UNKNOWN": 1, "WARN": 2}
    return [{"check": c["key"], "label": c["label"], "status": c["status"], "what": c["fix"] or c["evidence"]}
            for c in sorted((c for c in (r.checks if r else []) if c["status"] in order and (c["fix"] or c["status"] != "PASS")), key=lambda c: order[c["status"]])]


def serialize(r: TrackingGateResult | None, a: dict | None = None) -> dict | None:
    """``a`` is the current ``assessment``: a stale stored result is reported as UNKNOWN with the reason, its old verdict only as ``previous_state``."""
    if a is not None and a["stale"]:
        return {"id": r.id if r else None, "business_id": r.business_id if r else None, "state": "UNKNOWN", "effective_state": "UNKNOWN", "stale": True,
                "stale_reason": a["reason"], "previous_state": a.get("previous_state"), "override_active": False, "checks": [], "fix_list": [],
                "outstanding": [{"check": "reassess", "label": "Tracking assessment", "status": "UNKNOWN", "what": a["reason"]}],
                "evaluated_at": r.evaluated_at.isoformat() if r else None, "source_type": r.source_type if r else "NONE", "automation_allowed": False,
                "blocks_preparation": False, "deployment_ready": False}
    if not r:
        return None
    state, eff = normalize(r.state), normalize(r.effective_state)
    return {"id": r.id, "business_id": r.business_id, "state": state, "effective_state": eff, "stale": False, "stale_reason": "",
            "override_active": r.override_active, "checks": r.checks, "fix_list": r.fix_list, "outstanding": outstanding(r),
            "evaluated_at": r.evaluated_at.isoformat(), "source_type": r.source_type, "automation_allowed": state == "GREEN" and not r.override_active,
            "blocks_preparation": eff == "RED",  # only CONFIRMED broken tracking stops a draft being prepared
            "deployment_ready": eff == "GREEN" or bool(r.override_active)}


def serialize_for(db: Session, business_id: str) -> dict | None:
    a = assessment(db, business_id)
    return serialize(latest(db, business_id), a)


def get_business_or_404(db: Session, business_id: str) -> Business:
    b = db.get(Business, business_id)
    if not b:
        raise NotFound("Business not found")
    return b
