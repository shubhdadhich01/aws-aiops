###############################################################################
# Day 06 — Monitoring & AI-Powered Incident Analysis
# main.tf — the observability stack
#
# This file teaches as it goes. Read it top to bottom before you apply; the
# comments are the lesson and the resources are the exercise.
#
# WHAT GETS BUILT (CP1 — the deterministic half)
#
#   chaos workload Lambda ──> workload log group ──┬──> metric filters ──> metrics
#                                                  │                          │
#                                                  │                          ├──> error-RATE alarm
#                                                  │                          ├──> p95 LATENCY alarm
#                                                  │                          └──> NO-TELEMETRY alarm
#                                                  │                                    │
#                                                  │                            composite alarm ──> SNS ──> email
#                                                  └──> (section 10 will read this window of logs)
#
#   Plus, when create_insecure_examples = true, the same architecture built
#   badly, for obs_audit.py to tear apart.
#
# THE ARGUMENT THIS DAY MAKES
#
#   A summary you cannot check is worse than no summary.
#
#   Everything in this file is the part of observability that does NOT involve
#   a model, and it is deliberately first. Metric filters, alarms and Logs
#   Insights answer "how many", "when", "how bad" and "is it still happening"
#   exactly, in milliseconds, for free. A language model answers none of those
#   questions better and several of them worse.
#
#   Section 10 adds the model, for the one question this section genuinely
#   cannot answer: "what happened". Read that section's comments as the case
#   FOR and AGAINST it, because both are there.
#
# COST: dominated by log ingestion at $0.50/GB, which is a function of how
# chatty you are and not of what exists. See outputs.tf `cost_breakdown` and
# `silent_cost_growth`, and read them before you walk away from this stack.
###############################################################################


###############################################################################
# 1. PACKAGING
#
# Same mechanism as Day 04: the archive provider builds the zip at plan time
# and output_base64sha256 becomes source_code_hash, which is how Terraform
# knows to redeploy when you edit the Python and not when you don't.
###############################################################################

data "archive_file" "chaos" {
  type        = "zip"
  source_file = "${path.module}/lambda/chaos_workload.py"
  output_path = "${path.module}/build/chaos_workload.zip"
}


###############################################################################
# 2. SNS — where the page goes
#
# One topic. One subscription. Deliberately minimal, because the interesting
# decision on this day is not "where does the notification go", it is "which
# alarm is allowed to send one".
#
# The answer in this stack: exactly one alarm, the composite in section 7. The
# three alarms that feed it have no actions at all. That is not an oversight
# and obs_audit.py's OBS-004 knows the difference — see its docstring.
###############################################################################

resource "aws_sns_topic" "alerts" {
  name = "${local.prefix}-alerts-${local.suffix}"

  tags = {
    Name = "${local.prefix}-alerts"
  }
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.notification_email

  # There is no `confirmed` attribute to check here, and Terraform will report
  # this resource as successfully created whether or not you click the link in
  # the email. See outputs.tf `subscription_status_warning`.
}


###############################################################################
# 3. LOG GROUPS — created deliberately, with retention, on purpose
#
# THE SINGLE MOST EXPENSIVE HABIT IN AWS
#
# Almost nothing creates its own log group correctly. Lambda, ECS, API Gateway,
# EKS, RDS — all of them will happily create a log group for you on first write
# if one does not exist, and every one of those groups is created with:
#
#     retention: Never expire
#     tags:      none
#     owner:     nobody
#
# Ingestion is $0.50/GB, once. Storage is $0.03/GB-month, forever. Neither
# number is large. The problem is that "forever" compounds and nothing ever
# reminds you: log groups do not appear on a resource list you look at, they
# survive `terraform destroy` when Terraform did not create them, and Cost
# Explorer folds all of them into a single "CloudWatch" line item.
#
# The habit that fixes it, permanently: create every log group in code, with
# retention, BEFORE the thing that writes to it. Then the service finds the
# group already there and writes into yours.
#
# For Lambda specifically the name is not negotiable — it must be exactly
# /aws/lambda/<function-name> — which is why locals in providers.tf compute
# these names once.
###############################################################################

# The workload log group. Everything interesting on this day happens in here:
# the metric filters in section 4 read it, the alarms in section 5 are built on
# those metrics, and the analyser in section 10 queries it.
resource "aws_cloudwatch_log_group" "workload" {
  name              = local.workload_log_group_name
  retention_in_days = var.log_retention_days

  # STANDARD, and it has to be. INFREQUENT_ACCESS is half price and silently
  # supports no metric filters, no subscription filters and no Live Tail —
  # which would remove sections 4, 5, 6 and 7 of this file. See the variable
  # description; the class cannot be changed after creation.
  log_group_class = var.log_group_class

  tags = {
    Name = "${local.prefix}-workload"
    Role = "incident-data"
  }
}

# The chaos function's own log group, created here so Lambda does not create it
# for us without retention. This is the pattern; copy it into every Lambda you
# ever write.
resource "aws_cloudwatch_log_group" "chaos" {
  name              = local.chaos_log_group_name
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${local.prefix}-chaos"
  }
}


###############################################################################
# 4. METRIC FILTERS — turning log text into numbers
#
# A metric filter watches every log event arriving in a group, matches a
# pattern, and publishes a CloudWatch metric datapoint. It is the cheapest
# useful thing in observability:
#
#   THE FILTER ITSELF IS FREE. There is no per-filter and no per-evaluation
#   charge, at any volume.
#
#   THE METRIC IT PUBLISHES IS NOT. Every distinct (namespace, metric name,
#   dimension-value combination) is one custom metric at $0.30/month, and a
#   custom metric CANNOT BE DELETED. It ages out fifteen months after its last
#   datapoint. There is no API, no console button, and no support ticket that
#   removes it sooner.
#
# Those two facts together produce the classic bill: someone adds a dimension
# whose value is a request ID, a customer ID or a full URL path, and creates
# forty thousand custom metrics in an afternoon. $12,000/month, undeletable,
# for fifteen months. It is not a rare story.
#
# THE RULE: dimensions are for values from a set you could write down on a
# napkin. Everything else stays in the log line, where Logs Insights can query
# it for $0.005/GB scanned and nothing accumulates.
#
# THREE WAYS TO GET A METRIC OUT OF AN APPLICATION
#
#   Metric filter     Free to run. Reads logs you were already writing. Up to
#                     ~1 minute of lag. Cannot see anything you did not log.
#                     Use this by default.
#
#   EMF (Embedded     You log a specially-shaped JSON blob and CloudWatch
#   Metric Format)    extracts metrics from it at ingestion, with no filter to
#                     maintain and full dimension support. Same $0.30/metric.
#                     Best when the application knows things the log line does
#                     not — cache hit ratio, queue depth at decision time.
#
#   PutMetricData     A synchronous API call from your code. $0.01 per 1,000
#                     requests, plus the latency and the failure mode of an
#                     API call on your hot path. Use it when you need a metric
#                     within seconds and there is no log line to hang it on.
#
# Ninety per cent of custom metrics should be metric filters. Most of the rest
# should be EMF. PutMetricData is a specialist tool that gets reached for first
# because it is the one that appears in the SDK docs.
#
# PATTERN SYNTAX
#
# These filters use JSON selectors, because chaos_workload.py writes JSON:
#
#     { $.level = "ERROR" }              field equals
#     { $.status >= 500 }                numeric comparison
#     { $.level = "ERROR" && $.status = 503 }
#
# For unstructured text the syntax is positional instead:
#
#     [ts, level = ERROR, ...]           space-delimited fields
#     "connection refused"               plain substring
#
# Structured logs are worth the migration for this alone. Positional patterns
# break the day someone adds a field.
###############################################################################

