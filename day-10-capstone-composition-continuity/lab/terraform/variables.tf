###############################################################################
# Day 10 — variables.tf
#
# Capstone variables. Grouped into six clusters:
#
#   1. Identity: region, profile, owner, notification_email — the same four
#      variables every day in this repo needs.
#   2. Naming: prefix, resource-name choices that keep the resources
#      distinguishable when Days 01–10 are all present in one account.
#   3. Ambient-audit toggles: enable_scheduler, enable_archive_versioning,
#      enable_archive_lifecycle, enable_lambda_alarm, enable_dashboard,
#      enable_athena_table. All default OFF. STATE A is what you get when
#      apply completes and none of these are true.
#   4. Ambient-audit thresholds: schedule_interval_days, alarm_threshold,
#      sla_days_by_severity. These are the DECLARED NUMBERS the auditor
#      cites back.
#   5. Reference-arch toggle: enable_reference_arch spawns the composed
#      Days-01–09 module (built in CP3). Off by default because it costs
#      about $7/day to keep running.
#   6. Cost callouts: log_retention_days, tag_coverage_threshold_percent —
#      the same two thresholds Day 09 introduced, propagated here because
#      Day 10's stack has to answer to Day 09 too.
#
# Deliberately DIFFERENT from prior days: there is no `create_insecure_examples`.
# There are no wrong-shaped resources to demonstrate. The whole day is
# about the INFRASTRUCTURE OF AUDITING, and the fault surface is presence-
# or-absence of that infrastructure, so the toggles are `enable_*` rather
# than a single insecure-examples switch.
###############################################################################

# ─────────────────────────────────────────────────────────────────────────────
# 1. Identity
# ─────────────────────────────────────────────────────────────────────────────

variable "aws_region" {
  description = "Regional resources go here. Billing APIs are always us-east-1 regardless (Day 09 established this)."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile. Day 01 created 'bootcamp'."
  type        = string
  default     = "bootcamp"
}

variable "owner" {
  description = "Owner tag value. Propagates via provider default_tags to every resource."
  type        = string
  validation {
    condition     = length(var.owner) > 0
    error_message = "owner must be non-empty. This is what makes 'Group by Owner' in Cost Explorer useful, and an empty owner tag is worse than no tag at all — it produces an 'Owner: (blank)' rollup that looks legitimate."
  }
}

