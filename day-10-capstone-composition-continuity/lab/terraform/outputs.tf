###############################################################################
# Day 10 — outputs.tf
#
# Four jobs, in this order:
#   1. Identifiers you paste into commands all day.
#   2. THE DECLARED NUMBERS — the SLA thresholds the auditor cites back.
#   3. Cost, itemised, with guesses labelled as guesses.
#   4. next_steps: the copy-paste sequence. Its numbering is the contract
#      lab/README.md follows — if you renumber here, renumber there.
#
# The finding_contract at the bottom is LOCKED at CP2 and reproduced
# identically in five places (via sync_contract.py).
###############################################################################

###############################################################################
# Endpoints and identifiers
###############################################################################

output "archive_bucket_name" {
  description = "S3 bucket where every audit report lands. Queryable substrate for CAP-011 (Athena) and the visible artefact for CAP-010 (dashboard)."
  value       = aws_s3_bucket.archive.id
}

output "archive_bucket_arn" {
  description = "ARN of the archive bucket. Reference from other stacks or from the audit-runner's IAM policy in another account."
  value       = aws_s3_bucket.archive.arn
}

output "runner_function_name" {
  description = "The audit-runner Lambda's function name. Manual invocation: `aws lambda invoke --function-name <this> --payload '{}' /dev/stdout`."
  value       = aws_lambda_function.runner.function_name
}

output "runner_function_arn" {
  description = "Full ARN of the audit-runner Lambda."
  value       = aws_lambda_function.runner.arn
}

output "runner_role_arn" {
  description = "IAM role ARN. Attach additional read policies here to extend the audit's scope beyond SecurityAudit + AWSBillingReadOnlyAccess."
  value       = aws_iam_role.runner.arn
}

output "runner_log_group" {
  description = "CloudWatch log group name for the runner. Retention set via log_retention_days (default 30) so Day 09's COST-013 stays silent here — a lesson-integrating detail."
  value       = aws_cloudwatch_log_group.runner.name
}

output "alarms_topic_arn" {
  description = "SNS topic ARN. The Lambda-errors alarm and CAP-016 unread-report notifications publish here. CONFIRM THE EMAIL SUBSCRIPTION after apply."
  value       = aws_sns_topic.alarms.arn
}

output "schedule_state" {
  description = "Whether the EventBridge schedule is armed. STATE A condition: OFF; CAP-001 fires."
  value       = var.enable_scheduler ? "ARMED — every ${var.schedule_interval_days} day(s)" : "DISARMED — CAP-001 fires"
}

output "schedule_expression" {
  description = "The literal rate() expression EventBridge uses when the scheduler is armed."
  value       = var.enable_scheduler ? "rate(${var.schedule_interval_days} ${var.schedule_interval_days == 1 ? "day" : "days"})" : "(scheduler disabled)"
}

output "archive_versioning_state" {
  description = "Whether the archive bucket is versioned. STATE A condition: Suspended; CAP-004 fires. Versioning is what makes 'when did this finding first appear' answerable."
  value       = var.enable_archive_versioning ? "Enabled" : "Suspended — CAP-004 fires"
}

output "archive_lifecycle_state" {
  description = "Whether the archive bucket has a lifecycle rule. STATE A condition: not attached; CAP-005 fires."
  value       = var.enable_archive_lifecycle ? "attached (30d→IA, 90d→GLACIER_IR, expire 730d)" : "NOT attached — CAP-005 fires"
}

output "lambda_alarm_state" {
  description = "Whether the Lambda-errors CloudWatch alarm exists. STATE A condition: absent; CAP-009 fires."
  value       = var.enable_lambda_alarm ? "ARMED — threshold ${var.lambda_error_alarm_threshold} error(s)/hour" : "MISSING — CAP-009 fires"
}

output "dashboard_state" {
  description = "Whether the CloudWatch dashboard exists. STATE A condition: absent; CAP-010 fires."
  value       = var.enable_dashboard ? "created (${var.name_prefix}-capstone)" : "MISSING — CAP-010 fires"
}

output "athena_state" {
  description = "Whether Athena database + workgroup exist over the archive. STATE A condition: absent; CAP-011 fires."
  value       = var.enable_athena_table ? "created (workgroup ${var.name_prefix}-audits)" : "MISSING — CAP-011 fires"
}

