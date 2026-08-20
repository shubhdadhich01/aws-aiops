###############################################################################
# Day 07 — outputs.tf
#
# Repo convention: every day surfaces its cost in the outputs, not just in the
# docs.
#
# Day 06 broke the pattern by billing for things that HAPPEN rather than things
# that exist. Day 07 breaks it again, differently: the security services here
# bill for the VOLUME OF DATA THEY ANALYSE, which is a function of how busy
# your account is and has nothing to do with what this Terraform creates.
#
# So the number below is a floor with one large estimate in it, and the
# estimate is labelled. Do not quote it at anybody without reading
# `cost_breakdown`.
###############################################################################

###############################################################################
# Core resource references
###############################################################################

output "trail_name" {
  description = "The CloudTrail trail. Multi-region and validated when the defaults are left alone — which is the difference between logging and evidence."
  value       = aws_cloudtrail.main.name
}

output "trail_bucket" {
  description = "S3 bucket holding delivered CloudTrail objects. Versioned, encrypted, public-access-blocked, and TLS-only by bucket policy."
  value       = aws_s3_bucket.trail.id
}

output "trail_validation_command" {
  description = "The command that turns your trail from logs into evidence. Run it once now, so you know it works before you need it."
  value = join(" ", [
    "aws cloudtrail validate-logs",
    "--trail-arn ${local.trail_arn}",
    "--start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ)",
    "--profile ${var.aws_profile} --region ${var.aws_region}",
  ])
}

output "guardduty_detector_id" {
  description = "GuardDuty detector, or null when enable_guardduty = false. Needed for `create-sample-findings`."
  value       = var.enable_guardduty ? aws_guardduty_detector.main[0].id : null
}

output "security_hub_standards_enabled" {
  description = "Standards actually subscribed. One is the right answer on day one; the README explains why enabling all of them produces a number nobody drives to zero."
  value       = var.enable_security_hub ? [for s in aws_securityhub_standards_subscription.enabled : s.standards_arn] : []
}

output "sns_topic_arn" {
  description = "SNS topic for security findings. CP2 adds a second topic for containment actions, because 'we detected something' and 'we changed your account' have different audiences."
  value       = aws_sns_topic.security.arn
}

output "quarantine_security_group_id" {
  description = "The containment security group. No ingress, no egress — which also means no SSH and no Session Manager. Read main.tf section 9 before you use it in anger."
  value       = aws_security_group.quarantine.id
}

output "managed_secret_arn" {
  description = "The secret with rotation configured. Verify rotation actually RAN with describe-secret; RotationEnabled only means a schedule exists."
  value       = aws_secretsmanager_secret.app.arn
}

output "unrotated_secret_arn" {
  description = "The deliberately unrotated secret (SEC-010), or null when create_insecure_examples = false."
  value       = var.create_insecure_examples ? aws_secretsmanager_secret.legacy[0].arn : null
}

output "rotation_health_command" {
  description = "The one command that distinguishes rotation that is configured from rotation that works. LastRotatedDate is the only field that means anything."
  value = join(" ", [
    "aws secretsmanager describe-secret",
    "--secret-id ${aws_secretsmanager_secret.app.id}",
    "--profile ${var.aws_profile} --region ${var.aws_region}",
    "--query '{Enabled:RotationEnabled,Last:LastRotatedDate,Rules:RotationRules}'",
  ])
}

output "responder_function_name" {
  description = "The threat responder. Type allow-list, runtime kill switch, reversible containment only, rollback command recorded in every notification."
  value       = aws_lambda_function.responder.function_name
}

output "naive_responder_function_name" {
  description = "The SAME zip file, deployed badly: severity threshold instead of an allow-list (SEC-005), an intent to terminate (SEC-012), no kill switch (SEC-014), a wide-open role (SEC-008), a DISABLED rule with no DLQ (SEC-015/016). Null when create_insecure_examples = false."
  value       = var.create_insecure_examples ? aws_lambda_function.naive_responder[0].function_name : null
}

