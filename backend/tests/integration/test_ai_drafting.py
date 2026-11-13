"""AI drafting: primary/fallback behaviour, output parsing and schema rules, and how provider errors are classified and reported.
Each test drives the real competition / ai_service code with a scripted provider; nothing here talks to a network."""
from __future__ import annotations

import json

import httpx
import pytest

from app.adapters import registry
from app.adapters.ai import AIProvider, GroqAIProvider, RawCompletion, _groq_rate_limit
from app.adapters.base import InvalidResponse, RateLimited, ServiceUnavailable
from app.db.session import session_scope
from app.models import AIRun
from app.services import ai_service, competition, planner
from tests.fakes.ai import MockAIProvider
from tests.integration.test_ai_competition import CLAIMS, TABLE, _payload


def _compete():
    with session_scope() as db:
        return competition.run(db, scope="CAMPAIGN", payload=_payload(), table=TABLE, verified_claims=CLAIMS, conversion_bidding=None,
                               task_id=None, challenge_ctx={"verified_claims": CLAIMS, "excluded_services": [], "feasibility": {},
                                                            "measurement": {"conversion_bidding_supported": None, "reason": "", "issues": []}})


class Down(AIProvider):
    """Fails every drafting call with a given adapter error."""
    source = "LIVE"

    def __init__(self, name, exc):
        self.name, self.exc, self.calls = name, exc, 0

    def complete(self, *a, **k):
        self.calls += 1
        raise self.exc


class Scripted(AIProvider):
    """Returns the given raw texts in order (then repeats the last one), recording what it was asked."""
    name, source = "Scripted", "LIVE"

    def __init__(self, *texts):
        self.texts, self.seen = list(texts), []

    def complete(self, purpose, system, user, *, model_role="draft"):
        self.seen.append(user)
        text = self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        if isinstance(text, Exception):
            raise text
        return RawCompletion(text=text, model="scripted-1")


def _valid_draft() -> dict:
    return MockAIProvider()._h_draft_campaign(_payload())


# ------------------------------------------------------------------ primary / fallback
def test_primary_succeeds_both_drafts_compete():
    res = _compete()
    assert res["draft"]["ad_groups"] and res["competition"]["mode"] == "dual"


def test_primary_fails_and_the_fallback_draft_is_used_with_the_primary_reason_kept():
    too_large = RateLimited("Groq rejected the request: it needs ~9500 tokens but m allows 8000 per minute on this plan.", adapter="Groq",
                            details={"too_large": True, "limit": 8000, "requested": 9500})
    registry.override("ai", Down("GroqAIProvider", too_large))
    res = _compete()
    c = res["competition"]
    assert c["mode"] == "single" and res["draft"]["ad_groups"]
    assert "8000 per minute" in c["errors"]["internal"]  # the exact reason survives, it is not replaced by a generic message


def test_both_providers_fail_with_provider_specific_reasons(monkeypatch):
    monkeypatch.setattr(ai_service.time, "sleep", lambda s: None)
    registry.override("ai", Down("GroqAIProvider", RateLimited("Groq rejected the request as too large (HTTP 413)", adapter="Groq",
                                                                details={"too_large": True})))
    registry.override("claude_ai", Down("ClaudeAIProvider", ServiceUnavailable("Claude API unreachable", adapter="Claude")))
    with pytest.raises(ai_service.AIOutputInvalid) as e:
        _compete()
    msg = e.value.message
    assert msg.startswith("Neither AI produced a valid draft; nothing was created.")
    assert "Groq (internal AI): Groq rejected the request as too large (HTTP 413)" in msg
    assert "Claude: AI provider error (outage): Claude API unreachable" in msg
    assert set(e.value.details["providers"]) == {"internal", "claude"}


# ------------------------------------------------------------------ output parsing and schema rules
def test_invalid_json_is_rejected_then_re_asked_with_the_error():
    p = Scripted('{"campaign_name": "x", "ad_groups": [ {"name": ', json.dumps(_valid_draft()))
    registry.override("ai", p)
    with session_scope() as db:
        parsed, run = ai_service.generate(db, "DRAFT_CAMPAIGN", _payload())
        assert run.attempt == 2 and run.validation_ok and parsed.ad_groups
        first = db.query(AIRun).filter(AIRun.input_hash == run.input_hash, AIRun.attempt == 1).order_by(AIRun.created_at.desc()).first()
        assert first.validation_ok is False and first.error_kind == "malformed_json" and "Invalid AI output" in first.error
    assert "previous_attempt_error" in p.seen[1] and "previous_attempt_error" not in p.seen[0]


def test_markdown_wrapped_json_is_accepted():
    registry.override("ai", Scripted("```json\n" + json.dumps(_valid_draft()) + "\n```"))
    with session_scope() as db:
        parsed, run = ai_service.generate(db, "DRAFT_CAMPAIGN", _payload())
    assert run.attempt == 1 and parsed.campaign_name


