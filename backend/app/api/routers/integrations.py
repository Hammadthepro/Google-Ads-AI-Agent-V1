"""API & Account Integrations: credential cards, real connection tests, Google OAuth, operating mode, health."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.errors import NotFound
from app.db.session import get_db
from app.models import User
from app.services import activity, integrations_service as svc

router = APIRouter(tags=["integrations"])


def _provider(name: str) -> str:
    if name not in svc.PROVIDERS:
        raise NotFound("Unknown integration")
    return name


@router.get("/integrations")
def list_integrations(user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    return {"integrations": [svc.serialize(db, p) for p in svc.PROVIDERS], "mode": svc.mode_summary(db),
            "redirect_uri": svc.oauth_redirect_uri()}


@router.get("/integrations/health")
def integrations_health(user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    return svc.health(db)


class ModeIn(BaseModel):
    mode: str
    reason: str = ""


@router.get("/integrations/mode")
def get_mode(user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    return svc.mode_summary(db)


@router.put("/integrations/mode")
def put_mode(body: ModeIn, user: User = Depends(require("integrations.manage")), db: Session = Depends(get_db)):
    before = svc.operating_mode(db)
    out = svc.set_operating_mode(db, body.mode, user.id)
    activity.log(db, "integrations.mode_changed", actor=user, entity="system", entity_id="operating_mode",
                 details={"from": before, "to": body.mode, "reason": body.reason})
    db.commit()
    return out


@router.post("/integrations/test-all")
def test_all(user: User = Depends(require("integrations.manage")), db: Session = Depends(get_db)):
    res = svc.test_all(db, user.id)
    activity.log(db, "integrations.test_all", actor=user, entity="system", entity_id="integrations",
                 details={r["provider"]: r["status"] for r in res})
    db.commit()
    return {"results": res, "health": svc.health(db)}


class GoogleStart(BaseModel):
    providers: list[str] = Field(default_factory=lambda: ["google_ads"])


@router.post("/integrations/google/start")
def google_start(body: GoogleStart, user: User = Depends(require("integrations.manage")), db: Session = Depends(get_db)):
    out = svc.oauth_start(db, body.providers, user.id)
    activity.log(db, "integrations.oauth_started", actor=user, entity="integration", entity_id=",".join(body.providers))
    db.commit()
    return out


@router.get("/integrations/google/callback")
def google_callback(state: str = "", code: str = "", error: str = "", user: User = Depends(require("integrations.manage")),
                    db: Session = Depends(get_db)):
    if error or not code:
        return RedirectResponse(f"/integrations?oauth=failed&reason={error or 'no_code'}")
    out = svc.oauth_callback(db, state, code, user.id)
    activity.log(db, "integrations.oauth_connected", actor=user, entity="integration", entity_id=",".join(out["saved"]),
                 details={"scopes": out["granted_scopes"]})
    db.commit()
    return RedirectResponse("/integrations?oauth=connected&providers=" + ",".join(out["saved"]))


@router.get("/integrations/{provider}")
def get_integration(provider: str, user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    return svc.serialize(db, _provider(provider))


class ConfigIn(BaseModel):
    settings: dict = Field(default_factory=dict)
    secrets: dict = Field(default_factory=dict)  # write-only; blank = keep existing


@router.put("/integrations/{provider}")
def put_integration(provider: str, body: ConfigIn, user: User = Depends(require("integrations.manage")), db: Session = Depends(get_db)):
    svc.save(db, _provider(provider), settings=body.settings, secrets=body.secrets, user_id=user.id)
    activity.log(db, "integrations.configured", actor=user, entity="integration", entity_id=provider,
                 details={"settings": sorted(body.settings), "secrets_changed": sorted(k for k, v in body.secrets.items() if v)})
    db.commit()
    return svc.serialize(db, provider)


@router.post("/integrations/{provider}/test")
def test_integration(provider: str, user: User = Depends(require("integrations.manage")), db: Session = Depends(get_db)):
    res = svc.test_connection(db, _provider(provider), user_id=user.id)
    activity.log(db, "integrations.tested", actor=user, entity="integration", entity_id=provider,
                 details={"ok": res["ok"], "status": res["status"]})
    db.commit()
    return {**res, "integration": svc.serialize(db, provider)}


class EnableIn(BaseModel):
    enabled: bool


@router.post("/integrations/{provider}/enable")
def enable_integration(provider: str, body: EnableIn, user: User = Depends(require("integrations.manage")), db: Session = Depends(get_db)):
    svc.set_enabled(db, _provider(provider), body.enabled, user.id)
    activity.log(db, "integrations.enabled" if body.enabled else "integrations.disabled", actor=user, entity="integration", entity_id=provider)
    db.commit()
    return svc.serialize(db, provider)


@router.post("/integrations/{provider}/disconnect")
def disconnect_integration(provider: str, user: User = Depends(require("integrations.manage")), db: Session = Depends(get_db)):
    svc.disconnect(db, _provider(provider), user.id)
    if provider == "google_ads":
        from app.services import settings_service
        settings_service.set_value(db, "operating_mode", "mock", user.id)  # never leave real mode without credentials
    activity.log(db, "integrations.disconnected", actor=user, entity="integration", entity_id=provider)
    db.commit()
    svc.invalidate()
    return svc.serialize(db, provider)
