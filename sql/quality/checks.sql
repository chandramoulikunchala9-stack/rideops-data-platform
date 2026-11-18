-- Data-quality checks. Each SELECT returns one check result:
--   check_name, entity, check_type, failed_rows, total_rows, threshold, details
-- Status is decided by rideops.quality: PASS if failed_rows = 0, WARN if the failure rate is
-- within `threshold`, FAIL above it.
-- check_type: validity | uniqueness | completeness | referential | freshness | volume | schema

WITH
booking_keys AS (
    SELECT
        (SELECT count(*) FROM raw.bookings)                         AS raw_rows,
        (SELECT count(DISTINCT booking_id) FROM raw.bookings)       AS distinct_keys
),
daily AS (
    SELECT booking_date, count(*) AS bookings
    FROM core.bookings
    GROUP BY booking_date
),
calendar AS (
    SELECT generate_series(min(booking_date), max(booking_date), interval '1 day')::date AS day
    FROM daily
),
volume AS (
    SELECT
        c.day,
        coalesce(d.bookings, 0) AS bookings,
        avg(coalesce(d.bookings, 0)) OVER (
            ORDER BY c.day ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING
        ) AS trailing_avg
    FROM calendar c
    LEFT JOIN daily d ON d.booking_date = c.day
)

SELECT 'bookings_duplicate_deliveries' AS check_name, 'bookings' AS entity,
       'uniqueness' AS check_type, raw_rows - distinct_keys AS failed_rows,
       raw_rows AS total_rows, 0.02 AS threshold,
       jsonb_build_object('distinct_booking_ids', distinct_keys) AS details
FROM booking_keys

UNION ALL
SELECT 'bookings_primary_key_unique', 'bookings', 'uniqueness',
       count(*) - count(DISTINCT booking_id), count(*), 0.0, '{}'::jsonb
FROM core.bookings

UNION ALL
SELECT 'bookings_unknown_driver', 'bookings', 'referential',
       count(*) FILTER (WHERE 'unknown_driver_id' = ANY (dq_flags)), count(*), 0.01, '{}'::jsonb
FROM core.bookings

UNION ALL
SELECT 'bookings_missing_payment_method', 'bookings', 'completeness',
       count(*) FILTER (WHERE payment_method IS NULL), count(*), 0.02, '{}'::jsonb
FROM core.bookings

UNION ALL
SELECT 'bookings_reject_reasons', 'bookings', 'validity',
       -- sum(n), not count(*): the subquery has one row per *reason*
       coalesce(sum(n), 0), (SELECT count(*) FROM core.bookings) + coalesce(sum(n), 0), 0.02,
       coalesce(jsonb_object_agg(reason, n), '{}'::jsonb)
FROM (
    SELECT reason, count(*) AS n
    FROM core.rejected_records
    WHERE entity = 'bookings'
    GROUP BY reason
) AS reasons

UNION ALL
SELECT 'customers_missing_email', 'customers', 'completeness',
       count(*) FILTER (WHERE email IS NULL), count(*), 0.05, '{}'::jsonb
FROM core.customers

UNION ALL
SELECT 'customers_unknown_city', 'customers', 'validity',
       count(*) FILTER (WHERE 'unknown_city' = ANY (dq_flags)), count(*), 0.01, '{}'::jsonb
FROM core.customers

UNION ALL
SELECT 'drivers_rating_out_of_range', 'drivers', 'validity',
       count(*) FILTER (WHERE 'rating_out_of_range' = ANY (dq_flags)), count(*), 0.01, '{}'::jsonb
FROM core.drivers

UNION ALL
SELECT 'drivers_unknown_vehicle', 'drivers', 'referential',
       count(*) FILTER (WHERE 'unknown_vehicle_id' = ANY (dq_flags)), count(*), 0.01, '{}'::jsonb
FROM core.drivers

UNION ALL
SELECT 'payments_orphan_booking', 'payments', 'referential',
       count(*) FILTER (WHERE reason = 'unknown booking_id'),
       (SELECT count(*) FROM core.payments) + count(*), 0.02, '{}'::jsonb
FROM core.rejected_records
WHERE entity = 'payments'

UNION ALL
SELECT 'payments_failed_charge_rate', 'payments', 'validity',
       count(*) FILTER (WHERE status = 'failed'), count(*), 0.10, '{}'::jsonb
FROM core.payments
WHERE transaction_type = 'charge'

UNION ALL
SELECT 'cloud_costs_untagged', 'cloud_costs', 'completeness',
       count(*) FILTER (WHERE workload IS NULL), count(*), 0.05,
       jsonb_build_object('untagged_cost_usd', coalesce(sum(cost_usd) FILTER (WHERE workload IS NULL), 0))
FROM ops.cloud_costs

UNION ALL
SELECT 'cloud_costs_not_reconciling', 'cloud_costs', 'validity',
       count(*) FILTER (WHERE reason = 'cost does not reconcile'),
       (SELECT count(*) FROM ops.cloud_costs) + count(*), 0.01, '{}'::jsonb
FROM core.rejected_records
WHERE entity = 'cloud_costs'

UNION ALL
-- Missing expected data: calendar days inside the data range with no bookings at all.
SELECT 'bookings_missing_days', 'bookings', 'volume',
       count(*) FILTER (WHERE bookings = 0), count(*), 0.0,
       coalesce(jsonb_agg(day) FILTER (WHERE bookings = 0), '[]'::jsonb)
FROM volume

UNION ALL
-- Volume anomaly: a day with less than half its trailing 7-day average.
SELECT 'bookings_volume_drop', 'bookings', 'volume',
       count(*) FILTER (WHERE bookings < 0.5 * trailing_avg), count(*), 0.0,
       coalesce(jsonb_agg(day) FILTER (WHERE bookings < 0.5 * trailing_avg), '[]'::jsonb)
FROM volume
WHERE trailing_avg IS NOT NULL

UNION ALL
-- Freshness: the platform must have loaded new source files within the last 26 hours.
SELECT 'ingestion_freshness', 'all', 'freshness',
       CASE WHEN max(loaded_at) > now() - interval '26 hours' THEN 0 ELSE 1 END, 1, 0.0,
       jsonb_build_object(
           'last_loaded_at', max(loaded_at),
           'latest_booking_at', (SELECT max(requested_at) FROM core.bookings)
       )
FROM ops.ingestion_log
WHERE status = 'loaded'

UNION ALL
-- Schema mismatch: landing files rejected because their columns did not match the contract.
SELECT 'landing_schema_mismatch', 'all', 'schema',
       count(*) FILTER (WHERE status = 'failed' AND error_message LIKE 'Schema mismatch%'),
       count(*), 0.0,
       coalesce(
           jsonb_agg(file_path) FILTER (WHERE status = 'failed' AND error_message LIKE 'Schema mismatch%'),
           '[]'::jsonb
       )
FROM ops.ingestion_log;
