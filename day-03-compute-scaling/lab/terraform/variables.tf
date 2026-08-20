###############################################################################
# Day 03 — variables.tf
#
# Convention used throughout this repo: any variable that costs money says so
# in its description, with the actual figure. If you have to hunt a pricing page
# to understand a toggle, the toggle is badly documented.
###############################################################################

###############################################################################
# Identity & region
###############################################################################

variable "aws_region" {
  description = "AWS region for all Day 03 resources."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must be a valid region string, e.g. us-east-1."
  }
}

variable "aws_profile" {
  description = "Named AWS CLI profile used to authenticate. Day 01 created this."
  type        = string
  default     = "bootcamp"

  validation {
    condition     = length(var.aws_profile) > 0
    error_message = "aws_profile cannot be empty. Run `aws configure --profile bootcamp` first."
  }
}

variable "owner" {
  description = "Value for the Owner tag. Use your name or team so account-wide cost reports can attribute this spend to you."
  type        = string
  default     = "bootcamp-student"

  validation {
    condition     = length(var.owner) >= 2 && length(var.owner) <= 64
    error_message = "owner must be between 2 and 64 characters."
  }
}

###############################################################################
# Networking — Day 03 builds its OWN VPC so the lab is self-contained.
# It does not read Day 02 state. Destroying Day 03 destroys everything Day 03
# created and nothing else.
###############################################################################

variable "vpc_cidr" {
  description = "CIDR block for the Day 03 VPC. Deliberately different from Day 02's range so both can coexist and you could peer them later."
  type        = string
  default     = "10.30.0.0/16"

  validation {
    condition     = can(cidrnetmask(var.vpc_cidr))
    error_message = "vpc_cidr must be a valid IPv4 CIDR block, e.g. 10.30.0.0/16."
  }

  validation {
    condition     = tonumber(split("/", var.vpc_cidr)[1]) <= 20
    error_message = "vpc_cidr must be /20 or larger to leave room for the subnets this lab creates."
  }
}

variable "az_count" {
  description = "How many Availability Zones to spread across. 2 is the minimum for real HA. 3 costs slightly more (one NAT per AZ if you enable per-AZ NAT) but survives more."
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 4
    error_message = "az_count must be between 2 and 4. A value of 1 is not high availability — that is the whole point of today."
  }
}

variable "enable_nat_gateway" {
  description = <<-DESC
    COST-BEARING (~$32.40/month, $0.045/hour + $0.045/GB processed).

    Creates a single NAT Gateway so private-subnet instances can reach the
    internet to install packages. Set to false to save the money, but then the
    userdata cannot `dnf install` anything and the instances serve the
    fallback static page baked into the script.

    Honest guidance: leave it true for the ~3 hours of the lab (about $0.30)
    and then `terraform destroy`. Do not try to save $32/month on a stack you
    are going to delete this afternoon.
  DESC
  type        = bool
  default     = true
}

###############################################################################
# Compute
###############################################################################

variable "instance_type" {
  description = "COST-BEARING. EC2 instance type for the ASG. t3.micro is $0.0104/hour and free-tier eligible for the first 12 months (750 hours/month across all instances). t3.small doubles it."
  type        = string
  default     = "t3.micro"

  validation {
    condition     = can(regex("^[a-z][0-9][a-z]*\\.(nano|micro|small|medium|large|xlarge|[0-9]+xlarge)$", var.instance_type))
    error_message = "instance_type must look like a valid EC2 instance type, e.g. t3.micro."
  }
}

variable "instance_count" {
  description = <<-DESC
    COST-BEARING (~$0.0104/hour each on t3.micro, ~$7.49/month each).

    Desired capacity for the Auto Scaling Group, and also min_size.

    2 is the minimum that demonstrates high availability — one instance per AZ,
    so terminating one leaves the service up. Setting this to 1 makes the lab
    cheaper and makes the chaos test show a full outage, which is a legitimate
    thing to demonstrate once. Do not call it HA.
  DESC
  type        = number
  default     = 2

  validation {
    condition     = var.instance_count >= 1 && var.instance_count <= 4
    error_message = "instance_count must be between 1 and 4 to keep lab spend bounded."
  }
}

variable "asg_max_size" {
  description = "COST-BEARING at peak. Ceiling for the Auto Scaling Group. Must be greater than instance_count or your scaling policy can never act — a real and common misconfiguration."
  type        = number
  default     = 4

  validation {
    condition     = var.asg_max_size >= 1 && var.asg_max_size <= 6
    error_message = "asg_max_size must be between 1 and 6 to keep lab spend bounded."
  }
}

variable "root_volume_size_gb" {
  description = "COST-BEARING ($0.08/GB-month for gp3). Root EBS volume size per instance. 8 GB is enough for AL2023 plus a web server."
  type        = number
  default     = 8

  validation {
    condition     = var.root_volume_size_gb >= 8 && var.root_volume_size_gb <= 30
    error_message = "root_volume_size_gb must be between 8 and 30 GB."
  }
}

