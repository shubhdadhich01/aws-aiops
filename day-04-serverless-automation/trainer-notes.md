# Day 04 — Trainer Notes

**Serverless Automation · 3h 15m · five demos**

Day 04 is the first day where the interesting failures are **silent**. Nothing
crashes. Everything reports success. The pipeline does nothing. Build the whole
session around that, and the day lands.

---

## Before the session

### T-24 hours

- [ ] Apply the stack in your demo account with `create_insecure_examples = true`
      and **confirm the SNS subscription**. Do not do this live — the
      confirmation email can take a few minutes and you will lose the room.
- [ ] Fire the reactive path once (`aws s3 mb`) and confirm the latency you get
      today. Quote **that** number, not the docs' number.
- [ ] Run `serverless_audit.py` and check you get **14 findings, score 0/100**.
- [ ] Run `python3 -m unittest discover -s tests` → 47 passed.
- [ ] Pre-warm a second terminal tailing logs (`aws logs tail --follow`) so
      Demo 3 is instant.
- [ ] Check your Day 01 budget alarm is armed. You are about to spend a
      session telling people about five-figure serverless bills.

### T-30 minutes

- [ ] Two terminals: one in `lab/terraform`, one in `lab/python`.
- [ ] `terraform output` scrolled and ready.
- [ ] Browser tabs: Lambda console (the broken function's Configuration →
      Environment variables), EventBridge rules list, CloudWatch Logs.
- [ ] Font size up. The demos are all about reading a value on screen.

### The one thing to say in the first two minutes

> "Today nothing will crash. Every failure you see will report success. A rule
> that never fires is not an error — it is a configuration. A subscription that
> discards every message is not an error — it is `PendingConfirmation`. A
> function that times out has no stack trace, because a timeout is not an
> exception. By the end you will be able to find all three, and you will have
> written the tool that finds them for you."

That framing is the day. Come back to it at every demo.

---

## Timing

| Time | Segment |
|---|---|
| 0:00–0:10 | Opening · the silent-failure framing |
| 0:10–0:35 | Part 1: Lambda beyond the handler · **Demo 1** |
| 0:35–1:00 | Part 2: EventBridge, both paths |
| 1:00–1:10 | Break |
| 1:10–1:35 | Part 3: SNS, SQS, where failure goes · **Demo 2** |
| 1:35–2:00 | Lab: apply, confirm, invoke · **Demo 3** |
| 2:00–2:20 | **Demo 4**: the reactive path, live |
| 2:20–2:35 | **Demo 5**: force a failure into the DLQ |
| 2:35–3:00 | `serverless_audit.py` and the challenge |
| 3:00–3:10 | Cost: the two silent-growth traps |
| 3:10–3:15 | Teardown, confirmed out loud |

Running late? Cut Demo 5 (describe it) and shorten the challenge walkthrough.
**Never cut Demo 4** — the reactive path firing live is the moment people
remember, and never cut the teardown segment.

---

## 0:00–0:10 — Opening

Ask: *"Who has a Lambda function running right now that nobody has looked at
in six months?"* Most hands. *"How would you know if it stopped working?"*
Silence. That is the session.

Draw the target architecture (diagrams §1) while you talk. Do not show slides
of it — draw it. People remember the drawing.

---

## 0:10–0:35 — Part 1: Lambda beyond the handler

Open with the three invocation models table (diagrams §4). Spend real time
here: **the entire rest of the day only makes sense once people internalise
that asynchronous failures are discarded by default.**

Then the five settings table from the README. Ask the room for the default
timeout before you show it. Someone will say 30 seconds (that is API Gateway's
integration timeout). It is 3.

### 🎬 Demo 1 — reading the secrets out of a "secure" function (4 min)

The whole point: encryption at rest is not the threat model.

```bash
# A read-only call. This is what ReadOnlyAccess grants.
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

Say out loud: *"These are encrypted at rest. AWS is not lying to you. It is
just not answering the question you thought you asked."*

Then show the same values in `terraform show | grep -A5 environment`, and ask
where their state file lives and who can read that bucket. That question does
more work than the demo.

---

## 0:35–1:00 — Part 2: EventBridge, both paths

Whiteboard the two paths (diagrams §2) and make them argue for each other:
reactive is fast but only sees what you enumerated; scheduled is slow but sees
everything. Neither is sufficient alone.

Then the three-things-must-be-true list. Emphasise the resource policy:

> "The execution role is what the function **can do**. The resource policy is
> **who may call it**. Every one of you will lose an hour to this at least once.
> Today is that hour, and it is free."

Live-check the permission so they see the shape:

```bash
aws lambda get-policy --profile bootcamp \
  --function-name $(terraform output -raw scanner_function_name) \
  --query 'Policy' --output text | python3 -m json.tool
