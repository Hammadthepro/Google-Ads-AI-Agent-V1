"""Deterministic financial engine. Every formula in the product specification lives here, as pure functions, with no AI and no I/O.

Rules enforced by construction:
* Nothing is invented. A function that lacks an input returns ``None``; ``None`` is never coerced to zero and a division by zero is
  ``None``, not infinity.
* Rates are decimals (5% = 0.05) everywhere except ``ctr_pct`` / ``cvr_pct`` helpers, which are percentages and say so.
* Every scenario input records where it came from: ``client`` (supplied, unverified), ``measured`` (Google Ads / GA4 / CRM actuals),
  ``estimated`` (a third-party or Keyword Planner estimate), ``assumed`` (a stated planning assumption). All output is planning arithmetic:
  ``is_estimate`` is always true and nothing here predicts an auction price or guarantees a result.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DAYS_PER_MONTH = 30.4
SOURCES = ("client", "measured", "estimated", "assumed")
SCENARIOS = ("conservative", "expected", "optimistic")


def _num(x) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def _div(a, b) -> float | None:
    a, b = _num(a), _num(b)
    if a is None or b is None or b == 0:
        return None
    return a / b


def _mul(*xs) -> float | None:
    out = 1.0
    for x in xs:
        v = _num(x)
        if v is None:
            return None
        out *= v
    return out


def _r(x: float | None, n: int = 2) -> float | None:
    return None if x is None else round(x, n)


# ----------------------------------------------------------------------------------------- measured performance (actuals)
def actual_cpc(total_ad_spend, total_clicks) -> float | None:
    """CPC = Total Ad Spend / Total Clicks"""
    return _div(total_ad_spend, total_clicks)


def ctr_pct(clicks, impressions) -> float | None:
    """CTR = Clicks / Impressions x 100"""
    v = _div(clicks, impressions)
    return None if v is None else v * 100


def cvr_pct(conversions, clicks) -> float | None:
    """CVR = Conversions / Clicks x 100"""
    v = _div(conversions, clicks)
    return None if v is None else v * 100


def cost_per_lead(ad_spend, leads) -> float | None:
    """CPL = Ad Spend / Leads"""
    return _div(ad_spend, leads)


def cost_per_acquisition(ad_spend, customers) -> float | None:
    """CPA = Ad Spend / Customers (for lead-generation reporting a conversion that is a lead is labelled CPL by the caller)"""
    return _div(ad_spend, customers)


def lead_to_customer_rate_pct(customers, qualified_leads) -> float | None:
    """LCR = Customers / Qualified Leads x 100"""
    v = _div(customers, qualified_leads)
    return None if v is None else v * 100


# ----------------------------------------------------------------------------------------- affordability
def target_cpl(target_cac, lead_to_customer_rate) -> float | None:
    """TargetCPL = TargetCAC x LeadToCustomerRate   (also: MaxCPL)"""
    return _mul(target_cac, lead_to_customer_rate)


def max_cpc(target_cpl_value, landing_page_cvr) -> float | None:
    """MaxCPC = TargetCPL x LandingPageCVR"""
    return _mul(target_cpl_value, landing_page_cvr)


def max_cpc_from_cpa(target_cpa, expected_cvr) -> float | None:
    """MaxCPC = TargetCPA x ExpectedCVR"""
    return _mul(target_cpa, expected_cvr)


# ----------------------------------------------------------------------------------------- forecast arithmetic
def clicks_from_impressions(impressions, expected_ctr_pct) -> float | None:
    """Clicks = Impressions x ExpectedCTR / 100"""
    v = _mul(impressions, expected_ctr_pct)
    return None if v is None else v / 100


def clicks_from_budget(budget, expected_cpc) -> float | None:
    """Clicks = Budget / ExpectedCPC"""
    return _div(budget, expected_cpc)


def conversions(clicks, conversion_rate) -> float | None:
    """Conversions = Clicks x ConversionRate (decimal)"""
    return _mul(clicks, conversion_rate)


def spend(clicks, estimated_cpc) -> float | None:
    """Spend = Clicks x EstimatedCPC"""
    return _mul(clicks, estimated_cpc)


def customers_from_leads(qualified_leads, lead_to_customer_rate) -> float | None:
    """Customers = QualifiedLeads x LeadToCustomerRate"""
    return _mul(qualified_leads, lead_to_customer_rate)


def revenue(customers, average_customer_value) -> float | None:
    """Revenue = Customers x AverageCustomerValue"""
    return _mul(customers, average_customer_value)


def gross_profit(revenue_value, gross_margin) -> float | None:
    """GrossProfit = Revenue x GrossMargin (decimal)"""
    return _mul(revenue_value, gross_margin)


def net_profit(gross_profit_value, advertising_spend, other_operating_costs=0.0) -> float | None:
    """NetProfit = GrossProfit - AdvertisingSpend (- other operating costs when known). With no other costs supplied the result is
    'profit after advertising spend only' and callers must label it so (see ``profit_basis``)."""
    g, s, o = _num(gross_profit_value), _num(advertising_spend), _num(other_operating_costs)
    if g is None or s is None or o is None:
        return None
    return g - s - o


def contribution_profit(revenue_value, cost_of_goods_sold, ad_spend, other_variable_costs=0.0) -> float | None:
    """Profit = Revenue - CostOfGoodsSold - AdSpend - OtherVariableCosts"""
    vals = [_num(revenue_value), _num(cost_of_goods_sold), _num(ad_spend), _num(other_variable_costs)]
    if any(v is None for v in vals):
        return None
    return vals[0] - vals[1] - vals[2] - vals[3]


def roas(conversion_value, advertising_spend) -> float | None:
    """ROAS = ConversionValue / AdvertisingSpend"""
    return _div(conversion_value, advertising_spend)


def roas_pct(conversion_value, advertising_spend) -> float | None:
    """ROAS% = ROAS x 100"""
    v = roas(conversion_value, advertising_spend)
    return None if v is None else v * 100


def daily_budget(monthly_budget) -> float | None:
    """DailyBudget = MonthlyBudget / 30.4"""
    return _div(monthly_budget, DAYS_PER_MONTH)


def monthly_budget(daily_budget_value) -> float | None:
    """MonthlyBudget = DailyBudget x 30.4"""
    return _mul(daily_budget_value, DAYS_PER_MONTH)


# ----------------------------------------------------------------------------------------- scenario model
@dataclass
class Input:
    value: float | None = None
    source: str = "assumed"  # client | measured | estimated | assumed
    note: str = ""

    def __post_init__(self):
        if self.source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}")


@dataclass
class Assumptions:
    """Planning inputs. Anything not supplied stays ``None`` and flows through as 'unavailable'."""
    monthly_budget: Input = field(default_factory=Input)
    expected_cpc: Input = field(default_factory=Input)           # estimate (Keyword Planner bid range, SEMrush, history), never a promise
    landing_page_cvr: Input = field(default_factory=Input)       # decimal: visitors -> leads
    qualified_lead_rate: Input = field(default_factory=Input)    # decimal: leads that are genuine
    lead_to_customer_rate: Input = field(default_factory=Input)  # decimal: qualified leads -> customers
    average_customer_value: Input = field(default_factory=Input)
    gross_margin: Input = field(default_factory=Input)           # decimal
    target_cac: Input = field(default_factory=Input)
    other_operating_costs: Input = field(default_factory=Input)
    expected_impressions: Input = field(default_factory=Input)   # optional: Keyword Planner forecast
    expected_ctr_pct: Input = field(default_factory=Input)       # percent, with expected_impressions
    conversion_value_per_lead: Input = field(default_factory=Input)  # for ROAS when value is tracked per conversion


# How each scenario moves the base assumptions. Conservative = more expensive clicks and weaker funnel; optimistic = the reverse.
# These multipliers are planning assumptions (``assumed``), exposed in the output so nobody mistakes them for market data.
SCENARIO_FACTORS = {
    "conservative": {"expected_cpc": 1.30, "landing_page_cvr": 0.75, "qualified_lead_rate": 0.85, "lead_to_customer_rate": 0.85},
    "expected": {"expected_cpc": 1.00, "landing_page_cvr": 1.00, "qualified_lead_rate": 1.00, "lead_to_customer_rate": 1.00},
    "optimistic": {"expected_cpc": 0.80, "landing_page_cvr": 1.25, "qualified_lead_rate": 1.10, "lead_to_customer_rate": 1.15},
}


def _scaled(a: Assumptions, factors: dict[str, float]) -> dict[str, float | None]:
    out = {}
    for name in ("expected_cpc", "landing_page_cvr", "qualified_lead_rate", "lead_to_customer_rate"):
        v = _num(getattr(a, name).value)
        f = factors.get(name, 1.0)
        out[name] = None if v is None else v * f
    for name in ("monthly_budget", "average_customer_value", "gross_margin", "target_cac", "other_operating_costs", "expected_impressions",
                 "expected_ctr_pct", "conversion_value_per_lead"):
        out[name] = _num(getattr(a, name).value)
    for rate in ("landing_page_cvr", "qualified_lead_rate", "lead_to_customer_rate"):  # a rate can never exceed 100%
        if out[rate] is not None:
            out[rate] = min(out[rate], 1.0)
    return out


def project(a: Assumptions, scenario: str = "expected", *, factors: dict[str, float] | None = None, budget_override: float | None = None) -> dict:
    """One scenario, every step of the chain. Missing inputs make the dependent outputs ``None`` (reported in ``unavailable``)."""
    f = factors if factors is not None else SCENARIO_FACTORS[scenario]
    v = _scaled(a, f)
    budget = _num(budget_override) if budget_override is not None else v["monthly_budget"]
    if v["expected_impressions"] is not None and v["expected_ctr_pct"] is not None:
        clicks = clicks_from_impressions(v["expected_impressions"], v["expected_ctr_pct"])
        impressions = v["expected_impressions"]
        click_basis = "impressions x CTR"
        spent = spend(clicks, v["expected_cpc"])
    else:
        clicks = clicks_from_budget(budget, v["expected_cpc"])
        impressions = None  # not derived from budget alone: never invented
        click_basis = "budget / CPC"
        spent = budget if (budget is not None and clicks is not None) else None
    leads = conversions(clicks, v["landing_page_cvr"])
    qualified = _mul(leads, v["qualified_lead_rate"]) if v["qualified_lead_rate"] is not None else leads
    qualified_basis = "qualified-lead rate applied" if v["qualified_lead_rate"] is not None else "all leads treated as qualified (no qualified-lead rate supplied)"
    cust = customers_from_leads(qualified, v["lead_to_customer_rate"])
    rev = revenue(cust, v["average_customer_value"])
    gp = gross_profit(rev, v["gross_margin"])
    other = v["other_operating_costs"] if v["other_operating_costs"] is not None else 0.0
    net = net_profit(gp, spent, other)
    conv_value = _mul(leads, v["conversion_value_per_lead"]) if v["conversion_value_per_lead"] is not None else rev
    out = {
        "scenario": scenario, "is_estimate": True, "click_basis": click_basis, "qualified_basis": qualified_basis,
        "inputs": {k: _r(x, 4) for k, x in v.items()},
        "estimated_impressions": _r(impressions, 0), "estimated_clicks": _r(clicks, 1), "estimated_spend": _r(spent),
        "estimated_leads": _r(leads, 1), "estimated_cost_per_lead": _r(cost_per_lead(spent, leads)),
        "estimated_qualified_leads": _r(qualified, 1), "estimated_customers": _r(cust, 2),
        "estimated_cost_per_customer": _r(cost_per_acquisition(spent, cust)),
        "estimated_revenue": _r(rev), "estimated_gross_profit": _r(gp), "estimated_net_profit": _r(net),
        "profit_basis": "after advertising spend and stated operating costs" if v["other_operating_costs"] is not None
        else "after advertising spend only (no other operating costs supplied)",
        "estimated_roas": _r(roas(conv_value, spent), 2),
    }
    out["unavailable"] = sorted(k for k, x in out.items() if k.startswith("estimated_") and x is None)
    return out


def scenarios(a: Assumptions) -> dict[str, dict]:
    return {name: project(a, name) for name in SCENARIOS}


# ----------------------------------------------------------------------------------------- affordability and sufficiency
def affordability(a: Assumptions) -> dict:
    """Maximum affordable CPL / CPC from the client's economics, compared with the estimated CPC. Honest about gaps."""
    tcac, l2c, cvr, cpc = (_num(getattr(a, k).value) for k in ("target_cac", "lead_to_customer_rate", "landing_page_cvr", "expected_cpc"))
    tcpl = target_cpl(tcac, l2c)
    mcpc = max_cpc(tcpl, cvr)
    verdict, why = "unknown", []
    if mcpc is None:
        why = [m for m, x in (("target customer acquisition cost", tcac), ("lead-to-customer rate", l2c), ("landing-page conversion rate", cvr)) if x is None]
        why = ["missing: " + ", ".join(why)] if why else ["cannot be calculated"]
    elif cpc is None:
        verdict, why = "unknown", ["no estimated CPC yet to compare with the maximum affordable CPC"]
    elif cpc <= mcpc:
        verdict, why = "affordable", [f"estimated CPC ${cpc:.2f} is within the maximum affordable CPC ${mcpc:.2f}"]
    else:
        verdict, why = "over_limit", [f"estimated CPC ${cpc:.2f} exceeds the maximum affordable CPC ${mcpc:.2f}: the economics do not support this click price"]
    return {"target_cpl": _r(tcpl), "max_affordable_cpc": _r(mcpc), "estimated_cpc": _r(cpc), "verdict": verdict, "reasons": why, "is_estimate": True}