variable "enable_detailed_monitoring" {
  description = "COST-BEARING (~$2.10/month at this scale, $0.30 per metric per month). Enables 1-minute CloudWatch metrics instead of 5-minute. Target tracking reacts noticeably faster with this on — worth it for the lab so you can actually watch the scale-out happen."
  type        = bool
  default     = true
}

###############################################################################
# Scaling
###############################################################################

variable "target_cpu_utilization" {
  description = "Target value for the CPU target-tracking scaling policy. 50 means the ASG adds/removes instances to hold average CPU near 50%. Lower = more headroom = more cost."
  type        = number
  default     = 50

  validation {
    condition     = var.target_cpu_utilization >= 10 && var.target_cpu_utilization <= 90
    error_message = "target_cpu_utilization must be between 10 and 90. Below 10 you will never scale in; above 90 you will scale out too late to matter."
  }
}

variable "instance_warmup_seconds" {
  description = "How long a newly launched instance's metrics are EXCLUDED from the scaling aggregate. Set this to your real boot-to-healthy time plus ~30s. Too short causes a scaling storm; too long makes you slow. Measure it, do not guess."
  type        = number
  default     = 180

  validation {
    condition     = var.instance_warmup_seconds >= 30 && var.instance_warmup_seconds <= 900
    error_message = "instance_warmup_seconds must be between 30 and 900."
  }
}

variable "health_check_grace_period" {
  description = "Seconds after launch during which the ASG ignores health checks. If this is shorter than your boot time you get an infinite launch/terminate loop that bills you all night. 300 is generous and safe for AL2023 + httpd."
  type        = number
  default     = 300

  validation {
    condition     = var.health_check_grace_period >= 60 && var.health_check_grace_period <= 1800
    error_message = "health_check_grace_period must be between 60 and 1800 seconds. Below 60 you risk a launch loop."
  }
}

###############################################################################
# Load balancing
###############################################################################

variable "acm_certificate_arn" {
  description = <<-DESC
    Optional ACM certificate ARN. If set, the ALB gets a real HTTPS:443 listener
    and the HTTP:80 listener becomes a 301 redirect to it.

    Leave empty (the default) and the lab serves plain HTTP on port 80 — which
    deliberately leaves findings ASG-008 and ASG-009 in place so ha_audit.py has
    something real to catch. That is intentional, not an oversight.

    Getting a certificate requires a domain you control and a Route 53 or DNS
    validation record, which is out of scope for today.
  DESC
  type        = string
  default     = ""
}

variable "alb_deregistration_delay" {
  description = "Seconds the ALB waits for in-flight requests to finish before killing a draining target. Default AWS value is 300, which makes every scale-in and deploy feel glacial. 30 is right for a stateless HTTP app."
  type        = number
  default     = 30

  validation {
    condition     = var.alb_deregistration_delay >= 0 && var.alb_deregistration_delay <= 3600
    error_message = "alb_deregistration_delay must be between 0 and 3600 seconds."
  }
}

variable "enable_alb_access_logs" {
  description = "COST-BEARING (S3 storage + PUT requests, pennies at lab scale). Ships ALB access logs to a purpose-created S3 bucket. Off by default to keep teardown simple — a non-empty bucket blocks `terraform destroy` unless force_destroy is set, which it is here."
  type        = bool
  default     = false
}

variable "allowed_ingress_cidr" {
  description = "CIDR allowed to reach the ALB on port 80/443. Defaults to the whole internet because this is a public web tier. Set it to YOUR_IP/32 if you would rather not have the internet load-testing your lab."
  type        = string
  default     = "0.0.0.0/0"

  validation {
    condition     = can(cidrnetmask(var.allowed_ingress_cidr))
    error_message = "allowed_ingress_cidr must be a valid IPv4 CIDR block, e.g. 203.0.113.10/32."
  }
}

###############################################################################
# The deliberately-broken examples
###############################################################################

variable "create_insecure_examples" {
  description = <<-DESC
    COST-BEARING (~$0.0104/hour extra — adds one more t3.micro via a second ASG).

    Creates deliberately misconfigured resources so ha_audit.py has real
    findings to report:

      * cbc-day03-broken-asg           — single AZ, min_size 1, no scaling policy,
                                          health_check_type = "EC2", tiny grace
                                          period, single termination policy
      * cbc-day03-broken-lt            — IMDSv1 allowed (HttpTokens = optional),
                                          unencrypted root volume
      * the main ALB's HTTP:80 listener — forwards instead of redirecting, and
                                          there is no HTTPS listener

    Set to false for a clean-architecture reference build. Set to true (the
    default) for the teaching experience: run the auditor, see 10+ findings,
    fix them, watch the score fall.

    Never set this true in an account that holds anything real.
  DESC
  type        = bool
  default     = true
}
