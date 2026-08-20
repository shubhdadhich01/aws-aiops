# Day 09 — Lab Walkthrough

**Cost Optimization & Cost Anomaly Detection**

If you have not read the top-level `README.md`, do that first. This file is
where the actual work is done, and it assumes you know what the four states
are and what the auditor is trying to prove.

---

## What you will do, in order

Eleven numbered steps. The numbers match the `next_steps` output that
`terraform output` will print at the end of `apply`, so if you get lost, the
console can tell you where you are. Each step names which check IDs it is
about to move, so you can verify by re-running `cost_audit.py`.

| Step | Action                                                    | Checks that move                   |
|------|-----------------------------------------------------------|------------------------------------|
| 1    | Confirm the SNS subscription for cost alerts              | — (setup only)                     |
| 2    | Run the auditor against the shipped defaults (STATE A)    | 12 findings, score 31              |
| 3    | Turn on Budget                                            | `COST-001`                         |
| 3b   | Turn on Cost Anomaly Detection monitor                    | `COST-003`                         |
| 3c   | Turn on S3 bucket lifecycle                               | `COST-014`                         |
| 4    | Remove `create_insecure_examples`                         | `COST-005`, `COST-006`, `COST-009`,|
|      |                                                           | `COST-010`, `COST-011`, `COST-013` |
| 5    | Add gateway endpoints for S3 and DynamoDB                 | `COST-012` (if you added NAT)      |
| 6    | Tag every resource (already done by default_tags)         | `COST-004` stays silent            |
| 7    | Explore Cost Explorer                                     | — (observation)                    |
| 8    | Explore Cost Anomaly Detection                            | — (observation)                    |
| 9    | Re-run the auditor. This is STATE B                       | 0 findings, score 100              |
| 10   | Wait (or fake wait) 30 days. This is STATE C              | `COST-007`, `COST-015`, `COST-016` |
| 11   | Tear down                                                 | — (destroy)                        |

Time budget: about 90 minutes for the first pass. Step 10 is the interesting
one — it involves not doing anything.

---

## Step 0 — prerequisites

Before you `terraform apply`, verify:

```bash
# You are in the right directory.
cd lab/terraform

# Your AWS CLI profile works.
aws sts get-caller-identity --profile bootcamp

# You have the right region for the resources.
aws configure get region --profile bootcamp   # expect us-east-1

# You have Billing read for the account.
aws budgets describe-budgets --account-id "$(aws sts get-caller-identity --profile bootcamp --query Account --output text)" --profile bootcamp --region us-east-1
```

The last one is the honest test. If you get an `AccessDeniedException`, your
role does not carry `AWSBillingReadOnlyAccess` and everything about Budgets
and Cost Anomaly Detection will silently return empty from the auditor —
which will look like `COST-001` and `COST-003` firing when they should not.

Copy the tfvars template and fill it in:

```bash
cp terraform.tfvars.example terraform.tfvars
```

Open `terraform.tfvars` and set at minimum:

```hcl
notification_email = "your-email@example.com"
owner              = "your-name"
```

Everything else has a defensible default. If you want the fastest possible
STATE A, do not change any of the `enable_*` toggles — they are all off by
default, deliberately, so that the audit fails hard against a fresh account.

---

## Step 1 — `terraform apply` and confirm the SNS subscription

```bash
tofu init
tofu apply
```

**Expected output tail:**

```
Apply complete! Resources: 39 added, 0 changed, 0 destroyed.

Outputs:
  vpc_id                       = "vpc-0xxxxxxxxxxxxxxxx"
  app_instance_id              = "i-0xxxxxxxxxxxxxxxx"
  app_instance_type            = "t3.micro"
  orphan_volume_ids            = [
    "vol-0xxxxxxxxxxxxxxxx",
    "vol-0yyyyyyyyyyyyyyyy",
  ]
  ...
```

Now check the inbox `notification_email` points to. There will be an email
from `no-reply@sns.amazonaws.com` with the subject **AWS Notification -
Subscription Confirmation**. **Click the confirmation link.**

Until you do:
- Budget notifications will fire but go nowhere.
- Cost Anomaly Detection subscriptions will be created in a `PendingConfirmation` state.
- Everything else about the lab still works.

The confirmation is a five-second step that is easy to skip. The failure mode
of skipping it is silent, which is the fault this whole day exists to teach.

