output "raw_bucket" {
  description = "Set as RIDEOPS_S3_RAW_BUCKET"
  value       = aws_s3_bucket.this["raw"].bucket
}

output "processed_bucket" {
  description = "Unity Catalog managed location / external location"
  value       = aws_s3_bucket.this["processed"].bucket
}

output "analytics_bucket" {
  value = aws_s3_bucket.this["analytics"].bucket
}

output "policy_arns" {
  description = "Least-privilege policies, to attach to roles/groups"
  value = {
    landing_writer = aws_iam_policy.landing_writer.arn
    pipeline       = aws_iam_policy.pipeline.arn
    analytics_read = aws_iam_policy.analytics_read.arn
  }
}

output "databricks_uc_role_arn" {
  description = "Use in CREATE STORAGE CREDENTIAL (databricks/unity_catalog/01_catalog_and_schemas.sql)"
  value       = one(aws_iam_role.unity_catalog[*].arn)
}

output "github_ci_role_arn" {
  description = "Use with aws-actions/configure-aws-credentials (role-to-assume)"
  value       = one(aws_iam_role.github_ci[*].arn)
}

output "billing_bucket" {
  value = one(aws_s3_bucket.billing[*].bucket)
}
