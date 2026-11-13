"""Main AI / Challenger AI roles: encrypted keys that never leave the server, masking, revocation, the existing Groq setup adopted by
reference, workspace isolation, permissions, routing of every AI call through the roles, and honest "review not performed"."""
from __future__ import annotations

import httpx
import pytest

from app.adapters import registry
from app.adapters.ads_oauth import decrypt_token
from app.core.errors import IntegrationNotConfigured, ValidationFailed
from app.db.session import session_scope
from app.models import AIRoleConfig, AIRun, User, Workspace
from app.services import ai_roles, ai_service, challenger, integrations_service as svc
from tests.conftest import ANALYST, SUPER, Api
from tests.fakes.ai import MockAIProvider

KEY = "sk-proj-abcdefghijklmnop1234"


@pytest.fixture
def ws():
    """A fresh workspace per test (credentials never cross workspaces)."""
    import uuid
    with session_scope() as db:
        w = Workspace(name="AI roles test", slug=f"ai-{uuid.uuid4().hex[:10]}")
        db.add(w)
        db.flush()
        wid = w.id
    with svc.use_workspace(wid):
        ai_roles.invalidate()
        yield wid
    ai_roles.invalidate()


def _user(db, email=SUPER):
    return db.query(User).filter(User.email == email).one()


