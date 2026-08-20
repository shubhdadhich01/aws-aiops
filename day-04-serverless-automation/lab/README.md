# Day 04 Lab — Automated Resource Compliance Scanner

**~2 hours** · Terraform + Python (boto3) · **~$1.01/month while running**

Build an event-driven compliance scanner that runs on a schedule *and* reacts
to CloudTrail events, publishes findings to SNS, catches its own failures in a
dead letter queue — then audit the whole thing with a Python tool you write.

| Step | What | Time |
|---|---|---|
| 0 | [Prerequisites](#step-0--prerequisites) | 5 min |
| 1 | [Apply the stack](#step-1--apply-the-stack) | 10 min |
| 2 | [Confirm the SNS subscription](#step-2--confirm-the-sns-subscription) | 3 min |
| 3 | [Invoke the scanner by hand](#step-3--invoke-the-scanner-by-hand) | 10 min |
| 4 | [Trigger the reactive path](#step-4--trigger-the-reactive-path) | 15 min |
| 5 | [Force a failure into the DLQ](#step-5--force-a-failure-into-the-dlq) | 15 min |
| 6 | [Read the broken function's secrets](#step-6--read-the-broken-functions-secrets) | 5 min |
| 7 | [Run the auditor](#step-7--run-the-auditor) | 10 min |
| 8 | [Fix findings and watch the score climb](#step-8--fix-findings-and-watch-the-score-climb) | 20 min |
| 9 | [The challenge — write it yourself](#step-9--the-challenge) | ~2 h |
| 10 | [Destroy and verify](#step-10--destroy-and-verify) | 10 min |

---

## Step 0 — Prerequisites

```bash
aws sts get-caller-identity --profile bootcamp    # must succeed
terraform version                                 # >= 1.5 (or tofu >= 1.6)
python3 --version                                 # >= 3.9
```

You need a **real email address** you can check within the next five minutes.

Run the unit tests first — they need no AWS account at all, and getting a green
run before you spend a cent is a good habit:

```bash
cd lab/python
pip install -r requirements.txt
python3 -m unittest discover -s tests
# Ran 47 tests in 0.005s
# OK
```

---

## Step 1 — Apply the stack

```bash
cd ../terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`. The only value you **must** change:

```hcl
notification_email = "you@example.com"
owner              = "your-name"
```

Worth knowing before you apply:

```hcl
enable_kms_encryption    = true   # the entire $1.00/month. false = free.
create_insecure_examples = true   # the 14 findings. Keep true for the lab.
lambda_reserved_concurrency = 2   # the runaway-bill guard
log_retention_days          = 7   # the forever-bill guard
```

```bash
terraform init
terraform plan      # read it — 46 resources
terraform apply
```

**If apply fails with `InsufficientS3BucketPolicyException`:** the CloudTrail
bucket policy has not propagated yet. Re-run `terraform apply`. It succeeds the
second time. This is normal and not something you did.

Then read the outputs — they are written to be read:

```bash
terraform output next_steps
terraform output cost_breakdown
terraform output silent_cost_growth
```

---

## Step 2 — Confirm the SNS subscription

**Nothing works until you do this.** Check your inbox for
*"AWS Notification - Subscription Confirmation"* and click the link.

Then prove it:

```bash
aws sns list-subscriptions-by-topic --profile bootcamp \
  --topic-arn $(terraform output -raw sns_topic_arn) \
  --query 'Subscriptions[].[Endpoint,SubscriptionArn]' --output table
```

If the ARN column says `PendingConfirmation`, you are not subscribed. That is a
failure state wearing a neutral name: **every publish will succeed and every
message will be discarded**, with no error anywhere and no Terraform drift.

Test the path end to end before you rely on it:

```bash
aws sns publish --profile bootcamp \
  --topic-arn $(terraform output -raw sns_topic_arn) \
  --subject "Day 04 test" --message "If you are reading this, the path works."
```

No email? Check spam, then re-check the address in `terraform.tfvars`.
An alerting path nobody has ever fired is a hypothesis, not a control.

---

## Step 3 — Invoke the scanner by hand

Open a **second terminal** and tail the logs:

```bash
cd lab/terraform
aws logs tail $(terraform output -raw scanner_log_group) --follow --profile bootcamp
```

Back in the first terminal:

```bash
aws lambda invoke --profile bootcamp \
  --function-name $(terraform output -raw scanner_function_name) \
  --payload '{"scan_type":"scheduled-full-sweep"}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/scan.json

python3 -m json.tool < /tmp/scan.json
```

In the log tail, find the `REPORT` line:

```
REPORT RequestId: ...  Duration: 2841.55 ms  Billed Duration: 2842 ms
       Memory Size: 256 MB  Max Memory Used: 96 MB  Init Duration: 412.31 ms
```

**Run the invoke again.** The `Init Duration` line disappears — that was the
cold start. Everything you initialise at module scope (boto3 clients, cached
secrets) survives between warm invocations, which is why the secret-fetch
pattern in the day README caches in a module global.

> `Max Memory Used: 96 MB` against `Memory Size: 256 MB` is your right-sizing
> signal. Memory and CPU scale together in Lambda, so the cheapest setting is
> often not the smallest one — a 512 MB function that finishes in half the time
> costs the same and fails less.

---

## Step 4 — Trigger the reactive path

This is the interesting one. Keep the log tail running.

```bash
aws s3 mb s3://cbc-day04-demo-$RANDOM-$(date +%s) --profile bootcamp
```

Now wait. **15 to 90 seconds.** That delay is CloudTrail delivering the event,
EventBridge matching it, and Lambda cold-starting. Watch the log tail — you
will see the handler take the reactive branch and report on the single resource
that changed.

If your security requirement is "under 10 seconds", this architecture cannot
meet it and you need the service's own native events (GuardDuty findings, AWS
Config rules) rather than API-call events. Knowing that number is the point of
this step.

### Prove why it works

```bash
aws cloudtrail get-trail-status --profile bootcamp \
  --name $(terraform output -raw cloudtrail_name) --query 'IsLogging'
```

`true`. Set it to `false` and the reactive rule stops firing — silently, with
no error, forever. **EventBridge receives API activity only when a CloudTrail
trail is delivering management events.** That is why `main.tf` section 10
exists, and it is the single most common reason a perfect-looking reactive rule
never fires in someone else's account.

Clean up the demo bucket:

```bash
aws s3 rb s3://<the-bucket-you-made> --force --profile bootcamp
```

---

## Step 5 — Force a failure into the DLQ

The DLQ only catches **asynchronous** failures, so `--invocation-type Event`
is mandatory here. A synchronous invoke returns the error to you and never
touches the queue.

```bash
aws lambda invoke --profile bootcamp \
  --function-name $(terraform output -raw scanner_function_name) \
  --invocation-type Event \
  --payload '{"scan_type":"explode"}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/async.json
```

Wait about a minute for the retries to exhaust (`lambda_max_retry_attempts`
defaults to 1 so you are not waiting out full exponential backoff), then:

```bash
aws sqs receive-message --profile bootcamp \
  --queue-url $(terraform output -raw dlq_url) \
  --max-number-of-messages 1 \
  --query 'Messages[0].Body' --output text | python3 -m json.tool
```

Three things to look at in that message:

1. **The original event payload.** This is the thing you could not otherwise
   replay. Without the DLQ it would be gone and the only evidence would be
   `Errors` +1.
2. **The error context** — because the stack uses `destination_config` as well
   as `dead_letter_config`, you get the failure reason alongside the event.
3. **The queue depth alarm.** Check it fired:

```bash
aws cloudwatch describe-alarms --profile bootcamp \
  --alarm-name-prefix cbc-day04 \
  --query 'MetricAlarms[].[AlarmName,StateValue]' --output table
```

> Delete the message when you are done (`aws sqs purge-queue --queue-url ...`)
> or leave it — the alarm staying in ALARM is itself a useful thing to look at.

---

## Step 6 — Read the broken function's secrets

One read-only API call. This is what `ReadOnlyAccess` grants to anyone in your
account:

```bash
aws lambda get-function-configuration --profile bootcamp \
  --function-name $(terraform output -raw broken_function_name) \
  --query 'Environment.Variables'
```

```json
{
  "API_KEY": "sk-live-NOT-A-REAL-KEY-abcdef123456",
  "DB_PASSWORD": "hunter2-also-not-real",
  "DB_HOST": "prod-db.internal.example.com"
}
```

Those values are encrypted at rest. AWS is not lying to you — it is just not
answering the question you thought you asked. Now find them again in a place
you were not thinking about:

```bash
terraform show | grep -A 6 'environment {'
```

They are in your state file too. Ask yourself where that state file lives and
who can read that bucket.

---

## Step 7 — Run the auditor

```bash
cd ../python
python3 serverless_audit.py --profile bootcamp --region us-east-1
```

Expect **14 findings** and a **compliance score of 0/100, grade F**. Four
CRITICALs alone are 100 points. That zero is the intended shock.

Try the other formats and the CI behaviour:

```bash
python3 serverless_audit.py --format json --quiet > findings.json
python3 serverless_audit.py --format csv --min-severity HIGH
python3 serverless_audit.py --fail-on CRITICAL; echo "exit code: $?"    # 1
```

### Two things in this output are the lesson

**1. `--min-severity` filters the display, never the score.**

```bash
python3 serverless_audit.py --min-severity CRITICAL --profile bootcamp
# 4 findings shown. Still 0/100.
```

Otherwise anyone could improve their compliance posture by changing a flag,
which is not an improvement — it is a habit.

**2. CMP-008 and CMP-016 report nothing, on purpose.** Both functions pin
`python3.12`, and every `aws_lambda_permission` is scoped to a service
principal with `source_arn`. A check set where everything fires teaches you
nothing about false positives, and false positives are exactly how audit tools
get ignored. `tests/test_checks.py` asserts that silence explicitly.

---

## Step 8 — Fix findings and watch the score climb

Fix them in `main.tf` section 12, one at a time, re-applying and re-running the
auditor after each. Suggested order — cheapest points first is the wrong
instinct; fix the CRITICALs:

| Fix | Findings removed | Score after |
|---|---|---|
| Scope the broken role to specific actions | CMP-004 | 0 → 0 |
| Move secrets to Secrets Manager ARNs | CMP-002 | 0 → 0 |
| Add `dead_letter_config` | CMP-001 | 0 → 9 |
| Add the topic policy condition | CMP-011 | 9 → 34 |
| Add a log group with retention | CMP-005 | 34 → 38 |
| `timeout = 60`, `reserved_concurrent_executions = 2` | CMP-006, CMP-007 | 38 → 46 |
| Encrypt the topic and the queue | CMP-010, CMP-012 | 46 → 54 |
| Add a redrive policy | CMP-013 | 54 → 58 |
| Enable the rule, add retry + DLQ to the target | CMP-014, CMP-015 | 58 → 63 |
| `kms_key_arn` on the function, tracing Active | CMP-003, CMP-009 | 63 → **68+** |

The score only starts moving once the CRITICALs are gone — which is exactly
what a severity-weighted score is for. Fixing four LOW findings while an
`Action: "*"` role sits there is motion, not progress.

> Faster alternative if you are short on time: set
> `create_insecure_examples = false`, apply, and re-run. **0 findings, 100/100.**
> Then diff the two plans and read what changed.

---

## Step 9 — The challenge

Now write it yourself.

```bash
cd challenge
python3 serverless_audit_challenge.py --profile bootcamp --region us-east-1
# 0 findings — every check is stubbed. That is the starting line.
```

12 TODOs plus a stretch, roughly **100–120 minutes**. Everything that is not a
check is done for you: CLI, `Finding`, paginators, the collector, scoring, all
three renderers. Each TODO has the exact API fields, a hint, a time estimate
and a CHECKPOINT.

Test as you go, offline:

```bash
cd .. && python3 -m unittest discover -s tests -v
```

The two TODOs that separate a working auditor from a useful one:

- **TODO 10b (CMP-011)** — a wildcard principal narrowed by an
  `AWS:SourceAccount` condition is *correct*. Flag it and your tool cries wolf
  on the reference architecture it ships with.
- **TODO 11b (CMP-013)** — a dead letter queue needs no dead letter queue of
  its own. Get the exemption wrong and every DLQ in the account produces a
  finding nobody can action.

Do not read `../serverless_audit.py` until you are done. You will learn
nothing, and the checks are the whole exercise.

---

## Step 10 — Destroy and verify

**Empty the CloudTrail bucket first** or the destroy fails halfway:

```bash
cd ../../terraform
BUCKET=$(terraform output -raw cloudtrail_bucket)
aws s3 rm "s3://$BUCKET" --recursive --profile bootcamp
# versioned bucket — see teardown-checklist.md for versions and delete markers
terraform destroy
```

Then the step that is the real lesson of Day 04:

```bash
aws logs describe-log-groups --profile bootcamp \
  --log-group-name-prefix /aws/lambda/cbc-day04 \
  --query 'logGroups[].[logGroupName,retentionInDays]' --output table
```

The broken function's log group is **still there**, with null retention, after
a successful destroy. Terraform never created it — Lambda did, on first
invocation — so Terraform cannot delete it. Delete it by hand:

```bash
aws logs delete-log-group --profile bootcamp \
  --log-group-name /aws/lambda/cbc-day04-broken-function-<suffix>
```

Then widen the search to your whole account. Most people find log groups from
labs they ran years ago:

```bash
aws logs describe-log-groups --profile bootcamp \
  --query 'logGroups[?!retentionInDays].[logGroupName,storedBytes]' --output table
```

Full verification script and the KMS `PendingDeletion` explanation:
[`../teardown-checklist.md`](../teardown-checklist.md).

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `InsufficientS3BucketPolicyException` on apply | bucket policy not propagated | re-run `terraform apply` |
| No confirmation email | wrong address, or spam | fix `notification_email`, re-apply |
| Publishes succeed, no email arrives | subscription `PendingConfirmation` | click the link |
| Reactive rule never fires | no CloudTrail trail, or pattern typo | check `IsLogging`; `aws events test-event-pattern` |
| Rule fires, function never runs | missing `aws_lambda_permission` | `aws lambda get-policy --function-name ...` |
| Nothing reaches the DLQ | synchronous invoke | add `--invocation-type Event` |
| DLQ empty even async | role lacks `sqs:SendMessage` | check the execution role policy |
| `KMSAccessDeniedException` at cold start | role lacks `kms:Decrypt` | grant it on the key |
| Payload error on AWS CLI v2 | missing binary format flag | `--cli-binary-format raw-in-base64-out` |
| Auditor reports 0 findings | `create_insecure_examples = false` | set true and apply |
| Auditor reports more than 14 | other Lambdas/queues in the region | expected — it audits the whole region |
