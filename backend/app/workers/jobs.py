"""Plain job functions (called by Celery tasks, the CLI, and TEST_MODE time-advance)."""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.core import clock
from app.db.session import session_scope
from app.models import Client, JobRun

log = logging.getLogger("gaa.jobs")


def _run(job: str, fn):
    with session_scope() as db:
        row = JobRun(job=job, status="RUNNING", started_at=clock.now())
        db.add(row)
        db.flush()
        rid = row.id
    try:
        with session_scope() as db:
            result = fn(db)
        status, err = "SUCCEEDED", ""
    except Exception as exc:  # noqa: BLE001 — failures are recorded and surface in the emergency brake
        log.exception("job %s failed", job)
        result, status, err = None, "FAILED", f"{type(exc).__name__}: {str(exc)[:500]}"
    with session_scope() as db:
        row = db.get(JobRun, rid)
        row.status, row.error, row.finished_at = status, err, clock.now()
        row.details = result if isinstance(result, dict) else {"result": str(result)[:500]}
    return {"job": job, "status": status, "error": err}


def daily_sync():
    from app.services import monitoring
    return _run("monitoring.daily_sync_all", monitoring.run_daily_all)


def audit_measurement():
    from app.services import monitoring
    return _run("measurement.audit_all", lambda db: {"businesses": monitoring.audit_measurement_all(db)})


def hourly_brake():
    from app.services import emergency
    return _run("emergency.hourly_check", lambda db: {"events": emergency.hourly_check(db)})


def optimize_all():
    from app.services import optimization
    return _run("optimization.run_all", lambda db: {c.name: optimization.run_for_client(db, c) for c in db.scalars(select(Client))})


def insights_all():
    from app.services import performance_insights
    return _run("insights.run_all", performance_insights.run_all)


def recheck_fixes():
    from app.services import fixes_service
    return _run("fixes.recheck_measurable", lambda db: {"checked": fixes_service.recheck_measurable(db)})


def approvals_sweep():
    from app.services import approvals
    return _run("approvals.expiry_sweep", approvals.expiry_sweep)


def weekly_reports():
    from app.services import reporting
    return _run("reporting.weekly_reviews", lambda db: {c.name: reporting.generate(db, c).id for c in db.scalars(select(Client))})


def evaluate_predictions():
    from app.services import learning
    return _run("learning.evaluate_predictions", lambda db: {"evaluated": learning.evaluate_predictions(db)})


def run_daily_cycle() -> list[dict]:
    """One daily cycle: the same jobs Celery beat runs on a schedule."""
    out = [daily_sync(), audit_measurement(), optimize_all(), insights_all(), hourly_brake(), recheck_fixes(), approvals_sweep(), evaluate_predictions()]
    if clock.today().weekday() == 0:
        out.append(weekly_reports())
    return out
