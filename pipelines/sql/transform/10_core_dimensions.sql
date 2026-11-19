-- raw -> core for dimension entities: vehicles, drivers, customers.
--
-- Pattern used for every entity (runs inside one transaction):
--   1. stage   : rows loaded since the entity's watermark, cast to proper types, validated
--   2. version : ROW_NUMBER() per key, latest updated_at first; only version 1 is used
--   3. reject  : version-1 rows that cannot be trusted go to core.rejected_records
--   4. merge   : MERGE upserts the rest (an older re-delivery never overwrites newer data)
--   5. advance : move the watermark so the next run only sees new rows

-- ============================================================================ vehicles
CREATE TEMP TABLE stg_vehicles ON COMMIT DROP AS
WITH batch AS (
    SELECT doc, _source_file, _loaded_at
    FROM raw.vehicles
    WHERE _loaded_at > ops.watermark('vehicles')
),
typed AS (
    SELECT
        trim(doc ->> 'vehicle_id')                      AS vehicle_id,
        trim(doc ->> 'registration_number')             AS registration_number,
        lower(trim(doc ->> 'vehicle_type'))             AS vehicle_type,
        nullif(trim(doc ->> 'make'), '')                AS make,
        nullif(trim(doc ->> 'model'), '')               AS model,
        core.to_num(doc ->> 'manufacture_year')::int    AS manufacture_year_raw,
        core.to_num(doc ->> 'seats')::int               AS seats_raw,
        upper(trim(doc ->> 'city'))                     AS city_raw,
        (doc ->> 'is_active')::boolean                  AS is_active,
        core.parse_ts(doc ->> 'updated_at')             AS updated_at,
        doc                                             AS raw_record,
        _source_file,
        _loaded_at
    FROM batch
),
checked AS (
    SELECT
        t.*,
        CASE WHEN t.manufacture_year_raw BETWEEN 1990 AND extract(year FROM now())::int + 1
             THEN t.manufacture_year_raw END             AS manufacture_year,
        CASE WHEN t.seats_raw > 0 THEN t.seats_raw END  AS seats,
        c.city_code                                     AS city,
        array_remove(ARRAY[
            CASE WHEN t.manufacture_year_raw IS NOT NULL
                  AND t.manufacture_year_raw NOT BETWEEN 1990 AND extract(year FROM now())::int + 1
                 THEN 'invalid_manufacture_year' END,
            CASE WHEN t.seats_raw IS NOT NULL AND t.seats_raw <= 0 THEN 'invalid_seats' END,
            CASE WHEN t.make IS NULL OR t.model IS NULL THEN 'missing_make_or_model' END,
            CASE WHEN t.manufacture_year_raw IS NULL THEN 'missing_manufacture_year' END
        ], NULL)                                        AS dq_flags,
        CASE
            WHEN t.vehicle_id IS NULL OR t.vehicle_id !~ '^VEH-\d{6}$' THEN 'invalid vehicle_id'
            WHEN t.updated_at IS NULL THEN 'unparseable updated_at'
            WHEN vt.vehicle_type IS NULL THEN 'unknown vehicle_type'
        END                                             AS reject_reason
    FROM typed t
    LEFT JOIN core.cities c ON c.city_code = t.city_raw
    LEFT JOIN core.vehicle_types vt ON vt.vehicle_type = t.vehicle_type
)
SELECT
    *,
    row_number() OVER (
        PARTITION BY vehicle_id ORDER BY updated_at DESC NULLS LAST, _loaded_at DESC
    ) AS version_rank
FROM checked;

INSERT INTO core.rejected_records (entity, record_key, reason, raw_record, source_file)
SELECT 'vehicles', vehicle_id, reject_reason, raw_record, _source_file
FROM stg_vehicles
WHERE reject_reason IS NOT NULL AND version_rank = 1;

MERGE INTO core.vehicles AS t
USING (SELECT * FROM stg_vehicles WHERE reject_reason IS NULL AND version_rank = 1) AS s
    ON t.vehicle_id = s.vehicle_id
WHEN MATCHED AND s.updated_at > t.updated_at THEN UPDATE SET
    registration_number = s.registration_number, vehicle_type = s.vehicle_type, make = s.make,
    model = s.model, manufacture_year = s.manufacture_year, seats = s.seats, city = s.city,
    is_active = s.is_active, updated_at = s.updated_at, dq_flags = s.dq_flags,
    _loaded_at = s._loaded_at
