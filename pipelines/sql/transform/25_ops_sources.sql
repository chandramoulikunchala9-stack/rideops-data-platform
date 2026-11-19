-- raw -> ops for operational sources: simulated pipeline-run history and cloud costs.

-- ============================================================================ pipeline runs
CREATE TEMP TABLE stg_pipeline_runs ON COMMIT DROP AS
WITH typed AS (
    SELECT
        trim(doc ->> 'run_id')                          AS run_id,
        trim(doc ->> 'pipeline_name')                   AS pipeline_name,
        lower(trim(doc ->> 'layer'))                    AS layer,
        lower(trim(doc ->> 'trigger'))                  AS trigger,
        upper(trim(doc ->> 'status'))                   AS status,  -- 'running' -> 'RUNNING'
        core.parse_ts(doc ->> 'start_ts')               AS start_ts,
        core.parse_ts(doc ->> 'end_ts')                 AS end_ts,
        core.to_num(doc ->> 'duration_sec')::int        AS duration_sec,
        core.to_num(doc ->> 'records_read')::bigint     AS records_read,
        core.to_num(doc ->> 'records_written')::bigint  AS records_written,
        core.to_num(doc ->> 'records_rejected')::bigint AS records_rejected,
        coalesce(core.to_num(doc ->> 'retry_count')::int, 0) AS retry_count,
        nullif(doc ->> 'error_message', '')             AS error_message,
        doc ->> 'run_source'                            AS run_source,
        doc                                             AS raw_record,
        _source_file,
        _loaded_at
    FROM raw.pipeline_runs
    WHERE _loaded_at > ops.watermark('pipeline_runs')
)
SELECT
    *,
    CASE
        WHEN run_id IS NULL OR start_ts IS NULL THEN 'missing run_id or start_ts'
        WHEN status NOT IN ('RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED') THEN 'invalid status'
        WHEN end_ts < start_ts THEN 'end_ts before start_ts (clock skew)'
    END AS reject_reason,
    row_number() OVER (PARTITION BY run_id ORDER BY _loaded_at DESC) AS version_rank
FROM typed;

INSERT INTO core.rejected_records (entity, record_key, reason, raw_record, source_file)
SELECT 'pipeline_runs', run_id, reject_reason, raw_record, _source_file
FROM stg_pipeline_runs
WHERE reject_reason IS NOT NULL AND version_rank = 1;

INSERT INTO ops.pipeline_runs (
    run_id, pipeline_name, layer, trigger, status, start_ts, end_ts, duration_sec,
    records_read, records_written, records_rejected, retry_count, error_message, run_source
)
SELECT
    run_id, pipeline_name, layer, trigger, status, start_ts, end_ts, duration_sec,
    records_read, records_written, records_rejected, retry_count, error_message, run_source
FROM stg_pipeline_runs
WHERE reject_reason IS NULL AND version_rank = 1
ON CONFLICT (run_id) DO UPDATE SET
    status = excluded.status, end_ts = excluded.end_ts, duration_sec = excluded.duration_sec,
    records_written = excluded.records_written, records_rejected = excluded.records_rejected,
    error_message = excluded.error_message
WHERE ops.pipeline_runs.run_source = 'simulated_history';  -- never overwrite real runs

INSERT INTO ops.transform_watermarks (entity, last_loaded_at)
SELECT 'pipeline_runs', max(_loaded_at) FROM stg_pipeline_runs HAVING max(_loaded_at) IS NOT NULL
ON CONFLICT (entity) DO UPDATE
    SET last_loaded_at = excluded.last_loaded_at, updated_at = now();

-- ============================================================================ cloud costs
CREATE TEMP TABLE stg_cloud_costs ON COMMIT DROP AS
WITH typed AS (
    SELECT
        trim(cost_id)                   AS cost_id,
        usage_date::date                AS usage_date,
        lower(trim(provider))           AS provider,
        trim(service)                   AS service,
        nullif(trim(workload), '')      AS workload,
        lower(trim(environment))        AS environment,
        trim(usage_type)                AS usage_type,
        core.to_num(usage_quantity)     AS usage_quantity,
        trim(usage_unit)                AS usage_unit,
        core.to_num(unit_price_usd)     AS unit_price_usd,
        core.to_num(cost_usd)           AS cost_usd,
        upper(trim(currency))           AS currency,
        lower(trim(source))             AS source,
        to_jsonb(r) - '_loaded_at'      AS raw_record,
        _source_file,
        _loaded_at
    FROM raw.cloud_costs AS r
    WHERE _loaded_at > ops.watermark('cloud_costs')
)
SELECT
    *,
    CASE
        WHEN cost_id IS NULL OR usage_date IS NULL THEN 'missing cost_id or usage_date'
        WHEN usage_quantity IS NULL OR unit_price_usd IS NULL OR cost_usd IS NULL
            THEN 'missing usage, price or cost'
        -- a line item whose cost does not equal quantity x price cannot be trusted
        WHEN abs(cost_usd - usage_quantity * unit_price_usd) > 0.01 THEN 'cost does not reconcile'
        WHEN source NOT IN ('simulated', 'aws_cur', 'databricks_system_tables')
            THEN 'unknown cost source'
    END AS reject_reason,
    row_number() OVER (PARTITION BY cost_id ORDER BY _loaded_at DESC) AS version_rank
FROM typed;

INSERT INTO core.rejected_records (entity, record_key, reason, raw_record, source_file)
SELECT 'cloud_costs', cost_id, reject_reason, raw_record, _source_file
FROM stg_cloud_costs
WHERE reject_reason IS NOT NULL AND version_rank = 1;

INSERT INTO ops.cloud_costs (
    cost_id, usage_date, provider, service, workload, environment, usage_type, usage_quantity,
    usage_unit, unit_price_usd, cost_usd, currency, source, _loaded_at
)
SELECT
    cost_id, usage_date, provider, service, workload, environment, usage_type, usage_quantity,
    usage_unit, unit_price_usd, cost_usd, currency, source, _loaded_at
FROM stg_cloud_costs
WHERE reject_reason IS NULL AND version_rank = 1
ON CONFLICT (cost_id) DO NOTHING;  -- billing re-deliveries are exact duplicates

INSERT INTO ops.transform_watermarks (entity, last_loaded_at)
SELECT 'cloud_costs', max(_loaded_at) FROM stg_cloud_costs HAVING max(_loaded_at) IS NOT NULL
ON CONFLICT (entity) DO UPDATE
    SET last_loaded_at = excluded.last_loaded_at, updated_at = now();
