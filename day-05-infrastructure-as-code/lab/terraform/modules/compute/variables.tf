###############################################################################
# modules/compute/variables.tf
###############################################################################

variable "name_prefix" {
  description = "Prefix for every resource name this module creates, e.g. cbc-day05-dev."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]{3,40}$", var.name_prefix))
    error_message = "name_prefix must be 3-40 characters of lowercase letters, digits and hyphens."
  }
}

variable "instances" {
  description = "Map of logical instance name to its settings. EMPTY BY DEFAULT, which means this module costs nothing until you ask it for something. Each t3.micro is ~$7.59/month on-demand in us-east-1 (750 hours/month free for the first 12 months of a new account, and only for one instance). Keyed by name so adding or removing one instance never renumbers the others."
  type = map(object({
    instance_type     = string
    availability_zone = string
    root_volume_gb    = optional(number, 8)
  }))
  default = {}

  validation {
    condition     = alltrue([for i in values(var.instances) : can(regex("^[a-z0-9]+[.][a-z0-9]+$", i.instance_type))])
    error_message = "Every instance_type must look like t3.micro or m5.large."
  }

  validation {
    condition     = alltrue([for i in values(var.instances) : i.root_volume_gb >= 8 && i.root_volume_gb <= 100])
    error_message = "root_volume_gb must be between 8 and 100. gp3 storage is $0.08/GB-month, so 100 GB is $8/month of disk nobody is using."
  }
}

variable "subnet_ids" {
  description = "Map of availability zone name to subnet ID, exactly as modules/network returns it. Each instance is placed by looking up its availability_zone in this map."
  type        = map(string)

  validation {
    condition     = length(var.subnet_ids) >= 1
    error_message = "At least one subnet is required."
  }
}

variable "security_group_ids" {
  description = "Security groups to attach to every instance. Comes from the network module; this module deliberately does not create its own, because a security group whose rules are split across two modules is a debugging nightmare."
  type        = list(string)

  validation {
    condition     = length(var.security_group_ids) >= 1
    error_message = "At least one security group is required."
  }
}

variable "associate_public_ip" {
  description = "Give instances a public IP. True for a public-subnet lab tier; false for anything real. Note that a public IPv4 address is now billed at $0.005/hour (~$3.65/month) EACH, even when attached — AWS started charging for this in February 2024 and a lot of old sandbox estates got quietly more expensive that month."
  type        = bool
  default     = false
}

variable "enable_ssm" {
  description = "Attach the AmazonSSMManagedInstanceCore managed policy so you can reach instances with Session Manager instead of SSH. Free. This is how you avoid opening port 22 to anything, ever."
  type        = bool
  default     = true
}

variable "root_volume_encrypted" {
  description = "Encrypt the root EBS volume. Free with the AWS-managed key. There is no reason to set this false; it exists as a variable only so the audit tooling has something to read."
  type        = bool
  default     = true
}

variable "user_data" {
  description = "Optional user-data script. Rendered as-is. Do NOT put secrets here — user data is readable by anything that can reach the instance metadata service, and it is stored in Terraform state in the clear."
  type        = string
  default     = ""
}
