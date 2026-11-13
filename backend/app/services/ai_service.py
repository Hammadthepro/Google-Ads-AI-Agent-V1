"""Runs AI calls: builds versioned prompts, validates output against strict schemas, retries once,
records every attempt as an AIRun. Malformed output is rejected and never executed."""
from __future__ import annotations

import json
import time

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.adapters import registry
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


def system_prompt(purpose: str) -> str:
    spec = PROMPTS[purpose]
    schema = SCHEMAS[purpose].model_json_schema()
    return f"{BASE_RULES}\n\nTASK: {spec['task']}\n\nJSON SCHEMA:\n{json.dumps(schema)}"


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in response")
    return json.loads(text[start:end + 1])


def generate(db: Session, purpose: str, payload: dict, *, task_id: str | None = None,
             model_role: str = "draft", max_attempts: int = 2, provider=None,
             label: str | None = None) -> tuple[BaseModel, AIRun]:
    """`provider`/`label` let the AI competition run the SAME prompt through a specific provider (label ≤ 20 chars)."""
    provider = provider or registry.get("ai")
    spec = PROMPTS[purpose]
    schema = SCHEMAS[purpose]
    system = system_prompt(purpose)
    user = dict(payload)
    last_error = ""
    attempt = 0
    while attempt < max_attempts:  # (max_attempts can grow when a rate-limit window is being waited out)
        attempt += 1
        if last_error:
            user = {**payload, "previous_attempt_error": last_error[:1500],
                    "instruction": "Your previous output was rejected. Return valid JSON matching the schema exactly."}
        run = AIRun(task_id=task_id, purpose=purpose, provider=label or provider.name[:20], prompt_template_id=spec["id"],
                    prompt_version=spec["version"], input_hash=payload_hash(payload), attempt=attempt)
        db.add(run)
        model = getattr(provider, "challenger_model" if model_role == "challenger" else "model", None) or getattr(provider, "name", "")
        research_events.emit("ai_request", f"Asking {label or provider.name} ({model}) for {purpose.replace('_', ' ').lower()}",
                             purpose=purpose, provider=label or provider.name, model=model, attempt=attempt,
                             approx_input=(len(system) + len(json.dumps(user, default=str))) // 4)
        try:
            raw = provider.complete(purpose, system, user, model_role=model_role)
        except AdapterError as exc:
            run.error = f"{exc.kind}: {exc.message}"
            db.flush()
            if exc.retryable and exc.kind == "rate_limited" and not exc.details.get("too_large"):
                max_attempts = max(max_attempts, 3)  # a per-minute window usually clears: wait for it instead of failing instantly
                if attempt < max_attempts:
                    last_error = run.error
                    wait = min(max(float(exc.details.get("retry_after") or 0), 2.0 * attempt), RATE_WAIT_MAX)
                    research_events.emit("ai_waiting", f"{label or provider.name} is rate-limited; waiting {wait:.0f}s before attempt {attempt + 1}",
                                         level="warn", purpose=purpose, provider=label or provider.name, model=model, seconds=round(wait, 1),
                                         reason=exc.message)
                    time.sleep(wait)
                    continue
            if exc.kind == "invalid_response" and attempt < max_attempts:
                # the provider produced unusable output (e.g. JSON cut off at the output limit): re-ask with that feedback
                last_error = f"{exc.message}. Return a complete, compact JSON object: keep text fields short and do not repeat content."
                research_events.emit("ai_failed", f"{label or provider.name}: {exc.message} (attempt {attempt}); asking again",
                                     level="warn", purpose=purpose, provider=label or provider.name, model=model, error_kind="invalid_output", reason=exc.message)
                continue
            if exc.retryable and attempt < max_attempts and exc.kind != "rate_limited":
                last_error = run.error
                continue
            research_events.emit("ai_failed", f"{label or provider.name}: {exc.message}", level="error", purpose=purpose,
                                 provider=label or provider.name, model=model, error_kind=exc.kind, reason=exc.message,
                                 limit=exc.details.get("limit") or None, requested=exc.details.get("requested") or None)
            reason = exc.message if exc.details.get("too_large") or exc.kind in ("auth_failure", "invalid_input", "invalid_response") else f"AI provider error ({exc.kind}): {exc.message}"
            raise AIOutputInvalid(f"{reason}; nothing was executed", details=exc.to_dict()) from exc
        run.model, run.raw_output = raw.model, raw.text[:60000]
        run.tokens_in, run.tokens_out, run.latency_ms = raw.tokens_in, raw.tokens_out, raw.latency_ms
        try:
            parsed = schema.model_validate(_extract_json(raw.text))
        except (ValueError, ValidationError) as exc:
            run.error = f"Invalid AI output: {str(exc)[:1500]}"
            run.validation_ok = False
            db.flush()
            last_error = run.error
            research_events.emit("ai_failed", f"{label or provider.name} returned output that does not match the required format (attempt {attempt})",
                                 level="warn", purpose=purpose, provider=label or provider.name, model=raw.model, error_kind="invalid_output",
                                 detail=str(exc)[:300], reason="returned output that does not match the required format")
            continue
        run.parsed_output = parsed.model_dump()
        run.validation_ok = True
        db.flush()
        research_events.emit("ai_result", f"{label or provider.name} returned a valid {purpose.replace('_', ' ').lower()}", purpose=purpose,
                             provider=label or provider.name, model=raw.model, usage_in=raw.tokens_in, usage_out=raw.tokens_out, attempt=attempt)
        return parsed, run
    raise AIOutputInvalid(f"AI output for {purpose} failed schema validation after {max_attempts} attempts; "
                          "nothing was executed", details={"last_error": last_error[:500]})
