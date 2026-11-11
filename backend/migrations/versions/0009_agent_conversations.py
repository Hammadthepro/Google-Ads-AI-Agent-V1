"""conversational agent: conversations, messages, resumable runs (additive; no knowledge is duplicated here)

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "agent_conversations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_id", sa.String(32), nullable=True), sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False), sa.Column("context", JSONB, nullable=False), sa.Column("archived", sa.Boolean(), nullable=False))
    op.create_index("ix_agent_conversations_client_id", "agent_conversations", ["client_id"])
    op.create_index("ix_agent_conversations_user_id", "agent_conversations", ["user_id"])
    op.create_table(
        "agent_messages",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("conversation_id", sa.String(32), sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(10), nullable=False), sa.Column("kind", sa.String(24), nullable=False), sa.Column("content", sa.Text(), nullable=False),
        sa.Column("data", JSONB, nullable=False), sa.Column("run_id", sa.String(32), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_agent_messages_conversation_id", "agent_messages", ["conversation_id"])
    op.create_index("ix_agent_messages_conv_created", "agent_messages", ["conversation_id", "created_at"])
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("conversation_id", sa.String(32), sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.String(32), nullable=True), sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False), sa.Column("intent", sa.String(40), nullable=False), sa.Column("interpreted_by", sa.String(12), nullable=False),
        sa.Column("params", JSONB, nullable=False), sa.Column("plan", JSONB, nullable=False), sa.Column("status", sa.String(28), nullable=False),
        sa.Column("result", JSONB, nullable=False), sa.Column("error", sa.Text(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"])
    op.create_index("ix_agent_runs_client_id", "agent_runs", ["client_id"])
    for t in ("agent_conversations", "agent_messages", "agent_runs"):
        op.execute(sa.text(f'ALTER TABLE "{t}" ENABLE ROW LEVEL SECURITY'))


def downgrade() -> None:
    for t in ("agent_runs", "agent_messages", "agent_conversations"):
        op.drop_table(t)
