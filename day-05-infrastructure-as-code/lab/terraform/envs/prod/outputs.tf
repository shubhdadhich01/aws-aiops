###############################################################################
# Day 05 — envs/prod/outputs.tf
#
# Every output here has to survive var.enable_prod_environment being false, so
# each one goes through `one(...)`. `one()` takes a list of zero or one
# elements and returns the element or null — which is exactly the shape a
# count-gated module produces, and much easier to read than
# `length(module.x) > 0 ? module.x[0].y : null` repeated fourteen times.
###############################################################################

output "environment" {
  description = "Which environment this directory manages."
  value       = local.environment
}

output "prod_enabled" {
  description = "Whether prod is actually built. False by default — flipping it to true doubles the Day 05 footprint."
  value       = local.enabled
}

output "vpc_id" {
  description = "VPC ID for prod, or null when the environment is gated off."
  value       = one(module.network[*].vpc_id)
}

output "vpc_cidr_block" {
  description = "Prod VPC CIDR. Deliberately non-overlapping with dev's."
  value       = one(module.network[*].vpc_cidr_block)
}

output "public_subnet_ids" {
  description = "Map of availability zone to public subnet ID, or null when gated off."
  value       = one(module.network[*].public_subnet_ids)
}

output "private_subnet_ids" {
  description = "Map of availability zone to private subnet ID, or null when gated off."
  value       = one(module.network[*].private_subnet_ids)
}

output "app_security_group_id" {
  description = "Application security group, or null when gated off."
  value       = one(module.network[*].app_security_group_id)
}

output "nat_gateway_enabled" {
  description = "Whether a NAT gateway exists in prod. Each one is ~$32.40/month, and a real prod usually wants one per AZ."
  value       = one(module.network[*].nat_gateway_enabled)
}

output "instance_ids" {
  description = "Map of logical instance name to EC2 instance ID."
  value       = one(module.compute[*].instance_ids)
}

output "data_bucket_name" {
  description = "Application data bucket for prod, or null when gated off."
  value       = one(module.storage[*].bucket_name)
}

output "table_name" {
  description = "DynamoDB table name, or null when create_data_table is false or prod is gated off."
  value       = one(module.storage[*].table_name)
}

output "protected_resources" {
  description = "Resources carrying prevent_destroy. `terraform destroy` WILL FAIL on these until handled deliberately. In prod, that is the correct and desired behaviour, and force_destroy is false here too — emptying a prod bucket should be a separate act by a human who typed the name."
  value       = coalesce(one(module.storage[*].protected_resources), [])
}

###############################################################################
# Cost
###############################################################################

output "estimated_hourly_cost_usd" {
  description = "Rough on-demand hourly cost of prod as configured. Zero when the environment is gated off."
  value = format(
    "%.4f",
    (
      local.enabled ? (
        (var.enable_nat_gateway ? 32.40 : 0.0) +
        tonumber(one(module.compute[*].estimated_monthly_cost_usd)) +
        tonumber(one(module.storage[*].estimated_monthly_cost_usd)) +
        (var.enable_flow_logs ? 0.50 : 0.0)
      ) : 0.0
    ) / 730.0
  )
}

output "estimated_monthly_cost_usd" {
  description = "Rough monthly cost of prod as configured, us-east-1. Zero when gated off; about $0.02 when switched on with every other default left alone; about $32.42 the moment enable_nat_gateway follows it."
  value = format(
    "%.2f",
    local.enabled ? (
      (var.enable_nat_gateway ? 32.40 : 0.0) +
      tonumber(one(module.compute[*].estimated_monthly_cost_usd)) +
      tonumber(one(module.storage[*].estimated_monthly_cost_usd)) +
      (var.enable_flow_logs ? 0.50 : 0.0)
    ) : 0.0
  )
}

