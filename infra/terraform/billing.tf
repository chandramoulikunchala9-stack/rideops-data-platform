# --- Budget: the safety net against unexpected charges -------------------------------------
# AWS Budgets themselves are free for the first two budgets per account.

resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = ["user:project$${var.project}"] # only resources tagged project=rideops
  }

  dynamic "notification" {
    for_each = length(var.budget_alert_emails) > 0 ? [
      { type = "FORECASTED", threshold = 80 },
      { type = "ACTUAL", threshold = 100 },
    ] : []
    content {
      comparison_operator        = "GREATER_THAN"
      notification_type          = notification.value.type
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      subscriber_email_addresses = var.budget_alert_emails
    }
  }
}

# --- Optional: CUR 2.0 data export (real billing data for ops.cloud_costs) ----------------
# Loaded with: python -m rideops.costs load-cur s3://<billing-bucket>/cur2/

resource "aws_s3_bucket" "billing" {
  count = var.enable_cur_export ? 1 : 0

  bucket        = "${local.name}-billing-${random_id.suffix.hex}"
  force_destroy = var.environment == "dev"
}

resource "aws_s3_bucket_public_access_block" "billing" {
  count = var.enable_cur_export ? 1 : 0

  bucket                  = aws_s3_bucket.billing[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "billing_delivery" {
  count = var.enable_cur_export ? 1 : 0

  statement {
    sid     = "AllowBillingExportDelivery"
    actions = ["s3:PutObject", "s3:GetBucketPolicy"]
    resources = [
      aws_s3_bucket.billing[0].arn,
      "${aws_s3_bucket.billing[0].arn}/*",
    ]
    principals {
      type        = "Service"
      identifiers = ["bcm-data-exports.amazonaws.com", "billingreports.amazonaws.com"]
    }
    condition { # only deliveries for this account (confused-deputy protection)
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_s3_bucket_policy" "billing" {
  count = var.enable_cur_export ? 1 : 0

  bucket = aws_s3_bucket.billing[0].id
  policy = data.aws_iam_policy_document.billing_delivery[0].json
}

resource "aws_bcmdataexports_export" "cur2" {
  count = var.enable_cur_export ? 1 : 0

  export {
    name = "${local.name}-cur2"
    data_query {
      query_statement = join(" ", [
        "SELECT bill_billing_period_start_date, line_item_line_item_type,",
        "line_item_usage_start_date, line_item_product_code, line_item_usage_type,",
        "line_item_usage_amount, line_item_unblended_rate, line_item_unblended_cost,",
        "pricing_unit, resource_tags FROM COST_AND_USAGE_REPORT",
      ])
      table_configurations = {
        COST_AND_USAGE_REPORT = {
          TIME_GRANULARITY                      = "DAILY"
          INCLUDE_RESOURCES                     = "FALSE"
          INCLUDE_MANUAL_DISCOUNT_COMPATIBILITY = "FALSE"
          INCLUDE_SPLIT_COST_ALLOCATION_DATA    = "FALSE"
        }
      }
    }
    destination_configurations {
      s3_destination {
        s3_bucket = aws_s3_bucket.billing[0].bucket
        s3_prefix = "cur2"
        s3_region = var.aws_region
        s3_output_configurations {
          overwrite   = "OVERWRITE_REPORT"
          format      = "TEXT_OR_CSV"
          compression = "GZIP"
          output_type = "CUSTOM"
        }
      }
    }
    refresh_cadence {
      frequency = "SYNCHRONOUS"
    }
  }

  depends_on = [aws_s3_bucket_policy.billing]
}
