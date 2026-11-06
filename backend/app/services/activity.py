"""Append-only activity log. Details are redacted before storage."""
from __future__ import annotations

from contextvars import ContextVar

from sqlalchemy.orm import Session

from app.core.util import redact
from app.models import ActivityLog

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
ip_var: ContextVar[str] = ContextVar("ip", default="")


def log(db: Session, action: str, *, actor=None, entity: str = "", entity_id: str = "", client_id: str | None = None,
        details: dict | None = None, integration_mode: str = "") -> ActivityLog:
    row = ActivityLog(actor_id=getattr(actor, "id", None), actor_email=getattr(actor, "email", "system"),
                      client_id=client_id, action=action, entity=entity, entity_id=entity_id or "",
                      details=redact(details or {}), integration_mode=integration_mode,
                      request_id=request_id_var.get(), ip=ip_var.get())
    db.add(row)
    return row
