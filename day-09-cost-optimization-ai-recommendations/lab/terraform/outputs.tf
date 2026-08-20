###############################################################################
# Day 09 — outputs.tf
#
# Four jobs, in this order:
#   1. The handful of identifiers you will paste into commands all day.
#   2. THE DECLARED NUMBERS — the age thresholds the auditor cites back.
#   3. Cost, itemised, with the guesses labelled as guesses.
#   4. next_steps: the copy-paste sequence. Its numbering is the contract that
#      lab/README.md follows — if you renumber here, renumber there.
#
# The finding_contract at the bottom is LOCKED at CP2 and reproduced
# identically in five places. Do not edit it in isolation; use
# sync_contract.py.
###############################################################################

###############################################################################
# Endpoints and identifiers
###############################################################################

output "vpc_id" {
  description = "VPC id — the subject of COST-012 when a NAT gateway exists without S3 or DynamoDB endpoints alongside it."
  value       = aws_vpc.main.id
}

output "app_instance_id" {
  description = "The correctly-sized application instance. Its uptime is the input to COST-015 — long-running without a Savings Plan is what the check fires on."
  value       = aws_instance.app.id
}

output "app_instance_type" {
  description = "Whatever instance_type resolved to. If this reads t2 or m3 you have a COST-009 finding on your own stack rather than on the previous_gen example."
  value       = aws_instance.app.instance_type
}

output "app_root_volume_type" {
  description = "Root volume type. gp3 is the answer today. If this reads gp2, COST-010 fires against the correct instance rather than only the insecure example."
  value       = var.root_volume_type
}

output "previous_gen_instance_id" {
  description = "The deliberately previous-generation instance, when insecure examples are on. This is what COST-009 fires against by default."
  value       = var.create_insecure_examples ? aws_instance.previous_gen[0].id : "(create_insecure_examples = false)"
}

output "classic_elb_name" {
  description = "The deliberately Classic Load Balancer, when insecure examples are on. Delete it BEFORE you go on holiday — it bills at ~$16.20/month whether or not anything talks to it, which is the loudest single line item this stack produces."
  value       = var.create_insecure_examples ? aws_elb.classic[0].name : "(create_insecure_examples = false)"
}

output "orphan_volume_ids" {
  description = "The two deliberately unattached EBS volumes. Each bills at ~$0.64/month for the 8 GB gp3 storage they hold. Small individually, big when multiplied by an account's history."
  value = var.create_insecure_examples ? [
    aws_ebs_volume.orphan_a[0].id,
    aws_ebs_volume.orphan_b[0].id,
  ] : []
}

output "orphan_eip_addresses" {
  description = "The two deliberately unassociated Elastic IPs. Since February 2024 these bill at ~$0.005/hour ($3.60/month) EACH. Two orphans is $7.20/month for nothing."
  value = var.create_insecure_examples ? [
    aws_eip.orphan_a[0].public_ip,
    aws_eip.orphan_b[0].public_ip,
  ] : []
}

output "unbounded_log_groups" {
  description = "Log groups with no retention set. Retention 'Never Expire' at $0.03/GB/month is the slowest cost decay in the account — twelve months later it is the third line on the bill."
  value = var.create_insecure_examples ? [
    aws_cloudwatch_log_group.unbounded_a[0].name,
    aws_cloudwatch_log_group.unbounded_b[0].name,
  ] : []
}

output "artifacts_bucket" {
  description = "The application S3 bucket. When enable_bucket_lifecycle is false (the default), objects stay STANDARD forever — that is what COST-014 fires on."
  value       = aws_s3_bucket.artifacts.id
}

output "artifacts_bucket_lifecycle" {
  description = "Whether the bucket carries a lifecycle rule. false means STANDARD ($0.023/GB/month) forever; true means tier to STANDARD_IA at 30 days ($0.0125/GB), Glacier Instant Retrieval at 90 days ($0.004/GB), expire at 365."
  value       = var.enable_bucket_lifecycle ? "attached (transitions at 30/90 days, expiration at 365 days)" : "NOT attached — COST-014 fires"
}

