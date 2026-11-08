"""Structured editing of an AI campaign plan, plan versions, and original-vs-final diff.

An edit never mutates history: it creates plan version N+1 (parent = N, `original` = the AI-consolidated plan is kept),
rebuilds the exact Google Ads payload, re-validates it and supersedes the old approval with a NEW one that must be
approved again (exact-version approval). Nothing is sent to Google Ads by editing.
"""
from __future__ import annotations

import copy

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.util import normalize_term
from app.models import CampaignPlan, Task, User
from app.services import activity, approvals, auth_service, validation

EDIT_OPS = {"rename_campaign", "set_bidding", "set_budget", "add_keyword", "remove_keyword", "add_negative", "remove_negative",
            "edit_ad", "set_landing_page", "remove_ad_group"}


def _group(draft: dict, scope: str, name: str | None) -> dict:
    if scope == "AD_GROUP":
        return draft
    for g in draft.get("ad_groups", []):
        if normalize_term(g["name"]) == normalize_term(name or ""):
            return g
    raise ValidationFailed(f"Ad group '{name}' not found in this plan")


def _kw(e: dict) -> tuple[str, str]:
    text = (e.get("text") or "").strip().lower()
    mt = (e.get("match_type") or "").upper()
    if not text or len(text) > 80 or len(text.split()) > 10:
        raise ValidationFailed("Keyword must be 1-10 words and at most 80 characters")
    if mt not in ("EXACT", "PHRASE"):
        raise ValidationFailed("Only EXACT and PHRASE match are allowed in main ad groups (broad match is an approved experiment only)")
    return text, mt


