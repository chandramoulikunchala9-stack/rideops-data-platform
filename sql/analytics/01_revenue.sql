-- Revenue analytics. Revenue = fares of completed rides + cancellation fees (INR).

-- name: revenue_by_city_month
-- Monthly revenue per city with month-over-month growth (LAG window function).
WITH monthly AS (
    SELECT
        date_trunc('month', booking_date)::date AS month,
        city,
        sum(fare_amount)                        AS revenue
    FROM core.bookings
    WHERE status IN ('completed', 'cancelled')
    GROUP BY 1, 2
)
SELECT
    m.month,
    c.city_name,
    m.revenue,
    lag(m.revenue) OVER (PARTITION BY m.city ORDER BY m.month)  AS previous_month_revenue,
    round(
        100.0 * (m.revenue - lag(m.revenue) OVER (PARTITION BY m.city ORDER BY m.month))
        / nullif(lag(m.revenue) OVER (PARTITION BY m.city ORDER BY m.month), 0), 2
    )                                                           AS growth_pct
FROM monthly m
JOIN core.cities c ON c.city_code = m.city
ORDER BY m.month, m.revenue DESC;

-- name: daily_revenue_running_total
-- Daily revenue, 7-day moving average and month-to-date running total.
SELECT
    booking_date,
    sum(fare_amount)                                                        AS revenue,
    round(avg(sum(fare_amount)) OVER (
        ORDER BY booking_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 2) AS revenue_7d_avg,
    sum(sum(fare_amount)) OVER (
        PARTITION BY date_trunc('month', booking_date) ORDER BY booking_date
    )                                                                       AS month_to_date
FROM core.bookings
GROUP BY booking_date
ORDER BY booking_date;

-- name: revenue_by_vehicle_type_and_hour
-- Which vehicle types earn most at which local hour (surge effect included).
SELECT
    b.vehicle_type,
    extract(hour FROM b.requested_at AT TIME ZONE 'Asia/Kolkata')::int  AS local_hour,
    count(*)                                                            AS rides,
    sum(b.fare_amount)                                                  AS revenue,
    round(avg(b.surge_multiplier), 2)                                   AS avg_surge
FROM core.bookings b
WHERE b.status = 'completed'
GROUP BY 1, 2
ORDER BY 1, 2;

-- name: top_cities_above_average
-- Cities whose total revenue is above the average city revenue (HAVING + scalar subquery).
SELECT
    city,
    sum(fare_amount) AS revenue
FROM core.bookings
WHERE status = 'completed'
GROUP BY city
HAVING sum(fare_amount) > (
    SELECT avg(city_revenue)
    FROM (
        SELECT sum(fare_amount) AS city_revenue
        FROM core.bookings
        WHERE status = 'completed'
        GROUP BY city
    ) AS per_city
)
ORDER BY revenue DESC;