---

## Step 2 — run the auditor. This is STATE A.

```bash
cd ../python
pip install -r requirements.txt
python cost_audit.py --profile bootcamp --region us-east-1 --prefix cbc-day09
```

Reading the output. Expect:

- 12 findings.
- Score in the low 30s.
- Grade F.

The exact score is 31/100 if the fixtures matched CI exactly. In your
account it may be off by a point or two — a Config recorder that is running,
an S3 bucket somebody left over from another lab, a Lambda whose log group
was already un-bounded. That is fine. `--prefix cbc-day09` limits the check
scope to resources tagged with a matching Name; drop the prefix to audit
everything you have, and the score will move.

**If you get zero findings**, something is wrong:
- Are you in the right region?
- Are your credentials for the right account?
- Did the terraform apply actually complete?

**If you get more than 12 with the prefix on**, something else is wrong:
- Are there resources from a previous lab tagged `cbc-day09-...`?
- Try `--prefix cbc-day09-app` or similar to narrow further.

Save this output. You will diff it against subsequent runs to prove each
step made the finding it was supposed to.

```bash
python cost_audit.py --profile bootcamp --region us-east-1 --prefix cbc-day09 \
  --format json --quiet > state-a-findings.json
```

---

## Step 3 — turn on the free guardrails, one at a time

The point of turning them on ONE AT A TIME is to see what each does.

### Step 3a — Budget

Edit `terraform.tfvars`:

```hcl
enable_budget = true
```

Then:

```bash
cd ../terraform
tofu apply
cd ../python
python cost_audit.py --profile bootcamp --region us-east-1 --prefix cbc-day09
```

**Expected:** 11 findings. `COST-001` gone. Score up by 10.

Note that `COST-002` stayed silent even though you now have a budget. That
is silent-by-design: the `budget_notifications` variable's validation
refused to let the budget be created without at least one notification, so
the fault `COST-002` catches structurally cannot exist in this stack.

### Step 3b — Cost Anomaly Detection monitor

```hcl
enable_cost_anomaly_monitor = true
```

```bash
cd ../terraform
tofu apply
cd ../python
python cost_audit.py --profile bootcamp --region us-east-1 --prefix cbc-day09
```

**Expected:** 10 findings. `COST-003` gone. Score up by another 10.

The monitor is now created and subscribed to your SNS topic. It has no
history yet — Cost Anomaly Detection needs about 10 days of baseline before
it produces its first anomaly, and it will not fire in the meantime. Come
back to step 10 for the actual test.

### Step 3c — S3 bucket lifecycle

```hcl
enable_bucket_lifecycle = true
```

```bash
cd ../terraform
tofu apply
cd ../python
python cost_audit.py --profile bootcamp --region us-east-1 --prefix cbc-day09
```

**Expected:** 9 findings. `COST-014` gone. Score up by 4.

The lifecycle rule transitions objects to STANDARD-IA at 30 days, Glacier
Instant Retrieval at 90 days, and expires at 365. For a bucket that is
empty (like this one right now), the rule is invisible; it becomes the
difference between $23/month and $4/month once anything writes non-trivial
data.

---

## Step 4 — remove `create_insecure_examples`

This is the big one. It removes:
- The two orphan EBS volumes.
- The two unassociated Elastic IPs.
- The previous-generation `t2.micro` instance and its `gp2` root.
- The Classic Load Balancer.
- The two un-bounded CloudWatch log groups.

```hcl
create_insecure_examples = false
```

```bash
cd ../terraform
tofu apply
cd ../python
python cost_audit.py --profile bootcamp --region us-east-1 --prefix cbc-day09
```

**Expected:** 0 findings. Score 100/100. Grade A.

Wait — the plan is going to destroy things you might want to keep looking
at. That is fine. This lab is not production and the resources you are
destroying were only ever demonstrations of the checks. If you want to
inspect them longer, run the auditor with `--format json > state-a.json`
BEFORE this step, and the finding evidence will keep the resource IDs and
sizes for later reading.

---

## Step 5 — try NAT-without-endpoints (optional)

`COST-012` has stayed silent through everything so far. That is because
`enable_nat_gateway = false` by default. If you want to see it fire once:

```hcl
enable_nat_gateway = true
enable_vpc_endpoints = false
```

