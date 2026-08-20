###############################################################################
# Day 04 — Serverless Automation
# providers.tf — provider configuration, default tags, shared data sources
#
# Same shape as Day 03. Everything is tagged via default_tags; you should never
# write Project/Day/ManagedBy/Owner on an individual resource again.
#
# One Day 04 wrinkle: Lambda-created resources. The CloudWatch log group that
# Lambda creates for itself on first invocation is NOT created by Terraform, so
# it carries no tags and no retention. That is the single most expensive habit
# in serverless, and it is why this stack creates the log groups explicitly.
# See section 6 of main.tf.
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
      Day       = "04"
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

# Availability zones — Day 04 does not build a VPC (the whole point of
# serverless is that you do not have to), but the auditor reports on region
# context and the outputs reference it.
data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

# A short random suffix so S3 bucket names and SNS topics stay unique inside
# the account and you can run this lab twice without collisions.
resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
  numeric = true
}

locals {
  prefix = "cbc-day04"
  suffix = random_string.suffix.result

  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
  partition  = data.aws_partition.current.partition

  # Built once here so every ARN in main.tf reads the same way.
  arn_prefix = "arn:${data.aws_partition.current.partition}:"
}
