-- Gold tables exported by the Spark lakehouse (python -m rideops.spark export) land in schema
-- `lakehouse`. Two independent implementations of the same business rules (SQL ELT and PySpark)
-- should produce identical daily totals; this function reports every (date, city) that differs.

CREATE SCHEMA IF NOT EXISTS lakehouse;

CREATE OR REPLACE FUNCTION ops.reconcile_lakehouse_daily()
RETURNS TABLE (failed_rows bigint, total_rows bigint, details jsonb)
LANGUAGE plpgsql
STABLE
AS $$
BEGIN
    -- No export yet: return no row, so the check is simply absent rather than a false PASS.
    IF to_regclass('lakehouse.daily_city_summary') IS NULL THEN
        RETURN;
    END IF;
    -- Dynamic SQL: the table may not exist when this function is created.
    RETURN QUERY EXECUTE $q$
        WITH joined AS (
            SELECT
                coalesce(a.booking_date, l.booking_date)    AS booking_date,
                coalesce(a.city::text, l.city)              AS city,
                a.total_bookings AS sql_bookings, l.total_bookings AS spark_bookings,
                a.gross_revenue  AS sql_revenue,  l.gross_revenue  AS spark_revenue
            FROM analytics.daily_city_summary a
            FULL JOIN lakehouse.daily_city_summary l
              ON l.booking_date = a.booking_date AND l.city = a.city::text
        ),
        diffs AS (
            SELECT * FROM joined
            WHERE sql_bookings IS DISTINCT FROM spark_bookings
               OR sql_revenue IS DISTINCT FROM spark_revenue
        )
        SELECT
            (SELECT count(*) FROM diffs),
            (SELECT count(*) FROM joined),
            coalesce((SELECT jsonb_agg(to_jsonb(d)) FROM (SELECT * FROM diffs LIMIT 20) d), '[]'::jsonb)
    $q$;
END
$$;
