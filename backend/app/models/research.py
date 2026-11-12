"""Live research telemetry and provider bookkeeping.

* ResearchEvent  — what the research agent actually did, in order (one row per real backend event). The live audit view is rebuilt from
                   these rows, so a page refresh or a second viewer sees exactly the same history.
* ProviderCache  — responses of free/metered research providers (suggestions, web search, geocoding, POI lookups), keyed by request, so
                   the same query is never sent twice within its time-to-live (public infrastructure and credit budgets are respected).
* ProviderCall   — one row per provider request (cached or not): the usage ledger behind the Integrations page and per-audit budgets.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, utcnow


class ResearchEvent(IdMixin, Base):
    __tablename__ = "research_events"
    audit_report_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)     # one research run (= report id for report runs)
    audit_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)   # the individual audit the event came from
    client_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    business_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    seq: Mapped[int] = mapped_column(Integer)  # order within the run
    kind: Mapped[str] = mapped_column(String(40))          # audit_started, stage_started, url_crawling, page_extracted, ...
    stage: Mapped[str] = mapped_column(String(32), default="")
    level: Mapped[str] = mapped_column(String(8), default="info")   # info | warn | error
    title: Mapped[str] = mapped_column(String(300))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProviderCache(IdMixin, Base):
    __tablename__ = "provider_cache"
    __table_args__ = (UniqueConstraint("provider", "cache_key", name="uq_provider_cache_key"),)
    provider: Mapped[str] = mapped_column(String(40))
    cache_key: Mapped[str] = mapped_column(String(64))
    request: Mapped[dict] = mapped_column(JSONB, default=dict)
    response: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProviderCall(IdMixin, Base):
    __tablename__ = "provider_calls"
    provider: Mapped[str] = mapped_column(String(40), index=True)
    operation: Mapped[str] = mapped_column(String(40))
    ok: Mapped[bool] = mapped_column(Boolean)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    error_kind: Mapped[str] = mapped_column(String(40), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    cost_units: Mapped[float] = mapped_column(Float, default=0)   # provider credits when the provider documents them, else 0
    audit_report_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
