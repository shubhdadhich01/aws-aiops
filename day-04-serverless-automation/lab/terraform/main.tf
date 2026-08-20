###############################################################################
# Day 04 — Serverless Automation
# main.tf — Automated Resource Compliance Scanner
#
# This file teaches as it goes. Read it top to bottom before you apply; the
# comments are the lesson and the resources are the exercise.
#
# WHAT GETS BUILT
#
#   EventBridge schedule ──┐
#                          ├──> Lambda (compliance scanner) ──> SNS ──> email
#   EventBridge CloudTrail ┘              │
#   rule (RunInstances,                   ├──> CloudWatch Logs (with retention)
#    CreateBucket,                        └──> SQS dead letter queue
#    CreateSecurityGroup,
#    AuthorizeSecurityGroupIngress)
#
#   Plus, when create_insecure_examples = true, a parallel universe of the same
#   architecture built badly, for serverless_audit.py to tear apart.
#
# COST: essentially zero. The only line item is the optional KMS key at
# $1/month prorated hourly. Everything else is inside a PERMANENT free tier —
# not the 12-month one. Day 04 is the cheap day, but read the comment on the
# CloudWatch log groups (section 6) before you decide that means you can skip
# the teardown.
###############################################################################


###############################################################################
# 1. PACKAGING
#
# Lambda wants a zip. The archive provider builds one at plan time from the
# source file, and its output_base64sha256 becomes the source_code_hash on the
# function — which is how Terraform knows to redeploy when you edit the Python
# and not when you don't.
#
# Without source_code_hash, Terraform compares only the S3 key or filename,
# sees no change, and your edited function never actually deploys. That
# specific confusion has cost more debugging hours than it has any right to.
###############################################################################

data "archive_file" "scanner" {
  type        = "zip"
  source_file = "${path.module}/lambda/compliance_scanner.py"
  output_path = "${path.module}/build/compliance_scanner.zip"
}

data "archive_file" "broken" {
  count = var.create_insecure_examples ? 1 : 0

  type        = "zip"
  source_file = "${path.module}/lambda/broken_function.py"
  output_path = "${path.module}/build/broken_function.zip"
}


###############################################################################
# 2. KMS — the only thing on this day's bill
#
# One customer-managed key, encrypting the SNS topic, the SQS queue and the
# Lambda environment variables.
#
# The key policy below is worth reading properly. A KMS key policy is not like
# other resource policies: it is the ROOT of authority for the key. If the key
# policy does not grant access, no IAM policy anywhere can grant it — this is
# the one place in AWS where an Allow in an IAM policy is genuinely
# insufficient on its own.
#
# That first statement giving the account root full access is not laziness. If
# you omit it you can create a key that nobody, including you, can administer
# or delete. AWS support cannot fix it either. Always include it.
###############################################################################

resource "aws_kms_key" "main" {
  count = var.enable_kms_encryption ? 1 : 0

  description             = "Day 04 — encrypts SNS topic, SQS DLQ and Lambda environment variables"
  deletion_window_in_days = var.kms_deletion_window_days

  # Rotates the backing key material annually at no extra cost. There is no
  # good reason to leave this off.
  enable_key_rotation = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EnableAccountRootPermissions"
        Effect = "Allow"
        Principal = {
          AWS = "arn:${local.partition}:iam::${local.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "AllowSNSAndSQSToUseTheKey"
        Effect = "Allow"
        Principal = {
          Service = ["sns.amazonaws.com", "sqs.amazonaws.com"]
        }
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey*",
        ]
        Resource = "*"
      },
      {
        Sid    = "AllowCloudWatchLogs"
        Effect = "Allow"
        Principal = {
          Service = "logs.${local.region}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt*",
          "kms:Decrypt*",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:Describe*",
        ]
        Resource = "*"
      },
    ]
  })

  tags = {
    Name = "${local.prefix}-key"
  }
}

# An alias so you can refer to the key by a human name in the console and in
# CLI commands, instead of a UUID nobody can remember.
resource "aws_kms_alias" "main" {
  count = var.enable_kms_encryption ? 1 : 0

  name          = "alias/${local.prefix}-${local.suffix}"
  target_key_id = aws_kms_key.main[0].key_id
}