def _http(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_key_is_encrypted_at_rest_masked_and_never_returned(ws):
    with session_scope() as db:
        row = ai_roles.save(db, "main", {"provider": "openai", "model": "gpt-x", "api_key": KEY, "enabled": True}, _user(db))
        assert row.secrets_ciphertext and KEY not in row.secrets_ciphertext and decrypt_token(row.secrets_ciphertext) == KEY
        out = ai_roles.overview(db)
    text = str(out)
    assert KEY not in text and out["roles"]["main"]["key"] == {"present": True, "source": "saved", "fingerprint": row.key_fingerprint,
                                                                 "unreadable": False}
    assert KEY[-4:] not in row.key_fingerprint  # a hash prefix, not a slice of the key
    v = ai_roles.resolve("main")
    assert v["configured"] and v["api_key"] == KEY  # server side only


def test_a_key_is_never_sent_to_another_provider_and_can_be_revoked(ws):
    with session_scope() as db:
        u = _user(db)
        ai_roles.save(db, "main", {"provider": "openai", "model": "gpt-x", "api_key": KEY, "enabled": True}, u)
        row = ai_roles.save(db, "main", {"provider": "mistral", "model": "mistral-large", "enabled": False}, u)
        assert row.secrets_ciphertext == "" and row.status == "NOT_CONFIGURED"  # the OpenAI key was dropped, not reused for Mistral
        with pytest.raises(ValidationFailed):
            ai_roles.save(db, "main", {"enabled": True}, u)  # cannot turn on without a key
        ai_roles.save(db, "main", {"api_key": "mistral-key-1234567890", "enabled": True}, u)
        row = ai_roles.revoke(db, "main", u)
        assert row.secrets_ciphertext == "" and not row.enabled
    with pytest.raises(IntegrationNotConfigured):
        ai_roles.build_provider("main")


def test_existing_groq_settings_become_the_main_ai_by_reference(ws):
    with session_scope() as db:
        svc.save(db, "groq", settings={"model": "openai/gpt-oss-120b", "challenger_model": "qwen/qwen3.8-27b"},
                 secrets={"api_key": "gsk_existingkey1234567890"}, user_id=None)
    svc.invalidate()
    with session_scope() as db:
        out = ai_roles.overview(db)
        assert out["migrated_now"] == ["main", "challenger"]
        rows = {r.role: r for r in db.query(AIRoleConfig).filter(AIRoleConfig.workspace_id == ws)}
        assert rows["main"].provider == "groq" and rows["main"].model == "openai/gpt-oss-120b" and rows["main"].credential_ref == "integration:groq"
        assert rows["main"].secrets_ciphertext == ""  # the key was not copied
        assert out["roles"]["main"]["key"]["source"] == "groq_integration" and out["roles"]["main"]["source"] == "migrated_from_groq"
        assert out["roles"]["challenger"]["model"] == "qwen/qwen3.8-27b"
        assert out["independence"]["same_provider"] is True and "limited" in out["independence"]["note"]
        ai_roles.save(db, "main", {"model": "llama-x"}, _user(db))
    with session_scope() as db:
        assert ai_roles.overview(db)["migrated_now"] == []  # never re-run: what a person saved is never overwritten
        assert ai_roles._row(db, "main").model == "llama-x"
    p = ai_roles.build_provider("main")
    assert p.model == "llama-x" and type(p).__name__ == "GroqAIProvider"


def test_roles_are_isolated_per_workspace(ws):
    with session_scope() as db:
        ai_roles.save(db, "main", {"provider": "openai", "model": "gpt-x", "api_key": KEY, "enabled": True}, _user(db))
    import uuid
    with session_scope() as db:
        other = Workspace(name="Other team", slug=f"ai-{uuid.uuid4().hex[:10]}")
        db.add(other)
        db.flush()
        oid = other.id
    with svc.use_workspace(oid):
        ai_roles.invalidate()
        assert ai_roles.resolve("main")["configured"] is False
        with session_scope() as db:
            assert KEY not in str(ai_roles.overview(db)) and ai_roles.overview(db)["roles"]["main"]["provider"] == ""
    ai_roles.invalidate()
    assert ai_roles.resolve("main")["configured"] is True


def test_only_integration_managers_can_change_roles_and_no_response_contains_a_key():
    r = Api(ANALYST).put("/ai/roles/main", {"provider": "openai", "model": "x", "api_key": KEY})
    assert r.status_code == 403
    assert Api(ANALYST).get("/ai/roles").status_code == 200
    assert Api(ANALYST).get("/ai/usage").status_code == 403
    sup = Api(SUPER)
    r = sup.put("/ai/roles/challenger", {"provider": "deepseek", "model": "deepseek-chat", "api_key": "sk-deepseekkey12345678", "enabled": False})
    assert r.status_code == 200 and "sk-deepseekkey" not in r.text and r.json()["key"]["present"] is True
    assert "sk-deepseekkey" not in sup.get("/ai/roles").text
    sup.post("/ai/roles/challenger/revoke")


def test_connection_test_and_discovery_tell_a_bad_key_from_an_outage(ws):
    def ok(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-x"}, {"id": "text-embedding-3-large"}]})
        return httpx.Response(200, json={"model": "gpt-x", "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 20, "completion_tokens": 4}})
    with session_scope() as db:
        u = _user(db)
        ai_roles.save(db, "main", {"provider": "openai", "model": "gpt-x", "api_key": KEY}, u)
        found = ai_roles.discover(db, "main", {}, http=_http(ok))
        assert [m["id"] for m in found["models"]] == ["gpt-x"] and found["hidden"][0]["id"] == "text-embedding-3-large"
        res = ai_roles.test(db, "main", {}, u, http=_http(ok))
        assert res["ok"] and res["recorded"] and res["steps"][1]["json"] is True
        assert ai_roles._row(db, "main").status == "CONNECTED"
        bad = ai_roles.test(db, "main", {"api_key": "sk-wrongwrongwrong"}, u,
                            http=_http(lambda r: httpx.Response(401, json={"error": {"message": "Incorrect API key"}})))
        assert not bad["ok"] and bad["error"]["kind"] == "auth_failure" and bad["recorded"] is False
        assert ai_roles._row(db, "main").status == "CONNECTED"  # testing an unsaved key never changes the saved status
        down = ai_roles.discover(db, "main", {"refresh": True}, http=_http(lambda r: httpx.Response(503, text="x")))
        assert down["error"]["kind"] == "outage" and down["manual_entry"] is True
        manual = ai_roles.test(db, "main", {"model": "not-a-model"}, u,
                               http=_http(lambda r: ok(r) if r.url.path.endswith("/models") else httpx.Response(404, json={"error": {"message": "The model `not-a-model` does not exist", "code": "model_not_found"}})))
        assert manual["error"]["kind"] == "unsupported_model"


def test_every_ai_call_is_routed_by_role_and_recorded(ws):
    main, chal = MockAIProvider(), MockAIProvider()
    registry.override("ai", main)
    registry.override("challenger_ai", chal)
    with session_scope() as db:
        _, run = ai_service.generate(db, "EXPLAIN", {"headline": "x", "facts": {}})
        assert run.role == "main" and run.request_id and run.cost_usd is None and run.cost_source == ""
        _, run = ai_service.generate(db, "REVIEW_DIAGNOSIS", {"analysis": {}, "metrics": {}}, model_role="challenger")
        assert run.role == "challenger"


def test_cost_is_recorded_only_when_known(ws):
    p = MockAIProvider()
    p.pricing = {"input": 2.0, "output": 8.0}
    registry.override("ai", p)
    with session_scope() as db:
        _, run = ai_service.generate(db, "EXPLAIN", {"headline": "x", "facts": {}})
        expected = round((run.tokens_in * 2.0 + run.tokens_out * 8.0) / 1e6, 6)
        assert run.cost_usd == expected and run.cost_source == "configured_price"


def test_disabled_challenger_is_recorded_as_not_performed(ws, monkeypatch):
    registry._overrides.pop("challenger_ai", None)  # the real gate: no challenger configured in this workspace
    ai_roles.invalidate()
    with session_scope() as db:
        report, run_id = challenger.review_draft(db, {"campaign_name": "x"}, {}, kind="campaign_draft", task_id=None)
    assert report["performed"] is False and run_id is None and "not configured" in report["reason"].lower()
    assert report["verdict"] is None and report["independence"] == "not performed"


def test_challenger_review_carries_evidence_confidence_and_cannot_approve(ws):
    registry.override("challenger_ai", MockAIProvider())
    with session_scope() as db:
        report, run_id = challenger.review_draft(db, {"campaign_name": "x", "ad_groups": []}, {"verified_claims": []},
                                                 kind="campaign_draft", task_id=None)
        assert report["performed"] is True and run_id and db.get(AIRun, run_id).role == "challenger"
    assert {"verdict", "objections", "confidence", "limitations", "independence"} <= set(report)
    # the review is data: there is no field or code path through which it approves or executes anything
    assert "approval_id" not in report and "operations" not in report


def test_review_policy_can_turn_a_kind_of_review_off(ws):
    registry.override("challenger_ai", MockAIProvider())
    with session_scope() as db:
        u = _user(db)
        ai_roles.save(db, "challenger", {"provider": "openai", "model": "gpt-x", "api_key": KEY, "enabled": True,
                                         "review_policy": {"campaign_draft": False}}, u)
    with session_scope() as db:  # (the API commits a save before anything reads it)
        report, _ = challenger.review_draft(db, {"campaign_name": "x"}, {}, kind="campaign_draft", task_id=None)
    assert report["performed"] is False and "turned off" in report["reason"]


def test_usage_view_groups_by_role_provider_and_model_without_inventing_costs():
    import uuid as _u
    tag = f"usage-{_u.uuid4().hex[:6]}"
    with session_scope() as db:
        for i, (role, cost) in enumerate((("main", None), ("main", 0.002), ("challenger", None))):
            db.add(AIRun(purpose="EXPLAIN", provider=tag, model="m-x", prompt_template_id="explain", prompt_version="1", input_hash="h",
                         role=role, tokens_in=100, tokens_out=20, latency_ms=1000, validation_ok=i != 2, error_kind="" if i != 2 else "rate_limited",
                         cost_usd=cost, cost_source="provider" if cost else ""))
        db.add(AIRun(purpose="DRAFT_CAMPAIGN", provider=tag, model="m-x", prompt_template_id="d", prompt_version="1", input_hash="h", role="main",
                     tokens_in=9999, tokens_out=9999, provider_status="assembled", validation_ok=True))
    data = Api(SUPER).get("/ai/usage?days=1").json()
    rows = {r["role"]: r for r in data["models"] if r["provider"] == tag}
    assert rows["main"]["requests"] == 2 and rows["main"]["input_tokens"] == 200  # the assembled summary is not double-counted
    assert rows["main"]["cost_usd"] == 0.002 and rows["main"]["cost_note"] == "known for 1 of 2 requests"
    assert rows["challenger"]["cost_usd"] is None and rows["challenger"]["errors"] == {"rate_limited": 1} and rows["challenger"]["failed"] == 1
