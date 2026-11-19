-- Customer retention.

-- name: weekly_cohort_retention
-- Customers grouped by the week of their first booking; share still booking N weeks later.
WITH first_booking AS (
    SELECT customer_id, date_trunc('week', min(booking_date))::date AS cohort_week
    FROM core.bookings
    GROUP BY customer_id
),
activity AS (
    SELECT DISTINCT b.customer_id, date_trunc('week', b.booking_date)::date AS active_week
    FROM core.bookings b
),
cohort_activity AS (
    SELECT
        f.cohort_week,
        ((a.active_week - f.cohort_week) / 7)   AS weeks_since_first,
        count(DISTINCT a.customer_id)           AS customers
    FROM first_booking f
    JOIN activity a ON a.customer_id = f.customer_id
    GROUP BY 1, 2
)
SELECT
    cohort_week,
    weeks_since_first,
    customers,
    round(100.0 * customers / first_value(customers) OVER (
        PARTITION BY cohort_week ORDER BY weeks_since_first), 1) AS retention_pct
FROM cohort_activity
ORDER BY cohort_week, weeks_since_first;

-- name: repeat_vs_one_time_customers
SELECT
    CASE WHEN total_bookings = 1 THEN 'one_time' ELSE 'repeat' END AS customer_type,
    count(*)                                                        AS customers,
    round(avg(total_spend), 2)                                      AS avg_spend
FROM analytics.customer_activity
GROUP BY 1;

-- name: segment_summary
SELECT
    segment,
    count(*)                                AS customers,
    round(avg(total_bookings), 1)           AS avg_bookings,
    round(avg(avg_days_between_rides), 1)   AS avg_days_between_rides,
    sum(total_spend)                        AS total_spend
FROM analytics.customer_activity
GROUP BY segment
ORDER BY total_spend DESC;

-- name: customers_without_bookings
-- Signed-up customers who never booked (RIGHT JOIN written deliberately; same as a LEFT JOIN
-- with the tables swapped).
SELECT c.customer_id, c.city, c.signup_ts
FROM core.bookings b
RIGHT JOIN core.customers c ON c.customer_id = b.customer_id
WHERE b.booking_id IS NULL
ORDER BY c.signup_ts DESC;

-- name: loyalty_tier_value
SELECT
    coalesce(c.loyalty_tier, 'unknown') AS loyalty_tier,
    count(DISTINCT c.customer_id)       AS customers,
    count(b.booking_id)                 AS bookings,
    round(sum(b.fare_amount) / count(DISTINCT c.customer_id), 2) AS revenue_per_customer
FROM core.customers c
LEFT JOIN core.bookings b ON b.customer_id = c.customer_id AND b.status = 'completed'
GROUP BY 1
ORDER BY revenue_per_customer DESC;
