###############################################################################
# Day 05 — envs/prod/providers.tf
#
# This file is nearly identical to envs/dev/providers.tf. The duplication is
# the price of directory-per-environment, and it is a price worth paying —
# see the essay in envs/dev/providers.tf for the full argument against
# workspaces.
#
# What is NOT identical, and matters:
#
#   * Environment = "prod" in default_tags. Every resource this directory
#     creates is labelled prod in Cost Explorer without anyone remembering to.
#   * A separate state key, in backend.tf.
#   * A separate tfvars, with prod-sized values.
#   * An enable_prod_environment gate, because prod in this lab is opt-in and
#     doubles the footprint the moment it is switched on.
#
# In a real account this directory would also assume a DIFFERENT ROLE in a
# DIFFERENT ACCOUNT. That is the strongest argument of all for separate
# directories: the credentials that can change prod should not be the
# credentials sitting in your shell while you work on dev.
#
#   provider "aws" {
#     assume_role {
#       role_arn = "arn:aws:iam::222222222222:role/terraform-prod"
#     }
#   }
#
# Left out here because the bootcamp runs in one account, and pretending
# otherwise would mean shipping a role ARN nobody can assume.
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
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project     = "aws-aiops-bootcamp"
      Day         = "05"
      ManagedBy   = "terraform"
      Owner       = var.owner
      Environment = "prod"
    }
  }
}

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
  numeric = true
}

locals {
  environment = "prod"
  name_prefix = "cbc-day05-prod"
  suffix      = random_string.suffix.result

  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  # The gate. Everything expensive in this directory hangs off this one
  # boolean, and it is false by default. See variables.tf for the price.
  enabled = var.enable_prod_environment
}
