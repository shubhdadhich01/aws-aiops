###############################################################################
# Day 06 — outputs.tf
#
# Repo convention: every day surfaces its cost in the outputs, not just in the
# docs. If you have to open a pricing page to find out what you just built,
# the outputs are badly written.
#
# Day 06 is the first day where that convention strains, and the strain is the
# lesson. Days 01-05 billed for things that EXIST — an instance, a NAT
# gateway, a KMS key — so Terraform could count them and multiply. Day 06
# bills for things that HAPPEN: gigabytes ingested, custom metrics created,
# tokens sent to a model. Terraform knows how many alarms it made. It has no
# idea how chatty your application is.
#
# So the number below is the FLOOR. Read `silent_cost_growth` for the ceiling.
###############################################################################

###############################################################################
# Core resource references
###############################################################################

output "workload_log_group" {
  description = "The log group everything on this day revolves around: metric filters read it, alarms are built on those metrics, and the analyser queries it."
  value       = aws_cloudwatch_log_group.workload.name
}

output "workload_log_group_arn" {
  description = "ARN of the workload log group."
  value       = aws_cloudwatch_log_group.workload.arn
}

output "chaos_function_name" {
  description = "The workload that fails on demand. Invoke it with `aws lambda invoke` to manufacture an incident."
  value       = aws_lambda_function.chaos.function_name
}

output "chaos_log_group" {
  description = "The chaos function's own log group — created by Terraform WITH retention, so Lambda does not create one without. Its output is deliberately kept out of the workload group."
  value       = aws_cloudwatch_log_group.chaos.name
}

output "sns_topic_arn" {
  description = "SNS topic that receives alarm notifications. Exactly one alarm — the composite — publishes to it."
  value       = aws_sns_topic.alerts.arn
}

output "composite_alarm_name" {
  description = "The only alarm in this stack with a notification action. The three metric alarms feed it and stay silent by design."
  value       = aws_cloudwatch_composite_alarm.service_degraded.alarm_name
}

output "metric_alarm_names" {
  description = "The three diagnostic alarms behind the composite. They have no actions on purpose; obs_audit.py OBS-004 exempts alarms referenced by a composite rule."
  value = [
    aws_cloudwatch_metric_alarm.error_rate.alarm_name,
    aws_cloudwatch_metric_alarm.latency_p95.alarm_name,
    aws_cloudwatch_metric_alarm.no_telemetry.alarm_name,
  ]
}

output "metric_namespace" {
  description = "CloudWatch namespace for every custom metric this stack publishes. One namespace per system — a namespace called 'Custom' holding metrics from nine systems is a bill nobody can attribute."
  value       = local.metric_namespace
}

output "dashboard_name" {
  description = "Operational dashboard, or null when create_dashboard = false."
  value       = var.create_dashboard ? aws_cloudwatch_dashboard.main[0].dashboard_name : null
}

output "dashboard_url" {
  description = "Direct console link to the dashboard. Paste it into the incident channel; a dashboard nobody can find is a dashboard nobody opens."
  value       = var.create_dashboard ? "https://${local.region}.console.aws.amazon.com/cloudwatch/home?region=${local.region}#dashboards:name=${aws_cloudwatch_dashboard.main[0].dashboard_name}" : null
}

output "unretained_log_group" {
  description = "The deliberately broken log group with no retention (OBS-001), or null when create_insecure_examples = false. This one survives `terraform destroy` if anything wrote to it — the teardown checklist deletes it by name."
  value       = var.create_insecure_examples ? aws_cloudwatch_log_group.unretained[0].name : null
}

output "analyser_function_name" {
  description = "The incident analyser. Redaction on, token budget set, balanced sampling, citations verified in code."
  value       = aws_lambda_function.analyser.function_name
}

output "naive_analyser_function_name" {
  description = "The SAME zip file, deployed badly: no redaction (OBS-011), no token budget (OBS-012), tail-only sampling, bedrock:InvokeModel on Resource \"*\" (OBS-014), no retention on its log group (OBS-015). Null when create_insecure_examples = false."
  value       = var.create_insecure_examples ? aws_lambda_function.naive_analyser[0].function_name : null
}

output "summaries_topic_arn" {
  description = "SNS topic for incident summaries. Deliberately separate from the alarm topic: different data sensitivity, different mute policy, different reliability bar. See main.tf section 6."
  value       = aws_sns_topic.summaries.arn
}

output "bedrock_model_arn" {
  description = "The exact model ARN the analyser is permitted to invoke. Note the EMPTY account field — foundation models are not owned by your account, and writing your account ID there is the most common Bedrock IAM mistake."
  value       = local.bedrock_model_arn
}

