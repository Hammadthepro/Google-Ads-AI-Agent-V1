"""Provider adapters against recorded HTTP shapes (mocked transport only; no network): authentication, endpoint, request format,
response parsing, token usage, finish reasons, model discovery and the precise error kind of each failure."""
from __future__ import annotations

import json

import httpx
import pytest

from app.adapters.ai import providers as P
from app.adapters.base import AdapterError

KEY = "sk-test-0123456789abcdef"


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _client2(handler):
    """The anthropic SDK in this environment is built on httpx2 (same API)."""
    import httpx2
    return httpx2.Client(transport=httpx2.MockTransport(lambda req: _to2(handler(req))))


def _to2(resp: httpx.Response):
    import httpx2
    return httpx2.Response(resp.status_code, content=resp.content, headers=dict(resp.headers))


def _chat_ok(model="m", content='{"ok": true}', finish="stop", usage=None):
    return {"id": "x", "model": model, "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}],
            "usage": usage or {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16}}


WIRE = [("openai", "https://api.openai.com/v1", "max_completion_tokens", True),
        ("deepseek", "https://api.deepseek.com", "max_tokens", True),
        ("mistral", "https://api.mistral.ai/v1", "max_tokens", True),
        ("xai", "https://api.x.ai/v1", "max_tokens", True),
        ("openrouter", "https://openrouter.ai/api/v1", "max_tokens", False)]


@pytest.mark.parametrize("provider,base,token_param,json_mode", WIRE)
def test_openai_style_providers_send_their_own_request_and_parse_usage(provider, base, token_param, json_mode):
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers.get("authorization"), json.loads(req.content)
        return httpx.Response(200, json=_chat_ok("the-model"), headers={"x-ratelimit-limit-tokens": "30000", "x-ratelimit-remaining-tokens": "29000",
                                                                     "x-ratelimit-reset-tokens": "2s"})
    p = P.build(provider, KEY, "the-model", http=_client(handler))
    raw = p.complete("EXPLAIN", "system text", {"a": 1}, max_tokens=321)
    assert seen["url"] == f"{base}/chat/completions" and seen["auth"] == f"Bearer {KEY}"
    assert seen["body"][token_param] == 321 and seen["body"]["model"] == "the-model"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "system text"} and seen["body"]["messages"][1]["content"] == '{"a":1}'
    assert ("response_format" in seen["body"]) is json_mode
    assert (raw.text, raw.tokens_in, raw.tokens_out, raw.finish_reason) == ('{"ok": true}', 11, 5, "stop")
    assert KEY not in repr(p)


def test_openai_omits_temperature_for_reasoning_models_and_openrouter_reports_its_own_cost():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json=_chat_ok(usage={"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.00042}))
    P.build("openai", KEY, "o-model", http=_client(handler)).complete("EXPLAIN", "s", {})
    assert "temperature" not in seen
    raw = P.build("openrouter", KEY, "a/b", http=_client(handler), model_meta={"capabilities": {"structured_output": True}}).complete("EXPLAIN", "s", {})
    assert raw.cost_usd == 0.00042 and seen["response_format"] == {"type": "json_object"} and seen["usage"] == {"include": True}


def test_gemini_uses_header_auth_json_output_and_reports_truncation():
    seen = {}

    def handler(req):
        seen["url"], seen["key"], seen["body"] = str(req.url), req.headers.get("x-goog-api-key"), json.loads(req.content)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True}, {"text": '{"ok"'}]},
                                                         "finishReason": "MAX_TOKENS"}],
                                         "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 7, "thoughtsTokenCount": 3},
                                         "modelVersion": "gemini-x"})
    raw = P.build("gemini", KEY, "models/gemini-x", http=_client(handler)).complete("EXPLAIN", "sys", {"q": 1}, max_tokens=99)
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent" and KEY not in seen["url"]
    assert seen["key"] == KEY and seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert seen["body"]["generationConfig"]["maxOutputTokens"] == 99 and seen["body"]["systemInstruction"]["parts"][0]["text"] == "sys"
    assert (raw.text, raw.tokens_in, raw.tokens_out, raw.finish_reason) == ('{"ok"', 40, 10, "length")


