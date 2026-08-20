# Day 09 — Interview Questions

Ten questions someone might reasonably ask you in a systems design, SRE or
FinOps interview after Day 09. Each has a worked answer that tries to be
what a real answer sounds like rather than a bullet list.

---

## 1. Your account has a Cost Anomaly Detection monitor and a confirmed SNS subscription, and the CFO says nobody has ever received an anomaly alert. What is the shape of your first two questions?

The two questions are: "how long has the monitor existed" and "what
threshold does the subscription carry".

Cost Anomaly Detection needs about 10 days of baseline before it can
produce its first anomaly. If the monitor was created two days before the
question, the answer is "it has not fired yet". That is not a bug — it is
the service's honest constraint.

The threshold matters because a subscription only notifies you if the
anomaly's dollar impact is above the threshold. A subscription with a
threshold expression of `TotalImpact >= $1000` filters out the anomalies
worth $200 that are the interesting ones. The default threshold in the
console wizard is often higher than people realise; verify it.

If both are ruled out, the answer is `COST-016`: the monitor is producing
anomalies, they are being delivered to a subscription, the emails are
being sent, and they are going to an inbox nobody reads. That is the
modal failure mode, and it is a governance conversation rather than a
technical one.

---

## 2. Why is there one `LOW` and one `CRITICAL` check on this day?

The `LOW` is `COST-010` — gp2 EBS volumes when the strict successor gp3
exists. It is low severity because the choice is real and cheap and
non-urgent: no workload is degraded by staying on gp2, migration is
in-place, and the saving is modest per volume. It stays a check because
"nobody has looked" is a governance problem even when the specific defect
is minor.

The `CRITICAL` is `COST-016` — cost anomalies raised without triage. It is
critical because it is the meta-check: a stack where every other check is
green and this one is red is an account that has bought cost tooling and
not yet started using it, which is one of the largest cost-programme
failure modes in the industry. Every other finding on the day is a
specific resource. This one is "the whole system does not work".

Everything else is HIGH or MEDIUM because those are the ordinary
failure modes: a missing guardrail (HIGH — no ceiling on spend), a
guardrail that speaks into the void (HIGH), a specific wasteful resource
(MEDIUM), a resource that is superseded by a strict successor (MEDIUM).

---

## 3. A budget without a notification threshold — how does that happen in practice, and what does it cost you?

It happens three ways.

**The console wizard,** which lets you click through to "create budget"
and skip the notification step. If your only goal was to see the CSV
export of last quarter's spend, you may never revisit the budget.

**The Terraform module** that made notifications optional. There are
plenty of open-source modules that let `notifications = []` produce a
resource without any. This lab's `budget_notifications` variable's
validation refuses the empty list, on purpose — every path through this
Terraform produces at least one notification with a subscriber, so
`COST-002` is silent by design against this stack. Not every stack does
that.

**The threshold change** that removed all notifications. Somebody was
tuning thresholds, deleted them intending to add better ones, was
interrupted, forgot.

What it costs you: the budget itself is free. The notification is free.
What is missing is the alarm — the trigger that starts the conversation
when spend crosses the ceiling. So the account has a "budget" on paper,
which is what people cite when they say "we have cost governance", and
what they actually have is a decorative resource that produces no output.

The check catches exactly this transition. Fixing `COST-001` by creating
a budget with zero notifications is one of the two most common ways the
transition happens, so `COST-002` exists to catch that specific move.

---

## 4. Cost allocation tags do not show up in Cost Explorer even after you set them. What is the missing step?

Cost allocation tags have to be ACTIVATED, separately from being applied.
Setting `Tags.Owner = "platform"` on a resource is one step. Activating
`Owner` as a cost allocation tag in the Billing console is a second step.
Only after both are done will Cost Explorer let you group by that tag.

Activation is:
- Account-wide (not per-tag-value, per-tag-key).
- One-way (you cannot delete history to a deactivated tag).
- Manual (there is no boto3 call to activate tags — it is a console-only
  operation, though the AWS Billing Conductor has some overlap for
  linked accounts).
- Delayed (the tag has to have been present on a resource for the
  billing period before it will appear in the "activate" list).

That last point is the sharpest: if you tagged a resource yesterday and
went to activate the tag today, you may not find it, because AWS has not
processed a billing period with that tag yet. The tag has to have been
seen by the billing pipeline before it can be activated.

`COST-004` catches the "coverage" side of this — resources without the
required tags. It does not catch the "activated" side, because activation
is not queryable from a normal role. Most cost programmes need both.

---

## 5. Why does the auditor report unassociated EIPs at $3.60/month? That number changed recently, didn't it?

