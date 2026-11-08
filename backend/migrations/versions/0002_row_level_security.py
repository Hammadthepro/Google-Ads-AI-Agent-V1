"""Enable Row Level Security on every application table.

On Supabase, tables in the public schema are reachable through the PostgREST Data API with the anon /
authenticated roles. This app never uses the Data API: the backend connects as the table owner, which
bypasses RLS. Enabling RLS with NO policies (and revoking grants) denies all Data API access.
On plain PostgreSQL the Supabase roles do not exist and only ENABLE ROW LEVEL SECURITY runs (harmless).

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _tables():
    # Only tables that exist at this revision: later migrations add tables (and enable RLS on them) themselves.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    from app.models import Base
    return [t.name for t in Base.metadata.sorted_tables if t.name in existing] + ["alembic_version"]


def upgrade() -> None:
    for name in _tables():
        op.execute(sa.text(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text("""
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', r);
      EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', r);
    END IF;
  END LOOP;
END $$;"""))


def downgrade() -> None:
    for name in _tables():
        op.execute(sa.text(f'ALTER TABLE "{name}" DISABLE ROW LEVEL SECURITY'))
