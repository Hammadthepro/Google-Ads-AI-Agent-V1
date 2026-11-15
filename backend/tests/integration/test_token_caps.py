"""Manual application token caps per AI role: validated on save, enforced by the backend before anything is sent, reserved in an
atomic ledger (concurrent requests cannot overspend), settled to real usage, separate from the provider's own limits, and reported
per client, day and month."""
from __future__ import annotations

import threading
import uuid

import pytest

from app.adapters import registry
from app.adapters.ai import AIProvider, RawCompletion
from app.core.errors import ValidationFailed
from app.db.session import session_scope
from app.models import AIRoleConfig, AIRun, User
from app.services import ai_roles, ai_service, token_caps
from app.services import integrations_service as svc
from tests.conftest import ANALYST, SUPER, Api
from tests.integration.test_ai_roles import ws  # noqa: F401  (fixture)

KEY = "sk-proj-abcdefghijklmnop1234"


class Counting(AIProvider):
    name, source, supports_max_tokens, MAX_TOKENS = "Counting", "TEST", True, 6000
    model = challenger_model = "count-1"

    def __init__(self, tin=400, tout=100):
        self.sent, self.tin, self.tout = [], tin, tout

    def complete(self, purpose, system, user, *, model_role="draft", max_tokens=None) -> RawCompletion:
        self.sent.append(max_tokens)
        return RawCompletion(text='{"summary": "ok"}', model="count-1", tokens_in=self.tin, tokens_out=self.tout, finish_reason="stop")


def _caps(ws_id, role="main", **caps):
    with session_scope() as db:
        u = db.query(User).filter(User.email == SUPER).one()
        ai_roles.save(db, role, {"provider": "openai", "model": "gpt-x", "api_key": KEY, "enabled": True, "caps": caps}, u)
    ai_roles.invalidate()


def _explain(client_id=None, task_id=None, **kw):
    with session_scope() as db:
        return ai_service.generate(db, "EXPLAIN", {"q": "why"}, client_id=client_id, task_id=task_id, **kw)


def test_caps_are_validated_against_each_other_and_the_model(ws):  # noqa: F811
    v = token_caps.validate
    assert v({"max_output_tokens": "2,000", "client_daily_tokens": ""}) == {"max_output_tokens": 2000}
    for bad, why in (({"max_output_tokens": 100}, "below"), ({"max_input_tokens": -1}, "between"), ({"nope": 1}, "Unknown"),
                     ({"max_input_tokens": 5000, "max_total_tokens": 4000}, "larger than max input"),
                     ({"client_daily_tokens": 10_000, "client_monthly_tokens": 5_000}, "larger than"),
                     ({"max_total_tokens": 8000, "task_budget_tokens": 1000}, "every request would be refused"),
                     ({"max_input_tokens": "lots"}, "whole number")):
        with pytest.raises(ValidationFailed, match=why):
            v(bad)
    with pytest.raises(ValidationFailed, match="model's own maximum output"):
        v({"max_output_tokens": 9000}, {"max_output": 8192})
    with pytest.raises(ValidationFailed, match="context window"):
        v({"max_input_tokens": 200_000}, {"context_window": 128_000})


def test_per_request_caps_limit_the_output_and_refuse_oversized_input_without_sending(ws):  # noqa: F811
    p = Counting()
    registry.override("ai", p)
    _caps(ws, max_output_tokens=1000)
    _explain()
    assert p.sent == [1000]  # the answer is capped at the application limit, not the adapter's 6,000 default
    _caps(ws, max_input_tokens=20)
    with session_scope() as db:
        with pytest.raises(ai_service.TokenCapExceeded, match="application cap is 20 per request. Nothing was sent") as e:
            ai_service.generate(db, "EXPLAIN", {"q": "why"})
        assert e.value.details["cap"] == "max_input_tokens" and len(p.sent) == 1
        r = db.query(AIRun).filter(AIRun.error_kind == "app_cap_exceeded").order_by(AIRun.created_at.desc()).first()
        assert r.provider_status == "not_sent" and not r.tokens_in and "Nothing was sent" in r.error


def test_daily_client_budget_is_reserved_then_settled_to_real_usage(ws):  # noqa: F811
    p = Counting(tin=400, tout=100)
    registry.override("ai", p)
    client = uuid.uuid4().hex
    _caps(ws, max_output_tokens=1000, client_daily_tokens=2500)
    _explain(client_id=client)
    used = token_caps.usage(ws, "main", client)
    assert used["client_day"] == 500  # settled to the 400 + 100 the provider reported, not the ~1,000+ reserved
    _explain(client_id=client)
    _explain(client_id=client)
    with pytest.raises(ai_service.TokenCapExceeded, match="daily token budget has 1,000 of 2,500 left"):
        _explain(client_id=client)  # a 4th request could use ~1,0xx tokens: refused before sending
    assert len(p.sent) == 3
    other = uuid.uuid4().hex
    _explain(client_id=other)  # budgets are per client
    with session_scope() as db:
        assert db.query(AIRun).filter(AIRun.client_id == client, AIRun.validation_ok.is_(True)).count() == 3


def test_concurrent_requests_cannot_overspend_a_budget(ws):  # noqa: F811
    client = uuid.uuid4().hex
    p = token_caps.Plan(max_output=None, reserve=1000, buckets=[("client_day", client, "2026-11-14", 5000)])
    ok, refused = [], []

    def go():
        try:
            token_caps.reserve(ws, "main", p)
            ok.append(1)
        except token_caps.CapExceeded:
            refused.append(1)
    threads = [threading.Thread(target=go) for _ in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(ok) == 5 and len(refused) == 7
    with session_scope() as db:
        from app.models import AITokenLedger
        assert db.query(AITokenLedger).filter_by(workspace_id=ws, scope_key=client).one().used == 5000


def test_caps_are_per_role_and_the_challenger_has_its_own(ws):  # noqa: F811
    main, chal = Counting(), Counting()
    registry.override("ai", main)
    registry.override("challenger_ai", chal)
    _caps(ws, "challenger", max_output_tokens=500)
    _explain()
    _explain(model_role="challenger")
    assert main.sent == [None] and chal.sent == [500]


def test_caps_round_trip_through_the_api_with_provider_limits_shown_separately(ws):  # noqa: F811
    sup = Api(SUPER)
    with svc.use_workspace(ws):
        pass
    r = sup.put("/ai/roles/main", {"provider": "openai", "model": "gpt-x", "api_key": KEY, "caps": {"max_output_tokens": 3000, "client_monthly_tokens": 900000}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["caps"] == {"max_output_tokens": 3000, "client_monthly_tokens": 900000}
    assert set(body["provider_limits"]) == {"context_window", "max_output", "tokens_per_minute"} and "max_input_tokens" in body["cap_fields"]
    assert sup.put("/ai/roles/main", {"caps": {"max_output_tokens": 10}}).status_code == 422
    assert Api(ANALYST).put("/ai/roles/main", {"caps": {"max_output_tokens": 3000}}).status_code == 403
    sup.put("/ai/roles/main", {"caps": {}})
    u = sup.get("/ai/usage?days=30").json()
    assert {"clients", "daily", "monthly"} <= set(u) and all("by_role" in c for c in u["clients"])
    with session_scope() as db:  # the API path saved to the default workspace; leave nothing behind
        for row in db.query(AIRoleConfig).filter(AIRoleConfig.role == "main", AIRoleConfig.provider == "openai", AIRoleConfig.model == "gpt-x"):
            row.caps = {}
