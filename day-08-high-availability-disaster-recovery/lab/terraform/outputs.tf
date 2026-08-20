###############################################################################
# Day 08 — outputs.tf
#
# Four jobs, in this order:
#   1. The handful of identifiers you will paste into commands all day.
#   2. THE DECLARED NUMBERS — your RTO and RPO, printed back at you, so that
#      lab step 7 can compare them against something you actually said.
#   3. Cost, itemised, with the guesses labelled as guesses.
#   4. next_steps: the copy-paste sequence. Its numbering is the contract that
#      lab/README.md follows — if you renumber here, renumber there.
###############################################################################

###############################################################################
# Endpoints and identifiers
###############################################################################

output "alb_dns_name" {
  description = "Public DNS name of the load balancer. This is the service."
  value       = aws_lb.main.dns_name
}

output "app_url" {
  description = "Paste into a browser and refresh repeatedly — the AZ in the response changes as the load balancer spreads you across zones."
  value       = "http://${aws_lb.main.dns_name}/"
}

output "health_url" {
  description = "The endpoint the target group health check hits. Curl it in a loop during the failure exercise."
  value       = "http://${aws_lb.main.dns_name}${var.target_group_health_check.path}"
}

output "asg_name" {
  description = "Auto Scaling group name — the subject of most commands in next_steps."
  value       = aws_autoscaling_group.app.name
}

output "target_group_arn" {
  description = "Target group ARN. You will paste this into describe-target-health more than any other value today — it is the only place that tells you your ACTUAL capacity, as opposed to the capacity the ASG believes it has."
  value       = aws_lb_target_group.app.arn
}

output "asg_health_check_type" {
  description = "ELB or EC2. If this says EC2, an application failure will never cause a replacement. See DR-003."
  value       = aws_autoscaling_group.app.health_check_type
}

output "availability_zones" {
  description = "AZ names in use in the primary region. Remember these are per-account aliases: your us-east-1a is not mine."
  value       = local.azs
}

output "availability_zone_ids" {
  description = "The AZ IDs behind those names (e.g. use1-az4). THESE are stable across accounts, and they are what you should write in a runbook that more than one account will read."
  value       = slice(data.aws_availability_zones.available.zone_ids, 0, local.az_count)
}

output "dr_region_resolved" {
  description = "The region the aws.dr provider ACTUALLY resolved to, read back from the provider rather than from the variable. If this matches your primary region you have built a second copy inside the same blast radius, which is the one failure this whole day exists to avoid."
  value       = local.dr_region
}

output "dr_region_availability_zones" {
  description = "AZs available in the DR region. Nothing at CP1 is zonal there, but the count is worth knowing before you plan a warm standby — not every region has three."
  value       = data.aws_availability_zones.dr.names
}

output "nat_gateway_strategy" {
  description = "How private subnets reach the internet. 'single' means one AZ's failure removes outbound connectivity for the whole stack while every health check stays green — check DR-002."
  value       = "${var.nat_gateway_strategy} (${local.nat_gateway_count} gateway(s) for ${local.az_count} AZ(s))"
}

output "dynamodb_table_name" {
  description = "Primary DynamoDB table."
  value       = aws_dynamodb_table.orders.name
}

output "dynamodb_global_table_regions" {
  description = "Regions this table replicates to. Empty means your RPO for this table is PITR's ~5 minutes, in one region, and a region failure loses it entirely."
  value       = var.enable_dynamodb_global_table ? [var.dr_region] : []
}

output "s3_primary_bucket" {
  description = "Primary S3 bucket."
  value       = aws_s3_bucket.primary.id
}

output "s3_replica_bucket" {
  description = "DR-region replica bucket. Check the region in the console before you trust it — a replica created under the wrong provider alias looks identical in a plan."
  value       = aws_s3_bucket.replica.id
}

output "rds_endpoint" {
  description = "RDS endpoint, when create_rds is true. This is a DNS NAME, not an address, and that is the entire mechanism of Multi-AZ failover: the name is repointed at the standby. Anything that caches the resolution does not fail over."
  value       = var.create_rds ? aws_db_instance.main[0].endpoint : "(create_rds = false)"
}

output "rds_multi_az" {
  description = "Whether the RDS instance runs Multi-AZ. False means an AZ failure is a restore-from-backup event, not a 60-second DNS repoint."
  value       = var.create_rds ? tostring(aws_db_instance.main[0].multi_az) : "(create_rds = false)"
}

output "route53_health_check_id" {
  description = "Route 53 health check on the ALB — the only one of the three health checks that answers 'can my users reach this'."
  value       = var.enable_route53_health_check ? aws_route53_health_check.primary[0].id : "(disabled)"
}

output "dns_failover_records" {
  description = "Whether failover record sets were created. Requires hosted_zone_id AND dns_record_name."
  value       = local.create_dns_records ? "${var.dns_record_name} (PRIMARY alias -> ALB, SECONDARY -> placeholder)" : "(none — set hosted_zone_id and dns_record_name to create them)"
}

output "chaos_function_name" {
  description = "The chaos Lambda. Invoke it in dry-run first, every time."
  value       = var.enable_chaos_lambda ? aws_lambda_function.chaos[0].function_name : "(disabled)"
}

output "recovery_function_name" {
  description = "The recovery Lambda. Invoke it directly for the manual failback action — that one is deliberately not in the state machine."
  value       = var.enable_recovery_workflow ? aws_lambda_function.recovery[0].function_name : "(enable_recovery_workflow = false)"
}

output "backup_role_arn" {
  description = "The role AWS Backup assumes. Needed for start-backup-job and start-restore-job — and note it carries BOTH the backup and the restore managed policies, because a role that can back up and not restore fails at exactly the moment it is used."
  value       = var.enable_backup_plan ? aws_iam_role.backup[0].arn : "(enable_backup_plan = false)"
}

