"""Challenger resolutions and the research evidence of each plan version (additive)

Why: every Challenger objection gets a recorded resolution (applied as a new version, edited, revision requested, or dismissed with a
reason) that stays in the audit history; and each campaign plan version keeps the research and measurements it was built from, so the
Research & Evidence Appendix shows what that exact version used, captured from persisted records rather than recreated later.

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaign_plans", sa.Column("evidence", JSON, nullable=False, server_default="{}"))
    op.create_table(
        "challenger_resolutions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("task_id", sa.String(32), nullable=False, index=True),
        sa.Column("plan_id", sa.String(32), nullable=False, index=True),
        sa.Column("approval_id", sa.String(32), nullable=True),
        sa.Column("objection_key", sa.String(64), nullable=False, index=True),
        sa.Column("objection", JSON, nullable=False, server_default="{}"),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("edits", JSON, nullable=False, server_default="[]"),
        sa.Column("result_plan_id", sa.String(32), nullable=True),
        sa.Column("result_approval_id", sa.String(32), nullable=True),
        sa.Column("actor_id", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("challenger_resolutions")
    op.drop_column("campaign_plans", "evidence")
