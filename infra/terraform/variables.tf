variable "project" {
  description = "Short project name used in resource names and tags"
  type        = string
  default     = "rideops"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod"
  }
}

variable "aws_region" {
  description = "Region for buckets and IAM-adjacent resources"
  type        = string
  default     = "ap-south-2"
}

variable "monthly_budget_usd" {
  description = "AWS Budget limit; alerts at 80% forecast and 100% actual"
  type        = number
  default     = 20
}

variable "budget_alert_emails" {
  description = "Who receives budget alerts (empty = budget created without notifications)"
  type        = list(string)
  default     = []
}

variable "raw_retention_days" {
  description = "Days before raw landing files move to cheaper storage"
  type        = number
  default     = 30
}

variable "enable_unity_catalog_role" {
  description = "Create the IAM role Databricks Unity Catalog assumes to read/write the buckets"
  type        = bool
  default     = false
}

variable "databricks_account_id" {
  description = "Databricks account ID, used as the sts:ExternalId (required if enable_unity_catalog_role)"
  type        = string
  default     = ""
}

variable "databricks_unity_catalog_principal" {
  description = "Databricks' Unity Catalog master role (from the Databricks docs for AWS)"
  type        = string
  default     = "arn:aws:iam::414351767826:role/unity-catalog-prod-UCMasterRole-14S5ZJVKOTYTL"
}

variable "github_repository" {
  description = "owner/repo allowed to assume the CI role via GitHub OIDC (empty = no CI role)"
  type        = string
  default     = ""
}

variable "enable_cur_export" {
  description = "Create a CUR 2.0 data export (real billing data, delivered daily to S3)"
  type        = bool
  default     = false
}
