-- raw -> core for facts: bookings and payments. Same pattern as 10_core_dimensions.sql.
-- Facts are rejected (quarantined) when a required reference or amount cannot be trusted.

-- ============================================================================ bookings
CREATE TEMP TABLE stg_bookings ON COMMIT DROP AS
WITH batch AS (
    SELECT * FROM raw.bookings WHERE _loaded_at > ops.watermark('bookings')
),
typed AS (
    SELECT
        trim(booking_id)                        AS booking_id,
        nullif(trim(customer_id), '')           AS customer_id,
        nullif(trim(driver_id), '')             AS driver_id,
        nullif(trim(vehicle_id), '')            AS vehicle_id,
        upper(trim(city))                       AS city,
        nullif(lower(trim(vehicle_type)), '')   AS vehicle_type,
        lower(trim(status))                     AS status,       -- ' COMPLETED ' -> 'completed'
        core.parse_ts(requested_at)             AS requested_at,
        core.is_legacy_ts(requested_at)         AS requested_was_legacy,
        core.parse_ts(accepted_at)              AS accepted_at,
        core.parse_ts(pickup_at)                AS pickup_at,
        core.parse_ts(dropoff_at)               AS dropoff_at,
        core.parse_ts(cancelled_at)             AS cancelled_at,
        nullif(trim(cancelled_by), '')          AS cancelled_by,
        nullif(trim(cancellation_reason), '')   AS cancellation_reason,
        core.to_num(distance_km)                AS distance_km,
        core.to_num(duration_min)               AS duration_min,
        core.to_num(surge_multiplier)           AS surge_multiplier,
        core.to_num(fare_amount)                AS fare_amount,
        upper(trim(currency))                   AS currency,
        nullif(lower(trim(payment_method)), '') AS payment_method,
        core.parse_ts(updated_at)               AS updated_at,
        status <> lower(trim(status))           AS status_was_unnormalised,
        to_jsonb(b) - '_loaded_at'              AS raw_record,
        _source_file,
        _loaded_at
    FROM batch AS b
),
checked AS (
    SELECT
        t.*,
        array_remove(ARRAY[
            CASE WHEN t.requested_was_legacy THEN 'legacy_timestamp_standardised' END,
            CASE WHEN t.status_was_unnormalised THEN 'status_normalised' END,
            CASE WHEN t.driver_id IS NOT NULL AND d.driver_id IS NULL THEN 'unknown_driver_id' END,
            CASE WHEN t.payment_method IS NULL THEN 'missing_payment_method' END,
            CASE WHEN t.distance_km IS NULL THEN 'missing_distance' END,
            CASE WHEN t.surge_multiplier IS NULL THEN 'missing_surge_multiplier' END
        ], NULL) AS dq_flags,
        CASE
            WHEN t.booking_id IS NULL OR t.booking_id !~ '^BKG-\d{7}$' THEN 'invalid booking_id'
            WHEN t.requested_at IS NULL OR t.updated_at IS NULL THEN 'unparseable timestamp'
            WHEN t.customer_id IS NULL THEN 'missing customer_id'
            WHEN cu.customer_id IS NULL THEN 'unknown customer_id'
            WHEN ci.city_code IS NULL THEN 'unknown city'
            WHEN t.status NOT IN ('completed', 'cancelled', 'no_driver_found', 'in_progress')
                THEN 'invalid status'
            WHEN t.fare_amount IS NULL OR t.fare_amount < 0 THEN 'invalid fare_amount'
            WHEN t.dropoff_at < t.pickup_at THEN 'dropoff before pickup'
        END AS reject_reason
    FROM typed t
    LEFT JOIN core.customers cu ON cu.customer_id = t.customer_id
    LEFT JOIN core.drivers d ON d.driver_id = t.driver_id
    LEFT JOIN core.cities ci ON ci.city_code = t.city
)
SELECT
    *,
    row_number() OVER (
        PARTITION BY booking_id ORDER BY updated_at DESC NULLS LAST, _loaded_at DESC
    ) AS version_rank
FROM checked;

INSERT INTO core.rejected_records (entity, record_key, reason, raw_record, source_file)
SELECT 'bookings', booking_id, reject_reason, raw_record, _source_file
FROM stg_bookings
WHERE reject_reason IS NOT NULL AND version_rank = 1;

MERGE INTO core.bookings AS t
USING (SELECT * FROM stg_bookings WHERE reject_reason IS NULL AND version_rank = 1) AS s
    ON t.booking_id = s.booking_id
WHEN MATCHED AND s.updated_at > t.updated_at THEN UPDATE SET
    customer_id = s.customer_id, driver_id = s.driver_id, vehicle_id = s.vehicle_id,
    city = s.city, vehicle_type = s.vehicle_type, status = s.status,
    requested_at = s.requested_at, accepted_at = s.accepted_at, pickup_at = s.pickup_at,
    dropoff_at = s.dropoff_at, cancelled_at = s.cancelled_at, cancelled_by = s.cancelled_by,
    cancellation_reason = s.cancellation_reason, distance_km = s.distance_km,
    duration_min = s.duration_min, surge_multiplier = s.surge_multiplier,
    fare_amount = s.fare_amount, currency = s.currency, payment_method = s.payment_method,
    updated_at = s.updated_at, dq_flags = s.dq_flags, _loaded_at = s._loaded_at
