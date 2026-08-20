###############################################################################
# Day 06 — variables.tf
#
# Repo convention: any variable that costs money says so in its description,
# with the actual figure.
#
# Day 06 breaks a pattern the first five days established. On Days 01-05 the
# bill was a function of what EXISTS — an instance, a NAT gateway, a KMS key.
# You could look at the resource list and know the number. Day 06's bill is a
# function of what HAPPENS: how many log lines you ingest, how many custom
# metrics you create, and how many tokens you send to a model.
#
# Nothing in the Terraform tells you how chatty your application is. That is
# why three variables below carry a SILENT COST GROWTH GUARD marker, and why
# the teardown checklist for this day is longer than the ones before it.
###############################################################################

###############################################################################
# Identity & region
###############################################################################

variable "aws_region" {
  description = "AWS region for all Day 06 resources. Keep this the same as the region your Bedrock model is enabled in — log data crossing a region boundary to reach a model is check OBS-013, and it is a compliance question before it is a latency one."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must be a valid region string, e.g. us-east-1."
  }
}

variable "aws_profile" {
  description = "Named AWS CLI profile used to authenticate. Day 01 created this."
  type        = string
  default     = "bootcamp"

  validation {
    condition     = length(var.aws_profile) > 0
    error_message = "aws_profile cannot be empty. Run `aws configure --profile bootcamp` first."
  }
}

variable "owner" {
  description = "Value for the Owner tag. Use your name or team so account-wide cost reports can attribute this spend to you."
  type        = string
  default     = "bootcamp-student"

  validation {
    condition     = length(var.owner) >= 2 && length(var.owner) <= 64
    error_message = "owner must be between 2 and 64 characters."
  }
}

###############################################################################
# Notification — the one variable you MUST set
###############################################################################

variable "notification_email" {
  description = <<-DESC
    Email address that receives alarm notifications and incident summaries.

    This is the only variable with no usable default. Set it in
    terraform.tfvars before you apply.

    As on Day 04: AWS sends a confirmation email the moment the subscription is
    created and the subscription stays in "PendingConfirmation" until you click
    the link. An unconfirmed subscription silently drops every message.

    That failure mode is worth noticing twice, because on Day 06 it is not just
    an inconvenience — an alarm whose only action is an unconfirmed SNS
    subscription is an alarm that fires into nothing. It goes to ALARM, the
    console turns red, the history records the transition, and no human is
    told. Check OBS-004 finds alarms with no action at all; nothing in
    CloudWatch can find this one for you.
  DESC
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.notification_email))
    error_message = "notification_email must be a valid email address, e.g. you@example.com."
  }
}

###############################################################################
# Logging — where most of this day's money actually goes
###############################################################################

variable "log_retention_days" {
  description = <<-DESC
    ⚠️ SILENT COST GROWTH GUARD.

    Retention for every log group this stack creates deliberately.

    The AWS default when a log group is created for you — by Lambda, by ECS, by
    API Gateway, by anything — is "Never expire". Ingestion is $0.50/GB once,
    and storage is $0.03/GB-month FOREVER, for data nobody will read after
    Thursday.

    The arithmetic that catches people: a service logging 1 GB/day with no
    retention costs $15/month in ingestion and, after a year, $110/month in
    storage that is still growing. Nobody notices, because log groups do not
    appear in the console unless you go looking for them, and Cost Explorer
    aggregates them all into one "CloudWatch" line.

    7 days is right for this lab — you will generate the incident, analyse it,
    and destroy the stack the same afternoon. 30-90 is typical in production.
    Anything longer belongs in S3 through a subscription filter at $0.023/GB-
    month, not in CloudWatch Logs at $0.03/GB-month with no query engine that
    can afford to scan it.

    Missing retention is check OBS-001, the highest-frequency finding this
    auditor produces in a real account.
  DESC
  type        = number
  default     = 7

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_retention_days)
    error_message = "log_retention_days must be one of the values CloudWatch Logs accepts: 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653."
  }
}

