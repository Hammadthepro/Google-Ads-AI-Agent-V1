"""Monitoring and optimisation intents for the agent. They reuse the agent's planner/executor and read the same intelligence record:
the data is refreshed only when it is old, the record is brought up to date from it, then ``performance_insights`` analyses it.
Reading and analysing are automatic; turning a recommendation into an approval request needs an explicit confirmation."""
from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import select

from app.adapters import registry
from app.core import clock
from app.core.errors import ValidationFailed
from app.models import PerformanceSnapshot, Recommendation, WeeklyReport
from app.services import (accounts_service, agent_service as ag, auth_service, intelligence_service as intel, monitoring, optimization,
                          performance_insights as pi, reporting, settings_service)

WANT_MONITOR = ["measurement", "account_policy", "finance", "strategy"]


# ------------------------------------------------------------------------------------------------ shared stages
def _data_decision(c: ag.Ctx) -> tuple[str, str]:
    ads = registry.optional("google_ads")
    if ads is None:
        return "skip", "The Google Ads integration is not configured, so no performance data can be retrieved"
    acct = accounts_service.account_for_business(c.db, c.business)
    if acct is None:
        return "skip", "No Google Ads account is linked to this client"
    newest = c.db.scalar(select(PerformanceSnapshot.fetched_at).where(PerformanceSnapshot.google_ads_account_id == acct.id).order_by(PerformanceSnapshot.fetched_at.desc()).limit(1))
    hours = int(settings_service.get_value("agent.data_max_age_hours", 24, c.db))
    if newest is None:
        return "run", "No performance data has been retrieved for this account yet"
    age = max(0.0, (clock.now() - newest).total_seconds() / 3600)
    if age > hours:
        return "run", f"The latest data is {age:.0f} hours old (limit {hours})"
    return "reuse", f"Performance data retrieved {age:.1f} hours ago is still current"


def plan_monitor(c: ag.Ctx, *, record: bool = True) -> list[dict]:
    da, dr = _data_decision(c)
    steps = [{"key": "data_refresh", "label": "Read the latest Google Ads performance (read-only)", "action": da, "reason": dr}]
    if record:
        ma, mr, todo = ag.modules_decision(c.db, c.business, c.client, WANT_MONITOR, da == "run", "newly retrieved Google Ads data")
        steps.append({"key": "modules", "label": "Update the intelligence record", "action": ma, "reason": mr, "modules": todo, "wanted": WANT_MONITOR})
    steps.append({"key": "insights", "label": "Analyse against the intelligence record", "action": "run",
                  "reason": "Compares recent performance with its baseline, the targets and the research (reads only)"})
    return steps


@ag.step("data_refresh")
def _data_refresh(c: ag.Ctx) -> dict:
    acct = accounts_service.account_for_business(c.db, c.business)
    res = monitoring.daily_sync(c.db, acct)
    c.db.commit()
    if res.get("status") != "SUCCEEDED":
        raise ag.Blocked(f"The Google Ads data could not be refreshed: {res.get('error') or res.get('status')}. Showing the data already stored, if any.", "A working Google Ads connection")
    return {"summary": f"Retrieved {res.get('snapshots', 0)} data points"}


@ag.step("insights")
def _insights(c: ag.Ctx) -> dict:
    an = pi.analyse(c.db, c.client, c.business)
    recs = pi.recommend(c.db, c.client, an) if c.run.intent in ("recommendations", "performance_check", "diagnose_campaign") and an["available"] else []
    c.db.commit()
    return {"summary": f"{len(an.get('campaigns', []))} campaign(s) analysed" if an["available"] else "No data to analyse", "recs": len(recs)}


def _analysis(c: ag.Ctx) -> dict:
    return pi.analyse(c.db, c.client, c.business)


