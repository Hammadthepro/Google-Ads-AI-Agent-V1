"""AIProvider interface and GroqAIProvider (official Groq SDK).

Providers only return raw text. Parsing, schema validation, retries and AIRun recording happen in
services/ai_service.py so both providers are held to the same rules.
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.adapters.base import (AdapterTimeout, AuthError, ContextLengthExceeded, InvalidInput, InvalidResponse, RateLimited,
                               ServiceUnavailable)


def serialize(user: dict) -> str:
    """The user message exactly as sent: compact JSON (no indentation or spaces after separators)."""
    return json.dumps(user, default=str, separators=(",", ":"), ensure_ascii=False)


@dataclass
class RawCompletion:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    finish_reason: str = ""      # "stop", or "length" when the output hit max_tokens (then the JSON is cut off)
    status: str = "ok"           # provider status for the record


class AIProvider(ABC):
    name = "ai"
    source = "LIVE"
    tpm_limit: int | None = None          # tokens per minute for a request, when the provider has such a limit and it is known
    supports_max_tokens = False           # complete() accepts max_tokens

    @abstractmethod
    def complete(self, purpose: str, system: str, user: dict, *, model_role: str = "draft") -> RawCompletion: ...

    def rate_wait(self, needed_tokens: int) -> tuple[float, str]:
        """Seconds to wait before a request of `needed_tokens` prompt tokens fits the provider's current window (0 = send now),
        computed only from the provider's own rate-limit metadata. Providers without such metadata never wait."""
        return 0.0, ""


# =====================================================================================================
# Groq provider
# =====================================================================================================
def _duration(v) -> float:
    """Groq reset durations: "9.42s", "1m3.2s", "250ms"."""
    import re as _re
    if v in (None, ""):
        return 0.0
    m = _re.fullmatch(r"(?:(\d+)m(?!s))?(?:([\d.]+)s)?(?:([\d.]+)ms)?", str(v).strip())
    if not m:
        try:
            return float(v)
        except ValueError:
            return 0.0
    return float(m.group(1) or 0) * 60 + float(m.group(2) or 0) + float(m.group(3) or 0) / 1000


def _groq_rate_limit(exc, adapter: str, model: str, *, status: int = 429) -> RateLimited:
    """Tell a transient limit (wait `retry_after`) from a request that can NEVER fit the plan's tokens-per-minute limit.
    Groq answers 429 when the minute's budget is used up and 413 when one request alone is larger than the per-minute limit."""
    import re as _re
    text = str(getattr(exc, "message", "") or exc)
    headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
    try:
        retry_after = float(headers.get("retry-after", 0) or 0)
    except ValueError:
        retry_after = 0.0
    if not retry_after:  # Groq also states the wait in the message: "Please try again in 52.73s" / "in 1m3.2s"
        w = _re.search(r"try again in\s+(?:(\d+)m)?([\d.]+)s", text)
        if w:
            retry_after = float(w.group(1) or 0) * 60 + float(w.group(2))
    m = _re.search(r"Limit\s+(\d+).*?Requested\s+(\d+)", text, _re.S)
    limit, requested = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    if not limit:
        try:
            limit = int(headers.get("x-ratelimit-limit-tokens") or 0)
        except ValueError:
            limit = 0
    too_large = bool(limit and requested > limit) or status == 413
    msg = (f"The request needs ~{requested:,} tokens but {model} allows {limit:,} tokens per minute on this Groq plan" if too_large and requested and limit else
           f"The request is larger than {model}'s per-minute token limit on this Groq plan (HTTP 413)" if too_large else
           f"Groq's per-minute token budget for {model} is used up" + (f" (Groq asks to retry in {retry_after:.0f}s)" if retry_after else ""))
    return RateLimited(msg, adapter=adapter, details={"retry_after": retry_after, "too_large": too_large, "limit": limit, "requested": requested,
                                                      "model": model, "status": status,
                                                      "error_kind": "tpm_exceeded" if too_large else "rate_limited"})


