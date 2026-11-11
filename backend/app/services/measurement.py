"""Conversion measurement audit: what is actually configured and recorded, as findings with evidence.

There is no overall status, no gate and no override. Each finding says what was inspected, the actual result, the evidence, the business
impact, and what to do about it. A finding that could not be assessed (no linked account, unreadable account, unreachable site) says so:
missing evidence is never reported as a problem.

Nothing here blocks research, strategy, drafting or deployment. Where measurement is a real technical dependency of ONE operation, the
operation asks a specific question through ``facts`` and explains the consequence itself:
* conversion-based bidding has something to optimise toward only if a conversion action is recording (``supports_conversion_bidding``);
* a conclusion drawn from "no conversions" (pausing a keyword, excluding a search term) is only valid if conversions are being counted
  reliably (``conversion_data_reliable``).
"""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import registry
from app.adapters.base import AdapterError
from app.core import clock
from app.core.errors import NotFound
from app.models import Business, Campaign, MeasurementAudit, User
from app.services import accounts_service, activity, crawler_service, fixes_service, settings_service

LEAD_FORM_TYPES = {"WEBPAGE", "WEBPAGE_ONCLICK", "UPLOAD_CLICKS"}
CALL_TYPES = {"AD_CALL", "WEBSITE_CALL", "CLICK_TO_CALL", "CALL_FROM_ADS"}
GA_TYPES = {"GOOGLE_ANALYTICS_4_CUSTOM", "GOOGLE_ANALYTICS_4_PURCHASE", "UNIVERSAL_ANALYTICS_GOAL"}
RESULTS = ("ok", "issue", "attention", "not_assessable")  # per finding only; never aggregated into a status for the client


def finding(key: str, inspected: str, result: str, actual: str, evidence: str, *, severity: str | None = None, impact: str = "", action: str = "",
            action_type: str = "informational", dependency: str = "") -> dict:
    """action_type: informational | needs_approval | needs_dependency (then ``dependency`` names it)."""
    assert result in RESULTS and action_type in ("informational", "needs_approval", "needs_dependency")
    return {"key": key, "inspected": inspected, "result": result, "actual": actual, "evidence": evidence, "severity": severity if result == "issue" else None,
            "impact": impact, "action": action, "action_type": action_type, "dependency": dependency}


def latest(db: Session, business_id: str) -> MeasurementAudit | None:
    return db.scalar(select(MeasurementAudit).where(MeasurementAudit.business_id == business_id)
                     .order_by(MeasurementAudit.evaluated_at.desc(), MeasurementAudit.created_at.desc()))


def _readable_account(db: Session, business: Business):
    acct = accounts_service.account_for_business(db, business)
    return acct if (acct is not None and registry.optional("google_ads") is not None) else None


def current(db: Session, business: Business) -> MeasurementAudit | None:
    """The latest audit, only while it still describes this business: an audit taken under a different account link (or before any audit in the
    present format existed) is not evidence about today, so it is not returned."""
    r = latest(db, business.id)
    if r is None or not r.facts:
        return None
    acct = _readable_account(db, business)
    return r if (r.google_ads_account_id or None) == (acct.id if acct else None) else None