WHEN NOT MATCHED THEN INSERT (
    vehicle_id, registration_number, vehicle_type, make, model, manufacture_year, seats, city,
    is_active, updated_at, dq_flags, _loaded_at
) VALUES (
    s.vehicle_id, s.registration_number, s.vehicle_type, s.make, s.model, s.manufacture_year,
    s.seats, s.city, s.is_active, s.updated_at, s.dq_flags, s._loaded_at
);

INSERT INTO ops.transform_watermarks (entity, last_loaded_at)
SELECT 'vehicles', max(_loaded_at) FROM stg_vehicles HAVING max(_loaded_at) IS NOT NULL
ON CONFLICT (entity) DO UPDATE
    SET last_loaded_at = excluded.last_loaded_at, updated_at = now();

-- ============================================================================ drivers
CREATE TEMP TABLE stg_drivers ON COMMIT DROP AS
WITH batch AS (
    SELECT * FROM raw.drivers WHERE _loaded_at > ops.watermark('drivers')
),
typed AS (
    SELECT
        trim(driver_id)                         AS driver_id,
        nullif(trim(full_name), '')             AS full_name,
        nullif(trim(phone), '')                 AS phone,
        upper(trim(city))                       AS city_raw,
        nullif(trim(license_number), '')        AS license_number,
        nullif(trim(vehicle_id), '')            AS vehicle_id_raw,
        core.to_num(rating)                     AS rating_raw,
        lower(trim(status))                     AS status,
        core.parse_ts(joined_at)                AS joined_at,
        core.parse_ts(updated_at)               AS updated_at,
        to_jsonb(b) - '_loaded_at'              AS raw_record,
        _source_file,
        _loaded_at
    FROM batch AS b
),
checked AS (
    SELECT
        t.*,
        c.city_code                                             AS city,
        v.vehicle_id                                            AS vehicle_id,
        CASE WHEN t.rating_raw BETWEEN 1 AND 5 THEN t.rating_raw END AS rating,
        array_remove(ARRAY[
            CASE WHEN t.rating_raw IS NOT NULL AND t.rating_raw NOT BETWEEN 1 AND 5
                 THEN 'rating_out_of_range' END,
            CASE WHEN t.rating_raw IS NULL THEN 'missing_rating' END,
            CASE WHEN t.license_number IS NULL THEN 'missing_license_number' END,
            CASE WHEN t.vehicle_id_raw IS NOT NULL AND v.vehicle_id IS NULL
                 THEN 'unknown_vehicle_id' END,
            CASE WHEN t.vehicle_id_raw IS NULL THEN 'missing_vehicle_id' END,
            CASE WHEN t.phone IS NULL THEN 'missing_phone' END
        ], NULL)                                                AS dq_flags,
        CASE
            WHEN t.driver_id IS NULL OR t.driver_id !~ '^DRV-\d{6}$' THEN 'invalid driver_id'
            WHEN t.joined_at IS NULL OR t.updated_at IS NULL THEN 'unparseable timestamp'
            WHEN t.status NOT IN ('active', 'inactive', 'suspended') THEN 'invalid status'
        END                                                     AS reject_reason
    FROM typed t
    LEFT JOIN core.cities c ON c.city_code = t.city_raw
    LEFT JOIN core.vehicles v ON v.vehicle_id = t.vehicle_id_raw
)
SELECT
    *,
    row_number() OVER (
        PARTITION BY driver_id ORDER BY updated_at DESC NULLS LAST, _loaded_at DESC
    ) AS version_rank
FROM checked;

INSERT INTO core.rejected_records (entity, record_key, reason, raw_record, source_file)
SELECT 'drivers', driver_id, reject_reason, raw_record, _source_file
FROM stg_drivers
WHERE reject_reason IS NOT NULL AND version_rank = 1;

MERGE INTO core.drivers AS t
USING (SELECT * FROM stg_drivers WHERE reject_reason IS NULL AND version_rank = 1) AS s
    ON t.driver_id = s.driver_id
WHEN MATCHED AND s.updated_at > t.updated_at THEN UPDATE SET
    full_name = s.full_name, phone = s.phone, city = s.city, license_number = s.license_number,
    vehicle_id = s.vehicle_id, rating = s.rating, status = s.status, joined_at = s.joined_at,
    updated_at = s.updated_at, dq_flags = s.dq_flags, _loaded_at = s._loaded_at
WHEN NOT MATCHED THEN INSERT (
    driver_id, full_name, phone, city, license_number, vehicle_id, rating, status, joined_at,
    updated_at, dq_flags, _loaded_at
) VALUES (
    s.driver_id, s.full_name, s.phone, s.city, s.license_number, s.vehicle_id, s.rating,
    s.status, s.joined_at, s.updated_at, s.dq_flags, s._loaded_at
);

