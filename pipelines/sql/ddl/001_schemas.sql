-- Schemas and shared helper functions.
--   raw        landing files copied as-is (text / jsonb) plus load metadata
--   core       typed, validated, deduplicated tables (SQL "silver")
--   analytics  aggregates served by the API (SQL "gold")
--   ops        platform metadata: ingestion log, pipeline runs, data quality, costs, incidents

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS ops;

-- Parse the two timestamp formats found in source data into timestamptz (UTC).
-- Returns NULL for anything else, so callers can decide whether to reject the row.
CREATE OR REPLACE FUNCTION core.parse_ts(value text)
RETURNS timestamptz
LANGUAGE sql
STABLE
AS $$
    SELECT CASE
        WHEN value ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$'
            THEN value::timestamptz
        WHEN value ~ '^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$'
            THEN to_timestamp(value, 'DD/MM/YYYY HH24:MI')::timestamp AT TIME ZONE 'UTC'
    END
$$;

-- TRUE when a value uses the non-standard legacy format (used for data-quality flags).
CREATE OR REPLACE FUNCTION core.is_legacy_ts(value text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT coalesce(value ~ '^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$', false)
$$;

-- Safe numeric cast: NULL instead of an error for empty or non-numeric text.
CREATE OR REPLACE FUNCTION core.to_num(value text)
RETURNS numeric
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE WHEN trim(value) ~ '^-?\d+(\.\d+)?$' THEN trim(value)::numeric END
$$;