output "backup_test_resource_arn" {
  description = "A cheap, disposable resource to practise an on-demand backup and a real restore against — the 1 GiB volume from the insecure examples. Nothing depends on it, so a restore drill cannot damage anything."
  value = var.create_insecure_examples ? format(
    "arn:%s:ec2:%s:%s:volume/%s",
    local.partition, local.region, local.account_id, aws_ebs_volume.stale_source[0].id
  ) : "(create_insecure_examples = false — pick a resource yourself)"
}

output "sns_topic_arn" {
  description = "Notification topic for chaos and, at CP2, recovery events."
  value       = aws_sns_topic.dr.arn
}

###############################################################################
# The declared numbers
#
# These are outputs rather than a comment because the point of the lab is to
# put a claim on the record BEFORE measuring it. A number you can revise after
# seeing the result is not a prediction.
###############################################################################

output "declared_rto_minutes" {
  description = "Your DECLARED Recovery Time Objective. Nothing enforces this. Lab step 7 measures the real one."
  value       = var.rto_target_minutes
}

output "declared_rpo_minutes" {
  description = "Your DECLARED Recovery Point Objective. Lab step 6 measures replication lag, which is the real one for anything replicated."
  value       = var.rpo_target_minutes
}

output "rto_budget_already_spent" {
  description = <<-DESC
    The parts of your RTO that are consumed by configuration alone, before any
    human or any automation does anything. Read this next to declared_rto_minutes.
  DESC
  value = {
    alb_detection_seconds = format(
      "%d  (target group: interval %d x unhealthy_threshold %d)",
      var.target_group_health_check.interval * var.target_group_health_check.unhealthy_threshold,
      var.target_group_health_check.interval,
      var.target_group_health_check.unhealthy_threshold,
    )
    alb_draining_seconds = format("%d  (deregistration_delay on the target group)", aws_lb_target_group.app.deregistration_delay)
    asg_grace_seconds    = format("%d  (a replacement is not judged for this long after it launches)", var.asg_health_check_grace_period)
    route53_detection_seconds = var.enable_route53_health_check ? format(
      "%d  (health check: request_interval 30 x failure_threshold 3)", 90
    ) : "n/a (no Route 53 health check)"
    dns_ttl_seconds       = local.create_dns_records ? format("%d  (worst case, for clients that just refreshed. The ALB alias record is fixed at 60 regardless.)", var.route53_ttl) : "n/a (no failover records)"
    instance_boot_seconds = "~90-180  (AMI boot + user-data + first passing health check. MEASURE THIS; do not accept the estimate.)"
    NOTE                  = "None of the above includes detection by a human, the decision to act, or reconciling data. In most measured incidents those three exceed everything listed here combined."
  }
}

###############################################################################
# Cost
#
# Day 08 is the first day in this repo where the correct architecture is
# genuinely expensive, and where "do not do this" is sometimes the right
# answer. Three shapes of bill:
#
#   PER HOUR, EXACTLY     NAT gateways, the ALB, instances, EBS, RDS, public
#                         IPv4 addresses. All countable here, and all running
#                         whether or not anything uses them.
#   PER MONTH, EXACTLY    Route 53 health checks, detailed monitoring.
#   PER GB / PER REQUEST  S3 storage and replication transfer, DynamoDB
#                         throughput and PITR, NAT processing, ALB LCUs. Not
#                         countable from a plan; estimated below and labelled.
#
# The instance price below is hardcoded for t3.micro. If you change
# instance_type, this number is wrong and the label says so.
###############################################################################

locals {
  hours_per_month = 730

  price_nat_hour         = 0.045
  price_alb_hour         = 0.0225
  price_t3micro_hour     = 0.0104
  price_public_ipv4_hour = 0.005
  price_ebs_gb_month     = 0.08
  price_snapshot_gb_mo   = 0.05
  price_monitoring_month = 2.10
  price_health_check_mo  = 0.50
  price_rds_micro_hour   = 0.017
  price_rds_storage_gb   = 0.115

  # Estimates, not arithmetic. Labelled everywhere they appear.
  estimate_alb_lcu_month = 1.00
  estimate_data_month    = 0.50

  monthly_nat      = local.nat_gateway_count * local.price_nat_hour * local.hours_per_month
  monthly_nat_ipv4 = local.nat_gateway_count * local.price_public_ipv4_hour * local.hours_per_month

  # An internet-facing ALB places a node, with a public IPv4 address, in each
  # subnet you give it. Since February 2024 every in-use public IPv4 address is
  # billed at $0.005/hour — which quietly made every multi-AZ load balancer
  # $3.65/month more expensive per AZ, and made "add a third AZ" a slightly
  # larger decision than it used to be.
  monthly_alb      = local.price_alb_hour * local.hours_per_month
  monthly_alb_ipv4 = local.az_count * local.price_public_ipv4_hour * local.hours_per_month

  monthly_instances  = var.asg_desired_capacity * local.price_t3micro_hour * local.hours_per_month
  monthly_ebs        = var.asg_desired_capacity * 8 * local.price_ebs_gb_month
  monthly_monitoring = var.asg_desired_capacity * local.price_monitoring_month

  monthly_health_check = var.enable_route53_health_check ? local.price_health_check_mo : 0

  monthly_rds = var.create_rds ? (
    (local.price_rds_micro_hour * local.hours_per_month) + (20 * local.price_rds_storage_gb)
  ) * (var.rds_multi_az ? 2 : 1) : 0

  # The insecure examples: a 1 GiB volume and a 1 GiB snapshot. The legacy ASG
  # runs at desired capacity 0 and therefore costs nothing.
  monthly_insecure = var.create_insecure_examples ? (1 * local.price_ebs_gb_month) + (1 * local.price_snapshot_gb_mo) : 0

  monthly_total = (
    local.monthly_nat +
    local.monthly_nat_ipv4 +
    local.monthly_alb +
    local.monthly_alb_ipv4 +
    local.estimate_alb_lcu_month +
    local.monthly_instances +
    local.monthly_ebs +
    local.monthly_monitoring +
    local.monthly_health_check +
    local.monthly_rds +
    local.monthly_insecure +
    local.estimate_data_month
  )

  hourly_total = local.monthly_total / local.hours_per_month
}

