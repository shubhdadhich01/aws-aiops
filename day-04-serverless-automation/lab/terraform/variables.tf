###############################################################################
# Day 04 — variables.tf
#
# Convention used throughout this repo: any variable that costs money says so
# in its description, with the actual figure.
#
# Day 04 is the cheap day. The entire stack is ~$1.01/month, and $1.00 of that
# is one optional KMS key. Everything else — Lambda, EventBridge, SNS, SQS,
# CloudWatch Logs at this volume — sits inside a PERMANENT free tier, not the
# 12-month one. That is not a typo and it is the reason serverless wins so many
# architecture arguments.
#
# Do not let that lull you. Read the two silent-growth warnings:
#   * log_retention_days  — log groups with no retention grow forever
#   * lambda_reserved_concurrency — a recursive Lambda bills at machine speed
###############################################################################

###############################################################################
# Identity & region
###############################################################################

variable "aws_region" {
  description = "AWS region for all Day 04 resources."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must be a valid region string, e.g. us-east-1."
  }
}

variable "aws_profile" {
  description = "Named AWS CLI profile used to authenticate. Day 01 created this."
  type        = string
  default     = "bootcamp"

  validation {
    condition     = length(var.aws_profile) > 0
    error_message = "aws_profile cannot be empty. Run `aws configure --profile bootcamp` first."
  }
}

variable "owner" {
  description = "Value for the Owner tag. Use your name or team so account-wide cost reports can attribute this spend to you."
  type        = string
  default     = "bootcamp-student"

  validation {
    condition     = length(var.owner) >= 2 && length(var.owner) <= 64
    error_message = "owner must be between 2 and 64 characters."
  }
}

###############################################################################
# Notification — the one variable you MUST set
###############################################################################

variable "notification_email" {
  description = <<-DESC
    Email address that receives compliance findings via SNS.

    This is the only variable with no usable default. Set it in
    terraform.tfvars before you apply.

    AWS sends a confirmation email the moment the subscription is created and
    the subscription stays in "PendingConfirmation" until you click the link.
    An unconfirmed subscription silently drops every message — which is Step 3
    of the lab, and a failure mode you will meet again in production.
  DESC
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.notification_email))
    error_message = "notification_email must be a valid email address, e.g. you@example.com."
  }
}

###############################################################################
# Scheduling — the proactive half of the architecture
###############################################################################

variable "schedule_expression" {
  description = <<-DESC
    EventBridge schedule for the periodic compliance sweep.

    rate(1 hour) is the default and is free at this volume. During the lab you
    do not want to wait an hour, so Step 3 invokes the function by hand instead
    of shortening this.

    Note the asymmetry you are about to learn: the SCHEDULED path catches drift
    that already happened, the REACTIVE path (section 5) catches it as it
    happens. Real compliance platforms run both, because the reactive path
    misses anything created while the rule was broken and the scheduled path is
    the backstop that finds it.
  DESC
  type        = string
  default     = "rate(1 hour)"

  validation {
    condition     = can(regex("^(rate\\(|cron\\()", var.schedule_expression))
    error_message = "schedule_expression must be a rate(...) or cron(...) expression."
  }
}

variable "enable_scheduled_scan" {
  description = "Whether the periodic sweep rule is ENABLED. Set false to study the reactive path in isolation. Leave true — an EventBridge rule that exists but is disabled is check CMP-014, and a very common real-world outage cause."
  type        = bool
  default     = true
}

###############################################################################
# Lambda configuration
###############################################################################

variable "lambda_runtime" {
  description = "Python runtime for the compliance scanner. Keep this current — an out-of-support runtime is check CMP-008 and eventually AWS stops letting you update the function at all."
  type        = string
  default     = "python3.12"

  validation {
    condition     = can(regex("^python3\\.(9|10|11|12|13)$", var.lambda_runtime))
    error_message = "lambda_runtime must be a supported Python runtime, e.g. python3.12."
  }
}

variable "lambda_timeout_seconds" {
  description = <<-DESC
    Timeout for the compliance scanner.

    The AWS default is 3 seconds, which is almost always wrong and is check
    CMP-006. A function that paginates describe calls across a real account
    needs far more. 60 is comfortable here.

    Cost note: you are billed for duration actually used, not the timeout. A
    generous timeout costs nothing extra — it only bounds your worst case.
    There is no reason to run tight timeouts to "save money"; you are just
    buying yourself pager alerts.
  DESC
  type        = number
  default     = 60

  validation {
    condition     = var.lambda_timeout_seconds >= 3 && var.lambda_timeout_seconds <= 900
    error_message = "lambda_timeout_seconds must be between 3 and 900 (15 minutes is the Lambda hard ceiling)."
  }
}

