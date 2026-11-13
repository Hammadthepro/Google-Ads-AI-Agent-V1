"""ClaudeAIProvider — the independent second AI, via the official `anthropic` SDK.

Returns raw text only; ai_service validates it against the same strict schemas as the internal AI.
Server-side refusal fallback (`fallbacks: "default"`) is requested for Opus 5.x models, so a classifier
decline is retried by Anthropic on a recommended model inside the same call.
"""
from __future__ import annotations

import time

from app.adapters.ai import AIProvider, RawCompletion, serialize
from app.adapters.base import AdapterTimeout, AuthError, InvalidInput, ServiceUnavailable

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeAIProvider(AIProvider):
    name = "ClaudeAIProvider"
    source = "LIVE"
    supports_max_tokens = True
    MAX_TOKENS = 8000

    def __init__(self, api_key: str, model: str, client=None, *, http=None):
        if not api_key or not model:
            raise AuthError("Claude API key and model must be configured", adapter=self.name)
        self.model = self.challenger_model = model
        self.pricing = None
        if client is None:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key, timeout=120.0, max_retries=0, **({"http_client": http} if http is not None else {}))
        self.client = client

    def __repr__(self) -> str:
        return f"<ClaudeAIProvider model={self.model}>"

    def complete(self, purpose, system, user, *, model_role="draft", max_tokens: int | None = None) -> RawCompletion:
        import anthropic
        started = time.monotonic()
        kwargs = dict(model=self.model, max_tokens=int(max_tokens or self.MAX_TOKENS), system=system,
                      messages=[{"role": "user", "content": serialize(user)}])
        try:
            if self.model.startswith("claude-opus-5"):
                resp = self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
            else:
                resp = self.client.messages.create(**kwargs)
        except anthropic.APITimeoutError as exc:
            raise AdapterTimeout("Claude did not answer in time", adapter=self.name, details={"error_kind": "timeout"}) from exc
        except anthropic.APIConnectionError as exc:
            raise ServiceUnavailable("Could not reach the Claude API", adapter=self.name, details={"error_kind": "network"}) from exc
        except anthropic.APIStatusError as exc:  # one mapping for every provider (providers.classify): auth, credits, rate, context, model, outage
            from app.adapters.ai.providers import classify
            raise classify("anthropic", exc.status_code, getattr(exc, "body", None), str(getattr(exc, "message", "")),
                           getattr(getattr(exc, "response", None), "headers", {}) or {}, self.model) from exc
        if getattr(resp, "stop_reason", None) == "refusal":
            raise InvalidInput("Claude declined this request (refusal)", adapter=self.name)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        usage = getattr(resp, "usage", None)
        stop = getattr(resp, "stop_reason", "") or ""
        return RawCompletion(text=text, model=getattr(resp, "model", self.model),
                             tokens_in=getattr(usage, "input_tokens", 0) or 0, tokens_out=getattr(usage, "output_tokens", 0) or 0,
                             latency_ms=int((time.monotonic() - started) * 1000),
                             finish_reason="length" if stop == "max_tokens" else stop, status="ok")
