"""research events can belong to a standalone audit run, not only to an audit report (additive, nullable)

Why: website, Business Profile, competitor and keyword audits started from the Research & audits page are not part of an audit report,
so their live findings had nowhere to be recorded. `run_id` groups the events of one run (it equals the report id for report runs);
`audit_id` ties an event to the individual audit it came from, so every audit detail page can replay its own activity.

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("research_events", sa.Column("run_id", sa.String(32), nullable=True))
    op.add_column("research_events", sa.Column("audit_id", sa.String(32), nullable=True))
    op.create_index("ix_research_events_run_id", "research_events", ["run_id"])
    op.create_index("ix_research_events_audit_id", "research_events", ["audit_id"])
    op.execute(sa.text("UPDATE research_events SET run_id = audit_report_id WHERE run_id IS NULL"))
    op.create_index("ix_research_events_run_seq", "research_events", ["run_id", "seq"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_research_events_run_seq", table_name="research_events")
    op.drop_index("ix_research_events_audit_id", table_name="research_events")
    op.drop_index("ix_research_events_run_id", table_name="research_events")
    op.drop_column("research_events", "audit_id")
    op.drop_column("research_events", "run_id")
