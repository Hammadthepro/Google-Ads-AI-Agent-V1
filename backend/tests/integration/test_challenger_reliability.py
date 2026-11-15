"""Why the Challenger AI "sometimes did not run", and the fixes:
1. Gemini thinking models spend hidden reasoning tokens from the same output budget: the review was cut off or empty, retried, then
   recorded as "not performed". Now one re-ask with room for both, and an empty all-reasoning answer is named for what it is.
2. The strict review schema rejected substantive reviews over form (lower-case severity, a category outside the list, extra fields).
3. An unexpected reviewer error escaped and failed the whole build.
Every review now records its lifecycle: required, dispatched, provider, model, each attempt and the outcome."""
from __future__ import annotations

import json

import httpx

from app.adapters import registry
from app.adapters.ai import AIProvider, RawCompletion
from app.adapters.ai import providers as P
from app.db.session import session_scope
from app.models import AIRun
from app.services import challenger
from tests.integration.test_ai_roles import ws  # noqa: F401  (fixture)

KEY = "AIzaTestKey-not-real-0000000000"


class Scripted(AIProvider):
    name, source = "Scripted", "TEST"
    model = challenger_model = "scripted-1"

    def __init__(self, *texts: str, boom: Exception | None = None):
        self.texts, self.boom, self.calls = list(texts), boom, 0

    def complete(self, purpose, system, user, *, model_role="draft") -> RawCompletion:
        self.calls += 1
        if self.boom:
            raise self.boom
        return RawCompletion(text=self.texts.pop(0), model="scripted-1", tokens_in=100, tokens_out=50, latency_ms=12, finish_reason="stop")


MESSY = json.dumps({"verdict": "revise", "summary": "extra field", "confidence": "80%", "agreements": "structure is sound",
                    "objections": [{"category": "keywords", "severity": "critical", "issue": "‘unclog’ has weak intent", "fix": "remove it",
                                    "evidence": "research shows no volume"},
                                   {"category": "something new", "severity": "Low", "message": "minor wording"}]})


def test_a_substantive_review_with_imperfect_form_is_accepted_not_dropped(ws):  # noqa: F811
    p = Scripted(MESSY)
    registry.override("challenger_ai", p)
    with session_scope() as db:
        rep, run_id = challenger.review_draft(db, {"campaign_name": "x"}, {"verified_claims": []}, kind="campaign_draft", task_id=None)
        assert rep["performed"] is True and p.calls == 1 and db.get(AIRun, run_id).validation_ok
    assert rep["verdict"] == "REVISE" and rep["confidence"] == 0.8 and rep["agreements"] == ["structure is sound"]
    o1, o2 = rep["objections"]
    assert (o1["category"], o1["severity"], o1["suggestion"]) == ("IRRELEVANT_KEYWORD", "HIGH", "remove it")
    assert (o2["category"], o2["severity"]) == ("OTHER", "LOW")
    lc = rep["lifecycle"]
    assert lc["required"] and lc["dispatched"] and lc["outcome"] == "performed" and lc["attempts"][0]["ok"] is True


def test_a_review_without_a_verdict_is_still_rejected_and_the_failure_is_recorded(ws):  # noqa: F811
    registry.override("challenger_ai", Scripted('{"objections": []}', '{"notes": "looks fine"}'))
    with session_scope() as db:
        rep, run_id = challenger.review_draft(db, {"campaign_name": "y"}, {}, kind="campaign_draft", task_id=None)
    assert rep["performed"] is False and run_id is None and rep["verdict"] is None
    lc = rep["lifecycle"]
    assert lc["dispatched"] and lc["outcome"] == "failed" and len(lc["attempts"]) == 2
    assert all(a["error_kind"] == "schema_invalid" for a in lc["attempts"])


def test_an_unexpected_reviewer_error_never_fails_the_build_or_passes_as_reviewed(ws):  # noqa: F811
    registry.override("challenger_ai", Scripted(boom=KeyError("candidates")))
    with session_scope() as db:
        rep, run_id = challenger.review_draft(db, {"campaign_name": "z"}, {}, kind="campaign_draft", task_id=None)
    assert rep["performed"] is False and rep["error_kind"] == "unexpected" and "KeyError" in rep["reason"]
    assert rep["lifecycle"]["outcome"] == "failed"


def _gemini(handler):
    return P.build("gemini", KEY, "gemini-flash-lite-latest", http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_gemini_reasoning_that_eats_the_output_budget_gets_one_re_ask_with_room():
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        if len(bodies) == 1:
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "…", "thought": True}, {"text": '{"verd'}]},
                                                             "finishReason": "MAX_TOKENS"}],
                                             "usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 20, "thoughtsTokenCount": 5800}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"verdict": "APPROVE"}'}]}, "finishReason": "STOP"}],
                                         "usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 12, "thoughtsTokenCount": 5900}})
    raw = _gemini(handler).complete("CHALLENGE", "sys", {"draft": {}})
    assert len(bodies) == 2 and bodies[0]["generationConfig"]["maxOutputTokens"] == 6000
    assert bodies[1]["generationConfig"]["maxOutputTokens"] == 12000  # reasoning + answer room, within the model's limit
    assert raw.text == '{"verdict": "APPROVE"}' and raw.finish_reason == "stop"


def test_gemini_answer_that_is_all_reasoning_is_named_not_called_malformed():
    def handler(req):
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "…", "thought": True}]}, "finishReason": "MAX_TOKENS"}],
                                         "usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 0, "thoughtsTokenCount": 65000}})
    p = _gemini(handler)
    p.model_meta = {"max_output": 65536}
    try:
        p.complete("CHALLENGE", "sys", {"draft": {}}, max_tokens=65536)
        raise AssertionError("expected an error")
    except Exception as exc:  # noqa: BLE001
        assert "on reasoning and returned no answer" in str(exc) and exc.details["error_kind"] == "output_truncated"


def test_a_normal_gemini_answer_is_sent_once():
    n = []

    def handler(req):
        n.append(1)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}, "finishReason": "STOP"}],
                                         "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3}})
    assert _gemini(handler).complete("EXPLAIN", "sys", {}).text == '{"ok": true}' and n == [1]
