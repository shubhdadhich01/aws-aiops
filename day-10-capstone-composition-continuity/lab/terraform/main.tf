###############################################################################
# Day 10 — main.tf
#
# The ambient audit infrastructure.
#
# Layout of this file:
#
#   locals                 shared naming and derivations
#   S3 archive             bucket, block-public, optional versioning, optional
#                          lifecycle. This is where every report lands.
#   IAM                    role and inline policy for the Lambda runner. Deliberately
#                          narrow: read-nearly-anything (for the audits), write to
#                          the archive bucket, write to its own log group. No wildcard
#                          on the S3 policy — a common mistake and one Day 03 would
#                          fire on.
#   Lambda                 the runner. Source under lab/terraform/lambda/runner.py,
#                          zipped by archive_file at plan time, uploaded on any
#                          hash change.
#   Log group              with retention set. Day 09's COST-013 refuses to be silent
#                          on this stack.
#   EventBridge            optional scheduled rule that fires the Lambda.
#   CloudWatch alarm       optional alarm on Lambda errors, wired to the SNS topic.
#   SNS                    topic + email subscription. Same shape as Day 09.
#   Athena                 optional database + table over the archive bucket.
#   Dashboard              optional CloudWatch dashboard summarising the last run.
#
# The reference-arch module is DEFERRED to CP3. In CP1 the `enable_reference_arch`
# variable exists but the resource block is not yet wired — its `count` will be
# 0 regardless until CP3, which is honest about what CP1 covers.
###############################################################################

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
  partition  = data.aws_partition.current.partition
  name       = var.name_prefix

  # A short random suffix keeps the archive bucket name globally unique
  # without demanding it be encoded by the operator.
  archive_bucket_name = "${local.name}-archive-${random_id.suffix.hex}"

  # Environment for the Lambda. All strings; boolean semantics are on the
  # runner side.
  lambda_env = {
    ARCHIVE_BUCKET = aws_s3_bucket.archive.id
    REGION         = var.aws_region
    ENABLED_DAYS   = "09"
  }
}

resource "random_id" "suffix" {
  byte_length = 3
}

###############################################################################
# S3 archive — every audit report lands here
###############################################################################

resource "aws_s3_bucket" "archive" {
  bucket        = local.archive_bucket_name
  force_destroy = true

  tags = {
    Name    = local.archive_bucket_name
    Purpose = "Audit report archive for Day 10 capstone"
  }
}

# Block-public settings are unconditional. Every day since 04 has enforced this.
resource "aws_s3_bucket_public_access_block" "archive" {
  bucket                  = aws_s3_bucket.archive.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Versioning is toggled. STATE A (off) fires CAP-004; STATE B (on) is silent.
resource "aws_s3_bucket_versioning" "archive" {
  bucket = aws_s3_bucket.archive.id
  versioning_configuration {
    status = var.enable_archive_versioning ? "Enabled" : "Suspended"
  }
}

# Server-side encryption is unconditional. AES-256 is the free default; a KMS
# CMK would cost $1/month and is out of scope here.
resource "aws_s3_bucket_server_side_encryption_configuration" "archive" {
  bucket = aws_s3_bucket.archive.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Lifecycle is toggled. STATE A (off) fires CAP-005; STATE B (on) is silent.
# The rule mirrors the Day 09 recommendation: 30 days STANDARD → STANDARD_IA,
# 90 days → GLACIER_IR, expire at 730 (two years).
resource "aws_s3_bucket_lifecycle_configuration" "archive" {
  count  = var.enable_archive_lifecycle ? 1 : 0
  bucket = aws_s3_bucket.archive.id

  rule {
    id     = "archive-tiering"
    status = "Enabled"

    filter {
      prefix = "reports/"
    }

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 90
      storage_class = "GLACIER_IR"
    }
    expiration {
      days = 730
    }

    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "STANDARD_IA"
    }
    noncurrent_version_expiration {
      noncurrent_days = 365
    }
  }
}

