# Day 04 — Interview Q&A

**Serverless automation · Lambda · EventBridge · SNS · SQS**

Fifteen questions that get asked, with the answer an interviewer is actually
listening for. Most of these have a shallow correct answer and a deeper one;
the deeper one is what separates "has used Lambda" from "has operated Lambda".

---

## 1. What happens to an event when a Lambda invocation fails?

**It depends entirely on the invocation model**, and starting there is most of
the answer.

- **Synchronous** (API Gateway, ALB, `lambda invoke`): the error is returned to
  the caller. The caller decides whether to retry. Lambda does nothing.
- **Asynchronous** (EventBridge, SNS, S3 notifications): Lambda retries twice
  by default with backoff, then **discards the event**. If you configured a
  `DeadLetterConfig` target or an `OnFailure` destination, it goes there
  instead. If not, it is gone — the only trace is `Errors` +1.
- **Poll-based** (SQS, Kinesis, DynamoDB Streams): the record is retried until
  it expires or the queue's `maxReceiveCount` moves it to the queue's own DLQ.
  For streams, a poison record **blocks the shard** until it expires, which is
  the scariest of the three because it stops unrelated work.

The follow-up is usually: *"so does a DLQ help a synchronous invocation?"* No.
There is a caller holding the connection; it gets the error and owns the retry.

---

## 2. DeadLetterConfig or Lambda Destinations — which, and why?

Both catch failed asynchronous events. Destinations are the newer and richer
mechanism:

| | DeadLetterConfig | Destinations (OnFailure) |
|---|---|---|
| Carries | the event body | event **and** response/error context |
| Targets | SQS, SNS | SQS, SNS, EventBridge, Lambda |
| Success routing | no | yes (`OnSuccess`) |
| Configured on | the function | the event invoke config |

Use Destinations for anything new — having the error alongside the payload
turns a DLQ message from "something broke" into "this broke, here is why".
DeadLetterConfig still exists everywhere and still works, so an auditor must
accept either. Flagging a function that uses Destinations for "no DLQ" is a
false positive, and false positives are how tools get muted.

**The permission everyone forgets**: the execution role needs `sqs:SendMessage`
on the DLQ. Without it, DLQ delivery fails silently and the event is lost
anyway — the same problem, one layer down.

---

## 3. Why does reserved concurrency matter if my function is small?

Because concurrency is a **shared account-level pool** (1,000 by default), and
because loops happen.

The classic incident: a function writes to the S3 bucket that triggers it, or
an EventBridge rule fires on an API call the function itself makes. Unreserved,
that scales to 1,000 parallel copies billing at machine speed while you sleep.
People have woken to five-figure bills from a two-line mistake.

Reserved concurrency does two things:

1. **Caps this function**, making a runaway physically impossible rather than
   merely unlikely.
2. **Protects everyone else** — it also reserves capacity, so one noisy
   function cannot throttle the rest of the account out of the pool.

A senior-sounding addition: `0` is a valid value meaning "throttled to a
complete stop" — a useful kill switch during an incident, and a catastrophic
typo. Lambda's recursive-loop detection catches many patterns after ~16
iterations, but it does not catch all of them and it is not a substitute for a
cap.

---

## 4. Why does a 3-second timeout produce such hard-to-diagnose failures?

Because a timeout is not an exception. There is no stack trace, no error
object, no `except` block that runs. The log simply **stops mid-sentence**, and
the invocation is billed and marked as an error with the message
"Task timed out after 3.00 seconds".

Three seconds is the AWS default, and it is almost never a deliberate choice.
Any handler that cold-starts a boto3 client and makes a paginated API call can
exceed it.

The reasoning it corrects: people run tight timeouts to save money. **You are
billed for duration actually used, not for the timeout.** A generous timeout
costs nothing and only bounds your worst case. Set it from measured p99 plus
headroom, and alarm on `Duration` so you notice when real work creeps upward.

---

## 5. Environment variables are encrypted at rest. Why is putting a secret in one still wrong?

Encryption at rest is not the threat model. The threat is **read access to the
API**.