class GroqAIProvider(AIProvider):
    """Uses the official `groq` SDK with JSON-object mode. Model names come from configuration only.
    Every response's x-ratelimit-* headers are kept, so callers can size and pace requests from Groq's own numbers."""

    name = "GroqAIProvider"
    source = "LIVE"
    MAX_TOKENS = 6000
    supports_max_tokens = True
    _learned_limits: dict[str, int] = {}     # model -> tokens per minute, as stated by Groq (shared by every instance)

    def __init__(self, client=None):
        from app.core.config import get_settings
        from app.services import integrations_service
        v = integrations_service.resolve("groq")
        if not v.get("model"):
            raise AuthError("Groq model is not configured", adapter=self.name)
        self.model = v["model"]
        self.challenger_model = v.get("challenger_model") or v["model"]
        self._configured_tpm = int(get_settings().GROQ_TPM_LIMIT or 0)
        self.rate: dict = {}
        if client is None:
            if not v.get("api_key"):
                raise AuthError("Groq API key is not configured", adapter=self.name)
            from groq import Groq
            client = Groq(api_key=v["api_key"], timeout=60, max_retries=0)
        self.client = client

    @property
    def tpm_limit(self) -> int | None:  # type: ignore[override]
        return self._configured_tpm or self._learned_limits.get(self.model) or None

    def learn_limit(self, model: str, limit: int) -> None:
        if limit > 0:
            self._learned_limits[model] = limit

    def _note_headers(self, model: str, headers) -> None:
        try:
            limit = int(headers.get("x-ratelimit-limit-tokens") or 0)
            remaining = headers.get("x-ratelimit-remaining-tokens")
            if limit:
                self.learn_limit(model, limit)
            if remaining is not None:
                self.rate[model] = {"limit": limit, "remaining": int(remaining), "reset": _duration(headers.get("x-ratelimit-reset-tokens")),
                                    "at": time.monotonic()}
        except (TypeError, ValueError):
            pass

    def rate_wait(self, needed_tokens: int, model_role: str = "draft") -> tuple[float, str]:
        model = self.challenger_model if model_role == "challenger" else self.model
        r = self.rate.get(model)
        if not r:
            return 0.0, ""
        elapsed = time.monotonic() - r["at"]
        limit = r["limit"] or self.tpm_limit or 0
        # Groq refills the bucket to the full limit over `reset` seconds; assume linear refill (never more than the limit)
        remaining = r["remaining"] if not (limit and r["reset"] > 0) else min(limit, r["remaining"] + (limit - r["remaining"]) * elapsed / r["reset"])
        if remaining >= needed_tokens:
            return 0.0, ""
        wait = max(0.0, r["reset"] - elapsed) + 0.5
        return wait, f"needs ~{needed_tokens:,} tokens, about {int(remaining):,} of the {limit:,}/minute are left (Groq: full reset in {r['reset']:.0f}s)"

    def complete(self, purpose, system, user, *, model_role="draft", max_tokens: int | None = None) -> RawCompletion:
        import groq
        model = self.challenger_model if model_role == "challenger" else self.model
        out_budget = int(max_tokens or self.MAX_TOKENS)
        started = time.monotonic()
        try:
            args = dict(model=model, temperature=0.2, max_tokens=out_budget, response_format={"type": "json_object"},
                        messages=[{"role": "system", "content": system}, {"role": "user", "content": serialize(user)}])
            comp = self.client.chat.completions
            if hasattr(comp, "with_raw_response"):  # the SDK: keep Groq's rate-limit headers
                raw = comp.with_raw_response.create(**args)
                self._note_headers(model, raw.headers)
                resp = raw.parse()
            else:  # an injected client without raw access (tests): no header metadata
                resp = comp.create(**args)
        except groq.AuthenticationError as exc:
            raise AuthError("Groq authentication failed", adapter=self.name) from exc
        except groq.RateLimitError as exc:
            self._note_headers(model, getattr(getattr(exc, "response", None), "headers", {}) or {})
            err = _groq_rate_limit(exc, self.name, model)
            self.learn_limit(model, err.details.get("limit") or 0)
            raise err from exc
        except groq.APITimeoutError as exc:
            raise AdapterTimeout("Groq request timed out", adapter=self.name) from exc
        except groq.APIConnectionError as exc:
            raise ServiceUnavailable("Groq API unreachable", adapter=self.name) from exc
        except groq.APIStatusError as exc:
            if exc.status_code == 413:  # one request larger than the plan's tokens-per-minute limit: retrying the same request cannot help
                err = _groq_rate_limit(exc, self.name, model, status=413)
                self.learn_limit(model, err.details.get("limit") or 0)
                raise err from exc
            body = exc.body if isinstance(getattr(exc, "body", None), dict) else {}
            err = body.get("error") if isinstance(body.get("error"), dict) else body
            code, text = err.get("code"), str(err.get("message") or getattr(exc, "message", exc))
            if exc.status_code == 400 and code == "json_validate_failed":
                # The MODEL's output was not valid JSON. Groq does not say why; the usual cause on long drafts is the output limit.
                raise InvalidResponse(f"{model} returned malformed JSON (Groq json_validate_failed; the output may have been cut off at "
                                      f"{out_budget:,} tokens)", adapter=self.name,
                                      details={"code": code, "model": model, "max_tokens": out_budget, "error_kind": "malformed_json"}) from exc
            if exc.status_code == 400 and (code == "context_length_exceeded" or "context length" in text.lower() or "context window" in text.lower()):
                raise ContextLengthExceeded(f"The request is longer than {model}'s context window", adapter=self.name,
                                            details={"model": model, "error_kind": "context_length"}) from exc
            if exc.status_code in (400, 422):
                raise InvalidInput(f"Groq rejected the request (HTTP {exc.status_code}): {text[:200]}", adapter=self.name,
                                   details={"status": exc.status_code, "code": code}) from exc
            raise ServiceUnavailable(f"Groq API error {exc.status_code}", adapter=self.name, details={"status": exc.status_code}) from exc
        usage = getattr(resp, "usage", None)
        choice = resp.choices[0]
        return RawCompletion(text=choice.message.content or "", model=model,
                             tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
                             tokens_out=getattr(usage, "completion_tokens", 0) or 0,
                             latency_ms=int((time.monotonic() - started) * 1000),
                             finish_reason=getattr(choice, "finish_reason", "") or "", status="ok")