Yes — since February 2024, AWS bills for public IPv4 addresses even when
they are attached to something, at $0.005/hour. Before that, only
unassociated Elastic IPs billed at that rate.

So the effect is:

- Unassociated EIP: $3.60/month, unchanged. That is what `COST-006`
  catches.
- Associated EIP (or public IP on an EC2 launch): $3.60/month post-Feb
  2024, previously free.
- Public IPv4 assigned by AWS to any EC2 instance, ELB, RDS: same.

The check does not fire on associated public IPs because they usually
serve a purpose — an instance you can SSH into, a load balancer's
external endpoint. What it does catch is the specific "billing for
nothing" case: an Elastic IP allocated, not attached to any resource,
sitting.

If you want the full picture — the account's entire public IPv4 footprint
and its cost — the answer is Cost Explorer, grouped by service = "EC2 -
Other" and usage type filtered to `PublicIPv4:InUseAddress`. That is not
what the auditor was built for.

---

## 6. The check for "long-running instance with no Savings Plan" is deliberately narrow. What is it not doing, and why not?

It is not saying "you should buy a Savings Plan".

"Should we buy a Savings Plan" is a real, difficult question that
depends on how confident the team is that the workload will still exist
in a year (the shortest term is 1-year, No Upfront), whether the
workload is elastic (Savings Plans commit to a dollar-per-hour spend),
whether it can move regions/families (Compute Savings Plans cover any
family and region, Instance Savings Plans commit to a specific family in
a specific region for a further discount), and how much cash is available
today (All Upfront gives another 5-10% off).

The check has no way to answer any of that. What it CAN answer is: "for a
workload that has been running longer than 30 days, has anyone LOOKED at
the question". A "yes, we looked, decided not to" answer is a
suppression comment, not a finding to leave open — the check's
remediation text says so explicitly.

The check also stays silent if ANY Savings Plan or Reserved Instance
exists on the account, on the reasoning that "auditor cannot know from
list responses whether a specific instance is covered", and firing on
every long-running instance when a Savings Plan probably covers most of
them would train people to ignore the check. That is a design decision:
useless noise loses noticeable signal, so the check errs on the side of
staying silent.

---

## 7. If you fix every finding but do nothing else, will your bill drop?

Marginally.

The findings that produce a direct dollar saving:
- `COST-005` (unattached EBS volumes): $0.64/month for two 8 GB volumes.
- `COST-006` (unassociated EIPs): $7.20/month for two.
- `COST-011` (Classic ELB): $16.20/month.
- `COST-013` (unbounded log groups): depends on how much they hold, but
  starts small and grows.

Total, on this lab's stack: about $25/month. On a real account of any
size, `COST-013` and `COST-014` are usually the dominant lines because
they compound with data volume, and `COST-011` may or may not exist.

The findings that produce a saving IF something else happens:
- `COST-009` and `COST-010` (previous-gen, gp2): 0-10% depending on
  whether you rebuild anything.
- `COST-012` (NAT without endpoints): $0 today, potentially large on a
  workload that reads a lot from S3.
- `COST-015`: 15-30% saving on baseline compute, requires committing
  cash.

The findings that produce a saving because they trigger conversations:
- `COST-001`, `COST-003`: no direct saving; they enable the
  conversations that produce all subsequent savings.
- `COST-002`, `COST-014`, `COST-016`: same shape.
- `COST-004`: enables cost allocation, which enables per-team
  chargeback, which produces the political mechanism that unlocks the
  above.

So the honest answer is: fixing every finding here gets you ~$25/month
saving directly, plus the mechanism to save 20-40% of your total bill
over the next quarter. Which one matters depends entirely on how big the
bill is to start with.

---

## 8. In this lab, `COST-004` is silent by design. What is design-fragile about that, and what would you change in production?

The design that makes it silent is the `default_tags` block on the AWS
provider in `providers.tf`. Every resource this Terraform creates
inherits `Project`, `Day`, `ManagedBy` and `Owner` at plan time. The
check looks for `Owner` and `Project`, both of which are guaranteed to
be present on every resource, so the check cannot fire.

What is fragile:

- **Resources created outside this Terraform** don't inherit
  default_tags. A shell script, a console click, a Lambda function
  that creates an S3 bucket — none of them get the tags. In a lab this
  is fine because you have one Terraform and nothing else. In
  production it is common to have a mix.
- **`default_tags` doesn't work uniformly across every AWS resource
  type.** The provider has caught up on most, but S3 buckets, Elastic IPs
  and some CloudWatch resources have had quirks over the years. If you
  see `COST-004` fire against your own Terraform-created resource, the
  first suspect is a resource type where default_tags did not propagate.
