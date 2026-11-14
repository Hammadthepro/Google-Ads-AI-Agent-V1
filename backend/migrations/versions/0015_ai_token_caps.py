"""Manual token caps per AI role, an atomic token ledger, and the client on every AI run (additive)

Why: an administrator sets application token caps for the Main AI and the Challenger AI (per request, per task, per client per day
and per month). Caps are enforced by the backend before a request is sent, with reservations in a ledger updated by one atomic
statement, so concurrent tasks cannot overspend. These are the app's own limits, separate from the provider's TPM/RPM, context window
and maximum output. ai_runs.client_id lets usage be reported per client.

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_role_configs", sa.Column("caps", JSON, nullable=False, server_default="{}"))
    op.add_column("ai_runs", sa.Column("client_id", sa.String(32), nullable=True))
    op.create_index("ix_ai_runs_client_id", "ai_runs", ["client_id"])
    op.create_table(
        "ai_token_ledger",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.String(32), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),        # task | client_day | client_month
        sa.Column("scope_key", sa.String(32), nullable=False),    # task id or client id
        sa.Column("period", sa.String(10), nullable=False),       # YYYY-MM-DD, YYYY-MM or "all"
        sa.Column("used", sa.BigInteger, nullable=False, server_default="0"),
        sa.UniqueConstraint("workspace_id", "role", "scope", "scope_key", "period", name="uq_ai_token_ledger_bucket"),
    )


def downgrade() -> None:
    op.drop_table("ai_token_ledger")
    op.drop_index("ix_ai_runs_client_id", table_name="ai_runs")
    op.drop_column("ai_runs", "client_id")
    op.drop_column("ai_role_configs", "caps")
