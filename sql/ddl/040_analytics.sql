-- Analytics (gold) tables served by the API. Rebuilt from core by sql/transform/30_analytics.sql.

CREATE TABLE IF NOT EXISTS analytics.daily_city_summary (
    booking_date         date          NOT NULL,
    city                 char(3)       NOT NULL,
    total_bookings       integer       NOT NULL,
    completed_rides      integer       NOT NULL,
    cancelled_bookings   integer       NOT NULL,
    no_driver_bookings   integer       NOT NULL,
    gross_revenue        numeric(14, 2) NOT NULL,
    avg_fare             numeric(10, 2),
    avg_distance_km      numeric(7, 2),
    avg_wait_min         numeric(7, 2),
    active_drivers       integer       NOT NULL,
    active_customers     integer       NOT NULL,
    cancellation_rate    numeric(6, 4) NOT NULL,
    PRIMARY KEY (booking_date, city)
);

CREATE TABLE IF NOT EXISTS analytics.driver_performance (
    driver_id            text          PRIMARY KEY,
    city                 char(3),
    rating               numeric(3, 2),
    total_assigned       integer       NOT NULL,
    completed_rides      integer       NOT NULL,
    driver_cancellations integer       NOT NULL,
    completion_rate      numeric(6, 4) NOT NULL,
    total_revenue        numeric(14, 2) NOT NULL,
    total_distance_km    numeric(12, 2) NOT NULL,
    active_days          integer       NOT NULL,
    revenue_rank_in_city integer       NOT NULL,
    last_ride_at         timestamptz
);

CREATE TABLE IF NOT EXISTS analytics.customer_activity (
    customer_id          text          PRIMARY KEY,
    city                 char(3),
    loyalty_tier         text,
    first_booking_at     timestamptz   NOT NULL,
    last_booking_at      timestamptz   NOT NULL,
    total_bookings       integer       NOT NULL,
    completed_rides      integer       NOT NULL,
    total_spend          numeric(14, 2) NOT NULL,
    avg_days_between_rides numeric(8, 2),
    segment              text          NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics.cancellation_summary (
    booking_date         date          NOT NULL,
    city                 char(3)       NOT NULL,
    cancelled_by         text          NOT NULL,
    cancellation_reason  text          NOT NULL,
    cancellations        integer       NOT NULL,
    fees_collected       numeric(12, 2) NOT NULL,
    PRIMARY KEY (booking_date, city, cancelled_by, cancellation_reason)
);

CREATE TABLE IF NOT EXISTS analytics.payment_summary (
    payment_date         date          NOT NULL,
    method               text          NOT NULL,
    attempts             integer       NOT NULL,
    successful_charges   integer       NOT NULL,
    failed_attempts      integer       NOT NULL,
    refunds              integer       NOT NULL,
    charged_amount       numeric(14, 2) NOT NULL,
    refunded_amount      numeric(14, 2) NOT NULL,
    failure_rate         numeric(6, 4) NOT NULL,
    PRIMARY KEY (payment_date, method)
);

CREATE TABLE IF NOT EXISTS analytics.vehicle_utilization (
    vehicle_id           text          PRIMARY KEY,
    vehicle_type         text          NOT NULL,
    city                 char(3),
    drivers_sharing      integer       NOT NULL,
    completed_rides      integer       NOT NULL,
    ride_hours           numeric(10, 2) NOT NULL,
    active_days          integer       NOT NULL,
    utilization_rate     numeric(6, 4) NOT NULL  -- ride hours / (active days * 12 operating hours)
);

CREATE TABLE IF NOT EXISTS analytics.refresh_log (
    table_name    text        PRIMARY KEY,
    row_count     bigint      NOT NULL,
    refreshed_at  timestamptz NOT NULL
);