locals {
  kms_key_arn = var.enable_kms_encryption ? aws_kms_key.main[0].arn : null

  # SNS and SQS take a key ID or alias rather than an ARN, and accept the
  # AWS-managed alias as a fallback when the customer key is switched off.
  sns_kms_key_id = var.enable_kms_encryption ? aws_kms_key.main[0].key_id : "alias/aws/sns"
  sqs_kms_key_id = var.enable_kms_encryption ? aws_kms_key.main[0].key_id : "alias/aws/sqs"
}


###############################################################################
# 3. SNS — where findings go
#
# One topic, one email subscription.
#
# The thing everyone trips over: an email subscription is NOT active when
# Terraform reports success. AWS sends a confirmation link and the subscription
# sits in PendingConfirmation until a human clicks it. Until then every
# publish succeeds and every message is silently discarded.
#
# Terraform cannot confirm it for you and will never show it as a problem.
# That is Step 3 of the lab, and it is the reason your first "working"
# alerting pipeline in production will not alert anyone.
###############################################################################

resource "aws_sns_topic" "findings" {
  name              = "${local.prefix}-findings-${local.suffix}"
  display_name      = "Day 04 Compliance Findings"
  kms_master_key_id = local.sns_kms_key_id

  tags = {
    Name = "${local.prefix}-findings"
  }
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.findings.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

# Restrict publishing to this account. The default SNS topic policy is already
# account-scoped, but writing it explicitly means a later `aws sns
# add-permission` by a well-meaning colleague shows up as Terraform drift
# rather than sailing through unnoticed.
resource "aws_sns_topic_policy" "findings" {
  arn = aws_sns_topic.findings.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowOwnAccountPublish"
        Effect = "Allow"
        Principal = {
          AWS = "*"
        }
        Action   = "SNS:Publish"
        Resource = aws_sns_topic.findings.arn
        Condition = {
          StringEquals = {
            "AWS:SourceAccount" = local.account_id
          }
        }
      },
    ]
  })
}


###############################################################################
# 4. SQS — the dead letter queue
#
# When an asynchronous Lambda invocation fails every retry, the event is
# discarded unless something catches it. This queue is that something.
#
# Why this matters more in serverless than elsewhere: there is no server to SSH
# into and no local file the failed payload landed in. If you do not capture
# the event, the only evidence the work was ever attempted is an Errors metric
# ticking up by one. You cannot replay what you did not keep.
#
# Setting message retention to the 14-day maximum is deliberate. A DLQ that
# expires before a human reads it is decoration.
###############################################################################

resource "aws_sqs_queue" "dlq" {
  name                      = "${local.prefix}-scanner-dlq-${local.suffix}"
  message_retention_seconds = var.dlq_message_retention_seconds

  # Encryption. sqs_managed_sse_enabled and kms_master_key_id are mutually
  # exclusive — setting both is a plan-time error.
  kms_master_key_id                 = local.sqs_kms_key_id
  kms_data_key_reuse_period_seconds = 300

  tags = {
    Name = "${local.prefix}-scanner-dlq"
  }
}

# Deny anything that is not TLS. SQS accepts plain HTTP by default and there is
# no toggle for it — a resource policy is the only way to enforce transport
# encryption on a queue.
resource "aws_sqs_queue_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "sqs:*"
        Resource  = aws_sqs_queue.dlq.arn
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      },
      {
        Sid    = "AllowLambdaToSendFailedEvents"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action   = "sqs:SendMessage"
        Resource = aws_sqs_queue.dlq.arn
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = local.account_id
          }
        }
      },
    ]
  })
}


###############################################################################
# 5. IAM — the execution role
#
# Least privilege, applied honestly. Compare every statement here against the
# broken role in section 12, which is a single Action "*" on Resource "*".
#
# Note the split: read-only Describe/List/Get across the services being audited,
# and write access to exactly three ARNs (the log group, the topic, the queue).
# A compliance scanner has no business being able to modify anything it audits,
# and building it that way means a compromised scanner is a disclosure incident
# rather than a destruction incident.
###############################################################################

resource "aws_iam_role" "scanner" {
  name = "${local.prefix}-scanner-role-${local.suffix}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
        # Confused-deputy guard: only Lambda in THIS account may assume it.
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = local.account_id
          }
        }
      },
    ]
  })

  tags = {
    Name = "${local.prefix}-scanner-role"
  }
}

