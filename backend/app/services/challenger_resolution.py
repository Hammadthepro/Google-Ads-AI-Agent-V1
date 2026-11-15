"""Acting on Challenger findings.

Each objection of the current plan version is shown with its severity, evidence, suggestion, the component it is about, the current
value and the proposed replacement. The team can apply the fix (a NEW plan version through the plan editor: re-built payload,
re-validated, fresh approval), edit manually, ask the Main AI to revise with the objection as the instruction, dismiss it with a reason,
or re-run the review. Every action is recorded (ChallengerResolution + activity log) and never deleted.

The Challenger never changes anything itself: a fix is applied only when a person chooses it, through the same deterministic editor and
validation a manual edit uses. An approved version is never modified; replacing it needs explicit confirmation and a new approval.
"""
from __future__ import annotations

import hashlib
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.util import normalize_term
from app.models import Approval, Business, CampaignPlan, ChallengerResolution, Task, User
from app.services import activity, auth_service

ACTIONS = ("apply", "dismiss", "revise", "edited", "reopen")
_REMOVE = re.compile(r"\b(remove|drop|delete|exclude|eliminate|get rid of)\b", re.I)


def objection_key(o: dict) -> str:
    raw = "|".join(normalize_term(str(o.get(k) or "")) for k in ("category", "target", "message"))
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def _groups(draft: dict, scope: str) -> list[dict]:
    return [draft] if scope == "AD_GROUP" else list(draft.get("ad_groups") or [])


def _quoted(text: str) -> list[str]:
    return [m.strip() for m in re.findall(r"[\"'“‘]([^\"'”’]{2,80})[\"'”’]", text or "")]


def derive_fix(o: dict, draft: dict, scope: str) -> tuple[list[dict], str]:
    """The edits that implement an objection, and where they came from: the Challenger's own `fix` (checked against the draft), or a
    narrow deterministic reading of a "remove keyword 'x'" suggestion when the keyword exists in the draft. Otherwise none: the objection
    then needs a manual edit or a Main AI revision; nothing is guessed."""
    groups = _groups(draft, scope)
    edits: list[dict] = []
    for f in o.get("fix") or []:
        op = f.get("op")
        if op in ("remove_keyword", "add_keyword", "set_landing_page", "remove_ad_group"):
            if scope != "AD_GROUP" and not any(normalize_term(g["name"]) == normalize_term(f.get("ad_group", "")) for g in groups):
                continue
        if op == "remove_keyword":
            g = next((g for g in groups if scope == "AD_GROUP" or normalize_term(g["name"]) == normalize_term(f.get("ad_group", ""))), None)
            have = [k for k in (g or {}).get("keywords", []) if normalize_term(k["text"]) == normalize_term(f.get("text", ""))
                    and (not f.get("match_type") or k["match_type"] == f["match_type"].upper())]
            edits += [{"op": "remove_keyword", "ad_group": g["name"], "text": k["text"], "match_type": k["match_type"]} for k in have]
            continue
        edits.append({k: v for k, v in f.items() if v})
    if edits:
        return edits, "challenger"
    if _REMOVE.search(o.get("suggestion") or ""):
        wanted = {normalize_term(t) for t in _quoted(o.get("suggestion", "")) + _quoted(o.get("message", "")) + [o.get("target") or ""] if t}
        for g in groups:
            for k in g.get("keywords", []):
                if normalize_term(k["text"]) in wanted:
                    edits.append({"op": "remove_keyword", "ad_group": g["name"], "text": k["text"], "match_type": k["match_type"]})
        if edits:
            return edits, "rules"
    return [], ""


def _describe_edit(e: dict, draft: dict | None = None, scope: str = "CAMPAIGN") -> tuple[str, str, str]:
    """(component, original value, proposed replacement): the original is read from the draft, never a placeholder."""
    op = e["op"]
    draft = draft or {}
    grp = next((g for g in _groups(draft, scope) if normalize_term(g.get("name", "")) == normalize_term(e.get("ad_group", ""))), {})
    if op == "remove_keyword":
        return f"Ad group “{e['ad_group']}” · keyword", f"[{e['match_type']}] {e['text']}", "removed"
    if op == "add_keyword":
        return f"Ad group “{e.get('ad_group', '')}” · keyword", "—", f"[{e.get('match_type', '').upper()}] {e.get('text', '')}"
    if op == "add_negative":
        return "Campaign negatives", "—", e.get("text", "")
    if op == "remove_negative":
        return "Campaign negatives", e.get("text", ""), "removed"
    if op == "set_landing_page":
        return f"Ad group “{e.get('ad_group', '')}” · landing page", grp.get("landing_page") or "—", e.get("url", "")
    if op == "remove_ad_group":
        return f"Ad group “{e.get('ad_group', '')}”", f"{len(grp.get('keywords', []))} keywords, {len(grp.get('ads', []))} ad(s)", "removed (budget split recalculated)"
    if op == "rename_campaign":
        return "Campaign name", draft.get("campaign_name") or "—", e.get("value", "")
    if op == "set_bidding":
        return "Bidding strategy", draft.get("bidding_strategy") or "—", e.get("value", "")
    return op, "", ""