def audit(db: Session, business: Business, actor: User | None = None) -> MeasurementAudit:
    """Read the website and (when one is linked and readable) the Google Ads account, and record what was found. Read-only towards Google."""
    ads = registry.optional("google_ads")
    account = accounts_service.account_for_business(db, business) if ads is not None else None
    host = (urlparse(business.website_url).hostname or "").lower()
    out: list[dict] = []
    no_account = ("No Google Ads account is linked to this client" if ads is not None else "The Google Ads integration is not configured")
    link_dep = "a linked, readable Google Ads account"
    facts: dict = {"account_linked": account is not None, "account_readable": account is not None, "conversion_actions": None, "primary_lead_action": None,
                   "call_action": None, "counting_reliable": None, "recorded_30d": None, "clicks_30d": None, "suspected_outage": None, "tag_detected": None,
                   "tag_manager": None, "auto_tagging": None, "values_configured": None}

    # ---- account-level settings
    if account is None:
        out.append(finding("account", "Google Ads account available for measurement checks", "not_assessable", no_account,
                           "Conversion actions, counting, values, attribution and recorded conversions live in the account.",
                           action="Link the client's Google Ads account to assess measurement.", action_type="needs_dependency", dependency=link_dep))
    else:
        try:
            on = ads.get_customer(account.customer_id).get("auto_tagging_enabled")
            facts["auto_tagging"] = bool(on)
            out.append(finding("auto_tagging", "Auto-tagging (GCLID) on the account", "ok" if on else "issue", "enabled" if on else "disabled",
                               f"customer.auto_tagging_enabled = {on}", severity="high",
                               impact="" if on else "Without auto-tagging, clicks cannot be tied to conversions, so conversion reporting and conversion-based bidding lose their signal.",
                               action="" if on else "Turn on auto-tagging in the Google Ads account settings.", action_type="informational" if on else "needs_approval"))
        except AdapterError as exc:
            facts["account_readable"] = False
            out.append(finding("auto_tagging", "Auto-tagging (GCLID) on the account", "not_assessable", "The account could not be read", f"Google Ads read failed: {exc.kind}",
                               action="Fix the Google Ads connection, then run the audit again.", action_type="needs_dependency", dependency="a working Google Ads connection"))

    # ---- website: tag and thank-you page
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
    if fetched:
        facts["tag_detected"], facts["tag_manager"] = bool(aw), bool(gtm)

    # ---- conversion actions
    actions, readable = [], False
    if account is not None and facts["account_readable"]:
        try:
            enabled = [a for a in ads.list_conversion_actions(account.customer_id) if a.get("status", "ENABLED") == "ENABLED"]
            actions = [a for a in enabled if not a.get("business_host") or a.get("business_host") == host]
            readable = True
        except AdapterError as exc:
            facts["account_readable"] = False
            out.append(finding("conversion_actions", "Conversion actions in the account", "not_assessable", "They could not be read", f"Google Ads read failed: {exc.kind}",
                               action="Fix the Google Ads connection, then run the audit again.", action_type="needs_dependency", dependency="a working Google Ads connection"))
    lead = [a for a in actions if a.get("type") in LEAD_FORM_TYPES and a.get("primary_for_goal", True)]
    calls = [a for a in actions if a.get("type") in CALL_TYPES]
    ga = [a for a in actions if a.get("type") in GA_TYPES and a.get("primary_for_goal")]
    primary = [a for a in actions if a.get("primary_for_goal", True)]
    if readable:
        facts.update(conversion_actions=len(actions), primary_lead_action=bool(lead), call_action=bool(calls))
        out.append(finding("conversion_actions", "Conversion actions exist", "ok" if actions else "issue", f"{len(actions)} enabled conversion action(s)",
                           ", ".join(f"{a['name']} ({a.get('type')}, {'primary' if a.get('primary_for_goal', True) else 'secondary'})" for a in actions[:12]) or "none returned by Google Ads",
                           severity="high", impact="" if actions else "With no conversion action, Google Ads records no outcomes: results cannot be measured and conversion-based bidding has nothing to optimise toward.",
                           action="" if actions else "Create a conversion action for the outcome that matters (lead form submission, qualified call, purchase).",
                           action_type="informational" if actions else "needs_approval"))
        if actions:
            out.append(finding("primary_conversion", "A primary website lead conversion is used for bidding", "ok" if lead else "issue",
                               ", ".join(a["name"] for a in lead) or "no primary lead-form conversion", f"{len(primary)} primary and {len(actions) - len(primary)} secondary action(s)",
                               severity="high", impact="" if lead else "Bidding optimises toward primary actions only. Without a primary lead action it optimises toward something else, or nothing.",
                               action="" if lead else "Make the genuine lead outcome (form submission on the thank-you page) a primary conversion action.",
                               action_type="informational" if lead else "needs_approval"))
            if business.phone:
                min_len = int(settings_service.get_value("measurement.call_min_seconds", 60, db))
                short = [a for a in calls if not a.get("phone_call_duration_seconds") or int(a["phone_call_duration_seconds"]) < min_len]
                out.append(finding("call_conversion", "Phone calls are measured", "ok" if calls and not short else "attention" if calls else "issue",
                                   (", ".join(f"{a['name']}: {a.get('phone_call_duration_seconds') or 'no minimum'}s" for a in calls)) or "no call conversion action",
                                   f"The business lists the phone number {business.phone}", severity="medium",
                                   impact=("Calls from ads are not counted, so phone leads are invisible in reporting." if not calls else
                                           f"Calls shorter than {min_len}s are counted as leads, which inflates conversions." if short else ""),
                                   action=("Create a call conversion (calls from ads / website calls)." if not calls else f"Set the minimum call length to at least {min_len} seconds." if short else ""),
                                   action_type="needs_approval" if (not calls or short) else "informational"))
            many = [a for a in lead + calls if a.get("counting_type") == "MANY_PER_CLICK"]
            dup = bool(ga and lead)
            facts["counting_reliable"] = not many and not dup
            out.append(finding("counting", "Leads are counted once per click", "issue" if many else "ok", ", ".join(f"{a['name']} counts every conversion" for a in many) or "one per click",
                               "counting_type of the lead and call actions", severity="high", impact="One person submitting twice is reported as two leads, overstating results and misleading bidding." if many else "",
                               action="Set counting to 'One' for lead conversions." if many else "", action_type="needs_approval" if many else "informational"))
            out.append(finding("duplicates", "The same lead is not counted by two primary actions", "issue" if dup else "ok",
                               (f"Imported {', '.join(a['name'] for a in ga)} is primary alongside {', '.join(a['name'] for a in lead)}" if dup else "no duplicate primary lead actions"),
                               "primary actions by type (Google Ads tag vs imported Analytics)", severity="high",
                               impact="Each lead is counted twice, so cost per lead looks half of what it is and bidding overvalues every click." if dup else "",
                               action="Set the imported Google Analytics conversion to secondary." if dup else "", action_type="needs_approval" if dup else "informational"))
            with_value = [a for a in primary if float(((a.get("value_settings") or {}).get("default_value")) or 0) > 0]
            has_value_info = any("value_settings" in a for a in primary)
            facts["values_configured"] = bool(with_value) if has_value_info else None
            if has_value_info:
                out.append(finding("conversion_values", "Conversion values are configured", "ok" if with_value else "attention",
                                   f"{len(with_value)} of {len(primary)} primary action(s) carry a value", "conversion_action.value_settings.default_value",
                                   impact="" if with_value else "Without values, return on ad spend cannot be reported and value-based bidding is not available. Not required for lead volume goals.",
                                   action="" if with_value else "Add a value per lead (average customer value x close rate) if value reporting or value bidding is wanted."))
            else:
                out.append(finding("conversion_values", "Conversion values are configured", "not_assessable", "Value settings were not returned for these actions",
                                   "conversion_action.value_settings was absent from the account read"))
            models = sorted({(a.get("attribution_model_settings") or {}).get("attribution_model") for a in primary if (a.get("attribution_model_settings") or {}).get("attribution_model")})
            if models:
                old = [m for m in models if m not in ("GOOGLE_SEARCH_ATTRIBUTION_DATA_DRIVEN", "GOOGLE_ADS_LAST_CLICK")]
                out.append(finding("attribution", "Attribution model of the primary actions", "attention" if old else "ok", ", ".join(models), "conversion_action.attribution_model_settings",
                                   impact="Rule-based models other than last click are retired by Google and are converted to data-driven." if old else "",
                                   action="Review the attribution model in Google Ads." if old else ""))
            else:
                out.append(finding("attribution", "Attribution model of the primary actions", "not_assessable", "The attribution model was not returned for these actions",
                                   "conversion_action.attribution_model_settings was absent from the account read"))
    elif account is None:
        for key, what in (("conversion_actions", "Conversion actions exist"), ("primary_conversion", "A primary website lead conversion is used for bidding"),
                          ("counting", "Leads are counted once per click"), ("conversion_values", "Conversion values are configured"), ("attribution", "Attribution model of the primary actions")):
            out.append(finding(key, what, "not_assessable", no_account, "This is read from the Google Ads account.", action_type="needs_dependency", dependency=link_dep))

    # ---- recorded conversions
    camps = list(db.scalars(select(Campaign).where(Campaign.business_id == business.id, Campaign.campaign_type == "SEARCH")))
    need = int(settings_service.get_value("measurement.min_conversions_30d", 3, db))
    if account is not None and facts["account_readable"] and camps:
        end = clock.today() - timedelta(days=1)
        ids, names = {c.external_id for c in camps}, {c.external_id: c.name for c in camps}
        try:
            rows = [r for r in ads.get_metrics(account.customer_id, end - timedelta(days=29), end) if r["campaign_id"] in ids]
            conv, clicks = round(sum(r["conversions"] or 0 for r in rows), 1), sum(r["clicks"] for r in rows)
            cutoff = (end - timedelta(days=6)).isoformat()
            stopped = []
            for cid in ids:
                c_rows = [r for r in rows if r["campaign_id"] == cid]
                recent = [r for r in c_rows if r["date"] >= cutoff]
                if (sum(r["clicks"] for r in recent) >= 30 and sum(r["conversions"] or 0 for r in recent) == 0
                        and sum(r["conversions"] or 0 for r in c_rows if r["date"] < cutoff) >= 2):
                    stopped.append(names.get(cid, cid))
            outage = bool(stopped) or (clicks >= 100 and conv == 0)
            facts.update(recorded_30d=conv, clicks_30d=clicks, suspected_outage=outage)
            if outage:
                out.append(finding("recorded_conversions", "Conversions are being recorded", "issue", f"{conv:g} conversions from {clicks} clicks in 30 days",
                                   (f"Conversions stopped in the last 7 days despite clicks in {', '.join(stopped)}" if stopped else f"{clicks} clicks and no conversion in 30 days"), severity="high",
                                   impact="Either leads stopped arriving or they stopped being recorded. Until this is known, cost-per-lead figures and any conversion-based decision are unreliable.",
                                   action="Submit a test lead and check that it appears in Google Ads; check the form, the thank-you page and the tag.", action_type="needs_approval"))
            elif conv >= need:
                out.append(finding("recorded_conversions", "Conversions are being recorded", "ok", f"{conv:g} conversions from {clicks} clicks in 30 days", "Google Ads campaign metrics, last 30 days"))
            else:
                out.append(finding("recorded_conversions", "Conversions are being recorded", "attention", f"{conv:g} conversions from {clicks} clicks in 30 days",
                                   "Google Ads campaign metrics, last 30 days", impact=f"Fewer than {need} conversions: too few to confirm that recording works or to judge performance.",
                                   action="Submit a test lead and confirm it appears in Google Ads."))
        except AdapterError as exc:
            out.append(finding("recorded_conversions", "Conversions are being recorded", "not_assessable", "Campaign metrics could not be read", f"Google Ads read failed: {exc.kind}",
                               action_type="needs_dependency", dependency="a working Google Ads connection"))
    else:
        why = no_account if account is None else ("The account could not be read" if not facts["account_readable"] else "No Search campaign of this business has run yet")
        out.append(finding("recorded_conversions", "Conversions are being recorded", "not_assessable", why, "Recorded conversions come from campaigns that have served.",
                           action="After launch, submit a test lead and confirm it appears in Google Ads.",
                           action_type="needs_dependency", dependency=link_dep if account is None else "campaign history"))

    # ---- the Google tag (needs the facts above: recorded conversions prove a tag works whatever the page source shows)
    if fetched == 0:
        tag = finding("google_tag", "Google tag on the website", "not_assessable", "The website could not be fetched", ", ".join(pages) or "no website URL",
                      action="Make the website reachable, then run the audit again.", action_type="needs_dependency", dependency="a reachable website")
    elif aw:
        tag = finding("google_tag", "Google tag on the website", "ok", f"Google Ads tag {', '.join(aw)} on {fetched} page(s)", "page source of the website, lead form and thank-you page",
                      impact="Detection shows the tag is installed; only a recorded conversion shows that it fires.")
    elif (facts["recorded_30d"] or 0) > 0 and not facts["suspected_outage"]:
        tag = finding("google_tag", "Google tag on the website", "ok", "No AW- tag in the page source, but conversions are being recorded",
                      "recorded conversions in Google Ads; the tag is probably loaded by Tag Manager or a script")
    elif gtm:
        tag = finding("google_tag", "Google tag on the website", "attention", f"No AW- tag in the page source; Tag Manager {', '.join(sorted(gtm))} is present",
                      "page source", impact="Tag Manager may fire the conversion tag, which cannot be confirmed from the page source.", action="Confirm in Tag Manager that the Google Ads conversion tag fires.")
    elif account is not None:
        tag = finding("google_tag", "Google tag on the website", "issue", f"No Google tag or Tag Manager on {fetched} reachable page(s)", f"Tags seen: {', '.join(sorted(tags)) or 'none'}",
                      severity="high", impact="Website conversions cannot be recorded without the tag.", action="Install the Google Ads tag (or configure it in Tag Manager) on every page.", action_type="needs_approval")
    else:
        tag = finding("google_tag", "Google tag on the website", "attention", f"No Google tag found on {fetched} reachable page(s)", f"Tags seen: {', '.join(sorted(tags)) or 'none'}",
                      impact="The conversion tag comes from the Google Ads account's conversion action, so there is nothing to install until an account is linked.",
                      action="Once the account is linked, install its conversion tag.", action_type="needs_dependency", dependency=link_dep)
    out.insert(1, tag)
    if business.thank_you_url:
        out.append(finding("thank_you_page", "Thank-you page reachable", "ok" if thank_you_ok else "issue", business.thank_you_url, "HTTP fetch of the page", severity="medium",
                           impact="" if thank_you_ok else "Form conversions that fire on this page are not being recorded while it is unreachable.",
                           action="" if thank_you_ok else "Restore the thank-you page.", action_type="informational" if thank_you_ok else "needs_approval"))
    else:
        out.append(finding("thank_you_page", "Thank-you page set", "attention", "No thank-you page is recorded for this business", "client profile",
                           impact="Form submissions are harder to measure reliably without a confirmation page or event.", action="Add the thank-you page URL to the client profile."))
    if account is not None:
        out.append(finding("enhanced_conversions", "Enhanced conversions for leads", "not_assessable", "Not read by this audit",
                           "Enhanced-conversion settings are not exposed through the fields this app reads.",
                           impact="For lead businesses, sending qualified/closed leads back to Google improves bidding quality. Relevant once a CRM holds lead outcomes.",
                           action_type="needs_dependency", dependency="a CRM with lead outcomes and the Data Manager API integration"))

    row = MeasurementAudit(business_id=business.id, google_ads_account_id=account.id if account else None, findings=out, facts=facts,
                           evaluated_at=clock.now(), source_type=ads.source if ads else "NONE")
    db.add(row)
    # client work is requested only for confirmed problems; each is resolved when its finding is no longer an issue
    for f in out:
        key = f"measurement:{business.id}:{f['key']}"
        if f["result"] == "issue" and f["action"]:
            fixes_service.upsert(db, business.client_id, business.id, issue=f["action"], category="measurement", priority="HIGH" if f["severity"] == "high" else "MEDIUM",
                                 owner=business.client.tag_access_contact or "website manager", source="measurement_audit", dedupe_key=key, evidence=f"{f['inspected']}: {f['actual']}")
        elif f["result"] == "ok":
            fixes_service.resolve_by_key(db, key, "The measurement audit no longer finds this problem")
    activity.log(db, "measurement.audited", actor=actor, entity="business", entity_id=business.id, client_id=business.client_id,
                 details={"issues": [f["key"] for f in out if f["result"] == "issue"], "not_assessable": [f["key"] for f in out if f["result"] == "not_assessable"]},
                 integration_mode=row.source_type)
    db.flush()
    return row


