from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import registry
from app.api.deps import current_user, require
from app.core.config import get_settings
from app.core.errors import Forbidden
from app.db.session import get_db
from app.models import User
from app.services import auth_service

router = APIRouter(tags=["auth & users"])


def _set_cookies(resp: Response, token: str, csrf: str) -> None:
    s = get_settings()
    resp.set_cookie(auth_service.SESSION_COOKIE, token, httponly=True, secure=s.SESSION_COOKIE_SECURE, samesite="lax",
                    max_age=s.SESSION_TTL_HOURS * 3600, path="/")
    resp.set_cookie(auth_service.CSRF_COOKIE, csrf, httponly=False, secure=s.SESSION_COOKIE_SECURE, samesite="strict",
                    max_age=s.SESSION_TTL_HOURS * 3600, path="/")


@router.get("/auth/config")
def auth_config(db: Session = Depends(get_db)):
    s = get_settings()
    data = {"provider": s.AUTH_PROVIDER, "google_client_id": s.GOOGLE_CLIENT_ID if s.AUTH_PROVIDER == "google" else "",
            "app_env": s.APP_ENV, "integration_mode": s.INTEGRATION_MODE}
    if s.AUTH_PROVIDER == "mock" and s.APP_ENV != "production":
        data["dev_users"] = [{"email": u.email, "name": u.name, "role": u.role}
                             for u in db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.role))]
    return data


class DevLogin(BaseModel):
    email: EmailStr


@router.post("/auth/dev-login")
def dev_login(body: DevLogin, request: Request, response: Response, db: Session = Depends(get_db)):
    if get_settings().AUTH_PROVIDER != "mock":
        raise Forbidden("Development login is disabled")
    user, token, csrf = auth_service.login(db, {"email": body.email}, ip=request.client.host if request.client else "",
                                           user_agent=request.headers.get("user-agent", ""))
    _set_cookies(response, token, csrf)
    return {"user": auth_service.serialize_user(user, db), "csrf_token": csrf}


class GoogleLogin(BaseModel):
    credential: str


@router.post("/auth/google")
def google_login(body: GoogleLogin, request: Request, response: Response, db: Session = Depends(get_db)):
    if get_settings().AUTH_PROVIDER != "google":
        raise Forbidden("Google sign-in is not enabled (AUTH_PROVIDER=mock)")
    user, token, csrf = auth_service.login(db, {"id_token": body.credential}, ip=request.client.host if request.client else "",
                                           user_agent=request.headers.get("user-agent", ""))
    _set_cookies(response, token, csrf)
    return {"user": auth_service.serialize_user(user, db), "csrf_token": csrf}


@router.post("/auth/logout")
def logout(request: Request, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    auth_service.logout(db, request.state.session, user)
    response.delete_cookie(auth_service.SESSION_COOKIE, path="/")
    response.delete_cookie(auth_service.CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"user": auth_service.serialize_user(user, db), "auth_provider": registry.get("auth").name}


@router.get("/users")
def list_users(user: User = Depends(require("users.manage")), db: Session = Depends(get_db)):
    return [auth_service.serialize_user(u, db) for u in db.scalars(select(User).order_by(User.email))]


class UserIn(BaseModel):
    email: EmailStr
    name: str = ""
    role: str
    client_ids: list[str] = []


@router.post("/users")
def create_user(body: UserIn, user: User = Depends(require("users.manage")), db: Session = Depends(get_db)):
    u = auth_service.create_user(db, user, body.email, body.name, body.role, body.client_ids)
    db.commit()
    return auth_service.serialize_user(u, db)


class UserPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    client_ids: list[str] | None = None


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserPatch, user: User = Depends(require("users.manage")), db: Session = Depends(get_db)):
    u = auth_service.update_user(db, user, user_id, **body.model_dump(exclude_none=True))
    db.commit()
    return auth_service.serialize_user(u, db)
