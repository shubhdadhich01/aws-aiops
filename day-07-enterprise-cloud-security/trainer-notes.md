# Day 07 — Trainer Notes

**CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp**

Internal. Minute-by-minute timing, the two live demos, and the places this day
goes wrong in a room.

**Total taught: 3h 50m** including two breaks.

---

## Before the room

### The night before

- [ ] **Apply in your demo account and leave it running overnight.** GuardDuty
      needs time to settle, Security Hub's first control run takes a few hours,
      and a cold Security Hub console is an anticlimax.
- [ ] Confirm **both** SNS subscriptions — findings and containment.
- [ ] Generate a couple of sample findings so the console is not empty.
- [ ] **Run `validate-logs` yourself** and have the successful output ready. It
      needs at least an hour of delivered objects, and "no digest found" in
      front of a room is a bad five minutes.
- [ ] Force one rotation and read the four steps in the rotator log group.
- [ ] **Run both demos end to end.** Demo 2 depends on an EC2 instance
      existing; see its setup section.
- [ ] `python3 -m unittest discover -s tests` → 47/47.

### The thing to say in the first two minutes

> Today has a boundary in it. Everything before the second break only
> OBSERVES — nothing we build can change the account. After the break we cross
> that line, and we are going to spend more time on what the automation
> refuses to do than on what it does.

That framing does two jobs. It stops the "when do we get to the automation"
undercurrent, and it sets up Demo 2, which is the point of the day.

---

## Timing

| Time | Block | Notes |
|---|---|---|
| 0:00 | **Scenario and the day's argument** | 10m |
| 0:10 | **Part 1** — GuardDuty, severity vs confidence | 25m |
| 0:35 | **Lab Steps 0–2** — apply, confirm SNS, generate findings | 25m |
| 1:00 | **Part 3** — CloudTrail as evidence | 20m |
| 1:20 | **Break** | 10m |
| 1:30 | **Part 2 + Part 4** — Security Hub, Secrets Manager rotation | 30m |
| 2:00 | **Lab Steps 3–5** — validate logs, read severities, force a rotation | 25m |
| 2:25 | **Break** | 10m |
| 2:35 | **Part 5** — automated response | 35m |
| 3:10 | **DEMO 1 and DEMO 2** | 25m |
| 3:35 | **Part 7–8** — cost, the auditor, the contract | 15m |
| 3:50 | End |

Self-paced: about 2h 45m.

### If you are running short

Cut in this order:

1. **Part 2's Security Hub mechanics** — the "enable one standard" argument
   survives in one sentence.
2. **Part 4's four-step protocol detail** — keep `LastRotatedDate`, drop the
   step-by-step.
3. **Part 3's bucket policy mechanics** — keep validation and multi-region.

**Never cut:** severity-is-not-confidence, the kill switch, the Denies on the
responder role, and Demo 2.

---

## Block-by-block notes

### 0:10 — Part 1 (25m)

Open with the list and let the room do the work:

> A finding arrives. Severity 7.5, HIGH. Give me five things it could be.

They will get to "a real attack" and stall. Prompt them: your own pen test?
your own scanner? a researcher? somebody on hotel wifi? Then:

> All five produce 7.5. If your automation triggers on severity greater than
> seven, all five get contained. Four of them are your own people.

**This is the moment the day lands or it does not.** Do not rush it.

Then show the finding-type table and make the point that confidence lives in
the type, not the number.

### 0:35 — Lab Steps 0–2 (25m)

Two things while `apply` runs:

- **Confirm both SNS subscriptions now.** Say plainly why it is worse here than
  on Day 04 or 06: the automation can isolate a production instance and the
  notification is silently discarded.
- **Bedrock-style trap check:** get everyone to note that `create-sample-findings`
  produces `[SAMPLE]`-prefixed titles with fake instance ids. They will need
  that in Demo 2.

### 1:00 — Part 3 (20m)

Do `validate-logs` **live**, and then do it against the shadow trail and read
the failure. The contrast is the whole section and it takes ninety seconds.

The sentence worth writing on the board:

> Without validation you have logging. With it you have evidence. The
> difference matters exactly once, and then completely.

### 1:30 — Parts 2 and 4 (30m)

Security Hub is fifteen minutes: enable one standard, why not all, and the
per-control-per-resource-per-day pricing shape.

Spend the other fifteen on **`LastRotatedDate`**. Ask the room:

> `RotationEnabled` is true. Is rotation working?

Most will say yes. Then walk the failure: the Lambda throws, the next-rotation
date keeps moving, nothing is red, the credential has not changed since March.
Then the sentence:

> A rotator with `setSecret` stubbed out is a scheduled outage that passes its
> own compliance check.