variable "log_group_class" {
  description = <<-DESC
    COST-BEARING, in the direction people do not expect.

    STANDARD          $0.50/GB ingestion. All features.
    INFREQUENT_ACCESS $0.25/GB ingestion. Half price, and you lose things.

    What Infrequent Access takes away is the entire subject of this day:
      * no metric filters
      * no subscription filters
      * no alarms derived from log data
      * no Live Tail
    You keep storage, retrieval, and Logs Insights queries.

    So IA is correct for the audit trail you must keep and will read twice a
    year, and wrong for anything you alarm on. Choosing it for a log group that
    later needs a metric filter means creating a new log group — the class
    cannot be changed after creation, only set at create time.

    Leave this STANDARD. The workload log group in this lab has three metric
    filters on it and would silently lose all of them.
  DESC
  type        = string
  default     = "STANDARD"

  validation {
    condition     = contains(["STANDARD", "INFREQUENT_ACCESS"], var.log_group_class)
    error_message = "log_group_class must be STANDARD or INFREQUENT_ACCESS."
  }
}

###############################################################################
# Alarm shape — the part everyone gets wrong
###############################################################################

variable "alarm_period_seconds" {
  description = <<-DESC
    Evaluation period for the alarms in this stack.

    60 is standard resolution and free-tier friendly. Do not reach for 10 or 30
    ("high resolution") without a reason:

      * Standard-resolution alarm: $0.10/alarm-month, first 10 free.
      * High-resolution alarm:     $0.30/alarm-month, NOT covered by the free
                                   ten.

    High resolution buys you a faster page and a much noisier one. At a 10-
    second period, normal variance in a low-traffic service crosses almost any
    threshold you can name. If your reaction to that is to widen the threshold,
    you have paid triple for an alarm that now fires later than the 60-second
    one would have.

    Reach for high resolution when the thing you are protecting genuinely
    degrades in under a minute and you have an automated response ready. Not
    because faster feels better.
  DESC
  type        = number
  default     = 60

  validation {
    condition     = contains([10, 30, 60, 120, 300, 900], var.alarm_period_seconds)
    error_message = "alarm_period_seconds must be one of 10, 30, 60, 120, 300, 900. Values below 60 are high-resolution and are billed at $0.30/alarm-month with no free tier."
  }
}

variable "alarm_evaluation_periods" {
  description = <<-DESC
    The N in "M out of N datapoints".

    CloudWatch looks back over this many periods each time it evaluates. With
    the default period of 60 seconds and 5 evaluation periods, the alarm is
    reasoning about the last five minutes.

    Paired with alarm_datapoints_to_alarm (the M) below. The pair is the single
    most effective noise control in CloudWatch and the most commonly left at
    1-of-1, which is how you end up with an alarm that fires on one unlucky
    minute at 03:12 and resolves itself before you have found your laptop.
  DESC
  type        = number
  default     = 5

  validation {
    condition     = var.alarm_evaluation_periods >= 1 && var.alarm_evaluation_periods <= 288
    error_message = "alarm_evaluation_periods must be between 1 and 288."
  }
}

variable "alarm_datapoints_to_alarm" {
  description = <<-DESC
    The M in "M out of N datapoints".

    3 of 5 means: over the last five minutes, at least three individual minutes
    breached. A single bad minute does not page anyone; a genuinely degraded
    five minutes does.

    Why not simply average over five minutes instead? Because averaging hides
    the shape. Three catastrophic minutes and two perfect ones average to
    "somewhat elevated" and may not cross a threshold at all. M-of-N sees the
    three bad minutes for what they are.

    Must be <= alarm_evaluation_periods, and the validation below enforces it,
    because CloudWatch rejects the API call with a message that does not
    obviously say so.
  DESC
  type        = number
  default     = 3

  validation {
    condition     = var.alarm_datapoints_to_alarm >= 1 && var.alarm_datapoints_to_alarm <= 288
    error_message = "alarm_datapoints_to_alarm must be between 1 and 288, and no greater than alarm_evaluation_periods."
  }
}

