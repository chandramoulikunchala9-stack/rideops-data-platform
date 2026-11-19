# Databricks Setup

> **Verification status:** everything under `databricks/` is written for Databricks on AWS and
> checked locally (YAML structure, file references, task graph, Python syntax:
> `tests/unit/test_bundle_config.py`). It has **not yet been deployed to a real workspace**.
> The transformation logic the notebooks import *is* tested, in Spark 4.2 with Delta 4.4 (`tests/spark/`).

## What is in `databricks/`

| Path | Feature | Local equivalent (tested) |
|---|---|---|
| `databricks.yml` | Asset Bundle: dev/prod targets, variables, wheel artifact, prod `run_as` service principal | – |
| `resources/rideops_jobs.yml` | Lakeflow Jobs: daily DAG (bronze → silver → **DQ gate** → gold), 15-minute stream job, backfill job. Covers parameters, schedule, retries, timeouts, duration alerts, job clusters, serverless | `python -m rideops.spark all` |
| `resources/rideops_pipeline.yml` + `dlt/rideops_pipeline.py` | Lakeflow Declarative Pipeline (DLT): expectations, quarantine, `apply_changes` SCD1 + **SCD2** | silver rules in `rideops.spark.silver` |
| `notebooks/01_bronze_autoloader.py` | **Auto Loader** (`cloudFiles`), schema location, rescue mode, availableNow | `rideops.spark.bronze` (file stream + checkpoint) |
| `notebooks/02_silver_merge.py` | Incremental `foreachBatch` + **MERGE** into Unity Catalog tables | `rideops.spark.silver` |
| `notebooks/03_gold_aggregations.py` | **Change Data Feed**-driven incremental gold, `replaceWhere`, liquid clustering, OPTIMIZE | `rideops.spark.gold` |
| `notebooks/04_streaming_ride_events.py` | Auto Loader + **Structured Streaming**, watermark, dedup, windows, checkpoints | `rideops.spark.streaming` |
| `notebooks/05_data_quality.py` | Quality gate that blocks gold on failure | `rideops.spark.quality` |
| `notebooks/06_batch_backfill.py` | Batch reprocessing of a date range | – |
| `unity_catalog/01_catalog_and_schemas.sql` | Storage credential, external locations, catalog, schemas, **volumes** | – |
| `unity_catalog/02_roles_and_grants.sql` | 4 roles, least-privilege grants, **column masks**, **row filters** | – |
| `sql/01_gold_dashboards.sql` | Databricks SQL dashboard queries, time travel | `sql/analytics/` (PostgreSQL) |
| `sql/02_platform_monitoring_system_tables.sql` | Job runs, slow BI queries, **real DBU cost** from `system.billing.*` | `ops.cloud_costs` (simulated) |
| `policies/job_cluster_policy.json` | Compute policy: node allow-list, max workers, spot, mandatory tags | – |

## Deploying (when you have a workspace)

Free option: **Databricks Free Edition** supports serverless notebooks, jobs and pipelines, but has
limits: no custom AWS storage credentials and restricted compute types. Use the serverless tasks
there, and replace the `/Volumes/<catalog>/landing/raw` path with a managed volume you upload the
landing files to.

```bash
# 1. Install the CLI (https://docs.databricks.com/dev-tools/cli/install.html) and log in (OAuth, no tokens in files)
databricks auth login --host https://<workspace>.cloud.databricks.com

# 2. Create Unity Catalog objects (Databricks SQL editor, as an admin): run
#    unity_catalog/01_catalog_and_schemas.sql then 02_roles_and_grants.sql
#    (replace <account-id> / <suffix> with the Terraform outputs from infra/terraform)

# 3. Optional: compute policy, then put its id in rideops_jobs.yml (policy_id)
databricks cluster-policies create --json @databricks/policies/job_cluster_policy.json

# 4. Upload landing files to the volume
databricks fs cp -r data/landing dbfs:/Volumes/rideops_dev/landing/raw --overwrite

# 5. Validate, deploy and run the bundle (dev target: resources prefixed, schedules paused)
cd databricks
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run rideops_daily -t dev
databricks bundle run rideops_declarative -t dev
```

Verify: in **Jobs & Pipelines**, the `rideops_daily` run shows 4 green tasks. In
`rideops_dev.silver._quarantine` you should see rejected rows with reasons. The DLT pipeline graph
shows expectation pass/fail counts per table.

## Compute and permissions concepts used

| Concept | Where | Why |
|---|---|---|
| Job clusters (not all-purpose) | `rideops_daily` | Created for the run and terminated afterwards; cheaper, isolated |
| Serverless jobs compute | stream + backfill jobs | No cluster to size or wait for; billed per use |
| Autoscale 1–3 workers, spot with on-demand driver | job cluster | Elastic capacity at lower cost. The driver stays stable if spot nodes are reclaimed |
| `data_security_mode: SINGLE_USER` | job cluster | Unity Catalog-enabled access mode for jobs |
| Cluster policy | `policies/` | Stops oversized clusters and untagged spend |
| `run_as` service principal (prod) | `databricks.yml` | Jobs do not depend on a person's account or permissions |
| `max_concurrent_runs: 1` | all scheduled jobs | Overlapping runs could double-write and race MERGEs |
| Bundle `permissions` | prod target | Admins manage, engineers view: changes go through Git and CI only |

## Cost safety

* Dev schedules are `PAUSED` by default (`schedule_pause_status`), so nothing runs until you choose.
* All compute is tagged `project=rideops`. `sql/02_platform_monitoring_system_tables.sql` reports
  real spend per job and flags untagged usage.
* Run `databricks bundle destroy -t dev` to remove everything the bundle created.
