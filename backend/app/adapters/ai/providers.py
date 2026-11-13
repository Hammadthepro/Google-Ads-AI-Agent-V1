"""AI provider adapters for the Main AI and Challenger AI roles.

Each provider is described by a spec (endpoint, authentication, discovery support, API-level features as the provider documents them)
and implemented by an adapter that speaks that provider's real API:

  groq        official groq SDK            (GroqAIProvider in __init__.py)
  anthropic   official anthropic SDK       (ClaudeAIProvider in claude.py)
  gemini      Gemini REST generateContent  (GeminiProvider)
  openai, deepseek, xai, mistral, openrouter
              their OpenAI-style Chat Completions endpoints, each with its own base URL, token parameter, JSON-mode support,
              model listing and error mapping (OpenAIWireProvider subclasses)

Adapters return raw text plus usage; ai_service parses and validates. Model capabilities are reported only when the provider's own model
metadata states them ("unknown" otherwise): nothing here claims tools, vision, structured output or reasoning support on a guess.
Keys are only ever sent to the provider they belong to, in a header, never in a URL or a log line.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import httpx

from app.adapters.ai import AIProvider, RawCompletion, serialize
from app.adapters.base import (AdapterError, AdapterTimeout, AuthError, ContextLengthExceeded, InvalidInput, InvalidResponse, NotFoundError,
                               PermissionDenied, QuotaExceeded, RateLimited, ServiceUnavailable, Unsupported)


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    label: str
    base_url: str
    docs: str
    key_hint: str                       # what a key looks like (format hint only)
    discovery: bool                     # the provider has a model-listing endpoint
    api_features: tuple[str, ...]       # provider-level API features as documented (not a per-model guarantee)
    console: str = ""                   # where keys are created


SPECS: dict[str, ProviderSpec] = {s.key: s for s in (
    ProviderSpec("openai", "OpenAI", "https://api.openai.com/v1", "https://platform.openai.com/docs/api-reference/chat", "sk-…", True,
                 ("JSON mode", "function calling"), "https://platform.openai.com/api-keys"),
    ProviderSpec("anthropic", "Anthropic (Claude)", "https://api.anthropic.com/v1", "https://docs.anthropic.com/en/api/messages", "sk-ant-…", True,
                 ("tool use",), "https://console.anthropic.com/settings/keys"),
    ProviderSpec("groq", "Groq", "https://api.groq.com/openai/v1", "https://console.groq.com/docs/api-reference", "gsk_…", True,
                 ("JSON mode", "tool use"), "https://console.groq.com/keys"),
    ProviderSpec("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta", "https://ai.google.dev/api/generate-content",
                 "AIza…", True, ("JSON output", "function calling"), "https://aistudio.google.com/app/apikey"),
    ProviderSpec("mistral", "Mistral", "https://api.mistral.ai/v1", "https://docs.mistral.ai/api/", "(32 characters)", True,
                 ("JSON mode", "function calling"), "https://console.mistral.ai/api-keys"),
    ProviderSpec("deepseek", "DeepSeek", "https://api.deepseek.com", "https://api-docs.deepseek.com/", "sk-…", True,
                 ("JSON output", "function calling"), "https://platform.deepseek.com/api_keys"),
    ProviderSpec("xai", "xAI (Grok)", "https://api.x.ai/v1", "https://docs.x.ai/docs/api-reference", "xai-…", True,
                 ("structured outputs", "function calling"), "https://console.x.ai/"),
    ProviderSpec("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "https://openrouter.ai/docs/api-reference/overview", "sk-or-…", True,
                 ("per-model parameters (from model metadata)",), "https://openrouter.ai/settings/keys"),
)}

# Model families that are not chat/text-generation models. Hidden from the picker (with the reason) for providers whose listing gives no
# capability data; a hidden model can still be entered manually and is then checked with a real request.
NON_CHAT = re.compile(r"(embed|tts|whisper|dall-e|moderation|transcribe|realtime|audio|image|davinci|babbage|sora|playai|speech)", re.I)
TIMEOUT = httpx.Timeout(90.0, connect=10.0)


# --------------------------------------------------------------------------------------------- errors
def _retry_after(headers, text: str) -> float:
    for h in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        v = (headers or {}).get(h)
        if v:
            from app.adapters.ai import _duration
            secs = _duration(v)
            if secs:
                return secs
    m = re.search(r"try again in\s+(?:(\d+)m)?([\d.]+)s", text or "")
    if m:
        return float(m.group(1) or 0) * 60 + float(m.group(2))
    m = re.search(r'"retryDelay"\s*:\s*"([\d.]+)s"', text or "")  # Gemini RetryInfo
    return float(m.group(1)) if m else 0.0


def _message(body, text: str) -> tuple[str, str]:
    """(message, code) from the common provider error shapes."""
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict):
            return str(err.get("message") or err.get("status") or text)[:500], str(err.get("code") or err.get("type") or err.get("status") or "")
        if isinstance(err, str):
            return err[:500], ""
        if body.get("message"):
            return str(body["message"])[:500], str(body.get("type") or "")
    return (text or "")[:500], ""


def classify(provider: str, status: int, body, text: str, headers, model: str) -> AdapterError:
    """One provider HTTP error -> one precise kind. Secrets never appear: messages come from the provider's error body."""
    label = SPECS[provider].label if provider in SPECS else provider
    msg, code = _message(body, text)
    low = f"{msg} {code}".lower()
    details = {"status": status, "provider": provider, "model": model, "provider_code": code[:80]}
    if status in (401,) or "invalid api key" in low or "api key not valid" in low or "incorrect api key" in low or "invalid x-api-key" in low:
        return AuthError(f"{label} rejected the API key", adapter=provider, details={**details, "error_kind": "auth_failure"})
    if status == 402 or "insufficient_quota" in low or "credit balance" in low or "insufficient balance" in low or "insufficient credits" in low \
            or "exceeded your current quota" in low:
        return QuotaExceeded(f"{label}: the account has insufficient credits or quota ({msg[:200]})", adapter=provider,
                             details={**details, "error_kind": "insufficient_credits"})
    if status == 403:
        return PermissionDenied(f"{label} refused access ({msg[:200]})", adapter=provider, details={**details, "error_kind": "permission_denied"})
    if status == 404 or "model_not_found" in low or "does not exist" in low or "no endpoints found" in low and "model" in low:
        return NotFoundError(f"{label}: model '{model}' was not found or is not available to this key", adapter=provider,
                             details={**details, "error_kind": "unsupported_model"})
    if status == 413:
        return RateLimited(f"{label}: the request is larger than this plan allows ({msg[:200]})", adapter=provider,
                           details={**details, "too_large": True, "error_kind": "tpm_exceeded"})
    if status == 429 or "resource_exhausted" in low:
        wait = _retry_after(headers, text)
        kind = "tpm_exceeded" if ("token" in low and "minute" in low) or "tpm" in low else "rpm_exceeded" if ("request" in low and "minute" in low) or "rpm" in low else "rate_limited"
        m = re.search(r"Limit\s+(\d+).*?Requested\s+(\d+)", msg, re.S)
        too_large = bool(m and int(m.group(2)) > int(m.group(1)))
        return RateLimited(f"{label} rate limit ({kind.replace('_', ' ')}): {msg[:200]}", adapter=provider,
                           details={**details, "retry_after": wait, "too_large": too_large, "error_kind": "tpm_exceeded" if too_large else kind,
                                    **({"limit": int(m.group(1)), "requested": int(m.group(2))} if m else {})})
    if status in (400, 422):
        if any(w in low for w in ("context length", "context_length", "maximum context", "context window", "prompt is too long", "too many tokens",
                                  "input token count", "reduce the length")):
            return ContextLengthExceeded(f"{label}: the request is longer than {model}'s context window", adapter=provider,
                                         details={**details, "error_kind": "context_length"})
        if any(w in low for w in ("response_format", "json mode", "json_object", "not supported", "does not support", "unsupported")):
            return Unsupported(f"{label}: {model} does not support a parameter this task needs ({msg[:200]})", adapter=provider,
                               details={**details, "error_kind": "unsupported_capability"})
        if "json_validate_failed" in low:
            return InvalidResponse(f"{label}: {model} returned malformed JSON", adapter=provider, details={**details, "error_kind": "malformed_json"})
        return InvalidInput(f"{label} rejected the request (HTTP {status}): {msg[:200]}", adapter=provider, details={**details, "error_kind": "invalid_input"})
    if status in (500, 502, 503, 504, 529):
        return ServiceUnavailable(f"{label} is unavailable (HTTP {status})", adapter=provider, details={**details, "error_kind": "outage"})
    return ServiceUnavailable(f"{label} API error (HTTP {status}): {msg[:200]}", adapter=provider, details={**details, "error_kind": "outage"})