def test_groq_and_anthropic_use_their_sdks_with_the_role_key():
    seen = []

    def handler(req):
        h = {k.lower(): v for k, v in req.headers.items()}
        seen.append((str(req.url), h.get("authorization"), h.get("x-api-key")))
        if "anthropic" in str(req.url):
            return httpx.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "claude-x",
                                             "content": [{"type": "text", "text": '{"ok": true}'}], "stop_reason": "max_tokens",
                                             "usage": {"input_tokens": 9, "output_tokens": 4}})
        return httpx.Response(200, json=_chat_ok("llama-x"))
    g = P.build("groq", KEY, "llama-x", http=_client(handler)).complete("EXPLAIN", "s", {}, max_tokens=50)
    a = P.build("anthropic", KEY, "claude-x", http=_client2(handler)).complete("EXPLAIN", "s", {}, max_tokens=50)
    assert seen[0][0] == "https://api.groq.com/openai/v1/chat/completions" and seen[0][1] == f"Bearer {KEY}"
    assert seen[1][0] == "https://api.anthropic.com/v1/messages" and KEY in (seen[1][2], (seen[1][1] or "").removeprefix("Bearer "))
    assert g.tokens_in == 11 and a.finish_reason == "length" and a.tokens_out == 4


# ------------------------------------------------------------------ discovery
def test_model_discovery_reports_only_provider_stated_capabilities():
    data = {
        "openai": {"data": [{"id": "gpt-x", "object": "model"}, {"id": "text-embedding-3-small"}, {"id": "whisper-1"}]},
        "mistral": {"data": [{"id": "mistral-large", "capabilities": {"completion_chat": True, "function_calling": True, "vision": False},
                              "max_context_length": 131072}, {"id": "mistral-embed", "capabilities": {"completion_chat": False}}]},
        "openrouter": {"data": [{"id": "a/b", "name": "B", "context_length": 200000, "supported_parameters": ["tools", "response_format"],
                                 "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
                                 "pricing": {"prompt": "0.000003", "completion": "0.000015"}},
                                {"id": "img/gen", "architecture": {"output_modalities": ["image"]}}]},
        "gemini": {"models": [{"name": "models/gemini-x", "displayName": "Gemini X", "inputTokenLimit": 1048576, "outputTokenLimit": 65536,
                               "supportedGenerationMethods": ["generateContent", "countTokens"]},
                              {"name": "models/embedding-001", "supportedGenerationMethods": ["embedContent"]}]},
        "groq": {"data": [{"id": "llama-x", "context_window": 131072, "active": True}, {"id": "whisper-large-v3", "active": True}]},
        "anthropic": {"data": [{"id": "claude-x", "display_name": "Claude X", "type": "model"}], "has_more": False},
    }
    for prov, payload in data.items():
        models = P.list_models(prov, KEY, http=_client(lambda req, p=payload: httpx.Response(200, json=p)))
        shown = [m for m in models if not m.hidden_reason]
        hidden = [m for m in models if m.hidden_reason]
        assert shown and (prov == "anthropic" or hidden), prov
    gpt = P.list_models("openai", KEY, http=_client(lambda r: httpx.Response(200, json=data["openai"])))[0]
    assert gpt.capabilities == {}  # OpenAI's listing states no capabilities: none are claimed
    orr = P.list_models("openrouter", KEY, http=_client(lambda r: httpx.Response(200, json=data["openrouter"])))[0]
    assert orr.capabilities == {"tools": True, "structured_output": True, "reasoning": False, "vision": True}
    assert orr.pricing == {"input": 3.0, "output": 15.0} and orr.context_window == 200000
    mis = P.list_models("mistral", KEY, http=_client(lambda r: httpx.Response(200, json=data["mistral"])))[0]
    assert mis.capabilities == {"tools": True, "vision": False, "chat": True}