output "reference_arch_state" {
  description = "Whether the composed Days 01–09 reference architecture is deployed. Off by default because it costs ~$7/day to keep running. CAP-014 tests drift against it when on."
  value       = var.enable_reference_arch ? "deployed — CAP-014 tests drift" : "not deployed — CAP-014 silent by design"
}

###############################################################################
# The declared numbers — SLAs and thresholds the auditor cites back
#
# These do not configure any AWS resource. They tell the auditor what
# counts as "unread", "stale", "silent" or "misaligned", and they are
# exposed as an output so the same numbers appear in `terraform output` and
# in the auditor's --help. A finding that cites a 7-day SLA while the
# auditor was invoked with 3 is a finding you cannot reproduce.
###############################################################################

output "declared_thresholds" {
  description = "The numbers the auditor uses to decide what counts as 'unread', 'stale', 'silent'. Pass these to capstone_audit.py --*-days if you want the same output."
  value = {
    schedule_interval_days       = format("%d days  (CAP-002 fires when > 7; CAP-003 fires when last invocation age > interval * 1.5)", var.schedule_interval_days)
    lambda_error_alarm_threshold = format("%d error(s)/hour  (CAP-009 alarm threshold)", var.lambda_error_alarm_threshold)
    suppression_review_days      = format("%d days  (CAP-012 fires on suppressions older than this without a fresh review)", var.suppression_review_days)
    report_unread_days           = format("%d days  (CAP-016 fires on unread reports past this age)", var.report_unread_days)
    sla_days_critical            = format("%d day(s)   (CRITICAL findings acknowledgement SLA — CAP-016)", var.sla_days_by_severity.critical)
    sla_days_high                = format("%d days     (HIGH findings acknowledgement SLA)", var.sla_days_by_severity.high)
    sla_days_medium              = format("%d days     (MEDIUM findings acknowledgement SLA)", var.sla_days_by_severity.medium)
    sla_days_low                 = format("%d days    (LOW findings acknowledgement SLA)", var.sla_days_by_severity.low)
  }
}

###############################################################################
# Cost
#
# Day 10 has three cost shapes:
#
#   PER MONTH, EXACTLY   CloudWatch alarm ($0.10 each), CloudWatch dashboard
#                        ($3.00 each), Lambda base (free for the volumes
#                        Day 10 produces).
#   PER INVOCATION       Lambda compute (fractional cent), S3 PUT ($0.005 per
#                        1000), CloudWatch Logs ingest ($0.50/GB).
#   PER MONTH, LARGER    Reference-arch module (~$210/month when enabled),
#                        Athena queries ($5/TB scanned, near-zero for the
#                        tiny archive but non-zero if you keep querying).
#
# The reference-arch line is by far the biggest. Everything else on this day
# is coffee-money.
###############################################################################

locals {
  # A conservative estimate: audit runs weekly, 30s each, 512 MB memory.
  # Lambda compute price: $0.0000166667/GB-second.
  invocations_per_month     = 30 / var.schedule_interval_days
  lambda_gb_seconds         = local.invocations_per_month * (var.lambda_memory_mb / 1024.0) * 30
  cost_lambda_compute_month = local.lambda_gb_seconds * 0.0000166667

  # S3 PUTs: one per invocation per audited day. Assume 1 day (Day 09).
  cost_s3_put_month = local.invocations_per_month * 0.000005

  # CloudWatch dashboard: $3.00 per dashboard-month, flat.
  cost_dashboard_month = var.enable_dashboard ? 3.00 : 0

  # CloudWatch alarm: $0.10 per alarm-month.
  cost_alarm_month = var.enable_lambda_alarm ? 0.10 : 0

  # Reference-arch module (when enabled) — indicative ~$7/day.
  cost_reference_arch_month = var.enable_reference_arch ? 210.00 : 0

  cost_stack_month = (
    local.cost_lambda_compute_month
    + local.cost_s3_put_month
    + local.cost_dashboard_month
    + local.cost_alarm_month
    + local.cost_reference_arch_month
  )
}

