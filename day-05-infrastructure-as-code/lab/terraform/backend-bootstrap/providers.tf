###############################################################################
# Day 05 — Infrastructure as Code
# backend-bootstrap/providers.tf
#
# THE CHICKEN AND THE EGG
#
# Every other directory in this lab stores its state in S3. This one cannot,
# because this one CREATES the S3 bucket that the others store their state in.
# A backend block here would tell Terraform "write your state to a bucket that
# does not exist yet", and `terraform init` would fail before it ever got the
# chance to create it.
#
# So this directory deliberately uses LOCAL state. There is no backend block
# below and that is not an oversight — it is the only order the universe
# permits. Note the consequence: terraform.tfstate for the bootstrap lands on
# your laptop. It contains the bucket name and ARN and nothing secret, which
# is exactly why bootstrapping only the backend here (and nothing else) is the
# right scope. Do not be tempted to "just add the VPC while I'm here."
#
# You have three honest options for the bootstrap state, in descending order
# of how much I like them:
#
#   1. Keep it local, commit NOTHING, and accept that recreating the bucket is
#      a two-minute job if you lose the file. This is what this lab does.
#   2. After the bucket exists, migrate the bootstrap's own state INTO it
#      (add a backend block, run `terraform init -migrate-state`). Elegant,
#      and now the bucket holds the state that describes the bucket. It works.
#      It is also a bootstrapping puzzle for whoever inherits it.
#   3. Create the bucket by hand with the AWS CLI once, and never manage it
#      with Terraform at all. Boring, and completely defensible.
#
# What is NOT an option is running this from a laptop, losing the file, and
# reaching for `terraform import` at 2 a.m.
###############################################################################

terraform {
  # required_version is not decoration. Without it, someone runs this with a
  # Terraform 1.2 binary, hits a syntax feature that did not exist yet, and
  # files a bug against your module. Pin the floor. IAC-010 checks for this.
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # >= 5.80 is a hard requirement for this lab, not a preference:
      # S3-native state locking (use_lockfile) landed in 5.80. Below that you
      # are back to a DynamoDB lock table. IAC-011 checks that this is pinned.
      version = "~> 5.80"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # NO backend block here. See the essay above.
  #
  # iac-audit: allow-local-state
  #
  # That marker is not decoration — ../../python/iac_audit.py reads it and
  # suppresses IAC-005 for this directory. Every audit tool needs a way to
  # say "I know, and here is why", and the only good place for that
  # declaration is in the code, next to the thing being suppressed, where it
  # shows up in a grep and in a diff. A suppression list in a YAML file three
  # directories away is a suppression list nobody will ever revisit.
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  # default_tags applies these to every taggable resource this provider
  # creates. Write them once here; never write Project/Day/ManagedBy/Owner on
  # an individual resource again. IAC-014 knows about default_tags and will
  # not flag resources in a directory whose provider sets them.
  default_tags {
    tags = {
      Project   = "aws-aiops-bootcamp"
      Day       = "05"
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

# S3 bucket names are globally unique across every AWS account on Earth, so a
# fixed name will collide the moment a second person runs this lab. Six random
# characters is the cheapest fix and it survives `terraform apply` because the
# value lives in state.
resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
  numeric = true
}

locals {
  prefix = "cbc-day05"
  suffix = random_string.suffix.result

  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
  partition  = data.aws_partition.current.partition

  # The one name that everything else in this lab depends on.
  state_bucket_name = "${local.prefix}-tfstate-${local.suffix}"
}
