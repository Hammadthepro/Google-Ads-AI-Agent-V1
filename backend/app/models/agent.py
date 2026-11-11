"""Conversational agent persistence. The agent has NO knowledge store of its own: what it knows about a client lives in the intelligence
record (IntelligenceRun) and the existing audit/feasibility/monitoring tables. These tables hold only the conversation and resumable runs."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, JSONType, TimestampMixin, utcnow


class AgentConversation(TimestampMixin, Base):
    """One persistent thread per client (and user). ``context`` carries what the agent remembers between messages
    (selected business/campaign, a pending clarification or confirmation)."""
    __tablename__ = "agent_conversations"
    client_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    context: Mapped[dict] = mapped_column(JSONType, default=dict)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class AgentMessage(IdMixin, Base):
    __tablename__ = "agent_messages"
    __table_args__ = (Index("ix_agent_messages_conv_created", "conversation_id", "created_at"),)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("agent_conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))            # user | agent | system
    kind: Mapped[str] = mapped_column(String(24), default="text")  # text | plan | result | clarification | confirmation | error
    content: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentRun(TimestampMixin, Base):
    """One interpreted request and how it was carried out. ``plan`` lists every stage with its decision (run / reuse / skip) and a reason;
    each stage's status is saved as it finishes, so an interrupted run resumes from the first unfinished stage."""
    __tablename__ = "agent_runs"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("agent_conversations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(String(32))
    request_text: Mapped[str] = mapped_column(Text, default="")
    intent: Mapped[str] = mapped_column(String(40), default="")
    interpreted_by: Mapped[str] = mapped_column(String(12), default="rules")   # rules | ai | selection
    params: Mapped[dict] = mapped_column(JSONType, default=dict)
    plan: Mapped[list] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(28), default="PLANNED")
    # PLANNED RUNNING COMPLETED COMPLETED_WITH_BLOCKERS FAILED INTERRUPTED WAITING_FOR_CLARIFICATION WAITING_FOR_CONFIRMATION CANCELLED
    result: Mapped[dict] = mapped_column(JSONType, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
