"""API & Account Integrations: credential cards, real connection tests, Google OAuth, operating mode, health."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from urllib.parse import quote

from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.errors import NotFound, ValidationFailed
from app.db.session import get_db
from app.models import User
from app.services import activity, integrations_service as svc

router = APIRouter(tags=["integrations"])


def _origin(request: Request) -> str:
    """The address the browser is using (sent by browsers on POST/fetch); only used when GOOGLE_ADS_REDIRECT_URI is blank."""
    origin = request.headers.get("origin", "")
    if not origin:
        host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
        origin = f"{request.headers.get('x-forwarded-proto') or request.url.scheme}://{host}" if host else ""
    return origin


def _provider(name: str) -> str:
    if name not in svc.PROVIDERS:
        raise NotFound("Unknown integration")
    return name


@router.get("/integrations")
def list_integrations(request: Request, user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    uri, source = svc.oauth_redirect_uri(_origin(request))
    return {"integrations": [svc.serialize(db, p) for p in svc.listed()], "mode": svc.mode_summary(db),
            "redirect_uri": uri, "redirect_uri_source": source}


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
    from app.services import ai_roles
    for role in ai_roles.ROLES:  # the Main AI and Challenger AI are integrations too: test the configured ones with real requests
        v = ai_roles.resolve(role)
        if not v.get("provider"):
            res.append({"provider": f"ai_{role}", "ok": False, "connected": False, "skipped": True, "status": "NOT_CONFIGURED",
                        "steps": [{"name": "Configured", "status": "SKIPPED", "detail": f"{ai_roles.ROLES[role]} is not configured"}]})
            continue
        try:
            r = ai_roles.test(db, role, {}, user)
        except ValidationFailed as exc:
            res.append({"provider": f"ai_{role}", "ok": False, "connected": False, "skipped": True, "status": "NOT_CONFIGURED",
                        "steps": [{"name": "Configured", "status": "SKIPPED", "detail": exc.message}]})
            continue
        res.append({"provider": f"ai_{role}", "ok": r["ok"], "connected": r["ok"], "status": "CONNECTED" if r["ok"] else "FAILED",
                    "steps": [{"name": x["name"], "status": "PASSED" if x["ok"] else "FAILED", "detail": x["detail"]} for x in r["steps"]]})
    activity.log(db, "integrations.test_all", actor=user, entity="system", entity_id="integrations",
                 details={r["provider"]: r["status"] for r in res})
    db.commit()
    return {"results": res, "health": svc.health(db)}


class GoogleStart(BaseModel):
    providers: list[str] = Field(default_factory=lambda: ["google_ads"])


@router.post("/integrations/google/start")
def google_start(body: GoogleStart, request: Request, user: User = Depends(require("integrations.manage")), db: Session = Depends(get_db)):
    out = svc.oauth_start(db, body.providers, user.id, origin=_origin(request))
    activity.log(db, "integrations.oauth_started", actor=user, entity="integration", entity_id=",".join(body.providers),
                 details={"redirect_uri": out["redirect_uri"], "redirect_uri_source": out["redirect_uri_source"]})  # never the state, challenge or URL
    db.commit()
    return out


@router.get("/integrations/google/callback")
def google_callback(state: str = "", code: str = "", error: str = "", user: User = Depends(require("integrations.manage")),
                    db: Session = Depends(get_db)):
    if error or not code:
        return RedirectResponse("/integrations?oauth=failed&reason=" + quote((error or "no_code")[:80]))
    try:
        out = svc.oauth_callback(db, state, code, user.id)
    except ValidationFailed as exc:  # show the Integrations page with a reason, not a bare JSON error; the code/state are never logged
        db.commit()  # keeps the single-use mark on the state
        return RedirectResponse("/integrations?oauth=failed&reason=" + quote(str(exc.message)[:160]))
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
    if svc.PROVIDERS[_provider(provider)].get("readonly"):
        raise ValidationFailed("This component is configured through environment variables (.env), not on this page")
    svc.save(db, _provider(provider), settings=body.settings, secrets=body.secrets, user_id=user.id)
    activity.log(db, "integrations.configured", actor=user, entity="integration", entity_id=provider,
                 details={"settings": sorted(body.settings), "secrets_changed": sorted(k for k, v in body.secrets.items() if v)})
    db.commit()
    return svc.serialize(db, provider)


@router.post("/integrations/{provider}/test")
def test_integration(provider: str, target: str = "all", user: User = Depends(require("integrations.manage")), db: Session = Depends(get_db)):
    """Real request(s) to the provider. `target` narrows google_ads (manager|client) or groq (primary|challenger)."""
    res = svc.test_connection(db, _provider(provider), user_id=user.id, target=target)
    activity.log(db, "integrations.tested", actor=user, entity="integration", entity_id=provider,
                 details={"ok": res["ok"], "connected": res["connected"], "status": res["status"], "target": target})
    db.commit()
    return {**res, "integration": svc.serialize(db, provider)}


@router.get("/integrations/{provider}/options")
def integration_options(provider: str, user: User = Depends(require("integrations.manage")), db: Session = Depends(get_db)):
    """Live, read-only discovery for pickers: GA4 properties, Business Profile accounts/locations, Claude models."""
    out = svc.options(db, _provider(provider))
    activity.log(db, "integrations.options_listed", actor=user, entity="integration", entity_id=provider, details={"ok": out["ok"]})
    db.commit()
    return out


class TestEmailIn(BaseModel):
    to: str


@router.post("/integrations/smtp/test-email")
def smtp_test_email(body: TestEmailIn, user: User = Depends(require("integrations.manage")), db: Session = Depends(get_db)):
    """Sends ONE real message to the address typed by the administrator."""
    out = svc.send_test_email(db, body.to)
    activity.log(db, "integrations.smtp_test_email", actor=user, entity="integration", entity_id="smtp", details={"to": body.to, "sent": out["sent"]})
    db.commit()
    return out


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
    if svc.PROVIDERS[_provider(provider)].get("readonly"):
        raise ValidationFailed("This component is configured through environment variables (.env) and cannot be disconnected here")
    svc.disconnect(db, _provider(provider), user.id)
    if provider == "google_ads":
        from app.services import settings_service
        settings_service.set_value(db, "operating_mode", "read_only", user.id)  # never stay in execution without credentials
    activity.log(db, "integrations.disconnected", actor=user, entity="integration", entity_id=provider)
    db.commit()
    svc.invalidate()
    return svc.serialize(db, provider)
