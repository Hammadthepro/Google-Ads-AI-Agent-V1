"""Live research events: a durable, ordered record of what the research agent is actually doing.

Every event comes from real work (a URL being fetched, a page parsed, a provider answering or failing). Nothing here is simulated, and
there is no percentage: the UI shows what happened and what is pending. Events are persisted, so the live view survives a refresh.

Binding: `with bind(report_id, client_id=..., business_id=...)` makes every `emit()` in that context (and in threads started through
`propagate()`) belong to that audit. Outside a binding `emit()` does nothing, so instrumented code paths stay safe to call anywhere.
"""
from __future__ import annotations

import contextvars
import logging
import threading
from contextlib import contextmanager
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.core.util import redact
from app.db.session import session_scope
from app.models import ResearchEvent

logger = logging.getLogger(__name__)

KINDS = {
    "audit_started", "audit_completed", "audit_failed",
    "stage_started", "stage_completed", "stage_failed", "stage_skipped", "stage_waiting",
    "source_discovered", "url_crawling", "page_extracted", "page_failed", "crawl_blocked",
    "competitor_identified", "keyword_discovery", "location_identified", "web_result",
    "research_stored", "evidence_validated", "provider_unavailable", "provider_failed", "note",
}
LEVELS = ("info", "warn", "error")

_binding: contextvars.ContextVar[dict | None] = contextvars.ContextVar("research_binding", default=None)
_stage: contextvars.ContextVar[str] = contextvars.ContextVar("research_stage", default="")
_lock = threading.Lock()
_next: dict[str, int] = {}


_audit: contextvars.ContextVar[str | None] = contextvars.ContextVar("research_audit", default=None)


@contextmanager
def bind(report_id: str | None, *, client_id: str | None = None, business_id: str | None = None, run_id: str | None = None):
    """Bind events to a run. Report runs pass the report id (it is also the run id); standalone audit runs pass `run_id` only."""
    token = _binding.set({"report_id": report_id, "run_id": run_id or report_id, "client_id": client_id, "business_id": business_id})
    try:
        yield
    finally:
        _binding.reset(token)


@contextmanager
def staged(stage: str):
    """Events emitted inside this block without an explicit stage belong to `stage` (e.g. crawler events inside the website audit)."""
    token = _stage.set(stage)
    try:
        yield
    finally:
        _stage.reset(token)


@contextmanager
def for_audit(audit_id: str | None):
    """Events emitted inside this block belong to one audit row (shown on that audit's detail page)."""
    token = _audit.set(audit_id)
    try:
        yield
    finally:
        _audit.reset(token)


def in_stage(stage: str, fn: Callable, audit_id: str | None = None) -> Callable:
    """A callable that runs `fn` inside `staged(stage)` (and `for_audit(audit_id)`) with the caller's binding (for worker threads)."""
    def run(*a, **kw):
        with staged(stage), for_audit(audit_id):
            return fn(*a, **kw)
    return propagate(run)


def current_report_id() -> str | None:
    """The run the current code belongs to (report id for report runs). Used for per-run provider budgets."""
    b = _binding.get()
    return b["run_id"] if b else None


def propagate(fn: Callable) -> Callable:
    """Wrap a callable so it runs with the caller's binding inside another thread (ThreadPoolExecutor does not copy context)."""
    ctx = contextvars.copy_context()
    return lambda *a, **kw: ctx.run(fn, *a, **kw)


def _seq(db: Session, run_id: str) -> int:
    with _lock:
        if run_id not in _next:
            _next[run_id] = (db.scalar(select(func.max(ResearchEvent.seq)).where(ResearchEvent.run_id == run_id)) or 0) + 1
        n = _next[run_id]
        _next[run_id] = n + 1
        return n


def emit(kind: str, title: str, *, stage: str = "", level: str = "info", **data: Any) -> None:
    """Record one real event for the bound audit. Never raises: telemetry must not break research."""
    b = _binding.get()
    if b is None:
        return
    if kind not in KINDS:
        raise ValueError(f"unknown research event kind {kind}")  # a programming error, caught by tests
    level = level if level in LEVELS else "info"
    stage = stage or _stage.get()
    payload = redact({k: v for k, v in data.items() if v is not None})
    for attempt in range(3):
        try:
            with session_scope() as db:
                db.add(ResearchEvent(audit_report_id=b["report_id"], run_id=b["run_id"], audit_id=_audit.get(), client_id=b["client_id"],
                                     business_id=b["business_id"], seq=_seq(db, b["run_id"]), kind=kind, stage=stage, level=level,
                                     title=title[:300], data=payload, created_at=clock.real_now()))
            return
        except IntegrityError:  # another process wrote the same sequence number: resynchronise and try again
            with _lock:
                _next.pop(b["run_id"], None)
        except Exception:  # noqa: BLE001
            logger.exception("research event not recorded (%s)", kind)
            return
    logger.warning("research event %s dropped after sequence conflicts", kind)


def forget(report_id: str) -> None:
    with _lock:
        _next.pop(report_id, None)


def serialize(e: ResearchEvent) -> dict:
    return {"seq": e.seq, "kind": e.kind, "stage": e.stage, "level": e.level, "title": e.title, "data": e.data,
            "at": e.created_at.isoformat()}


def list_events(db: Session, report_id: str | None = None, *, after: int = 0, limit: int = 500, audit_id: str | None = None,
                run_id: str | None = None) -> list[dict]:
    """Events of a report, of one run, or of one audit, in order. `after` is the last seq the caller already has."""
    q = select(ResearchEvent).where(ResearchEvent.seq > after)
    if audit_id:
        q = q.where(ResearchEvent.audit_id == audit_id)
    elif run_id:
        q = q.where(ResearchEvent.run_id == run_id)
    else:
        q = q.where(ResearchEvent.audit_report_id == report_id)
    return [serialize(e) for e in db.scalars(q.order_by(ResearchEvent.seq).limit(max(1, min(limit, 2000))))]


def last_activity(db: Session, audit_id: str):
    return db.scalar(select(func.max(ResearchEvent.created_at)).where(ResearchEvent.audit_id == audit_id))
