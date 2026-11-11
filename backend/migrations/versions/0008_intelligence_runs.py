"""ten-module intelligence runs: versioned, evidence-bearing findings per business (additive; nothing is changed or dropped)

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "intelligence_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_id", sa.String(length=32), nullable=False),
        sa.Column("business_id", sa.String(length=32), nullable=False),
        sa.Column("module", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("findings", JSONB, nullable=False),
        sa.Column("coverage", JSONB, nullable=False),
        sa.Column("missing_information", JSONB, nullable=False),
        sa.Column("contradictions", JSONB, nullable=False),
        sa.Column("risks", JSONB, nullable=False),
        sa.Column("recommendations", JSONB, nullable=False),
        sa.Column("client_actions", JSONB, nullable=False),
        sa.Column("research_performed", JSONB, nullable=False),
        sa.Column("data_sources", JSONB, nullable=False),
        sa.Column("decision", sa.String(length=40), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source_report_id", sa.String(length=32), nullable=True),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_intelligence_runs_client_id"), "intelligence_runs", ["client_id"], unique=False)
    op.create_index(op.f("ix_intelligence_runs_business_id"), "intelligence_runs", ["business_id"], unique=False)
    op.create_index("ix_intelligence_runs_current", "intelligence_runs", ["business_id", "module", "is_current"], unique=False)
    op.execute(sa.text('ALTER TABLE "intelligence_runs" ENABLE ROW LEVEL SECURITY'))  # same Data-API lockout as migration 0002


def downgrade() -> None:
    op.drop_index("ix_intelligence_runs_current", table_name="intelligence_runs")
    op.drop_index(op.f("ix_intelligence_runs_business_id"), table_name="intelligence_runs")
    op.drop_index(op.f("ix_intelligence_runs_client_id"), table_name="intelligence_runs")
    op.drop_table("intelligence_runs")