variable "lambda_memory_mb" {
  description = <<-DESC
    Memory for the compliance scanner. CPU scales linearly with memory, so this
    is really a speed dial.

    256 MB is the sweet spot for an API-bound function like this one: enough
    CPU that JSON parsing is not the bottleneck, not so much that you pay for
    idle cores while waiting on the network.

    Cost: free tier covers 400,000 GB-seconds/month PERMANENTLY. At 256 MB and
    ~5 seconds per run, an hourly schedule uses roughly 900 GB-seconds/month.
    You are using 0.2% of the free tier.
  DESC
  type        = number
  default     = 256

  validation {
    condition     = var.lambda_memory_mb >= 128 && var.lambda_memory_mb <= 3008
    error_message = "lambda_memory_mb must be between 128 and 3008 MB."
  }
}

variable "lambda_reserved_concurrency" {
  description = <<-DESC
    ⚠️ SILENT COST GROWTH GUARD. Read this one.

    Reserved concurrency caps how many copies of this function can run at once.
    Set to -1 for "unreserved" (the AWS default), which means this function can
    scale to your whole account limit — 1,000 concurrent executions by default.

    Why it matters: the classic serverless bill shock is a function that writes
    to the bucket that triggers it, or an EventBridge rule that fires on an API
    call the function itself makes. That is an infinite loop that scales to
    1,000 parallel invocations and bills at machine speed. People have woken up
    to five-figure bills from a two-line mistake.

    2 is plenty for this lab and makes a runaway physically impossible. Leaving
    it unreserved is check CMP-007.
  DESC
  type        = number
  default     = 2

  validation {
    condition     = var.lambda_reserved_concurrency == -1 || (var.lambda_reserved_concurrency >= 0 && var.lambda_reserved_concurrency <= 100)
    error_message = "lambda_reserved_concurrency must be -1 (unreserved) or between 0 and 100. Note that 0 means the function is throttled to a complete stop."
  }
}

variable "enable_xray_tracing" {
  description = "COST-BEARING but effectively free here (100,000 traces/month permanently free; this stack produces a few hundred). Enables AWS X-Ray active tracing so you can see where the function spends its time. Off is check CMP-009."
  type        = bool
  default     = true
}

###############################################################################
# Logging — the silent cost growth trap
###############################################################################

variable "log_retention_days" {
  description = <<-DESC
    ⚠️ SILENT COST GROWTH GUARD. Read this one too.

    CloudWatch Logs retention for the Lambda log groups.

    If you let Lambda create its own log group, retention is "Never expire".
    Ingestion is $0.50/GB and storage is $0.03/GB-month FOREVER. A chatty
    function in a forgotten account is the single most common line item people
    cannot explain on an AWS bill, because nobody thinks of log groups as
    infrastructure — they are invisible in the console until you go looking.

    This stack creates both log groups explicitly, with retention, precisely so
    that never happens. Missing or absent retention is check CMP-005.

    14 days is right for a lab. 30-90 is typical in production; anything longer
    belongs in S3 via a subscription filter, not in CloudWatch Logs.
  DESC
  type        = number
  default     = 14

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_retention_days)
    error_message = "log_retention_days must be one of the values CloudWatch Logs accepts: 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653."
  }
}

###############################################################################
# Reliability — the dead letter queue
###############################################################################

variable "dlq_message_retention_seconds" {
  description = "How long a failed event sits in the dead letter queue before SQS deletes it. 14 days (1209600) is the maximum and the right default — a DLQ that expires before a human reads it is decoration. Free tier covers 1,000,000 SQS requests/month permanently."
  type        = number
  default     = 1209600

  validation {
    condition     = var.dlq_message_retention_seconds >= 60 && var.dlq_message_retention_seconds <= 1209600
    error_message = "dlq_message_retention_seconds must be between 60 and 1209600 (14 days)."
  }
}

variable "lambda_max_retry_attempts" {
  description = "How many times EventBridge/Lambda async invocation retries before giving up and sending the event to the DLQ. AWS default is 2. Set to 0 to make Step 5 of the lab fail fast and land in the DLQ immediately instead of making you wait through the backoff."
  type        = number
  default     = 1

  validation {
    condition     = var.lambda_max_retry_attempts >= 0 && var.lambda_max_retry_attempts <= 2
    error_message = "lambda_max_retry_attempts must be 0, 1 or 2 — that is the range Lambda accepts for asynchronous invocation."
  }
}

###############################################################################
# Encryption — the ONLY line item on this day's bill
###############################################################################

