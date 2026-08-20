###############################################################################
# Day 05 — envs/dev/main.tf
#
# This file is almost entirely module calls, and that is the point. The
# environment directory answers "what does dev look like"; the modules answer
# "how is a network built". Mixing the two is how a repo ends up with three
# subtly different VPCs nobody can diff.
#
# envs/prod/main.tf is the SAME three modules with different inputs. If you
# find yourself wanting a resource in dev that prod cannot have, you want
# either a module input or a fourth module — not a copy of the module with one
# line changed.
#
# MODULE SOURCES — the three kinds you will meet
#
#   source = "../../modules/network"                        local path
#   source = "terraform-aws-modules/vpc/aws"                registry
#   source = "git::https://github.com/org/repo.git//vpc?ref=v1.4.0"   git
#
# Local paths: no version argument, because the version IS your commit. Great
#   for modules that live and change with the code that calls them.
# Registry: `version = "~> 5.0"` is required and honoured. Great for
#   well-maintained public modules.
# Git: pin with `?ref=` to a TAG, never a branch. `?ref=main` means your
#   infrastructure changes when someone else merges a PR in another repo,
#   which you will discover during an unrelated apply.
#
# This lab uses local paths so the whole thing works with the registry
# unreachable, and so you can read every line of what you are running.
###############################################################################

###############################################################################
# 1. Network
###############################################################################

module "network" {
  source = "../../modules/network"

  name_prefix = local.name_prefix
  vpc_cidr    = var.vpc_cidr

  public_subnets  = var.public_subnets
  private_subnets = var.private_subnets

  # ~$32.40/month. Off in dev. Nothing in the dev private subnets makes
  # outbound calls, so paying for egress they do not use is pure waste.
  enable_nat_gateway = var.enable_nat_gateway

  enable_flow_logs        = var.enable_flow_logs
  flow_log_retention_days = 7

  app_ingress_port = 80
}

###############################################################################
# 2. Compute
#
# var.instances is empty by default, so this module creates an IAM role and
# nothing else until you ask for an instance. Put one in terraform.tfvars when
# you want to see the plan graph do something interesting.
###############################################################################

module "compute" {
  source = "../../modules/compute"

  name_prefix = local.name_prefix

  # These two arguments are the entire dependency graph between the modules.
  # Terraform does not need `depends_on` here: reading module.network's output
  # is what creates the edge. Adding depends_on between modules that already
  # share data is noise, and adding it between modules that do NOT share data
  # usually means you have hidden a real dependency somewhere it cannot be
  # seen.
  subnet_ids         = module.network.public_subnet_ids
  security_group_ids = [module.network.app_security_group_id]

  instances           = var.instances
  associate_public_ip = var.associate_public_ip
  enable_ssm          = true
}

###############################################################################
# 3. Storage
###############################################################################

module "storage" {
  source = "../../modules/storage"

  name_prefix       = local.name_prefix
  enable_versioning = var.enable_versioning

  noncurrent_version_expiration_days = var.noncurrent_version_expiration_days
  abort_incomplete_upload_days       = 7

  create_data_table             = var.create_data_table
  enable_point_in_time_recovery = false

  # true so that the lab teardown can complete. In anything real this is
  # false, and emptying the bucket is a deliberate, separate, logged act.
  force_destroy = true

  log_retention_days = 7
}

###############################################################################
# 4. The deliberately broken half
#
# Everything below exists so the LIVE checks in ../../../python/iac_audit.py
# have something real to find. It is gated behind var.create_insecure_examples
# and it is the only thing in this directory that is wrong on purpose.
#
# The STATIC bad examples live in ../../bad-examples/ and are applied by
# nothing. Together they cover the whole check set:
#
#   bad-examples/ (parsed, never applied)  →  IAC-001, 002, 005, 008, 009,
#                                             010, 011, 012, 013, 014, 016
#   here (applied, when the toggle is on)  →  IAC-006, IAC-007
#   nothing, by design                     →  IAC-003, IAC-004
#
# What this bucket is NOT: it is not publicly readable. The public access
# block below is real. A teaching repo that ships a world-readable S3 bucket
# is one `terraform apply` away from being a teaching repo that leaked
# somebody's data, and IAC-004 staying silent is a better lesson than the
# alternative anyway — a check set where everything fires teaches you nothing
# about false positives.
###############################################################################

resource "aws_s3_bucket" "insecure_state_example" {
  count = var.create_insecure_examples ? 1 : 0

  bucket = "${local.name_prefix}-tfstate-insecure-${local.suffix}"

  # So the lab can be torn down without a manual empty step. Never in real
  # life on anything holding state.
  force_destroy = true

  lifecycle {
    # Present so that IAC-013 does NOT fire here. The whole point of this
    # bucket is to exercise IAC-006 and IAC-007 and nothing else — a fixture
    # that trips six checks at once tells you nothing about which one you
    # broke. The missing-prevent_destroy example lives in bad-examples/.
    prevent_destroy = true
  }

  tags = {
    Name    = "${local.name_prefix}-tfstate-insecure"
    Purpose = "deliberately-misconfigured-audit-fixture"
  }
}

# WRONG ON PURPOSE, PART 1: no aws_s3_bucket_versioning resource for this
# bucket. A state bucket without versioning has no rollback path when an apply
# writes a corrupt or truncated state file, and that is an outage, not an
# inconvenience.  →  IAC-006 (HIGH)

# WRONG ON PURPOSE, PART 2: no
# aws_s3_bucket_server_side_encryption_configuration resource either. State
# files hold every secret Terraform has ever touched, in plaintext.
#   →  IAC-007 (HIGH)

# RIGHT ON PURPOSE: the public access block IS here. IAC-004 stays silent.
resource "aws_s3_bucket_public_access_block" "insecure_state_example" {
  count = var.create_insecure_examples ? 1 : 0

  bucket = aws_s3_bucket.insecure_state_example[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

###############################################################################
# 5. The drift target
#
# ONE tag, on one cheap resource, that the lab tells you to change by hand in
# the console in Step 6. That is the entire drift demonstration: change it
# there, run `terraform plan` here, watch Terraform notice, then fix it three
# different ways and understand why each is sometimes correct.
#
# A CloudWatch log group is the right target for this: it is free, it is
# instant, and getting it wrong costs nothing.
###############################################################################

resource "aws_cloudwatch_log_group" "drift_target" {
  name              = "/aws/${local.name_prefix}/drift-demo"
  retention_in_days = 7

  tags = {
    Name = "${local.name_prefix}-drift-demo"
    # CHANGE THIS VALUE IN THE CONSOLE IN STEP 6. Do not change it here.
    CostCentre = "engineering"
  }
}