def _shell(c: ag.Ctx, an: dict, headline: str, wanted: list[str] | None = None) -> dict:
    a = ag.base_answer(c, wanted or WANT_MONITOR, headline)
    a["headline"] = headline
    a["sections"] = []  # monitoring answers lead with data, not with the ten-stage evidence
    # For monitoring the blockers are the analysis' own (no account, no data, ...) plus any stage that could not run; the ten-stage
    # blockers of the preparation workflow do not apply here (a missing account is not a blocker for research, but it is for monitoring).
    a["blockers"] = list(an["blockers"])
    a["warnings"] = list(dict.fromkeys(an["warnings"] + [w for w in a["warnings"] if "No Google Ads account is linked" not in w]))
    a["data_status"] = an["data_status"]
    for s in c.run.plan:
        if s["status"] == "BLOCKED":
            a["blockers"].append({"what": s["result"], "needs": s.get("prerequisite", "")})
    a["decision"] = a.get("decision")
    return a


def _name_filter(c: ag.Ctx, an: dict) -> list[dict]:
    t = c.run.request_text.lower()
    named = [x for x in an.get("campaigns", []) if x["name"].lower() in t]
    return named or an.get("campaigns", [])


def _fmt(v, kind="n"):
    if v is None:
        return "unavailable"
    return f"${v:,.2f}" if kind == "$" else f"{v:.1%}" if kind == "%" else f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def _window_table(cd: dict) -> dict | None:
    an = cd["anomalies"]
    cur, base = an.get("current"), an.get("baseline")
    if not cur or not base:
        return None
    rows = []
    for label, key, kind in (("Spend", "cost", "$"), ("Clicks", "clicks", "n"), ("Average CPC", "cpc", "$"), ("CTR", "ctr", "%"), ("Conversions", "conversions", "n"),
                             ("Conversion rate", "cvr", "%"), ("Cost per lead", "cpl", "$")):
        ch = pi._pct(cur[key], base[key]) if key in ("cpc", "ctr", "cvr", "cpl") else pi._pct(cur[key] / cur["days"], base[key] / base["days"])
        rows.append([label, _fmt(cur[key], kind), _fmt(base[key], kind), "unavailable" if ch is None else f"{ch:+.0%}"])
    return {"type": "table", "title": f"{cd['name']}: last 7 days vs the 28 days before", "columns": ["metric", "last 7 days", "previous 28 days", "change (per day where a total)"],
            "rows": rows, "note": f"Windows {an['current_window']} and {an['baseline_window']}. Ratios are computed from totals, not averaged."}


# ------------------------------------------------------------------------------------------------ diagnose
def plan_diagnose(c: ag.Ctx) -> list[dict]:
    return plan_monitor(c)


@ag.answer("diagnose_campaign")
def ans_diagnose(c: ag.Ctx) -> dict:
    an = _analysis(c)
    camps = _name_filter(c, an)
    flagged = [x for x in camps if x["anomalies"].get("flags") or x["shortfall"].get("status") == "short"]
    a = _shell(c, an, f"Diagnosis for {c.client.name}: {len(flagged)} of {len(camps)} campaign(s) show a problem." if an["available"] else f"I cannot diagnose {c.client.name} yet.")
    if not an["available"]:
        return a
    base = an["baselines"]
    for cd in sorted(camps, key=lambda x: (not x["anomalies"].get("flags"), x["name"])):
        an_ = cd["anomalies"]
        t = _window_table(cd)
        if t:
            a["sections"].append(t)
        items = []
        if an_["status"] == "insufficient_history":
            items.append(an_["note"])
        for f in an_.get("flags", []):
            items.append(f"{f['kind'].replace('_', ' ').title()} [{f['severity']}, confidence {f['confidence']}]: {f['detail']}" + (f" — corroborated by {', '.join(x.lower().replace('_', ' ') for x in f['corroborated_by'])}" if f["corroborated_by"] else f" — {f.get('note', '')}"))
        for d in an_.get("diagnosis", []):
            items.append(f"Likely cause (hypothesis, confidence {d['confidence']}): {d['hypothesis']}. Evidence: {'; '.join(d['evidence'])}")
        sf = cd["shortfall"]
        if sf.get("status") == "short":
            items.append(f"Target: {sf['actual']:g} conversions last week against {sf['target']:g} ({sf['gap']:+g})" + (f"; closing the gap at today's cost per lead would cost about ${sf['cost_to_close_gap_estimate']:,.0f} more (estimate)" if sf.get("cost_to_close_gap_estimate") else ""))
        pc = cd["pacing"]
        if pc.get("status") in ("over", "under"):
            items.append(f"Budget pacing: {pc['status']} — ${pc['month_to_date']:,.2f} spent against ${pc['expected_to_date']:,.2f} expected to date; projected month-end ${pc['projected_month_end']:,.2f}")
        if not items:
            items.append("No anomaly against its own baseline and the target is met.")
        a["sections"].append({"type": "list", "title": cd["name"], "items": items})
    a["sections"].append({"type": "numbers", "title": "What it was judged against (from the intelligence record)", "items": [
        {"label": "Weekly target", "value": f"{base['weekly_target']['value'] or 'not supplied'} ({base['weekly_target']['source']})"},
        {"label": "Recommended monthly budget", "value": f"{base['recommended_monthly_budget']['value'] or 'not calculated'} ({base['recommended_monthly_budget']['source']})"},
        {"label": "Maximum affordable CPC", "value": f"{base['max_affordable_cpc']['value'] or 'unknown'} ({base['max_affordable_cpc']['source']})"},
        {"label": "Conversion data", "value": ("reliable: " if base["measurement"]["conversion_data_reliable"] else "not usable for decisions: ") + base["measurement"]["reason"]}, {"label": "Research", "value": base["research"].get("reason", "")}]})
    a["next_actions"] = [{"label": "What should we improve?", "say": "What should we improve?"}, {"label": "Show wasted spend and search terms", "say": "Show wasted spend and search terms"}]
    return a


