###############################################################################
# modules/storage/main.tf — application data bucket, optional table, log group
#
# The point of this module in Day 05 is not S3. It is `prevent_destroy` and
# what happens when you mean it.
#
# Both stateful resources below carry prevent_destroy. That means
# `terraform destroy` on an environment using this module WILL FAIL, at plan
# time, before touching anything. That is the desired behaviour and it is also
# the thing that will trip you up at teardown. Read
# ../../../../teardown-checklist.md before you meet it in anger.
#
# Note the rule you cannot design around: prevent_destroy takes a LITERAL
# boolean. `prevent_destroy = var.protect` is a hard error — "Variables may
# not be used here" — because lifecycle is evaluated before variables are
# resolved. There is no toggle. You either mean it or you delete the block and
# apply, deliberately, as a reviewed change.
###############################################################################

resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
  numeric = true
}

locals {
  bucket_name = "${var.name_prefix}-data-${random_string.suffix.result}"
  table_name  = "${var.name_prefix}-data"
}

###############################################################################
# 1. The data bucket
###############################################################################

resource "aws_s3_bucket" "data" {
  bucket        = local.bucket_name
  force_destroy = var.force_destroy

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id

  versioning_configuration {
    status = var.enable_versioning ? "Enabled" : "Suspended"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      # SSE-S3. Free, on by default for new buckets since January 2023, and
      # declared explicitly here anyway — because "it is the default" is not
      # something an auditor can read out of your code, and defaults change.
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket = aws_s3_bucket.data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

###############################################################################
# 2. Lifecycle — where old versions and dead uploads go
#
# The two rules here cover the two ways an S3 bucket grows without anyone
# adding a file:
#
#   * non-current versions, retained forever by default once versioning is on
#   * incomplete multipart uploads, which do not appear in the object listing
#     at all and are billed as storage indefinitely
#
# Neither is visible in the console at a glance. Both are permanent.
###############################################################################

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  depends_on = [aws_s3_bucket_versioning.data]

  rule {
    id     = "expire-noncurrent-versions"
    status = var.enable_versioning ? "Enabled" : "Disabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_expiration_days
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = var.abort_incomplete_upload_days
    }
  }
}

###############################################################################
# 3. Optional DynamoDB table
#
# PAY_PER_REQUEST, not PROVISIONED. Provisioned capacity on a table nobody is
# using bills for capacity nobody is using, and the free tier's 25 WCU/25 RCU
# is the single most common source of "I thought DynamoDB was free".
#
# This is NOT a Terraform state lock table. State locking in this lab is
# S3-native (use_lockfile). See backend-bootstrap/main.tf for why the lock
# table is legacy.
###############################################################################

resource "aws_dynamodb_table" "data" {
  count = var.create_data_table ? 1 : 0

  name         = local.table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = var.enable_point_in_time_recovery
  }

  server_side_encryption {
    # AWS-owned key. Free. Set kms_key_arn and a customer-managed key if you
    # need the ability to revoke access to the data independently of IAM.
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name = local.table_name
  }
}

###############################################################################
# 4. Log group
#
# Explicit, with retention set. If you let AWS create a log group implicitly,
# it is created with retention "Never expire" and no tags, and it survives
# `terraform destroy` because Terraform never knew about it. That orphan is
# the single most common permanent leftover in an AWS account.
###############################################################################

resource "aws_cloudwatch_log_group" "data" {
  name              = "/aws/${var.name_prefix}/storage"
  retention_in_days = var.log_retention_days
}