output "estimated_hourly_cost_usd" {
  description = "Approximate on-demand cost per hour (us-east-1). Contains two labelled estimates. See cost_breakdown."
  value       = format("$%.5f/hour", local.hourly_total)
}

output "estimated_monthly_cost_usd" {
  description = <<-DESC
    Approximate cost for a 730-hour month.

    Unlike Days 01-07, this is NOT a small number, and that is the point. High
    availability is a thing you buy. The single largest line here is usually
    the NAT gateway, followed by the load balancer, and NEITHER of them scales
    down when your traffic does — they bill hourly for existing.

    Read cost_breakdown before you set nat_gateway_strategy = "per_az" or
    rds_multi_az = true. Both are correct. Both roughly double a line item.
  DESC
  value       = format("$%.2f/month", local.monthly_total)
}

output "cost_breakdown" {
  description = "Line by line, with the estimates labelled as estimates."
  value = {
    nat_gateways = local.nat_gateway_count > 0 ? format(
      "$%.2f  (%d x $0.045/hour) — %s",
      local.monthly_nat,
      local.nat_gateway_count,
      var.nat_gateway_strategy == "single" ? "SINGLE AZ DEPENDENCY. Cheapest line to halve and the most expensive to be wrong about. Check DR-002." : "one per AZ, the correct answer",
    ) : "$0.00 (nat_gateway_strategy = none; private subnets have no outbound route)"
    nat_public_ipv4 = local.nat_gateway_count > 0 ? format("$%.2f  (%d x $0.005/hour — in-use public IPv4, billed since Feb 2024)", local.monthly_nat_ipv4, local.nat_gateway_count) : "$0.00"
    nat_processing  = local.nat_gateway_count > 0 ? "USAGE-BASED — ~$0.045/GB processed, ON TOP of the hourly charge. The S3 gateway endpoint in section 1 is free and removes the largest share of this in most stacks." : "$0.00"
    alb             = format("$%.2f  ($0.0225/hour, billed for existing regardless of traffic)", local.monthly_alb)
    alb_public_ipv4 = format("$%.2f  (%d AZ(s) x $0.005/hour — one ALB node, one public IPv4, per subnet)", local.monthly_alb_ipv4, local.az_count)
    alb_lcu         = format("$%.2f  ESTIMATE — LCUs bill on connections, requests, bandwidth and rule evaluations. A quiet lab is near the minimum; a busy service is frequently larger than the hourly charge.", local.estimate_alb_lcu_month)
    ec2_instances   = format("$%.2f  (%d x t3.micro at $0.0104/hour). PRICE HARDCODED FOR t3.micro — if you changed instance_type this figure is wrong.", local.monthly_instances, var.asg_desired_capacity)
    ec2_ebs         = format("$%.2f  (%d x 8 GiB gp3 at $0.08/GiB-month)", local.monthly_ebs, var.asg_desired_capacity)
    ec2_monitoring  = format("$%.2f  (%d x detailed monitoring at ~$2.10/month). Buys 1-minute metrics instead of 5-minute — which is the difference between detecting inside a 30-minute RTO with margin and spending a sixth of it waiting for a data point.", local.monthly_monitoring, var.asg_desired_capacity)
    rds = var.create_rds ? format(
      "$%.2f  (%s at ~$0.017/hour + 20 GiB gp3 at $0.115/GiB-month%s)",
      local.monthly_rds,
      var.rds_instance_class,
      var.rds_multi_az ? ", DOUBLED for Multi-AZ — the standby serves no traffic and is not a read replica" : ", SINGLE-AZ: an AZ failure here is a restore, not a failover",
    ) : "$0.00 (create_rds = false)"
    route53_health_check = var.enable_route53_health_check ? format("$%.2f  (1 check at $0.50/month). HTTPS, string matching, fast interval and latency measurement are ~$1.00/month EACH on top.", local.monthly_health_check) : "$0.00 (disabled)"
    route53_hosted_zone  = "$0.00  (this stack does not create one — see the variable description for why creating a zone you do not own is a $0.50/month resource that survives teardown)"
    dynamodb             = "USAGE-BASED — on-demand at ~$1.25 per million writes, ~$0.25 per million reads, ~$0.25/GiB-month storage. PITR adds ~$0.20/GiB-month. An idle lab table is cents; a global table replica doubles storage and bills replicated writes at ~1.5x."
    s3_and_replication   = "USAGE-BASED — ~$0.023/GiB-month in EACH region, ~$0.02/GiB inter-region transfer, ~$0.005 per 1,000 destination PUTs, plus ~$0.015/GiB if Replication Time Control is on. Replication duplicates: you pay transfer once and storage twice, forever."
    insecure_examples    = var.create_insecure_examples ? format("$%.2f  (1 GiB gp3 volume + 1 GiB snapshot). The legacy ASG runs at desired capacity 0 and costs nothing — a misconfiguration you can audit without paying to run it.", local.monthly_insecure) : "$0.00 (create_insecure_examples = false)"
    data_estimate        = format("$%.2f  ESTIMATE — a lab-sized S3 and DynamoDB footprint. Replace with your own Cost Explorer figure after a month.", local.estimate_data_month)
    lambda               = "$0.00  (permanent free tier: 1M requests + 400k GB-seconds/month)"
    sns                  = "$0.00  (permanent free tier: 1,000 email notifications/month)"
    TOTAL_COUNTABLE      = format("$%.2f/month — contains two labelled estimates and excludes every usage-based line above.", local.monthly_total)
  }
}