output "kill_switch_parameter" {
  description = "The runtime brake. Flip it with put-parameter — no plan, no apply, no pipeline. The responder re-reads it on every invocation."
  value       = aws_ssm_parameter.kill_switch.name
}

output "kill_switch_command" {
  description = "The command you want muscle memory for. Run it once, deliberately, before the night you need it."
  value = join(" ", [
    "aws ssm put-parameter --name ${aws_ssm_parameter.kill_switch.name}",
    "--value DISARMED --type String --overwrite",
    "--profile ${var.aws_profile} --region ${var.aws_region}",
  ])
}

output "containment_topic_arn" {
  description = "SNS topic for actions this account took to itself. Deliberately separate from the findings topic: different urgency, different audience, different retention."
  value       = aws_sns_topic.containment.arn
}

output "containment_mode" {
  description = "What the responder actually does. Start in dry-run, run it for a week, read what it would have done — that week always changes the allow-list."
  value       = var.containment_mode
}

output "responder_allow_list" {
  description = "The reviewed finding TYPES the responder acts on. Not a severity threshold: severity is impact, not confidence, and a threshold matches your own penetration test."
  value       = var.respond_to_finding_types
}

output "response_rule_state" {
  description = "ENABLED or DISABLED. A rule everyone believes is enabled and is not is its own outage — check SEC-015."
  value       = aws_cloudwatch_event_rule.guardduty_findings.state
}

output "responder_dlq_url" {
  description = "Dead letter queue for findings that never reached the responder. A vanished detection is indistinguishable from one correctly ignored."
  value       = aws_sqs_queue.responder_dlq.url
}

output "subscription_status_warning" {
  description = "The same trap as Days 04 and 06, with the highest stakes yet."
  value       = <<-WARN
    An SNS email subscription is NOT active until you click the confirmation
    link AWS just emailed to ${var.notification_email}.

    Until you do, every publish SUCCEEDS and every message is DISCARDED.

    On Day 04 that meant a missed compliance report. On Day 06 it meant an
    alarm that paged nobody. Here it means an automated response can isolate a
    production instance at 03:00 and no human is told — the action succeeds,
    the notification is discarded, and the first anyone knows is a customer
    ticket.

    Confirm the subscription BEFORE you enable automatic response in CP2.

      aws sns list-subscriptions-by-topic --topic-arn ${aws_sns_topic.security.arn} \
        --profile ${var.aws_profile} --region ${var.aws_region} \
        --query 'Subscriptions[].{Endpoint:Endpoint,Arn:SubscriptionArn}' --output table
  WARN
}

###############################################################################
# Cost
#
# Three shapes of bill on this day, and only one of them is countable here:
#
#   PER RESOURCE     Secrets Manager, at $0.40 per secret per month. This is
#                    the only line Terraform can compute exactly.
#   PER CHECK        Security Hub, at ~$0.0010 per security check. Counted per
#                    control per resource per day, so the number is driven by
#                    how many resources your account has — not by anything in
#                    this file.
#   PER GB ANALYSED  GuardDuty. Driven by CloudTrail event volume, VPC flow log
#                    volume and DNS query volume. Invisible here, and hidden
#                    for the first 30 days by the free trial.
#
# The Security Hub figure below is a flat ESTIMATE for a small lab account. It
# is the one number in this repo that is a guess rather than arithmetic, and it
# is labelled as such wherever it appears.
###############################################################################

locals {
  price_secret_month = 0.40

  # A small lab account against one standard. A production account with a few
  # hundred resources is comfortably ten times this. Replace it with your own
  # Cost Explorer figure after the first month.
  estimate_security_hub_month = 2.00
  estimate_s3_month           = 0.05

  count_secrets = 1 + (var.create_insecure_examples ? 1 : 0)

  monthly_secrets      = local.count_secrets * local.price_secret_month
  monthly_security_hub = var.enable_security_hub ? local.estimate_security_hub_month : 0

  monthly_total = local.monthly_secrets + local.monthly_security_hub + local.estimate_s3_month
  hourly_total  = local.monthly_total / 730
}

