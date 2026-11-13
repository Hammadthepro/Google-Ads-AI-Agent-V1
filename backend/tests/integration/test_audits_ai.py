"""#6-#13 audits, #15-#18 AI provider behaviour, #32 integration failure handling."""
from __future__ import annotations

import json

import pytest

from app.adapters import registry
from app.adapters.ai import GroqAIProvider
from app.services import ai_service
from tests.conftest import ADMIN, ANALYST, Api, inject


def _biz(api, name):
    return next(b for b in api.get("/businesses").json() if b["name"] == name)


# ------------------------------------------------------------------ audits
def test_website_gbp_competitor_and_keyword_audits():
    ana = Api(ANALYST)
    plumb = _biz(ana, "Northside Plumbing")
    r = ana.post("/audits", {"business_id": plumb["id"], "audit_type": "ALL"})
    assert r.status_code == 200, r.text
    by_type = {a["audit_type"]: a for a in r.json()}
    web = by_type["WEBSITE"]["result"]
    assert by_type["WEBSITE"]["status"] == "COMPLETED"
    assert "Leak Detection" in web["services_without_page"]
    assert "Sewer Line Repair" in web["services_not_in_profile"]
    assert web["robots_found"] and web["sitemap_urls"] > 0
    assert any(t.startswith("AW-") for t in web["google_tags_detected"])
    assert web["pagespeed"]["mobile"]["performance_score"] and web["pagespeed"]["desktop"]["performance_score"]
    assert web["content_scores"] and all(0 <= c["score"] <= 100 for c in web["content_scores"])
    assert "Plumber" in web["schema_types"]
    gbp = by_type["GBP"]["result"]
    assert gbp["client"]["simulated"] is True and len(gbp["competitors"]) == 3
    assert gbp["client"]["reviews"][0]["simulated"] is True
    comp = by_type["COMPETITOR"]["result"]
    rapid = next(c for c in comp["competitors"] if c["name"] == "Rapid Rooter Columbus")
    assert "Hydro Jetting" in rapid["services_client_lacks"]
    assert all("—" not in s and "Rapid Rooter" not in s for s in rapid["services"])  # brand headings are not services
    assert comp["pasted_ads_analysis"]["verification"].startswith("Pasted by the team")
    assert any("$49" in o for o in comp["pasted_ads_analysis"]["offers"])
    kw = ana.post("/audits", {"business_id": plumb["id"], "audit_type": "KEYWORD", "keyword_mode": "refresh", "refresh": ["WEBSITE", "GBP", "COMPETITOR"], "confirm": True}).json()[-1]["result"]
    labels = {k["keyword"]: k["label"] for k in kw["keywords"]}
    assert labels.get("drain cleaning jobs") == "IRRELEVANT" and labels.get("drain cleaning near me") == "SERVICE"
    assert all(set(k["match_types"]) <= {"EXACT", "PHRASE"} for k in kw["keywords"])
    assert "drain cleaning jobs" in kw["negative_ideas"]
    assert set(kw["clusters"]) >= {"Drain Cleaning", "Water Heater Repair"}
    assert ana.post("/audits", {"business_id": plumb["id"], "audit_type": "ACCOUNT"}).status_code == 422  # analyst limits


def test_landing_page_outage_detected_by_website_audit():
    admin = Api(ADMIN)
    law = _biz(admin, "Summit Injury Law")
    web = admin.post("/audits", {"business_id": law["id"], "audit_type": "WEBSITE"}).json()[0]["result"]
    assert any(e["status"] == 503 for e in web["crawl_errors"])


# ------------------------------------------------------------------ AI
def _draft_payload():
    return {"business_name": "Northside Plumbing", "website": "https://www.northsideplumbing.example/", "city": "Columbus",
            "phone": "(555) 201-4400", "verified_claims": ["Licensed & Insured"], "excluded_services": [], "measurement": {"conversion_bidding_supported": None, "reason": "not verified", "issues": []},
            "services": [{"name": "Drain Cleaning", "landing_page": "https://www.northsideplumbing.example/drain-cleaning"}],
            "keyword_table": [], "playbook_rules": []}


def test_mock_ai_draft_is_schema_valid_and_recorded(db):
    parsed, run = ai_service.generate(db, "DRAFT_CAMPAIGN", _draft_payload())
    assert parsed.ad_groups and parsed.bidding_strategy == "MAXIMIZE_CONVERSIONS"
    assert run.validation_ok and run.provider == "MockAIProvider" and run.prompt_version
    db.rollback()


def test_challenger_disagrees_with_bad_draft(db):
    bad = {"campaign_name": "x", "bidding_strategy": "MAXIMIZE_CONVERSIONS", "campaign_negatives": [],
           "ad_groups": [{"name": "Drains", "service": "Drain Cleaning", "landing_page": "https://site.example/",
                          "keywords": [{"text": "drain cleaning", "match_type": "BROAD"}],
                          "ads": [{"headlines": ["Best Plumber in Town", "Guaranteed Fix", "Call Today"], "descriptions": ["a", "b"]}]},
                         {"name": "Drains 2", "service": "Drain Cleaning", "landing_page": "https://site.example/drain",
                          "keywords": [{"text": "drain cleaning", "match_type": "BROAD"}], "ads": []}]}
    report, _ = ai_service.generate(db, "CHALLENGE", {"draft": bad, "verified_claims": [], "measurement": {"conversion_bidding_supported": None, "reason": "not verified", "issues": []},
                                                      "feasibility": {"status": "UNREALISTIC", "expected_weekly_conversions": 2, "weekly_target": 10}},
                                    model_role="challenger")
    cats = {o.category for o in report.objections}
    assert report.verdict == "REVISE"
    assert {"UNSUPPORTED_CLAIM", "KEYWORD_OVERLAP", "WEAK_INTENT", "MISSING_EXCLUSION", "BUDGET", "MEASUREMENT", "LANDING_PAGE"} <= cats
    db.rollback()