output "dr_ladder_comparison" {
  description = <<-DESC
    The four postures, for THIS workload, at THIS size. The whole point of the
    day compressed into one output. Costs are monthly and approximate; RTOs are
    what a team that has practised can actually achieve, not brochure numbers.
  DESC
  value = {
    "1_multi_az_only" = "~$85/month · RTO 2-5 min · RPO 0 within the region. Survives an AZ. Loses everything if the region does. THIS IS THE RIGHT ANSWER FOR MOST WORKLOADS AND MOST TEAMS."
    "2_pilot_light"   = "~$95/month · RTO 1-4 HOURS · RPO minutes. Data replicated, compute defined but not running. Cheap. Slow. And the compute has never booted in the DR region, which is where the four hours actually go."
    "3_warm_standby"  = "~$160/month · RTO 10-30 min · RPO minutes. A scaled-down second environment, running. Roughly doubles the deployment surface and the config drift, and it is exercised only during an incident."
    "4_active_active" = "~$180/month + engineering · RTO near zero · RPO = replication lag. Both regions serve. Requires your application to tolerate last-writer-wins conflicts and split brain. The cost is not the bill; it is that every feature now ships to two regions and every data model needs a conflict story."
    HONEST_NOTE       = "Going from 1 to 3 costs roughly double and buys you protection against a class of event that happens to a given region roughly once every few years. Many organisations should choose 1 and spend the difference on testing it. Say that out loud in the design review."
  }
}

output "silent_cost_growth" {
  description = "The three ways this becomes an expensive month. None of them appears in a plan."
  value       = <<-GROWTH

    Day 08's countable floor is ${format("$%.2f", local.monthly_total)}/month. Three things move it and
    none of them shows up in `terraform plan`.

    1. SNAPSHOTS AND AMIs NOBODY DELETES
       Snapshots bill per GiB-month, indefinitely, and they are the most
       durable artefact most accounts produce: they survive `terraform
       destroy` of the volume they came from, they survive the instance, they
       survive the person who took them. An AMI is a snapshot with a label,
       and deregistering the AMI does NOT delete the snapshots behind it —
       that is a separate operation almost nobody performs.

       Count them, in both regions:
         aws ec2 describe-snapshots --owner-ids ${local.account_id} \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           --query 'length(Snapshots)'
         aws ec2 describe-snapshots --owner-ids ${local.account_id} \
           --profile ${var.aws_profile} --region ${var.dr_region} \
           --query 'length(Snapshots)'

    2. CROSS-REGION REPLICAS IN A REGION NOBODY LOOKS AT
       This is the DR-specific one. Your dashboards are scoped to
       ${var.aws_region}. Your budget alerts are probably account-wide, but
       your INVESTIGATION of a budget alert starts in the region you work in.
       An S3 replica with versioning on and no lifecycle rule, or a DynamoDB
       replica with provisioned capacity, grows in ${var.dr_region} for months
       without anybody's dashboard changing.

       The lifecycle rules in section 6 exist for exactly this and they are
       applied to BOTH buckets, deliberately, because a rule is per bucket and
       the DR bucket is the one that gets forgotten.

    3. A WARM STANDBY THAT WAS SCALED UP FOR A TEST AND NEVER SCALED BACK
       (CP2 builds the recovery workflow that can do this to you.)

       The failover test succeeds. Everybody is pleased. The DR environment is
       running at production capacity because that is what the test needed, and
       scaling it back down is step 11 of a runbook that ended at step 9 when
       the test passed.

       This is the most expensive item on the list and the least visible,
       because the resources are correct, tagged, and doing exactly what they
       were told. Put the scale-down IN the test, not after it.

  GROWTH
}

###############################################################################
# next_steps
#
# THE NUMBERING HERE IS A CONTRACT. lab/README.md follows it, and the finding
# contract references specific steps. Renumber in one place and you have
# broken the other two.
###############################################################################

