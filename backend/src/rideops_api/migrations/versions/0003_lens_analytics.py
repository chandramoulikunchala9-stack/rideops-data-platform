"""Lens analytics: catalog, table stats, schema changes, profiles, insights, queries, dashboards.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-24

Design: docs/ReContruction/lens-system-design.md §12 (catalog & analytics, queries & dashboards).
Tables keyed by workspace get the same Row-Level Security policy as migration 0002; catalog tables
are reached only through a connection the request already resolved inside its workspace.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TENANT_TABLES = ("saved_queries", "query_runs", "dashboards")

UPGRADE = """
CREATE TABLE app.catalog_syncs (
  id            bigserial PRIMARY KEY,
  connection_id uuid NOT NULL REFERENCES app.connections(id) ON DELETE CASCADE,
  started_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  ok            boolean,
  table_count   int,
  column_count  int,
  error         text
);
CREATE INDEX catalog_syncs_conn ON app.catalog_syncs (connection_id, started_at DESC);

CREATE TABLE app.catalog_tables (
  connection_id    uuid NOT NULL REFERENCES app.connections(id) ON DELETE CASCADE,
  schema_name      text NOT NULL,
  table_name       text NOT NULL,
  kind             text NOT NULL,
  comment          text,
  columns          jsonb NOT NULL,
  indexes          jsonb NOT NULL DEFAULT '[]',
  foreign_keys     jsonb NOT NULL DEFAULT '[]',
  primary_key      text[],
  freshness_column text,
  pii_columns      text[] NOT NULL DEFAULT '{}',
  first_seen_at    timestamptz NOT NULL DEFAULT now(),
  last_seen_sync   bigint REFERENCES app.catalog_syncs(id) ON DELETE SET NULL,
  dropped_at       timestamptz,
  is_pinned        boolean NOT NULL DEFAULT false,
  PRIMARY KEY (connection_id, schema_name, table_name)
);

CREATE TABLE app.table_stats_snapshots (
  connection_id  uuid NOT NULL REFERENCES app.connections(id) ON DELETE CASCADE,
  schema_name    text NOT NULL,
  table_name     text NOT NULL,
  taken_at       timestamptz NOT NULL DEFAULT now(),
  row_estimate   bigint,
  row_exact      bigint,
  total_bytes    bigint,
  table_bytes    bigint,
  index_bytes    bigint,
  dead_rows      bigint,
  seq_scans      bigint,
  idx_scans      bigint,
  inserts        bigint,
  updates        bigint,
  deletes        bigint,
  last_vacuum    timestamptz,
  last_analyze   timestamptz,
  freshness_max  timestamptz,
  PRIMARY KEY (connection_id, schema_name, table_name, taken_at)
);

CREATE TABLE app.schema_changes (
  id            bigserial PRIMARY KEY,
  connection_id uuid NOT NULL REFERENCES app.connections(id) ON DELETE CASCADE,
  detected_at   timestamptz NOT NULL DEFAULT now(),
  change_type   text NOT NULL,
  object_ref    text NOT NULL,
  before        jsonb,
  after         jsonb
);
CREATE INDEX schema_changes_conn ON app.schema_changes (connection_id, detected_at DESC);

CREATE TABLE app.column_profiles (
  id            bigserial PRIMARY KEY,
  connection_id uuid NOT NULL REFERENCES app.connections(id) ON DELETE CASCADE,
  schema_name   text NOT NULL,
  table_name    text NOT NULL,
  profiled_at   timestamptz NOT NULL DEFAULT now(),
  sample_rows   int NOT NULL,
  method        text NOT NULL,
  elapsed_ms    int,
  profiles      jsonb NOT NULL,
  charts        jsonb NOT NULL
);
CREATE INDEX column_profiles_table ON app.column_profiles (connection_id, schema_name, table_name, profiled_at DESC);

CREATE TABLE app.insights (
  id             bigserial PRIMARY KEY,
  connection_id  uuid NOT NULL REFERENCES app.connections(id) ON DELETE CASCADE,
  code           text NOT NULL,
  severity       text NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
  object_ref     text NOT NULL DEFAULT '',
  title          text NOT NULL,
  detail         jsonb NOT NULL DEFAULT '{}',
  recommendation text,
  first_seen_at  timestamptz NOT NULL DEFAULT now(),
  last_seen_at   timestamptz NOT NULL DEFAULT now(),
  resolved_at    timestamptz,
  dismissed_by   uuid,
  dismissed_at   timestamptz,
  UNIQUE (connection_id, code, object_ref)
);

CREATE TABLE app.saved_queries (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE CASCADE,
  connection_id uuid NOT NULL REFERENCES app.connections(id) ON DELETE CASCADE,
  name          text NOT NULL,
  description   text,
  statement     text NOT NULL,
  tags          text[] NOT NULL DEFAULT '{}',
  created_by    uuid,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.query_runs (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id     uuid NOT NULL,
  connection_id    uuid NOT NULL REFERENCES app.connections(id) ON DELETE CASCADE,
  saved_query_id   uuid REFERENCES app.saved_queries(id) ON DELETE SET NULL,
  statement_hash   text NOT NULL,
  statement        text NOT NULL,
  status           text NOT NULL CHECK (status IN ('queued', 'running', 'ok', 'error', 'cancelled', 'rejected')),
  row_count        int,
  truncated        boolean,
  elapsed_ms       int,
  error_code       text,
  error_message    text,
  result_cache     jsonb,
  cache_expires_at timestamptz,
  run_by           uuid,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX query_runs_ws ON app.query_runs (workspace_id, created_at DESC);

CREATE TABLE app.dashboards (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id       uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE CASCADE,
  name               text NOT NULL,
  description        text,
  is_auto            boolean NOT NULL DEFAULT false,
  connection_id      uuid REFERENCES app.connections(id) ON DELETE CASCADE,
  layout             jsonb NOT NULL DEFAULT '[]',
  refresh_interval_s int CHECK (refresh_interval_s IS NULL OR refresh_interval_s >= 900),
  last_refreshed_at  timestamptz,
  created_by         uuid,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.widgets (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dashboard_id uuid NOT NULL REFERENCES app.dashboards(id) ON DELETE CASCADE,
  position     int NOT NULL DEFAULT 0,
  kind         text NOT NULL CHECK (kind IN ('kpi', 'line', 'bar', 'table', 'status', 'uptime', 'text')),
  title        text NOT NULL,
  source       jsonb NOT NULL,
  options      jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE app.share_links (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL,
  dashboard_id uuid NOT NULL REFERENCES app.dashboards(id) ON DELETE CASCADE,
  token_hash   text NOT NULL,
  snapshot     jsonb NOT NULL,
  expires_at   timestamptz NOT NULL,
  revoked_at   timestamptz,
  created_by   uuid,
  created_at   timestamptz NOT NULL DEFAULT now()
);
"""

POLICY = """
ALTER TABLE app.{t} ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.{t} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.{t}
  USING (NULLIF(current_setting('app.workspace_id', true), '') IS NULL
         OR workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
  WITH CHECK (NULLIF(current_setting('app.workspace_id', true), '') IS NULL
              OR workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
"""

DOWNGRADE = """
DROP TABLE IF EXISTS app.share_links, app.widgets, app.dashboards, app.query_runs, app.saved_queries,
  app.insights, app.column_profiles, app.schema_changes, app.table_stats_snapshots, app.catalog_tables,
  app.catalog_syncs;
"""


def upgrade() -> None:
    op.execute(UPGRADE)
    for table in TENANT_TABLES:
        op.execute(POLICY.format(t=table))


def downgrade() -> None:
    op.execute(DOWNGRADE)