def test_missing_required_fields_fail_validation_and_nothing_is_returned():
    d = _valid_draft()
    d.pop("ad_groups")
    registry.override("ai", Scripted(json.dumps(d)))
    with session_scope() as db, pytest.raises(ai_service.AIOutputInvalid) as e:
        ai_service.generate(db, "DRAFT_CAMPAIGN", _payload())
    assert "failed schema validation after 2 attempts" in e.value.message and "ad_groups" in e.value.details["last_error"]


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(bidding_strategy="TARGET_IMPRESSION_SHARE"),          # not an allowed strategy
    lambda d: d.update(ad_groups=[]),                                         # a campaign needs at least one ad group
    lambda d: d["ad_groups"][0]["keywords"].append({"text": "x", "match_type": "BROADISH"}),  # unknown match type
    lambda d: d["budget_allocation"].append({"ad_group": "x", "percent": 140}),               # share over 100%
])
def test_invalid_campaign_data_is_rejected(mutate):
    d = _valid_draft()
    mutate(d)
    registry.override("ai", Scripted(json.dumps(d)))
    with session_scope() as db, pytest.raises(ai_service.AIOutputInvalid):
        ai_service.generate(db, "DRAFT_CAMPAIGN", _payload())


# ------------------------------------------------------------------ Groq error classification
def _status_error(status: int, body: dict, message: str = "error"):
    import groq
    resp = httpx.Response(status, request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"), json=body)
    cls = {400: groq.BadRequestError, 413: groq.APIStatusError, 429: groq.RateLimitError}[status]
    return cls(message, response=resp, body=body)


class _Client:
    def __init__(self, exc):
        self.chat = type("C", (), {"completions": type("X", (), {"create": staticmethod(lambda **k: (_ for _ in ()).throw(exc))})()})()


def _groq(exc) -> GroqAIProvider:
    p = GroqAIProvider.__new__(GroqAIProvider)
    p.model = p.challenger_model = "openai/gpt-oss-120b"
    p.client = _Client(exc)
    return p


def test_groq_json_validate_failed_is_bad_output_not_a_bad_request():
    exc = _status_error(400, {"error": {"message": "Failed to generate JSON.", "code": "json_validate_failed", "type": "invalid_request_error"}})
    with pytest.raises(InvalidResponse) as e:
        _groq(exc).complete("DRAFT_CAMPAIGN", "s", {})
    assert e.value.kind == "invalid_response" and e.value.details["error_kind"] == "malformed_json" and "returned malformed JSON" in e.value.message


def test_groq_413_is_reported_as_too_large_and_not_retried_as_an_outage():
    exc = _status_error(413, {"error": {"message": "Request too large for model"}}, message="Request too large for model")
    with pytest.raises(RateLimited) as e:
        _groq(exc).complete("DRAFT_CAMPAIGN", "s", {})
    assert e.value.details["too_large"] is True and e.value.details["status"] == 413 and "HTTP 413" in e.value.message


def test_groq_retry_wait_is_read_from_the_message_when_there_is_no_header():
    class E(Exception):
        message = "Rate limit reached on tokens per minute (TPM): Limit 8000, Used 7984, Requested 1200. Please try again in 1m3.5s."
        response = type("R", (), {"headers": {}})()
    r = _groq_rate_limit(E(), "Groq", "m")
    assert r.details["retry_after"] == pytest.approx(63.5) and r.details["too_large"] is False and "retry in 64s" in r.message


def test_a_cut_off_json_answer_is_re_asked_with_a_compact_instruction():
    p = Scripted(InvalidResponse("m did not return valid JSON (Groq json_validate_failed)", adapter="Groq"), json.dumps(_valid_draft()))
    registry.override("ai", p)
    with session_scope() as db:
        parsed, run = ai_service.generate(db, "DRAFT_CAMPAIGN", _payload())
    assert run.attempt == 2 and parsed.ad_groups
    assert "compact JSON" in p.seen[1]["previous_attempt_error"]


def test_rate_limit_wait_follows_groqs_requested_window_up_to_the_cap(monkeypatch):
    slept = []
    monkeypatch.setattr(ai_service.time, "sleep", lambda s: slept.append(s))
    p = Scripted(RateLimited("Groq rate limit reached", adapter="Groq", details={"retry_after": 52.7, "too_large": False}),
                 RateLimited("Groq rate limit reached", adapter="Groq", details={"retry_after": 500, "too_large": False}),
                 json.dumps({"summary": "ok", "highlights": []}))
    registry.override("ai", p)
    with session_scope() as db:
        ai_service.generate(db, "EXPLAIN", {"headline": "x", "facts": {}})
    assert slept == [52.7, ai_service.RATE_WAIT_MAX]


# ------------------------------------------------------------------ the draft request itself
def test_compact_keyword_input_never_invents_volumes_and_is_bounded():
    rows = [{"keyword": f"drain cleaning {i}", "label": "SERVICE", "service": "Drain Cleaning", "selected": True,
             "avg_monthly_searches": (100 - i) if i % 2 else None} for i in range(60)]
    rows += [{"keyword": f"junk {i}", "label": "IRRELEVANT", "selected": False} for i in range(80)]
    rows += [{"keyword": "unselected", "label": "SERVICE", "service": "Drain Cleaning", "selected": False, "avg_monthly_searches": 5000}]
    out = planner.compact_keywords(rows)
    pairs = out["keywords_by_service"]["Drain Cleaning"]
    assert len(pairs) == planner.PER_SERVICE and "unselected" not in [k for k, _ in pairs]
    assert pairs[0] == ["drain cleaning 1", 99]  # strongest known volume first
    assert all(v is None for k, v in pairs if int(k.rsplit(" ", 1)[1]) % 2 == 0)  # unknown stays unknown (null), never 0
    assert len(out["irrelevant_terms"]) == planner.MAX_IRRELEVANT
    assert len({k for k, _ in pairs}) == len(pairs)  # each keyword once
    assert len(json.dumps(out)) < len(json.dumps(rows)) / 3
