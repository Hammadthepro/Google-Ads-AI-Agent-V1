"""Audit jobs started from the Research & audits page (website, business presence, competitors, keyword research).

Lifecycle: every requested audit is created QUEUED, then RUNNING, then COMPLETED / FAILED / SKIPPED, each step committed on its own,
with a start and finish time, a data source, a summary or the real error, and its own live research events. A run that dies leaves no
audit RUNNING: the runner marks unfinished audits FAILED when it ends, and `recover_stale` fails audits that show no progress for
STALE_MINUTES (for example after a restart). Keyword research never starts other audits by itself: the caller chooses, per source,
whether to reuse what exists or refresh it (`keyword_sources` says what exists and whether it is usable).
"""
from __future__ import annotations

import logging
import threading
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import Conflict, ValidationFailed
from app.core.util import new_id
from app.db.session import SessionLocal
from app.models import Audit, AuditReport, Business
from app.services import audits, research_events, settings_service

logger = logging.getLogger(__name__)
STALE_MINUTES = 20
ACTIVE = ("QUEUED", "RUNNING")
SOURCES = ("WEBSITE", "GBP", "COMPETITOR")
LABEL = {"WEBSITE": "Website audit", "GBP": "Business presence (GBP) audit", "COMPETITOR": "Competitor research"}


# --------------------------------------------------------------------------------------------- what exists
def _quality(kind: str, r: dict) -> tuple[bool, str]:
    if kind == "WEBSITE":
        n = len(r.get("page_inventory") or [])
        return (n > 0, f"{n} page(s) read, {len(r.get('service_inventory') or [])} service(s) found" if n else "No page was read")
    if kind == "GBP":
        if r.get("places_available") is False:
            geo = r.get("geo") or {}
            return (bool(geo.get("available")), f"Map data only: {(geo.get('density') or {}).get('total', 0)} nearby business(es); no ratings or reviews"
                    if geo.get("available") else "No presence data")
        return (bool(r.get("client")), "Google Places listing found" if r.get("client") else "No Google listing found")
    if kind == "COMPETITOR":
        if r.get("skipped"):
            return (False, r.get("reason") or "No competitors were analysed")
        return (True, f"{len(r.get('competitors') or [])} competitor(s) analysed")
    return (True, "")


def keyword_sources(db: Session, biz: Business) -> dict:
    """For each research source keyword research can use: the latest finished audit, its age, and whether it is usable, outdated,
    insufficient, missing or currently running. Nothing is started here."""
    max_age = int(settings_service.get_value("agent.research_max_age_days", 30, db))
    now = clock.real_now()
    out = []
    for kind in SOURCES:
        running = db.scalar(select(Audit).where(Audit.business_id == biz.id, Audit.audit_type == kind, Audit.status.in_(ACTIVE)).limit(1))
        latest = db.scalar(select(Audit).where(Audit.business_id == biz.id, Audit.audit_type == kind, Audit.status.in_(("COMPLETED", "SKIPPED")))
                           .order_by(Audit.finished_at.desc().nullslast(), Audit.created_at.desc()).limit(1))
        item = {"type": kind, "label": LABEL[kind], "audit_id": latest.id if latest else None, "running_audit_id": running.id if running else None}
        if latest is None:
            item.update(state="running" if running else "missing", reason="Currently running" if running else "Never run for this business")
        else:
            done = latest.finished_at or latest.created_at
            age = (now - done).days
            ok, quality = _quality(kind, latest.result or {})
            edited = biz.updated_at is not None and biz.updated_at > done
            state = "insufficient" if not ok else "outdated" if (age > max_age or edited) else "usable"
            reason = quality + ("" if state != "outdated" else
                                "; the business profile was edited after this audit" if edited else f"; {age} days old (limit {max_age})")
            item.update(state=state, reason=reason, finished_at=done.isoformat(), age_days=age)
        out.append(item)
    usable = [x for x in out if x["state"] == "usable"]
    return {"business_id": biz.id, "sources": out, "max_age_days": max_age,
            "all_usable": len(usable) == len(SOURCES),
            "missing_or_weak": [x["type"] for x in out if x["state"] != "usable"],
            "summary": ("All research sources are current: keyword research can use them as they are." if len(usable) == len(SOURCES) else
                        "Some sources are missing, outdated or insufficient: " + "; ".join(f"{x['label']}: {x['reason']}" for x in out if x["state"] != "usable"))}