output "app_log_group" {
  description = "Application log group with retention set. Two others (unbounded-a, unbounded-b) are created when insecure examples are on — those are what COST-013 fires against by default."
  value       = aws_cloudwatch_log_group.app.name
}

output "budget_name" {
  description = "Monthly cost budget name, when enable_budget = true. Silent by design against COST-002 because the notifications list is required to be non-empty."
  value       = var.enable_budget ? aws_budgets_budget.monthly[0].name : "(enable_budget = false — COST-001 fires)"
}

output "budget_limit_usd" {
  description = "The monthly limit the budget alarms against, in USD."
  value       = var.enable_budget ? var.budget_monthly_limit_usd : 0
}

output "cost_anomaly_monitor_arn" {
  description = "Cost Anomaly Detection monitor ARN, when enabled. Without this, COST-003 fires. Without a subscription on top of this, the monitor speaks into the console."
  value       = var.enable_cost_anomaly_monitor ? aws_ce_anomaly_monitor.account[0].arn : "(enable_cost_anomaly_monitor = false — COST-003 fires)"
}

output "cost_anomaly_subscription_arn" {
  description = "Cost Anomaly Detection subscription ARN. The path that turns a console-only anomaly into an email somebody reads. Confirm the SNS subscription BEFORE waiting on the first anomaly — see notification_email in variables.tf."
  value       = var.enable_cost_anomaly_monitor ? aws_ce_anomaly_subscription.account[0].arn : "(enable_cost_anomaly_monitor = false)"
}

output "cost_anomaly_threshold_usd" {
  description = "Minimum anomaly impact ($) that produces a notification. Anomalies below this stay in the console."
  value       = var.enable_cost_anomaly_monitor ? var.cost_anomaly_threshold_usd : 0
}

output "cost_sns_topic_arn" {
  description = "SNS topic that Cost Anomaly Detection subscriptions publish to."
  value       = aws_sns_topic.cost.arn
}

output "vpc_endpoints_enabled" {
  description = "Whether the free S3 and DynamoDB gateway endpoints are attached. Both cost nothing. NAT gateway costs $0.045/GB processed; endpoints remove that on S3 and DynamoDB traffic."
  value       = var.enable_vpc_endpoints ? "S3 and DynamoDB gateway endpoints attached to private route table" : "NOT attached — every S3/DynamoDB call from the private subnet traverses NAT and bills per-GB"
}

output "nat_gateway_state" {
  description = "Whether a NAT gateway exists. Off by default because this stack does not need outbound internet from the private subnet — turning it on lets you see COST-012 fire when enable_vpc_endpoints is still false."
  value       = var.enable_nat_gateway ? "ON — ~$32.85/month plus $0.045/GB processed" : "OFF — no outbound internet from the private subnet, no NAT bill"
}

###############################################################################
# The declared numbers — thresholds the auditor cites back
#
# These do not configure any AWS resource. They tell the auditor what
# counts as "old", "stale", "unattended" or "unreviewed", and they are
# exposed as an output so the same numbers appear in `terraform output` and
# in the auditor's --help. A finding that cites "> 7 days" while the auditor
# was invoked with 30 is a finding you cannot reproduce.
###############################################################################

output "declared_thresholds" {
  description = "The numbers the auditor uses to decide what counts as 'old', 'stale' or 'unattended'. Pass these to cost_audit.py --*-days if you want the same output."
  value = {
    volume_orphan_days             = format("%d days  (unattached EBS older than this fires COST-005)", var.volume_orphan_days)
    eip_orphan_days                = format("%d days  (unassociated EIPs older than this fires COST-006)", var.eip_orphan_days)
    snapshot_retention_days        = format("%d days  (EBS snapshots older than this fires COST-007)", var.snapshot_retention_days)
    instance_stopped_days          = format("%d days  (EC2 stopped longer than this fires COST-008)", var.instance_stopped_days)
    long_running_instance_days     = format("%d days  (EC2 running longer than this without SP/RI coverage fires COST-015)", var.long_running_instance_days)
    anomaly_triage_days            = format("%d days  (cost anomaly open without feedback longer than this fires COST-016)", var.anomaly_triage_days)
    tag_coverage_threshold_percent = format("%d%%     (Owner+Project coverage below this fires COST-004)", var.tag_coverage_threshold_percent)
  }
}