```bash
cd ../terraform
tofu apply
cd ../python
python cost_audit.py --profile bootcamp --region us-east-1 --prefix cbc-day09
```

**Expected:** 1 finding. `COST-012` fires with 4 points. Score 96.

Then attach the endpoints:

```hcl
enable_vpc_endpoints = true
```

```bash
cd ../terraform
tofu apply
cd ../python
python cost_audit.py --profile bootcamp --region us-east-1 --prefix cbc-day09
```

**Expected:** 0 findings. `COST-012` silent again.

The NAT gateway costs about $32/month plus $0.045/GB processed. The two
gateway endpoints (S3 and DynamoDB) are free. On a real workload that does
any of its outbound traffic through S3 or DynamoDB — backup restores, log
shipping, artifact downloads, the AL2023 dnf mirror — this change is one
of the largest cost lines a five-minute config edit removes.

You can safely turn `enable_nat_gateway` back off before continuing, so
that the shutdown cost of the day is $0/hour rather than $0.045/hour.

---

## Step 6 — tag coverage

`COST-004` has been silent throughout because the `default_tags` block on
the AWS provider (see `providers.tf`) carries `Project`, `Day`,
`ManagedBy` and `Owner` on every resource this stack creates. That is
silent-by-design.

To confirm, look at a resource you did not tag directly:

```bash
aws ec2 describe-volumes --volume-ids "$(tofu output -raw orphan_volume_ids | jq -r '.[0]')" --profile bootcamp | jq '.Volumes[0].Tags'
```

Every resource carries the tags. The stack cannot produce `COST-004`.

For your OWN resources — anything you created outside this Terraform, from
a shell script or an old lab — the picture is different. Drop the
`--prefix cbc-day09` filter and re-run:

```bash
python cost_audit.py --profile bootcamp --region us-east-1
```

If `COST-004` fires here, that is what a real audit looks like.

---

## Step 7 — explore Cost Explorer

Open the console:

```
https://console.aws.amazon.com/cost-management/home
```

Three things to look at:

**Group by Service.** The default view. This is where you find the answer
to "what is our biggest cost line". Almost always EC2 (compute + attached
EBS), sometimes S3, sometimes NAT gateway (Data Transfer).

**Group by Owner.** Change the group dimension to Tag → Owner. If nothing
shows up, the tag has not been ACTIVATED as a cost allocation tag. That is
a separate step in the Billing console:

```
Billing → Cost allocation tags → User-defined cost allocation tags → Owner → Activate
```

This is account-wide, one-way, and manual. It is the reason many tags that
"should work" don't group in Cost Explorer.

**Group by Project.** Same story. Once these two views become useful, you
have a governance conversation with a metric attached instead of a bill
that lands on a desk once a month.

---

## Step 8 — explore Cost Anomaly Detection

Open the console:

```
https://console.aws.amazon.com/cost-management/home#/anomaly-detection
```

Three tabs:

**Cost monitors.** Your monitor should be here, named `cbc-day09-monitor`.
Its status will be `Active` and its "Anomalies detected" will be 0. That is
normal — it needs about 10 days of baseline.

**Alert subscriptions.** Your subscription should be here, listed with the
SNS topic ARN. If the status column says "Confirmed", the SNS topic has a
subscriber you confirmed. If it says something else, retrace step 1.

**Anomalies.** Empty. Come back in about ten days.

---

## Step 9 — re-run the auditor. This is STATE B.

You already saw this in step 4 — 0 findings, 100/100, grade A. This is the
end of the "immediate" work.

Save it for later reference:

```bash
python cost_audit.py --profile bootcamp --region us-east-1 --prefix cbc-day09 \
  --format json --quiet > state-b-findings.json
```

**Do not tear down yet.** Step 10 is the whole point of the day.

---

## Step 10 — STATE C: unchanged account, 30 days later

The account is in STATE B. It will stay in STATE B until:

- **COST-007** fires: any EBS snapshot on the account ages past
  `snapshot_retention_days` (default 90). In a lab account that is not
  producing snapshots, this may take a while. If you want to see it faster,
  create a snapshot manually and reduce the threshold in the auditor:
  ```bash
  aws ec2 create-snapshot --volume-id "$(tofu output -raw app_instance_id | xargs aws ec2 describe-instances --instance-ids --query 'Reservations[].Instances[].BlockDeviceMappings[].Ebs.VolumeId' --output text --profile bootcamp)" --description "manual snapshot for testing COST-007" --profile bootcamp
  ```
  Then re-run with `--snapshot-retention-days 0`.

