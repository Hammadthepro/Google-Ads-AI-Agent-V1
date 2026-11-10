"""Redis/Celery are retained: Celery is the scheduler and out-of-process runner, Redis only its broker (and rate-limit counters)."""
from __future__ import annotations

from app.workers import jobs
from app.workers.celery_app import celery, run_task_job


def test_every_scheduled_job_is_a_registered_task_and_matches_a_job_function():
    sched = celery.conf.beat_schedule
    assert len(sched) == 8
    for entry in sched.values():
        name = entry["task"]
        assert name in celery.tasks, f"{name} is scheduled but not registered"
        assert hasattr(jobs, name.split(".", 1)[1]), f"{name} has no job function"
    assert "gaa.run_task" in celery.tasks


def test_redis_is_only_a_broker_nothing_persistent_and_results_are_not_stored():
    assert celery.conf.broker_url.startswith("redis://")
    assert celery.conf.result_backend in (None, "")  # no business data is kept in Redis
    assert celery.conf.task_ignore_result is True and celery.conf.task_acks_late is True


def test_user_started_tasks_run_in_process_when_eager_and_reach_the_service(monkeypatch):
    seen = []
    from app.services import tasks_service
    monkeypatch.setattr(tasks_service, "run_task", lambda task_id: seen.append(task_id) or "ok")
    assert celery.conf.task_always_eager is True  # tests/dev: CELERY_TASK_ALWAYS_EAGER
    assert run_task_job.apply_async(args=["t1"], retry=False).get() == "ok" and seen == ["t1"]


def test_job_failures_are_recorded_not_raised_so_one_bad_account_cannot_stop_the_schedule():
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models import JobRun
    out = jobs._run("test.failing_job", lambda db: 1 / 0)
    assert out["status"] == "FAILED" and "ZeroDivisionError" in out["error"]
    with session_scope() as db:
        assert db.scalar(select(JobRun.status).where(JobRun.job == "test.failing_job").order_by(JobRun.created_at.desc()).limit(1)) == "FAILED"


def test_rate_limiter_degrades_to_memory_when_redis_is_unreachable():
    from app.main import RateLimiter
    rl = RateLimiter(limit=2)
    rl.redis = None
    assert [rl.hit("k") for _ in range(3)] == [True, True, False]


def test_readyz_reports_redis_state_without_requiring_it():
    from fastapi.testclient import TestClient

    from app.main import app
    body = TestClient(app).get("/readyz").json()
    assert body["checks"]["database"] == "ok" and "redis" in body["checks"]
