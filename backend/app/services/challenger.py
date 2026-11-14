"""Independent review by the Challenger AI.

The Challenger reviews important Main AI outputs with the evidence they were built from, and returns structured findings (agreement,
objections with evidence and severity, recommended corrections, confidence, limitations). It is a reviewer, not an authority: it never
approves anything, never overrides deterministic rules or validation, and never triggers a Google Ads action. Its findings are shown
next to the output (and high-severity objections become listed risks on the approval).

When the Challenger is off, not configured, not enabled for this kind of output, or its request cannot be served, the result says
"independent review not performed" and why. Nothing is presented as reviewed when it was not.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import IntegrationNotConfigured
from app.core.util import payload_hash
from app.models import AIRun
from app.services import ai_roles, ai_service, research_events

log = logging.getLogger(__name__)


def not_performed(reason: str, kind: str) -> dict:
    return {"performed": False, "reason": reason, "kind": kind, "verdict": None, "objections": [], "agreements": [],
            "independence": "not performed"}


def _reviewer() -> dict:
    st = ai_roles.challenger_status()
    return {"provider": st.get("provider"), "model": st.get("model"), "independence": st["independence"],
            "same_provider": st["same_provider"], "same_model": st["same_model"]}


def _runs(db: Session, purpose: str, payload: dict, task_id: str | None) -> list[AIRun]:
    q = select(AIRun).where(AIRun.purpose == purpose, AIRun.role == "challenger", AIRun.input_hash == payload_hash(payload))
    if task_id:
        q = q.where(AIRun.task_id == task_id)
    return list(db.scalars(q.order_by(AIRun.created_at, AIRun.attempt)))


def _lifecycle(st: dict, required: bool, dispatched: bool, outcome: str, reason: str, runs: list[AIRun]) -> dict:
    """What happened to the review, recorded every time: was it required, was it sent, to which provider/model, what came back."""
    return {"required": required, "dispatched": dispatched, "outcome": outcome, "reason": reason,
            "provider": st.get("provider"), "model": st.get("model"),
            "attempts": [{"run_id": r.id, "attempt": r.attempt, "model": r.model, "ok": bool(r.validation_ok), "error_kind": r.error_kind or "",
                          "error": (r.error or "")[:300], "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "latency_ms": r.latency_ms,
                          "finish_reason": r.finish_reason or ""} for r in runs]}


def review_draft(db: Session, draft: dict, evidence: dict, *, kind: str, task_id: str | None) -> tuple[dict, str | None]:
    """CHALLENGE a draft (campaign / ad group / ad) against its evidence. Returns (report, ai_run_id or None).
    Never raises: a review that cannot be done is reported as "not performed" with its cause, and the draft continues to validation."""
    st = ai_roles.challenger_status()
    required = bool(st["review_policy"].get(kind, False))
    if not st["available"]:
        why = f"The Challenger AI is not available: {st['reason'] or 'not configured'}"
        return {**not_performed(why, kind), "lifecycle": _lifecycle(st, required, False, "skipped", why, [])}, None
    if not required:
        why = "Independent review of this kind of output is turned off in the Challenger AI settings"
        return {**not_performed(why, kind), "lifecycle": _lifecycle(st, False, False, "skipped", why, [])}, None
    payload = {"draft": draft, **evidence}
    research_events.emit("challenger_dispatched", f"Independent review sent to the Challenger AI ({st.get('provider')} · {st.get('model')})",
                         provider=st.get("provider"), model=st.get("model"), review_kind=kind)
    try:
        parsed, run = ai_service.generate(db, "CHALLENGE", payload, task_id=task_id, model_role="challenger")
    except IntegrationNotConfigured as exc:
        rep, kind_err = not_performed(exc.message, kind), "not_configured"
    except ai_service.AIOutputInvalid as exc:
        rep = not_performed(f"The Challenger AI could not complete the review: {exc.message}", kind)
        kind_err = (exc.details or {}).get("error_kind")
    except Exception as exc:  # noqa: BLE001 - an unexpected reviewer failure must never fail the build or pass as reviewed
        log.exception("challenger review failed")
        rep, kind_err = not_performed(f"The Challenger AI review failed unexpectedly ({type(exc).__name__}); the draft was not reviewed", kind), "unexpected"
    else:
        report = {**parsed.model_dump(), "performed": True, "kind": kind, "reviewer": _reviewer(), "run_id": run.id,
                  "independence": _reviewer()["independence"]}
        report["lifecycle"] = _lifecycle(st, True, True, "performed", "", _runs(db, "CHALLENGE", payload, task_id))
        research_events.emit("challenger_result", f"Challenger AI verdict: {parsed.verdict} with {len(parsed.objections)} objection(s)",
                             verdict=parsed.verdict, objections=len(parsed.objections))
        return report, run.id
    research_events.emit("challenger_failed", f"Independent review not performed: {rep['reason']}", level="warn", error_kind=kind_err)
    return {**rep, "error_kind": kind_err, "lifecycle": _lifecycle(st, True, True, "failed", rep["reason"], _runs(db, "CHALLENGE", payload, task_id))}, None


def review_diagnosis(db: Session, analysis: dict, metrics: dict) -> dict | None:
    """Advisory review of the weekly diagnosis (only when turned on). Returns None when it is turned off, so reports stay unchanged."""
    if not ai_roles.reviews("performance_diagnosis"):
        return None
    try:
        parsed, run = ai_service.generate(db, "REVIEW_DIAGNOSIS", {"analysis": analysis, "metrics": metrics}, model_role="challenger")
    except IntegrationNotConfigured as exc:
        return not_performed(exc.message, "performance_diagnosis")
    except ai_service.AIOutputInvalid as exc:
        return not_performed(f"The Challenger AI could not complete the review: {exc.message}", "performance_diagnosis")
    except Exception as exc:  # noqa: BLE001 - advisory only: never breaks the weekly report
        log.exception("challenger diagnosis review failed")
        return not_performed(f"The Challenger AI review failed unexpectedly ({type(exc).__name__})", "performance_diagnosis")
    return {**parsed.model_dump(), "performed": True, "kind": "performance_diagnosis", "reviewer": _reviewer(), "run_id": run.id}