Anyone with `lambda:GetFunctionConfiguration` — which `ReadOnlyAccess` grants,
and which is handed out freely — reads the values back in plaintext. They also
appear in the console, in `terraform show`, and in your **Terraform state
file**, which is often in an S3 bucket with a broader reader list than anyone
remembers.

The correct pattern keeps the pointer in the environment and the secret in
something built for secrets:

```python
_cache = {}
def get_secret():
    if "v" not in _cache:
        arn = os.environ["SECRET_ARN"]           # pointer, not secret
        _cache["v"] = json.loads(
            boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
        )
    return _cache["v"]        # module global: survives warm invocations
```

The cache matters — Secrets Manager is $0.05 per 10,000 API calls and a
per-invocation fetch on a hot function adds latency to every request.

---

## 6. What does a customer-managed KMS key buy you over the default Lambda key?

Three things, and "it is more encrypted" is not one of them. The data is
encrypted either way.

1. **You own the key policy** — you decide who may `Decrypt`, independently of
   IAM.
2. **CloudTrail records every Decrypt** naming the caller. With the default
   service key you get no per-caller trail.
3. **You can revoke access to the data** by revoking the grant or disabling the
   key, without touching the data itself.

Cost: ~$1/month per key, prorated hourly, plus $0.03 per 10,000 requests. That
is the entire Day 04 bill, and it is a reasonable thing to switch off in a lab
and mandatory in a regulated environment.

Watch out: grant the execution role `kms:Decrypt` on that key, or the function
fails at cold start with a `KMSAccessDeniedException` that names KMS rather
than your configuration.

---

## 7. My EventBridge rule fires but the Lambda never runs. Diagnose it.

The answer is almost always the **resource-based policy**, and the structure of
the answer matters as much as the conclusion:

1. Check the rule's `Invocations` and `FailedInvocations` metrics. Invocations
   climbing with FailedInvocations climbing = delivery is being attempted and
   rejected.
2. `aws lambda get-policy --function-name <fn>` — is there a statement allowing
   `events.amazonaws.com` with this rule's ARN in `SourceArn`?
3. If not, that is it. The **execution role** governs what the function may
   do; the **resource policy** governs who may call it. Adding permissions to
   the role does nothing for this.
4. Also check: is the rule ENABLED, is the target ARN the right version/alias,
   and is the function's reserved concurrency set to `0` (which throttles it to
   a stop and looks identical from the outside).

```hcl
resource "aws_lambda_permission" "allow_rule" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scanner.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.scheduled_scan.arn   # scope it
}
```

Omit `source_arn` and any rule in any account can invoke your function.

---

## 8. Why does an EventBridge rule matching CloudTrail events need a trail to exist?

Because **EventBridge does not receive API activity unless a CloudTrail trail
is delivering management events** to the account. CloudTrail Event history in
the console is a separate, always-on, 90-day view — it does not put events on
the default event bus.

So a `detail-type: "AWS API Call via CloudTrail"` rule in an account with no
trail is syntactically perfect, deployed, enabled, and will never fire. No
error is raised anywhere. This is why Day 04's Terraform creates a trail in
section 10 before the reactive rule in section 9 can do anything.

Cost note that shows you have run this in production: the **first** trail
delivering management events to S3 is free; additional trails are $2 per
100,000 events. So create one organisation-wide trail, not one per team.

---

## 9. Walk me through a scheduled-plus-reactive compliance design. Why both?

Because each covers the other's failure mode.

- **Reactive** (CloudTrail → EventBridge → Lambda): 15–90 second latency,
  scans the single resource that changed. Catches the public bucket before it
  is public for an hour. But it only catches what your event pattern lists —
  a service or API you did not enumerate is invisible to it, silently.
- **Scheduled** (`rate(1 hour)` → Lambda): scans everything, no matter how it
  got there. Catches console changes you did not pattern-match, drift, and
  anything created while the reactive path was broken. But up to an hour late.

The design answer is one handler branching on the payload shape, not two
functions — because two copies of compliance logic drift within a quarter, and
then you have two different definitions of "compliant" and no way to know which
one the report used.

Add: the scheduled sweep is also your **canary for the reactive path**. If the
sweep keeps finding things the reactive rule should have caught in real time,
your event pattern has a gap.

