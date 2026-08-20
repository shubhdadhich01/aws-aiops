###############################################################################
# modules/network/variables.tf
#
# A module's variables ARE its API. Renaming one is a breaking change for
# every caller, so name them as if you cannot take it back — because you
# cannot, cheaply.
#
# Every variable has a type and a description. `type = any` is not a type, it
# is a decision to skip validation, and it turns a clear plan-time error into
# a confusing apply-time one.
###############################################################################

variable "name_prefix" {
  description = "Prefix for every resource name this module creates, e.g. cbc-day05-dev. The caller owns naming; the module never invents its own."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]{3,40}$", var.name_prefix))
    error_message = "name_prefix must be 3-40 characters of lowercase letters, digits and hyphens."
  }
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC. /16 gives you 65,536 addresses and room to add subnets later; /24 will feel clever right up until it does not."
  type        = string

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr must be a valid IPv4 CIDR block, e.g. 10.20.0.0/16."
  }

  validation {
    condition     = tonumber(split("/", var.vpc_cidr)[1]) <= 24
    error_message = "vpc_cidr must be /24 or larger (a smaller prefix number). Anything tighter cannot be subdivided across availability zones."
  }
}

variable "public_subnets" {
  description = "Map of availability zone name to CIDR block for public subnets, e.g. { \"us-east-1a\" = \"10.20.1.0/24\" }. Keyed by AZ on purpose: for_each over this map means adding a third AZ adds one subnet instead of renumbering the other two."
  type        = map(string)

  validation {
    condition     = length(var.public_subnets) >= 1
    error_message = "At least one public subnet is required — the internet gateway route has to live somewhere."
  }

  validation {
    condition     = alltrue([for cidr in values(var.public_subnets) : can(cidrhost(cidr, 0))])
    error_message = "Every value in public_subnets must be a valid IPv4 CIDR block."
  }
}

variable "private_subnets" {
  description = "Map of availability zone name to CIDR block for private subnets. May be empty — a lab that does not need private compute should not pay for the plumbing that serves it."
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for cidr in values(var.private_subnets) : can(cidrhost(cidr, 0))])
    error_message = "Every value in private_subnets must be a valid IPv4 CIDR block."
  }
}

variable "enable_nat_gateway" {
  description = "Create a NAT Gateway so private subnets can reach the internet outbound. COSTS ~$32.40/month ($0.045/hour) PLUS $0.045 per GB processed, and it is billed the moment it exists whether or not a single packet crosses it. This is the most expensive single toggle in the entire bootcamp and it is false by default for that reason. Private subnets without it still work perfectly for anything that does not need egress."
  type        = bool
  default     = false
}

variable "enable_flow_logs" {
  description = "Send VPC Flow Logs to CloudWatch Logs. COSTS ~$0.50/GB ingested plus storage; a quiet lab VPC produces well under 1 GB/month, so call it under $1.00/month here — but on a busy production VPC flow logs are routinely a three-figure line item. Off by default."
  type        = bool
  default     = false
}

variable "flow_log_retention_days" {
  description = "CloudWatch Logs retention for flow logs. Only used when enable_flow_logs = true. The AWS default is 'Never expire', which is how log groups quietly become the largest thing in an account."
  type        = number
  default     = 7

  validation {
    condition = contains(
      [1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653],
      var.flow_log_retention_days
    )
    error_message = "flow_log_retention_days must be one of the retention periods CloudWatch Logs accepts."
  }
}

variable "app_ingress_port" {
  description = "TCP port the application security group accepts from inside the VPC."
  type        = number
  default     = 80

  validation {
    condition     = var.app_ingress_port > 0 && var.app_ingress_port <= 65535
    error_message = "app_ingress_port must be a valid TCP port."
  }
}