output "estimated_hourly_cost_usd" {
  description = "Approximate on-demand cost per hour (us-east-1), excluding all usage-based analysis charges. See cost_breakdown for what is missing."
  value       = format("$%.5f/hour", local.hourly_total)
}

output "estimated_monthly_cost_usd" {
  description = <<-DESC
    Approximate cost for a 730-hour month on a QUIET account.

    This is a floor containing one estimate. It counts secrets exactly, guesses
    Security Hub, and cannot count GuardDuty at all, because GuardDuty bills
    for the volume of data it analyses and nothing in this Terraform determines
    that.

    On a busy account GuardDuty is frequently the largest line in the security
    budget, and it is invisible for 30 days because of the free trial. Set the
    budget alarm before day 31, not after.
  DESC
  value       = format("$%.2f/month", local.monthly_total)
}

output "cost_breakdown" {
  description = "Line-by-line, with the guesses labelled as guesses."
  value = {
    secrets_manager    = format("$%.2f  (%d secret(s) x $0.40/month, plus ~$0.05 per 10,000 API calls)", local.monthly_secrets, local.count_secrets)
    security_hub       = var.enable_security_hub ? format("$%.2f  ESTIMATE — ~$0.0010 per security check, counted per control per resource per DAY. Scales with your resource count, not with this stack.", local.monthly_security_hub) : "$0.00 (disabled)"
    guardduty          = var.enable_guardduty ? "USAGE-BASED, NOT ESTIMATED — ~$4.00 per million CloudTrail events, ~$1.00/GB of VPC flow and DNS logs. Free for 30 days per account per region, then not. This is the line that surprises people." : "$0.00 (disabled)"
    guardduty_s3       = var.enable_guardduty_s3_protection ? "USAGE-BASED — ~$0.80 per million S3 data events. A busy bucket produces ~26 million/day." : "$0.00 (S3 protection disabled — deliberately; see the variable description)"
    cloudtrail         = var.create_insecure_examples ? "USAGE-BASED — the first trail's management events are FREE; the deliberately broken second trail is ~$2.00 per 100,000 events." : "$0.00 (first trail with management events is free)"
    cloudtrail_data    = var.cloudtrail_enable_data_events ? "USAGE-BASED — ~$0.10 per 100,000 data events, NO free allowance. Check your object request volume before leaving this on." : "$0.00 (data events disabled — deliberately)"
    s3_storage         = format("$%.2f  (small objects, %d-day lifecycle expiry configured)", local.estimate_s3_month, var.trail_log_retention_days)
    lambda             = "$0.00  (permanent free tier: 1M requests + 400k GB-seconds/month)"
    sns                = "$0.00  (permanent free tier: 1,000 email notifications/month)"
    ec2_security_group = "$0.00  (security groups are free; the quarantine group costs nothing until it is attached)"
    TOTAL_COUNTABLE    = format("$%.2f  — floor only. The three USAGE-BASED lines above are not in it.", local.monthly_total)
  }
}