---

## 10. What is wrong with `Principal: "*"` on an SNS topic policy, and when is it fine?

Unconditioned, it means **every AWS principal on Earth** may perform the listed
actions. On a topic with an email subscription, a stranger can send mail that
arrives from your own alerting pipeline and looks completely legitimate to the
reader.

It is **fine — and normal — when narrowed by a condition**:

```hcl
Condition = {
  StringEquals = { "AWS:SourceAccount" = data.aws_caller_identity.current.account_id }
}
```

Other acceptable narrowings: `AWS:SourceArn`, `AWS:SourceOwner`,
`aws:PrincipalOrgID` for org-wide access.

This distinction is the reason a naive auditor is useless: the default SNS
topic policy AWS generates uses a wildcard principal with an account condition.
Flag that and you produce a CRITICAL finding on every topic in every account,
and your tool gets ignored within a week.

---

## 11. A queue has no redrive policy. Is that always a finding?

No — and knowing when it is not is the interesting half.

A **dead letter queue has no dead letter queue of its own**, and that is
correct, not a gap. An auditor that flags every DLQ produces a finding nobody
can action, on purpose, forever.

How to tell a DLQ from a queue that is missing one:

1. **Relationship**: is this queue's ARN the `deadLetterTargetArn` in another
   queue's redrive policy, the `TargetArn` of a Lambda `DeadLetterConfig`, an
   `OnFailure` destination, or an EventBridge target `DeadLetterConfig`? If
   yes, something already treats it as a DLQ.
2. **Naming**, as a fallback: `*-dlq`, `*-dead-letter*`. Weaker evidence, but
   it covers the real case where the source queue lives in another stack or
   another account and nothing in this region points at it.

For a queue that genuinely needs one: `maxReceiveCount` around 5, the DLQ at
the 14-day maximum retention, and an alarm on
`ApproximateNumberOfMessagesVisible > 0` — because a DLQ nobody watches is a
folder of unread incidents.

---

## 12. Your SNS alerts stopped arriving. Nothing in the logs shows an error. What happened?

The overwhelmingly likely answer: **the subscription was never confirmed**, or
somebody clicked "unsubscribe" in an email.

An email subscription is not active when Terraform reports success. AWS sends a
confirmation link and the subscription sits in `PendingConfirmation` until a
human clicks it. Meanwhile **every publish succeeds** — SNS accepted the
message — and every message is discarded. There is no error, no failed metric,
nothing in the function's log.

```bash
aws sns list-subscriptions-by-topic --topic-arn <arn> \
  --query 'Subscriptions[].[Endpoint,SubscriptionArn]' --output table
```

`PendingConfirmation` in the ARN column is a failure state wearing a neutral
name. Terraform will never show it as drift, because from the API's point of
view nothing is wrong.

Second candidate if confirmation is fine: the topic is KMS-encrypted and the
publisher lost `kms:GenerateDataKey`, which fails the publish with an error
naming KMS. Third: a subscription filter policy that no longer matches.

---

## 13. How do you stop a log group from quietly costing money forever?

Set retention **before the function ever runs**, and declare the group in
Terraform:

```hcl
resource "aws_cloudwatch_log_group" "scanner" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 7
}

resource "aws_lambda_function" "scanner" {
  # …
  depends_on = [aws_cloudwatch_log_group.scanner]
}
```

Why it matters: if you do not, **Lambda creates the group itself on first
invocation** with retention "Never expire". Three consequences, in increasing
order of annoyance:

1. $0.50/GB ingestion and $0.03/GB-month storage, forever, for logs nobody
   reads.
2. Terraform does not know it exists, so `terraform destroy` leaves it behind.
   You believe the environment is gone. It is not.
3. The bill arrives months later with no obvious cause, because a log group has
   no tags, no owner, and a name that means nothing to whoever is investigating.

The `depends_on` matters too: without it Lambda races Terraform to create the
group and wins about half the time, leaving you with an untracked group anyway.

Account-wide sweep worth running today:

```bash
aws logs describe-log-groups \
  --query 'logGroups[?!retentionInDays].[logGroupName,storedBytes]' --output table
```