# ------------------------------------------------------------------------------------------------ performance
@ag.answer("performance_check")
def ans_performance(c: ag.Ctx) -> dict:
    an = _analysis(c)
    a = _shell(c, an, f"Performance of {c.client.name}." if an["available"] else f"No performance data for {c.client.name} yet.")
    if not an["available"]:
        return a
    rows = []
    for cd in an["campaigns"]:
        w, pc, sf = cd["last_30_days"], cd["pacing"], cd["shortfall"]
        rows.append([cd["name"], _fmt(w["cost"], "$") if w else "unavailable", _fmt(w["conversions"]) if w else "unavailable", _fmt(w["cpl"], "$") if w else "unavailable",
                     pc.get("status", "unavailable").replace("_", " "), (f"{sf['actual']:g}/{sf['target']:g}" if sf.get("target") and sf.get("actual") is not None else sf.get("status", "").replace("_", " ")),
                     ", ".join(f["kind"].replace("_", " ").lower() for f in cd["anomalies"].get("flags", [])) or ("not enough history" if cd["anomalies"]["status"] == "insufficient_history" else "none")])
    a["sections"].append({"type": "table", "title": "Last 30 days", "columns": ["campaign", "spend", "conversions", "cost per lead", "budget pacing", "last week vs target", "anomalies"], "rows": rows,
                          "note": f"Data retrieved {an['retrieved_at']}. 'unavailable' means no data, not zero."})
    a["next_actions"] = [{"label": "Why is a campaign underperforming?", "say": "Why is this campaign underperforming?"}, {"label": "What should we improve?", "say": "What should we improve?"}]
    return a


# ------------------------------------------------------------------------------------------------ search terms
@ag.answer("search_terms")
def ans_terms(c: ag.Ctx) -> dict:
    an = _analysis(c)
    a = _shell(c, an, f"Search terms for {c.client.name}.")
    st = an.get("search_terms") or {}
    if st.get("status") != "ok":
        a["blockers"].append({"what": st.get("note", "No search-term data"), "needs": "A linked account with synced data"})
        return a
    a["headline"] = (f"Search terms for {c.client.name}: {st['terms']} terms, ${st['total_cost']:,.2f} in 30 days. ${st['irrelevant_cost']:,.2f} on irrelevant terms, "
                     f"${st['wasted_cost']:,.2f} on relevant terms that did not convert.")
    cols = ["term", "campaign", "clicks", "cost", "conversions", "basis"]
    for title, key in (("Irrelevant terms (exclusion candidates)", "irrelevant_candidates"), ("Relevant but not converting (review, do not exclude blindly)", "relevant_but_not_converting"),
                       ("Converting terms that are not keywords yet", "keyword_opportunities")):
        rows = [[i["term"], i["campaign"], i["clicks"], f"${i['cost']:,.2f}", i["conversions"], i["basis"]] for i in st[key]]
        a["sections"].append({"type": "table", "title": f"{title} ({len(rows)})", "columns": cols, "rows": rows or [["none", "", "", "", "", ""]], "note": st["note"] if key == "irrelevant_candidates" else ""})
    a["next_actions"] = [{"label": "What should we improve?", "say": "What should we improve?"}]
    return a


