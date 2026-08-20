###############################################################################
# Day 04 — outputs.tf
#
# Convention: every day surfaces its cost in the outputs, not just in the docs.
# If you have to open a pricing page to find out what you just built, the
# outputs are badly written.
#
# Day 04's number is small enough to be surprising. Read silent_cost_growth
# before you conclude it does not matter.
###############################################################################

###############################################################################
# Core resource references
###############################################################################

output "scanner_function_name" {
  description = "Name of the compliance scanner Lambda function. Use this with `aws lambda invoke`."
  value       = aws_lambda_function.scanner.function_name
}

output "scanner_function_arn" {
  description = "ARN of the compliance scanner Lambda function."
  value       = aws_lambda_function.scanner.arn
}

output "scanner_log_group" {
  description = "CloudWatch log group for the scanner. Created by Terraform WITH retention — see main.tf section 6 for why that matters."
  value       = aws_cloudwatch_log_group.scanner.name
}

output "sns_topic_arn" {
  description = "SNS topic that receives compliance findings."
  value       = aws_sns_topic.findings.arn
}

output "dlq_url" {
  description = "URL of the dead letter queue. Step 5 of the lab reads messages back out of this."
  value       = aws_sqs_queue.dlq.url
}

output "dlq_arn" {
  description = "ARN of the dead letter queue."
  value       = aws_sqs_queue.dlq.arn
}

output "scheduled_rule_name" {
  description = "EventBridge rule that runs the periodic sweep."
  value       = aws_cloudwatch_event_rule.scheduled_scan.name
}

output "reactive_rule_name" {
  description = "EventBridge rule that fires on resource-creation API calls."
  value       = aws_cloudwatch_event_rule.reactive_scan.name
}

output "cloudtrail_name" {
  description = "CloudTrail trail. Without this the reactive rule never fires — see main.tf section 10."
  value       = aws_cloudtrail.main.name
}

output "kms_key_arn" {
  description = "Customer-managed KMS key, or null when enable_kms_encryption = false."
  value       = local.kms_key_arn
}

output "broken_function_name" {
  description = "The deliberately misconfigured function, or null when create_insecure_examples = false."
  value       = var.create_insecure_examples ? aws_lambda_function.broken[0].function_name : null
}

output "subscription_status_warning" {
  description = "The single most common Day 04 confusion."
  value       = <<-WARN
    An SNS email subscription is NOT active until you click the confirmation
    link AWS just emailed to ${var.notification_email}.

    Until you do, every publish SUCCEEDS and every message is DISCARDED. There
    is no error, no metric, and no indication anywhere in Terraform.

    Check it:
      aws sns list-subscriptions-by-topic --topic-arn ${aws_sns_topic.findings.arn} \
        --profile ${var.aws_profile} --region ${var.aws_region} \
        --query 'Subscriptions[].{Endpoint:Endpoint,Arn:SubscriptionArn}' --output table

    SubscriptionArn = "PendingConfirmation" means go and click the link.
  WARN
}

###############################################################################
# Cost
#
# Day 04 is the cheap day and it is worth understanding exactly why.
#
# Lambda, EventBridge, SNS, SQS and CloudWatch Logs all have PERMANENT free
# tiers — not the 12-month introductory kind that expires and surprises you.
# This stack uses a fraction of a percent of each:
#
#   Lambda      1,000,000 requests + 400,000 GB-seconds/month, permanently.
#               This stack: ~730 invocations, ~900 GB-seconds.
#   EventBridge AWS service events and scheduled rules are free.
#   SNS         1,000 email notifications/month free.
#   SQS         1,000,000 requests/month free.
#   CW Logs     5 GB ingestion + 5 GB storage/month free.
#   CW Alarms   10 alarms free. This stack has 2.
#   CloudTrail  The FIRST trail delivering management events is free.
#
# What is left is one KMS key at $1/month, and a few cents of S3 for the trail.
###############################################################################

