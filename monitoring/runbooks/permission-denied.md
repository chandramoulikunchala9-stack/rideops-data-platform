# Runbook: permission denied

**Symptoms:** `AccessDeniedException ... not authorized to perform s3:GetObject`, Databricks
`PERMISSION_DENIED: User does not have SELECT on table`, or an HTTP 403.

## Triage
1. **Who** is the caller? Look for the IAM role ARN in the message, the job's `run_as`, or the service principal.
2. **What** action, on **which** resource? Note the bucket **and the prefix**: the policies here are
   scoped to prefixes.
3. **What changed?** A recent Terraform apply, a rotated role, a new prefix, or a grant revoked in Unity Catalog.
   ```bash
   aws sts get-caller-identity
   aws iam simulate-principal-policy --policy-source-arn <role-arn> \
     --action-names s3:GetObject --resource-arns arn:aws:s3:::<bucket>/landing/x.csv
   ```
   ```sql
   SHOW GRANTS `<principal>` ON SCHEMA rideops_prod.silver;   -- Databricks SQL
   ```

## Resolve
* Fix it **in code**, in `infra/terraform/iam.tf` or `databricks/unity_catalog/02_roles_and_grants.sql`.
  Open a PR, review the plan, then apply. Do not hand-edit policies in the console, because that creates drift.
* Grant the **narrowest** permission that fixes the problem (one action, one prefix). Never use `s3:*` or
  `ALL PRIVILEGES` just to "make it work".

## Verify
Re-run the pipeline, and use `simulate-principal-policy` to confirm only the intended access was added.

## Prevent
Keep all IAM changes in Terraform, validated in CI. Alert on spikes of `AccessDenied` errors in CloudTrail.
