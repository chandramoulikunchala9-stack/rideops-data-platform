-- Operational metadata used by monitoring, data-quality and cost features.

-- One row per landing file. Lets the loader skip files it has already loaded (incremental).
CREATE TABLE IF NOT EXISTS ops.ingestion_log (
    file_path      text        PRIMARY KEY,
    entity         text        NOT NULL,
    batch_id       text        NOT NULL,
    status         text        NOT NULL CHECK (status IN ('loaded', 'failed')),
    rows_loaded    integer     NOT NULL DEFAULT 0,
    file_bytes     bigint      NOT NULL,
    file_sha256    char(64)    NOT NULL,
    error_message  text,
    attempts       smallint    NOT NULL DEFAULT 1,
    loaded_at      timestamptz NOT NULL DEFAULT now()
);

-- High-water mark per entity for incremental raw -> core transforms.
CREATE TABLE IF NOT EXISTS ops.transform_watermarks (
    entity          text        PRIMARY KEY,
    last_loaded_at  timestamptz NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION ops.watermark(p_entity text)
RETURNS timestamptz
LANGUAGE sql
STABLE
AS $$
    SELECT coalesce(
        (SELECT last_loaded_at FROM ops.transform_watermarks WHERE entity = p_entity),
        '-infinity'::timestamptz
    )
$$;

-- Pipeline executions: simulated history (run_source = 'simulated_history') and real runs
-- recorded by rideops.monitoring.tracker (run_source = 'local_run').
CREATE TABLE IF NOT EXISTS ops.pipeline_runs (
    run_id            text        PRIMARY KEY,
    pipeline_name     text        NOT NULL,
    layer             text        NOT NULL,
    trigger           text        NOT NULL CHECK (trigger IN ('scheduled', 'retry', 'manual', 'api')),
    status            text        NOT NULL CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')),
    start_ts          timestamptz NOT NULL,
    end_ts            timestamptz,
    duration_sec      integer,
    records_read      bigint,
    records_written   bigint,
    records_rejected  bigint,
    retry_count       smallint    NOT NULL DEFAULT 0,
    error_message     text,
    run_source        text        NOT NULL,
    retry_of_run_id   text        REFERENCES ops.pipeline_runs,
    CHECK (end_ts IS NULL OR end_ts >= start_ts)
);
CREATE INDEX IF NOT EXISTS ix_pipeline_runs_name_start ON ops.pipeline_runs (pipeline_name, start_ts DESC);
CREATE INDEX IF NOT EXISTS ix_pipeline_runs_status ON ops.pipeline_runs (status) WHERE status <> 'SUCCESS';

CREATE TABLE IF NOT EXISTS ops.pipeline_run_logs (
    log_id     bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id     text        NOT NULL REFERENCES ops.pipeline_runs ON DELETE CASCADE,
    logged_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    level      text        NOT NULL CHECK (level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR')),
    message    text        NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pipeline_run_logs_run ON ops.pipeline_run_logs (run_id, logged_at);

-- Results of every data-quality check execution.
CREATE TABLE IF NOT EXISTS ops.data_quality_results (
    result_id     bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id        text          REFERENCES ops.pipeline_runs ON DELETE SET NULL,
    check_name    text          NOT NULL,
    entity        text          NOT NULL,
    check_type    text          NOT NULL,  -- completeness | uniqueness | validity | ...
    status        text          NOT NULL CHECK (status IN ('PASS', 'WARN', 'FAIL')),
    failed_rows   bigint        NOT NULL,
    total_rows    bigint        NOT NULL,
    failure_rate  numeric(9, 6) GENERATED ALWAYS AS (
        CASE WHEN total_rows = 0 THEN 0 ELSE failed_rows::numeric / total_rows END
    ) STORED,
    threshold     numeric(9, 6) NOT NULL,
    details       jsonb         NOT NULL DEFAULT '{}',
    checked_at    timestamptz   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_dq_results_checked ON ops.data_quality_results (checked_at DESC);

-- Cloud usage and cost. `source` separates simulated rows from real billing data.
CREATE TABLE IF NOT EXISTS ops.cloud_costs (
    cost_id         text           PRIMARY KEY,
    usage_date      date           NOT NULL,
    provider        text           NOT NULL,
    service         text           NOT NULL,
    workload        text,           -- NULL = untagged resource
    environment     text           NOT NULL,
    usage_type      text           NOT NULL,
    usage_quantity  numeric(14, 4) NOT NULL,
    usage_unit      text           NOT NULL,
    unit_price_usd  numeric(12, 6) NOT NULL,
    cost_usd        numeric(14, 4) NOT NULL,
    currency        char(3)        NOT NULL,
    source          text           NOT NULL
        CHECK (source IN ('simulated', 'aws_cur', 'databricks_system_tables')),
    _loaded_at      timestamptz    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cloud_costs_date ON ops.cloud_costs (usage_date);

-- Incidents and their resolution notes (production-support history).
CREATE TABLE IF NOT EXISTS ops.incidents (
    incident_id    text        PRIMARY KEY,
    title          text        NOT NULL,
    severity       text        NOT NULL CHECK (severity IN ('SEV1', 'SEV2', 'SEV3', 'SEV4')),
    status         text        NOT NULL CHECK (status IN ('OPEN', 'INVESTIGATING', 'RESOLVED')),
    pipeline_name  text,
    run_id         text        REFERENCES ops.pipeline_runs ON DELETE SET NULL,
    opened_at      timestamptz NOT NULL,
    resolved_at    timestamptz,
    root_cause     text,
    resolution     text,
    runbook        text
);

-- Alerts raised by monitoring rules (failed job, long-running, freshness, budget, ...).
CREATE TABLE IF NOT EXISTS ops.alerts (
    alert_id      bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rule          text        NOT NULL,
    severity      text        NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
    subject       text        NOT NULL,
    message       text        NOT NULL,
    details       jsonb       NOT NULL DEFAULT '{}',
    raised_at     timestamptz NOT NULL DEFAULT now(),
    acknowledged  boolean     NOT NULL DEFAULT false,
    UNIQUE (rule, subject)
);