# --------------------------------------------------------------------------------------------- start
def start(db: Session, biz: Business, actor_id: str | None, *, types: list[str], keyword_mode: str | None = None,
          refresh: list[str] | None = None, confirm: bool = False, background: bool = True) -> list[Audit]:
    """Create the requested audit rows (QUEUED) and run them. Keyword research needs an explicit choice:
    keyword_mode='reuse' uses the existing source audits; keyword_mode='refresh' runs only the sources listed in `refresh`, after `confirm`."""
    types = list(dict.fromkeys(types))
    reuse: dict[str, str] = {}
    if "KEYWORD" in types:
        if keyword_mode not in ("reuse", "refresh"):
            raise Conflict("Keyword research needs a choice: use the research that already exists, or run fresh audits first.",
                           details={"choice_required": True, "sources": keyword_sources(db, biz)})
        refresh = [r for r in (refresh or []) if r in SOURCES]
        if keyword_mode == "refresh":
            if not refresh:
                raise ValidationFailed("Choose at least one source to refresh (website, business presence or competitors), or use the existing data.")
            if not confirm:
                raise Conflict("Running fresh audits needs your confirmation.", details={"confirm_required": True, "refresh": refresh})
        else:
            refresh = []
        srcs = keyword_sources(db, biz)
        reuse = {x["type"]: x["audit_id"] for x in srcs["sources"] if x["type"] not in refresh and x["audit_id"]}
        types = [t for t in types if t != "KEYWORD" and t not in refresh] + refresh + ["KEYWORD"]
    if any(t not in SOURCES + ("KEYWORD",) for t in types):
        raise ValidationFailed("Unknown audit type")
    busy = db.scalar(select(Audit).where(Audit.business_id == biz.id, Audit.audit_type.in_(types), Audit.status.in_(ACTIVE)).limit(1))
    if busy:
        raise Conflict(f"A {busy.audit_type.lower()} audit is already {busy.status.lower()} for this business. Open it to follow its progress.",
                       details={"audit_id": busy.id})
    run_id = new_id()
    rows = [Audit(client_id=biz.client_id, business_id=biz.id, audit_type=t, status="QUEUED", created_by_id=actor_id, started_at=clock.real_now(),
                  result={}) for t in types]
    for r in rows:
        db.add(r)
    db.commit()
    ids = {r.audit_type: r.id for r in rows}
    args = (run_id, biz.id, ids, reuse)
    if background:
        threading.Thread(target=execute, args=args, daemon=True, name=f"audit-run-{run_id[:8]}").start()
    else:
        execute(*args)
        for r in rows:
            db.refresh(r)
    return rows


def _reused(db: Session, audit_id: str | None) -> tuple[dict, dict | None]:
    if not audit_id:
        return {}, None
    a = db.get(Audit, audit_id)
    if a is None:
        return {}, None
    return a.result or {}, {"audit_id": a.id, "type": a.audit_type, "finished_at": (a.finished_at or a.created_at).isoformat(), "status": a.status}