output "next_steps" {
  description = "Copy-paste sequence for the CP1 foundation. The recovery workflow is added at CP2."
  value       = <<-STEPS

    ============================================================================
      Day 08 — High Availability & Disaster Recovery
      Foundation is up. Countable floor: ${format("$%.5f/hour", local.hourly_total)} / ${format("$%.2f/month", local.monthly_total)}
      This is NOT a cents-per-day stack. Read `cost_breakdown` today, not on the 1st.
    ============================================================================

    0. CONFIRM THE SNS SUBSCRIPTION. Before anything else, and before CP2
       gives the account the ability to fail itself over.

         aws sns list-subscriptions-by-topic \
           --topic-arn ${aws_sns_topic.dr.arn} \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           --query 'Subscriptions[].SubscriptionArn' --output text

       "PendingConfirmation" means every notification is being discarded.

    1. WAIT FOR THE TARGETS TO GO HEALTHY, then look at the service.

         aws elbv2 describe-target-health \
           --target-group-arn ${aws_lb_target_group.app.arn} \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           --query 'TargetHealthDescriptions[].[Target.Id,TargetHealth.State]' \
           --output table

         curl http://${aws_lb.main.dns_name}/

       Refresh a few times. The instance id and AZ in the response change.
       That is the load balancer spreading you across ${local.az_count} zones.

    2. WRITE DOWN YOUR RTO. Do this NOW, in a file, before you measure
       anything. You have declared ${var.rto_target_minutes} minutes in tfvars.

       Write down three separate predictions, in seconds:
         (a) terminate one instance    -> service fully back to ${var.asg_desired_capacity} healthy targets
         (b) isolate one AZ            -> ALB stops routing to that zone
         (c) restore a DynamoDB table  -> a usable table, queryable by the app

       This ordering is the pedagogical point of the day. A number you write
       after seeing the answer is not a prediction, and every DR plan in the
       world is full of numbers written in that order.

    3. LOOK AT WHAT YOU ALREADY SPENT. `terraform output rto_budget_already_spent`
       shows the part of your RTO consumed by configuration before any human
       or automation acts. Compare it to your (a) above.

    4. READ THE THREE HEALTH CHECKS. Confirm which one you actually have:

         aws autoscaling describe-auto-scaling-groups \
           --auto-scaling-group-names ${local.asg_name} \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           --query 'AutoScalingGroups[0].[HealthCheckType,HealthCheckGracePeriod]' \
           --output text

       "EC2" means an application failure will never trigger a replacement.

    5. WRITE SOMETHING TO THE DATA TIER so there is data to lose.

         aws dynamodb put-item --table-name ${aws_dynamodb_table.orders.name} \
           --item '{"pk":{"S":"order#1"},"sk":{"S":"v1"},"note":{"S":"before failover"}}' \
           --profile ${var.aws_profile} --region ${var.aws_region}

         echo "before failover $(date -u +%FT%TZ)" > /tmp/cbc-day08.txt
         aws s3 cp /tmp/cbc-day08.txt s3://${aws_s3_bucket.primary.id}/ \
           --profile ${var.aws_profile} --region ${var.aws_region}

    6. MEASURE YOUR RPO instead of declaring it.

         # How long did that object take to appear in ${var.dr_region}?
         aws s3 ls s3://${aws_s3_bucket.replica.id}/ \
           --profile ${var.aws_profile} --region ${var.dr_region}

       Poll it. Time it. That interval is your S3 RPO right now, for an idle
       bucket, with no SLA behind it. Set s3_replication_time_control = true
       and you get a 15-minute SLA and, more usefully, a CloudWatch metric.

       If you set enable_dynamodb_global_table = true, the ReplicationLatency
       metric is the same number for DynamoDB, on a graph, continuously:

         aws cloudwatch get-metric-statistics --namespace AWS/DynamoDB \
           --metric-name ReplicationLatency --statistics Average --period 60 \
           --dimensions Name=TableName,Value=${aws_dynamodb_table.orders.name} Name=ReceivingRegion,Value=${var.dr_region} \
           --start-time $(date -u -d '-30 minutes' +%FT%TZ) \
           --end-time $(date -u +%FT%TZ) \
           --profile ${var.aws_profile} --region ${var.aws_region}

    7. BREAK SOMETHING AND TIME IT. Dry run FIRST, every time.

         aws lambda invoke --function-name ${local.chaos_function} \
           --payload '{"mode":"terminate_instance","dry_run":true}' \
           --cli-binary-format raw-in-base64-out \
           --profile ${var.aws_profile} --region ${var.aws_region} /dev/stdout

       Read the plan. Then, with a stopwatch running and a curl loop open in
       another terminal:

         while true; do date -u +%T; curl -s -m 2 -o /dev/null -w '%%{http_code}\n' \
           http://${aws_lb.main.dns_name}/; sleep 1; done

         aws lambda invoke --function-name ${local.chaos_function} \
           --payload '{"mode":"terminate_instance","dry_run":false}' \
           --cli-binary-format raw-in-base64-out \
           --profile ${var.aws_profile} --region ${var.aws_region} /dev/stdout

       Stop the clock when target health returns to ${var.asg_desired_capacity} healthy.
       Compare against your prediction from step 2(a).

       Then repeat with {"mode":"isolate_az","dry_run":false}, and afterwards
       {"mode":"restore"}. Notice how much longer the second one takes to think
       about than the first. That is failback.

    8. RESTORE SOMETHING. A backup nobody has restored is a file.

         aws dynamodb restore-table-to-point-in-time \
           --source-table-name ${aws_dynamodb_table.orders.name} \
           --target-table-name ${aws_dynamodb_table.orders.name}-restored \
           --use-latest-restorable-time \
           --profile ${var.aws_profile} --region ${var.aws_region}

       Time it to ACTIVE. Then notice the part nobody counts: the restored
       table has a DIFFERENT NAME, so your application cannot use it until
       something repoints it. That work is RTO too. Delete the restored table
       when you are done — it bills like any other table.

    9. FIX THE NAT GATEWAY and watch a finding disappear.

         terraform apply -var 'nat_gateway_strategy=per_az'

       Re-run the audit at CP3 and compare. Then look at the price, and decide
       honestly whether this workload should pay it.

    10. IF YOU OWN A DOMAIN, complete the DNS half:

         terraform apply \
           -var 'hosted_zone_id=Z...' \
           -var 'dns_record_name=app.example.com' \
           -var 'route53_ttl=60'

        Then dig the record, fail the health check, and time how long the old
        answer keeps coming back. That number is TTL, and it is spent RTO.

    11. TEAR DOWN when you are finished. `terraform destroy` does NOT remove
        the EBS snapshot, and cross-region resources need the DR region
        checked separately. See teardown-checklist.md.

  STEPS
}

###############################################################################
# CP2 — backup, the brake, and the workflow
###############################################################################

output "backup_vault_name" {
  description = "Primary-region backup vault."
  value       = var.enable_backup_plan ? aws_backup_vault.main[0].name : "(enable_backup_plan = false)"
}

