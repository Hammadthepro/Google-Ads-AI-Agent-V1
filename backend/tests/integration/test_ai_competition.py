"""AI competition: identical inputs, cross-challenge, rule-based consolidation, graceful degradation."""
from __future__ import annotations


from app.adapters import registry
from app.adapters.ai import AIProvider, MockAIProvider
from app.adapters.base import ServiceUnavailable
from app.db.session import session_scope
from app.models import AIRun
from app.services import competition

SERVICES = [{"name": "Drain Cleaning", "landing_page": "https://x.example/drain"}, {"name": "Water Heater Repair", "landing_page": "https://x.example/wh"}]
TABLE = [{"keyword": "drain cleaning", "label": "SERVICE", "service": "Drain Cleaning", "avg_monthly_searches": 900, "selected": True},
         {"keyword": "water heater repair", "label": "SERVICE", "service": "Water Heater Repair", "avg_monthly_searches": 700, "selected": True},
         {"keyword": "plumbing jobs", "label": "IRRELEVANT", "service": None, "avg_monthly_searches": 100, "selected": False}]
CLAIMS = ["Licensed and insured"]


def _payload():
    return {"business_name": "Acme Plumbing", "website": "https://x.example", "city": "Austin", "phone": "", "radius_miles": 20,
            "verified_claims": CLAIMS, "brand_rules": [], "excluded_services": [], "tracking_state": "AMBER", "playbook_rules": [],
            "services": SERVICES, "monthly_budget": 1500, "feasibility": {}, "keyword_table": TABLE, "note": ""}


def _run(scope="CAMPAIGN"):
    with session_scope() as db:
        res = competition.run(db, scope=scope, payload=_payload(), challenge_ctx={"verified_claims": CLAIMS, "excluded_services": [],
                              "tracking_state": "AMBER", "feasibility": {}}, table=TABLE, verified_claims=CLAIMS,
                              tracking_state="AMBER", override=False, task_id=None)
        runs = db.query(AIRun).order_by(AIRun.created_at).all()
        return res, [(r.purpose, r.provider, r.prompt_template_id, r.prompt_version, r.input_hash) for r in runs[-6:]]


def test_both_sides_get_identical_inputs_and_cross_challenge():
    res, runs = _run()
    c = res["competition"]
    assert c["mode"] == "dual" and c["identical_inputs"] and c["agreement_is_not_proof"]
    drafts = [r for r in runs if r[0] == "DRAFT_CAMPAIGN"]
    assert len(drafts) == 2 and drafts[0][2:] == drafts[1][2:]  # same template, version AND input hash
    assert {r[1] for r in drafts} == {"internal:mock", "claude:mock"}
    assert set(c["challenges"]) == {"internal_on_claude", "claude_on_internal"}
    assert c["comparison"]["comparable"] and "signal, not proof" in c["comparison"]["note"]


def test_consolidation_is_evidence_based():
    res, _ = _run()
    final = res["draft"]
    kws = [(k["text"], k["match_type"]) for g in final["ad_groups"] for k in g["keywords"]]
    assert all(mt in ("EXACT", "PHRASE") for _, mt in kws)
    assert not any("near me" in t for t, _ in kws)  # claude-side-only keyword without research evidence is dropped
    dropped = [d for i in res["competition"]["consolidation"]["groups"] for d in i["dropped"]]
    assert any("near me" in d["keyword"] and "no research evidence" in d["reason"] for d in dropped)
    heads = [h for g in final["ad_groups"] for a in g["ads"] for h in a["headlines"]]
    assert not any(w in h.lower() for h in heads for w in ("#1", "top rated"))  # unverified claims from either side removed
    assert final["bidding_strategy"] == "MAXIMIZE_CONVERSIONS"
    assert abs(sum(b["percent"] for b in final["budget_allocation"]) - 100) < 0.2
    assert len({(t, m) for t, m in kws}) == len(kws)  # no overlap
    assert "plumbing jobs" in final["campaign_negatives"]


def test_degrades_to_single_draft_when_second_ai_fails_never_fabricates():
    class Down(AIProvider):
        name, source = "ClaudeAIProvider", "LIVE"

        def complete(self, *a, **k):
            raise ServiceUnavailable("down", adapter=self.name)
    registry.override("claude_ai", Down())
    res, _ = _run()
    c = res["competition"]
    assert c["mode"] == "single" and "claude" in c["errors"] and "claude" in c["degraded_reason"]
    assert res["draft"]["ad_groups"]  # the internal draft still consolidates


def test_agreement_does_not_override_rules():
    # both sides propose the same bad claim → still removed
    class Same(MockAIProvider):
        def _h_draft_campaign(self, u):
            d = super()._h_draft_campaign(u)
            for g in d["ad_groups"]:
                g["ads"][0]["headlines"].insert(0, "Rated #1 Plumber Anywhere")
                g["keywords"].append({"text": "drain cleaning", "match_type": "BROAD"})
            return d
    registry.override("ai", Same())
    registry.override("claude_ai", Same(variant="b"))
    res, _ = _run()
    heads = [h for g in res["draft"]["ad_groups"] for a in g["ads"] for h in a["headlines"]]
    assert not any("#1" in h for h in heads)
    assert not any(k["match_type"] == "BROAD" for g in res["draft"]["ad_groups"] for k in g["keywords"])


def test_ad_group_scope():
    with session_scope() as db:
        p = {**_payload(), "service": SERVICES[0]}
        res = competition.run(db, scope="AD_GROUP", payload=p, challenge_ctx={"verified_claims": CLAIMS, "excluded_services": [],
                              "tracking_state": "AMBER", "feasibility": {}}, table=TABLE, verified_claims=CLAIMS,
                              tracking_state="AMBER", override=False, task_id=None)
    assert res["draft"]["service"] == "Drain Cleaning" and res["draft"]["ads"]
