"""retire the tracking-status gate: tracking_gate_results becomes measurement_audits (findings + facts, no status columns)

The status columns (state, effective_state, override_active) are dropped: they were derived values of a workflow that no longer exists.
Historical check lists are kept in `legacy_checks` / `legacy_fix_list`. The `tracking_overrides` table is kept as-is (renamed
`retired_tracking_overrides`) as an audit record of past admin decisions; the application no longer reads or writes it.

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.rename_table("tracking_gate_results", "measurement_audits")
    op.execute('ALTER INDEX IF EXISTS ix_tracking_gate_results_business_id RENAME TO ix_measurement_audits_business_id')
    op.alter_column("measurement_audits", "checks", new_column_name="legacy_checks", nullable=True)
    op.alter_column("measurement_audits", "fix_list", new_column_name="legacy_fix_list", nullable=True)
    op.add_column("measurement_audits", sa.Column("findings", JSONB, nullable=False, server_default="[]"))
    op.add_column("measurement_audits", sa.Column("facts", JSONB, nullable=False, server_default="{}"))
    op.alter_column("measurement_audits", "findings", server_default=None)
    op.alter_column("measurement_audits", "facts", server_default=None)
    for col in ("state", "effective_state", "override_active"):
        op.drop_column("measurement_audits", col)
    op.rename_table("tracking_overrides", "retired_tracking_overrides")
    op.execute("UPDATE intelligence_runs SET module = 'measurement' WHERE module = 'tracking'")
    op.execute("UPDATE system_settings SET key = 'measurement.min_conversions_30d' WHERE key = 'tracking.green_min_conversions_30d'")
    op.execute("UPDATE system_settings SET key = 'measurement.call_min_seconds' WHERE key = 'tracking.call_min_seconds'")
    op.execute("DELETE FROM system_settings WHERE key = 'tracking.override_roles'")  # a role list for a control that no longer exists
    # task rows stopped by the retired gate keep their history under a status that still exists
    op.execute("UPDATE tasks SET status = 'FAILED', error = 'Stopped by the retired tracking gate. ' || coalesce(error, '') WHERE status = 'BLOCKED_TRACKING'")


def downgrade() -> None:
    op.execute("UPDATE intelligence_runs SET module = 'tracking' WHERE module = 'measurement'")
    op.execute("UPDATE system_settings SET key = 'tracking.green_min_conversions_30d' WHERE key = 'measurement.min_conversions_30d'")
    op.execute("UPDATE system_settings SET key = 'tracking.call_min_seconds' WHERE key = 'measurement.call_min_seconds'")
    op.rename_table("retired_tracking_overrides", "tracking_overrides")
    op.add_column("measurement_audits", sa.Column("state", sa.String(8), nullable=False, server_default="UNKNOWN"))
    op.add_column("measurement_audits", sa.Column("effective_state", sa.String(8), nullable=False, server_default="UNKNOWN"))
    op.add_column("measurement_audits", sa.Column("override_active", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.drop_column("measurement_audits", "facts")
    op.drop_column("measurement_audits", "findings")
    op.execute("UPDATE measurement_audits SET legacy_checks = '[]' WHERE legacy_checks IS NULL")
    op.execute("UPDATE measurement_audits SET legacy_fix_list = '[]' WHERE legacy_fix_list IS NULL")
    op.alter_column("measurement_audits", "legacy_checks", new_column_name="checks", nullable=False)
    op.alter_column("measurement_audits", "legacy_fix_list", new_column_name="fix_list", nullable=False)
    op.execute('ALTER INDEX IF EXISTS ix_measurement_audits_business_id RENAME TO ix_tracking_gate_results_business_id')
    op.rename_table("measurement_audits", "tracking_gate_results")