output "analysis_rule_name" {
  description = "EventBridge rule from composite-alarm ALARM transition to the analyser. Filtered at the broker so OK transitions never cost an invocation."
  value       = aws_cloudwatch_event_rule.alarm_to_analyser.name
}

output "analysis_rule_state" {
  description = "ENABLED or DISABLED. A disabled rule everyone believes is enabled is its own outage — if you turn it off, say so somewhere a human reads."
  value       = aws_cloudwatch_event_rule.alarm_to_analyser.state
}

output "analyser_dlq_url" {
  description = "Dead letter queue for failed analyser invocations. An async invocation that fails twice and vanishes is indistinguishable from an incident nobody noticed."
  value       = aws_sqs_queue.analyser_dlq.url
}

output "idempotency_table_name" {
  description = "DynamoDB table holding one TTL'd row per summarised alarm. Bounds a flapping alarm to one paid model invocation per window instead of one per flap."
  value       = aws_dynamodb_table.idempotency.name
}

output "bedrock_invocation_log_group" {
  description = "Where every prompt and completion is recorded, or null when enable_bedrock_invocation_logging = false (the default, and check OBS-016). Enabling it fixes an audit gap by opening a data-access gap — set retention and a resource policy on it, or do not enable it."
  value       = var.enable_bedrock_invocation_logging ? aws_cloudwatch_log_group.bedrock_invocations[0].name : null
}

output "subscription_status_warning" {
  description = "The same trap as Day 04, with sharper teeth on this day."
  value       = <<-WARN
    An SNS email subscription is NOT active until you click the confirmation
    link AWS just emailed to ${var.notification_email}.

    Until you do, every publish SUCCEEDS and every message is DISCARDED. There
    is no error, no metric, and no indication anywhere in Terraform.

    On Day 04 that meant missing a compliance report. Here it means the
    composite alarm transitions to ALARM, the console turns red, the alarm
    history records the action as delivered, and nobody is told. There is no
    check in this repo — or in CloudWatch — that can find that for you.

    Check it:
      aws sns list-subscriptions-by-topic --topic-arn ${aws_sns_topic.alerts.arn} \
        --profile ${var.aws_profile} --region ${var.aws_region} \
        --query 'Subscriptions[].{Endpoint:Endpoint,Arn:SubscriptionArn}' --output table

    SubscriptionArn = "PendingConfirmation" means go and click the link.
  WARN
}

###############################################################################
# Cost
#
# CloudWatch has a PERMANENT free tier, not the 12-month kind:
#
#   10 custom metrics          (this stack publishes 7)
#   10 standard alarms         (this stack creates 3, or 4 with broken examples)
#    3 dashboards              (this stack creates 1, or 2 with broken examples)
#    5 GB log ingestion/month  (the lab uses well under 1 MB)
#    5 GB log storage/month
#    1,000,000 API requests
#
# Composite alarms are NOT in that free tier. They are $0.50/alarm-month each,
# and they are the entire floor of this stack's bill.
#
# The estimate below assumes a FRESH account. If you have been doing Days
# 01-05 in this account you have already spent some of that free tier, so
# every "free" line here may be a real charge on your bill. That gap between
# "what this stack costs" and "what this stack adds to your bill" is why
# per-account budgets exist, and it is Day 09's subject.
###############################################################################

locals {
  price_custom_metric_month   = 0.30
  price_standard_alarm_month  = 0.10
  price_composite_alarm_month = 0.50
  price_dashboard_month       = 3.00

  free_custom_metrics = 10
  free_alarms         = 10
  free_dashboards     = 3

  # 3 plain metrics (RequestCount, ErrorCount, LatencyMillis) plus one metric
  # per ErrorType dimension value. chaos_workload.py bounds ERROR_TYPES to
  # four on purpose — see main.tf section 4c.
  count_custom_metrics = 3 + 4

  count_standard_alarms  = 3 + (var.create_insecure_examples ? 1 : 0)
  count_composite_alarms = 1 + (var.create_insecure_examples ? 1 : 0)
  count_dashboards       = (var.create_dashboard ? 1 : 0) + (var.create_insecure_examples ? 1 : 0)

  billable_custom_metrics  = max(0, local.count_custom_metrics - local.free_custom_metrics)
  billable_standard_alarms = max(0, local.count_standard_alarms - local.free_alarms)
  billable_dashboards      = max(0, local.count_dashboards - local.free_dashboards)

  # A deliberately generous allowance for the log data the lab generates. The
  # default burst is ~110 KB; you would have to invoke the chaos function about
  # 45,000 times to leave the free 5 GB.
  cost_logs_estimate = 0.01

  monthly_total = (
    local.billable_custom_metrics * local.price_custom_metric_month
    + local.billable_standard_alarms * local.price_standard_alarm_month
    + local.count_composite_alarms * local.price_composite_alarm_month
    + local.billable_dashboards * local.price_dashboard_month
    + local.cost_logs_estimate
  )

  hourly_total = local.monthly_total / 730
}