output "cost_summary" {
  description = "Indicative monthly cost of this stack, us-east-1 on-demand. The reference-arch line dominates by two orders of magnitude when enabled."
  value = {
    "01_lambda_compute"  = format("~$%.4f/month  (%d invocation(s) at %d MB, 30s each)", local.cost_lambda_compute_month, ceil(local.invocations_per_month), var.lambda_memory_mb)
    "02_s3_puts"         = format("~$%.4f/month  (one PUT per invocation per audited day)", local.cost_s3_put_month)
    "03_s3_storage"      = "~$0/month at CP2 (archive is empty until first invocation). ~$0.023/GB/month growth."
    "04_cloudwatch_logs" = "~$0/month at CP2. $0.50/GB ingest + $0.03/GB/month storage AFTER."
    "05_dashboard"       = var.enable_dashboard ? "$3.00/month (flat)" : "not created"
    "06_lambda_alarm"    = var.enable_lambda_alarm ? "$0.10/month" : "not created"
    "07_eventbridge"     = var.enable_scheduler ? "$0/month (rate() rules are free)" : "not created"
    "08_athena"          = var.enable_athena_table ? "$0/month base + $5/TB scanned" : "not created"
    "09_sns"             = "$0/month for email at this scale"
    "10_reference_arch"  = var.enable_reference_arch ? "~$210/month (composed Days 01-09 stack)" : "not created"
    "TOTAL_estimate"     = format("~$%.2f/month  (excluding reference-arch when off)", local.cost_stack_month)
    "NOTE"               = "The dominant cost when reference_arch is enabled is the reference-arch itself, not this day's audit infrastructure. That is the honest picture — the ambient audit costs almost nothing; running a workload for it to audit costs money."
  }
}

###############################################################################
# next_steps — the copy-paste sequence
#
# The numbering here is the contract lab/README.md follows. Renumber one,
# renumber both.
###############################################################################

output "next_steps" {
  description = "The full lab sequence, numbered. lab/README.md's step numbers match these."
  value       = <<-STEPS

    Copy terraform.tfvars.example to terraform.tfvars and set at minimum
    notification_email and owner. Then:

      tofu init && tofu apply

    Then work through the steps below in order. Each one names the
    resource it exercises and the check IDs you should see move.

    -------------------------------------------------------------------

    1. Confirm the SNS subscription for alarms.

       Check ${var.notification_email} for a "AWS Notification -
       Subscription Confirmation" message and click the link. Nothing
       that follows works until this is done — the Lambda-errors alarm
       and the CAP-016 unread-report notifications both publish here.

    2. Run the auditor against the shipped defaults. This is STATIC STATE A.

         cd ../python && pip install -r requirements.txt
         python capstone_audit.py --profile ${var.aws_profile} --region ${var.aws_region} \\
           --archive-bucket ${aws_s3_bucket.archive.id}

       Expect 8 findings, 47 points, score 53/100, grade D.

       That is the shape of an ambient-audit deployment where the
       infrastructure was applied but nothing was turned on. It is worse
       than it looks — steps 3-6 fix six of the eight findings with
       exactly six variable toggles.

    3. Turn on the free (or nearly free) guardrails one at a time.

         # enable_scheduler = true          -> CAP-001 stops firing
         # enable_archive_versioning = true -> CAP-004 stops firing
         # enable_archive_lifecycle  = true -> CAP-005 stops firing
         # enable_lambda_alarm       = true -> CAP-009 stops firing

       Re-run capstone_audit.py after each apply.

    4. Turn on the two paid guardrails.

         # enable_dashboard    = true       -> CAP-010 stops firing (+$3/mo)
         # enable_athena_table = true       -> CAP-011 stops firing (+near-$0)

    5. Add a suppressions.yaml file to the archive bucket root.

         cat > suppressions.yaml <<'YAML'
         # Documented exceptions with review dates.
         # Every entry MUST carry a review_by field, or CAP-012 fires.
         suppressions: []
         YAML
         aws s3 cp suppressions.yaml s3://${aws_s3_bucket.archive.id}/suppressions.yaml

       This stops CAP-008 firing (no baseline suppression file present).

    6. Configure git remote metadata for the auditor.

       The runner reads a git-remote hint from a report field. Populate it
       either by including the git remote URL as ENABLED_GIT_REMOTE in the
       Lambda environment, or by tagging the runner Lambda with GitRemote.
       This stops CAP-015 firing.

    7. Trigger an invocation manually to seed the archive.

         aws lambda invoke --function-name ${aws_lambda_function.runner.function_name} \\
           --payload '{}' /dev/stdout --profile ${var.aws_profile} --region ${var.aws_region}

       Then verify:

         aws s3 ls s3://${aws_s3_bucket.archive.id}/reports/ --recursive --profile ${var.aws_profile}

       One JSON should appear under reports/day=09/year=YYYY/month=MM/day=DD/.

    8. Run the auditor one more time. This is STATE B.

         python capstone_audit.py --profile ${var.aws_profile} --region ${var.aws_region} \\
           --archive-bucket ${aws_s3_bucket.archive.id}

       Expect 0 findings, 100/100, grade A.

    9. WAIT (or fake-wait) 30 days. This is STATE C — the day's thesis.

       In a real deployment, three findings will re-emerge WITHOUT
       ANYBODY CHANGING ANYTHING:
         - CAP-003 fires if EventBridge missed an invocation.
         - CAP-012 fires as suppressions age past ${var.suppression_review_days} days
           without a documented re-review.
         - CAP-016 fires on the latest report if it has been open for
           more than ${var.report_unread_days} days without acknowledgement.

       Total: 3 findings, 45 points, score 55/100, grade F.

       STATE C IS WORSE THAN STATE A. Read that again. A silent break in
       the audit programme is a worse posture than never having had one,
       because you think you have it. That is the whole day.

       To see STATE C in the lab without waiting 30 days:

         cd lab/python
         python3 -m unittest tests.test_checks.TestContractTotals.test_state_c_worse_than_state_a -v

    10. Enable the reference-arch (optional, expensive).

        # enable_reference_arch = true      -> deploys the composed Day 01-09 stack

        Now CAP-006 (cross-cutting risk) has resources to correlate against
        and CAP-014 (reference-arch drift) has something to check drift
        against. Both stay silent as long as the reference-arch is
        actually the reference — that is what "reference" means.

    11. Tear down.

        See lab/teardown-checklist.md. IMPORTANT: disable
        enable_reference_arch = false and apply BEFORE tofu destroy, so
        the composed stack tears down cleanly and the archive is empty of
        its last-run reports.

          tofu destroy

  STEPS
}

