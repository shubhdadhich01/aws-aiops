###############################################################################
# Day 05 — envs/dev/providers.tf
#
# DIRECTORY PER ENVIRONMENT, NOT WORKSPACES
#
# There are two ways to run the same code against two environments.
#
#   terraform workspace new prod          # one directory, N state files
#   envs/dev/ and envs/prod/              # N directories, N state files
#
# Workspaces look tidier and are almost always the wrong choice for
# environments. Three reasons, in order of how much they hurt:
#
#   1. ONE BACKEND, ONE SET OF CREDENTIALS. Every workspace shares the same
#      backend block, so dev state and prod state live in the same bucket
#      under the same permissions. Anyone who can plan dev can read prod's
#      state, which contains prod's secrets.
#   2. THE DIFFERENCES HIDE IN CONDITIONALS. Environments genuinely differ,
#      so the code fills with `var.instance_type = terraform.workspace ==
#      "prod" ? "m5.large" : "t3.micro"`. Now the only way to know what prod
#      looks like is to mentally evaluate every ternary in the repo.
#   3. `terraform workspace select` IS ONE WORD FROM A DISASTER. Forget it,
#      and you apply dev's plan to prod. There is no directory in your prompt
#      to tell you which you are in. Whole postmortems have been written about
#      that single missing word.
#
# Directory-per-environment costs you some duplication — this file is nearly
# identical to envs/prod/providers.tf — and buys you a separate backend, a
# separate state file, separate IAM, a separate review, and a path on screen
# that says which environment you are about to change. That trade is not close.
#
# Workspaces ARE genuinely good for: short-lived per-developer or per-PR copies
# of the SAME environment. Same config, same risk profile, different name.
###############################################################################

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # The ROOT module pins tightly. Child modules permit a range (see any
      # modules/*/versions.tf). Root pins, modules permit — get that backwards
      # and adding a second module becomes a dependency-resolution puzzle.
      #
      # ~> 5.80 means >= 5.80.0, < 5.81.0. Combined with a committed
      # .terraform.lock.hcl, everyone and every CI run gets the same provider
      # binary and the same checksums.
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
      Environment = "dev"
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
  environment = "dev"
  name_prefix = "cbc-day05-dev"
  suffix      = random_string.suffix.result

  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
}
