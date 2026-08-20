###############################################################################
# Day 05 — envs/dev/variables.tf
#
# VARIABLE PRECEDENCE, HIGHEST WINS
#
#   1. -var and -var-file on the command line   (last one on the line wins)
#   2. *.auto.tfvars / *.auto.tfvars.json       (alphabetical order)
#   3. terraform.tfvars.json
#   4. terraform.tfvars
#   5. TF_VAR_<name> environment variables
#   6. the `default` in the variable block
#   7. an interactive prompt, if there is no default and nothing above supplied
#      a value
#
# Two things surprise people:
#
#   * TF_VAR_ env vars sit BELOW tfvars files, not above. Exporting
#     TF_VAR_owner and wondering why terraform.tfvars still wins is a
#     twenty-minute debugging session that this list prevents.
#   * `terraform.tfvars` is loaded automatically; any other .tfvars file needs
#     an explicit -var-file, EXCEPT files ending in .auto.tfvars.
#
# WHICH TO USE
#
#   terraform.tfvars    your local values. GITIGNORED. The default channel.
#   *.auto.tfvars       values you want applied automatically in CI. Careful:
#                       "automatic" means nobody sees them on the command line.
#   TF_VAR_             secrets in CI, where the value comes from a secret
#                       store and must not touch disk. The right tool for that
#                       job and the wrong one for everything else.
#   -var on the CLI     one-off overrides. Shows up in shell history, so never
#                       for secrets.
#
# All of them end up in the state file regardless. See backend.tf.
###############################################################################

variable "aws_region" {
  description = "AWS region for this environment. Must match the region in backend.tf — the backend block cannot read this variable, so they are kept in sync by hand."
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
    error_message = "aws_profile must not be empty."
  }
}

variable "owner" {
  description = "Value for the Owner tag. Applied to everything through provider default_tags."
  type        = string
  default     = "bootcamp-student"

  validation {
    condition     = length(var.owner) >= 3 && length(var.owner) <= 64
    error_message = "owner must be between 3 and 64 characters."
  }
}

###############################################################################
# Network
###############################################################################

variable "vpc_cidr" {
  description = "CIDR block for the dev VPC. Deliberately different from prod's so the two could be peered later without renumbering."
  type        = string
  default     = "10.20.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr must be a valid IPv4 CIDR block."
  }
}

variable "public_subnets" {
  description = "Map of availability zone name to public subnet CIDR."
  type        = map(string)
  default = {
    "us-east-1a" = "10.20.1.0/24"
    "us-east-1b" = "10.20.2.0/24"
  }

  validation {
    condition     = length(var.public_subnets) >= 1
    error_message = "At least one public subnet is required."
  }
}

variable "private_subnets" {
  description = "Map of availability zone name to private subnet CIDR. Free to create; only the NAT gateway that serves them costs money."
  type        = map(string)
  default = {
    "us-east-1a" = "10.20.11.0/24"
    "us-east-1b" = "10.20.12.0/24"
  }
}

variable "enable_nat_gateway" {
  description = "Create a NAT Gateway for the private subnets. COSTS ~$32.40/month plus $0.045/GB processed, billed from the moment it exists. This is the most expensive single toggle in the bootcamp. Dev does not need it — nothing in the dev private subnets makes outbound calls."
  type        = bool
  default     = false
}

variable "enable_flow_logs" {
  description = "VPC flow logs to CloudWatch. ~$0.50/month at lab traffic levels; three figures a month on a busy production VPC."
  type        = bool
  default     = false
}

###############################################################################
# Compute
###############################################################################

variable "instances" {
  description = "Map of logical name to instance settings. EMPTY by default so a fresh dev apply costs $0.00 in compute. One t3.micro is ~$7.59/month on-demand (free for 750 hours/month in the first 12 months of a new account, one instance only)."
  type = map(object({
    instance_type     = string
    availability_zone = string
    root_volume_gb    = optional(number, 8)
  }))
  default = {}
}

variable "associate_public_ip" {
  description = "Give dev instances a public IPv4 address. ~$3.65/month EACH, charged since February 2024 whether traffic flows or not. Session Manager (enabled by default in the compute module) means you almost never need this."
  type        = bool
  default     = false
}

###############################################################################
# Storage
###############################################################################

variable "enable_versioning" {
  description = "Object versioning on the dev data bucket. Free to enable; the expiration rule below is what keeps it from growing forever."
  type        = bool
  default     = true
}

variable "noncurrent_version_expiration_days" {
  description = "Days to keep non-current object versions in the dev data bucket."
  type        = number
  default     = 30

  validation {
    condition     = var.noncurrent_version_expiration_days >= 1
    error_message = "noncurrent_version_expiration_days must be at least 1."
  }
}

variable "create_data_table" {
  description = "Create the DynamoDB table in the storage module. PAY_PER_REQUEST, so ~$0.00/month idle."
  type        = bool
  default     = false
}

###############################################################################
# The deliberately broken half
###############################################################################

variable "create_insecure_examples" {
  description = "Create a second, deliberately misconfigured 'state' bucket — no versioning, no encryption — so the LIVE checks in iac_audit.py (IAC-006, IAC-007) have something real to find. Costs ~$0.00. Set false and the auditor's live checks correctly report nothing, which is the boring, correct outcome and worth seeing at least once. The bucket still has a public access block: IAC-004 stays silent by design, and this repo does not ship a publicly readable bucket even as a teaching example."
  type        = bool
  default     = true
}