def _send(provider: str, http: httpx.Client, method: str, url: str, model: str = "", **kw) -> httpx.Response:
    try:
        r = http.request(method, url, **kw)
    except httpx.TimeoutException as exc:
        raise AdapterTimeout(f"{SPECS[provider].label} did not answer in time", adapter=provider, details={"error_kind": "timeout"}) from exc
    except httpx.TransportError as exc:
        raise ServiceUnavailable(f"Could not reach {SPECS[provider].label} ({type(exc).__name__})", adapter=provider,
                                 details={"error_kind": "network"}) from exc
    if r.status_code >= 400:
        try:
            body = r.json()
        except ValueError:
            body = None
        raise classify(provider, r.status_code, body, r.text[:2000], r.headers, model)
    return r


# --------------------------------------------------------------------------------------------- model metadata
@dataclass
class ModelInfo:
    id: str
    name: str = ""
    context_window: int | None = None
    max_output: int | None = None
    capabilities: dict = field(default_factory=dict)   # only keys the provider stated: {"tools": True, "vision": False, ...}
    deprecated: bool | None = None
    pricing: dict | None = None                         # provider-stated USD per 1M tokens {"input": x, "output": y}
    hidden_reason: str = ""                             # why it is not offered for these tasks (still enterable manually)
    raw_note: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, "", {}, [])}


