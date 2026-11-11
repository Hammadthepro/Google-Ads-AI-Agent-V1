"""The ten-module intelligence record: complete specification coverage, provenance on every finding, honest statuses, reuse,
selective invalidation, stage re-runs, versioned history - all without a linked Google Ads account."""
from __future__ import annotations

import pytest

from app.db.session import session_scope
from app.services import intelligence_service as intel
from tests.conftest import ADMIN, MANAGER, Api
from tests.integration.test_intake_v2 import _fixture_client

REQUIRED = {"finding_id", "module", "category", "subject", "finding", "result", "severity", "origin", "verification", "source_type", "source_reference",
            "retrieved_at", "geography", "confidence", "evidence", "contradicting_evidence", "limitations", "recommendation", "reason"}


@pytest.fixture(scope="module")
def pipeline():
    """One client, researched once (the external-research audit runs against the fixtures), reused by the read-only assertions below."""
    api = Api(ADMIN)
    cid, bid = _fixture_client(api, "Intelligence Co", weekly_target=8)
    r = api.post(f"/clients/{cid}/intelligence/refresh", {"research": True})
    assert r.status_code == 200, r.text
    return api, cid, bid, r.json()


# ------------------------------------------------------------------------------------------------ the specification as data
def test_ten_modules_in_pipeline_order_each_with_its_specified_checks():
    assert intel.MODULE_KEYS == ["business_intelligence", "website", "competitors", "search_intent", "keywords", "geography", "finance", "tracking",
                                 "account_policy", "strategy"]
    assert [m["number"] for m in intel.MODULES.values()] == list(range(1, 11))
    assert intel.spec_check_count() >= 200
    spot = {"business_intelligence": {"usp", "capacity", "restrictions", "client_vs_website_conflicts", "acceptable_acquisition_cost"},
            "website": {"core_web_vitals", "landing_page_suitability", "keyword_to_page_mapping", "ad_promise_match", "landing_page_scores"},
            "competitors": {"semrush_domain_overview", "semrush_keyword_gap", "auction_insights", "google_business_profile"},
            "search_intent": {"keyword_intent_classification", "emergency_vs_non", "brand_vs_nonbrand"},
            "keywords": {"src_keyword_planner", "metric_bid_high", "negative_keywords", "keyword_planner_independence", "forecasts"},
            "geography": {"radius_targeting", "location_option", "excluded_locations"},
            "finance": {"three_scenarios", "affordability", "budget_recommendation", "target_cac", "sensitivity"},
            "tracking": {"offline_conversions", "duplicate_conversions", "measurement_readiness", "consent_privacy"},
            "account_policy": {"billing", "policy_status", "search_terms", "experiments", "change_history"},
            "strategy": {"campaign_structure", "bidding_strategy", "readiness_decision", "launch_requirements"}}
    for m, keys in spot.items():
        have = {c["key"] for c in intel.MODULES[m]["checks"]}
        assert keys <= have, (m, keys - have)
    # every module only depends on earlier modules (a logical order, not a page order)
    for k, m in intel.MODULES.items():
        assert all(intel.MODULE_KEYS.index(d) < intel.MODULE_KEYS.index(k) for d in m["depends_on"])


def test_finding_constructor_keeps_provenance_honest():
    seq = intel._Seq("keywords")
    ok = dict(result="INFO", origin="google_ads_api", verification="verified", source_type="keyword_planner", source_reference="x")
    assert intel.make_finding(seq, "metric_avg_searches", "s", "t", **ok)["confidence"] == 0.9
    with pytest.raises(ValueError, match="not a specified check"):
        intel.make_finding(seq, "not_a_check", "s", "t", **ok)
    with pytest.raises(ValueError, match="never be 'verified'"):
        intel.make_finding(seq, "metric_avg_searches", "s", "t", **{**ok, "origin": "ai_recommendation"})
    with pytest.raises(ValueError, match="not a verified fact"):
        intel.make_finding(seq, "metric_avg_searches", "s", "t", **{**ok, "origin": "third_party_estimate"})
    with pytest.raises(ValueError, match="needs a source"):
        intel.make_finding(seq, "metric_avg_searches", "s", "t", **{**ok, "source_type": "", "source_reference": ""})
    with pytest.raises(ValueError, match="between 0 and 1"):
        intel.make_finding(seq, "metric_avg_searches", "s", "t", confidence=1.5, **ok)
    with pytest.raises(ValueError, match="unknown result"):
        intel.make_finding(seq, "metric_avg_searches", "s", "t", **{**ok, "result": "GREAT"})


