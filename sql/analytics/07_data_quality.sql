-- Data-quality reporting (ad-hoc queries; automated checks live in sql/quality/checks.sql).

-- name: rejected_records_by_entity_and_reason
SELECT entity, reason, count(*) AS rows_rejected, min(rejected_at) AS first_seen
FROM core.rejected_records
GROUP BY entity, reason
ORDER BY entity, rows_rejected DESC;

-- name: dq_flag_frequency
-- How often each repaired field-level problem occurs (unnest the dq_flags arrays).
SELECT 'bookings' AS entity, flag, count(*) AS rows_flagged
FROM core.bookings, unnest(dq_flags) AS flag
GROUP BY flag
UNION ALL
SELECT 'customers', flag, count(*) FROM core.customers, unnest(dq_flags) AS flag GROUP BY flag
UNION ALL
SELECT 'drivers', flag, count(*) FROM core.drivers, unnest(dq_flags) AS flag GROUP BY flag
UNION ALL
SELECT 'vehicles', flag, count(*) FROM core.vehicles, unnest(dq_flags) AS flag GROUP BY flag
ORDER BY entity, rows_flagged DESC;

-- name: duplicate_keys_in_raw
-- Keys delivered more than once by the source, and how many versions arrived.
SELECT booking_id, count(*) AS versions, min(updated_at) AS first_version, max(updated_at) AS last_version
FROM raw.bookings
GROUP BY booking_id
HAVING count(*) > 1
ORDER BY versions DESC, booking_id
LIMIT 50;

-- name: latest_check_results
-- Most recent result of every data-quality check (DISTINCT ON keeps the first row per check).
SELECT DISTINCT ON (check_name)
    check_name, entity, check_type, status, failed_rows, total_rows, failure_rate, checked_at
FROM ops.data_quality_results
ORDER BY check_name, checked_at DESC;

-- name: null_rate_per_column
SELECT
    round(100.0 * count(*) FILTER (WHERE payment_method IS NULL) / count(*), 3)   AS payment_method_null_pct,
    round(100.0 * count(*) FILTER (WHERE distance_km IS NULL) / count(*), 3)      AS distance_null_pct,
    round(100.0 * count(*) FILTER (WHERE surge_multiplier IS NULL) / count(*), 3) AS surge_null_pct,
    round(100.0 * count(*) FILTER (WHERE driver_id IS NULL) / count(*), 3)        AS driver_null_pct
FROM core.bookings;
