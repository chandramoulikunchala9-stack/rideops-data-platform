# Cloud Usage and Cost Management

## Three kinds of cost data, never mixed up

Every row in `ops.cloud_costs` has a `source` column. The API returns the list of sources with every
cost figure, and the dashboard shows it next to the numbers.

| `source` | What it is | How it gets there | Status |
|---|---|---|---|
| `simulated` | Synthetic daily usage derived from pipeline run durations, with illustrative unit prices | `python -m rideops.generators` → `python -m rideops.db all` | Implemented and tested |
| `aws_cur` | **Real** AWS billing from a CUR 2.0 Data Export | Terraform `enable_cur_export = true` → `python -m rideops.costs load-cur s3://...` | Parser tested on a CUR-format fixture; real account pending |
| `databricks_system_tables` | **Real** DBU usage from `system.billing.usage` × `system.billing.list_prices` | `databricks/sql/02_platform_monitoring_system_tables.sql` | Query written; workspace verification pending |

The simulated prices are round illustrative numbers, not quotes. **Never present simulated figures as
real spending.** The dashboard labels them "SIMULATED".

## Reports (API, dashboard, SQL)

| Need | Where |
|---|---|
| Daily / monthly cost | `GET /api/v1/costs/trends`, `GET /api/v1/costs/summary?month=YYYY-MM`, `pipelines/sql/analytics/09_cloud_costs.sql` |
| Cost by service / workload / environment | `costs/summary` breakdowns; `cost_by_workload_and_environment` query |
| Usage trends | `costs/trends?group_by=service\|workload\|environment\|provider` |
| Anomaly detection | Days more than 3 standard deviations above the trailing 14-day mean (`daily_cost_anomalies`, `cost_anomaly` alert) |
| Budget warnings | `budget_status`: OK, FORECAST_OVER_BUDGET or OVER_BUDGET, from a linear run-rate forecast; `budget` alert rule; AWS Budget in Terraform |
| Expensive jobs | `most_expensive_jobs` (cost per run, per million records); `expensive_jobs` in the summary |
| Untagged spend | `untagged_usd`; `cloud_costs_untagged` data-quality check; Databricks `untagged_spend` query |

## What the simulated data demonstrates

* Compute cost follows pipeline run time and cluster size: `cost = worker-hours × DBU rate + EC2 rate`.
* Storage grows by about 8 GB per day.
* **Incident INC-2026-002**: a dev cluster left running on 2026-10-01 and 10-02 shows up as a z-score anomaly
  and pushes October over the default 1,200 USD budget.
* 2% of line items are untagged, the classic FinOps problem of costs nobody can be charged for.

## Cost controls built into the platform

| Control | Where |
|---|---|
| Job clusters, not all-purpose clusters; autoscaling 1–3; spot workers | `databricks/resources/rideops_jobs.yml` |
| Serverless jobs for small or spiky workloads | stream and backfill jobs |
| Cluster policy: node allow-list, max workers, mandatory cost tags | `databricks/policies/job_cluster_policy.json` |
| Dev schedules paused by default | bundle variable `schedule_pause_status` |
| `availableNow` streaming instead of always-on clusters | ride events job, every 15 minutes |
| Incremental processing (checkpoints, watermarks, CDF) | no full reprocessing every day |
| S3 lifecycle tiering, versioning cleanup, multipart abort | `infra/terraform/s3.tf` |
| AWS Budget alerts at 80% forecast / 100% actual | `infra/terraform/billing.tf` |
| `default_tags` on every AWS resource | `infra/terraform/versions.tf` |