variable "notification_email" {
  description = "Where the audit-runner error alarm and the CAP-016 unread-report notifications land."
  type        = string
  validation {
    condition     = can(regex("^[^@]+@[^@]+\\.[^@]+$", var.notification_email))
    error_message = "notification_email must look like an email. This day depends on messages arriving; if the address is wrong, every ambient-audit alarm is silent."
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. Naming
# ─────────────────────────────────────────────────────────────────────────────

variable "name_prefix" {
  description = "Resource name prefix. Day 10 uses cbc-day10-* consistently, following the convention from Days 01–09."
  type        = string
  default     = "cbc-day10"
}

# ─────────────────────────────────────────────────────────────────────────────
# 3. Ambient-audit toggles — default OFF, so STATE A is what apply produces
# ─────────────────────────────────────────────────────────────────────────────

variable "enable_scheduler" {
  description = "Create the EventBridge rule that fires the audit-runner Lambda on schedule_interval_days. Off by default — with it off, the CAP-001 finding fires (no scheduled invocation)."
  type        = bool
  default     = false
}

variable "enable_archive_versioning" {
  description = "Turn on S3 versioning for the audit-report archive. Off by default — with it off, CAP-004 fires. Versioning is what makes 'when did this finding first appear' answerable."
  type        = bool
  default     = false
}

variable "enable_archive_lifecycle" {
  description = "Attach a lifecycle rule to the audit-report archive so it does not become COST-014 in its own right. Off by default — with it off, CAP-005 fires."
  type        = bool
  default     = false
}

variable "enable_lambda_alarm" {
  description = "Create a CloudWatch alarm on the audit-runner Lambda's error metric, wired to the SNS topic. Off by default — with it off, CAP-009 fires. A Lambda that errors without an alarm is the ambient audit that stopped without anyone noticing."
  type        = bool
  default     = false
}

variable "enable_dashboard" {
  description = "Create a CloudWatch dashboard summarising the last audit invocation. Off by default — CAP-010 fires when off. The dashboard is not analytics; it is the one URL somebody clicks to answer 'is the audit programme working today'."
  type        = bool
  default     = false
}

variable "enable_athena_table" {
  description = "Create an Athena database + external table over the S3 archive so historical findings are queryable. Off by default — CAP-011 fires. Athena keeps the archive queryable without provisioning capacity."
  type        = bool
  default     = false
}

variable "enable_reference_arch" {
  description = "Provision the Days 01–09 reference architecture module (added in CP3), which by construction passes every prior day's audit at 100/100. Off by default because it costs ~$7/day to keep running. CAP-014 tests drift against it when it is on."
  type        = bool
  default     = false
}

# ─────────────────────────────────────────────────────────────────────────────
# 4. Ambient-audit thresholds — the DECLARED NUMBERS the auditor cites back
# ─────────────────────────────────────────────────────────────────────────────

variable "schedule_interval_days" {
  description = "How often the audit-runner Lambda fires, in days. CAP-002 fires when > 7. CAP-003 (silent-schedule) fires when the last invocation age > interval * 1.5."
  type        = number
  default     = 7
  validation {
    condition     = var.schedule_interval_days >= 1 && var.schedule_interval_days <= 30
    error_message = "schedule_interval_days must be in [1, 30]. Daily catches faults within 24 hours. Weekly is the honest floor for most orgs. Anything beyond 30 is aspirational."
  }
}

variable "lambda_error_alarm_threshold" {
  description = "How many Lambda errors within an hour trigger the CAP-009 alarm. Default 1 — any error should be visible."
  type        = number
  default     = 1
  validation {
    condition     = var.lambda_error_alarm_threshold >= 1
    error_message = "lambda_error_alarm_threshold must be >= 1."
  }
}

variable "sla_days_by_severity" {
  description = "Days-to-acknowledge SLA per severity, for CAP-013 (SLA-not-defined) and CAP-016 (report-unread-past-SLA). The auditor cites these numbers directly."
  type = object({
    critical = number
    high     = number
    medium   = number
    low      = number
  })
  default = {
    critical = 1
    high     = 3
    medium   = 7
    low      = 30
  }
  validation {
    condition     = var.sla_days_by_severity.critical <= var.sla_days_by_severity.high && var.sla_days_by_severity.high <= var.sla_days_by_severity.medium && var.sla_days_by_severity.medium <= var.sla_days_by_severity.low
    error_message = "SLA days must be monotonically non-decreasing across severities: critical <= high <= medium <= low. A HIGH with a shorter SLA than a CRITICAL is a category error."
  }
}

variable "suppression_review_days" {
  description = "How stale a suppression may be before CAP-012 fires. Suppressions are exceptions with a story; the story expires."
  type        = number
  default     = 90
  validation {
    condition     = var.suppression_review_days >= 30 && var.suppression_review_days <= 365
    error_message = "suppression_review_days must be in [30, 365]. Under 30 is churn; over 365 is 'we never review them'."
  }
}

variable "report_unread_days" {
  description = "How long a report can sit without acknowledgement before CAP-016 fires. This is the day's central check."
  type        = number
  default     = 7
  validation {
    condition     = var.report_unread_days >= 1 && var.report_unread_days <= 30
    error_message = "report_unread_days must be in [1, 30]."
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# 5. Reference-arch parameters (used when enable_reference_arch = true)
# ─────────────────────────────────────────────────────────────────────────────

variable "reference_arch_instance_type" {
  description = "Instance type for the reference-arch composed workload. Small default so the reference does not itself trigger COST-015. Only used when enable_reference_arch = true."
  type        = string
  default     = "t3.micro"
}

# ─────────────────────────────────────────────────────────────────────────────
# 6. Cost callouts propagated from Day 09
# ─────────────────────────────────────────────────────────────────────────────

variable "log_retention_days" {
  description = "CloudWatch log retention for the audit-runner Lambda's log group. Day 09's COST-013 fires against unbounded groups; this stack refuses to create one."
  type        = number
  default     = 30
  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_retention_days)
    error_message = "log_retention_days must be one of the values CloudWatch Logs allows."
  }
}

variable "tag_coverage_threshold_percent" {
  description = "Same as Day 09's COST-004 threshold. Not used by any CAP check directly, but the reference-arch module inherits it."
  type        = number
  default     = 90
  validation {
    condition     = var.tag_coverage_threshold_percent >= 0 && var.tag_coverage_threshold_percent <= 100
    error_message = "tag_coverage_threshold_percent must be a percentage."
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Lambda-side wiring
# ─────────────────────────────────────────────────────────────────────────────

variable "lambda_memory_mb" {
  description = "Memory allocated to the audit-runner Lambda. Audits iterate list responses and reasoning about tags is CPU-light, so 512 is generous. Raise if you audit large accounts."
  type        = number
  default     = 512
  validation {
    condition     = var.lambda_memory_mb >= 128 && var.lambda_memory_mb <= 3008 && var.lambda_memory_mb % 64 == 0
    error_message = "lambda_memory_mb must be a multiple of 64 between 128 and 3008."
  }
}

variable "lambda_timeout_seconds" {
  description = "Timeout for the audit-runner Lambda. Day 09 established that billing APIs are slow — Cost Explorer can take 20s in a busy account. 300 is defensive."
  type        = number
  default     = 300
  validation {
    condition     = var.lambda_timeout_seconds >= 60 && var.lambda_timeout_seconds <= 900
    error_message = "lambda_timeout_seconds must be in [60, 900]. The audit needs longer than a default 3s function; 15 minutes is Lambda's ceiling."
  }
}