- **COST-015** fires: the app instance's uptime crosses
  `long_running_instance_days` (default 30) AND the account has zero
  active Savings Plans or Reserved Instances. In this lab, both those
  conditions are permanent. Wait 30 days, or re-run with
  `--long-running-instance-days 0`.

- **COST-016** fires: Cost Anomaly Detection has produced at least one
  anomaly, and the anomaly has been open without Feedback for longer than
  `anomaly_triage_days` (default 7). This one requires actual time and
  actual account activity — Cost Anomaly Detection cannot be forced to
  produce an anomaly on demand.

**In the lab, the fastest way to see STATE C** is to run the tests:

```bash
cd lab/python
python3 -m unittest tests.test_checks.TestContractTotals.test_state_c_the_clock_alone_changes_the_answer -v
```

This test reproduces STATE C by moving the injected clock forward 30 days
on a clean fixture and running every check. The result: exactly 3 findings,
33 points, 67/100, grade C. Nothing about the configuration changed.

That is the day's thesis in one test.

---

## Step 11 — tear down

See `teardown-checklist.md` for the ordered list. Two things worth
emphasising here:

**Delete the Classic ELB FIRST.** It bills at ~$16.20/month whether or not
anything talks to it. If your teardown fails partway, this is the loudest
recurring charge in the residue.

**Release the Elastic IPs SECOND.** They bill at $3.60/month EACH,
regardless of use, since Feb 2024. If you don't have Terraform destroy
them successfully, `aws ec2 release-address` them manually.

```bash
tofu destroy
```

Then verify:

```bash
# No cbc-day09-* resources should remain in any of these lists:
aws ec2 describe-instances --filters 'Name=tag:Project,Values=cbc-day09' --profile bootcamp --region us-east-1 --query 'Reservations[].Instances[].[InstanceId,State.Name]'
aws ec2 describe-volumes   --filters 'Name=tag:Project,Values=cbc-day09' --profile bootcamp --region us-east-1 --query 'Volumes[].[VolumeId,State]'
aws ec2 describe-addresses --profile bootcamp --region us-east-1 --query 'Addresses[].[AllocationId,AssociationId]'
aws elb describe-load-balancers --profile bootcamp --region us-east-1 --query 'LoadBalancerDescriptions[?starts_with(LoadBalancerName, `cbc-day09`)][LoadBalancerName]'
```

The full audited teardown is in `teardown-checklist.md`. Do that before you
close the tab.

---

## Common failures

**"AccessDeniedException" from the Budgets or Cost Explorer APIs.**

Your role or user does not carry `AWSBillingReadOnlyAccess`. Attach it and
retry. This is the #1 reason Day 09 does not work — the IAM policies from
Day 03 are correct for compute and storage, they are not correct for
billing APIs, and billing APIs are an entirely separate permission surface
that many organisations gate on the CFO.

**"The audit shows COST-002 firing."**

Something in your account has a budget without a notification. The
Terraform-created budget cannot produce this, so the finding is against
something else — a budget somebody created in the console for a report, or
a budget from a previous lab that was destroyed but whose IAM policy
prevented the notification block from being cleaned up.

Look at the finding's `evidence.BudgetName` field to see which one.

**"COST-004 fires against my own resources."**

That is the point of the check — it fires against resources that don't
carry `Owner` and `Project` tags. If you added a resource outside the
Terraform, tag it, or add a `Owner=you Project=cbc-day09` pair with:

```bash
aws ec2 create-tags --resources i-xxxxxx --tags Key=Owner,Value=you Key=Project,Value=cbc-day09
```

**"terraform destroy leaves an EIP behind."**

Terraform sometimes fails to release an EIP if the AWS API returns
transient errors. `aws ec2 release-address --allocation-id eipalloc-xxx`
resolves it manually.

**"The auditor took 15 seconds to run."**

Normal, on a busy account. Cost Explorer, Cost Anomaly Detection, and
Savings Plans APIs are slower than EC2/EBS. Add `--quiet` if the
progress lines are getting in your way.

