"""Deterministic budget feasibility + lead forecasting. All outputs are ESTIMATES, never guarantees. No AI."""
from __future__ import annotations

import statistics
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core import clock
from app.models import Business, FeasibilityResult
from app.services import fixes_service, settings_service

DAYS_PER_MONTH = 30.4


def calculate(*, monthly_budget: float, weekly_target: float, cpc_range: tuple[float, float, float],
              cvr_range: tuple[float, float, float], review_adjustment: float = 0.0,
              weeks_per_month: float = 4.345) -> dict:
    """cpc_range / cvr_range = (conservative, expected, optimistic). Conservative = HIGH cpc + LOW cvr."""
    daily = monthly_budget / DAYS_PER_MONTH
    weekly = monthly_budget / weeks_per_month
    scenarios = {}
    for name, cpc, cvr in (("conservative", cpc_range[0], cvr_range[0]), ("expected", cpc_range[1], cvr_range[1]),
                           ("optimistic", cpc_range[2], cvr_range[2])):
        adj_cvr = cvr * (1 - review_adjustment)
        clicks_w = weekly / cpc if cpc > 0 else 0
        conv_w = clicks_w * adj_cvr
        scenarios[name] = {"cpc": round(cpc, 2), "conversion_rate": round(adj_cvr, 4),
                           "weekly_clicks": round(clicks_w, 1), "weekly_conversions": round(conv_w, 1),
                           "monthly_conversions": round(conv_w * weeks_per_month, 1),
                           "cost_per_lead": round(weekly / conv_w, 2) if conv_w else None}
    exp = scenarios["expected"]
    allowed_cpl = round(weekly / weekly_target, 2) if weekly_target else None
    required_cvr = round((weekly_target * exp["cpc"]) / weekly, 4) if weekly and weekly_target else None
    needed_budget = round(weekly_target * (exp["cost_per_lead"] or 0) * weeks_per_month, 2) if weekly_target else None
    if not weekly_target:
        status = "NO_TARGET"
    elif exp["weekly_conversions"] >= weekly_target:
        status = "FEASIBLE"
    elif scenarios["optimistic"]["weekly_conversions"] >= weekly_target:
        status = "STRETCH"
    else:
        status = "UNREALISTIC"
    return {
        "daily_budget": round(daily, 2), "weekly_budget": round(weekly, 2), "monthly_budget": round(monthly_budget, 2),
        "weekly_target": weekly_target, "allowed_cost_per_lead": allowed_cpl, "required_conversion_rate": required_cvr,
        "expected_weekly_conversions": exp["weekly_conversions"],
        "realistic_weekly_range": [scenarios["conservative"]["weekly_conversions"], scenarios["optimistic"]["weekly_conversions"]],
        "budget_needed_for_target_monthly": needed_budget,
        "budget_gap_monthly": round(needed_budget - monthly_budget, 2) if needed_budget else None,
        "status": status, "scenarios": scenarios, "review_adjustment": round(review_adjustment, 3),
        "is_estimate": True,
    }


def review_adjustment(client_place: dict | None, competitor_places: list[dict], max_adj: float) -> tuple[float, dict]:
    comps = [p for p in competitor_places if p]
    if not client_place or not comps:
        return 0.0, {}
    med_count = statistics.median(p.get("user_rating_count", 0) for p in comps)
    med_rating = statistics.median(p.get("rating") or 0 for p in comps)
    count, rating = client_place.get("user_rating_count", 0), client_place.get("rating") or 0
    adj = 0.0
    if med_count and count < 0.25 * med_count:
        adj += max_adj * 0.6
    elif med_count and count < 0.6 * med_count:
        adj += max_adj * 0.3
    if rating < med_rating - 0.2:
        adj += max_adj * 0.4
    return min(adj, max_adj), {"client_reviews": count, "client_rating": rating, "competitor_median_reviews": med_count,
                               "competitor_median_rating": med_rating,
                               "reviews_needed_to_reach_median": max(0, int(med_count - count))}


