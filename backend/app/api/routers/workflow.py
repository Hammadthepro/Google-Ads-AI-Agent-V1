"""Tasks, campaign plans, validation, approvals and execution."""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import registry
from app.api.deps import current_user, require
from app.core.config import get_settings
from app.core.errors import NotFound
from app.db.session import get_db
from app.models import AIRun, Approval, Business, CampaignPlan, Operation, Task, User, ValidationRun
from app.services import approvals, auth_service, execution, experiments, learning, tasks_service, validation

router = APIRouter()
log = logging.getLogger("gaa.workflow")


def dispatch(task_id: str) -> str:
    """Run via Celery when a broker is configured; otherwise (or on broker failure) run in a background thread."""
    if not get_settings().CELERY_TASK_ALWAYS_EAGER:
        try:
            from app.workers.celery_app import run_task_job
            run_task_job.apply_async(args=[task_id], retry=False, ignore_result=True)
            return "celery"
        except Exception as exc:  # noqa: BLE001
            log.warning("Celery dispatch failed (%s); running task inline", type(exc).__name__)
    threading.Thread(target=tasks_service.run_task, args=(task_id,), daemon=True).start()
    return "thread"


# ------------------------------------------------------------------ tasks
class TaskIn(BaseModel):
    task_type: str
    client_id: str
    business_id: str | None = None
    campaign_id: str | None = None
    ad_group_id: str | None = None
    ad_id: str | None = None
    fields: dict = {}
    human_plan: str = ""
    note: str = ""


@router.get("/tasks/types", tags=["tasks"])
def task_types(user: User = Depends(current_user)):
    return tasks_service.TASK_TYPES


