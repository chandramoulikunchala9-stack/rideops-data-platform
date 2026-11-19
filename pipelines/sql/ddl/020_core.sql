-- Core layer: typed, validated, deduplicated business entities.
-- `dq_flags` lists field-level problems that were repaired (value set to NULL or standardised),
-- so analysts can see exactly which rows were touched.

CREATE TABLE IF NOT EXISTS core.cities (
    city_code      char(3)       PRIMARY KEY,
    city_name      text          NOT NULL,
    state_code     char(2)       NOT NULL,
    base_fare      numeric(8, 2) NOT NULL,
    per_km_rate    numeric(8, 2) NOT NULL
);

CREATE TABLE IF NOT EXISTS core.vehicle_types (
    vehicle_type     text          PRIMARY KEY,
    seats            smallint      NOT NULL,
    fare_multiplier  numeric(4, 2) NOT NULL
);

CREATE TABLE IF NOT EXISTS core.vehicles (
    vehicle_id           text        PRIMARY KEY,
    registration_number  text        NOT NULL,
    vehicle_type         text        NOT NULL REFERENCES core.vehicle_types,
    make                 text,
    model                text,
    manufacture_year     smallint    CHECK (manufacture_year BETWEEN 1990 AND 2100),
    seats                smallint    CHECK (seats > 0),
    city                 char(3)     REFERENCES core.cities,
    is_active            boolean,
    updated_at           timestamptz NOT NULL,
    dq_flags             text[]      NOT NULL DEFAULT '{}',
    _loaded_at           timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS core.drivers (
    driver_id       text          PRIMARY KEY,
    full_name       text,
    phone           text,
    city            char(3)       REFERENCES core.cities,
    license_number  text,
    vehicle_id      text          REFERENCES core.vehicles,
    rating          numeric(3, 2) CHECK (rating BETWEEN 1 AND 5),
    status          text          NOT NULL CHECK (status IN ('active', 'inactive', 'suspended')),
    joined_at       timestamptz   NOT NULL,
    updated_at      timestamptz   NOT NULL,
    dq_flags        text[]        NOT NULL DEFAULT '{}',
    _loaded_at      timestamptz   NOT NULL
);

CREATE TABLE IF NOT EXISTS core.customers (
    customer_id   text        PRIMARY KEY,
    full_name     text,
    email         text,
    phone         text,
    city          char(3)     REFERENCES core.cities,
    loyalty_tier  text,
    signup_ts     timestamptz NOT NULL,
    updated_at    timestamptz NOT NULL,
    dq_flags      text[]      NOT NULL DEFAULT '{}',
    _loaded_at    timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS core.bookings (
    booking_id           text          PRIMARY KEY,
    customer_id          text          NOT NULL REFERENCES core.customers,
    driver_id            text,          -- no FK: a fact may arrive before its driver (flagged instead)
    vehicle_id           text,
    city                 char(3)       NOT NULL REFERENCES core.cities,
    vehicle_type         text,
    status               text          NOT NULL
        CHECK (status IN ('completed', 'cancelled', 'no_driver_found', 'in_progress')),
    requested_at         timestamptz   NOT NULL,
    accepted_at          timestamptz,
    pickup_at            timestamptz,
    dropoff_at           timestamptz,
    cancelled_at         timestamptz,
    cancelled_by         text,
    cancellation_reason  text,
    distance_km          numeric(7, 2),
    duration_min         numeric(7, 1),
    surge_multiplier     numeric(4, 2),
    fare_amount          numeric(10, 2) NOT NULL CHECK (fare_amount >= 0),
    currency             char(3)       NOT NULL,
    payment_method       text,
    updated_at           timestamptz   NOT NULL,
    -- business day in local (IST) time, not UTC
    booking_date         date          GENERATED ALWAYS AS ((requested_at AT TIME ZONE 'Asia/Kolkata')::date) STORED,
    dq_flags             text[]        NOT NULL DEFAULT '{}',
    _loaded_at           timestamptz   NOT NULL,
    CHECK (dropoff_at IS NULL OR dropoff_at >= pickup_at)
);

CREATE TABLE IF NOT EXISTS core.payments (
    payment_id        text           PRIMARY KEY,
    booking_id        text           NOT NULL REFERENCES core.bookings,
    customer_id       text,
    transaction_type  text           NOT NULL CHECK (transaction_type IN ('charge', 'refund')),
    attempt_number    smallint       NOT NULL,
    amount            numeric(10, 2) NOT NULL CHECK (amount >= 0),
    currency          char(3)        NOT NULL,
    method            text,
    gateway           text,
    status            text           NOT NULL CHECK (status IN ('success', 'failed')),
    failure_reason    text,
    processed_at      timestamptz    NOT NULL,
    dq_flags          text[]         NOT NULL DEFAULT '{}',
    _loaded_at        timestamptz    NOT NULL
);

-- Quarantine: rows that could not be trusted, with the reason and the original record.
CREATE TABLE IF NOT EXISTS core.rejected_records (
    rejected_id   bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity        text        NOT NULL,
    record_key    text,
    reason        text        NOT NULL,
    raw_record    jsonb       NOT NULL,
    source_file   text        NOT NULL,
    rejected_at   timestamptz NOT NULL DEFAULT now()
);

-- Indexes chosen for the access paths in sql/analytics (see docs/sql-optimization.md).
CREATE INDEX IF NOT EXISTS ix_bookings_booking_date_city ON core.bookings (booking_date, city);
CREATE INDEX IF NOT EXISTS ix_bookings_customer_requested ON core.bookings (customer_id, requested_at);
CREATE INDEX IF NOT EXISTS ix_bookings_driver ON core.bookings (driver_id) WHERE driver_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_payments_booking ON core.payments (booking_id);
CREATE INDEX IF NOT EXISTS ix_payments_processed ON core.payments (processed_at);
CREATE INDEX IF NOT EXISTS ix_rejected_entity_reason ON core.rejected_records (entity, reason);
