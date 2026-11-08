"""ClaudeAIProvider — the independent second AI, via the official `anthropic` SDK.

Returns raw text only; ai_service validates it against the same strict schemas as the internal AI.
Server-side refusal fallback (`fallbacks: "default"`) is requested for Opus 5.x models, so a classifier
decline is retried by Anthropic on a recommended model inside the same call.
"""
from __future__ import annotations

import json
import time

from app.adapters.ai import AIProvider, RawCompletion
from app.adapters.base import AdapterTimeout, AuthError, InvalidInput, PermissionDenied, RateLimited, ServiceUnavailable

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeAIProvider(AIProvider):
    name = "ClaudeAIProvider"
    source = "LIVE"

    def __init__(self, api_key: str, model: str, client=None):
        if not api_key or not model:
            raise AuthError("Claude API key and model must be configured", adapter=self.name)
        self.model = model
        if client is None:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key, timeout=120.0, max_retries=1)
        self.client = client

    def complete(self, purpose, system, user, *, model_role="draft") -> RawCompletion:
        import anthropic
        started = time.monotonic()
        kwargs = dict(model=self.model, max_tokens=8000, system=system,
                      messages=[{"role": "user", "content": json.dumps(user, default=str)}])
        try:
            if self.model.startswith("claude-opus-5"):
                resp = self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
            else:
                resp = self.client.messages.create(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise AuthError("Claude authentication failed", adapter=self.name) from exc
        except anthropic.PermissionDeniedError as exc:
            raise PermissionDenied("Claude API key lacks permission", adapter=self.name) from exc
        except anthropic.NotFoundError as exc:
            raise InvalidInput(f"Claude model '{self.model}' not found", adapter=self.name) from exc
        except anthropic.BadRequestError as exc:
            raise InvalidInput(f"Claude rejected the request: {getattr(exc, 'message', exc)}"[:400], adapter=self.name) from exc
        except anthropic.RateLimitError as exc:
            raise RateLimited("Claude rate limit reached", adapter=self.name) from exc
        except anthropic.APITimeoutError as exc:
            raise AdapterTimeout("Claude request timed out", adapter=self.name) from exc
        except anthropic.APIConnectionError as exc:
            raise ServiceUnavailable("Claude API unreachable", adapter=self.name) from exc
        except anthropic.APIStatusError as exc:
            raise ServiceUnavailable(f"Claude API error {exc.status_code}", adapter=self.name) from exc
        if getattr(resp, "stop_reason", None) == "refusal":
            raise InvalidInput("Claude declined this request (refusal)", adapter=self.name)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        usage = getattr(resp, "usage", None)
        return RawCompletion(text=text, model=getattr(resp, "model", self.model),
                             tokens_in=getattr(usage, "input_tokens", 0) or 0, tokens_out=getattr(usage, "output_tokens", 0) or 0,
                             latency_ms=int((time.monotonic() - started) * 1000))
