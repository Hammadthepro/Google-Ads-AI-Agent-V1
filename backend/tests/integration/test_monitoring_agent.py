"""Monitoring and optimisation bound to the intelligence record: windowed anomalies with corroboration, pacing, target shortfall, search terms,
ads/landing pages, recommendations with impact estimates and approval requirements, outcomes, the weekly report, and the agent that drives them."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core import clock
from app.core.config import get_settings
from app.db.session import session_scope
from app.models import Approval, Campaign, Client, JobRun, Operation, PerformanceSnapshot, Recommendation, WeeklyReport
from app.services import optimization
from app.services import performance_insights as pi
from tests.conftest import ADMIN, Api
from tests.integration.test_intake_v2 import _fixture_client


def _cid(name: str) -> str:
    with session_scope() as db:
        return db.scalar(select(Client.id).where(Client.name == name))


def _count(model, **where) -> int:
    with session_scope() as db:
        q = select(func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return db.scalar(q)


def _agent(api: Api, cid: str, text: str, **kw) -> dict:
    conv = api.post("/agent/conversations", {"client_id": cid}).json()["id"]
    t = api.post(f"/agent/conversations/{conv}/messages?sync=true", {"text": text, **kw}).json()
    last = t["messages"][-1]
    if last["kind"] == "clarification" and last["data"]["type"] == "business":  # clients with several businesses are asked once, then it is remembered
        t = api.post(f"/agent/conversations/{conv}/messages?sync=true", {"text": "", "choice": {"value": last["data"]["options"][0]["value"]}}).json()
    return list(t["runs"].values())[-1], t, conv


@pytest.fixture(scope="module")
def api():
    return Api(ADMIN)


@pytest.fixture
def fresh_data():
    """Some tests make the synced data look old; put it back afterwards so tests do not affect each other."""
    yield
    with session_scope() as db:
        db.query(PerformanceSnapshot).update({"fetched_at": clock.now()})


# ------------------------------------------------------------------------------------------------ analysis
def test_anomalies_are_windowed_corroborated_and_hypotheses_not_conclusions():
    with session_scope() as db:
        an = pi.analyse(db, db.get(Client, _cid("Brightsmile Dental Group")))
    assert an["available"] and an["data_status"]["state"] == "data_available"
    drop = next(c for c in an["campaigns"] if c["anomalies"]["status"] == "anomalies")["anomalies"]
    kinds = {f["kind"]: f for f in drop["flags"]}
    assert {"CONVERSION_DROP", "CONVERSIONS_STOPPED"} <= set(kinds)
    assert kinds["CONVERSION_DROP"]["corroborated_by"] == ["CONVERSIONS_STOPPED"] and kinds["CONVERSION_DROP"]["confidence"] == "medium"
    assert drop["current_window"] != drop["baseline_window"] and drop["baseline"]["days"] >= 14
    dx = {d["key"]: d for d in drop["diagnosis"]}
    assert "MEASUREMENT" in dx and dx["MEASUREMENT"]["is_hypothesis"] and len(dx["MEASUREMENT"]["evidence"]) >= 2
    m = an["baselines"]["measurement"]
    assert m["audited"] and m["conversion_data_reliable"] is False and m["issues"] and "status" not in m and "gate" not in m
    assert any("held:" in w and m["reason"] in w for w in an["warnings"])


def _scratch_series(db, c, days, *, conv=lambda i: 5, lost=lambda i: 0.0, clicks=lambda i: 100, cost=lambda i: 300.0):
    """Replace one campaign's daily history inside an uncommitted transaction (rolled back by the caller): controlled data, seeded data untouched."""
    from datetime import timedelta as td
    q = db.query(PerformanceSnapshot).filter(PerformanceSnapshot.level == "CAMPAIGN", PerformanceSnapshot.entity_external_id == c.external_id,
                                             PerformanceSnapshot.google_ads_account_id == c.google_ads_account_id)
    q.delete()
    db.flush()
    end = clock.today() - td(days=1)
    for i in range(days):
        db.add(PerformanceSnapshot(google_ads_account_id=c.google_ads_account_id, client_id=c.client_id, campaign_id=c.id, level="CAMPAIGN", entity_external_id=c.external_id,
                                   date=end - td(days=i), impressions=2000, clicks=clicks(i), cost_micros=int(cost(i) * 1e6), conversions=conv(i), budget_lost_is=lost(i),
                                   rank_lost_is=0.05, source_type="MOCK"))
    db.flush()


