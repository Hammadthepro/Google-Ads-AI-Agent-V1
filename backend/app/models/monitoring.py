"""Monitoring, emergency brake, recommendations, reports, experiments, learning loop."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, JSONType, TimestampMixin, utcnow


class PerformanceSnapshot(IdMixin, Base):
    __tablename__ = "performance_snapshots"
    __table_args__ = (UniqueConstraint("level", "entity_external_id", "date", "source_type"),)
    google_ads_account_id: Mapped[str] = mapped_column(String(32), index=True)
    client_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    campaign_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    level: Mapped[str] = mapped_column(String(12))  # CAMPAIGN / AD_GROUP / KEYWORD / AD
    entity_external_id: Mapped[str] = mapped_column(String(80))
    date: Mapped[date] = mapped_column(Date, index=True)
    impressions: Mapped[int] = mapped_column(BigInteger, default=0)
    clicks: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    conversions: Mapped[float] = mapped_column(Float, default=0)
    form_conversions: Mapped[float] = mapped_column(Float, default=0)
    call_conversions: Mapped[float] = mapped_column(Float, default=0)
    search_impression_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_lost_is: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank_lost_is: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source_type: Mapped[str] = mapped_column(String(8), default="NONE")


class SearchTermRow(IdMixin, Base):
    __tablename__ = "search_terms"
    __table_args__ = (UniqueConstraint("google_ads_account_id", "campaign_external_id", "ad_group_external_id", "term", "window_days"),)
    google_ads_account_id: Mapped[str] = mapped_column(String(32), index=True)
    client_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    campaign_external_id: Mapped[str] = mapped_column(String(40))
    ad_group_external_id: Mapped[str] = mapped_column(String(40))
    term: Mapped[str] = mapped_column(String(300))
    matched_keyword: Mapped[str] = mapped_column(String(200), default="")
    window_days: Mapped[int] = mapped_column(Integer, default=30)
    impressions: Mapped[int] = mapped_column(BigInteger, default=0)
    clicks: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    conversions: Mapped[float] = mapped_column(Float, default=0)
    conversions_90d: Mapped[float] = mapped_column(Float, default=0)
    last_conversion_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source_type: Mapped[str] = mapped_column(String(8), default="NONE")


class CallRecord(IdMixin, Base):
    __tablename__ = "call_records"
    google_ads_account_id: Mapped[str] = mapped_column(String(32), index=True)
    campaign_external_id: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(60), unique=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # ANSWERED / MISSED
    source_type: Mapped[str] = mapped_column(String(8), default="NONE")


class ChangeEvent(IdMixin, Base):
    __tablename__ = "change_events"
    google_ads_account_id: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(60), unique=True)
    resource_name: Mapped[str] = mapped_column(String(200))
    change_type: Mapped[str] = mapped_column(String(40))
    user_email: Mapped[str] = mapped_column(String(320))
    client_type: Mapped[str] = mapped_column(String(40), default="")
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    made_outside_app: Mapped[bool] = mapped_column(Boolean, default=False)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
    source_type: Mapped[str] = mapped_column(String(8), default="NONE")


class EmergencyEvent(TimestampMixin, Base):
    __tablename__ = "emergency_events"
    client_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    google_ads_account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    campaign_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rule: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))
    observed: Mapped[dict] = mapped_column(JSONType, default=dict)
    threshold: Mapped[dict] = mapped_column(JSONType, default=dict)
    action: Mapped[str] = mapped_column(String(40))
    action_mode: Mapped[str] = mapped_column(String(16))  # SIMULATED / INTERNAL / REAL
    message: Mapped[str] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_type: Mapped[str] = mapped_column(String(8), default="NONE")


class Alert(TimestampMixin, Base):
    __tablename__ = "alerts"
    client_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    channel: Mapped[str] = mapped_column(String(20), default="email")
    recipients: Mapped[list] = mapped_column(JSONType, default=list)
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    delivery_status: Mapped[str] = mapped_column(String(20), default="MOCK_DELIVERED")
    category: Mapped[str] = mapped_column(String(40), default="general")


class JobRun(TimestampMixin, Base):
    __tablename__ = "job_runs"
    job: Mapped[str] = mapped_column(String(60), index=True)
    client_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16))  # SUCCEEDED / FAILED
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class Recommendation(TimestampMixin, Base):
    __tablename__ = "recommendations"
    client_id: Mapped[str] = mapped_column(String(32), index=True)
    google_ads_account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    campaign_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    category: Mapped[str] = mapped_column(String(40))  # NEGATIVE_KEYWORD / BUDGET / PROMOTE_TERM / PAUSE_KEYWORD / REPLACE_AD / ...
    size: Mapped[str] = mapped_column(String(10))  # SMALL / BIG
    source: Mapped[str] = mapped_column(String(10), default="RULES")  # RULES / AI / BOTH
    title: Mapped[str] = mapped_column(String(300))
    proposal: Mapped[dict] = mapped_column(JSONType, default=dict)
    evidence: Mapped[dict] = mapped_column(JSONType, default=dict)
    enough_data: Mapped[dict] = mapped_column(JSONType, default=dict)
    decision: Mapped[str] = mapped_column(String(12), default="PENDING")  # GO / HOLD / PENDING
    hold_reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN / PROPOSED / APPLIED / REJECTED / HELD / SHADOW
    approval_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    operation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), default="", index=True)
    source_type: Mapped[str] = mapped_column(String(8), default="NONE")


class WeeklyReport(TimestampMixin, Base):
    __tablename__ = "weekly_reports"
    client_id: Mapped[str] = mapped_column(String(32), index=True)
    week_start: Mapped[date] = mapped_column(Date)
    week_end: Mapped[date] = mapped_column(Date)
    metrics: Mapped[dict] = mapped_column(JSONType, default=dict)
    diagnosis: Mapped[dict] = mapped_column(JSONType, default=dict)
    sections: Mapped[dict] = mapped_column(JSONType, default=dict)
    ai_summary: Mapped[str] = mapped_column(Text, default="")
    ai_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_type: Mapped[str] = mapped_column(String(8), default="NONE")


class Experiment(TimestampMixin, Base):
    __tablename__ = "experiments"
    client_id: Mapped[str] = mapped_column(String(32), index=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"))
    name: Mapped[str] = mapped_column(String(200))
    variable: Mapped[str] = mapped_column(String(30))  # BIDDING / AD_ANGLE / MATCH_TYPE / LANDING_PAGE
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    treatment: Mapped[dict] = mapped_column(JSONType, default=dict)
    split_percent: Mapped[int] = mapped_column(Integer, default=50)
    status: Mapped[str] = mapped_column(String(24), default="PROPOSED")
    external_id: Mapped[str] = mapped_column(String(40), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    results: Mapped[dict] = mapped_column(JSONType, default=dict)
    start_approval_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    winner_approval_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    winner_arm: Mapped[str] = mapped_column(String(20), default="")
    winner_selected_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_type: Mapped[str] = mapped_column(String(8), default="NONE")


class PlaybookRule(TimestampMixin, Base):
    __tablename__ = "playbook_rules"
    text: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(20), default="GLOBAL")  # GLOBAL / BUSINESS_TYPE / CLIENT
    scope_value: Mapped[str] = mapped_column(String(100), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    author_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_note: Mapped[str] = mapped_column(Text, default="")


class Prediction(TimestampMixin, Base):
    __tablename__ = "predictions"
    client_id: Mapped[str] = mapped_column(String(32), index=True)
    subject_type: Mapped[str] = mapped_column(String(30))  # FEASIBILITY / CHANGE
    subject_id: Mapped[str] = mapped_column(String(32))
    campaign_external_id: Mapped[str] = mapped_column(String(40), default="")
    predicted: Mapped[dict] = mapped_column(JSONType, default=dict)
    actual: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    evaluate_after: Mapped[date | None] = mapped_column(Date, nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RegressionCase(TimestampMixin, Base):
    __tablename__ = "regression_cases"
    name: Mapped[str] = mapped_column(String(200))
    inputs: Mapped[dict] = mapped_column(JSONType, default=dict)
    expectations: Mapped[dict] = mapped_column(JSONType, default=dict)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_result: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


# ---------------------------------------------------------------- simulated external world (mock only)
class MockCustomer(IdMixin, Base):
    __tablename__ = "mock_gads_customers"
    customer_id: Mapped[str] = mapped_column(String(20), unique=True)
    descriptive_name: Mapped[str] = mapped_column(String(200))
    manager_customer_id: Mapped[str] = mapped_column(String(20), default="")
    is_manager: Mapped[bool] = mapped_column(Boolean, default=False)
    currency_code: Mapped[str] = mapped_column(String(3), default="USD")
    time_zone: Mapped[str] = mapped_column(String(64), default="America/New_York")
    auto_tagging_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    accessible: Mapped[bool] = mapped_column(Boolean, default=True)


class MockResource(TimestampMixin, Base):
    __tablename__ = "mock_gads_resources"
    __table_args__ = (UniqueConstraint("customer_id", "resource_type", "resource_id"),)
    customer_id: Mapped[str] = mapped_column(String(20), index=True)
    resource_type: Mapped[str] = mapped_column(String(40), index=True)
    resource_id: Mapped[int] = mapped_column(BigInteger)
    resource_name: Mapped[str] = mapped_column(String(200), unique=True)
    parent_resource_name: Mapped[str] = mapped_column(String(200), default="", index=True)
    status: Mapped[str] = mapped_column(String(16), default="ENABLED")
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    labels: Mapped[list] = mapped_column(JSONType, default=list)


class MockMetricDaily(IdMixin, Base):
    __tablename__ = "mock_gads_metrics_daily"
    __table_args__ = (UniqueConstraint("customer_id", "level", "entity_id", "date"),)
    customer_id: Mapped[str] = mapped_column(String(20), index=True)
    campaign_id: Mapped[int] = mapped_column(BigInteger, index=True)
    level: Mapped[str] = mapped_column(String(12))
    entity_id: Mapped[int] = mapped_column(BigInteger)
    date: Mapped[date] = mapped_column(Date, index=True)
    impressions: Mapped[int] = mapped_column(BigInteger, default=0)
    clicks: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    final_conversions: Mapped[float] = mapped_column(Float, default=0)
    form_share: Mapped[float] = mapped_column(Float, default=0.6)
    search_impression_share: Mapped[float] = mapped_column(Float, default=0.5)
    budget_lost_is: Mapped[float] = mapped_column(Float, default=0.0)
    rank_lost_is: Mapped[float] = mapped_column(Float, default=0.0)


class MockSearchTerm(IdMixin, Base):
    __tablename__ = "mock_gads_search_terms"
    __table_args__ = (UniqueConstraint("customer_id", "ad_group_id", "term", "date"),)
    customer_id: Mapped[str] = mapped_column(String(20), index=True)
    campaign_id: Mapped[int] = mapped_column(BigInteger)
    ad_group_id: Mapped[int] = mapped_column(BigInteger)
    term: Mapped[str] = mapped_column(String(300))
    matched_keyword: Mapped[str] = mapped_column(String(200), default="")
    date: Mapped[date] = mapped_column(Date, index=True)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    cost_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    conversions: Mapped[float] = mapped_column(Float, default=0)


class MockCallRecord(IdMixin, Base):
    __tablename__ = "mock_gads_call_records"
    customer_id: Mapped[str] = mapped_column(String(20), index=True)
    campaign_id: Mapped[int] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))


class MockChangeEvent(IdMixin, Base):
    __tablename__ = "mock_gads_change_events"
    customer_id: Mapped[str] = mapped_column(String(20), index=True)
    resource_name: Mapped[str] = mapped_column(String(200))
    change_type: Mapped[str] = mapped_column(String(40))
    user_email: Mapped[str] = mapped_column(String(320))
    client_type: Mapped[str] = mapped_column(String(40))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict] = mapped_column(JSONType, default=dict)


class MockFailureInjection(TimestampMixin, Base):
    __tablename__ = "mock_failure_injections"
    adapter: Mapped[str] = mapped_column(String(40))
    method: Mapped[str] = mapped_column(String(60), default="*")
    failure: Mapped[str] = mapped_column(String(40))
    entity: Mapped[str] = mapped_column(String(200), default="")
    times: Mapped[int] = mapped_column(Integer, default=1)  # -1 = always
    used: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


