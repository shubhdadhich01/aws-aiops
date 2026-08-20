###############################################################################
# Day 07 — Enterprise Cloud Security
# providers.tf — provider configuration, default tags, shared data sources
#
# Flat layout, like Day 06. Day 05 split into modules/ and envs/ because the
# layout WAS its subject; here the subject is what happens between a detector
# firing and something changing in your account without a human involved.
#
# Version floor is Day 05's: Terraform >= 1.10, AWS provider ~> 5.80. The
# provider floor matters here — `aws_securityhub_standards_control_association`
# and the newer GuardDuty feature blocks landed well after 5.0, and the
# failure mode of an older provider is a plan that silently omits them.
###############################################################################

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project   = "aws-aiops-bootcamp"
      Day       = "07"
      ManagedBy = "terraform"
      Owner     = var.owner
    }
  }
}

###############################################################################
# Shared data sources
###############################################################################

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_partition" "current" {}

# The default VPC, used only so the quarantine security group in section 9 has
# somewhere to live. If your account has no default VPC, set quarantine_vpc_id
# explicitly — the variable exists for exactly that case.
data "aws_vpc" "selected" {
  count = var.quarantine_vpc_id == "" ? 1 : 0

  default = true
}

resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
  numeric = true
}

locals {
  prefix = "cbc-day07"
  suffix = random_string.suffix.result

  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
  partition  = data.aws_partition.current.partition

  vpc_id = var.quarantine_vpc_id == "" ? data.aws_vpc.selected[0].id : var.quarantine_vpc_id

  # Names computed once. Several resources reference each of these — the trail
  # references the bucket, the bucket policy references the trail, the auditor
  # matches on the names — and a typo in one place that reads fine in another
  # is how security tooling ends up watching nothing.
  trail_name         = "${local.prefix}-trail-${local.suffix}"
  trail_bucket_name  = "${local.prefix}-trail-${local.account_id}-${local.suffix}"
  quarantine_sg_name = "${local.prefix}-quarantine-${local.suffix}"

  # The ARN CloudTrail will write under. Built here because the bucket policy
  # in section 1 has to name it BEFORE the trail in section 2 exists — which
  # is the small chicken-and-egg this day starts with, and the reason the
  # policy uses a constructed ARN rather than a resource reference.
  trail_arn = "arn:${local.partition}:cloudtrail:${local.region}:${local.account_id}:trail/${local.trail_name}"
}
