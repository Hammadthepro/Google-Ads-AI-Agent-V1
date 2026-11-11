"""Client intake validation: finds missing or inconsistent information BEFORE any research or AI runs.

Deterministic (no AI). Each issue has a stable `code`, so a team member's clarification can be recorded against it.
BLOCKER  → the task cannot be created until fixed.
QUESTION → an inconsistency a person must explain (answer is stored with the task); unanswered questions block creation.
WARNING  → proceed, but it is shown on the review card and may weaken results.
"""
from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Business, Client, GoogleAdsAccount
from app.services import clients_service, settings_service

WEEKS_PER_MONTH = 4.345


def _issue(code, severity, field, message, question=""):
    return {"code": code, "severity": severity, "field": field, "message": message, "question": question or ""}


def _host(url: str) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def validate(db: Session, business: Business, client: Client, task_type: str, fields: dict) -> dict:
    fields = fields or {}
    clar = {k: v for k, v in (fields.get("clarifications") or {}).items() if str(v).strip()}
    issues: list[dict] = []
    active = clients_service.active_services(business)
    active_names = {s.name.lower() for s in active}

    # ---- business profile completeness
    if not business.website_url:
        issues.append(_issue("NO_WEBSITE", "BLOCKER", "website_url", "The business has no website URL."))
    if not business.phone and not business.lead_form_url:
        issues.append(_issue("NO_CONVERSION_PATH", "BLOCKER", "phone",
                             "Neither a phone number nor a lead form URL is set, so leads cannot be measured."))
    if not business.address and not business.zip_codes:
        issues.append(_issue("NO_LOCATION", "BLOCKER", "address", "Neither an address nor ZIP codes are set, so targeting cannot be built."))
    if business.has_gbp is not False and not business.gbp_url:
        issues.append(_issue("NO_GBP", "WARNING", "gbp_url", "No Google Business Profile URL: review and competitor benchmarks will be missing."))
    if not business.business_hours:
        issues.append(_issue("NO_HOURS", "WARNING", "business_hours", "No business hours: the call asset cannot be limited to open hours."))
    if not business.verified_claims:
        issues.append(_issue("NO_VERIFIED_CLAIMS", "WARNING", "verified_claims",
                             "No verified claims. Ads will avoid claims entirely (licensed, insured, years in business...)."))
    if not business.thank_you_url and task_type == "BUILD_CAMPAIGN":
        issues.append(_issue("NO_THANK_YOU_PAGE", "WARNING", "thank_you_url", "No thank-you page: form conversions are harder to verify."))
    no_page = [s.name for s in active if not s.landing_page_url]
    if no_page and task_type == "BUILD_CAMPAIGN":
        issues.append(_issue("SERVICES_WITHOUT_PAGE", "WARNING", "services",
                             f"No landing page set for: {', '.join(no_page)}. They will land on the home page unless the site audit finds one."))
    account = db.get(GoogleAdsAccount, business.google_ads_account_id) if business.google_ads_account_id else None
    if account is None and client.id:
        account = db.scalar(select(GoogleAdsAccount).where(GoogleAdsAccount.client_id == client.id))
    if account is None:
        # Never a blocker for preparation (research, strategy, drafts, validation). The account is required only to read an account
        # (audit-only) and to deploy; execution enforces it.
        sev = "BLOCKER" if task_type == "AUDIT_ONLY" else "WARNING"
        issues.append(_issue("NO_ADS_ACCOUNT", sev, "google_ads_account_id",
                             "No Google Ads account is linked to this client." if sev == "BLOCKER" else
                             "No Google Ads account is linked yet. Preparation continues; link an account before deployment."))

    if task_type == "BUILD_CAMPAIGN":
        monthly = float(fields.get("monthly_budget") or 0)  # optional: the analysis recommends a budget when it is absent
        target = float(fields.get("weekly_target") or client.weekly_conversion_target or 0)
        wanted = [s for s in (fields.get("services") or []) if s]
        if not active:
            issues.append(_issue("NO_SERVICES", "BLOCKER", "services", "The business has no active services."))
        for s in wanted:
            if s.lower() not in active_names:
                issues.append(_issue("UNKNOWN_SERVICE", "BLOCKER", "services",
                                     f"'{s}' is not an active service of this business (or is marked excluded)."))
        if len({s.lower() for s in wanted}) != len(wanted):
            issues.append(_issue("DUPLICATE_SERVICE", "BLOCKER", "services", "A service is listed twice."))
        if monthly and monthly < 150:
            issues.append(_issue("BUDGET_TOO_LOW", "BLOCKER", "monthly_budget",
                                 f"${monthly:,.0f}/month is below the $150 minimum for a Search campaign."))
        weekly_budget = monthly / WEEKS_PER_MONTH if monthly else 0
        if monthly and target:
            implied_cpl = weekly_budget / target
            if implied_cpl < 8:
                issues.append(_issue("IMPLAUSIBLE_CPL", "QUESTION", "weekly_target",
                                     f"The target implies about ${implied_cpl:,.2f} per lead (${weekly_budget:,.0f}/week for {target:g} leads).",
                                     "That is far below typical lead costs. Is the target or the budget a typo? Explain why this is realistic."))
        if monthly and wanted and monthly / len(wanted) < 300:
            issues.append(_issue("THIN_BUDGET_PER_SERVICE", "WARNING", "monthly_budget",
                                 f"${monthly / len(wanted):,.0f}/month per service is thin; consider fewer services."))
        radius = float(fields.get("radius_miles") or business.default_radius_miles or 0)
        if radius > 50:
            issues.append(_issue("WIDE_RADIUS", "QUESTION", "radius_miles", f"A {radius:g}-mile radius is very wide for a local service.",
                                 "Does the business really serve this whole area?"))
        if fields.get("zip_codes") and fields.get("radius_miles") and float(fields["radius_miles"]) != float(business.default_radius_miles or 0):
            issues.append(_issue("ZIP_AND_RADIUS", "QUESTION", "zip_codes",
                                 "Both ZIP codes and a custom radius were given; ZIP codes take precedence.",
                                 "Should the radius be ignored?"))
        if business.lead_form_url and business.website_url and _host(business.lead_form_url) != _host(business.website_url):
            issues.append(_issue("FORM_ON_OTHER_DOMAIN", "QUESTION", "lead_form_url",
                                 f"The lead form is on {_host(business.lead_form_url)}, not on the website domain ({_host(business.website_url)}).",
                                 "Is conversion tracking installed on that domain too?"))
        if not client.weekly_conversion_target:
            issues.append(_issue("NO_CLIENT_TARGET", "WARNING", "client.weekly_conversion_target",
                                 "The client has no weekly lead target; weekly reports can only compare per campaign."))
        elif target and target > client.weekly_conversion_target:
            issues.append(_issue("TARGET_ABOVE_CLIENT_TARGET", "QUESTION", "weekly_target",
                                 f"This campaign's target ({target:g}/week) exceeds the client's total weekly target ({client.weekly_conversion_target:g}).",
                                 "Should the client's target be raised, or this one lowered?"))
        if client.monthly_budget_usd and monthly and abs(monthly - client.monthly_budget_usd) / client.monthly_budget_usd > 0.25:
            issues.append(_issue("BUDGET_DIFFERS_FROM_CLIENT", "QUESTION", "monthly_budget",
                                 f"${monthly:,.0f}/month differs by more than 25% from the client's agreed budget (${client.monthly_budget_usd:,.0f}).",
                                 "Was a different budget agreed with the client?"))
    for i in issues:
        if i["severity"] == "QUESTION" and i["code"] in clar:
            i["answer"], i["severity"] = clar[i["code"]], "RESOLVED"
    blockers = [i for i in issues if i["severity"] == "BLOCKER"]
    questions = [i for i in issues if i["severity"] == "QUESTION"]
    status = "BLOCKED" if blockers else ("NEEDS_CLARIFICATION" if questions else "OK")
    return {"status": status, "issues": issues, "blockers": len(blockers), "questions": len(questions),
            "warnings": len([i for i in issues if i["severity"] == "WARNING"]),
            "min_clean_budget": settings_service.get_value("intake.min_monthly_budget", 150, db)}