def _per_million(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f * 1_000_000, 4) if f >= 0 else None


def parse_models(provider: str, data: dict) -> list[ModelInfo]:
    out: list[ModelInfo] = []
    if provider == "gemini":
        for m in data.get("models", []):
            mid = str(m.get("name", "")).removeprefix("models/")
            methods = m.get("supportedGenerationMethods") or []
            caps = {}
            if "thinking" in m:
                caps["reasoning"] = bool(m.get("thinking"))
            out.append(ModelInfo(mid, m.get("displayName", ""), m.get("inputTokenLimit"), m.get("outputTokenLimit"), caps,
                                 hidden_reason="" if "generateContent" in methods else "does not support generateContent (text generation)"))
        return out
    if provider == "xai" and "models" in data:  # /language-models
        for m in data["models"]:
            caps = {"vision": "image" in (m.get("input_modalities") or [])} if m.get("input_modalities") else {}
            # (xAI also lists prices, in units its docs describe separately; they are not converted here, so no price is shown)
            out.append(ModelInfo(m["id"], m.get("id"), None, None, caps,
                                 hidden_reason="" if "text" in (m.get("output_modalities") or ["text"]) else "does not produce text"))
        return out
    for m in data.get("data", []):
        mid = m.get("id", "")
        caps: dict = {}
        hidden = ""
        ctx = m.get("context_window") or m.get("context_length") or m.get("max_context_length") or m.get("max_input_tokens")
        max_out = m.get("max_tokens") or (m.get("top_provider") or {}).get("max_completion_tokens")
        deprecated = None
        pricing = None
        if provider == "mistral":
            c = m.get("capabilities") or {}
            caps = {k2: bool(c[k1]) for k1, k2 in (("function_calling", "tools"), ("vision", "vision"), ("completion_chat", "chat")) if k1 in c}
            if c and not c.get("completion_chat"):
                hidden = "not a chat model (provider metadata)"
            if m.get("deprecation"):
                deprecated = True
        elif provider == "openrouter":
            params = m.get("supported_parameters") or []
            caps = {"tools": "tools" in params, "structured_output": "response_format" in params or "structured_outputs" in params,
                    "reasoning": "reasoning" in params} if params else {}
            arch = m.get("architecture") or {}
            if arch.get("input_modalities"):
                caps["vision"] = "image" in arch["input_modalities"]
            if arch.get("output_modalities") and "text" not in arch["output_modalities"]:
                hidden = "does not produce text (provider metadata)"
            p = m.get("pricing") or {}
            if p.get("prompt") is not None and p.get("completion") is not None:
                pricing = {"input": _per_million(p["prompt"]), "output": _per_million(p["completion"])}
        elif provider == "groq":
            if m.get("active") is False:
                deprecated = True
            if NON_CHAT.search(mid):
                hidden = "not a chat model (speech/audio family)"
        elif provider == "anthropic":
            c = m.get("capabilities") or {}
            for k in ("vision", "tools", "thinking"):
                if isinstance(c.get(k), dict) and "supported" in c[k]:
                    caps["reasoning" if k == "thinking" else k] = bool(c[k]["supported"])
                elif isinstance(c.get(k), bool):
                    caps["reasoning" if k == "thinking" else k] = c[k]
        elif NON_CHAT.search(mid):  # openai / deepseek / xai /models: no capability data in the listing
            hidden = "not a chat-completion model (model family)"
        out.append(ModelInfo(mid, m.get("display_name") or m.get("name") or "", ctx, max_out, caps, deprecated, pricing, hidden))
    return out