@router.post("/tasks", tags=["tasks"])
def create_task(body: TaskIn, sync: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = tasks_service.create(db, user, body.model_dump())
    db.commit()
    if sync:
        tasks_service.run_task(t.id)
        db.expire_all()
        return tasks_service.serialize(db.get(Task, t.id))
    runner = dispatch(t.id)
    data = tasks_service.serialize(t)
    data["runner"] = runner
    return data


class BudgetDecisionIn(BaseModel):
    decision: str
    monthly_budget: float | None = None
    weekly_target: float | None = None
    note: str = ""


@router.post("/tasks/{task_id}/budget-decision", tags=["tasks"])
def budget_decision(task_id: str, body: BudgetDecisionIn, sync: bool = False, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    t = tasks_service.decide_budget(db, user, task_id, body.model_dump())
    db.commit()
    if sync:
        tasks_service.run_task(t.id)
        db.expire_all()
        return tasks_service.serialize(db.get(Task, t.id))
    runner = dispatch(t.id)
    return {**tasks_service.serialize(t), "runner": runner}


class IntakeIn(BaseModel):
    task_type: str = "BUILD_CAMPAIGN"
    client_id: str
    business_id: str
    fields: dict = {}


@router.post("/intake/validate", tags=["tasks"])
def intake_validate(body: IntakeIn, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    from app.models import Business, Client
    from app.services import intake
    auth_service.require_client_access(db, user, body.client_id)
    c, b = db.get(Client, body.client_id), db.get(Business, body.business_id)
    if not c or not b or b.client_id != c.id:
        raise NotFound("Client or business not found")
    return intake.validate(db, b, c, body.task_type, body.fields)


@router.get("/tasks", tags=["tasks"])
def list_tasks(client_id: str | None = None, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    ids = auth_service.accessible_client_ids(db, user)
    q = select(Task).order_by(Task.created_at.desc()).limit(200)
    if client_id:
        q = q.where(Task.client_id == client_id)
    return [tasks_service.serialize(t) for t in db.scalars(q) if ids is None or t.client_id in ids]


@router.get("/tasks/{task_id}", tags=["tasks"])
def get_task(task_id: str, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    t = db.get(Task, task_id)
    if not t:
        raise NotFound("Task not found")
    auth_service.require_client_access(db, user, t.client_id)
    return tasks_service.serialize(t)


# ------------------------------------------------------------------ plans
def _plan(p: CampaignPlan, db: Session) -> dict:
    runs = {r.id: r for r in db.scalars(select(AIRun).where(AIRun.id.in_([x for x in (p.draft_ai_run_id, p.challenger_ai_run_id) if x])))}
    meta = lambda rid: ({"provider": runs[rid].provider, "model": runs[rid].model, "prompt": f"{runs[rid].prompt_template_id}@{runs[rid].prompt_version}",  # noqa: E731
                         "attempt": runs[rid].attempt, "latency_ms": runs[rid].latency_ms} if rid in runs else None)
    return {"id": p.id, "task_id": p.task_id, "client_id": p.client_id, "business_id": p.business_id, "scope": p.scope,
            "version": p.version, "parent_plan_id": p.parent_plan_id, "change_summary": p.change_summary,
            "edited_by_id": p.edited_by_id, "competition": p.competition, "original": p.original, "draft": p.draft, "challenger": p.challenger, "human_comparison": p.human_comparison,
            "playbook_rule_ids": p.playbook_rule_ids, "status": p.status, "source_type": p.source_type,
            "draft_ai": meta(p.draft_ai_run_id), "challenger_ai": meta(p.challenger_ai_run_id), "created_at": p.created_at.isoformat()}


@router.get("/plans", tags=["planner"])
def list_plans(client_id: str | None = None, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    ids = auth_service.accessible_client_ids(db, user)
    q = select(CampaignPlan).order_by(CampaignPlan.created_at.desc()).limit(100)
    if client_id:
        q = q.where(CampaignPlan.client_id == client_id)
    return [_plan(p, db) for p in db.scalars(q) if ids is None or p.client_id in ids]


@router.get("/plans/{plan_id}", tags=["planner"])
def get_plan(plan_id: str, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    p = db.get(CampaignPlan, plan_id)
    if not p:
        raise NotFound("Plan not found")
    auth_service.require_client_access(db, user, p.client_id)
    return _plan(p, db)


@router.get("/plans/{plan_id}/versions", tags=["planner"])
def plan_versions(plan_id: str, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    from app.services import plan_editor
    p = db.get(CampaignPlan, plan_id)
    if not p:
        raise NotFound("Plan not found")
    auth_service.require_client_access(db, user, p.client_id)
    return [{"id": v.id, "version": v.version, "status": v.status, "change_summary": v.change_summary, "edited_by_id": v.edited_by_id,
             "created_at": v.created_at.isoformat(), "is_current": v.id == p.id} for v in plan_editor.versions(db, p)]


@router.get("/plans/{plan_id}/diff", tags=["planner"])
def plan_diff(plan_id: str, against: str = "original", user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    """Difference between the AI-consolidated original (default) or another version, and this version."""
    from app.services import plan_editor
    p = db.get(CampaignPlan, plan_id)
    if not p:
        raise NotFound("Plan not found")
    auth_service.require_client_access(db, user, p.client_id)
    base = p.original or p.draft
    if against != "original":
        other = db.get(CampaignPlan, against)
        if not other or other.task_id != p.task_id:
            raise NotFound("Version not found")
        base = other.draft
    return {"against": against, "version": p.version, **plan_editor.diff(base, p.draft, p.scope)}


class PlanEditIn(BaseModel):
    edits: list[dict]
    note: str = ""


@router.post("/approvals/{approval_id}/plan-edit", tags=["approvals"])
def plan_edit(approval_id: str, body: PlanEditIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.services import plan_editor
    out = plan_editor.edit_approval(db, user, approval_id, body.edits, body.note)
    db.commit()
    return out


@router.get("/ai-runs", tags=["planner"])
def ai_runs(task_id: str | None = None, user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    q = select(AIRun).order_by(AIRun.created_at.desc()).limit(100)
    if task_id:
        q = q.where(AIRun.task_id == task_id)
    return [{"id": r.id, "task_id": r.task_id, "purpose": r.purpose, "provider": r.provider, "model": r.model,
             "prompt": f"{r.prompt_template_id}@{r.prompt_version}", "attempt": r.attempt, "validation_ok": r.validation_ok,
             "error": r.error, "latency_ms": r.latency_ms, "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
             "created_at": r.created_at.isoformat()} for r in db.scalars(q)]


# ------------------------------------------------------------------ approvals
@router.get("/approvals", tags=["approvals"])
def list_approvals(status: str | None = None, client_id: str | None = None, user: User = Depends(require("clients.view")),
                   db: Session = Depends(get_db)):
    ids = auth_service.accessible_client_ids(db, user)
    q = select(Approval).order_by(Approval.created_at.desc()).limit(300)
    if status:
        q = q.where(Approval.status == status)
    if client_id:
        q = q.where(Approval.client_id == client_id)
    out = []
    for a in db.scalars(q):
        if ids is None or a.client_id in ids:
            d = approvals.serialize(a, db)
            d.pop("payload")
            d.pop("context")
            out.append(d)
    return out


@router.get("/approvals/{approval_id}", tags=["approvals"])
def get_approval(approval_id: str, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    a = approvals.get(db, approval_id)
    auth_service.require_client_access(db, user, a.client_id)
    data = approvals.serialize(a, db, with_events=True)
    ops = list(db.scalars(select(Operation).where(Operation.approval_id == a.id).order_by(Operation.created_at)))
    data["operations"] = [execution.serialize(o) for o in ops]
    data["can_decide"] = auth_service.has_permission(user, "approvals.decide")
    if a.validation_run_id:
        v = db.get(ValidationRun, a.validation_run_id)
        data["validation_run"] = validation.serialize(v)
    return data


class Decision(BaseModel):
    payload_hash: str = ""
    reason: str = ""


@router.post("/approvals/{approval_id}/approve", tags=["approvals"])
def approve(approval_id: str, body: Decision, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = approvals.approve(db, user, approval_id, body.payload_hash, body.reason)
    db.commit()
    return approvals.serialize(a, db)


@router.post("/approvals/{approval_id}/reject", tags=["approvals"])
def reject(approval_id: str, body: Decision, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = approvals.reject(db, user, approval_id, body.reason)
    db.commit()
    return approvals.serialize(a, db)


@router.post("/approvals/{approval_id}/request-changes", tags=["approvals"])
def request_changes(approval_id: str, body: Decision, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = approvals.reject(db, user, approval_id, body.reason, changes_requested=True)
    db.commit()
    return approvals.serialize(a, db)


class PayloadEdit(BaseModel):
    payload: dict


@router.put("/approvals/{approval_id}/payload", tags=["approvals"])
def edit_payload(approval_id: str, body: PayloadEdit, user: User = Depends(current_user), db: Session = Depends(get_db)):
    def revalidate(payload, business: Business):
        return validation.validate(db, payload, business)
    new = approvals.edit_payload(db, user, approval_id, body.payload, revalidate)
    db.commit()
    return approvals.serialize(new, db)


# ------------------------------------------------------------------ execution
@router.post("/approvals/{approval_id}/execute", tags=["execution"])
def execute(approval_id: str, user: User = Depends(require("execution.run")), db: Session = Depends(get_db)):
    a = approvals.get(db, approval_id)
    auth_service.require_client_access(db, user, a.client_id)
    op = execution.execute(db, user, approval_id)
    a = db.get(Approval, approval_id)
    tasks_service.on_approval_executed(db, approval_id, op.state)
    experiments.on_executed(db, a, op)
    if op.state == "SUCCEEDED" and a.payload.get("kind") == "BUILD_CAMPAIGN" and a.context.get("feasibility"):
        from app.models import Campaign
        for rn in op.external_resource_names:
            c = db.scalar(select(Campaign).where(Campaign.external_resource_name == rn))
            if c:
                learning.record_prediction(db, a.client_id, c, a.context["feasibility"].get("summary", {}))
    db.commit()
    return {**execution.serialize(op), "data_source": registry.get("google_ads").source,
            "note": "MOCK operation — nothing was sent to Google Ads." if not registry.get("google_ads").is_real else "LIVE operation"}


@router.get("/operations", tags=["execution"])
def list_operations(client_id: str | None = None, user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    ids = auth_service.accessible_client_ids(db, user)
    q = select(Operation).order_by(Operation.created_at.desc()).limit(200)
    if client_id:
        q = q.where(Operation.client_id == client_id)
    return [execution.serialize(o) for o in db.scalars(q) if ids is None or o.client_id in ids]


@router.get("/operations/{op_id}", tags=["execution"])
def get_operation(op_id: str, user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    o = db.get(Operation, op_id)
    if not o:
        raise NotFound("Operation not found")
    auth_service.require_client_access(db, user, o.client_id)
    return execution.serialize(o)


@router.post("/operations/{op_id}/reconcile", tags=["execution"])
def reconcile(op_id: str, user: User = Depends(require("execution.run")), db: Session = Depends(get_db)):
    o = db.get(Operation, op_id)
    if not o:
        raise NotFound("Operation not found")
    execution.reconcile(db, o)
    db.commit()
    return execution.serialize(o)