###############################################################################
# Cost
#
# Day 09 has three shapes of bill:
#
#   PER HOUR, EXACTLY   NAT gateway, EIPs (attached OR unattached), EC2
#                       instances, the Classic ELB. All countable here.
#   PER MONTH, EXACTLY  Nothing — Budgets, Cost Anomaly Detection and gateway
#                       endpoints are all $0.
#   PER GB / PER REQUEST   EBS storage, S3 storage and requests, CloudWatch
#                       Logs ingest and storage, NAT processing. Not
#                       countable from a plan; estimated below and labelled.
#
# The instance prices below are hardcoded for the shipped defaults
# (t3.micro, t2.micro when insecure). Change instance_type and the labels
# say so.
###############################################################################

locals {
  # Per-hour numbers, indicative us-east-1 on-demand at time of writing.
  cost_hourly = {
    t3_micro       = 0.0104
    t2_micro       = 0.0116
    classic_elb    = 0.025
    nat_gateway    = 0.045
    unattached_eip = 0.005
  }

  hours_per_month = 730

  # Compute a rough monthly figure per resource.
  cost_correct_instance_month = local.cost_hourly.t3_micro * local.hours_per_month
  cost_previous_gen_month     = var.create_insecure_examples ? local.cost_hourly.t2_micro * local.hours_per_month : 0
  cost_classic_elb_month      = var.create_insecure_examples ? local.cost_hourly.classic_elb * local.hours_per_month : 0
  cost_nat_gateway_month      = var.enable_nat_gateway ? local.cost_hourly.nat_gateway * local.hours_per_month : 0
  cost_orphan_eips_month      = var.create_insecure_examples ? 2 * local.cost_hourly.unattached_eip * local.hours_per_month : 0

  # ~$0.08/GB/month for gp3, ~$0.10/GB/month for gp2.
  cost_correct_root_month   = var.root_volume_type == "gp3" ? var.root_volume_size_gb * 0.08 : var.root_volume_size_gb * 0.10
  cost_prevgen_root_month   = var.create_insecure_examples ? 8 * 0.10 : 0     # 8 GB gp2
  cost_orphan_volumes_month = var.create_insecure_examples ? 2 * 8 * 0.08 : 0 # 2 * 8 GB gp3

  cost_stack_month = (
    local.cost_correct_instance_month
    + local.cost_previous_gen_month
    + local.cost_classic_elb_month
    + local.cost_nat_gateway_month
    + local.cost_orphan_eips_month
    + local.cost_correct_root_month
    + local.cost_prevgen_root_month
    + local.cost_orphan_volumes_month
  )
}

output "cost_summary" {
  description = "Indicative monthly cost of this stack, us-east-1 on-demand. The whole point of the day is that these numbers are small individually and matter in aggregate across an account's history."
  value = {
    "01_app_instance"      = format("~$%.2f/month  (%s in us-east-1)", local.cost_correct_instance_month, var.instance_type)
    "02_app_root_volume"   = format("~$%.2f/month  (%d GB %s)", local.cost_correct_root_month, var.root_volume_size_gb, var.root_volume_type)
    "03_previous_gen"      = var.create_insecure_examples ? format("~$%.2f/month  (t2.micro instance, COST-009)", local.cost_previous_gen_month) : "not created"
    "04_previous_gen_root" = var.create_insecure_examples ? format("~$%.2f/month  (8 GB gp2 root, COST-010)", local.cost_prevgen_root_month) : "not created"
    "05_classic_elb"       = var.create_insecure_examples ? format("~$%.2f/month  (Classic ELB, plus $0.008/GB processed, COST-011)", local.cost_classic_elb_month) : "not created"
    "06_orphan_ebs"        = var.create_insecure_examples ? format("~$%.2f/month  (2 x 8 GB unattached gp3, COST-005)", local.cost_orphan_volumes_month) : "not created"
    "07_orphan_eips"       = var.create_insecure_examples ? format("~$%.2f/month  (2 unassociated Elastic IPs at $0.005/hour EACH, COST-006)", local.cost_orphan_eips_month) : "not created"
    "08_nat_gateway"       = var.enable_nat_gateway ? format("~$%.2f/month  (NAT gateway, plus $0.045/GB processed if you drive traffic through it)", local.cost_nat_gateway_month) : "not created"
    "09_bucket_storage"    = "~$0/month at CP2 (empty bucket). Every GB stored is $0.023/month at STANDARD; put objects in and forget the lifecycle, and this is the line that grows."
    "10_log_ingest"        = "~$0/month at CP2 (nothing writing yet). $0.50/GB ingested is the actual price you pay; storage is $0.03/GB/month AFTER."
    "11_budget_and_cad"    = "$0/month regardless. Budgets are within free tier at this scale; Cost Anomaly Detection is entirely free."
    "TOTAL_estimate"       = format("~$%.2f/month  (excluding data transfer, S3 storage growth and Logs ingest, which are the three that actually catch people)", local.cost_stack_month)
    "NOTE"                 = "Numbers are indicative us-east-1 on-demand. The three things NOT counted here — data transfer, S3 storage growth, CloudWatch Logs ingest — are the three that dominate real bills, and they are the three the auditor's guardrail checks (COST-012, COST-014, COST-013) exist to bound."
  }
}