# ---------------------------------------------------------------------------
# 4a. Request count. The denominator of every rate you will ever alarm on.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_metric_filter" "requests" {
  name           = "${local.prefix}-requests"
  log_group_name = aws_cloudwatch_log_group.workload.name
  pattern        = "{ $.event = \"request_completed\" }"

  metric_transformation {
    name      = "RequestCount"
    namespace = local.metric_namespace
    value     = "1"

    # default_value publishes a 0 for every period in which nothing matched.
    #
    # This matters more than it looks. Without it, a period with no traffic
    # produces NO DATAPOINT AT ALL, not a zero — and "no datapoint" is what
    # every alarm's treat_missing_data setting has to guess about. Setting 0
    # here turns a guessing problem into arithmetic.
    #
    # The catch, and it is a real one: AWS does not allow default_value
    # together with dimensions. See 4c, which has dimensions and therefore
    # cannot have this.
    default_value = "0"
  }
}

# ---------------------------------------------------------------------------
# 4b. Error count. The numerator.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_metric_filter" "errors" {
  name           = "${local.prefix}-errors"
  log_group_name = aws_cloudwatch_log_group.workload.name
  pattern        = "{ $.level = \"ERROR\" }"

  metric_transformation {
    name          = "ErrorCount"
    namespace     = local.metric_namespace
    value         = "1"
    default_value = "0"
  }
}

# ---------------------------------------------------------------------------
# 4c. Errors broken down by type — the one filter with a dimension.
#
# ERROR_TYPES in chaos_workload.py is a tuple of exactly four strings. That is
# not tidiness, it is a cost control: four dimension values is four custom
# metrics is $1.20/month. The same filter pointed at $.request_id would be one
# custom metric per request.
#
# Note the missing default_value. AWS rejects the combination, so this metric
# is genuinely absent in periods where nothing failed — which is correct here.
# You do not want a manufactured zero for "CIRCUIT_OPEN" on a quiet Sunday.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_metric_filter" "errors_by_type" {
  name           = "${local.prefix}-errors-by-type"
  log_group_name = aws_cloudwatch_log_group.workload.name
  pattern        = "{ $.level = \"ERROR\" && $.error_type = * }"

  metric_transformation {
    name      = "ErrorCountByType"
    namespace = local.metric_namespace
    value     = "1"

    dimensions = {
      ErrorType = "$.error_type"
    }
  }
}

# ---------------------------------------------------------------------------
# 4d. Latency. The filter that extracts a VALUE rather than counting.
#
# value = "$.latency_ms" publishes the actual number from each matching log
# line. That is what gives CloudWatch a full statistic set, and a full
# statistic set is what makes p95 possible.
#
# A filter with value = "1" can only ever be counted, averaged or summed. If
# you have ever wondered why an extended statistic on your metric returns
# nothing, this is why.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_metric_filter" "latency" {
  name           = "${local.prefix}-latency"
  log_group_name = aws_cloudwatch_log_group.workload.name
  pattern        = "{ $.event = \"request_completed\" && $.latency_ms = * }"

  metric_transformation {
    name      = "LatencyMillis"
    namespace = local.metric_namespace
    value     = "$.latency_ms"
    unit      = "Milliseconds"

    # No default_value. A manufactured 0ms datapoint would drag every
    # percentile down and make the p95 alarm quietly useless during exactly
    # the low-traffic periods you most need it. Absent is the honest answer to
    # "how slow were the requests we did not receive".
  }
}


###############################################################################
# 5. ALARMS — and the four settings people get wrong
#
# ------------------------------------------------------------------ 5.1
# treat_missing_data. READ THIS ONE.
#
# CloudWatch evaluates an alarm on a fixed schedule whether or not data
# arrived. When a period has no datapoint, this setting decides what happens:
#
#   missing       (THE DEFAULT) The period is ignored. CloudWatch looks further
#                 back for enough real datapoints to decide. If it never finds
#                 any, the alarm goes to INSUFFICIENT_DATA and STAYS THERE.
#                 It will not notify, it is not red, and on a dashboard it is
#                 a polite grey. This is how an alarm on a metric that stopped
#                 being published — because the service died, or someone
#                 renamed it, or a deploy dropped the log line the filter
#                 matched — becomes permanently, silently useless.
#
#   notBreaching  Missing periods count as "fine". Use when absence genuinely
#                 means health: an error-count metric with no datapoints
#                 usually means no errors.
#
#   breaching     Missing periods count as a breach. This is the dead-man's
#                 switch. Use it when silence is itself the bad news — see
#                 the no-telemetry alarm in 5c, which is the single most
#                 valuable alarm in this stack.
#
#   ignore        The alarm keeps its current state and never transitions on
#                 missing data. Use for a metric that is legitimately bursty
#                 and where you would rather hold the last known state than
#                 flap.
#
# Every alarm below sets this explicitly. Leaving it at the default is check
# OBS-005 and it is not a style rule — the default is the option most likely
# to hide an outage.
#
# ------------------------------------------------------------------ 5.2
# Alarm on RATE or RATIO, never raw count.
#
# "More than 50 errors in 5 minutes" is a different statement at 03:00 than at
# midday. It fires on a traffic spike where nothing is wrong, and it goes
# quiet during the outage where traffic collapsed. 5% of requests failing is
# 5% of requests failing at every hour of the day.
#
# The mechanism is metric math over two metric filters. 5a does it.
#
# ------------------------------------------------------------------ 5.3
# M out of N.
#
# datapoints_to_alarm (M) and evaluation_periods (N). 3 of 5 means at least
# three of the last five minutes breached. 1 of 1 — the default shape people
# accidentally build — pages on a single unlucky minute and resolves before
# anyone reaches a laptop, and that is how a team learns to ignore the topic.
#
# Do not reach for a longer average instead: averaging hides the shape. Three
# catastrophic minutes and two perfect ones average to "slightly elevated".
#
# ------------------------------------------------------------------ 5.4
# Pricing, since it decides how many alarms you build.
#
#   Standard-resolution alarm   $0.10/alarm-month, first 10 free
#   High-resolution alarm       $0.30/alarm-month, NOT in the free ten
#   Composite alarm             $0.50/alarm-month, NOT in the free ten
#
# This stack: 3 standard + 1 composite (+2 broken examples). On a fresh
# account the standard ones are free and the composite is $0.50/month.
###############################################################################

