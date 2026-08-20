###############################################################################
# Day 08 — High Availability & Disaster Recovery
# providers.tf — provider configuration, the DR alias, default tags, data sources
#
# Flat layout, like Days 06 and 07. Day 05 split into modules/ and envs/
# because the layout WAS its subject. Here the subject is the failover path,
# and a failover path spread across four module boundaries is a failover path
# nobody reads before the incident.
#
# Version floor is Day 05's: Terraform >= 1.10, AWS provider ~> 5.80.
#
# =========================== THE SECOND PROVIDER =============================
#
# This is the first day in the repo with two providers, and the reason is not
# cosmetic. A region is not a parameter you pass to a resource — it is a
# property of the PROVIDER, and the only way to put a resource in another
# region is to configure another provider and point the resource at it with
# `provider = aws.dr`.
#
# Three consequences that surprise people, all of which this day exercises:
#
#   1. `provider = aws.dr` cannot be interpolated. It is not an expression. You
#      cannot write `provider = var.enable_dr ? aws.dr : aws` and you cannot
#      loop a resource across a list of regions from a variable. If you want N
#      regions you write N provider blocks, or you use a module with
#      `providers = { aws = aws.dr }`. This is a language limit, not a
#      preference, and it is the single most common reason a "just make it
#      multi-region" ticket takes three days instead of three hours.
#
#   2. Data sources are regional too. `data.aws_availability_zones` under the
#      default provider returns primary-region AZs; the DR region needs its
#      own data source with `provider = aws.dr`. Forgetting this produces a
#      plan that builds DR subnets addressed by primary-region AZ names, which
#      fails at apply with an error that names neither region.
#
#   3. `default_tags` is per provider block. Both blocks below carry the same
#      tags, written out twice, because there is no inheritance between them.
#      A DR region full of untagged resources is the normal outcome of doing
#      this once, quickly, during an incident — and untagged DR resources are
#      exactly the ones nobody finds when they go looking for spend six months
#      later. See the teardown checklist.
#
# Both providers use the SAME profile. Cross-region does not mean
# cross-account. Splitting DR into a separate account is a defensible design
# — it makes the blast radius of a compromised credential smaller — but it is
# a different lesson and it would double the setup this day asks of you.
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

# The primary region. Everything without an explicit `provider =` argument
# lands here.
provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project   = "aws-aiops-bootcamp"
      Day       = "08"
      ManagedBy = "terraform"
      Owner     = var.owner
    }
  }
}

# The DR region. Used by the replica S3 bucket, the DynamoDB global table
# replica, and the DR-side copy vault added at CP2.
#
# The extra `Region = "dr"` tag is deliberate and it is the cheapest DR
# hygiene you will ever buy: it makes "show me everything I own in the DR
# region" a tag query rather than an archaeology project.
provider "aws" {
  alias   = "dr"
  region  = var.dr_region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project   = "aws-aiops-bootcamp"
      Day       = "08"
      ManagedBy = "terraform"
      Owner     = var.owner
      Region    = "dr"
    }
  }
}

###############################################################################
# Shared data sources
#
# Note the pairs. Every regional lookup that DR needs is duplicated with
# `provider = aws.dr`, because a data source resolves against the provider it
# is attached to and there is no way to ask one provider about another
# provider's region.
###############################################################################

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_partition" "current" {}

data "aws_region" "dr" {
  provider = aws.dr
}

# Availability zones in the primary region.
#
# `state = "available"` is not decoration. AZs can be temporarily
# unavailable for new capacity, and an account that has never launched in a
# given AZ may not be opted in to it at all. Hard-coding "us-east-1a" is the
# classic version of this bug — and worse, AZ NAMES ARE PER ACCOUNT. Your
# us-east-1a and my us-east-1a are different physical facilities. That
# randomisation is deliberate on AWS's part: it stops everyone piling into
# the alphabetically-first AZ. It also means an AZ name in a runbook is
# meaningless to anyone in another account. Use the AZ ID
# (`data.aws_availability_zones.available.zone_ids`, e.g. use1-az4) when you
# need to talk about the same physical place across accounts.
data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