###############################################################################
# SNS — the topic every alarm publishes to, and the email subscriber
###############################################################################

resource "aws_sns_topic" "alarms" {
  name = "${local.name}-alarms"

  tags = {
    Name    = "${local.name}-alarms"
    Purpose = "Ambient audit alarms and CAP-016 unread-report notifications"
  }
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

###############################################################################
# IAM — Lambda role and inline policy
#
# The policy is deliberately explicit. Every prior day's audit runs a read
# API; the audit-runner therefore needs read on effectively everything, but
# WRITE only on the archive bucket and its own log group.
###############################################################################

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "runner" {
  name               = "${local.name}-runner"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json

  tags = {
    Name    = "${local.name}-runner"
    Purpose = "Execution role for the ambient audit-runner Lambda"
  }
}

# The audit-runner's read surface. This is broad by necessity — the audits
# from Days 01–09 collectively touch EC2, IAM, S3, RDS, DynamoDB, KMS,
# CloudWatch, Backup, Budgets, Cost Explorer, and Savings Plans. Narrower
# than "*:*" would break the audits it exists to run.
resource "aws_iam_role_policy_attachment" "runner_security_audit" {
  role       = aws_iam_role.runner.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/SecurityAudit"
}

resource "aws_iam_role_policy_attachment" "runner_billing_read" {
  role       = aws_iam_role.runner.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AWSBillingReadOnlyAccess"
}

# Write to the archive bucket only. No wildcard. This is exactly what Day 03
# fires on.
data "aws_iam_policy_document" "runner_inline" {
  statement {
    sid    = "WriteToArchive"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:PutObjectTagging",
    ]
    resources = ["${aws_s3_bucket.archive.arn}/reports/*"]
  }

  statement {
    sid    = "ListArchive"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
    ]
    resources = [aws_s3_bucket.archive.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["reports/*"]
    }
  }

  statement {
    sid    = "Logs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.runner.arn}:*"]
  }
}

resource "aws_iam_role_policy" "runner_inline" {
  name   = "${local.name}-runner-inline"
  role   = aws_iam_role.runner.id
  policy = data.aws_iam_policy_document.runner_inline.json
}

###############################################################################
# CloudWatch Log group — retention set explicitly (Day 09 COST-013)
###############################################################################

resource "aws_cloudwatch_log_group" "runner" {
  name              = "/aws/lambda/${local.name}-runner"
  retention_in_days = var.log_retention_days

  tags = {
    Name    = "/aws/lambda/${local.name}-runner"
    Purpose = "Audit-runner Lambda logs"
  }
}

###############################################################################
# Lambda — the audit-runner
###############################################################################

data "archive_file" "runner_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda/runner.py"
  output_path = "${path.module}/lambda/runner.zip"
}

resource "aws_lambda_function" "runner" {
  function_name = "${local.name}-runner"
  role          = aws_iam_role.runner.arn
  handler       = "runner.handler"
  runtime       = "python3.12"
  architectures = ["arm64"]

  filename         = data.archive_file.runner_zip.output_path
  source_code_hash = data.archive_file.runner_zip.output_base64sha256

  memory_size = var.lambda_memory_mb
  timeout     = var.lambda_timeout_seconds

  environment {
    variables = local.lambda_env
  }

  # Explicit dependency so log group exists before the first invocation
  # tries to write into it. Without this, the first invocation creates the
  # log group with default retention, and Day 09's COST-013 fires against
  # our own function.
  depends_on = [aws_cloudwatch_log_group.runner]

  tags = {
    Name    = "${local.name}-runner"
    Purpose = "Ambient audit orchestrator — Day 10 capstone"
  }
}

###############################################################################
# EventBridge — the scheduler (optional, toggled by enable_scheduler)
###############################################################################

