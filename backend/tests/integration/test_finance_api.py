from __future__ import annotations

from tests.conftest import ADMIN, Api


def test_finance_analyse_endpoint_returns_scenarios_provenance_and_labels_everything_an_estimate():
    api = Api(ADMIN)
    r = api.post("/finance/analyse", {"assumptions": {
        "monthly_budget": {"value": 3000, "source": "client"}, "expected_cpc": {"value": 5, "source": "estimated", "note": "Keyword Planner high-range bid"},
        "landing_page_cvr": {"value": 0.05, "source": "assumed"}, "qualified_lead_rate": {"value": 0.7, "source": "assumed"},
        "lead_to_customer_rate": {"value": 0.2, "source": "client"}, "target_cac": {"value": 300, "source": "client"}}})
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d["scenarios"]) == {"conservative", "expected", "optimistic"} and d["scenarios"]["expected"]["estimated_customers"] == 4.2
    assert d["affordability"]["max_affordable_cpc"] == 3.0 and d["affordability"]["verdict"] == "over_limit"  # $5 estimated CPC > $3 affordable
    assert d["daily_budget"] == 98.68 and d["input_provenance"]["expected_cpc"]["source"] == "estimated" and d["is_estimate"]
    assert "Not a prediction" in d["caveat"] and "estimated_revenue" in d["unavailable"]  # revenue unknown: reported, not zero-filled


def test_finance_analyse_rejects_unknown_inputs_and_bad_sources():
    api = Api(ADMIN)
    r = api.post("/finance/analyse", {"assumptions": {"monthly_budjet": {"value": 1}}})
    assert r.status_code == 422 and "monthly_budjet" in r.text
    assert api.post("/finance/analyse", {"assumptions": {"monthly_budget": {"value": 1, "source": "guess"}}}).status_code == 422