def test_a_single_metric_is_a_signal_and_too_little_history_concludes_nothing():
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        c = db.scalar(select(Campaign).where(Campaign.name.like("%Drain Cleaning%")))
        base = {"measurement": {"issues": []}}
        _scratch_series(db, c, 35, lost=lambda i: 0.4 if i < 7 else 0.0)  # only the budget-lost share moves; spend, CPC, CTR and conversions are flat
        an = pi.anomalies(db, c, base)
        assert [f["kind"] for f in an["flags"]] == ["BUDGET_LIMITED"] and an["status"] == "anomalies"
        f = an["flags"][0]
        assert f["corroborated_by"] == [] and f["confidence"] == "low" and "Single metric" in f["note"] and an["diagnosis"] == []
        _scratch_series(db, c, 35, conv=lambda i: 0 if i < 7 else 5)  # conversions vanish while clicks, CPC and CTR hold: post-click, not traffic
        an = pi.anomalies(db, c, base)
        kinds = {f["kind"] for f in an["flags"]}
        assert {"CONVERSION_DROP", "CONVERSIONS_STOPPED"} <= kinds and "CPC_INCREASE" not in kinds and "CTR_DROP" not in kinds
        assert {d["key"] for d in an["diagnosis"]} == {"POST_CLICK", "MEASUREMENT"} and all(d["is_hypothesis"] for d in an["diagnosis"])
        _scratch_series(db, c, 35, cost=lambda i: 700.0 if i < 7 else 300.0)  # spend more than doubles, nothing else changes
        an = pi.anomalies(db, c, base)
        assert "SPEND_SPIKE" in {f["kind"] for f in an["flags"]} and an["flags"][0]["change"] > 1
        _scratch_series(db, c, 5)  # five days of history
        an = pi.anomalies(db, c, base)
        assert an["status"] == "insufficient_history" and an["flags"] == [] and "Nothing is concluded" in an["note"]
    finally:
        db.rollback()
        db.close()


def test_pacing_and_shortfall_are_arithmetic_on_the_stored_data():
    with session_scope() as db:
        c = db.scalar(select(Campaign).where(Campaign.name.like("%Drain Cleaning%")))
        cl = db.get(Client, c.client_id)
        pc, sf = pi.pacing(db, c), pi.shortfall(db, c, cl)
        today = clock.today()
        end = today - timedelta(days=1)
        if end >= today.replace(day=1):
            spent = float(db.scalar(select(func.sum(PerformanceSnapshot.cost_micros)).where(
                PerformanceSnapshot.level == "CAMPAIGN", PerformanceSnapshot.entity_external_id == c.external_id,
                PerformanceSnapshot.google_ads_account_id == c.google_ads_account_id, PerformanceSnapshot.date >= today.replace(day=1), PerformanceSnapshot.date <= end)) or 0) / 1e6
            assert pc["month_to_date"] == round(spent, 2) and pc["status"] in ("on_track", "over", "under")
            assert pc["projected_month_end"] == pytest.approx(spent / pc["days_elapsed"] * pc["days_in_month"], abs=0.02)
        conv = float(db.scalar(select(func.sum(PerformanceSnapshot.conversions)).where(
            PerformanceSnapshot.level == "CAMPAIGN", PerformanceSnapshot.entity_external_id == c.external_id,
            PerformanceSnapshot.google_ads_account_id == c.google_ads_account_id, PerformanceSnapshot.date >= end - timedelta(days=6), PerformanceSnapshot.date <= end)))
    assert sf["actual"] == round(conv, 2) and sf["gap"] == round(sf["actual"] - sf["target"], 1) and sf["status"] in ("met", "short")
    if sf["status"] == "short" and sf.get("cost_to_close_gap_estimate"):
        assert "assumes the same cost per lead" in sf["estimate_basis"]