# The same lookup in the DR region. Needed by nothing at CP1 — the DR side is
# S3 and DynamoDB, both regional rather than zonal — but present because CP2's
# warm-standby option is subnet-bound and because leaving it out is how the
# `provider = aws.dr` lesson above gets learned the expensive way.
data "aws_availability_zones" "dr" {
  provider = aws.dr
  state    = "available"

  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

# Amazon Linux 2023, resolved at plan time rather than pinned.
#
# There is a real DR argument on both sides of this and it is worth having
# before you copy it. A floating AMI means a recovery launch picks up whatever
# is current, which is good for patching and bad for reproducibility: the
# instance that comes up during your failover is not the instance you tested
# with. A pinned AMI is reproducible and rots. Production answer: pin to an
# AMI you built and COPY IT TO THE DR REGION, because AMIs are regional and a
# recovery plan that references a primary-region AMI ID cannot execute in the
# DR region. That copy is check DR-014's territory and the teardown checklist
# hunts for the orphans it leaves.
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
  numeric = true
}

locals {
  prefix = "cbc-day08"
  suffix = random_string.suffix.result

  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
  dr_region  = data.aws_region.dr.name
  partition  = data.aws_partition.current.partition
  ami_id     = data.aws_ami.al2023.id

  # How many NAT gateways the strategy asks for. The whole cost-versus-
  # availability trade-off of section 1 collapses into this one expression, and
  # the "single" case is the one that produces an architecture everybody calls
  # multi-AZ and nobody has tested against an AZ failure.
  nat_gateway_count = var.nat_gateway_strategy == "none" ? 0 : (
    var.nat_gateway_strategy == "single" ? 1 : local.az_count
  )

  # DNS failover records need a zone you actually own. Both variables, or
  # neither — a zone id with no record name produces a plan error at apply
  # time rather than at plan time, which is the wrong end.
  create_dns_records = var.hosted_zone_id != "" && var.dns_record_name != ""

  # How many AZs we actually use. Capped by what the region offers, because
  # `az_count = 3` in a two-AZ region is an apply-time index error rather than
  # a plan-time one, and plan-time is where you want your errors.
  az_count = min(var.az_count, length(data.aws_availability_zones.available.names))

  azs = slice(data.aws_availability_zones.available.names, 0, local.az_count)

  # Subnet CIDRs, derived rather than listed. /24s out of the VPC /16: public
  # subnets at .0, .1, .2 and private at .10, .11, .12. Derived because a
  # hand-maintained list of CIDRs is where the third AZ gets a subnet that
  # overlaps the second one.
  public_cidrs  = [for i in range(local.az_count) : cidrsubnet(var.vpc_cidr, 8, i)]
  private_cidrs = [for i in range(local.az_count) : cidrsubnet(var.vpc_cidr, 8, i + 10)]

  # Names computed once. Several resources reference each of these and the
  # auditor matches on the prefix, so a name assembled twice is a name that
  # will eventually be assembled two ways.
  vpc_name           = "${local.prefix}-vpc-${local.suffix}"
  alb_name           = "${local.prefix}-alb-${local.suffix}"
  asg_name           = "${local.prefix}-asg-${local.suffix}"
  table_name         = "${local.prefix}-orders-${local.suffix}"
  primary_bucket     = "${local.prefix}-data-${local.account_id}-${local.suffix}"
  replica_bucket     = "${local.prefix}-data-dr-${local.account_id}-${local.suffix}"
  chaos_function     = "${local.prefix}-chaos-${local.suffix}"
  recovery_function  = "${local.prefix}-recovery-${local.suffix}"
  replication_role   = "${local.prefix}-s3-replication-${local.suffix}"
  health_check_label = "${local.prefix}-alb-${local.suffix}"

  # ALB names are capped at 32 characters by the API, and the error arrives at
  # apply rather than plan. cbc-day08-alb-abc123 is 20, so there is headroom —
  # but if you fork this and lengthen the prefix, this is the line that breaks.
  # substr() here rather than a comment-and-hope.
  alb_name_safe = substr(local.alb_name, 0, 32)
}