resource "aws_iam_role_policy" "scanner_read" {
  name = "${local.prefix}-scanner-read"
  role = aws_iam_role.scanner.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadOnlyInspectionOfAuditedServices"
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeVolumes",
          "ec2:DescribeTags",
          "s3:ListAllMyBuckets",
          "s3:GetBucketPublicAccessBlock",
          "s3:GetBucketVersioning",
          "s3:GetEncryptionConfiguration",
          "s3:GetBucketTagging",
        ]
        # These particular Describe/List actions do not support
        # resource-level permissions — AWS requires "*". That is a real
        # constraint, not sloppiness, and it is exactly the kind of thing
        # a good auditor should NOT flag as a wildcard violation.
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_role_policy" "scanner_write" {
  name = "${local.prefix}-scanner-write"
  role = aws_iam_role.scanner.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "WriteOwnLogsOnly"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        # Scoped to this function's log group. Note there is no
        # logs:CreateLogGroup — the group is created by Terraform in
        # section 6, so the function does not need permission to make one.
        Resource = "${aws_cloudwatch_log_group.scanner.arn}:*"
      },
      {
        Sid      = "PublishFindingsToOwnTopicOnly"
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = aws_sns_topic.findings.arn
      },
      {
        Sid      = "SendFailedEventsToOwnDlqOnly"
        Effect   = "Allow"
        Action   = "sqs:SendMessage"
        Resource = aws_sqs_queue.dlq.arn
      },
    ]
  })
}

# KMS permissions, only when a customer-managed key is in play. Without this
# the function cannot decrypt its own environment variables and every
# invocation fails at cold start with an opaque KMSAccessDeniedException.
resource "aws_iam_role_policy" "scanner_kms" {
  count = var.enable_kms_encryption ? 1 : 0

  name = "${local.prefix}-scanner-kms"
  role = aws_iam_role.scanner.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
        ]
        Resource = aws_kms_key.main[0].arn
      },
    ]
  })
}

# X-Ray write access. AWS provides a managed policy for this and it is one of
# the few managed policies genuinely worth attaching: the action list is small,
# stable, and there is no resource to scope to.
resource "aws_iam_role_policy_attachment" "scanner_xray" {
  count = var.enable_xray_tracing ? 1 : 0

  role       = aws_iam_role.scanner.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AWSXRayDaemonWriteAccess"
}


###############################################################################
# 6. CLOUDWATCH LOGS — created explicitly, and this is the point
#
# ⚠️ READ THIS ONE.
#
# If you do not create the log group, Lambda creates it for you on first
# invocation — with retention set to "Never expire". Forever. At $0.03/GB-month
# storage on top of $0.50/GB ingestion.
#
# That is the most common untracked line item on an AWS bill, because:
#   * it does not appear in Terraform state, so `terraform destroy` leaves it
#     behind and you are billed after you think you deleted everything
#   * it is invisible in the Lambda console — you have to go to CloudWatch and
#     know to look
#   * it grows silently and slowly, so it never triggers an anomaly alert
#
# Creating the group here fixes all three: it gets retention, it gets tags, and
# `terraform destroy` actually removes it.
#
# The teardown checklist has an account-wide sweep for orphaned no-retention
# log groups from every lab you have ever run. Run it once and see what you
# find.
###############################################################################

resource "aws_cloudwatch_log_group" "scanner" {
  name              = "/aws/lambda/${local.prefix}-compliance-scanner-${local.suffix}"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${local.prefix}-scanner-logs"
  }
}

# NOTE: there is deliberately NO log group for the broken function. That is
# check CMP-005 and section 12 explains it.


###############################################################################
# 7. THE LAMBDA FUNCTION
#
# Everything the broken function in section 12 gets wrong, this gets right.
###############################################################################

