-- core -> analytics (gold). Full refresh inside one transaction: readers see either the old
-- or the new version of every table, never a half-built one (TRUNCATE is transactional in
-- PostgreSQL).

-- ============================================================================ daily city summary
TRUNCATE analytics.daily_city_summary;
INSERT INTO analytics.daily_city_summary
SELECT
    booking_date,
    city,
    count(*)                                                        AS total_bookings,
    count(*) FILTER (WHERE status = 'completed')                    AS completed_rides,
    count(*) FILTER (WHERE status = 'cancelled')                    AS cancelled_bookings,
    count(*) FILTER (WHERE status = 'no_driver_found')              AS no_driver_bookings,
    sum(fare_amount)                                                AS gross_revenue,
    round(avg(fare_amount) FILTER (WHERE status = 'completed'), 2)  AS avg_fare,
    round(avg(distance_km) FILTER (WHERE status = 'completed'), 2)  AS avg_distance_km,
    round(avg(extract(epoch FROM pickup_at - requested_at) / 60)
          FILTER (WHERE pickup_at IS NOT NULL), 2)                  AS avg_wait_min,
    count(DISTINCT driver_id) FILTER (WHERE status = 'completed')   AS active_drivers,
    count(DISTINCT customer_id)                                     AS active_customers,
    round((count(*) FILTER (WHERE status = 'cancelled'))::numeric / count(*), 4)
                                                                    AS cancellation_rate
FROM core.bookings
GROUP BY booking_date, city;

-- ============================================================================ driver performance
TRUNCATE analytics.driver_performance;
INSERT INTO analytics.driver_performance
WITH per_driver AS (
    SELECT
        b.driver_id,
        count(*)                                                        AS total_assigned,
        count(*) FILTER (WHERE b.status = 'completed')                  AS completed_rides,
        count(*) FILTER (WHERE b.cancelled_by = 'driver')               AS driver_cancellations,
        coalesce(sum(b.fare_amount) FILTER (WHERE b.status = 'completed'), 0) AS total_revenue,
        coalesce(sum(b.distance_km) FILTER (WHERE b.status = 'completed'), 0) AS total_distance_km,
        count(DISTINCT b.booking_date) FILTER (WHERE b.status = 'completed')  AS active_days,
        max(b.dropoff_at)                                               AS last_ride_at
    FROM core.bookings b
    WHERE b.driver_id IS NOT NULL
    GROUP BY b.driver_id
)
SELECT
    p.driver_id,
    d.city,
    d.rating,
    p.total_assigned,
    p.completed_rides,
    p.driver_cancellations,
    round(p.completed_rides::numeric / p.total_assigned, 4)            AS completion_rate,
    p.total_revenue,
    p.total_distance_km,
    p.active_days,
    rank() OVER (PARTITION BY d.city ORDER BY p.total_revenue DESC)    AS revenue_rank_in_city,
    p.last_ride_at
FROM per_driver p
LEFT JOIN core.drivers d ON d.driver_id = p.driver_id;

-- ============================================================================ customer activity
TRUNCATE analytics.customer_activity;
INSERT INTO analytics.customer_activity
WITH rides AS (
    SELECT
        customer_id,
        requested_at,
        status,
        fare_amount,
        -- LAG: time since this customer's previous booking
        requested_at - lag(requested_at) OVER (
            PARTITION BY customer_id ORDER BY requested_at
        ) AS gap
    FROM core.bookings
),
per_customer AS (
    SELECT
        customer_id,
        min(requested_at)                                       AS first_booking_at,
        max(requested_at)                                       AS last_booking_at,
        count(*)                                                AS total_bookings,
        count(*) FILTER (WHERE status = 'completed')            AS completed_rides,
        sum(fare_amount)                                        AS total_spend,
        round((avg(extract(epoch FROM gap)) / 86400)::numeric, 2) AS avg_days_between_rides
    FROM rides
    GROUP BY customer_id
),
reference_time AS (
    SELECT max(requested_at) AS as_of FROM core.bookings
)
SELECT
    p.customer_id,
    c.city,
    c.loyalty_tier,
    p.first_booking_at,
    p.last_booking_at,
    p.total_bookings,
    p.completed_rides,
    p.total_spend,
    p.avg_days_between_rides,
    CASE
        WHEN p.last_booking_at < r.as_of - interval '21 days' THEN 'at_risk'
        WHEN p.total_bookings >= 20 THEN 'power_user'
        WHEN p.first_booking_at >= r.as_of - interval '14 days' THEN 'new'
        ELSE 'regular'
    END AS segment
