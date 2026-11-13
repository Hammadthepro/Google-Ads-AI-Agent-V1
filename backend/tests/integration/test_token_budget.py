"""Token budgets: a campaign that does not fit one request is drafted in stages on one fixed plan, every request stays inside the
provider's per-minute budget, nothing is repeated identically after a size rejection, and an incomplete draft is never returned."""
from __future__ import annotations

import json

import pytest

from app.adapters.ai import GroqAIProvider, RawCompletion, serialize
from app.adapters.base import RateLimited
from app.core.config import get_settings
from app.db.session import session_scope
from app.models import AIRun
from app.services import ai_service, drafting
from tests.fakes.ai import MockAIProvider

SERVICES = [f"Service {i}" for i in range(12)]


def _payload(n=12):
    svcs = SERVICES[:n]
    kws = {s: [[f"{s.lower()} {w}", 500 - j * 10 if j % 2 else None] for j, w in enumerate(
        ["near me", "cost", "company", "repair", "emergency", "price", "local", "best", "quote", "pro", "fix", "install"])] for s in svcs}
    return {"business_name": "Acme Plumbing", "website": "https://acme.example", "city": "Austin", "phone": "", "radius_miles": 20,
            "verified_claims": ["Licensed and insured"], "brand_rules": [], "excluded_services": [], "playbook_rules": ["Exact and phrase only"],
            "measurement": {"conversion_bidding_supported": True, "reason": "", "issues": []},
            "services": [{"name": s, "landing_page": f"https://acme.example/{i}"} for i, s in enumerate(svcs)],
            "monthly_budget": 5000, "feasibility": {"status": "FEASIBLE"},
            "keyword_table": {"keywords_by_service": kws, "irrelevant_terms": ["plumbing jobs", "diy plumbing"]}, "note": ""}


class Budgeted(MockAIProvider):
    """The test AI with a per-minute token limit; records what each request would cost, and can misbehave on demand."""
    supports_max_tokens = True

    def __init__(self, tpm_limit=None, **kw):
        super().__init__(**kw)
        self.tpm_limit, self.sent, self.hooks = tpm_limit, [], {}

    def complete(self, purpose, system, user, *, model_role="draft", max_tokens=None):
        est = ai_service.estimate_tokens(system, serialize(user))
        self.sent.append({"purpose": purpose, "estimate": est, "max_tokens": max_tokens, "user": user})
        hook = self.hooks.get(purpose)
        if hook:
            r = hook(user, len([x for x in self.sent if x["purpose"] == purpose]))
            if r is not None:
                return r
        return super().complete(purpose, system, user, model_role=model_role)


def _draft(provider, payload=None):
    with session_scope() as db:
        draft, run, info = drafting.draft_campaign(db, payload or _payload(), provider=provider, label="internal:test", task_id=None)
        return draft, run.id, info


def test_small_campaign_is_one_request():
    p = Budgeted(tpm_limit=20000)
    draft, _, info = _draft(p, _payload(2))
    assert info["mode"] == "single" and [s["purpose"] for s in p.sent] == ["DRAFT_CAMPAIGN"]
    assert p.sent[0]["max_tokens"] == get_settings().AI_DRAFT_MAX_OUTPUT_TOKENS and len(draft.ad_groups) == 2