def run(db: Session, business: Business, *, monthly_budget: float, weekly_target: float, keyword_rows: list[dict],
        audits: dict, account_history: dict | None, task_id: str | None = None, campaign_id: str | None = None,
        source_type: str = "MOCK") -> FeasibilityResult:
    """Build inputs from keyword bids + history + audits, compute, record bottlenecks into the fix tracker."""
    bids = [((r.get("low_top_of_page_bid_micros") or 0) + (r.get("high_top_of_page_bid_micros") or 0)) / 2 / 1e6
            for r in keyword_rows if r.get("label") == "SERVICE" and r.get("high_top_of_page_bid_micros")]
    cvr_table = settings_service.get_value("feasibility.cvr_by_business_type", {}, db)
    cvr = tuple(cvr_table.get(business.business_type) or cvr_table.get("default") or [0.03, 0.06, 0.10])
    basis = "keyword_planner_bids"
    if account_history and account_history.get("clicks", 0) >= 200:
        h_cpc = account_history["cost"] / account_history["clicks"]
        h_cvr = (account_history["conversions"] / account_history["clicks"]) if account_history["clicks"] else cvr[1]
        cpc = (h_cpc * 1.2, h_cpc, h_cpc * 0.85)
        if h_cvr > 0:
            cvr = (h_cvr * 0.75, h_cvr, h_cvr * 1.2)
        basis = "account_history_90d"
    elif bids:
        med = statistics.median(bids)
        cpc = (med * 1.3, med, med * 0.8)
    else:
        cpc = (8.0, 6.0, 4.5)
        basis = "default_assumptions"
    gbp = audits.get("gbp") or {}
    adj, review_info = review_adjustment(gbp.get("client"), gbp.get("competitors", []),
                                         float(settings_service.get_value("feasibility.review_adjust_max", 0.2, db)))
    result = calculate(monthly_budget=monthly_budget, weekly_target=weekly_target, cpc_range=cpc, cvr_range=cvr,
                       review_adjustment=adj,
                       weeks_per_month=float(settings_service.get_value("feasibility.weeks_per_month", 4.345, db)))
    bottlenecks = []
    if review_info and review_info.get("reviews_needed_to_reach_median", 0) > 0 and adj > 0:
        need = review_info["reviews_needed_to_reach_median"]
        bottlenecks.append({"type": "REVIEW_GAP", "detail": f"{review_info['client_reviews']} reviews vs competitor median "
                            f"{review_info['competitor_median_reviews']:.0f}; about {need} more reviews needed",
                            "fix": f"Run a review-request campaign to gain ~{need} Google reviews", "priority": "HIGH",
                            "recheck": f"reviews:{int(review_info['competitor_median_reviews'])}"})
    web = audits.get("website") or {}
    speed = (web.get("pagespeed") or {}).get("mobile", {}).get("performance_score")
    if speed is not None and speed < 50:
        bottlenecks.append({"type": "SLOW_MOBILE", "detail": f"Mobile PageSpeed score {speed}/100",
                            "fix": "Improve mobile page speed (compress images, remove unused scripts) to at least 60",
                            "priority": "HIGH", "recheck": "pagespeed_mobile:60"})
    for svc in web.get("services_without_page", []):
        bottlenecks.append({"type": "MISSING_PAGE", "detail": f"No dedicated page for '{svc}'",
                            "fix": f"Create a dedicated landing page for {svc}", "priority": "MEDIUM", "recheck": ""})
    for p in web.get("thin_pages", []):
        bottlenecks.append({"type": "WEAK_CONTENT", "detail": f"Thin service page {p}",
                            "fix": f"Expand {p} with pricing, FAQs, trust signals and a clear call to action",
                            "priority": "MEDIUM", "recheck": ""})
    if account_history and account_history.get("lead_drop"):
        bottlenecks.append({"type": "LEADS_DROPPING", "detail": "Leads dropped while ad metrics stayed stable",
                            "fix": "Check forms, call handling and tracking on the website", "priority": "HIGH", "recheck": ""})
    for bn in bottlenecks:
        fixes_service.upsert(db, business.client_id, business.id, issue=bn["fix"], category=bn["type"].lower(),
                             priority=bn["priority"], source="feasibility", evidence=bn["detail"],
                             dedupe_key=f"bottleneck:{business.id}:{bn['type']}:{bn['detail'][:80]}",
                             auto_recheck_type=bn.get("recheck", ""))
    effort = [bn["fix"] for bn in bottlenecks]
    row = FeasibilityResult(client_id=business.client_id, business_id=business.id, task_id=task_id, campaign_id=campaign_id,
                            inputs={"monthly_budget": monthly_budget, "weekly_target": weekly_target,
                                    "cpc_range": [round(x, 2) for x in cpc], "cvr_range": [round(x, 4) for x in cvr],
                                    "basis": basis, "review_info": review_info, "business_type": business.business_type},
                            scenarios=result["scenarios"], summary={**{k: v for k, v in result.items() if k != "scenarios"},
                                                                    "effort_estimate": effort},
                            status=result["status"], bottlenecks=bottlenecks, source_type=source_type)
    db.add(row)
    db.flush()
    return row


def serialize(r: FeasibilityResult | None) -> dict | None:
    if not r:
        return None
    return {"id": r.id, "status": r.status, "inputs": r.inputs, "scenarios": r.scenarios, "summary": r.summary,
            "bottlenecks": r.bottlenecks, "is_estimate": True, "source_type": r.source_type,
            "created_at": r.created_at.isoformat(), "task_id": r.task_id, "business_id": r.business_id}


def account_history(ads, customer_id: str, campaign_ids: set[str] | None = None) -> dict | None:
    end = clock.today() - timedelta(days=1)
    rows = ads.get_metrics(customer_id, end - timedelta(days=89), end)
    if campaign_ids is not None:
        rows = [r for r in rows if r["campaign_id"] in campaign_ids]
    if not rows:
        return None
    clicks = sum(r["clicks"] for r in rows)
    cost = sum(r["cost_micros"] for r in rows) / 1e6
    conv = sum(r["conversions"] or 0 for r in rows)
    last14 = [r for r in rows if r["date"] >= (end - timedelta(days=13)).isoformat()]
    prev14 = [r for r in rows if (end - timedelta(days=27)).isoformat() <= r["date"] < (end - timedelta(days=13)).isoformat()]
    c_last, c_prev = sum(r["conversions"] or 0 for r in last14), sum(r["conversions"] or 0 for r in prev14)
    k_last, k_prev = sum(r["clicks"] for r in last14), sum(r["clicks"] for r in prev14)
    lead_drop = bool(c_prev >= 4 and c_last < 0.5 * c_prev and k_prev and abs(k_last - k_prev) / k_prev < 0.25)
    return {"clicks": clicks, "cost": round(cost, 2), "conversions": round(conv, 1), "days": 90, "lead_drop": lead_drop}