variable "enable_kms_encryption" {
  description = <<-DESC
    COST-BEARING (~$1.00/month, prorated hourly, plus $0.03 per 10,000 requests).

    This is the entire Day 04 bill. One customer-managed KMS key, used to
    encrypt the SNS topic, the SQS dead letter queue, and the Lambda
    environment variables.

    Set false and those three resources fall back to AWS-managed keys
    (aws/sns, aws/sqs) or, for Lambda env vars, to the default service key.
    That is free, and it is genuinely fine for a lab.

    Set true (the default) and you learn why enterprises pay the dollar: with a
    customer-managed key you control the key policy, you get an auditable
    CloudTrail Decrypt event naming the caller, and you can revoke access to
    the data by revoking the grant without touching the data itself. With an
    AWS-managed key you have none of those three things.

    A dollar a month is the cheapest lesson in this bootcamp. Leave it on for
    the afternoon, then destroy — you will be billed a fraction of a cent.

    Note: KMS keys have a MANDATORY 7-30 day waiting period on deletion. See
    kms_deletion_window_days below and the teardown checklist.
  DESC
  type        = bool
  default     = true
}

variable "kms_deletion_window_days" {
  description = <<-DESC
    Waiting period before a scheduled KMS key deletion completes. AWS enforces
    a minimum of 7 days and there is no way around it — you cannot delete a KMS
    key immediately, ever.

    This surprises people at teardown: `terraform destroy` returns successfully
    and the key still exists in PendingDeletion for a week. That is correct
    behaviour, not a failed destroy, and you are NOT billed for a key in
    PendingDeletion state.

    7 is the minimum and the right choice for a lab. Production should use 30.
  DESC
  type        = number
  default     = 7

  validation {
    condition     = var.kms_deletion_window_days >= 7 && var.kms_deletion_window_days <= 30
    error_message = "kms_deletion_window_days must be between 7 and 30. AWS does not allow immediate KMS key deletion under any circumstances."
  }
}

###############################################################################
# What the scanner actually checks for
###############################################################################

variable "required_tag_keys" {
  description = "Tag keys the compliance scanner treats as mandatory on EC2 instances, S3 buckets and security groups. Anything missing one of these is reported as a violation. These match the repo-wide tagging convention from Day 01."
  type        = list(string)
  default     = ["Project", "Owner", "ManagedBy"]

  validation {
    condition     = length(var.required_tag_keys) > 0
    error_message = "required_tag_keys must contain at least one tag key, otherwise the scanner has nothing to enforce."
  }
}

variable "scan_severity_threshold" {
  description = "Minimum severity that triggers an SNS notification. Findings below this are logged but not emailed. Valid values: CRITICAL, HIGH, MEDIUM, LOW. Set to LOW during the lab so you actually receive mail; raise it in production or you will train your team to ignore the topic."
  type        = string
  default     = "LOW"

  validation {
    condition     = contains(["CRITICAL", "HIGH", "MEDIUM", "LOW"], var.scan_severity_threshold)
    error_message = "scan_severity_threshold must be one of CRITICAL, HIGH, MEDIUM, LOW."
  }
}

###############################################################################
# The deliberately-broken examples
###############################################################################

variable "create_insecure_examples" {
  description = <<-DESC
    FREE (a second Lambda function that is never invoked on a schedule costs
    nothing at rest; you pay only for the handful of manual invocations in
    Step 5 of the lab).

    Creates deliberately misconfigured resources so serverless_audit.py has
    real findings to report:

      * cbc-day04-broken-function   — no DLQ, plaintext secrets in environment
                                       variables, 3-second timeout against a
                                       5-second sleep, no reserved concurrency,
                                       no X-Ray tracing
      * its log group               — deliberately absent, so Lambda creates one
                                       with "Never expire" retention (CMP-005)
      * cbc-day04-broken-role       — IAM policy with Action "*" on Resource "*"
      * cbc-day04-broken-topic      — SNS topic with no encryption and a topic
                                       policy allowing Principal "*"
      * cbc-day04-broken-queue      — SQS queue with no encryption and no
                                       redrive policy
      * cbc-day04-broken-rule       — an EventBridge rule created in the
                                       DISABLED state, wired to a target with
                                       no DLQ and no retry policy

    Set to false for a clean-architecture reference build — Step 7 of the lab
    has you do exactly that and diff the plan. Set to true (the default) for
    the teaching experience: run the auditor, see exactly 14 findings across
    CMP-001…CMP-016 (CMP-008 and CMP-016 stay silent on this stack by design —
    a check set where everything fires teaches you nothing about false
    positives), fix them, watch the score climb from 0.

    Never set this true in an account that holds anything real. The broken role
    genuinely grants administrator access to a function whose source code is in
    a public git repository.
  DESC
  type        = bool
  default     = true
}