def test_no_account_or_no_data_means_blockers_and_nothing_invented(api):
    cid, _ = _fixture_client(api, "Monitoring No Account Co")
    with session_scope() as db:
        an = pi.analyse(db, db.get(Client, cid))
    assert an["available"] is False and an["campaigns"] == [] and an["blockers"][0]["needs"] == "Link a Google Ads account to this client"
    assert pi.recommend(None, None, an) == []  # nothing to recommend from no data
    run, t, _ = _agent(api, cid, "Why is this campaign underperforming?")
    assert run["plan"][0]["key"] == "data_refresh" and run["plan"][0]["action"] == "skip" and "No Google Ads account is linked" in run["plan"][0]["reason"]
    assert run["status"] == "COMPLETED_WITH_BLOCKERS" and run["result"]["blockers"][0]["needs"] == "Link a Google Ads account to this client"
    assert "cannot diagnose" in run["result"]["headline"] and run["result"]["sections"] == []


def test_search_terms_are_classified_with_evidence_and_converting_terms_are_protected():
    with session_scope() as db:
        an = pi.analyse(db, db.get(Client, _cid("Brightsmile Dental Group")))
    st = an["search_terms"]
    assert st["status"] == "ok" and st["irrelevant_candidates"] and st["window_days"] == 30
    for i in st["irrelevant_candidates"]:
        assert i["conversions_90d"] == 0 and i["basis"] and i["cost"] >= 0  # a term that ever converted is never offered for exclusion
    assert st["irrelevant_cost"] == round(sum(i["cost"] for i in st["irrelevant_candidates"]), 2) or len(st["irrelevant_candidates"]) == 25


def test_recommendations_carry_impact_estimates_gates_and_are_idempotent_advice_cannot_execute():
    cid = _cid("Brightsmile Dental Group")
    with session_scope() as db:
        client = db.get(Client, cid)
        an = pi.analyse(db, client)
        first = pi.recommend(db, client, an)
        n = db.scalar(select(func.count()).select_from(Recommendation).where(Recommendation.client_id == cid))
        second = pi.recommend(db, client, an)
        assert db.scalar(select(func.count()).select_from(Recommendation).where(Recommendation.client_id == cid)) == n  # same week: refreshed, not duplicated
        assert {r.id for r in first} == {r.id for r in second}
        cats = {r.category: r for r in first}
        assert "MEASUREMENT_CHECK" in cats and "NEGATIVE_REVIEW" in cats
        neg = cats["NEGATIVE_REVIEW"]
        imp = neg.evidence["impact_estimate"]
        assert imp["is_estimate"] and imp["low"] < imp["expected"] <= imp["high"] and "avoided" in imp["metric"] and "Not a promise" in imp["caveat"]
        assert neg.evidence["requires_approval"] is True and neg.evidence["executable"] is False
        conv_gated = [r for r in first if r.category in ("CONVERSION_SHORTFALL", "BID_CEILING", "LANDING_PAGE")]
        assert all(r.decision == "HOLD" and "Conversions are recorded and counted once" in r.hold_reason for r in conv_gated)  # held for a named data reason
        from app.core.errors import ValidationFailed
        with pytest.raises(ValidationFailed, match="advice only|HOLD"):
            optimization.create_approval_for(db, db.scalar(select(__import__("app.models", fromlist=["User"]).User)), neg.id)


# ------------------------------------------------------------------------------------------------ the record uses the synced data
def test_the_intelligence_record_reads_the_synced_account_data(api):
    cid = _cid("Brightsmile Dental Group")
    out = api.post(f"/clients/{cid}/intelligence/refresh", {"modules": ["account_policy", "keywords"]}).json()
    mods = {m["module"]: m for m in out["report"]["modules"]}
    acct = mods["account_policy"]
    assert acct["status"] != "WAITING_FOR_INTEGRATION"
    hp = next(f for f in acct["findings"] if f["category"] == "historical_performance" and f["source_type"] == "google_ads_api")
    assert hp["origin"] == "google_ads_api" and hp["verification"] == "verified" and "synced Google Ads data" in hp["source_reference"] and hp["value"]["d30"]["days_with_data"] > 0
    assert acct["coverage"]["search_terms"] == "evaluated" and acct["coverage"]["change_history"] == "evaluated" and acct["coverage"]["policy_status"] == "not_assessed"
    assert mods["keywords"]["coverage"]["src_search_terms"] == "evaluated"