output "silent_cost_growth" {
  description = "The three ways this becomes an expensive month. None of them is visible in a plan."
  value       = <<-GROWTH

    Day 07's countable floor is ${format("$%.2f", local.monthly_total)}/month. Three things move it and
    none of them appears in `terraform plan`.

    1. GUARDDUTY AND SECURITY HUB LEFT ON IN REGIONS NOBODY USES
       Both are REGIONAL services with account-level state. Enabling them here
       does nothing for the other twenty-odd regions — which is check SEC-002,
       and the correct fix is to enable them everywhere.

       The trap is the other direction: somebody enables them everywhere, once,
       during a compliance push, and then the account keeps paying for
       detection in fifteen regions that have never held a resource. On a quiet
       account that is small. On an account with real CloudTrail volume
       replicated everywhere, it is not.

       Audit it deliberately:
         for r in $(aws ec2 describe-regions --query 'Regions[].RegionName' --output text); do
           echo -n "$r: "
           aws guardduty list-detectors --region $r --profile ${var.aws_profile} \
             --query 'DetectorIds[0]' --output text
         done

    2. CLOUDTRAIL DATA EVENTS ON A BUSY BUCKET
       Data events are currently ${var.cloudtrail_enable_data_events ? "ENABLED — scoped to one bucket, but check the volume" : "DISABLED"}.

       They are billed per event with no free allowance, and they are generated
       at application volume rather than human volume. A bucket serving a few
       hundred reads per second produces roughly 26 million events per day,
       which is roughly $26/day, which is roughly $780/month. For one bucket.

       There is exactly one right way to use them: an explicit selector naming
       the buckets whose contents you would have a notification obligation
       about. Never account-wide. Never `arn:aws:s3:::*/*`.

    3. A QUARANTINE GROUP LEFT ATTACHED AFTER A FALSE POSITIVE
       (CP2 wires the automation that attaches it. The trap is worth reading
       before you build it, not after.)

       This one does not cost dollars directly. It costs an instance that has
       been isolated since a Tuesday in March, serving nothing, billing hourly,
       while everyone assumes it is fine because nothing alerted.

       Containment must be reversible AND reversal must be verified. CP2's
       responder records what it detached so a human can put it back, and the
       teardown checklist looks for instances still wearing the group.

  GROWTH
}

###############################################################################
# next_steps
###############################################################################

