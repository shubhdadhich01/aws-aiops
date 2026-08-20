# Day 09 — Trainer Notes

Notes for the person delivering this day live. Reading these gives you the
things a first-time learner does not yet know to look for, in the order
they come up during a real delivery.

---

## Time budget for a live class

- **90 minutes** if you race through the apply-and-audit sequence and
  spend the remaining time on Cost Explorer / CAD walk-throughs.
- **3 hours** if you want the class to actually finish the challenge
  scaffold and see all 47 tests green on their own machines.
- **1 day** if you want to include the honest "wait for a real anomaly"
  wait, which most classes will not do — see below.

I recommend the 3-hour version. The 90-minute version is possible but
skips the "produce STATE C by running the tests" moment, which is the
most memorable moment of the day.

---

## The one moment that matters

**The demonstration that STATE C exists** is the thing everybody
remembers a month later. Everything else can be re-derived from
docstrings and the auditor's help output; the thesis of the day cannot.

The moment is at Step 10 of the lab walkthrough. It is:

1. The class has reached STATE B — 0 findings, 100/100, grade A.
2. You tell them: "the account will not stay this way."
3. You run:
   ```bash
   python3 -m unittest tests.test_checks.TestContractTotals.test_state_c_the_clock_alone_changes_the_answer -v
   ```
4. The test names the three findings that appear: `COST-007`,
   `COST-015`, `COST-016`. The class sees the delta.
5. You ask: "which of these three needed anyone to change anything?"
6. The answer is: none of them. The clock alone changed the answer.

The follow-up is: "so when should this audit be running?" The answer is:
"on a schedule, not at merge time. Preferably weekly." That is the
governance lesson.

If time is short, skip anything else. Do not skip this.

---

## Common learner failures

### "AccessDeniedException" from the Budgets or Cost Explorer APIs

The most common. It looks like the check fires when it should not:
`COST-001` reports "no budget exists" against an account that has three
budgets, because the auditor could not list them.

**Fix**: Attach `AWSBillingReadOnlyAccess` to the role. Verify with:

```bash
aws budgets describe-budgets --account-id "$(aws sts get-caller-identity --profile bootcamp --query Account --output text)" --profile bootcamp --region us-east-1
```

If that returns `AccessDeniedException`, no attempt to fix the auditor
will work.

### "The auditor takes 20 seconds to run"

Normal on any real account. The billing APIs are slow. The auditor
prints progress to stderr; add `--quiet` if that is distracting.

### "COST-004 fires against the resources I created outside Terraform"

That is the check working. The `default_tags` on this Terraform stack
guarantees coverage of anything IT creates. Resources from previous
labs or shell scripts do not inherit those tags.

The learner's instinct is to disable the check. Push back — this is
one of the two most useful checks in the day for a real account, and
finding uncovered resources IS the deliverable. Tag them:

```bash
aws ec2 create-tags --resources i-xxxxx --tags Key=Owner,Value=<name> Key=Project,Value=<name>
```

### "COST-016 never fires even after I turn on Cost Anomaly Detection"

Correct. It cannot fire until AWS has produced an anomaly, which
requires ~10 days of baseline PLUS a spend pattern that materially
departs from the baseline. In a lab account with $10/month of steady
spend, it may never fire.

The **class demonstration** of `COST-016` is the test. The **real-world**
demonstration is running the auditor on a real account that has cost
tooling and seeing whether feedback has been provided.

### "I ran terraform destroy and it hung on the VPC"

Almost always an ENI still attached to something Terraform did not
manage. Look in EC2 console → Network Interfaces filtered by the
VPC. Detach and delete manually.

Second most common cause: a security group with a rule referencing
another SG in the VPC. Terraform destroys the custom SGs but leaves the
default one, and the default cannot be deleted until the VPC is empty
of everything else. This is not a bug; it is fine to leave the default
SG in place.

### "My score is 33 instead of 31"

Almost certainly an extra resource on the account that the auditor is
finding. Add `--prefix cbc-day09` to filter to this lab's resources.
If the score is still off, the extra resource is one this lab produced
that has picked up an extra evidence line — usually a log group from
Lambda or a CloudTrail trail. Investigate; the auditor's evidence
dict names the resource IDs.

### "I turned everything on and the score is still not 100"

Look at the JSON output: `python cost_audit.py ... --format json`. The
`findings` array names each finding. Most common cause: `COST-004`
firing because a resource somewhere in the account is missing tags.
Second most common: `COST-013` firing on a log group that Lambda created
automatically (Lambda functions default to unbounded retention).

---

## What to say vs what to type

Some things read better than they demonstrate live.

**Read aloud, don't demonstrate**:
- The finding contract table (the class can see it in the auditor output).
- The full list of check IDs (too long for a slide, too much for a talk).
- Every field of every Finding (the fixture code shows this).

**Demonstrate, don't read aloud**:
- STATE A → STATE B transition (~2 minutes with 3 tf applies).
- The STATE C test (5 seconds; the impact is in what the class realises).
- The Cost Explorer group-by-Owner view (Cost Explorer is slow enough
  that the pause is dramatic).
