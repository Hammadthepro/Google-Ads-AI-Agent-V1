"""integrations, client targets, plan versions, budget decisions

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-08 13:39:26.779257
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('integration_configs',
    sa.Column('provider', sa.String(length=40), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('settings', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('secrets_ciphertext', sa.Text(), nullable=False),
    sa.Column('secret_fingerprints', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=40), nullable=False),
    sa.Column('connected_account', sa.String(length=320), nullable=False),
    sa.Column('account_id', sa.String(length=100), nullable=False),
    sa.Column('scopes', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('last_test_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_test_ok', sa.Boolean(), nullable=True),
    sa.Column('last_test_steps', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=False),
    sa.Column('discovered', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('updated_by_id', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_integration_configs_provider'), 'integration_configs', ['provider'], unique=True)
    op.create_table('oauth_states',
    sa.Column('state_hash', sa.String(length=64), nullable=False),
    sa.Column('provider', sa.String(length=40), nullable=False),
    sa.Column('verifier_ciphertext', sa.Text(), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('redirect_uri', sa.String(length=500), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_oauth_states_state_hash'), 'oauth_states', ['state_hash'], unique=True)
    op.add_column('campaign_plans', sa.Column('competition', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), server_default=sa.text("'{}'"), nullable=False))
    op.add_column('campaign_plans', sa.Column('original', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), server_default=sa.text("'{}'"), nullable=False))
    op.add_column('campaign_plans', sa.Column('parent_plan_id', sa.String(length=32), nullable=True))
    op.add_column('campaign_plans', sa.Column('change_summary', sa.Text(), server_default='', nullable=False))
    op.add_column('campaign_plans', sa.Column('edited_by_id', sa.String(length=32), nullable=True))
    op.add_column('clients', sa.Column('weekly_conversion_target', sa.Float(), server_default='0', nullable=False))
    op.add_column('clients', sa.Column('monthly_budget_usd', sa.Float(), server_default='0', nullable=False))
    op.add_column('feasibility_results', sa.Column('decision', sa.String(length=24), server_default='', nullable=False))
    op.add_column('feasibility_results', sa.Column('decision_note', sa.Text(), server_default='', nullable=False))
    op.add_column('feasibility_results', sa.Column('decided_by_id', sa.String(length=32), nullable=True))
    op.add_column('feasibility_results', sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('feasibility_results', sa.Column('accepted_monthly_budget', sa.Float(), nullable=True))
    op.add_column('feasibility_results', sa.Column('accepted_weekly_target', sa.Float(), nullable=True))
    op.add_column('google_ads_accounts', sa.Column('access_level', sa.String(length=16), server_default='READ_ONLY', nullable=False))
    # backfill: existing campaign targets roll up into the new client-level target
    op.execute("UPDATE clients SET weekly_conversion_target = COALESCE((SELECT SUM(c.weekly_conversion_target) FROM campaigns c "
               "WHERE c.client_id = clients.id AND c.status != 'REMOVED'), 0)")
    # new tables follow the same rule as 0002: RLS on, no Data API grants
    for name in ("integration_configs", "oauth_states"):
        op.execute(sa.text(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text("""
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('REVOKE ALL ON TABLE public.integration_configs, public.oauth_states FROM %I', r);
    END IF;
  END LOOP;
END $$;"""))
    op.add_column('tasks', sa.Column('intake', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), server_default=sa.text("'{}'"), nullable=False))


def downgrade() -> None:
    op.drop_column('tasks', 'intake')
    op.drop_column('google_ads_accounts', 'access_level')
    op.drop_column('feasibility_results', 'accepted_weekly_target')
    op.drop_column('feasibility_results', 'accepted_monthly_budget')
    op.drop_column('feasibility_results', 'decided_at')
    op.drop_column('feasibility_results', 'decided_by_id')
    op.drop_column('feasibility_results', 'decision_note')
    op.drop_column('feasibility_results', 'decision')
    op.drop_column('clients', 'monthly_budget_usd')
    op.drop_column('clients', 'weekly_conversion_target')
    op.drop_column('campaign_plans', 'edited_by_id')
    op.drop_column('campaign_plans', 'change_summary')
    op.drop_column('campaign_plans', 'parent_plan_id')
    op.drop_column('campaign_plans', 'original')
    op.drop_column('campaign_plans', 'competition')
    op.drop_index(op.f('ix_oauth_states_state_hash'), table_name='oauth_states')
    op.drop_table('oauth_states')
    op.drop_index(op.f('ix_integration_configs_provider'), table_name='integration_configs')
    op.drop_table('integration_configs')