# ---------------------------------------------------------------------------
# 5a. ERROR RATE — metric math, and the shape worth memorising
#
# Five query blocks, one returning data:
#
#   m_errors    raw ErrorCount        (return_data = false)
#   m_requests  raw RequestCount      (return_data = false)
#   e           FILL(m_errors, 0)     — treat gaps as zero errors
#   r           FILL(m_requests, 0)   — treat gaps as zero requests
#   error_rate  IF(r > 0, 100*e/r, 0) — and never divide by zero
#
# The IF() is not defensive programming for its own sake. Without it, a period
# with zero traffic produces a division by zero, CloudWatch emits no datapoint
# for the expression, and the alarm falls back to treat_missing_data — which
# means the behaviour of your alarm during quiet periods is decided by a
# setting three lines further down rather than by the expression you wrote.
# Make it explicit.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "error_rate" {
  alarm_name        = "${local.prefix}-error-rate-${local.suffix}"
  alarm_description = "Percentage of requests returning an error, over ${var.alarm_datapoints_to_alarm} of the last ${var.alarm_evaluation_periods} periods. Rate, not count, so it means the same thing at 03:00 as at midday."

  comparison_operator = "GreaterThanThreshold"
  threshold           = var.error_rate_threshold_percent
  evaluation_periods  = var.alarm_evaluation_periods
  datapoints_to_alarm = var.alarm_datapoints_to_alarm

  # notBreaching: FILL() already turned "no data" into an explicit zero for the
  # inputs, so a genuinely missing period here means CloudWatch had nothing at
  # all — no traffic. No traffic is not an error rate breach. Silence is caught
  # by 5c instead, which is the right division of labour.
  treat_missing_data = "notBreaching"

  # No alarm_actions. This alarm is an INPUT to the composite in section 7,
  # which is the only thing in this stack allowed to wake someone. OBS-004
  # exempts alarms referenced by a composite rule for exactly this reason.
  actions_enabled = true

  metric_query {
    id          = "m_errors"
    return_data = false

    metric {
      metric_name = aws_cloudwatch_log_metric_filter.errors.metric_transformation[0].name
      namespace   = local.metric_namespace
      period      = var.alarm_period_seconds
      stat        = "Sum"
    }
  }

  metric_query {
    id          = "m_requests"
    return_data = false

    metric {
      metric_name = aws_cloudwatch_log_metric_filter.requests.metric_transformation[0].name
      namespace   = local.metric_namespace
      period      = var.alarm_period_seconds
      stat        = "Sum"
    }
  }

  metric_query {
    id          = "e"
    expression  = "FILL(m_errors, 0)"
    label       = "Errors (gaps filled with 0)"
    return_data = false
  }

  metric_query {
    id          = "r"
    expression  = "FILL(m_requests, 0)"
    label       = "Requests (gaps filled with 0)"
    return_data = false
  }

  metric_query {
    id          = "error_rate"
    expression  = "IF(r > 0, 100 * e / r, 0)"
    label       = "Error rate %"
    return_data = true
  }

  tags = {
    Name     = "${local.prefix}-error-rate"
    Signal   = "errors"
    PagesVia = "composite"
  }
}

# ---------------------------------------------------------------------------
# 5b. LATENCY at p95 — because the average is a liar
#
# An average latency of 200ms is entirely consistent with one request in
# twenty taking nine seconds. The customers who leave are always in the tail.
#
# extended_statistic works here only because the metric filter in 4d publishes
# a VALUE. On a count-only metric this field silently produces nothing.
#
# Note also what this alarm does during the circuit-breaker phase of the lab's
# incident: latency DROPS, sharply, because the breaker is failing fast. On a
# latency-only dashboard that looks like recovery. It is why 5a exists.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "latency_p95" {
  alarm_name        = "${local.prefix}-latency-p95-${local.suffix}"
  alarm_description = "p95 request latency above ${var.latency_p95_threshold_ms}ms. p95, not average — an average hides exactly the tail that customers notice."

  namespace          = local.metric_namespace
  metric_name        = aws_cloudwatch_log_metric_filter.latency.metric_transformation[0].name
  extended_statistic = "p95"
  period             = var.alarm_period_seconds

  comparison_operator = "GreaterThanThreshold"
  threshold           = var.latency_p95_threshold_ms
  evaluation_periods  = var.alarm_evaluation_periods
  datapoints_to_alarm = var.alarm_datapoints_to_alarm

  # notBreaching, and here the reasoning is different from 5a. The latency
  # filter deliberately has NO default_value (see 4d), so quiet periods really
  # do produce no datapoint. "We received no requests" is not a latency
  # breach — but it might be an outage, which is 5c's job, not this alarm's.
  # One alarm, one question.
  treat_missing_data = "notBreaching"
  actions_enabled    = true

  tags = {
    Name     = "${local.prefix}-latency-p95"
    Signal   = "latency"
    PagesVia = "composite"
  }
}

# ---------------------------------------------------------------------------
# 5c. NO TELEMETRY — the dead-man's switch, and the best alarm here
#
# Every alarm above answers "is the data bad". This one answers "is there any
# data", which is the question that catches the outages the others cannot see:
# the service that crashed on boot, the log driver that broke, the deploy that
# renamed the field a metric filter matched on, the IAM change that revoked
# logs:PutLogEvents.
#
# In every one of those cases the metrics simply stop. The error-rate alarm
# sees no errors. The latency alarm sees no slow requests. Both sit in a
# comfortable OK — or, with the default treat_missing_data, drift to
# INSUFFICIENT_DATA and stay grey forever while the service is dark.
#
# treat_missing_data = "breaching" is what inverts that. Missing data IS the
# breach. This is the one place where the setting people are told to avoid is
# exactly the right answer.
#
# Practical note: pick a threshold that reflects your genuinely quietest
# period, and use a longer evaluation window than your other alarms. A
# dead-man's switch that flaps every Sunday at 04:00 gets muted, and a muted
# dead-man's switch is worse than none because it looks like coverage.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "no_telemetry" {
  alarm_name        = "${local.prefix}-no-telemetry-${local.suffix}"
  alarm_description = "Fewer than 1 request logged per period. Missing data is treated as BREACHING on purpose — this alarm exists to detect silence, which every other alarm in this stack is blind to."

  namespace   = local.metric_namespace
  metric_name = aws_cloudwatch_log_metric_filter.requests.metric_transformation[0].name
  statistic   = "Sum"
  period      = var.alarm_period_seconds

  comparison_operator = "LessThanThreshold"
  threshold           = 1
  evaluation_periods  = 10
  datapoints_to_alarm = 10

  treat_missing_data = "breaching"
  actions_enabled    = true

  tags = {
    Name     = "${local.prefix}-no-telemetry"
    Signal   = "liveness"
    PagesVia = "composite"
  }
}


###############################################################################
# 6. A SECOND TOPIC, FOR SUMMARIES
#
# Alarm pages and AI-generated summaries do NOT go to the same topic, and the
# reasons are worth more than the five lines of Terraform they cost.
#
#   DIFFERENT DATA. A page says "error rate is 12%". A summary contains
#   fragments of your application's log lines — which is to say, whatever your
#   application happened to print, redacted on a best-effort basis. Those are
#   different sensitivity classes and they deserve different subscriber lists.
#   On one topic, everyone who wants to know the service is down is also
#   reading log content.
#
#   DIFFERENT MUTE POLICY. When the summaries turn out to be noisy — and the
#   first month they will be — you want to be able to switch them off without
#   switching off the alarm that pages you. On one topic, the only available
#   action is unsubscribing from both, and somebody will.
#
#   DIFFERENT RELIABILITY BAR. The page must arrive. The summary is a nice to
#   have that depends on a model endpoint in another service. Coupling the
#   thing that must work to the thing that might not is a bad trade you make
#   once.
#
# This is the general shape of the argument for keeping the AI path beside the
# alerting path rather than inside it. The same reasoning is why the summary
# is not a dashboard widget (section 8) and why nothing downstream is
# automated off it.
###############################################################################

resource "aws_sns_topic" "summaries" {
  name = "${local.prefix}-summaries-${local.suffix}"

  tags = {
    Name = "${local.prefix}-summaries"
    Data = "contains-log-content"
  }
}

resource "aws_sns_topic_subscription" "summaries_email" {
  topic_arn = aws_sns_topic.summaries.arn
  protocol  = "email"
  endpoint  = var.notification_email

  # Same address in the lab, because you only have one inbox. In production
  # this is the line where the two audiences diverge, and the tag on the topic
  # above is there to make the reviewer stop and think about who is on it.
}



