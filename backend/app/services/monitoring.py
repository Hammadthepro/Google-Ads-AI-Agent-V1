"""Daily monitoring (step 10): pulls real results, re-downloads the last 30 days (conversion lag),
records search terms, calls and manual changes. Numbers come only from the adapter."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.adapters import registry
from app.adapters.base import AdapterError
from app.core import clock
from app.core.errors import IntegrationNotConfigured
from app.models import (Business, CallRecord, Campaign, ChangeEvent, Client, GoogleAdsAccount, JobRun, PerformanceSnapshot,
                        SearchTermRow)
from app.services import accounts_service, activity, measurement, settings_service

log = logging.getLogger("gaa.monitoring")
LEVELS = ("CAMPAIGN", "AD_GROUP", "KEYWORD", "AD")


def daily_sync(db: Session, account: GoogleAdsAccount) -> dict:
    ads = registry.get("google_ads")
    job = JobRun(job="monitoring.daily_sync", client_id=account.client_id, status="RUNNING")
    db.add(job)
    db.flush()
    try:
        accounts_service.sync_account(db, account)
        end = clock.today() - timedelta(days=1)
        start = end - timedelta(days=29)
        camp_map = {c.external_id: c.id for c in db.scalars(select(Campaign).where(Campaign.google_ads_account_id == account.id))}
        n = 0
        for level in LEVELS:
            rows = ads.get_metrics(account.customer_id, start, end, level)
            existing = {(s.entity_external_id, s.date): s for s in db.scalars(select(PerformanceSnapshot).where(
                PerformanceSnapshot.google_ads_account_id == account.id, PerformanceSnapshot.level == level,
                PerformanceSnapshot.date >= start, PerformanceSnapshot.source_type == ads.source))}
            for r in rows:
                d = date.fromisoformat(r["date"])
                snap = existing.get((r["entity_id"], d))
                if not snap:
                    snap = PerformanceSnapshot(google_ads_account_id=account.id, client_id=account.client_id, level=level,
                                               entity_external_id=r["entity_id"], date=d, source_type=ads.source)
                    db.add(snap)
                snap.campaign_id = camp_map.get(r["campaign_id"])
                snap.impressions, snap.clicks, snap.cost_micros = r["impressions"], r["clicks"], r["cost_micros"]
                snap.conversions = r["conversions"] or 0  # refreshed: late conversions update older days
                snap.form_conversions = r.get("form_conversions") or 0
                snap.call_conversions = r.get("call_conversions") or 0
                snap.search_impression_share = r.get("search_impression_share")
                snap.budget_lost_is = r.get("search_budget_lost_impression_share")
                snap.rank_lost_is = r.get("search_rank_lost_impression_share")
                snap.fetched_at = clock.now()
                n += 1
        for window in (30, 90):
            terms = ads.get_search_terms(account.customer_id, end - timedelta(days=window - 1), end)
            db.execute(delete(SearchTermRow).where(SearchTermRow.google_ads_account_id == account.id, SearchTermRow.window_days == window))
            db.flush()
            for t in terms:
                db.add(SearchTermRow(google_ads_account_id=account.id, client_id=account.client_id, campaign_external_id=t["campaign_id"],
                                     ad_group_external_id=t["ad_group_id"], term=t["term"], matched_keyword=t.get("matched_keyword") or "",
                                     window_days=window, impressions=t["impressions"], clicks=t["clicks"], cost_micros=t["cost_micros"],
                                     conversions=t["conversions"] or 0,
                                     last_conversion_date=date.fromisoformat(t["last_conversion_date"]) if t.get("last_conversion_date") else None,
                                     source_type=ads.source))
        db.flush()
        # conversions_90d onto the 30-day rows (used by negative safety checks)
        conv90 = {(r.ad_group_external_id, r.term): r.conversions for r in db.scalars(select(SearchTermRow).where(
            SearchTermRow.google_ads_account_id == account.id, SearchTermRow.window_days == 90))}
        for r in db.scalars(select(SearchTermRow).where(SearchTermRow.google_ads_account_id == account.id, SearchTermRow.window_days == 30)):
            r.conversions_90d = conv90.get((r.ad_group_external_id, r.term), r.conversions)
        for c in ads.get_call_records(account.customer_id, start, end):
            if not db.scalar(select(CallRecord.id).where(CallRecord.external_id == c["id"])):
                db.add(CallRecord(google_ads_account_id=account.id, campaign_external_id=c["campaign_id"], external_id=c["id"],
                                  started_at=datetime.fromisoformat(c["start_call_date_time"]),
                                  duration_seconds=c["call_duration_seconds"], status=c["call_status"], source_type=ads.source))
        for e in ads.get_change_events(account.customer_id, (clock.now() - timedelta(days=29)).isoformat()):
            if not db.scalar(select(ChangeEvent.id).where(ChangeEvent.external_id == e["id"])):
                db.add(ChangeEvent(google_ads_account_id=account.id, external_id=e["id"], resource_name=e["change_resource_name"],
                                   change_type=e["resource_change_operation"], user_email=e["user_email"], client_type=e["client_type"],
                                   changed_at=datetime.fromisoformat(e["change_date_time"]),
                                   made_outside_app=e["client_type"] != "GOOGLE_ADS_API", details=e.get("details") or {},
                                   source_type=ads.source))
        anomalies = detect_anomalies(db, account)
        job.status, job.details = "SUCCEEDED", {"snapshots": n, "anomalies": anomalies}
    except AdapterError as exc:
        # safe, user-facing text only; the technical detail was already written to the backend log by the adapter / handler
        log.warning("daily sync for %s failed: %s", account.customer_id, exc.to_dict())
        job.status = {"auth_failure": "AUTH_FAILED", "rate_limited": "RATE_LIMITED"}.get(exc.kind, "FAILED")
        job.error = exc.message
        db.rollback()  # keep previously retrieved snapshots untouched; they stay as historical data with their original fetched_at
        db.add(job)
    job.finished_at = clock.now()
    activity.log(db, "monitoring.daily_sync", entity="google_ads_account", entity_id=account.id, client_id=account.client_id,
                 details={"status": job.status, "error": job.error}, integration_mode=accounts_service.adapter_mode())
    db.flush()
    return {"status": job.status, "error": job.error, **(job.details or {})}


def detect_anomalies(db: Session, account: GoogleAdsAccount) -> list[str]:
    mult = float(settings_service.get_value("monitoring.anomaly_multiple", 2.0, db))
    out = []
    yesterday = clock.today() - timedelta(days=1)
    for c in db.scalars(select(Campaign).where(Campaign.google_ads_account_id == account.id, Campaign.status == "ENABLED")):
        rows = {s.date: s for s in db.scalars(select(PerformanceSnapshot).where(
            PerformanceSnapshot.level == "CAMPAIGN", PerformanceSnapshot.entity_external_id == c.external_id,
            PerformanceSnapshot.google_ads_account_id == account.id, PerformanceSnapshot.date >= yesterday - timedelta(days=14)))}
        prior = [rows[d].cost_micros for d in rows if d < yesterday]
        if yesterday in rows and len(prior) >= 7:
            avg = sum(prior) / len(prior)
            y = rows[yesterday].cost_micros
            if avg and (y > mult * avg or y < avg / mult):
                out.append(f"{c.name}: yesterday spend ${y / 1e6:,.2f} vs 14-day average ${avg / 1e6:,.2f}")
    if out:
        registry.get("notifier").send(recipients=["team"], subject=f"Spend anomaly for {account.descriptive_name}",
                                      body="\n".join(out), client_id=account.client_id, category="anomaly")
    return out


# ------------------------------------------------------------------ read models for the Monitor screen
def campaign_summary(db: Session, client_id: str, days: int = 30) -> list[dict]:
    end = clock.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    camps = list(db.scalars(select(Campaign).where(Campaign.client_id == client_id).order_by(Campaign.name)))
    out = []
    for c in camps:
        q = select(func.sum(PerformanceSnapshot.impressions), func.sum(PerformanceSnapshot.clicks), func.sum(PerformanceSnapshot.cost_micros),
                   func.sum(PerformanceSnapshot.conversions), func.avg(PerformanceSnapshot.search_impression_share),
                   func.avg(PerformanceSnapshot.budget_lost_is), func.avg(PerformanceSnapshot.rank_lost_is),
                   func.max(PerformanceSnapshot.source_type)).where(
            PerformanceSnapshot.level == "CAMPAIGN", PerformanceSnapshot.entity_external_id == c.external_id,
            PerformanceSnapshot.google_ads_account_id == c.google_ads_account_id,
            PerformanceSnapshot.date >= start, PerformanceSnapshot.date <= end)
        imp, clicks, cost, conv, sis, blis, rlis, src = db.execute(q).one()
        sis, blis, rlis = (float(x) if x is not None else None for x in (sis, blis, rlis))
        clicks, cost, conv = int(clicks or 0), float(cost or 0) / 1e6, float(conv or 0)
        out.append({"campaign_id": c.id, "name": c.name, "status": c.status, "type": c.campaign_type, "read_only": c.is_read_only,
                    "business_id": c.business_id, "impressions": int(imp or 0), "clicks": clicks, "cost": round(cost, 2),
                    "conversions": round(conv, 1), "ctr": round(clicks / imp, 4) if imp else None,
                    "cpc": round(cost / clicks, 2) if clicks else None, "cvr": round(conv / clicks, 4) if clicks else None,
                    "cost_per_conversion": round(cost / conv, 2) if conv else None,
                    "search_impression_share": round(sis, 3) if sis is not None else None,
                    "budget_lost_is": round(blis, 3) if blis is not None else None,
                    "rank_lost_is": round(rlis, 3) if rlis is not None else None,
                    "daily_budget": c.daily_budget_micros / 1e6, "weekly_target": c.weekly_conversion_target,
                    "created_by_app": c.created_by_app, "source_type": src or c.source_type, "window_days": days})
    return out


def daily_series(db: Session, campaign: Campaign, days: int = 30) -> list[dict]:
    end = clock.today() - timedelta(days=1)
    rows = db.scalars(select(PerformanceSnapshot).where(
        PerformanceSnapshot.level == "CAMPAIGN", PerformanceSnapshot.entity_external_id == campaign.external_id,
        PerformanceSnapshot.google_ads_account_id == campaign.google_ads_account_id,
        PerformanceSnapshot.date >= end - timedelta(days=days - 1)).order_by(PerformanceSnapshot.date))
    return [{"date": r.date.isoformat(), "clicks": r.clicks, "cost": round(r.cost_micros / 1e6, 2), "conversions": round(r.conversions, 1),
             "source_type": r.source_type} for r in rows]


def wasted_spend(db: Session, client_id: str) -> dict:
    min_clicks = int(settings_service.get_value("wasted.min_clicks", 10, db))
    window = int(settings_service.get_value("wasted.window_days", 30, db))
    rows = list(db.scalars(select(SearchTermRow).where(SearchTermRow.client_id == client_id, SearchTermRow.window_days == 30,
                                                       SearchTermRow.clicks >= min_clicks, SearchTermRow.conversions == 0)
                           .order_by(SearchTermRow.cost_micros.desc())))
    camps = {c.external_id: c.name for c in db.scalars(select(Campaign).where(Campaign.client_id == client_id))}
    return {"window_days": window, "min_clicks": min_clicks,
            "total": round(sum(r.cost_micros for r in rows) / 1e6, 2),
            "terms": [{"term": r.term, "campaign": camps.get(r.campaign_external_id, r.campaign_external_id), "clicks": r.clicks,
                       "cost": round(r.cost_micros / 1e6, 2), "conversions_90d": r.conversions_90d, "source_type": r.source_type}
                      for r in rows[:100]]}


def search_terms(db: Session, client_id: str, limit: int = 200) -> list[dict]:
    camps = {c.external_id: c.name for c in db.scalars(select(Campaign).where(Campaign.client_id == client_id))}
    rows = db.scalars(select(SearchTermRow).where(SearchTermRow.client_id == client_id, SearchTermRow.window_days == 30)
                      .order_by(SearchTermRow.clicks.desc()).limit(limit))
    return [{"term": r.term, "campaign": camps.get(r.campaign_external_id, r.campaign_external_id), "ad_group_id": r.ad_group_external_id,
             "matched_keyword": r.matched_keyword, "clicks": r.clicks, "cost": round(r.cost_micros / 1e6, 2), "conversions": r.conversions,
             "conversions_90d": r.conversions_90d, "source_type": r.source_type} for r in rows]


def change_events(db: Session, client_id: str) -> list[dict]:
    accts = [a.id for a in db.scalars(select(GoogleAdsAccount).where(GoogleAdsAccount.client_id == client_id))]
    scope = ChangeEvent.google_ads_account_id.in_(accts or ["-"])
    recent = list(db.scalars(select(ChangeEvent).where(scope).order_by(ChangeEvent.changed_at.desc()).limit(100)))
    # changes made outside this app matter most (they can conflict with approved work), so they are never pushed out by the app's own writes
    outside = list(db.scalars(select(ChangeEvent).where(scope, ChangeEvent.made_outside_app.is_(True)).order_by(ChangeEvent.changed_at.desc()).limit(100)))
    seen, rows = set(), []
    for r in sorted(recent + outside, key=lambda r: r.changed_at, reverse=True):
        if r.id not in seen:
            seen.add(r.id)
            rows.append(r)
    return [{"resource": r.resource_name, "change": r.change_type, "user": r.user_email, "client_type": r.client_type,
             "changed_at": r.changed_at.isoformat(), "made_outside_app": r.made_outside_app, "source_type": r.source_type} for r in rows]


def calls_summary(db: Session, client_id: str) -> dict:
    accts = [a.id for a in db.scalars(select(GoogleAdsAccount).where(GoogleAdsAccount.client_id == client_id))]
    rows = list(db.scalars(select(CallRecord).where(CallRecord.google_ads_account_id.in_(accts or ["-"]),
                                                    CallRecord.started_at >= clock.now() - timedelta(days=30))))
    min_len = int(settings_service.get_value("measurement.call_min_seconds", 60, db))
    missed = sum(1 for r in rows if r.status == "MISSED")
    short = sum(1 for r in rows if r.status == "ANSWERED" and r.duration_seconds < min_len)
    return {"calls": len(rows), "missed": missed, "short": short, "missed_rate": round(missed / len(rows), 3) if rows else None,
            "min_seconds": min_len}


def audit_measurement_all(db: Session) -> int:
    n = 0
    for b in db.scalars(select(Business)):
        if b.website_url:
            measurement.audit(db, b)
            n += 1
    return n


def run_daily_all(db: Session) -> dict:
    out = {}
    try:
        registry.get("google_ads")
    except IntegrationNotConfigured as exc:
        db.add(JobRun(job="monitoring.daily_sync", status="NOT_CONFIGURED", error=exc.message, finished_at=clock.now()))
        db.flush()
        return {"skipped": exc.message}
    for a in db.scalars(select(GoogleAdsAccount).where(GoogleAdsAccount.client_id.is_not(None), GoogleAdsAccount.is_manager.is_(False))):
        client = db.get(Client, a.client_id)
        out[a.customer_id] = daily_sync(db, a)
        db.commit()
        if client:
            pass
    return out


# ------------------------------------------------------------------ data freshness (what the UI shows next to every number)
def data_status(db: Session, client_id: str) -> dict:
    """Is the data on screen current, historical, missing, or blocked by a missing integration?

    state: not_configured | no_account | no_data | data_available | historical | failed
    `retrieved_at` is always the time the data was ACTUALLY retrieved from Google Ads, never the time of the last attempt.
    """
    try:
        registry.get("google_ads")
    except IntegrationNotConfigured as exc:
        return {"state": "not_configured", "message": exc.message, "retrieved_at": None, "last_attempt": None}
    accounts = list(db.scalars(select(GoogleAdsAccount).where(GoogleAdsAccount.client_id == client_id)))
    if not accounts:
        return {"state": "no_account", "message": "No Google Ads account is linked to this client yet.", "retrieved_at": None, "last_attempt": None}
    ids = [a.id for a in accounts]
    newest = db.scalar(select(func.max(PerformanceSnapshot.fetched_at)).where(PerformanceSnapshot.google_ads_account_id.in_(ids)))
    last = db.scalar(select(JobRun).where(JobRun.job == "monitoring.daily_sync", JobRun.client_id == client_id).order_by(JobRun.created_at.desc()).limit(1))
    attempt = {"at": (last.finished_at or last.started_at).isoformat(), "status": last.status, "message": last.error} if last else None
    if last and last.status != "SUCCEEDED":
        return {"state": "historical" if newest else "failed", "retrieved_at": newest.isoformat() if newest else None, "last_attempt": attempt,
                "message": (f"Showing data retrieved on {newest:%Y-%m-%d %H:%M} UTC. The latest refresh failed: {last.error}" if newest
                            else f"Google Ads data could not be retrieved: {last.error}")}
    if not newest:
        return {"state": "no_data", "message": "Google Ads is connected but no data has been retrieved yet.", "retrieved_at": None, "last_attempt": attempt}
    return {"state": "data_available", "message": "", "retrieved_at": newest.isoformat(), "last_attempt": attempt}
