"""Users, sessions, CSRF and role-based permissions (enforced on the backend for every protected route)."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import registry
from app.core.config import get_settings
from app.core.errors import Forbidden, NotFound, Unauthorized
from app.models import Role, User, UserClientAssignment, UserSession
from app.services import activity

# permission -> roles
PERMISSIONS: dict[str, set[str]] = {
    "users.manage": {Role.SUPER_ADMIN, Role.ADMIN},
    "integrations.manage": {Role.SUPER_ADMIN},
    "settings.manage": {Role.SUPER_ADMIN},
    "settings.view": {Role.SUPER_ADMIN, Role.ADMIN},
    "killswitch.global": {Role.SUPER_ADMIN},
    "killswitch.client": {Role.SUPER_ADMIN, Role.ADMIN},
    "clients.create": {Role.SUPER_ADMIN, Role.ADMIN},
    "clients.edit": {Role.SUPER_ADMIN, Role.ADMIN, Role.CAMPAIGN_MANAGER},
    "clients.view": set(Role.ALL),
    "audits.run": set(Role.ALL),
    "tasks.run": {Role.SUPER_ADMIN, Role.ADMIN, Role.CAMPAIGN_MANAGER},
    "plans.create": {Role.SUPER_ADMIN, Role.ADMIN, Role.CAMPAIGN_MANAGER},
    "approvals.submit": {Role.SUPER_ADMIN, Role.ADMIN, Role.CAMPAIGN_MANAGER},
    "approvals.decide": {Role.SUPER_ADMIN, Role.ADMIN},
    "approvals.decide_privileged": {Role.SUPER_ADMIN},
    "execution.run": {Role.SUPER_ADMIN, Role.ADMIN},
    "tracking.override": {Role.SUPER_ADMIN, Role.ADMIN},
    "real_writes.authorize": {Role.SUPER_ADMIN},
    "experiments.pick_winner": {Role.SUPER_ADMIN, Role.ADMIN},
    "playbook.manage": {Role.SUPER_ADMIN, Role.ADMIN},
    "reports.view": set(Role.ALL),
    "monitor.run": {Role.SUPER_ADMIN, Role.ADMIN, Role.CAMPAIGN_MANAGER},
    "activity.view_all": {Role.SUPER_ADMIN},
    "activity.view_clients": {Role.SUPER_ADMIN, Role.ADMIN},
    "activity.view_own": {Role.SUPER_ADMIN, Role.ADMIN, Role.CAMPAIGN_MANAGER},
}
ANALYST_AUDITS = {"WEBSITE", "GBP", "COMPETITOR", "KEYWORD"}

SESSION_COOKIE = "gaa_session"
CSRF_COOKIE = "gaa_csrf"


def _hash(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def has_permission(user: User, permission: str) -> bool:
    return user.is_active and user.role in PERMISSIONS.get(permission, set())


def require(user: User, permission: str) -> None:
    if not has_permission(user, permission):
        raise Forbidden(f"Your role ({user.role}) does not allow '{permission}'")


def accessible_client_ids(db: Session, user: User) -> set[str] | None:
    """None = all clients (Super Admin / Admin). Otherwise the assigned set."""
    if user.role in (Role.SUPER_ADMIN, Role.ADMIN):
        return None
    return set(db.scalars(select(UserClientAssignment.client_id).where(UserClientAssignment.user_id == user.id)))


def require_client_access(db: Session, user: User, client_id: str | None) -> None:
    if client_id is None:
        return
    ids = accessible_client_ids(db, user)
    if ids is not None and client_id not in ids:
        raise Forbidden("You are not assigned to this client")


# ---------------------------------------------------------------- login & sessions
def login(db: Session, credential: dict, *, ip: str = "", user_agent: str = "") -> tuple[User, str, str]:
    identity = registry.get("auth").verify(credential)
    s = get_settings()
    user = db.scalar(select(User).where(User.email == identity["email"]))
    if user is None and s.BOOTSTRAP_ADMIN_EMAIL and identity["email"] == s.BOOTSTRAP_ADMIN_EMAIL.lower():
        if not db.scalar(select(User.id).where(User.is_bootstrap_admin.is_(True))):
            user = User(email=identity["email"], name=identity["name"], role=Role.SUPER_ADMIN, is_bootstrap_admin=True)
            db.add(user)
            db.flush()
            activity.log(db, "user.bootstrap_super_admin", actor=user, entity="user", entity_id=user.id)
    if user is None:
        activity.log(db, "auth.login_rejected", details={"email": identity["email"], "reason": "unknown user"})
        db.commit()
        raise Unauthorized("This account has not been invited to the application")
    if not user.is_active:
        activity.log(db, "auth.login_rejected", actor=user, details={"reason": "inactive"})
        db.commit()
        raise Unauthorized("This account is deactivated")
    if identity.get("sub") and not user.google_sub:
        user.google_sub = identity["sub"]
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    db.add(UserSession(user_id=user.id, session_hash=_hash(token), csrf_token_hash=_hash(csrf),
                       expires_at=datetime.now(timezone.utc) + timedelta(hours=s.SESSION_TTL_HOURS),
                       ip=ip[:64], user_agent=user_agent[:300]))
    user.last_login_at = datetime.now(timezone.utc)
    activity.log(db, "auth.login", actor=user, entity="user", entity_id=user.id,
                 details={"provider": registry.get("auth").name})
    db.commit()
    return user, token, csrf


def resolve_session(db: Session, token: str | None) -> tuple[User, UserSession]:
    if not token:
        raise Unauthorized("Not signed in")
    sess = db.scalar(select(UserSession).where(UserSession.session_hash == _hash(token)))
    now = datetime.now(timezone.utc)
    if not sess or sess.revoked_at or sess.expires_at.replace(tzinfo=sess.expires_at.tzinfo or timezone.utc) < now:
        raise Unauthorized("Session expired")
    user = db.get(User, sess.user_id)
    if not user or not user.is_active:
        raise Unauthorized("Account inactive")
    return user, sess


def check_csrf(sess: UserSession, header_token: str | None) -> None:
    if not header_token or not secrets.compare_digest(_hash(header_token), sess.csrf_token_hash):
        raise Forbidden("CSRF token missing or invalid")


def logout(db: Session, sess: UserSession, user: User) -> None:
    sess.revoked_at = datetime.now(timezone.utc)
    activity.log(db, "auth.logout", actor=user, entity="user", entity_id=user.id)
    db.commit()


# ---------------------------------------------------------------- users
def serialize_user(u: User, db: Session | None = None) -> dict:
    data = {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "is_active": u.is_active,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "permissions": sorted(p for p, roles in PERMISSIONS.items() if u.role in roles)}
    if db is not None:
        data["client_ids"] = list(db.scalars(select(UserClientAssignment.client_id).where(UserClientAssignment.user_id == u.id)))
    return data


def create_user(db: Session, actor: User, email: str, name: str, role: str, client_ids: list[str]) -> User:
    require(actor, "users.manage")
    if role not in Role.ALL:
        raise Forbidden("Unknown role")
    if role == Role.SUPER_ADMIN and actor.role != Role.SUPER_ADMIN:
        raise Forbidden("Only a Super Admin can create Super Admins")
    if db.scalar(select(User.id).where(User.email == email.lower())):
        raise Forbidden("A user with this email already exists")
    u = User(email=email.lower(), name=name, role=role, created_by_id=actor.id)
    db.add(u)
    db.flush()
    for cid in client_ids:
        db.add(UserClientAssignment(user_id=u.id, client_id=cid))
    activity.log(db, "user.created", actor=actor, entity="user", entity_id=u.id, details={"email": u.email, "role": role})
    return u


def update_user(db: Session, actor: User, user_id: str, *, role: str | None = None, name: str | None = None,
                is_active: bool | None = None, client_ids: list[str] | None = None) -> User:
    require(actor, "users.manage")
    u = db.get(User, user_id)
    if not u:
        raise NotFound("User not found")
    if (u.role == Role.SUPER_ADMIN or role == Role.SUPER_ADMIN) and actor.role != Role.SUPER_ADMIN:
        raise Forbidden("Only a Super Admin can change Super Admin accounts")
    if u.id == actor.id and is_active is False:
        raise Forbidden("You cannot deactivate yourself")
    before = serialize_user(u)
    if role:
        u.role = role
    if name is not None:
        u.name = name
    if is_active is not None:
        u.is_active = is_active
        if not is_active:
            for s in db.scalars(select(UserSession).where(UserSession.user_id == u.id, UserSession.revoked_at.is_(None))):
                s.revoked_at = datetime.now(timezone.utc)
    if client_ids is not None:
        for a in db.scalars(select(UserClientAssignment).where(UserClientAssignment.user_id == u.id)):
            db.delete(a)
        db.flush()
        for cid in client_ids:
            db.add(UserClientAssignment(user_id=u.id, client_id=cid))
    activity.log(db, "user.updated", actor=actor, entity="user", entity_id=u.id,
                 details={"before": {k: before[k] for k in ("role", "is_active")}, "after": {"role": u.role, "is_active": u.is_active}})
    return u
