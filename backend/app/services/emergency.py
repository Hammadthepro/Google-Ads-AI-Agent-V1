"""Emergency brake (step 10b): hourly, deterministic rules only — no AI.

By default (EMERGENCY_BRAKE_WRITE_MODE=simulate) a Google Ads action is only RECORDED as SIMULATED, never executed — a
safety mode, not demo data. Freezing automation is an internal flag and is always applied. Real emergency writes need EMERGENCY_BRAKE_WRITE_MODE=real AND a separate
Super Admin authorization (decision D-09) — both off by default.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import registry
from app.adapters.base import AdapterError
from app.core import clock
from app.core.config import get_settings
from app.models import Ad, AdGroup, Campaign, Client, EmergencyEvent, GoogleAdsAccount, JobRun, PerformanceSnapshot
from app.services import activity, crawler_service, settings_service


def _already(db, rule, key) -> bool:
    start = clock.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return bool(db.scalar(select(EmergencyEvent.id).where(EmergencyEvent.rule == rule, EmergencyEvent.campaign_id == key,
                                                          EmergencyEvent.created_at >= start)))


def _record(db, client: Client, account, campaign_id, rule, severity, observed, threshold, action, message) -> EmergencyEvent:
    s = get_settings()
    google_action = action not in ("FREEZE_AUTOMATION", "FLAG_CLIENT", "ALERT_ONLY")
    mode = "INTERNAL" if not google_action else ("REAL" if (s.EMERGENCY_BRAKE_WRITE_MODE == "real" and
                                                            settings_service.get_value("real_emergency_writes_authorized", False, db))
                                                 else "SIMULATED")
    if action in ("FREEZE_AUTOMATION",) or rule in ("conversion_anomaly", "spend_spike"):
        client.automation_frozen = True
    if action == "FLAG_CLIENT":
        client.flagged, client.flag_reason = True, message
    ev = EmergencyEvent(client_id=client.id, google_ads_account_id=account.id if account else None, campaign_id=campaign_id, rule=rule,
                        severity=severity, observed=observed, threshold=threshold, action=action, action_mode=mode, message=message,
                        source_type=registry.get("google_ads").source)
    db.add(ev)
    registry.get("notifier").send(recipients=[client.owner_contact or "team"], subject=f"Emergency brake: {rule} ({client.name})",
                                  body=f"{message}\nAction: {action} [{mode}]", client_id=client.id, category="emergency")
    activity.log(db, "emergency.triggered", entity="client", entity_id=client.id, client_id=client.id,
                 details={"rule": rule, "action": action, "mode": mode, "observed": observed})
    return ev


def hourly_check(db: Session) -> list[dict]:
    if settings_service.kill_switch_on(db, "GLOBAL", ""):
        return [{"skipped": "global kill switch on — automation already halted"}]
    ads = registry.get("google_ads")
    alert_mult = float(settings_service.get_value("emergency.alert_multiple", 1.5, db))
    freeze_mult = float(settings_service.get_value("emergency.freeze_multiple", 2.0, db))
    drop_pct = float(settings_service.get_value("emergency.conversion_drop_pct", 70, db))
    now = clock.now()
    frac = max((now.hour + now.minute / 60) / 24, 0.01)
    events = []
    for account in db.scalars(select(GoogleAdsAccount).where(GoogleAdsAccount.client_id.is_not(None), GoogleAdsAccount.is_manager.is_(False))):
        client = db.get(Client, account.client_id)
        camps = {c.external_id: c for c in db.scalars(select(Campaign).where(Campaign.google_ads_account_id == account.id))}
        # 1. spend / spikes
        try:
            spend = ads.get_today_spend(account.customer_id)
        except AdapterError as exc:
            spend = []
            if not _already(db, "data_job_failed", account.id):
                events.append(_record(db, client, account, account.id, "data_job_failed", "HIGH", {"error": exc.kind}, {},
                                      "FLAG_CLIENT", f"Hourly spend check failed for {account.descriptive_name}: {exc.kind}"))
        for s in spend:
            c = camps.get(s["campaign_id"])
            budget = s["daily_budget_micros"] / 1e6
            if not c or budget <= 0:
                continue
            cost = s["cost_micros"] / 1e6
            projected = cost / frac if frac >= 0.25 else cost
            obs = {"spend_today": round(cost, 2), "projected_day": round(projected, 2), "daily_budget": budget, "as_of": s.get("as_of")}
            if projected > freeze_mult * budget and not _already(db, "spend_spike", c.id):
                events.append(_record(db, client, account, c.id, "spend_spike", "CRITICAL", obs, {"freeze_multiple": freeze_mult},
                                      "PAUSE_CAMPAIGN", f"{c.name}: projected spend ${projected:,.0f} > {freeze_mult}× daily budget ${budget:,.0f}"))
            elif projected > alert_mult * budget and not _already(db, "unusual_spend", c.id):
                events.append(_record(db, client, account, c.id, "unusual_spend", "HIGH", obs, {"alert_multiple": alert_mult},
                                      "LOWER_BUDGET", f"{c.name}: projected spend ${projected:,.0f} > {alert_mult}× daily budget ${budget:,.0f}"))
        # 2. landing page availability (enabled ads)
        urls: dict[str, list] = {}
        for c in camps.values():
            if c.status != "ENABLED":
                continue
            for g in db.scalars(select(AdGroup).where(AdGroup.campaign_id == c.id, AdGroup.status == "ENABLED")):
                for a in db.scalars(select(Ad).where(Ad.ad_group_id == g.id, Ad.status == "ENABLED")):
                    if a.final_url:
                        urls.setdefault(a.final_url, []).append((c, a))
        for url, items in urls.items():
            res = crawler_service.check_url(url)
            c = items[0][0]
            if not res["ok"] and not _already(db, "landing_page_down", c.id):
                ev = _record(db, client, account, c.id, "landing_page_down", "CRITICAL", {"url": url, "status": res["status"]},
                             {"expected": "2xx"}, "PAUSE_ADS",
                             f"Landing page {url} returned {res['status'] or res['error']} — {len(items)} ad(s) send traffic there")
                ev.observed = {**ev.observed, "ads": [a.external_resource_name for _, a in items]}
                events.append(ev)
        # 3. conversion anomaly (snapshots)
        end = clock.today() - timedelta(days=1)
        for c in camps.values():
            if c.status != "ENABLED":
                continue
            rows = list(db.scalars(select(PerformanceSnapshot).where(
                PerformanceSnapshot.level == "CAMPAIGN", PerformanceSnapshot.entity_external_id == c.external_id,
                PerformanceSnapshot.google_ads_account_id == account.id, PerformanceSnapshot.date >= end - timedelta(days=20))))
            recent = [r for r in rows if r.date > end - timedelta(days=5)]
            prior = [r for r in rows if r.date <= end - timedelta(days=5)]
            if len(prior) >= 10 and recent:
                p_avg = sum(r.conversions for r in prior) / len(prior)
                r_avg = sum(r.conversions for r in recent) / len(recent)
                clicks_ok = sum(r.clicks for r in recent) / len(recent) >= 0.7 * (sum(r.clicks for r in prior) / len(prior))
                if p_avg >= 0.5 and clicks_ok and r_avg < p_avg * (1 - drop_pct / 100) and not _already(db, "conversion_anomaly", c.id):
                    events.append(_record(db, client, account, c.id, "conversion_anomaly", "HIGH",
                                          {"recent_daily_conversions": round(r_avg, 2), "prior_daily_conversions": round(p_avg, 2)},
                                          {"drop_pct": drop_pct}, "FREEZE_AUTOMATION",
                                          f"{c.name}: conversions fell {100 * (1 - r_avg / p_avg):.0f}% while clicks held — conversions may have stopped being recorded"))
        # 5. failed background jobs
        failed = db.scalar(select(JobRun).where(JobRun.client_id == client.id, JobRun.status == "FAILED",
                                                JobRun.created_at >= now - timedelta(hours=24)))
        if failed and not _already(db, "data_job_failed", client.id):
            events.append(_record(db, client, account, client.id, "data_job_failed", "MEDIUM", {"job": failed.job, "error": failed.error}, {},
                                  "FLAG_CLIENT", f"Background job {failed.job} failed: {failed.error[:200]}"))
    db.flush()
    return [{"rule": e.rule, "action": e.action, "mode": e.action_mode, "message": e.message} for e in events]


def serialize(e: EmergencyEvent) -> dict:
    return {"id": e.id, "client_id": e.client_id, "campaign_id": e.campaign_id, "rule": e.rule, "severity": e.severity,
            "observed": e.observed, "threshold": e.threshold, "action": e.action, "action_mode": e.action_mode,
            "message": e.message, "created_at": e.created_at.isoformat(), "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
            "source_type": e.source_type}
