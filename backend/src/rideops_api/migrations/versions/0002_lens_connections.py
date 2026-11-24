"""Lens connections: connections, encrypted secrets, connection tests, status snapshots, RLS.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24

Design: docs/ReContruction/lens-system-design.md §12 and FR-04 (Row-Level Security as defence
in depth). Policies read `app.workspace_id`, which the API sets per transaction
(`Database.tenant_transaction`). When the setting is absent (workers, scheduler, migrations)
the policy allows all rows: system code is trusted, request code is scoped. FORCE makes the
policies apply to the table owner too (superusers still bypass, as PostgreSQL always does).
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TENANT_TABLES = ("connections", "connection_tests", "status_snapshots")

UPGRADE = """
CREATE TYPE app.engine AS ENUM ('postgres', 'mysql', 'mssql', 'sqlite', 'mongodb',
                                'snowflake', 'bigquery', 'databricks');
CREATE TYPE app.conn_state AS ENUM ('healthy', 'degraded', 'down', 'auth_failed',
                                    'tls_error', 'unreachable', 'unknown', 'paused');

CREATE TABLE app.connections (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id     uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE CASCADE,
  name             text NOT NULL,
  engine           app.engine NOT NULL,
  environment      text NOT NULL DEFAULT 'dev' CHECK (environment IN ('prod', 'staging', 'dev', 'demo')),
  host             text,
  port             int,
  database         text,
  username         text,
  display_url      text NOT NULL,
  options          jsonb NOT NULL DEFAULT '{}',
  tls_mode         text NOT NULL DEFAULT 'require'
                   CHECK (tls_mode IN ('disable', 'require', 'verify-ca', 'verify-full')),
  connectivity     text NOT NULL DEFAULT 'direct' CHECK (connectivity IN ('direct', 'ssh', 'agent')),
  ssh_config       jsonb,
  agent_id         uuid,
  tags             text[] NOT NULL DEFAULT '{}',
  color            text,
  check_interval_s int NOT NULL DEFAULT 300 CHECK (check_interval_s >= 60),
  is_paused        boolean NOT NULL DEFAULT false,
  is_demo          boolean NOT NULL DEFAULT false,
  current_state    app.conn_state NOT NULL DEFAULT 'unknown',
  health_score     smallint,
  server_version   text,
  capabilities     text[] NOT NULL DEFAULT '{}',
  last_checked_at  timestamptz,
  last_state_change_at timestamptz,
  created_by       uuid REFERENCES app.users(id) ON DELETE SET NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  deleted_at       timestamptz
);
-- Names are unique among live connections; a soft-deleted name can be reused.
CREATE UNIQUE INDEX connections_ws_name ON app.connections (workspace_id, lower(name)) WHERE deleted_at IS NULL;
CREATE INDEX connections_ws ON app.connections (workspace_id) WHERE deleted_at IS NULL;

CREATE TABLE app.connection_secrets (
  connection_id uuid PRIMARY KEY REFERENCES app.connections(id) ON DELETE CASCADE,
  kek_version   int NOT NULL,
  wrapped_dek   bytea NOT NULL,
  nonce         bytea NOT NULL,
  ciphertext    bytea NOT NULL,
  rotated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.connection_tests (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id       uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE CASCADE,
  connection_id      uuid REFERENCES app.connections(id) ON DELETE CASCADE,
  ok                 boolean NOT NULL,
  stages             jsonb NOT NULL,
  privilege_findings jsonb,
  server_version     text,
  capabilities       text[] NOT NULL DEFAULT '{}',
  -- Keyed HMAC of the tested config + secret: proves a later save uses what was tested,
  -- without storing anything a password could be guessed from offline.
  config_fingerprint text NOT NULL,
  created_by         uuid,
  created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.status_snapshots (
  id             bigserial PRIMARY KEY,
  workspace_id   uuid NOT NULL,
  connection_id  uuid NOT NULL REFERENCES app.connections(id) ON DELETE CASCADE,
  taken_at       timestamptz NOT NULL DEFAULT now(),
  state          app.conn_state NOT NULL,
  reachable      boolean NOT NULL,
  latency_ms     int,
  error_code     text,
  error_message  text,
  server_version text,
  uptime_s       bigint,
  db_size_bytes  bigint,
  health_score   smallint,
  score_breakdown jsonb NOT NULL DEFAULT '[]',
  metrics        jsonb NOT NULL DEFAULT '{}',
  details        jsonb NOT NULL DEFAULT '{}',
  unavailable    jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX status_snapshots_conn ON app.status_snapshots (connection_id, taken_at DESC);

CREATE TABLE app.metric_rollups (
  connection_id uuid NOT NULL REFERENCES app.connections(id) ON DELETE CASCADE,
  grain         text NOT NULL CHECK (grain IN ('hour', 'day')),
  bucket        timestamptz NOT NULL,
  metric        text NOT NULL,
  min double precision, max double precision, avg double precision, p95 double precision,
  samples       int NOT NULL,
  up_checks     int,
  total_checks  int,
  PRIMARY KEY (connection_id, grain, bucket, metric)
);

DELETE FROM app.schedules;  -- Phase 11 had no connections, so nothing real is lost
ALTER TABLE app.schedules
  ADD CONSTRAINT schedules_connection_fk FOREIGN KEY (connection_id)
  REFERENCES app.connections(id) ON DELETE CASCADE;
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
ALTER TABLE app.schedules DROP CONSTRAINT IF EXISTS schedules_connection_fk;
DROP TABLE IF EXISTS app.metric_rollups, app.status_snapshots, app.connection_tests,
  app.connection_secrets, app.connections;
DROP TYPE IF EXISTS app.conn_state;
DROP TYPE IF EXISTS app.engine;
"""


def upgrade() -> None:
    op.execute(UPGRADE)
    for table in TENANT_TABLES:
        op.execute(POLICY.format(t=table))


def downgrade() -> None:
    op.execute(DOWNGRADE)
