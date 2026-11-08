"""Integration credential store (encrypted) and OAuth state.

Secrets are stored ONLY as Fernet ciphertext (`secrets_ciphertext`); the API returns fingerprints, never values.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONType, TimestampMixin, utcnow


class IntegrationConfig(TimestampMixin, Base):
    __tablename__ = "integration_configs"
    provider: Mapped[str] = mapped_column(String(40), unique=True, index=True)
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
    source_type: Mapped[str] = mapped_column(String(8), default="MOCK")
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
