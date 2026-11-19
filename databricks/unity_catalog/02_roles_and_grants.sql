-- Role-based access for RideOps (least privilege). Groups are account-level groups, ideally
-- synced from the identity provider (SCIM), never individual users.
-- STATUS: written for Databricks; cloud verification pending.
--
-- | Role                    | Group                     | Can do                                            |
-- |-------------------------|---------------------------|---------------------------------------------------|
-- | Platform Administrator  | rideops-platform-admins   | own the catalog, manage grants, all data          |
-- | Data Engineer           | rideops-data-engineers    | read/write bronze, silver, gold, ops; read files  |
-- | Data Analyst            | rideops-data-analysts     | read gold (+ silver without PII columns)          |
-- | Read-only User          | rideops-readonly          | read gold only                                    |
--
-- Privileges are inherited downwards: a grant on a schema applies to all its current and
-- future tables. USE CATALOG / USE SCHEMA are needed to "enter" a container; they do not grant
-- data access on their own.

-- Ownership: the admin group, not a person, owns the catalog.
ALTER CATALOG rideops_dev OWNER TO `rideops-platform-admins`;

-- Data Engineers --------------------------------------------------------------------------
GRANT USE CATALOG ON CATALOG rideops_dev TO `rideops-data-engineers`;
GRANT USE SCHEMA, SELECT, MODIFY, CREATE TABLE ON SCHEMA rideops_dev.bronze TO `rideops-data-engineers`;
GRANT USE SCHEMA, SELECT, MODIFY, CREATE TABLE ON SCHEMA rideops_dev.silver TO `rideops-data-engineers`;
GRANT USE SCHEMA, SELECT, MODIFY, CREATE TABLE ON SCHEMA rideops_dev.gold   TO `rideops-data-engineers`;
GRANT USE SCHEMA, SELECT, MODIFY, CREATE TABLE ON SCHEMA rideops_dev.ops    TO `rideops-data-engineers`;
GRANT USE SCHEMA ON SCHEMA rideops_dev.landing TO `rideops-data-engineers`;
GRANT READ VOLUME ON VOLUME rideops_dev.landing.raw TO `rideops-data-engineers`;
GRANT READ VOLUME, WRITE VOLUME ON VOLUME rideops_dev.landing.checkpoints TO `rideops-data-engineers`;

-- Data Analysts ---------------------------------------------------------------------------
GRANT USE CATALOG ON CATALOG rideops_dev TO `rideops-data-analysts`;
GRANT USE SCHEMA, SELECT ON SCHEMA rideops_dev.gold TO `rideops-data-analysts`;
GRANT USE SCHEMA ON SCHEMA rideops_dev.silver TO `rideops-data-analysts`;
GRANT SELECT ON TABLE rideops_dev.silver.bookings TO `rideops-data-analysts`;
GRANT SELECT ON TABLE rideops_dev.silver.customers TO `rideops-data-analysts`;  -- PII masked below

-- Read-only users ---------------------------------------------------------------------------
GRANT USE CATALOG ON CATALOG rideops_dev TO `rideops-readonly`;
GRANT USE SCHEMA, SELECT ON SCHEMA rideops_dev.gold TO `rideops-readonly`;

-- Platform Administrators -------------------------------------------------------------------
GRANT ALL PRIVILEGES ON CATALOG rideops_dev TO `rideops-platform-admins`;

-- Production service principal used by jobs (run_as in databricks.yml) --------------------
GRANT USE CATALOG ON CATALOG rideops_prod TO `rideops-prod-sp`;
GRANT USE SCHEMA, SELECT, MODIFY, CREATE TABLE ON SCHEMA rideops_prod.bronze TO `rideops-prod-sp`;
GRANT USE SCHEMA, SELECT, MODIFY, CREATE TABLE ON SCHEMA rideops_prod.silver TO `rideops-prod-sp`;
GRANT USE SCHEMA, SELECT, MODIFY, CREATE TABLE ON SCHEMA rideops_prod.gold TO `rideops-prod-sp`;

-- Data access boundaries: column masks and row filters -------------------------------------
-- Column mask: only data engineers see full email addresses; everyone else sees ***@domain.
CREATE OR REPLACE FUNCTION rideops_dev.ops.mask_email(email STRING)
  RETURNS STRING
  RETURN CASE
    WHEN is_account_group_member('rideops-data-engineers') THEN email
    ELSE regexp_replace(email, '^[^@]+', '***')
  END;
ALTER TABLE rideops_dev.silver.customers ALTER COLUMN email SET MASK rideops_dev.ops.mask_email;
CREATE OR REPLACE FUNCTION rideops_dev.ops.mask_phone(phone STRING)
  RETURNS STRING
  RETURN CASE
    WHEN is_account_group_member('rideops-data-engineers') THEN phone
    ELSE concat('******', right(phone, 4))
  END;
ALTER TABLE rideops_dev.silver.customers ALTER COLUMN phone SET MASK rideops_dev.ops.mask_phone;

-- Row filter: regional analysts only see their cities (mapping kept in a governed table).
CREATE TABLE IF NOT EXISTS rideops_dev.ops.analyst_city_access (group_name STRING, city STRING);
-- The parameter is named row_city on purpose: inside the subquery an unqualified `city` would
-- resolve to a.city (the table column), making the condition always true.
CREATE OR REPLACE FUNCTION rideops_dev.ops.city_filter(row_city STRING)
  RETURNS BOOLEAN
  RETURN is_account_group_member('rideops-data-engineers')
      OR is_account_group_member('rideops-platform-admins')
      OR EXISTS (
          SELECT 1 FROM rideops_dev.ops.analyst_city_access a
          WHERE a.city = row_city AND is_account_group_member(a.group_name)
      );
ALTER TABLE rideops_dev.gold.daily_city_summary SET ROW FILTER rideops_dev.ops.city_filter ON (city);

-- Review what a group can do:
SHOW GRANTS `rideops-data-analysts` ON SCHEMA rideops_dev.gold;
