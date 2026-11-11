"""Performance monitoring and optimisation analysis bound to the intelligence record.

Reads only: synced Google Ads data (PerformanceSnapshot, SearchTermRow, Campaign/Ad/Keyword rows) and the intelligence record (targets, budget
recommendation, measurement findings, selected and rejected keywords, landing-page findings). It writes only Recommendation rows (the existing
store), and a recommendation never executes: executable kinds go through the approval pipeline, the rest are advice.

Rules
* No data -> no analysis: a missing window is "unavailable", never zero; nothing is extrapolated from a single day.
* One metric alone is not a diagnosis: every anomaly lists what corroborates it, and a diagnosis is a labelled hypothesis with its evidence.
* Impact numbers are estimates computed from the account's own measured CPC / conversion rate, shown with a low-expected-high range.
* Advice judged by conversion counts is held until conversions are recorded and counted once (the enough-data check says exactly why).
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.util import normalize_term
from app.models import (Ad, AdGroup, Audit, Business, Campaign, ChangeEvent, Client, GoogleAdsAccount, Keyword, NegativeKeyword, PerformanceSnapshot,
                        Recommendation, SearchTermRow)
from app.services import finance, intelligence_service as intel, monitoring, optimization, measurement, settings_service
from app.services.keyword_labels import label_keyword


def _g(db: Session, key: str, default):
    return settings_service.get_value(key, default, db)


def _pct(cur: float | None, base: float | None) -> float | None:
    return None if cur is None or not base else round((cur - base) / base, 3)


# ------------------------------------------------------------------------------------------------ windows over synced data
def window(db: Session, c: Campaign, start: date, end: date, *, level: str = "CAMPAIGN", ext_id: str | None = None) -> dict | None:
    """Totals over [start, end], or None when NO snapshot exists in the window (no data is not zero)."""
    q = select(func.count(), func.sum(PerformanceSnapshot.impressions), func.sum(PerformanceSnapshot.clicks), func.sum(PerformanceSnapshot.cost_micros),
               func.sum(PerformanceSnapshot.conversions), func.avg(PerformanceSnapshot.budget_lost_is), func.avg(PerformanceSnapshot.rank_lost_is),
               func.max(PerformanceSnapshot.fetched_at)).where(
        PerformanceSnapshot.level == level, PerformanceSnapshot.entity_external_id == (ext_id or c.external_id),
        PerformanceSnapshot.google_ads_account_id == c.google_ads_account_id, PerformanceSnapshot.date >= start, PerformanceSnapshot.date <= end)
    n, imp, clk, cost, conv, blis, rlis, fetched = db.execute(q).one()
    if not n:
        return None
    imp, clk, cost, conv = int(imp or 0), int(clk or 0), float(cost or 0) / 1e6, float(conv or 0)
    return {"days": int(n), "impressions": imp, "clicks": clk, "cost": round(cost, 2), "conversions": round(conv, 2),
            "ctr": round(clk / imp, 4) if imp else None, "cpc": round(cost / clk, 2) if clk else None, "cvr": round(conv / clk, 4) if clk else None,
            "cpl": round(cost / conv, 2) if conv else None, "budget_lost_is": round(float(blis), 3) if blis is not None else None,
            "rank_lost_is": round(float(rlis), 3) if rlis is not None else None, "fetched_at": fetched.isoformat() if fetched else None}


def _per_day(w: dict | None, key: str) -> float | None:
    return None if not w or not w["days"] else round(w[key] / w["days"], 3)


# ------------------------------------------------------------------------------------------------ what the intelligence record says
def baselines(db: Session, client: Client, biz: Business) -> dict:
    """Targets and expectations to judge performance against, each with where it came from. Nothing here is invented."""
    rep = intel.report(db, biz, client)
    mods = {m["module"]: m for m in rep["modules"]}
    fin = {f["category"]: f for f in mods["finance"]["findings"]}
    ictx = intel.Ctx(db, biz, client)
    aff = (fin.get("affordability") or {}).get("value") or {}
    budget = (fin.get("budget_recommendation") or {}).get("value") or {}
    neg = _latest_negative_ideas(db, biz)
    return {
        "weekly_target": {"value": client.weekly_conversion_target or None, "source": "client intake" if client.weekly_conversion_target else "not supplied"},
        "recommended_monthly_budget": {"value": budget.get("monthly"), "source": "financial feasibility (estimate)" if budget.get("monthly") else "not calculated"},
        "max_affordable_cpc": {"value": aff.get("max_affordable_cpc"), "source": "client economics" if aff.get("max_affordable_cpc") else "needs target CAC, lead-to-customer and conversion rates"},
        "planning_cpc": {"value": aff.get("estimated_cpc"), "source": "Keyword Planner / history (estimate)" if aff.get("estimated_cpc") else "not available"},
        "measurement": _measurement_baseline(db, biz, ictx.measurement),
        "readiness": rep["decision_label"], "stale_modules": rep["stale_modules"], "research": rep["research"],
        "selected_keywords": sorted({normalize_term(r["keyword"]) for r in ictx.kw_rows if r.get("selected")}),
        "research_negatives": sorted(neg), "excluded_services": sorted(normalize_term(s) for s in _excluded(biz)),
    }


def _measurement_baseline(db: Session, biz: Business, ma) -> dict:
    ok, why = measurement.conversion_data_reliable(db, biz)
    return {"audited": ma is not None, "conversion_data_reliable": ok, "reason": why,
            "issues": [f"{f['inspected']}: {f['actual']}" for f in (ma.findings if ma else []) if f["result"] == "issue"], "source": "measurement audit"}


def _excluded(biz: Business) -> list[str]:
    from app.services import clients_service
    return clients_service.excluded_services(biz)


def _latest_negative_ideas(db: Session, biz: Business) -> set[str]:
    a = db.scalar(select(Audit).where(Audit.business_id == biz.id, Audit.audit_type == "KEYWORD", Audit.status == "COMPLETED").order_by(Audit.created_at.desc()))
    return {normalize_term(x) for x in (a.result or {}).get("negative_ideas", [])} if a else set()


# ------------------------------------------------------------------------------------------------ anomalies (windowed, corroborated)
def anomalies(db: Session, c: Campaign, base: dict) -> dict:
    pct = float(_g(db, "insights.anomaly_pct", 0.35))
    min_clicks = int(_g(db, "insights.min_clicks_window", 30))
    end = clock.today() - timedelta(days=1)
    cur = window(db, c, end - timedelta(days=6), end)
    ref = window(db, c, end - timedelta(days=34), end - timedelta(days=7))
    out = {"campaign": c.name, "campaign_id": c.id, "current_window": f"{end - timedelta(days=6)}..{end}", "baseline_window": f"{end - timedelta(days=34)}..{end - timedelta(days=7)}",
           "current": cur, "baseline": ref, "flags": [], "diagnosis": [], "status": "ok"}
    if cur is None or ref is None or ref["days"] < 14 or cur["days"] < 4:
        out["status"] = "insufficient_history"
        out["note"] = (f"Not enough history to compare: {cur['days'] if cur else 0} recent day(s) and {ref['days'] if ref else 0} baseline day(s) "
                       "(need at least 4 and 14). Nothing is concluded from so little data.")
        return out
    flags = out["flags"]

    def flag(kind, metric, now, was, change, severity, detail):
        flags.append({"kind": kind, "metric": metric, "current": now, "baseline": was, "change": change, "severity": severity, "detail": detail})
    spend_c, spend_b = _per_day(cur, "cost"), _per_day(ref, "cost")
    ch = _pct(spend_c, spend_b)
    if ch is not None and ch >= pct:
        flag("SPEND_SPIKE", "cost per day", spend_c, spend_b, ch, "high" if ch >= 2 * pct else "medium", f"Spend/day ${spend_c:,.2f} vs ${spend_b:,.2f} (+{ch:.0%})")
    elif ch is not None and ch <= -pct:
        flag("SPEND_DROP", "cost per day", spend_c, spend_b, ch, "medium", f"Spend/day ${spend_c:,.2f} vs ${spend_b:,.2f} ({ch:.0%})")
    if cur["clicks"] >= min_clicks:
        ch = _pct(cur["cpc"], ref["cpc"])
        if ch is not None and ch >= pct:
            flag("CPC_INCREASE", "average CPC", cur["cpc"], ref["cpc"], ch, "high" if ch >= 2 * pct else "medium", f"CPC ${cur['cpc']:.2f} vs ${ref['cpc']:.2f} (+{ch:.0%})")
    if cur["impressions"] >= 10 * min_clicks:
        ch = _pct(cur["ctr"], ref["ctr"])
        if ch is not None and ch <= -pct:
            flag("CTR_DROP", "click-through rate", cur["ctr"], ref["ctr"], ch, "medium", f"CTR {cur['ctr']:.1%} vs {ref['ctr']:.1%} ({ch:.0%})")
    conv_c, conv_b = _per_day(cur, "conversions"), _per_day(ref, "conversions")
    ch = _pct(conv_c, conv_b)
    if ref["conversions"] >= 5 and ch is not None and ch <= -pct:
        flag("CONVERSION_DROP", "conversions per day", conv_c, conv_b, ch, "high" if ch <= -0.6 else "medium", f"Conversions/day {conv_c:.2f} vs {conv_b:.2f} ({ch:.0%})")
    if cur["conversions"] > 0 and ref["cpl"]:
        ch = _pct(cur["cpl"], ref["cpl"])
        if ch is not None and ch >= pct:
            flag("CPL_INCREASE", "cost per lead", cur["cpl"], ref["cpl"], ch, "high" if ch >= 2 * pct else "medium", f"Cost per lead ${cur['cpl']:.2f} vs ${ref['cpl']:.2f} (+{ch:.0%})")
    if c.status == "ENABLED":
        last3 = window(db, c, end - timedelta(days=2), end)
        if last3 and last3["impressions"] == 0 and ref["impressions"] > 0:
            flag("NOT_SERVING", "impressions", 0, ref["impressions"], -1.0, "high", "An enabled campaign had no impressions for the last 3 days although it served before")
    if (cur["budget_lost_is"] or 0) > float(_g(db, "monitoring.budget_limited_is", 0.2)):
        flag("BUDGET_LIMITED", "impression share lost to budget", cur["budget_lost_is"], ref["budget_lost_is"], None, "medium",
             f"Losing {cur['budget_lost_is']:.0%} of impressions to budget")
    m_issues = base["measurement"]["issues"]
    if cur["clicks"] >= min_clicks and cur["conversions"] == 0 and ref["conversions"] > 0:
        flag("CONVERSIONS_STOPPED", "conversions", 0, conv_b, -1.0, "high", f"{cur['clicks']} clicks and zero conversions after a period with conversions")
    # --- corroboration and hypotheses: never from one metric alone
    kinds = {f["kind"] for f in flags}
    for f in flags:
        f["corroborated_by"] = sorted(kinds - {f["kind"]})
        f["confidence"] = "medium" if f["corroborated_by"] else "low"
        if not f["corroborated_by"]:
            f["note"] = "Single metric: treat as a signal to watch, not a diagnosis."
    stable_traffic = (_pct(cur["ctr"], ref["ctr"]) or 0) > -0.15 and (_pct(cur["cpc"], ref["cpc"]) or 0) < 0.15
    if kinds & {"CONVERSION_DROP", "CONVERSIONS_STOPPED"}:
        if stable_traffic:
            ev = ["CTR and CPC are stable, so the same kind of traffic arrived", "conversions fell afterwards"]
            hyp = [("POST_CLICK", "Something after the click changed: landing page, form, phone handling or conversion tracking", ev)]
            if m_issues or "CONVERSIONS_STOPPED" in kinds:
                hyp.append(("MEASUREMENT", "Conversions may have stopped being recorded rather than stopped happening",
                            ev + ([f"the measurement audit found: {m_issues[0]}"] if m_issues else ["clicks continued while recorded conversions went to zero"])))
        else:
            hyp = [("TRAFFIC_QUALITY", "The traffic changed: ad relevance, competition or search-term mix",
                    [f"CTR change {_pct(cur['ctr'], ref['ctr'])}", f"CPC change {_pct(cur['cpc'], ref['cpc'])}"])]
        for key, text, ev in hyp:
            out["diagnosis"].append({"key": key, "hypothesis": text, "evidence": ev, "confidence": "medium" if len(ev) >= 2 else "low", "is_hypothesis": True})
    if "CPL_INCREASE" in kinds and "CPC_INCREASE" in kinds and "CONVERSION_DROP" not in kinds:
        out["diagnosis"].append({"key": "AUCTION_COST", "hypothesis": "Clicks became more expensive while conversion volume held: more competition or lower ad rank",
                                 "evidence": ["CPC and cost per lead rose together", "conversions per day held"], "confidence": "medium", "is_hypothesis": True})
    if not flags:
        out["status"] = "no_anomaly"
    else:
        out["status"] = "anomalies"
    return out


# ------------------------------------------------------------------------------------------------ pacing and shortfall
def pacing(db: Session, c: Campaign) -> dict:
    today = clock.today()
    end = today - timedelta(days=1)
    first = today.replace(day=1)
    budget = (c.monthly_budget_micros / 1e6) or (c.daily_budget_micros / 1e6 * 30.4)
    if end < first:
        return {"status": "unavailable", "note": "It is the first day of the month: no days to pace yet."}
    w = window(db, c, first, end)
    if w is None or not budget:
        return {"status": "unavailable", "note": "No spend data this month" if w is None else "No monthly budget is set on this campaign"}
    dim = calendar.monthrange(today.year, today.month)[1]
    elapsed = (end - first).days + 1
    expected = budget * elapsed / dim
    ratio = round(w["cost"] / expected, 3) if expected else None
    over, under = float(_g(db, "insights.pacing_over", 1.15)), float(_g(db, "insights.pacing_under", 0.75))
    state = "over" if ratio and ratio > over else "under" if ratio is not None and ratio < under else "on_track"
    return {"status": state, "month_to_date": w["cost"], "expected_to_date": round(expected, 2), "ratio": ratio, "monthly_budget": round(budget, 2),
            "projected_month_end": round(w["cost"] / elapsed * dim, 2), "days_elapsed": elapsed, "days_in_month": dim, "budget_lost_is": w["budget_lost_is"],
            "note": "Projection assumes the current daily pace continues. It is arithmetic, not a forecast."}


def shortfall(db: Session, c: Campaign, client: Client) -> dict:
    end = clock.today() - timedelta(days=1)
    target = c.weekly_conversion_target or 0
    w = window(db, c, end - timedelta(days=6), end)
    if not target:
        return {"status": "no_target", "note": "No weekly target is set for this campaign."}
    if w is None or w["days"] < 7:
        return {"status": "unavailable", "target": target, "note": f"Only {w['days'] if w else 0} of the last 7 days have data."}
    gap = round(w["conversions"] - target, 1)
    weeks = []
    for i in range(1, 5):
        e = end - timedelta(days=7 * i)
        ww = window(db, c, e - timedelta(days=6), e)
        weeks.append({"week_end": e.isoformat(), "conversions": ww["conversions"] if ww else None})
    out = {"status": "met" if gap >= 0 else "short", "target": target, "actual": w["conversions"], "gap": gap, "previous_weeks": weeks}
    if gap < 0 and w["cpl"]:
        out["cost_to_close_gap_estimate"] = round(-gap * w["cpl"], 2)
        out["estimate_basis"] = "gap x this week's measured cost per lead; assumes the same cost per lead at higher volume, which usually rises"
    return out


# ------------------------------------------------------------------------------------------------ search terms
def search_term_analysis(db: Session, client: Client, biz: Business, base: dict) -> dict:
    min_clicks = int(_g(db, "wasted.min_clicks", 10))
    rows = list(db.scalars(select(SearchTermRow).where(SearchTermRow.client_id == client.id, SearchTermRow.window_days == 30)))
    if not rows:
        return {"status": "unavailable", "note": "No search-term data has been retrieved for this client."}
    camps = {x.external_id: x for x in db.scalars(select(Campaign).where(Campaign.client_id == client.id))}
    services = [s.name for s in biz.services if not s.is_excluded]
    excluded = _excluded(biz)
    live_kw = {normalize_term(k.text) for c in camps.values() for g in db.scalars(select(AdGroup).where(AdGroup.campaign_id == c.id))
               for k in db.scalars(select(Keyword).where(Keyword.ad_group_id == g.id))}
    live_neg = {normalize_term(n.text) for n in db.scalars(select(NegativeKeyword).where(NegativeKeyword.google_ads_account_id.in_({c.google_ads_account_id for c in camps.values()} or {"-"})))}
    research_neg = set(base["research_negatives"])
    irrelevant, wasted, opportunities = [], [], []
    for r in rows:
        n = normalize_term(r.term)
        cost = round(r.cost_micros / 1e6, 2)
        label, _, why = label_keyword(r.term, services, excluded)
        item = {"term": r.term, "campaign": camps[r.campaign_external_id].name if r.campaign_external_id in camps else r.campaign_external_id, "clicks": r.clicks,
                "cost": cost, "conversions": r.conversions, "conversions_90d": r.conversions_90d, "matched_keyword": r.matched_keyword}
        if n in live_neg:
            continue
        if (n in research_neg or label == "IRRELEVANT") and r.conversions_90d == 0:
            item.update(basis="research: already identified as irrelevant" if n in research_neg else f"rule: {why}",
                        risk="Verify before excluding: negatives can block useful traffic." if r.clicks < min_clicks else "")
            irrelevant.append(item)
        elif r.clicks >= min_clicks and r.conversions == 0 and r.conversions_90d == 0:
            item.update(basis=f"observed: {r.clicks} clicks and no conversions in 30 days (and none in 90)", risk="Relevant wording: review the landing page and intent before excluding.")
            wasted.append(item)
        elif r.conversions >= 1 and n not in live_kw:
            item.update(basis=f"converted {r.conversions:g}x in 30 days and is not a keyword yet")
            opportunities.append(item)
    tot_cost = round(sum(r.cost_micros for r in rows) / 1e6, 2)
    key = lambda x: -x["cost"]  # noqa: E731
    return {"status": "ok", "terms": len(rows), "total_cost": tot_cost, "irrelevant_candidates": sorted(irrelevant, key=key)[:25],
            "irrelevant_cost": round(sum(i["cost"] for i in irrelevant), 2), "relevant_but_not_converting": sorted(wasted, key=key)[:25],
            "wasted_cost": round(sum(i["cost"] for i in wasted), 2), "keyword_opportunities": sorted(opportunities, key=lambda x: -x["conversions"])[:25],
            "window_days": 30, "retrieved_at": max((r.fetched_at for r in rows)).isoformat(),
            "note": "Excluding a term is a proposal: it goes through the negative-keyword safety checks and, outside shadow mode, an approval."}


# ------------------------------------------------------------------------------------------------ ads and landing pages
def ad_landing_analysis(db: Session, c: Campaign, biz: Business) -> dict:
    end = clock.today() - timedelta(days=1)
    ratio = float(_g(db, "monitoring.weak_ad_ctr_ratio", 0.6))
    camp = window(db, c, end - timedelta(days=29), end)
    ads_out, pages = [], {}
    for g in db.scalars(select(AdGroup).where(AdGroup.campaign_id == c.id)):
        stats = []
        for a in db.scalars(select(Ad).where(Ad.ad_group_id == g.id)):
            w = window(db, c, end - timedelta(days=29), end, level="AD", ext_id=a.external_id)
            if w is None:
                continue
            stats.append((a, w))
            p = pages.setdefault(a.final_url or g.landing_page_url or "(none)", {"url": a.final_url or g.landing_page_url or "(none)", "clicks": 0, "conversions": 0.0, "cost": 0.0, "ads": 0})
            p["clicks"] += w["clicks"]
            p["conversions"] += w["conversions"]
            p["cost"] += w["cost"]
            p["ads"] += 1
        with_imp = [(a, w) for a, w in stats if w["impressions"]]
        avg = (sum(w["ctr"] or 0 for _, w in with_imp) / len(with_imp)) if with_imp else None
        for a, w in stats:
            ads_out.append({"ad_id": a.id, "ad_group": g.name, "status": a.status, "impressions": w["impressions"], "clicks": w["clicks"], "ctr": w["ctr"],
                            "conversions": w["conversions"], "cvr": w["cvr"], "ad_group_avg_ctr": round(avg, 4) if avg is not None else None,
                            "weak": bool(avg and len(with_imp) >= 2 and w["ctr"] is not None and w["ctr"] < ratio * avg and a.status == "ENABLED")})
    avg_cvr = camp["cvr"] if camp else None
    page_rows = []
    for p in pages.values():
        cvr = round(p["conversions"] / p["clicks"], 4) if p["clicks"] else None
        low = bool(avg_cvr and cvr is not None and p["clicks"] >= 50 and cvr < 0.5 * avg_cvr)
        page_rows.append({**p, "cost": round(p["cost"], 2), "conversions": round(p["conversions"], 1), "cvr": cvr, "campaign_avg_cvr": avg_cvr, "low_converting": low})
    return {"ads": ads_out, "landing_pages": sorted(page_rows, key=lambda x: -x["clicks"]),
            "limits": "Landing-page results are derived from ad-level data grouped by final URL; Google Ads does not report landing-page conversion rate separately here."}


# ------------------------------------------------------------------------------------------------ impact estimates (finance engine, measured inputs)
def _range(expected: float | None, lo: float, hi: float, metric: str, basis: str) -> dict | None:
    if expected is None:
        return None
    return {"metric": metric, "low": round(expected * lo, 2), "expected": round(expected, 2), "high": round(expected * hi, 2), "basis": basis, "is_estimate": True,
            "caveat": "Planning estimate from this account's own recent CPC and conversion rate. Not a promise."}


def impact_budget_raise(w: dict | None, extra_daily: float) -> dict | None:
    if not w or not w["cpc"] or w["cvr"] is None:
        return None
    clicks = finance.clicks_from_budget(extra_daily * 30.4, w["cpc"])
    return _range(finance.conversions(clicks, w["cvr"]), 0.5, 1.2, "extra conversions per month", f"CPC ${w['cpc']:.2f} and conversion rate {w['cvr']:.1%} measured over {w['days']} days")


def impact_savings(cost_30d: float) -> dict:
    return _range(cost_30d, 0.4, 1.0, "spend avoided per month", "30-day cost of the matching search terms; only part may recur") or {}


def impact_page(page: dict, avg_cvr: float | None) -> dict | None:
    if not avg_cvr or page["cvr"] is None:
        return None
    return _range(max(0.0, (avg_cvr - page["cvr"]) * page["clicks"]), 0.25, 1.0, "extra conversions per month", f"gap to the campaign average conversion rate over {page['clicks']} clicks")


# ------------------------------------------------------------------------------------------------ the analysis for a client
def analyse(db: Session, client: Client, biz: Business | None = None) -> dict:
    """Everything the agent, the weekly report and the scheduled job need, in one read-only pass."""
    biz = biz or (client.businesses[0] if client.businesses else None)
    ds = monitoring.data_status(db, client.id)
    out: dict = {"client_id": client.id, "data_status": ds, "available": ds["state"] in ("data_available", "historical"), "campaigns": [], "blockers": [], "warnings": []}
    if biz is None:
        out["blockers"].append({"what": "The client has no business profile", "needs": "Add one"})
        return out
    if not out["available"]:
        needs = {"not_configured": "Configure the Google Ads integration", "no_account": "Link a Google Ads account to this client",
                 "no_data": "Run the Google Ads data sync", "failed": "Fix the Google Ads connection, then sync"}.get(ds["state"], "")
        out["blockers"].append({"what": ds["message"] or "No performance data is available", "needs": needs})
        return out
    if ds["state"] == "historical":
        out["warnings"].append(ds["message"])
    base = baselines(db, client, biz)
    out["baselines"] = base
    out["retrieved_at"] = ds["retrieved_at"]
    camps = [c for c in db.scalars(select(Campaign).where(Campaign.client_id == client.id, Campaign.campaign_type == "SEARCH").order_by(Campaign.name))]
    for c in camps:
        end = clock.today() - timedelta(days=1)
        out["campaigns"].append({"campaign_id": c.id, "name": c.name, "status": c.status, "anomalies": anomalies(db, c, base), "pacing": pacing(db, c),
                                 "shortfall": shortfall(db, c, client), "ads_and_pages": ad_landing_analysis(db, c, biz),
                                 "last_30_days": window(db, c, end - timedelta(days=29), end)})
    out["search_terms"] = search_term_analysis(db, client, biz, base)
    if not base["measurement"]["conversion_data_reliable"]:
        out["warnings"].append(f"Recommendations judged by conversion counts are held: {base['measurement']['reason']}.")
    if base["research"].get("exists") and not base["research"]["up_to_date"]:
        out["warnings"].append("The research that sets these baselines predates the client's latest information.")
    return out


# ------------------------------------------------------------------------------------------------ recommendations (existing store)
def recommend(db: Session, client: Client, analysis: dict) -> list[Recommendation]:
    """Create or refresh recommendations from an analysis. Idempotent (dedupe keys); nothing executes. Executable kinds reuse the
    optimisation categories so they go through ``create_approval_for``; the rest are advice and say so."""
    recs: list[Recommendation] = []
    if not analysis.get("available"):
        return recs
    wk = clock.today().isocalendar()[1]
    for cd in analysis["campaigns"]:
        c = db.get(Campaign, cd["campaign_id"])
        acct = db.get(GoogleAdsAccount, c.google_ads_account_id)
        gate_conv, gate_plain = optimization.enough_data(db, c), optimization.enough_data(db, c, needs_conversions=False)

        def add(category, title, evidence, impact, key, *, conv=True, executable=False, proposal=None):
            r = optimization._rec(db, client_id=client.id, account_id=acct.id, campaign_id=c.id, category=category, size="BIG", title=title,
                                  proposal=proposal or {"kind": "ADVICE"}, evidence={**evidence, "impact_estimate": impact, "requires_approval": True, "executable": executable,
                                                                                   "analysis_source": "performance_insights"},
                                  gate=gate_conv if conv else gate_plain, dedupe_key=key, source="RULES")
            recs.append(r)
        pc, sf, an = cd["pacing"], cd["shortfall"], cd["anomalies"]
        w30 = cd["last_30_days"]
        if pc["status"] == "over":
            add("PACING_OVER", f"{c.name} is spending ahead of its monthly budget ({pc['ratio']:.0%} of the pro-rata amount)", {"pacing": pc}, None,
                f"pacing:{c.id}:{wk}:over", conv=False)
        elif pc["status"] == "under" and (pc.get("budget_lost_is") or 0) < 0.05:
            add("PACING_UNDER", f"{c.name} is under-spending ({pc['ratio']:.0%} of the pro-rata budget) without being budget-limited: demand or targeting is the constraint",
                {"pacing": pc}, None, f"pacing:{c.id}:{wk}:under", conv=False)
        if sf.get("status") == "short" and (sf["actual"] < 0.7 * sf["target"]):
            add("CONVERSION_SHORTFALL", f"{c.name} produced {sf['actual']:g} conversions last week against a target of {sf['target']:g}",
                {"shortfall": sf, "diagnosis": an.get("diagnosis", []), "anomalies": [f["kind"] for f in an.get("flags", [])]}, None, f"shortfall:{c.id}:{wk}")
        for f in an.get("flags", []):
            if f["kind"] == "CONVERSIONS_STOPPED":
                add("MEASUREMENT_CHECK", f"{c.name}: conversions stopped while clicks continue — verify that leads are still arriving and being recorded", {"flag": f, "diagnosis": an["diagnosis"]},
                    None, f"trackchk:{c.id}:{wk}", conv=False)
        mx = analysis["baselines"]["max_affordable_cpc"]["value"]
        if mx and w30 and w30["cpc"] and w30["cpc"] > mx:
            add("BID_CEILING", f"{c.name}: average CPC ${w30['cpc']:.2f} is above the maximum affordable ${mx:.2f} from the client's economics",
                {"actual_cpc": w30["cpc"], "max_affordable_cpc": mx, "source": analysis["baselines"]["max_affordable_cpc"]["source"]}, None, f"bidceil:{c.id}:{wk}")
        for p in cd["ads_and_pages"]["landing_pages"]:
            if p["low_converting"]:
                add("LANDING_PAGE", f"{p['url']} converts at {p['cvr']:.1%} against {p['campaign_avg_cvr']:.1%} for {c.name}",
                    {"page": p, "limits": cd["ads_and_pages"]["limits"]}, impact_page(p, p["campaign_avg_cvr"]), f"lp:{c.id}:{normalize_term(p['url'])}:{wk}")
    st = analysis.get("search_terms") or {}
    if st.get("status") == "ok" and st["irrelevant_candidates"] and st["irrelevant_cost"] > 0:
        c = db.get(Campaign, analysis["campaigns"][0]["campaign_id"]) if analysis["campaigns"] else None
        if c:
            acct = db.get(GoogleAdsAccount, c.google_ads_account_id)
            r = optimization._rec(db, client_id=client.id, account_id=acct.id, campaign_id=c.id, category="NEGATIVE_REVIEW", size="BIG",
                                  title=f"{len(st['irrelevant_candidates'])} irrelevant search term(s) cost ${st['irrelevant_cost']:,.2f} in 30 days",
                                  proposal={"kind": "ADVICE", "terms": [i["term"] for i in st["irrelevant_candidates"][:15]]},
                                  evidence={"terms": st["irrelevant_candidates"][:15], "impact_estimate": impact_savings(st["irrelevant_cost"]), "requires_approval": True,
                                            "executable": False, "note": "Negatives are applied by the safe-negative flow (shadow mode) or a reviewed approval, never from this advice.",
                                            "analysis_source": "performance_insights"},
                                  gate=optimization.enough_data(db, c, needs_conversions=False), dedupe_key=f"negreview:{client.id}:{wk}")
            recs.append(r)
    db.flush()
    return recs


# ------------------------------------------------------------------------------------------------ did past recommendations work?
def outcome_review(db: Session, client: Client) -> list[dict]:
    """For approved/applied recommendations that have been live at least 7 days: 7 days before vs 7 days after. Correlational, and labelled so."""
    out = []
    today = clock.today()
    for r in db.scalars(select(Recommendation).where(Recommendation.client_id == client.id, Recommendation.status.in_(["APPLIED", "PROPOSED"]), Recommendation.campaign_id.is_not(None))):
        if r.status == "PROPOSED" and not r.operation_id:
            continue
        at = (r.updated_at.date() if r.updated_at else None)
        c = db.get(Campaign, r.campaign_id)
        if not at or not c or (today - at).days < 8:
            continue
        before, after = window(db, c, at - timedelta(days=7), at - timedelta(days=1)), window(db, c, at + timedelta(days=1), at + timedelta(days=7))
        if before and after:
            out.append({"recommendation": r.title, "applied_on": at.isoformat(), "campaign": c.name,
                        "before": {k: before[k] for k in ("conversions", "cost", "cpl", "ctr", "cpc")}, "after": {k: after[k] for k in ("conversions", "cost", "cpl", "ctr", "cpc")},
                        "predicted": (r.evidence or {}).get("impact_estimate"),
                        "caveat": "Before/after comparison. Seasonality, other changes and conversion lag also move these numbers: it does not prove the change caused them."})
    return out


def changes_outside_app(db: Session, client: Client, days: int = 14) -> list[dict]:
    ids = [a.id for a in db.scalars(select(GoogleAdsAccount).where(GoogleAdsAccount.client_id == client.id))]
    since = clock.now() - timedelta(days=days)
    return [{"resource": e.resource_name, "change": e.change_type, "user": e.user_email, "at": e.changed_at.isoformat()}
            for e in db.scalars(select(ChangeEvent).where(ChangeEvent.google_ads_account_id.in_(ids or ["-"]), ChangeEvent.made_outside_app.is_(True),
                                                          ChangeEvent.changed_at >= since).order_by(ChangeEvent.changed_at.desc()).limit(20))]


def run_all(db: Session) -> dict:
    """The scheduled pass: keep every client's intelligence record current with newly synced data, analyse, and refresh recommendations.
    Reads and writes only our own tables; it never calls Google Ads (the daily sync does) and never executes anything."""
    out = {}
    for client in db.scalars(select(Client)):
        biz = client.businesses[0] if client.businesses else None
        if biz is None or not db.scalar(select(GoogleAdsAccount.id).where(GoogleAdsAccount.client_id == client.id, GoogleAdsAccount.is_manager.is_(False))):
            continue
        try:
            with db.begin_nested():
                ran = intel.refresh(db, biz, client, None)["ran"]
                an = analyse(db, client, biz)
                recs = recommend(db, client, an)
            out[client.name] = {"available": an["available"], "modules_updated": ran, "recommendations": len(recs),
                                "anomalies": sum(len(c["anomalies"].get("flags", [])) for c in an.get("campaigns", []))}
        except Exception as exc:  # noqa: BLE001 - one client's failure is recorded, the rest continue
            out[client.name] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    return out

