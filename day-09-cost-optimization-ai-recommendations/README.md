# Day 09 — Cost Optimization & Cost Anomaly Detection

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

---

## The thesis, in one line

**Cost is a lagging measure of a decision nobody re-examined.**

A cost audit that passes on the 1st of the month fails on the 31st, on an
unchanged account, because the account did not change but the world it exists
in did. That is not a defect of the audit. It is the difference between a
configuration audit and a cost audit, and it is the point of this day.

## What today is about

Day 09 is different from Days 01–08 in one respect: nothing that fails today
takes anyone down.

The Auto Scaling group on Day 08 that lived in one AZ is a production incident
waiting for AZ maintenance. The IAM policy on Day 03 that granted `*:*` was one
compromise away from account takeover. Both would fail an audit AND fail a
customer. The failure modes were graphical.

The failure modes on Day 09 are ledger entries. The un-attached EBS volume
does not degrade anything except the bill. The unread cost anomaly does not
degrade anything except the bill. Nobody pages you at 3am because a NAT
gateway is expensive; they page you three months later, in a meeting about
"why is AWS costing so much", and by then the finding is old enough to have
compounded.

That is why the day has a `CRITICAL` check (`COST-016`) whose whole subject is
"nobody read the alert". A stack that has a Cost Anomaly Detection monitor, a
confirmed SNS subscription, working emails and forty-seven open anomalies that
have been sitting for months believes it has cost monitoring, and what it
actually has is a machine talking to itself.

## The four states you will produce

By the end of this lab, the auditor will have shown you four scores against
what is largely the same account:

| State | What it is                                                            | Findings | Score | Grade |
|-------|-----------------------------------------------------------------------|----------|-------|-------|
| A     | Static, right after `terraform apply` with the shipped defaults      | 12       | 31    | F     |
| B     | Guardrails on, insecure examples off, endpoints attached, tags at 100% | 0        | 100   | A     |
| C     | Thirty days after B, **with nothing changed**, anomalies un-triaged   | 3        | 67    | C     |
| D     | B, plus a Savings Plan covering baseline usage AND anomaly triage SLA | 0        | 100   | A     |

**STATE C is the point.** Between B and C, nobody deploys anything. No
console click, no `apply`, no merge. Three findings appear because time
passed: an EBS snapshot ages past `snapshot_retention_days`; the app
instance's uptime crosses `long_running_instance_days` without a Savings
Plan being purchased; and Cost Anomaly Detection produces at least one
anomaly which nobody provides Feedback on within `anomaly_triage_days`.

You will not need to wait 30 days to see STATE C, because the clock is
injected into the auditor rather than read from `datetime.now()`, and the
tests reproduce STATE C by moving the clock. But the point of the state is
that in a real account, it happens WITHOUT ANYBODY BEING TOLD.

## Sixteen checks, one CRITICAL, one LOW

Every day in this repo has 16 checks. On Day 09:

| ID       | Severity | What it catches                                            |
|----------|----------|------------------------------------------------------------|
| COST-001 | HIGH     | No AWS Budget defined                                      |
| COST-002 | HIGH     | Budget without a notification threshold                    |
| COST-003 | HIGH     | No Cost Anomaly Detection monitor                          |
| COST-004 | MEDIUM   | Cost allocation tag coverage below threshold               |
| COST-005 | HIGH     | Unattached EBS volume                                      |
| COST-006 | MEDIUM   | Unassociated Elastic IP                                    |
| COST-007 | MEDIUM   | EBS snapshot older than retention                          |
| COST-008 | MEDIUM   | EC2 stopped for extended period                            |
| COST-009 | MEDIUM   | Previous-generation instance family                        |
| COST-010 | LOW      | gp2 EBS volume (should be gp3)                             |
| COST-011 | MEDIUM   | Classic Load Balancer                                      |
| COST-012 | MEDIUM   | VPC has NAT gateway but no S3/DynamoDB gateway endpoints   |
| COST-013 | MEDIUM   | CloudWatch log group has no retention                      |
| COST-014 | MEDIUM   | S3 bucket has no lifecycle rule                            |
| COST-015 | MEDIUM   | Long-running EC2 with zero Savings Plan or RI coverage     |
| COST-016 | CRITICAL | Cost anomaly untraiged past SLA                            |

Two of these (`COST-002`, `COST-004`) are **silent by design** against this
stack — the Terraform structurally cannot produce the fault. Five of them
(`COST-007`, `COST-008`, `COST-012`, `COST-015`, `COST-016`) are **silent
by situation** in STATE A because a freshly-applied stack cannot produce
them yet; three of the five (`COST-007`, `COST-015`, `COST-016`) become the
STATE C decay findings on the same account thirty days later without
anything having changed.