# ------------------------------------------------------------------------------------------------ one persistent record, no account needed
def test_all_ten_modules_run_without_a_linked_account_and_every_check_is_accounted_for(pipeline):
    api, cid, _, out = pipeline
    assert out["ran"] == intel.MODULE_KEYS and out["reused"] == []
    rep = out["report"]
    assert rep["account_linked"] is False and len(rep["modules"]) == 10
    for m in rep["modules"]:
        assert m["status"] in intel.STATUSES, m["status"]
        spec = {c["key"] for c in intel.MODULES[m["module"]]["checks"]}
        assert set(m["coverage"]) == spec or m["status"] in ("WAITING_FOR_INTEGRATION", "NOT_STARTED"), (m["module"], spec ^ set(m["coverage"]))
        assert m["checks_total"] == len(spec) and m["version"] == 1
    assert rep["totals"]["checks_specified"] == intel.spec_check_count() and 0 < rep["totals"]["checks_evaluated"] < rep["totals"]["checks_specified"]


def test_every_finding_carries_its_provenance_and_estimates_are_never_verified(pipeline):
    _, _, _, out = pipeline
    findings = [f for m in out["report"]["modules"] for f in m["findings"]]
    assert len(findings) > 100
    for f in findings:
        assert REQUIRED <= set(f), (f["finding_id"], REQUIRED - set(f))
        assert f["origin"] in intel.ORIGINS and f["verification"] in intel.VERIFICATION and f["result"] in intel.RESULTS and f["severity"] in intel.SEVERITIES
        assert 0 <= f["confidence"] <= 1 and f["retrieved_at"]
        if f["origin"] in ("ai_assumption", "ai_recommendation", "third_party_estimate"):
            assert f["verification"] != "verified", f
        if f["verification"] == "unknown":
            assert f["confidence"] == 0
    assert len({f["finding_id"] for f in findings}) == len(findings)
    origins = {f["origin"] for f in findings}
    assert {"client_provided", "externally_researched", "ai_recommendation"} <= origins  # the six kinds of information stay distinguishable


def test_unavailable_data_is_unavailable_not_zero_and_account_history_waits_for_an_account(pipeline):
    _, _, _, out = pipeline
    mods = {m["module"]: m for m in out["report"]["modules"]}
    acct = mods["account_policy"]
    assert acct["status"] == "WAITING_FOR_INTEGRATION" and "not a blocker" in acct["summary"] and "Unavailable, not zero" in acct["summary"]
    assert all(f["result"] == "UNAVAILABLE" and f["verification"] == "unknown" for f in acct["findings"])
    assert all(v == "not_assessed" for v in acct["coverage"].values())
    bi = mods["business_intelligence"]
    profit = [f for f in bi["findings"] if f["category"] == "profitability"]
    assert profit and all(f["result"] == "UNAVAILABLE" for f in profit)  # margins/AOV were never supplied: unknown, not invented
    assert bi["missing_information"] and bi["client_actions"]
    fin = {f["category"]: f for f in mods["finance"]["findings"]}
    assert fin["target_cac"]["result"] == "UNAVAILABLE" and fin["weekly_conversion_target"]["verification"] == "client_provided"


def test_client_provided_and_researched_information_are_separate(pipeline):
    _, _, _, out = pipeline
    mods = {m["module"]: m for m in out["report"]["modules"]}
    assert all(f["origin"] == "client_provided" for f in mods["business_intelligence"]["findings"] if f["source_type"] == "client_intake")
    assert any(f["origin"] == "externally_researched" for f in mods["website"]["findings"])
    assert not any(f["origin"] == "client_provided" for f in mods["website"]["findings"] if f["source_type"] == "audit_report")


def test_readiness_decision_is_explained_and_missing_account_is_a_condition_not_a_block(pipeline):
    _, _, _, out = pipeline
    rep = out["report"]
    assert rep["decision"] in ("ready", "ready_with_conditions", "blocked", "not_commercially_viable")
    assert rep["decision_label"] in ("Ready", "Ready with conditions", "Not ready", "Not commercially viable")
    strat = next(m for m in rep["modules"] if m["module"] == "strategy")
    assert strat["decision"] == rep["decision"] and rep["decision_reason"]
    conditions = next(f for f in strat["findings"] if f["category"] == "readiness_decision")["value"]["conditions"]
    assert any("No Google Ads account is linked" in c for c in conditions)
    assert "separate approvals" in " ".join(next(f for f in strat["findings"] if f["category"] == "readiness_decision")["limitations"])


