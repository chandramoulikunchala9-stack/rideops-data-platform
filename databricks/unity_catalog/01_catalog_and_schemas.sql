-- Unity Catalog objects for RideOps (run as a metastore/catalog admin in Databricks SQL).
-- STATUS: written for Databricks; cloud verification pending.
--
-- Three-level namespace:  catalog . schema . table      e.g. rideops_dev.gold.daily_city_summary
-- One catalog per environment keeps dev and prod data and permissions completely separate.

-- 1. Storage credential + external location: how Unity Catalog reaches the S3 buckets created by
--    Terraform (infra/terraform). The IAM role ARN comes from the `databricks_uc_role_arn` output.
CREATE STORAGE CREDENTIAL IF NOT EXISTS rideops_s3_credential
  WITH (AWS_IAM_ROLE = 'arn:aws:iam::<account-id>:role/rideops-dev-unity-catalog')
  COMMENT 'Access to RideOps S3 buckets';

CREATE EXTERNAL LOCATION IF NOT EXISTS rideops_dev_raw
  URL 's3://rideops-dev-raw-<suffix>/'
  WITH (STORAGE CREDENTIAL rideops_s3_credential)
  COMMENT 'Landing zone written by source systems';

CREATE EXTERNAL LOCATION IF NOT EXISTS rideops_dev_lake
  URL 's3://rideops-dev-processed-<suffix>/'
  WITH (STORAGE CREDENTIAL rideops_s3_credential)
  COMMENT 'Managed storage for the RideOps catalog';

-- 2. Catalog and schemas (one schema per medallion layer, plus ops and the DLT target).
CREATE CATALOG IF NOT EXISTS rideops_dev
  MANAGED LOCATION 's3://rideops-dev-processed-<suffix>/uc'
  COMMENT 'RideOps development environment';

CREATE SCHEMA IF NOT EXISTS rideops_dev.landing COMMENT 'Volumes over raw source files';
CREATE SCHEMA IF NOT EXISTS rideops_dev.bronze  COMMENT 'Raw records + ingestion metadata';
CREATE SCHEMA IF NOT EXISTS rideops_dev.silver  COMMENT 'Validated, deduplicated entities';
CREATE SCHEMA IF NOT EXISTS rideops_dev.gold    COMMENT 'Business aggregates for BI and the API';
CREATE SCHEMA IF NOT EXISTS rideops_dev.ops     COMMENT 'Data-quality results and platform metadata';
CREATE SCHEMA IF NOT EXISTS rideops_dev.dlt     COMMENT 'Lakeflow Declarative Pipeline target';

-- 3. Volumes: governed access to *files* (Auto Loader reads from these paths).
--    External volume: files stay in the raw bucket written by producers.
CREATE EXTERNAL VOLUME IF NOT EXISTS rideops_dev.landing.raw
  LOCATION 's3://rideops-dev-raw-<suffix>/landing'
  COMMENT 'Source drops: /Volumes/rideops_dev/landing/raw/<entity>/batch_date=YYYY-MM-DD/';

--    Managed volume: Unity Catalog owns the storage (checkpoints, schema tracking).
CREATE VOLUME IF NOT EXISTS rideops_dev.landing.checkpoints
  COMMENT 'Auto Loader schema locations and streaming checkpoints';

-- 4. Table properties used across the lakehouse.
ALTER SCHEMA rideops_dev.silver SET DBPROPERTIES ('owner_team' = 'data-platform');
