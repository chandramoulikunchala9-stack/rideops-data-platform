-- Raw layer: one table per landing dataset.
-- CSV sources keep every column as text (no casting = no load failures on bad values).
-- JSON Lines sources keep the whole object in a jsonb column.
-- Every table records where each row came from and when it was loaded.

CREATE TABLE IF NOT EXISTS raw.customers (
    customer_id   text,
    full_name     text,
    email         text,
    phone         text,
    city          text,
    loyalty_tier  text,
    signup_ts     text,
    updated_at    text,
    _source_file  text        NOT NULL,
    _batch_id     text        NOT NULL,
    _row_number   integer     NOT NULL,
    _loaded_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw.drivers (
    driver_id       text,
    full_name       text,
    phone           text,
    city            text,
    license_number  text,
    vehicle_id      text,
    rating          text,
    status          text,
    joined_at       text,
    updated_at      text,
    _source_file    text        NOT NULL,
    _batch_id       text        NOT NULL,
    _row_number     integer     NOT NULL,
    _loaded_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw.bookings (
    booking_id           text,
    customer_id          text,
    driver_id            text,
    vehicle_id           text,
    city                 text,
    vehicle_type         text,
    status               text,
    requested_at         text,
    accepted_at          text,
    pickup_at            text,
    dropoff_at           text,
    cancelled_at         text,
    cancelled_by         text,
    cancellation_reason  text,
    distance_km          text,
    duration_min         text,
    surge_multiplier     text,
    fare_amount          text,
    currency             text,
    payment_method       text,
    updated_at           text,
    _source_file         text        NOT NULL,
    _batch_id            text        NOT NULL,
    _row_number          integer     NOT NULL,
    _loaded_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw.cloud_costs (
    cost_id         text,
    usage_date      text,
    provider        text,
    service         text,
    workload        text,
    environment     text,
    usage_type      text,
    usage_quantity  text,
    usage_unit      text,
    unit_price_usd  text,
    cost_usd        text,
    currency        text,
    source          text,
    _source_file    text        NOT NULL,
    _batch_id       text        NOT NULL,
    _row_number     integer     NOT NULL,
    _loaded_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw.vehicles (
    doc           jsonb       NOT NULL,
    _source_file  text        NOT NULL,
    _batch_id     text        NOT NULL,
    _row_number   integer     NOT NULL,
    _loaded_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw.payments (
    doc           jsonb       NOT NULL,
    _source_file  text        NOT NULL,
    _batch_id     text        NOT NULL,
    _row_number   integer     NOT NULL,
    _loaded_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw.pipeline_runs (
    doc           jsonb       NOT NULL,
    _source_file  text        NOT NULL,
    _batch_id     text        NOT NULL,
    _row_number   integer     NOT NULL,
    _loaded_at    timestamptz NOT NULL DEFAULT now()
);

-- Incremental transforms read only rows loaded after the last watermark.
CREATE INDEX IF NOT EXISTS ix_raw_customers_loaded     ON raw.customers (_loaded_at);
CREATE INDEX IF NOT EXISTS ix_raw_drivers_loaded       ON raw.drivers (_loaded_at);
CREATE INDEX IF NOT EXISTS ix_raw_bookings_loaded      ON raw.bookings (_loaded_at);
CREATE INDEX IF NOT EXISTS ix_raw_cloud_costs_loaded   ON raw.cloud_costs (_loaded_at);
CREATE INDEX IF NOT EXISTS ix_raw_vehicles_loaded      ON raw.vehicles (_loaded_at);
CREATE INDEX IF NOT EXISTS ix_raw_payments_loaded      ON raw.payments (_loaded_at);
CREATE INDEX IF NOT EXISTS ix_raw_pipeline_runs_loaded ON raw.pipeline_runs (_loaded_at);
