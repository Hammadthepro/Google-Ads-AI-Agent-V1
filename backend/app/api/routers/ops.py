"""Dashboard, monitoring, emergency brake, recommendations, weekly reports, experiments, learning, integrations,
settings, activity logs, alerts and TEST_MODE mock controls."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters import registry
from app.adapters.base import AdapterError
from app.api.deps import current_user, require
from app.core import clock
from app.core.config import get_settings
from app.core.errors import Forbidden, NotFound, ValidationFailed
from app.db.session import get_db
from app.models import (ActivityLog, Alert, Approval, Campaign, Client, EmergencyEvent, Experiment, FixTrackerItem, GoogleAdsAccount,
                        IntegrationConnection, KillSwitch, MockFailureInjection, PlaybookRule, Prediction, Recommendation, RegressionCase,
                        Task, User, WeeklyReport)
from app.services import (activity, auth_service, emergency, experiments, learning, monitoring, optimization, reporting,
                          settings_service, tracking_gate)

router = APIRouter()


def _client_ok(db, user, client_id):
    auth_service.require_client_access(db, user, client_id)


# ------------------------------------------------------------------ dashboard
@router.get("/dashboard", tags=["dashboard"])
def dashboard(user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    ids = auth_service.accessible_client_ids(db, user)
    clients = [c for c in db.scalars(select(Client).order_by(Client.name)) if ids is None or c.id in ids]
    cards = []
    for c in clients:
        camps = monitoring.campaign_summary(db, c.id, 7)
        cost = sum(x["cost"] for x in camps)
        conv = sum(x["conversions"] for x in camps)
        target = sum(x["weekly_target"] or 0 for x in camps if x["type"] == "SEARCH")
        states = {b.name: tracking_gate.effective_state(db, b.id) for b in c.businesses}
        cards.append({"client_id": c.id, "name": c.name, "cost_7d": round(cost, 2), "conversions_7d": round(conv, 1),
                      "weekly_target": target, "gap": round(conv - target, 1), "tracking": states,
                      "automation_frozen": c.automation_frozen, "flagged": c.flagged,
                      "open_fixes": db.scalar(select(func.count(FixTrackerItem.id)).where(FixTrackerItem.client_id == c.id, FixTrackerItem.status != "RESOLVED")),
                      "pending_approvals": db.scalar(select(func.count(Approval.id)).where(Approval.client_id == c.id, Approval.status == "PENDING_APPROVAL")),
                      "source_type": camps[0]["source_type"] if camps else "MOCK"})
    cid_set = [c.id for c in clients]
    return {"clients": cards, "now": clock.now().isoformat(), "simulated_clock": get_settings().TEST_MODE,
            "integration_mode": get_settings().INTEGRATION_MODE, "google_ads_writes_enabled": get_settings().GOOGLE_ADS_WRITES_ENABLED,
            "data_source": registry.get("google_ads").source,
            "recent_emergency": [emergency.serialize(e) for e in db.scalars(select(EmergencyEvent).where(EmergencyEvent.client_id.in_(cid_set or ["-"]))
                                                                            .order_by(EmergencyEvent.created_at.desc()).limit(6))],
            "recent_tasks": [{"id": t.id, "task_type": t.task_type, "status": t.status, "created_at": t.created_at.isoformat(), "client_id": t.client_id}
                             for t in db.scalars(select(Task).where(Task.client_id.in_(cid_set or ["-"])).order_by(Task.created_at.desc()).limit(8))]}


# ------------------------------------------------------------------ monitoring
@router.get("/monitor/{client_id}", tags=["monitoring"])
def monitor(client_id: str, days: int = 30, user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    _client_ok(db, user, client_id)
    return {"campaigns": monitoring.campaign_summary(db, client_id, days), "wasted_spend": monitoring.wasted_spend(db, client_id),
            "calls": monitoring.calls_summary(db, client_id), "change_events": monitoring.change_events(db, client_id),
            "data_source": registry.get("google_ads").source, "as_of": clock.today().isoformat()}


@router.get("/monitor/{client_id}/search-terms", tags=["monitoring"])
def terms(client_id: str, user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    _client_ok(db, user, client_id)
    return monitoring.search_terms(db, client_id)


@router.get("/monitor/campaign/{campaign_id}/series", tags=["monitoring"])
def series(campaign_id: str, days: int = 30, user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    c = db.get(Campaign, campaign_id)
    if not c:
        raise NotFound("Campaign not found")
    _client_ok(db, user, c.client_id)
    return monitoring.daily_series(db, c, days)


@router.post("/monitor/{client_id}/sync", tags=["monitoring"])
def sync_now(client_id: str, user: User = Depends(require("monitor.run")), db: Session = Depends(get_db)):
    _client_ok(db, user, client_id)
    out = {}
    for a in db.scalars(select(GoogleAdsAccount).where(GoogleAdsAccount.client_id == client_id, GoogleAdsAccount.is_manager.is_(False))):
        out[a.customer_id] = monitoring.daily_sync(db, a)
    db.commit()
    return out


# ------------------------------------------------------------------ emergency brake
@router.get("/emergency/events", tags=["emergency"])
def emergency_events(user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    ids = auth_service.accessible_client_ids(db, user)
    return [emergency.serialize(e) for e in db.scalars(select(EmergencyEvent).order_by(EmergencyEvent.created_at.desc()).limit(200))
            if ids is None or e.client_id in ids]


@router.post("/emergency/run", tags=["emergency"])
def emergency_run(user: User = Depends(require("monitor.run")), db: Session = Depends(get_db)):
    out = emergency.hourly_check(db)
    db.commit()
    return out


@router.get("/kill-switches", tags=["emergency"])
def kill_switches(user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    return [{"scope": k.scope, "scope_id": k.scope_id, "is_on": k.is_on, "reason": k.reason, "updated_at": k.updated_at.isoformat()}
            for k in db.scalars(select(KillSwitch))]


class KillIn(BaseModel):
    scope: str
    scope_id: str = ""
    is_on: bool
    reason: str = ""


@router.post("/kill-switches", tags=["emergency"])
def set_kill(body: KillIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.scope == "GLOBAL":
        auth_service.require(user, "killswitch.global")
    elif body.scope in ("CLIENT", "ACCOUNT"):
        auth_service.require(user, "killswitch.client")
    else:
        raise ValidationFailed("Invalid scope")
    k = settings_service.set_kill_switch(db, body.scope, body.scope_id, body.is_on, body.reason, user.id)
    activity.log(db, "killswitch.set", actor=user, entity="kill_switch", entity_id=k.id, details=body.model_dump())
    db.commit()
    return {"scope": k.scope, "scope_id": k.scope_id, "is_on": k.is_on}


@router.post("/clients/{client_id}/unfreeze", tags=["emergency"])
def unfreeze(client_id: str, user: User = Depends(require("killswitch.client")), db: Session = Depends(get_db)):
    c = db.get(Client, client_id)
    if not c:
        raise NotFound("Client not found")
    c.automation_frozen, c.flagged, c.flag_reason = False, False, ""
    activity.log(db, "client.unfrozen", actor=user, entity="client", entity_id=c.id, client_id=c.id)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ recommendations / optimization
@router.get("/recommendations", tags=["optimization"])
def recs(client_id: str | None = None, user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    ids = auth_service.accessible_client_ids(db, user)
    q = select(Recommendation).order_by(Recommendation.created_at.desc()).limit(300)
    if client_id:
        q = q.where(Recommendation.client_id == client_id)
    return [optimization.serialize(r) for r in db.scalars(q) if ids is None or r.client_id in ids]


@router.post("/recommendations/run/{client_id}", tags=["optimization"])
def run_opt(client_id: str, user: User = Depends(require("monitor.run")), db: Session = Depends(get_db)):
    _client_ok(db, user, client_id)
    out = optimization.run_for_client(db, db.get(Client, client_id))
    db.commit()
    return out


@router.post("/recommendations/{rec_id}/approval", tags=["optimization"])
def rec_to_approval(rec_id: str, user: User = Depends(require("approvals.submit")), db: Session = Depends(get_db)):
    a = optimization.create_approval_for(db, user, rec_id)
    db.commit()
    return {"approval_id": a.id, "status": a.status}


@router.get("/campaigns/{campaign_id}/enough-data", tags=["optimization"])
def enough(campaign_id: str, user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    c = db.get(Campaign, campaign_id)
    if not c:
        raise NotFound("Campaign not found")
    return optimization.enough_data(db, c)


# ------------------------------------------------------------------ weekly reports
@router.get("/reports", tags=["reports"])
def reports(client_id: str | None = None, user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    ids = auth_service.accessible_client_ids(db, user)
    q = select(WeeklyReport).order_by(WeeklyReport.created_at.desc()).limit(100)
    if client_id:
        q = q.where(WeeklyReport.client_id == client_id)
    return [{"id": r.id, "client_id": r.client_id, "week_start": r.week_start.isoformat(), "week_end": r.week_end.isoformat(),
             "totals": r.metrics.get("totals"), "source_type": r.source_type, "created_at": r.created_at.isoformat()}
            for r in db.scalars(q) if ids is None or r.client_id in ids]


@router.post("/reports/generate/{client_id}", tags=["reports"])
def gen_report(client_id: str, user: User = Depends(require("monitor.run")), db: Session = Depends(get_db)):
    _client_ok(db, user, client_id)
    r = reporting.generate(db, db.get(Client, client_id))
    db.commit()
    return reporting.serialize(r)


@router.get("/reports/{report_id}", tags=["reports"])
def get_report(report_id: str, user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    r = db.get(WeeklyReport, report_id)
    if not r:
        raise NotFound("Report not found")
    _client_ok(db, user, r.client_id)
    return reporting.serialize(r)


# ------------------------------------------------------------------ experiments
class ExpIn(BaseModel):
    campaign_id: str
    variable: str
    hypothesis: str
    treatment: dict = {}


@router.get("/experiments", tags=["experiments"])
def list_exps(user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    ids = auth_service.accessible_client_ids(db, user)
    return [experiments.serialize(e) for e in db.scalars(select(Experiment).order_by(Experiment.created_at.desc())) if ids is None or e.client_id in ids]


@router.post("/experiments", tags=["experiments"])
def create_exp(body: ExpIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = experiments.propose(db, user, body.campaign_id, body.variable, body.hypothesis, body.treatment)
    db.commit()
    return experiments.serialize(e)


@router.get("/experiments/{exp_id}/results", tags=["experiments"])
def exp_results(exp_id: str, user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    e = db.get(Experiment, exp_id)
    if not e:
        raise NotFound("Experiment not found")
    out = experiments.results(db, e)
    db.commit()
    return {"experiment": experiments.serialize(e), "results": out}


class WinnerIn(BaseModel):
    arm: str
    reason: str = ""


@router.post("/experiments/{exp_id}/select-winner", tags=["experiments"])
def winner(exp_id: str, body: WinnerIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = experiments.select_winner(db, user, exp_id, body.arm, body.reason)
    db.commit()
    return {"approval_id": a.id, "note": "Winner is applied only after this approval is approved and executed."}


# ------------------------------------------------------------------ learning loop
@router.get("/learning/feedback", tags=["learning"])
def feedback(user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    return learning.feedback_log(db)


@router.get("/playbook", tags=["learning"])
def playbook(user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    return [{"id": r.id, "text": r.text, "scope": r.scope, "scope_value": r.scope_value, "is_active": r.is_active, "version": r.version,
             "source_note": r.source_note, "created_at": r.created_at.isoformat()} for r in db.scalars(select(PlaybookRule).order_by(PlaybookRule.created_at))]


class RuleIn(BaseModel):
    text: str
    scope: str = "GLOBAL"
    scope_value: str = ""
    note: str = ""


@router.post("/playbook", tags=["learning"])
def add_rule(body: RuleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = learning.create_rule(db, user, body.text, body.scope, body.scope_value, body.note)
    db.commit()
    return {"id": r.id}


@router.patch("/playbook/{rule_id}", tags=["learning"])
def patch_rule(rule_id: str, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = learning.update_rule(db, user, rule_id, body)
    db.commit()
    return {"id": r.id, "version": r.version, "is_active": r.is_active}


@router.get("/learning/predictions", tags=["learning"])
def predictions(user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    return [{"id": p.id, "client_id": p.client_id, "subject": p.subject_type, "predicted": p.predicted, "actual": p.actual,
             "evaluate_after": p.evaluate_after.isoformat() if p.evaluate_after else None} for p in db.scalars(select(Prediction))]


@router.get("/learning/regression-cases", tags=["learning"])
def cases(user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    return [{"id": c.id, "name": c.name, "inputs": c.inputs, "expectations": c.expectations, "last_result": c.last_result,
             "last_run_at": c.last_run_at.isoformat() if c.last_run_at else None} for c in db.scalars(select(RegressionCase))]


@router.post("/learning/regression-cases", tags=["learning"])
def add_case(body: dict, user: User = Depends(require("playbook.manage")), db: Session = Depends(get_db)):
    c = RegressionCase(name=body["name"], inputs=body.get("inputs", {}), expectations=body.get("expectations", {}), created_by_id=user.id)
    db.add(c)
    db.commit()
    return {"id": c.id}


@router.post("/learning/regression-cases/run", tags=["learning"])
def run_cases(user: User = Depends(require("playbook.manage")), db: Session = Depends(get_db)):
    out = learning.run_regression(db, user)
    db.commit()
    return out


# ------------------------------------------------------------------ integrations
STATUS_KEY = "integration_status"


@router.get("/integrations", tags=["integrations"])
def integrations(user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    s = get_settings()
    rows = {r.provider: r for r in db.scalars(select(IntegrationConnection))}
    out = []
    for name in ("google_ads", "keyword_research", "places", "pagespeed", "crawler", "ai", "auth", "ads_oauth", "notifier"):
        a = registry.get(name)
        live = a.source == "LIVE"
        r = rows.get(name)
        status = (r.status if r else "NOT_TESTED") if live else "MOCK CONNECTED"
        out.append({"name": name, "adapter": a.name, "mode": "REAL" if live else "MOCK", "status": status,
                    "last_success_at": r.last_success_at.isoformat() if r and r.last_success_at else None,
                    "last_test_at": r.last_test_at.isoformat() if r and r.last_test_at else None,
                    "last_error": r.last_error if r else "", "is_mock": not live})
    ads_conn = [{"id": r.id, "status": r.status, "fingerprint": r.token_fingerprint, "mode": r.integration_mode, "scopes": r.scopes}
                for r in rows.values() if r.provider == "google_ads_oauth"]
    return {"integrations": out, "config": s.public_summary(), "ads_connections": ads_conn,
            "real_writes": {"env_enabled": s.GOOGLE_ADS_WRITES_ENABLED, "authorized": settings_service.get_value("real_writes_authorized", False, db)}}


@router.post("/integrations/{name}/test", tags=["integrations"])
def test_integration(name: str, user: User = Depends(require("integrations.manage")), db: Session = Depends(get_db)):
    if name not in registry.ALL:
        raise NotFound("Unknown integration")
    a = registry.get(name)
    r = db.scalar(select(IntegrationConnection).where(IntegrationConnection.provider == name))
    if not r:
        r = IntegrationConnection(provider=name)
        db.add(r)
    r.integration_mode = "REAL" if a.source == "LIVE" else "MOCK"
    r.last_test_at = datetime.now(timezone.utc)
    try:
        if name == "google_ads":
            detail = f"{len(a.list_accessible_customers())} accessible customers"
        elif name == "places":
            detail = f"{len(a.text_search('plumber'))} results"
        elif name == "pagespeed":
            detail = f"score {a.run('https://www.northsideplumbing.example/', 'mobile')['performance_score']}"
        elif name == "crawler":
            detail = f"status {a.fetch('https://www.northsideplumbing.example/').status}"
        elif name == "ai":
            from app.services import ai_service
            parsed, run = ai_service.generate(db, "EXPLAIN", {"headline": "Connection test", "facts": {"ok": True}})
            detail = f"model {run.model}"
        elif name == "keyword_research":
            detail = f"{len(a.generate_ideas('', ['plumber']))} ideas"
        else:
            detail = "configured"
        r.status, r.last_success_at, r.last_error = ("MOCK CONNECTED" if a.source == "MOCK" else "CONNECTED"), r.last_test_at, ""
    except (AdapterError, Exception) as exc:  # noqa: BLE001 — report failures, never fake success
        r.status, r.last_error, detail = "ERROR", f"{type(exc).__name__}: {getattr(exc, 'message', str(exc))[:300]}", ""
    activity.log(db, "integration.tested", actor=user, entity="integration", entity_id=name, details={"status": r.status})
    db.commit()
    return {"name": name, "status": r.status, "detail": detail, "error": r.last_error, "mode": r.integration_mode}


# ------------------------------------------------------------------ settings
@router.get("/settings", tags=["settings"])
def get_settings_(user: User = Depends(require("settings.view")), db: Session = Depends(get_db)):
    return {"settings": settings_service.all_settings(db), "config": get_settings().public_summary()}


class SettingIn(BaseModel):
    key: str
    value: object
    reason: str = ""


@router.patch("/settings", tags=["settings"])
def set_setting(body: SettingIn, user: User = Depends(require("settings.manage")), db: Session = Depends(get_db)):
    if body.key in ("real_writes_authorized", "real_emergency_writes_authorized"):
        auth_service.require(user, "real_writes.authorize")
        if body.value and len(body.reason.strip()) < 20:
            raise ValidationFailed("Authorizing real writes requires a written reason (20+ characters)")
    try:
        settings_service.set_value(db, body.key, body.value, user.id)
    except KeyError as exc:
        raise NotFound("Unknown setting") from exc
    activity.log(db, "settings.changed", actor=user, entity="setting", entity_id=body.key, details={"value": body.value, "reason": body.reason})
    db.commit()
    return {"key": body.key, "value": body.value}


# ------------------------------------------------------------------ activity & alerts
@router.get("/activity", tags=["activity"])
def activity_logs(client_id: str | None = None, action: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(500)
    if client_id:
        q = q.where(ActivityLog.client_id == client_id)
    if action:
        q = q.where(ActivityLog.action.like(f"{action}%"))
    if auth_service.has_permission(user, "activity.view_all"):
        pass
    elif auth_service.has_permission(user, "activity.view_clients"):
        ids = auth_service.accessible_client_ids(db, user)
        if ids is not None:
            q = q.where(ActivityLog.client_id.in_(ids or ["-"]))
    elif auth_service.has_permission(user, "activity.view_own"):
        q = q.where(ActivityLog.actor_id == user.id)
    else:
        raise Forbidden("Your role cannot view activity logs")
    return [{"id": r.id, "at": r.created_at.isoformat(), "actor": r.actor_email, "action": r.action, "entity": r.entity,
             "entity_id": r.entity_id, "client_id": r.client_id, "details": r.details, "integration_mode": r.integration_mode,
             "request_id": r.request_id} for r in db.scalars(q)]


@router.get("/alerts", tags=["activity"])
def alerts(user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    ids = auth_service.accessible_client_ids(db, user)
    return [{"id": a.id, "at": a.created_at.isoformat(), "subject": a.subject, "body": a.body, "status": a.delivery_status,
             "category": a.category, "client_id": a.client_id, "recipients": a.recipients}
            for a in db.scalars(select(Alert).order_by(Alert.created_at.desc()).limit(200)) if ids is None or a.client_id in ids]


# ------------------------------------------------------------------ TEST_MODE mock controls
def _test_mode():
    if not get_settings().TEST_MODE or get_settings().APP_ENV == "production":
        raise Forbidden("Mock controls are only available in TEST_MODE")


@router.post("/dev/mock/reset", tags=["mock controls"])
def mock_reset(user: User = Depends(require("mock.control"))):
    _test_mode()
    from app.mock_world import seed
    seed.reset()
    return seed.seed()


class AdvanceIn(BaseModel):
    days: int = 1


@router.post("/dev/mock/advance-time", tags=["mock controls"])
def advance(body: AdvanceIn, user: User = Depends(require("mock.control"))):
    _test_mode()
    if not 1 <= body.days <= 30:
        raise ValidationFailed("Advance 1-30 days at a time")
    from app.workers import jobs
    log = []
    for _ in range(body.days):
        clock.advance(24)
        log.append(jobs.run_daily_cycle())
    return {"now": clock.now().isoformat(), "days": body.days, "cycles": log}


@router.get("/dev/mock/failures", tags=["mock controls"])
def failures(user: User = Depends(require("mock.control")), db: Session = Depends(get_db)):
    return [{"id": f.id, "adapter": f.adapter, "method": f.method, "failure": f.failure, "entity": f.entity, "times": f.times,
             "used": f.used, "is_active": f.is_active} for f in db.scalars(select(MockFailureInjection).order_by(MockFailureInjection.created_at.desc()))]


class FailureIn(BaseModel):
    adapter: str
    method: str = "*"
    failure: str
    entity: str = ""
    times: int = 1


@router.post("/dev/mock/failures", tags=["mock controls"])
def add_failure(body: FailureIn, user: User = Depends(require("mock.control")), db: Session = Depends(get_db)):
    _test_mode()
    allowed = {"auth_failure", "permission_denied", "rate_limited", "timeout", "timeout_after_commit", "not_found", "missing_data",
               "invalid_input", "outage", "empty", "partial_data", "invalid_json", "schema_mismatch"}
    if body.failure not in allowed:
        raise ValidationFailed(f"failure must be one of {sorted(allowed)}")
    f = MockFailureInjection(**body.model_dump())
    db.add(f)
    db.commit()
    return {"id": f.id}


@router.delete("/dev/mock/failures/{failure_id}", tags=["mock controls"])
def del_failure(failure_id: str, user: User = Depends(require("mock.control")), db: Session = Depends(get_db)):
    f = db.get(MockFailureInjection, failure_id)
    if f:
        f.is_active = False
        db.commit()
    return {"ok": True}
