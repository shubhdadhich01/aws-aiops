###############################################################################
# Day 05 — envs/prod/main.tf
#
# The SAME three modules as envs/dev, with different inputs. Read this file
# next to envs/dev/main.tf: the module blocks are identical in shape and the
# arguments differ. That is the whole promise of modules, and the reason a
# copy-pasted "prod-vpc" module with one line changed is a bug rather than a
# shortcut.
#
# One structural difference: every module here is gated by
# `count = local.enabled ? 1 : 0`, driven by var.enable_prod_environment.
# Prod is opt-in in this bootcamp, because switching it on doubles the
# resource count of Day 05 and every subsequent cost decision has to be made
# twice.
#
# WHY `count` IS CORRECT HERE AND WRONG ON A SUBNET
#
# This is a genuine on/off. There will never be 2.5 prod environments.
# `count = var.x ? 1 : 0` is the one shape where count beats for_each, and it
# is the only place you will find count in this lab outside bad-examples/.
#
# The cost is real, though, and you should know it before you copy the
# pattern: module.network[0] is an INDEXED address. If this ever became
# for_each over a set of environments, every resource would move from
# module.network[0].* to module.network["prod"].*, and Terraform would plan to
# destroy and recreate every single one. The fix for that is a `moved` block,
# not a weekend — see the day README.
###############################################################################

module "network" {
  source = "../../modules/network"
  count  = local.enabled ? 1 : 0

  name_prefix = local.name_prefix
  vpc_cidr    = var.vpc_cidr

  public_subnets  = var.public_subnets
  private_subnets = var.private_subnets

  # Real prod usually wants this true, and usually wants one NAT per AZ
  # (~$97/month for three) so a single AZ failure does not take egress down
  # for the other two. False here because this is a bootcamp.
  enable_nat_gateway = var.enable_nat_gateway

  enable_flow_logs = var.enable_flow_logs
  # Longer than dev's 7 days. Prod logs answer questions asked weeks later.
  flow_log_retention_days = 30

  app_ingress_port = 80
}

module "compute" {
  source = "../../modules/compute"
  count  = local.enabled ? 1 : 0

  name_prefix = local.name_prefix

  # Private subnets in prod, public in dev. Same module, different wiring —
  # and note that this is the only difference between a lab VPC and a real
  # one that actually matters for security.
  subnet_ids = length(var.private_subnets) > 0 ? module.network[0].private_subnet_ids : module.network[0].public_subnet_ids

  security_group_ids = [module.network[0].app_security_group_id]

  instances           = var.instances
  associate_public_ip = var.associate_public_ip
  enable_ssm          = true
}

module "storage" {
  source = "../../modules/storage"
  count  = local.enabled ? 1 : 0

  name_prefix       = local.name_prefix
  enable_versioning = var.enable_versioning

  noncurrent_version_expiration_days = var.noncurrent_version_expiration_days
  abort_incomplete_upload_days       = 7

  create_data_table             = var.create_data_table
  enable_point_in_time_recovery = var.enable_point_in_time_recovery

  # FALSE in prod, unlike dev. force_destroy = true on a prod data bucket
  # means one `terraform destroy` in the wrong directory deletes the data.
  # Emptying a prod bucket should be a deliberate, separate, logged act by a
  # human who typed the bucket name.
  force_destroy = false

  # 30 days, not dev's 7. The question you ask of a prod log is usually asked
  # after the incident review, not during it.
  log_retention_days = 30
}

###############################################################################
# NOTE: there are no insecure examples in prod.
#
# envs/dev has a create_insecure_examples toggle. This directory does not, and
# will not. A deliberately misconfigured resource in a directory named `prod`
# is exactly the sort of thing that gets copied into a real repo by somebody
# skimming for a starting point.
#
# The static bad examples live in ../../bad-examples/, which is applied by
# nothing at all.
###############################################################################
