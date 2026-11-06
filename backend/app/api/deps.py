from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User
from app.services import auth_service

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user, sess = auth_service.resolve_session(db, request.cookies.get(auth_service.SESSION_COOKIE))
    if request.method in UNSAFE:
        auth_service.check_csrf(sess, request.headers.get("X-CSRF-Token"))
    request.state.user = user
    request.state.session = sess
    return user


def require(permission: str):
    def dep(user: User = Depends(current_user)) -> User:
        auth_service.require(user, permission)
        return user
    return dep
