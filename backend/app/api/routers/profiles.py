"""Client profile form (create / edit / validate) and the five-part audit reports stored against each client."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, require
from app.core.errors import NotFound, ValidationFailed
from app.db.session import get_db
from app.models import AuditReport, Business, User
from app.services import activity, audit_report, auth_service, client_profile, clients_service

router = APIRouter(tags=["client profiles"])


class ProfileIn(BaseModel):
    profile: dict
    business_id: str | None = None


@router.post("/client-profiles/validate")
def validate(body: ProfileIn, client_id: str | None = None, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    """Field-level validation while typing. Writes nothing."""
    if client_id:
        auth_service.require_client_access(db, user, client_id)
    clean, errors = client_profile.validate_profile(db, body.profile, exclude_client_id=client_id)
    return {"valid": not errors, "errors": errors, "normalized": clean}


@router.post("/client-profiles")
def create(body: ProfileIn, audit: str = "background", user: User = Depends(current_user), db: Session = Depends(get_db)):
    client, biz = client_profile.create(db, user, body.profile)
    db.commit()
    report = None
    if audit != "none" and auth_service.has_permission(user, "audits.run"):
        report = audit_report.launch(db, biz, client, user.id, sync=(audit == "sync"))
    return {"client_id": client.id, "client_code": client.client_code, "business_id": biz.id, "audit_report_id": report.id if report else None}


@router.get("/client-profiles/{client_id}")
def read(client_id: str, business_id: str | None = None, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    return client_profile.get(db, user, client_id, business_id)


@router.put("/client-profiles/{client_id}")
def update(client_id: str, body: ProfileIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    client, biz, changed = client_profile.update(db, user, client_id, body.business_id, body.profile)
    db.commit()
    return {**client_profile.to_form(client, biz), "changed": changed,
            "message": "Profile saved. Run a new audit to refresh the findings." if changed else "No changes."}


def _business(db: Session, user: User, client_id: str, business_id: str | None) -> Business:
    client = clients_service.get_client(db, user, client_id)
    b = db.get(Business, business_id) if business_id else (client.businesses[0] if client.businesses else None)
    if not b or b.client_id != client.id:
        raise NotFound("This client has no business profile")
    return b


@router.post("/clients/{client_id}/audit-reports")
def start_audit(client_id: str, business_id: str | None = None, sync: bool = False, user: User = Depends(require("audits.run")),
                db: Session = Depends(get_db)):
    b = _business(db, user, client_id, business_id)
    rep = audit_report.launch(db, b, b.client, user.id, sync=sync)
    activity.log(db, "audit_report.started", actor=user, entity="audit_report", entity_id=rep.id, client_id=client_id, details={"version": rep.version})
    db.commit()
    return audit_report.serialize(rep, full=sync)


@router.get("/clients/{client_id}/audit-reports")
def list_reports(client_id: str, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    clients_service.get_client(db, user, client_id)
    rows = list(db.scalars(select(AuditReport).where(AuditReport.client_id == client_id).order_by(AuditReport.started_at.desc())))
    cur = {}
    for r in rows:
        if r.business_id not in cur:
            b = db.get(Business, r.business_id)
            cur[r.business_id] = audit_report._hash(audit_report.snapshot(b, b.client)) if b else None
    return [audit_report.serialize(r, full=False, current_profile_hash=cur.get(r.business_id)) for r in rows]


@router.get("/audit-reports/{report_id}")
def get_report(report_id: str, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    r = audit_report.get_for_user(db, report_id)
    auth_service.require_client_access(db, user, r.client_id)  # reports are only ever visible through their own client
    b = db.get(Business, r.business_id)
    cur = audit_report._hash(audit_report.snapshot(b, b.client)) if b else None
    return audit_report.serialize(r, current_profile_hash=cur)


@router.get("/audit-reports/{report_id}/keywords.csv", response_class=PlainTextResponse)
def keywords_csv(report_id: str, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    r = audit_report.get_for_user(db, report_id)
    auth_service.require_client_access(db, user, r.client_id)
    if r.status != "COMPLETED":
        raise ValidationFailed("The report is not finished yet")
    return PlainTextResponse(audit_report.keyword_csv(r), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="keywords-{r.client_id[:8]}-v{r.version}.csv"'})
