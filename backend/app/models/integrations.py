"""Integration credential store (encrypted) and OAuth state.

Secrets are stored ONLY as Fernet ciphertext (`secrets_ciphertext`); the API returns fingerprints, never values.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONType, TimestampMixin


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