locals {
  price_kms_key_month = 1.00
  price_s3_trail_est  = 0.01

  monthly_kms   = var.enable_kms_encryption ? local.price_kms_key_month : 0
  monthly_total = local.monthly_kms + local.price_s3_trail_est

  # Prorated. KMS is billed hourly, which is why an afternoon costs a fraction
  # of a cent rather than a dollar.
  hourly_total = local.monthly_total / 730
}

output "estimated_hourly_cost_usd" {
  description = "Approximate on-demand cost per hour while this stack is running (us-east-1, excluding data transfer)."
  value       = format("$%.5f/hour", local.hourly_total)
}

output "estimated_monthly_cost_usd" {
  description = <<-DESC
    Approximate cost if you leave this running for a 730-hour month.

    Yes, it really is about a dollar. Everything except the KMS key sits inside
    a permanent free tier at this volume.

    This is NOT a reason to skip `terraform destroy`. Read silent_cost_growth.
  DESC
  value       = format("$%.2f/month", local.monthly_total)
}

output "cost_breakdown" {
  description = "Line-by-line monthly estimate so you can see where the (very little) money goes."
  value = {
    kms_key            = var.enable_kms_encryption ? format("$%.2f  (set enable_kms_encryption = false to remove)", local.price_kms_key_month) : "$0.00 (disabled, using AWS-managed keys)"
    s3_cloudtrail_logs = format("$%.2f  (30-day lifecycle expiry configured)", local.price_s3_trail_est)
    lambda             = "$0.00  (permanent free tier: 1M requests + 400k GB-s/month)"
    eventbridge        = "$0.00  (AWS service events and scheduled rules are free)"
    sns                = "$0.00  (permanent free tier: 1,000 email notifications/month)"
    sqs                = "$0.00  (permanent free tier: 1M requests/month)"
    cloudwatch_logs    = "$0.00  (permanent free tier: 5 GB ingestion + 5 GB storage/month)"
    cloudwatch_alarms  = "$0.00  (10 alarms free; this stack creates 2)"
    cloudtrail         = "$0.00  (first trail with management events is free)"
    TOTAL              = format("$%.2f", local.monthly_total)
  }
}

output "silent_cost_growth" {
  description = "The two ways this ~$1 stack becomes an expensive one. Read before you walk away."
  value       = <<-GROWTH

    Day 04 costs about a dollar a month. Two things can change that, and
    neither shows up on a cost anomaly alert because both grow slowly.

    1. LOG GROUPS WITHOUT RETENTION
       ${var.create_insecure_examples ? "You are creating one RIGHT NOW." : "Not applicable — insecure examples are disabled."}

       The broken function has no Terraform-managed log group. Lambda creates
       /aws/lambda/${local.prefix}-broken-function-${local.suffix} on first
       invocation with retention "Never expire".

       Because Terraform does not know about it, `terraform destroy` leaves it
       behind and you are billed at $0.03/GB-month forever, for a function you
       believe you deleted. This is the most common orphaned line item on a
       personal AWS bill.

       Delete it explicitly:
         aws logs delete-log-group \
           --log-group-name /aws/lambda/${local.prefix}-broken-function-${local.suffix} \
           --profile ${var.aws_profile} --region ${var.aws_region}

       Then run the account-wide sweep in the teardown checklist. It will find
       orphans from Days 01-03 too.

    2. RECURSIVE INVOCATION
       Reserved concurrency on the scanner is ${var.lambda_reserved_concurrency == -1 ? "UNRESERVED — this guard is OFF" : var.lambda_reserved_concurrency}.

       A Lambda that triggers itself — writing to the bucket that invokes it,
       or making the API call its own EventBridge rule listens for — scales to
       your account concurrency limit and bills at machine speed. Five-figure
       overnight bills have come from two lines of code.

       AWS added a recursive-loop detector in 2023 that stops most cases after
       ~16 iterations, but it does not catch every topology. Reserved
       concurrency is the guard that always works.

  GROWTH
}

###############################################################################
# next_steps — the output people actually read
###############################################################################

