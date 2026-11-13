"""Main AI / Challenger AI role configuration, and role, request id and cost on every AI run (additive)

Why: each workspace chooses its own provider and model for the Main AI (does the work) and the Challenger AI (reviews important
outputs). Keys are stored Fernet-encrypted like every other credential and are never returned by the API. Existing Groq settings
are adopted at runtime by reference (credential_ref), never copied or overwritten.

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_role_configs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.String(32), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False, server_default=""),
        sa.Column("model", sa.String(200), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("secrets_ciphertext", sa.Text(), nullable=False, server_default=""),
        sa.Column("key_fingerprint", sa.String(32), nullable=False, server_default=""),
        sa.Column("credential_ref", sa.String(40), nullable=False, server_default=""),
        sa.Column("source", sa.String(30), nullable=False, server_default="configured"),
        sa.Column("status", sa.String(30), nullable=False, server_default="NOT_CONFIGURED"),
        sa.Column("model_meta", JSON, nullable=False, server_default="{}"),
        sa.Column("models_cache", JSON, nullable=False, server_default="[]"),
        sa.Column("models_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("models_error", sa.String(300), nullable=False, server_default=""),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_test_steps", JSON, nullable=False, server_default="[]"),
        sa.Column("last_error", sa.String(500), nullable=False, server_default=""),
        sa.Column("last_error_kind", sa.String(40), nullable=False, server_default=""),
        sa.Column("pricing", JSON, nullable=False, server_default="{}"),
        sa.Column("review_policy", JSON, nullable=False, server_default="{}"),
        sa.Column("updated_by_id", sa.String(32), nullable=True),
        sa.UniqueConstraint("workspace_id", "role", name="uq_ai_role_workspace"),
    )
    op.create_index("ix_ai_role_configs_workspace_id", "ai_role_configs", ["workspace_id"])
    op.add_column("ai_runs", sa.Column("role", sa.String(20), nullable=False, server_default=""))
    op.add_column("ai_runs", sa.Column("request_id", sa.String(32), nullable=False, server_default=""))
    op.add_column("ai_runs", sa.Column("cost_usd", sa.Float(), nullable=True))
    op.add_column("ai_runs", sa.Column("cost_source", sa.String(20), nullable=False, server_default=""))
    op.create_index("ix_ai_runs_created_role", "ai_runs", ["created_at", "role"])


def downgrade() -> None:
    op.drop_index("ix_ai_runs_created_role", table_name="ai_runs")
    for c in ("cost_source", "cost_usd", "request_id", "role"):
        op.drop_column("ai_runs", c)
    op.drop_index("ix_ai_role_configs_workspace_id", table_name="ai_role_configs")
    op.drop_table("ai_role_configs")