output "cost_breakdown" {
  description = "What switching prod on actually commits you to."
  value = {
    gate = local.enabled ? "ON. Every resource in Day 05 now exists twice." : "OFF. This directory creates nothing. Flip enable_prod_environment to true and the whole stack appears a second time."
    network = local.enabled ? (
      var.enable_nat_gateway ? "~$32.40/month — one NAT Gateway. A real prod wants one per AZ: ~$97.20/month for three." : "$0.00 — VPC, subnets, route tables, IGW and security groups are always free."
    ) : "$0.00 — gated off."
    compute         = local.enabled ? "USD ${one(module.compute[*].estimated_monthly_cost_usd)}/month" : "$0.00 — gated off."
    storage         = local.enabled ? "USD ${one(module.storage[*].estimated_monthly_cost_usd)}/month — includes point-in-time recovery, which dev does not pay for." : "$0.00 — gated off."
    the_doubling    = "This is the honest cost of multi-environment IaC and it is not the VPC. It is that every future decision — NAT, instance size, log retention, backups — now gets made and paid for twice, and the second one is the one nobody reviews."
    the_asymmetry   = "Note what prod turns on that dev does not: point-in-time recovery, 90-day version retention, 30-day log retention, force_destroy = false. That asymmetry living in two tfvars files instead of in ternaries scattered through the code is the whole argument for directory-per-environment."
    silent_growth_1 = "Non-current state versions in the shared state bucket. Prod writes its own, on its own key, on every apply."
    silent_growth_2 = "A fourth orphaned .terraform/ provider cache on disk, several hundred MB, that nothing ever cleans up."
  }
}

output "next_steps" {
  description = "What to do with prod."
  value       = <<-STEPS

    ============================================================================
      PROD ENVIRONMENT — ${local.enabled ? "BUILT" : "GATED OFF"}
    ============================================================================

      enable_prod_environment : ${local.enabled}
      Name prefix             : ${local.name_prefix}
      State key               : day-05/prod/terraform.tfstate
      VPC                     : ${local.enabled ? one(module.network[*].vpc_id) : "not created"}
      Data bucket             : ${local.enabled ? one(module.storage[*].bucket_name) : "not created"}

    ${local.enabled ? "" : "    Prod is switched OFF. `terraform apply` here creates nothing, and that\n    is the correct default. Set enable_prod_environment = true in\n    terraform.tfvars to build it — and understand first that you are\n    doubling the resource count of Day 05.\n"}
    1. DIFF THE TWO ENVIRONMENTS

         diff ../dev/main.tf main.tf
         diff ../dev/terraform.tfvars.example terraform.tfvars.example

       The module blocks are the same shape. The inputs differ. If you ever
       find yourself diffing two MODULES to see how environments differ, the
       repo has already gone wrong.

    2. CONFIRM THE STATE FILES ARE SEPARATE

         aws s3 ls s3://<your-state-bucket>/day-05/ --recursive \
           --profile ${var.aws_profile} --region ${var.aws_region}

       Two keys, one bucket. In a real estate, prod's state belongs in a
       bucket in the prod ACCOUNT — whoever can read the state file can read
       prod's secrets.

    3. PROVE THAT -target IS A SMELL

         terraform plan -target=module.network

       It works. It is also how you end up with a state file that has been
       applied in pieces and a plan that no longer matches any commit.
       -target exists for recovering from a failed apply, and for nothing
       else. If you need it routinely, your root module is too big — split it.

    4. THE CI SHAPE THIS DIRECTORY IS BUILT FOR

         terraform plan -detailed-exitcode -out=tfplan
         # exit 0 = no changes   1 = error   2 = changes present

       Plan on the pull request, post the exit code and the diff as a comment,
       apply only on merge to main, and authenticate with an OIDC role rather
       than a long-lived access key. See the day README.

    5. TEARDOWN

       Prod first, then dev, then backend-bootstrap LAST. prevent_destroy WILL
       block you and force_destroy is false here on purpose. Read
       ../../../../teardown-checklist.md before you start, not during.

    ============================================================================
  STEPS
}