###############################################################################
# ambient_audit_commands — the CLI calls that inspect the runner state
###############################################################################

output "ambient_audit_commands" {
  description = "The AWS CLI calls that let you inspect the audit-runner's state directly."
  value       = <<-AMBIENT

    # List reports written by the runner:
    aws s3 ls s3://${aws_s3_bucket.archive.id}/reports/ --recursive --profile ${var.aws_profile}

    # Read the most recent report (Day 09):
    aws s3api list-objects-v2 --bucket ${aws_s3_bucket.archive.id} \\
      --prefix reports/day=09/ --profile ${var.aws_profile} \\
      --query 'reverse(sort_by(Contents, &LastModified))[0].Key' --output text | \\
      xargs -I {} aws s3 cp s3://${aws_s3_bucket.archive.id}/{} - --profile ${var.aws_profile}

    # Trigger the runner ad hoc for a specific day:
    aws lambda invoke --function-name ${aws_lambda_function.runner.function_name} \\
      --payload '{"day": "09"}' --profile ${var.aws_profile} --region ${var.aws_region} /dev/stdout

    # Read the runner's log group tail:
    aws logs tail ${aws_cloudwatch_log_group.runner.name} --since 1h \\
      --profile ${var.aws_profile} --region ${var.aws_region}

    # See the EventBridge rule and its next scheduled fire time:
    aws events describe-rule --name ${var.name_prefix}-schedule \\
      --profile ${var.aws_profile} --region ${var.aws_region} 2>/dev/null || \\
      echo "(no schedule — enable_scheduler is false)"

    # See recent Lambda errors:
    aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Errors \\
      --dimensions Name=FunctionName,Value=${aws_lambda_function.runner.function_name} \\
      --start-time $(date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ) \\
      --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \\
      --period 3600 --statistics Sum \\
      --profile ${var.aws_profile} --region ${var.aws_region}

  AMBIENT
}

###############################################################################
# The finding contract — LOCKED at CP2
###############################################################################

