###############################################################################
# Day 09 — providers.tf
#
# One provider. Deliberately.
#
# Day 08 was multi-region because a DR posture that cannot see the DR region
# is not an audit. Day 09 is single-region because a cost posture spans an
# entire ACCOUNT rather than a single region, and the auditor solves that by
# calling the global billing APIs (AWS Budgets, Cost Explorer, Cost Anomaly
# Detection, Savings Plans) directly. Those APIs live at us-east-1 regardless
# of where the resources they describe run, which is why the auditor and this
# stack both pin us-east-1: a Cost Explorer client instantiated in another
# region will fail its first call with UnrecognizedClientException, and that
# is a real, expensive, cross-team debugging session that has happened.
#
# The tags applied here are the second half of Day 09's central point.
# Cost Allocation Tags are only useful if EVERY resource carries them, and
# `default_tags` on the provider is how you get that guarantee at the plan
# level rather than at the review-comment level. Check COST-004 exists
# because in most accounts the guarantee is a review comment.
###############################################################################

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  # Every resource this plan creates carries these four tags. That is how
  # COST-004 stays silent against this stack even when the auditor is asked
  # for 100% coverage — not by exception, but by there being no path through
  # the plan that omits them.
  #
  # If you fork this and drop `default_tags`, run cost_audit.py before you
  # commit: COST-004 will fire on every resource, because the moment tags are
  # a per-resource decision they are also a per-resource oversight.
  default_tags {
    tags = {
      Project   = "careerbytecode-aws-bootcamp"
      Day       = "09"
      ManagedBy = "terraform"
      Owner     = var.owner
    }
  }
}
