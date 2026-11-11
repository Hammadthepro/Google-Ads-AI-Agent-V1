"""A/B experiments (13b): one variable, approval to start, 50/50 split, results per arm,
enough-data check, and a HUMAN picks the winner (never automatic in V1)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.adapters import registry
from app.adapters.base import AdapterError
from app.core import clock
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.models import Campaign, Experiment, GoogleAdsAccount, User
from app.services import activity, approvals, auth_service, settings_service

VARIABLES = ("BIDDING", "AD_ANGLE", "MATCH_TYPE", "LANDING_PAGE")


def propose(db: Session, actor: User, campaign_id: str, variable: str, hypothesis: str, treatment: dict) -> Experiment:
    auth_service.require(actor, "plans.create")
    if variable not in VARIABLES:
        raise ValidationFailed(f"Variable must be one of {VARIABLES}")
    c = db.get(Campaign, campaign_id)
    if not c or c.campaign_type != "SEARCH":
        raise ValidationFailed("Select a Search campaign")
    if variable == "MATCH_TYPE" and treatment.get("match_type") == "BROAD":
        from app.models import Business
        from app.services import measurement
        ok, why = measurement.conversion_data_reliable(db, db.get(Business, c.business_id) if c.business_id else None)
        if not ok:  # broad match is only safe to judge with conversion data: say exactly what is missing
            raise ValidationFailed(f"A broad-match test is judged by conversions, and {why}")
    account = db.get(GoogleAdsAccount, c.google_ads_account_id)
    exp = Experiment(client_id=c.client_id, campaign_id=c.id, name=f"{c.name} — {variable.replace('_', ' ').lower()} test",
                     variable=variable, hypothesis=hypothesis, treatment=treatment, split_percent=50, status="PROPOSED",
                     created_by_id=actor.id, source_type=registry.get("google_ads").source)
    db.add(exp)
    db.flush()
    payload = {"kind": "EXPERIMENT_START", "customer_id": account.customer_id,
               "operations": [{"resource": "experiment", "op": "create", "temp_id": "-1",
                               "fields": {"base_campaign": c.external_resource_name, "name": exp.name, "type": "SEARCH_CUSTOM",
                                          "traffic_split": 50, "variable": variable, "treatment": treatment,
                                          "start_date": clock.today().isoformat(), "experiment_status": "RUNNING", "status": "ENABLED"}}],
               "summary": {"campaign": c.name, "variable": variable, "treatment": treatment, "split": "50/50"},
               "internal": {"experiment_id": exp.id}}
    a = approvals.create(db, client_id=c.client_id, approval_type="EXPERIMENT_START", title=f"Start experiment: {exp.name}",
                         payload=payload, requested_by=actor, business_id=c.business_id, account_id=account.id,
                         current_state={"campaign": c.name}, proposed_state=payload["summary"], reason=hypothesis,
                         expected_effect="Half of eligible traffic sees the treatment; results compared per arm.",
                         risks=["Learning period for the treatment arm", "Split traffic reduces per-arm volume"],
                         context={"experiment_id": exp.id})
    exp.start_approval_id = a.id
    activity.log(db, "experiment.proposed", actor=actor, entity="experiment", entity_id=exp.id, client_id=c.client_id)
    return exp


def on_executed(db: Session, approval, op_row) -> None:
    exp_id = (approval.payload.get("internal") or {}).get("experiment_id")
    exp = db.get(Experiment, exp_id) if exp_id else None
    if not exp or op_row.state != "SUCCEEDED":
        return
    if approval.approval_type == "EXPERIMENT_START":
        exp.status, exp.started_at = "RUNNING", clock.now()
        exp.external_id = op_row.external_resource_names[0] if op_row.external_resource_names else ""
    elif approval.approval_type == "EXPERIMENT_WINNER":
        exp.status, exp.ended_at = "COMPLETED", clock.now()
        exp.winner_arm = approval.payload["internal"]["winner_arm"]
        exp.winner_selected_by_id = approval.decided_by_id


def results(db: Session, exp: Experiment) -> dict:
    if not exp.external_id:
        return {"status": exp.status, "arms": {}, "enough_data": False, "reason": "Experiment not started"}
    account = db.get(GoogleAdsAccount, db.get(Campaign, exp.campaign_id).google_ads_account_id)
    try:
        r = registry.get("google_ads").get_experiment_results(account.customer_id, exp.external_id)
    except AdapterError as exc:
        return {"status": exp.status, "arms": {}, "enough_data": False, "reason": exc.message}
    min_clicks = int(settings_service.get_value("experiments.min_clicks_per_arm", 100, db))
    min_conv = int(settings_service.get_value("experiments.min_conversions_per_arm", 10, db))
    arms = r["arms"]
    enough = all(a["clicks"] >= min_clicks and a["conversions"] >= min_conv for a in arms.values())
    for a in arms.values():
        a["cost"] = round(a["cost_micros"] / 1e6, 2)
        a["cost_per_lead"] = round(a["cost_per_conversion_micros"] / 1e6, 2) if a.get("cost_per_conversion_micros") else None
    exp.results = {**r, "enough_data": enough, "thresholds": {"min_clicks_per_arm": min_clicks, "min_conversions_per_arm": min_conv}}
    if enough and exp.status == "RUNNING":
        exp.status = "AWAITING_WINNER"
    return {**exp.results, "note": "A person must choose the winner. The system never picks automatically."}


def select_winner(db: Session, actor: User, exp_id: str, arm: str, reason: str):
    auth_service.require(actor, "experiments.pick_winner")
    exp = db.get(Experiment, exp_id)
    if not exp:
        raise NotFound("Experiment not found")
    if arm not in ("control", "treatment"):
        raise ValidationFailed("Arm must be control or treatment")
    if exp.status not in ("RUNNING", "AWAITING_WINNER"):
        raise Conflict(f"Experiment is {exp.status}")
    res = results(db, exp)
    if not res.get("enough_data"):
        raise ValidationFailed("Not enough data in both arms yet to choose a winner")
    c = db.get(Campaign, exp.campaign_id)
    account = db.get(GoogleAdsAccount, c.google_ads_account_id)
    payload = {"kind": "EXPERIMENT_WINNER", "customer_id": account.customer_id,
               "operations": [{"resource": "experiment", "op": "update", "resource_name": exp.external_id,
                               "fields": {"experiment_status": "PROMOTED" if arm == "treatment" else "ENDED"}}],
               "summary": {"experiment": exp.name, "winner": arm, "results": res.get("arms")},
               "internal": {"experiment_id": exp.id, "winner_arm": arm}}
    a = approvals.create(db, client_id=exp.client_id, approval_type="EXPERIMENT_WINNER", title=f"Apply winner '{arm}': {exp.name}",
                         payload=payload, requested_by=actor, business_id=c.business_id, account_id=account.id,
                         current_state={"status": exp.status}, proposed_state={"winner": arm}, reason=reason,
                         expected_effect="Winning arm becomes the campaign configuration" if arm == "treatment" else "Experiment ends; control kept",
                         context={"experiment_id": exp.id, "results": res})
    exp.winner_approval_id = a.id
    return a


def serialize(e: Experiment) -> dict:
    return {"id": e.id, "client_id": e.client_id, "campaign_id": e.campaign_id, "name": e.name, "variable": e.variable,
            "hypothesis": e.hypothesis, "treatment": e.treatment, "split_percent": e.split_percent, "status": e.status,
            "external_id": e.external_id, "results": e.results, "start_approval_id": e.start_approval_id,
            "winner_approval_id": e.winner_approval_id, "winner_arm": e.winner_arm, "source_type": e.source_type,
            "started_at": e.started_at.isoformat() if e.started_at else None, "created_at": e.created_at.isoformat()}
