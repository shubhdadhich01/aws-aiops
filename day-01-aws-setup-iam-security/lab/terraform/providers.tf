terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Day 05 replaces this local state with an S3 backend + DynamoDB locking.
  # For Day 01, local state is fine — but never commit terraform.tfstate.
}

provider "aws" {
  region = var.aws_region

  # Tags applied automatically to every taggable resource this provider creates.
  # This is what makes teardown and cost attribution possible.
  default_tags {
    tags = {
      Project   = "aws-aiops-bootcamp"
      Day       = "01"
      ManagedBy = "terraform"
      Owner     = var.owner
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
