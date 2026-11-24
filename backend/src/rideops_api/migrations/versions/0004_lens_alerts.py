"""Lens alerts: rules, encrypted notification channels, alert events, in-app notifications,
maintenance windows and incidents.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-24

Design: docs/ReContruction/lens-system-design.md §12 (alerts) and §17. Deviation: incidents live in
`app.incidents` (workspace-scoped) instead of the RideOps `ops.incidents` table, because the Lens
metadata database can be separate from the RideOps warehouse (DATABASE_URL).
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TENANT_TABLES = ("alert_rules", "notification_channels", "alert_events", "notifications", "maintenance_windows",
                 "incidents")  # fmt: skip

UPGRADE = """
CREATE TABLE app.notification_channels (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE CASCADE,
  kind         text NOT NULL CHECK (kind IN ('email', 'webhook', 'slack', 'inapp')),
  name         text NOT NULL,
  kek_version  int,
  wrapped_dek  bytea,
  nonce        bytea,
  config_enc   bytea,           -- AES-GCM ciphertext of {url, secret, emails}; null for in-app
  summary      text,            -- non-secret display text (e.g. "hooks.slack.com/…", "2 recipients")
  is_verified  boolean NOT NULL DEFAULT false,
  created_by   uuid,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.alert_rules (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE CASCADE,
  connection_id uuid REFERENCES app.connections(id) ON DELETE CASCADE,  -- null = every connection
  name          text NOT NULL,
  rule_type     text NOT NULL CHECK (rule_type IN ('status', 'metric', 'freshness', 'row_drop', 'schema_change',
                                                     'privilege')),
  condition     jsonb NOT NULL,
  severity      text NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
  channels      uuid[] NOT NULL DEFAULT '{}',
  runbook_url   text,
  is_enabled    boolean NOT NULL DEFAULT true,
  is_default    boolean NOT NULL DEFAULT false,
  created_by    uuid,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX alert_rules_ws ON app.alert_rules (workspace_id);

CREATE TABLE app.incidents (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE CASCADE,
  connection_id uuid REFERENCES app.connections(id) ON DELETE SET NULL,
  title        text NOT NULL,
  severity     text NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
  status       text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'acknowledged', 'resolved')),
  runbook_url  text,
  opened_at    timestamptz NOT NULL DEFAULT now(),
  acknowledged_at timestamptz,
  resolved_at  timestamptz,
  created_by   uuid
);

CREATE TABLE app.incident_notes (
  id          bigserial PRIMARY KEY,
  incident_id uuid NOT NULL REFERENCES app.incidents(id) ON DELETE CASCADE,
  author_id   uuid,
  kind        text NOT NULL DEFAULT 'note' CHECK (kind IN ('note', 'event')),
  body        text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.alert_events (
  id              bigserial PRIMARY KEY,
  workspace_id    uuid NOT NULL,
  rule_id         uuid NOT NULL REFERENCES app.alert_rules(id) ON DELETE CASCADE,
  connection_id   uuid REFERENCES app.connections(id) ON DELETE CASCADE,
  dedup_key       text NOT NULL,
  status          text NOT NULL CHECK (status IN ('firing', 'acknowledged', 'resolved', 'muted')),
  severity        text NOT NULL,
  opened_at       timestamptz NOT NULL DEFAULT now(),
  acknowledged_at timestamptz,
  acknowledged_by uuid,
  resolved_at     timestamptz,
  muted_until     timestamptz,
  ok_streak       int NOT NULL DEFAULT 0,
  last_value      jsonb,
  summary         text NOT NULL,
  incident_id     uuid REFERENCES app.incidents(id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX alert_open_dedup ON app.alert_events (dedup_key)
  WHERE status IN ('firing', 'acknowledged', 'muted');
CREATE INDEX alert_events_ws ON app.alert_events (workspace_id, opened_at DESC);

CREATE TABLE app.notifications (
  id           bigserial PRIMARY KEY,
  workspace_id uuid NOT NULL,
  user_id      uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  kind         text NOT NULL,
  title        text NOT NULL,
  body         text,
  link         text,
  read_at      timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX notifications_user ON app.notifications (user_id, created_at DESC);

CREATE TABLE app.maintenance_windows (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE CASCADE,
  connection_id uuid REFERENCES app.connections(id) ON DELETE CASCADE,  -- null = whole workspace
  starts_at     timestamptz NOT NULL,
  ends_at       timestamptz NOT NULL CHECK (ends_at > starts_at),
  reason        text,
  created_by    uuid
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
DROP TABLE IF EXISTS app.maintenance_windows, app.notifications, app.alert_events, app.incident_notes,
  app.incidents, app.alert_rules, app.notification_channels;
"""


def upgrade() -> None:
    op.execute(UPGRADE)
    for table in TENANT_TABLES:
        op.execute(POLICY.format(t=table))


def downgrade() -> None:
    op.execute(DOWNGRADE)
