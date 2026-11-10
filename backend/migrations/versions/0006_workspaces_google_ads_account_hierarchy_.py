"""workspaces, google ads account hierarchy, user account selections

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-10 06:26:26.076297
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('workspaces',
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('slug', sa.String(length=60), nullable=False),
    sa.Column('google_ads_manager_customer_id', sa.String(length=20), nullable=False),
    sa.Column('google_ads_manager_name', sa.String(length=200), nullable=False),
    sa.Column('accounts_discovered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('discovery_status', sa.String(length=20), nullable=False),
    sa.Column('discovery_message', sa.String(length=500), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('user_account_selections',
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('workspace_id', sa.String(length=32), nullable=False),
    sa.Column('customer_id', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'workspace_id')
    )
    op.create_index(op.f('ix_user_account_selections_user_id'), 'user_account_selections', ['user_id'], unique=False)
    op.create_index(op.f('ix_user_account_selections_workspace_id'), 'user_account_selections', ['workspace_id'], unique=False)
    # additive columns; server defaults let existing rows pass NOT NULL, then are dropped so the ORM stays the source of defaults
    op.add_column('google_ads_accounts', sa.Column('workspace_id', sa.String(length=32), nullable=True))
    for name, col, default in [('status', sa.String(length=16), 'UNKNOWN'), ('level', sa.Integer(), '0'), ('is_test_account', sa.Boolean(), 'false'),
                               ('is_hidden', sa.Boolean(), 'false'), ('discovery_state', sa.String(length=12), 'ACTIVE'),
                               ('discovery_error', sa.String(length=64), '')]:
        op.add_column('google_ads_accounts', sa.Column(name, col, nullable=False, server_default=default))
        op.alter_column('google_ads_accounts', name, server_default=None)
    op.add_column('google_ads_accounts', sa.Column('last_discovered_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_google_ads_accounts_workspace_id'), 'google_ads_accounts', ['workspace_id'], unique=False)
    op.create_foreign_key('fk_google_ads_accounts_workspace_id', 'google_ads_accounts', 'workspaces', ['workspace_id'], ['id'])
    # the single default workspace; existing accounts (if any) join it. Nothing is deleted or rewritten.
    op.execute(sa.text("INSERT INTO workspaces (id, name, slug, google_ads_manager_customer_id, google_ads_manager_name, discovery_status, discovery_message, created_at, updated_at) "
                       "VALUES (replace(gen_random_uuid()::text, '-', ''), 'Default workspace', 'default', '', '', 'NEVER_RUN', '', now(), now())"))
    op.execute(sa.text("UPDATE google_ads_accounts SET workspace_id = (SELECT id FROM workspaces WHERE slug = 'default') WHERE workspace_id IS NULL"))
    for name in ('workspaces', 'user_account_selections'):  # same Data-API lockout as migration 0002
        op.execute(sa.text(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY'))
    # ### end Alembic commands ###


def downgrade() -> None:
    # WARNING: constraint name is None; this directive will fail as
    # rendered.  Add a name, or use a naming convention; see
    # https://alembic.sqlalchemy.org/en/latest/naming.html
    op.drop_constraint('fk_google_ads_accounts_workspace_id', 'google_ads_accounts', type_='foreignkey')
    op.drop_index(op.f('ix_google_ads_accounts_workspace_id'), table_name='google_ads_accounts')
    op.drop_column('google_ads_accounts', 'last_discovered_at')
    op.drop_column('google_ads_accounts', 'discovery_error')
    op.drop_column('google_ads_accounts', 'discovery_state')
    op.drop_column('google_ads_accounts', 'is_hidden')
    op.drop_column('google_ads_accounts', 'is_test_account')
    op.drop_column('google_ads_accounts', 'level')
    op.drop_column('google_ads_accounts', 'status')
    op.drop_column('google_ads_accounts', 'workspace_id')
    op.drop_index(op.f('ix_user_account_selections_workspace_id'), table_name='user_account_selections')
    op.drop_index(op.f('ix_user_account_selections_user_id'), table_name='user_account_selections')
    op.drop_table('user_account_selections')
    op.drop_table('workspaces')
    # ### end Alembic commands ###
