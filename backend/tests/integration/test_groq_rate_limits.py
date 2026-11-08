"""Groq plan limits: transient 429s are waited out, 'request can never fit' is reported clearly, the dashboard shows the limit."""
from __future__ import annotations

import json

import httpx
import pytest

from app.adapters import registry
from app.adapters.ai import AIProvider, RawCompletion, _groq_rate_limit
from app.adapters.base import RateLimited
from app.db.session import session_scope
from app.models import IntegrationConfig
from app.services import ai_service
from app.services import integrations_service as svc


class _Exc(Exception):
    def __init__(self, message, retry_after=None):
        self.message = message
        self.response = type("R", (), {"headers": {"retry-after": str(retry_after)} if retry_after is not None else {}})()


def test_classifies_transient_and_too_large_rate_limits():
    t = _groq_rate_limit(_Exc("Rate limit reached for model on tokens per minute (TPM): Limit 8000, Used 7000, Requested 2500", retry_after=12), "G", "m")
    assert t.details["retry_after"] == 12 and t.details["too_large"] is False
    big = _groq_rate_limit(_Exc("Request too large: tokens per minute (TPM): Limit 8000, Used 0, Requested 9500"), "G", "openai/gpt-oss-120b")
    assert big.details["too_large"] is True and "8000" in big.message and "higher Groq tier" in big.message


class _Flaky(AIProvider):
    name, source = "Flaky", "LIVE"

    def __init__(self, fails, details):
        self.fails, self.details, self.calls = fails, details, 0

    def complete(self, purpose, system, user, *, model_role="draft"):
        self.calls += 1
        if self.calls <= self.fails:
            raise RateLimited("Groq rate limit reached", adapter="Flaky", details=self.details)
        return RawCompletion(text=json.dumps({"summary": "ok", "highlights": []}), model="m")


def test_ai_service_waits_for_the_rate_limit_window_then_succeeds(monkeypatch):
    slept = []
    monkeypatch.setattr(ai_service.time, "sleep", lambda s: slept.append(s))
    p = _Flaky(2, {"retry_after": 7, "too_large": False})
    registry.override("ai", p)
    with session_scope() as db:
        parsed, run = ai_service.generate(db, "EXPLAIN", {"headline": "x", "facts": {}})
    assert p.calls == 3 and slept == [7.0, 7.0] and run.validation_ok


def test_a_request_that_can_never_fit_fails_fast_with_a_clear_message(monkeypatch):
    slept = []
    monkeypatch.setattr(ai_service.time, "sleep", lambda s: slept.append(s))
    p = _Flaky(99, {"too_large": True, "limit": 8000, "requested": 9500})
    registry.override("ai", p)
    with session_scope() as db, pytest.raises(ai_service.AIOutputInvalid) as e:
        ai_service.generate(db, "EXPLAIN", {"headline": "x", "facts": {}})
    assert p.calls == 1 and not slept and "rate_limited" in str(e.value.details)


def test_dashboard_records_and_shows_the_groq_plan_limit():
    def handler(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})
        return httpx.Response(200, headers={"x-ratelimit-limit-tokens": "8000", "x-ratelimit-limit-requests": "1000"},
                              json={"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"total_tokens": 9}})
    try:
        with session_scope() as db:
            svc.save(db, "groq", settings={"model": "a", "challenger_model": "b"}, secrets={"api_key": "gsk_fakefakefake"}, user_id=None)
            res = svc.test_connection(db, "groq", http=httpx.Client(transport=httpx.MockTransport(handler)))
            assert res["connected"] and "8000 tokens/min" in " ".join(s["detail"] for s in res["steps"])
            info = svc.serialize(db, "groq")["info"]
            assert info["rate_limits"] == {"a": {"tokens_per_minute": 8000, "requests_per_day": 1000}, "b": {"tokens_per_minute": 8000, "requests_per_day": 1000}}
    finally:
        with session_scope() as db:
            db.query(IntegrationConfig).delete()
        svc.invalidate()