- **The tag has to be activated in Billing to be useful.** Silent-by-
  design does not mean useful; it means the check will not fire. A tag
  present on every resource but not activated in Billing is present on
  every resource and useless in Cost Explorer.

In production I would extend the check in two ways. First, add a
CRITICAL_TAGS list per team — `CostCenter` for finance, `Environment`
for platform, `DataClassification` for security. Second, add an
`activated_tag_keys` collector that reads the Billing API's list of
activated cost allocation tags and warns when a required tag is present
on resources but not activated. That is the tag whose coverage looks
perfect and whose Cost Explorer view is empty.

---

## 9. What does STATE C actually feel like from the operator's perspective?

An operator in STATE C is doing what they were doing last week.

The workload is running. The dashboards are green. The Slack channel is
quiet. The bill from last month was $X and this month's projection is
$X + normal-monthly-drift. Nothing has changed and nothing needs to.

Meanwhile:
- One EBS snapshot from three months ago exists that nobody remembers
  taking. It bills for $0.40/month.
- The app instance has been running since the workload launched. There
  is no Savings Plan. Nobody has done the "should we buy one" analysis.
  The bill is about 25% higher than it needs to be.
- Cost Anomaly Detection has produced two anomalies in the last month.
  Both were delivered to an SNS topic that emails a distribution list.
  The distribution list has 40 people on it. Nobody clicked into either
  anomaly.

None of these things degrade the workload. None of them are urgent. But
the operator is running an account whose "cost posture" score dropped
from 100 to 67 in 30 days without them doing anything, and if they don't
have the auditor running on a schedule, they will not find out about it
until quarter-end.

That is the honest picture of a cost-optimised account that stopped
being cost-optimised. It is the picture of "we set it up correctly and
did not maintain it". That is the picture the day exists to help you
recognise.

---

## 10. Compare this day's approach to Day 08's. What is the same and what is different?

**Same:**

- Same finding contract shape. Same weights (CRITICAL 25, HIGH 10,
  MEDIUM 4, LOW 1, INFO 0). Same score-out-of-100. Same grading
  bands (A/B/C/D/F).
- Same auditor architecture. `collect()` separates all AWS I/O; every
  check is a pure function over the collected dict, so tests need no
  credentials.
- Same idea that a state can decay purely with the passage of time. On
  Day 08 that was `DR-008` (backup age crossing RPO) — the DR test
  passes on Monday and fails on Tuesday against an unchanged
  configuration. On Day 09 the same shape appears in three places
  (`COST-007`, `COST-015`, `COST-016`), which is what makes the
  clock-alone-changes-the-answer point undeniable rather than a
  quirk of one check.
- Same "silent by design" vs "silent by situation" distinction and the
  same explicit tests for both classes.
- Same sync_contract.py mechanism, same challenge scaffold, same test
  count (47).

**Different:**

- Day 08 had NO `LOW` and NO `INFO` because every fault either cost you
  data or cost you time during an outage. Day 09 has one `LOW`
  (`COST-010`) because the gp2/gp3 choice is real, cheap and non-urgent
  in a way that DR is not.
- Day 08 had one `CRITICAL` per subject (single-AZ compute, no RDS
  multi-AZ, never-restored account, un-gated automation). Day 09 has
  one `CRITICAL`, which is the meta-check about whether the whole cost
  programme works. The severity distribution reflects the fact that
  cost failures are quieter than DR failures.
- Day 08 checks care about REGIONS in a specific way (a copy target in
  the same region as its source is a defect). Day 09 checks care about
  the fact that Budgets, Cost Explorer and Savings Plans are pinned to
  `us-east-1` regardless of where the resources they describe live —
  which is a common failure of cost tooling that instantiates the Cost
  Explorer client in the caller's region.
- Day 08's insecure examples cost you almost nothing per hour to keep
  around while you play with them (a Classic ELB is $16/month, an old
  RDS is more). Day 09's insecure examples cost you almost nothing per
  hour to keep around either — but they are the shape of what does
  accumulate silently, which is the point.
- Day 08's tear-down is aggressive because leaving the resources costs
  you nothing meaningful. Day 09's tear-down is aggressive because
  leaving the resources costs you exactly the sums the checks were
  measuring, and it is a bad joke to leave an audit's "waste examples"
  on the account.

The pattern is the same. What is different is the failure mode's shape,
and once you have internalised that a cost audit is measuring a
different kind of decay than a DR audit, the rest of the day is
familiar.
