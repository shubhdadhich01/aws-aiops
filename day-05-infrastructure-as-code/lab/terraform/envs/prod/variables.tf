###############################################################################
# Day 05 — envs/prod/variables.tf
#
# Same shape as envs/dev/variables.tf, different values, plus one gate.
# The variable precedence table is documented once, in envs/dev/variables.tf.
###############################################################################

variable "enable_prod_environment" {
  description = "THE GATE. False by default, and it is false for a reason. Prod in this lab is a second, complete copy of the dev stack: another VPC, another set of subnets, another data bucket, another log group, another state file. Switching this to true DOUBLES the resource count of Day 05 in one apply. As configured (no NAT gateway, no instances) that doubling costs about $0.02/month, because everything in the default footprint is free — but the moment prod is real, prod also wants enable_nat_gateway (+$32.40/month), instances (+$7.59/month each) and probably flow logs. Multi-environment is not expensive because a VPC costs money. It is expensive because every toggle you flip, you now flip twice."
  type        = bool
  default     = false
}

variable "aws_region" {
  description = "AWS region for this environment. Must match the region in backend.tf — the backend block cannot read variables, so they are kept in sync by hand."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must look like us-east-1 or eu-west-2."
  }
}

variable "aws_profile" {
  description = "AWS CLI named profile. In a real estate this would be a prod-only profile assuming a prod-only role in a prod-only account."
  type        = string
  default     = "bootcamp"

  validation {
    condition     = length(trimspace(var.aws_profile)) > 0
    error_message = "aws_profile must not be empty."
  }
}

variable "owner" {
  description = "Value for the Owner tag. On prod this should be a team, not a person — people leave, and an orphaned resource tagged with a former employee's name is how estates rot."
  type        = string
  default     = "bootcamp-student"

  validation {
    condition     = length(var.owner) >= 3 && length(var.owner) <= 64
    error_message = "owner must be between 3 and 64 characters."
  }
}

###############################################################################
# Network — note the CIDR does not overlap dev's
###############################################################################

variable "vpc_cidr" {
  description = "CIDR block for the prod VPC. Deliberately non-overlapping with dev's 10.20.0.0/16 so the two could be peered or joined to a transit gateway later without renumbering an entire environment. Overlapping CIDRs are the single most common reason two VPCs can never be connected."
  type        = string
  default     = "10.30.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr must be a valid IPv4 CIDR block."
  }
}

variable "public_subnets" {
  description = "Map of availability zone name to public subnet CIDR. Three AZs in prod against dev's two — that difference is data in a tfvars file, not a fork of the module."
  type        = map(string)
  default = {
    "us-east-1a" = "10.30.1.0/24"
    "us-east-1b" = "10.30.2.0/24"
    "us-east-1c" = "10.30.3.0/24"
  }

  validation {
    condition     = length(var.public_subnets) >= 2
    error_message = "Prod requires at least two availability zones. One AZ is not an environment, it is a single point of failure with a VPC around it."
  }
}

variable "private_subnets" {
  description = "Map of availability zone name to private subnet CIDR."
  type        = map(string)
  default = {
    "us-east-1a" = "10.30.11.0/24"
    "us-east-1b" = "10.30.12.0/24"
    "us-east-1c" = "10.30.13.0/24"
  }
}

variable "enable_nat_gateway" {
  description = "Create a NAT Gateway so prod private subnets have outbound internet. COSTS ~$32.40/month plus $0.045/GB processed, billed from creation. Real prod almost always needs this and usually needs ONE PER AZ (~$97/month for three) so an AZ failure does not take egress down everywhere. It is false here because this is a bootcamp, not because prod does not need it."
  type        = bool
  default     = false
}

variable "enable_flow_logs" {
  description = "VPC flow logs to CloudWatch. ~$0.50/month at lab traffic. On a real production VPC, flow logs are routinely a three-figure monthly line item — and routinely worth it the first time you have to answer 'what talked to that host'."
  type        = bool
  default     = false
}

###############################################################################
# Compute
###############################################################################

variable "instances" {
  description = "Map of logical name to instance settings. Empty by default so switching on prod does not silently start billing for compute. A t3.small is ~$15.18/month on-demand."
  type = map(object({
    instance_type     = string
    availability_zone = string
    root_volume_gb    = optional(number, 8)
  }))
  default = {}
}

variable "associate_public_ip" {
  description = "Public IPv4 on prod instances. ~$3.65/month each. Prod instances should sit in private subnets behind a load balancer and be reached with Session Manager, so the honest default here is false and staying false."
  type        = bool
  default     = false
}

###############################################################################
# Storage — prod turns on the things dev leaves off
###############################################################################

variable "enable_versioning" {
  description = "Object versioning on the prod data bucket. On, and not negotiable in prod."
  type        = bool
  default     = true
}

variable "noncurrent_version_expiration_days" {
  description = "Days to keep non-current object versions. Longer in prod than dev (90 vs 30) because the cost of an extra 60 days of small objects is pennies and the cost of not being able to recover a two-month-old overwrite is not."
  type        = number
  default     = 90

  validation {
    condition     = var.noncurrent_version_expiration_days >= 1
    error_message = "noncurrent_version_expiration_days must be at least 1."
  }
}

variable "create_data_table" {
  description = "Create the DynamoDB table in the storage module. PAY_PER_REQUEST — ~$0.00/month idle, $1.25 per million writes."
  type        = bool
  default     = false
}

variable "enable_point_in_time_recovery" {
  description = "Continuous DynamoDB backups, restorable to any second in the last 35 days. COSTS $0.20/GB-month of table size — cents at lab scale. On in prod, off in dev, and that asymmetry is the entire reason environments have separate tfvars."
  type        = bool
  default     = true
}
