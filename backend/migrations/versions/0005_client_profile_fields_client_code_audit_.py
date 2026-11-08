"""client profile fields, client code, audit reports; drop legacy integration_connections

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-08 17:17:38.443289
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NOTE: the legacy integration_connections table was unused since V2 (credentials moved to integration_configs).
    op.create_table('audit_reports',
    sa.Column('client_id', sa.String(length=32), nullable=False),
    sa.Column('business_id', sa.String(length=32), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('previous_report_id', sa.String(length=32), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('steps', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('sections', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('scores', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('recommendations', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('summary', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('changes', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('input_snapshot', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('input_hash', sa.String(length=64), nullable=False),
    sa.Column('source_type', sa.String(length=8), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by_id', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_reports_business_id'), 'audit_reports', ['business_id'], unique=False)
    op.create_index(op.f('ix_audit_reports_client_id'), 'audit_reports', ['client_id'], unique=False)
    op.drop_index(op.f('ix_integration_connections_provider'), table_name='integration_connections')
    op.drop_table('integration_connections')
    op.add_column('businesses', sa.Column('category', sa.String(length=120), server_default='', nullable=False))
    op.add_column('businesses', sa.Column('email', sa.String(length=320), server_default='', nullable=False))
    op.add_column('businesses', sa.Column('gtm_id', sa.String(length=40), server_default='', nullable=False))
    op.add_column('businesses', sa.Column('has_gbp', sa.Boolean(), nullable=True))
    op.add_column('businesses', sa.Column('service_areas', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), server_default=sa.text("'[]'"), nullable=False))
    op.add_column('businesses', sa.Column('states', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), server_default=sa.text("'[]'"), nullable=False))
    op.add_column('businesses', sa.Column('targeting_mode', sa.String(length=12), server_default='AREAS', nullable=False))
    op.add_column('businesses', sa.Column('monthly_budget_usd', sa.Float(), server_default='0', nullable=False))
    op.add_column('businesses', sa.Column('notes', sa.Text(), server_default='', nullable=False))
    op.add_column('clients', sa.Column('client_code', sa.String(length=16), server_default='', nullable=False))
    # backfill existing rows: unique client codes in creation order, profile fields derived from what is already stored
    op.execute("UPDATE clients c SET client_code = 'CL-' || LPAD(n.rn::text, 6, '0') FROM "
               "(SELECT id, ROW_NUMBER() OVER (ORDER BY created_at, id) AS rn FROM clients) n WHERE n.id = c.id")
    op.create_index(op.f('ix_clients_client_code'), 'clients', ['client_code'], unique=True)
    op.execute("UPDATE businesses SET category = business_type, has_gbp = (gbp_url <> ''), "
               "monthly_budget_usd = COALESCE((SELECT monthly_budget_usd FROM clients WHERE clients.id = businesses.client_id), 0)")
    op.execute(sa.text('ALTER TABLE audit_reports ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text("""
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('REVOKE ALL ON TABLE public.audit_reports FROM %I', r);
    END IF;
  END LOOP;
END $$;"""))
    op.add_column('competitors', sa.Column('maps_url', sa.String(length=500), server_default='', nullable=False))


def downgrade() -> None:
    op.drop_column('competitors', 'maps_url')
    op.drop_index(op.f('ix_clients_client_code'), table_name='clients')
    op.drop_column('clients', 'client_code')
    op.drop_column('businesses', 'notes')
    op.drop_column('businesses', 'monthly_budget_usd')
    op.drop_column('businesses', 'targeting_mode')
    op.drop_column('businesses', 'states')
    op.drop_column('businesses', 'service_areas')
    op.drop_column('businesses', 'has_gbp')
    op.drop_column('businesses', 'gtm_id')
    op.drop_column('businesses', 'email')
    op.drop_column('businesses', 'category')
    op.create_table('integration_connections',
    sa.Column('provider', sa.VARCHAR(length=40), autoincrement=False, nullable=False),
    sa.Column('account_ref', sa.VARCHAR(length=100), autoincrement=False, nullable=False),
    sa.Column('status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('integration_mode', sa.VARCHAR(length=8), autoincrement=False, nullable=False),
    sa.Column('scopes', postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False),
    sa.Column('encrypted_refresh_token', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('token_fingerprint', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('connected_by_id', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.Column('last_test_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('last_success_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('last_error', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('oauth_state', sa.VARCHAR(length=100), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.Column('id', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('integration_connections_pkey'))
    )
    op.create_index(op.f('ix_integration_connections_provider'), 'integration_connections', ['provider'], unique=False)
    op.drop_index(op.f('ix_audit_reports_client_id'), table_name='audit_reports')
    op.drop_index(op.f('ix_audit_reports_business_id'), table_name='audit_reports')
    op.drop_table('audit_reports')
