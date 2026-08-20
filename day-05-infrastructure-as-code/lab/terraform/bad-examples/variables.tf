###############################################################################
#                    ██  WRONG ON PURPOSE  ██
#
# bad-examples/variables.tf
#
# FAULTS IN THIS FILE:
#   IAC-016  variable declared with no type and no description        LOW
###############################################################################

# IAC-016 — no type, no description.
#
# Two separate problems in three characters.
#
# No TYPE means Terraform infers one from whatever is supplied. Pass a string
# where the code expects a list and you do not get a plan-time error, you get
# a confusing apply-time one from deep inside a resource. `type = any` is not
# a fix; it is the same decision written down.
#
# No DESCRIPTION means that when a value is missing, `terraform plan` prompts
# for it with a blank line:
#
#     var.environment_name
#       Enter a value:
#
# and somebody types the wrong thing into production because there was
# nothing on screen telling them what it was for. The description is not
# documentation for the README, it is the prompt text.
variable "environment_name" {
}

# This one is fine, and is here so the check has to distinguish rather than
# flag every variable in the file.
variable "report_bucket_names" {
  description = "Names of the reporting buckets to create. See resources.tf for how NOT to iterate over this."
  type        = list(string)
  default     = ["reports-alpha", "reports-beta", "reports-gamma"]
}

variable "vpc_id" {
  description = "VPC the deliberately wide-open security group would attach to, if anything ever applied this directory. Nothing does."
  type        = string
  default     = "vpc-00000000000000000"
}