###############################################################################
# 7. COMPOSITE ALARM — one page, three signals
#
# The three alarms above have no notification actions. This one has all of
# them. That is the entire argument for composite alarms:
#
#   * The children are DIAGNOSTIC. They exist so that when you are paged you
#     can see, in one screen, which of the three signals tripped.
#   * The parent is the PAGE. One notification per incident, not three.
#
# Without this, a real cascade sends error-rate mail, then latency mail, then
# no-telemetry mail, at which point somebody writes an inbox rule and the
# next incident is discovered by a customer.
#
# THE TRAP: A COMPOSITE ALARM THAT CAN NEVER FIRE
#
# The rule language accepts anything syntactically valid, including rules that
# are logically impossible. `ALARM(a) AND OK(a)` is accepted, created, billed
# monthly, shows a reassuring green in the console, and cannot transition under
# any circumstances. Section 12 builds one deliberately; OBS-007 finds it.
#
# The realistic version of this mistake is not that obvious. It is an AND
# across two conditions that never co-occur — "the queue is deep AND the
# consumer is idle" — written by someone reducing noise, tested by nobody,
# discovered eighteen months later during a postmortem.
#
# Test every composite alarm by forcing a child into ALARM with
# `aws cloudwatch set-alarm-state` and watching the parent. Step 5 of the lab
# does exactly that. It takes ninety seconds and it is the only proof there is.
#
# COST: $0.50/alarm-month, and composite alarms are NOT covered by the ten
# free standard alarms.
###############################################################################

resource "aws_cloudwatch_composite_alarm" "service_degraded" {
  alarm_name        = "${local.prefix}-service-degraded-${local.suffix}"
  alarm_description = "Any one of: error rate above threshold, p95 latency above threshold, or no telemetry at all. The only alarm in this stack with a notification action."

  alarm_rule = join(" OR ", [
    "ALARM(${aws_cloudwatch_metric_alarm.error_rate.alarm_name})",
    "ALARM(${aws_cloudwatch_metric_alarm.latency_p95.alarm_name})",
    "ALARM(${aws_cloudwatch_metric_alarm.no_telemetry.alarm_name})",
  ])

  actions_enabled = true
  alarm_actions   = [aws_sns_topic.alerts.arn]
  ok_actions      = [aws_sns_topic.alerts.arn]

  # An OK action is not decoration. Half of alarm fatigue is not knowing
  # whether the thing you were paged about is over.

  tags = {
    Name = "${local.prefix}-service-degraded"
  }
}


###############################################################################
# 8. DASHBOARD — and the argument against most dashboards
#
# A dashboard nobody opens is not observability. It is a screensaver with a
# $3/month bill and, worse, it is a false sense of coverage: "we have a
# dashboard for that" is one of the more expensive sentences in operations.
#
# The test a dashboard has to pass: can a person who has just been paged, at
# 03:00, on a phone, answer a specific question with it in under thirty
# seconds? If the answer needs three widgets and a mental join, the dashboard
# has failed and an alarm should have carried the answer instead.
#
# This one exists to answer exactly three questions, in this order:
#
#   1. Is it happening now?             -> alarm status widget, top left
#   2. How bad, and getting worse?      -> error rate and p95, side by side
#   3. What KIND of failure?            -> errors by type, and the raw log
#                                          query underneath
#
# Question 3 is the one that makes this dashboard worth keeping: the log
# widget runs a real Logs Insights query, so the last stop before you leave
# the dashboard is the actual log lines. A dashboard that cannot get you to
# the logs sends you to the console to start over.
#
# COST: $3.00/month per dashboard beyond the first three. This stack creates
# one, so on most accounts it is free.
#
# Note `region` on every widget. Omit it and the widget renders against
# whatever region the console happens to be showing, which produces the
# "the dashboard is empty but the metrics exist" support question.
###############################################################################

resource "aws_cloudwatch_dashboard" "main" {
  count = var.create_dashboard ? 1 : 0

  dashboard_name = "${local.prefix}-operations-${local.suffix}"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "alarm"
        x      = 0
        y      = 0
        width  = 24
        height = 3
        properties = {
          title = "1. Is it happening now?"
          alarms = [
            aws_cloudwatch_composite_alarm.service_degraded.arn,
            aws_cloudwatch_metric_alarm.error_rate.arn,
            aws_cloudwatch_metric_alarm.latency_p95.arn,
            aws_cloudwatch_metric_alarm.no_telemetry.arn,
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 3
        width  = 12
        height = 6
        properties = {
          title  = "2a. Error rate % (the alarm's own expression)"
          region = local.region
          view   = "timeSeries"
          stat   = "Sum"
          period = var.alarm_period_seconds
          yAxis  = { left = { min = 0 } }
          metrics = [
            [local.metric_namespace, "ErrorCount", { id = "err", visible = false, stat = "Sum" }],
            [local.metric_namespace, "RequestCount", { id = "req", visible = false, stat = "Sum" }],
            [{ id = "rate", expression = "IF(FILL(req,0) > 0, 100 * FILL(err,0) / FILL(req,0), 0)", label = "Error rate %" }],
          ]
          annotations = {
            horizontal = [{
              label = "alarm threshold"
              value = var.error_rate_threshold_percent
            }]
          }
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 3
        width  = 12
        height = 6
        properties = {
          title  = "2b. Latency p50 / p95 / p99"
          region = local.region
          view   = "timeSeries"
          period = var.alarm_period_seconds
          metrics = [
            [local.metric_namespace, "LatencyMillis", { stat = "p50", label = "p50" }],
            ["...", { stat = "p95", label = "p95" }],
            ["...", { stat = "p99", label = "p99" }],
          ]
          annotations = {
            horizontal = [{
              label = "p95 alarm threshold"
              value = var.latency_p95_threshold_ms
            }]
          }
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 9
        width  = 12
        height = 6
        properties = {
          title   = "3a. Errors by type (4 dimension values, on purpose)"
          region  = local.region
          view    = "timeSeries"
          stacked = true
          period  = var.alarm_period_seconds
          metrics = [
            [local.metric_namespace, "ErrorCountByType", "ErrorType", "DB_CONN_TIMEOUT", { stat = "Sum" }],
            ["...", "POOL_EXHAUSTED", { stat = "Sum" }],
            ["...", "CIRCUIT_OPEN", { stat = "Sum" }],
            ["...", "UPSTREAM_5XX", { stat = "Sum" }],
          ]
        }
      },
      {
        type   = "log"
        x      = 12
        y      = 9
        width  = 12
        height = 6
        properties = {
          title  = "3b. The actual log lines — the last stop before the console"
          region = local.region
          view   = "table"
          query  = "SOURCE '${aws_cloudwatch_log_group.workload.name}' | fields @timestamp, level, error_type, latency_ms, message | filter level = 'ERROR' | sort @timestamp desc | limit 50"
        }
      },
      {
        type   = "text"
        x      = 0
        y      = 15
        width  = 24
        height = 4
        properties = {
          markdown = <<-MD
            ### How to read this dashboard during an incident

            **Top row** answers *is it happening now*. If the composite is green, stop here.

            **Middle row** answers *how bad*. Watch for the shape where **latency
            falls while errors stay high** — that is a circuit breaker failing fast,
            not a recovery.

            **Bottom left** answers *what kind*. **Bottom right** is the raw evidence.

            The AI incident summary is **not** on this dashboard, and that is
            deliberate. A generated summary belongs where you can see the log lines
            it cites next to it. See the Day 06 README, section *"Why the summary is
            not a widget"*.
          MD
        }
      },
    ]
  })
}


###############################################################################
# 9. THE CHAOS WORKLOAD — a failure you can cause on demand
#
# Monitoring pointed at nothing teaches nothing. This function manufactures a
# realistic incident on demand: a deploy shrinks a connection pool, latency
# climbs, connections time out, retries amplify the load, a circuit breaker
# opens, and customers see 503s.
#
# Read the docstring in lambda/chaos_workload.py before the lab. The design
# point that matters: THE CAUSE APPEARS EXACTLY ONCE, at the very start, as a
# single INFO line, and everything after it is consequence. That asymmetry is
# what makes section 10's naive "summarise the last 200 lines" approach fail
# in a way you can watch.
#
# IAM NOTE: the role below can write to two named log groups and can do
# nothing else. In particular it deliberately does NOT have logs:CreateLogGroup
# — because if it did, and someone renamed the workload group, the function
# would silently create a replacement with no retention and no metric filters,
# and the whole stack would look healthy while measuring nothing.
###############################################################################