output "backup_vault_dr_name" {
  description = "DR-region backup vault. A vault in the region that just failed is not a recovery option — this is the one that matters."
  value       = var.enable_backup_plan && var.backup_copy_to_dr ? aws_backup_vault.dr[0].name : "(no cross-region copy)"
}

output "backup_vault_lock" {
  description = "Vault lock state. GOVERNANCE mode only — this stack never sets changeable_for_days, because that argument's PRESENCE is what selects the irreversible compliance mode."
  value       = var.enable_vault_lock ? "governance mode, retention ${var.backup_retention_days}-${var.backup_retention_days * 2} days" : "NONE — check DR-009 fires once per vault"
}

output "backup_rpo_ceiling" {
  description = "Your backup schedule restated as the RPO it actually implies, so the two numbers can be compared without translation."
  value = {
    schedule       = var.backup_schedule
    retention_days = var.backup_retention_days
    copied_to_dr   = var.backup_copy_to_dr ? var.dr_region : "NO — these recovery points do not survive a regional event"
    declared_rpo   = "${var.rpo_target_minutes} minutes"
    NOTE           = "If the schedule is daily and the declared RPO is under a day, one of those two numbers is a fiction. DR-008 asks that as arithmetic instead of as a question."
  }
}

output "kill_switch_parameter" {
  description = "The runtime brake. Read as the FIRST state of every workflow execution; anything other than 'enabled' aborts."
  value       = aws_ssm_parameter.kill_switch.name
}

output "kill_switch_command" {
  description = "Pull the brake without a deploy, from anywhere, including a phone."
  value       = "aws ssm put-parameter --name ${aws_ssm_parameter.kill_switch.name} --value disabled --overwrite --profile ${var.aws_profile} --region ${var.aws_region}"
}

output "active_region_parameter" {
  description = "The application's source of truth for where writes go. The failover flips it. IF NOTHING READS IT, YOUR FAILOVER IS THEATRE — go and check that something does."
  value       = aws_ssm_parameter.active_region.name
}

output "recovery_state_machine_arn" {
  description = "The recovery workflow. Its execution history is the RTO measurement."
  value       = var.enable_recovery_workflow ? aws_sfn_state_machine.recovery[0].arn : "(enable_recovery_workflow = false)"
}

output "naive_state_machine_arn" {
  description = "The same failover with no brake, no assessment, no approval and no verification. Built from the identical zip file. Check DR-015."
  value       = var.create_insecure_examples && var.enable_recovery_workflow ? aws_sfn_state_machine.naive[0].arn : "(not created)"
}

output "recovery_workflow_guards" {
  description = "What stands between a trigger and an irreversible regional failover, right now."
  value = {
    kill_switch       = "${aws_ssm_parameter.kill_switch.name} = ${var.kill_switch_default} (read first, every execution; unreadable means abort)"
    dry_run           = var.recovery_dry_run ? "ON — the failover step logs and changes nothing" : "OFF — the failover step will make real changes"
    approval_gate     = var.require_approval_for_failover ? "ON — waitForTaskToken, ${var.approval_timeout_minutes} minute timeout" : "OFF — no human decides"
    verification      = "ON — a failed verify FAILS the execution rather than reporting success"
    failback          = "NOT AUTOMATED, deliberately. lambda/recovery.py has a manual failback action; read its docstring for the five things it cannot do."
    effective_rto     = var.require_approval_for_failover ? "worst case ${var.approval_timeout_minutes} minutes before the failover even STARTS — that is a ceiling, not an estimate" : "bounded by detection and execution only"
    NAIVE_ALTERNATIVE = var.create_insecure_examples ? "a second state machine exists with NONE of the above. Same code, different deployment. 'We reviewed the code' is not 'we reviewed the deployment'." : "(not created)"
  }
}

output "failover_drill_commands" {
  description = "The drill, in order. Dry run, then real, then failback. Time every one of them."
  value       = <<-DRILL

    DRY RUN FIRST, EVERY TIME.

      aws stepfunctions start-execution \
        --state-machine-arn ${var.enable_recovery_workflow ? aws_sfn_state_machine.recovery[0].arn : "(disabled)"} \
        --profile ${var.aws_profile} --region ${var.aws_region}

    Read the execution history. Every step, with timestamps. Those timestamps
    ARE your RTO measurement — that is why this is a state machine and not a
    Lambda with a try/except.

      aws stepfunctions get-execution-history --execution-arn <arn> \
        --profile ${var.aws_profile} --region ${var.aws_region} \
        --query 'events[].[timestamp,type,stateEnteredEventDetails.name]' --output table

    FOR REAL: set recovery_dry_run = false and apply, then start again. If the
    approval gate is on, the request arrives by email with a task token:

      aws stepfunctions send-task-success --task-token <TOKEN> --task-output '{}' \
        --profile ${var.aws_profile} --region ${var.aws_region}

    TIME THE APPROVAL SEPARATELY. In every real drill it is the largest single
    component of the RTO and it is the one never included in the estimate.

    FAILBACK IS MANUAL AND IS NOT IN THE WORKFLOW:

      aws lambda invoke --function-name ${var.enable_recovery_workflow ? aws_lambda_function.recovery[0].function_name : "(disabled)"} \
        --payload '{"action":"failback","dry_run":true}' \
        --cli-binary-format raw-in-base64-out \
        --profile ${var.aws_profile} --region ${var.aws_region} /dev/stdout

    Read the CANNOT_REVERSE list in the response before you set dry_run false.

  DRILL
}