The one LOW (`COST-010`, gp2 → gp3) is on this day because the choice is
real, cheap, and non-urgent, unlike anything on Day 08. The one CRITICAL
(`COST-016`) is on this day because it is the meta-check: a stack where
every other check is green and `COST-016` is red is an account that has
bought cost tooling and not yet started using it, which is the modal state
of cost tooling.

## What you will build

A single Terraform stack that provisions, in `us-east-1`:

- One VPC with public and private subnets across two AZs, with an
  Internet Gateway attached. No NAT gateway by default — enabling it is
  step 5 of the lab, so that `COST-012` fires.
- One correctly-sized application instance (`t3.micro`, `gp3` root, 8 GB)
  behind an optional Application Load Balancer.
- Deliberate examples of every category the auditor checks:
  - Two unattached EBS volumes (`COST-005`),
  - Two unassociated Elastic IPs (`COST-006`),
  - One previous-generation `t2.micro` instance with a `gp2` root
    (`COST-009` + `COST-010`),
  - One Classic Load Balancer (`COST-011`),
  - Two CloudWatch log groups with no retention (`COST-013`).
- One S3 bucket for artifacts, with lifecycle rule OFF by default so
  `COST-014` fires; toggling `enable_bucket_lifecycle = true` attaches a
  30-day / 90-day / 365-day transition-and-expiration rule.
- An AWS Budget monthly cost alarm (off by default; `enable_budget = true`
  turns it on) with notifications wired to an SNS topic.
- A Cost Anomaly Detection monitor and subscription (off by default;
  `enable_cost_anomaly_monitor = true` turns them on).

The lab README walks the whole apply sequence and names which check moves
at each step.

## What you will run

A Python auditor (`cost_audit.py`) that reads the account through boto3 and
produces one of three outputs — table, JSON, or CSV — plus a compliance
score out of 100 and a grade. Everything the checks reason about arrives
through `collect()` into a normalised dict, and every check is a pure
function over that dict, so the whole check surface is testable without
credentials. The 47 unit tests in `lab/python/tests/test_checks.py` prove
that STATE A, STATE B and STATE C are the exact scores the contract says
they are.

```
$ python3 cost_audit.py --profile bootcamp --region us-east-1 --prefix cbc-day09
Collecting cost posture...
  · EC2, EBS, EIPs, VPC topology
  · CloudWatch log groups
  · S3 buckets and lifecycle
  · Classic Load Balancers
  · AWS Budgets (us-east-1)
  · Cost Anomaly Detection (us-east-1)
  · Reserved Instances and Savings Plans
Running checks...

====================================================================================================
  COST OPTIMISATION AUDIT
  CareerByteCode · Day 09 · Cost Optimization & Cost Anomaly Detection
  2025-06-01 12:00:00 UTC
====================================================================================================

  Scanned: 2 instance(s) · 4 volume(s) · 0 snapshot(s) · 2 EIP(s) · 1 VPC(s) · 3 log group(s) ·
  1 bucket(s) · 1 classic ELB(s) · 0 budget(s) · 0 anomaly monitor(s) · 0 anomaly record(s)

  ------------------------------------------------------------------------------------------------
  SEVERITY   CHECK      RESOURCE                          FINDING
  ------------------------------------------------------------------------------------------------
  HIGH       COST-001   account/123456789012              Account has no AWS Budget defined
  HIGH       COST-003   account/123456789012              Account has no Cost Anomaly Detection…
  HIGH       COST-005   vol-orphan-a                      EBS volume is unattached and older th…
  HIGH       COST-005   vol-orphan-b                      EBS volume is unattached and older th…
  MEDIUM     COST-006   eipalloc-a                        Elastic IP is unassociated and billin…
  MEDIUM     COST-006   eipalloc-b                        Elastic IP is unassociated and billin…
  MEDIUM     COST-009   i-prevgen                         Instance is of previous-generation fa…
  MEDIUM     COST-011   cbc-day09-classic                 Classic Load Balancer (ELBv1) is in use
  MEDIUM     COST-013   /aws/cbc-day09/unbounded-a        CloudWatch log group has no retention…
  MEDIUM     COST-013   /aws/cbc-day09/unbounded-b        CloudWatch log group has no retention…
  MEDIUM     COST-014   cbc-day09-artifacts-abc           S3 bucket has no active lifecycle rule
  LOW        COST-010   vol-prevgen-root                  EBS volume type is gp2, superseded by…
  ------------------------------------------------------------------------------------------------

  ...
  COMPLIANCE SCORE: 31/100   F — do not point this at production data
====================================================================================================
```

## Prerequisites

You are ready if:

