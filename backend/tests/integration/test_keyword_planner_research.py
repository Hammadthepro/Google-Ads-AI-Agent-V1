"""Keyword Planner runs under a separately configured RESEARCH customer: independent of any client's deployment account, never borrowing
one, reporting the exact missing prerequisite, and never turning a missing metric into a zero."""
from __future__ import annotations

import pytest
from google.ads.googleads.v24.common.types.keyword_plan_common import KeywordPlanHistoricalMetrics as Metrics

from app.adapters.keyword_research import GoogleKeywordPlannerAdapter, idea_row
from app.core.errors import IntegrationNotConfigured
from app.db.session import session_scope
from app.models import GoogleAdsAccount
from app.services import integrations_service as svc

KW = dict(retrieved_at="2026-11-11T10:00:00+00:00", location=["geoTargetConstants/2840"], language="languageConstants/1000",
          network="GOOGLE_SEARCH", seeds=["plumber"])


class _Ads:
    """Stands in for RealGoogleAdsAdapter: only what the adapter touches."""
    def _read(self, fn):
        return fn()


@pytest.fixture
def research_cleared():
    def clear():
        with session_scope() as db:
            row = svc._row(db, "keyword_planner")
            if row:
                row.settings = {}
        svc.invalidate()
    clear()
    yield
    clear()


def test_missing_research_customer_is_reported_exactly_and_no_account_is_borrowed(research_cleared):
    with session_scope() as db:  # plenty of enabled client accounts exist in the workspace: none may be used
        assert db.query(GoogleAdsAccount).filter(GoogleAdsAccount.is_manager.is_(False)).count() > 0
    ad = GoogleKeywordPlannerAdapter(_Ads())
    with pytest.raises(IntegrationNotConfigured) as exc:
        ad._customer("1110000004")  # a client's deployment customer is passed in: it must not be used as a fallback
    assert exc.value.provider == "keyword_planner" and "research_customer_id" in str(exc.value.details if hasattr(exc.value, "details") else exc.value.message)
    with pytest.raises(IntegrationNotConfigured):
        ad._customer("")


def test_research_customer_is_independent_of_the_deployment_customer(research_cleared):
    with session_scope() as db:
        svc.save(db, "keyword_planner", settings={"research_customer_id": "555-444-3333"}, secrets={}, user_id=None)
    ad = GoogleKeywordPlannerAdapter(_Ads())
    assert ad._customer("1110000004") == ad._customer("") == ad._customer("9999999999") == "5554443333"  # the deployment ID changes nothing


def test_research_customer_must_be_a_ten_digit_id(research_cleared):
    with session_scope() as db:
        with pytest.raises(Exception, match="10-digit"):
            svc.save(db, "keyword_planner", settings={"research_customer_id": "12345"}, secrets={}, user_id=None)


def test_present_metrics_keep_their_value_and_missing_ones_are_none_never_zero():
    full = idea_row("plumber", Metrics(avg_monthly_searches=1200, competition=4, competition_index=64, low_top_of_page_bid_micros=2_500_000,
                                       high_top_of_page_bid_micros=9_000_000), **KW)
    assert full["avg_monthly_searches"] == 1200 and full["competition"] == "HIGH" and full["competition_index"] == 64
    assert full["low_top_of_page_bid_micros"] == 2_500_000 and full["metrics_unavailable"] == []
    # real zero searches is a value; an absent metric is not
    zero = idea_row("rare term", Metrics(avg_monthly_searches=0), **KW)
    assert zero["avg_monthly_searches"] == 0
    assert zero["competition"] is None and zero["competition_index"] is None and zero["low_top_of_page_bid_micros"] is None
    assert set(zero["metrics_unavailable"]) == {"competition", "competition_index", "low_top_of_page_bid_micros", "high_top_of_page_bid_micros"}
    none = idea_row("no data", Metrics(), **KW)
    assert none["avg_monthly_searches"] is None and "avg_monthly_searches" in none["metrics_unavailable"]


def test_every_idea_records_its_provenance():
    r = idea_row("plumber austin", Metrics(avg_monthly_searches=90), **KW)
    assert r["source"] == "LIVE" and r["provider"] == "google_keyword_planner" and r["retrieved_at"].startswith("2026-11-11")
    assert r["location"] == ["geoTargetConstants/2840"] and r["language"] == "languageConstants/1000" and r["network"] == "GOOGLE_SEARCH"
    assert r["seed_keywords"] == ["plumber"]


def test_audit_pipeline_keeps_missing_metrics_missing(monkeypatch):
    """A keyword without Keyword Planner metrics (e.g. a competitor-derived idea) stays None in the stored keyword table."""
    from app.adapters import registry
    from app.services import audits

    class OneIdea:
        name, source = "OneIdea", "LIVE"

        def generate_ideas(self, customer_id, seeds, **kw):
            assert customer_id == ""  # the pipeline no longer passes a deployment customer into research
            return [idea_row("drain cleaning", Metrics(avg_monthly_searches=400, low_top_of_page_bid_micros=3_000_000, high_top_of_page_bid_micros=6_000_000), **KW)]
    registry.override("keyword_research", OneIdea())
    ctx = {"services": [{"name": "Drain Cleaning", "landing_page_url": ""}], "customer_id": "1110000004", "city": "Austin", "excluded": []}
    comp = {"competitors": [{"name": "Rival", "keyword_ideas": ["rival drain service"]}]}
    with session_scope() as db:
        out = audits.keyword_research(db, ctx, {}, comp, None)
    rows = {r["keyword"]: r for r in out["keywords"]}
    assert rows["rival drain service"]["avg_monthly_searches"] is None and rows["rival drain service"]["high_top_of_page_bid_micros"] is None
    assert rows["drain cleaning"]["avg_monthly_searches"] == 400