# --------------------------------------------------------------------------------------------- OpenAI-style providers
class OpenAIWireProvider(AIProvider):
    """Chat Completions over HTTPS. Subclasses set the provider-specific endpoint, token parameter and JSON-mode behaviour."""

    provider = "openai"
    token_param = "max_tokens"
    json_mode = True
    send_temperature = True
    supports_max_tokens = True
    models_path = "/models"
    MAX_TOKENS = 6000

    def __init__(self, api_key: str, model: str, *, http: httpx.Client | None = None, model_meta: dict | None = None,
                 pricing: dict | None = None):
        if not api_key:
            raise AuthError(f"{SPECS[self.provider].label} API key is not configured", adapter=self.provider)
        self.name = SPECS[self.provider].label
        self.source = "LIVE"
        self.model, self.challenger_model = model, model
        self._key = api_key
        self.http = http or httpx.Client(timeout=TIMEOUT)
        self.model_meta = model_meta or {}
        self.pricing = pricing
        self.rate: dict = {}
        self.tpm_limit = None

    # auth headers stay inside the adapter; repr never shows them
    def __repr__(self) -> str:
        return f"<{type(self).__name__} model={self.model}>"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

    def _url(self, path: str) -> str:
        return SPECS[self.provider].base_url.rstrip("/") + path

    def _body(self, system: str, user: dict, max_tokens: int) -> dict:
        body = {"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": serialize(user)}],
                self.token_param: max_tokens}
        if self.send_temperature:
            body["temperature"] = 0.2
        if self._json_ok():
            body["response_format"] = {"type": "json_object"}
        return body

    def _json_ok(self) -> bool:
        return self.json_mode

    def _note_rate(self, headers) -> None:
        try:
            limit, remaining = headers.get("x-ratelimit-limit-tokens"), headers.get("x-ratelimit-remaining-tokens")
            if limit and remaining is not None:
                from app.adapters.ai import _duration
                self.tpm_limit = int(limit)
                self.rate = {"limit": int(limit), "remaining": int(remaining), "reset": _duration(headers.get("x-ratelimit-reset-tokens")),
                             "at": time.monotonic()}
        except (TypeError, ValueError):
            pass

    def rate_wait(self, needed_tokens: int, model_role: str = "draft") -> tuple[float, str]:
        r = self.rate
        if not r:
            return 0.0, ""
        elapsed = time.monotonic() - r["at"]
        remaining = r["remaining"] if not r["reset"] else min(r["limit"], r["remaining"] + (r["limit"] - r["remaining"]) * elapsed / r["reset"])
        if remaining >= needed_tokens:
            return 0.0, ""
        return max(0.0, r["reset"] - elapsed) + 0.5, f"needs ~{needed_tokens:,} tokens, about {int(remaining):,} of {r['limit']:,}/minute left"

    def complete(self, purpose, system, user, *, model_role="draft", max_tokens: int | None = None) -> RawCompletion:
        started = time.monotonic()
        r = _send(self.provider, self.http, "POST", self._url("/chat/completions"), self.model, headers=self._headers(),
                  json=self._body(system, user, int(max_tokens or self.MAX_TOKENS)), timeout=TIMEOUT)
        self._note_rate(r.headers)
        try:
            data = r.json()
            choice = data["choices"][0]
        except (ValueError, KeyError, IndexError) as exc:
            raise InvalidResponse(f"{self.name} returned an unexpected response shape", adapter=self.provider,
                                  details={"error_kind": "malformed_json"}) from exc
        usage = data.get("usage") or {}
        return RawCompletion(text=(choice.get("message") or {}).get("content") or "", model=data.get("model") or self.model,
                             tokens_in=int(usage.get("prompt_tokens") or 0), tokens_out=int(usage.get("completion_tokens") or 0),
                             latency_ms=int((time.monotonic() - started) * 1000), finish_reason=str(choice.get("finish_reason") or ""),
                             status=f"http_{r.status_code}", cost_usd=self._cost(usage))

    def _cost(self, usage: dict) -> float | None:
        return None

    # discovery + checks ---------------------------------------------------------------------------
    def check_key(self) -> None:
        """Proves the key with an authenticated read (no generation, no cost)."""
        _send(self.provider, self.http, "GET", self._url(self.models_path), headers=self._headers(), timeout=30)

    def list_models(self) -> list[ModelInfo]:
        r = _send(self.provider, self.http, "GET", self._url(self.models_path), headers=self._headers(), timeout=30)
        return parse_models(self.provider, r.json())