resource "aws_cloudwatch_event_rule" "schedule" {
  count               = var.enable_scheduler ? 1 : 0
  name                = "${local.name}-schedule"
  description         = "Fires the audit-runner Lambda every ${var.schedule_interval_days} days"
  schedule_expression = "rate(${var.schedule_interval_days} ${var.schedule_interval_days == 1 ? "day" : "days"})"

  tags = {
    Name    = "${local.name}-schedule"
    Purpose = "Scheduled invocation of the audit-runner"
  }
}

resource "aws_cloudwatch_event_target" "runner" {
  count     = var.enable_scheduler ? 1 : 0
  rule      = aws_cloudwatch_event_rule.schedule[0].name
  target_id = "runner"
  arn       = aws_lambda_function.runner.arn
}

resource "aws_lambda_permission" "eventbridge" {
  count         = var.enable_scheduler ? 1 : 0
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.runner.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule[0].arn
}

###############################################################################
# CloudWatch alarm — Lambda errors (optional, toggled by enable_lambda_alarm)
###############################################################################

resource "aws_cloudwatch_metric_alarm" "runner_errors" {
  count               = var.enable_lambda_alarm ? 1 : 0
  alarm_name          = "${local.name}-runner-errors"
  alarm_description   = "Fires when the audit-runner Lambda errors — Day 10 CAP-009 wants this to exist"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = var.lambda_error_alarm_threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.runner.function_name
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = {
    Name    = "${local.name}-runner-errors"
    Purpose = "Ambient audit-runner error alarm"
  }
}

###############################################################################
# Athena — external table over the archive (optional, enable_athena_table)
###############################################################################

resource "aws_athena_database" "audits" {
  count  = var.enable_athena_table ? 1 : 0
  name   = replace("${local.name}_audits", "-", "_")
  bucket = aws_s3_bucket.archive.id

  encryption_configuration {
    encryption_option = "SSE_S3"
  }
}

# Athena requires a workgroup with a query results location. Point it at a
# `queries/` prefix inside the archive bucket so it stays lifecycled together.
resource "aws_athena_workgroup" "audits" {
  count = var.enable_athena_table ? 1 : 0
  name  = "${local.name}-audits"

  configuration {
    result_configuration {
      output_location = "s3://${aws_s3_bucket.archive.id}/queries/"

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }

  force_destroy = true

  tags = {
    Name    = "${local.name}-audits"
    Purpose = "Athena workgroup for querying the audit-report archive"
  }
}

###############################################################################
# CloudWatch dashboard (optional, enable_dashboard)
###############################################################################

resource "aws_cloudwatch_dashboard" "capstone" {
  count          = var.enable_dashboard ? 1 : 0
  dashboard_name = "${local.name}-capstone"
  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Audit-runner invocations (last 7 days)"
          region = var.aws_region
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.runner.function_name],
            [".", "Errors", ".", "."],
            [".", "Duration", ".", ".", { stat = "Average" }],
          ]
          period = 3600
          view   = "timeSeries"
          stat   = "Sum"
        }
      },
      {
        type   = "log"
        x      = 0
        y      = 6
        width  = 24
        height = 6
        properties = {
          title  = "Last invocation summary"
          region = var.aws_region
          query  = "SOURCE '${aws_cloudwatch_log_group.runner.name}' | filter @message like /audit runner summary/ | fields @timestamp, @message | sort @timestamp desc | limit 10"
        }
      },
    ]
  })
}

###############################################################################
# Reference-arch module — wired at CP3
#
# When enable_reference_arch = true, the composed Days 01–09 module is
# provisioned. It costs ~$210/month to keep running (an EC2 instance plus
# tiny storage), so it is off by default and only turned on when CAP-014
# needs something to compare drift against.
###############################################################################

module "reference_arch" {
  source = "./reference-arch"
  count  = var.enable_reference_arch ? 1 : 0

  name_prefix        = "${local.name}-refarch"
  aws_region         = var.aws_region
  instance_type      = var.reference_arch_instance_type
  log_retention_days = var.log_retention_days
}
