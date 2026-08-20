###############################################################################
# Day 03 — Compute Architecture & Intelligent Scaling
# providers.tf — provider configuration, default tags, shared data sources
#
# Everything in this stack is tagged automatically via default_tags. You should
# never write Project/Day/ManagedBy/Owner on an individual resource again —
# if you find yourself doing that, the tag belongs here instead.
###############################################################################

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  # default_tags applies these to every taggable resource this provider creates.
  # Two things to know:
  #   1. It does NOT apply to resources created *by* other resources — an EC2
  #      instance launched by an Auto Scaling Group is created by the ASG, not
  #      by Terraform, so it needs tag_specifications on the launch template.
  #      That is why you'll see tags repeated there and nowhere else.
  #   2. Tags set explicitly on a resource win over default_tags.
  default_tags {
    tags = {
      Project   = "aws-aiops-bootcamp"
      Day       = "03"
      ManagedBy = "terraform"
      Owner     = var.owner
    }
  }
}

###############################################################################
# Shared data sources
###############################################################################

# Which AZs can this account actually use in this region? Never hardcode
# "us-east-1a" — AZ names are randomised per account, and not every AZ supports
# every instance type. Filtering on opt-in-status excludes Local Zones and
# Wavelength Zones, which would otherwise break subnet creation.
data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

# Current account/region/partition — used to build ARNs and outputs without
# hardcoding an account number into the repo.
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_partition" "current" {}

# Latest Amazon Linux 2023 AMI.
#
# ⚠️ most_recent = true means a `terraform apply` three months from now can pick
# up a newer AMI and produce a new launch template version. In production, pin
# the AMI via SSM Parameter Store and bump it deliberately as part of a release.
# For a teaching lab, always-latest is the right trade.
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "state"
    values = ["available"]
  }
}

# A short random suffix so ALB/target-group names stay globally unique inside the
# account and you can run this lab twice without name collisions.
resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
  numeric = true
}

locals {
  prefix = "cbc-day03"
  suffix = random_string.suffix.result

  # Take the first N AZs the account can use. Two is the minimum for HA;
  # three is better if you're paying for it anyway.
  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
}
