-- Databricks SQL: queries behind the RideOps BI dashboard (run on a SQL warehouse).
-- Parameters use the {{ }} syntax of Databricks SQL dashboards.
-- STATUS: written for Databricks SQL; cloud verification pending.

-- name: kpi_tiles
SELECT
  sum(total_bookings)                                        AS bookings,
  sum(completed_rides)                                       AS completed_rides,
  round(sum(gross_revenue), 0)                               AS revenue_inr,
  round(sum(cancelled_bookings) / sum(total_bookings) * 100, 2) AS cancellation_pct
FROM rideops_dev.gold.daily_city_summary
WHERE booking_date BETWEEN {{ start_date }} AND {{ end_date }};

-- name: revenue_trend_by_city
SELECT booking_date, city, gross_revenue,
       avg(gross_revenue) OVER (PARTITION BY city ORDER BY booking_date
                                ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS revenue_7d_avg
FROM rideops_dev.gold.daily_city_summary
WHERE booking_date >= current_date() - INTERVAL 90 DAYS
ORDER BY booking_date, city;

-- name: top_drivers
SELECT city, driver_id, completed_rides, total_revenue, revenue_rank_in_city
FROM rideops_dev.gold.driver_performance
WHERE revenue_rank_in_city <= 5
ORDER BY city, revenue_rank_in_city;

-- name: funnel_by_platform
SELECT platform, sum(sessions) AS sessions, sum(bookings) AS bookings,
       round(sum(bookings) / sum(sessions) * 100, 2) AS conversion_pct
FROM rideops_dev.gold.app_funnel_daily
GROUP BY platform
ORDER BY sessions DESC;

-- name: live_ride_activity
-- Near-real-time view from the streaming windows (last 6 hours).
SELECT window_start, city, event_type, events
FROM rideops_dev.gold.ride_event_windows
WHERE window_start >= current_timestamp() - INTERVAL 6 HOURS
ORDER BY window_start DESC, city;

-- name: time_travel_audit
-- What did yesterday's dashboard show? Query the gold table as of a past version/time.
SELECT sum(gross_revenue) AS revenue_as_of_yesterday
FROM rideops_dev.gold.daily_city_summary TIMESTAMP AS OF date_sub(current_date(), 1);