# ------------------------------------------------------------------ errors
CASES = [
    (401, {"error": {"message": "Incorrect API key provided"}}, {}, "auth_failure"),
    (402, {"error": {"message": "Insufficient credits"}}, {}, "insufficient_credits"),
    (429, {"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}}, {}, "insufficient_credits"),
    (429, {"error": {"message": "Rate limit reached on tokens per minute (TPM)"}}, {"retry-after": "7"}, "tpm_exceeded"),
    (429, {"error": {"message": "Rate limit reached on requests per minute (RPM)"}}, {}, "rpm_exceeded"),
    (404, {"error": {"message": "The model `nope` does not exist", "code": "model_not_found"}}, {}, "unsupported_model"),
    (400, {"error": {"message": "This model's maximum context length is 8192 tokens"}}, {}, "context_length"),
    (400, {"error": {"message": "response_format json_object is not supported with this model"}}, {}, "unsupported_capability"),
    (413, {"error": {"message": "Request too large"}}, {}, "tpm_exceeded"),
    (503, {"error": {"message": "overloaded"}}, {}, "outage"),
]


@pytest.mark.parametrize("provider", ["openai", "deepseek", "mistral", "xai", "openrouter", "gemini"])
@pytest.mark.parametrize("status,body,headers,kind", CASES)
def test_every_failure_has_a_precise_kind(provider, status, body, headers, kind):
    p = P.build(provider, KEY, "nope", http=_client(lambda r: httpx.Response(status, json=body, headers=headers)))
    with pytest.raises(AdapterError) as e:
        p.complete("EXPLAIN", "s", {})
    assert e.value.details["error_kind"] == kind and KEY not in e.value.message
    if kind == "tpm_exceeded" and headers:
        assert e.value.details["retry_after"] == 7


def test_timeouts_and_network_errors_are_told_apart():
    def slow(req):
        raise httpx.ReadTimeout("slow", request=req)

    def down(req):
        raise httpx.ConnectError("refused", request=req)
    with pytest.raises(AdapterError) as e:
        P.build("openai", KEY, "m", http=_client(slow)).complete("EXPLAIN", "s", {})
    assert e.value.details["error_kind"] == "timeout"
    with pytest.raises(AdapterError) as e:
        P.build("openai", KEY, "m", http=_client(down)).complete("EXPLAIN", "s", {})
    assert e.value.details["error_kind"] == "network"


def test_anthropic_sdk_errors_use_the_same_kinds():
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": "Your credit balance is too low to access the Anthropic API."}}
    p = P.build("anthropic", KEY, "claude-x", http=_client2(lambda r: httpx.Response(400, json=body)))
    with pytest.raises(AdapterError) as e:
        p.complete("EXPLAIN", "s", {})
    assert e.value.details["error_kind"] == "insufficient_credits"
    p = P.build("anthropic", KEY, "claude-x", http=_client2(lambda r: httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})))
    with pytest.raises(AdapterError) as e:
        p.complete("EXPLAIN", "s", {})
    assert e.value.details["error_kind"] == "auth_failure"


def test_key_check_distinguishes_a_bad_key_from_an_outage():
    with pytest.raises(AdapterError) as e:
        P.check_key("deepseek", KEY, http=_client(lambda r: httpx.Response(401, json={"error": {"message": "Authentication Fails"}})))
    assert e.value.details["error_kind"] == "auth_failure"
    with pytest.raises(AdapterError) as e:
        P.check_key("deepseek", KEY, http=_client(lambda r: httpx.Response(503, text="down")))
    assert e.value.details["error_kind"] == "outage"
    P.check_key("openrouter", KEY, http=_client(lambda r: httpx.Response(200, json={"data": {"label": "k"}}) if r.url.path.endswith("/key") else httpx.Response(404)))