- The Cost Anomaly Detection empty tab ("this is what a working
  monitor looks like on day one").

**Skip if time is short**:
- Step 5 (NAT + endpoints). The mechanism is easily read from the
  variables.tf comments; running it live adds nothing.
- The full teardown. Point at the checklist and let them do it after.

---

## Slide-independent narrative structure

If your platform does not do slides well, this delivery works entirely
from the terminal:

1. Open the top-level README. Read the thesis line. Read the four-states
   table. This takes 5 minutes.
2. `cd lab/terraform && cat terraform.tfvars.example`. Point out
   `notification_email`, `create_insecure_examples`, and the four
   `enable_*` toggles. 3 minutes.
3. `tofu apply` (5 minutes, run in background or pre-applied).
4. `cd ../python && cat cost_audit.py | grep '^def check_'`. Point out
   16 checks. Read the docstring of one — I recommend `check_untriaged_anomalies`
   because it is the day's thesis in a docstring. 5 minutes.
5. `python cost_audit.py --profile bootcamp --region us-east-1 --prefix
   cbc-day09`. This is STATE A. Read the table together. 10 minutes.
6. Turn on the three guardrails, one at a time. 15 minutes.
7. Set `create_insecure_examples = false`. Re-run. STATE B. 10 minutes.
8. The STATE C moment (see above). 10 minutes.
9. Cost Explorer / CAD walk-through. 15 minutes.
10. Q&A on the interview questions. 15 minutes.

Total: 90-100 minutes.

For the 3-hour version, add:

11. Point at the challenge scaffold. Have the class open it and read
    one docstring. Give them 60 minutes to fix one check of their
    choice. 60 minutes.
12. Collective walk-through of `check_no_budget` implementation. Read
    the reference version together. 30 minutes.

---

## The check-relationship map to draw on the whiteboard

There are five interactions the tests explicitly cover. Draw them:

```
   COST-001 ──── COST-002       (existence vs decorative)
      │            │
      └── HIGH ────┘

   COST-003 ──── COST-016       (existence vs unread)
      │            │
      HIGH        CRITICAL

   COST-005 ──── COST-006       (same idea, different price)
      │            │
      HIGH        MEDIUM

   COST-009 ──── COST-010       (same instance, different remediation)
      │            │
      MEDIUM      LOW

   COST-013 (per log group, never deduplicated)
```

The class needs to see this once. After that, the interactions read as
sensible.

---

## When learners ask about AI

The day's title includes "Cost Anomaly Detection" which uses ML on the
AWS side. Learners occasionally ask whether they should be running their
own ML on cost data.

The honest answer: probably not, unless you have a specific reason.
Amazon has more cost data than you do and their model is free. What is
worth building yourself is:

- **Attribution**: which team, which product, which environment produced
  the anomaly. Cost Anomaly Detection's root-cause section is generic;
  your model of your account structure is specific.
- **Predictive budgets**: "at current trajectory we will exceed budget
  in 6 days". AWS Budgets does linear-forecast; anything more useful
  needs your own signal.
- **Cross-account rollup**: for consolidated organisations. This is
  operational engineering, not ML.

If a learner is interested in the AI aspect specifically, point at the
Day 10 capstone. Day 09 uses the ML that AWS provides; Day 10 wires
several data sources together.

---

## When learners ask about GCP or Azure

The equivalents are: Azure Cost Management (Anomalies), GCP Billing
alerts and BigQuery on billing exports. The check patterns port almost
directly — the fault surface is different, but "unread alert" is
universal.

The parallel course for Azure is in `Azure-Cloud-AIOPS-BootCamp` if you
teach the whole track. GCP is not currently in the course.

---

## Things that go wrong live but shouldn't

**Terraform provider version drift.** If a learner has a different
`aws` provider version, sometimes the `default_tags` interaction with
S3 or CloudWatch behaves differently. Pin the provider version to
`~> 5.40` in every fixture terminal you demonstrate from.

**Region drift.** A learner types `--region eu-west-1` and the auditor
runs, but Budgets and Cost Explorer are pinned to us-east-1 and return
empty. This produces the right result (nothing is in eu-west-1 for
cost APIs), but the learner may not know that. The auditor's collect
progress lines say "AWS Budgets (us-east-1)" — point at this if it
comes up.

**Anomaly Detection Confirmation.** If a learner is on a corporate email
that quarantines AWS notifications, the SNS confirmation link never
arrives. Have them run:

```bash
aws sns list-subscriptions --profile bootcamp --region us-east-1 --query 'Subscriptions[?TopicArn && contains(TopicArn, `cbc-day09`)].[SubscriptionArn,Protocol,Endpoint]' --output table
```

If the `SubscriptionArn` field says `PendingConfirmation`, the email did
not get through. Either:
- Ask them to use a personal email temporarily.
- Delete and recreate with a different email.

---

## What to note down after each delivery

For your own iteration on this day:

- Which check was the class most confused by. My money is on `COST-015`
  (long-running without SP), because it depends on subjective judgement
  and doesn't have a clean "fires when X, silent when Y" story.
- Which interview question triggered the best discussion. Question 9
  (what STATE C feels like) is usually the winner.
- Any AWS API behaviour that changed since the last delivery (Cost
  Explorer's API surface moves faster than the rest of AWS).

Update the docstrings and re-run `sync_contract.py --write`. That is
the mechanism.
