"""ai_runs: finish reason, provider status, error kind, draft stage and the prompt-size estimate (additive)

Why: a failed draft must say whether the request was over the per-minute token limit, rate-limited, over the context window, cut off
at the output limit, malformed JSON or a genuine schema failure, and which stage of a staged draft it was. Prompt and completion
token counts already exist (tokens_in / tokens_out).

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

COLUMNS = (("finish_reason", 30), ("provider_status", 40), ("error_kind", 40), ("stage", 60))


def upgrade() -> None:
    for name, size in COLUMNS:
        op.add_column("ai_runs", sa.Column(name, sa.String(size), nullable=False, server_default=""))
    op.add_column("ai_runs", sa.Column("prompt_estimate", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("ai_runs", "prompt_estimate")
    for name, _ in COLUMNS:
        op.drop_column("ai_runs", name)