resource "aws_lambda_function" "scanner" {
  function_name = "${local.prefix}-compliance-scanner-${local.suffix}"
  role          = aws_iam_role.scanner.arn

  filename         = data.archive_file.scanner.output_path
  source_code_hash = data.archive_file.scanner.output_base64sha256

  handler = "compliance_scanner.lambda_handler"
  runtime = var.lambda_runtime

  timeout     = var.lambda_timeout_seconds
  memory_size = var.lambda_memory_mb

  # -1 means unreserved. Anything else caps concurrency and makes a runaway
  # loop physically impossible. See the variable description — this is the
  # guard against the classic five-figure serverless bill.
  reserved_concurrent_executions = var.lambda_reserved_concurrency

  # Where events go when every retry has failed. Without this they are
  # discarded silently.
  dead_letter_config {
    target_arn = aws_sqs_queue.dlq.arn
  }

  tracing_config {
    mode = var.enable_xray_tracing ? "Active" : "PassThrough"
  }

  # Encrypts the environment variables below at rest with the customer-managed
  # key. Note this protects them at rest only — anyone with
  # lambda:GetFunctionConfiguration still reads them in plaintext through the
  # API. Which is why nothing sensitive is in here.
  kms_key_arn = local.kms_key_arn

  environment {
    variables = {
      SNS_TOPIC_ARN      = aws_sns_topic.findings.arn
      REQUIRED_TAG_KEYS  = join(",", var.required_tag_keys)
      SEVERITY_THRESHOLD = var.scan_severity_threshold
      RESOURCE_PREFIX    = local.prefix
      LOG_LEVEL          = "INFO"
    }
  }

  # Without this, Lambda races Terraform to create the log group on first
  # invocation and wins about half the time, leaving you with a group that has
  # no retention and is not in state.
  depends_on = [
    aws_cloudwatch_log_group.scanner,
    aws_iam_role_policy.scanner_write,
  ]

  tags = {
    Name = "${local.prefix}-compliance-scanner"
  }
}

# Asynchronous invocation behaviour. EventBridge invokes Lambda asynchronously,
# so this is what governs retries for BOTH rules in sections 8 and 9.
#
# maximum_retry_attempts defaults to 2 in AWS. The variable defaults to 1 here
# so Step 5 of the lab lands the event in the DLQ quickly rather than making
# you wait out the full exponential backoff.
resource "aws_lambda_function_event_invoke_config" "scanner" {
  function_name = aws_lambda_function.scanner.function_name

  maximum_retry_attempts       = var.lambda_max_retry_attempts
  maximum_event_age_in_seconds = 3600

  destination_config {
    on_failure {
      destination = aws_sqs_queue.dlq.arn
    }
  }
}


###############################################################################
# 8. EVENTBRIDGE — the scheduled sweep (proactive path)
#
# Fires on a rate() or cron() expression regardless of what is happening in the
# account. This is the backstop that catches whatever the reactive path missed.
###############################################################################

resource "aws_cloudwatch_event_rule" "scheduled_scan" {
  name                = "${local.prefix}-scheduled-scan-${local.suffix}"
  description         = "Periodic full-account compliance sweep"
  schedule_expression = var.schedule_expression

  # An EventBridge rule that exists but is disabled is check CMP-014 and a
  # genuinely common outage cause — somebody disables it during an incident
  # and nobody re-enables it. The infrastructure looks correct in every
  # diagram and console screenshot; it just does not run.
  state = var.enable_scheduled_scan ? "ENABLED" : "DISABLED"

  tags = {
    Name = "${local.prefix}-scheduled-scan"
  }
}

resource "aws_cloudwatch_event_target" "scheduled_scan" {
  rule      = aws_cloudwatch_event_rule.scheduled_scan.name
  target_id = "ComplianceScannerScheduled"
  arn       = aws_lambda_function.scanner.arn

  # Retry policy on the TARGET, which is separate from the Lambda-side retry
  # config in section 7. EventBridge retries delivery; Lambda retries
  # execution. They are different failure domains and both need configuring.
  retry_policy {
    maximum_event_age_in_seconds = 3600
    maximum_retry_attempts       = 2
  }

  dead_letter_config {
    arn = aws_sqs_queue.dlq.arn
  }

  # A constant payload so the handler can tell scheduled invocations apart
  # from a manual `aws lambda invoke` during the lab.
  input = jsonencode({
    scan_type = "scheduled-full-sweep"
    source    = "eventbridge-schedule"
  })
}

# Resource-based policy allowing EventBridge to invoke the function. This is
# not the same thing as the execution role: the role is what the function can
# DO, this is who is allowed to CALL it. Forgetting this produces a rule that
# fires forever with no visible effect and no error anywhere obvious.
resource "aws_lambda_permission" "allow_schedule" {
  statement_id  = "AllowExecutionFromEventBridgeSchedule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scanner.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.scheduled_scan.arn
}


