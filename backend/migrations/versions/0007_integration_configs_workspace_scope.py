"""integration_configs are scoped to a workspace (credentials never cross workspaces)

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("integration_configs", sa.Column("workspace_id", sa.String(length=32), nullable=True))
    # make sure the default workspace exists, then give every existing row to it (nothing is deleted or changed otherwise)
    op.execute(sa.text("INSERT INTO workspaces (id, name, slug, google_ads_manager_customer_id, google_ads_manager_name, discovery_status, discovery_message, created_at, updated_at) "
                       "SELECT replace(gen_random_uuid()::text, '-', ''), 'Default workspace', 'default', '', '', 'NEVER_RUN', '', now(), now() "
                       "WHERE NOT EXISTS (SELECT 1 FROM workspaces WHERE slug = 'default')"))
    op.execute(sa.text("UPDATE integration_configs SET workspace_id = (SELECT id FROM workspaces WHERE slug = 'default') WHERE workspace_id IS NULL"))
    op.drop_index("ix_integration_configs_provider", table_name="integration_configs")
    op.create_index("ix_integration_configs_provider", "integration_configs", ["provider"], unique=False)
    op.create_index("ix_integration_configs_workspace_id", "integration_configs", ["workspace_id"], unique=False)
    op.create_foreign_key("fk_integration_configs_workspace_id", "integration_configs", "workspaces", ["workspace_id"], ["id"])
    op.create_unique_constraint("uq_integration_provider_workspace", "integration_configs", ["provider", "workspace_id"])


def downgrade() -> None:
    op.drop_constraint("uq_integration_provider_workspace", "integration_configs", type_="unique")
    op.drop_constraint("fk_integration_configs_workspace_id", "integration_configs", type_="foreignkey")
    op.drop_index("ix_integration_configs_workspace_id", table_name="integration_configs")
    op.drop_index("ix_integration_configs_provider", table_name="integration_configs")
    op.create_index("ix_integration_configs_provider", "integration_configs", ["provider"], unique=True)
    op.drop_column("integration_configs", "workspace_id")