variable "error_rate_threshold_percent" {
  description = <<-DESC
    Threshold for the error RATE alarm, as a percentage of total requests.

    Rate, not count. This is the single most transferable idea in the day.

    An alarm on "more than 50 errors in 5 minutes" means one thing at 3am and
    something completely different at midday. Traffic doubles; the alarm fires;
    nothing is wrong. Traffic collapses because the load balancer is unhealthy;
    errors fall below 50; the alarm goes quiet during your worst outage.

    5% of requests failing is 5% of requests failing at any hour, and the
    number means the same thing to the person reading the page as it did to the
    person who set it.

    Section 5 of main.tf builds this with a metric MATH expression over two
    metric filters, which is the mechanism you need for any ratio.
  DESC
  type        = number
  default     = 5

  validation {
    condition     = var.error_rate_threshold_percent > 0 && var.error_rate_threshold_percent <= 100
    error_message = "error_rate_threshold_percent must be greater than 0 and at most 100."
  }
}

variable "latency_p95_threshold_ms" {
  description = <<-DESC
    Threshold for the latency alarm, in milliseconds, evaluated at p95.

    p95, not average. An average latency of 200ms is perfectly consistent with
    one request in twenty taking nine seconds, and the customers who churn are
    always in the tail. CloudWatch supports p50/p90/p95/p99 as an extended
    statistic on any metric with a full statistic set — which the metric filter
    in section 4 produces because it extracts a VALUE, not just a count.

    2000ms is deliberately generous so the lab's chaos Lambda has to work to
    breach it.
  DESC
  type        = number
  default     = 2000

  validation {
    condition     = var.latency_p95_threshold_ms > 0
    error_message = "latency_p95_threshold_ms must be greater than 0."
  }
}

###############################################################################
# Dashboard
###############################################################################

variable "create_dashboard" {
  description = <<-DESC
    COST-BEARING ($3.00/month per dashboard beyond the first 3; this stack
    creates 1, so on a fresh account it is free).

    Creates the operational dashboard described in section 7.

    Leave it on. Then read the comment above the resource, because the argument
    that dashboard makes is mostly an argument against dashboards: a dashboard
    nobody opens is not observability, it is a screensaver with a bill. The
    dashboard here exists to answer three specific questions during the lab's
    incident, and section 7 says which three.
  DESC
  type        = bool
  default     = true
}

###############################################################################
# The workload that fails on demand
###############################################################################

variable "chaos_lambda_runtime" {
  description = "Python runtime for the chaos workload. Keep it current; an out-of-support runtime eventually stops being updatable at all."
  type        = string
  default     = "python3.12"

  validation {
    condition     = can(regex("^python3\\.(9|10|11|12|13)$", var.chaos_lambda_runtime))
    error_message = "chaos_lambda_runtime must be a supported Python runtime, e.g. python3.12."
  }
}

variable "chaos_lambda_memory_mb" {
  description = "Memory for the chaos workload. It writes log lines and sleeps; 128 MB is plenty. Free tier covers 400,000 GB-seconds/month permanently."
  type        = number
  default     = 128

  validation {
    condition     = var.chaos_lambda_memory_mb >= 128 && var.chaos_lambda_memory_mb <= 3008
    error_message = "chaos_lambda_memory_mb must be between 128 and 3008 MB."
  }
}

variable "chaos_lambda_timeout_seconds" {
  description = "Timeout for the chaos workload. It emits a burst and returns; 120 seconds is generous. You are billed for duration used, never for the timeout you set."
  type        = number
  default     = 120

  validation {
    condition     = var.chaos_lambda_timeout_seconds >= 3 && var.chaos_lambda_timeout_seconds <= 900
    error_message = "chaos_lambda_timeout_seconds must be between 3 and 900."
  }
}

variable "chaos_default_burst_lines" {
  description = <<-DESC
    ⚠️ SILENT COST GROWTH GUARD.

    How many log lines the chaos workload emits per invocation when the event
    does not say otherwise.

    400 lines at roughly 275 bytes each is about 110 KB per invocation. You
    could invoke it forty thousand times and still be inside the 5 GB/month
    free ingestion tier.

    The guard exists because the obvious experiment — "what does the analyser
    do with a really big incident?" — is one zero away from being expensive.
    4,000,000 lines is about 1.1 GB, which is $0.55 of ingestion, and then
    the analyser tries to read it, which is section 10's problem and a much
    larger number. The lab's Step 6 has you do the interesting version of that
    experiment on purpose, with a token budget in place.
  DESC
  type        = number
  default     = 400

  validation {
    condition     = var.chaos_default_burst_lines >= 10 && var.chaos_default_burst_lines <= 20000
    error_message = "chaos_default_burst_lines must be between 10 and 20000. Above 20000 you are paying to learn something the lab teaches for free."
  }
}