###############################################################################
# 9. EVENTBRIDGE — the reactive CloudTrail rule (fast path)
#
# Fires within seconds of specific API calls. This is what makes Step 4 of the
# lab work: create a security group open to the world, get an email before you
# have finished reading the confirmation screen.
#
# ⚠️ PREREQUISITE PEOPLE MISS: "AWS API Call via CloudTrail" events only reach
# EventBridge if a CloudTrail trail is logging management events in this
# region. The CloudTrail *Event history* console view is not enough — that is a
# 90-day lookback, not a delivery mechanism. Section 10 creates the trail.
#
# Without a trail this rule is syntactically perfect, deploys cleanly, and
# never fires once. There is no warning and no error. It simply does nothing,
# and you conclude EventBridge is broken.
###############################################################################

resource "aws_cloudwatch_event_rule" "reactive_scan" {
  name        = "${local.prefix}-reactive-scan-${local.suffix}"
  description = "Fires on resource-creation API calls and inspects the new resource immediately"

  event_pattern = jsonencode({
    source        = ["aws.ec2", "aws.s3"]
    "detail-type" = ["AWS API Call via CloudTrail"]
    detail = {
      eventSource = ["ec2.amazonaws.com", "s3.amazonaws.com"]
      eventName = [
        "RunInstances",
        "CreateBucket",
        "CreateSecurityGroup",
        "AuthorizeSecurityGroupIngress",
      ]
    }
  })

  tags = {
    Name = "${local.prefix}-reactive-scan"
  }
}

resource "aws_cloudwatch_event_target" "reactive_scan" {
  rule      = aws_cloudwatch_event_rule.reactive_scan.name
  target_id = "ComplianceScannerReactive"
  arn       = aws_lambda_function.scanner.arn

  retry_policy {
    maximum_event_age_in_seconds = 3600
    maximum_retry_attempts       = 2
  }

  dead_letter_config {
    arn = aws_sqs_queue.dlq.arn
  }

  # No `input` here — unlike the scheduled rule, the handler needs the real
  # CloudTrail event payload to know which resource changed.
}

resource "aws_lambda_permission" "allow_reactive" {
  statement_id  = "AllowExecutionFromEventBridgeReactive"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scanner.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.reactive_scan.arn
}


###############################################################################
# 10. CLOUDTRAIL — the prerequisite for section 9
#
# One trail, management events only, writing to a dedicated bucket.
#
# Cost: the FIRST trail delivering management events to S3 is free. Additional
# trails are $2 per 100,000 events. S3 storage for a lab's worth of logs is a
# few cents. This is why the lab creates exactly one trail and no more.
#
# The bucket policy below is prescribed by AWS almost verbatim; CloudTrail
# validates it at trail-creation time and refuses to create the trail if the
# policy is wrong. If `terraform apply` fails here with
# InsufficientS3BucketPolicyException, the cause is nearly always that the
# bucket policy has not propagated yet — re-run apply and it succeeds.
###############################################################################

resource "aws_s3_bucket" "trail" {
  bucket = "${local.prefix}-trail-${local.account_id}-${local.suffix}"

  # Lets `terraform destroy` remove the bucket even though CloudTrail has
  # written objects into it. Without this, teardown fails on a non-empty
  # bucket and you delete it by hand.
  force_destroy = true

  tags = {
    Name = "${local.prefix}-trail"
  }
}

resource "aws_s3_bucket_public_access_block" "trail" {
  bucket = aws_s3_bucket.trail.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "trail" {
  bucket = aws_s3_bucket.trail.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "trail" {
  bucket = aws_s3_bucket.trail.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Expire the log objects so a forgotten bucket cannot grow indefinitely — the
# S3 equivalent of the log group retention lesson in section 6.
resource "aws_s3_bucket_lifecycle_configuration" "trail" {
  bucket = aws_s3_bucket.trail.id

  rule {
    id     = "expire-trail-logs"
    status = "Enabled"

    filter {}

    expiration {
      days = 30
    }

    noncurrent_version_expiration {
      noncurrent_days = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.trail]
}

resource "aws_s3_bucket_policy" "trail" {
  bucket = aws_s3_bucket.trail.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AWSCloudTrailAclCheck"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "s3:GetBucketAcl"
        Resource = aws_s3_bucket.trail.arn
        Condition = {
          StringEquals = {
            "aws:SourceArn" = "arn:${local.partition}:cloudtrail:${local.region}:${local.account_id}:trail/${local.prefix}-trail-${local.suffix}"
          }
        }
      },
      {
        Sid    = "AWSCloudTrailWrite"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.trail.arn}/AWSLogs/${local.account_id}/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl"  = "bucket-owner-full-control"
            "aws:SourceArn" = "arn:${local.partition}:cloudtrail:${local.region}:${local.account_id}:trail/${local.prefix}-trail-${local.suffix}"
          }
        }
      },
    ]
  })
}