# ------------------------------------------------------------------------------------------------ recommendations
def _impact_text(imp) -> str:
    if not imp:
        return "not estimated"
    return f"{imp['metric']}: {imp['low']:g}–{imp['high']:g} (expected {imp['expected']:g}) — estimate"


@ag.answer("recommendations")
def ans_recs(c: ag.Ctx) -> dict:
    an = _analysis(c)
    a = _shell(c, an, f"Recommendations for {c.client.name}.")
    recs = list(c.db.scalars(select(Recommendation).where(Recommendation.client_id == c.client.id, Recommendation.status.in_(["OPEN", "HELD", "PROPOSED", "SHADOW"]))
                             .order_by(Recommendation.created_at.desc()).limit(40)))
    if not recs:
        a["headline"] = f"No recommendations for {c.client.name}" + ("." if an["available"] else ": there is no performance data to base any on.")
        return a
    executable_cats = {"BUDGET", "PROMOTE_TERM", "PAUSE_KEYWORD", "NEGATIVE_KEYWORD"}
    rows, actions = [], []
    for r in recs:
        ev = r.evidence or {}
        ex = ev.get("executable", r.category in executable_cats)
        state = r.status if r.status in ("PROPOSED", "SHADOW") else ("HOLD: " + r.hold_reason if r.decision == "HOLD" else "ready")
        rows.append([r.id[:8], r.category.replace("_", " ").title(), r.title, _impact_text(ev.get("impact_estimate")), state, "approval, then execution" if ex else "advice: a person makes the change"])
        if ex and r.decision == "GO" and r.status == "OPEN":
            actions.append({"label": f"Request approval: {r.title[:50]}", "say": f"Request approval for recommendation {r.id[:8]}"})
    a["headline"] = f"{len(recs)} recommendation(s) for {c.client.name}; {len(actions)} can be sent for approval."
    a["sections"].append({"type": "table", "title": "Recommendations", "columns": ["id", "type", "what", "estimated impact", "state", "how it is applied"], "rows": rows,
                          "note": "Impact figures are estimates from this account's measured CPC and conversion rate. Held items say exactly which data they are waiting for. Nothing is applied without an approval."})
    a["next_actions"] = actions[:5]
    return a


def plan_recs(c: ag.Ctx) -> list[dict]:
    return plan_monitor(c)


# ------------------------------------------------------------------------------------------------ request approval (confirmation first)
def plan_request(c: ag.Ctx) -> list[dict]:
    m = re.search(r"recommendation\s+([0-9a-f]{6,32})", c.run.request_text.lower())
    if not m:
        raise ag.Blocked("Say which recommendation, for example “Request approval for recommendation 1a2b3c4d”.", "A recommendation id")
    hits = [r for r in c.db.scalars(select(Recommendation).where(Recommendation.client_id == c.client.id)) if r.id.startswith(m.group(1))]
    if len(hits) != 1:
        raise ag.Blocked("I could not find exactly one recommendation with that id for this client." if not hits else "That id matches several recommendations: use more characters.", "A valid recommendation id")
    r = hits[0]
    c.run.params = {**c.run.params, "recommendation_id": r.id}
    return [{"key": "confirm_request", "label": "Ask you to confirm before requesting approval", "action": "run", "reason": "Requesting approval creates an approval card for a person to review"}]


@ag.step("confirm_request")
def _confirm_request(c: ag.Ctx) -> dict:
    return {"summary": "Waiting for your confirmation"}