###############################################################################
# The deliberately-broken examples
###############################################################################

variable "create_insecure_examples" {
  description = <<-DESC
    FREE at rest, with one exception noted below.

    Creates deliberately misconfigured observability so obs_audit.py has real
    findings to report. This is the same pattern as Day 04's broken function
    and Day 05's bad-examples/ directory.

    What it creates is listed in main.tf section 12. The short version:

      * a log group with NO retention                → OBS-001
      * two log groups nothing ever reads            → OBS-002 (fires twice)
      * a metric filter with a request-ID dimension  → OBS-003
      * an alarm with no actions, at the default
        treat_missing_data, on a raw count, over a
        single datapoint                             → OBS-004/005/006/010
      * a composite alarm that can never fire        → OBS-007
      * a dashboard widget pointing at a metric that
        does not exist                               → OBS-008
      * the SAME analyser zip deployed with no
        redaction, no token budget, tail-only
        sampling, bedrock:InvokeModel on "*", and no
        retention on its log group                   → OBS-011/012/014/015

    The exception to "free": the unretained log group is the one resource here
    that keeps costing money after `terraform destroy`, because the lab
    deliberately has data written into it. It is a few kilobytes and it is
    still the point. The teardown checklist deletes it by name.

    Set to false for a clean reference build — Step 9 of the lab has you do
    exactly that and re-run the auditor. Set to true (the default) for the
    teaching experience: exactly 15 findings, 144 points, a compliance score
    of 0/100, and a set of mistakes you will recognise in every real account
    you ever audit. The full breakdown is the finding contract, quoted in the
    outputs, both READMEs, obs_audit.py and its tests.
  DESC
  type        = bool
  default     = true
}

###############################################################################
# The AI half — Amazon Bedrock
#
# Every variable in this block is a cost or a data-governance decision. There
# is no "just leave the defaults" option here that is purely technical.
###############################################################################

variable "bedrock_model_id" {
  description = <<-DESC
    Bedrock model the analyser invokes.

    Haiku is the right default for this job and it is not a compromise. Log
    summarisation is a reading task on a large input with a small, structured
    output — exactly the shape where the cheap fast model is nearly as good and
    roughly five times cheaper. Reach for a larger model when the task needs
    reasoning ACROSS the evidence, not when it needs more of it read.

    Indicative us-east-1 on-demand pricing at the time of writing, per 1,000
    tokens. VERIFY THESE — model prices move and new models land monthly:

      Claude 3.5 Haiku    ~$0.0008 in / ~$0.004 out
      Claude 3.5 Sonnet   ~$0.003  in / ~$0.015 out

    You must explicitly request access to a model in the Bedrock console
    before any of this works. An un-requested model returns AccessDenied and
    the message does not say "go and click the button", which is the single
    most common first failure on this day.
  DESC
  type        = string
  default     = "anthropic.claude-3-5-haiku-20241022-v1:0"

  validation {
    condition     = length(var.bedrock_model_id) > 0
    error_message = "bedrock_model_id cannot be empty."
  }
}

variable "bedrock_region" {
  description = <<-DESC
    Region the model is invoked in. Empty string (the default) means "the same
    region as everything else", which is the only value that does not require
    a conversation with someone.

    Set it to a different region and you have built check OBS-013: log data —
    which contains, routinely, whatever your application happened to print —
    leaves the region it was collected in. That is a data-residency question
    before it is a latency one, and the honest version of the answer is that
    most people who did this did not know they had.

    Step 7 of the lab sets this deliberately so you can watch OBS-013 fire.
    That is the ONLY way this check fires against this stack, which is what
    "silent by design" means in the finding contract.
  DESC
  type        = string
  default     = ""

  validation {
    condition     = var.bedrock_region == "" || can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]$", var.bedrock_region))
    error_message = "bedrock_region must be empty (meaning: same as aws_region) or a valid region string."
  }
}