# ------------------------------------------------------------------------------------------------ reuse, invalidation, stage re-runs
def test_unchanged_research_is_reused_not_repeated(pipeline):
    api, cid, _, _ = pipeline
    again = api.post(f"/clients/{cid}/intelligence/refresh", {"research": True}).json()
    still_stale = set(again["report"]["stale_modules"])
    assert set(again["ran"]) == still_stale  # only modules that could never complete (nothing changed) are re-evaluated
    assert {"business_intelligence", "website", "competitors", "keywords", "geography", "finance"} <= set(again["reused"])
    for m in again["report"]["modules"]:
        if m["module"] in again["reused"]:
            assert m["version"] == 1


def test_editing_an_input_makes_only_the_affected_modules_stale(pipeline):
    api, cid, bid, _ = pipeline
    before = api.get(f"/clients/{cid}/intelligence").json()
    base_stale = set(before["stale_modules"])
    with session_scope() as db:  # a competitor changes: competitors and everything built on it; not business intelligence, website or tracking
        from app.models import Competitor
        db.add(Competitor(business_id=bid, name="New Rival", website_url="https://newrival.example/"))
    after = set(api.get(f"/clients/{cid}/intelligence").json()["stale_modules"])
    new = after - base_stale
    assert {"competitors", "search_intent", "keywords", "geography", "finance", "strategy"} <= after
    assert new and not ({"business_intelligence", "website", "tracking", "account_policy"} & new)
    out = api.post(f"/clients/{cid}/intelligence/refresh", {}).json()
    assert "competitors" in out["ran"] and "business_intelligence" in out["reused"] and "website" in out["reused"] and "tracking" in out["reused"]
    hist = api.get(f"/clients/{cid}/intelligence/competitors/history").json()
    assert [h["version"] for h in hist] == [2, 1] and hist[0]["is_current"] and not hist[1]["is_current"]  # history kept, old version no longer current


def test_a_single_stage_can_be_rerun_and_only_its_dependents_follow(pipeline):
    api, cid, _, _ = pipeline
    before = {m["module"]: m["version"] for m in api.get(f"/clients/{cid}/intelligence").json()["modules"]}
    out = api.post(f"/clients/{cid}/intelligence/refresh", {"modules": ["finance"]}).json()
    assert out["ran"] == ["finance", "strategy"]  # strategy consumes finance; nothing else does
    after = {m["module"]: m["version"] for m in out["report"]["modules"]}
    assert after["finance"] == before["finance"] + 1 and after["strategy"] == before["strategy"] + 1
    assert all(after[m] == before[m] for m in intel.MODULE_KEYS if m not in ("finance", "strategy"))
    assert api.post(f"/clients/{cid}/intelligence/refresh", {"modules": ["nonsense"]}).status_code == 422


def test_a_failing_stage_is_recorded_as_failed_never_as_completed(pipeline, monkeypatch):
    api, cid, _, _ = pipeline

    def boom(ctx, seq):
        raise RuntimeError("source exploded")
    monkeypatch.setitem(intel.EVALUATORS, "geography", boom)
    out = api.post(f"/clients/{cid}/intelligence/refresh", {"modules": ["geography"]}).json()
    geo = next(m for m in out["report"]["modules"] if m["module"] == "geography")
    assert geo["status"] == "FAILED" and "source exploded" in geo["summary"] and geo["findings"] == []
    assert "geography" in out["report"]["stale_modules"]  # a failed run is never treated as reusable
    monkeypatch.undo()
    fixed = api.post(f"/clients/{cid}/intelligence/refresh", {}).json()
    assert "geography" in fixed["ran"] and next(m for m in fixed["report"]["modules"] if m["module"] == "geography")["status"] != "FAILED"


def test_intelligence_respects_client_access(pipeline):
    _, cid, _, _ = pipeline
    mgr = Api(MANAGER)  # not assigned to this client
    assert mgr.get(f"/clients/{cid}/intelligence").status_code in (403, 404)
    assert mgr.post(f"/clients/{cid}/intelligence/refresh", {}).status_code in (403, 404)


def test_without_research_nothing_is_invented():
    api = Api(ADMIN)
    cid, _ = _fixture_client(api, "No Research Co")
    out = api.post(f"/clients/{cid}/intelligence/refresh", {}).json()  # research=false: no external service is contacted
    mods = {m["module"]: m for m in out["report"]["modules"]}
    assert mods["website"]["status"] == "NOT_STARTED" and mods["website"]["findings"] and all(f["result"] == "UNAVAILABLE" for f in mods["website"]["findings"])
    assert mods["keywords"]["status"] in ("NOT_STARTED", "PARTIALLY_COMPLETED")
    est = next(f for f in mods["finance"]["findings"] if f["category"] == "estimated_cpc_range")
    assert est["result"] == "UNAVAILABLE" and est["verification"] == "unknown" and est["value"] is None