---

## 14. Design a serverless automation that reacts to a security event within a minute, and explain where it can fail.

**Design:**

```
CloudTrail (management events) ──► EventBridge rule (event pattern)
    ──► Lambda (reserved concurrency, DLQ, 60s timeout, scoped role)
    ──► SNS (KMS, account-scoped policy) ──► on-call
    └─► DynamoDB / S3 for the finding record
```

**Where it fails, in the order you should mention them:**

1. **No CloudTrail trail** → the rule never fires, and nothing reports it.
2. **Event pattern gap** → a `eventName` you did not list is invisible. Mitigate
   with a scheduled full sweep as a backstop.
3. **Missing `aws_lambda_permission`** → rule fires, function never runs.
4. **Latency expectation** → CloudTrail-to-EventBridge is typically 15–90
   seconds, not instant. If your SLO is "under 10 seconds", this architecture
   cannot meet it and you need the service's own native event (e.g. GuardDuty
   findings, Config rules) rather than the API-call event.
5. **Function fails and the event is discarded** → DLQ plus an alarm on DLQ
   depth, not just on `Errors`.
6. **Alerting is broken** → unconfirmed SNS subscription. Test it deliberately;
   an alerting path nobody has ever fired is a hypothesis, not a control.
7. **The function itself becomes the incident** → recursive trigger, so cap
   concurrency and never let it write to the source of its own events.

The answer that lands is the last one plus a metric: what you alarm on, and
what you do when the alarm is silent for a week.

---

## 15. Where would AI fit into this pipeline, and where would you keep it out?

**Good fits:**

- **Summarising a burst of findings** into one paragraph an on-call engineer
  can read at 3 a.m. — "14 findings, all on one stack, three CRITICAL, all
  created 11 minutes ago by the same role" is more useful than 14 JSON blobs.
- **Explaining a finding in context**: turning `CMP-004 wildcard policy` into
  "this role can delete any bucket in the account, and it is attached to a
  function whose source is in a public repo".
- **Drafting the remediation** — a scoped IAM policy generated from observed
  CloudTrail activity, presented for review.

**Where to keep it out:**

- **The detection itself.** The checks must be deterministic, versioned and
  testable. A compliance finding that appears one run and not the next destroys
  trust faster than a missed finding.
- **Anything auto-applying a change** without a human approving the diff, in
  the first year at least.
- **Anything that sees raw secrets.** The DLQ message bodies and the finding
  evidence may contain them; redact before you send.

The framing interviewers like: **deterministic detection, probabilistic
explanation.** The score, the check IDs and the pass/fail must be reproducible;
the prose around them can be generated. Day 06 builds this properly with
Bedrock.

---

## Rapid-fire

- **Default Lambda timeout?** 3 seconds. Max 15 minutes.
- **Default async retries?** 2, with backoff.
- **Max SQS message retention?** 14 days.
- **Free tier that never expires for Lambda?** 1M requests + 400,000 GB-seconds
  per month.
- **Cost of an EventBridge rule matching AWS service events?** Free.
- **Cost of the first CloudTrail trail (management events, to S3)?** Free.
- **`rate(1 hour)` vs `cron(0 * * * ? *)`?** Same effect; `rate` is relative to
  creation, `cron` is absolute and always UTC.
- **What does `?` mean in a cron expression?** No specific value — required in
  day-of-week when day-of-month is specified, and vice versa.
- **Which is enabled: `ENABLED_WITH_ALL_CLOUDTRAIL_MANAGEMENT_EVENTS`?**
  Enabled. Test the prefix, not equality.
- **Can `sqs_managed_sse_enabled` and `kms_master_key_id` both be set?** No —
  plan-time error.
- **What type are the values in an SQS `Attributes` response?** Strings. The
  string `"false"` is truthy in Python.
- **Reserved concurrency of 0 means?** Function throttled to a complete stop.
- **Cold start cost of a customer-managed KMS key?** One `Decrypt` call per
  cold start, ~$0.03 per 10,000.
- **Where do Lambda logs go if the group does not exist?** Lambda creates it,
  retention "Never expire", outside your Terraform state.
