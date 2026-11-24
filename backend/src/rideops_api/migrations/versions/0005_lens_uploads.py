"""Lens uploads: SQLite database files added by upload (F-CON-1).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-24
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE app.uploads (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE CASCADE,
  kind         text NOT NULL DEFAULT 'sqlite' CHECK (kind IN ('sqlite')),
  filename     text NOT NULL,
  bytes        bigint NOT NULL,
  sha256       text NOT NULL,
  created_by   uuid,
  created_at   timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE app.uploads ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.uploads FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.uploads
  USING (NULLIF(current_setting('app.workspace_id', true), '') IS NULL
         OR workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
  WITH CHECK (NULLIF(current_setting('app.workspace_id', true), '') IS NULL
              OR workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.uploads")