# ------------------------------------------------------------------------------------------------ specific dependencies (asked by one operation at a time)
def facts_for(db: Session, business: Business | None) -> dict | None:
    r = current(db, business) if business else None
    return r.facts if r else None


def supports_conversion_bidding(facts: dict | None) -> tuple[bool | None, str]:
    """(answer, reason). None = cannot be determined from the evidence available (the caller says so; it is not treated as 'no')."""
    if not facts or not facts.get("account_readable"):
        return None, "No measurement audit against a linked account exists, so it is not known whether a conversion action is recording."
    if not facts.get("conversion_actions"):
        return False, "The account has no conversion action, so conversion-based bidding would have nothing to optimise toward."
    if not facts.get("primary_lead_action"):
        return False, "No primary lead conversion is configured, so conversion-based bidding would optimise toward the wrong outcome."
    if facts.get("suspected_outage"):
        return False, "Conversions stopped being recorded despite clicks, so conversion-based bidding would currently learn from missing data."
    return True, "A primary conversion action exists" + (f" and recorded {facts['recorded_30d']:g} conversion(s) in 30 days." if facts.get("recorded_30d") else "; nothing is recorded yet.")


def conversion_data_reliable(db: Session, business: Business | None) -> tuple[bool, str]:
    """Can a conclusion be drawn from conversion counts (including from their absence)? Requires evidence that conversions are recorded and
    counted once. This is the only place 'no evidence' leads to a no: an automated decision must not rest on unverified data."""
    f = facts_for(db, business)
    if not f or not f.get("account_readable"):
        return False, "conversion recording has not been verified against the linked account"
    need = int(settings_service.get_value("measurement.min_conversions_30d", 3, db))
    if f.get("suspected_outage"):
        return False, "conversions stopped being recorded despite clicks"
    if f.get("counting_reliable") is False:
        return False, "leads are double-counted (counting or duplicate primary actions)"
    if (f.get("recorded_30d") or 0) < need:
        return False, f"fewer than {need} conversions were recorded in 30 days"
    return True, f"{f['recorded_30d']:g} conversions recorded in 30 days, counted once"


