"""Google Ads manager (MCC) connection, client-account discovery and per-user account selection. Read-only toward Google Ads."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_user, require
from app.db.session import get_db
from app.models import User
from app.services import mcc_service

router = APIRouter(prefix="/google-ads", tags=["google ads accounts"])


class CustomerIn(BaseModel):
    customer_id: str


@router.get("/status")
def status(user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    out = {**mcc_service.workspace_summary(db), **mcc_service.get_selection(db, user)}
    db.commit()
    return out


@router.get("/managers")
def managers(user: User = Depends(require("integrations.manage")), db: Session = Depends(get_db)):
    out = mcc_service.manager_candidates(db)
    db.commit()
    return out


@router.put("/manager")
def choose_manager(body: CustomerIn, user: User = Depends(require("integrations.manage")), db: Session = Depends(get_db)):
    out = mcc_service.select_manager(db, user, body.customer_id)
    db.commit()
    return out


@router.post("/accounts/refresh")
def refresh(user: User = Depends(require("clients.edit")), db: Session = Depends(get_db)):
    out = mcc_service.discover(db, user)
    db.commit()
    return out


@router.get("/accounts")
def accounts(q: str = "", status: str = "", include_managers: bool = False, only_selectable: bool = False, limit: int = 200, offset: int = 0,
             user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    out = mcc_service.list_accounts(db, user, q=q, status=status, include_managers=include_managers, only_selectable=only_selectable,
                                    limit=limit, offset=offset)
    db.commit()
    return out


@router.get("/selection")
def selection(user: User = Depends(current_user), db: Session = Depends(get_db)):
    out = mcc_service.get_selection(db, user)
    db.commit()
    return out


@router.put("/selection")
def select(body: CustomerIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    out = mcc_service.select_account(db, user, body.customer_id)
    db.commit()
    return out
