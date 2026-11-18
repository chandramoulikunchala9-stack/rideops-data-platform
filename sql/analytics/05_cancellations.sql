-- Cancellation analysis.

-- name: cancellation_rate_by_city_and_actor
SELECT
    city,
    count(*)                                                                AS bookings,
    round(100.0 * count(*) FILTER (WHERE cancelled_by = 'customer') / count(*), 2) AS customer_cancel_pct,
    round(100.0 * count(*) FILTER (WHERE cancelled_by = 'driver') / count(*), 2)   AS driver_cancel_pct
FROM core.bookings
GROUP BY city
ORDER BY city;

-- name: top_cancellation_reasons
-- Share of each reason within its actor (window SUM over the grouped result).
SELECT
    cancelled_by,
    cancellation_reason,
    count(*) AS cancellations,
    round(100.0 * count(*) / sum(count(*)) OVER (PARTITION BY cancelled_by), 1) AS pct_of_actor
FROM core.bookings
WHERE status = 'cancelled'
GROUP BY cancelled_by, cancellation_reason
ORDER BY cancelled_by, cancellations DESC;

-- name: cancellation_vs_wait
-- Do customers cancel more when the driver is assigned late? Bucket by time-to-accept.
SELECT
    width_bucket(extract(epoch FROM accepted_at - requested_at), 0, 300, 5) AS accept_bucket,
    count(*)                                                                AS bookings,
    round(100.0 * count(*) FILTER (WHERE cancelled_by = 'customer') / count(*), 2) AS customer_cancel_pct
FROM core.bookings
WHERE accepted_at IS NOT NULL
GROUP BY 1
ORDER BY 1;

-- name: cancellation_fee_revenue_by_week
SELECT
    date_trunc('week', booking_date)::date  AS week,
    count(*) FILTER (WHERE fare_amount > 0)  AS fees_charged,
    sum(fare_amount)                         AS fee_revenue
FROM core.bookings
WHERE status = 'cancelled'
GROUP BY 1
ORDER BY 1;