# ------------------------------------------------------------------------------------------------ agent: monitoring intents
def test_diagnosis_request_reads_fresh_data_without_resyncing_and_shows_hypotheses_with_evidence(api):
    cid = _cid("Brightsmile Dental Group")
    syncs = _count(JobRun, job="monitoring.daily_sync")
    run, _, _ = _agent(api, cid, "Why is this campaign underperforming?")
    assert run["intent"] == "diagnose_campaign" and run["interpreted_by"] == "rules"
    assert run["plan"][0]["action"] == "reuse" and "still current" in run["plan"][0]["reason"]
    assert _count(JobRun, job="monitoring.daily_sync") == syncs  # fresh data is not downloaded again
    res = run["result"]
    assert "show a problem" in res["headline"]
    tables = [s for s in res["sections"] if s["type"] == "table"]
    assert tables and tables[0]["columns"][1] == "last 7 days"
    text = " ".join(i for s in res["sections"] if s["type"] == "list" for i in s["items"])
    assert "hypothesis" in text and "Evidence:" in text and "corroborated by" in text
    judged = next(s for s in res["sections"] if s["title"].startswith("What it was judged against"))
    assert any(i["label"] == "Conversion data" and i["value"].startswith("not usable for decisions") for i in judged["items"])
    assert any("judged by conversion counts are held" in w for w in res["warnings"])


def test_old_data_is_refreshed_read_only_and_the_record_follows(api, fresh_data):
    cid = _cid("Northside Home Services")
    before_v = {m["module"]: m["version"] for m in api.get(f"/clients/{cid}/intelligence").json()["modules"]}
    with session_scope() as db:
        db.query(PerformanceSnapshot).update({"fetched_at": clock.now() - timedelta(hours=48)})
    syncs, ops = _count(JobRun, job="monitoring.daily_sync"), _count(Operation)
    run, _, _ = _agent(api, cid, "How is performance this month?")
    assert run["intent"] == "performance_check" and run["plan"][0]["action"] == "run" and "48 hours old" in run["plan"][0]["reason"]
    assert _count(JobRun, job="monitoring.daily_sync") == syncs + 1 and _count(Operation) == ops  # a read, never a write
    assert run["plan"][1]["action"] == "run" and "newly retrieved Google Ads data" in run["plan"][1]["reason"]
    after_v = {m["module"]: m["version"] for m in api.get(f"/clients/{cid}/intelligence").json()["modules"]}
    assert after_v["account_policy"] > before_v["account_policy"]
    tbl = run["result"]["sections"][0]
    assert tbl["type"] == "table" and "unavailable" in tbl["note"]


def test_search_terms_and_recommendation_intents(api):
    cid = _cid("Brightsmile Dental Group")
    run, _, conv = _agent(api, cid, "Show wasted spend and search terms")
    assert run["intent"] == "search_terms"
    titles = [s["title"] for s in run["result"]["sections"]]
    assert titles[0].startswith("Irrelevant terms") and any("converting" in t.lower() for t in titles)
    run, _, _ = _agent(api, cid, "What should we improve?")
    assert run["intent"] == "recommendations"
    rows = run["result"]["sections"][0]["rows"]
    assert rows and all(len(r) == 6 for r in rows) and any("estimate" in r[3] or r[3] == "not estimated" for r in rows)
    assert any("advice" in r[5] for r in rows)  # advice is labelled as advice: a person makes those changes
    assert any(h.startswith("HOLD") for h in [r[4] for r in rows])  # conversion-based ones are held, each with its reason