output "finding_contract" {
  description = "The Day 08 finding contract, locked at CP2. Reproduced identically in five files; /home/claude/sync_contract.py is what keeps them identical."
  value       = <<-CONTRACT

    =============================================================================
    DAY 08 FINDING CONTRACT — LOCKED AT CP2
    =============================================================================
    This block is reproduced identically in five places. Change one, change all
    five: README.md, lab/README.md, lab/terraform/outputs.tf (finding_contract),
    lab/python/dr_audit.py (module docstring), lab/python/tests/test_checks.py.

    Weights are the repo-wide ones, identical to Days 03 through 07:
    CRITICAL 25, HIGH 10, MEDIUM 4, LOW 1, INFO 0. Score is 100 minus the sum,
    floored at 0. Grades: 90+ A, 75+ B, 60+ C, 40+ D, below that F.

    Day 08 has NO LOW AND NO INFO CHECKS, and that is a decision rather than an
    oversight. On this day every fault either costs you data or costs you time
    during an outage. There is no informational gap in a recovery path — a thing
    that does not matter when the region is on fire does not belong in an audit
    whose whole subject is the hour the region is on fire.

    STATIC STATE — after terraform apply with the shipped defaults
    (create_insecure_examples = true, nat_gateway_strategy = "single",
    create_rds = false, enable_vault_lock = false,
    s3_replication_time_control = false, hosted_zone_id = ""), before any backup
    job has run, before any restore, before any workflow execution.

      ID       SEVERITY   W   N  PTS  SOURCE RESOURCE
      -------  --------  --  --  ---  ------------------------------------------
      DR-001   CRITICAL  25   1   25  aws_autoscaling_group.single_az
      DR-002   HIGH      10   1   10  aws_nat_gateway.main - strategy "single"
      DR-003   HIGH      10   1   10  aws_autoscaling_group.single_az
      DR-004   MEDIUM     4   1    4  aws_autoscaling_group.single_az
      DR-005   CRITICAL  25   0    0  none - SILENT BY SITUATION, see below
      DR-006   HIGH      10   0    0  none - SILENT BY SITUATION, see below
      DR-007   MEDIUM     4   1    4  aws_dynamodb_table.no_pitr
      DR-008   HIGH      10   2   20  aws_backup_vault.main, aws_backup_vault.dr
      DR-009   MEDIUM     4   2    8  aws_backup_vault.main, aws_backup_vault.dr
      DR-010   CRITICAL  25   1   25  account-level singleton - no restore, ever
      DR-011   HIGH      10   0    0  none - SILENT BY DESIGN, see below
      DR-012   MEDIUM     4   1    4  aws_s3_bucket.unversioned
      DR-013   HIGH      10   1   10  aws_s3_bucket_replication_configuration.primary
      DR-014   HIGH      10   0    0  none - SILENT BY SITUATION, see below
      DR-015   CRITICAL  25   1   25  aws_sfn_state_machine.naive
      DR-016   CRITICAL  25   2   50  both Day 08 state machines - never executed
      -------  --------  --  --  ---  ------------------------------------------
      TOTALS                    15  195

      FIFTEEN findings from SIXTEEN checks. Four checks are silent here and they
      are silent for two different reasons, which is the most useful thing in this
      table: three because this particular stack cannot currently produce the
      fault (DR-005, DR-006, DR-014), and one because NO configuration of this
      stack can ever produce it (DR-011).

      Score: 100 - 195 = -95, floored to 0/100. Grade F.

      SEVERITY HISTOGRAM of the 16 checks: 5 CRITICAL, 7 HIGH, 4 MEDIUM,
      0 LOW, 0 INFO.

    THE FOUR STATES

      STATE                                        FINDINGS  POINTS    SCORE  GRADE
      -------------------------------------------  --------  ------  -------  -----
      A  Static: after apply, nothing run yet            15     195    0/100      F
      B  Live: after lab steps 6a, 7 and 8 - one
         on-demand backup copied to DR, one
         workflow execution succeeded, one
         restore performed                               11     125    0/100      F
      C  Sixty-one minutes after B, WITH NOTHING
         CHANGED - the recovery points have aged
         past rpo_target_minutes                         13     145    0/100      F
      -------------------------------------------  --------  ------  -------  -----
      D  Reference build: create_insecure_examples
         = false, nat_gateway_strategy = "per_az",
         s3_replication_time_control = true,
         enable_vault_lock = true, plus a completed
         backup, a completed restore and one
         successful workflow execution                    0       0  100/100      A

      STATE C IS THE POINT OF THIS TABLE AND IT IS THE THESIS OF THE DAY.

      Between B and C, nobody deploys anything. No console click, no apply, no
      merge. Two findings appear because time passed and DR-008 measures the age
      of the newest recovery point against the RPO you declared.

      An audit that passes at 14:00 fails at 15:01 on an unchanged account.

      That is not a defect in the auditor. It is the correct behaviour, and it is
      the difference between a configuration audit and a recovery audit. RTO and
      RPO are not properties of a configuration. They are claims about a
      PROCEDURE, and a claim about a procedure decays continuously from the last
      time somebody ran it. A merge-time-only audit certifies the account as it
      was on the day somebody last changed it, and that is not the property a DR
      posture needs to have.

      With the shipped hourly backup schedule, DR-008 therefore SAWTOOTHS: silent
      for the minutes after each successful job, firing again as the recovery
      point ages past the 60-minute RPO. Two numbers that are one minute apart
      produce different audit results, and both are correct. If that is
      uncomfortable, the fix is not a looser check - it is a schedule that is
      actually faster than the RPO you claimed.

      Day 07's contract had the finding COUNT identical before and after the lab
      with a different SET. Day 08 does not repeat that trick, because forcing it
      here would have been dishonest: doing the work genuinely removes findings.
      What Day 08 has instead is a state that gets WORSE while you are asleep.

    SILENT BY DESIGN — DR-011, a replication or backup copy target in the same
    region as its source.

      No shipped default and no typo can produce this fault. The dr_region
      variable carries a cross-variable validation refusing dr_region ==
      aws_region; the S3 replica bucket is created under provider = aws.dr; the
      AWS Backup copy rule targets the DR vault or does not exist. There is no
      path through this Terraform that puts a DR copy in the primary region, so
      the plan refuses to produce one.

      It is not a hypothetical fault. S3 Same-Region Replication is a real and
      legitimate feature - compliance separation, log aggregation, cross-account
      isolation - and an AWS Backup copy rule will happily target a vault in the
      source region. Both get pressed into service as "DR" by people who were
      solving a different problem last week, and both produce a second copy inside
      the same blast radius.

      A check that stays silent because the stack cannot produce the fault is
      evidence that the auditor does not cry wolf.

    SILENT BY SITUATION — DR-005, DR-006 and DR-014.

      DR-005 and DR-006 are the RDS checks. create_rds defaults to false, so there
      is no RDS instance to be single-AZ or to have one day of retention. The
      moment somebody sets create_rds = true with the shipped defaults, BOTH fire
      immediately, for 35 points, because rds_multi_az defaults to false and
      rds_backup_retention_days defaults to 1.

      DR-014 is the Route 53 failover-record check. The failover record sets
      require a hosted zone you own, hosted_zone_id defaults to empty, so there
      are no failover records to be missing a health check.

      NOTHING HAS TO CHANGE FOR ANY OF THESE TO STOP BEING TRUE, and in DR-005's
      case the change is one boolean typed by somebody adding a database on a
      Thursday.

    THE DIFFERENCE MATTERS. Silent by design tells you something about the
    auditor: it cannot fire, so its silence is a property of the tool. Silent by
    situation tells you nothing about the auditor and everything about today's
    account - and "we have no findings" and "we have nothing to find" are
    different states that render identically in every report. Never read the
    second as the first.

    CHECK INTERACTIONS THAT ARE DELIBERATE, NOT BUGS

      DR-001, DR-003 and DR-004 all fire on aws_autoscaling_group.single_az and
      they are not duplicates. DR-001 is WHERE it runs - one failure domain.
      DR-003 is WHETHER IT NOTICES an application failure - health_check_type
      "EC2" means a deadlocked process is a healthy instance forever. DR-004 is
      WHETHER A REPLACEMENT CAN START AT ALL - a zero grace period is a
      termination loop. Fixing any one leaves the other two, the remediations are
      unrelated, and in most organisations they have different owners: the network
      team owns the subnets, the platform team owns the ASG, and the application
      team owns how long a boot takes.

      DR-002 IS THE ONLY CHECK THAT FIRES ON YOUR OWN CORRECTLY-INTENDED STACK
      rather than on a deliberately broken example. nat_gateway_strategy defaults
      to "single", which is a real, defensible, extremely common cost decision
      that puts a single-AZ dependency inside an architecture everybody calls
      multi-AZ. It is also the only finding in this contract that you clear by
      SPENDING MONEY rather than by fixing a mistake - roughly $36/month more.
      That is deliberate. An auditor whose findings are all strawmen teaches
      people that findings are strawmen.

      DR-008 AND DR-010 LOOK LIKE THE SAME CHECK AND ARE NOT. DR-008 asks "is
      there a recent enough backup". DR-010 asks "has anybody ever proved a backup
      can be turned back into a system". A vault full of fresh, correctly
      retained, cross-region-copied recovery points that has never had a single
      restore performed against it scores 0 on DR-008 and 25 on DR-010, and that
      is the normal state of most organisations. The failure modes DR-010 exists
      for - a rotated KMS key, a missing AMI, an instance type unavailable in the
      DR region, a deprecated engine version, a restore that works and takes nine
      hours - are all invisible in a backup report and all obvious in one restore
      test.

      DR-010 AND DR-016 ARE THE SAME IDEA ABOUT TWO DIFFERENT THINGS - restore
      versus failover - and both are reported at a level ABOVE any single
      resource. DR-010 is an account-level singleton; DR-016 is per state machine.
      Neither is attached to a data resource, deliberately: they are statements
      about the ORGANISATION, not about a bucket, and attaching them to a resource
      id invites somebody to close the finding by deleting the resource.

      DR-013 FIRES ON A CORRECTLY-CONFIGURED REPLICATION RULE. The rule works.
      Objects replicate. What is absent is the METRIC, because Replication Time
      Control is off - and without it there is no way to answer "what is my
      current replication lag", which means there is no way to state an RPO that
      is anything more than an adjective. This is the only check in the set that
      fires on something which is not broken, and it is Day 06's argument in new
      clothes: a summary you cannot check is worse than no summary, and an RPO you
      cannot measure is worse than no RPO, because you will quote it.

      DR-009 FIRES TWICE, ONCE PER VAULT, INCLUDING THE DR VAULT, and is
      deliberately not deduplicated up to the plan. A locked primary vault beside
      an unlocked DR copy vault is a real and common asymmetry, and it is exactly
      backwards: the DR vault is the one an attacker who has already compromised
      the primary account will reach for, because it is the copy that survives
      everything they just did.

      DR-016 FIRES ON THE NAIVE STATE MACHINE TOO, and after lab step 7 it is the
      only DR-016 finding left. An automated failover that has never been executed
      is untested; an automated failover that has never been executed AND has no
      kill switch, no assessment, no approval gate and no verification is untested
      in a way that will be discovered by production. DR-015 and DR-016 fire on
      the same resource for genuinely different reasons and neither remediates the
      other.
    =============================================================================

  CONTRACT
}
