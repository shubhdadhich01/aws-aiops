# Day 04 — Serverless Automation

**CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp**

> **Enterprise scenario**
> Routine operational toil is eating engineer time. Somebody runs a compliance
> check by hand every Monday, notices half of it, and forgets the other half.
> The checks and the reactions to events should run themselves, serverlessly,
> and tell the team when something is wrong.

Today you build an **Automated Resource Compliance Scanner**: a Lambda function
that runs on a schedule *and* reacts to CloudTrail events in near-real time,
publishes findings to SNS, captures its own failures in a dead letter queue,
and is watched by CloudWatch alarms. Then you audit it — and everything else
serverless in the account — with a Python tool you write yourself.

| | |
|---|---|
| **Level** | Intermediate |
| **Stack** | Terraform + Python (boto3) + AI |
| **Cost** | ~$1.01/month, $1.00 of which is one optional KMS key |
| **Time** | 3h 15m taught · ~2h self-paced |
| **Region** | `us-east-1` · profile `bootcamp` · prefix `cbc-day04-` |

---

## Table of contents

1. [Learning objectives](#learning-objectives)
2. [Prerequisites](#prerequisites)
3. [Architecture](#architecture)
4. [Part 1 — Lambda: the parts that are not the code](#part-1--lambda-the-parts-that-are-not-the-code)
5. [Part 2 — EventBridge: scheduled and reactive](#part-2--eventbridge-scheduled-and-reactive)
6. [Part 3 — SNS, SQS and where failure goes](#part-3--sns-sqs-and-where-failure-goes)
7. [Part 4 — Compliance as code](#part-4--compliance-as-code)
8. [The mistakes people actually make](#the-mistakes-people-actually-make)
9. [Cost — read this before you apply](#cost--read-this-before-you-apply)
10. [Lab](#lab)
11. [Day 04 checklist](#day-04-checklist)

---

## Learning objectives

By the end of today you can:

1. Write a Lambda function that serves **two invocation models** — a scheduled
   full sweep and a reactive single-resource check — from one handler.
2. Explain the difference between **synchronous, asynchronous and stream**
   invocation, and why only one of them has a dead letter queue.
3. Wire **EventBridge** to Lambda both ways: `rate()`/`cron()` schedules and
   CloudTrail-backed event patterns, including the resource-based permission
   that everyone forgets.
4. Configure **failure handling that leaves evidence**: DLQ, destinations,
   retry policy on the target, and alarms on all of it.
5. Recognise the two **silent cost-growth traps** in serverless — log groups
   with no retention, and recursive invocation — and design them out.
6. Build `serverless_audit.py`: 16 compliance checks, severity-weighted
   scoring, three output formats, 47 unit tests.

---

## Prerequisites

- Day 01 complete: `bootcamp` profile, budget alarm, least-privilege IAM.
- Day 03 complete, or at least read: today's auditor is the same shape as
  `ha_audit.py` and assumes you have seen the Finding/score/CLI pattern once.
- Python 3.9+ and `pip install -r lab/python/requirements.txt`.
- Terraform 1.5+ (or OpenTofu 1.8+).
- **A real email address** for `notification_email`. You will have to click a
  confirmation link, and until you do, nothing in your alerting pipeline works.

---

## Architecture

```
                    ┌───────────────────────────────┐
                    │  EventBridge (proactive)      │
   rate(1 hour) ───►│  cbc-day04-scheduled-scan     │──┐
                    └───────────────────────────────┘  │
                                                       │  async invoke
                    ┌───────────────────────────────┐  │
   API calls ──────►│  CloudTrail  ──►  EventBridge │  │
   (RunInstances,   │  cbc-day04-reactive-scan      │──┤
    CreateBucket…)  └───────────────────────────────┘  │
                                                       ▼
                          ┌──────────────────────────────────────┐
                          │  Lambda cbc-day04-compliance-scanner │
                          │  python3.12 · 60s · reserved 2       │
                          │  X-Ray Active · env vars KMS-encrypted│
                          └───────┬───────────────┬──────────────┘
                                  │               │  on failure
                       findings   │               ▼
                                  │        ┌──────────────────────┐
                                  │        │ SQS cbc-day04-       │
                                  │        │ scanner-dlq (14 days)│
                                  ▼        └──────────┬───────────┘
                          ┌──────────────┐            │
                          │ SNS          │            ▼
                          │ cbc-day04-   │     ┌──────────────────┐
                          │ findings     │     │ CloudWatch alarm │
                          └──────┬───────┘     │ DLQ not empty    │
                                 ▼             └──────────────────┘
                              your inbox
```

Twelve sections in `lab/terraform/main.tf`, 46 resources:

| § | What | Why it is there |
|---|------|-----------------|
| 1 | Data sources, locals | account ID, region, random suffix |
| 2 | KMS key + alias | the entire Day 04 bill, and optional |
| 3 | SNS topic, subscription, policy | where findings go |
| 4 | SQS dead letter queue + policy | where failures go |
| 5 | IAM role and four policies | least privilege, split by concern |
| 6 | CloudWatch log group | retention set **before** the function exists |
| 7 | Lambda function + invoke config | the scanner |
| 8 | EventBridge scheduled rule | the proactive path |
| 9 | EventBridge reactive rule | the CloudTrail-driven path |
| 10 | CloudTrail + S3 bucket | **section 9 cannot work without this** |
| 11 | CloudWatch alarms | errors, and DLQ depth |
| 12 | Deliberately broken examples | 14 findings for the auditor |

### Why section 10 exists

The reactive rule matches CloudTrail management events. Here is the part that
surprises people: **EventBridge does not receive API activity unless a
CloudTrail trail exists in the account.** The default event bus gets
`AWS API Call via CloudTrail` events only when a trail is delivering
management events. No trail, no events, and your rule sits there looking
perfectly correct and firing never.

The first trail delivering management events to S3 is free. That is why the
lab creates exactly one.

---

## Part 1 — Lambda: the parts that are not the code

Most Lambda tutorials are about the handler. Almost every Lambda *incident* is
about the configuration around it.

### Three invocation models, three failure behaviours

| Model | Who calls it | On failure |
|---|---|---|
| **Synchronous** | API Gateway, `lambda invoke`, ALB | error returned to caller; caller decides |
| **Asynchronous** | EventBridge, SNS, S3 notifications | retried twice, then **discarded** unless you configured somewhere for it to go |
| **Stream / poll** | SQS, Kinesis, DynamoDB Streams | retried until the record expires; blocks the shard/queue |

Today is entirely asynchronous, which is why so much of the configuration is
about failure. There is no caller waiting to be told. If you do not capture
the event, the only evidence work was attempted is an `Errors` metric ticking
up by one, and you cannot replay what you did not keep.

### Dead letter queue vs destinations

Two mechanisms, both valid, and `serverless_audit.py` accepts either:

```hcl
# Older, simpler. Event body only.
dead_letter_config {
  target_arn = aws_sqs_queue.dlq.arn
}

# Newer. Carries the event AND the response/error, and can route
# success separately from failure.
resource "aws_lambda_function_event_invoke_config" "scanner" {
  function_name          = aws_lambda_function.scanner.function_name
  maximum_retry_attempts = 1
  destination_config {
    on_failure { destination = aws_sqs_queue.dlq.arn }
  }
}
```

The stack uses both, deliberately, so you can see the difference in the DLQ
message shape when you force a failure in Step 5.

> **The permission nobody adds**: the execution role needs `sqs:SendMessage`
> on the DLQ. Without it, delivery to the DLQ fails silently and the event is
> lost anyway — the same problem, one layer down.

### The five configuration settings that matter

| Setting | Default | What the default costs you | Check |
|---|---|---|---|
| `timeout` | **3 seconds** | timeouts with no exception and no stack trace | CMP-006 |
| `reserved_concurrent_executions` | unreserved | a runaway loop scales to 1,000 copies | CMP-007 |
| `dead_letter_config` | none | failed events discarded | CMP-001 |
| `tracing_config` | PassThrough | no trace, ever, for event-driven functions | CMP-009 |
| `kms_key_arn` | service key | no key policy, no Decrypt trail, no revocation | CMP-003 |

A timeout deserves a second look because the reasoning is usually backwards.
People run tight timeouts to save money. **You are billed for duration
actually used, not for the timeout.** A generous timeout costs nothing and
only bounds your worst case. Running 3 seconds against a function that makes
API calls buys you nothing and costs you a pager alert that is very hard to
diagnose — the log just stops mid-sentence.

### Environment variables are not a secret store

Lambda encrypts environment variables at rest. That is not the point. Anyone
with `lambda:GetFunctionConfiguration` — which `ReadOnlyAccess` grants — reads
them back in plaintext, and they appear in the console, in `terraform show`,
and in your state file.

```python
# Wrong
API_KEY = os.environ["API_KEY"]

# Right: the pointer is in the environment, the secret is not.
import boto3, os, json
_cache = {}
def get_secret():
    if "v" not in _cache:
        arn = os.environ["SECRET_ARN"]
        raw = boto3.client("secretsmanager").get_secret_value(SecretId=arn)
        _cache["v"] = json.loads(raw["SecretString"])
    return _cache["v"]      # module-global cache survives warm invocations
```

`cbc-day04-broken-function` does it the wrong way on purpose. Read the values
out of it yourself in Step 5 with nothing but a read-only policy, and the
lesson lands harder than any slide:

```bash
aws lambda get-function-configuration \
  --function-name $(terraform output -raw broken_function_name) \
  --query 'Environment.Variables' --profile bootcamp
```

---

## Part 2 — EventBridge: scheduled and reactive

### The proactive path

```hcl
resource "aws_cloudwatch_event_rule" "scheduled_scan" {
  name                = "cbc-day04-scheduled-scan"
  schedule_expression = "rate(1 hour)"      # or cron(0 9 ? * MON-FRI *)
  state               = "ENABLED"
}
```

`rate()` is relative to when the rule was created and is fine for "every N".
`cron()` is UTC — always, with no timezone option on classic rules — and the
day-of-week field uses `?` where day-of-month is specified, which is the
single most common syntax error in EventBridge.

### The reactive path

```hcl
event_pattern = jsonencode({
  source      = ["aws.ec2", "aws.s3", "aws.iam"]
  detail-type = ["AWS API Call via CloudTrail"]
  detail = {
    eventName = ["RunInstances", "CreateBucket", "CreateUser"]
  }
})
```

Event patterns match by **presence and value**. A field you do not mention is
not matched against — that is why a pattern with a typo in `eventName` matches
nothing and reports no error. Test patterns before you deploy them:

```bash
aws events test-event-pattern \
  --event-pattern file://pattern.json \
  --event file://sample-event.json
```

### Three things that must all be true for a rule to work

1. The rule is **ENABLED**. A disabled rule is not an error, it is a
   configuration — and it produces silence, which looks exactly like success.
   (CMP-014. This is the outage that survives a code review, an architecture
   review and a screenshot in the runbook.)
2. The target has a **retry policy and a DLQ**, or delivery failures vanish
   into a `FailedInvocations` metric with no payload. (CMP-015.)
3. Lambda has a **resource-based policy** allowing `events.amazonaws.com` to
   invoke it, scoped by `source_arn`:

```hcl
resource "aws_lambda_permission" "allow_schedule" {
  statement_id  = "AllowExecutionFromEventBridgeSchedule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scanner.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.scheduled_scan.arn   # scope it
}
```

The execution role is what the function **can do**. The resource policy is
**who may call it**. Confusing the two produces a rule that fires forever with
no visible effect and no error anywhere obvious.

> Leave off `source_arn` and any EventBridge rule in any account can invoke
> your function. That is CMP-016 territory.

### Dual-mode handlers

One function, two event shapes. `compliance_scanner.py` branches on the
payload:

```python
def lambda_handler(event, context):
    if event.get("scan_type") == "scheduled-full-sweep":
        return full_sweep()                    # scheduled: audit everything
    if event.get("detail-type") == "AWS API Call via CloudTrail":
        return check_one(event["detail"])      # reactive: audit what changed
    return full_sweep()                        # manual `lambda invoke`
```

Why one function and not two: the checks are identical, and two copies of
compliance logic drift within a quarter. The cost is a slightly branchy
handler, which is a cost worth paying.

---

## Part 3 — SNS, SQS and where failure goes

### The subscription that is not a subscription

An email subscription is **not active** when Terraform reports success. AWS
sends a confirmation link and the subscription sits in `PendingConfirmation`
until a human clicks it. Until then, **every publish succeeds and every
message is silently discarded.**

Terraform cannot confirm it for you and will never show it as a problem. This
is the reason your first "working" alerting pipeline in production alerts
nobody. Check it:

```bash
aws sns list-subscriptions-by-topic \
  --topic-arn $(terraform output -raw sns_topic_arn) \
  --query 'Subscriptions[].SubscriptionArn' --profile bootcamp
# "PendingConfirmation" is a failure state wearing a neutral name.
```

### Topic policies and the wildcard principal

`Principal: "*"` with no condition means every AWS principal on Earth may
publish. On a topic with an email subscription that is a spam relay wearing
your alerting pipeline's name. The correct form keeps the wildcard and narrows
it:

```hcl
Condition = {
  StringEquals = { "AWS:SourceAccount" = local.account_id }
}
```

The stack's own findings topic does exactly this, which is why CMP-011 stays
silent on it and fires on `cbc-day04-broken-topic`. Getting that distinction
right in your auditor is TODO 10b, and it is the difference between a tool
people run and a tool people mute.

### The dead letter queue

```hcl
resource "aws_sqs_queue" "dlq" {
  name                      = "cbc-day04-scanner-dlq"
  message_retention_seconds = 1209600     # 14 days, the maximum
  kms_master_key_id         = local.sqs_kms_key_id
}
```

Fourteen days is deliberate: **a DLQ that expires before a human reads it is
decoration.** And a DLQ nobody watches is a folder of unread incidents, which
is why section 11 alarms on `ApproximateNumberOfMessagesVisible > 0`.

Note that `sqs_managed_sse_enabled` and `kms_master_key_id` are mutually
exclusive — setting both is a plan-time error. Note also that every value in
an SQS `Attributes` response is a **string**, so `if attrs.get("SqsManagedSseEnabled")`
is true for the string `"false"`. That bug silently passes every unencrypted
queue in the account and it is the most common defect in home-grown SQS
auditors.

---

## Part 4 — Compliance as code

`lab/python/serverless_audit.py` implements 16 checks. Same shape as Day 03:
`Finding` dataclass, severity-weighted score from 100, `--format table|json|csv`,
`--min-severity`, `--fail-on`, paginated boto3 everywhere.

| ID | Check | Severity |
|---|---|---|
| CMP-001 | Lambda has no dead letter queue configured | CRITICAL |
| CMP-002 | Secret-shaped plaintext Lambda environment variables | CRITICAL |
| CMP-003 | Lambda env vars not encrypted with a customer-managed KMS key | MEDIUM |
| CMP-004 | Lambda execution role grants wildcard Action/Resource | CRITICAL |
| CMP-005 | Log group missing or retention = Never expire | MEDIUM |
| CMP-006 | Lambda timeout at the 3s default | MEDIUM |
| CMP-007 | No reserved concurrency (unreserved) | MEDIUM |
| CMP-008 | Deprecated / out-of-support runtime | HIGH |
| CMP-009 | X-Ray active tracing disabled | LOW |
| CMP-010 | SNS topic not encrypted at rest | MEDIUM |
| CMP-011 | SNS topic policy allows wildcard principal | CRITICAL |
| CMP-012 | SQS queue not encrypted | MEDIUM |
| CMP-013 | SQS queue has no redrive policy / no DLQ of its own | MEDIUM |
| CMP-014 | EventBridge rule exists but is DISABLED | MEDIUM |
| CMP-015 | EventBridge target has no retry policy and no DLQ | LOW |
| CMP-016 | Lambda function URL or resource policy is public | CRITICAL |

Scoring: 100 minus `CRITICAL 25 / HIGH 10 / MEDIUM 4 / LOW 1 / INFO 0`, floored
at 0. `--min-severity` filters the **display only** — never the score, because
otherwise anyone can improve their compliance posture by passing
`--min-severity CRITICAL`, which is not an improvement, it is a habit.

### Two checks must say nothing

Against this stack, **CMP-008 and CMP-016 produce zero findings**, by design.
Both functions pin `python3.12`, and every `aws_lambda_permission` is scoped to
a service principal with `source_arn`.

That is not a gap in the lab. A check set where everything fires teaches you
nothing about false positives — and false positives are precisely how audit
tools get ignored. `tests/test_checks.py` asserts the silence explicitly:

```
Ran 47 tests in 0.005s
OK
```

Expect **exactly 14 findings** and a compliance score of **0/100** on a fresh
apply with `create_insecure_examples = true`. Four CRITICALs alone are 100
points. That zero is the intended shock; Step 6 is fixing them one at a time
and watching it climb.

---

## The mistakes people actually make

1. **Not clicking the SNS confirmation link.** Everything appears to work.
   Nothing is delivered. Hours are lost.
2. **Forgetting `aws_lambda_permission`.** The rule fires, the function never
   runs, and no error appears anywhere you are looking.
3. **Expecting the reactive rule to fire with no CloudTrail trail.** See
   section 10. This is not a Terraform problem and no error is raised.
4. **Letting Lambda create its own log group.** Retention "Never expire",
   outside state, survives `destroy`, bills forever.
5. **Leaving concurrency unreserved on a function with a recursive trigger.**
   This is the five-figure-bill story.
6. **Putting secrets in environment variables** and believing "encrypted at
   rest" means "not readable".
7. **`if attrs.get("SqsManagedSseEnabled")`** on a string `"false"`.
8. **Testing an event pattern only by deploying it.** Use
   `aws events test-event-pattern`.
9. **Treating a DLQ as done.** Unwatched, it is a folder of unread incidents.
10. **Writing an auditor that flags its own reference architecture.** Get the
    condition-scoped wildcard cases right or nobody will run it twice.

---

## Cost — read this before you apply

**~$1.01/month**, and $1.00 of that is one optional KMS key.

| Item | Cost | Notes |
|---|---|---|
| Lambda invocations & duration | $0.00 | 1M requests + 400,000 GB-s **permanently** free |
| EventBridge rules (AWS events) | $0.00 | no charge for AWS-source events |
| SNS | $0.00 | first 1,000 email notifications/month free |
| SQS | $0.00 | first 1M requests/month **permanently** free |
| CloudWatch Logs | ~$0.00 | a few MB, with 7-day retention set |
| CloudWatch alarms | $0.00 | first 10 alarms free |
| CloudTrail (1st trail, mgmt events) | $0.00 | additional trails are $2/100k events |
| S3 for trail logs | ~$0.01 | a lab's worth of logs |
| **KMS customer-managed key** | **$1.00** | prorated hourly + $0.03/10k requests |
| **Total** | **~$1.01** | |

Run it for free: `enable_kms_encryption = false` in `terraform.tfvars`. SNS,
SQS and the Lambda environment fall back to AWS-managed keys. Everything works;
CMP-003 will then fire on the scanner, which is the check doing its job.

### The two silent cost-growth traps

**1. Log groups without retention.** $0.50/GB ingestion, $0.03/GB-month
storage, forever, on a resource with no tags and no owner. Sweep your whole
account today:

```bash
aws logs describe-log-groups \
  --query 'logGroups[?!retentionInDays].[logGroupName,storedBytes]' \
  --output table --profile bootcamp
```

Most people find log groups from labs they ran years ago.

**2. Recursive invocation.** A function that writes to the thing that triggers
it. Unreserved concurrency turns that into a thousand parallel copies billing
at machine speed. Reserved concurrency makes it *physically impossible* rather
than merely unlikely — and Lambda's own recursive-loop detection stops most,
but not all, patterns after ~16 iterations. Do not rely on it as the only
guard.

---

## Lab

Full walkthrough: [`lab/README.md`](lab/README.md). In short:

```bash
cd lab/terraform
cp terraform.tfvars.example terraform.tfvars   # set notification_email
terraform init && terraform apply
# → then CLICK THE CONFIRMATION LINK IN YOUR INBOX

cd ../python
pip install -r requirements.txt
python3 -m unittest discover -s tests          # 47 tests, no AWS needed
python3 serverless_audit.py --profile bootcamp --region us-east-1
```

Then work `challenge/serverless_audit_challenge.py` — 12 TODOs plus a stretch,
about two hours — and compare against the reference only when you are done.

Teardown is **not optional**, even at a dollar a month:
[`teardown-checklist.md`](teardown-checklist.md).

---

## Day 04 checklist

- [ ] `terraform apply` clean, 46 resources
- [ ] SNS subscription **confirmed** (not `PendingConfirmation`)
- [ ] Manual `lambda invoke` returns findings
- [ ] Reactive rule fires on a real API call and you saw it in the logs
- [ ] You forced a failure and found the event in the DLQ
- [ ] `serverless_audit.py` reports **14 findings, score 0/100**
- [ ] `python3 -m unittest discover -s tests` → **47 passed**
- [ ] You read the broken function's secrets with a read-only call
- [ ] You can explain why CMP-008 and CMP-016 report nothing here
- [ ] `terraform destroy` complete, and you checked for the orphaned log group
- [ ] Interview questions reviewed: [`interview-qa.md`](interview-qa.md)

---

**Next:** Day 05 — Infrastructure as Code. Everything you have written by hand
becomes modules, remote state and drift detection.
