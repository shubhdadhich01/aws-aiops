variable "aws_region" {
  description = "AWS region for regional resources. Budgets and IAM are global, but the provider still needs one."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for every resource name, so everything is greppable and easy to delete."
  type        = string
  default     = "cbc-day01"
}

variable "owner" {
  description = "Your name or team — used as the Owner tag for cost attribution."
  type        = string
  default     = "bootcamp-student"
}

variable "alert_email" {
  description = "Email address that receives budget alerts. You MUST confirm the SNS subscription email."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be a valid email address."
  }
}

variable "budget_limit_usd" {
  description = "Monthly budget ceiling in USD. Pick a number that would genuinely annoy you."
  type        = string
  default     = "10"
}

variable "create_bad_policy" {
  description = <<-EOT
    Create a deliberately over-permissive policy so the Python audit tool has something to find.
    Keep this true for the lab. It is NEVER attached to any identity — it just exists as a target.
  EOT
  type        = bool
  default     = true
}

variable "min_password_length" {
  description = "Minimum IAM console password length. CIS benchmark says 14."
  type        = number
  default     = 14

  validation {
    condition     = var.min_password_length >= 8 && var.min_password_length <= 128
    error_message = "min_password_length must be between 8 and 128."
  }
}

variable "max_password_age_days" {
  description = "Force console password rotation after this many days."
  type        = number
  default     = 90
}

variable "allowed_regions" {
  description = "Regions the scoped developer policy permits. Everything else is denied by condition."
  type        = list(string)
  default     = ["us-east-1"]
}