resource "aws_cloudtrail" "main" {
  name           = "${local.prefix}-trail-${local.suffix}"
  s3_bucket_name = aws_s3_bucket.trail.id

  # Management events only. Data events (per-object S3 reads, per-function
  # Lambda invokes) are billed per event and can produce an enormous volume in
  # a busy account — leave them off unless you have a specific reason.
  include_global_service_events = true
  is_multi_region_trail         = false
  enable_log_file_validation    = true

  depends_on = [aws_s3_bucket_policy.trail]

  tags = {
    Name = "${local.prefix}-trail"
  }
}


###############################################################################
# 11. CLOUDWATCH ALARMS — watching the watcher
#
# A compliance scanner that has silently stopped running is worse than no
# scanner, because the empty inbox reads as "no violations" rather than "no
# scans". These two alarms are the difference.
###############################################################################

resource "aws_cloudwatch_metric_alarm" "scanner_errors" {
  alarm_name          = "${local.prefix}-scanner-errors-${local.suffix}"
  alarm_description   = "The compliance scanner is throwing errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  # Without this, a period with zero invocations reports "insufficient data"
  # and the alarm sits in a grey state forever rather than telling you
  # anything useful.
  treat_missing_data = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.scanner.function_name
  }

  alarm_actions = [aws_sns_topic.findings.arn]

  tags = {
    Name = "${local.prefix}-scanner-errors"
  }
}

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${local.prefix}-dlq-not-empty-${local.suffix}"
  alarm_description   = "Something failed every retry and landed in the dead letter queue"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.dlq.name
  }

  alarm_actions = [aws_sns_topic.findings.arn]

  tags = {
    Name = "${local.prefix}-dlq-not-empty"
  }
}


###############################################################################
# 12. DELIBERATELY BROKEN EXAMPLES
#
# Everything below is wrong on purpose and exists only so serverless_audit.py
# has real findings to report. Gated behind create_insecure_examples.
#
# Never set that variable true in an account holding anything real. The role
# below genuinely grants administrator access to a function whose source code
# lives in a public git repository.
###############################################################################

# ---- The role: Action "*" on Resource "*" ------------------------- CMP-004 --
#
# This is the single most common serious IAM finding in real accounts. It
# usually starts as "let's get it working and tighten it later", and later
# never arrives because nothing ever breaks to remind you.

resource "aws_iam_role" "broken" {
  count = var.create_insecure_examples ? 1 : 0

  name = "${local.prefix}-broken-role-${local.suffix}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
        # Note the absence of a SourceAccount condition here too.
      },
    ]
  })

  tags = {
    Name = "${local.prefix}-broken-role"
  }
}

resource "aws_iam_role_policy" "broken" {
  count = var.create_insecure_examples ? 1 : 0

  name = "${local.prefix}-broken-policy"
  role = aws_iam_role.broken[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ThisIsAdministratorAccessWithExtraSteps"
        Effect   = "Allow"
        Action   = "*"
        Resource = "*"
      },
    ]
  })
}

# ---- The function: five findings in one resource -------- CMP-001/2/6/7/9 --