###############################################################################
# next_steps — the copy-paste sequence
#
# The numbering here is the contract lab/README.md follows. Renumber one,
# renumber both. Each step names the resource it inspects and the check(s)
# it exercises, so a step that produces nothing is a finding on its own.
###############################################################################

output "next_steps" {
  description = "The full lab sequence, numbered. lab/README.md's step numbers match these."
  value       = <<-STEPS

    Copy terraform.tfvars.example to terraform.tfvars and set at minimum
    notification_email. Then:

      tofu init && tofu apply

    Then work through the steps below in order. Each one names the
    resource it exercises and the check IDs you should see move.

    -------------------------------------------------------------------

    1. Confirm the SNS subscription for cost alerts.

       Check ${var.notification_email} for a "AWS Notification -
       Subscription Confirmation" message and click the link. Nothing that
       follows works until this is done — and unlike Days 06 and 07, the
       failure mode here is silent: anomalies keep firing, nobody reads
       them, the bill keeps growing.

    2. Run the auditor against the shipped defaults. This is STATIC STATE A.

         cd ../python && pip install -r requirements.txt
         python cost_audit.py --profile ${var.aws_profile} --region ${var.aws_region} --prefix cbc-day09

       Expect roughly 12 findings, ~77 points, score in the low 20s, grade F.
       That is the shape of a Day-1 account: no guardrails, waste from the
       insecure examples, obsolete generations still around.

    3. Turn on the free guardrails one at a time. Each `apply` should
       remove exactly the check ID named.

         # enable_budget = true                 -> COST-001 stops firing
         # enable_cost_anomaly_monitor = true   -> COST-003 stops firing
         # enable_bucket_lifecycle = true       -> COST-014 stops firing

       Re-run cost_audit.py after each apply. Note that at this point
       COST-016 (untriaged anomalies) still cannot fire because Cost Anomaly
       Detection needs 10 days of baseline before it produces its first
       anomaly — that is the honest constraint the finding contract calls
       out.

    4. Remove the deliberate waste. Set create_insecure_examples = false
       and apply. This removes the two orphan volumes, two orphan EIPs,
       the previous-gen instance and its gp2 root, the Classic ELB, and
       the two unbounded log groups.

         # create_insecure_examples = false
         #   -> COST-005, COST-006, COST-009, COST-010, COST-011, COST-013
         #      all stop firing

    5. Add the S3 and DynamoDB gateway endpoints. Free.

         # enable_vpc_endpoints = true          -> COST-012 stops firing

    6. Tag every resource. In this stack, `default_tags` on the provider
       already carries Project/Day/ManagedBy/Owner across every resource,
       so re-running the audit should produce a coverage of 100% and
       COST-004 stays silent. If you added any resources without going
       through this Terraform, tag them now — otherwise COST-004 fires
       against your work, not against this stack.

    7. Explore Cost Explorer. Open the console:

         https://console.aws.amazon.com/cost-management/home

       Group by Owner. Group by Project. Group by Service. Note that all
       three views become useful only after step 6 — an account with 60%
       under "Owner: unknown" is unowned cost regardless of how good
       everything else looks.

    8. Explore Cost Anomaly Detection. Open the console:

         https://console.aws.amazon.com/cost-management/home#/anomaly-detection

       At this point the monitor is enabled but has no history. It needs
       roughly 10 days of baseline before it produces its first anomaly.
       Come back to this step next week. THAT IS THE POINT — Day 09's
       central lesson is that cost is a lagging measure, and the tools
       that catch it work on a delay measured in days, not minutes.

    9. Run the auditor one more time. This is STATE B.

         python cost_audit.py --profile ${var.aws_profile} --region ${var.aws_region} --prefix cbc-day09

       Expect 0 findings against this stack. Grade A.

    10. Two weeks from now (or whenever the next Cost Anomaly Detection
        anomaly appears — you will receive an email), do NOT clear it.
        Leave it un-triaged for anomaly_triage_days days and re-run the
        auditor without --prefix. This is STATE C: an unchanged account,
        the guardrails still on, the anomalies still firing, nobody
        reading them, and COST-016 firing to say so.

          python cost_audit.py --profile ${var.aws_profile} --region ${var.aws_region} --format json > cost-state-c.json

        Then go into the console, mark the anomaly with Feedback (Yes/No/
        Planned Activity — any of the three), and re-run. COST-016 is
        silent again. That round trip is the day's thesis in one exercise.

    11. TEAR DOWN. See lab/teardown-checklist.md. In particular DELETE the
        Classic ELB and the orphan EIPs first — they are the loudest
        recurring charges this stack can produce, and they are the two
        resources that `terraform destroy` will kill correctly but that a
        partial destroy (which is the shape of most real ones) will leave
        behind.

          tofu destroy

  STEPS
}

