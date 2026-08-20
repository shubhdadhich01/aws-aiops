###############################################################################
# Day 10 — providers.tf
#
# Capstone: Composition & Continuity.
#
# Unlike Days 01–09 which each stood alone, Day 10 is the first day whose
# Terraform is ABOUT the auditor. The stack provisions:
#
#   - an S3 archive bucket where every audit report lands, versioned so the
#     history is queryable, lifecycled so the archive does not become
#     COST-014 in its own right;
#   - a Lambda that runs the audit on a schedule, imports each day's audit
#     module by path, and writes a report per invocation;
#   - an EventBridge rule that fires the Lambda on the interval named in
#     variables.tf;
#   - a CloudWatch alarm that shouts (via SNS) when the Lambda errors;
#   - optionally a CloudWatch dashboard and an Athena table over the S3
#     archive, both toggled by variables.
#
# The default_tags block matches every other day in this repo: same shape,
# same keys, same reasoning. `Day = "10"` is what lets Cost Explorer
# distinguish this day's spend from Days 01–09 when they are all applied to
# the same account, which they are on this day, deliberately.
#
# The `archive_file` data source is used to zip the Lambda source; the null
# provider is here for the trigger resource that forces a Lambda upload on
# any source change. Both are tiny; they save the `zip` shell out.
###############################################################################

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
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
      Project   = "careerbytecode-aws-bootcamp"
      Day       = "10"
      ManagedBy = "terraform"
      Owner     = var.owner
    }
  }
}