output "estimated_hourly_cost_usd" {
  description = "Approximate on-demand cost per hour while this stack is running (us-east-1, fresh-account free tier assumed, excluding data transfer and any model invocations)."
  value       = format("$%.5f/hour", local.hourly_total)
}

output "estimated_monthly_cost_usd" {
  description = <<-DESC
    Approximate cost if you leave this running for a 730-hour month, assuming
    a fresh account's free tier.

    This is the FLOOR, not the estimate. It counts things that exist. It
    cannot count gigabytes you have not ingested yet or tokens you have not
    sent yet, and on this day those are the numbers that move.

    Read silent_cost_growth. It is not boilerplate on Day 06.
  DESC
  value       = format("$%.2f/month", local.monthly_total)
}

output "cost_breakdown" {
  description = "Line-by-line monthly estimate, with the free-tier arithmetic shown so you can redo it for an account that has already spent its allowance."
  value = {
    composite_alarms = format("$%.2f  (%d x $0.50 — composite alarms are NOT in the free ten)", local.count_composite_alarms * local.price_composite_alarm_month, local.count_composite_alarms)
    standard_alarms  = format("$%.2f  (%d created, %d free)", local.billable_standard_alarms * local.price_standard_alarm_month, local.count_standard_alarms, local.free_alarms)
    custom_metrics   = format("$%.2f  (%d created, %d free; $0.30 each after, and they CANNOT be deleted)", local.billable_custom_metrics * local.price_custom_metric_month, local.count_custom_metrics, local.free_custom_metrics)
    dashboards       = format("$%.2f  (%d created, %d free; $3.00 each after)", local.billable_dashboards * local.price_dashboard_month, local.count_dashboards, local.free_dashboards)
    log_ingestion    = format("$%.2f  (lab volume is <1 MB; free tier is 5 GB/month, then $0.50/GB)", local.cost_logs_estimate)
    log_storage      = "$0.00  (free tier 5 GB/month, then $0.03/GB-month — FOREVER, for any group without retention)"
    bedrock          = "$0.00 AT REST — and this is the line that breaks the convention. Bedrock is priced per 1,000 input and output tokens. There is no resource, so nothing appears in a teardown sweep and nothing shows up here. See silent_cost_growth for the worked example."
    dynamodb         = "$0.00  (on-demand, a handful of items, inside the permanent 25 GB free tier; TTL deletions are not billed)"
    sqs              = "$0.00  (permanent free tier: 1M requests/month)"
    eventbridge      = "$0.00  (AWS service events are free)"
    metric_filters   = "$0.00  (the filters themselves are free at any volume; the metrics they publish are not)"
    lambda           = "$0.00  (permanent free tier: 1M requests + 400k GB-seconds/month)"
    sns              = "$0.00  (permanent free tier: 1,000 email notifications/month)"
    TOTAL            = format("$%.2f", local.monthly_total)
  }
}

