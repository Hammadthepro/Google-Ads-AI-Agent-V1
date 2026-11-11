"""Financial engine: every formula in the specification, its worked examples, and the 'never invent a number' rules."""
from __future__ import annotations

import pytest

from app.services import finance as f


def test_spec_example_target_cpl_and_max_cpc():
    assert f.target_cpl(300, 0.20) == pytest.approx(60)  # TargetCPL = TargetCAC x LeadToCustomerRate
    assert f.max_cpc(60, 0.05) == pytest.approx(3.0)     # MaxCPC = TargetCPL x LandingPageCVR
    assert f.max_cpc_from_cpa(60, 0.05) == pytest.approx(3.0)


def test_spec_example_three_thousand_dollar_month():
    a = f.Assumptions(monthly_budget=f.Input(3000, "client"), expected_cpc=f.Input(5, "estimated"), landing_page_cvr=f.Input(0.05, "assumed"),
                      qualified_lead_rate=f.Input(0.70, "assumed"), lead_to_customer_rate=f.Input(0.20, "assumed"))
    p = f.project(a, "expected")
    assert p["estimated_clicks"] == 600 and p["estimated_leads"] == 30 and p["estimated_qualified_leads"] == 21
    assert p["estimated_customers"] == 4.2 and p["estimated_cost_per_lead"] == 100.0 and p["estimated_cost_per_customer"] == 714.29
    assert p["estimated_impressions"] is None  # budget alone never produces an impression count
    assert p["estimated_revenue"] is None and p["estimated_net_profit"] is None and "estimated_revenue" in p["unavailable"]  # unknown, not zero


def test_basic_formulas():
    assert f.actual_cpc(500, 100) == 5 and f.ctr_pct(5, 100) == 5 and f.cvr_pct(5, 100) == 5
    assert f.cost_per_lead(300, 6) == 50 and f.cost_per_acquisition(300, 3) == 100 and f.lead_to_customer_rate_pct(2, 10) == 20
    assert f.clicks_from_impressions(10_000, 4) == 400 and f.clicks_from_budget(1000, 4) == 250
    assert f.conversions(400, 0.05) == 20 and f.spend(400, 2.5) == 1000
    assert f.customers_from_leads(20, 0.25) == 5 and f.revenue(5, 800) == 4000 and f.gross_profit(4000, 0.4) == 1600
    assert f.net_profit(1600, 1000) == 600 and f.net_profit(1600, 1000, 150) == 450
    assert f.contribution_profit(4000, 2400, 1000, 100) == 500
    assert f.roas(4000, 1000) == 4 and f.roas_pct(4000, 1000) == 400
    assert f.daily_budget(3040) == pytest.approx(100) and f.monthly_budget(100) == pytest.approx(3040)


def test_missing_inputs_and_zero_division_are_none_never_zero_or_infinity():
    assert f.actual_cpc(100, 0) is None and f.cost_per_lead(100, 0) is None and f.roas(10, 0) is None
    assert f.actual_cpc(None, 10) is None and f.conversions(None, 0.05) is None and f.revenue(3, None) is None
    assert f.target_cpl("abc", 0.2) is None and f.max_cpc(60, float("nan")) is None and f.spend(True, 2) is None
    assert f.net_profit(None, 100) is None


def test_scenarios_move_in_the_right_direction():
    a = f.Assumptions(monthly_budget=f.Input(3000, "client"), expected_cpc=f.Input(5, "estimated"), landing_page_cvr=f.Input(0.05, "assumed"),
                      qualified_lead_rate=f.Input(0.7, "assumed"), lead_to_customer_rate=f.Input(0.2, "assumed"),
                      average_customer_value=f.Input(2000, "client"), gross_margin=f.Input(0.5, "client"))
    s = f.scenarios(a)
    cons, exp, opt = (s[k] for k in ("conservative", "expected", "optimistic"))
    assert cons["estimated_leads"] < exp["estimated_leads"] < opt["estimated_leads"]
    assert cons["estimated_cost_per_lead"] > exp["estimated_cost_per_lead"] > opt["estimated_cost_per_lead"]
    assert cons["estimated_net_profit"] < exp["estimated_net_profit"] < opt["estimated_net_profit"]
    assert exp["estimated_revenue"] == 8400.0 and exp["estimated_gross_profit"] == 4200.0 and exp["estimated_net_profit"] == 1200.0
    assert exp["profit_basis"].startswith("after advertising spend only")
    assert all(x["is_estimate"] for x in s.values())