@ag.answer("request_approval")
def ans_request(c: ag.Ctx) -> dict:
    r = c.db.get(Recommendation, c.run.params["recommendation_id"])
    ev = r.evidence or {}
    gated = r.decision != "GO" and r.category != "NEGATIVE_KEYWORD"
    a = {"headline": f"Request approval for: {r.title}", "sections": [{"type": "numbers", "title": "What would be requested", "items": [
        {"label": "Recommendation", "value": r.title}, {"label": "Estimated impact", "value": _impact_text(ev.get("impact_estimate"))},
        {"label": "Enough-data check", "value": r.enough_data.get("decision", "") + (f" — {r.hold_reason}" if r.hold_reason else "")}]}],
         "blockers": ([{"what": f"On hold: {r.hold_reason}", "needs": "The data named in the hold reason"}] if gated else []), "warnings": [], "next_actions": [], "ran": [], "reused": [], "stale_modules": []}
    if not gated and ev.get("executable", r.category in ("BUDGET", "PROMOTE_TERM", "PAUSE_KEYWORD", "NEGATIVE_KEYWORD")):
        a["confirmation"] = {"action": "request_rec_approval", "permission": "approvals.submit", "recommendation_id": r.id,
                             "describes": ("This creates an approval card for a person to review and decide. It does not change Google Ads, and campaign writes are disabled by "
                                           "configuration; even after approval, execution is a separate step guarded by the write protection and the kill switch.")}
    elif not gated:
        a["blockers"].append({"what": "This recommendation is advice only: a person makes the change in Google Ads or through a task.", "needs": ""})
    return a


def _do_request(db, user, run, pend) -> dict:
    auth_service.require(user, "approvals.submit")
    try:
        ap = optimization.create_approval_for(db, user, pend["recommendation_id"])
    except ValidationFailed as exc:
        return {"message": f"No approval was created: {exc.message}"}
    db.flush()
    return {"approval_id": ap.id, "message": f"Approval request {ap.id[:8]}… created. Review it under Approvals. Nothing was changed in Google Ads."}


ag.ACTIONS["request_rec_approval"] = _do_request


# ------------------------------------------------------------------------------------------------ weekly report
def plan_weekly(c: ag.Ctx) -> list[dict]:
    steps = plan_monitor(c)
    end = clock.today() - timedelta(days=1)
    last = c.db.scalar(select(WeeklyReport).where(WeeklyReport.client_id == c.client.id, WeeklyReport.week_end == end).order_by(WeeklyReport.created_at.desc()))
    newest = c.db.scalar(select(PerformanceSnapshot.fetched_at).where(PerformanceSnapshot.client_id == c.client.id).order_by(PerformanceSnapshot.fetched_at.desc()).limit(1))
    if last and (newest is None or last.created_at >= newest) and steps[0]["action"] != "run":
        steps.append({"key": "report", "label": "Weekly report", "action": "reuse", "reason": f"The report for the week ending {end} was written after the latest data"})
    else:
        steps.append({"key": "report", "label": "Write the weekly report", "action": "run", "reason": "No report covers the latest data" if not last else "Newer data exists than the last report"})
    return steps


@ag.step("report")
def _report(c: ag.Ctx) -> dict:
    r = reporting.generate(c.db, c.client)
    c.db.commit()
    return {"summary": f"Report for the week ending {r.week_end}"}