output "silent_cost_growth" {
  description = "The three ways this stack's bill stops being a rounding error. All three grow slowly enough that no anomaly detector will tell you."
  value       = <<-GROWTH

    Day 06's floor is ${format("$%.2f", local.monthly_total)}/month. Three things move it, and none of
    them appear in `terraform plan`.

    1. LOG GROUPS WITH NO RETENTION
       ${var.create_insecure_examples ? "You are creating one RIGHT NOW: ${local.unretained_log_group_name}" : "Not applicable — insecure examples are disabled."}

       "Never expire" is the default for every log group AWS creates on your
       behalf. Ingestion is $0.50/GB once; storage is $0.03/GB-month forever.

       A service logging 1 GB/day with no retention: $15/month ingestion, and
       storage that reaches $110/month after a year and keeps climbing. It is
       invisible in the console unless you go looking, and Cost Explorer folds
       every log group in the account into one "CloudWatch" line.

       Find every offender in the account:
         aws logs describe-log-groups --profile ${var.aws_profile} --region ${var.aws_region} \
           --query 'logGroups[?!not_null(retentionInDays)].logGroupName' --output table

    2. CUSTOM METRICS THAT CANNOT BE DELETED
       This stack publishes ${local.count_custom_metrics}, deliberately.

       Every distinct combination of namespace, metric name and dimension
       VALUES is one custom metric at $0.30/month. There is no delete API. A
       custom metric ages out fifteen months after its last datapoint and not
       one day sooner — no console button, no support ticket.

       So the mistake is permanent. Put a request ID, a customer ID or a URL
       path in a metric filter dimension and you can create forty thousand
       metrics in an afternoon: $12,000/month, for fifteen months, for data
       you deleted the same day you noticed.

       chaos_workload.py bounds its error types to four for exactly this
       reason. Dimensions are for values you could write on a napkin.

    3. AN ANALYSER TRIGGERED BY AN ALARM THAT FLAPS
       Automatic analysis is currently ${var.enable_auto_analysis ? "ENABLED" : "DISABLED"}, with a token budget of ${var.analyser_max_input_tokens == 0 ? "NONE (OBS-012)" : format("%d tokens", var.analyser_max_input_tokens)}
       and an idempotency window of ${var.analyser_idempotency_minutes} minutes.

       Alarm goes to ALARM, EventBridge invokes the analyser, the analyser
       reads a window of logs and sends them to a model priced per token.
       Nothing here creates a resource, so nothing appears in a teardown
       sweep and nothing is listed in cost_breakdown above. Day 06 is the
       first day where the expensive thing leaves nothing behind to delete.

       WORKED EXAMPLE — one careless "analyse the last 24 hours".

         A chatty service logs 1 GB/day. At ~275 bytes/line that is about
         3.9 million lines, roughly 268 million tokens. No context window
         holds that, so a naive implementation sends whatever fits — call it
         200,000 tokens.

           Logs Insights scan, 1 GB          $0.005
           Model input, 200k tokens
             at ~$0.0008/1K (Haiku 3.5)      $0.160
             at ~$0.003/1K  (Sonnet 3.5)     $0.600

         The QUERY is half a cent. The MODEL is thirty to a hundred and
         twenty times the query. That ratio is the whole reason "just send it
         all" is not a strategy.

         Now put it behind an alarm with datapoints_to_alarm = 1 on a noisy
         metric. Forty transitions an hour, twelve hours overnight:

           480 invocations x $0.16  =  $76.80   (Haiku)
           480 invocations x $0.60  = $288.00   (Sonnet)

         Nothing was broken. No dashboard turned red. The alarm did exactly
         what it was asked to do.

       Guards, in order of how much they help: M-of-N on the triggering alarm
       (it stops the transition happening at all), a hard token budget, and
       an idempotency window so one incident is summarised once rather than
       once per flap. This stack has all three.

  GROWTH
}

###############################################################################
# next_steps — the output people actually read
###############################################################################