def serialize(r: MeasurementAudit | None) -> dict | None:
    if not r:
        return None
    order = {"issue": 0, "attention": 1, "not_assessable": 2, "ok": 3}
    fs = sorted(r.findings or [], key=lambda f: order.get(f["result"], 9))
    ok, why = supports_conversion_bidding(r.facts)
    return {"id": r.id, "business_id": r.business_id, "findings": fs, "facts": r.facts, "evaluated_at": r.evaluated_at.isoformat(), "source_type": r.source_type,
            "account_assessed": bool(r.google_ads_account_id), "counts": {k: sum(1 for f in fs if f["result"] == k) for k in RESULTS},
            "conversion_bidding": {"supported": ok, "reason": why}}


def serialize_for(db: Session, business: Business) -> dict | None:
    """The current audit; when the stored one no longer describes the business (account link changed), nothing is shown as if it did."""
    r = current(db, business)
    if r is None and latest(db, business.id) is not None:
        return {"id": None, "business_id": business.id, "findings": [], "facts": {}, "evaluated_at": None, "source_type": "NONE", "account_assessed": False,
                "counts": {k: 0 for k in RESULTS}, "conversion_bidding": {"supported": None, "reason": supports_conversion_bidding(None)[1]},
                "outdated": "The Google Ads account link changed since the last measurement audit. Run it again."}
    return serialize(r)


def get_business_or_404(db: Session, business_id: str) -> Business:
    b = db.get(Business, business_id)
    if not b:
        raise NotFound("Business not found")
    return b