def test_invalid_ai_json_is_retried_then_rejected(db):
    inject("ai", "invalid_json", method="DRAFT_CAMPAIGN", times=1)
    parsed, run = ai_service.generate(db, "DRAFT_CAMPAIGN", _draft_payload())
    assert run.attempt == 2 and run.validation_ok
    inject("ai", "invalid_json", method="DRAFT_CAMPAIGN", times=2)
    with pytest.raises(ai_service.AIOutputInvalid):
        ai_service.generate(db, "DRAFT_CAMPAIGN", _draft_payload())
    from app.models import AIRun
    bad = db.query(AIRun).filter(AIRun.validation_ok.is_(False), AIRun.error.like("%Invalid AI output%")).all()
    assert len(bad) >= 2 and all(r.parsed_output is None for r in bad)  # rejected output is recorded and never parsed
    inject("ai", "schema_mismatch", method="CHALLENGE", times=2)
    with pytest.raises(ai_service.AIOutputInvalid):
        ai_service.generate(db, "CHALLENGE", {"draft": {}})
    db.rollback()


class _FakeGroq:
    """Stands in for groq.Groq — records the request, returns canned JSON."""

    def __init__(self, content):
        self.content, self.calls = content, []
        self.chat = self
        self.completions = self

    def create(self, **kw):
        self.calls.append(kw)
        msg = type("M", (), {"content": self.content})
        usage = type("U", (), {"prompt_tokens": 11, "completion_tokens": 22})
        return type("R", (), {"choices": [type("C", (), {"message": msg})], "usage": usage})


def test_groq_provider_contract_with_fake_client(db, monkeypatch):
    monkeypatch.setenv("GROQ_MODEL", "configured-model-name")
    from app.core.config import reset_settings_cache
    reset_settings_cache()
    try:
        fake = _FakeGroq(json.dumps({"summary": "ok", "highlights": []}))
        registry.override("ai", GroqAIProvider(client=fake))
        parsed, run = ai_service.generate(db, "EXPLAIN", {"headline": "x"})
        assert parsed.summary == "ok" and run.provider == "GroqAIProvider" and run.model == "configured-model-name"
        call = fake.calls[0]
        assert call["response_format"] == {"type": "json_object"} and call["model"] == "configured-model-name"
        assert "JSON SCHEMA" in call["messages"][0]["content"]
        registry.override("ai", GroqAIProvider(client=_FakeGroq("not json at all")))
        with pytest.raises(ai_service.AIOutputInvalid):
            ai_service.generate(db, "EXPLAIN", {"headline": "x"})
    finally:
        reset_settings_cache()
        db.rollback()


@pytest.mark.skipif(not (__import__("os").environ.get("GROQ_API_KEY") and __import__("os").environ.get("GROQ_MODEL")),
                    reason="Groq not configured (set GROQ_API_KEY and GROQ_MODEL to run the live Groq test)")
def test_live_groq_draft(db):  # scenario #16 — reported as SKIPPED, never PASSED, when not configured
    registry.override("ai", GroqAIProvider())
    parsed, run = ai_service.generate(db, "DRAFT_CAMPAIGN", _draft_payload())
    assert parsed.ad_groups and run.tokens_out > 0
    db.rollback()


# ------------------------------------------------------------------ integration failures (#32)
def test_integration_failures_are_handled_and_never_faked():
    admin = Api(ADMIN)
    plumb = _biz(admin, "Northside Plumbing")
    inject("places", "outage", times=-1)
    r = admin.post("/audits", {"business_id": plumb["id"], "audit_type": "ALL"})
    by = {a["audit_type"]: a for a in r.json()}
    assert by["GBP"]["status"] == "FAILED" and "outage" in by["GBP"]["error"]
    assert by["WEBSITE"]["status"] == "COMPLETED"  # one failure does not stop the others
    inject("pagespeed", "rate_limited", times=-1)
    web = admin.post("/audits", {"business_id": plumb["id"], "audit_type": "WEBSITE"}).json()[0]["result"]
    assert web["pagespeed"]["mobile"]["error"] == "rate_limited"
    inject("keyword_research", "empty", times=-1)
    kw = admin.post("/audits", {"business_id": plumb["id"], "audit_type": "KEYWORD", "keyword_mode": "refresh", "refresh": ["WEBSITE", "GBP", "COMPETITOR"], "confirm": True}).json()[-1]["result"]
    # Keyword Planner returned nothing: other sources still contribute, but no keyword carries a volume or bid it did not observe
    assert not any(k["sources"][0]["source"] == "keyword_planner" for k in kw["keywords"])
    assert all(k["avg_monthly_searches"] is None and k["high_top_of_page_bid_micros"] is None for k in kw["keywords"])
    assert all(m["basis"] == "unavailable" and m["value"] is None for k in kw["keywords"] for m in k["metrics"].values())