def _current(db: Session, task: Task) -> tuple[CampaignPlan, Approval | None]:
    plan = db.get(CampaignPlan, task.plan_id) if task.plan_id else None
    if plan is None:
        raise NotFound("This task has no campaign plan")
    return plan, (db.get(Approval, task.approval_id) if task.approval_id else None)


def findings(db: Session, task: Task) -> dict:
    plan, appr = _current(db, task)
    rep = plan.challenger or {}
    recs = list(db.scalars(select(ChallengerResolution).where(ChallengerResolution.task_id == task.id).order_by(ChallengerResolution.created_at)))
    latest: dict[str, ChallengerResolution] = {}
    for r in recs:
        latest[r.objection_key] = r
    out = []
    for i, o in enumerate(rep.get("objections") or []):
        key = objection_key(o)
        edits, source = derive_fix(o, plan.draft, plan.scope)
        desc = [dict(zip(("component", "original", "replacement"), _describe_edit(e, plan.draft, plan.scope))) for e in edits]
        r = latest.get(key)
        out.append({"index": i, "key": key, "category": o.get("category"), "severity": o.get("severity"), "target": o.get("target", ""),
                    "message": o.get("message", ""), "evidence": o.get("evidence", ""), "suggestion": o.get("suggestion", ""),
                    "edits": edits, "fix_source": source, "changes": desc,
                    "component": desc[0]["component"] if desc else (o.get("target") or o.get("category", "").replace("_", " ").lower()),
                    "status": ("open" if r is None or r.action == "reopen" else r.action),
                    "resolution": None if r is None else {"action": r.action, "reason": r.reason, "at": r.created_at.isoformat(), "actor_id": r.actor_id,
                                                          "result_plan_id": r.result_plan_id, "plan_id": r.plan_id}})
    return {"performed": bool(rep.get("performed", bool(rep.get("verdict")))), "reason": rep.get("reason", ""), "verdict": rep.get("verdict"),
            "reviewed_plan_version": rep.get("reviewed_version", plan.version if rep.get("verdict") else None), "plan_id": plan.id,
            "plan_version": plan.version, "approval_id": appr.id if appr else None, "approval_status": appr.status if appr else None,
            "reviewer": rep.get("reviewer"), "agreements": rep.get("agreements") or [], "limitations": rep.get("limitations") or [],
            "findings": out, "history": [{"action": r.action, "reason": r.reason, "objection": r.objection.get("message", ""), "at": r.created_at.isoformat(),
                                          "plan_id": r.plan_id, "result_plan_id": r.result_plan_id} for r in recs],
            "review_history": rep.get("history") or []}


