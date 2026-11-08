"""Pure-logic unit tests: feasibility math (#14), hashing, claim detection, keyword labelling, overlap, policy text checks."""
from __future__ import annotations

import pytest

from app.adapters.ai import label_keyword, mock_ad_copy
from app.adapters.google_ads.mock import policy_issues_for_text
from app.core.util import canonical_json, payload_hash, redact
from app.services.audits import overlap_check
from app.services.feasibility import calculate, review_adjustment
from app.services.planner import parse_human_plan
from app.services.validation import claim_violations


def test_feasibility_pdf_example_allowed_cpl():
    # PDF: 50/week at $1,500/month allows about $7 per lead
    r = calculate(monthly_budget=1500, weekly_target=50, cpc_range=(8, 6, 4.5), cvr_range=(0.03, 0.06, 0.1))
    assert r["allowed_cost_per_lead"] == pytest.approx(6.9, abs=0.05)
    assert r["daily_budget"] == pytest.approx(1500 / 30.4, abs=0.01)
    assert r["weekly_budget"] == pytest.approx(1500 / 4.345, abs=0.01)
    assert r["status"] == "UNREALISTIC"
    assert r["is_estimate"] is True


def test_feasibility_scenarios_and_status():
    r = calculate(monthly_budget=3000, weekly_target=8, cpc_range=(10, 7, 5), cvr_range=(0.04, 0.08, 0.12))
    exp = r["scenarios"]["expected"]
    weekly = 3000 / 4.345
    assert exp["weekly_clicks"] == pytest.approx(weekly / 7, abs=0.1)
    assert exp["weekly_conversions"] == pytest.approx(weekly / 7 * 0.08, abs=0.1)
    assert r["scenarios"]["conservative"]["weekly_conversions"] < exp["weekly_conversions"] < r["scenarios"]["optimistic"]["weekly_conversions"]
    assert r["status"] == "PARTIALLY_FEASIBLE"  # expected 7.9 < 8 <= optimistic
    assert r["required_conversion_rate"] == pytest.approx(8 * 7 / weekly, abs=0.001)
    assert r["budget_gap_monthly"] > 0


def test_feasibility_review_adjustment_lowers_forecast():
    base = calculate(monthly_budget=2000, weekly_target=5, cpc_range=(8, 6, 4), cvr_range=(0.04, 0.06, 0.08))
    adj, info = review_adjustment({"user_rating_count": 12, "rating": 4.1}, [{"user_rating_count": 300, "rating": 4.8},
                                                                             {"user_rating_count": 200, "rating": 4.7}], 0.2)
    assert adj == pytest.approx(0.2)
    assert info["reviews_needed_to_reach_median"] == 238
    low = calculate(monthly_budget=2000, weekly_target=5, cpc_range=(8, 6, 4), cvr_range=(0.04, 0.06, 0.08), review_adjustment=adj)
    assert low["expected_weekly_conversions"] < base["expected_weekly_conversions"]


def test_canonical_hash_is_order_independent_and_change_sensitive():
    a = {"b": 1, "a": [1, {"y": 2, "x": 1}]}
    b = {"a": [1, {"x": 1, "y": 2}], "b": 1}
    assert canonical_json(a) == canonical_json(b) and payload_hash(a) == payload_hash(b)
    assert payload_hash(a) != payload_hash({**a, "b": 2})


def test_unverified_claims_detected():
    assert claim_violations("Rated #1 in Columbus", ["Licensed & Insured"]) == ["#1"]
    assert claim_violations("Licensed & Insured", ["Licensed & Insured"]) == []
    assert "guarantee" in claim_violations("Guaranteed results", [])
    assert claim_violations("Request a Quote Today", []) == []


def test_policy_text_checks():
    assert policy_issues_for_text("Call Now!", "headline")
    assert policy_issues_for_text("BEST PLUMBER", "headline")
    assert policy_issues_for_text("Call (555) 201-4400", "description")
    assert not policy_issues_for_text("HVAC Repair in Columbus", "headline")


def test_keyword_labelling_rules():
    services = ["Drain Cleaning", "Water Heater Repair"]
    assert label_keyword("drain cleaning near me", services, [])[0:2] == ("SERVICE", "Drain Cleaning")
    assert label_keyword("plumber jobs", services, [])[0] == "IRRELEVANT"
    assert label_keyword("diy drain cleaning", services, [])[0] == "IRRELEVANT"
    assert label_keyword("septic tank pumping cost", services, ["Septic Tank Pumping"])[0] == "IRRELEVANT"
    assert label_keyword("plumber near me", services, [])[0] == "GENERIC"


def test_overlap_check_duplicates_and_phrase_containment():
    out = overlap_check({"A": ["drain cleaning", "drain cleaning cost"], "B": ["Drain Cleaning"]}, {})
    assert any(o["type"] == "DUPLICATE" and o["keyword"] == "drain cleaning" for o in out)
    assert any(o["type"] == "PHRASE_CONTAINS" for o in out)
    assert overlap_check({"A": ["ac repair"], "B": ["furnace repair"]}, {}) == []


def test_mock_ad_copy_respects_limits():
    ad = mock_ad_copy("Water Heater Repair", "Columbus", "Northside Plumbing", ["Licensed & Insured"])
    assert 3 <= len(ad["headlines"]) <= 15 and all(len(h) <= 30 for h in ad["headlines"])
    assert 2 <= len(ad["descriptions"]) <= 4 and all(len(d) <= 90 for d in ad["descriptions"])
    assert len(ad["path1"]) <= 15


def test_human_plan_parser():
    p = parse_human_plan("Budget: $2,000\nBidding: Maximize Clicks\nAd groups: Drains, Heaters\nKeywords: a b, c\nNegatives: jobs")
    assert p["monthly_budget"] == 2000 and p["bidding_strategy"] == "MAXIMIZE_CLICKS"
    assert p["ad_groups"] == ["Drains", "Heaters"] and p["keywords"] == ["a b", "c"] and p["negatives"] == ["jobs"]


def test_redaction():
    out = redact({"api_key": "x", "nested": {"refresh_token": "y", "ok": "fine gsk_abcdefghijklmnop"}})
    assert out["api_key"] == "***REDACTED***" and out["nested"]["refresh_token"] == "***REDACTED***"
    assert "gsk_" not in out["nested"]["ok"]