def test_large_campaign_is_staged_on_one_plan_and_every_request_fits_the_budget():
    p = Budgeted(tpm_limit=3500)
    budget = ai_service.budget_for(p)
    draft, run_id, info = _draft(p)
    purposes = [s["purpose"] for s in p.sent]
    assert info["mode"] == "staged" and "DRAFT_CAMPAIGN" not in purposes  # never sent the request that could not fit
    assert purposes[0] == "DRAFT_CAMPAIGN_SPEC" and purposes.count("DRAFT_AD_GROUP_CONTENT") >= 2
    assert max(s["estimate"] for s in p.sent) <= budget
    assert all(s["max_tokens"] == get_settings().AI_STAGE_MAX_OUTPUT_TOKENS for s in p.sent)
    # the assembled draft is complete and consistent with the plan: one group per service, plan's names/pages, every group written once
    assert [g.service for g in draft.ad_groups] == SERVICES
    assert all(g.landing_page == f"https://acme.example/{i}" for i, g in enumerate(draft.ad_groups))
    assert all(g.keywords and g.ads for g in draft.ad_groups)
    written = [g["name"] for s in p.sent if s["purpose"] == "DRAFT_AD_GROUP_CONTENT" for g in s["user"]["ad_groups"]]
    assert sorted(written) == sorted(g.name for g in draft.ad_groups)
    # content requests carry the fixed plan, not a chance to change it
    content = next(s["user"] for s in p.sent if s["purpose"] == "DRAFT_AD_GROUP_CONTENT")
    assert content["campaign"]["bidding_strategy"] == draft.bidding_strategy and "monthly_budget" not in content
    with session_scope() as db:
        rec = db.get(AIRun, run_id)
        assert rec.stage == "assembled" and rec.validation_ok and rec.parsed_output["campaign_name"] == draft.campaign_name
        assert rec.tokens_in >= 0 and "validated stage answers" in rec.raw_output


def test_output_budget_alone_forces_stages(monkeypatch):
    monkeypatch.setattr(get_settings(), "AI_DRAFT_MAX_OUTPUT_TOKENS", 3000)
    p = Budgeted(tpm_limit=None)  # no per-minute limit (e.g. a provider without one)
    _, _, info = _draft(p, _payload(4))
    assert info["mode"] == "staged" and "output tokens" in info["reason"]


def test_a_size_rejection_is_never_repeated_identically():
    p = Budgeted(tpm_limit=None)
    p.hooks["DRAFT_CAMPAIGN"] = lambda u, n: (_ for _ in ()).throw(RateLimited(
        "The request needs ~8,636 tokens but m allows 8,000 tokens per minute", adapter="Groq",
        details={"too_large": True, "limit": 8000, "requested": 8636, "status": 413, "error_kind": "tpm_exceeded"}))
    draft, _, info = _draft(p, _payload(3))
    assert [s["purpose"] for s in p.sent].count("DRAFT_CAMPAIGN") == 1 and info["mode"] == "staged"
    assert "8,636" in info["reason"] and len(draft.ad_groups) == 3


def test_a_cut_off_single_answer_switches_to_stages():
    p = Budgeted(tpm_limit=None)
    p.hooks["DRAFT_CAMPAIGN"] = lambda u, n: RawCompletion(text='{"campaign_name": "x", "ad_gr', model="m", tokens_out=6000, finish_reason="length")
    _, _, info = _draft(p, _payload(3))
    assert info["mode"] == "staged" and "cut off" in info["reason"]
    with session_scope() as db:
        cut = db.query(AIRun).filter(AIRun.error_kind == "output_truncated").order_by(AIRun.created_at.desc()).first()
        assert cut.finish_reason == "length" and cut.validation_ok is False and cut.parsed_output is None


def test_a_cut_off_batch_is_split_not_repeated(monkeypatch):
    monkeypatch.setattr(get_settings(), "AI_DRAFT_MAX_OUTPUT_TOKENS", 1000)  # force stages
    p = Budgeted(tpm_limit=None)

    def cut_big_batches(u, n):
        if len(u["ad_groups"]) > 2:
            return RawCompletion(text="{", model="m", tokens_out=4000, finish_reason="length")
        return None
    p.hooks["DRAFT_AD_GROUP_CONTENT"] = cut_big_batches
    draft, _, _ = _draft(p, _payload(8))
    batches = [tuple(g["name"] for g in s["user"]["ad_groups"]) for s in p.sent if s["purpose"] == "DRAFT_AD_GROUP_CONTENT"]
    big = [b for b in batches if len(b) > 2]
    assert big and len(big) == len(set(big))  # each over-sized batch was sent once, then split
    assert all(len(b) <= 2 for b in batches if b not in big) and len(draft.ad_groups) == 8