def test_requesting_approval_needs_confirmation_and_creates_only_an_approval_request(api):
    cid = _cid("Northside Home Services")
    with session_scope() as db:
        optimization.run_for_client(db, db.get(Client, cid))  # shadow-mode negatives
        rec = db.scalar(select(Recommendation).where(Recommendation.client_id == cid, Recommendation.category == "NEGATIVE_KEYWORD", Recommendation.status == "SHADOW"))
        rid = rec.id if rec else None
    if rid is None:
        pytest.skip("the seeded world produced no safe negative candidate for this client")
    approvals, ops = _count(Approval, client_id=cid), _count(Operation)
    run, t, conv = _agent(api, cid, f"Request approval for recommendation {rid[:8]}")
    assert run["intent"] == "request_approval" and run["status"] == "WAITING_FOR_CONFIRMATION"
    assert "does not change Google Ads" in run["result"]["confirmation"]["describes"]
    assert _count(Approval, client_id=cid) == approvals  # asking created nothing
    out = api.post(f"/agent/runs/{run['id']}/confirm", {"approve": True})
    assert out.status_code == 200, out.text
    assert _count(Approval, client_id=cid) == approvals + 1 and _count(Operation) == ops  # an approval request, not an execution
    with session_scope() as db:
        ap = db.get(Approval, out.json()["approval_id"])
        assert ap.status in ("PENDING_APPROVAL", "VALIDATION_FAILED") and db.get(Recommendation, rid).status == "PROPOSED"
    bad, _, _ = _agent(api, cid, "Request approval for recommendation zzzzzzzz")
    assert bad["status"] == "COMPLETED_WITH_BLOCKERS" or "recommendation" in bad["result"]["headline"].lower()


def test_weekly_report_is_integrated_with_the_record_and_reused_when_current(api):
    cid = _cid("Brightsmile Dental Group")
    with session_scope() as db:
        db.query(WeeklyReport).filter(WeeklyReport.client_id == cid).delete()
    run, _, conv = _agent(api, cid, "Show the weekly report")
    assert run["intent"] == "weekly_report" and run["plan"][-1]["action"] == "run"
    with session_scope() as db:
        rep = db.scalar(select(WeeklyReport).where(WeeklyReport.client_id == cid))
        s = rep.sections
        assert {"insights", "optimisation", "outcomes_of_earlier_changes", "intelligence"} <= set(s)
        assert s["intelligence"]["judged_against"]["measurement"]["conversion_data_reliable"] is False and "stale_modules" in s["intelligence"]
        assert any(c["anomalies"] for c in s["insights"]["campaigns"]) and s["optimisation"]
        n = db.scalar(select(func.count()).select_from(WeeklyReport).where(WeeklyReport.client_id == cid))
    run2 = list(api.post(f"/agent/conversations/{conv}/messages?sync=true", {"text": "weekly report"}).json()["runs"].values())[-1]
    assert run2["plan"][-1]["action"] == "reuse"
    assert _count(WeeklyReport, client_id=cid) == n
    assert any(sec["type"] == "table" and sec["title"].startswith("Campaigns") for sec in run2["result"]["sections"])


# ------------------------------------------------------------------------------------------------ scheduling and safety
def test_the_scheduled_insights_pass_runs_for_linked_clients_and_is_part_of_the_daily_cycle():
    from app.workers import jobs
    r = jobs.insights_all()
    assert r["status"] == "SUCCEEDED", r
    with session_scope() as db:
        row = db.scalar(select(JobRun).where(JobRun.job == "insights.run_all").order_by(JobRun.created_at.desc()))
        assert {"Brightsmile Dental Group", "Northside Home Services"} <= set(row.details)
        assert row.details["Brightsmile Dental Group"]["anomalies"] >= 2 and "error" not in row.details["Brightsmile Dental Group"]
    import inspect
    assert "insights_all()" in inspect.getsource(jobs.run_daily_cycle)


def test_nothing_in_monitoring_or_the_agent_can_write_to_google_ads():
    s = get_settings()
    assert s.GOOGLE_ADS_WRITES_ENABLED is False and s.AUTO_NEGATIVES_MODE == "shadow"
    ops = _count(Operation)
    api = Api(ADMIN)
    cid = _cid("Summit Legal Partners")
    for text in ("Why is this campaign underperforming?", "What should we improve?", "Show wasted spend and search terms", "weekly report", "How is performance?"):
        _agent(api, cid, text)
    assert _count(Operation) == ops