**"I turned on enable_cost_anomaly_monitor but no anomalies ever appear."**

Cost Anomaly Detection needs about 10 days of baseline before it can
produce its first anomaly, and it will only produce anomalies for spend
patterns that materially depart from the learned baseline. In a lab
account with $10/month of spend, it may take a lot longer than 10 days for
the first anomaly to appear. That is the correct behaviour of the service,
and it is why the recommendation is to enable it now rather than on the
day of the incident.

---

## Where to go next

- If you finished the walkthrough and everything worked: `interview-qa.md`
  is the "prove you understood it" file. Ten questions with worked answers.
- If the auditor is behaving unexpectedly, `trainer-notes.md` has the
  common learner failures I have seen while delivering this day and how
  they present.
- If you want to record what your actual account looked like — the actual
  monthly cost, the actual anomaly count, the actual tag coverage percent
  — `cost-observations.md` is a template. It is worth doing in a real
  account; it is what makes the material sticky.

---

## The LOCKED contract

Below this line is the contract, synchronised across five files by
`sync_contract.py`. Do not edit it here — edit `cost_audit.py`'s docstring
and re-run `sync_contract.py --write`.

<!-- CONTRACT-BEGIN -->
DAY 09 FINDING CONTRACT — LOCKED AT CP2
=============================================================================
This block is reproduced identically in five places. Change one, change all
five: README.md, lab/README.md, lab/terraform/outputs.tf (finding_contract),
lab/python/cost_audit.py (module docstring), lab/python/tests/test_checks.py.

Weights are the repo-wide ones, identical to Days 03 through 08:
CRITICAL 25, HIGH 10, MEDIUM 4, LOW 1, INFO 0. Score is 100 minus the sum,
floored at 0. Grades: 90+ A, 75+ B, 60+ C, 40+ D, below that F.

Day 09 uses CRITICAL, HIGH, MEDIUM and LOW, but not INFO. There is one LOW
(COST-010, gp2 vs gp3), because the choice is real, cheap and non-urgent —
unlike anything on Day 08. There is one CRITICAL (COST-016), because it is
the day's thesis: a cost anomaly nobody triaged is a bill nobody stopped,
and that is the failure mode the whole day exists to make concrete.

STATIC STATE — after terraform apply with the shipped defaults
(create_insecure_examples = true, enable_budget = false,
enable_cost_anomaly_monitor = false, enable_bucket_lifecycle = false,
enable_vpc_endpoints = false, enable_nat_gateway = false), before any
anomaly has been raised, before any triage, before any Savings Plan.

  ID        SEVERITY   W   N  PTS  SOURCE RESOURCE
  --------  --------  --  --  ---  ------------------------------------------
  COST-001  HIGH      10   1   10  account - no budget exists
  COST-002  HIGH      10   0    0  none - SILENT BY DESIGN, see below
  COST-003  HIGH      10   1   10  account - no anomaly monitor
  COST-004  MEDIUM     4   0    0  none - SILENT BY DESIGN, see below
  COST-005  HIGH      10   2   20  aws_ebs_volume.orphan_a, aws_ebs_volume.orphan_b
  COST-006  MEDIUM     4   2    8  aws_eip.orphan_a, aws_eip.orphan_b
  COST-007  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
  COST-008  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
  COST-009  MEDIUM     4   1    4  aws_instance.previous_gen
  COST-010  LOW        1   1    1  aws_instance.previous_gen root volume (gp2)
  COST-011  MEDIUM     4   1    4  aws_elb.classic
  COST-012  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
  COST-013  MEDIUM     4   2    8  aws_cloudwatch_log_group.unbounded_a and _b
  COST-014  MEDIUM     4   1    4  aws_s3_bucket.artifacts - no lifecycle
  COST-015  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
  COST-016  CRITICAL  25   0    0  none - SILENT BY SITUATION, see below
  --------  --------  --  --  ---  ------------------------------------------
  TOTALS                    12   69

  TWELVE findings from SIXTEEN checks. Seven checks are silent here and
  they are silent for two different reasons, which is the most useful thing
  in this table: five because this particular stack cannot currently
  produce the fault (COST-007, COST-008, COST-012, COST-015, COST-016),
  and two because NO configuration of this stack can ever produce them
  (COST-002 and COST-004).

  Score: 100 - 69 = 31/100. Grade F.

  SEVERITY HISTOGRAM of the 16 checks: 1 CRITICAL, 4 HIGH, 10 MEDIUM,
  1 LOW, 0 INFO.

