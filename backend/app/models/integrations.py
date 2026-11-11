"""Integration credential store (encrypted) and OAuth state.

Secrets are stored ONLY as Fernet ciphertext (`secrets_ciphertext`); the API returns fingerprints, never values.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONType, TimestampMixin, utcnow


class IntegrationConfig(TimestampMixin, Base):
    __tablename__ = "integration_configs"
    __table_args__ = (UniqueConstraint("provider", "workspace_id", name="uq_integration_provider_workspace"),)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), nullable=True, index=True)  # whose credentials these are
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    settings: Mapped[dict] = mapped_column(JSONType, default=dict)            # non-secret values (ids, model names)
    secrets_ciphertext: Mapped[str] = mapped_column(Text, default="")         # Fernet(JSON {name: value})
    secret_fingerprints: Mapped[dict] = mapped_column(JSONType, default=dict)  # {name: "ab12cd34"}
    status: Mapped[str] = mapped_column(String(40), default="NOT_CONNECTED")
    connected_account: Mapped[str] = mapped_column(String(320), default="")
    account_id: Mapped[str] = mapped_column(String(100), default="")
    scopes: Mapped[list] = mapped_column(JSONType, default=list)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_steps: Mapped[list] = mapped_column(JSONType, default=list)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    discovered: Mapped[dict] = mapped_column(JSONType, default=dict)          # e.g. accessible customers, GA4 properties
    updated_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class OAuthState(TimestampMixin, Base):
    __tablename__ = "oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(40))
    verifier_ciphertext: Mapped[str] = mapped_column(Text)
    user_id: Mapped[str] = mapped_column(String(32))
    redirect_uri: Mapped[str] = mapped_column(String(500), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditReport(TimestampMixin, Base):
    """One run of the five-part audit for a business. Immutable history: a new run creates a new report."""
    __tablename__ = "audit_reports"
    client_id: Mapped[str] = mapped_column(String(32), index=True)
    business_id: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)                     # per business, 1,2,3…
    previous_report_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="RUNNING")     # RUNNING / COMPLETED / FAILED
    steps: Mapped[list] = mapped_column(JSONType, default=list)            # live progress
    sections: Mapped[dict] = mapped_column(JSONType, default=dict)         # {client|website|gbp|competitors|keywords: {findings, score,…}}
    scores: Mapped[dict] = mapped_column(JSONType, default=dict)
    recommendations: Mapped[dict] = mapped_column(JSONType, default=dict)
    summary: Mapped[dict] = mapped_column(JSONType, default=dict)
    changes: Mapped[dict] = mapped_column(JSONType, default=dict)          # vs previous report
    input_snapshot: Mapped[dict] = mapped_column(JSONType, default=dict)   # the client info this audit was based on
    input_hash: Mapped[str] = mapped_column(String(64), default="")
    source_type: Mapped[str] = mapped_column(String(8), default="NONE")
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class IntelligenceRun(TimestampMixin, Base):
    """One evaluation of one of the ten intelligence modules for a business. Append-only history: a re-run creates a new version and
    the previous one stops being current. Findings are stored as structured records (never one AI text blob); each carries its own
    provenance (source, source type, retrieval time, confidence, verification status, origin)."""
    __tablename__ = "intelligence_runs"
    __table_args__ = (Index("ix_intelligence_runs_current", "business_id", "module", "is_current"),)
    client_id: Mapped[str] = mapped_column(String(32), index=True)
    business_id: Mapped[str] = mapped_column(String(32), index=True)
    module: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer, default=1)                 # per business and module
    status: Mapped[str] = mapped_column(String(32), default="NOT_STARTED")   # lifecycle states of the specification
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    input_hash: Mapped[str] = mapped_column(String(64), default="")          # hash of the inputs this run used: a change makes it stale
    findings: Mapped[list] = mapped_column(JSONType, default=list)
    coverage: Mapped[dict] = mapped_column(JSONType, default=dict)           # spec check -> evaluated | not_assessed
    missing_information: Mapped[list] = mapped_column(JSONType, default=list)
    contradictions: Mapped[list] = mapped_column(JSONType, default=list)
    risks: Mapped[list] = mapped_column(JSONType, default=list)
    recommendations: Mapped[list] = mapped_column(JSONType, default=list)
    client_actions: Mapped[list] = mapped_column(JSONType, default=list)
    research_performed: Mapped[list] = mapped_column(JSONType, default=list)
    data_sources: Mapped[list] = mapped_column(JSONType, default=list)
    decision: Mapped[str] = mapped_column(String(40), default="")            # module-level decision (strategy: the readiness decision)
    summary: Mapped[str] = mapped_column(Text, default="")
    source_report_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