output "next_steps" {
  description = "Copy-paste command sequence for the rest of the lab."
  value       = <<-STEPS

    ============================================================================
      Day 04 — Serverless Automation
      Stack is up. Estimated cost: ${format("$%.5f/hour", local.hourly_total)} / ${format("$%.2f/month", local.monthly_total)}
    ============================================================================

    1. CONFIRM THE SNS SUBSCRIPTION.  NOTHING WORKS UNTIL YOU DO THIS.

       Check your inbox for "AWS Notification - Subscription Confirmation"
       sent to ${var.notification_email} and click the link. Check spam.

         aws sns list-subscriptions-by-topic \
           --topic-arn ${aws_sns_topic.findings.arn} \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           --query 'Subscriptions[].SubscriptionArn' --output text

       "PendingConfirmation" means go and click it. Anything starting with
       "arn:" means you are good.

    2. INVOKE THE SCANNER BY HAND

         aws lambda invoke \
           --function-name ${aws_lambda_function.scanner.function_name} \
           --payload '{}' \
           --cli-binary-format raw-in-base64-out \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           /tmp/scan.json

         cat /tmp/scan.json | python3 -m json.tool

       You should get back a mode of "scheduled" and a findings count. An
       email follows within a minute or two.

    3. TRIGGER THE REACTIVE PATH — the fun one

         SG_ID=$(aws ec2 create-security-group \
           --group-name ${local.prefix}-oops-${local.suffix} \
           --description "deliberately terrible, deleted in step 4" \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           --query 'GroupId' --output text)

         aws ec2 authorize-security-group-ingress \
           --group-id $SG_ID --protocol tcp --port 22 --cidr 0.0.0.0/0 \
           --profile ${var.aws_profile} --region ${var.aws_region}

       Now watch your inbox. 30-90 seconds, most of which is CloudTrail
       delivery latency, not your code.

       Clean it up:
         aws ec2 delete-security-group --group-id $SG_ID \
           --profile ${var.aws_profile} --region ${var.aws_region}

    4. TAIL THE LOGS WHILE IT HAPPENS

         aws logs tail ${aws_cloudwatch_log_group.scanner.name} --follow \
           --profile ${var.aws_profile} --region ${var.aws_region}

    5. FORCE A FAILURE INTO THE DEAD LETTER QUEUE

         aws lambda invoke \
           --function-name ${aws_lambda_function.scanner.function_name} \
           --invocation-type Event \
           --payload '{"force_failure": true}' \
           --cli-binary-format raw-in-base64-out \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           /tmp/async.json

       Wait ~60 seconds for the retries to exhaust, then pull it back:

         aws sqs receive-message --queue-url ${aws_sqs_queue.dlq.url} \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           --max-number-of-messages 10 --wait-time-seconds 10

    6. RUN THE AUDITOR

         cd ../python
         pip install -r requirements.txt
         python3 serverless_audit.py --profile ${var.aws_profile} --region ${var.aws_region}

       Expect exactly 14 findings and a compliance score of 0/100. CMP-008
       (deprecated runtime) and CMP-016 (public function) stay silent on this
       stack on purpose — they prove the auditor does not cry wolf. The broken
       resources
       (${var.create_insecure_examples ? "enabled" : "DISABLED — set create_insecure_examples = true for the full experience"})
       are there on purpose. Read every finding before you fix anything.

    7. TRY THE OUTPUT FORMATS

         python3 serverless_audit.py --format json --quiet > findings.json
         python3 serverless_audit.py --format csv --min-severity HIGH
         python3 serverless_audit.py --fail-on HIGH ; echo "exit code: $?"

    8. DESTROY. THIS IS NOT OPTIONAL, EVEN AT A DOLLAR A MONTH.

         terraform destroy -auto-approve

       Then delete the orphaned log group Terraform does not know about:

         aws logs delete-log-group \
           --log-group-name /aws/lambda/${local.prefix}-broken-function-${local.suffix} \
           --profile ${var.aws_profile} --region ${var.aws_region}

       The KMS key enters PendingDeletion for ${var.kms_deletion_window_days} days. That is
       mandatory AWS behaviour, not a failed destroy, and you are not billed
       for it during the wait.

       Full verification: ../../teardown-checklist.md

    ============================================================================
  STEPS
}