THE FOUR STATES

  STATE                                        FINDINGS  POINTS    SCORE  GRADE
  -------------------------------------------  --------  ------  -------  -----
  A  Static: after apply, nothing configured        12      69   31/100      F
  B  Live: guardrails on, insecure examples
     off, endpoints on, tags at 100%                  0       0  100/100      A
  C  Thirty days after B, WITH NOTHING
     CHANGED - anomalies raised, none
     triaged, snapshots aged past retention           3      33   67/100      C
  -------------------------------------------  --------  ------  -------  -----
  D  Reference build: everything in B, plus a
     Savings Plan covering baseline usage
     AND each anomaly triaged within its
     SLA AND snapshots pruned by an aging
     rule                                             0       0  100/100      A

  STATE C IS THE POINT OF THIS TABLE AND IT IS THE THESIS OF THE DAY.

  Between B and C, nobody deploys anything. No console click, no apply, no
  merge. Three findings appear because time passed: COST-007 fires as
  snapshots the account has been quietly accumulating age past
  snapshot_retention_days; COST-015 fires as the app instance's uptime
  crosses long_running_instance_days without a Savings Plan being
  purchased; COST-016 fires as Cost Anomaly Detection has produced at least
  one anomaly which nobody has provided Feedback on within
  anomaly_triage_days.

  An audit that passes on the 1st fails on the 31st on an unchanged account.

  That is not a defect in the auditor. It is the correct behaviour, and it
  is the difference between a configuration audit and a cost audit. Cost is
  not a property of a configuration. It is a lagging measure of a decision
  nobody re-examined, and a claim about "we watch our spend" decays
  continuously from the last time somebody looked at Cost Explorer.

  Day 08's contract had a state that decayed WITHIN AN HOUR - DR-008
  fired the minute the newest recovery point aged past a 60-minute RPO,
  and the point was that a merge-time audit is blind to that. Day 09
  makes the same argument on a monthly timescale, with three separate
  decay paths so the pattern is undeniable rather than a single quirky
  check.

SILENT BY DESIGN - COST-002 (a budget with no notification threshold) and
COST-004 (cost allocation tag coverage below threshold).

  COST-002: No shipped default and no typo can produce this fault. The
  budget_notifications variable carries a validation refusing an empty
  list, and the aws_budgets_budget resource uses `dynamic "notification"`
  over that list. There is no path through this Terraform that produces a
  budget with zero notifications, so the plan refuses to.

  It is not a hypothetical fault. Every Billing console has a "create
  budget" wizard that will let you click through to a budget with no
  notifications attached, and every account with more than about ten
  budgets has one - usually created for a specific report that generated
  the CSV, and never revisited. A budget without a notification is a
  decorative object.

  COST-004: The AWS provider carries default_tags with Project and Owner,
  which are exactly the tags this check looks for. Every resource that
  goes through this Terraform plan inherits them automatically at create
  time - a resource without them is a resource that was NOT created by
  this plan. So the check stays silent against this stack even at 100%
  target coverage, and the same check fires on the account next door
  where somebody was creating buckets from a shell script.

  A check that stays silent because the stack cannot produce the fault is
  evidence that the auditor does not cry wolf.

SILENT BY SITUATION - COST-007, COST-008, COST-012, COST-015 and COST-016.

  COST-007 is the aged-snapshot check. A fresh terraform apply produces
  no snapshots at all, and even after the lab creates one for backup
  testing, snapshot_retention_days (default 90) is a long time. In a real
  account this fires readily - every automated backup rule accumulates
  copies unless a companion rule ages them out.

  COST-008 is the stopped-instance check. The app instance defaults to
  running; nothing in the lab stops it and leaves it for 30 days. In a
  real account it fires against forgotten test boxes.

  COST-012 is the NAT-without-endpoints check. enable_nat_gateway defaults
  to false, so there is no NAT gateway for the check to fire against. The
  moment somebody sets enable_nat_gateway = true WITHOUT setting
  enable_vpc_endpoints = true, it fires immediately with 4 points.

  COST-015 is the long-running-without-Savings-Plan check. The app
  instance was created seconds ago at apply time, so uptime is not yet
  above long_running_instance_days (default 30). This one fires with the
  clock alone, without anybody changing anything, and that is exactly the
  lesson of STATE C.

  COST-016 is the untriaged-anomaly check. With enable_cost_anomaly_monitor
  = false there is no monitor and no anomalies to triage. Once the
  monitor is enabled it needs roughly 10 days of baseline before producing
  its first anomaly. Once anomalies exist, this check fires with the
  CLOCK ALONE - no configuration change required - until somebody opens
  the console and marks the anomaly with Feedback.

  NOTHING HAS TO CHANGE FOR ANY OF THESE TO STOP BEING SILENT except the
  passage of time.

