"""Runs AI calls: builds versioned prompts, validates output against strict schemas, retries once,
records every attempt as an AIRun. Malformed output is rejected and never executed."""
from __future__ import annotations

import json
import time
import uuid

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.adapters import registry
from app.adapters.ai import serialize
from app.adapters.ai.prompts import BASE_RULES, PROMPTS
from app.adapters.ai.schemas import SCHEMAS
from app.adapters.base import AdapterError
from app.core.errors import AppError
from app.core.util import payload_hash
from app.models import AIRun
from app.services import research_events

RATE_WAIT_MAX = 65.0  # Groq's per-minute windows: wait for the window it names (up to a minute), never forever


class AIOutputInvalid(AppError):
    status_code = 502
    code = "ai_output_invalid"


class TokenBudgetExceeded(AIOutputInvalid):
    """The request is larger than the provider's per-minute token limit: sending it (again) cannot succeed; a smaller request can."""
    code = "ai_token_budget"


class OutputTruncated(AIOutputInvalid):
    """The provider stopped at the output-token limit: the JSON is incomplete and is never used."""
    code = "ai_output_truncated"


ERROR_TEXT = {
    "tpm_exceeded": "request larger than the per-minute token limit", "rate_limited": "per-minute token budget used up",
    "context_length": "request longer than the model's context window", "output_truncated": "output cut off at the output-token limit",
    "malformed_json": "malformed JSON", "schema_invalid": "output did not match the required schema",
}


def _strip_titles(node):
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if not (k == "title" and isinstance(v, str))}
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


def system_prompt(purpose: str) -> str:
    """Rules + task + the JSON schema the answer must match. The schema is sent compact and without pydantic's generated titles
    (they repeat the field names); types, required fields, enums and length limits are all kept."""
    spec = PROMPTS[purpose]
    schema = _strip_titles(SCHEMAS[purpose].model_json_schema())
    return f"{BASE_RULES}\n\nTASK: {spec['task']}\n\nJSON SCHEMA:\n{json.dumps(schema, separators=(',', ':'))}"


def estimate_tokens(*texts: str) -> int:
    """A deliberately conservative prompt-size estimate (it over-counts): used to size requests BEFORE sending them."""
    from app.core.config import get_settings
    cpt = max(1.5, float(get_settings().AI_CHARS_PER_TOKEN or 3.3))
    return int(sum(len(t) for t in texts) / cpt) + 8


def request_tokens(purpose: str, payload: dict) -> int:
    return estimate_tokens(system_prompt(purpose), serialize(payload))


def budget_for(provider) -> int | None:
    """The largest prompt (in estimated tokens) this provider takes in one request, or None when it has no per-minute limit we know of."""
    from app.core.config import get_settings
    limit = getattr(provider, "tpm_limit", None)
    if not limit:
        return None
    return int(limit * (1 - float(get_settings().AI_TOKEN_SAFETY_MARGIN or 0)))


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in response")
    return json.loads(text[start:end + 1])


def _complete(provider, purpose, system, user, model_role, max_output_tokens):
    if max_output_tokens and getattr(provider, "supports_max_tokens", False):
        return provider.complete(purpose, system, user, model_role=model_role, max_tokens=max_output_tokens)
    return provider.complete(purpose, system, user, model_role=model_role)


def _rate_wait(provider, needed: int, model_role: str) -> tuple[float, str]:
    fn = getattr(provider, "rate_wait", None)
    if not fn:
        return 0.0, ""
    try:
        return fn(needed, model_role=model_role)
    except TypeError:
        return fn(needed)


def _cost(provider, raw) -> tuple[float | None, str]:
    """Only a real number: the provider's own charge, or the admin-configured price per 1M tokens. Otherwise unknown (None)."""
    if raw.cost_usd is not None:
        return round(float(raw.cost_usd), 6), "provider"
    price = getattr(provider, "pricing", None) or {}
    if isinstance(price, dict) and price.get("input") is not None and price.get("output") is not None:
        return round((raw.tokens_in * float(price["input"]) + raw.tokens_out * float(price["output"])) / 1_000_000, 6), "configured_price"
    return None, ""


