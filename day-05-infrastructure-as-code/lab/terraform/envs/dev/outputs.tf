###############################################################################
# Day 05 — envs/dev/outputs.tf
#
# Nothing here is a secret, and that is a deliberate property rather than a
# happy accident. IAC-008 flags outputs whose name or value looks like a
# credential and which are not marked `sensitive`.
#
# And to say it once more, because it is the single most misunderstood thing
# in Terraform: `sensitive = true` hides a value from CLI OUTPUT. It does not
# encrypt it, redact it, or keep it out of the state file. The value sits in
# state in plaintext either way.
###############################################################################

output "environment" {
  description = "Which environment this directory manages."
  value       = local.environment
}

output "vpc_id" {
  description = "VPC ID for dev."
  value       = module.network.vpc_id
}

output "public_subnet_ids" {
  description = "Map of availability zone to public subnet ID."
  value       = module.network.public_subnet_ids
}

output "private_subnet_ids" {
  description = "Map of availability zone to private subnet ID."
  value       = module.network.private_subnet_ids
}

output "app_security_group_id" {
  description = "Application security group. Ingress is VPC-internal only."
  value       = module.network.app_security_group_id
}

output "nat_gateway_enabled" {
  description = "Whether a NAT gateway exists. False in dev by default — that is ~$32.40/month you are not spending."
  value       = module.network.nat_gateway_enabled
}

output "instance_ids" {
  description = "Map of logical instance name to EC2 instance ID. Empty unless var.instances was populated."
  value       = module.compute.instance_ids
}

output "ssm_session_command" {
  description = "Paste-ready Session Manager command for the first instance, if there is one."
  value       = module.compute.ssm_session_command
}

output "data_bucket_name" {
  description = "Application data bucket for dev."
  value       = module.storage.bucket_name
}

output "protected_resources" {
  description = "Resources carrying prevent_destroy. `terraform destroy` WILL FAIL on these until you handle them deliberately. That failure is the feature — see teardown-checklist.md."
  value = concat(
    module.storage.protected_resources,
    var.create_insecure_examples ? [aws_s3_bucket.insecure_state_example[0].id] : [],
  )
}

output "insecure_example_bucket" {
  description = "The deliberately misconfigured bucket the live audit checks look for (no versioning, no encryption, public access still blocked). Null when create_insecure_examples is false."
  value       = var.create_insecure_examples ? aws_s3_bucket.insecure_state_example[0].id : null
}

output "drift_target_log_group" {
  description = "The log group whose CostCentre tag you will change in the console in Step 6, so terraform plan can catch you doing it."
  value       = aws_cloudwatch_log_group.drift_target.name
}

output "audit_command" {
  description = "The exact command to run the day's auditor against this environment."
  value = format(
    "cd ../../../python && python3 iac_audit.py --path ../terraform --profile %s --region %s --state-bucket %s",
    var.aws_profile,
    var.aws_region,
    var.create_insecure_examples ? aws_s3_bucket.insecure_state_example[0].id : "YOUR-STATE-BUCKET",
  )
}

###############################################################################
# Cost
###############################################################################

output "estimated_hourly_cost_usd" {
  description = "Rough on-demand hourly cost of the dev environment as configured."
  value = format(
    "%.4f",
    (
      (var.enable_nat_gateway ? 32.40 : 0.0) +
      tonumber(module.compute.estimated_monthly_cost_usd) +
      tonumber(module.storage.estimated_monthly_cost_usd) +
      (var.enable_flow_logs ? 0.50 : 0.0)
    ) / 730.0
  )
}

output "estimated_monthly_cost_usd" {
  description = "Rough monthly cost of the dev environment as configured, us-east-1. With every default left alone this is cents."
  value = format(
    "%.2f",
    (var.enable_nat_gateway ? 32.40 : 0.0) +
    tonumber(module.compute.estimated_monthly_cost_usd) +
    tonumber(module.storage.estimated_monthly_cost_usd) +
    (var.enable_flow_logs ? 0.50 : 0.0)
  )
}