FROM per_customer p
JOIN core.customers c ON c.customer_id = p.customer_id
CROSS JOIN reference_time r;

-- ============================================================================ cancellations
TRUNCATE analytics.cancellation_summary;
INSERT INTO analytics.cancellation_summary
SELECT
    booking_date,
    city,
    cancelled_by,
    cancellation_reason,
    count(*)            AS cancellations,
    sum(fare_amount)    AS fees_collected
FROM core.bookings
WHERE status = 'cancelled'
GROUP BY booking_date, city, cancelled_by, cancellation_reason;

-- ============================================================================ payments
TRUNCATE analytics.payment_summary;
INSERT INTO analytics.payment_summary
SELECT
    (processed_at AT TIME ZONE 'Asia/Kolkata')::date                            AS payment_date,
    coalesce(method, 'unknown')                                                 AS method,
    count(*) FILTER (WHERE transaction_type = 'charge')                         AS attempts,
    count(*) FILTER (WHERE transaction_type = 'charge' AND status = 'success')  AS successful_charges,
    count(*) FILTER (WHERE transaction_type = 'charge' AND status = 'failed')   AS failed_attempts,
    count(*) FILTER (WHERE transaction_type = 'refund')                         AS refunds,
    coalesce(sum(amount) FILTER (WHERE transaction_type = 'charge' AND status = 'success'), 0)
                                                                                AS charged_amount,
    coalesce(sum(amount) FILTER (WHERE transaction_type = 'refund'), 0)         AS refunded_amount,
    round(
        (count(*) FILTER (WHERE transaction_type = 'charge' AND status = 'failed'))::numeric
        / nullif(count(*) FILTER (WHERE transaction_type = 'charge'), 0), 4
    )                                                                           AS failure_rate
FROM core.payments
GROUP BY 1, 2
HAVING count(*) FILTER (WHERE transaction_type = 'charge') > 0;

-- ============================================================================ vehicle utilization
TRUNCATE analytics.vehicle_utilization;
INSERT INTO analytics.vehicle_utilization
WITH rides AS (
    SELECT
        vehicle_id,
        count(DISTINCT driver_id)                               AS drivers_sharing,
        count(*)                                                AS completed_rides,
        coalesce(sum(duration_min) / 60, 0)                     AS ride_hours,
        count(DISTINCT booking_date)                            AS active_days
    FROM core.bookings
    WHERE status = 'completed' AND vehicle_id IS NOT NULL
    GROUP BY vehicle_id
)
SELECT
    v.vehicle_id,
    v.vehicle_type,
    v.city,
    coalesce(r.drivers_sharing, 0),
    coalesce(r.completed_rides, 0),
    round(coalesce(r.ride_hours, 0), 2),
    coalesce(r.active_days, 0),
    -- 12 operating hours per active day is the capacity assumption
    round(coalesce(r.ride_hours / nullif(r.active_days * 12, 0), 0), 4)
FROM core.vehicles v
LEFT JOIN rides r ON r.vehicle_id = v.vehicle_id;

-- ============================================================================ refresh log
INSERT INTO analytics.refresh_log (table_name, row_count, refreshed_at)
SELECT t.table_name, t.row_count, now()
FROM (
    SELECT 'daily_city_summary' AS table_name, count(*) AS row_count FROM analytics.daily_city_summary
    UNION ALL SELECT 'driver_performance', count(*) FROM analytics.driver_performance
    UNION ALL SELECT 'customer_activity', count(*) FROM analytics.customer_activity
    UNION ALL SELECT 'cancellation_summary', count(*) FROM analytics.cancellation_summary
    UNION ALL SELECT 'payment_summary', count(*) FROM analytics.payment_summary
    UNION ALL SELECT 'vehicle_utilization', count(*) FROM analytics.vehicle_utilization
) AS t
ON CONFLICT (table_name) DO UPDATE
    SET row_count = excluded.row_count, refreshed_at = excluded.refreshed_at;