THE DIFFERENCE MATTERS. Silent by design tells you something about the
auditor: it cannot fire, so its silence is a property of the tool. Silent
by situation tells you nothing about the auditor and everything about
today's account - and "we have no findings" and "we have nothing to find"
are different states that render identically in every report. Never read
the second as the first.

CHECK INTERACTIONS THAT ARE DELIBERATE, NOT BUGS

  COST-001 AND COST-002 LOOK LIKE THE SAME CHECK AND ARE NOT. COST-001
  fires when NO budget exists. COST-002 fires when a budget EXISTS but has
  no notification threshold. The first is a missing guardrail; the second
  is a guardrail that speaks into the void. Fixing COST-001 by creating a
  budget with zero notifications is exactly what people do, and it is the
  transition COST-002 is there to catch. On this stack COST-002 is silent
  by design; on somebody else's stack it is the second-most common cost
  governance finding, after "no budget at all".

  COST-003 AND COST-016 ARE THE SAME PATTERN AT TWO LAYERS. COST-003 asks
  "does the anomaly detector exist"; COST-016 asks "does anybody read
  what it says". A monitor without a subscription is halfway there. A
  monitor with a subscription pointed at an unconfirmed SNS topic is
  three-quarters of the way there. A monitor with a subscription pointed
  at a confirmed SNS topic whose emails nobody reads is COST-016, and that
  is the shape of most real accounts that have "cost monitoring".

  COST-005 AND COST-006 ARE THE SAME IDEA AT DIFFERENT PRICE POINTS. An
  unattached EBS volume is $0.08/GB/month. An unassociated EIP is
  $3.60/month flat, since February 2024. Both are "resources billing for
  nothing", both accumulate in the same way (a stack that half-destroyed,
  a manual test that "we'll clean up later"), and both are worth
  surfacing separately so remediation is not one giant list.

  COST-009 AND COST-010 FIRE ON THE SAME INSTANCE and are not duplicates.
  COST-009 says "the instance family is previous-generation". COST-010
  says "the root volume type is superseded". Same resource, unrelated
  remediations, potentially different owners: the platform team owns the
  instance type, and the storage or database team may own the volume type.
  Fixing one leaves the other.

  COST-013 FIRES ONCE PER LOG GROUP, DELIBERATELY NOT DEDUPLICATED. Each
  log group is billed independently and each one has a separate person or
  pipeline whose logs land there. A single finding at "account has 40
  unbounded log groups" is a finding nobody knows how to remediate,
  because there is no single owner. Per-log-group findings can be routed
  to per-log-group owners.

  COST-015 IS THE ONLY CHECK THAT DEPENDS ON A SUBJECTIVE JUDGEMENT, and
  it is deliberately narrow to compensate. "Should we buy a Savings Plan"
  is a real, difficult decision that depends on how confident the team is
  that the workload will still exist in a year. The check does not answer
  it. It only asks "has anyone LOOKED at this question for a workload
  that has been running longer than a month". A "yes we looked, decided
  not to" answer is a suppression comment, not a finding to leave open -
  and the check's remediation language reflects that.

  COST-016 AND EVERY OTHER CHECK: it is the only CRITICAL because it is
  the only one where the failure mode is "the whole cost governance
  program does not work". Every other finding is a specific missing or
  wasteful resource. COST-016 is the meta-check: the machine is running,
  the alerts are firing, nobody is reading them. A stack where every
  other check is green and COST-016 is red is an account that has bought
  cost tooling and not yet started using it, which is the modal state of
  cost tooling.
<!-- CONTRACT-END -->
