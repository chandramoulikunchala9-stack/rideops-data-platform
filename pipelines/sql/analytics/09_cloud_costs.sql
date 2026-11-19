-- Cloud cost analytics. All figures come from ops.cloud_costs; the `source` column says whether
-- a row is simulated or real billing data, and every query keeps it visible.

-- name: monthly_cost_by_service
SELECT
    date_trunc('month', usage_date)::date   AS month,
    source,
    provider,
    service,
    round(sum(cost_usd), 2)                  AS cost_usd
FROM ops.cloud_costs
GROUP BY 1, 2, 3, 4
ORDER BY month, cost_usd DESC;

-- name: cost_by_workload_and_environment
SELECT
    coalesce(workload, 'UNTAGGED')  AS workload,
    environment,
    source,
    round(sum(cost_usd), 2)         AS cost_usd,
    round(100.0 * sum(cost_usd) / sum(sum(cost_usd)) OVER (), 2) AS pct_of_total
FROM ops.cloud_costs
GROUP BY 1, 2, 3
ORDER BY cost_usd DESC;

-- name: daily_cost_anomalies
-- Days whose cost exceeds the trailing 14-day mean by more than 3 standard deviations.
WITH daily AS (
    SELECT usage_date, sum(cost_usd) AS cost
    FROM ops.cloud_costs
    GROUP BY usage_date
),
stats AS (
    SELECT
        usage_date,
        cost,
        avg(cost)         OVER w AS trailing_mean,
        stddev_samp(cost) OVER w AS trailing_stddev
    FROM daily
    WINDOW w AS (ORDER BY usage_date ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING)
)
SELECT
    usage_date,
    round(cost, 2)                                              AS cost_usd,
    round(trailing_mean, 2)                                     AS trailing_mean,
    round((cost - trailing_mean) / nullif(trailing_stddev, 0), 1) AS z_score
FROM stats
WHERE trailing_stddev IS NOT NULL
  AND cost > trailing_mean + 3 * trailing_stddev
ORDER BY usage_date;

-- name: most_expensive_jobs
-- Cost per pipeline joined to its run statistics: cost per run and per million records.
WITH job_cost AS (
    SELECT workload AS pipeline_name, sum(cost_usd) AS cost_usd
    FROM ops.cloud_costs
    WHERE workload IS NOT NULL
    GROUP BY workload
),
job_runs AS (
    SELECT pipeline_name, count(*) AS runs, sum(records_read) AS records
    FROM ops.pipeline_runs
    GROUP BY pipeline_name
)
SELECT
    c.pipeline_name,
    round(c.cost_usd, 2)                                         AS cost_usd,
    r.runs,
    round(c.cost_usd / nullif(r.runs, 0), 4)                     AS cost_per_run,
    round(c.cost_usd / nullif(r.records, 0) * 1e6, 2)            AS cost_per_million_records
FROM job_cost c
INNER JOIN job_runs r ON r.pipeline_name = c.pipeline_name
ORDER BY c.cost_usd DESC;

-- name: month_to_date_vs_budget
-- Month-to-date spend, a linear end-of-month forecast and the budget status.
-- The budget is a parameter in the API (MONTHLY_BUDGET_USD); 1200 is used here as an example.
WITH mtd AS (
    SELECT
        date_trunc('month', max(usage_date))::date      AS month,
        max(usage_date)                                 AS as_of,
        sum(cost_usd) FILTER (
            WHERE usage_date >= date_trunc('month', (SELECT max(usage_date) FROM ops.cloud_costs))
        )                                               AS spend
    FROM ops.cloud_costs
)
SELECT
    month,
    as_of,
    round(spend, 2) AS month_to_date_usd,
    round(spend / extract(day FROM as_of) *
          extract(day FROM (month + interval '1 month - 1 day')), 2) AS forecast_usd,
    CASE
        WHEN spend > 1200 THEN 'OVER_BUDGET'
        WHEN spend / extract(day FROM as_of) * extract(day FROM (month + interval '1 month - 1 day')) > 1200
            THEN 'FORECAST_OVER_BUDGET'
        ELSE 'OK'
    END AS budget_status
FROM mtd;