output "finding_contract" {
  description = "The Day 10 finding contract, locked at CP2. Reproduced identically in five files; sync_contract.py keeps them identical."
  value       = <<-CONTRACT

    =============================================================================
    # CONTRACT-BEGIN
    DAY 10 FINDING CONTRACT — LOCKED AT CP2
    =============================================================================
    This block is reproduced identically in five places. Change one, change all
    five: README.md, lab/README.md, lab/terraform/outputs.tf (finding_contract),
    lab/python/capstone_audit.py (module docstring), lab/python/tests/test_checks.py.

    Weights are the repo-wide ones, identical to Days 03 through 09:
    CRITICAL 25, HIGH 10, MEDIUM 4, LOW 1, INFO 0. Score is 100 minus the sum,
    floored at 0. Grades: 90+ A, 75+ B, 60+ C, 40+ D, below that F.

    Day 10 uses all four severities except INFO, matching Day 09. There is one
    LOW (CAP-015, git remote metadata) because the finding is a housekeeping
    detail that makes reports traceable but doesn't degrade the audit's
    correctness. There are TWO CRITICALs (CAP-006 cross-cutting risk, CAP-016
    unread report) because those are the two failure modes where the audit
    programme itself is broken rather than just incomplete.

    STATIC STATE — after terraform apply with the shipped defaults
    (all enable_* toggles false, no suppressions file uploaded, no reports
    in the archive yet).

      ID       SEVERITY   W   N  PTS  SOURCE RESOURCE
      -------  --------  --  --  ---  ---------------------------------------------
      CAP-001  HIGH      10   1   10  account - no EventBridge schedule
      CAP-002  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
      CAP-003  HIGH      10   0    0  none - SILENT BY SITUATION, see below
      CAP-004  HIGH      10   1   10  aws_s3_bucket.archive - versioning suspended
      CAP-005  MEDIUM     4   1    4  aws_s3_bucket.archive - no lifecycle rule
      CAP-006  CRITICAL  25   0    0  none - SILENT BY SITUATION, see below
      CAP-007  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
      CAP-008  MEDIUM     4   1    4  account - no suppressions.yaml
      CAP-009  HIGH      10   1   10  aws_lambda_function.runner - no error alarm
      CAP-010  MEDIUM     4   1    4  account - no CloudWatch dashboard
      CAP-011  MEDIUM     4   1    4  account - no Athena table over archive
      CAP-012  HIGH      10   0    0  none - SILENT BY SITUATION, see below
      CAP-013  MEDIUM     4   0    0  none - SILENT BY DESIGN, see below
      CAP-014  MEDIUM     4   0    0  none - SILENT BY DESIGN, see below
      CAP-015  LOW        1   0    0  none - SILENT BY SITUATION, see below
      CAP-016  CRITICAL  25   0    0  none - SILENT BY SITUATION, see below
      -------  --------  --  --  ---  ---------------------------------------------
      TOTALS                    7   46

      SEVEN findings from SIXTEEN checks. Nine checks are silent here —
      two by design (CAP-013, CAP-014) and seven by situation (CAP-002,
      CAP-003, CAP-006, CAP-007, CAP-012, CAP-015, CAP-016).

      Score: 100 - 46 = 54/100. Grade D.

      SEVERITY HISTOGRAM of the 16 checks: 2 CRITICAL, 5 HIGH, 8 MEDIUM,
      1 LOW, 0 INFO.

    THE FOUR STATES

      STATE                                        FINDINGS  POINTS    SCORE  GRADE
      -------------------------------------------  --------  ------  -------  -----
      A  Static: apply done, all toggles off,
         no history, no suppressions                     7      46   54/100      D
      B  Live: all toggles on, suppressions file
         present with review dates, git remote
         configured, at least one report written         0       0  100/100      A
      C  Thirty days after B, WITH NOTHING
         CHANGED - scheduler stopped 14 days ago,
         four weekly reports piled up unread,
         one suppression past review                     6     120    0/100      F
      -------------------------------------------  --------  ------  -------  -----
      D  Reference build: everything in B, plus
         weekly triage rota where each report is
         acknowledged within its SLA and
         suppressions are reviewed on cadence            0       0  100/100      A

      STATE C IS DRAMATICALLY WORSE THAN STATE A. Read that twice.

      An operator in STATE A has a score of 54/100 and knows the ambient
      audit programme has not been set up. Bad posture, but informed.

      An operator in STATE C has a score of 0/100 and believes they have
      working cost and security governance. The runner is deployed, the
      alarms are wired, the dashboard exists, the archive is populated,
      the suppressions are documented. What has silently happened is:

        - The EventBridge rule stopped firing about two weeks ago. Nobody
          notices because there is no "the scheduler didn't fire"
          notification - the absence of activity is the failure mode.
        - Four consecutive weekly reports piled up unread in the archive
          (CAP-016 fires 4 times at 25 points each = 100 points). Nobody
          triaged them because the weekly review meeting was cancelled
          for month-end.
        - One suppression's review_by date passed 15 days ago. Nobody
          revisited it (CAP-012 fires with 10 points). The exception is
          now an ignored finding without an active decision.
        - CAP-003 fires with 10 points to name the scheduler silence
          explicitly.

      Total: 120 points, floored at 0/100. Grade F.

      STATE C IS THE INFORMED VERSION OF THE STATE-A OPERATOR'S IGNORANCE,
      compounded by two weeks of accumulated debt. This is the day's
      thesis. Days 01-09 audit CONFIGURATION - the state of a resource
      at a moment in time. Day 10 audits PROCESS - whether the
      configuration auditing is still happening at all.

      A process that used to work and stopped is a worse posture than a
      process that was never started. STATE C is the shape of an
      organisation that "did FinOps" for a quarter and then stopped
      without noticing.

    SILENT BY DESIGN — CAP-013 (SLA per severity not defined) and CAP-014
    (reference-arch drift).

      CAP-013: The sla_days_by_severity variable's type constraint requires
      all four severity keys (critical, high, medium, low) to be present in
      the object literal. The default value provides all four. The
      validation block requires them to be monotonically non-decreasing.
      There is no path through this Terraform that produces a stack with
      an undefined-per-severity SLA. So the check stays silent against
      this stack. It will fire immediately on a deployment that imports
      the module and passes sla_days_by_severity = {} or on a real
      organisation that has "an SLA" but where the ambiguity between
      severities is where the missed acknowledgements accumulate.

      CAP-014: When enable_reference_arch = false, no resource in this
      stack claims to be a reference. The check cannot fire because it
      has nothing to compare against. When enable_reference_arch = true
      the check becomes a real comparison — does running each prior day's
      audit against the composed module produce zero findings, as the
      module claims. Answering "yes" every time is the definition of
      "reference"; the first "no" is CAP-014 firing.

      Both silent-by-design classifications are structural facts about
      this stack, not judgements about the account.

    SILENT BY SITUATION — CAP-002, CAP-003, CAP-006, CAP-007, CAP-012, CAP-016.

      CAP-002 (schedule interval > 7 days): silent because no schedule
      exists in STATE A. schedule_interval_days is 7 by default; when
      enable_scheduler goes true, the rate() expression is
      rate(7 days), and CAP-002 stays silent because 7 is the boundary,
      not above it. Change schedule_interval_days to 14 and this check
      fires without touching enable_scheduler.

      CAP-003 (last invocation age > interval * 1.5): silent because no
      invocations have happened. In STATE B (after one manual invocation
      to seed the archive), the check is silent because the invocation is
      fresh. In STATE C the check fires because the last invocation is
      now older than 10.5 days (interval 7 * 1.5), and nothing has
      re-fired the scheduler.

      CAP-006 (cross-cutting risk): silent because there are no prior-day
      findings in the archive. Requires at least two report objects
      referencing the same ARN across different days. Silent forever on
      an audit-runner that only enables day 09 (the shipped default);
      becomes possible once ENABLED_DAYS is expanded.

      CAP-007 (findings not deduplicated across audits): silent because
      no reports exist yet. Once reports exist, this checks whether the
      same finding appears in consecutive reports with different resource
      IDs due to normalisation drift.

      CAP-012 (suppressions past review): silent because no suppressions
      exist. Once suppressions.yaml is uploaded and its entries have
      review_by fields, this fires as those dates pass. STATE C's
      manifestation.

      CAP-016 (report unread past SLA): silent because no reports exist.
      Once reports exist and time passes, this fires as the newest report's
      age crosses report_unread_days without an acknowledgement API call.
      This is the CRITICAL that carries the day's thesis.

      CAP-015 is the git-remote-metadata check on the newest report in
      the archive. In STATE A there are no reports at all, so the check
      has nothing to inspect - silent by situation. Once STATE B is
      reached and reports start landing, CAP-015 fires immediately if the
      runner Lambda was deployed without ENABLED_GIT_REMOTE or a
      GitRemote tag.

      NOTHING HAS TO CHANGE FOR ANY OF THESE TO STOP BEING SILENT except
      the passage of time and the population of the archive.

    THE DIFFERENCE MATTERS. Silent-by-design tells you something about
    the auditor: it cannot fire, so its silence is a property of the
    tool. Silent-by-situation tells you nothing about the auditor and
    everything about today's account. "We have no findings" and "we
    have nothing to find" are different states that render identically
    in every report. Never read the second as the first.

    CHECK INTERACTIONS THAT ARE DELIBERATE, NOT BUGS

      CAP-001 AND CAP-003 LOOK LIKE THE SAME CHECK AND ARE NOT. CAP-001
      catches the absence of a schedule at ALL. CAP-003 catches a schedule
      that EXISTS but hasn't fired. The first fault is "we never set up
      automation"; the second is "automation stopped, nobody noticed".
      On this stack CAP-001 fires in STATE A and CAP-003 fires in
      STATE C, and they are the two sides of the same governance failure
      at two different lifecycles.

      CAP-004 AND CAP-005 ARE THE SAME IDEA AT DIFFERENT ANGLES. CAP-004
      asks "can I answer when did this finding first appear" — that
      requires versioning. CAP-005 asks "will this archive itself become
      expensive over time" — that requires lifecycle. Both are S3-bucket
      properties, both fire independently, both are trivial one-line
      Terraform fixes. Bucketing them together as "S3 hygiene" would
      confuse two different questions.

      CAP-008 AND CAP-012 ARE A LIFECYCLE. CAP-008 fires when there is no
      suppression file at all — the account has never articulated the
      exceptions it wants the auditor to skip. CAP-012 fires when the
      exceptions are present but stale — the account articulated them
      once and never revisited. On a mature account, CAP-008 fires briefly
      after the first audit and then never again; CAP-012 fires on cadence
      as review dates expire. These are the two phases of "exception
      management works".

      CAP-009 AND CAP-016 ARE THE SAME PATTERN AT TWO LAYERS OF THE STACK.
      CAP-009 asks "does the Lambda have an error alarm" — a technical
      failure of the runner. CAP-016 asks "does anybody read the reports"
      — an organisational failure to consume the runner's output. A
      stack where CAP-009 is silent (alarm exists) and CAP-016 is
      firing (nobody reads reports) is technically working audit
      infrastructure that produces no organisational value. That is the
      shape of most cost programmes.

      CAP-010 AND CAP-011 ARE THE SAME QUESTION AT TWO TIME HORIZONS.
      CAP-010 (dashboard) asks "is there ONE URL a stakeholder can
      click today to see the current state". CAP-011 (Athena) asks "can
      an operator answer HISTORICAL questions about what the state used
      to be". Both are queryability questions, in tension with each
      other: dashboards give right-now, Athena gives history-back-to-
      whenever. Neither substitutes for the other.

      CAP-006 IS THE ONLY CHECK THAT LOOKS AT MORE THAN ONE DAY'S
      FINDINGS AT ONCE, and it is deliberately narrow. It only fires when
      the same ARN appears in findings from TWO OR MORE prior-day audits.
      A resource with a Day 03 IAM overshare AND a Day 08 no-backup
      finding is a resource with cross-cutting risk — remediating one
      leaves the other, and shipping either fix without the other is
      shipping a partial improvement. Ordinary within-day findings are
      not what this check is for; the whole prior-day audit surface
      already covers those.

      CAP-016 IS ONE OF TWO CRITICALS BECAUSE IT IS THE ONLY CHECK WHOSE
      FAILURE MEANS "THE WHOLE PROGRAMME HAS STOPPED WORKING". Every
      other Day 10 finding is a specific infrastructure defect. CAP-016
      is the meta-check: the machine is running, the alerts are firing,
      nobody is reading them. A stack where every other check is green
      and CAP-016 is red is an organisation that has built cost
      governance and then stopped using it, which is one of the largest
      failure modes in the industry.

      THIS IS THE SAME STRUCTURAL POINT DAY 09 MADE with COST-016, on the
      next layer up. Day 09 caught "nobody reads AWS's cost anomalies".
      Day 10 catches "nobody reads YOUR audit's reports". The same
      failure mode, two layers of the stack, two consecutive days making
      it undeniable.
    # CONTRACT-END
    =============================================================================

  CONTRACT
}
