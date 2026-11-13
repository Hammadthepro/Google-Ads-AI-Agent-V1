"""Independent review by the Challenger AI.

The Challenger reviews important Main AI outputs with the evidence they were built from, and returns structured findings (agreement,
objections with evidence and severity, recommended corrections, confidence, limitations). It is a reviewer, not an authority: it never
approves anything, never overrides deterministic rules or validation, and never triggers a Google Ads action. Its findings are shown
next to the output (and high-severity objections become listed risks on the approval).

When the Challenger is off, not configured, not enabled for this kind of output, or its request cannot be served, the result says
"independent review not performed" and why. Nothing is presented as reviewed when it was not.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import IntegrationNotConfigured
from app.services import ai_roles, ai_service


def not_performed(reason: str, kind: str) -> dict:
    return {"performed": False, "reason": reason, "kind": kind, "verdict": None, "objections": [], "agreements": [],
            "independence": "not performed"}


def _reviewer() -> dict:
    st = ai_roles.challenger_status()
    return {"provider": st.get("provider"), "model": st.get("model"), "independence": st["independence"],
            "same_provider": st["same_provider"], "same_model": st["same_model"]}


def review_draft(db: Session, draft: dict, evidence: dict, *, kind: str, task_id: str | None) -> tuple[dict, str | None]:
    """CHALLENGE a draft (campaign / ad group / ad) against its evidence. Returns (report, ai_run_id or None)."""
    st = ai_roles.challenger_status()
    if not st["available"]:
        return not_performed(f"The Challenger AI is not available: {st['reason'] or 'not configured'}", kind), None
    if not st["review_policy"].get(kind, False):
        return not_performed("Independent review of this kind of output is turned off in the Challenger AI settings", kind), None
    try:
        parsed, run = ai_service.generate(db, "CHALLENGE", {"draft": draft, **evidence}, task_id=task_id, model_role="challenger")
    except IntegrationNotConfigured as exc:
        return not_performed(exc.message, kind), None
    except ai_service.AIOutputInvalid as exc:
        return {**not_performed(f"The Challenger AI could not complete the review: {exc.message}", kind),
                "error_kind": (exc.details or {}).get("error_kind")}, None
    return {**parsed.model_dump(), "performed": True, "kind": kind, "reviewer": _reviewer(), "run_id": run.id,
            "independence": _reviewer()["independence"]}, run.id


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
    return {**parsed.model_dump(), "performed": True, "kind": "performance_diagnosis", "reviewer": _reviewer(), "run_id": run.id}