data "aws_iam_policy_document" "chaos_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "chaos" {
  name               = "${local.prefix}-chaos-${local.suffix}"
  assume_role_policy = data.aws_iam_policy_document.chaos_assume.json

  tags = {
    Name = "${local.prefix}-chaos"
  }
}

data "aws_iam_policy_document" "chaos" {
  statement {
    sid    = "WriteToNamedLogGroupsOnly"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "${aws_cloudwatch_log_group.workload.arn}:*",
      "${aws_cloudwatch_log_group.chaos.arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "chaos" {
  name   = "${local.prefix}-chaos-policy"
  role   = aws_iam_role.chaos.id
  policy = data.aws_iam_policy_document.chaos.json
}

resource "aws_lambda_function" "chaos" {
  function_name = "${local.prefix}-chaos-${local.suffix}"
  role          = aws_iam_role.chaos.arn
  handler       = "chaos_workload.handler"
  runtime       = var.chaos_lambda_runtime

  filename         = data.archive_file.chaos.output_path
  source_code_hash = data.archive_file.chaos.output_base64sha256

  memory_size = var.chaos_lambda_memory_mb
  timeout     = var.chaos_lambda_timeout_seconds

  environment {
    variables = {
      WORKLOAD_LOG_GROUP  = aws_cloudwatch_log_group.workload.name
      DEFAULT_BURST_LINES = tostring(var.chaos_default_burst_lines)
      SERVICE_NAME        = "checkout-api"
    }
  }

  # The explicit dependency on the log group is what stops Lambda creating its
  # own without retention on the first invocation. Terraform has no way to
  # infer this ordering — the function does not reference the group.
  depends_on = [
    aws_cloudwatch_log_group.chaos,
    aws_iam_role_policy.chaos,
  ]

  tags = {
    Name = "${local.prefix}-chaos"
    Role = "incident-generator"
  }
}


###############################################################################
# 10. THE ANALYSER — Bedrock IAM, and the narrowest policy that works
#
# ------------------------------------------------------------------ 10.1
# THE MODEL ARN SHAPE
#
# The single most common IAM mistake on Bedrock is writing your own account ID
# into a foundation-model ARN:
#
#   WRONG  arn:aws:bedrock:us-east-1:123456789012:foundation-model/anthropic...
#   RIGHT  arn:aws:bedrock:us-east-1::foundation-model/anthropic...
#                                    ^^ empty. The model is not yours.
#
# The wrong one matches nothing, produces AccessDeniedException, and the error
# message does not tell you why. The usual "fix" — after twenty minutes — is
# Resource: "*", which is check OBS-014, and which then ships to production
# because it works.
#
# Resource: "*" on bedrock:InvokeModel means this function may invoke ANY
# model in the account, including ones you have not evaluated, ones with
# different data-handling terms, and ones that cost forty times more per
# token. A compromised or simply buggy function with that permission is a
# blank cheque against your Bedrock bill.
#
# ------------------------------------------------------------------ 10.2
# INFERENCE PROFILES
#
# If you switch to a cross-region inference profile — model IDs beginning
# "us.", "eu." — the ARN shape changes and DOES carry your account:
#
#   arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.anthropic...
#
# And you need BOTH: the profile you invoke, and the foundation-model ARN in
# every region the profile may route to. Getting only the first is the second
# most common Bedrock IAM failure, and it fails intermittently — which is much
# worse than failing always — because it only breaks when the profile happens
# to route you elsewhere.
#
# Worth noticing before you reach for one: a cross-region inference profile
# means log content may be processed in a region you did not choose. That is
# OBS-013's concern, arriving through a door most people do not check.
#
# ------------------------------------------------------------------ 10.3
# WHAT ELSE THIS ROLE CAN DO
#
# Read one log group. Publish to one topic. Write one DynamoDB item. Write its
# own logs. That is the entire policy, and it is worth checking that yours can
# be described in a sentence too.
###############################################################################

resource "aws_cloudwatch_log_group" "analyser" {
  name              = local.analyser_log_group_name
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${local.prefix}-analyser"
    Role = "observability-tooling"
  }
}

# ---------------------------------------------------------------------------
# 10.4. Idempotency table
#
# One row per alarm, with a TTL. The analyser writes it with a conditional
# expression, so a second invocation for the same alarm inside the window is
# rejected by DynamoDB rather than by application logic that might race.
#
# TTL deletions are FREE — they are not billed as write units. Do not build
# this with a scheduled cleanup Lambda.
#
# COST: on-demand billing, a handful of items. Comfortably $0.00 at lab
# volumes, inside the permanent 25 GB free tier.
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "idempotency" {
  name         = "${local.prefix}-analysis-idempotency-${local.suffix}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "alarm_name"

  attribute {
    name = "alarm_name"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  tags = {
    Name = "${local.prefix}-idempotency"
  }
}

data "aws_iam_policy_document" "analyser_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "analyser" {
  name               = "${local.prefix}-analyser-${local.suffix}"
  assume_role_policy = data.aws_iam_policy_document.analyser_assume.json

  tags = {
    Name = "${local.prefix}-analyser"
  }
}

data "aws_iam_policy_document" "analyser" {
  statement {
    sid    = "InvokeExactlyOneModel"
    effect = "Allow"

    actions = [
      "bedrock:InvokeModel",
      # Converse maps to InvokeModel; there is no separate bedrock:Converse
      # action. InvokeModelWithResponseStream is listed because switching to
      # streaming later is a one-line code change and a silent 403 otherwise.
      "bedrock:InvokeModelWithResponseStream",
    ]

    resources = [local.bedrock_model_arn]
  }

  statement {
    sid    = "ReadTheWorkloadLogGroupOnly"
    effect = "Allow"

    actions = [
      "logs:FilterLogEvents",
      "logs:GetLogEvents",
      "logs:StartQuery",
      "logs:GetQueryResults",
    ]

    resources = [
      "${aws_cloudwatch_log_group.workload.arn}:*",
    ]
  }

  statement {
    sid       = "WriteItsOwnLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.analyser.arn}:*"]
  }

  statement {
    sid       = "PublishSummaries"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.summaries.arn]
  }

  statement {
    sid       = "IdempotencyLock"
    effect    = "Allow"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.idempotency.arn]
  }
}

resource "aws_iam_role_policy" "analyser" {
  name   = "${local.prefix}-analyser-policy"
  role   = aws_iam_role.analyser.id
  policy = data.aws_iam_policy_document.analyser.json
}

data "archive_file" "analyser" {
  type        = "zip"
  source_file = "${path.module}/lambda/incident_analyser.py"
  output_path = "${path.module}/build/incident_analyser.zip"
}

