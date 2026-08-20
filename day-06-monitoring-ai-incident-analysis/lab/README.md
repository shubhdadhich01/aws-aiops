# Day 06 Lab — AI-Based Log Analysis and Incident Summary

**CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp**

Deploy an observability stack, break a service on purpose, **read the logs
yourself and commit to a conclusion**, and only then let a model summarise them
so you can check its answer against yours.

That ordering is the entire pedagogical point of the day and it is easy to get
backwards. Step 3 is not a warm-up. It is the control group.

| | |
|---|---|
| **Time** | ~2h 35m |
| **Cost** | ~$1.01/month floor while running; a few cents for an afternoon |
| **Region** | `us-east-1` · profile `bootcamp` · prefix `cbc-day06-` |
| **Needs** | Terraform ≥ 1.10 or OpenTofu ≥ 1.8, Python 3.9+, boto3, **Bedrock model access granted** |

---

## Steps at a glance

| Step | What | Time |
|---|---|---|
| [0](#step-0--deploy) | Deploy, and confirm the SNS subscription | 20m |
| [1](#step-1--establish-a-baseline) | Establish a baseline | 5m |
| [2](#step-2--break-it-on-purpose) | Break it on purpose | 5m |
| [3](#step-3--read-the-logs-yourself-first) | **Read the logs yourself first** | 15m |
| [4](#step-4--watch-the-alarms-and-the-dashboard) | Watch the alarms and the dashboard | 15m |
| [5](#step-5--prove-the-composite-alarm-can-fire) | Prove the composite alarm can fire | 10m |
| [6](#step-6--now-let-the-model-read-it) | Now let the model read it | 25m |
| [7](#step-7--run-the-auditor) | Run the auditor | 20m |
| [8](#step-8--break-two-more-things-on-purpose) | Break two more things on purpose | 15m |
| [9](#step-9--the-reference-build) | The reference build | 10m |
| [10](#step-10--destroy-and-verify) | Destroy, and verify | 15m |

---

## Before you start

**Request Bedrock model access.** Console → Bedrock → Model access → request
`anthropic.claude-3-5-haiku-20241022-v1:0` in your region. It is usually
instant. An un-requested model returns `AccessDeniedException` and the message
does not say "go and press the button", which is the most common way this lab
starts badly.

```bash
aws bedrock list-foundation-models --profile bootcamp --region us-east-1 \
  --query 'modelSummaries[?contains(modelId, `haiku`)].modelId' --output table
```

---

## Step 0 — Deploy

**~20 minutes**

```bash
cd day-06-monitoring-ai-incident-analysis/lab/terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` and set **one** value:

```hcl
notification_email = "you@example.com"
```

Everything else has a working default. Read the commented-out block anyway —
several of those defaults exist to stop you making an expensive mistake, and
each one says which.

```bash
terraform init
terraform plan
terraform apply
```

While it runs, read `main.tf` sections 3, 4 and 5. The comments are the lesson;
the resources are the exercise.

### Confirm the SNS subscription NOW

Two emails arrive within seconds — one for the alerts topic, one for summaries.
**Click both.** Then verify:

```bash
aws sns list-subscriptions-by-topic \
  --topic-arn "$(terraform output -raw sns_topic_arn)" \
  --profile bootcamp --region us-east-1 \
  --query 'Subscriptions[].SubscriptionArn' --output text
```

`PendingConfirmation` means go and click it. Anything starting with `arn:`
means you are good.

This is not housekeeping. An unconfirmed subscription means **every publish
succeeds and every message is discarded** — no error, no metric, and the alarm
history records the action as delivered. Nothing in this repo, or in
CloudWatch, can detect it for you.

### Read the outputs

```bash
terraform output next_steps
terraform output cost_breakdown
terraform output silent_cost_growth
```

`cost_breakdown` is the honest version of what you just built. Note which line
says `$0.00 AT REST` and why.

---

## Step 1 — Establish a baseline

**~5 minutes**

Healthy traffic, so the graphs have a "before". Cold graphs make Step 4
meaningless.

```bash
aws lambda invoke \
  --function-name "$(terraform output -raw chaos_function_name)" \
  --payload '{"mode":"normal","lines":300,"window_minutes":20}' \
  --cli-binary-format raw-in-base64-out \
  --profile bootcamp --region us-east-1 \
  /tmp/baseline.json && python3 -m json.tool < /tmp/baseline.json
```

While that lands, try the mode that teaches something on its own:

```bash
aws lambda invoke \
  --function-name "$(terraform output -raw chaos_function_name)" \
  --payload '{"mode":"latency","lines":200}' \
  --cli-binary-format raw-in-base64-out \
  --profile bootcamp --region us-east-1 \
  /tmp/slow.json
```

`latency` mode emits requests that are slow and **entirely successful** — zero
ERROR lines. Watch the error-rate alarm sit stubbornly at OK while every
customer waits four seconds. That is the cleanest demonstration in the lab that
*"is it erroring"* and *"is it working"* are different questions, and it is why
`main.tf` section 5b exists.

---

## Step 2 — Break it on purpose

**~5 minutes**

```bash
aws lambda invoke \
  --function-name "$(terraform output -raw chaos_function_name)" \
  --payload '{"mode":"cascade","lines":900}' \
  --cli-binary-format raw-in-base64-out \
  --profile bootcamp --region us-east-1 \
  /tmp/incident.json && python3 -m json.tool < /tmp/incident.json
```

This writes a realistic seven-phase incident into the workload log group: a
deploy, a quiet period, climbing latency with no errors, connection timeouts, a
retry storm, a circuit breaker opening, and customers seeing 503s.

**One interaction that confuses everyone the first time.** Metric filter
datapoints carry the **log event** timestamp, so the graphs fill in
immediately. Alarms evaluate on **wall-clock** periods, so they need a few
minutes to transition. The dashboard will look broken before the alarms do
anything. It is not.

---

## Step 3 — Read the logs yourself first

**~15 minutes. Do not skip this and do not read ahead.**

You are about to be shown a machine-generated summary of this incident. Its
value depends entirely on your being able to tell whether it is right, and you
cannot do that if you meet it before you have an opinion of your own.

Pull the window down and read it:

```bash
QUERY_ID=$(aws logs start-query \
  --log-group-name "$(terraform output -raw workload_log_group)" \
  --start-time $(( $(date +%s) - 3600 )) \
  --end-time $(date +%s) \
  --query-string 'fields @timestamp, level, error_type, latency_ms, message | sort @timestamp asc | limit 200' \
  --profile bootcamp --region us-east-1 \
  --query 'queryId' --output text)

sleep 5

aws logs get-query-results --query-id "$QUERY_ID" \
  --profile bootcamp --region us-east-1 \
  --query 'results[*][?field==`@message`].value' --output text | head -40
```

Then try the queries that matter during a real incident:

```bash
# When did it start? The first non-zero bin is your incident start.
'fields @timestamp | filter level = "ERROR" | stats count(*) as errors by bin(1m) | sort @timestamp asc'

# What is failing?
'fields @timestamp, error_type | filter level = "ERROR" | stats count(*) as n by error_type | sort n desc'

# WHAT CHANGED? — the first question of every incident review.
'fields @timestamp, @message | filter event in ["config_applied", "deploy_completed"] | sort @timestamp asc'
```

### Now write it down

**Write one sentence** — on paper, or in a message to yourself — saying what
you think happened and why. Keep it. You will compare it against the model's
answer in Step 6, and the comparison only works if you commit first.

<details>
<summary><strong>Hint, if you have been stuck for more than eight minutes</strong></summary>

Sort **ascending** and read the first ten lines.

The cause appears exactly once, at the very beginning, and it is not an ERROR.
Everything after it is consequence: hundreds of error lines all describing the
database, none of them mentioning what actually changed.

That asymmetry is not an accident of this lab. It is the shape of most
cascades, and it is why Step 6 is going to be interesting.

</details>

---

## Step 4 — Watch the alarms and the dashboard

**~15 minutes**

```bash
aws cloudwatch describe-alarms --alarm-name-prefix cbc-day06 \
  --profile bootcamp --region us-east-1 \
  --query 'MetricAlarms[].{Name:AlarmName,State:StateValue,Missing:TreatMissingData}' \
  --output table

aws cloudwatch describe-alarms --alarm-types CompositeAlarm \
  --alarm-name-prefix cbc-day06 \
  --profile bootcamp --region us-east-1 \
  --query 'CompositeAlarms[].{Name:AlarmName,State:StateValue,Rule:AlarmRule}' \
  --output table
```

Open the dashboard:

```bash
terraform output -raw dashboard_url
```

Read it in the order it was built to be read:

1. **Top row** — is it happening now?
2. **Middle row** — how bad? Look for the shape where **latency falls while
   errors stay high**. That is the circuit breaker failing fast, and on a
   latency-only dashboard it looks exactly like recovery.
3. **Bottom row** — what kind of failure, and then the raw log lines.

### The contrast worth waiting for

Stop invoking the chaos function and wait about ten minutes.

- The **no-telemetry** alarm goes to ALARM, because `treat_missing_data =
  "breaching"`. Silence is the breach.
- The **error-rate** alarm sits happily at OK the whole time, because zero
  errors out of zero requests is not a breach.

Both are correct. That contrast is why both alarms exist, and it is the single
most transferable idea in Part 4 of the day guide.

---

## Step 5 — Prove the composite alarm can fire

**~10 minutes**

A composite alarm that can never fire looks identical to one that works. Green,
billed monthly, reassuring. The only proof is forcing it.

```bash
# The three diagnostic alarms, in the order error-rate / latency / liveness.
terraform output -json metric_alarm_names

ERROR_RATE=$(terraform output -json metric_alarm_names | python3 -c 'import json,sys; print(json.load(sys.stdin)[0])')

aws cloudwatch set-alarm-state \
  --alarm-name "$ERROR_RATE" \
  --state-value ALARM --state-reason "deliberate test" \
  --profile bootcamp --region us-east-1
```

Within seconds the composite should transition and you should get mail. Check:

```bash
aws cloudwatch describe-alarms --alarm-types CompositeAlarm \
  --alarm-names "$(terraform output -raw composite_alarm_name)" \
  --profile bootcamp --region us-east-1 \
  --query 'CompositeAlarms[].StateValue' --output text
```

Now look at the deliberately broken one:

```bash
aws cloudwatch describe-alarms --alarm-types CompositeAlarm \
  --alarm-name-prefix cbc-day06-impossible \
  --profile bootcamp --region us-east-1 \
  --query 'CompositeAlarms[].AlarmRule' --output text
```

Read that rule and work out why nothing you do will ever move it. Then try —
force its child into ALARM and watch nothing happen.

**Do this for every composite alarm you ever build.** Ninety seconds, and it is
the only evidence there is.

---

## Step 6 — Now let the model read it

**~25 minutes. This is the day.**

### 6a. The good analyser

```bash
aws lambda invoke \
  --function-name "$(terraform output -raw analyser_function_name)" \
  --payload '{"alarmName":"manual-run","lookback_minutes":30}' \
  --cli-binary-format raw-in-base64-out \
  --profile bootcamp --region us-east-1 \
  /tmp/good.json

python3 - <<'PY'
import json
d = json.load(open("/tmp/good.json"))
f, a = d["deterministic_facts"], d["analysis"]
print("== MEASURED (no model involved) ==")
print("  error rate :", f["error_rate_pct"], "%")
print("  latency    :", f["latency_ms"])
print("  first error:", f["first_error"])
print("  CHANGES    :", f["change_events_in_window"])
print()
print("== SAMPLING ==")
print(" ", d["sampling"]["strategy"], d["sampling"]["sampled_lines"], "of",
      d["sampling"]["total_lines"], f'({d["sampling"]["coverage_pct"]}%)')
print()
print("== NARRATIVE ==")
print("  summary   :", a["summary"])
print("  root cause:", a["root_cause"])
print("  next check:", a["recommended_next_check"])
print("  grounding :", a["grounding"]["claims_verified"], "/", a["grounding"]["claims_total"])
for c in a["claims"]:
    print("   ", "OK " if c["verified"] else "BAD", c["claim"], "-> line", c["cite"])
PY
```

Four things to do with that output, in order:

1. **Read the measured block first.** No model was involved in producing it. On
   most incidents it is already the answer — and notice that the change events
   are found by a Python `if` statement, not by intelligence.
2. **Compare the narrative against your sentence from Step 3.** Did it find
   what you found?
3. **Follow a citation.** Pick a claim, take its line index, and go and look at
   that log line. The quoted fragment should be there verbatim, because the
   Lambda already checked — but check it yourself once, because that is the
   habit the whole design exists to support.
4. **Read the coverage.** It saw some fraction of the window and it says so.

### 6b. The naive analyser, on the same incident

```bash
aws lambda invoke \
  --function-name "$(terraform output -raw naive_analyser_function_name)" \
  --payload '{"alarmName":"naive-run"}' \
  --cli-binary-format raw-in-base64-out \
  --profile bootcamp --region us-east-1 \
  /tmp/naive.json

python3 - <<'PY'
import json
d = json.load(open("/tmp/naive.json"))
a = d["analysis"]
print("  strategy  :", d["sampling"]["strategy"])
print("  warning   :", d["sampling"].get("warning"))
print("  summary   :", a["summary"])
print("  root cause:", a["root_cause"])
print("  confidence:", a["confidence"])
PY
```

**Identical zip file. Identical model. Identical incident.** Four environment
variables differ: no redaction, no token budget, tail-only sampling, and
`bedrock:InvokeModel` on `Resource: "*"`.

Put the two summaries side by side and answer honestly: **which one would you
have acted on at 03:00?** Both read as authoritative. Only one is right.

Then look at why:

```bash
python3 - <<'PY'
import json
for name, path in (("good", "/tmp/good.json"), ("naive", "/tmp/naive.json")):
    s = json.load(open(path))["sampling"]
    print(f"{name:6} {s['strategy']:40} {s['sampled_lines']} of {s['total_lines']}")
PY
```

The naive one sampled the tail. The deploy line is in the first 1% of the
window. **It was never shown the cause** — so it explained the consequences,
perfectly, and there is nothing in its output to say the cause was missing.

A model cannot report a gap it was never told about. That is the sentence to
take away from this lab.

### 6c. Optional — permission to say nothing

**~3 minutes.** Generate an incident with the cause removed entirely, then run
the **good** analyser on it:

```bash
aws lambda invoke \
  --function-name "$(terraform output -raw chaos_function_name)" \
  --payload '{"mode":"cascade","include_cause":false}' \
  --cli-binary-format raw-in-base64-out \
  --profile bootcamp --region us-east-1 /tmp/nocause.json
```

With `insufficient_evidence` in the schema and a prompt that says returning it
is a correct answer, it will often decline to name a cause. That behaviour had
to be deliberately built a route to — because a model asked "what caused this"
will otherwise always produce a cause, since that is what it was asked for.

---

## Step 7 — Run the auditor

**~20 minutes**

```bash
cd ../python
pip install -r requirements.txt
python3 obs_audit.py --profile bootcamp --region us-east-1 --prefix cbc-day06
```

Expect **15 findings, 144 points, 0/100, grade F.**

```bash
python3 obs_audit.py --format json --quiet --prefix cbc-day06 > findings.json
python3 obs_audit.py --format csv --min-severity HIGH --prefix cbc-day06
python3 obs_audit.py --fail-on CRITICAL --prefix cbc-day06 ; echo "exit: $?"
```

`--min-severity HIGH` shows 7 of the 15 and the score stays 0/100.
**Filtering the display must never flatter the score**, or people improve their
posture by passing `--min-severity CRITICAL`.

### Notice what did not change

You have just generated a real incident, watched three alarms transition, paged
yourself and run two analysers — and the auditor's output is **identical to
what it was immediately after `apply`**.

That is not a limitation. `obs_audit.py` audits **configuration**; monitoring
watches **runtime**. Treating either one as the other is the category error
this day exists to prevent.

### The finding contract

```
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
```

---

## Step 8 — Break two more things on purpose

**~15 minutes**

Two checks are currently silent. Make each one fire, and notice that they are
silent for very different reasons.

### 8a. Send the logs to a model in another region — OBS-013

```bash
cd ../terraform
echo 'bedrock_region = "eu-west-1"' >> terraform.tfvars
terraform apply -auto-approve
```

### 8b. Take away the dead-man's switch — OBS-009

Outside Terraform, the way it actually happens:

```bash
LIVENESS=$(terraform output -json metric_alarm_names | python3 -c 'import json,sys; print(json.load(sys.stdin)[2])')

aws cloudwatch put-metric-alarm \
  --alarm-name "$LIVENESS" \
  --namespace "$(terraform output -raw metric_namespace)" \
  --metric-name RequestCount --statistic Sum --period 60 \
  --evaluation-periods 10 --datapoints-to-alarm 10 \
  --threshold 1 --comparison-operator LessThanThreshold \
  --treat-missing-data notBreaching \
  --profile bootcamp --region us-east-1
```

`put-metric-alarm` is an upsert: same name, same everything else, one attribute
different. That is what makes it a realistic way to break something.

One command. No code review. No Terraform diff. And the alarm that detects
silence now treats silence as fine.

```bash
cd ../python
python3 obs_audit.py --profile bootcamp --region us-east-1 --prefix cbc-day06
```

**18 findings, 174 points, still 0/100.**

Three new findings, not two: OBS-009 once, and **OBS-013 twice** — because
`bedrock_region` is one variable feeding both analysers, and a shared setting
does not care which of your functions was carefully written.

### The difference between the two

| | OBS-013 | OBS-009 |
|---|---|---|
| Why it was silent | The stack **cannot** produce the fault — one variable feeds both the log region and the model ARN | One alarm **happened** to be configured correctly |
| How it fired | A deliberate edit to a variable | One CLI command, outside Terraform |
| What its silence told you | Something about the auditor: it does not cry wolf | Nothing about the auditor; only about today |

**Silent by design** is evidence. **Silent by situation** is a snapshot. Never
read the second as the first — which is an argument for running the auditor on
a schedule, not only at merge time.

Put it back:

```bash
cd ../terraform
# Remove the bedrock_region line you appended, then:
terraform apply -auto-approve
```

Notice that `terraform plan` catches 8b as drift. Nothing else would have.

---

## Step 9 — The reference build

**~10 minutes**

Set both toggles and re-run:

```hcl
create_insecure_examples          = false
enable_bedrock_invocation_logging = true
```

```bash
terraform apply -auto-approve
cd ../python
python3 obs_audit.py --profile bootcamp --region us-east-1 --prefix cbc-day06
```

**0 findings, 100/100, grade A.**

Now do the part that matters: **look at which checks went silent and satisfy
yourself that each went silent for a reason** rather than because the auditor
stopped looking. A tool that cannot return a clean result on clean input is a
tool nobody can ever finish fixing things with — and a tool whose clean result
you cannot explain is worse.

One thing to sit with: turning on `enable_bedrock_invocation_logging` fixed
OBS-016 by writing every prompt and completion to a log group readable by
anyone with CloudWatch access. You fixed an audit gap by opening a data-access
gap. **Both halves or neither** — go and look at what retention and resource
policy that group has.

### Build the auditor yourself

**~130 minutes**, separately from this lab:

```bash
cd lab/python
OBS_AUDIT_MODULE=obs_audit_challenge python3 -m unittest discover -s tests -v
```

47 tests, no credentials, no account, under a second. 16 numbered TODOs in
`challenge/obs_audit_challenge.py`, each with exact fields, a hint and a
checkpoint.

---

## Step 10 — Destroy, and verify

**~15 minutes**

```bash
cd ../terraform
terraform destroy -auto-approve
```

**`destroy` is genuinely not enough on this day**, and not because you were
careless. Three categories of cost here are structurally invisible to it:

- **Log groups Terraform did not create.** It does not know they exist.
- **Custom metrics.** There is no delete API at all. They age out fifteen
  months after their last datapoint.
- **Bedrock spend.** There was never a resource, so no sweep can find it.

Work through
[`../teardown-checklist.md`](../teardown-checklist.md) and run the
verification script at the bottom of it.

And if you enabled invocation logging in Step 9 in a shared account: it is an
**account-level, region-singleton** setting, so destroying it turned logging
off for everyone in that region. Tell them.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `AccessDeniedException` from Bedrock | Model access not requested | Console → Bedrock → Model access |
| No alarm email | Unconfirmed SNS subscription | Check `list-subscriptions-by-topic` for `PendingConfirmation` |
| Dashboard empty | Chaos not run, or you are in the wrong region | Run Step 1; check the widget `region` |
| Alarms stuck in `INSUFFICIENT_DATA` | Backdated events fill graphs; alarms evaluate on wall-clock | Wait a few minutes, or invoke the chaos function again |
| Analyser times out | Timeout lowered below 30s | A Logs Insights query plus a model call needs the default 180 |
| Analyser returns `{"skipped": "idempotency"}` | You invoked it twice inside the window | Working as designed — wait, or set `analyser_idempotency_minutes = 0` |
| `terraform destroy` leaves a log group | It was created outside Terraform | `aws logs delete-log-group --log-group-name <name>` |
| Auditor reports 0 findings | `create_insecure_examples = false` | That is Step 9, not a bug |

---

## What to take away

1. **"Never expire" is not a setting anyone chose.** Create log groups in code,
   with retention, before the thing that writes to them.
2. **Every stack needs one alarm that treats missing data as breaching.** It is
   the only one that catches a service that went dark.
3. **A composite alarm you have not forced is decoration.**
4. **A dimension value must come from a set you could write on a napkin.** The
   alternative is undeletable and bills for fifteen months.
5. **Read the logs before the summary.** You cannot check an answer you met
   before you had an opinion.
6. **A model cannot report a gap it was never told about.** Which is why
   sampling, coverage reporting and checked citations are not polish — they are
   the tool.
