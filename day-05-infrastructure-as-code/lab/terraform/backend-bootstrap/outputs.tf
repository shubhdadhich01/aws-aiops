###############################################################################
# Day 05 — backend-bootstrap/outputs.tf
#
# Outputs are the module's public API. Everything here is either a value the
# next directory needs, or a value a human needs in order to know what to do
# next. Nothing here is secret — which is worth saying out loud, because
# IAC-008 exists to catch the outputs that are.
###############################################################################

output "state_bucket_name" {
  description = "Name of the S3 bucket holding Terraform state. Copy this into envs/dev/backend.tf and envs/prod/backend.tf."
  value       = aws_s3_bucket.state.id
}

output "state_bucket_arn" {
  description = "ARN of the state bucket, for IAM policies that grant plan/apply access in CI."
  value       = aws_s3_bucket.state.arn
}

output "state_bucket_region" {
  description = "Region the state bucket lives in. The backend block must name this region explicitly."
  value       = local.region
}

output "state_encryption" {
  description = "Which encryption the state bucket uses: SSE-S3 (free) or a customer-managed KMS key ($1.00/month)."
  value       = var.enable_kms_encryption ? "aws:kms (customer-managed key)" : "AES256 (SSE-S3)"
}

output "state_kms_key_arn" {
  description = "ARN of the customer-managed key, or null when enable_kms_encryption is false. Not secret — a key ARN grants nothing on its own."
  value       = var.enable_kms_encryption ? aws_kms_key.state[0].arn : null
}

###############################################################################
# Cost
#
# Day 05 is the cheapest day in the bootcamp in dollars and the most expensive
# in commitments. Read the breakdown, not just the total.
###############################################################################

output "estimated_hourly_cost_usd" {
  description = "Rough on-demand hourly cost of the bootstrap, us-east-1."
  value       = format("%.5f", (var.enable_kms_encryption ? 1.00 : 0.0) / 730.0)
}

output "estimated_monthly_cost_usd" {
  description = "Rough monthly cost of the bootstrap, us-east-1. The state bucket itself is fractions of a cent; the KMS key, if enabled, is the whole bill."
  value       = format("%.2f", (var.enable_kms_encryption ? 1.00 : 0.0) + 0.02)
}

output "cost_breakdown" {
  description = "Line-by-line, so nobody has to guess which toggle did it."
  value = {
    s3_state_storage = "~$0.01/month — a few hundred KB of state at $0.023/GB-month. Effectively free."
    s3_requests      = "~$0.01/month — PUT/GET per plan and apply at $0.005 per 1,000 PUT and $0.0004 per 1,000 GET. A busy team's CI is still cents."
    s3_versioning    = "$0.00 to enable, unbounded to leave alone. Every apply writes a new version and keeps the old one. The noncurrent_version_expiration rule (currently ${var.noncurrent_version_expiration_days} days) is what stops this becoming a real number."
    s3_native_lock   = "$0.00 — use_lockfile writes a small .tflock object during apply and deletes it after. This replaces the legacy DynamoDB lock table, which was ~$0.25/month and is the resource most likely to still be running in an account three years after its project died."
    kms_key          = var.enable_kms_encryption ? "$1.00/month for the CMK, plus $0.03 per 10,000 requests. Bucket keys cut the request charge by up to 99%." : "$0.00 — enable_kms_encryption is false, so SSE-S3 is doing the work for free."
    the_real_cost    = "None of the above. The real cost of this bucket is that you can never safely delete it, and that every environment you add doubles the resources it describes. Dollars are not the constraint on Day 05; commitments are."
  }
}

output "next_steps" {
  description = "What to do now that the backend exists."
  value       = <<-STEPS

    ============================================================================
      BACKEND BOOTSTRAP COMPLETE
    ============================================================================

      State bucket : ${aws_s3_bucket.state.id}
      Region       : ${local.region}
      Encryption   : ${var.enable_kms_encryption ? "aws:kms (customer-managed)" : "AES256 (SSE-S3)"}
      Locking      : S3-native (use_lockfile). No DynamoDB table. On purpose.
      Local state  : YES — ./terraform.tfstate. This directory is the one
                     exception in the whole lab, because the backend cannot
                     create itself.

    1. VERIFY WHAT YOU JUST BUILT

         aws s3api get-bucket-versioning --bucket ${aws_s3_bucket.state.id} \
           --profile ${var.aws_profile} --region ${var.aws_region}

         aws s3api get-public-access-block --bucket ${aws_s3_bucket.state.id} \
           --profile ${var.aws_profile} --region ${var.aws_region}

       Both should come back configured. If either does not, stop and fix it
       before you put a single state file in there.

    2. WIRE THE ENVIRONMENTS TO IT

       Open envs/dev/backend.tf and envs/prod/backend.tf and set:

         bucket = "${aws_s3_bucket.state.id}"
         region = "${local.region}"

       The backend block cannot use variables or interpolation. Not "should
       not" — CANNOT. It is read before Terraform has evaluated anything.
       Either hardcode it as above, or pass it at init time with
       `-backend-config`. There is no third option and everyone tries to find
       one.

    3. STAND UP DEV

         cd ../envs/dev
         terraform init
         terraform plan
         terraform apply

       On init, watch for: "Successfully configured the backend s3!" — that
       line is the whole point of this directory.

    4. THEN PROD, FROM THE SAME MODULES

         cd ../prod
         terraform init
         terraform apply

       Same three modules, different tfvars, different state key. That is the
       entire idea behind directory-per-environment.

    5. NEVER RUN `terraform destroy` HERE UNTIL EVERYTHING ELSE IS GONE

       prevent_destroy is set on the bucket and destroy WILL fail. That is
       intentional. ../../../teardown-checklist.md explains how to take it down
       deliberately, in the correct order, and why doing it in a hurry is how
       people lose production buckets.

    ============================================================================
  STEPS
}