# ---------------------------------------------------------------------------
# 10.5. The analyser itself.
#
# Read lambda/incident_analyser.py before you deploy this. The Terraform is
# unremarkable; the argument is in the Python, and specifically in
# verify_claims() — the loop that resolves every citation the model produces
# and checks the quoted fragment really appears at that line.
#
# Note what is in the environment. Redaction, the token budget and the
# sampling strategy are all CONFIGURATION, not code. That is deliberate: the
# broken analyser in section 12 runs the identical zip file and differs only
# in these variables, which is exactly how these mistakes reach production.
# Nobody writes a bad log summariser. People deploy a good one badly.
# ---------------------------------------------------------------------------
resource "aws_lambda_function" "analyser" {
  function_name = "${local.prefix}-analyser-${local.suffix}"
  role          = aws_iam_role.analyser.arn
  handler       = "incident_analyser.handler"
  runtime       = var.chaos_lambda_runtime

  filename         = data.archive_file.analyser.output_path
  source_code_hash = data.archive_file.analyser.output_base64sha256

  memory_size = var.analyser_lambda_memory_mb
  timeout     = var.analyser_lambda_timeout_seconds

  # A model invocation is slow and this function is invoked by an event, not
  # by a user waiting. There is no reason for more than a handful of these to
  # run at once, and a hard cap is the difference between a flapping alarm
  # costing dollars and costing thousands.
  reserved_concurrent_executions = 2

  environment {
    variables = {
      WORKLOAD_LOG_GROUP  = aws_cloudwatch_log_group.workload.name
      BEDROCK_MODEL_ID    = var.bedrock_model_id
      BEDROCK_REGION      = local.bedrock_region
      SUMMARY_TOPIC_ARN   = aws_sns_topic.summaries.arn
      MAX_INPUT_TOKENS    = tostring(var.analyser_max_input_tokens)
      MAX_LOG_LINES       = tostring(var.analyser_max_log_lines)
      LOOKBACK_MINUTES    = tostring(var.analyser_lookback_minutes)
      REDACT_LOGS         = var.analyser_redact_logs ? "true" : "false"
      IDEMPOTENCY_MINUTES = tostring(var.analyser_idempotency_minutes)
      IDEMPOTENCY_TABLE   = aws_dynamodb_table.idempotency.name
      SAMPLE_STRATEGY     = "balanced"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.analyser,
    aws_iam_role_policy.analyser,
  ]

  tags = {
    Name = "${local.prefix}-analyser"
    Role = "incident-analysis"
  }
}



###############################################################################
# 11. WIRING — alarm state change to analyser, and the audit trail
#
# ------------------------------------------------------------------ 11.1
# WHY EVENTBRIDGE AND NOT SNS
#
# The composite alarm already publishes to SNS, so the obvious wiring is to
# subscribe the analyser to that topic. Don't:
#
#   * An SNS alarm message is a JSON string inside a JSON envelope, and you
#     end up parsing a string field to find out which alarm fired.
#   * You cannot filter on state transition direction without doing it in
#     code — so the analyser is invoked, and billed, on every OK message too.
#   * The topic is a notification channel with humans on it. Adding a compute
#     subscriber to it means every future change to the human notification
#     path is now also a change to the automation path.
#
# EventBridge gives you the alarm state change as a first-class event with a
# schema, and the pattern below filters to ALARM transitions only, at the
# broker, before anything of yours runs. Filtering in the event bus rather
# than in your code is free; filtering in your code costs an invocation.
#
# ------------------------------------------------------------------ 11.2
# THE RULE IS A COST CONTROL
#
# Every transition matched here is a model invocation you pay for. The three
# guards, in order of effectiveness:
#
#   1. M-of-N on the alarm (section 5) — stops the transition happening.
#   2. This pattern — only ALARM, never OK.
#   3. The idempotency table (section 10.4) — one summary per alarm per
#      window, no matter how much it flaps.
#
# Guard 1 is the one that matters. If your alarm is noisy, no amount of
# downstream deduplication makes the AI half cheap; it makes it quieter while
# still costing something. Fix the alarm.
#
# ------------------------------------------------------------------ 11.3
# THE DISABLED-RULE TRAP
#
# `enable_auto_analysis = false` creates this rule in the DISABLED state. That
# is a legitimate thing to want — Step 6 of the lab runs the analyser by hand
# first — but a disabled rule that everyone believes is enabled is its own
# outage. It is Day 04's CMP-014 and it looks completely normal in the
# console. If you disable it, say so somewhere a human reads.
###############################################################################

resource "aws_cloudwatch_event_rule" "alarm_to_analyser" {
  name        = "${local.prefix}-alarm-to-analyser-${local.suffix}"
  description = "Invoke the incident analyser when the composite alarm enters ALARM. Filtered at the broker so OK transitions never cost an invocation."

  state = var.enable_auto_analysis ? "ENABLED" : "DISABLED"

  event_pattern = jsonencode({
    source      = ["aws.cloudwatch"]
    detail-type = ["CloudWatch Alarm State Change"]
    detail = {
      alarmName = [aws_cloudwatch_composite_alarm.service_degraded.alarm_name]
      state = {
        value = ["ALARM"]
      }
    }
  })

  tags = {
    Name = "${local.prefix}-alarm-to-analyser"
  }
}

resource "aws_cloudwatch_event_target" "analyser" {
  rule      = aws_cloudwatch_event_rule.alarm_to_analyser.name
  target_id = "analyser"
  arn       = aws_lambda_function.analyser.arn

  # A retry policy and a DLQ, because an asynchronous invocation that fails
  # twice and vanishes is indistinguishable from an incident nobody noticed.
  # Day 04 made this argument at length; it has not changed.
  retry_policy {
    maximum_retry_attempts       = 1
    maximum_event_age_in_seconds = 300
  }

  dead_letter_config {
    arn = aws_sqs_queue.analyser_dlq.arn
  }
}

resource "aws_sqs_queue" "analyser_dlq" {
  name                      = "${local.prefix}-analyser-dlq-${local.suffix}"
  message_retention_seconds = 1209600

  tags = {
    Name = "${local.prefix}-analyser-dlq"
  }
}

data "aws_iam_policy_document" "analyser_dlq" {
  statement {
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.analyser_dlq.arn]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.alarm_to_analyser.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "analyser_dlq" {
  queue_url = aws_sqs_queue.analyser_dlq.id
  policy    = data.aws_iam_policy_document.analyser_dlq.json
}

resource "aws_lambda_permission" "eventbridge_analyser" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.analyser.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.alarm_to_analyser.arn
}

# ---------------------------------------------------------------------------
# 11.4. BEDROCK MODEL INVOCATION LOGGING — the audit trail, and its price
#
# Default OFF, and that default is an argument rather than laziness.
#
# ON, you can answer "what did the model actually see" — which is the first
# question after a summary turns out to be confidently wrong, and which you
# cannot answer any other way. The prompt is gone the moment the Lambda
# returns.
#
# ALSO ON, every prompt and completion is written to a CloudWatch log group in
# your account. That group now holds the log content you were careful about,
# in full, readable by anyone with CloudWatch read access — a much wider
# audience, in most organisations, than the people who can read the original
# application logs. You have fixed an audit gap by opening a data-access gap.
#
# Enable it AND set retention AND put a resource policy on the destination.
# Both halves or neither.
#
# Two operational notes people meet the hard way:
#   * This is an ACCOUNT-LEVEL, region-singleton setting. Two stacks that both
#     manage it will fight, and `terraform destroy` turns it off for the whole
#     region, not just this lab.
#   * The destination log group needs a resource policy allowing
#     bedrock.amazonaws.com to write to it. Without it the setting applies and
#     silently logs nothing, which is the worst of both worlds.
#
# With this false, check OBS-016 fires — correctly. The finding is not "you
# did something stupid", it is "nothing here can tell you what you sent".
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "bedrock_invocations" {
  count = var.enable_bedrock_invocation_logging ? 1 : 0

  name              = "/${local.prefix}/bedrock-invocations-${local.suffix}"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${local.prefix}-bedrock-invocations"
    Data = "contains-full-prompts-and-completions"
  }
}