def generate(db: Session, purpose: str, payload: dict, *, task_id: str | None = None,
             model_role: str = "draft", max_attempts: int = 2, provider=None,
             label: str | None = None, max_output_tokens: int | None = None, stage: str = "",
             split_on_truncation: bool = False, check=None, role: str | None = None) -> tuple[BaseModel, AIRun]:
    """`provider`/`label` let the AI competition run the SAME prompt through a specific provider (label <= 20 chars).
    `max_output_tokens` caps the answer; `stage` names a step of a staged draft; `check(parsed) -> [errors]` adds rules the schema
    cannot express (treated exactly like schema errors). With `split_on_truncation`, a cut-off answer or a request over the per-minute
    limit raises at once (OutputTruncated / TokenBudgetExceeded) so the caller can send smaller requests instead of repeating this one."""
    # One routing point for every AI call: model_role "challenger" -> the Challenger AI role, anything else -> the Main AI role.
    role = role or ("challenger" if model_role == "challenger" else "second_drafter" if (label or "").startswith("claude") else "main")
    provider = provider or registry.get("challenger_ai" if model_role == "challenger" else "ai")
    spec = PROMPTS[purpose]
    schema = SCHEMAS[purpose]
    system = system_prompt(purpose)
    user = dict(payload)
    last_error = ""
    attempt = 0
    who = label or provider.name
    what = purpose.replace("_", " ").lower() + (f" ({stage})" if stage else "")
    while attempt < max_attempts:  # (max_attempts can grow when a rate-limit window is being waited out)
        attempt += 1
        if last_error:
            user = {**payload, "previous_attempt_error": last_error[:1500],
                    "instruction": "Your previous output was rejected. Return valid JSON matching the schema exactly."}
        estimate = estimate_tokens(system, serialize(user))
        run = AIRun(task_id=task_id, purpose=purpose, provider=who[:20], prompt_template_id=spec["id"],
                    prompt_version=spec["version"], input_hash=payload_hash(payload), attempt=attempt, stage=stage[:60],
                    prompt_estimate=estimate, role=role[:20], request_id=uuid.uuid4().hex)
        db.add(run)
        model = getattr(provider, "challenger_model" if model_role == "challenger" else "model", None) or getattr(provider, "name", "")
        limit = budget_for(provider)
        if limit and estimate > limit:  # never send a request the plan cannot accept
            run.model, run.error_kind, run.provider_status = model, "tpm_exceeded", "not_sent"
            run.error = f"tpm_exceeded: ~{estimate:,} tokens estimated, the per-minute budget is {limit:,}"
            db.flush()
            research_events.emit("ai_failed", f"{who}: the {what} request (~{estimate:,} tokens) is larger than the per-minute budget ({limit:,})",
                                 level="warn" if split_on_truncation else "error", purpose=purpose, provider=who, model=model,
                                 error_kind="tpm_exceeded", estimate=estimate, limit=limit, stage=stage or None,
                                 reason=f"request of ~{estimate:,} tokens is larger than the {limit:,}-token per-minute budget")
            raise TokenBudgetExceeded(f"The {what} request needs ~{estimate:,} tokens but {model} allows about {limit:,} per minute; "
                                      "nothing was sent", details={"error_kind": "tpm_exceeded", "estimate": estimate, "limit": limit})
        wait, why = _rate_wait(provider, estimate, model_role)
        if wait > 0:
            wait = min(wait, RATE_WAIT_MAX)
            research_events.emit("ai_waiting", f"{who}: waiting {wait:.0f}s for the per-minute token window before the {what}",
                                 level="warn", purpose=purpose, provider=who, model=model, seconds=round(wait, 1), reason=why, stage=stage or None)
            time.sleep(wait)
        research_events.emit("ai_request", f"Asking {who} ({model}) for {what}", purpose=purpose, provider=who, model=model,
                             attempt=attempt, approx_input=estimate, max_output=max_output_tokens, stage=stage or None)
        try:
            raw = _complete(provider, purpose, system, user, model_role, max_output_tokens)
        except AdapterError as exc:
            kind = exc.details.get("error_kind") or ("tpm_exceeded" if exc.details.get("too_large") else exc.kind)
            if kind == "invalid_response":  # the provider answered with unusable output: same handling as malformed JSON
                kind = "malformed_json"
            run.error = f"{kind}: {exc.message}"
            run.error_kind = kind
            run.provider_status = f"http_{exc.details['status']}" if exc.details.get("status") else exc.kind
            run.model = model
            db.flush()
            if kind == "tpm_exceeded":  # the same request can never fit: report it now, the caller sends smaller ones
                research_events.emit("ai_failed", f"{who}: {exc.message}", level="warn" if split_on_truncation else "error", purpose=purpose,
                                     provider=who, model=model, error_kind=kind, limit=exc.details.get("limit") or None,
                                     requested=exc.details.get("requested") or None, reason=exc.message, stage=stage or None)
                raise TokenBudgetExceeded(f"{exc.message}; nothing was executed", details={**exc.to_dict(), "error_kind": kind}) from exc
            if exc.retryable and exc.kind == "rate_limited" and float(exc.details.get("retry_after") or 0) > 0:
                # the provider said when its window reopens: wait for exactly that (bounded). Without that metadata the same
                # request is NOT repeated blindly; the error is reported instead.
                max_attempts = max(max_attempts, 3)
                if attempt < max_attempts:
                    last_error = ""  # the same request is still right; only the window was full
                    wait = min(float(exc.details.get("retry_after") or 0), RATE_WAIT_MAX)
                    research_events.emit("ai_waiting", f"{who} is rate-limited; waiting {wait:.0f}s before attempt {attempt + 1}",
                                         level="warn", purpose=purpose, provider=who, model=model, seconds=round(wait, 1),
                                         reason=exc.message, stage=stage or None)
                    time.sleep(wait)
                    continue
            if kind == "malformed_json" and attempt < max_attempts and not split_on_truncation:
                last_error = f"{exc.message}. Return a complete, compact JSON object: keep text fields short and do not repeat content."
                research_events.emit("ai_failed", f"{who}: {exc.message} (attempt {attempt}); asking again", level="warn", purpose=purpose,
                                     provider=who, model=model, error_kind=kind, reason=exc.message, stage=stage or None)
                continue
            if kind == "malformed_json" and split_on_truncation:  # Groq JSON mode gives no reason; treat like a cut-off answer
                research_events.emit("ai_failed", f"{who}: {exc.message}", level="warn", purpose=purpose, provider=who, model=model,
                                     error_kind=kind, reason=exc.message, stage=stage or None)
                raise OutputTruncated(f"{exc.message}; nothing was executed", details={**exc.to_dict(), "error_kind": kind}) from exc
            if exc.retryable and attempt < max_attempts and exc.kind != "rate_limited":
                last_error = run.error
                continue
            research_events.emit("ai_failed", f"{who}: {exc.message}", level="error", purpose=purpose, provider=who, model=model,
                                 error_kind=kind, limit=exc.details.get("limit") or None, requested=exc.details.get("requested") or None,
                                 reason=exc.message, stage=stage or None)
            reason = exc.message if kind in ("auth_failure", "invalid_input", "invalid_response", "malformed_json", "context_length") \
                else f"AI provider error ({kind}): {exc.message}"
            raise AIOutputInvalid(f"{reason}; nothing was executed", details={**exc.to_dict(), "error_kind": kind}) from exc
        run.model, run.raw_output = raw.model, raw.text[:60000]
        run.tokens_in, run.tokens_out, run.latency_ms = raw.tokens_in, raw.tokens_out, raw.latency_ms
        run.finish_reason, run.provider_status = (raw.finish_reason or "")[:30], (raw.status or "ok")[:40]
        run.cost_usd, run.cost_source = _cost(provider, raw)
        if raw.finish_reason == "length":  # cut off: the JSON is incomplete, whatever it parses to
            run.error_kind, run.validation_ok = "output_truncated", False
            run.error = f"output_truncated: stopped at the output-token limit after {raw.tokens_out:,} tokens"
            db.flush()
            research_events.emit("ai_failed", f"{who}: the answer was cut off at the output-token limit ({raw.tokens_out:,} tokens)",
                                 level="warn", purpose=purpose, provider=who, model=raw.model, error_kind="output_truncated",
                                 reason=f"output cut off after {raw.tokens_out:,} tokens", stage=stage or None,
                                 usage_in=raw.tokens_in, usage_out=raw.tokens_out)
            if split_on_truncation:
                raise OutputTruncated(f"The {what} answer was cut off at the output-token limit ({raw.tokens_out:,} tokens); nothing was used",
                                      details={"error_kind": "output_truncated", "tokens_out": raw.tokens_out})
            last_error = "Your previous answer was cut off at the output limit. Return a complete, compact JSON object: keep text short."
            continue
        try:
            data = _extract_json(raw.text)
        except ValueError as exc:
            run.error, run.error_kind, run.validation_ok = f"malformed_json: Invalid AI output: {str(exc)[:1500]}", "malformed_json", False
            db.flush()
            last_error = f"Invalid AI output: {str(exc)[:1500]}"
            research_events.emit("ai_failed", f"{who} returned malformed JSON (attempt {attempt})", level="warn", purpose=purpose, provider=who,
                                 model=raw.model, error_kind="malformed_json", detail=str(exc)[:300], reason="returned malformed JSON",
                                 stage=stage or None)
            continue
        try:
            parsed = schema.model_validate(data)
            extra = list(check(parsed)) if check else []
            if extra:
                raise ValueError("; ".join(extra))
        except (ValueError, ValidationError) as exc:
            run.error, run.error_kind, run.validation_ok = f"schema_invalid: Invalid AI output: {str(exc)[:1500]}", "schema_invalid", False
            db.flush()
            last_error = f"Invalid AI output: {str(exc)[:1500]}"
            research_events.emit("ai_failed", f"{who} returned output that does not match the required format (attempt {attempt})",
                                 level="warn", purpose=purpose, provider=who, model=raw.model, error_kind="schema_invalid",
                                 detail=str(exc)[:300], reason="returned output that does not match the required format", stage=stage or None)
            continue
        run.parsed_output = parsed.model_dump()
        run.validation_ok = True
        db.flush()
        research_events.emit("ai_result", f"{who} returned a valid {what}", purpose=purpose, provider=who, model=raw.model,
                             usage_in=raw.tokens_in, usage_out=raw.tokens_out, attempt=attempt, finish_reason=raw.finish_reason or None,
                             stage=stage or None)
        return parsed, run
    raise AIOutputInvalid(f"AI output for {purpose}{f' ({stage})' if stage else ''} failed schema validation after {max_attempts} attempts; "
                          "nothing was executed", details={"last_error": last_error[:500], "error_kind": "schema_invalid"})