resource "aws_lambda_function" "broken" {
  count = var.create_insecure_examples ? 1 : 0

  function_name = "${local.prefix}-broken-function-${local.suffix}"
  role          = aws_iam_role.broken[0].arn

  filename         = data.archive_file.broken[0].output_path
  source_code_hash = data.archive_file.broken[0].output_base64sha256

  handler = "broken_function.lambda_handler"
  runtime = var.lambda_runtime

  # CMP-006: the AWS default of 3 seconds, against a handler that sleeps for 5.
  # Every invocation times out. Reliably reproducible failure for Step 5.
  timeout     = 3
  memory_size = 128

  # CMP-007: unreserved concurrency. Nothing caps this function.
  reserved_concurrent_executions = -1

  # CMP-001: no dead_letter_config block. Failed async events vanish.

  # CMP-009: no tracing_config block, so tracing stays PassThrough.

  # CMP-002: secrets in plaintext environment variables, and no kms_key_arn so
  # they use the default service key. Read them with:
  #   aws lambda get-function-configuration --function-name <this>
  # These are obviously fake. The point is that a real one would be equally
  # readable to anyone holding a read-only policy.
  environment {
    variables = {
      API_KEY     = "sk-live-NOT-A-REAL-KEY-abcdef123456"
      DB_PASSWORD = "hunter2-also-not-real"
      DB_HOST     = "prod-db.internal.example.com"
    }
  }

  # CMP-005: deliberately NO depends_on and no aws_cloudwatch_log_group for
  # this function. Lambda will create /aws/lambda/<name> itself on first
  # invocation with retention "Never expire", and because Terraform does not
  # know about it, `terraform destroy` will leave it behind — billing you
  # after you believe everything is gone.
  #
  # Deleting it is part of the teardown checklist. That is not busywork; it is
  # the actual lesson.

  tags = {
    Name = "${local.prefix}-broken-function"
  }
}

# ---- The topic: no encryption, wildcard principal ------------ CMP-010/011 --

resource "aws_sns_topic" "broken" {
  count = var.create_insecure_examples ? 1 : 0

  name = "${local.prefix}-broken-topic-${local.suffix}"

  # CMP-010: no kms_master_key_id, so messages are unencrypted at rest.

  tags = {
    Name = "${local.prefix}-broken-topic"
  }
}

resource "aws_sns_topic_policy" "broken" {
  count = var.create_insecure_examples ? 1 : 0

  arn = aws_sns_topic.broken[0].arn

  # CMP-011: Principal "*" with NO condition narrowing it to this account.
  # Any AWS principal anywhere can publish to this topic. If you had an email
  # subscription on it, any stranger could send mail to your team from your
  # own alerting pipeline.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AnyoneAtAllMayPublish"
        Effect = "Allow"
        Principal = {
          AWS = "*"
        }
        Action   = "SNS:Publish"
        Resource = aws_sns_topic.broken[0].arn
      },
    ]
  })
}

# ---- The queue: no encryption, no redrive ------------------- CMP-012/013 --

resource "aws_sqs_queue" "broken" {
  count = var.create_insecure_examples ? 1 : 0

  name = "${local.prefix}-broken-queue-${local.suffix}"

  # CMP-012: neither sqs_managed_sse_enabled nor kms_master_key_id.
  sqs_managed_sse_enabled = false

  # CMP-013: no redrive_policy, so this queue has no dead letter queue of its
  # own. A poison message here is retried until it expires, forever.

  # Short retention as well — a failed message is gone in a day.
  message_retention_seconds = 86400

  tags = {
    Name = "${local.prefix}-broken-queue"
  }
}

# ---- The rule: created DISABLED, target has no DLQ ---------- CMP-014/015 --

resource "aws_cloudwatch_event_rule" "broken" {
  count = var.create_insecure_examples ? 1 : 0

  name        = "${local.prefix}-broken-rule-${local.suffix}"
  description = "Deliberately disabled — looks correct in every diagram, never runs"

  schedule_expression = "rate(1 day)"

  # CMP-014: DISABLED. This is the outage that survives a code review, an
  # architecture review and a screenshot in the runbook.
  state = "DISABLED"

  tags = {
    Name = "${local.prefix}-broken-rule"
  }
}

resource "aws_cloudwatch_event_target" "broken" {
  count = var.create_insecure_examples ? 1 : 0

  rule      = aws_cloudwatch_event_rule.broken[0].name
  target_id = "BrokenTargetNoDlq"
  arn       = aws_lambda_function.broken[0].arn

  # CMP-015: no retry_policy and no dead_letter_config. Delivery failures are
  # discarded with no record.
}

resource "aws_lambda_permission" "allow_broken" {
  count = var.create_insecure_examples ? 1 : 0

  statement_id  = "AllowExecutionFromBrokenRule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.broken[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.broken[0].arn
}
