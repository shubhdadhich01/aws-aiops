###############################################################################
# modules/storage/variables.tf
###############################################################################

variable "name_prefix" {
  description = "Prefix for every resource name this module creates, e.g. cbc-day05-dev. A six-character random suffix is appended to the bucket name because S3 names are globally unique."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]{3,40}$", var.name_prefix))
    error_message = "name_prefix must be 3-40 characters of lowercase letters, digits and hyphens. S3 bucket names allow nothing else."
  }
}

variable "enable_versioning" {
  description = "Keep every version of every object. Costs storage for each version, which is why the expiration rule below matters. Free to enable; unbounded to leave unmanaged. Set false only for genuinely disposable data."
  type        = bool
  default     = true
}

variable "noncurrent_version_expiration_days" {
  description = "Days to keep non-current object versions. Only meaningful when enable_versioning is true. This is the valve on the same silent-growth trap as the state bucket: versioning without expiration grows forever at $0.023/GB-month and nobody notices until it is measured in terabytes."
  type        = number
  default     = 30

  validation {
    condition     = var.noncurrent_version_expiration_days >= 1 && var.noncurrent_version_expiration_days <= 3650
    error_message = "noncurrent_version_expiration_days must be between 1 and 3650."
  }
}

variable "abort_incomplete_upload_days" {
  description = "Days before S3 aborts incomplete multipart uploads. Failed uploads are INVISIBLE in the console object listing and billed as storage forever. This is the cheapest lifecycle rule you will ever write and the one most often missing."
  type        = number
  default     = 7

  validation {
    condition     = var.abort_incomplete_upload_days >= 1
    error_message = "abort_incomplete_upload_days must be at least 1."
  }
}

variable "force_destroy" {
  description = "Allow `terraform destroy` to delete the bucket even when it still contains objects. FALSE in real life. True is a loaded gun; it exists so a lab can be torn down without a manual empty step."
  type        = bool
  default     = false
}

variable "create_data_table" {
  description = "Also create a DynamoDB table in PAY_PER_REQUEST mode. Costs ~$0.00/month idle — you pay $1.25 per million writes and $0.25 per million reads, and an idle table with no traffic bills nothing but the storage for its rows ($0.25/GB-month). It exists here so the module has a second stateful resource for the prevent_destroy lesson."
  type        = bool
  default     = false
}

variable "enable_point_in_time_recovery" {
  description = "Continuous backups for the DynamoDB table, restorable to any second in the last 35 days. Costs $0.20/GB-month of table size — pennies for a lab, and the difference between a bad afternoon and a lost dataset in production. Only used when create_data_table is true."
  type        = bool
  default     = false
}

variable "log_retention_days" {
  description = "Retention for the module's CloudWatch log group. The AWS default is 'Never expire', which is how log groups quietly become the largest thing in an account. There is no good reason to leave retention unset."
  type        = number
  default     = 7

  validation {
    condition = contains(
      [1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653],
      var.log_retention_days
    )
    error_message = "log_retention_days must be one of the retention periods CloudWatch Logs accepts."
  }
}
