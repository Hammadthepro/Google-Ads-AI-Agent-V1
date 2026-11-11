"""Google Ads account discovery, linking and structure sync (adapter -> our mirror tables)."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import registry
from app.core.errors import IntegrationNotConfigured, NotFound
from app.models import Ad, AdGroup, Business, Campaign, ConversionAction, GoogleAdsAccount, Keyword, NegativeKeyword, User
from app.services import activity, auth_service


def adapter_mode() -> str:
    return "REAL" if registry.get("google_ads").is_real else "MOCK"  # MOCK only ever appears when a test fake is installed


def source() -> str:
    return registry.get("google_ads").source


def source_or_none() -> str | None:
    """The data source label for screens that must still render when Google Ads is not configured."""
    try:
        return registry.get("google_ads").source
    except IntegrationNotConfigured:
        return None


def serialize_account(a: GoogleAdsAccount) -> dict:
    return {"id": a.id, "client_id": a.client_id, "customer_id": a.customer_id, "descriptive_name": a.descriptive_name,
            "currency_code": a.currency_code, "time_zone": a.time_zone, "is_manager": a.is_manager,
            "parent_customer_id": a.parent_customer_id, "permission_status": a.permission_status,
            "writes_allowed": a.writes_allowed, "auto_tagging_enabled": a.auto_tagging_enabled,
            "integration_mode": a.integration_mode, "status": a.status, "level": a.level, "is_test_account": a.is_test_account,
            "discovery_state": a.discovery_state, "customer_id_display": f"{a.customer_id[:3]}-{a.customer_id[3:6]}-{a.customer_id[6:]}", "last_synced_at": a.last_synced_at.isoformat() if a.last_synced_at else None}


def link(db: Session, user: User, account_id: str, client_id: str | None) -> GoogleAdsAccount:
    auth_service.require(user, "clients.edit")
    a = db.get(GoogleAdsAccount, account_id)
    if not a:
        raise NotFound("Account not found")
    a.client_id = client_id
    activity.log(db, "ads_account.linked", actor=user, entity="google_ads_account", entity_id=a.id, client_id=client_id)
    reassess_tracking(db, user, client_id)
    return a


def reassess_tracking(db: Session, user: User | None, client_id: str | None) -> list[str]:
    """Linking (or unlinking) an account changes what tracking evidence exists, so the client's tracking is assessed again at once: a result
    taken without the account must not keep describing the business. A failed assessment is recorded and leaves the state UNKNOWN."""
    if not client_id:
        return []
    from app.services import tracking_gate
    db.flush()  # the link itself must be visible to the assessment's account lookup
    done = []
    for b in db.scalars(select(Business).where(Business.client_id == client_id)):
        if not b.website_url:
            continue
        try:
            with db.begin_nested():
                tracking_gate.evaluate(db, b, user)
            done.append(b.id)
        except Exception as exc:  # noqa: BLE001 - the stale result already reads as UNKNOWN, so nothing wrong is left behind
            activity.log(db, "tracking_gate.reassess_failed", actor=user, entity="business", entity_id=b.id, client_id=client_id, details={"error": type(exc).__name__})
    return done


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def sync_account(db: Session, account: GoogleAdsAccount) -> dict:
    """Read the account structure from the adapter into our mirror. Never writes to Google Ads."""
    ads = registry.get("google_ads")
    cid = account.customer_id
    cust = ads.get_customer(cid)
    account.auto_tagging_enabled = cust.get("auto_tagging_enabled")
    businesses = list(db.scalars(select(Business).where(Business.client_id == account.client_id))) if account.client_id else []
    by_host = {_host(b.website_url): b for b in businesses if b.website_url}
    counts = {"campaigns": 0, "ad_groups": 0, "ads": 0, "keywords": 0, "negatives": 0, "conversion_actions": 0}
    budgets = {b["resource_name"]: b for b in ads.list_resources(cid, "campaign_budget")}
    all_ads = ads.list_ads(cid)
    all_groups = ads.list_ad_groups(cid)
    all_criteria = ads.list_resources(cid, "ad_group_criterion")
    camp_criteria = ads.list_campaign_criteria(cid)
    seen_campaign_ids = set()
    for c in ads.list_campaigns(cid):
        row = db.scalar(select(Campaign).where(Campaign.google_ads_account_id == account.id, Campaign.external_id == c["id"]))
        if not row:
            row = Campaign(google_ads_account_id=account.id, external_id=c["id"], name=c.get("name", ""))
            db.add(row)
        seen_campaign_ids.add(c["id"])
        row.external_resource_name = c["resource_name"]
        row.name = c.get("name", row.name)
        row.status = c.get("status", row.status)
        row.campaign_type = c.get("advertising_channel_type", "SEARCH")
        row.is_read_only = row.campaign_type != "SEARCH"
        row.client_id = account.client_id
        row.integration_mode = adapter_mode()
        row.source_type = ads.source
        budget = budgets.get(c.get("campaign_budget", ""))
        if budget:
            row.daily_budget_micros = int(budget.get("amount_micros", 0))
            if not row.monthly_budget_micros:
                row.monthly_budget_micros = int(row.daily_budget_micros * 30.4)
        row.bidding_strategy = ("MAXIMIZE_CLICKS" if "target_spend" in c or "maximize_clicks" in c else "MAXIMIZE_CONVERSIONS")
        row.bidding_system_status = c.get("bidding_strategy_system_status", "")
        if c.get("weekly_conversion_target") and not row.weekly_conversion_target:
            row.weekly_conversion_target = float(c["weekly_conversion_target"])
        if c.get("start_date") and not row.launched_at:
            row.launched_at = datetime.fromisoformat(c["start_date"]).replace(tzinfo=timezone.utc)
        row.locations = [{"type": cc.get("type"), **({"proximity": cc["proximity"]} if "proximity" in cc else {}),
                          **({"geo_target_constant": cc.get("location", {}).get("geo_target_constant")} if cc.get("type") == "LOCATION" else {})}
                         for cc in camp_criteria if cc["parent"] == c["resource_name"] and cc.get("type") in ("LOCATION", "PROXIMITY")]
        db.flush()
        counts["campaigns"] += 1
        hosts = set()
        for g in [g for g in all_groups if g["parent"] == c["resource_name"]]:
            grow = db.scalar(select(AdGroup).where(AdGroup.campaign_id == row.id, AdGroup.external_id == g["id"]))
            if not grow:
                grow = AdGroup(campaign_id=row.id, external_id=g["id"], name=g.get("name", ""))
                db.add(grow)
            grow.external_resource_name, grow.name, grow.status = g["resource_name"], g.get("name", ""), g.get("status", "PAUSED")
            grow.service = g.get("service", grow.service)
            db.flush()
            counts["ad_groups"] += 1
            for a in [a for a in all_ads if a["parent"] == g["resource_name"]]:
                arow = db.scalar(select(Ad).where(Ad.ad_group_id == grow.id, Ad.external_id == a["id"]))
                if not arow:
                    arow = Ad(ad_group_id=grow.id, external_id=a["id"])
                    db.add(arow)
                rsa = (a.get("ad") or {}).get("responsive_search_ad") or {}
                arow.external_resource_name, arow.status = a["resource_name"], a.get("status", "PAUSED")
                arow.headlines, arow.descriptions = rsa.get("headlines", []), rsa.get("descriptions", [])
                arow.path1, arow.path2 = rsa.get("path1", ""), rsa.get("path2", "")
                arow.final_url = ((a.get("ad") or {}).get("final_urls") or [""])[0]
                if arow.final_url:
                    hosts.add(_host(arow.final_url))
                    grow.landing_page_url = grow.landing_page_url or arow.final_url
                counts["ads"] += 1
            for k in [k for k in all_criteria if k["parent"] == g["resource_name"] and k.get("type") == "KEYWORD"]:
                kw = k.get("keyword") or {}
                if k.get("negative"):
                    _upsert_negative(db, account, "AD_GROUP", row.id, grow.id, k["id"], kw)
                    counts["negatives"] += 1
                    continue
                krow = db.scalar(select(Keyword).where(Keyword.ad_group_id == grow.id, Keyword.external_id == k["id"]))
                if not krow:
                    krow = Keyword(ad_group_id=grow.id, external_id=k["id"], text=kw.get("text", ""), match_type=kw.get("match_type", "EXACT"))
                    db.add(krow)
                krow.text, krow.match_type, krow.status = kw.get("text", ""), kw.get("match_type", "EXACT"), k.get("status", "PAUSED")
                krow.external_resource_name = k.get("resource_name", "")  # real API: customers/{cid}/adGroupCriteria/{adGroupId}~{criterionId}
                counts["keywords"] += 1
        for cc in [cc for cc in camp_criteria if cc["parent"] == c["resource_name"] and cc.get("type") == "KEYWORD" and cc.get("negative")]:
            _upsert_negative(db, account, "CAMPAIGN", row.id, None, cc["id"], cc.get("keyword") or {})
            counts["negatives"] += 1
        if row.business_id is None and hosts:
            for h in hosts:
                if h in by_host:
                    row.business_id = by_host[h].id
                    break
        if row.business_id:
            owner = db.get(Business, row.business_id)
            row.client_id = owner.client_id if owner else account.client_id
        if row.landing_page_url == "" and hosts:
            row.landing_page_url = f"https://{sorted(hosts)[0]}/"
    for ca in ads.list_conversion_actions(cid):
        crow = db.scalar(select(ConversionAction).where(ConversionAction.google_ads_account_id == account.id,
                                                        ConversionAction.external_id == ca["id"]))
        if not crow:
            crow = ConversionAction(google_ads_account_id=account.id, external_id=ca["id"], name=ca.get("name", ""), type=ca.get("type", ""))
            db.add(crow)
        crow.name, crow.type = ca.get("name", ""), ca.get("type", "WEBPAGE")
        crow.origin, crow.category = ca.get("origin", "WEBSITE"), ca.get("category", "SUBMIT_LEAD_FORM")
        crow.primary_for_goal = bool(ca.get("primary_for_goal", True))
        crow.counting_type = ca.get("counting_type", "ONE_PER_CLICK")
        crow.call_min_duration_seconds = ca.get("phone_call_duration_seconds")
        crow.status = ca.get("status", "ENABLED")
        if ca.get("business_host") and ca["business_host"] in by_host:
            crow.business_id = by_host[ca["business_host"]].id
        counts["conversion_actions"] += 1
    account.last_synced_at = datetime.now(timezone.utc)
    db.flush()
    return counts


def _upsert_negative(db, account, level, campaign_id, ad_group_id, ext_id, kw):
    row = db.scalar(select(NegativeKeyword).where(NegativeKeyword.google_ads_account_id == account.id,
                                                  NegativeKeyword.external_id == ext_id))
    if not row:
        row = NegativeKeyword(google_ads_account_id=account.id, level=level, campaign_id=campaign_id, ad_group_id=ad_group_id,
                              external_id=ext_id, text=kw.get("text", ""), match_type=kw.get("match_type", "EXACT"))
        db.add(row)
    row.text, row.match_type = kw.get("text", ""), kw.get("match_type", "EXACT")


def account_for_business(db: Session, business: Business) -> GoogleAdsAccount | None:
    if business.google_ads_account_id:
        return db.get(GoogleAdsAccount, business.google_ads_account_id)
    return db.scalar(select(GoogleAdsAccount).where(GoogleAdsAccount.client_id == business.client_id,
                                                    GoogleAdsAccount.is_manager.is_(False)))