###############################################################################
# Cost Explorer / Cost Anomaly Detection — the console commands
###############################################################################

output "cost_explorer_commands" {
  description = "The AWS CLI calls that let you inspect the same data the auditor reads. Useful for building intuition about the shape of Cost Explorer and Cost Anomaly Detection responses."
  value       = <<-CE

    Cost Explorer — total cost this month, grouped by service:

      aws ce get-cost-and-usage \
        --time-period Start=$(date -u +%Y-%m-01),End=$(date -u -d 'tomorrow' +%Y-%m-%d) \
        --granularity MONTHLY \
        --metrics UnblendedCost \
        --group-by Type=DIMENSION,Key=SERVICE \
        --profile ${var.aws_profile} --region us-east-1

    Cost Explorer — same but grouped by Owner tag (after step 6):

      aws ce get-cost-and-usage \
        --time-period Start=$(date -u +%Y-%m-01),End=$(date -u -d 'tomorrow' +%Y-%m-%d) \
        --granularity MONTHLY \
        --metrics UnblendedCost \
        --group-by Type=TAG,Key=Owner \
        --profile ${var.aws_profile} --region us-east-1

      Note: the Owner tag has to be ACTIVATED as a cost allocation tag in
      the Billing console BEFORE it can be grouped on. That step is
      account-wide, one-way and manual, and it is the reason many tags
      that "should work" don't.

    Cost Anomaly Detection — list monitors:

      aws ce get-anomaly-monitors \
        --profile ${var.aws_profile} --region us-east-1

    Cost Anomaly Detection — list subscriptions:

      aws ce get-anomaly-subscriptions \
        --profile ${var.aws_profile} --region us-east-1

    Cost Anomaly Detection — list anomalies from the last 30 days
    (empty for the first 10 days after enabling the monitor, deliberately):

      aws ce get-anomalies \
        --date-interval StartDate=$(date -u -d '30 days ago' +%Y-%m-%d),EndDate=$(date -u +%Y-%m-%d) \
        --profile ${var.aws_profile} --region us-east-1

    Budgets — describe the one this stack created:

      aws budgets describe-budget \
        --account-id ${local.account_id} \
        --budget-name ${var.enable_budget ? "cbc-day09-monthly" : "(not created)"} \
        --profile ${var.aws_profile} --region us-east-1

    Savings Plans utilisation over the last 30 days (empty for a lab
    account, deliberately — that is COST-015 in one command):

      aws ce get-savings-plans-utilization \
        --time-period Start=$(date -u -d '30 days ago' +%Y-%m-%d),End=$(date -u +%Y-%m-%d) \
        --profile ${var.aws_profile} --region us-east-1

  CE
}