variable "analyser_max_input_tokens" {
  description = <<-DESC
    ⚠️ SILENT COST GROWTH GUARD. This is the most important number on the day.

    Hard ceiling on the tokens of log data sent to the model in one
    invocation. The analyser samples down to fit and reports how much it
    dropped, so the reader always knows the summary is based on a sample.

    Why a ceiling at all, when modern context windows are enormous? Because
    "the model can take 200,000 tokens" and "you should send it 200,000
    tokens" are different claims, and only the first one is true.

      COST     At ~$0.0008/1K input, one 200,000-token invocation is $0.16.
               Wire that to an alarm that flaps forty times an hour overnight
               and you have spent $77 by breakfast, on a system that is not
               broken.

      LATENCY  Time-to-first-token scales with input. A summary that arrives
               after the incident is over is a postmortem, not a tool.

      QUALITY  This is the one people do not expect. Recall degrades over a
               very long context, and a specific line in the middle of
               180,000 tokens is genuinely harder for the model to use than
               the same line in 12,000. Sending everything makes the answer
               worse, not just slower.

    12,000 tokens is roughly 48,000 characters, roughly 175 log lines at this
    stack's average line length. That is enough to hold a whole incident when
    it is sampled properly — which is the analyser's actual job, and why
    incident_analyser.py samples HEAD + TAIL + STRATIFIED rather than
    truncating to the last N lines. Read that function's comments; tail-only
    truncation is how the demo in trainer-notes.md produces a confident,
    fluent, completely wrong answer.

    Set to 0 to disable the budget entirely. That is check OBS-012 and the
    lab never asks you to do it.
  DESC
  type        = number
  default     = 12000

  validation {
    condition     = var.analyser_max_input_tokens == 0 || (var.analyser_max_input_tokens >= 1000 && var.analyser_max_input_tokens <= 150000)
    error_message = "analyser_max_input_tokens must be 0 (no budget — OBS-012) or between 1000 and 150000."
  }
}

variable "analyser_max_log_lines" {
  description = "Ceiling on log lines fetched from CloudWatch before sampling. A second, cruder guard in front of the token budget: it bounds the Logs Insights bill ($0.005/GB scanned) and the analyser's own memory, neither of which the token budget protects."
  type        = number
  default     = 600

  validation {
    condition     = var.analyser_max_log_lines >= 50 && var.analyser_max_log_lines <= 10000
    error_message = "analyser_max_log_lines must be between 50 and 10000."
  }
}

variable "analyser_lookback_minutes" {
  description = <<-DESC
    How far back from the alarm transition the analyser reads.

    Longer is not better. The cause of an incident is usually within a few
    minutes of the first symptom, and every extra minute of window is more
    tokens spent on healthy traffic that dilutes the evidence.

    30 minutes is right here because the lab's cascade runs over 12. In
    production, match this to your deploy frequency: the window has to be long
    enough to contain the change that caused the incident, because that change
    is what you are actually looking for.
  DESC
  type        = number
  default     = 30

  validation {
    condition     = var.analyser_lookback_minutes >= 5 && var.analyser_lookback_minutes <= 1440
    error_message = "analyser_lookback_minutes must be between 5 and 1440 (24 hours)."
  }
}

variable "analyser_redact_logs" {
  description = <<-DESC
    Whether the analyser redacts obvious secrets and PII from log text before
    putting it in a prompt.

    Leave this true, and do not mistake it for a solution.

    Redaction is a regex pass over data you did not control the shape of. It
    catches AWS access key IDs, bearer tokens, JWTs, connection strings,
    email addresses and card-shaped digit runs. It will not catch a customer's
    full name in a free-text field, a session identifier your framework
    invented, or a stack trace with local variables still in it.

    The real control is upstream: do not log the secret. Redaction is the
    seatbelt, not the brakes.

    What you are actually deciding here is where log content goes and who can
    read it. With this true, a redacted sample goes to Bedrock in whichever
    region bedrock_region resolves to and, if invocation logging is on, a copy
    of the whole prompt lands in a CloudWatch log group in your account that
    inherits whatever access controls that group has. Say that out loud before
    you turn invocation logging on.

    Set false and you have built check OBS-011.
  DESC
  type        = bool
  default     = true
}