data "aws_iam_policy_document" "bedrock_logging_assume" {
  count = var.enable_bedrock_invocation_logging ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "bedrock_logging" {
  count = var.enable_bedrock_invocation_logging ? 1 : 0

  name               = "${local.prefix}-bedrock-logging-${local.suffix}"
  assume_role_policy = data.aws_iam_policy_document.bedrock_logging_assume[0].json

  tags = {
    Name = "${local.prefix}-bedrock-logging"
  }
}

data "aws_iam_policy_document" "bedrock_logging" {
  count = var.enable_bedrock_invocation_logging ? 1 : 0

  statement {
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.bedrock_invocations[0].arn}:*"]
  }
}

resource "aws_iam_role_policy" "bedrock_logging" {
  count = var.enable_bedrock_invocation_logging ? 1 : 0

  name   = "${local.prefix}-bedrock-logging-policy"
  role   = aws_iam_role.bedrock_logging[0].id
  policy = data.aws_iam_policy_document.bedrock_logging[0].json
}

resource "aws_bedrock_model_invocation_logging_configuration" "main" {
  count = var.enable_bedrock_invocation_logging ? 1 : 0

  logging_config {
    embedding_data_delivery_enabled = false
    image_data_delivery_enabled     = false
    text_data_delivery_enabled      = true

    cloudwatch_config {
      log_group_name = aws_cloudwatch_log_group.bedrock_invocations[0].name
      role_arn       = aws_iam_role.bedrock_logging[0].arn
    }
  }

  depends_on = [aws_iam_role_policy.bedrock_logging]
}



###############################################################################
# 12. DELIBERATELY BROKEN OBSERVABILITY
#
# Gated behind create_insecure_examples, default true, exactly as on Days 04
# and 05. These exist so obs_audit.py has real findings rather than a clean
# account and a green score that teaches nothing.
#
# Every resource here is a mistake somebody has actually shipped. Read the
# comment above each one and see how ordinary the mistake looks in a pull
# request.
###############################################################################

# ---------------------------------------------------------------------------
# 12a. A log group with no retention → OBS-001
#
# This is not an exotic misconfiguration. It is what you get by DEFAULT, from
# every AWS service that creates a log group on your behalf, unless somebody
# went out of their way to stop it. "Never expire" is not a setting anyone
# chose; it is a setting nobody chose.
#
# Note there is no `retention_in_days` line to delete. That is the whole
# problem: the failure is an absence, so it never appears in a diff and never
# gets reviewed.
#
# It is also the one resource in this file that keeps costing money after
# `terraform destroy` if data was written into it outside Terraform's
# knowledge. The teardown checklist deletes it by name.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "unretained" {
  count = var.create_insecure_examples ? 1 : 0

  name = local.unretained_log_group_name

  tags = {
    Name    = "${local.prefix}-legacy-app"
    Finding = "OBS-001"
  }
}

# ---------------------------------------------------------------------------
# 12b. A log group nobody reads → OBS-002
#
# Retention is set, so it passes the obvious check. But there is no metric
# filter on it, no subscription filter, and no alarm derived from it. Data
# goes in at $0.50/GB and nothing ever comes out.
#
# This is the most common form of expensive theatre in observability: the team
# is definitely logging, the auditor is satisfied, and no signal from this
# group has ever reached a human. Write-only logging is a backup you never
# test — an ingestion bill for the possibility that one day somebody greps it.
#
# The fix is rarely "add a filter". Usually it is "stop logging this".
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "write_only" {
  count = var.create_insecure_examples ? 1 : 0

  name              = "/${local.prefix}/write-only-${local.suffix}"
  retention_in_days = var.log_retention_days

  tags = {
    Name    = "${local.prefix}-write-only"
    Finding = "OBS-002"
  }
}

# ---------------------------------------------------------------------------
# 12c. An alarm with no actions, and no composite referencing it → OBS-004
#
# It evaluates. It transitions. It turns red in a console nobody has open. It
# tells no one, ever.
#
# Compare with the three alarms in section 5, which also have no actions and
# are correct, because a composite alarm references them and that composite
# notifies. OBS-004 has to resolve the composite rules to tell these apart,
# and that is why the check is more than a null test.
#
# The realistic origin of this: someone built the alarm during an incident to
# watch something, meant to wire the topic up afterwards, and did not.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "orphan" {
  count = var.create_insecure_examples ? 1 : 0

  alarm_name        = "${local.prefix}-orphan-no-action-${local.suffix}"
  alarm_description = "DELIBERATELY BROKEN (OBS-004, OBS-005, OBS-006). No actions, default treat_missing_data, and alarms on a raw count."

  namespace   = local.metric_namespace
  metric_name = aws_cloudwatch_log_metric_filter.errors.metric_transformation[0].name
  statistic   = "Sum"
  period      = 300

  comparison_operator = "GreaterThanThreshold"

  # OBS-006: a raw COUNT threshold. Fifty errors means one thing at midday and
  # something entirely different at 03:00. It fires on traffic growth and it
  # goes quiet during an outage that collapses traffic.
  threshold = 50

  # OBS-005: 1 of 1. One unlucky minute pages someone.
  evaluation_periods = 1

  # OBS-005 continued: treat_missing_data is not set at all, so it defaults to
  # "missing". If the metric filter ever stops matching — a field rename in a
  # deploy is enough — this alarm goes INSUFFICIENT_DATA and stays there,
  # grey and quiet, forever.

  tags = {
    Name    = "${local.prefix}-orphan"
    Finding = "OBS-004"
  }
}

# ---------------------------------------------------------------------------
# 12d. A composite alarm that can never fire → OBS-007
#
# `ALARM(x) AND OK(x)`. An alarm cannot be in ALARM and OK simultaneously, so
# this rule is unsatisfiable. CloudWatch accepts it without complaint, creates
# it, bills $0.50/month for it, and displays a comforting green OK.
#
# The version of this that ships in real repositories is subtler: an AND
# across two conditions that never co-occur, written by someone trying to cut
# noise. There is no syntax error to catch it and no test that runs by default.
# The only defence is forcing the children into ALARM by hand and watching
# whether the parent moves — Step 5 of the lab.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_composite_alarm" "impossible" {
  count = var.create_insecure_examples ? 1 : 0

  alarm_name        = "${local.prefix}-impossible-${local.suffix}"
  alarm_description = "DELIBERATELY BROKEN (OBS-007). The rule is unsatisfiable: an alarm cannot be in ALARM and OK at the same time."

  alarm_rule = "ALARM(${aws_cloudwatch_metric_alarm.orphan[0].alarm_name}) AND OK(${aws_cloudwatch_metric_alarm.orphan[0].alarm_name})"

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = {
    Name    = "${local.prefix}-impossible"
    Finding = "OBS-007"
  }
}

# ---------------------------------------------------------------------------
# 12e. A dashboard pointing at a metric that does not exist → OBS-008
#
# CloudWatch dashboards do not validate metric references. A widget naming a
# namespace and metric that were never published renders as an empty graph
# with a legend, which looks exactly like "nothing has happened yet".
#
# That is how a dashboard survives a refactor. Someone renames a metric, the
# widget keeps rendering, the graph is flat, and for four months everybody
# reads the flat line as good news.
#
# The check is mechanical — enumerate the metrics a dashboard references and
# compare against ListMetrics — and it is worth running in CI.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_dashboard" "broken" {
  count = var.create_insecure_examples ? 1 : 0

  dashboard_name = "${local.prefix}-broken-${local.suffix}"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 24
        height = 6
        properties = {
          title  = "Checkout success rate (DELIBERATELY BROKEN — OBS-008)"
          region = local.region
          view   = "timeSeries"
          period = 300
          metrics = [
            # This metric was never published by anything. The widget renders
            # a flat, empty, entirely reassuring graph.
            [local.metric_namespace, "CheckoutSuccessRate", { stat = "Average" }],
            ["CareerByteCode/Day05", "DriftDetected", { stat = "Sum" }],
          ]
        }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# 12f. A metric filter dimension with unbounded cardinality → OBS-003
