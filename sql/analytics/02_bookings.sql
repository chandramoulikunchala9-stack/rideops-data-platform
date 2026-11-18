-- Booking analytics.

-- name: daily_bookings_by_status
SELECT
    booking_date,
    count(*)                                            AS total,
    count(*) FILTER (WHERE status = 'completed')        AS completed,
    count(*) FILTER (WHERE status = 'cancelled')        AS cancelled,
    count(*) FILTER (WHERE status = 'no_driver_found')  AS no_driver_found,
    count(*) FILTER (WHERE status = 'in_progress')      AS in_progress
FROM core.bookings
GROUP BY booking_date
ORDER BY booking_date;

-- name: weekday_vs_weekend_demand
SELECT
    CASE WHEN extract(isodow FROM booking_date) IN (6, 7) THEN 'weekend' ELSE 'weekday' END AS day_type,
    count(*)                                        AS bookings,
    count(DISTINCT booking_date)                    AS days,
    round(count(*)::numeric / count(DISTINCT booking_date), 1) AS bookings_per_day
FROM core.bookings
GROUP BY 1;

-- name: peak_hours_by_city
-- The busiest local hour in each city (ROW_NUMBER to pick the top row per group).
WITH hourly AS (
    SELECT
        city,
        extract(hour FROM requested_at AT TIME ZONE 'Asia/Kolkata')::int AS local_hour,
        count(*) AS bookings
    FROM core.bookings
    GROUP BY 1, 2
),
ranked AS (
    SELECT *, row_number() OVER (PARTITION BY city ORDER BY bookings DESC) AS rn
    FROM hourly
)
SELECT city, local_hour AS peak_hour, bookings
FROM ranked
WHERE rn = 1
ORDER BY city;

-- name: wait_time_distribution
-- Minutes from request to pickup, as percentiles per city.
SELECT
    city,
    round(percentile_cont(0.5) WITHIN GROUP (
        ORDER BY extract(epoch FROM pickup_at - requested_at) / 60)::numeric, 1) AS p50_wait_min,
    round(percentile_cont(0.9) WITHIN GROUP (
        ORDER BY extract(epoch FROM pickup_at - requested_at) / 60)::numeric, 1) AS p90_wait_min
FROM core.bookings
WHERE pickup_at IS NOT NULL
GROUP BY city
ORDER BY p90_wait_min DESC;

-- name: booking_volume_vs_previous_week
-- Same weekday last week comparison (LAG with offset 7 over a dense calendar).
WITH daily AS (
    SELECT booking_date, count(*) AS bookings
    FROM core.bookings
    GROUP BY booking_date
)
SELECT
    booking_date,
    bookings,
    lag(bookings, 7) OVER (ORDER BY booking_date)   AS same_day_last_week,
    bookings - lag(bookings, 7) OVER (ORDER BY booking_date) AS change
FROM daily
ORDER BY booking_date;
