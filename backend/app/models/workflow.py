"""Tasks, measurement audits, audits, feasibility, fixes, AI runs, plans, validation, approvals, operations."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, JSONType, TimestampMixin, utcnow


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    business_id: Mapped[str | None] = mapped_column(ForeignKey("businesses.id"), nullable=True)
    task_type: Mapped[str] = mapped_column(String(40))
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True)
    ad_group_id: Mapped[str | None] = mapped_column(ForeignKey("ad_groups.id"), nullable=True)
    ad_id: Mapped[str | None] = mapped_column(ForeignKey("ads.id"), nullable=True)
    fields: Mapped[dict] = mapped_column(JSONType, default=dict)
    human_plan: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="QUEUED")
    current_step: Mapped[str] = mapped_column(String(60), default="")
    steps: Mapped[list] = mapped_column(JSONType, default=list)
    error: Mapped[str] = mapped_column(Text, default="")
    approval_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_type: Mapped[str] = mapped_column(String(8), default="NONE")
    intake: Mapped[dict] = mapped_column(JSONType, default=dict)  # intake validation result + clarifications


class MeasurementAudit(TimestampMixin, Base):
    """One conversion-measurement audit of a business: findings with evidence and the facts specific operations depend on. No status."""
    __tablename__ = "measurement_audits"
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    google_ads_account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # the account the audit read (None: no account)
    findings: Mapped[list] = mapped_column(JSONType, default=list)
    facts: Mapped[dict] = mapped_column(JSONType, default=dict)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source_type: Mapped[str] = mapped_column(String(8), default="NONE")


class Audit(TimestampMixin, Base):
    __tablename__ = "audits"
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    audit_type: Mapped[str] = mapped_column(String(20))  # WEBSITE / GBP / COMPETITOR / KEYWORD / ACCOUNT
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict] = mapped_column(JSONType, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str] = mapped_column(String(8), default="NONE")
    created_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class FeasibilityResult(TimestampMixin, Base):
    __tablename__ = "feasibility_results"
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    campaign_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    inputs: Mapped[dict] = mapped_column(JSONType, default=dict)
    scenarios: Mapped[dict] = mapped_column(JSONType, default=dict)
    summary: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(20))
    bottlenecks: Mapped[list] = mapped_column(JSONType, default=list)
    is_estimate: Mapped[bool] = mapped_column(Boolean, default=True)
    source_type: Mapped[str] = mapped_column(String(8), default="NONE")
    # team decision on the recommendation: ACCEPT_RECOMMENDED / KEEP_CURRENT / ADJUST (pending = "")
    decision: Mapped[str] = mapped_column(String(24), default="")
    decision_note: Mapped[str] = mapped_column(Text, default="")
    decided_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_monthly_budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    accepted_weekly_target: Mapped[float | None] = mapped_column(Float, nullable=True)


class FixTrackerItem(TimestampMixin, Base):
    __tablename__ = "fix_tracker_items"
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    business_id: Mapped[str | None] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=True)
    issue: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40), default="general")
    priority: Mapped[str] = mapped_column(String(10), default="MEDIUM")
    owner: Mapped[str] = mapped_column(String(200), default="client")
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN / IN_PROGRESS / RESOLVED
    evidence: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(40), default="audit")
    dedupe_key: Mapped[str] = mapped_column(String(200), default="", index=True)
    auto_recheck_type: Mapped[str] = mapped_column(String(40), default="")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AIRun(TimestampMixin, Base):
    __tablename__ = "ai_runs"
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    purpose: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(100), default="")
    prompt_template_id: Mapped[str] = mapped_column(String(60))
    prompt_version: Mapped[str] = mapped_column(String(20))
    input_hash: Mapped[str] = mapped_column(String(64))
    raw_output: Mapped[str] = mapped_column(Text, default="")
    parsed_output: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    validation_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    # what happened, in terms that tell the causes apart (never the prompt or any secret)
    finish_reason: Mapped[str] = mapped_column(String(30), default="")        # stop | length (output cut off) | provider-specific
    provider_status: Mapped[str] = mapped_column(String(40), default="")      # ok | http_413 | http_429 | ...
    error_kind: Mapped[str] = mapped_column(String(40), default="")           # tpm_exceeded | rate_limited | context_length | output_truncated | malformed_json | schema_invalid | ...
    stage: Mapped[str] = mapped_column(String(60), default="")                # e.g. "spec" or "ad groups 5-8" for a staged draft
    prompt_estimate: Mapped[int] = mapped_column(Integer, default=0)          # tokens the app estimated before sending


class CampaignPlan(TimestampMixin, Base):
    __tablename__ = "campaign_plans"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    business_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scope: Mapped[str] = mapped_column(String(20))  # CAMPAIGN / AD_GROUP / AD / KEYWORDS / CHANGE
    version: Mapped[int] = mapped_column(Integer, default=1)
    draft: Mapped[dict] = mapped_column(JSONType, default=dict)
    challenger: Mapped[dict] = mapped_column(JSONType, default=dict)
    human_comparison: Mapped[dict] = mapped_column(JSONType, default=dict)
    playbook_rule_ids: Mapped[list] = mapped_column(JSONType, default=list)
    draft_ai_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    challenger_ai_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="DRAFTED")
    source_type: Mapped[str] = mapped_column(String(8), default="NONE")
    # dual-LLM competition record: both drafts, both cross-challenges, comparison and consolidation rationale
    competition: Mapped[dict] = mapped_column(JSONType, default=dict)
    original: Mapped[dict] = mapped_column(JSONType, default=dict)   # the AI-consolidated plan before any human edit
    parent_plan_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    change_summary: Mapped[str] = mapped_column(Text, default="")
    edited_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ValidationRun(TimestampMixin, Base):
    __tablename__ = "validation_runs"
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    approval_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    passed: Mapped[bool] = mapped_column(Boolean)
    errors: Mapped[list] = mapped_column(JSONType, default=list)
    warnings: Mapped[list] = mapped_column(JSONType, default=list)
    adapter_result: Mapped[dict] = mapped_column(JSONType, default=dict)
    ai_fix_attempts: Mapped[int] = mapped_column(Integer, default=0)


class Approval(TimestampMixin, Base):
    __tablename__ = "approvals"
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    business_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    google_ads_account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    approval_type: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(300))
    payload: Mapped[dict] = mapped_column(JSONType)
    payload_version: Mapped[int] = mapped_column(Integer, default=1)
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    current_state: Mapped[dict] = mapped_column(JSONType, default=dict)
    proposed_state: Mapped[dict] = mapped_column(JSONType, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    expected_effect: Mapped[str] = mapped_column(Text, default="")
    risks: Mapped[list] = mapped_column(JSONType, default=list)
    validation_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    validation_summary: Mapped[dict] = mapped_column(JSONType, default=dict)
    ai_explanation: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[dict] = mapped_column(JSONType, default=dict)  # measurement, feasibility, fixes, challenger...
    requested_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING_APPROVAL", index=True)
    decided_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str] = mapped_column(Text, default="")
    approved_payload_hash: Mapped[str] = mapped_column(String(64), default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_to_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    supersedes_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    superseded_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parent_operation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    integration_mode: Mapped[str] = mapped_column(String(8), default="REAL")
    source_type: Mapped[str] = mapped_column(String(8), default="NONE")


class ApprovalEvent(IdMixin, Base):
    __tablename__ = "approval_events"
    approval_id: Mapped[str] = mapped_column(ForeignKey("approvals.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    event: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_email: Mapped[str] = mapped_column(String(320), default="system")
    payload_hash: Mapped[str] = mapped_column(String(64), default="")
    payload_version: Mapped[int] = mapped_column(Integer, default=1)
    reason: Mapped[str] = mapped_column(Text, default="")
    before: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONType, nullable=True)


class Operation(TimestampMixin, Base):
    __tablename__ = "operations"
    client_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    google_ads_account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    approval_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    policy: Mapped[str] = mapped_column(String(30))  # APPROVAL / AUTO_NEGATIVE / EMERGENCY
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    tracking_label: Mapped[str] = mapped_column(String(40), index=True)
    adapter_name: Mapped[str] = mapped_column(String(60))
    integration_mode: Mapped[str] = mapped_column(String(8))
    operation_type: Mapped[str] = mapped_column(String(30))
    request_payload: Mapped[dict] = mapped_column(JSONType)
    payload_hash: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(24), default="PENDING")
    external_response: Mapped[dict] = mapped_column(JSONType, default=dict)
    external_resource_names: Mapped[list] = mapped_column(JSONType, default=list)
    verification_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    verification_diff: Mapped[list] = mapped_column(JSONType, default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    requested_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    approved_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


