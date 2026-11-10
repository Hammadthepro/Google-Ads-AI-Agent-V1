"""Clients, businesses, Google Ads accounts, Ads OAuth, tracking gate, audits, fixes, feasibility."""
from __future__ import annotations


from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import registry
from app.api.deps import current_user, require
from app.core.errors import NotFound, ValidationFailed
from app.db.session import get_db
from app.models import (Ad, AdGroup, Audit, Campaign, ConversionAction, FeasibilityResult, FixTrackerItem, GoogleAdsAccount,
                        Keyword, NegativeKeyword, User)
from app.services import (accounts_service, activity, audits, auth_service, clients_service, feasibility, fixes_service,
                          tracking_gate)

router = APIRouter()


# ------------------------------------------------------------------ clients & businesses
@router.get("/clients", tags=["clients"])
def list_clients(user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    return [clients_service.serialize_client(c) for c in clients_service.list_clients(db, user)]


@router.post("/clients", tags=["clients"])
def create_client(body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = clients_service.create_client(db, user, body)
    db.commit()
    return clients_service.serialize_client(c)


@router.get("/clients/{client_id}", tags=["clients"])
def get_client(client_id: str, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    c = clients_service.get_client(db, user, client_id)
    data = clients_service.serialize_client(c)
    data["accounts"] = [accounts_service.serialize_account(a) for a in db.scalars(select(GoogleAdsAccount).where(GoogleAdsAccount.client_id == c.id))]
    data["tracking"] = {b.id: tracking_gate.serialize(tracking_gate.latest(db, b.id)) for b in c.businesses}
    return data


@router.patch("/clients/{client_id}", tags=["clients"])
def update_client(client_id: str, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = clients_service.update_client(db, user, client_id, body)
    db.commit()
    return clients_service.serialize_client(c)


@router.post("/clients/{client_id}/businesses", tags=["clients"])
def create_business(client_id: str, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = clients_service.create_business(db, user, client_id, body)
    db.commit()
    return clients_service.serialize_business(b)


@router.get("/businesses", tags=["clients"])
def list_businesses(user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    out = []
    for c in clients_service.list_clients(db, user):
        for b in c.businesses:
            d = clients_service.serialize_business(b)
            d["client_name"] = c.name
            d["tracking"] = tracking_gate.serialize(tracking_gate.latest(db, b.id))
            out.append(d)
    return out


@router.get("/businesses/{business_id}", tags=["clients"])
def get_business(business_id: str, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    b = clients_service.get_business(db, user, business_id)
    d = clients_service.serialize_business(b)
    d["tracking"] = tracking_gate.serialize(tracking_gate.latest(db, b.id))
    d["override"] = (lambda o: {"by": o.admin_email, "reason": o.reason, "at": o.created_at.isoformat()} if o else None)(
        tracking_gate.active_override(db, b.id))
    return d


@router.patch("/businesses/{business_id}", tags=["clients"])
def update_business(business_id: str, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = clients_service.update_business(db, user, business_id, body)
    db.commit()
    return clients_service.serialize_business(b)


# ------------------------------------------------------------------ Google Ads accounts
@router.get("/ads-accounts", tags=["google ads"])
def list_accounts(user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    ids = auth_service.accessible_client_ids(db, user)
    rows = db.scalars(select(GoogleAdsAccount).order_by(GoogleAdsAccount.customer_id))
    return [accounts_service.serialize_account(a) for a in rows if ids is None or a.client_id in ids]


@router.post("/ads-accounts/discover", tags=["google ads"])
def discover(user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.services import mcc_service
    mcc_service.discover(db, user)  # customer_client hierarchy of the selected manager (not just ListAccessibleCustomers)
    db.commit()
    ids = auth_service.accessible_client_ids(db, user)
    rows = db.scalars(select(GoogleAdsAccount).order_by(GoogleAdsAccount.customer_id))
    return [accounts_service.serialize_account(a) for a in rows if ids is None or a.client_id in ids or a.client_id is None]


class LinkIn(BaseModel):
    client_id: str | None


@router.post("/ads-accounts/{account_id}/link", tags=["google ads"])
def link(account_id: str, body: LinkIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = accounts_service.link(db, user, account_id, body.client_id)
    db.commit()
    return accounts_service.serialize_account(a)


@router.post("/ads-accounts/{account_id}/sync", tags=["google ads"])
def sync(account_id: str, user: User = Depends(require("monitor.run")), db: Session = Depends(get_db)):
    a = db.get(GoogleAdsAccount, account_id)
    if not a:
        raise NotFound("Account not found")
    auth_service.require_client_access(db, user, a.client_id)
    counts = accounts_service.sync_account(db, a)
    db.commit()
    return {"counts": counts, "source": registry.get("google_ads").source}


@router.get("/clients/{client_id}/structure", tags=["google ads"])
def structure(client_id: str, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    """Campaign → ad group → ad/keyword tree from the mirror (refreshed from the selected adapter)."""
    auth_service.require_client_access(db, user, client_id)
    out = []
    for c in db.scalars(select(Campaign).where(Campaign.client_id == client_id).order_by(Campaign.name)):
        groups = []
        for g in db.scalars(select(AdGroup).where(AdGroup.campaign_id == c.id).order_by(AdGroup.name)):
            groups.append({"id": g.id, "name": g.name, "status": g.status, "service": g.service, "landing_page_url": g.landing_page_url,
                           "ads": [{"id": a.id, "status": a.status, "headlines": a.headlines, "descriptions": a.descriptions,
                                    "final_url": a.final_url, "external_resource_name": a.external_resource_name}
                                   for a in db.scalars(select(Ad).where(Ad.ad_group_id == g.id))],
                           "keywords": [{"id": k.id, "text": k.text, "match_type": k.match_type, "status": k.status}
                                        for k in db.scalars(select(Keyword).where(Keyword.ad_group_id == g.id))]})
        negs = [{"text": n.text, "match_type": n.match_type, "level": n.level}
                for n in db.scalars(select(NegativeKeyword).where(NegativeKeyword.campaign_id == c.id))]
        out.append({"id": c.id, "name": c.name, "status": c.status, "campaign_type": c.campaign_type, "read_only": c.is_read_only,
                    "business_id": c.business_id, "daily_budget": c.daily_budget_micros / 1e6, "monthly_budget": c.monthly_budget_micros / 1e6,
                    "weekly_target": c.weekly_conversion_target, "bidding_strategy": c.bidding_strategy, "locations": c.locations,
                    "external_resource_name": c.external_resource_name, "created_by_app": c.created_by_app,
                    "source_type": c.source_type, "ad_groups": groups, "negatives": negs})
    accts = [a.id for a in db.scalars(select(GoogleAdsAccount).where(GoogleAdsAccount.client_id == client_id))]
    conv = [{"name": x.name, "type": x.type, "counting_type": x.counting_type, "primary": x.primary_for_goal,
             "call_min_seconds": x.call_min_duration_seconds, "status": x.status}
            for x in db.scalars(select(ConversionAction).where(ConversionAction.google_ads_account_id.in_(accts or ["-"])))]
    return {"campaigns": out, "conversion_actions": conv, "source": registry.get("google_ads").source}


# ------------------------------------------------------------------ tracking gate
@router.post("/businesses/{business_id}/tracking-gate/run", tags=["tracking"])
def run_gate(business_id: str, user: User = Depends(require("audits.run")), db: Session = Depends(get_db)):
    b = clients_service.get_business(db, user, business_id)
    r = tracking_gate.evaluate(db, b, user)
    db.commit()
    return tracking_gate.serialize(r)


@router.get("/businesses/{business_id}/tracking-gate", tags=["tracking"])
def get_gate(business_id: str, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    b = clients_service.get_business(db, user, business_id)
    return tracking_gate.serialize(tracking_gate.latest(db, b.id))


class OverrideIn(BaseModel):
    reason: str


@router.post("/businesses/{business_id}/tracking-gate/override", tags=["tracking"])
def override(business_id: str, body: OverrideIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = clients_service.get_business(db, user, business_id)
    ov = tracking_gate.override(db, user, b, body.reason)
    db.commit()
    return {"override_id": ov.id, "by": ov.admin_email, "reason": ov.reason, "at": ov.created_at.isoformat(),
            "effective_state": "AMBER", "note": "Override behaves like AMBER: launch allowed with approval; no conversion-based automation."}


# ------------------------------------------------------------------ audits
class AuditIn(BaseModel):
    business_id: str
    audit_type: str  # WEBSITE / GBP / COMPETITOR / KEYWORD / ACCOUNT / ALL


@router.post("/audits", tags=["audits"])
def run_audit(body: AuditIn, user: User = Depends(require("audits.run")), db: Session = Depends(get_db)):
    b = clients_service.get_business(db, user, body.business_id)
    if user.role == "ANALYST" and body.audit_type not in auth_service.ANALYST_AUDITS | {"ALL"}:
        raise ValidationFailed("Analysts can run website, business profile, competitor and keyword audits")
    t = body.audit_type
    if t == "ACCOUNT":
        rows = [audits.account_audit(db, b, None, user.id)]
    elif t == "KEYWORD":
        par = audits.run_parallel(db, b, task_id=None, actor_id=user.id)
        kw = audits.keyword_research(db, par["context"], par["website"], par["competitor"], None)
        rows = [audits.save_keyword_audit(db, b, None, user.id, kw)]
    elif t in ("WEBSITE", "GBP", "COMPETITOR", "ALL"):
        par = audits.run_parallel(db, b, task_id=None, actor_id=user.id)
        rows = [db.get(Audit, i) for k, i in par["audit_ids"].items() if t == "ALL" or k == t]
    else:
        raise ValidationFailed("Unknown audit type")
    activity.log(db, "audit.run", actor=user, entity="business", entity_id=b.id, client_id=b.client_id, details={"type": t})
    db.commit()
    return [audits.serialize(r) for r in rows]


@router.get("/audits", tags=["audits"])
def list_audits(business_id: str | None = None, client_id: str | None = None, user: User = Depends(require("clients.view")),
                db: Session = Depends(get_db)):
    ids = auth_service.accessible_client_ids(db, user)
    q = select(Audit).order_by(Audit.started_at.desc()).limit(200)
    if business_id:
        q = q.where(Audit.business_id == business_id)
    if client_id:
        q = q.where(Audit.client_id == client_id)
    return [{k: v for k, v in audits.serialize(a).items() if k != "result"} for a in db.scalars(q) if ids is None or a.client_id in ids]


@router.get("/audits/{audit_id}", tags=["audits"])
def get_audit(audit_id: str, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    a = db.get(Audit, audit_id)
    if not a:
        raise NotFound("Audit not found")
    auth_service.require_client_access(db, user, a.client_id)
    return audits.serialize(a)


# ------------------------------------------------------------------ fixes & feasibility
@router.get("/fixes", tags=["fixes"])
def list_fixes(client_id: str | None = None, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    ids = auth_service.accessible_client_ids(db, user)
    q = select(FixTrackerItem).order_by(FixTrackerItem.status, FixTrackerItem.created_at.desc())
    if client_id:
        q = q.where(FixTrackerItem.client_id == client_id)
    return [fixes_service.serialize(f) for f in db.scalars(q) if ids is None or f.client_id in ids]


@router.post("/fixes", tags=["fixes"])
def create_fix(body: dict, user: User = Depends(require("plans.create")), db: Session = Depends(get_db)):
    auth_service.require_client_access(db, user, body.get("client_id"))
    f = fixes_service.upsert(db, body["client_id"], body.get("business_id"), issue=body["issue"], category=body.get("category", "general"),
                             priority=body.get("priority", "MEDIUM"), owner=body.get("owner", "client"), source="manual",
                             evidence=body.get("evidence", ""))
    db.commit()
    return fixes_service.serialize(f)


@router.patch("/fixes/{fix_id}", tags=["fixes"])
def update_fix(fix_id: str, body: dict, user: User = Depends(require("plans.create")), db: Session = Depends(get_db)):
    f = fixes_service.update(db, fix_id, body)
    activity.log(db, "fix.updated", actor=user, entity="fix", entity_id=f.id, client_id=f.client_id, details=body)
    db.commit()
    return fixes_service.serialize(f)


class FeasIn(BaseModel):
    monthly_budget: float
    weekly_target: float
    cpc: list[float] = [8.0, 6.0, 4.5]
    cvr: list[float] = [0.03, 0.06, 0.10]
    review_adjustment: float = 0.0


@router.post("/feasibility/calculate", tags=["feasibility"])
def calc(body: FeasIn, user: User = Depends(current_user)):
    if body.monthly_budget <= 0:
        raise ValidationFailed("Budget must be positive")
    return feasibility.calculate(monthly_budget=body.monthly_budget, weekly_target=body.weekly_target, cpc_range=tuple(body.cpc),
                                 cvr_range=tuple(body.cvr), review_adjustment=body.review_adjustment)


@router.get("/feasibility", tags=["feasibility"])
def list_feas(business_id: str | None = None, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    ids = auth_service.accessible_client_ids(db, user)
    q = select(FeasibilityResult).order_by(FeasibilityResult.created_at.desc()).limit(50)
    if business_id:
        q = q.where(FeasibilityResult.business_id == business_id)
    return [feasibility.serialize(r) for r in db.scalars(q) if ids is None or r.client_id in ids]
