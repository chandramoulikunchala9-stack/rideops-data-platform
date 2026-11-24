"""Lens foundations: identity, tenancy, sessions, one-time tokens, jobs, audit.

Revision ID: 0001
Revises:
Create Date: 2026-11-23

Design: docs/ReContruction/lens-system-design.md §12. Connection tables arrive in Phase 12
(migration 0002), which also adds the foreign key from app.schedules to app.connections.
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE app.users (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email           citext UNIQUE NOT NULL,
  display_name    text NOT NULL,
  password_hash   text,
  email_verified  boolean NOT NULL DEFAULT false,
  totp_secret_enc bytea,
  is_disabled     boolean NOT NULL DEFAULT false,
  created_at      timestamptz NOT NULL DEFAULT now(),
  last_login_at   timestamptz
);

CREATE TABLE app.oauth_identities (
  user_id  uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  provider text NOT NULL,
  subject  text NOT NULL,
  PRIMARY KEY (provider, subject)
);

-- id is the SHA-256 hex digest of the cookie value; the raw value is never stored.
CREATE TABLE app.sessions (
  id           text PRIMARY KEY,
  user_id      uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  csrf_token   text NOT NULL,
  ip           inet,
  user_agent   text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  expires_at   timestamptz NOT NULL
);
CREATE INDEX sessions_user ON app.sessions (user_id);

-- Single-use email tokens (verification 24 h, password reset 30 min). Stored hashed.
CREATE TABLE app.user_tokens (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  purpose    text NOT NULL CHECK (purpose IN ('verify_email', 'password_reset')),
  token_hash text UNIQUE NOT NULL,
  expires_at timestamptz NOT NULL,
  used_at    timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.login_attempts (
  id         bigserial PRIMARY KEY,
  email      citext NOT NULL,
  ip         inet,
  succeeded  boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX login_attempts_email ON app.login_attempts (email, created_at DESC);
CREATE INDEX login_attempts_ip ON app.login_attempts (ip, created_at DESC);

CREATE TABLE app.workspaces (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name       text NOT NULL,
  slug       citext UNIQUE NOT NULL,
  plan       text NOT NULL DEFAULT 'free',
  settings   jsonb NOT NULL DEFAULT '{"allow_insecure_connections": false,
                                       "allowed_ports": [], "egress_allowlist": []}',
  created_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz
);

CREATE TYPE app.role AS ENUM ('owner', 'admin', 'editor', 'viewer');

CREATE TABLE app.memberships (
  workspace_id uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE CASCADE,
  user_id      uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  role         app.role NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, user_id)
);
CREATE INDEX memberships_user ON app.memberships (user_id);

CREATE TABLE app.invitations (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE CASCADE,
  email        citext NOT NULL,
  role         app.role NOT NULL,
  token_hash   text UNIQUE NOT NULL,
  expires_at   timestamptz NOT NULL,
  accepted_at  timestamptz,
  invited_by   uuid REFERENCES app.users(id) ON DELETE SET NULL,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.api_tokens (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE CASCADE,
  user_id      uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  name         text NOT NULL,
  prefix       text NOT NULL,
  token_hash   text NOT NULL,
  scopes       text[] NOT NULL,
  expires_at   timestamptz,
  last_used_at timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  revoked_at   timestamptz
);

-- ===== Jobs (ADR-2: Postgres as the queue) =================================
CREATE TABLE app.jobs (
  id              bigserial PRIMARY KEY,
  workspace_id    uuid,
  connection_id   uuid,
  job_type        text NOT NULL,
  payload         jsonb NOT NULL DEFAULT '{}',
  idempotency_key text UNIQUE,
  status          text NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
  priority        int NOT NULL DEFAULT 50,
  run_at          timestamptz NOT NULL DEFAULT now(),
  attempts        int NOT NULL DEFAULT 0,
  max_attempts    int NOT NULL DEFAULT 3,
  timeout_s       int NOT NULL DEFAULT 60,
  locked_by       text,
  locked_at       timestamptz,
  heartbeat_at    timestamptz,
  last_error      text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz
);
CREATE INDEX jobs_ready ON app.jobs (priority DESC, run_at) WHERE status = 'queued';
CREATE INDEX jobs_running ON app.jobs (heartbeat_at) WHERE status = 'running';

-- One row per connection x job type. FK to app.connections is added in migration 0002.
CREATE TABLE app.schedules (
  connection_id uuid NOT NULL,
  job_type      text NOT NULL,
  interval_s    int NOT NULL CHECK (interval_s >= 60),
  next_run_at   timestamptz NOT NULL,
  PRIMARY KEY (connection_id, job_type)
);

-- ===== Audit ================================================================
CREATE TABLE app.audit_log (
  id             bigserial PRIMARY KEY,
  workspace_id   uuid,
  actor_user_id  uuid,
  actor_token_id uuid,
  action         text NOT NULL,
  target_type    text,
  target_id      text,
  ip             inet,
  user_agent     text,
  request_id     text,
  metadata       jsonb NOT NULL DEFAULT '{}',
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_ws ON app.audit_log (workspace_id, created_at DESC);
"""

DOWNGRADE = """
DROP TABLE IF EXISTS app.audit_log, app.schedules, app.jobs, app.api_tokens, app.invitations,
  app.memberships, app.workspaces, app.login_attempts, app.user_tokens, app.sessions,
  app.oauth_identities, app.users;
DROP TYPE IF EXISTS app.role;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
