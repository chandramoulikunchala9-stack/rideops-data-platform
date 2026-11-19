# Least-privilege IAM. Each policy grants only the actions and prefixes one job needs.
#
#   landing_writer  source systems / CI: write new files to raw/landing/ (cannot read or delete)
#   pipeline        processing: read raw/landing/, read+write processed/, write analytics/
#   analytics_read  analysts / BI tools: read analytics/ only

data "aws_caller_identity" "current" {}

locals {
  raw       = aws_s3_bucket.this["raw"]
  processed = aws_s3_bucket.this["processed"]
  analytics = aws_s3_bucket.this["analytics"]
}

data "aws_iam_policy_document" "landing_writer" {
  statement {
    sid       = "WriteLandingFiles"
    actions   = ["s3:PutObject"]
    resources = ["${local.raw.arn}/landing/*"]
  }
  statement {
    sid       = "ListLandingPrefix"
    actions   = ["s3:ListBucket"]
    resources = [local.raw.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["landing/*"]
    }
  }
}

data "aws_iam_policy_document" "pipeline" {
  statement {
    sid       = "ReadLanding"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${local.raw.arn}/landing/*"]
  }
  statement {
    sid       = "ReadWriteLakehouse"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${local.processed.arn}/*"]
  }
  statement {
    sid       = "PublishAnalytics"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
    resources = ["${local.analytics.arn}/*"]
  }
  statement {
    sid       = "ListBuckets"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [local.raw.arn, local.processed.arn, local.analytics.arn]
  }
}

data "aws_iam_policy_document" "analytics_read" {
  statement {
    sid       = "ReadAnalytics"
    actions   = ["s3:GetObject"]
    resources = ["${local.analytics.arn}/*"]
  }
  statement {
    sid       = "ListAnalytics"
    actions   = ["s3:ListBucket"]
    resources = [local.analytics.arn]
  }
}

resource "aws_iam_policy" "landing_writer" {
  name   = "${local.name}-landing-writer"
  policy = data.aws_iam_policy_document.landing_writer.json
}

resource "aws_iam_policy" "pipeline" {
  name   = "${local.name}-pipeline"
  policy = data.aws_iam_policy_document.pipeline.json
}

resource "aws_iam_policy" "analytics_read" {
  name   = "${local.name}-analytics-read"
  policy = data.aws_iam_policy_document.analytics_read.json
}

# Analysts get access through group membership, never through per-user policies.
resource "aws_iam_group" "analysts" {
  name = "${local.name}-analysts"
}

resource "aws_iam_group_policy_attachment" "analysts" {
  group      = aws_iam_group.analysts.name
  policy_arn = aws_iam_policy.analytics_read.arn
}

# --- Databricks Unity Catalog storage credential role (optional) -------------------------
# Databricks assumes this role on behalf of Unity Catalog. The ExternalId (your Databricks
# account ID) prevents the "confused deputy" problem: other Databricks customers cannot make
# Databricks assume *your* role. Unity Catalog also requires the role to be self-assuming.

locals {
  uc_role_name = "${local.name}-unity-catalog"
  uc_role_arn  = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.uc_role_name}"
}

data "aws_iam_policy_document" "uc_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = [var.databricks_unity_catalog_principal, local.uc_role_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [var.databricks_account_id]
    }
  }
}

resource "aws_iam_role" "unity_catalog" {
  count = var.enable_unity_catalog_role ? 1 : 0

  name               = local.uc_role_name
  assume_role_policy = data.aws_iam_policy_document.uc_trust.json

  lifecycle {
    precondition {
      condition     = var.databricks_account_id != ""
      error_message = "Set databricks_account_id when enable_unity_catalog_role = true."
    }
  }
}

resource "aws_iam_role_policy_attachment" "unity_catalog" {
  count = var.enable_unity_catalog_role ? 1 : 0

  role       = aws_iam_role.unity_catalog[0].name
  policy_arn = aws_iam_policy.pipeline.arn
}

# --- GitHub Actions OIDC role (optional): CI gets short-lived credentials, no stored keys ---

resource "aws_iam_openid_connect_provider" "github" {
  count = var.github_repository != "" ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "github_trust" {
  count = var.github_repository != "" ? 1 : 0

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition { # only workflows running on main in this repository
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_ci" {
  count = var.github_repository != "" ? 1 : 0

  name                 = "${local.name}-github-ci"
  assume_role_policy   = data.aws_iam_policy_document.github_trust[0].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "github_ci" {
  count = var.github_repository != "" ? 1 : 0

  role       = aws_iam_role.github_ci[0].name
  policy_arn = aws_iam_policy.landing_writer.arn
}