class OpenAIProvider(OpenAIWireProvider):
    provider = "openai"
    token_param = "max_completion_tokens"   # required by OpenAI's reasoning models; accepted by the others
    send_temperature = False                # reasoning models reject a non-default temperature


class DeepSeekProvider(OpenAIWireProvider):
    provider = "deepseek"


class MistralProvider(OpenAIWireProvider):
    provider = "mistral"


class XAIProvider(OpenAIWireProvider):
    provider = "xai"

    def list_models(self) -> list[ModelInfo]:
        try:  # richer metadata (modalities) when the endpoint is available to the key
            r = _send(self.provider, self.http, "GET", self._url("/language-models"), headers=self._headers(), timeout=30)
            return parse_models(self.provider, r.json())
        except NotFoundError:
            return super().list_models()


class OpenRouterProvider(OpenAIWireProvider):
    provider = "openrouter"

    def _headers(self) -> dict:
        return {**super()._headers(), "X-Title": "Google Ads AI Agent"}

    def _json_ok(self) -> bool:  # only when the model's own metadata lists response_format (else OpenRouter may reject the call)
        return bool((self.model_meta.get("capabilities") or {}).get("structured_output"))

    def _body(self, system, user, max_tokens):
        return {**super()._body(system, user, max_tokens), "usage": {"include": True}}

    def _cost(self, usage: dict) -> float | None:
        c = usage.get("cost")
        return float(c) if isinstance(c, (int, float)) else None   # OpenRouter's own charge for this call

    def check_key(self) -> None:
        _send(self.provider, self.http, "GET", self._url("/key"), headers=self._headers(), timeout=30)


# --------------------------------------------------------------------------------------------- Gemini
class GeminiProvider(AIProvider):
    provider = "gemini"
    supports_max_tokens = True
    MAX_TOKENS = 6000

    def __init__(self, api_key: str, model: str, *, http: httpx.Client | None = None, model_meta: dict | None = None, pricing: dict | None = None):
        if not api_key:
            raise AuthError("Google Gemini API key is not configured", adapter=self.provider)
        self.name, self.source = SPECS["gemini"].label, "LIVE"
        self.model = self.challenger_model = model.removeprefix("models/")
        self._key = api_key
        self.http = http or httpx.Client(timeout=TIMEOUT)
        self.model_meta, self.pricing, self.tpm_limit = model_meta or {}, pricing, None

    def __repr__(self) -> str:
        return f"<GeminiProvider model={self.model}>"

    def _headers(self) -> dict:
        return {"x-goog-api-key": self._key, "Content-Type": "application/json"}   # header, so the key never appears in a URL

    def complete(self, purpose, system, user, *, model_role="draft", max_tokens: int | None = None) -> RawCompletion:
        started = time.monotonic()
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": serialize(user)}]}],
                "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": int(max_tokens or self.MAX_TOKENS), "temperature": 0.2}}
        r = _send("gemini", self.http, "POST", f"{SPECS['gemini'].base_url}/models/{self.model}:generateContent", self.model,
                  headers=self._headers(), json=body, timeout=TIMEOUT)
        data = r.json()
        cands = data.get("candidates") or []
        if not cands:
            reason = (data.get("promptFeedback") or {}).get("blockReason") or "no candidates"
            raise InvalidResponse(f"Gemini returned no answer ({reason})", adapter="gemini", details={"error_kind": "malformed_json"})
        c = cands[0]
        text = "".join(p.get("text", "") for p in (c.get("content") or {}).get("parts", []) if not p.get("thought"))
        finish = str(c.get("finishReason") or "")
        if finish in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"):
            raise InvalidResponse(f"Gemini stopped the answer ({finish})", adapter="gemini", details={"error_kind": "malformed_json", "finish": finish})
        u = data.get("usageMetadata") or {}
        return RawCompletion(text=text, model=data.get("modelVersion") or self.model, tokens_in=int(u.get("promptTokenCount") or 0),
                             tokens_out=int(u.get("candidatesTokenCount") or 0) + int(u.get("thoughtsTokenCount") or 0),
                             latency_ms=int((time.monotonic() - started) * 1000),
                             finish_reason="length" if finish == "MAX_TOKENS" else finish.lower(), status=f"http_{r.status_code}")

    def check_key(self) -> None:
        _send("gemini", self.http, "GET", f"{SPECS['gemini'].base_url}/models?pageSize=1", headers=self._headers(), timeout=30)

    def list_models(self) -> list[ModelInfo]:
        out, token = [], ""
        for _ in range(10):
            r = _send("gemini", self.http, "GET", f"{SPECS['gemini'].base_url}/models?pageSize=1000" + (f"&pageToken={token}" if token else ""),
                      headers=self._headers(), timeout=30)
            data = r.json()
            out += parse_models("gemini", data)
            token = data.get("nextPageToken") or ""
            if not token:
                break
        return out