@ag.answer("weekly_report")
def ans_weekly(c: ag.Ctx) -> dict:
    r = c.db.scalar(select(WeeklyReport).where(WeeklyReport.client_id == c.client.id).order_by(WeeklyReport.week_end.desc(), WeeklyReport.created_at.desc()))
    an = _analysis(c)
    a = _shell(c, an, f"Weekly report for {c.client.name}." if r else f"No weekly report for {c.client.name} yet.")
    if not r:
        return a
    t = r.metrics["totals"]
    a["headline"] = f"Weekly report for {c.client.name}, week ending {r.week_end}: {t['conversions']:g} conversions against a target of {t['target']:g}, ${t['cost']:,.2f} spent."
    a["sections"].append({"type": "numbers", "title": f"Week {r.week_start} to {r.week_end}", "items": [
        {"label": "Conversions / target", "value": f"{t['conversions']:g} / {t['target']:g}"}, {"label": "Spend", "value": f"${t['cost']:,.2f}"}, {"label": "Clicks", "value": t["clicks"]},
        {"label": "Wasted spend (30 days)", "value": f"${t['wasted_spend']:,.2f}"}], "note": f"Data source: {r.source_type}. {r.ai_summary[:300]}"})
    a["sections"].append({"type": "table", "title": "Campaigns (conversions per week, oldest to newest)", "columns": ["campaign", "target", "actual", "gap", "trend", "recommended actions"],
                          "rows": [[x["name"], x["target"], x["actual"], x["gap"] if x["gap"] is not None else "n/a", " → ".join(f"{v:g}" for v in x["trend"]), "; ".join(x["recommended_actions"]) or "none agreed by rules and AI"] for x in r.metrics["campaigns"]]})
    ins = r.sections.get("insights") or {}
    if ins.get("error"):
        a["warnings"].append(f"The monitoring section of this report could not be built: {ins['error']}")
    opt = r.sections.get("optimisation") or []
    if opt:
        a["sections"].append({"type": "table", "title": "Optimisation recommendations", "columns": ["type", "what", "estimated impact", "state"],
                              "rows": [[o["category"].replace("_", " ").title(), o["title"], _impact_text(o.get("impact_estimate")), o["status"] if o["decision"] == "GO" else "HOLD: " + (o["hold_reason"] or "")] for o in opt[:12]]})
    out = r.sections.get("outcomes_of_earlier_changes") or []
    if out:
        a["sections"].append({"type": "list", "title": "Did earlier changes help? (before/after, correlational)", "items": [
            f"{o['recommendation']}: conversions {o['before']['conversions']:g} → {o['after']['conversions']:g}, cost per lead {_fmt(o['before']['cpl'], '$')} → {_fmt(o['after']['cpl'], '$')}. {o['caveat']}" for o in out]})
    if r.sections.get("changes_outside_app"):
        a["warnings"].append(f"{len(r.sections['changes_outside_app'])} change(s) were made in Google Ads outside this app in the last 14 days.")
    a["next_actions"] = [{"label": "What should we improve?", "say": "What should we improve?"}]
    return a


# ------------------------------------------------------------------------------------------------ registration
ag.intent("diagnose_campaign", "Diagnose underperformance", [(r"underperform", 4), (r"(why|what happened).*(drop|down|fall|fell|low|expensive|worse|not (work|convert)|stopped)", 4),
                                                            (r"diagnos", 3), (r"what.?s wrong", 3), (r"(stopped|not) converting", 3)], plan_diagnose, example="Why is this campaign underperforming?")
ag.intent("performance_check", "Check performance and anomalies", [(r"how (is|are|did).*(doing|perform|going)", 3), (r"performance", 2), (r"anomal", 3),
                                                                    (r"pacing|overspend|underspend|on track|spending", 3)], lambda c: plan_monitor(c), example="How is performance this month?")
ag.intent("search_terms", "Analyse search terms and wasted spend", [(r"search terms?", 4), (r"wasted", 3), (r"irrelevant (queries|searches|terms)", 3), (r"negatives?\b", 3)],
          lambda c: plan_monitor(c), example="Show wasted spend and search terms")
ag.intent("recommendations", "Recommend improvements", [(r"what should (we|i) (improve|change|do|fix)", 4), (r"optimi[sz]", 3), (r"improve", 2), (r"opportunit", 2), (r"recommendations?", 2)],
          plan_recs, permission="monitor.run", example="What should we improve?")
ag.intent("weekly_report", "Weekly report", [(r"weekly", 3), (r"\breport\b", 3)], plan_weekly, example="Show the weekly report")
ag.intent("request_approval", "Request approval for a recommendation", [(r"request approval for recommendation\s+[0-9a-f]{6,}", 8)], plan_request, permission="approvals.submit",
          confirm=True, example="Request approval for recommendation 1a2b3c4d")
_ = (intel,)