def budget_sufficiency(a: Assumptions, *, min_clicks_for_signal: int = 300, learning_conversions: int = 30) -> dict:
    """Is the monthly budget enough to reach the click and conversion volume needed to learn anything? Planning thresholds are parameters."""
    budget, cpc, cvr = (_num(getattr(a, k).value) for k in ("monthly_budget", "expected_cpc", "landing_page_cvr"))
    clicks = clicks_from_budget(budget, cpc)
    leads = conversions(clicks, cvr)
    need_budget = _mul(_div(learning_conversions, cvr), cpc)  # budget to buy `learning_conversions` leads
    checks = []
    if clicks is not None:
        checks.append({"check": "click volume", "ok": clicks >= min_clicks_for_signal, "detail": f"~{clicks:.0f} clicks/month vs {min_clicks_for_signal} wanted"})
    if leads is not None:
        checks.append({"check": "conversion volume", "ok": leads >= learning_conversions,
                       "detail": f"~{leads:.1f} leads/month vs {learning_conversions} for automated bidding to learn from"})
    return {"checks": checks, "budget_for_learning_threshold": _r(need_budget, 0), "sufficient": bool(checks) and all(c["ok"] for c in checks) if checks else None,
            "is_estimate": True}


def sensitivity(a: Assumptions) -> dict:
    """What happens to the expected case when one assumption moves. Ranks the drivers by impact on net profit (or on leads when profit is unknown)."""
    base = project(a, "expected")
    metric = "estimated_net_profit" if base["estimated_net_profit"] is not None else "estimated_leads"
    rows = []

    def add(label, **kw):
        factors = {**SCENARIO_FACTORS["expected"], **{k: v for k, v in kw.items() if k in SCENARIO_FACTORS["expected"]}}
        p = project(a, "expected", factors=factors, budget_override=kw.get("budget"))
        b, n = base[metric], p[metric]
        rows.append({"change": label, metric: n, "delta": None if b is None or n is None else _r(n - b), "estimated_cost_per_lead": p["estimated_cost_per_lead"]})
    for pct in (10, 20, 30):
        add(f"CPC +{pct}%", expected_cpc=1 + pct / 100)
    add("Landing-page conversion rate -25%", landing_page_cvr=0.75)
    add("Lead quality (qualified-lead rate) -20%", qualified_lead_rate=0.80)
    add("Close rate (lead-to-customer) -20%", lead_to_customer_rate=0.80)
    b = _num(a.monthly_budget.value)
    if b is not None:
        add("Budget -25%", budget=b * 0.75)
        add("Budget +25%", budget=b * 1.25)
    ranked = sorted((r for r in rows if r["delta"] is not None), key=lambda r: abs(r["delta"]), reverse=True)
    return {"metric": metric, "base": base[metric], "rows": rows, "largest_driver": ranked[0]["change"] if ranked else None, "is_estimate": True}


