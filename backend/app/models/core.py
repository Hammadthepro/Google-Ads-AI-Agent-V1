"""Identity, clients, businesses, Google Ads mirror, settings and audit logs."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, JSONType, TimestampMixin, utcnow


# ---------------------------------------------------------------- identity
class User(TimestampMixin, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    role: Mapped[str] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_bootstrap_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class UserSession(TimestampMixin, Base):
    __tablename__ = "sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")


class UserClientAssignment(IdMixin, Base):
    __tablename__ = "user_client_assignments"
    __table_args__ = (UniqueConstraint("user_id", "client_id"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))


# ---------------------------------------------------------------- clients
class Client(TimestampMixin, Base):
    __tablename__ = "clients"
    name: Mapped[str] = mapped_column(String(200))
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    backup_owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    owner_contact: Mapped[str] = mapped_column(String(300), default="")
    backup_contact: Mapped[str] = mapped_column(String(300), default="")
    tag_access_contact: Mapped[str] = mapped_column(String(300), default="")
    automation_frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_reason: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # client-level weekly lead target (all campaigns combined) and agreed monthly ad budget
    weekly_conversion_target: Mapped[float] = mapped_column(Float, default=0)
    monthly_budget_usd: Mapped[float] = mapped_column(Float, default=0)
    businesses: Mapped[list["Business"]] = relationship(back_populates="client", cascade="all, delete-orphan")


class Business(TimestampMixin, Base):
    __tablename__ = "businesses"
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    business_type: Mapped[str] = mapped_column(String(80), default="home_services")
    website_url: Mapped[str] = mapped_column(String(500), default="")
    gbp_url: Mapped[str] = mapped_column(String(500), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    address: Mapped[str] = mapped_column(String(400), default="")
    lead_form_url: Mapped[str] = mapped_column(String(500), default="")
    thank_you_url: Mapped[str] = mapped_column(String(500), default="")
    default_radius_miles: Mapped[float] = mapped_column(Float, default=20)
    zip_codes: Mapped[list] = mapped_column(JSONType, default=list)
    business_hours: Mapped[dict] = mapped_column(JSONType, default=dict)
    verified_claims: Mapped[list] = mapped_column(JSONType, default=list)
    brand_rules: Mapped[list] = mapped_column(JSONType, default=list)
    competitor_ads_text: Mapped[str] = mapped_column(Text, default="")
    google_ads_account_id: Mapped[str | None] = mapped_column(ForeignKey("google_ads_accounts.id"), nullable=True)
    client: Mapped[Client] = relationship(back_populates="businesses")
    services: Mapped[list["BusinessService"]] = relationship(cascade="all, delete-orphan")
    competitors: Mapped[list["Competitor"]] = relationship(cascade="all, delete-orphan")


class BusinessService(IdMixin, Base):
    __tablename__ = "business_services"
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    landing_page_url: Mapped[str] = mapped_column(String(500), default="")
    is_excluded: Mapped[bool] = mapped_column(Boolean, default=False)


class Competitor(IdMixin, Base):
    __tablename__ = "competitors"
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    website_url: Mapped[str] = mapped_column(String(500), default="")
    place_id: Mapped[str] = mapped_column(String(200), default="")


# ---------------------------------------------------------------- google ads mirror
class GoogleAdsAccount(TimestampMixin, Base):
    __tablename__ = "google_ads_accounts"
    client_id: Mapped[str | None] = mapped_column(ForeignKey("clients.id"), nullable=True, index=True)
    customer_id: Mapped[str] = mapped_column(String(20), unique=True)
    login_customer_id: Mapped[str] = mapped_column(String(20), default="")
    descriptive_name: Mapped[str] = mapped_column(String(200), default="")
    currency_code: Mapped[str] = mapped_column(String(3), default="USD")
    time_zone: Mapped[str] = mapped_column(String(64), default="America/New_York")
    is_manager: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_customer_id: Mapped[str] = mapped_column(String(20), default="")
    permission_status: Mapped[str] = mapped_column(String(32), default="OK")
    writes_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_tagging_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    integration_mode: Mapped[str] = mapped_column(String(8), default="MOCK")
    access_level: Mapped[str] = mapped_column(String(16), default="READ_ONLY")  # READ_ONLY / STANDARD (app-side)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IntegrationConnection(TimestampMixin, Base):
    __tablename__ = "integration_connections"
    provider: Mapped[str] = mapped_column(String(40), index=True)
    account_ref: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(32), default="DISCONNECTED")
    integration_mode: Mapped[str] = mapped_column(String(8), default="MOCK")
    scopes: Mapped[list] = mapped_column(JSONType, default=list)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, default="")  # ciphertext only
    token_fingerprint: Mapped[str] = mapped_column(String(32), default="")
    connected_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    oauth_state: Mapped[str] = mapped_column(String(100), default="")


class Campaign(TimestampMixin, Base):
    __tablename__ = "campaigns"
    __table_args__ = (UniqueConstraint("google_ads_account_id", "external_id"),)
    google_ads_account_id: Mapped[str] = mapped_column(ForeignKey("google_ads_accounts.id"), index=True)
    business_id: Mapped[str | None] = mapped_column(ForeignKey("businesses.id"), nullable=True, index=True)
    client_id: Mapped[str | None] = mapped_column(ForeignKey("clients.id"), nullable=True, index=True)
    external_id: Mapped[str] = mapped_column(String(40))
    external_resource_name: Mapped[str] = mapped_column(String(200), default="")
    name: Mapped[str] = mapped_column(String(255))
    goal: Mapped[str] = mapped_column(String(32), default="LEADS")
    campaign_type: Mapped[str] = mapped_column(String(32), default="SEARCH")
    status: Mapped[str] = mapped_column(String(16), default="PAUSED")
    is_read_only: Mapped[bool] = mapped_column(Boolean, default=False)
    daily_budget_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    monthly_budget_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    weekly_conversion_target: Mapped[float] = mapped_column(Float, default=0)
    bidding_strategy: Mapped[str] = mapped_column(String(40), default="MAXIMIZE_CONVERSIONS")
    bidding_system_status: Mapped[str] = mapped_column(String(40), default="")
    landing_page_url: Mapped[str] = mapped_column(String(500), default="")
    services_covered: Mapped[list] = mapped_column(JSONType, default=list)
    locations: Mapped[list] = mapped_column(JSONType, default=list)
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_app: Mapped[bool] = mapped_column(Boolean, default=False)
    integration_mode: Mapped[str] = mapped_column(String(8), default="MOCK")
    source_type: Mapped[str] = mapped_column(String(8), default="MOCK")


class AdGroup(TimestampMixin, Base):
    __tablename__ = "ad_groups"
    __table_args__ = (UniqueConstraint("campaign_id", "external_id"),)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(40))
    external_resource_name: Mapped[str] = mapped_column(String(200), default="")
    name: Mapped[str] = mapped_column(String(255))
    service: Mapped[str] = mapped_column(String(200), default="")
    landing_page_url: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(16), default="PAUSED")
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Ad(TimestampMixin, Base):
    __tablename__ = "ads"
    __table_args__ = (UniqueConstraint("ad_group_id", "external_id"),)
    ad_group_id: Mapped[str] = mapped_column(ForeignKey("ad_groups.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(40))
    external_resource_name: Mapped[str] = mapped_column(String(200), default="")
    ad_type: Mapped[str] = mapped_column(String(40), default="RESPONSIVE_SEARCH_AD")
    headlines: Mapped[list] = mapped_column(JSONType, default=list)
    descriptions: Mapped[list] = mapped_column(JSONType, default=list)
    path1: Mapped[str] = mapped_column(String(15), default="")
    path2: Mapped[str] = mapped_column(String(15), default="")
    final_url: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(16), default="PAUSED")
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Keyword(TimestampMixin, Base):
    __tablename__ = "keywords"
    __table_args__ = (UniqueConstraint("ad_group_id", "external_id"),)
    ad_group_id: Mapped[str] = mapped_column(ForeignKey("ad_groups.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(40))
    external_resource_name: Mapped[str] = mapped_column(String(200), default="", server_default="")
    text: Mapped[str] = mapped_column(String(200))
    match_type: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(16), default="PAUSED")
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NegativeKeyword(TimestampMixin, Base):
    __tablename__ = "negative_keywords"
    google_ads_account_id: Mapped[str] = mapped_column(ForeignKey("google_ads_accounts.id"), index=True)
    level: Mapped[str] = mapped_column(String(16))  # CAMPAIGN / AD_GROUP / SHARED_LIST
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=True)
    ad_group_id: Mapped[str | None] = mapped_column(ForeignKey("ad_groups.id", ondelete="CASCADE"), nullable=True)
    shared_list_name: Mapped[str] = mapped_column(String(200), default="")
    external_id: Mapped[str] = mapped_column(String(40), default="")
    text: Mapped[str] = mapped_column(String(200))
    match_type: Mapped[str] = mapped_column(String(10))
    origin: Mapped[str] = mapped_column(String(16), default="SYNCED")  # SYNCED / AUTO / APPROVED
    applied_operation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ConversionAction(TimestampMixin, Base):
    __tablename__ = "conversion_actions"
    __table_args__ = (UniqueConstraint("google_ads_account_id", "external_id"),)
    google_ads_account_id: Mapped[str] = mapped_column(ForeignKey("google_ads_accounts.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(40))
    origin: Mapped[str] = mapped_column(String(40), default="WEBSITE")
    category: Mapped[str] = mapped_column(String(40), default="SUBMIT_LEAD_FORM")
    primary_for_goal: Mapped[bool] = mapped_column(Boolean, default=True)
    counting_type: Mapped[str] = mapped_column(String(32), default="ONE_PER_CLICK")
    call_min_duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ENABLED")
    business_id: Mapped[str | None] = mapped_column(ForeignKey("businesses.id"), nullable=True)


# ---------------------------------------------------------------- settings & logs
class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONType)
    updated_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class KillSwitch(TimestampMixin, Base):
    __tablename__ = "kill_switches"
    __table_args__ = (UniqueConstraint("scope", "scope_id"),)
    scope: Mapped[str] = mapped_column(String(16))  # GLOBAL / CLIENT / ACCOUNT
    scope_id: Mapped[str] = mapped_column(String(32), default="")
    is_on: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    set_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ActivityLog(IdMixin, Base):
    __tablename__ = "activity_logs"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    actor_email: Mapped[str] = mapped_column(String(320), default="system")
    client_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity: Mapped[str] = mapped_column(String(60), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
    integration_mode: Mapped[str] = mapped_column(String(8), default="")
    request_id: Mapped[str] = mapped_column(String(40), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")