# --------------------------------------------------------------------------------------------- Anthropic / Groq discovery
def _anthropic_headers(key: str) -> dict:
    return {"x-api-key": key, "anthropic-version": "2023-06-01"}


def _list_paginated_anthropic(http: httpx.Client, key: str) -> list[ModelInfo]:
    out, after = [], ""
    for _ in range(10):
        r = _send("anthropic", http, "GET", f"{SPECS['anthropic'].base_url}/models?limit=1000" + (f"&after_id={after}" if after else ""),
                  headers=_anthropic_headers(key), timeout=30)
        data = r.json()
        out += parse_models("anthropic", data)
        if not data.get("has_more"):
            break
        after = data.get("last_id") or ""
    return out


ADAPTERS = {"openai": OpenAIProvider, "deepseek": DeepSeekProvider, "mistral": MistralProvider, "xai": XAIProvider,
            "openrouter": OpenRouterProvider, "gemini": GeminiProvider}


def build(provider: str, api_key: str, model: str, *, http: httpx.Client | None = None, model_meta: dict | None = None,
          pricing: dict | None = None) -> AIProvider:
    """The adapter for one configured role. Groq and Anthropic use their official SDKs."""
    if provider not in SPECS:
        raise Unsupported(f"Unknown AI provider '{provider}'", adapter=provider, details={"error_kind": "unsupported_model"})
    if not model:
        raise InvalidInput(f"No model is selected for {SPECS[provider].label}", adapter=provider, details={"error_kind": "unsupported_model"})
    if provider == "groq":
        from app.adapters.ai import GroqAIProvider
        p = GroqAIProvider(api_key=api_key, model=model, http=http)
    elif provider == "anthropic":
        from app.adapters.ai.claude import ClaudeAIProvider
        p = ClaudeAIProvider(api_key, model, http=http)
    else:
        p = ADAPTERS[provider](api_key, model, http=http, model_meta=model_meta, pricing=pricing)
    p.pricing = pricing if pricing is not None else getattr(p, "pricing", None)
    p.provider_key = provider
    return p


def check_key(provider: str, api_key: str, *, http: httpx.Client | None = None) -> None:
    """Authenticated read that proves the key. Raises AuthError / QuotaExceeded / ServiceUnavailable / ... with a precise kind."""
    http = http or httpx.Client(timeout=TIMEOUT)
    if provider == "anthropic":
        _send("anthropic", http, "GET", f"{SPECS['anthropic'].base_url}/models?limit=1", headers=_anthropic_headers(api_key), timeout=30)
    elif provider == "groq":
        _send("groq", http, "GET", f"{SPECS['groq'].base_url}/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
    else:
        ADAPTERS[provider](api_key, "-", http=http).check_key()


def list_models(provider: str, api_key: str, *, http: httpx.Client | None = None) -> list[ModelInfo]:
    http = http or httpx.Client(timeout=TIMEOUT)
    if provider == "anthropic":
        return _list_paginated_anthropic(http, api_key)
    if provider == "groq":
        r = _send("groq", http, "GET", f"{SPECS['groq'].base_url}/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
        return parse_models("groq", r.json())
    return ADAPTERS[provider](api_key, "-", http=http).list_models()