variable "enable_bedrock_invocation_logging" {
  description = <<-DESC
    Whether to enable Bedrock model invocation logging for the whole account.

    Default FALSE, and the default is not laziness — it is the honest position
    that turning this on is a decision, not a best practice you apply blindly.

    ON:  every prompt and every completion is written to a CloudWatch log
         group. You can audit what was sent, reproduce a bad summary, and
         answer "what did the model actually see". You need this the first
         time a summary is confidently wrong.

    ALSO ON: every prompt and every completion is written to a CloudWatch log
         group. That group now contains the log text you were careful about,
         in full, readable by anyone with CloudWatch read access — which in
         most accounts is a much wider group of people than those who can read
         the original application logs.

    So it fixes an audit problem by creating a data-access problem. Enable it,
    and then go and set retention on the destination group and put a resource
    policy on it. Both halves, or neither.

    Note this is an ACCOUNT-LEVEL, region-singleton setting. Terraform will
    fight another stack that also manages it, and `terraform destroy` turns it
    off for everything in the region, not just this lab.

    With it false, check OBS-016 fires. That is correct and intended: the
    check is not "you did something stupid", it is "nothing here can tell you
    what you sent to the model".
  DESC
  type        = bool
  default     = false
}

variable "enable_auto_analysis" {
  description = <<-DESC
    ⚠️ SILENT COST GROWTH GUARD.

    Whether the EventBridge rule that invokes the analyser on alarm state
    change is ENABLED.

    This is the switch that turns a $1/month stack into a four-figure weekend.
    The alarm goes to ALARM, EventBridge invokes the analyser, the analyser
    sends a window of logs to a model priced per token. Now put that behind an
    alarm with datapoints_to_alarm = 1 on a noisy metric: it transitions dozens
    of times an hour, all night, and every transition is a paid invocation.

    Nothing about that looks broken. No dashboard turns red. The alarm is
    doing exactly what you asked.

    Three guards, and you want all three:
      1. M-of-N on the triggering alarm (section 5).
      2. A hard token budget (analyser_max_input_tokens).
      3. An idempotency window (analyser_idempotency_minutes) so the same
         incident is summarised once, not once per flap.

    Set false to run the analyser only by hand, which is how Step 6 of the lab
    starts. A DISABLED rule that everyone believes is enabled is its own
    outage — Day 04's CMP-014 — so if you turn it off, say so somewhere a
    human will read.
  DESC
  type        = bool
  default     = true
}

variable "analyser_idempotency_minutes" {
  description = "How long after summarising an alarm the analyser refuses to summarise the same alarm again. Bounds the cost of a flapping alarm to one invocation per window. 15 minutes is comfortable for a lab; match it to your alarm's evaluation window in production. Set to 0 to disable, which the lab never asks you to do."
  type        = number
  default     = 15

  validation {
    condition     = var.analyser_idempotency_minutes >= 0 && var.analyser_idempotency_minutes <= 1440
    error_message = "analyser_idempotency_minutes must be between 0 and 1440."
  }
}

variable "analyser_lambda_memory_mb" {
  description = "Memory for the analyser. CPU scales with memory and this function spends most of its life waiting on two network calls, but Logs Insights results and the JSON parsing want headroom. 512 MB is comfortable and stays inside the permanent free tier at lab volumes."
  type        = number
  default     = 512

  validation {
    condition     = var.analyser_lambda_memory_mb >= 128 && var.analyser_lambda_memory_mb <= 3008
    error_message = "analyser_lambda_memory_mb must be between 128 and 3008 MB."
  }
}

variable "analyser_lambda_timeout_seconds" {
  description = "Timeout for the analyser. A Logs Insights query can take 10-30 seconds and a model invocation on a large prompt can take another 20, so the AWS default of 3 seconds is nowhere near enough. You are billed for duration used, never for the timeout you set."
  type        = number
  default     = 180

  validation {
    condition     = var.analyser_lambda_timeout_seconds >= 30 && var.analyser_lambda_timeout_seconds <= 900
    error_message = "analyser_lambda_timeout_seconds must be between 30 and 900. Below 30 the Logs Insights query alone will time out."
  }
}
