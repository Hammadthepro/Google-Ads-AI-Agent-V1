"""Learning loop (step 14): edit/rejection log, playbook rules written by people, predictions vs actuals,
and saved regression cases re-run before any prompt or rule change. AI never rewrites production rules."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import NotFound, ValidationFailed
from app.core.util import normalize_term
from app.models import (Approval, ApprovalEvent, Campaign, CampaignPlan, PerformanceSnapshot, PlaybookRule, Prediction,
                        RegressionCase, User)
from app.services import activity, ai_service, auth_service
from app.services.validation import claim_violations


def feedback_log(db: Session, limit: int = 100) -> list[dict]:
    rows = db.execute(select(ApprovalEvent, Approval).join(Approval, Approval.id == ApprovalEvent.approval_id)
                      .where(ApprovalEvent.event.in_(["EDITED", "REJECTED", "CHANGES_REQUESTED", "APPROVED"]))
                      .order_by(ApprovalEvent.created_at.desc()).limit(limit)).all()
    out = [{"type": e.event, "approval": a.title, "approval_id": a.id, "actor": e.actor_email, "reason": e.reason,
            "at": e.created_at.isoformat(), "kind": a.payload.get("kind")} for e, a in rows]
    for p in db.scalars(select(CampaignPlan).where(CampaignPlan.human_comparison != None).order_by(CampaignPlan.created_at.desc()).limit(30)):  # noqa: E711
        hc = p.human_comparison or {}
        if hc.get("disagreements") or hc.get("applied_human_decisions"):
            out.append({"type": "HUMAN_PLAN_DIFF", "approval": f"Plan {p.id[:8]}", "approval_id": None, "actor": "team",
                        "reason": "; ".join(hc.get("applied_human_decisions", [])[:3]) or f"{len(hc.get('disagreements', []))} disagreement(s)",
                        "at": p.created_at.isoformat(), "kind": p.scope})
        if p.challenger and p.challenger.get("objections"):
            out.append({"type": "CHALLENGER", "approval": f"Plan {p.id[:8]}", "approval_id": None, "actor": "challenger AI",
                        "reason": f"{len(p.challenger['objections'])} objection(s), verdict {p.challenger.get('verdict')}",
                        "at": p.created_at.isoformat(), "kind": p.scope})
    return sorted(out, key=lambda x: x["at"], reverse=True)[:limit]


def create_rule(db: Session, actor: User, text: str, scope: str = "GLOBAL", scope_value: str = "", note: str = "") -> PlaybookRule:
    auth_service.require(actor, "playbook.manage")
    if len(text.strip()) < 10:
        raise ValidationFailed("Write the rule in plain language (at least 10 characters)")
    if scope not in ("GLOBAL", "BUSINESS_TYPE", "CLIENT"):
        raise ValidationFailed("Invalid scope")
    r = PlaybookRule(text=text.strip(), scope=scope, scope_value=scope_value, author_id=actor.id, source_note=note)
    db.add(r)
    db.flush()
    activity.log(db, "playbook.rule_created", actor=actor, entity="playbook_rule", entity_id=r.id, details={"text": text})
    return r


def update_rule(db: Session, actor: User, rule_id: str, data: dict) -> PlaybookRule:
    auth_service.require(actor, "playbook.manage")
    r = db.get(PlaybookRule, rule_id)
    if not r:
        raise NotFound("Rule not found")
    if "text" in data and data["text"] != r.text:
        r.text, r.version = data["text"], r.version + 1
    if "is_active" in data:
        r.is_active = bool(data["is_active"])
    activity.log(db, "playbook.rule_updated", actor=actor, entity="playbook_rule", entity_id=r.id, details=data)
    return r


def record_prediction(db: Session, client_id: str, campaign: Campaign, feasibility_summary: dict) -> Prediction:
    p = Prediction(client_id=client_id, subject_type="FEASIBILITY", subject_id=campaign.id, campaign_external_id=campaign.external_id,
                   predicted={"weekly_conversions": feasibility_summary.get("expected_weekly_conversions"),
                              "range": feasibility_summary.get("realistic_weekly_range")},
                   evaluate_after=clock.today() + timedelta(days=35))
    db.add(p)
    return p


def evaluate_predictions(db: Session) -> int:
    n = 0
    for p in db.scalars(select(Prediction).where(Prediction.actual.is_(None), Prediction.evaluate_after <= clock.today())):
        c = db.get(Campaign, p.subject_id)
        if not c:
            continue
        end = clock.today() - timedelta(days=1)
        conv = db.scalar(select(func.coalesce(func.sum(PerformanceSnapshot.conversions), 0)).where(
            PerformanceSnapshot.level == "CAMPAIGN", PerformanceSnapshot.entity_external_id == c.external_id,
            PerformanceSnapshot.google_ads_account_id == c.google_ads_account_id, PerformanceSnapshot.date >= end - timedelta(days=27)))
        actual = round(float(conv) / 4, 1)
        pred = p.predicted.get("weekly_conversions") or 0
        p.actual = {"weekly_conversions": actual, "error_pct": round((actual - pred) / pred * 100, 1) if pred else None}
        p.evaluated_at = clock.now()
        n += 1
    return n


def check_expectations(draft: dict, exp: dict) -> list[dict]:
    groups = draft.get("ad_groups") or []
    ads = [a for g in groups for a in g.get("ads", [])]
    kws = [k for g in groups for k in g.get("keywords", [])]
    text = " ".join(h for a in ads for h in a.get("headlines", []) + a.get("descriptions", [])).lower()
    results = []
    if exp.get("no_broad_match"):
        results.append({"expectation": "No broad match", "passed": all(k["match_type"] != "BROAD" for k in kws)})
    if exp.get("max_headline_length"):
        results.append({"expectation": f"Headlines ≤ {exp['max_headline_length']} chars",
                        "passed": all(len(h) <= exp["max_headline_length"] for a in ads for h in a.get("headlines", []))})
    if exp.get("min_ad_groups"):
        results.append({"expectation": f"At least {exp['min_ad_groups']} ad groups", "passed": len(groups) >= exp["min_ad_groups"]})
    for phrase in exp.get("must_not_contain", []):
        results.append({"expectation": f"Ads never mention '{phrase}'", "passed": phrase.lower() not in text
                        and all(phrase.lower() not in normalize_term(k["text"]) for k in kws)})
    for neg in exp.get("must_include_negatives", []):
        results.append({"expectation": f"Negative '{neg}' present", "passed": neg.lower() in [n.lower() for n in draft.get("campaign_negatives", [])]})
    if exp.get("only_verified_claims"):
        bad = [h for a in ads for h in a.get("headlines", []) if claim_violations(h, exp.get("verified_claims", []))]
        results.append({"expectation": "Only verified claims (before validation fixes)", "passed": not bad, "detail": bad[:5],
                        "informational": True})
    return results


def run_regression(db: Session, actor: User | None = None) -> list[dict]:
    """Re-run every saved case through the CURRENT prompts + playbook rules (draft only, nothing executed)."""
    from app.services import planner
    out = []
    for case in db.scalars(select(RegressionCase)):
        inputs = dict(case.inputs)
        rules = [r.text for r in db.scalars(select(PlaybookRule).where(PlaybookRule.is_active.is_(True)))]
        try:
            parsed, run = ai_service.generate(db, "DRAFT_CAMPAIGN", {**inputs, "playbook_rules": rules})
            checks = check_expectations(parsed.model_dump(), case.expectations)
            passed = all(c["passed"] for c in checks if not c.get("informational"))
            res = {"passed": passed, "checks": checks, "prompt_version": f"{run.prompt_template_id}@{run.prompt_version}",
                   "model": run.model, "at": clock.now().isoformat()}
        except ai_service.AIOutputInvalid as exc:
            res = {"passed": False, "checks": [], "error": exc.message, "at": clock.now().isoformat()}
        case.last_result, case.last_run_at = res, clock.now()
        out.append({"case": case.name, **res})
    activity.log(db, "learning.regression_run", actor=actor, details={"cases": len(out), "failed": sum(1 for o in out if not o["passed"])})
    planner  # noqa: B018
    return out
