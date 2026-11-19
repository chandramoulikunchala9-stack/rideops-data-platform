-- Driver performance.

-- name: top_drivers_per_city
-- Top 3 drivers by revenue in each city, with RANK and DENSE_RANK side by side.
WITH driver_revenue AS (
    SELECT
        b.driver_id,
        d.full_name,
        b.city,
        count(*)            AS rides,
        sum(b.fare_amount)  AS revenue
    FROM core.bookings b
    INNER JOIN core.drivers d ON d.driver_id = b.driver_id
    WHERE b.status = 'completed'
    GROUP BY b.driver_id, d.full_name, b.city
),
ranked AS (
    SELECT
        *,
        rank()       OVER (PARTITION BY city ORDER BY revenue DESC) AS revenue_rank,
        dense_rank() OVER (PARTITION BY city ORDER BY rides DESC)   AS rides_dense_rank
    FROM driver_revenue
)
SELECT *
FROM ranked
WHERE revenue_rank <= 3
ORDER BY city, revenue_rank;

-- name: driver_cancellation_offenders
-- Drivers with at least 10 assignments who cancel more than 10% of them (HAVING).
SELECT
    driver_id,
    count(*)                                                        AS assigned,
    count(*) FILTER (WHERE cancelled_by = 'driver')                 AS driver_cancellations,
    round(100.0 * count(*) FILTER (WHERE cancelled_by = 'driver') / count(*), 1) AS cancel_pct
FROM core.bookings
WHERE driver_id IS NOT NULL
GROUP BY driver_id
HAVING count(*) >= 10
   AND count(*) FILTER (WHERE cancelled_by = 'driver')::numeric / count(*) > 0.10
ORDER BY cancel_pct DESC;

-- name: idle_drivers
-- Active drivers with no completed ride in the last 7 days of data (LEFT JOIN anti-join).
SELECT d.driver_id, d.full_name, d.city, d.joined_at
FROM core.drivers d
LEFT JOIN core.bookings b
    ON  b.driver_id = d.driver_id
    AND b.status = 'completed'
    AND b.requested_at >= (SELECT max(requested_at) FROM core.bookings) - interval '7 days'
WHERE d.status = 'active'
  AND b.booking_id IS NULL
ORDER BY d.city, d.driver_id;

-- name: rating_band_vs_completion
SELECT
    CASE
        WHEN d.rating >= 4.7 THEN '4.7+'
        WHEN d.rating >= 4.3 THEN '4.3-4.7'
        WHEN d.rating IS NOT NULL THEN '<4.3'
        ELSE 'unknown'
    END AS rating_band,
    count(DISTINCT d.driver_id)                                     AS drivers,
    round(avg(p.completion_rate), 4)                                AS avg_completion_rate,
    round(avg(p.total_revenue), 2)                                  AS avg_revenue
FROM core.drivers d
JOIN analytics.driver_performance p ON p.driver_id = d.driver_id
GROUP BY 1
ORDER BY 1;

-- name: time_between_rides
-- Average gap between a driver's consecutive rides (LEAD looks at the next ride).
WITH rides AS (
    SELECT
        driver_id,
        dropoff_at,
        lead(pickup_at) OVER (PARTITION BY driver_id, booking_date ORDER BY pickup_at) AS next_pickup
    FROM core.bookings
    WHERE status = 'completed'
)
SELECT
    driver_id,
    count(next_pickup)                                                       AS gaps,
    round(avg(extract(epoch FROM next_pickup - dropoff_at) / 60)::numeric, 1) AS avg_idle_min
FROM rides
WHERE next_pickup > dropoff_at
GROUP BY driver_id
ORDER BY avg_idle_min
LIMIT 20;
