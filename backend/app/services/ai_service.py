"""Runs AI calls: builds versioned prompts, validates output against strict schemas, retries once,
records every attempt as an AIRun. Malformed output is rejected and never executed."""
from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.adapters import registry
from app.adapters.ai.prompts import BASE_RULES, PROMPTS
from app.adapters.ai.schemas import SCHEMAS
from app.adapters.base import AdapterError
from app.core.errors import AppError
from app.core.util import payload_hash
from app.models import AIRun


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
    for attempt in range(1, max_attempts + 1):
        if last_error:
            user = {**payload, "previous_attempt_error": last_error[:1500],
                    "instruction": "Your previous output was rejected. Return valid JSON matching the schema exactly."}
        run = AIRun(task_id=task_id, purpose=purpose, provider=label or provider.name[:20], prompt_template_id=spec["id"],
                    prompt_version=spec["version"], input_hash=payload_hash(payload), attempt=attempt)
        db.add(run)
        try:
            raw = provider.complete(purpose, system, user, model_role=model_role)
        except AdapterError as exc:
            run.error = f"{exc.kind}: {exc.message}"
            db.flush()
            if exc.retryable and attempt < max_attempts:
                last_error = run.error
                continue
            raise AIOutputInvalid(f"AI provider error ({exc.kind}); nothing was executed", details=exc.to_dict()) from exc
        run.model, run.raw_output = raw.model, raw.text[:60000]
        run.tokens_in, run.tokens_out, run.latency_ms = raw.tokens_in, raw.tokens_out, raw.latency_ms
        try:
            parsed = schema.model_validate(_extract_json(raw.text))
        except (ValueError, ValidationError) as exc:
            run.error = f"Invalid AI output: {str(exc)[:1500]}"
            run.validation_ok = False
            db.flush()
            last_error = run.error
            continue
        run.parsed_output = parsed.model_dump()
        run.validation_ok = True
        db.flush()
        return parsed, run
    raise AIOutputInvalid(f"AI output for {purpose} failed schema validation after {max_attempts} attempts; "
                          "nothing was executed", details={"last_error": last_error[:500]})
