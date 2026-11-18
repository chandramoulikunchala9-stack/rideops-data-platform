-- Payment analysis.

-- name: payment_success_by_method
SELECT
    method,
    count(*) FILTER (WHERE transaction_type = 'charge')                         AS attempts,
    count(*) FILTER (WHERE transaction_type = 'charge' AND status = 'failed')   AS failed,
    round(100.0 * count(*) FILTER (WHERE transaction_type = 'charge' AND status = 'failed')
          / nullif(count(*) FILTER (WHERE transaction_type = 'charge'), 0), 2)  AS failure_pct
FROM core.payments
GROUP BY method
ORDER BY failure_pct DESC NULLS LAST;

-- name: failure_reasons
SELECT failure_reason, count(*) AS failures
FROM core.payments
WHERE status = 'failed'
GROUP BY failure_reason
ORDER BY failures DESC;

-- name: unpaid_completed_rides
-- Completed rides with a fare but no successful charge (revenue leakage).
SELECT b.booking_id, b.customer_id, b.fare_amount, b.payment_method, b.dropoff_at
FROM core.bookings b
WHERE b.status = 'completed'
  AND b.fare_amount > 0
  AND NOT EXISTS (
      SELECT 1
      FROM core.payments p
      WHERE p.booking_id = b.booking_id
        AND p.transaction_type = 'charge'
        AND p.status = 'success'
  )
ORDER BY b.fare_amount DESC;

-- name: retry_recovery_rate
-- Of bookings whose first attempt failed, how many were eventually paid?
WITH first_failed AS (
    SELECT DISTINCT booking_id
    FROM core.payments
    WHERE transaction_type = 'charge' AND attempt_number = 1 AND status = 'failed'
)
SELECT
    count(*)                                            AS first_attempt_failed,
    count(*) FILTER (WHERE EXISTS (
        SELECT 1 FROM core.payments p
        WHERE p.booking_id = f.booking_id AND p.status = 'success'
          AND p.transaction_type = 'charge'
    ))                                                  AS recovered_by_retry
FROM first_failed f;

-- name: late_settlements
-- Charges processed more than a day after the ride ended (late-arriving data).
SELECT
    p.method,
    count(*)                                                        AS late_charges,
    round(avg(extract(epoch FROM p.processed_at - b.dropoff_at) / 3600)::numeric, 1) AS avg_delay_hours
FROM core.payments p
JOIN core.bookings b ON b.booking_id = p.booking_id
WHERE p.transaction_type = 'charge'
  AND p.status = 'success'
  AND p.processed_at > b.dropoff_at + interval '1 day'
GROUP BY p.method;

-- name: net_revenue_after_refunds
SELECT
    date_trunc('week', processed_at)::date                                          AS week,
    sum(amount) FILTER (WHERE transaction_type = 'charge' AND status = 'success')    AS charged,
    coalesce(sum(amount) FILTER (WHERE transaction_type = 'refund'), 0)              AS refunded,
    sum(CASE WHEN transaction_type = 'refund' THEN -amount
             WHEN status = 'success' THEN amount ELSE 0 END)                         AS net
FROM core.payments
GROUP BY 1
ORDER BY 1;