def apply(draft: dict, scope: str, edits: list[dict]) -> tuple[dict, list[str], dict]:
    """Returns (new_draft, human-readable change log, task-field changes)."""
    if not edits:
        raise ValidationFailed("No edits supplied")
    d = copy.deepcopy(draft)
    log: list[str] = []
    fields: dict = {}
    for e in edits:
        op = e.get("op")
        if op not in EDIT_OPS:
            raise ValidationFailed(f"Unknown edit '{op}'")
        if op == "rename_campaign":
            if scope != "CAMPAIGN":
                raise ValidationFailed("Only campaign plans have a campaign name")
            name = (e.get("value") or "").strip()
            if not name or len(name) > 255:
                raise ValidationFailed("Campaign name must be 1-255 characters")
            log.append(f"Renamed campaign '{d['campaign_name']}' → '{name}'")
            d["campaign_name"] = name
        elif op == "set_bidding":
            if e.get("value") not in ("MAXIMIZE_CONVERSIONS", "MAXIMIZE_CLICKS"):
                raise ValidationFailed("Bidding must be MAXIMIZE_CONVERSIONS or MAXIMIZE_CLICKS")
            log.append(f"Bidding {d.get('bidding_strategy')} → {e['value']}")
            d["bidding_strategy"] = e["value"]
        elif op == "set_budget":
            v = float(e.get("monthly_budget") or 0)
            if v <= 0:
                raise ValidationFailed("Monthly budget must be positive")
            fields["monthly_budget"] = v
            log.append(f"Monthly budget set to ${v:,.0f}")
        elif op in ("add_keyword", "remove_keyword"):
            g = _group(d, scope, e.get("ad_group"))
            text, mt = _kw(e)
            have = [k for k in g["keywords"] if normalize_term(k["text"]) == normalize_term(text) and k["match_type"] == mt]
            if op == "add_keyword":
                if have:
                    raise ValidationFailed(f"'{text}' ({mt}) is already in {g['name']}")
                g["keywords"].append({"text": text, "match_type": mt})
                log.append(f"Added keyword [{mt}] '{text}' to {g['name']}")
            else:
                if not have:
                    raise ValidationFailed(f"'{text}' ({mt}) is not in {g['name']}")
                g["keywords"].remove(have[0])
                log.append(f"Removed keyword [{mt}] '{text}' from {g['name']}")
        elif op in ("add_negative", "remove_negative"):
            text = (e.get("text") or "").strip().lower()
            if not text:
                raise ValidationFailed("Negative keyword text is required")
            key = "campaign_negatives" if scope == "CAMPAIGN" else "negatives"
            lst = d.setdefault(key, [])
            if op == "add_negative":
                if text in lst:
                    raise ValidationFailed(f"Negative '{text}' already exists")
                lst.append(text)
                log.append(f"Added negative keyword '{text}'")
            else:
                if text not in lst:
                    raise ValidationFailed(f"Negative '{text}' not found")
                lst.remove(text)
                log.append(f"Removed negative keyword '{text}'")
        elif op == "edit_ad":
            target = _group(d, scope, e.get("ad_group")) if scope != "AD" else d
            ads = target["ads"] if scope != "AD" else [d]
            i = int(e.get("index", 0))
            if i >= len(ads):
                raise ValidationFailed("Ad not found")
            ad = ads[i]
            for k in ("headlines", "descriptions"):
                if k in e:
                    vals = [str(x).strip() for x in e[k] if str(x).strip()]
                    if not vals:
                        raise ValidationFailed(f"{k} cannot be empty")
                    ad[k] = vals
            for k in ("path1", "path2"):
                if k in e:
                    ad[k] = str(e[k])[:15]
            log.append("Edited responsive search ad" + (f" in {target.get('name')}" if scope != "AD" else ""))
        elif op == "set_landing_page":
            g = _group(d, scope, e.get("ad_group"))
            url = (e.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                raise ValidationFailed("Landing page must be an http(s) URL")
            log.append(f"Landing page of {g['name']}: {g['landing_page']} → {url}")
            g["landing_page"] = url
        elif op == "remove_ad_group":
            if scope != "CAMPAIGN":
                raise ValidationFailed("Only campaign plans contain several ad groups")
            g = _group(d, scope, e.get("ad_group"))
            if len(d["ad_groups"]) == 1:
                raise ValidationFailed("A campaign needs at least one ad group")
            d["ad_groups"].remove(g)
            share = round(100 / len(d["ad_groups"]), 1)
            d["budget_allocation"] = [{"ad_group": x["name"], "percent": share} for x in d["ad_groups"]]
            log.append(f"Removed ad group '{g['name']}' (budget split recalculated)")
    return d, log, fields


# ------------------------------------------------------------------------------------------ diff
def _kwset(g):
    return {(normalize_term(k["text"]), k["match_type"]) for k in g.get("keywords", [])}


def diff(original: dict, current: dict, scope: str) -> dict:
    """Structured difference original → current, grouped for display."""
    out: dict = {"changed": False, "campaign": [], "ad_groups": [], "negatives": {"added": [], "removed": []}}

    def field(label, a, b):
        if a != b:
            out["campaign"].append({"field": label, "from": a, "to": b})
    if scope == "CAMPAIGN":
        field("campaign_name", original.get("campaign_name"), current.get("campaign_name"))
        field("bidding_strategy", original.get("bidding_strategy"), current.get("bidding_strategy"))
        og = {normalize_term(g["name"]): g for g in original.get("ad_groups", [])}
        cg = {normalize_term(g["name"]): g for g in current.get("ad_groups", [])}
        pairs = [(og.get(k), cg.get(k)) for k in dict.fromkeys([*og, *cg])]
        neg_key = "campaign_negatives"
    else:
        pairs = [(original, current)]
        neg_key = "negatives"
    for o, c in pairs:
        if o is None or c is None:
            out["ad_groups"].append({"name": (c or o)["name"], "status": "ADDED" if o is None else "REMOVED"})
            continue
        ent: dict = {"name": c["name"], "status": "CHANGED", "keywords_added": [], "keywords_removed": [], "headlines_added": [],
                     "headlines_removed": [], "descriptions_changed": False, "landing_page": None}
        ko, kc = _kwset(o), _kwset(c)
        ent["keywords_added"] = [f"[{m}] {t}" for t, m in sorted(kc - ko)]
        ent["keywords_removed"] = [f"[{m}] {t}" for t, m in sorted(ko - kc)]
        ho = {h for a in o.get("ads", []) for h in a["headlines"]}
        hc = {h for a in c.get("ads", []) for h in a["headlines"]}
        ent["headlines_added"], ent["headlines_removed"] = sorted(hc - ho), sorted(ho - hc)
        ent["descriptions_changed"] = [a["descriptions"] for a in o.get("ads", [])] != [a["descriptions"] for a in c.get("ads", [])]
        if o.get("landing_page") != c.get("landing_page"):
            ent["landing_page"] = {"from": o.get("landing_page"), "to": c.get("landing_page")}
        if any([ent["keywords_added"], ent["keywords_removed"], ent["headlines_added"], ent["headlines_removed"],
                ent["descriptions_changed"], ent["landing_page"]]):
            out["ad_groups"].append(ent)
    on, cn = set(original.get(neg_key, [])), set(current.get(neg_key, []))
    out["negatives"] = {"added": sorted(cn - on), "removed": sorted(on - cn)}
    out["changed"] = bool(out["campaign"] or out["ad_groups"] or out["negatives"]["added"] or out["negatives"]["removed"])
    return out


# ------------------------------------------------------------------------------------------ versions
def versions(db: Session, plan: CampaignPlan) -> list[CampaignPlan]:
    return list(db.scalars(select(CampaignPlan).where(CampaignPlan.task_id == plan.task_id, CampaignPlan.scope == plan.scope)
                           .order_by(CampaignPlan.version, CampaignPlan.created_at)))


def edit_approval(db: Session, actor: User, approval_id: str, edits: list[dict], note: str = "") -> dict:
    from app.services.tasks_service import Runner
    auth_service.require(actor, "approvals.submit")
    old = approvals.get(db, approval_id)
    auth_service.require_client_access(db, actor, old.client_id)
    if old.status not in ("PENDING_APPROVAL", "VALIDATION_FAILED", "CHANGES_REQUESTED"):
        raise Conflict(f"Approval is {old.status}; only an open approval can be edited (an approved version is never changed)")
    plan = db.get(CampaignPlan, (old.context or {}).get("plan_id") or "")
    task = db.get(Task, old.task_id) if old.task_id else None
    if not plan or not task:
        raise NotFound("This approval has no editable plan (it was not produced by the AI planner)")
    new_draft, log, field_changes = apply(plan.draft, plan.scope, edits)
    if field_changes:
        task.fields = {**(task.fields or {}), **field_changes}
    new_plan = CampaignPlan(task_id=plan.task_id, client_id=plan.client_id, business_id=plan.business_id, scope=plan.scope,
                            version=plan.version + 1, draft=new_draft, challenger=plan.challenger, human_comparison=plan.human_comparison,
                            playbook_rule_ids=plan.playbook_rule_ids, draft_ai_run_id=plan.draft_ai_run_id,
                            challenger_ai_run_id=plan.challenger_ai_run_id, competition=plan.competition,
                            original=plan.original or plan.draft, parent_plan_id=plan.id, status="EDITED",
                            change_summary="; ".join(log) + (f" — {note}" if note else ""), edited_by_id=actor.id,
                            source_type=plan.source_type)
    db.add(new_plan)
    db.flush()
    plan.status = "SUPERSEDED"
    runner = Runner(db, task)
    runner.ctx["plan"] = new_plan
    payload, _atype, _title, _cur, proposed = runner.build_payload()
    new = approvals.edit_payload(db, actor, approval_id, payload, lambda p, b: validation.validate(db, p, b, task_id=task.id))
    d = diff(new_plan.original, new_plan.draft, new_plan.scope)
    new.proposed_state = proposed
    new.context = {**(old.context or {}), "draft": new_plan.draft, "plan_id": new_plan.id, "plan_version": new_plan.version,
                   "diff_from_original": d, "edit_log": log}
    task.plan_id, task.approval_id = new_plan.id, new.id
    activity.log(db, "plan.edited", actor=actor, entity="plan", entity_id=new_plan.id, client_id=plan.client_id,
                 details={"changes": log, "note": note, "new_approval": new.id, "old_approval": old.id})
    return {"approval_id": new.id, "plan_id": new_plan.id, "plan_version": new_plan.version, "changes": log, "diff": d,
            "validation_passed": new.validation_summary.get("passed", True)}
