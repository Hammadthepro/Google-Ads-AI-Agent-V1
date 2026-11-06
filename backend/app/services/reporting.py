"""Weekly review (step 11). Rolling 4 weeks. Numbers are computed here; AI only writes the narrative.
Recommended actions = causes named by BOTH the deterministic rules and the AI."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters import registry
from app.core import clock
from app.models import (Approval, Business, Campaign, Client, EmergencyEvent, Experiment, FixTrackerItem, PerformanceSnapshot,
                        Recommendation, SourceType, WeeklyReport)
from app.services import ai_service, monitoring, settings_service, tracking_gate

ORDER = ["TRACKING", "BUDGET_LIMITED", "RANK_LIMITED", "LOW_CTR", "LOW_CVR", "CALLS_MISSED", "HIGH_CPC", "OPEN_CLIENT_FIXES"]
ACTIONS = {"TRACKING": "Fix conversion tracking before optimising (see tracking fix list)",
           "BUDGET_LIMITED": "Raise budget or narrow targeting — impression share is lost to budget",
           "RANK_LIMITED": "Improve ad relevance, landing pages or bids — impression share is lost to ad rank",
           "LOW_CTR": "Test new ad angles / replace weak headlines",
           "LOW_CVR": "Improve landing page conversion (offer, form, trust signals) and tighten keywords",
           "CALLS_MISSED": "Answer or route missed calls; review call handling hours",
           "HIGH_CPC": "Review match types and bids; add negatives to cut expensive irrelevant clicks",
           "OPEN_CLIENT_FIXES": "Chase open client fixes (reviews, page speed, missing pages)"}
LABELS = {"TRACKING": "Is tracking working?", "BUDGET_LIMITED": "Is budget limiting traffic?",
          "RANK_LIMITED": "Is ad rank limiting traffic?", "LOW_CTR": "Is click-through rate low?",
          "LOW_CVR": "Is conversion rate low?", "CALLS_MISSED": "Are calls missed or too short?",
          "HIGH_CPC": "Is CPC too high?", "OPEN_CLIENT_FIXES": "Are client fixes still open?"}


def _week_rows(db, c: Campaign, start: date, end: date):
    q = select(func.coalesce(func.sum(PerformanceSnapshot.impressions), 0), func.coalesce(func.sum(PerformanceSnapshot.clicks), 0),
               func.coalesce(func.sum(PerformanceSnapshot.cost_micros), 0), func.coalesce(func.sum(PerformanceSnapshot.conversions), 0),
               func.coalesce(func.sum(PerformanceSnapshot.form_conversions), 0), func.coalesce(func.sum(PerformanceSnapshot.call_conversions), 0),
               func.avg(PerformanceSnapshot.budget_lost_is), func.avg(PerformanceSnapshot.rank_lost_is),
               func.count(func.distinct(PerformanceSnapshot.source_type)), func.max(PerformanceSnapshot.source_type)).where(
        PerformanceSnapshot.level == "CAMPAIGN", PerformanceSnapshot.entity_external_id == c.external_id,
        PerformanceSnapshot.google_ads_account_id == c.google_ads_account_id,
        PerformanceSnapshot.date >= start, PerformanceSnapshot.date <= end)
    imp, clicks, cost, conv, forms, calls, blis, rlis, nsrc, src = db.execute(q).one()
    cost = float(cost)
    blis, rlis = (float(x) if x is not None else None for x in (blis, rlis))
    return {"impressions": int(imp), "clicks": int(clicks), "cost": round(cost / 1e6, 2), "conversions": round(float(conv), 1),
            "forms": round(float(forms), 1), "calls": round(float(calls), 1),
            "budget_lost_is": round(blis, 3) if blis is not None else None, "rank_lost_is": round(rlis, 3) if rlis is not None else None,
            "source_type": "MIXED" if nsrc and nsrc > 1 else (src or "MOCK")}


def rule_causes(m: dict, tracking_state: str, missed_rate: float | None, open_fixes: int, cpc_benchmark: float | None, db) -> list[str]:
    causes = []
    if tracking_state != "GREEN":
        causes.append("TRACKING")
    if (m.get("budget_lost_is") or 0) > float(settings_service.get_value("monitoring.budget_limited_is", 0.2, db)):
        causes.append("BUDGET_LIMITED")
    if (m.get("rank_lost_is") or 0) > float(settings_service.get_value("monitoring.rank_limited_is", 0.3, db)):
        causes.append("RANK_LIMITED")
    if m.get("ctr") is not None and m["ctr"] < 0.045:
        causes.append("LOW_CTR")
    if m.get("cvr") is not None and m["cvr"] < 0.05:
        causes.append("LOW_CVR")
    if missed_rate is not None and missed_rate > 0.2:
        causes.append("CALLS_MISSED")
    if m.get("cpc") and cpc_benchmark and m["cpc"] > 1.25 * cpc_benchmark:
        causes.append("HIGH_CPC")
    if open_fixes:
        causes.append("OPEN_CLIENT_FIXES")
    return causes


def generate(db: Session, client: Client, week_end: date | None = None) -> WeeklyReport:
    week_end = week_end or (clock.today() - timedelta(days=1))
    week_start = week_end - timedelta(days=6)
    camps = list(db.scalars(select(Campaign).where(Campaign.client_id == client.id, Campaign.campaign_type == "SEARCH")))
    calls = monitoring.calls_summary(db, client.id)
    waste = monitoring.wasted_spend(db, client.id)
    fixes = list(db.scalars(select(FixTrackerItem).where(FixTrackerItem.client_id == client.id, FixTrackerItem.status != "RESOLVED")))
    campaigns, sources, totals = [], [], {"target": 0.0, "conversions": 0.0, "cost": 0.0, "clicks": 0, "forms": 0.0, "calls": 0.0}
    by_business: dict[str, dict] = {}
    recommended_all, warnings = [], []
    for c in camps:
        weeks = []
        for i in range(4):
            e = week_end - timedelta(days=7 * i)
            weeks.append({"week_end": e.isoformat(), **_week_rows(db, c, e - timedelta(days=6), e)})
        cur = weeks[0]
        sources.append(cur["source_type"])
        m = dict(cur)
        m["ctr"] = round(cur["clicks"] / cur["impressions"], 4) if cur["impressions"] else None
        m["cvr"] = round(cur["conversions"] / cur["clicks"], 4) if cur["clicks"] else None
        m["cpc"] = round(cur["cost"] / cur["clicks"], 2) if cur["clicks"] else None
        m["cpl"] = round(cur["cost"] / cur["conversions"], 2) if cur["conversions"] else None
        b = db.get(Business, c.business_id) if c.business_id else None
        state = tracking_gate.effective_state(db, b.id) if b else "RED"
        gate = tracking_gate.latest(db, b.id) if b else None
        if gate and gate.override_active:
            warnings.append(f"{b.name}: tracking override active — conversion-based automation disabled")
        elif state != "GREEN" and b:
            warnings.append(f"{b.name}: tracking {state}")
        b_fixes = [f for f in fixes if b and f.business_id == b.id]
        prior = [w for w in weeks[1:] if w["clicks"]]
        bench = (sum(w["cost"] for w in prior) / sum(w["clicks"] for w in prior)) if prior else None
        rules = rule_causes(m, state, calls.get("missed_rate"), len(b_fixes), bench, db)
        try:
            ai, _ = ai_service.generate(db, "DIAGNOSE", {"metrics": {**m, "missed_call_rate": calls.get("missed_rate"),
                                                                     "cpc_benchmark": bench, "budget_lost_is": m["budget_lost_is"],
                                                                     "rank_lost_is": m["rank_lost_is"]},
                                                         "tracking_state": state, "open_fixes": len(b_fixes)})
            ai_causes = list(ai.causes)
        except ai_service.AIOutputInvalid:
            ai_causes = []
        agreed = [k for k in ORDER if k in rules and k in ai_causes]
        target = c.weekly_conversion_target or 0
        entry = {"campaign_id": c.id, "name": c.name, "status": c.status, "business": b.name if b else None, "target": target,
                 "actual": cur["conversions"], "gap": round(cur["conversions"] - target, 1) if target else None,
                 "trend": [w["conversions"] for w in reversed(weeks)], "weeks": list(reversed(weeks)), "metrics": m,
                 "forms": cur["forms"], "calls": cur["calls"],
                 "diagnosis": [{"step": i + 1, "key": k, "question": LABELS[k], "rules": k in rules, "ai": k in ai_causes,
                                "recommended": k in agreed} for i, k in enumerate(ORDER)],
                 "recommended_actions": [ACTIONS[k] for k in agreed], "tracking_state": state}
        campaigns.append(entry)
        recommended_all += [f"{c.name}: {ACTIONS[k]}" for k in agreed]
        totals["target"] += target
        totals["conversions"] += cur["conversions"]
        totals["cost"] += cur["cost"]
        totals["clicks"] += cur["clicks"]
        totals["forms"] += cur["forms"]
        totals["calls"] += cur["calls"]
        bb = by_business.setdefault(b.name if b else "Unassigned", {"target": 0.0, "conversions": 0.0, "cost": 0.0})
        bb["target"] += target
        bb["conversions"] += cur["conversions"]
        bb["cost"] += cur["cost"]
    totals = {k: round(v, 2) for k, v in totals.items()}
    totals["gap"] = round(totals["conversions"] - totals["target"], 1)
    totals["wasted_spend"] = waste["total"]
    ws = clock.now() - timedelta(days=7)
    approvals_week = list(db.scalars(select(Approval).where(Approval.client_id == client.id, Approval.decided_at >= ws)))
    brake = list(db.scalars(select(EmergencyEvent).where(EmergencyEvent.client_id == client.id, EmergencyEvent.created_at >= ws)))
    recs = list(db.scalars(select(Recommendation).where(Recommendation.client_id == client.id).order_by(Recommendation.created_at.desc()).limit(60)))
    exps = list(db.scalars(select(Experiment).where(Experiment.client_id == client.id, Experiment.status.in_(["RUNNING", "AWAITING_WINNER", "PROPOSED"]))))
    if client.automation_frozen:
        warnings.append("Automation frozen by the emergency brake")
    sections = {
        "tracking_warnings": warnings,
        "open_fixes": [{"issue": f.issue, "priority": f.priority, "owner": f.owner, "since": f.created_at.date().isoformat()} for f in fixes],
        "emergency_actions": [{"rule": e.rule, "action": e.action, "mode": e.action_mode, "message": e.message, "at": e.created_at.isoformat()} for e in brake],
        "recommendations": {"small_changes_made": [r.title for r in recs if r.size == "SMALL" and r.status == "APPLIED"],
                            "proposed_shadow": [r.title for r in recs if r.status == "SHADOW"][:20],
                            "big_changes_proposed": [{"id": r.id, "title": r.title, "approval_id": r.approval_id, "status": r.status} for r in recs if r.size == "BIG" and r.status in ("OPEN", "PROPOSED")],
                            "held": [{"title": r.title, "reason": r.hold_reason} for r in recs if r.status == "HELD"][:20]},
        "approved_changes": [{"title": a.title, "at": a.decided_at.isoformat()} for a in approvals_week if a.status in ("APPROVED", "EXECUTED")],
        "rejected_changes": [{"title": a.title, "reason": a.decision_reason} for a in approvals_week if a.status in ("REJECTED", "CHANGES_REQUESTED")],
        "experiments": [{"name": e.name, "status": e.status, "variable": e.variable} for e in exps],
        "business_breakdown": by_business, "calls": calls,
        "wasted_spend": {"total": waste["total"], "window_days": waste["window_days"], "top_terms": waste["terms"][:10]},
    }
    source = SourceType.combine(*sources) if sources else registry.get("google_ads").source
    try:
        summ, run = ai_service.generate(db, "REPORT_SUMMARY", {"client_name": client.name, "totals": totals,
                                                               "recommended_actions": recommended_all, "warnings": warnings})
        ai_text, run_id = summ.summary + ("\n" + "\n".join(f"• {h}" for h in summ.highlights) if summ.highlights else ""), run.id
    except ai_service.AIOutputInvalid:
        ai_text, run_id = "(AI summary unavailable — figures above are complete.)", None
    report = WeeklyReport(client_id=client.id, week_start=week_start, week_end=week_end,
                          metrics={"totals": totals, "campaigns": campaigns}, diagnosis={"order": ORDER},
                          sections=sections, ai_summary=ai_text, ai_run_id=run_id, source_type=source)
    db.add(report)
    db.flush()
    return report


def serialize(r: WeeklyReport) -> dict:
    return {"id": r.id, "client_id": r.client_id, "week_start": r.week_start.isoformat(), "week_end": r.week_end.isoformat(),
            "metrics": r.metrics, "sections": r.sections, "ai_summary": r.ai_summary, "source_type": r.source_type,
            "created_at": r.created_at.isoformat(),
            "note": "Numbers are calculated by the application; the AI only writes the summary text."}