def test_rates_are_capped_at_one_hundred_percent():
    a = f.Assumptions(monthly_budget=f.Input(1000), expected_cpc=f.Input(1), landing_page_cvr=f.Input(0.9), qualified_lead_rate=f.Input(0.95),
                      lead_to_customer_rate=f.Input(0.95))
    p = f.project(a, "optimistic")
    assert max(p["inputs"][k] for k in ("landing_page_cvr", "qualified_lead_rate", "lead_to_customer_rate")) <= 1.0


def test_impressions_path_uses_ctr_and_does_not_assume_the_budget_is_spent():
    a = f.Assumptions(expected_impressions=f.Input(20_000, "estimated"), expected_ctr_pct=f.Input(5, "estimated"), expected_cpc=f.Input(2, "estimated"),
                      landing_page_cvr=f.Input(0.1, "assumed"))
    p = f.project(a, "expected")
    assert p["click_basis"] == "impressions x CTR" and p["estimated_clicks"] == 1000 and p["estimated_spend"] == 2000 and p["estimated_leads"] == 100


def test_affordability_verdicts():
    base = dict(target_cac=f.Input(300, "client"), lead_to_customer_rate=f.Input(0.2, "client"), landing_page_cvr=f.Input(0.05, "measured"))
    ok = f.affordability(f.Assumptions(**base, expected_cpc=f.Input(2.5, "estimated")))
    assert ok["verdict"] == "affordable" and ok["max_affordable_cpc"] == 3.0 and ok["target_cpl"] == 60
    over = f.affordability(f.Assumptions(**base, expected_cpc=f.Input(6, "estimated")))
    assert over["verdict"] == "over_limit" and "do not support" in over["reasons"][0]
    unknown = f.affordability(f.Assumptions(expected_cpc=f.Input(6, "estimated")))
    assert unknown["verdict"] == "unknown" and unknown["max_affordable_cpc"] is None and "missing" in unknown["reasons"][0]


def test_budget_sufficiency_and_sensitivity():
    a = f.Assumptions(monthly_budget=f.Input(500, "client"), expected_cpc=f.Input(5, "estimated"), landing_page_cvr=f.Input(0.05, "assumed"),
                      lead_to_customer_rate=f.Input(0.2, "assumed"), average_customer_value=f.Input(2000, "client"), gross_margin=f.Input(0.5, "client"))
    s = f.budget_sufficiency(a)
    assert s["sufficient"] is False and s["budget_for_learning_threshold"] == 3000  # 30 leads / 5% x $5
    sens = f.sensitivity(a)
    assert sens["metric"] == "estimated_net_profit" and sens["largest_driver"]
    cpc10, cpc30 = (next(r for r in sens["rows"] if r["change"] == c) for c in ("CPC +10%", "CPC +30%"))
    assert cpc30["delta"] < cpc10["delta"] < 0
    assert f.sensitivity(f.Assumptions())["largest_driver"] is None  # nothing to analyse -> no invented driver


def test_confidence_never_high_without_measured_data():
    mk = lambda src: f.Assumptions(**{k: f.Input(1, src) for k in ("expected_cpc", "landing_page_cvr", "lead_to_customer_rate", "average_customer_value", "gross_margin")})  # noqa: E731
    assert f.confidence(mk("assumed"))["level"] == "low"  # five assumed inputs never read as confident
    assert f.confidence(mk("estimated"))["level"] != "high"
    assert f.confidence(mk("measured"))["level"] == "high"
    assert f.confidence(f.Assumptions())["level"] == "low"


def test_invalid_source_is_rejected():
    with pytest.raises(ValueError):
        f.Input(1, "guess")
