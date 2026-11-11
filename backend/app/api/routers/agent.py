"""Conversational agent API. All state lives in the conversation tables and the intelligence record."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, require
from app.db.session import get_db
from app.models import AgentConversation, User
from app.services import agent_service as agent

router = APIRouter(prefix="/agent", tags=["agent"])


class OpenIn(BaseModel):
    client_id: str | None = None
    new: bool = False


class MessageIn(BaseModel):
    text: str = ""
    choice: dict | None = None   # an option chosen from a clarification: {"value": ...}


class ConfirmIn(BaseModel):
    approve: bool


@router.post("/conversations")
def open_conversation(body: OpenIn, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    c = agent.open_conversation(db, user, body.client_id, new=body.new)
    db.commit()
    return agent.thread(db, user, c.id)


@router.get("/conversations")
def list_conversations(client_id: str | None = None, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    q = select(AgentConversation).where(AgentConversation.user_id == user.id, AgentConversation.archived.is_(False)).order_by(AgentConversation.updated_at.desc()).limit(50)
    if client_id:
        q = q.where(AgentConversation.client_id == client_id)
    return [{"id": c.id, "client_id": c.client_id, "title": c.title, "updated_at": c.updated_at.isoformat()} for c in db.scalars(q)]


@router.get("/conversations/{conv_id}")
def get_conversation(conv_id: str, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    out = agent.thread(db, user, conv_id)
    db.commit()  # persists runs marked INTERRUPTED by the sweep
    return out


@router.post("/conversations/{conv_id}/messages")
def post_message(conv_id: str, body: MessageIn, sync: bool = False, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    _, run = agent.post_message(db, user, conv_id, body.text, choice=body.choice)
    db.commit()
    if run is not None:
        agent.dispatch(run.id, sync=sync)
        db.expire_all()
    return agent.thread(db, user, conv_id)


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: str, sync: bool = False, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    run = agent.resume(db, user, run_id)
    conv_id = run.conversation_id
    db.commit()
    agent.dispatch(run.id, sync=sync)
    db.expire_all()
    return agent.thread(db, user, conv_id)


@router.post("/runs/{run_id}/confirm")
def confirm_run(run_id: str, body: ConfirmIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    out = agent.confirm(db, user, run_id, body.approve)
    db.commit()
    return out