```

Point at `"AWS:SourceArn"` in the condition and say: remove that, and any rule
in any AWS account can invoke this function.

**The CloudTrail dependency.** Ask: *"If I delete the trail, what breaks?"*
Let them work out that the reactive rule silently stops firing with no error
anywhere. This is the best five minutes of the segment.

---

## 1:10–1:35 — Part 3: SNS, SQS, where failure goes

### 🎬 Demo 2 — the subscription that discards everything (5 min)

Before you show the confirmed one, show what unconfirmed looks like:

```bash
aws sns list-subscriptions-by-topic --profile bootcamp \
  --topic-arn $(terraform output -raw sns_topic_arn) \
  --query 'Subscriptions[].[Endpoint,SubscriptionArn]' --output table
```

If you have a spare address, subscribe it live and **do not** confirm it, then
publish:

```bash
aws sns publish --profile bootcamp \
  --topic-arn $(terraform output -raw sns_topic_arn) \
  --subject "Does this arrive?" --message "It does not."
# → returns a MessageId. HTTP 200. Success. Nothing was delivered.
```

Say: *"That publish succeeded. Terraform is happy. The metric is green. Nobody
got an email. This is how your first production alerting pipeline will
behave."*

Then the DLQ: 14 days because a DLQ that expires before a human reads it is
decoration, and the alarm on depth because a DLQ nobody watches is a folder of
unread incidents.

---

## 1:35–2:00 — Lab: apply, confirm, invoke

Everyone applies. Expect the two questions:

1. *"The apply failed with `InsufficientS3BucketPolicyException`."* Bucket
   policy propagation. Re-run apply. It succeeds.
2. *"I did not get the confirmation email."* Check spam, check the address in
   `terraform.tfvars`, and if all else fails re-subscribe by hand. **Nobody
   proceeds past this point unconfirmed** — half the day's demos depend on it.

### 🎬 Demo 3 — manual invoke with a live log tail (5 min)

Terminal 2:

```bash
aws logs tail $(terraform output -raw scanner_log_group) --follow --profile bootcamp
```

Terminal 1:

```bash
aws lambda invoke --profile bootcamp \
  --function-name $(terraform output -raw scanner_function_name) \
  --payload '{"scan_type":"scheduled-full-sweep"}' --cli-binary-format raw-in-base64-out \
  /tmp/out.json && cat /tmp/out.json | python3 -m json.tool
```

Point at the `REPORT` line: `Duration`, `Billed Duration`, `Init Duration`.
*"Init Duration only appears on a cold start. Invoke again."* Do it — the
second invocation has no Init line. That is the module-global caching lesson in
one screenshot.

---

## 2:00–2:20 — 🎬 Demo 4: the reactive path, live

The centrepiece. Do not skip it and do not rush it.

```bash
# Terminal 2 is still tailing the log.
# Terminal 1: make an API call the pattern matches.
aws s3 mb s3://cbc-day04-demo-$RANDOM-$(date +%s) --profile bootcamp
```

Then **wait**, and narrate the wait rather than filling it with apology:

> "This is going to take somewhere between fifteen and ninety seconds. That
> delay is CloudTrail delivering the event, EventBridge matching it and Lambda
> starting. If your security SLO is ten seconds, this architecture cannot meet
> it and you need the service's own native finding events instead. Knowing that
> number is the difference between a design that works and a design that
> demos."

When the log line appears, the room reacts. Then immediately:

```bash
# Prove the dependency people just accepted on faith.
aws cloudtrail get-trail-status --profile bootcamp \
  --name $(terraform output -raw cloudtrail_name) --query 'IsLogging'
```

*"Set that to false and everything you just watched stops happening, silently."*

Delete the demo bucket before you move on.

---

## 2:20–2:35 — 🎬 Demo 5: force a failure into the DLQ

```bash
# Async invoke (note: --invocation-type Event) with a payload that breaks it.
aws lambda invoke --profile bootcamp \
  --function-name $(terraform output -raw scanner_function_name) \
  --invocation-type Event \
  --payload '{"scan_type":"explode"}' --cli-binary-format raw-in-base64-out \
  /tmp/async.json

# Wait out the retries (~1 minute with lambda_max_retry_attempts = 1), then:
aws sqs receive-message --profile bootcamp \
  --queue-url $(terraform output -raw dlq_url) \
  --max-number-of-messages 1 --query 'Messages[0].Body' --output text | python3 -m json.tool
