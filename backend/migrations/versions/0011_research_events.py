"""live research events, provider response cache and provider usage ledger (additive)

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None
JSONB = postgresql.JSONB(astext_type=sa.Text())
TABLES = ("research_events", "provider_cache", "provider_calls")


def upgrade() -> None:
    op.create_table(
        "research_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("audit_report_id", sa.String(32), nullable=True), sa.Column("client_id", sa.String(32), nullable=True),
        sa.Column("business_id", sa.String(32), nullable=True), sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False), sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("level", sa.String(8), nullable=False), sa.Column("title", sa.String(300), nullable=False),
        sa.Column("data", JSONB, nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_research_events_audit_report_id", "research_events", ["audit_report_id"])
    op.create_index("ix_research_events_client_id", "research_events", ["client_id"])
    op.create_index("ix_research_events_report_seq", "research_events", ["audit_report_id", "seq"], unique=True)
    op.create_table(
        "provider_cache",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False), sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("request", JSONB, nullable=False), sa.Column("response", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "cache_key", name="uq_provider_cache_key"))
    op.create_table(
        "provider_calls",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False), sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False), sa.Column("cached", sa.Boolean(), nullable=False),
        sa.Column("error_kind", sa.String(40), nullable=False), sa.Column("error", sa.Text(), nullable=False),
        sa.Column("cost_units", sa.Float(), nullable=False), sa.Column("audit_report_id", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_provider_calls_provider", "provider_calls", ["provider"])
    op.create_index("ix_provider_calls_audit_report_id", "provider_calls", ["audit_report_id"])
    op.create_index("ix_provider_calls_created_at", "provider_calls", ["created_at"])
    for t in TABLES:
        op.execute(sa.text(f'ALTER TABLE "{t}" ENABLE ROW LEVEL SECURITY'))


def downgrade() -> None:
    for t in reversed(TABLES):
        op.drop_table(t)