###############################################################################
# The finding contract — LOCKED at CP2
###############################################################################

output "finding_contract" {
  description = "The Day 09 finding contract, locked at CP2. Reproduced identically in five files; the sync_contract.py script keeps them identical."
  value       = <<-CONTRACT

    =============================================================================
    # CONTRACT-BEGIN
    DAY 09 FINDING CONTRACT — LOCKED AT CP2
    =============================================================================
    This block is reproduced identically in five places. Change one, change all
    five: README.md, lab/README.md, lab/terraform/outputs.tf (finding_contract),
    lab/python/cost_audit.py (module docstring), lab/python/tests/test_checks.py.

    Weights are the repo-wide ones, identical to Days 03 through 08:
    CRITICAL 25, HIGH 10, MEDIUM 4, LOW 1, INFO 0. Score is 100 minus the sum,
    floored at 0. Grades: 90+ A, 75+ B, 60+ C, 40+ D, below that F.

    Day 09 uses CRITICAL, HIGH, MEDIUM and LOW, but not INFO. There is one LOW
    (COST-010, gp2 vs gp3), because the choice is real, cheap and non-urgent —
    unlike anything on Day 08. There is one CRITICAL (COST-016), because it is
    the day's thesis: a cost anomaly nobody triaged is a bill nobody stopped,
    and that is the failure mode the whole day exists to make concrete.

    STATIC STATE — after terraform apply with the shipped defaults
    (create_insecure_examples = true, enable_budget = false,
    enable_cost_anomaly_monitor = false, enable_bucket_lifecycle = false,
    enable_vpc_endpoints = false, enable_nat_gateway = false), before any
    anomaly has been raised, before any triage, before any Savings Plan.

      ID        SEVERITY   W   N  PTS  SOURCE RESOURCE
      --------  --------  --  --  ---  ------------------------------------------
      COST-001  HIGH      10   1   10  account - no budget exists
      COST-002  HIGH      10   0    0  none - SILENT BY DESIGN, see below
      COST-003  HIGH      10   1   10  account - no anomaly monitor
      COST-004  MEDIUM     4   0    0  none - SILENT BY DESIGN, see below
      COST-005  HIGH      10   2   20  aws_ebs_volume.orphan_a, aws_ebs_volume.orphan_b
      COST-006  MEDIUM     4   2    8  aws_eip.orphan_a, aws_eip.orphan_b
      COST-007  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
      COST-008  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
      COST-009  MEDIUM     4   1    4  aws_instance.previous_gen
      COST-010  LOW        1   1    1  aws_instance.previous_gen root volume (gp2)
      COST-011  MEDIUM     4   1    4  aws_elb.classic
      COST-012  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
      COST-013  MEDIUM     4   2    8  aws_cloudwatch_log_group.unbounded_a and _b
      COST-014  MEDIUM     4   1    4  aws_s3_bucket.artifacts - no lifecycle
      COST-015  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
      COST-016  CRITICAL  25   0    0  none - SILENT BY SITUATION, see below
      --------  --------  --  --  ---  ------------------------------------------
      TOTALS                    12   69

      TWELVE findings from SIXTEEN checks. Seven checks are silent here and
      they are silent for two different reasons, which is the most useful thing
      in this table: five because this particular stack cannot currently
      produce the fault (COST-007, COST-008, COST-012, COST-015, COST-016),
      and two because NO configuration of this stack can ever produce them
      (COST-002 and COST-004).

      Score: 100 - 69 = 31/100. Grade F.

      SEVERITY HISTOGRAM of the 16 checks: 1 CRITICAL, 4 HIGH, 10 MEDIUM,
      1 LOW, 0 INFO.

    THE FOUR STATES

      STATE                                        FINDINGS  POINTS    SCORE  GRADE
      -------------------------------------------  --------  ------  -------  -----
      A  Static: after apply, nothing configured        12      69   31/100      F
      B  Live: guardrails on, insecure examples
         off, endpoints on, tags at 100%                  0       0  100/100      A
      C  Thirty days after B, WITH NOTHING
         CHANGED - anomalies raised, none
         triaged, snapshots aged past retention           3      33   67/100      C
      -------------------------------------------  --------  ------  -------  -----
      D  Reference build: everything in B, plus a
         Savings Plan covering baseline usage
         AND each anomaly triaged within its
         SLA AND snapshots pruned by an aging
         rule                                             0       0  100/100      A

      STATE C IS THE POINT OF THIS TABLE AND IT IS THE THESIS OF THE DAY.

      Between B and C, nobody deploys anything. No console click, no apply, no
      merge. Three findings appear because time passed: COST-007 fires as
      snapshots the account has been quietly accumulating age past
      snapshot_retention_days; COST-015 fires as the app instance's uptime
      crosses long_running_instance_days without a Savings Plan being
      purchased; COST-016 fires as Cost Anomaly Detection has produced at least
      one anomaly which nobody has provided Feedback on within
      anomaly_triage_days.

      An audit that passes on the 1st fails on the 31st on an unchanged account.

      That is not a defect in the auditor. It is the correct behaviour, and it
      is the difference between a configuration audit and a cost audit. Cost is
      not a property of a configuration. It is a lagging measure of a decision
      nobody re-examined, and a claim about "we watch our spend" decays
      continuously from the last time somebody looked at Cost Explorer.

      Day 08's contract had a state that decayed WITHIN AN HOUR - DR-008
      fired the minute the newest recovery point aged past a 60-minute RPO,
      and the point was that a merge-time audit is blind to that. Day 09
      makes the same argument on a monthly timescale, with three separate
      decay paths so the pattern is undeniable rather than a single quirky
      check.

    SILENT BY DESIGN - COST-002 (a budget with no notification threshold) and
    COST-004 (cost allocation tag coverage below threshold).

      COST-002: No shipped default and no typo can produce this fault. The
      budget_notifications variable carries a validation refusing an empty
      list, and the aws_budgets_budget resource uses `dynamic "notification"`
      over that list. There is no path through this Terraform that produces a
      budget with zero notifications, so the plan refuses to.

      It is not a hypothetical fault. Every Billing console has a "create
      budget" wizard that will let you click through to a budget with no
      notifications attached, and every account with more than about ten
      budgets has one - usually created for a specific report that generated
      the CSV, and never revisited. A budget without a notification is a
      decorative object.

      COST-004: The AWS provider carries default_tags with Project and Owner,
      which are exactly the tags this check looks for. Every resource that
      goes through this Terraform plan inherits them automatically at create
      time - a resource without them is a resource that was NOT created by
      this plan. So the check stays silent against this stack even at 100%
      target coverage, and the same check fires on the account next door
      where somebody was creating buckets from a shell script.

      A check that stays silent because the stack cannot produce the fault is
      evidence that the auditor does not cry wolf.

    SILENT BY SITUATION - COST-007, COST-008, COST-012, COST-015 and COST-016.

      COST-007 is the aged-snapshot check. A fresh terraform apply produces
      no snapshots at all, and even after the lab creates one for backup
      testing, snapshot_retention_days (default 90) is a long time. In a real
      account this fires readily - every automated backup rule accumulates
      copies unless a companion rule ages them out.

      COST-008 is the stopped-instance check. The app instance defaults to
      running; nothing in the lab stops it and leaves it for 30 days. In a
      real account it fires against forgotten test boxes.

      COST-012 is the NAT-without-endpoints check. enable_nat_gateway defaults
      to false, so there is no NAT gateway for the check to fire against. The
      moment somebody sets enable_nat_gateway = true WITHOUT setting
      enable_vpc_endpoints = true, it fires immediately with 4 points.

      COST-015 is the long-running-without-Savings-Plan check. The app
      instance was created seconds ago at apply time, so uptime is not yet
      above long_running_instance_days (default 30). This one fires with the
      clock alone, without anybody changing anything, and that is exactly the
      lesson of STATE C.

      COST-016 is the untriaged-anomaly check. With enable_cost_anomaly_monitor
      = false there is no monitor and no anomalies to triage. Once the
      monitor is enabled it needs roughly 10 days of baseline before producing
      its first anomaly. Once anomalies exist, this check fires with the
      CLOCK ALONE - no configuration change required - until somebody opens
      the console and marks the anomaly with Feedback.

      NOTHING HAS TO CHANGE FOR ANY OF THESE TO STOP BEING SILENT except the
      passage of time.

    THE DIFFERENCE MATTERS. Silent by design tells you something about the
    auditor: it cannot fire, so its silence is a property of the tool. Silent
    by situation tells you nothing about the auditor and everything about
    today's account - and "we have no findings" and "we have nothing to find"
    are different states that render identically in every report. Never read
    the second as the first.

    CHECK INTERACTIONS THAT ARE DELIBERATE, NOT BUGS

      COST-001 AND COST-002 LOOK LIKE THE SAME CHECK AND ARE NOT. COST-001
      fires when NO budget exists. COST-002 fires when a budget EXISTS but has
      no notification threshold. The first is a missing guardrail; the second
      is a guardrail that speaks into the void. Fixing COST-001 by creating a
      budget with zero notifications is exactly what people do, and it is the
      transition COST-002 is there to catch. On this stack COST-002 is silent
      by design; on somebody else's stack it is the second-most common cost
      governance finding, after "no budget at all".

      COST-003 AND COST-016 ARE THE SAME PATTERN AT TWO LAYERS. COST-003 asks
      "does the anomaly detector exist"; COST-016 asks "does anybody read
      what it says". A monitor without a subscription is halfway there. A
      monitor with a subscription pointed at an unconfirmed SNS topic is
      three-quarters of the way there. A monitor with a subscription pointed
      at a confirmed SNS topic whose emails nobody reads is COST-016, and that
      is the shape of most real accounts that have "cost monitoring".

      COST-005 AND COST-006 ARE THE SAME IDEA AT DIFFERENT PRICE POINTS. An
      unattached EBS volume is $0.08/GB/month. An unassociated EIP is
      $3.60/month flat, since February 2024. Both are "resources billing for
      nothing", both accumulate in the same way (a stack that half-destroyed,
      a manual test that "we'll clean up later"), and both are worth
      surfacing separately so remediation is not one giant list.

      COST-009 AND COST-010 FIRE ON THE SAME INSTANCE and are not duplicates.
      COST-009 says "the instance family is previous-generation". COST-010
      says "the root volume type is superseded". Same resource, unrelated
      remediations, potentially different owners: the platform team owns the
      instance type, and the storage or database team may own the volume type.
      Fixing one leaves the other.

      COST-013 FIRES ONCE PER LOG GROUP, DELIBERATELY NOT DEDUPLICATED. Each
      log group is billed independently and each one has a separate person or
      pipeline whose logs land there. A single finding at "account has 40
      unbounded log groups" is a finding nobody knows how to remediate,
      because there is no single owner. Per-log-group findings can be routed
      to per-log-group owners.

      COST-015 IS THE ONLY CHECK THAT DEPENDS ON A SUBJECTIVE JUDGEMENT, and
      it is deliberately narrow to compensate. "Should we buy a Savings Plan"
      is a real, difficult decision that depends on how confident the team is
      that the workload will still exist in a year. The check does not answer
      it. It only asks "has anyone LOOKED at this question for a workload
      that has been running longer than a month". A "yes we looked, decided
      not to" answer is a suppression comment, not a finding to leave open -
      and the check's remediation language reflects that.

      COST-016 AND EVERY OTHER CHECK: it is the only CRITICAL because it is
      the only one where the failure mode is "the whole cost governance
      program does not work". Every other finding is a specific missing or
      wasteful resource. COST-016 is the meta-check: the machine is running,
      the alerts are firing, nobody is reading them. A stack where every
      other check is green and COST-016 is red is an account that has bought
      cost tooling and not yet started using it, which is the modal state of
      cost tooling.
    # CONTRACT-END
    =============================================================================

  CONTRACT
}