INSERT INTO ops.transform_watermarks (entity, last_loaded_at)
SELECT 'drivers', max(_loaded_at) FROM stg_drivers HAVING max(_loaded_at) IS NOT NULL
ON CONFLICT (entity) DO UPDATE
    SET last_loaded_at = excluded.last_loaded_at, updated_at = now();

-- ============================================================================ customers
CREATE TEMP TABLE stg_customers ON COMMIT DROP AS
WITH batch AS (
    SELECT * FROM raw.customers WHERE _loaded_at > ops.watermark('customers')
),
typed AS (
    SELECT
        trim(customer_id)                       AS customer_id,
        nullif(trim(full_name), '')             AS full_name,
        nullif(lower(trim(email)), '')          AS email_raw,
        nullif(trim(phone), '')                 AS phone,
        upper(trim(city))                       AS city_raw,
        nullif(lower(trim(loyalty_tier)), '')   AS loyalty_tier,
        core.parse_ts(signup_ts)                AS signup_ts,
        core.is_legacy_ts(signup_ts)            AS signup_was_legacy,
        core.parse_ts(updated_at)               AS updated_at,
        to_jsonb(b) - '_loaded_at'              AS raw_record,
        _source_file,
        _loaded_at
    FROM batch AS b
),
checked AS (
    SELECT
        t.*,
        c.city_code AS city,
        CASE WHEN t.email_raw ~ '^[^@\s]+@[^@\s]+\.[a-z]{2,}$' THEN t.email_raw END AS email,
        array_remove(ARRAY[
            CASE WHEN t.email_raw IS NOT NULL AND t.email_raw !~ '^[^@\s]+@[^@\s]+\.[a-z]{2,}$'
                 THEN 'malformed_email' END,
            CASE WHEN t.email_raw IS NULL THEN 'missing_email' END,
            CASE WHEN t.phone IS NULL THEN 'missing_phone' END,
            CASE WHEN t.loyalty_tier IS NULL THEN 'missing_loyalty_tier' END,
            CASE WHEN c.city_code IS NULL THEN 'unknown_city' END,
            CASE WHEN t.signup_was_legacy THEN 'legacy_timestamp_standardised' END
        ], NULL) AS dq_flags,
        CASE
            WHEN t.customer_id IS NULL OR t.customer_id !~ '^CUS-\d{6}$' THEN 'invalid customer_id'
            WHEN t.signup_ts IS NULL OR t.updated_at IS NULL THEN 'unparseable timestamp'
        END AS reject_reason
    FROM typed t
    LEFT JOIN core.cities c ON c.city_code = t.city_raw
)
SELECT
    *,
    row_number() OVER (
        PARTITION BY customer_id ORDER BY updated_at DESC NULLS LAST, _loaded_at DESC
    ) AS version_rank
FROM checked;

INSERT INTO core.rejected_records (entity, record_key, reason, raw_record, source_file)
SELECT 'customers', customer_id, reject_reason, raw_record, _source_file
FROM stg_customers
WHERE reject_reason IS NOT NULL AND version_rank = 1;

MERGE INTO core.customers AS t
USING (SELECT * FROM stg_customers WHERE reject_reason IS NULL AND version_rank = 1) AS s
    ON t.customer_id = s.customer_id
WHEN MATCHED AND s.updated_at > t.updated_at THEN UPDATE SET
    full_name = s.full_name, email = s.email, phone = s.phone, city = s.city,
    loyalty_tier = s.loyalty_tier, signup_ts = s.signup_ts, updated_at = s.updated_at,
    dq_flags = s.dq_flags, _loaded_at = s._loaded_at
WHEN NOT MATCHED THEN INSERT (
    customer_id, full_name, email, phone, city, loyalty_tier, signup_ts, updated_at, dq_flags,
    _loaded_at
) VALUES (
    s.customer_id, s.full_name, s.email, s.phone, s.city, s.loyalty_tier, s.signup_ts,
    s.updated_at, s.dq_flags, s._loaded_at
);

INSERT INTO ops.transform_watermarks (entity, last_loaded_at)
SELECT 'customers', max(_loaded_at) FROM stg_customers HAVING max(_loaded_at) IS NOT NULL
ON CONFLICT (entity) DO UPDATE
    SET last_loaded_at = excluded.last_loaded_at, updated_at = now();
