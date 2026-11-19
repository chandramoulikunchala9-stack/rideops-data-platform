resource "random_id" "suffix" {
  byte_length = 3 # bucket names are global: a short random suffix avoids collisions
}

locals {
  name = "${var.project}-${var.environment}"
  # Tag values may only contain letters, numbers, spaces and + - = . _ : / @ (AWS rule; S3
  # rejected parentheses and commas with InvalidTag on the first real apply).
  buckets = {
    raw       = "Landing zone for files delivered by source systems"
    processed = "Lakehouse storage for bronze and silver - Unity Catalog managed location"
    analytics = "Gold extracts for BI tools"
  }
}

resource "aws_s3_bucket" "this" {
  for_each = local.buckets

  bucket        = "${local.name}-${each.key}-${random_id.suffix.hex}"
  force_destroy = var.environment == "dev" # dev buckets can be destroyed with their data; prod cannot

  tags = {
    layer       = each.key
    description = each.value
  }
}

# --- Security baseline for every bucket ---------------------------------------------------

resource "aws_s3_bucket_public_access_block" "this" {
  for_each = aws_s3_bucket.this

  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id
  rule {
    object_ownership = "BucketOwnerEnforced" # disables ACLs; access only through IAM policies
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256" # SSE-S3: no KMS request charges
    }
  }
}

data "aws_iam_policy_document" "tls_only" {
  for_each = aws_s3_bucket.this

  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      each.value.arn,
      "${each.value.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "tls_only" {
  for_each = aws_s3_bucket.this

  bucket     = each.value.id
  policy     = data.aws_iam_policy_document.tls_only[each.key].json
  depends_on = [aws_s3_bucket_public_access_block.this]
}

# --- Data protection and cost control ------------------------------------------------------

resource "aws_s3_bucket_versioning" "raw" {
  bucket = aws_s3_bucket.this["raw"].id
  versioning_configuration {
    status = "Enabled" # recover from accidental overwrite/delete of source files
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  bucket = aws_s3_bucket.this["raw"].id

  rule {
    id     = "tier-and-expire-landing"
    status = "Enabled"
    filter {
      prefix = "landing/"
    }
    transition {
      days          = var.raw_retention_days
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = var.raw_retention_days + 60
      storage_class = "GLACIER_IR"
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.raw]
}

resource "aws_s3_bucket_lifecycle_configuration" "processed" {
  bucket = aws_s3_bucket.this["processed"].id

  rule {
    id     = "expire-checkpoint-temp-files"
    status = "Enabled"
    filter {
      prefix = "tmp/"
    }
    expiration {
      days = 7
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}
