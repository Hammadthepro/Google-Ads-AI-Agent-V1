"""Approval workflow bound to the exact payload version (SHA-256 of canonical JSON)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import registry
from app.core import clock
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.util import payload_hash
from app.models import Approval, ApprovalEvent, Business, Client, User, ValidationRun
from app.services import activity, auth_service, settings_service

TYPES = ("CREATE", "ENABLE", "UPDATE", "PAUSE", "BUDGET", "NEGATIVES", "CONVERSION_ACTION", "EXPERIMENT_START",
         "EXPERIMENT_WINNER", "INTERNAL")
NO_SELF_APPROVAL = {"CREATE", "ENABLE", "BUDGET", "EXPERIMENT_WINNER"}
OPEN_STATES = ("PENDING_APPROVAL", "VALIDATION_FAILED")


def _event(db, a: Approval, event: str, actor: User | None = None, reason: str = "", before=None, after=None):
    db.add(ApprovalEvent(approval_id=a.id, event=event, actor_id=getattr(actor, "id", None),
                         actor_email=getattr(actor, "email", "system"), payload_hash=a.payload_hash,
                         payload_version=a.payload_version, reason=reason, before=before, after=after))


def create(db: Session, *, client_id: str, approval_type: str, title: str, payload: dict, requested_by: User | None,
           business_id: str | None = None, account_id: str | None = None, task_id: str | None = None,
           current_state: dict | None = None, proposed_state: dict | None = None, reason: str = "",
           expected_effect: str = "", risks: list | None = None, validation: ValidationRun | None = None,
           ai_explanation: str = "", context: dict | None = None, parent_operation_id: str | None = None,
           version: int = 1, supersedes_id: str | None = None) -> Approval:
    if approval_type not in TYPES:
        raise ValidationFailed(f"Unknown approval type {approval_type}")
    hours = float(settings_service.get_value("approvals.expiry_hours", 72, db))
    ads = registry.optional("google_ads")  # preparation does not need the Google Ads integration; execution does
    a = Approval(client_id=client_id, business_id=business_id, google_ads_account_id=account_id, task_id=task_id,
                 approval_type=approval_type, title=title, payload=payload, payload_version=version,
                 payload_hash=payload_hash(payload), current_state=current_state or {}, proposed_state=proposed_state or {},
                 reason=reason, expected_effect=expected_effect, risks=risks or [],
                 validation_run_id=validation.id if validation else None,
                 validation_summary={"passed": validation.passed, "errors": validation.errors, "warnings": validation.warnings}
                 if validation else {"passed": True, "errors": [], "warnings": []},
                 ai_explanation=ai_explanation, context=context or {}, requested_by_id=getattr(requested_by, "id", None),
                 status="PENDING_APPROVAL" if (validation is None or validation.passed) else "VALIDATION_FAILED",
                 expires_at=clock.now() + timedelta(hours=hours), parent_operation_id=parent_operation_id,
                 supersedes_id=supersedes_id,
                 integration_mode=("REAL" if ads.is_real else "MOCK") if ads else "NONE",
                 source_type=ads.source if ads else "NONE")
    db.add(a)
    db.flush()
    if validation:
        validation.approval_id = a.id
    _event(db, a, "CREATED", requested_by)
    activity.log(db, "approval.created", actor=requested_by, entity="approval", entity_id=a.id, client_id=client_id,
                 details={"type": approval_type, "title": title, "hash": a.payload_hash, "status": a.status},
                 integration_mode=a.integration_mode)
    return a


def get(db: Session, approval_id: str) -> Approval:
    a = db.get(Approval, approval_id)
    if not a:
        raise NotFound("Approval not found")
    return a


def _expire_if_needed(db, a: Approval) -> None:
    if a.status == "PENDING_APPROVAL" and a.expires_at and a.expires_at.replace(tzinfo=a.expires_at.tzinfo) < clock.now():
        a.status = "EXPIRED"
        _event(db, a, "EXPIRED")


def approve(db: Session, actor: User, approval_id: str, seen_hash: str, reason: str = "") -> Approval:
    auth_service.require(actor, "approvals.decide")
    a = get(db, approval_id)
    auth_service.require_client_access(db, actor, a.client_id)
    _expire_if_needed(db, a)
    if a.status != "PENDING_APPROVAL":
        raise Conflict(f"Approval is {a.status}; only PENDING_APPROVAL can be approved")
    if a.context.get("requires_super_admin"):
        auth_service.require(actor, "approvals.decide_privileged")
    allow_self = settings_service.get_value("approvals.allow_self_approval", False, db)
    if not allow_self and a.approval_type in NO_SELF_APPROVAL and a.requested_by_id == actor.id:
        raise Forbidden("You cannot approve your own request for this change type (separation of duties)")
    stored = payload_hash(a.payload)
    if stored != a.payload_hash:
        a.status = "INVALIDATED"
        _event(db, a, "INVALIDATED", actor, "Stored payload no longer matches its hash")
        db.commit()
        raise Conflict("Payload changed after it was submitted — approval invalidated; a new approval is required")
    if seen_hash != a.payload_hash:
        raise Conflict("The version you reviewed is not the current version — reload before approving",
                       details={"current_hash": a.payload_hash})
    a.status, a.decided_by_id, a.decided_at = "APPROVED", actor.id, clock.now()
    a.approved_payload_hash, a.decision_reason = a.payload_hash, reason
    _event(db, a, "APPROVED", actor, reason)
    activity.log(db, "approval.approved", actor=actor, entity="approval", entity_id=a.id, client_id=a.client_id,
                 details={"hash": a.payload_hash, "version": a.payload_version, "type": a.approval_type})
    return a


def reject(db: Session, actor: User, approval_id: str, reason: str, *, changes_requested: bool = False) -> Approval:
    auth_service.require(actor, "approvals.decide")
    a = get(db, approval_id)
    auth_service.require_client_access(db, actor, a.client_id)
    if a.status not in OPEN_STATES:
        raise Conflict(f"Approval is {a.status}")
    if len((reason or "").strip()) < 5:
        raise ValidationFailed("A reason is required")
    a.status = "CHANGES_REQUESTED" if changes_requested else "REJECTED"
    a.decided_by_id, a.decided_at, a.decision_reason = actor.id, clock.now(), reason
    _event(db, a, a.status, actor, reason)
    activity.log(db, f"approval.{a.status.lower()}", actor=actor, entity="approval", entity_id=a.id, client_id=a.client_id,
                 details={"reason": reason})
    return a


def edit_payload(db: Session, actor: User, approval_id: str, new_payload: dict, revalidate) -> Approval:
    """Inline edit = new version. The old approval is INVALIDATED; the new one must be validated and approved."""
    auth_service.require(actor, "approvals.submit")
    old = get(db, approval_id)
    auth_service.require_client_access(db, actor, old.client_id)
    if old.status not in ("PENDING_APPROVAL", "VALIDATION_FAILED", "CHANGES_REQUESTED", "APPROVED"):
        raise Conflict(f"Approval is {old.status} and cannot be edited")
    if payload_hash(new_payload) == old.payload_hash:
        raise ValidationFailed("No change in payload")
    business = db.get(Business, old.business_id) if old.business_id else None
    validation = revalidate(new_payload, business) if business else None
    new = create(db, client_id=old.client_id, approval_type=old.approval_type, title=old.title, payload=new_payload,
                 requested_by=actor, business_id=old.business_id, account_id=old.google_ads_account_id, task_id=old.task_id,
                 current_state=old.current_state, proposed_state=old.proposed_state, reason=old.reason,
                 expected_effect=old.expected_effect, risks=old.risks, validation=validation, ai_explanation=old.ai_explanation,
                 context=old.context, parent_operation_id=old.parent_operation_id, version=old.payload_version + 1,
                 supersedes_id=old.id)
    before = old.status
    old.status, old.superseded_by_id = "INVALIDATED", new.id
    _event(db, old, "INVALIDATED", actor, "Payload edited; superseded by new version", before={"status": before},
           after={"superseded_by": new.id})
    _event(db, new, "EDITED", actor, "New version from inline edit", before={"payload": old.payload}, after={"payload": new_payload})
    activity.log(db, "approval.edited", actor=actor, entity="approval", entity_id=new.id, client_id=old.client_id,
                 details={"old": old.id, "old_hash": old.payload_hash, "new_hash": new.payload_hash})
    return new


def expiry_sweep(db: Session) -> dict:
    remind_h = float(settings_service.get_value("approvals.reminder_hours", 24, db))
    now = clock.now()
    reminded = expired = 0
    for a in db.scalars(select(Approval).where(Approval.status == "PENDING_APPROVAL")):
        exp = a.expires_at.replace(tzinfo=a.expires_at.tzinfo) if a.expires_at else None
        if exp and exp < now:
            a.status = "EXPIRED"
            _event(db, a, "EXPIRED")
            client = db.get(Client, a.client_id)
            backup = db.get(User, client.backup_owner_user_id) if client and client.backup_owner_user_id else None
            succ = create(db, client_id=a.client_id, approval_type=a.approval_type, title=a.title, payload=a.payload,
                          requested_by=None, business_id=a.business_id, account_id=a.google_ads_account_id, task_id=a.task_id,
                          current_state=a.current_state, proposed_state=a.proposed_state, reason=a.reason,
                          expected_effect=a.expected_effect, risks=a.risks, ai_explanation=a.ai_explanation,
                          context={**a.context, "escalated_from": a.id}, parent_operation_id=a.parent_operation_id,
                          version=a.payload_version, supersedes_id=a.id)
            succ.requested_by_id = a.requested_by_id
            succ.escalated_to_id = backup.id if backup else None
            a.superseded_by_id = succ.id
            registry.get("notifier").send(recipients=[backup.email] if backup else [client.backup_contact or "team"],
                                          subject=f"Approval escalated: {a.title}",
                                          body=f"An approval was not acted on in time and was escalated to you. Approval {succ.id}.",
                                          client_id=a.client_id, category="approval_escalation")
            expired += 1
        elif not a.reminder_sent_at and a.created_at.replace(tzinfo=a.created_at.tzinfo) + timedelta(hours=remind_h) < now:
            a.reminder_sent_at = now
            client = db.get(Client, a.client_id)
            owner = db.get(User, client.owner_user_id) if client and client.owner_user_id else None
            registry.get("notifier").send(recipients=[owner.email] if owner else [client.owner_contact or "team"],
                                          subject=f"Reminder: approval waiting — {a.title}",
                                          body=f"Approval {a.id} is waiting for a decision.", client_id=a.client_id,
                                          category="approval_reminder")
            _event(db, a, "REMINDER_SENT")
            reminded += 1
    return {"reminded": reminded, "expired": expired}


def serialize(a: Approval, db: Session | None = None, with_events: bool = False) -> dict:
    data = {"id": a.id, "client_id": a.client_id, "business_id": a.business_id, "task_id": a.task_id,
            "approval_type": a.approval_type, "title": a.title, "payload": a.payload, "payload_version": a.payload_version,
            "payload_hash": a.payload_hash, "current_state": a.current_state, "proposed_state": a.proposed_state,
            "reason": a.reason, "expected_effect": a.expected_effect, "risks": a.risks, "validation": a.validation_summary,
            "ai_explanation": a.ai_explanation, "context": a.context, "requested_by_id": a.requested_by_id,
            "status": a.status, "decided_by_id": a.decided_by_id, "decided_at": a.decided_at.isoformat() if a.decided_at else None,
            "decision_reason": a.decision_reason, "approved_payload_hash": a.approved_payload_hash,
            "expires_at": a.expires_at.isoformat() if a.expires_at else None, "superseded_by_id": a.superseded_by_id,
            "supersedes_id": a.supersedes_id, "parent_operation_id": a.parent_operation_id, "escalated_to_id": a.escalated_to_id,
            "integration_mode": a.integration_mode, "source_type": a.source_type, "created_at": a.created_at.isoformat()}
    if db is not None:
        users = {u.id: u.email for u in db.scalars(select(User).where(User.id.in_([x for x in (a.requested_by_id, a.decided_by_id) if x])))}
        data["requested_by_email"] = users.get(a.requested_by_id, "system")
        data["decided_by_email"] = users.get(a.decided_by_id)
    if with_events and db is not None:
        data["events"] = [{"event": e.event, "actor": e.actor_email, "at": e.created_at.isoformat(), "reason": e.reason,
                           "payload_hash": e.payload_hash, "version": e.payload_version}
                          for e in db.scalars(select(ApprovalEvent).where(ApprovalEvent.approval_id == a.id).order_by(ApprovalEvent.created_at))]
    return data