output "next_steps" {
  description = "Copy-paste command sequence for the detection half of the lab. The response half is added at CP2."
  value       = <<-STEPS

    ============================================================================
      Day 07 — Enterprise Cloud Security
      Detection stack is up. Countable floor: ${format("$%.5f/hour", local.hourly_total)} / ${format("$%.2f/month", local.monthly_total)}
      (GuardDuty and Security Hub bill for what they analyse — not in that number.)
    ============================================================================

    0. CONFIRM THE SNS SUBSCRIPTION. DO THIS BEFORE CP2 ENABLES AUTOMATION.

         aws sns list-subscriptions-by-topic \
           --topic-arn ${aws_sns_topic.security.arn} \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           --query 'Subscriptions[].SubscriptionArn' --output text

       "PendingConfirmation" means go and click the link.

    1. PROVE THE TRAIL IS EVIDENCE, NOT JUST LOGS.

       Wait ~15 minutes for the first delivery, then:

         aws s3 ls s3://${aws_s3_bucket.trail.id}/AWSLogs/${local.account_id}/ --recursive \
           --profile ${var.aws_profile} | head

         ${local.trail_arn == "" ? "" : "aws cloudtrail validate-logs --trail-arn ${local.trail_arn} --start-time $(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%SZ) --profile ${var.aws_profile} --region ${var.aws_region}"}

       Log file validation is ${var.cloudtrail_enable_log_file_validation ? "ON" : "OFF — this trail is logging, not evidence"}.

       Then do the same for the deliberately broken shadow trail and read the
       error. That difference is the whole point of section 2.

    2. GENERATE FINDINGS ON DEMAND.

         ${var.enable_guardduty ? "aws guardduty create-sample-findings --detector-id ${aws_guardduty_detector.main[0].id} --finding-types UnauthorizedAccess:EC2/SSHBruteForce CryptoCurrencyMining:EC2/BitcoinTool.B!DNS --profile ${var.aws_profile} --region ${var.aws_region}" : "(GuardDuty is disabled — set enable_guardduty = true)"}

       Then list them, sorted by severity:

         ${var.enable_guardduty ? "aws guardduty list-findings --detector-id ${aws_guardduty_detector.main[0].id} --profile ${var.aws_profile} --region ${var.aws_region} --query 'FindingIds' --output text" : ""}

    3. READ THE SEVERITIES, AND DO NOT TRUST THEM AS CONFIDENCE.

       Pull one finding and look at what is actually in it:

         ${var.enable_guardduty ? "aws guardduty get-findings --detector-id ${aws_guardduty_detector.main[0].id} --finding-ids <id> --profile ${var.aws_profile} --region ${var.aws_region} --query 'Findings[0].{Type:Type,Severity:Severity,Title:Title,Resource:Resource.ResourceType}'" : ""}

       Note the "[SAMPLE]" prefix on the title and the fake instance id. Note
       also that severity says how BAD this would be if real, not how LIKELY it
       is to be real. Section 3 of main.tf is the argument; this is the
       evidence for it.

    4. WATCH IT REACH SECURITY HUB.

         aws securityhub get-findings \
           --filters '{"ProductName":[{"Value":"GuardDuty","Comparison":"EQUALS"}]}' \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           --query 'Findings[].{Sev:Severity.Label,Type:Types[0],Title:Title}' --output table

       The GuardDuty-to-Security-Hub integration is automatic when both are
       enabled in a region. There is no resource wiring them together, which is
       why people go looking for one.

    5. CHECK WHETHER ROTATION HAS ACTUALLY RUN.

         ${join(" ", ["aws secretsmanager describe-secret", "--secret-id ${aws_secretsmanager_secret.app.id}", "--profile ${var.aws_profile} --region ${var.aws_region}", "--query '{Enabled:RotationEnabled,Last:LastRotatedDate}'"])}

       `RotationEnabled: true` only means a schedule exists. `LastRotatedDate`
       is the field that means it ran. Force one and watch the four steps in
       the rotator's log group:

         aws secretsmanager rotate-secret --secret-id ${aws_secretsmanager_secret.app.id} \
           --profile ${var.aws_profile} --region ${var.aws_region}

         aws logs tail ${aws_cloudwatch_log_group.rotator.name} --follow \
           --profile ${var.aws_profile} --region ${var.aws_region}

       You should see createSecret, setSecret, testSecret, finishSecret. Read
       what setSecret logs. It is a no-op, loudly, and the docstring explains
       why that is the most dangerous thing a rotator can be.

    6. WATCH THE RESPONDER DECIDE — including when it decides to do nothing.

       Containment mode is currently ${var.containment_mode}. The rule is ${aws_cloudwatch_event_rule.guardduty_findings.state}.

         aws logs tail /aws/lambda/${aws_lambda_function.responder.function_name} --follow \
           --profile ${var.aws_profile} --region ${var.aws_region}

       Generate a finding on the allow-list, and one that is not:

         ${var.enable_guardduty ? "aws guardduty create-sample-findings --detector-id ${aws_guardduty_detector.main[0].id} --finding-types CryptoCurrencyMining:EC2/BitcoinTool.B!DNS UnauthorizedAccess:EC2/SSHBruteForce --profile ${var.aws_profile} --region ${var.aws_region}" : "(GuardDuty disabled)"}

       BOTH produce a log record and a notification. That is deliberate: the
       responder explains itself when it does nothing, because "why did
       nothing happen" is asked far more often than the opposite, and a
       responder that only speaks when it acts cannot answer it.

       Note that both are SAMPLE findings, so the good responder refuses on
       those grounds first. Read that log line and then read is_sample() in
       lambda/threat_responder.py — getting that test backwards produces a
       responder that works perfectly here and does nothing in production.

    7. FLIP THE KILL SWITCH, BECAUSE ONE YOU HAVE NEVER FLIPPED IS A GUESS.

         ${join(" ", ["aws ssm put-parameter --name ${aws_ssm_parameter.kill_switch.name}", "--value DISARMED --type String --overwrite", "--profile ${var.aws_profile} --region ${var.aws_region}"])}

       Generate another finding. The responder now logs that it was invoked,
       notifies, and changes nothing. No apply, no plan, no pipeline — which
       is the entire point, because at 03:00 you do not have those.

       Put it back:

         aws ssm put-parameter --name ${aws_ssm_parameter.kill_switch.name} \
           --value ARMED --type String --overwrite \
           --profile ${var.aws_profile} --region ${var.aws_region}

    8. RUN THE AUDITOR.

         cd ../python
         pip install -r requirements.txt
         python3 sec_audit.py --profile ${var.aws_profile} --region ${var.aws_region}

    =============================================================================
    DAY 07 FINDING CONTRACT — LOCKED AT CP2
    =============================================================================
    This block is reproduced identically in five places. Change one, change all
    five: README.md, lab/README.md, lab/terraform/outputs.tf (next_steps),
    lab/python/sec_audit.py (module docstring), lab/python/tests/test_checks.py.

    Weights are the repo-wide ones, identical to Days 03 through 06:
    CRITICAL 25, HIGH 10, MEDIUM 4, LOW 1, INFO 0. Score is 100 minus the sum,
    floored at 0. Grades: 90+ A, 75+ B, 60+ C, 40+ D, below that F.

    STATIC STATE — after terraform apply with the shipped defaults
    (create_insecure_examples = true), before anything has been invoked and
    before rotation has run.

      ID       SEVERITY   W   N  PTS  SOURCE RESOURCE
      -------  --------  --  --  ---  ------------------------------------------
      SEC-001  CRITICAL  25   0    0  none - GuardDuty is enabled
      SEC-002  HIGH      10   0    0  none - Security Hub is enabled with a standard
      SEC-003  MEDIUM     4   0    0  none - no findings exist yet. LIVE ONLY.
      SEC-004  LOW        1   0    0  none - SILENT BY DESIGN, see below
      SEC-005  CRITICAL  25   1   25  aws_lambda_function.naive_responder
      SEC-006  HIGH      10   1   10  aws_cloudtrail.shadow
      SEC-007  HIGH      10   1   10  aws_cloudtrail.shadow
      SEC-008  CRITICAL  25   1   25  aws_iam_role_policy.naive_responder
      SEC-009  HIGH      10   1   10  aws_s3_bucket.shadow
      SEC-010  MEDIUM     4   1    4  aws_secretsmanager_secret.legacy
      SEC-011  HIGH      10   1   10  aws_secretsmanager_secret.app
      SEC-012  CRITICAL  25   1   25  aws_lambda_function.naive_responder
      SEC-013  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
      SEC-014  HIGH      10   1   10  aws_lambda_function.naive_responder
      SEC-015  MEDIUM     4   1    4  aws_cloudwatch_event_rule.naive_responder
      SEC-016  MEDIUM     4   1    4  aws_cloudwatch_event_target.naive_responder
      -------  --------  --  --  ---  ------------------------------------------
      TOTALS                    11  137

      ELEVEN findings from SIXTEEN checks. Five are silent at this point and they
      are silent for four different reasons, which is the most useful thing in
      this table: two because the stack is built correctly (SEC-001, SEC-002), one
      because it reads runtime state that does not exist yet (SEC-003), one
      because the stack cannot produce the fault (SEC-004), and one because not
      enough time has passed (SEC-013).

      Score: 100 - 137 = -37, floored to 0/100. Grade F.

    THE THREE STATES

      STATE                                        FINDINGS  POINTS    SCORE  GRADE
      -------------------------------------------  --------  ------  -------  -----
      Static: after apply, before anything runs          11     137    0/100      F
      Live: after lab steps 1-5 — sample findings
        generated and left unresolved, and one
        rotation forced                                  11     131    0/100      F
      After lab step 8 — publishing frequency set
        to SIX_HOURS, and max_access_key_age_days
        lowered to 0                                     13     136    0/100      F
      -------------------------------------------  --------  ------  -------  -----
      Reference build: create_insecure_examples =
        false, after rotation has run at least once       0       0  100/100      A

      STATIC AND LIVE HAVE THE SAME COUNT AND A DIFFERENT SET, AND THAT IS THE
      POINT. Two checks move in opposite directions between them:

        SEC-011 FIRES at static and goes SILENT live. Rotation is configured but
                has never run, because rotate_immediately is false. Forcing one
                rotation in lab step 5 clears it.
        SEC-003 is SILENT at static and FIRES live. It reads the age of unresolved
                findings, and there are none until you generate them.

      Eleven findings before, eleven after, six points apart, and a different
      problem. NEVER DIFF ON THE COUNT. Two audit runs with the same total can
      describe completely different accounts, and a dashboard that trends the
      number without the set is worse than no dashboard.

      This is also the direct contrast with Day 06, where static and live were
      IDENTICAL because every check read configuration only. Day 07 has checks
      that read runtime state — findings, rotation history, key age — and the
      moment an auditor does that, "when you ran it" becomes part of the answer.

      Setting create_insecure_examples = false BEFORE rotation has run leaves
      exactly one finding — SEC-011 — for 10 points and 90/100, grade A. Both
      conditions are needed for 100/100.

    SILENT BY DESIGN — SEC-004, GuardDuty finding publishing frequency left at
    SIX_HOURS. The variable defaults to FIFTEEN_MINUTES and its validation accepts
    only the three documented values, so no shipped default and no typo can
    produce the fault. The check fires only if somebody edits the variable on
    purpose, which lab step 8a asks you to do. A check that stays silent because
    the stack cannot produce the fault is evidence that the auditor does not cry
    wolf.

    SILENT BY SITUATION — SEC-013, an active IAM access key older than
    max_access_key_age_days. The deliberately broken example creates exactly the
    credential this check exists to find, and the check does not fire, because the
    key is hours old.

      NOTHING HAS TO CHANGE FOR THAT TO STOP BEING TRUE. No edit, no deploy, no
      console click. In 91 days the same unchanged account fails the same
      unchanged check. The calendar is the situation.

      That makes SEC-013 the clearest argument in this repo for running an auditor
      on a SCHEDULE rather than at merge time. A merge-time-only audit certifies
      the account as it was on the day somebody last changed it, and a
      point-in-time pass is not a property that persists.

      Lab step 8b sets max_access_key_age_days to 0 to make the point in a second
      rather than in three months.

    THE DIFFERENCE MATTERS. Silent by design tells you something about the
    auditor. Silent by situation tells you nothing about the auditor and
    everything about today — and in SEC-013's case, only about today. Never read
    the second as the first.

    CHECK INTERACTIONS THAT ARE DELIBERATE, NOT BUGS

      SEC-005 and SEC-012 both fire on aws_lambda_function.naive_responder, and
      they are not duplicates. SEC-005 is about WHEN it acts (a severity threshold
      rather than a reviewed allow-list of finding types). SEC-012 is about WHAT
      it does when it acts (an intent to terminate rather than to isolate). Fixing
      one leaves the other, and they have different owners in most organisations.

      SEC-012 fires on CONFIGURED INTENT, not on observed behaviour. The shared
      responder code refuses CONTAINMENT_MODE=terminate and changes nothing, which
      is correct and does not make the configuration acceptable — the next person
      to "fix" the responder will implement what the configuration asks for.

      SEC-014 (no kill switch) is scoped to functions that can actually take an
      action. A read-only Lambda with no containment permissions does not need a
      brake, and flagging it would train people to ignore the check.

      SEC-016 reports on the TARGET, not the rule. One rule with three targets and
      no dead-letter queue is three findings, because each target is a separate
      path a detection can vanish down.

      SEC-011 requires rotation to be CONFIGURED before it can fire. A secret with
      no rotation at all is SEC-010, not SEC-011 — one finding, not two, and the
      remediations are different: SEC-010 is "decide whether this should rotate",
      SEC-011 is "it says it rotates and it does not".
    =============================================================================

    9. THE REFERENCE BUILD — the other half of the lesson.

         create_insecure_examples = false

       Re-run the auditor: 0 findings, 100/100, grade A, PROVIDED rotation has
       run. Skip step 5 and you get 1 finding, 10 points, 90/100 — and the one
       finding is SEC-011.

       Then look at WHICH checks went silent and satisfy yourself that each one
       went silent for a reason rather than because the auditor stopped
       looking.

    10. DESTROY, AND THEN CHECK. `destroy` IS NOT ENOUGH ON THIS DAY EITHER.

         terraform destroy -auto-approve

       Four things survive it:
         * GuardDuty and Security Hub keep billing until disabled in EVERY
           region, not just this one
         * secrets enter a ${var.secret_recovery_window_days}-day recovery window rather than deleting
         * trail objects outlive the trail
         * any instance still wearing the quarantine security group

       Full verification: ../../teardown-checklist.md

    ============================================================================
  STEPS
}
