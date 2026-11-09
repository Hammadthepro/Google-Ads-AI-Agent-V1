"""AIProvider interface and GroqAIProvider (official Groq SDK).

Providers only return raw text. Parsing, schema validation, retries and AIRun recording happen in
services/ai_service.py so both providers are held to the same rules.
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.adapters.base import AdapterTimeout, AuthError, RateLimited, ServiceUnavailable


@dataclass
class RawCompletion:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0


class AIProvider(ABC):
    name = "ai"
    source = "LIVE"

    @abstractmethod
    def complete(self, purpose: str, system: str, user: dict, *, model_role: str = "draft") -> RawCompletion: ...


# =====================================================================================================
# Groq provider
# =====================================================================================================
def _groq_rate_limit(exc, adapter: str, model: str) -> RateLimited:
    """Tell a transient limit (wait `retry_after`) from a request that can NEVER fit the plan's tokens-per-minute limit."""
    import re as _re
    text = str(getattr(exc, "message", "") or exc)
    headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
    try:
        retry_after = float(headers.get("retry-after", 0) or 0)
    except ValueError:
        retry_after = 0.0
    m = _re.search(r"Limit\s+(\d+).*?Requested\s+(\d+)", text, _re.S)
    limit, requested = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    too_large = bool(limit and requested > limit)
    msg = (f"Groq rejected the request: it needs ~{requested} tokens but {model} allows {limit} per minute on this plan. Use a smaller prompt or a higher Groq tier."
           if too_large else "Groq rate limit reached")
    return RateLimited(msg, adapter=adapter, details={"retry_after": retry_after, "too_large": too_large, "limit": limit, "requested": requested, "model": model})


class GroqAIProvider(AIProvider):
    """Uses the official `groq` SDK with JSON-object mode. Model names come from configuration only."""

    name = "GroqAIProvider"
    source = "LIVE"

    def __init__(self, client=None):
        from app.services import integrations_service
        v = integrations_service.resolve("groq")
        if not v.get("model"):
            raise AuthError("Groq model is not configured", adapter=self.name)
        self.model = v["model"]
        self.challenger_model = v.get("challenger_model") or v["model"]
        if client is None:
            if not v.get("api_key"):
                raise AuthError("Groq API key is not configured", adapter=self.name)
            from groq import Groq
            client = Groq(api_key=v["api_key"], timeout=60, max_retries=0)
        self.client = client

    def complete(self, purpose, system, user, *, model_role="draft") -> RawCompletion:
        import groq
        model = self.challenger_model if model_role == "challenger" else self.model
        started = time.monotonic()
        try:
            resp = self.client.chat.completions.create(
                model=model, temperature=0.2, max_tokens=6000,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": json.dumps(user, default=str)}])
        except groq.AuthenticationError as exc:
            raise AuthError("Groq authentication failed", adapter=self.name) from exc
        except groq.RateLimitError as exc:
            raise _groq_rate_limit(exc, self.name, model) from exc
        except groq.APITimeoutError as exc:
            raise AdapterTimeout("Groq request timed out", adapter=self.name) from exc
        except groq.APIConnectionError as exc:
            raise ServiceUnavailable("Groq API unreachable", adapter=self.name) from exc
        except groq.APIStatusError as exc:
            raise ServiceUnavailable(f"Groq API error {exc.status_code}", adapter=self.name) from exc
        usage = getattr(resp, "usage", None)
        return RawCompletion(text=resp.choices[0].message.content or "", model=model,
                             tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
                             tokens_out=getattr(usage, "completion_tokens", 0) or 0,
                             latency_ms=int((time.monotonic() - started) * 1000))
