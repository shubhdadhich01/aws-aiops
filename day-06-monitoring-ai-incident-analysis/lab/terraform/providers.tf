###############################################################################
# Day 06 — Monitoring & AI-Powered Incident Analysis
# providers.tf — provider configuration, default tags, shared data sources
#
# Flat layout. Day 05 split into modules/ and envs/ because the subject WAS the
# layout; Day 06's subject is observability and one directory is the honest
# shape for it. Do not read the flatness as a step backwards — read Day 05's
# README on when a module earns its keep.
#
# Version floor is Day 05's: Terraform >= 1.10, AWS provider ~> 5.80. The
# provider floor is not decorative here. `aws_cloudwatch_log_group.log_group_class`
# (Standard vs Infrequent Access, the $0.50/GB vs $0.25/GB decision) landed in
# provider 5.30, and the Bedrock model-invocation-logging resources used in
# section 9 are newer still.
###############################################################################

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Root modules pin, child modules permit. There are no child modules
      # today, but the convention is worth keeping — the day you add one you
      # want the root already pinned.
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
      Day       = "06"
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

# A short random suffix so log group names and SNS topics stay unique inside
# the account. Log group names in particular are a real collision risk on this
# day: a log group is not deleted by `terraform destroy` if something outside
# Terraform recreated it, and re-running the lab into an existing group with
# "Never expire" retention is exactly the trap the day is about.
resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
  numeric = true
}

locals {
  prefix = "cbc-day06"
  suffix = random_string.suffix.result

  region     = data.aws_region.current.name
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  # The region the model actually runs in. Empty string means "the same region
  # as everything else", which is the only setting that does not need a
  # conversation with someone about data residency. Setting it to anything
  # else is check OBS-013 and Step 7 of the lab asks you to do it on purpose.
  bedrock_region = var.bedrock_region == "" ? data.aws_region.current.name : var.bedrock_region

  # THE MODEL ARN SHAPE — worth reading once, carefully.
  #
  #   arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-5-haiku-20241022-v1:0
  #                                             ^^
  #                                    the account field is EMPTY
  #
  # Foundation models are owned by the provider, not by you, so there is no
  # account ID in the ARN. Writing your account ID in there produces an IAM
  # policy that matches nothing, an AccessDeniedException, and forty minutes
  # of confusion — and the usual "fix" is to widen the policy to Resource:"*",
  # which is check OBS-014.
  #
  # Cross-region inference profiles are the exception and DO carry an account:
  #
  #   arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.anthropic.claude-3-5-haiku-20241022-v1:0
  #
  # If you switch to an inference profile you need BOTH ARNs in the policy:
  # the profile you invoke, and the foundation model in every region the
  # profile can route to. See main.tf section 10.
  bedrock_model_arn = "arn:${local.partition}:bedrock:${local.bedrock_region}::foundation-model/${var.bedrock_model_id}"

  # The CloudWatch namespace for every custom metric this stack publishes.
  #
  # One namespace per day, per system, is the convention worth adopting. The
  # failure mode on the other side is a namespace called "Custom" holding
  # metrics from nine unrelated systems, at which point nobody can answer
  # "what is this metric and who owns it" and nobody can safely delete
  # anything. Custom metrics cost $0.30 each per month and cannot be deleted
  # (see the note in main.tf section 4), so that mess is also a bill.
  metric_namespace = "CareerByteCode/Day06"

  # Names are computed here, once, because four different resources reference
  # each of them: the log group, its metric filters, the alarms built on those
  # metrics, the dashboard, the analyser's Logs Insights query, and the
  # auditor's expected-resource list. A typo in one place that reads fine in
  # another is the specific way observability stacks rot.
  workload_log_group_name   = "/${local.prefix}/workload-${local.suffix}"
  chaos_log_group_name      = "/aws/lambda/${local.prefix}-chaos-${local.suffix}"
  analyser_log_group_name   = "/aws/lambda/${local.prefix}-analyser-${local.suffix}"
  naive_log_group_name      = "/aws/lambda/${local.prefix}-naive-analyser-${local.suffix}"
  unretained_log_group_name = "/${local.prefix}/legacy-app-${local.suffix}"
}
