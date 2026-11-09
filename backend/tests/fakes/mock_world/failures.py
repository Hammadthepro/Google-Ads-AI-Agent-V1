"""Seeded failure injection shared by every mock adapter."""
from __future__ import annotations

from sqlalchemy import select

from app.adapters.base import FAILURE_CLASSES, AdapterTimeout
from app.db.session import session_scope
from app.models import MockFailureInjection


class EmptyResult(Exception):
    """Signal to the adapter to return an empty (valid) result."""


class PartialResult(Exception):
    """Signal to the adapter to return partial data."""


def check_failure(adapter: str, method: str, entity: str = "", *, phase: str = "before") -> str | None:
    """Raise the injected failure for (adapter, method, entity), if one is active.

    The usage counter is committed BEFORE raising. phase="after" serves `timeout_after_commit`,
    which simulates a write whose outcome is unknown. Unknown failure kinds are returned to the caller
    (e.g. "invalid_json" for the mock AI provider).
    """
    triggered: str | None = None
    with session_scope() as db:
        rows = db.scalars(select(MockFailureInjection).where(
            MockFailureInjection.adapter == adapter, MockFailureInjection.is_active.is_(True))
            .order_by(MockFailureInjection.created_at)).all()
        for row in rows:
            if row.method not in ("*", method):
                continue
            if row.entity and row.entity not in entity:
                continue
            if (phase == "after") != (row.failure == "timeout_after_commit"):
                continue
            row.used += 1
            if row.times != -1 and row.used >= row.times:
                row.is_active = False
            triggered = row.failure
            break
    if triggered is None:
        return None
    if triggered == "timeout_after_commit":
        raise AdapterTimeout("Simulated timeout after the request reached the server", adapter=adapter,
                             details={"injected": True, "outcome": "unknown"})
    if triggered == "empty":
        raise EmptyResult()
    if triggered == "partial_data":
        raise PartialResult()
    cls = FAILURE_CLASSES.get(triggered)
    if cls:
        raise cls(f"Simulated {triggered} in {adapter}.{method}", adapter=adapter, details={"injected": True})
    return triggered