Finish with the Day 06 callback — rotating does nothing about the copy of the
old value sitting in a log group with no retention. In a room that did Day 06,
this gets a visible reaction.

### 2:35 — Part 5 (35m)

Order matters:

1. **What must never be automated** — do this FIRST, before any mechanism. It
   frames everything that follows as constraints rather than features.
2. **Contain, do not destroy** — the table.
3. **Reversible in principle vs reversible at 03:00.** Show the notification
   with the previous security groups and the rollback command in it.
4. **The kill switch** — four properties, and "a kill switch nobody has flipped
   is a hypothesis".
5. **The Denies** — read them out. Ask why each one is a Deny and not just an
   absence.
6. **Filter in the responder, not the pattern** — "why did nothing happen".

Keep dry-run for last, as the practical recommendation.

---

## DEMO 1 — the response working, correctly and fast (10 minutes)

**Goal:** establish that this genuinely works and is genuinely fast, so Demo 2
is a surprise. A room that already thinks auto-remediation is a bad idea learns
nothing from watching it fail.

**Setup (do this before the room):** you need one real EC2 instance, `t3.micro`
in the same VPC as the quarantine group, tagged so you recognise it. Nothing
needs to run on it.

**Show the responder in dry-run first:**

```bash
aws lambda invoke \
  --function-name cbc-day07-responder-XXXX \
  --cli-binary-format raw-in-base64-out \
  --payload '{"detail":{"id":"demo-1","type":"CryptoCurrencyMining:EC2/BitcoinTool.B!DNS","severity":8.0,"title":"Demo finding","resource":{"resourceType":"Instance","instanceDetails":{"instanceId":"i-0YOURINSTANCE"}}}}' \
  --profile bootcamp --region us-east-1 /tmp/dry.json

python3 -m json.tool < /tmp/dry.json
```

**Narrate, in this order:**

1. **`decision: DRY-RUN`** and `would_have`. Nothing changed.
2. **`previous_security_groups`** — it recorded them before deciding anything.
3. **`rollback_command`** — the exact command, in the output, already.
4. **`reason`** — "finding type is on the allow-list". Point out that it names
   the *type*, not the severity.

**Then switch to isolate and do it for real:**

```bash
# terraform.tfvars: containment_mode = "isolate"
terraform apply -auto-approve

aws lambda invoke --function-name cbc-day07-responder-XXXX \
  --cli-binary-format raw-in-base64-out --payload file:///tmp/finding.json \
  --profile bootcamp --region us-east-1 /tmp/live.json

aws ec2 describe-instances --instance-ids i-0YOURINSTANCE \
  --query 'Reservations[].Instances[].SecurityGroups' \
  --profile bootcamp --region us-east-1
```

The security groups have been replaced. Check the mail. Then show the tags:

```bash
aws ec2 describe-tags --filters "Name=resource-id,Values=i-0YOURINSTANCE" \
  --query 'Tags[?starts_with(Key, `Security`)]' \
  --profile bootcamp --region us-east-1
```

**Say the thing about the tags**, because nobody expects it:

> An isolated instance nobody can explain gets terminated by somebody tidying
> up three weeks later, along with the evidence. The tags are so that person
> stops.

**Then reverse it, using the command from the notification.** Do not type it
from memory — copy it out of the email. That is the point.

---

## DEMO 2 — the same automation containing something it should not (12 minutes)

**This is the most valuable five minutes of the day.**

**Goal:** the room watches the identical code, on an identical instance,
contain something on evidence that is wrong — and then sits with the fact that
nothing in the output would have told them.

**Frame it before you run it:**

> Same zip file. Same function code. Three environment variables different, and
> one IAM policy. Watch what it decides and, more importantly, watch what it
> tells you about that decision.

**Run the naive responder on a finding that is NOT on the good allow-list:**

```bash
aws lambda invoke \
  --function-name cbc-day07-naive-responder-XXXX \
  --cli-binary-format raw-in-base64-out \
  --payload '{"detail":{"id":"demo-2","type":"UnauthorizedAccess:EC2/SSHBruteForce","severity":8.0,"title":"[SAMPLE] SSH brute force","resource":{"resourceType":"Instance","instanceDetails":{"instanceId":"i-0YOURINSTANCE"}}}}' \
  --profile bootcamp --region us-east-1 /tmp/naive.json

python3 -m json.tool < /tmp/naive.json
```

**What you will see, and the three things to draw out:**

**One — it acted on a finding the good responder refuses.** SSH brute-force
against an internet-facing host is background noise; it is on nobody's
allow-list. The naive responder took it because severity 8.0 cleared its
threshold. Read the `reason` field out loud — the shared code is honest about
what it did and why, which the real-world version usually is not.

