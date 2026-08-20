# Day 07 Lab — Automated Threat Response

**CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp**

Deploy detection and evidence, generate a threat on demand, watch automation
contain it in seconds — then **make the detector wrong on purpose and reverse
the containment by hand** before you are shown the documented rollback.

Knowing containment is undoable and knowing *how* are different things, and
the gap between them is where incidents get worse.

| | |
|---|---|
| **Time** | ~2h 45m |
| **Cost** | ~$2.85/month countable; three cost lines are usage-based and not in that number |
| **Region** | `us-east-1` · profile `bootcamp` · prefix `cbc-day07-` |
| **Needs** | Terraform ≥ 1.10 or OpenTofu ≥ 1.8, Python 3.9+, boto3, and an account you are willing to enable GuardDuty in |

---

## Steps at a glance

| Step | What | Time |
|---|---|---|
| [0](#step-0--deploy) | Deploy, and confirm **both** SNS subscriptions | 20m |
| [1](#step-1--prove-the-trail-is-evidence) | Prove the trail is evidence, not logs | 15m |
| [2](#step-2--generate-threats-on-demand) | Generate threats on demand | 10m |
| [3](#step-3--read-the-severities-and-do-not-trust-them) | Read the severities, and do not trust them | 15m |
| [4](#step-4--watch-it-reach-security-hub) | Watch it reach Security Hub | 10m |
| [5](#step-5--find-out-whether-rotation-has-ever-run) | Find out whether rotation has ever run | 15m |
| [6](#step-6--watch-the-responder-decide) | Watch the responder decide — including doing nothing | 25m |
| [7](#step-7--flip-the-kill-switch) | Flip the kill switch | 10m |
| [8](#step-8--run-the-auditor-then-break-two-things) | Run the auditor, then break two things | 25m |
| [9](#step-9--the-reference-build) | The reference build | 10m |
| [10](#step-10--destroy-and-verify) | Destroy, and verify | 20m |

---

## Before you start

**This lab enables GuardDuty and Security Hub.** Both are
account-and-region-level services with a 30-day free trial and a real cost
afterwards, and both keep billing until disabled — in every region, not just
this one. Use an account you are willing to do that in, and read
[`../teardown-checklist.md`](../teardown-checklist.md) before you decide.

**Step 6 needs one real EC2 instance** in the same VPC as the quarantine
security group. A `t3.micro` with nothing on it is fine. Launch it now so it is
ready.

---

## Step 0 — Deploy

**~20 minutes**

```bash
cd day-07-enterprise-cloud-security/lab/terraform
cp terraform.tfvars.example terraform.tfvars
```

Set **one** value:

```hcl
notification_email = "you@example.com"
```

Read the commented-out block anyway. On this day some of the defaults are the
difference between a lab that costs three dollars and one that costs several
hundred, and each of those says so.

```bash
terraform init
terraform plan
terraform apply
```

While it runs, read `main.tf` section 3 — the GuardDuty severity argument. It
is the sentence the rest of the day turns on.

### Confirm BOTH subscriptions

Two emails arrive: one for findings, one for containment actions.

```bash
aws sns list-subscriptions-by-topic \
  --topic-arn "$(terraform output -raw sns_topic_arn)" \
  --profile bootcamp --region us-east-1 \
  --query 'Subscriptions[].SubscriptionArn' --output text

aws sns list-subscriptions-by-topic \
  --topic-arn "$(terraform output -raw containment_topic_arn)" \
  --profile bootcamp --region us-east-1 \
  --query 'Subscriptions[].SubscriptionArn' --output text
```

`PendingConfirmation` means go and click.

This matters more here than on Day 04 or Day 06. **An unconfirmed containment
subscription means the automation can isolate a production instance and nobody
is told** — the action succeeds, the notification is discarded, and the first
anyone knows is a customer ticket.

```bash
terraform output next_steps
terraform output cost_breakdown
terraform output silent_cost_growth
```

Note which lines in `cost_breakdown` say `USAGE-BASED, NOT ESTIMATED`. Three of
them do, and they are the ones that matter.

---

## Step 1 — Prove the trail is evidence

**~15 minutes**

CloudTrail takes about 15 minutes to make the first delivery. Check:

```bash
aws s3 ls "s3://$(terraform output -raw trail_bucket)/AWSLogs/" --recursive \
  --profile bootcamp | head
```

Once objects exist, run the command that turns logs into evidence:

```bash
terraform output -raw trail_validation_command
# then run it
```

You should get `Results requested for <range>` and a count of digest and log
files validated, with no invalid ones.

### Now do it against the broken trail

```bash
aws cloudtrail validate-logs \
  --trail-arn "arn:aws:cloudtrail:us-east-1:$(aws sts get-caller-identity --query Account --output text --profile bootcamp):trail/cbc-day07-shadow-trail-XXXX" \
  --start-time "$(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%SZ)" \
  --profile bootcamp --region us-east-1
```

It fails, because there are no digest files to validate against. **That is the
entire difference between the two trails**, it is one boolean in the Terraform,
and it is free.

Run the good one once now so you know the command works before the day you
need it.

---

## Step 2 — Generate threats on demand

**~10 minutes**

```bash
DETECTOR=$(terraform output -raw guardduty_detector_id)

aws guardduty create-sample-findings --detector-id "$DETECTOR" \
  --finding-types \
    "CryptoCurrencyMining:EC2/BitcoinTool.B!DNS" \
    "UnauthorizedAccess:EC2/SSHBruteForce" \
    "Backdoor:EC2/C&CActivity.B!DNS" \
    "Recon:EC2/PortProbeUnprotectedPort" \
  --profile bootcamp --region us-east-1

aws guardduty list-findings --detector-id "$DETECTOR" \
  --profile bootcamp --region us-east-1 --query 'FindingIds' --output text
```

**Leave these unresolved.** Step 8 needs them sitting untriaged.

---

## Step 3 — Read the severities, and do not trust them

**~15 minutes**

```bash
aws guardduty get-findings --detector-id "$DETECTOR" \
  --finding-ids $(aws guardduty list-findings --detector-id "$DETECTOR" \
    --profile bootcamp --region us-east-1 --query 'FindingIds' --output text) \
  --profile bootcamp --region us-east-1 \
  --query 'Findings[].{Type:Type,Sev:Severity,Title:Title}' --output table
```

Look at what you have: four findings, a spread of severities, and titles
prefixed `[SAMPLE]` on fake instance ids like `i-99999999`.

**Now answer this before moving on.** For each finding type, write down whether
you would let automation contain a production instance on it, with no human, at
03:00.

<details>
<summary><strong>What most people conclude, and why it is the right answer</strong></summary>

Cryptomining and command-and-control DNS: **yes**. There is no innocent reason
for an instance to resolve a mining pool or a known C2 domain. Rarely false
positives.

SSH brute-force and port probing: **no**. That is the internet. Anything with a
public IP sees it constantly, and containing on it means containing on
background noise.

Notice what you did **not** use to decide: the severity number. All four can be
HIGH. Severity scores **impact if real**, not confidence that it is real — a
severity-7 finding is as likely to be your own penetration test as a
compromise.

That is why `respond_to_finding_types` is an allow-list of **types** and check
SEC-005 fires on anything that uses a threshold instead.

</details>

---

## Step 4 — Watch it reach Security Hub

**~10 minutes**

```bash
aws securityhub get-findings \
  --filters '{"ProductName":[{"Value":"GuardDuty","Comparison":"EQUALS"}]}' \
  --profile bootcamp --region us-east-1 \
  --query 'Findings[].{Sev:Severity.Label,Type:Types[0],Title:Title}' --output table
```

There is **no resource wiring these together**. The integration is automatic
when both services are enabled in a region, which is why people go looking for
one and do not find it.

Then look at the compliance side:

```bash
aws securityhub get-findings \
  --filters '{"ComplianceStatus":[{"Value":"FAILED","Comparison":"EQUALS"}]}' \
  --profile bootcamp --region us-east-1 \
  --query 'length(Findings)' --output text
```

That number is from **one** standard, in an account with almost nothing in it.
Multiply it by your real resource count, then by three if you had enabled every
standard, and you have the number nobody ever drives to zero.

Controls take a few hours to run for the first time. If this is empty, it is
not broken.

---

## Step 5 — Find out whether rotation has ever run

**~15 minutes**

```bash
terraform output -raw rotation_health_command
# then run it
```

You will get `RotationEnabled: true` and **`LastRotatedDate: null`**.

Rotation is configured and has never run. `rotate_immediately` is false, so
that is correct rather than broken — and it is exactly the state that looks
green on a dashboard while a credential goes unchanged for months.

**`RotationEnabled` only means a schedule exists. `LastRotatedDate` is the only
field that means it ran.**

Force one and watch all four steps:

```bash
aws secretsmanager rotate-secret \
  --secret-id "$(terraform output -raw managed_secret_arn)" \
  --profile bootcamp --region us-east-1

aws logs tail /aws/lambda/cbc-day07-rotator-XXXX --follow \
  --profile bootcamp --region us-east-1
```

You should see `createSecret`, `setSecret`, `testSecret`, `finishSecret`.

**Read what `setSecret` logs.** It is a no-op, loudly, because this lab has no
database to push to. Then read the docstring in
`lambda/secret_rotator.py` on why that is the most dangerous thing a rotator
can be: every rotation "succeeds", `LastRotatedDate` updates, the console is
green, and the credential in the database never changes. A scheduled outage
that passes its own compliance check.

Re-run the health command. `LastRotatedDate` is now set.

---

## Step 6 — Watch the responder decide

**~25 minutes. This is the day.**

Containment mode is `dry-run` by default. Leave it there for 6a.

### 6a. A finding on the allow-list

Replace `i-0YOURINSTANCE` with your real instance id:

```bash
cat > /tmp/finding.json <<'EOF'
{"detail":{
  "id":"lab-6a",
  "type":"CryptoCurrencyMining:EC2/BitcoinTool.B!DNS",
  "severity":8.0,
  "title":"EC2 instance is querying a domain associated with cryptocurrency mining",
  "resource":{"resourceType":"Instance",
              "instanceDetails":{"instanceId":"i-0YOURINSTANCE"}}
}}
EOF

aws lambda invoke \
  --function-name "$(terraform output -raw responder_function_name)" \
  --cli-binary-format raw-in-base64-out --payload file:///tmp/finding.json \
  --profile bootcamp --region us-east-1 /tmp/dry.json

python3 -m json.tool < /tmp/dry.json
```

Four things in that output, in this order:

1. **`decision: DRY-RUN`** — nothing changed.
2. **`previous_security_groups`** — recorded *before* any decision.
3. **`rollback_command`** — the exact command, already, in dry-run.
4. **`reason`** — it names the **type**, not the severity.

### 6b. A finding that is not on the allow-list

```bash
sed 's/CryptoCurrencyMining:EC2\/BitcoinTool.B!DNS/UnauthorizedAccess:EC2\/SSHBruteForce/; s/lab-6a/lab-6b/' \
  /tmp/finding.json > /tmp/finding-noise.json

aws lambda invoke \
  --function-name "$(terraform output -raw responder_function_name)" \
  --cli-binary-format raw-in-base64-out --payload file:///tmp/finding-noise.json \
  --profile bootcamp --region us-east-1 /tmp/noise.json

python3 -c "import json;d=json.load(open('/tmp/noise.json'));print(d['decision'],'|',d['reason'])"
```

**It produced a record and a notification saying it did nothing, and why.**

That is deliberate. The EventBridge rule matches *all* findings rather than
filtering, because a finding filtered out at the broker produces no invocation,
no log line and no notification — indistinguishable from the rule being broken.
*"Why did nothing happen"* is asked far more often than the opposite.

### 6c. Contain for real

```bash
# terraform.tfvars: containment_mode = "isolate"
terraform apply -auto-approve

aws lambda invoke \
  --function-name "$(terraform output -raw responder_function_name)" \
  --cli-binary-format raw-in-base64-out --payload file:///tmp/finding.json \
  --profile bootcamp --region us-east-1 /tmp/live.json

aws ec2 describe-instances --instance-ids i-0YOURINSTANCE \
  --query 'Reservations[].Instances[].SecurityGroups' \
  --profile bootcamp --region us-east-1
```

The security groups have been replaced. Check your inbox.

Now try to reach the instance. **You cannot** — no SSH, no Session Manager. You
have contained the incident and destroyed your own ability to investigate from
inside the box. Read `main.tf` section 9 on the quarantine group that permits
egress to the SSM endpoints only, which is the design you actually want.

### 6d. Reverse it BY HAND, before you look at the rollback command

**Do not read `/tmp/live.json` yet.**

From what you can see in the console right now, restore the instance's original
security groups.

<details>
<summary><strong>Once you have tried</strong></summary>

If you could not do it, that is the point of this step. The original groups are
gone from the instance and there is nothing on screen that says what they were.

Two places recorded them, both put there deliberately:

```bash
aws ec2 describe-tags --filters "Name=resource-id,Values=i-0YOURINSTANCE" \
  --query 'Tags[?starts_with(Key, `Security`)]' \
  --profile bootcamp --region us-east-1 --output table

python3 -c "import json;print(json.load(open('/tmp/live.json'))['rollback_command'])"
```

"Reversible in principle" and "reversible by the person on call at 3am who did
not build this" are different claims. Recording the previous state **before**
changing it is what converts one into the other.

The tags exist for a reason that is not obvious: an isolated instance nobody
can explain gets terminated three weeks later by somebody tidying up, along
with the evidence.

</details>

Now run the rollback command and confirm the groups are back.

### 6e. The naive responder, on the same instance

```bash
aws lambda invoke \
  --function-name "$(terraform output -raw naive_responder_function_name)" \
  --cli-binary-format raw-in-base64-out --payload file:///tmp/finding-noise.json \
  --profile bootcamp --region us-east-1 /tmp/naive.json

python3 -m json.tool < /tmp/naive.json
```

**Identical zip file. Identical model of the world. Three environment
variables and one IAM policy different.**

It acted on `SSHBruteForce` — background noise the good responder refuses —
because severity 8.0 cleared its threshold. Then read the `action` field:

```
"action": "REFUSED",
"reason": "CONTAINMENT_MODE='terminate' is not a mode this responder implements..."
```

The shared code protected you. **Ask yourself whether that makes the
configuration acceptable.**

It does not. The next person to "fix" this responder will implement what the
configuration asks for, because the configuration is what somebody wrote down
as the intent. That is why SEC-012 fires on configured intent rather than on
observed behaviour.

And look at what its role can do:

```bash
aws iam get-role-policy --role-name cbc-day07-naive-responder-XXXX \
  --policy-name cbc-day07-naive-responder-policy \
  --query 'PolicyDocument.Statement[0].Action' \
  --profile bootcamp --region us-east-1
```

`cloudtrail:*` and `iam:*`. **This function can delete the trail that recorded
what it just did.**

---

## Step 7 — Flip the kill switch

**~10 minutes**

A kill switch nobody has ever flipped is a hypothesis.

```bash
terraform output -raw kill_switch_command
# then run it

aws lambda invoke \
  --function-name "$(terraform output -raw responder_function_name)" \
  --cli-binary-format raw-in-base64-out --payload file:///tmp/finding.json \
  --profile bootcamp --region us-east-1 /tmp/killed.json

python3 -c "import json;d=json.load(open('/tmp/killed.json'));print(d['decision'],'|',d['reason'])"
```

`NO ACTION | kill switch is DISARMED`.

**No apply. No plan. No pipeline.** That is the entire point — at 03:00 you do
not have those, and disabling the EventBridge rule requires all three.

Two properties worth confirming while you are here:

```bash
# It cannot disarm its own brake. This should be Deny.
aws iam get-role-policy --role-name cbc-day07-responder-XXXX \
  --policy-name cbc-day07-responder-policy \
  --query 'PolicyDocument.Statement[?Sid==`DenyDisablingItsOwnBrake`]' \
  --profile bootcamp --region us-east-1
```

And read `kill_switch_armed()` in `lambda/threat_responder.py`: if the
parameter is unreadable it returns **False**, not True. Automation that keeps
containing production while its own control plane is broken is worse than
automation that stops.

Put it back:

```bash
aws ssm put-parameter --name /cbc-day07/kill-switch --value ARMED \
  --type String --overwrite --profile bootcamp --region us-east-1
```

---

## Step 8 — Run the auditor, then break two things

**~25 minutes**

```bash
cd ../python
pip install -r requirements.txt
python3 sec_audit.py --profile bootcamp --region us-east-1 --prefix cbc-day07
```

Because you forced a rotation in Step 5 and left findings untriaged in Step 2,
you are in the **live** state: **11 findings, 131 points, 0/100.**

```bash
python3 sec_audit.py --format json --quiet --prefix cbc-day07 > findings.json
python3 sec_audit.py --fail-on CRITICAL --prefix cbc-day07 ; echo "exit: $?"
```

### The same count is not the same account

Immediately after `apply` this stack reports **11 findings and 137 points**.
Now it reports **11 findings and 131 points**. One check cleared, another
appeared:

- **SEC-011** fired at apply because rotation had never run. Step 5 cleared it.
- **SEC-003** was silent at apply because no findings existed. Step 2 created
  it.

Eleven before, eleven after, six points apart, **different problem**.

> **Never diff on the count.** A dashboard that trends "11" reports a stability
> that is not there.

This is the direct contrast with Day 06, where static and live were identical
because every check read configuration only. Three checks here read runtime
state, and `findings.json` names them in `runtime_dependent_checks`.

### The finding contract

```
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
```

### 8a. Slow the publishing frequency down — SEC-004

```bash
cd ../terraform
echo 'guardduty_finding_publishing_frequency = "SIX_HOURS"' >> terraform.tfvars
terraform apply -auto-approve
```

### 8b. Make time visible — SEC-013

The lab created an IAM user with a long-lived access key, and the check finds
nothing, because the key is hours old.

**Nothing has to change for that to stop being true.** In ninety-one days the
same unchanged account fails the same unchanged check. Rather than wait:

```bash
cd ../python
python3 sec_audit.py --profile bootcamp --region us-east-1 --prefix cbc-day07 \
  --max-access-key-age-days 0
```

**13 findings, 136 points, still 0/100.**

### The two silences are not the same kind

| | SEC-004 | SEC-013 |
|---|---|---|
| Why it was silent | The stack **cannot** produce the fault — the variable validates to three values and defaults to the right one | The key **happens** to be new |
| How it fired | A deliberate edit | A threshold change standing in for the passage of time |
| What its silence told you | The auditor does not cry wolf | Only what today looks like |

**Silent by design** is evidence about the tool. **Silent by situation** is a
snapshot. SEC-013 is the sharper of the two, because it needs nobody to do
anything at all — which is the argument for running this on a schedule rather
than at merge time. A point-in-time pass is not a property that persists.

Put 8a back:

```bash
cd ../terraform
# remove the guardduty_finding_publishing_frequency line, then:
terraform apply -auto-approve
```

---

## Step 9 — The reference build

**~10 minutes**

```hcl
create_insecure_examples = false
```

```bash
terraform apply -auto-approve
cd ../python
python3 sec_audit.py --profile bootcamp --region us-east-1 --prefix cbc-day07
```

**0 findings, 100/100, grade A** — provided rotation has run. If you skipped
Step 5 you get 1 finding, 10 points and 90/100, and the one finding is SEC-011.

Now do the part that matters: **look at which checks went silent and satisfy
yourself that each went silent for a reason** rather than because the auditor
stopped looking. A tool that cannot return a clean result on clean input is a
tool nobody can finish fixing things with — and a tool whose clean result you
cannot explain is worse.

### Build the auditor yourself

**~2 hours**, separately from this lab:

```bash
cd lab/python
SEC_AUDIT_MODULE=sec_audit_challenge python3 -m unittest discover -s tests -v
```

47 tests, no credentials, under a second. 16 numbered TODOs, each with exact
fields, a hint and a checkpoint. Read the briefing's section on the clock
before TODO 1 — three checks are age-based and reaching for `datetime.now()`
will pass the fire tests and break the divergence test.

---

## Step 10 — Destroy, and verify

**~20 minutes**

**Detach the quarantine group first.** If any instance is still isolated,
`destroy` fails with `DependencyViolation` — correct behaviour, annoying way to
find out:

```bash
aws ec2 describe-instances \
  --filters "Name=instance.group-name,Values=cbc-day07-quarantine-*" \
  --query 'Reservations[].Instances[].InstanceId' \
  --profile bootcamp --region us-east-1 --output text
```

Then:

```bash
cd ../terraform
terraform destroy -auto-approve
```

**`destroy` removes this stack and leaves the two most expensive things
running.** GuardDuty and Security Hub are account-and-region-level services;
this stack only enabled them in one region, and if you enabled them elsewhere
nothing here will find them.

Also outliving `destroy`: secrets, which enter a 7-day recovery window rather
than deleting.

Work through [`../teardown-checklist.md`](../teardown-checklist.md) and run the
verification script at the bottom. Its section 6 is a cross-region sweep and it
is the part `terraform destroy` structurally cannot do.

**And terminate the EC2 instance you launched for Step 6.** Terraform does not
know about it.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `validate-logs`: no digest found | Under an hour of delivered objects | Wait, or come back to Step 1 later |
| Security Hub findings empty | First control run takes hours | Not broken. Move on and check later. |
| Responder does nothing on a real finding | It is a `[SAMPLE]`, and `ACT_ON_SAMPLES=false` | Correct behaviour. Use the hand-written payload in Step 6. |
| `destroy` fails with `DependencyViolation` | An instance still wears the quarantine group | Restore its groups first — Step 10 |
| Cannot recreate a secret after destroy | 7-day recovery window | `delete-secret --force-delete-without-recovery`, lab only |
| Rotation log shows only one step | You read it too early | `logs tail --follow` and re-run `rotate-secret` |
| Auditor says 11 findings, contract says 11 too, but points differ | You are in live, not static | That is the lesson — check the set, not the count |
| Instance unreachable after Step 6c | It is isolated. That is containment working. | Roll back with the recorded command |

---

## What to take away

1. **Severity is impact, not confidence.** Automate on finding type, or you
   will contain your own penetration test.
2. **Log file validation is the difference between logging and evidence**, it
   is free, and you should run the command once before you need it.
3. **`RotationEnabled` means a schedule exists. `LastRotatedDate` means it
   ran.**
4. **Reversible in principle is not reversible at 3am.** Record the previous
   state before you change it, and put the rollback in the notification.
5. **A kill switch must be runtime, fail safe, unwritable by the thing it
   brakes, and tested.**
6. **Everything that made the good responder safe was configuration** — which
   means none of it went through a code review.
7. **The same finding count is not the same account.** Diff the set.
