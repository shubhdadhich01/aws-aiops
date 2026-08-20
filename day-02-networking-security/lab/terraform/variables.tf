variable "aws_region" {
  description = "AWS region. VPCs are regional — everything you build today lives and dies here."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for every resource name, so everything is greppable and easy to delete."
  type        = string
  default     = "cbc-day02"
}

variable "owner" {
  description = "Your name or team — used as the Owner tag for cost attribution."
  type        = string
  default     = "bootcamp-student"
}

variable "vpc_cidr" {
  description = <<-EOT
    CIDR block for the VPC. /16 gives you 65,536 addresses and room to carve /24 subnets.
    Pick a range that will not collide with your office, your VPN, or any VPC you might
    peer with later. RFC1918 ranges: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16.
  EOT
  type        = string
  default     = "10.20.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr must be a valid IPv4 CIDR block, e.g. 10.20.0.0/16."
  }

  validation {
    condition     = tonumber(split("/", var.vpc_cidr)[1]) >= 16 && tonumber(split("/", var.vpc_cidr)[1]) <= 20
    error_message = "vpc_cidr prefix must be between /16 and /20. This lab carves six subnets out of it using cidrsubnet(cidr, 8, n) — anything smaller than /20 produces subnets AWS will reject."
  }
}

variable "az_count" {
  description = "How many Availability Zones to spread subnets across. 2 is the minimum for real HA."
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3. One AZ is not high availability; more than 3 triples your NAT bill for no lab benefit."
  }
}

variable "enable_nat_gateway" {
  description = <<-EOT
    💸 Create a NAT Gateway so private subnets can reach the internet outbound.

    THIS COSTS MONEY: roughly $0.045/hour (~$32/month) PLUS $0.045 per GB processed,
    and it bills from the moment it exists whether or not anything uses it.

    Set to false if you only want to run the assessment tool and study routing —
    the tool works fine either way. Set to true if you want the real thing.
  EOT
  type        = bool
  default     = true
}

variable "single_nat_gateway" {
  description = <<-EOT
    Put ONE NAT Gateway in the first AZ and route every private subnet through it.

    true  = ~$32/month, but the NAT's AZ is a single point of failure.
    false = one NAT per AZ (~$32/month EACH), which is what production does.

    Keep this true for the lab. The assessment tool will flag the SPOF, and that
    finding is the lesson.
  EOT
  type        = bool
  default     = true
}

variable "enable_flow_logs" {
  description = <<-EOT
    Enable VPC Flow Logs on the main VPC, delivered to CloudWatch Logs.

    Cost is small but non-zero: CloudWatch Logs ingestion is ~$0.50/GB. An idle lab
    VPC generates a few MB a day. The BAD VPC deliberately has flow logs OFF so the
    assessment tool has a VPC-014 finding to report.
  EOT
  type        = bool
  default     = true
}

variable "flow_logs_retention_days" {
  description = "CloudWatch Logs retention for flow logs. Short retention keeps the lab cheap."
  type        = number
  default     = 7

  validation {
    condition = contains(
      [1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653],
      var.flow_logs_retention_days
    )
    error_message = "flow_logs_retention_days must be one of the values CloudWatch Logs accepts (1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, ...)."
  }
}

variable "enable_interface_endpoints" {
  description = <<-EOT
    💸 Create interface VPC endpoints (PrivateLink) for SSM and EC2 Messages.

    Each interface endpoint costs ~$0.01/hour per AZ (~$7.30/month per AZ) plus data
    processing. The S3 GATEWAY endpoint is always created because it is FREE.

    Leave false unless you specifically want to see PrivateLink working.
  EOT
  type        = bool
  default     = false
}

variable "create_insecure_examples" {
  description = <<-EOT
    Create deliberately insecure networking resources so the Python assessment tool has
    something to find:
      • a security group with 0.0.0.0/0 on 22, 3389, 5432 and ALL traffic
      • a security group with ::/0 IPv6 ingress
      • an orphaned security group attached to nothing
      • a NACL that allows everything, with a shadowed deny rule below it
      • a subnet named "private" that actually routes to the Internet Gateway
      • a second VPC with NO flow logs

    None of these have compute in them, so they cost $0. Keep true for the lab.
  EOT
  type        = bool
  default     = true
}

variable "trusted_admin_cidr" {
  description = <<-EOT
    Your own public IP as a /32, used by the CORRECT bastion security group.
    Find it with:  curl -s https://checkip.amazonaws.com
    Never leave this as 0.0.0.0/0 — that is exactly what the tool flags as CRITICAL.
  EOT
  type        = string
  default     = "203.0.113.10/32"

  validation {
    condition     = can(cidrhost(var.trusted_admin_cidr, 0))
    error_message = "trusted_admin_cidr must be a valid IPv4 CIDR, e.g. 198.51.100.24/32."
  }

  validation {
    condition     = var.trusted_admin_cidr != "0.0.0.0/0"
    error_message = "trusted_admin_cidr must not be 0.0.0.0/0. The whole point of the bastion SG is that it is narrow."
  }
}

variable "app_port" {
  description = "TCP port the application tier listens on, reachable only from the load balancer SG."
  type        = number
  default     = 8080

  validation {
    condition     = var.app_port > 0 && var.app_port <= 65535
    error_message = "app_port must be a valid TCP port between 1 and 65535."
  }
}

variable "db_port" {
  description = "TCP port the data tier listens on, reachable only from the app SG. 5432 = PostgreSQL."
  type        = number
  default     = 5432

  validation {
    condition     = var.db_port > 0 && var.db_port <= 65535
    error_message = "db_port must be a valid TCP port between 1 and 65535."
  }
}