output "cost_breakdown" {
  description = "Where the dev money goes, and the two places it grows while nobody is looking."
  value = {
    network         = var.enable_nat_gateway ? "~$32.40/month — NAT Gateway, billed from creation regardless of traffic, plus $0.045/GB processed." : "$0.00 — VPC, subnets, route tables, IGW and security groups are always free. No NAT gateway."
    compute         = "USD ${module.compute.estimated_monthly_cost_usd}/month — see module.compute.cost_breakdown for the split between instances, gp3 storage and public IPv4 addresses."
    storage         = "USD ${module.storage.estimated_monthly_cost_usd}/month — S3, optional DynamoDB, log group."
    flow_logs       = var.enable_flow_logs ? "~$0.50/month at lab traffic. Three figures a month on a busy production VPC." : "$0.00 — disabled."
    state_bucket    = "~$0.02/month, shared with prod, charged to the bootstrap. See ../../backend-bootstrap/outputs.tf."
    silent_growth_1 = "Non-current state file versions. Versioning is on in the state bucket (you want it), so EVERY apply writes a new version and keeps the old one. The bootstrap's noncurrent_version_expiration rule is the only thing stopping that growing forever."
    silent_growth_2 = "Orphaned .terraform/ provider caches. Every directory you have ever run `terraform init` in holds a few hundred MB of provider binaries on your disk. There are four such directories in this lab. Nothing ever cleans them up."
    the_real_cost   = "Not dollars. Day 05's expensive commitments are (a) a state bucket you can never safely delete, (b) versioning quietly retaining every state file forever, and (c) enable_prod_environment in ../prod, which doubles every resource the moment it flips to true."
  }
}

output "next_steps" {
  description = "What to do once dev is up."
  value       = <<-STEPS

    ============================================================================
      DEV ENVIRONMENT UP  —  ${local.name_prefix}
    ============================================================================

      VPC              : ${module.network.vpc_id}
      Public subnets   : ${length(module.network.public_subnet_ids)}
      Private subnets  : ${length(module.network.private_subnet_ids)}
      NAT gateway      : ${module.network.nat_gateway_enabled ? "YES — ~$32.40/month" : "no — $0.00"}
      Instances        : ${module.compute.instance_count}
      Data bucket      : ${module.storage.bucket_name}
      State            : s3://<your-state-bucket>/day-05/dev/terraform.tfstate
      Locking          : S3-native (use_lockfile). No DynamoDB table.
      Monthly estimate : see estimated_monthly_cost_usd

    1. PROVE THE STATE IS REMOTE

         ls terraform.tfstate            # should NOT exist
         terraform state list | head

       If terraform.tfstate is sitting in this directory, `terraform init`
       did not pick up backend.tf and you are running on local state.

    2. READ YOUR OWN STATE FILE. THIS IS THE POINT OF THE EXERCISE.

         aws s3 cp s3://<your-state-bucket>/day-05/dev/terraform.tfstate - \
           --profile ${var.aws_profile} --region ${var.aws_region} | head -60

       Every attribute of every resource, in plaintext JSON. Now imagine an
       RDS master password in there — because that is exactly where it goes.
       Anyone with s3:GetObject on that bucket has read it.

    3. WATCH THE LOCK EXIST

       In one terminal:

         terraform apply

       In another, while it runs:

         aws s3 ls s3://<your-state-bucket>/day-05/dev/ \
           --profile ${var.aws_profile} --region ${var.aws_region}

       You will see terraform.tfstate.tflock appear and disappear. That object
       IS the lock. No DynamoDB table involved.

    4. STAND UP PROD FROM THE SAME MODULES

         cd ../prod
         terraform init
         terraform apply

       Note what changes: the tfvars, the state key, the tags. Not the modules.

    5. DETECT DRIFT — the actual lab

       a. In the AWS console, find the log group
          ${aws_cloudwatch_log_group.drift_target.name}
          and change its CostCentre tag from "engineering" to "finance".

       b. Come back here and run:

            terraform plan

          Terraform refreshes state against reality, sees the tag it did not
          write, and proposes to put it back. That is drift detection. It is
          not a background service; it is a plan.

       c. Now fix it three different ways and understand each:

            terraform apply                  # reconcile: code wins
            terraform plan -refresh-only     # accept reality: AWS wins
            lifecycle { ignore_changes = [tags["CostCentre"]] }
                                             # stop caring about this attribute

    6. RUN THE AUDITOR

         cd ../../../python
         pip install -r requirements.txt
         python3 iac_audit.py --path ../terraform \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           --state-bucket ${var.create_insecure_examples ? aws_s3_bucket.insecure_state_example[0].id : "<your-state-bucket>"}

       Expect 13 findings with no credentials at all, and 15 with them.
       Compliance score 0/100 either way. IAC-003 and IAC-004 stay silent on
       purpose. The insecure examples are
       ${var.create_insecure_examples ? "ENABLED" : "DISABLED — set create_insecure_examples = true for the full experience"}.

    7. TEAR DOWN — AND READ THE CHECKLIST FIRST

       `terraform destroy` WILL FAIL here. ${length(module.storage.protected_resources)} resource(s) in the
       storage module carry prevent_destroy, plus the insecure example bucket.
       That is the seatbelt working. Do NOT delete the lifecycle block in a
       hurry; that is how production buckets go missing.

         ../../../../teardown-checklist.md

       Order matters: envs first, backend-bootstrap LAST.

    ============================================================================
  STEPS
}