output "next_steps" {
  description = "Copy-paste command sequence for the deterministic half of the lab. The AI half is added at CP2."
  value       = <<-STEPS

    ============================================================================
      Day 06 — Monitoring & AI-Powered Incident Analysis
      Stack is up. Estimated floor: ${format("$%.5f/hour", local.hourly_total)} / ${format("$%.2f/month", local.monthly_total)}
    ============================================================================

    0. CONFIRM THE SNS SUBSCRIPTION. NOTHING NOTIFIES UNTIL YOU DO.

         aws sns list-subscriptions-by-topic \
           --topic-arn ${aws_sns_topic.alerts.arn} \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           --query 'Subscriptions[].SubscriptionArn' --output text

       "PendingConfirmation" means go and click the link in your inbox.

    1. ESTABLISH A BASELINE. Healthy traffic, so the graphs have a "before".

         aws lambda invoke \
           --function-name ${aws_lambda_function.chaos.function_name} \
           --payload '{"mode":"normal","lines":300,"window_minutes":20}' \
           --cli-binary-format raw-in-base64-out \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           /tmp/baseline.json && cat /tmp/baseline.json | python3 -m json.tool

    2. BREAK IT ON PURPOSE.

         aws lambda invoke \
           --function-name ${aws_lambda_function.chaos.function_name} \
           --payload '{"mode":"cascade"}' \
           --cli-binary-format raw-in-base64-out \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           /tmp/incident.json && cat /tmp/incident.json | python3 -m json.tool

       Metric filter datapoints carry the LOG EVENT timestamp, so the graph
       fills in immediately. Alarms evaluate on wall-clock periods, so give
       them a few minutes to transition.

    3. READ THE LOGS YOURSELF, BEFORE ANYTHING SUMMARISES THEM.

       This ordering is the entire point of the day. Do not skip it.

         aws logs start-query \
           --log-group-name ${aws_cloudwatch_log_group.workload.name} \
           --start-time $(( $(date +%s) - 3600 )) \
           --end-time $(date +%s) \
           --query-string 'fields @timestamp, level, error_type, latency_ms, message | sort @timestamp asc | limit 200' \
           --profile ${var.aws_profile} --region ${var.aws_region}

       Then `aws logs get-query-results --query-id <id>`.

       Write down, in one sentence, what you think happened and why. Keep it.
       You will compare it to the model's answer in the CP2 half of this lab,
       and the comparison only works if you commit first.

       Hint if you are stuck: sort ASCENDING, and read the first ten lines.
       The cause is in there exactly once and it is not an ERROR.

    4. WATCH THE ALARMS AND THE DASHBOARD.

         aws cloudwatch describe-alarms \
           --alarm-name-prefix ${local.prefix} \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           --query 'MetricAlarms[].{Name:AlarmName,State:StateValue,Missing:TreatMissingData}' \
           --output table

         aws cloudwatch describe-alarms --alarm-types CompositeAlarm \
           --alarm-name-prefix ${local.prefix} \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           --query 'CompositeAlarms[].{Name:AlarmName,State:StateValue,Rule:AlarmRule}' \
           --output table

       Dashboard: ${var.create_dashboard ? "see the dashboard_url output" : "disabled (create_dashboard = false)"}

    5. PROVE THE COMPOSITE ALARM ACTUALLY FIRES.

       A composite alarm that can never fire looks identical to one that
       works. The only proof is forcing it:

         aws cloudwatch set-alarm-state \
           --alarm-name ${aws_cloudwatch_metric_alarm.error_rate.alarm_name} \
           --state-value ALARM --state-reason "deliberate test" \
           --profile ${var.aws_profile} --region ${var.aws_region}

       Within seconds the composite should transition and you should get mail.
       Then try the same trick on the deliberately impossible one:

         ${var.create_insecure_examples ? "aws cloudwatch describe-alarms --alarm-types CompositeAlarm --alarm-names ${local.prefix}-impossible-${local.suffix} --profile ${var.aws_profile} --region ${var.aws_region} --query 'CompositeAlarms[].AlarmRule'" : "(insecure examples disabled — nothing to compare against)"}

       Read that rule and work out why nothing you do will ever move it.

    6. SEE WHAT SILENCE LOOKS LIKE.

       Wait ten minutes without invoking the chaos function. The no-telemetry
       alarm goes to ALARM because treat_missing_data = "breaching". Then
       compare with the error-rate alarm, which sits happily at OK the whole
       time, because zero errors out of zero requests is not a breach.

       That contrast is the reason both alarms exist.

    6. NOW LET THE MODEL READ IT — and only now.

       Run the GOOD analyser against the incident you already understand:

         aws lambda invoke \
           --function-name ${aws_lambda_function.analyser.function_name} \
           --payload '{"alarmName":"manual-run","lookback_minutes":30}' \
           --cli-binary-format raw-in-base64-out \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           /tmp/summary.json

         python3 -c "import json;d=json.load(open('/tmp/summary.json'));print(d['analysis']['summary']);print();print('root cause:',d['analysis']['root_cause']);print('grounding:',d['analysis']['grounding'])"

       Compare it against the sentence you wrote in Step 3. Then check the
       claims: every one carries a line index and a verbatim quote, and the
       Lambda has already verified each quote against the line it cites.
       Anything marked UNVERIFIED is the model inventing evidence.

       ${var.create_insecure_examples ? "Then run the NAIVE one on the SAME incident:" : "(The naive analyser is disabled — set create_insecure_examples = true to see the comparison, which is the most valuable five minutes of the day.)"}

         ${var.create_insecure_examples ? "aws lambda invoke --function-name ${aws_lambda_function.naive_analyser[0].function_name} --payload '{\"alarmName\":\"naive-run\"}' --cli-binary-format raw-in-base64-out --profile ${var.aws_profile} --region ${var.aws_region} /tmp/naive.json" : ""}

       Identical zip file. Identical model. Four different environment
       variables. Read both summaries side by side and work out which one you
       would have acted on at 03:00.

    7. RUN THE AUDITOR.

         cd ../python
         pip install -r requirements.txt
         python3 obs_audit.py --profile ${var.aws_profile} --region ${var.aws_region}

    =============================================================================
    DAY 06 FINDING CONTRACT — LOCKED AT CP2
    =============================================================================
    This block is reproduced identically in five places. Change one, change all
    five: README.md, lab/README.md, lab/terraform/outputs.tf (next_steps),
    lab/python/obs_audit.py (module docstring), lab/python/tests/test_checks.py.

    Weights are the repo-wide ones, identical to Days 03, 04 and 05:
    CRITICAL 25, HIGH 10, MEDIUM 4, LOW 1, INFO 0. Score is 100 minus the sum,
    floored at 0. Grades: 90+ A, 75+ B, 60+ C, 40+ D, below that F.

    STATIC STATE — after terraform apply with the shipped defaults
    (create_insecure_examples = true, enable_bedrock_invocation_logging = false),
    before anything has been invoked.

      ID       SEVERITY   W   N  PTS  SOURCE RESOURCE
      -------  --------  --  --  ---  ------------------------------------------
      OBS-001  HIGH      10   1   10  aws_cloudwatch_log_group.unretained
      OBS-002  MEDIUM     4   2    8  aws_cloudwatch_log_group.unretained
                                      aws_cloudwatch_log_group.write_only
      OBS-003  CRITICAL  25   1   25  aws_cloudwatch_log_metric_filter.high_cardinality
      OBS-004  HIGH      10   1   10  aws_cloudwatch_metric_alarm.orphan
      OBS-005  MEDIUM     4   1    4  aws_cloudwatch_metric_alarm.orphan
      OBS-006  MEDIUM     4   1    4  aws_cloudwatch_metric_alarm.orphan
      OBS-007  HIGH      10   1   10  aws_cloudwatch_composite_alarm.impossible
      OBS-008  MEDIUM     4   1    4  aws_cloudwatch_dashboard.broken
      OBS-009  HIGH      10   0    0  none — SILENT BY SITUATION, see below
      OBS-010  LOW        1   1    1  aws_cloudwatch_metric_alarm.orphan
      OBS-011  CRITICAL  25   1   25  aws_lambda_function.naive_analyser
      OBS-012  HIGH      10   1   10  aws_lambda_function.naive_analyser
      OBS-013  HIGH      10   0    0  none — SILENT BY DESIGN, see below
      OBS-014  CRITICAL  25   1   25  aws_iam_role_policy.naive_analyser
      OBS-015  MEDIUM     4   1    4  aws_cloudwatch_log_group.naive_analyser
      OBS-016  MEDIUM     4   1    4  account-level Bedrock invocation logging
      -------  --------  --  --  ---  ------------------------------------------
      TOTALS                    15  144

      FIFTEEN findings from SIXTEEN checks. Check count and finding count are not
      the same number and never will be: OBS-002 fires twice, and OBS-009 and
      OBS-013 do not fire at all. If you are reconciling this table against a real
      run, reconcile the N column, not the number of rows.

      Score: 100 - 144 = -44, floored to 0/100. Grade F.

    THE THREE STATES

      STATE                                        FINDINGS  POINTS    SCORE  GRADE
      -------------------------------------------  --------  ------  -------  -----
      Static: after apply, before anything runs          15     144    0/100      F
      Live: after lab steps 1-6 — incident
        generated, alarms transitioned, composite
        proven, both analysers run                       15     144    0/100      F
      After lab step 8 — bedrock_region pointed at
        another region, and the no-telemetry
        alarm's treat_missing_data changed to
        notBreaching outside Terraform                   18     174    0/100      F
      -------------------------------------------  --------  ------  -------  -----
      Reference build: create_insecure_examples =
        false AND enable_bedrock_invocation_logging
        = true                                            0       0  100/100      A

      STATIC AND LIVE ARE IDENTICAL, AND THAT IS THE POINT. obs_audit.py audits
      CONFIGURATION, not runtime. Generating a real incident, watching three
      alarms transition, paging yourself and running both analysers changes
      nothing in its output. A
      configuration auditor and a monitoring system answer different questions,
      and treating either one as the other is the category error this day exists
      to prevent.

      Setting create_insecure_examples = false on its own leaves exactly one
      finding — OBS-016 — for 4 points and 96/100, grade A. Both toggles are
      needed for 100/100, and turning invocation logging on obliges you to set
      retention and a resource policy on its destination log group. That is
      stated in the variable description and it is not optional.

      Step 8 adds THREE findings, not two: OBS-009 once, and OBS-013 twice.
      bedrock_region is a single variable feeding BOTH analysers, so pointing it
      at another region moves the good one's log data as well as the naive one's.
      That is worth noticing — the misconfiguration is in a shared setting, and a
      shared setting does not care which of your functions was carefully written.

    SILENT BY DESIGN — OBS-013, log data crossing a region boundary to reach the
    model. bedrock_region defaults to the empty string, which resolves to
    aws_region, and the model ARN in the analyser's IAM policy is built from that
    same resolved value. No combination of shipped defaults can put the logs and
    the model in different regions. The check fires only if you edit a variable on
    purpose, which lab step 8 asks you to do. A check that stays silent because
    the stack cannot produce the misconfiguration is evidence that the auditor
    does not cry wolf.

    SILENT BY SITUATION — OBS-009, no liveness alarm anywhere in the region. This
    is silent only because aws_cloudwatch_metric_alarm.no_telemetry happens to
    exist with treat_missing_data set to breaching. Nothing structural prevents it
    firing. One attribute on one alarm, changed in the console in thirty seconds,
    and it fires — which is exactly what lab step 8 does.

    THE DIFFERENCE MATTERS. Silent by design tells you something about the
    auditor. Silent by situation tells you nothing about the auditor and
    everything about today's configuration. Never read the second as the first: a
    check that is silent by situation must be re-run, never assumed.

    CHECK INTERACTIONS THAT ARE DELIBERATE, NOT BUGS

      OBS-001 skips the log group of any function that holds bedrock:InvokeModel;
      OBS-015 owns those. An unretained analyser log group is ONE finding, not
      two.

      OBS-002 skips log groups under /aws/lambda/. A function's own execution log
      is a diagnostic artefact, not a data feed, and having no metric filter on it
      is correct rather than negligent.

      OBS-004 exempts alarms referenced by a composite alarm's rule. The three
      metric alarms in main.tf section 5 have no actions and are correct, because
      the composite in section 7 notifies on their behalf.

      OBS-006 exempts liveness alarms — treat_missing_data set to breaching with
      a LessThan comparison. A dead-man's switch is legitimately a raw count, and
      flagging it would be the auditor crying wolf about the best alarm in the
      stack.

      OBS-004's composite exemption only counts a composite that notifies AND
      whose rule can actually fire. An alarm watched solely by an unsatisfiable
      composite is exactly as silent as an orphan — and worse, because a reviewer
      scanning for orphans sees the reference and moves on. So OBS-007 firing on a
      composite also makes OBS-004 fire on its children. In this stack that is
      precisely what happens: the orphan alarm IS referenced, by the deliberately
      impossible composite, and is still reported as notifying nobody. Cause and
      consequence, not duplicates — fixing the rule clears both.
    =============================================================================
    This block is reproduced identically in five places. Change one, change all
    five: README.md, lab/README.md, lab/terraform/outputs.tf (next_steps),
    lab/python/obs_audit.py (module docstring), lab/python/tests/test_checks.py.

    Weights are the repo-wide ones, identical to Days 03, 04 and 05:
    CRITICAL 25, HIGH 10, MEDIUM 4, LOW 1, INFO 0. Score is 100 minus the sum,
    floored at 0. Grades: 90+ A, 75+ B, 60+ C, 40+ D, below that F.

    STATIC STATE — after terraform apply with the shipped defaults
    (create_insecure_examples = true, enable_bedrock_invocation_logging = false),
    before anything has been invoked.

      ID       SEVERITY   W   N  PTS  SOURCE RESOURCE
      -------  --------  --  --  ---  ------------------------------------------
      OBS-001  HIGH      10   1   10  aws_cloudwatch_log_group.unretained
      OBS-002  MEDIUM     4   2    8  aws_cloudwatch_log_group.unretained
                                      aws_cloudwatch_log_group.write_only
      OBS-003  CRITICAL  25   1   25  aws_cloudwatch_log_metric_filter.high_cardinality
      OBS-004  HIGH      10   1   10  aws_cloudwatch_metric_alarm.orphan
      OBS-005  MEDIUM     4   1    4  aws_cloudwatch_metric_alarm.orphan
      OBS-006  MEDIUM     4   1    4  aws_cloudwatch_metric_alarm.orphan
      OBS-007  HIGH      10   1   10  aws_cloudwatch_composite_alarm.impossible
      OBS-008  MEDIUM     4   1    4  aws_cloudwatch_dashboard.broken
      OBS-009  HIGH      10   0    0  none — SILENT BY SITUATION, see below
      OBS-010  LOW        1   1    1  aws_cloudwatch_metric_alarm.orphan
      OBS-011  CRITICAL  25   1   25  aws_lambda_function.naive_analyser
      OBS-012  HIGH      10   1   10  aws_lambda_function.naive_analyser
      OBS-013  HIGH      10   0    0  none — SILENT BY DESIGN, see below
      OBS-014  CRITICAL  25   1   25  aws_iam_role_policy.naive_analyser
      OBS-015  MEDIUM     4   1    4  aws_cloudwatch_log_group.naive_analyser
      OBS-016  MEDIUM     4   1    4  account-level Bedrock invocation logging
      -------  --------  --  --  ---  ------------------------------------------
      TOTALS                    16  144

      Sixteen findings from sixteen checks is a COINCIDENCE, not a mapping.
      OBS-002 fires twice; OBS-009 and OBS-013 do not fire at all.

      Score: 100 - 144 = -44, floored to 0/100. Grade F.

    THE THREE STATES

      STATE                                        FINDINGS  POINTS    SCORE  GRADE
      -------------------------------------------  --------  ------  -------  -----
      Static: after apply, before anything runs          16     144    0/100      F
      Live: after lab steps 1-6 — incident
        generated, alarms transitioned, composite
        proven, both analysers run                       16     144    0/100      F
      After lab step 8 — bedrock_region pointed at
        another region, and the no-telemetry
        alarm's treat_missing_data changed to
        notBreaching outside Terraform                   18     164    0/100      F
      -------------------------------------------  --------  ------  -------  -----
      Reference build: create_insecure_examples =
        false AND enable_bedrock_invocation_logging
        = true                                            0       0  100/100      A

      STATIC AND LIVE ARE IDENTICAL, AND THAT IS THE POINT. obs_audit.py audits
      CONFIGURATION, not runtime. Generating a real incident, watching three
      alarms transition, paging yourself and running both analysers changes
      nothing in its output. A
      configuration auditor and a monitoring system answer different questions,
      and treating either one as the other is the category error this day exists
      to prevent.

      Setting create_insecure_examples = false on its own leaves exactly one
      finding — OBS-016 — for 4 points and 96/100, grade A. Both toggles are
      needed for 100/100, and turning invocation logging on obliges you to set
      retention and a resource policy on its destination log group. That is
      stated in the variable description and it is not optional.

    SILENT BY DESIGN — OBS-013, log data crossing a region boundary to reach the
    model. bedrock_region defaults to the empty string, which resolves to
    aws_region, and the model ARN in the analyser's IAM policy is built from that
    same resolved value. No combination of shipped defaults can put the logs and
    the model in different regions. The check fires only if you edit a variable on
    purpose, which lab step 8 asks you to do. A check that stays silent because
    the stack cannot produce the misconfiguration is evidence that the auditor
    does not cry wolf.

    SILENT BY SITUATION — OBS-009, no liveness alarm anywhere in the region. This
    is silent only because aws_cloudwatch_metric_alarm.no_telemetry happens to
    exist with treat_missing_data set to breaching. Nothing structural prevents it
    firing. One attribute on one alarm, changed in the console in thirty seconds,
    and it fires — which is exactly what lab step 8 does.

    THE DIFFERENCE MATTERS. Silent by design tells you something about the
    auditor. Silent by situation tells you nothing about the auditor and
    everything about today's configuration. Never read the second as the first: a
    check that is silent by situation must be re-run, never assumed.

    CHECK INTERACTIONS THAT ARE DELIBERATE, NOT BUGS

      OBS-001 skips the log group of any function that holds bedrock:InvokeModel;
      OBS-015 owns those. An unretained analyser log group is ONE finding, not
      two.

      OBS-002 skips log groups under /aws/lambda/. A function's own execution log
      is a diagnostic artefact, not a data feed, and having no metric filter on it
      is correct rather than negligent.

      OBS-004 exempts alarms referenced by a composite alarm's rule. The three
      metric alarms in main.tf section 5 have no actions and are correct, because
      the composite in section 7 notifies on their behalf.

      OBS-006 exempts liveness alarms — treat_missing_data set to breaching with
      a LessThan comparison. A dead-man's switch is legitimately a raw count, and
      flagging it would be the auditor crying wolf about the best alarm in the
      stack.
    =============================================================================

    8. BREAK IT ON PURPOSE, AND WATCH TWO SILENT CHECKS WAKE UP.

       (a) Send the logs to a model in another region — OBS-013:

             echo 'bedrock_region = "eu-west-1"' >> terraform.tfvars
             terraform apply -auto-approve

       (b) Take away the dead-man's switch, outside Terraform, the way it
           actually happens — OBS-009:

             aws cloudwatch put-metric-alarm \
               --alarm-name ${aws_cloudwatch_metric_alarm.no_telemetry.alarm_name} \
               --namespace ${local.metric_namespace} --metric-name RequestCount \
               --statistic Sum --period ${var.alarm_period_seconds} \
               --evaluation-periods 10 --datapoints-to-alarm 10 \
               --threshold 1 --comparison-operator LessThanThreshold \
               --treat-missing-data notBreaching \
               --profile ${var.aws_profile} --region ${var.aws_region}

       Re-run the auditor: 18 findings, 174 points, still 0/100.

       Note what just happened to (b). One attribute, changed in one command,
       with no code review and no Terraform diff — and the alarm that detects
       silence now treats silence as fine. `terraform plan` will show the
       drift; nothing else would have.

       Put it back: terraform apply -auto-approve

    9. THE REFERENCE BUILD — the other half of the lesson.

         create_insecure_examples          = false
         enable_bedrock_invocation_logging = true

       Re-run the auditor: 0 findings, 100/100, grade A. Watch WHICH checks
       went silent, and satisfy yourself that each one went silent for a
       reason rather than because the auditor stopped looking.

    10. DESTROY, AND THEN CHECK. `destroy` IS NOT ENOUGH ON THIS DAY.

         terraform destroy -auto-approve

       Three things survive it and one of them cannot be deleted at all:
         * log groups Terraform did not create
         * custom metrics — no delete API, 15 months to age out
         * whatever you spent on Bedrock, which left nothing behind to find

       Full verification: ../../teardown-checklist.md

    ============================================================================
  STEPS
}