- You have completed Days 01–08.
- You have a working `bootcamp` AWS CLI profile.
- The IAM role or user attached to that profile has `SecurityAudit` or
  `ReadOnlyAccess` PLUS `AWSBillingReadOnlyAccess` — the last one is what
  most cost-optimisation labs skip, and it is what makes `COST-001`,
  `COST-002`, `COST-003`, `COST-015` and `COST-016` work.
- You have `terraform` or `tofu` version ≥ 1.5, and `boto3` installed via
  `pip install -r lab/python/requirements.txt`.
- You have set `notification_email` in `lab/terraform/terraform.tfvars` to
  an inbox you actually read. This day depends on emails arriving —
  differently from the DR day where you could pretend to receive them.

## Quick start

```bash
cd lab/terraform
cp terraform.tfvars.example terraform.tfvars

# Edit terraform.tfvars — set at minimum:
#   notification_email = "your-email@example.com"
#   owner              = "your-name"

tofu init
tofu apply

# Now confirm the SNS subscription in your inbox. Nothing that follows
# works until you do.

cd ../python
pip install -r requirements.txt
python cost_audit.py --profile bootcamp --region us-east-1 --prefix cbc-day09
```

Then read `lab/README.md` and walk through the eleven-step sequence, which
takes about 90 minutes if you go through it in order.

## Repository layout

```
day-09-cost-optimization-ai-recommendations/
├── README.md                          # this file
├── sync_contract.py                   # verify or write the contract to all copies
└── lab/
    ├── README.md                      # the walkthrough — start here after this file
    ├── interview-qa.md                # ten interview questions with worked answers
    ├── teardown-checklist.md          # tear-down, prioritised (delete the ELB FIRST)
    ├── trainer-notes.md               # notes for the instructor
    ├── cost-observations.md           # template for recording real cost observations
    ├── terraform/                     # the stack
    │   ├── providers.tf
    │   ├── variables.tf
    │   ├── main.tf
    │   ├── outputs.tf
    │   ├── terraform.tfvars.example
    │   └── .gitignore
    └── python/                        # the auditor
        ├── cost_audit.py              # 16 checks, three renderers, argparse CLI
        ├── requirements.txt
        ├── .gitignore
        ├── tests/
        │   ├── __init__.py
        │   └── test_checks.py         # 47 unit tests, credentials-free
        └── challenge/
            ├── generate_challenge.py  # regenerator (--check verifies drift)
            └── cost_audit_challenge.py # generated scaffold, for you to finish
```

## What "challenge mode" is

`lab/python/challenge/cost_audit_challenge.py` is the reference file with
every check body stubbed out. The docstrings are the specification. Point
the test runner at it:

```bash
cd lab/python
COST_AUDIT_MODULE=cost_audit_challenge PYTHONPATH=challenge \
  python3 -m unittest discover -s tests -v
```

You will see 22 failures and 25 passes. The 25 are the "silent" tests
(empty return matches empty return) plus renderers plus helpers plus
silent-by-design. Every "fires" test fails, along with the contract totals
and the score. Your job is to make them green in check-ID order.

Time budget: ~2 hours if you work through it methodically. The four longer
checks are `COST-004` (tag coverage arithmetic), `COST-008` (parsing
`StateTransitionReason` for the stopped-at timestamp), `COST-012` (the
NAT/endpoint graph across VPCs), and `COST-016` (the CRITICAL one whose
logic is small but whose message needs to be right).

## What "the contract" is, and why the same block appears in five files

The finding contract — the total number of findings in each state, the
severity of each check, which checks are silent and why — is the single
most important reference material on this day. It is duplicated into
five files by design, so that a reader of any one of them sees the same
totals, the same states, and the same interactions in the same words.

Duplication is a maintenance risk. `sync_contract.py` exists to make the
risk cheap:

```bash
# Verify all copies are identical (default). Exit 1 on drift.
python3 sync_contract.py

# Rewrite all copies to match the source-of-truth (cost_audit.py's docstring).
python3 sync_contract.py --write

# Print the extracted source-of-truth to stdout.
python3 sync_contract.py --show
```

The five copy sites are: this file (below), `lab/README.md`,
`lab/terraform/outputs.tf` (`finding_contract` output),
`lab/python/cost_audit.py` (module docstring — the source-of-truth), and
`lab/python/tests/test_checks.py` (module docstring).

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

---

## Now go read `lab/README.md`

The lab walkthrough is where the actual work is. It follows the same
11-step sequence as the `next_steps` output from `terraform output`, and
each step names which check IDs you should see move.

## What comes next in the bootcamp

Day 10 (Capstone) integrates every day. There is nothing in Day 10 that is
new; there is only the composition of what you already have. Day 09 is the
last day that introduces a check surface. Read it in that spirit: this is
the LAST time this repo will say "here are 16 new categories of thing".

---

_© CareerByteCode. Instructor coordination via Sonali Kurade. Director:
Sangeetha._