#
# THE MOST EXPENSIVE SINGLE LINE IN THIS FILE.
#
# `dimensions = { RequestId = "$.request_id" }` looks helpful in review. It
# reads as "break the metric down by request so we can trace it", and the
# person writing it is trying to be thorough.
#
# What it does is create ONE CUSTOM METRIC PER UNIQUE REQUEST ID. At $0.30 per
# metric per month. Forty thousand requests in an afternoon is forty thousand
# custom metrics: $12,000/month.
#
# And you cannot undo it. There is no DeleteMetric API. A custom metric ages
# out fifteen months after its last datapoint and not one day sooner — no
# console button, no support ticket, no exception. You will be paying for this
# mistake into the year after next.
#
# The rule that prevents it, and it is the only rule you need: a dimension
# value must come from a set you could write on a napkin. Status codes, error
# types, environments, regions. Never an ID, never a path, never a user, never
# anything with the word "trace" in it.
#
# Compare with 4c, which has a dimension too, and whose values come from a
# four-element tuple in the source code for exactly this reason.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_metric_filter" "high_cardinality" {
  count = var.create_insecure_examples ? 1 : 0

  name           = "${local.prefix}-per-request"
  log_group_name = aws_cloudwatch_log_group.workload.name
  pattern        = "{ $.event = \"request_completed\" && $.request_id = * }"

  metric_transformation {
    name      = "RequestsByRequestId"
    namespace = local.metric_namespace
    value     = "1"

    dimensions = {
      RequestId = "$.request_id"
    }
  }
}

# ---------------------------------------------------------------------------
# 12g-12i. THE NAIVE ANALYSER → OBS-011, OBS-012, OBS-014, OBS-015
#
# This runs the IDENTICAL zip file as the analyser in section 10. Same Python,
# same handler, same everything. It differs in four environment variables and
# one IAM policy.
#
# That is the point, and it is worth sitting with for a second. Nobody writes
# a bad log summariser. The code in lambda/incident_analyser.py is the same
# code in both functions, and it contains all the right machinery: redaction,
# a token budget, balanced sampling, citation verification. Every one of those
# is switched off here by configuration.
#
# Which is how it happens in production. Somebody sets MAX_INPUT_TOKENS=0
# during a demo because the budget was truncating something interesting.
# Somebody sets REDACT_LOGS=false while debugging why a value looked wrong.
# Somebody widens the IAM policy to "*" because the model ARN would not match
# and the deadline was Friday. None of those changes touch a line of code, so
# none of them get a code review, and all of them are still there a year later.
#
# obs_audit.py finds misconfiguration rather than bad code because
# misconfiguration is what actually ships.
#
# Step 6 of the lab invokes this one AFTER the good one, on the same incident,
# and asks you to compare. trainer-notes.md makes that the second live demo.
# ---------------------------------------------------------------------------

# 12g. bedrock:InvokeModel on Resource "*" → OBS-014
data "aws_iam_policy_document" "naive_analyser" {
  count = var.create_insecure_examples ? 1 : 0

  statement {
    sid    = "InvokeAnyModelAtAll"
    effect = "Allow"

    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]

    # DELIBERATELY BROKEN (OBS-014). This permits any model in the account,
    # including ones nobody evaluated, ones with different data-handling
    # terms, and ones costing forty times more per token. It is what you get
    # when the correctly-scoped ARN did not match and the deadline was Friday.
    resources = ["*"]
  }

  statement {
    effect  = "Allow"
    actions = ["logs:FilterLogEvents", "logs:GetLogEvents"]
    resources = [
      "${aws_cloudwatch_log_group.workload.arn}:*",
    ]
  }

  statement {
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.naive_analyser[0].arn}:*"]
  }

  statement {
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.summaries.arn]
  }
}

resource "aws_iam_role" "naive_analyser" {
  count = var.create_insecure_examples ? 1 : 0

  name               = "${local.prefix}-naive-analyser-${local.suffix}"
  assume_role_policy = data.aws_iam_policy_document.analyser_assume.json

  tags = {
    Name    = "${local.prefix}-naive-analyser"
    Finding = "OBS-014"
  }
}

resource "aws_iam_role_policy" "naive_analyser" {
  count = var.create_insecure_examples ? 1 : 0

  name   = "${local.prefix}-naive-analyser-policy"
  role   = aws_iam_role.naive_analyser[0].id
  policy = data.aws_iam_policy_document.naive_analyser[0].json
}

# 12h. The analyser's own log group, with no retention → OBS-015
#
# The observability tool that is not observable. It is a small irony and a
# real problem: when the summariser starts producing nonsense, its own logs
# are where you find out why, and here they accumulate forever at
# $0.03/GB-month while nobody ever sets a retention on them because nobody
# thinks of the tooling as a workload.
#
# OBS-015 is a specialisation of OBS-001 and OBS-001 deliberately skips the
# log groups OBS-015 owns, so this resource produces ONE finding, not two.
# The finding contract states that explicitly.
resource "aws_cloudwatch_log_group" "naive_analyser" {
  count = var.create_insecure_examples ? 1 : 0

  name = local.naive_log_group_name

  tags = {
    Name    = "${local.prefix}-naive-analyser"
    Finding = "OBS-015"
  }
}

# 12i. No redaction, no token budget, tail-only sampling → OBS-011, OBS-012
resource "aws_lambda_function" "naive_analyser" {
  count = var.create_insecure_examples ? 1 : 0

  function_name = "${local.prefix}-naive-analyser-${local.suffix}"
  role          = aws_iam_role.naive_analyser[0].arn
  handler       = "incident_analyser.handler"
  runtime       = var.chaos_lambda_runtime

  filename         = data.archive_file.analyser.output_path
  source_code_hash = data.archive_file.analyser.output_base64sha256

  memory_size = var.analyser_lambda_memory_mb
  timeout     = var.analyser_lambda_timeout_seconds

  # No reserved_concurrent_executions either. Nothing bounds how many of these
  # can run at once against a per-token API.

  environment {
    variables = {
      WORKLOAD_LOG_GROUP = aws_cloudwatch_log_group.workload.name
      BEDROCK_MODEL_ID   = var.bedrock_model_id
      BEDROCK_REGION     = local.bedrock_region
      SUMMARY_TOPIC_ARN  = aws_sns_topic.summaries.arn

      # OBS-012: no token budget. The prompt is as large as the log window
      # happens to be, and the log window is as large as the incident happens
      # to be. Nothing here is bounded by anything you control.
      MAX_INPUT_TOKENS = "0"

      # OBS-011: raw log text goes into the prompt verbatim. Whatever the
      # application printed — tokens, connection strings, customer data — is
      # now in a request to another service, and in the invocation log if that
      # is enabled, and in nobody's threat model.
      REDACT_LOGS = "false"

      # Not a finding, and the most instructive setting here. Tail-only
      # sampling is not a misconfiguration any auditor can call wrong — it is
      # a defensible engineering choice that happens to discard the cause of
      # every cascade. Step 6 of the lab shows you what it produces.
      SAMPLE_STRATEGY = "tail"

      MAX_LOG_LINES       = tostring(var.analyser_max_log_lines)
      LOOKBACK_MINUTES    = tostring(var.analyser_lookback_minutes)
      IDEMPOTENCY_MINUTES = "0"
      IDEMPOTENCY_TABLE   = ""
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.naive_analyser,
    aws_iam_role_policy.naive_analyser,
  ]

  tags = {
    Name    = "${local.prefix}-naive-analyser"
    Finding = "OBS-011"
  }
}