```

Three things to point at in the message:

1. The original event payload — **this is the thing you could not otherwise
   replay**.
2. The `requestContext` with the error condition.
3. `--invocation-type Event` — say explicitly that a synchronous invoke would
   have returned the error to you and never touched the DLQ.

Then show the alarm that fired on DLQ depth, and ask: *"who would this have
paged, and would they have known what to do?"*

---

## 2:35–3:00 — `serverless_audit.py` and the challenge

Run it live:

```bash
cd ../python && python3 serverless_audit.py --profile bootcamp --region us-east-1
```

**14 findings. Score 0/100. Grade F.** Let that sit for a second.

Then the two things worth teaching from the output:

1. **`--min-severity` filters the display, never the score.** Demonstrate:
   `--min-severity CRITICAL` shows 4 findings and still says 0/100. *"Otherwise
   people improve their compliance posture by changing a flag."*
2. **CMP-008 and CMP-016 report nothing, on purpose.** Ask why before you
   explain. The answer — a check set where everything fires teaches you nothing
   about false positives — is the most transferable idea in the day.

Run the tests to show the assertion is real:

```bash
python3 -m unittest discover -s tests    # Ran 47 tests ... OK
```

Then set up the challenge: 12 TODOs plus a stretch, ~2 hours, and the reference
implementation is right there so the only person they can cheat is themselves.
Point at TODO 10b (the condition-scoped wildcard) and TODO 11b (the DLQ
exemption) as the two that separate a working auditor from a useful one.

---

## 3:00–3:10 — Cost: the two silent-growth traps

Run this against **their own** accounts, not the demo one:

```bash
aws logs describe-log-groups --profile bootcamp \
  --query 'logGroups[?!retentionInDays].[logGroupName,storedBytes]' --output table
```

Somebody in the room will have a log group from 2022. Ask them what it is for.
They will not know. That is the lesson delivered by their own account.

Then the recursion trap (diagrams §7). Tell the story in one sentence — a
function that writes to the bucket that triggers it — and then show
`reserved_concurrent_executions = 2` as the thing that makes it *physically*
bounded rather than merely unlikely. Mention Lambda's recursive-loop detection
as a safety net that catches many, not all, patterns.

---

## 3:10–3:15 — Teardown, confirmed out loud

Do not let anyone leave without running it, and go through the log-group step
together:

```bash
aws logs describe-log-groups --profile bootcamp \
  --log-group-name-prefix /aws/lambda/cbc-day04 \
  --query 'logGroups[].[logGroupName,retentionInDays]' --output table
```

The broken function's group is still there with null retention **after a
successful destroy**. Say the line:

> "Terraform did not create it, so Terraform cannot destroy it. Everything you
> ever apply has a shadow of things it caused to exist. Verifying teardown is
> not paranoia — it is the job."

Then `terraform destroy`, empty the S3 bucket first, and remind them the KMS
key will sit in `PendingDeletion` and is not billed.

---

## Questions you will get

**"Why not two functions instead of one dual-mode handler?"**
Because two copies of compliance logic drift within a quarter and then you have
two definitions of "compliant" and no idea which one produced the report. The
cost is a branchy handler; that is the cheaper cost.

**"Is Step Functions better for this?"**
For this, no — it is one function, one decision. Step Functions earns its place
when you have multi-step orchestration, human approval, long waits or complex
retry/compensation logic. Day 10 covers it.

**"Can I use CloudWatch Events instead of EventBridge?"**
They are the same service; EventBridge is the current name and superset. The
`aws_cloudwatch_event_rule` resource name is Terraform keeping backwards
compatibility, which confuses everyone exactly once.

**"Should I use provisioned concurrency?"**
Different thing from reserved. Reserved caps and guarantees a share of the
pool; provisioned keeps environments warm to kill cold starts, and it costs
money continuously. For an hourly scanner, no.

**"Why is `python3.12` in a variable rather than hard-coded?"**
So CMP-008 can be demonstrated by changing one line, and so runtime upgrades
are a diff rather than an archaeology project.

**"Does the auditor need write permissions?"**
No. Every call it makes is read-only; `SecurityAudit` or `ReadOnlyAccess`
covers it. Say this clearly — people will want to run it in production and the
first question their security team asks is this one.

---

## Common learner mistakes

1. Not confirming the SNS subscription, then debugging Lambda for 20 minutes.
2. Using `aws lambda invoke` without `--invocation-type Event` and wondering why
   nothing reaches the DLQ.
3. Forgetting `--cli-binary-format raw-in-base64-out` on AWS CLI v2 and getting
   an opaque payload error.
4. Expecting the reactive rule to fire within 2 seconds.
5. Editing `main.tf` while `apply` is running.
6. Writing `if concurrency:` in TODO 7, which treats a reserved value of 0 as
   unreserved.
7. Writing `if attrs.get("SqsManagedSseEnabled"):` in TODO 11a — the string
   `"false"` is truthy.
8. Flagging the good SNS topic in TODO 10b because they skipped the Condition
   logic. Point them at the test that catches it.
9. Destroying without emptying the CloudTrail bucket, then having to clean up a
   half-destroyed stack.
10. Believing an empty `terraform state list` means an empty account.

---

## Closing (30 seconds)

> "Today's failures were all silent. A disabled rule, an unconfirmed
> subscription, a discarded event, a log group nobody knows about. None of them
> raised an error, and every one of them is findable by a tool you now know how
> to write. Tomorrow we stop writing infrastructure by hand and start writing
> modules — and the drift detection you build will catch the fourth kind of
> silent failure: the change somebody made in the console at 6 p.m. on a
> Friday."