WHEN NOT MATCHED THEN INSERT (
    booking_id, customer_id, driver_id, vehicle_id, city, vehicle_type, status, requested_at,
    accepted_at, pickup_at, dropoff_at, cancelled_at, cancelled_by, cancellation_reason,
    distance_km, duration_min, surge_multiplier, fare_amount, currency, payment_method,
    updated_at, dq_flags, _loaded_at
) VALUES (
    s.booking_id, s.customer_id, s.driver_id, s.vehicle_id, s.city, s.vehicle_type, s.status,
    s.requested_at, s.accepted_at, s.pickup_at, s.dropoff_at, s.cancelled_at, s.cancelled_by,
    s.cancellation_reason, s.distance_km, s.duration_min, s.surge_multiplier, s.fare_amount,
    s.currency, s.payment_method, s.updated_at, s.dq_flags, s._loaded_at
);

INSERT INTO ops.transform_watermarks (entity, last_loaded_at)
SELECT 'bookings', max(_loaded_at) FROM stg_bookings HAVING max(_loaded_at) IS NOT NULL
ON CONFLICT (entity) DO UPDATE
    SET last_loaded_at = excluded.last_loaded_at, updated_at = now();

-- ============================================================================ payments
-- Payment rows are immutable events: an exact re-delivery has the same payment_id, so
-- duplicates collapse to one row and are never updated.
CREATE TEMP TABLE stg_payments ON COMMIT DROP AS
WITH batch AS (
    SELECT doc, _source_file, _loaded_at
    FROM raw.payments
    WHERE _loaded_at > ops.watermark('payments')
),
typed AS (
    SELECT
        trim(doc ->> 'payment_id')                  AS payment_id,
        trim(doc ->> 'booking_id')                  AS booking_id,
        nullif(trim(doc ->> 'customer_id'), '')     AS customer_id,
        lower(trim(doc ->> 'transaction_type'))     AS transaction_type,
        core.to_num(doc ->> 'attempt_number')::int  AS attempt_number,
        core.to_num(doc ->> 'amount')               AS amount,
        upper(trim(doc ->> 'currency'))             AS currency,
        nullif(lower(trim(doc ->> 'method')), '')   AS method,
        nullif(trim(doc ->> 'gateway'), '')         AS gateway,
        lower(trim(doc ->> 'status'))               AS status,
        nullif(trim(doc ->> 'failure_reason'), '')  AS failure_reason,
        core.parse_ts(doc ->> 'processed_at')       AS processed_at,
        doc                                         AS raw_record,
        _source_file,
        _loaded_at
    FROM batch
),
checked AS (
    SELECT
        t.*,
        array_remove(ARRAY[
            CASE WHEN t.method IS NULL THEN 'missing_method' END,
            CASE WHEN t.gateway IS NULL THEN 'missing_gateway' END
        ], NULL) AS dq_flags,
        CASE
            WHEN t.payment_id IS NULL OR t.payment_id !~ '^PAY-\d{8}$' THEN 'invalid payment_id'
            WHEN t.processed_at IS NULL THEN 'unparseable processed_at'
            WHEN b.booking_id IS NULL THEN 'unknown booking_id'
            WHEN t.amount IS NULL OR t.amount < 0 THEN 'invalid amount'
            WHEN t.currency <> 'INR' THEN 'unsupported currency'
            WHEN t.transaction_type NOT IN ('charge', 'refund') THEN 'invalid transaction_type'
            WHEN t.status NOT IN ('success', 'failed') THEN 'invalid status'
        END AS reject_reason
    FROM typed t
    LEFT JOIN core.bookings b ON b.booking_id = t.booking_id
)
SELECT
    *,
    row_number() OVER (PARTITION BY payment_id ORDER BY _loaded_at) AS version_rank
FROM checked;

INSERT INTO core.rejected_records (entity, record_key, reason, raw_record, source_file)
SELECT 'payments', payment_id, reject_reason, raw_record, _source_file
FROM stg_payments
WHERE reject_reason IS NOT NULL AND version_rank = 1;

INSERT INTO core.payments (
    payment_id, booking_id, customer_id, transaction_type, attempt_number, amount, currency,
    method, gateway, status, failure_reason, processed_at, dq_flags, _loaded_at
)
SELECT
    payment_id, booking_id, customer_id, transaction_type, attempt_number, amount, currency,
    method, gateway, status, failure_reason, processed_at, dq_flags, _loaded_at
FROM stg_payments
WHERE reject_reason IS NULL AND version_rank = 1
ON CONFLICT (payment_id) DO NOTHING;

INSERT INTO ops.transform_watermarks (entity, last_loaded_at)
SELECT 'payments', max(_loaded_at) FROM stg_payments HAVING max(_loaded_at) IS NOT NULL
ON CONFLICT (entity) DO UPDATE
    SET last_loaded_at = excluded.last_loaded_at, updated_at = now();
