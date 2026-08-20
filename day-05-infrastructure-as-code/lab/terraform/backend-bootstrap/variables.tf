###############################################################################
# Day 05 — backend-bootstrap/variables.tf
#
# Every variable here has a type and a description. That is not a style rule
# for its own sake — `terraform plan` prints the description when it prompts
# for a missing value, and a variable with no description prompts with a blank
# line, which is how people type the wrong thing into production. IAC-016
# flags variables missing either.
#
# Every cost-bearing toggle is priced IN ITS DESCRIPTION, in dollars, at
# us-east-1 on-demand rates. If a toggle costs money and the description does
# not say how much, the description is incomplete.
###############################################################################

variable "aws_region" {
  description = "AWS region for the state bucket. Keep it in the same region as the workloads to avoid cross-region GET latency on every plan."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must look like us-east-1 or eu-west-2."
  }
}

variable "aws_profile" {
  description = "AWS CLI named profile. Day 01 created 'bootcamp'."
  type        = string
  default     = "bootcamp"

  validation {
    condition     = length(trimspace(var.aws_profile)) > 0
    error_message = "aws_profile must not be empty. Use the profile name, not an access key."
  }
}

variable "owner" {
  description = "Value for the Owner tag. Use something a human can be paged at — 'admin' tells a future FinOps report nothing."
  type        = string
  default     = "bootcamp-student"

  validation {
    condition     = length(var.owner) >= 3 && length(var.owner) <= 64
    error_message = "owner must be between 3 and 64 characters."
  }
}

variable "state_bucket_force_destroy" {
  description = "Allow `terraform destroy` to delete the state bucket even when it still holds objects. FALSE in real life, always. Set true ONLY at the end of this lab when you are deliberately tearing the whole thing down. Costs nothing; risks everything."
  type        = bool
  default     = false
}

variable "noncurrent_version_expiration_days" {
  description = "Days to keep NON-CURRENT state file versions before S3 deletes them. This is the silent-growth valve: versioning is on (you want it), so every single apply writes a new version and keeps the old one FOREVER unless this rule exists. A busy repo writes hundreds of 200 KB state versions a month. At $0.023/GB-month that is pennies — but pennies that never stop, on data nobody will ever read past day 30."
  type        = number
  default     = 30

  validation {
    condition     = var.noncurrent_version_expiration_days >= 7 && var.noncurrent_version_expiration_days <= 365
    error_message = "noncurrent_version_expiration_days must be between 7 and 365. Below 7 you lose the ability to roll back a bad apply after a weekend."
  }
}

variable "abort_incomplete_upload_days" {
  description = "Days before S3 aborts incomplete multipart uploads in the state bucket. Failed uploads are invisible in the console and billed as storage forever. 7 days is plenty; the cost of leaving this unset is small, permanent and impossible to notice."
  type        = number
  default     = 7

  validation {
    condition     = var.abort_incomplete_upload_days >= 1
    error_message = "abort_incomplete_upload_days must be at least 1."
  }
}

variable "enable_kms_encryption" {
  description = "Encrypt the state bucket with a customer-managed KMS key instead of SSE-S3. COSTS $1.00/month for the key, plus $0.03 per 10,000 API calls. SSE-S3 (the default when this is false) is free and satisfies IAC-007. Choose true when you need a revocable, auditable key with its own grant policy — which is a real requirement the moment your state file describes production."
  type        = bool
  default     = false
}

variable "kms_deletion_window_days" {
  description = "Waiting period before a scheduled KMS key deletion completes. Only applies when enable_kms_encryption = true. You are NOT billed during the window. 7 is the legal minimum and the right choice for a lab."
  type        = number
  default     = 7

  validation {
    condition     = var.kms_deletion_window_days >= 7 && var.kms_deletion_window_days <= 30
    error_message = "kms_deletion_window_days must be between 7 and 30 (an AWS hard limit)."
  }
}
