"""Celery app + beat schedule. Start with:
    celery -A app.workers.celery_app worker -l info
    celery -A app.workers.celery_app beat -l info
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings
from app.workers import jobs

s = get_settings()
celery = Celery("gaa", broker=s.REDIS_URL, backend=None)
celery.conf.update(task_always_eager=s.CELERY_TASK_ALWAYS_EAGER, task_acks_late=True, worker_prefetch_multiplier=1,
                   broker_connection_retry_on_startup=True, timezone="UTC", task_ignore_result=True,
                   broker_transport_options={"socket_connect_timeout": 2})


@celery.task(name="gaa.run_task")
def run_task_job(task_id: str) -> str:
    from app.services import tasks_service
    return tasks_service.run_task(task_id)


for _name in ("daily_sync", "recheck_tracking", "hourly_brake", "optimize_all", "recheck_fixes", "approvals_sweep",
              "weekly_reports", "evaluate_predictions"):
    celery.task(name=f"gaa.{_name}")(getattr(jobs, _name))

celery.conf.beat_schedule = {
    "daily-sync": {"task": "gaa.daily_sync", "schedule": crontab(hour=6, minute=17)},
    "recheck-tracking": {"task": "gaa.recheck_tracking", "schedule": crontab(hour=6, minute=47)},
    "optimize": {"task": "gaa.optimize_all", "schedule": crontab(hour=7, minute=13)},
    "hourly-brake": {"task": "gaa.hourly_brake", "schedule": crontab(minute=23)},
    "recheck-fixes": {"task": "gaa.recheck_fixes", "schedule": crontab(hour=8, minute=5)},
    "approvals-sweep": {"task": "gaa.approvals_sweep", "schedule": crontab(minute=41)},
    "weekly-reports": {"task": "gaa.weekly_reports", "schedule": crontab(day_of_week=1, hour=9, minute=11)},
    "predictions": {"task": "gaa.evaluate_predictions", "schedule": crontab(hour=10, minute=3)},
}
app = celery