def resolve(db: Session, actor: User, task_id: str, key: str, action: str, *, reason: str = "", confirm_supersede: bool = False) -> dict:
    task = db.get(Task, task_id)
    if not task:
        raise NotFound("Task not found")
    auth_service.require_client_access(db, actor, task.client_id)
    if action not in ACTIONS:
        raise ValidationFailed(f"Unknown action “{action}”")
    plan, appr = _current(db, task)
    f = next((x for x in findings(db, task)["findings"] if x["key"] == key), None)
    if f is None:
        raise NotFound("That concern is not part of the current version's review")
    o = next(o for o in plan.challenger.get("objections", []) if objection_key(o) == key)
    rec = ChallengerResolution(task_id=task.id, plan_id=plan.id, approval_id=appr.id if appr else None, objection_key=key, objection=o,
                               action={"apply": "applied", "dismiss": "dismissed", "revise": "revise_requested"}.get(action, action),
                               reason=(reason or "").strip()[:2000], actor_id=actor.id)
    result: dict = {}
    if action == "dismiss":
        auth_service.require(actor, "approvals.submit")
        if len(rec.reason) < 5:
            raise ValidationFailed("Give a reason for dismissing the concern (it is kept in the audit history)")
    elif action == "apply":
        if not f["edits"]:
            raise ValidationFailed("This concern has no change the app can apply automatically. Edit the plan manually or ask the Main AI to revise.")
        if appr is None:
            raise Conflict("The plan has no approval request to supersede")
        if appr.status == "APPROVED" and not confirm_supersede:
            raise Conflict("This version is approved. Applying the change creates a new version that must be approved again; confirm to continue.")
        from app.services import plan_editor
        result = plan_editor.edit_approval(db, actor, appr.id, f["edits"], f"Applied Challenger suggestion: {f['suggestion'][:300]}",
                                           supersede_approved=confirm_supersede)
        rec.edits, rec.result_plan_id, rec.result_approval_id = f["edits"], result["plan_id"], result["approval_id"]
    elif action == "revise":
        from app.services import tasks_service
        instr = (f"Address this independent review concern ({f['severity']}, {f['category']}): {f['message']}"
                 + (f" Suggested correction: {f['suggestion']}" if f["suggestion"] else "") + (f" Note: {rec.reason}" if rec.reason else ""))
        t, attempt = tasks_service.revise_draft(db, actor, task.id, instr[:tasks_service.MAX_INSTRUCTIONS])
        result = {"queued": True, "attempt": attempt}
    elif action == "edited":
        auth_service.require(actor, "approvals.submit")
    db.add(rec)
    activity.log(db, f"challenger.{rec.action}", actor=actor, entity="plan", entity_id=plan.id, client_id=task.client_id,
                 details={"objection": f["message"][:300], "severity": f["severity"], "reason": rec.reason, "edits": rec.edits,
                          "result_plan_id": rec.result_plan_id})
    return {"resolution": rec.action, **result}


def rereview(db: Session, actor: User, task_id: str) -> dict:
    """Run the Challenger again on the CURRENT version (e.g. after applying fixes). The previous report is kept in its history; the
    draft and approval are not changed by a review."""
    from app.services import challenger, clients_service, tasks_service
    task = db.get(Task, task_id)
    if not task:
        raise NotFound("Task not found")
    auth_service.require(actor, "tasks.run")
    auth_service.require_client_access(db, actor, task.client_id)
    plan, _ = _current(db, task)
    biz = db.get(Business, task.business_id) if task.business_id else None
    info = tasks_service.research_for_retry(db, task)
    ev = {"verified_claims": (biz.verified_claims if biz else []) or [], "excluded_services": clients_service.excluded_services(biz) if biz else [],
          "feasibility": {k: (info["ctx"].get("feasibility") or {}).get("summary", {}).get(k) for k in ("status", "expected_weekly_conversions", "weekly_target")}}
    kw = (info["ctx"].get("keywords") or {}).get("keywords")
    if kw:
        from app.services.competition import _evidence_keywords
        ev["research"] = _evidence_keywords(kw)
    kind = "campaign_draft" if plan.scope in ("CAMPAIGN", "AD_GROUP") else "ad_copy"
    from app.services import research_events
    with research_events.bind(None, client_id=task.client_id, business_id=task.business_id, run_id=task.id):
        research_events.emit("stage_started", f"Challenger re-review of version {plan.version}", stage="challenge", step="challenge")
        report, run_id = challenger.review_draft(db, plan.draft, ev, kind=kind, task_id=task.id)
        research_events.emit("stage_completed", "Challenger re-review finished", stage="challenge", step="challenge",
                             summary=(f"Verdict {report.get('verdict')}" if report.get("performed") else f"Not performed: {report.get('reason')}")[:400])
    research_events.forget(task.id)
    prev = dict(plan.challenger or {})
    history = list(prev.pop("history", []) or [])
    if prev:
        history.append({k: prev.get(k) for k in ("verdict", "performed", "reason", "objections", "reviewed_version", "run_id")})
    plan.challenger = {**report, "reviewed_version": plan.version, "history": history[-10:]}
    if run_id:
        plan.challenger_ai_run_id = run_id
    activity.log(db, "challenger.rereviewed", actor=actor, entity="plan", entity_id=plan.id, client_id=task.client_id,
                 details={"performed": report.get("performed"), "verdict": report.get("verdict"), "reason": report.get("reason", "")})
    return findings(db, task)