def test_a_stage_that_never_returns_its_ad_groups_fails_the_whole_draft(monkeypatch):
    monkeypatch.setattr(get_settings(), "AI_DRAFT_MAX_OUTPUT_TOKENS", 1000)
    p = Budgeted(tpm_limit=None)
    p.hooks["DRAFT_AD_GROUP_CONTENT"] = lambda u, n: RawCompletion(text=json.dumps({"ad_groups": [
        {"name": "Invented group", "keywords": [], "negatives": [], "ads": []}]}), model="m", finish_reason="stop")
    with pytest.raises(ai_service.AIOutputInvalid) as e:
        _draft(p, _payload(3))
    assert "failed schema validation" in e.value.message and "return exactly these ad groups" in e.value.details["last_error"]
    assert [s["purpose"] for s in p.sent].count("DRAFT_AD_GROUP_CONTENT") == 2  # re-asked once with the reason, then the draft fails


def test_the_plan_must_cover_exactly_the_given_services(monkeypatch):
    monkeypatch.setattr(get_settings(), "AI_DRAFT_MAX_OUTPUT_TOKENS", 1000)
    p = Budgeted(tpm_limit=None)
    bad_plan = {"campaign_name": "c", "bidding_strategy": "MAXIMIZE_CONVERSIONS",
                "ad_groups": [{"name": "Made up", "service": "Roofing", "landing_page": "https://x"}]}
    p.hooks["DRAFT_CAMPAIGN_SPEC"] = lambda u, n: RawCompletion(text=json.dumps(bad_plan), model="m", finish_reason="stop")
    with pytest.raises(ai_service.AIOutputInvalid) as e:
        _draft(p, _payload(2))
    assert e.value.details["error_kind"] == "schema_invalid" and "not one of the listed services" in e.value.details["last_error"]
    assert "DRAFT_AD_GROUP_CONTENT" not in [s["purpose"] for s in p.sent]  # nothing is written against a rejected plan


def test_a_request_over_the_known_limit_is_not_sent():
    p = Budgeted(tpm_limit=500)
    with session_scope() as db, pytest.raises(ai_service.TokenBudgetExceeded):
        ai_service.generate(db, "EXPLAIN", {"headline": "x" * 4000, "facts": {}}, provider=p)
    assert p.sent == []


# ------------------------------------------------------------------ Groq metadata and error kinds
def _groq() -> GroqAIProvider:
    g = GroqAIProvider.__new__(GroqAIProvider)
    g.model = g.challenger_model = "openai/gpt-oss-120b"
    g._configured_tpm, g.rate = 0, {}
    return g


def test_groq_paces_from_its_own_rate_headers():
    g = _groq()
    g._note_headers(g.model, {"x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "900", "x-ratelimit-reset-tokens": "52.5s"})
    assert g.tpm_limit == 8000
    wait, why = g.rate_wait(3000)
    assert 50 < wait <= 53.1 and "8,000" in why
    assert g.rate_wait(500) == (0.0, "")


def test_groq_finish_reason_and_context_errors_are_told_apart():
    import groq
    import httpx

    class C:
        def __init__(self, fn):
            self.chat = type("c", (), {"completions": type("x", (), {"create": staticmethod(fn)})()})()
    g = _groq()
    choice = type("ch", (), {"message": type("m", (), {"content": '{"a": '})(), "finish_reason": "length"})()
    g.client = C(lambda **k: type("r", (), {"choices": [choice], "usage": type("u", (), {"prompt_tokens": 10, "completion_tokens": 6000})()})())
    assert g.complete("EXPLAIN", "s", {}, max_tokens=6000).finish_reason == "length"
    body = {"error": {"message": "This model's maximum context length is 131072 tokens", "code": "context_length_exceeded"}}
    err = groq.BadRequestError("x", response=httpx.Response(400, request=httpx.Request("POST", "https://api.groq.com"), json=body), body=body)
    g.client = C(lambda **k: (_ for _ in ()).throw(err))
    from app.adapters.base import ContextLengthExceeded
    with pytest.raises(ContextLengthExceeded):
        g.complete("EXPLAIN", "s", {})


def test_the_system_prompt_schema_is_compact_and_complete():
    sp = ai_service.system_prompt("DRAFT_CAMPAIGN")
    assert '"title":"Campaign Name"' not in sp and '"title":"CampaignDraft"' not in sp and '"required"' in sp and "MAXIMIZE_CLICKS" in sp and '"maxItems":15' in sp