**Two — it acted on a `[SAMPLE]` finding.** `ACT_ON_SAMPLES=true`. Ask the
room what the opposite mistake would look like. Wait for it. The answer —
*a responder that only acts on samples works perfectly in the lab and does
nothing in production* — usually comes from somebody who has just realised it,
and it is worth the pause.

**Three — the refusal.** `CONTAINMENT_MODE=terminate`, and the shared code
refuses:

```
"action": "REFUSED",
"reason": "CONTAINMENT_MODE='terminate' is not a mode this responder implements..."
```

Then the important question:

> The code protected us. Does that make the configuration acceptable?

Let them argue. Land it:

> No. The next person to "fix" this responder will implement what the
> configuration asks for, because the configuration is what somebody wrote down
> as the intent. That is why SEC-012 fires on configured intent rather than on
> observed behaviour.

**Four — the IAM.** Show that the naive role holds `cloudtrail:*` and `iam:*`:

```bash
aws iam get-role-policy --role-name cbc-day07-naive-responder-XXXX \
  --policy-name cbc-day07-naive-responder-policy \
  --query 'PolicyDocument.Statement[0].Action' \
  --profile bootcamp --region us-east-1
```

> This function can delete the trail that recorded what it just did. It is the
> most valuable thing in this account to compromise, and it can erase its own
> footprints.

**Close by connecting the two demos:**

> Demo 1 was forty lines of Lambda and it worked beautifully. Demo 2 was the
> same forty lines. Everything that made the first one safe was configuration —
> which means none of it was in a code review, and all of it is still there a
> year later.

### The optional third run — the kill switch

If you have three minutes, this is the best possible closer:

```bash
aws ssm put-parameter --name /cbc-day07/kill-switch \
  --value DISARMED --type String --overwrite \
  --profile bootcamp --region us-east-1

# same invoke as Demo 1
```

`decision: NO ACTION`, `reason: kill switch is DISARMED`. One command, no
apply, no plan, no pipeline. Point out that the responder's own role is
explicitly denied `ssm:PutParameter`, so it cannot undo this.

Put it back to `ARMED` in front of them, so nobody leaves with a disarmed
demo account.

---

## 3:35 — Cost and the auditor (15m)

Run it live:

```bash
cd lab/python && python3 sec_audit.py --profile bootcamp --region us-east-1 --prefix cbc-day07
```

**11 findings, 137 points, 0/100, grade F.**

Then the two things worth saying:

**One — the same count is not the same account.** Force a rotation, leave the
findings untriaged, re-run. Still eleven. One check cleared, another appeared,
six points apart. Then:

> Never diff on the count. A dashboard that trends "11" reports a stability
> that is not there.

**Two — the calendar is an input.** SEC-013 finds nothing today because the
access key is hours old. Nobody has to change anything for that to stop being
true. Ask what that means for a merge-time-only audit. The answer — that a
point-in-time pass is not a property that persists — is the sentence people
quote back months later.

Finish on cost. The GuardDuty free trial ending on day 31 is the practical
warning most rooms need.

---

## Where this day goes wrong in a room

| Symptom | Cause | Fix |
|---|---|---|
| Security Hub console is empty | First control run takes hours | Apply the night before. Non-negotiable. |
| `validate-logs` says no digest | Under an hour of delivered objects | Apply the night before; have saved output as backup. |
| Demo 2 has no instance to isolate | You forgot the `t3.micro` | Check the night before; the demo does not work without it. |
| Responder does nothing in Demo 1 | Sample-finding refusal, or the type is not on the allow-list | Use a hand-crafted payload without `[SAMPLE]`, as above. |
| Room argues auto-remediation is always wrong | Reasonable position, held too hard | Agree with the constraints. Then run Demo 1. Speed is the argument. |
| Room wants `terminate` added | Very common, and worth taking seriously | The Step Functions answer: a wait-for-approval state, not an env var. |
| Nobody clicks the SNS links | It is the least interesting step | Do it together at 0:35. Demo 1's mail depends on it. |
| Everyone leaves GuardDuty on | It is not obvious it keeps billing | Teardown is a taught block, not homework. Say day 31 out loud. |

---

## The three sentences to leave them with

1. **Severity is impact, not confidence.** Automate on finding type, or you
   will contain your own penetration test.
2. **Reversible in principle is not reversible at 3am.** Record the previous
   state before you change it, and put the rollback command in the
   notification.
3. **Everything that made the good responder safe was configuration** — which
   means none of it went through a code review, and all of it is one deadline
   away from being changed.