def execute(run_id: str, business_id: str, ids: dict[str, str], reuse: dict[str, str]) -> None:
    db = SessionLocal()
    try:
        biz = db.get(Business, business_id)
        with research_events.bind(None, client_id=biz.client_id, business_id=biz.id, run_id=run_id):
            research_events.emit("audit_started", f"Research started for {biz.name}", business=biz.name, website=biz.website_url or None,
                                 audits=list(ids), reused=list(reuse))
            ctx = audits.build_context(db, biz)
            par = {k: v for k, v in ids.items() if k in SOURCES}
            fresh = audits.run_jobs(ctx, par, task_id=None) if par else {}
            if "KEYWORD" in ids:
                kid = ids["KEYWORD"]
                audits.mark_running(kid)
                with research_events.for_audit(kid), research_events.staged("keywords"):
                    research_events.emit("stage_started", audits.STAGE_TITLE["KEYWORD"], stage="keywords")
                    inputs = {}
                    data = {}
                    for kind in ("WEBSITE", "COMPETITOR", "GBP"):
                        if kind in fresh:
                            data[kind], inputs[kind] = fresh[kind], {"audit_id": ids[kind], "type": kind, "fresh": True}
                        else:
                            data[kind], inputs[kind] = _reused(db, reuse.get(kind))
                            research_events.emit("note", f"Using the existing {audits.STAGE_TITLE[kind].lower()} result" if inputs[kind] else
                                                 f"No {audits.STAGE_TITLE[kind].lower()} result is available; keyword research continues without it",
                                                 stage="keywords", level="info" if inputs[kind] else "warn")
                    w = data["WEBSITE"] if not data["WEBSITE"].get("error") else {}
                    budget = (biz.monthly_budget_usd or 0) / 30.4 or None
                    try:
                        kw = audits.keyword_research(db, ctx, w, data["COMPETITOR"], None, daily_budget=budget)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("keyword research failed")
                        kw = {"error": f"{type(exc).__name__}: {str(exc)[:300]}", "keywords": [], "source": "NONE"}
                    kw["inputs"] = inputs
                    db.commit()
                audits.finish(kid, "KEYWORD", kw)
            research_events.emit("audit_completed", "Research run finished")
    except Exception as exc:  # noqa: BLE001 - the run itself failed: say so on every unfinished audit
        logger.exception("audit run %s failed", run_id)
        audits.fail_unfinished(ids.values(), f"The audit run failed: {type(exc).__name__}: {str(exc)[:300]}")
    finally:
        audits.fail_unfinished(ids.values(), "The audit run ended before this audit finished.")
        research_events.forget(run_id)
        db.close()


# --------------------------------------------------------------------------------------------- recovery
def recover_stale(db: Session) -> int:
    """Fail audits and audit reports that are QUEUED/RUNNING with no recorded progress for STALE_MINUTES. Their process stopped."""
    cutoff = clock.real_now() - timedelta(minutes=STALE_MINUTES)
    n = 0
    reason = (f"Interrupted: no progress for {STALE_MINUTES} minutes. The process running this audit stopped (for example a server restart). "
              "Nothing was lost from earlier audits; run it again.")
    for a in db.scalars(select(Audit).where(Audit.status.in_(ACTIVE), Audit.started_at < cutoff)):
        last = research_events.last_activity(db, a.id)
        if last is not None and last > cutoff:
            continue
        a.status, a.error, a.finished_at, a.summary = "FAILED", reason, clock.real_now(), f"Failed: {reason}"
        n += 1
    for r in db.scalars(select(AuditReport).where(AuditReport.status == "RUNNING", AuditReport.started_at < cutoff)):
        last = db.scalar(select(research_events.ResearchEvent.created_at).where(research_events.ResearchEvent.audit_report_id == r.id)
                         .order_by(research_events.ResearchEvent.seq.desc()).limit(1))
        if last is not None and last > cutoff:
            continue
        r.status, r.error, r.finished_at = "FAILED", reason, clock.real_now()
        r.steps = [({**s, "status": "FAILED" if s["status"] == "RUNNING" else "SKIPPED"} if s["status"] in ("RUNNING", "PENDING") else s) for s in r.steps]
        n += 1
    if n:
        db.commit()
    return n
