terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Day 05 replaces this local state with an S3 backend + DynamoDB locking.
  # For Day 02, local state is fine — but never commit terraform.tfstate.
}

provider "aws" {
  region = var.aws_region

  # Tags applied automatically to every taggable resource this provider creates.
  # From Day 02 onward this matters more than it did yesterday: networking creates
  # resources that COST money, and the Day tag is how you find them again.
  default_tags {
    tags = {
      Project   = "aws-aiops-bootcamp"
      Day       = "02"
      ManagedBy = "terraform"
      Owner     = var.owner
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Availability Zones that are actually usable in this region right now.
# Never hardcode "us-east-1a" — AZ names are randomised per account, and some
# AZs don't support every instance type.
data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}