def analyse(a: Assumptions) -> dict:
    """The complete financial feasibility package for the pipeline: scenarios, affordability, sufficiency, sensitivity, recommended budget."""
    sc = scenarios(a)
    aff = affordability(a)
    suff = budget_sufficiency(a)
    exp = sc["expected"]
    mb = _num(a.monthly_budget.value)
    return {
        "scenarios": sc, "affordability": aff, "sufficiency": suff, "sensitivity": sensitivity(a),
        "daily_budget": _r(daily_budget(mb)), "monthly_budget": _r(mb),
        "input_provenance": {k: {"value": getattr(a, k).value, "source": getattr(a, k).source, "note": getattr(a, k).note}
                             for k in a.__dataclass_fields__},
        "scenario_factors": SCENARIO_FACTORS, "scenario_factors_source": "assumed",
        "confidence": confidence(a), "is_estimate": True,
        "caveat": "Planning arithmetic from stated inputs. Not a prediction of auction prices and not a promise of leads, customers or profit.",
        "unavailable": exp["unavailable"],
    }


def confidence(a: Assumptions) -> dict:
    """Low/medium/high from how many inputs are measured or client-supplied rather than assumed. Never above 'medium' without measured data."""
    present = [(k, getattr(a, k)) for k in a.__dataclass_fields__ if _num(getattr(a, k).value) is not None]
    key = ("expected_cpc", "landing_page_cvr", "lead_to_customer_rate", "average_customer_value", "gross_margin")
    have = {k: i.source for k, i in present if k in key}
    missing = [k for k in key if k not in have]
    measured = sum(1 for s in have.values() if s == "measured")
    assumed = sum(1 for s in have.values() if s == "assumed")
    level = "low"
    if not missing and measured >= 2 and assumed == 0:
        level = "high"
    elif len(missing) <= 1 and assumed <= 2:
        level = "medium"
    return {"level": level, "missing_key_inputs": missing, "assumed_key_inputs": [k for k, s in have.items() if s == "assumed"]}
