# Day 08 — High Availability & Disaster Recovery

**Advanced · Terraform + Python + AI · Hands-On Lab**

> A brand-new enterprise platform has survived seven days of construction. It
> has identity, a segmented network, self-healing compute, serverless
> automation, infrastructure as code, observability with AI incident analysis,
> and automated threat response. It has never been broken on purpose.
>
> Today you break it, time the recovery, and find out which of the numbers in
> your architecture document were measurements and which were wishes.

---

## The argument this day makes

**An untested recovery path is a hypothesis, and RTO is a claim about a
procedure nobody has run.**

Building for high availability is easy. It appears on every architecture
diagram ever drawn, and most of those diagrams are honest about the boxes and
silent about the arrows. Two Availability Zones, a load balancer, a Multi-AZ
database, a cross-region backup vault — you can build all of that in an
afternoon, and this repo has been quietly building most of it since Day 02.

The engineering problem is different and harder. **The failover path is the
only code in your system that runs exclusively during your worst hour.** That
makes it, structurally and inevitably, the least exercised code you own and
the most confidently described. Every other code path in your platform gets
run thousands of times a day by users who complain when it breaks. The
recovery path gets run when the region is on fire, by people who have been
awake for nineteen hours, from a runbook written by somebody who has left the
company.

So the whole of today is organised around a single distinction:

| A configuration audit asks | A recovery audit asks |
| --- | --- |
| Is there a backup? | Has anybody ever restored one? |
| Is the ASG multi-AZ? | Has an AZ failure ever been simulated? |
| Is there a failover record? | Has DNS ever actually moved? |
| Is the RTO documented? | When was it last measured, and by whom? |
| Is replication configured? | What is the lag right now, in seconds? |

The left column can be answered from a Terraform plan. **The right column
cannot be answered from anything except doing it.** Every check in
`dr_audit.py` that matters lives in the right column, and three of them
(DR-008, DR-010, DR-016) will change their answer on an account nobody has
touched — because a claim about a procedure decays continuously from the last
time somebody ran the procedure.

That last property produces the most uncomfortable fact in today's finding
contract, and it is the one worth carrying into your own work:

> **An audit that passes at 14:00 fails at 15:01 on an unchanged account.**

---

## Table of contents

- [The argument this day makes](#the-argument-this-day-makes)
- [Learning objectives](#learning-objectives)
- [Prerequisites](#prerequisites)
- [Architecture](#architecture)
- [Part 1 — Failure domains and blast radius](#part-1--failure-domains-and-blast-radius)
- [Part 2 — RTO and RPO are measurements, not settings](#part-2--rto-and-rpo-are-measurements-not-settings)
- [Part 3 — The four strategies, priced](#part-3--the-four-strategies-priced)
- [Part 4 — Three health checks, three jobs](#part-4--three-health-checks-three-jobs)
- [Part 5 — The data tier](#part-5--the-data-tier)
- [Part 6 — DNS failover, and why TTL is spent RTO](#part-6--dns-failover-and-why-ttl-is-spent-rto)
- [Part 7 — The recovery workflow](#part-7--the-recovery-workflow)
- [Part 8 — Chaos: the only thing that makes a number real](#part-8--chaos-the-only-thing-that-makes-a-number-real)
- [Part 9 — The mistakes people actually make](#part-9--the-mistakes-people-actually-make)
- [Part 10 — Cost](#part-10--cost)
- [Part 11 — Auditing recovery posture](#part-11--auditing-recovery-posture)
- [The finding contract](#the-finding-contract)
- [What you built](#what-you-built)

---

## Learning objectives

By the end of today you will be able to:

1. **Distinguish a failure domain from a blast radius**, and explain why
   multi-AZ is the answer to almost every real availability requirement while
   multi-region is the answer to a much narrower one.
2. **Find the single-AZ dependency inside a multi-AZ architecture** — and
   understand why the most common one is created by a cost optimisation rather
   than by carelessness.
3. **State an RTO and an RPO you can defend**, by knowing which parts of each
   are consumed by configuration alone before any human acts.
4. **Choose between backup-and-restore, pilot light, warm standby and
   active-active** on cost and on engineering burden, and say out loud when the
   correct answer is the cheapest one.
5. **Explain the three health checks in an AWS stack** — target group, EC2
   status, Route 53 — what each one can and cannot see, and why an ASG with
   `health_check_type = "EC2"` silently loses capacity for months.
6. **Configure backup that survives the event it exists for**: cross-region
   copies, vault lock in governance mode, and retention that outlasts a
   weekend.
7. **Build an automated recovery workflow with a brake** — kill switch, dry
   run, human approval before anything irreversible, and a verification step
   that can fail the execution.
8. **Break your own infrastructure on purpose and time the recovery**, then
   compare the measurement against what you wrote down beforehand.
9. **Audit a recovery posture with `dr_audit.py`**, and read a report that
   changes with the clock without concluding the tool is broken.

---

## Prerequisites

| You need | Because |
| --- | --- |
| Days 01–07 completed, or their equivalent | Today assumes an IAM profile, a VPC pattern, an ASG behind a load balancer, Terraform with remote state, and the habit of a kill switch |
| An AWS account you can afford to spend ~$85/month in | **This is the first expensive day in the repo.** See [Part 10](#part-10--cost) before you apply |
| Terraform ≥ 1.10 | The stack uses cross-variable `validation` blocks, which need 1.9+ |
| Python 3.9+ and `boto3` | `dr_audit.py` reads nine AWS services across two regions |
| Two regions enabled | `us-east-1` and `us-west-2` by default. Both must be usable by your profile |
| A stopwatch | Not a joke. Half of today is measurement |

> **Set `notification_email` in `terraform.tfvars` and confirm the SNS
> subscription before you start.** An unconfirmed subscription means the
> recovery workflow can fail your account over at 03:00 with nobody told: the
> failover succeeds, the notification is accepted, billed, and discarded, and
> you find out from the bill.

---

## Architecture

```
   ┌─────────────────── PRIMARY REGION (us-east-1) ────────────────────┐
   │                                                                    │
   │   Route 53 health check ──────► ALB (public subnets, 2 AZs)        │
   │            │                        │                              │
   │            │                        ▼                              │
   │            │                   target group                        │
   │            │                        │                              │
   │            │                        ▼                              │
   │            │            ASG (private subnets, 2 AZs)               │
   │            │             health_check_type = ELB                   │
   │            │                        │                              │
   │            │                        ▼                              │
   │            │              DynamoDB (PITR on)  ──── global table ───┼──┐
   │            │              S3 (versioned)      ──── CRR ───────────┼──┼─┐
   │            │              RDS (optional)                          │  │ │
   │            │                                                      │  │ │
   │            │   ┌──────────── recovery path ─────────────┐          │  │ │
   │            └───│  kill switch (SSM)                     │          │  │ │
   │                │      ↓                                 │          │  │ │
   │                │  Step Functions: assess → decide →     │          │  │ │
   │                │  approve (waitForTaskToken) →          │          │  │ │
   │                │  fail over → verify → notify           │          │  │ │
   │                └────────────────────────────────────────┘          │  │ │
   │                                                                    │  │ │
   │   AWS Backup vault ──────────── copy rule ─────────────────────────┼──┼─┼─┐
   │   Chaos Lambda + deny-all NACL                                     │  │ │ │
   └────────────────────────────────────────────────────────────────────┘  │ │ │
                                                                           │ │ │
   ┌──────────────────── DR REGION (us-west-2) ─────────────────────────┐  │ │ │
   │   DynamoDB replica ◄───────────────────────────────────────────────┼──┘ │ │
   │   S3 replica bucket (versioned) ◄──────────────────────────────────┼────┘ │
   │   AWS Backup copy vault ◄──────────────────────────────────────────┼──────┘
   └────────────────────────────────────────────────────────────────────┘

   Plus, when create_insecure_examples = true, the same ideas built wrong:
   a single-AZ ASG with EC2 health checks and a zero grace period, an
   unversioned "dr-archive" bucket, a sessions table with no PITR, a manual
   snapshot nobody scheduled, and a one-state failover workflow with no brake.
```

Two things about this diagram are worth noticing before you read any further.

**The DR region contains no compute.** That is deliberate, and it is the
correct answer for most workloads — see [Part 3](#part-3--the-four-strategies-priced).
Data is replicated continuously; compute is a decision you make during the
incident. It also means this stack's honest RTO for a regional event includes
"stand up an environment", which is exactly the number people leave out.

**The recovery path has more boxes than the thing it recovers.** A kill
switch, an assessment, an approval gate, an execution step, a verification
step and a notification, to perform two API calls. That ratio is not
overengineering. It is what it costs to make an irreversible automated
decision responsibly, and [Part 7](#part-7--the-recovery-workflow) is the
argument for every box.

---

## Part 1 — Failure domains and blast radius

Two ideas that get used interchangeably and are not the same. Getting them
apart is most of what makes a DR conversation productive.

### 1.1 What an Availability Zone actually is

An AZ is a **failure domain**: the unit that fails together.

Physically, one or more discrete data centres with independent power, cooling,
and network paths, within a metropolitan area, joined to the other AZs in the
region by dedicated low-latency fibre. The distances are small — typically
single-digit to low-double-digit kilometres — and the latency between them is
under a millisecond.

That last number is not trivia. It is the whole reason AZs and regions need
different answers:

> **AZs are close enough that synchronous replication is practical. Regions
> are not.**

Sub-millisecond round trip means an RDS Multi-AZ standby can acknowledge every
commit before the primary returns, which is why your RPO for an AZ failure is
zero. A cross-region standby 4,000 km away adds ~70 ms per round trip to every
write, which no transactional workload will accept — so cross-region
replication is asynchronous, which means it has a lag, which means it is an
RPO rather than a guarantee.

Losing an AZ, if you have built for it, is a **capacity event**, not an
architecture event. Nothing about your design changes. The remaining zones
absorb the load. Your job on the day is to have enough headroom, which brings
us to the arithmetic nobody does:

> Two instances across two AZs, one AZ fails, and the survivor is now taking
> **100% of the load it was sized at 50% for.**
>
> "Multi-AZ" and "multi-AZ with enough capacity to survive losing an AZ" are
> different architectures with different bills, and the second one is what you
> promised. N+1 across two AZs means running at 50% utilisation. Across three
> AZs it means 67%. **That is the real reason three AZs is often cheaper than
> two** for anything large — you buy less idle headroom.

### 1.2 What a region actually is, and what "region down" usually means

A region is a **blast radius**: the unit that fails together when the failure
is not physical.

Regions share almost no infrastructure. Different buildings, different power,
different network. If AZ failures were the only thing that happened, one
region with three AZs would be enough for nearly everyone.

But look at what actually causes large public cloud outages. It is very rarely
a flood. It is:

- a configuration change pushed to a regional control plane
- a capacity cascade, where a partial failure causes retries that cause a
  larger failure
- a dependency on a service in one region that nobody had documented
- an expired certificate
- a deployment pipeline that shipped the same bug everywhere

**Multi-region protects you from those in exact proportion to how independent
your two regions really are** — and a DR region deployed by the same pipeline,
from the same repository, five minutes later, is not very independent at all.
It has the same bad config, the same expired certificate, the same bug.

This is the most under-discussed point in DR and it deserves to be said
plainly: **most multi-region architectures do not protect against most
multi-region outages.** They protect against the physical events, which are
rare, and share the correlated failure modes, which are common. If you want
genuine independence you need different deployment timing, different change
windows, and the discipline to not ship to both at once — which is an
organisational property, not an architectural one.

### 1.3 The NAT gateway trap

This is the most common real high-availability defect in production AWS
accounts, it is check **DR-002**, and the reason it is so common is that it is
produced by a **cost optimisation rather than by carelessness**.

A NAT gateway is zonal. It lives in one subnet, in one AZ, and costs ~$32.85 a
month plus ~$0.045 per GB processed. The correct architecture is one per AZ,
with each private subnet routing to the gateway in its own zone. For two AZs
that is ~$65.70 a month for a lab that serves no traffic.

So somebody — entirely reasonably, during a cost review, with a spreadsheet
that says the NAT gateways are 40% of the bill — deletes one and points both
private route tables at the survivor. The bill halves. Every test passes. The
architecture diagram is unchanged, because the diagram shows two AZs and there
are still two AZs.

Now picture the failure, precisely, because **it is not an outage and that is
what makes it dangerous**:

1. AZ-a becomes unavailable.
2. Instances in AZ-b keep running. They pass EC2 status checks.
3. They pass ALB target group health checks — because a target group health
   check is an HTTP GET **from the load balancer, inside the VPC**, and does
   not traverse NAT.
4. The load balancer is happy. The dashboard is green. There is no alarm.
5. Every outbound call fails with a timeout: the payment provider, the OAuth
   endpoint, the package repository during a deploy, S3 if there is no gateway
   endpoint.

You get an incident that reads as **"third-party API is down"** for the first
twenty minutes, and the first twenty minutes of an incident are the ones where
being wrong is most expensive.

The stack ships with `nat_gateway_strategy = "single"` **deliberately**, so
that DR-002 fires against your own infrastructure rather than against a
strawman. Lab step 9 is to flip it to `per_az`, apply, re-run the audit, watch
the finding disappear, and then look at the price and decide honestly whether
this workload should pay it. Both answers are defensible. Only one of them is
defensible silently.

> **The genuinely cheap correct answer is often neither.** VPC **gateway**
> endpoints for S3 and DynamoDB are **free**, and they remove the largest
> source of NAT traffic in most stacks while also removing an AZ dependency
> from your data path. Interface endpoints are ~$7.30/month per endpoint per
> AZ. Once you need three or four of them, per-AZ NAT is cheaper; below that,
> endpoints win on both cost and availability. This stack creates the S3
> gateway endpoint unconditionally, because free is free.

### 1.4 AZ names are per-account aliases

`us-east-1a` in your account and `us-east-1a` in mine are **different physical
facilities**.

AWS randomises the mapping from AZ name to AZ ID per account. The reason is
sensible — it stops everyone piling into the alphabetically-first zone — and
the consequence catches people out constantly:

- An AZ name in a runbook is **meaningless to anyone in another account**.
- "We moved everything out of us-east-1a during the incident" is not a
  statement another team can act on.
- Cross-account capacity comparisons using AZ names are nonsense.

The stable identifier is the **AZ ID**: `use1-az4`, `usw2-az1`. It is the same
physical place in every account. `data.aws_availability_zones.available`
exposes both, and this stack outputs both:

```bash
terraform output availability_zones      # the per-account aliases
terraform output availability_zone_ids   # the real, comparable identifiers
```

Use the ID in anything more than one account will read.

---

## Part 2 — RTO and RPO are measurements, not settings

### 2.1 The two numbers

| | Question it answers | Unit |
| --- | --- | --- |
| **RTO** — Recovery Time Objective | How long can the service be unavailable? | Time |
| **RPO** — Recovery Point Objective | How much data can we lose? | **Also time** |

RPO being measured in time is the part that clicks late for most people. You
do not lose "some records"; you lose "everything written since the last
recoverable point", and the size of that is a function of how often you make
recoverable points. **Your backup schedule is your RPO ceiling.** Daily at
05:00 means that at 04:59 you are 23 hours 59 minutes from your last recovery
point, and no amount of replication elsewhere changes that for the resources
that plan protects.

This stack has `rto_target_minutes` and `rpo_target_minutes` variables that
**configure nothing**. Nothing in AWS reads them. They exist to be printed
back at you by `terraform output`, written into the recovery workflow's
notifications, and compared against by check DR-008.

That is the point. **Setting an RTO in a tfvars file feels like configuration
and is closer to a New Year resolution.**

### 2.2 Where RTO actually goes

Run `terraform output rto_budget_already_spent` after you apply. It prints the
parts of your recovery budget that are consumed by **configuration alone**,
before any human or any automation does anything:

| Component | With the shipped defaults | Why |
| --- | --- | --- |
| ALB detection | 60 s | `interval` 30 × `unhealthy_threshold` 2 |
| ALB connection draining | 30 s | `deregistration_delay` — AWS's default is **300** |
| ASG grace period | 300 s | a replacement is not judged for this long |
| Route 53 detection | 90 s | `request_interval` 30 × `failure_threshold` 3 |
| DNS TTL | up to 60 s | worst case for a client that just refreshed |
| Instance boot to healthy | ~90–180 s | **measure this; do not accept the estimate** |

Add those up and you are already past four minutes before anything interesting
happens. And the list is missing the three largest components in every real
incident, because none of them is in a config file:

1. **Detection by a human.** Not by a health check — by a person deciding that
   the thing the health check is saying means what they think it means.
2. **The decision to act.** Somebody has to say "we are failing over" out
   loud, and that sentence has a career attached to it.
3. **Data reconciliation.** Unbounded, if your replication was asynchronous.

In most measured incidents those three exceed everything in the table
combined. An RTO that counts only the technical steps is not an RTO; it is the
technical half of one.

> **The AWS default that costs the most and is tuned the least is
> `deregistration_delay = 300`.** During a deliberate failover, five minutes of
> connection draining is five minutes of your recovery budget spent being
> polite to connections that are about to be cut anyway. Set it to your 99th
> percentile request duration plus a margin. Not to 300 because that is what
> was in the box.

### 2.3 Where RPO actually comes from

| Mechanism | RPO | Guaranteed? |
| --- | --- | --- |
| RDS Multi-AZ standby | **0** for an AZ failure | Yes — synchronous |
| DynamoDB global tables | typically < 1 s | No SLA, but **there is a metric** |
| DynamoDB PITR | ~5 minutes | Yes, continuous |
| RDS automated backups | ~5 minutes via transaction log | Yes, if retention > 0 |
| S3 CRR, default | seconds to minutes | **No SLA and no metric** |
| S3 CRR + Replication Time Control | 15 minutes for 99.99% of objects | **Yes, with metrics** |
| Daily snapshots only | up to 24 hours | Yes, trivially |

Read that table again with money in mind. The difference between "a few
minutes, probably" and "15 minutes, contractually" for S3 is Replication Time
Control, at ~$0.015 per GB replicated — roughly doubling the per-GB cost of
replication.

**And what you are actually buying is the metric, not the speed.** The data
replicates either way. What $0.015/GB buys is the ability to answer "what is
my replication lag right now" — which means the ability to state an RPO that
is a number rather than an adjective. This is check **DR-013**, and it is the
only check in today's set that fires on something which is not broken.

> Day 06's argument, in new clothes: a summary you cannot check is worse than
> no summary. An RPO you cannot measure is worse than no RPO, **because you
> will quote it.**

DynamoDB global tables are the exception that makes today teachable: they
publish `ReplicationLatency` to CloudWatch. That number, in seconds, is your
worst-case data loss if the source region disappears right now. Almost nothing
else in AWS lets you watch your own RPO on a graph, which is why this stack's
primary data store is DynamoDB rather than RDS.

### 2.4 Writing the number down before you measure it

**Lab step 2 asks you to write three predictions, in seconds, before you touch
anything:**

- (a) terminate one instance → service back to full healthy capacity
- (b) isolate one AZ → the ALB stops routing to that zone
- (c) restore a DynamoDB table → a usable table the application can query

Then steps 7 and 8 measure the same three things.

The ordering is the pedagogical point of the entire day. **A number you write
down after seeing the answer is not a prediction**, and every DR plan in the
world is full of numbers written in that order. In most first attempts the
measurement is between two and ten times the declaration, and the gap is the
lesson.

The (c) prediction is the one that humbles people. The restore itself is
usually fast. What takes the time is that **a DynamoDB PITR restore creates a
new table** — it cannot restore in place, and it cannot restore into an
existing table. So the procedure is not "restore the table"; it is "restore to
a new table, then repoint every consumer at a different name, then decide what
to do about whatever wrote to the old table in the meantime". That is
application work performed under pressure, and it is where the RTO of a data
restore actually goes.

---

## Part 3 — The four strategies, priced

Run `terraform output dr_ladder_comparison` for the version of this table with
this stack's actual numbers in it.

### 3.1 Backup and restore

**~$85/month for this stack · RTO 2–5 min within a region, hours across
regions · RPO = your backup interval**

Data is backed up and copied to another region. There is no standing compute
anywhere except production. Recovery means creating an environment.

This is what today's stack builds, and **it is the right answer for most
workloads and most teams.** It survives an AZ failure automatically (that part
is not "restore" — the ASG and the ALB handle it in minutes). It survives a
regional failure slowly, at whatever pace you can stand up an environment,
which for an infrastructure-as-code shop that has practised is hours and for
everyone else is a day.

### 3.2 Pilot light

**~$95/month · RTO 1–4 hours · RPO minutes**

Data replicated continuously; compute defined but not running. Databases
exist and are small; instances are defined in launch templates and scaled to
zero.

Cheap, and slower than it looks on paper. **The compute has never booted in
the DR region**, which is where the four hours actually go: the AMI is not
copied, the instance type is not available, the security group references a
group ID that does not exist in that region, the launch template references a
key pair that was never created there. Every one of those is a five-minute
problem and there are eight of them.

### 3.3 Warm standby

**~$160/month · RTO 10–30 min · RPO minutes**

A scaled-down but running copy of the environment in the DR region. Failover
means scaling it up and moving traffic.

The cost on the invoice is roughly double. **The cost that is not on the
invoice is that you now have two environments to deploy to, two sets of
configuration to drift, and a second environment that is exercised only during
an incident.** A warm standby that has been receiving deploys for eight months
and traffic for zero minutes is a warm standby whose behaviour under load is
unknown.

There is also a specific trap this stack's CP2 can create for you: **a warm
standby that was scaled up for a test and never scaled back.** The failover
test succeeds, everybody is pleased, and the DR environment is running at
production capacity because that is what the test needed. Scaling it back down
is step 11 of a runbook that ended at step 9 when the test passed. This is the
most expensive item in `terraform output silent_cost_growth` and the least
visible, because the resources are correct, tagged, and doing exactly what
they were told.

### 3.4 Active-active

**~$180/month + engineering · RTO near zero · RPO = replication lag**

Both regions serve traffic. There is no failover event; there is a capacity
change.

The bill is not the cost. The cost is that **your application now needs a
conflict story for every data model it owns.** DynamoDB global tables are
multi-active with **last-writer-wins** conflict resolution and nothing else:
if both regions accept a write to the same item during a partition, one of
them is silently discarded and the survivor is whichever clock was ahead.

Whether that is acceptable is a property of your application's semantics, not
of DynamoDB. For an append-only event log it is fine. For a counter, an
inventory level, or a bank balance it is data loss with extra steps. This is
the reason "just go active-active" is a six-month project rather than a
checkbox.

### 3.5 Choosing, honestly

```
   cost
     ▲
     │                                          ● active-active
     │
     │                        ● warm standby
     │
     │        ● pilot light
     │   ● backup & restore
     └──────────────────────────────────────────────────────► RTO
       hours          30 min        10 min        near zero
```

The honest note, which belongs in your design review and is usually absent
from it:

> Going from backup-and-restore to warm standby costs roughly double and buys
> protection against a class of event that happens to a given region roughly
> once every few years. **Many organisations should choose the cheap option and
> spend the difference on testing it.**
>
> An untested warm standby and a tested backup-and-restore have very different
> real RTOs from their advertised ones, and the difference usually runs in
> favour of the cheap one.

Say that out loud. Somebody in the room is about to spend $200k a year on a DR
region nobody will ever fail over to, because failing over to it has never
been rehearsed and nobody will authorise it during an actual incident.


---

## Part 4 — Three health checks, three jobs

This is the section people skim and then spend an afternoon debugging. Almost
every surprising result in today's lab traces back to one of these three doing
exactly what it says and not what was assumed.

There are **three independent health checks** in this stack. They ask
different questions, they fail independently, and **none of them is a
substitute for another.**

### 4.1 The target group health check

| | |
| --- | --- |
| **Question** | Should the load balancer send the next request to *this* target? |
| **Mechanism** | HTTP GET from the ALB's own ENIs, **inside the VPC**, to the target's private address |
| **On failure** | The target is deregistered from rotation. Nothing is terminated. Nothing alarms unless you built an alarm |
| **Blind to** | Anything the ALB cannot reach — including the entire internet-facing question. It never leaves your VPC |

That last row is why the NAT gateway trap in [Part 1.3](#13-the-nat-gateway-trap)
is invisible: the health check never traverses NAT, so it cannot tell you that
NAT is gone.

Configured by `target_group_health_check` in `terraform.tfvars`. The four
numbers multiply into your detection time:

```
time to mark unhealthy = interval × unhealthy_threshold
```

With the shipped 30 × 2 that is 60 seconds. Tighten to 10 × 2 and you detect
in 20 — at the cost of more health check traffic and more sensitivity to a
slow response, which is how a garbage-collection pause becomes a
deregistration.

> **The asymmetry is deliberate and correct.** `unhealthy_threshold` should be
> LOW (fail fast; the cost of a false positive is one instance out of
> rotation). `healthy_threshold` should be HIGHER (recover slow; the cost of a
> false negative is traffic sent to something that is not ready). Stacks that
> set both to 2 are usually not thinking about either. This one ships 2 and 3.

### 4.2 The EC2 status check

| | |
| --- | --- |
| **Question** | Is the hypervisor alive, and is the instance's OS reachable on the network? |
| **On failure** | The ASG replaces the instance — this one is **always** honoured |
| **Blind to** | Literally everything about your application |

A process that has deadlocked, a JVM in permanent full GC, a container that
exited leaving the instance up, an application returning 500 to every request:
**all healthy. Forever.**

### 4.3 The Route 53 health check

| | |
| --- | --- |
| **Question** | Can the outside world reach this endpoint? |
| **Mechanism** | HTTP from 15+ checker locations around the world, from **outside** your VPC |
| **On failure** | DNS stops returning this record, if it participates in a failover or weighted policy |
| **Blind to** | *Which* target is broken. It only sees the aggregate |
| **Costs** | ~$0.50/month, **plus ~$1.00/month for each optional feature** |

The only one of the three that is billed, and the only one that answers the
question your users are actually asking.

The optional features are HTTPS, string matching, fast interval (10 s), and
latency measurement, at roughly a dollar a month each. **String matching is
worth the dollar on anything real**: without it, a health check passes as long
as the endpoint returns 200 — including the 200 your load balancer returns
from a maintenance page, and including the 200 an application returns while
every downstream call is failing.

The detection arithmetic is `failure_threshold × request_interval`. With this
stack's 3 × 30 that is 90 seconds before Route 53 will even *consider* the
endpoint unhealthy. Add the DNS TTL. **You are at 150 seconds before anything
else has happened.**

### 4.4 The most expensive missing line in AWS

```hcl
health_check_type = "ELB"
```

The AWS default is `"EC2"`. This is check **DR-003**, and it is the single
most common finding this auditor produces against real accounts.

Here is the failure that actually happens, step by step:

1. Your application process deadlocks. The instance is running. The OS
   answers. EC2 status checks pass.
2. The target group health check fails. **Correctly.**
3. The ALB deregisters that target. **Correctly.**
4. Traffic goes to the healthy instances. **The service is fine.**
5. The ASG does nothing, because EC2 says the instance is alive.
6. You now pay for an instance that serves zero requests, **indefinitely**.
7. Your effective capacity is silently N-1.
8. **Nothing alarms, because nothing is down.**

This state survives for months. It is discovered during the next incident,
when the spare capacity that was supposed to absorb an AZ failure turns out to
have been dead since March.

The line costs nothing. And there is a corollary that makes it a two-line fix:

> Turning on `health_check_type = "ELB"` **without** an adequate
> `health_check_grace_period` converts a silent capacity leak into a loud boot
> loop. Both lines, or neither.

### 4.5 The grace period, and the boot loop

`asg_health_check_grace_period` is the number of seconds after launch before
health checks count against an instance. This is check **DR-004**.

Set it shorter than your application takes to become ready and you get:
instance launches → health check fails because the app is still starting → ASG
terminates it as unhealthy → ASG launches a replacement → replacement fails
identically → forever. The activity history shows a tidy launch/terminate loop
that **looks like an AZ problem and is not.**

The part that makes this genuinely dangerous:

> **It is self-concealing during a real incident.** Under load your application
> boots *slower* — cold caches, contended disks, a database that is already
> struggling. So a grace period that was adequate on a quiet Tuesday is
> inadequate on the one day it matters, and the ASG responds to your outage by
> killing every instance that tries to help.

Measure it. Time a boot to the first successful health check, then double it.
This stack's application is trivial and ready in about sixty seconds; the
default is 300, because doubling is not enough of a margin when the number is
a guess.

Note that DR-004's severity **depends on DR-003's subject**: HIGH when the
group honours ELB health checks (a real boot loop), MEDIUM when it does not
(harmless today, a boot loop the moment somebody correctly fixes DR-003).

### 4.6 What a health check should check

This stack's `/health` returns 200 unconditionally, which is honest for a lab
serving static text and is the **wrong pattern for anything real**.

The rule worth carrying away:

> A health check should verify the dependencies **this instance needs to serve
> a request**, and nothing else.

Check your database connection, yes. **Do not check a downstream service that
is not on the critical path** — you have just given that service the ability to
take your entire fleet out of rotation simultaneously, which is a correlated
failure you invented yourself. That specific mistake has caused more than one
large public outage: a non-critical dependency degrades, every instance's
health check fails at once, the ASG terminates the entire fleet, and the
outage becomes total instead of partial.

And never make the health check expensive. It runs every `interval` seconds
against every target from every ALB node.

---

## Part 5 — The data tier

**Failover is a data problem, not a compute problem.**

Standing up compute in another AZ or another region takes minutes and is
almost entirely automatic. Everything in Part 4 is the easy part. The RTO goes
somewhere else: into reconciling state, into DNS caches, into connection
pools, into deciding whether writes that happened during the outage are
recoverable, and into the meeting where somebody has to say out loud whether
the data is trustworthy.

### 5.1 Multi-AZ RDS is not a read replica

Check **DR-005**. The misconception this exists to kill:

| Multi-AZ **is** | Multi-AZ **is not** |
| --- | --- |
| A synchronous standby in another AZ | A read replica |
| An automatic DNS failover in 60–120 s | Anything you can query |
| RPO of **zero** for an AZ failure | An improvement to read throughput |
| A hot spare costing exactly what it spares | An improvement to write throughput or latency |

Teams enable it expecting read scaling and are then genuinely puzzled that
nothing got faster. Read replicas are a **different feature**: asynchronous,
promotable manually, billed separately. You can have both. Most people who
need one need both.

Two more things worth knowing before you claim zero downtime:

**A Multi-AZ failover drops every connection and rolls back every in-flight
transaction.** An application with a connection pool and no retry logic
experiences it as an outage whose length is set by the pool's TCP timeout —
which is very often *longer* than the 60–120 second failover it was supposed to
hide. "We have Multi-AZ so we have no downtime" is false in a way that only
shows up during the failover.

**The DB subnet group must span at least two AZs even for a single-AZ
instance.** AWS makes you pre-declare where a standby *could* go. Which means
the only thing standing between single-AZ and Multi-AZ is one boolean and a
doubled bill — and that is the whole of check DR-005.

`create_rds` defaults to **false** in this stack, because RDS costs ~$12.41 a
month single-AZ and takes 15–25 minutes to create. When you set it true, both
DR-005 and DR-006 fire immediately with the shipped defaults, for 35 points.
They are **silent by situation**, not by design — see the finding contract.

### 5.2 DynamoDB: PITR, global tables, and last-writer-wins

DynamoDB is this day's primary data store for three reasons, in order:

1. **It is the only store in AWS where you can watch your own RPO on a
   graph.** A global table publishes `ReplicationLatency` to CloudWatch.
2. Three genuinely distinct RPO postures, switchable in minutes: none (~your
   last manual backup), PITR (~5 minutes), global table (~sub-second).
3. On-demand billing means an idle lab table costs cents. RDS has a $12/month
   floor before it stores a single row.

**Point-in-time recovery** (check DR-007) is ~$0.20 per GB-month and gives you
continuous backup with restore to any second in the last 35 days. On most
tables it is the cheapest RPO improvement available anywhere in AWS.

The part that catches people, again, because it belongs in your RTO rather
than your RPO: **a PITR restore creates a new table.** In-place restore does
not exist. Time it in lab step 8 and notice how much of the elapsed time is
after the restore finished.

**Global tables** replicate bidirectionally, multi-actively, with
**last-writer-wins and no conflict resolution beyond a timestamp.** Read that
again:

> If your application writes to both regions during a split brain, one of those
> writes is **discarded silently**, and the loser is whichever clock was
> behind.

That is a correctness property of your application, not of DynamoDB. It is
also the reason `enable_dynamodb_global_table` defaults to false in this
stack: it is the day's most instructive option and its most consequential one.
Turn it on for lab step 6, watch the latency metric, and then decide.

One operational detail: **streams must be enabled with
`NEW_AND_OLD_IMAGES` before you can add a replica**, and changing stream
configuration on a table that already has replicas is a conversation with
support. This stack enables streams unconditionally so that turning on global
tables later is a two-minute change rather than a table replacement.

### 5.3 S3 replication: asynchronous means it is an RPO

Three properties worth internalising, all of which produce real incidents:

**Versioning is mandatory on both source and destination.** Not a
recommendation — an API constraint. And it has a cost consequence: with
versioning on, deletes do not delete. Every overwrite keeps the old version
and bills for it, in **both** regions, until a lifecycle rule removes it. A
replicated bucket with no noncurrent-version lifecycle rule is the most
reliable way to grow a storage bill in a region nobody looks at. This stack
applies the rule to **both** buckets, deliberately, because a lifecycle rule
is per bucket and the DR bucket is the one that gets forgotten.

**Replication is not retroactive.** Turning it on replicates objects created
*after* that moment. Everything already in the bucket stays where it is until
you run S3 Batch Replication explicitly. Teams discover this during the
failover, when the DR bucket turns out to contain three weeks of data and the
primary contains three years.

**Deletes do not replicate by default.** Delete markers are excluded unless
you opt in. That is usually the *safe* default — an accidental mass delete in
the primary does not propagate — but it means your two buckets diverge
permanently and **neither is a mirror of the other**. Decide which behaviour
you want and write down why, because the person doing the recovery will assume
the other one.

That last point is the shape of the constraint that produces check **DR-012**:
a bucket created for a DR requirement, named `dr-archive`, with no replication
rule, because the first attempt failed with a versioning error on a Friday and
nobody came back to it. The bucket exists. It appears in the DR document. It
contains nothing that will ever leave the region.

### 5.4 The three things that go wrong with backups

In the order they are discovered, which is the reverse of the order they
matter:

| | What goes wrong | When you find out | How common |
| --- | --- | --- | --- |
| 1 | There is no backup | Immediately | Rare |
| 2 | The backup is too old | At restore time | Common |
| 3 | **The backup cannot be restored** | **At the worst possible moment** | **Common, and invisible** |

The third is the interesting one, and it has boring causes:

- the KMS key the snapshot was encrypted with has been rotated or deleted
- the AMI the recovery point references no longer exists
- the instance type is not available in the DR region
- the database engine version has been deprecated and cannot be launched
- the IAM role has **backup** permissions and not **restore** permissions
- the restore works, and takes nine hours

**None of these is visible in a backup report. All of them are obvious after
one restore.** And that last one is not a failure at all — it is an RTO,
discovered rather than declared.

This is check **DR-010**, it is CRITICAL, and it is reported as a single
account-level finding rather than per resource. That is deliberate: it is a
statement about the **organisation**, not about a vault, and attaching it to a
resource id invites somebody to close it by deleting the resource.

> **A backup nobody has restored is a file.**

Note the interaction with DR-008 that the tests assert in both directions: a
vault full of fresh, correctly retained, cross-region-copied recovery points
that has never had a single restore performed against it scores **0 on DR-008
and 25 on DR-010**. That is the normal state of most organisations.

### 5.5 Vault lock, and the argument that presence selects

Check **DR-009**. Vault lock makes recovery points immutable: nobody,
including the account root, can shorten retention or delete a recovery point
before it expires.

It is the control that survives a **compromised administrator**, which is the
threat model backups are actually for once you take ransomware seriously. An
attacker who has your credentials and wants your recovery options limited does
not need a zero-day; they need `delete-recovery-point`.

Two modes, and the difference is not a detail:

| | Removable by | Protects against |
| --- | --- | --- |
| **Governance** | anyone with `backup:DeleteBackupVaultLockConfiguration` | accident, process failure, a cost-saving script |
| **Compliance** | **nobody. Ever. Not AWS Support.** | an attacker with admin |

And here is the thing that makes this genuinely dangerous:

> **The mode is selected by the PRESENCE of an argument, not by a value.**
>
> `changeable_for_days` absent → governance. Present → compliance.
>
> There is no `mode = "governance"` line to get wrong. There is a line whose
> mere existence changes everything, and **adding it looks like adding
> detail.**

In compliance mode, after the cooling-off period, the lock is permanent for
the life of the vault, and the vault cannot be deleted while it holds recovery
points. Set 365-day retention in compliance mode on a lab account and you have
bought a year of storage with no undo.

**This stack therefore never sets `changeable_for_days` and exposes no
variable that could.** Governance only. If you want compliance mode in
production, write it yourself, deliberately, with a colleague reading the
plan, in a repository where somebody reviews it.

DR-009 fires **once per vault, including the DR vault**, and is deliberately
not deduplicated up to the plan. A locked primary vault beside an unlocked DR
copy vault is a real and common asymmetry, and it is exactly backwards: **the
DR vault is the one an attacker who has already compromised the primary
account will reach for**, because it is the copy that survives everything they
just did.

---

## Part 6 — DNS failover, and why TTL is spent RTO

### 6.1 The mechanism

Route 53 failover routing uses two record sets with the same name and
different `SetIdentifier`s, one marked PRIMARY and one SECONDARY, with a
health check attached to the primary. When the health check fails, Route 53
stops returning the primary and starts returning the secondary.

Check **DR-014** exists because of one specific property:

> **Route 53 treats a PRIMARY failover record with no health check as
> permanently healthy. It never fails over.**

You have built the mechanism, wired the DNS, drawn it in the diagram, and
disconnected the trigger. It is a configuration that passes every review that
looks for the *existence* of things: the record set is there, its type is
PRIMARY, there is a SECONDARY. Only a review that asks "what makes this
switch" catches it, and only a test proves it.

There is a legitimate alternative the check exempts: an **alias record with
`evaluate_target_health = true`**, where Route 53 uses the target's own health
instead. Alias-plus-evaluate-target-health works for ALBs and does not exist
for a plain A record, and the two look similar in the console. If you are
reading a real account, that exemption is the thing to check by hand.

> This stack does **not** create a hosted zone. Creating one for a domain you
> do not own produces a zone that resolves for nobody, bills $0.50/month, and
> survives teardown because people do not think of zones as resources. Set
> `hosted_zone_id` and `dns_record_name` if you own a domain; otherwise the
> health check is still created and DR-014 is silent by situation.

### 6.2 TTL is a request, not a guarantee

When you fail over, a resolver that fetched your record one second earlier
keeps serving the old address for the full TTL. **Not on average — as a worst
case, for some fraction of your users, no matter how fast everything else
was.** A 300-second TTL means five minutes of your recovery budget is gone
before anything you did has any effect on those clients.

So why not set it to 1? Because TTL is also your DNS bill (~$0.40 per million
standard queries, and a TTL of 1 multiplies query volume by roughly 300
against a TTL of 300) and a few milliseconds in front of every cold
connection.

And then the caveat that ruins the arithmetic entirely:

> **Resolvers clamp minimums. Corporate resolvers cache far longer than you
> asked. Java, historically and by default, cached DNS resolutions for the
> life of the JVM.**

That last one is the origin of "we failed over successfully but the
application servers kept connecting to the old database", a story every senior
engineer has a version of.

The design consequence: **DNS failover is a coarse, slow, best-effort
mechanism.** It is fine for shifting human traffic between regions. It is a
poor mechanism for anything that needs to be fast or exact — which is why
in-AZ failover uses load balancer target health and not DNS.

This stack takes a position rather than only teaching one. `route53_ttl`
carries a **cross-variable validation** refusing any value above a quarter of
`rto_target_minutes`:

```hcl
validation {
  condition     = var.route53_ttl <= (var.rto_target_minutes * 60) / 4
  error_message = "route53_ttl consumes more than a quarter of rto_target_minutes..."
}
```

That is **enforcement instead of detection**, and it is deliberately *not* a
check in `dr_audit.py`. An audit tells you about a TTL problem after you have
shipped it; a validation refuses to ship it. When you can do either, do this
one — an auditor finding is a ticket and a plan failure is a conversation, and
the conversation is cheaper.

> One asymmetry worth noticing: an **alias** record to an ALB has an
> AWS-managed TTL of 60 seconds that you cannot set, so `route53_ttl` applies
> only to the non-alias secondary. Aliases quietly give you a reasonable TTL,
> and the moment you use a plain A or CNAME for a failover target you own the
> TTL problem again.

### 6.3 The inverted health check, and the trap in it

The standard way to run a DR drill without breaking anything is to **invert
the health check**: `aws route53 update-health-check --health-check-id <id>
--inverted`. Route 53 now reports the primary unhealthy, DNS fails over, and
you have exercised the whole path with one reversible API call.

This stack's recovery workflow does exactly that as its failover action. And
it carries a trap that must be in your runbook:

> **While the health check is inverted, Route 53 reports the primary unhealthy
> REGARDLESS OF WHETHER IT IS.** If the primary recovers during the incident,
> nothing tells you — the signal you would use to notice has been deliberately
> disabled by your own failover.

Every drill that uses this technique must have "un-invert the health check" as
an explicit step **with an owner**, and every one that does not eventually
leaves it inverted for a week.

---

## Part 7 — The recovery workflow

Day 07's argument, carried forward and made heavier:

> An automated response is a decision you are making now, to be executed later,
> by nobody, on evidence that might be wrong.

**Day 07's automation contained a threat. This one declares a region dead.**

And the evidence it acts on is health checks, which lie during exactly the
network conditions that make you want to fail over. An automated regional
failover triggered by a transient partition is how you get split brain, and
split brain in a last-writer-wins data store is **silent, permanent data
loss.**

### 7.1 What must never be automated

| Reversible by doing nothing | Not reversible by doing nothing |
| --- | --- |
| Replacing an unhealthy instance | Declaring a region non-authoritative |
| Scaling out | Promoting a replica |
| Taking a snapshot | Repointing writes |
| Failing a health check on purpose | Failing back |

Everything in the left column can be automated without a gate. The ASG already
does the first one; if your `health_check_type` is `"ELB"`, the workflow's
in-AZ recovery step is nearly redundant, and the honest version of this
workflow says so.

Everything in the right column needs a person. Not because automation is
untrustworthy, but because **the cost of a false positive and the cost of a
false negative are wildly asymmetric**: a false negative is a few more minutes
of a partial outage, and a false positive is a divergent dataset.

### 7.2 Detect, decide, approve, execute, verify, notify

```
CheckKillSwitch ──► KillSwitchGate ──► Assess ──► ScopeGate
                          │                           │
                       Aborted            ┌───────────┼───────────┐
                                          │           │           │
                                     NoAction    RecoverInAz  ApprovalGate
                                                      │            │
                                                      │      RequestApproval
                                                      │       (waitForTaskToken,
                                                      │        30 min timeout)
                                                      │            │
                                                      │      ExecuteFailover
                                                      │            │
                                                      │          Verify
                                                      │            │
                                                      └──────► VerifyGate
                                                                   │
                                                    NotifySuccess ─┴─ VerificationFailed
```

Twenty-one states, four of which end in `Fail`. **The interesting states are
the four that end in Fail**, and the console diagram — which is a picture of
the happy path — will not draw your attention to them.

**Why Step Functions and not a Lambda with a try/except?** Four properties,
and the first is the one that matters today:

1. **The execution history is a timestamped, per-step audit trail.** After the
   drill you read exactly how long each phase took. **That is the RTO
   measurement.** It is a free side effect of the structure.
2. The approval gate is a first-class state (`waitForTaskToken`) rather than a
   Lambda blocking for thirty minutes against a fifteen-minute limit.
3. **A failed verification fails the execution.** A Lambda would return 200
   with a field nobody reads.
4. Timeouts and retries are declared next to the step they protect.

The `Assess` step classifies damage as `none`, `in_az`, `regional`, or
`unknown`, and the fourth branch is the one worth defending. When the
assessment cannot tell an outage from an empty stack, **the workflow stops and
asks for a person.** An automation that treats "I could not tell" as "probably
fine" is the same automation that treats it as "probably a disaster" on a
different Tuesday.

The classification is also deliberately conservative: `regional` is only
returned when essentially nothing is healthy. **The cost of a false positive
here is split brain and the cost of a false negative is a few more minutes of
a partial outage. Those are not symmetric.**

Note also that `ExecuteFailover` has **no `Retry` block**. Retrying an action
that may have half succeeded is how you get two failovers, or a failover
racing its own rollback. If that step fails, a human reads the execution
history.

### 7.3 The kill switch

Day 07's pattern, unchanged, and it matters more here because the action it
stops is larger. An SSM parameter, read as the **first state of every
execution**:

```bash
aws ssm put-parameter --name /cbc-day08/<suffix>/recovery-enabled \
  --value disabled --overwrite --profile bootcamp --region us-east-1
```

The properties that make it useful are the ones that make it unfashionable:
it is **not** in Terraform's state after you change it by hand, it can be
flipped **from a phone** by somebody who has never run `terraform init`, and it
takes effect on the next execution with no deploy. An automation whose only
brake requires a pipeline is an automation with no brake at 03:00 on a Sunday.

It **fails safe in one direction only**, and the direction is a design choice:
if the parameter is missing, unreadable, or holds anything other than
`enabled`, the workflow aborts. **An automation that cannot confirm it is
allowed to run does not run.** The opposite default — proceed unless
explicitly stopped — is what turns a permissions mistake into an unrequested
regional failover.

The Terraform carries `lifecycle { ignore_changes = [value] }` on the
parameter. If somebody pulled the brake during an incident, the next
`terraform apply` must not helpfully set it back.

And the recovery Lambda's IAM policy scopes `ssm:PutParameter` to the
active-region parameter **only**. It cannot write the kill switch. **An
automation that can re-enable its own brake does not have a brake**, and that
is not theoretical — it is the first thing a badly-written "self-healing"
wrapper does.

### 7.4 The assessment is made from inside the region under suspicion

This is the largest architectural gap in this lab, and it is stated here
rather than hidden.

Every signal the `assess` step reads — `describe_target_health`,
`describe_auto_scaling_groups` — is an API call to a **regional endpoint in
the region that might be failing**. If the regional control plane is degraded,
which is what a lot of real "region down" events actually are, that call fails
or returns stale data. **The assessment that decides whether to fail over is
made from evidence produced by the thing under suspicion.**

It is not fixable from inside the primary region. It is fixable by **running
the workflow from the DR region**, which is a design most teams arrive at
after their first real incident and which costs a second deployment of
everything. The `assess` docstring in `lambda/recovery.py` says so, and every
assessment result it returns carries a `caveat` field saying so too.

### 7.5 Verify, or you have not recovered

A workflow that ends at "executed" reports success when **the API call
succeeded and the outcome did not.** Those diverge more often than anybody
expects.

The `Verify` step checks **outcomes rather than re-reading its own intent**:
the active-region parameter's current value, the health check's actual
inverted state, the DR table's status. If a step failed silently, this is
where it stops being silent.

It is also honest about what it cannot verify, and the list is in the
function's return value:

- that any client actually resolved to the DR region
- that in-flight writes to the primary were captured
- that the data in the DR region is **complete** rather than merely present

### 7.6 Failback is not automated, and here is what that means

> **THERE IS NO AUTOMATED FAILBACK IN THIS REPO, AND THERE IS NOT ONE IN MOST
> REAL SYSTEMS EITHER.**

That is not an omission this lab ran out of time for. It is the shape of the
problem:

> **Failing over is a decision about ROUTING. Failing back is a decision about
> DATA**, and it can only be made by something that knows what your writes
> mean.

`lambda/recovery.py` has a `failback` action. It is **manual-invoke only**, it
is not in the state machine, and it reverses the two routing changes in about
ten seconds. Its docstring is a five-item list of what it **cannot** do, and
that list is the actual content of the failback problem:

1. **Reconcile the writes that landed in the DR region.** Everything written
   while you were failed over exists only there — unless your replication is
   bidirectional, in which case you have a last-writer-wins merge you did not
   review. Somebody has to decide, per data set, which version wins. There is
   no general answer.
2. **Decide whether the primary's data is stale or wrong.** Stale is missing
   recent writes. Wrong is having accepted writes during a partition that the
   DR region also accepted differently. The second is silent and permanent.
3. **Drain and repoint connections.** Pools, long-lived gRPC channels, message
   consumers with in-flight leases. Each fails back on its own schedule and
   some do not fail back at all without a restart.
4. **Verify the primary is actually better.** The health check you inverted has
   been reporting the primary unhealthy the entire time, which means **you have
   had no signal about the primary since the moment you failed over**. Un-invert
   it and *wait* for real health data before moving traffic back. Failing back
   into a still-broken primary is the classic second outage, and it is worse
   than the first because you have now proved to everyone that failover does
   not help.
5. **Scale the DR environment back down.** It was scaled up for the incident.
   It still is. The most expensive item on the list and the least likely to be
   noticed.

Notice the ratio in `lambda/chaos.py` too: `mode_isolate_az` is one API call.
`mode_restore` is the longest function in the file. **That ratio is not an
accident of this repo. It is the shape of the whole problem** — the outbound
path is one call and the return path has to reconstruct state the outbound
path destroyed.

Rehearse failback, timed, in the same drill as the failover. **Every DR
exercise that ends at "we failed over successfully" has tested half a procedure
and measured a third of an RTO.**


---

## Part 8 — Chaos: the only thing that makes a number real

Everything before this section is a hypothesis. This is the experiment.

> **The only thing that makes an RTO real is breaking something and timing the
> recovery.** A DR plan validated by reading it is a document review.

### 8.1 Why a dry run first

`lambda/chaos.py` defaults to dry run, from `chaos_dry_run`. Day 07 argued
that anything irreversible needs a dry run and a human gate. That argument
applies with more force here, because **Day 07's automation contained a threat
and this one causes an outage.**

The dry run is not training wheels. It is how you verify that the blast radius
is what you think it is **before the blast**, and every chaos exercise in a
real organisation starts with one.

Read the dry-run output. Every time. The plan it prints includes an `expected`
field describing what should happen and how long it should take, and comparing
that against what actually happens is the entire exercise.

### 8.2 What this lab's chaos can and cannot simulate

| Mode | What it does | What it proves |
| --- | --- | --- |
| `terminate_instance` | Terminates one InService ASG instance | The plain replacement path: ASG notices capacity below desired, launches a replacement |
| `mark_unhealthy` | `autoscaling:SetInstanceHealth` Unhealthy | The safe analogue of "the application is broken but the instance is fine" — the failure EC2 health checks cannot see |
| `isolate_az` | Associates a **deny-all NACL** with one AZ's private subnet | Instances keep running, keep passing EC2 status checks, and become unreachable |
| `restore` | Reassociates the default NACL | Failback, in miniature |

**NACLs rather than security groups for the isolation, and the reason is worth
knowing.** Security groups are **stateful** and instance-attached, so isolating
with one means touching every instance and the return traffic of established
connections still flows. NACLs are **stateless** and subnet-attached, so a
single association call takes out an entire subnet, both directions,
immediately. That is what makes it a passable AZ analogue — and it is also why
NACLs are a genuinely dangerous tool in an automated response.

Now the honest limits, because a chaos tool that oversells itself teaches
false confidence:

> **`isolate_az` is not an AZ failure.** A real AZ failure takes the NAT
> gateway, the RDS standby, the EBS control plane for that zone, and every
> cross-AZ dependency you did not know you had — **simultaneously**, while the
> AWS console is also degraded. This takes the network.

The gap between the two is worth naming out loud in the debrief. **AWS Fault
Injection Service** has a genuine
`aws:ec2:asg-insufficient-instance-capacity-error` and an
AZ-availability-power-interruption action that gets much closer. It costs
~$0.10 per action-minute and it is the right next step after this lab.

Also notice the chaos Lambda's IAM policy. `ec2:TerminateInstances` is scoped
by a **tag condition** on `Project` and `Day`. A chaos tool is an outage
generator with an IAM role: the blast radius of a bug in `chaos.py` is exactly
that policy and not one action more. **The failure mode of a chaos tool with
excessive permissions is indistinguishable from an attacker.**

### 8.3 Running the drill

The full sequence is `terraform output next_steps`, steps 0–11. The core of it:

```bash
# 1. A curl loop in one terminal, so you can see the outage happen
while true; do date -u +%T; curl -s -m 2 -o /dev/null -w '%{http_code}\n' \
  http://$(terraform output -raw alb_dns_name)/; sleep 1; done

# 2. Dry run FIRST, and read the plan
aws lambda invoke --function-name $(terraform output -raw chaos_function_name) \
  --payload '{"mode":"terminate_instance","dry_run":true}' \
  --cli-binary-format raw-in-base64-out /dev/stdout

# 3. For real, with a stopwatch
aws lambda invoke --function-name $(terraform output -raw chaos_function_name) \
  --payload '{"mode":"terminate_instance","dry_run":false}' \
  --cli-binary-format raw-in-base64-out /dev/stdout

# 4. Stop the clock when target health returns to full
aws elbv2 describe-target-health --target-group-arn <arn> \
  --query 'TargetHealthDescriptions[].[Target.Id,TargetHealth.State]' --output table
```

Then compare against your prediction from step 2. Then do `isolate_az`, then
`restore`, and **notice how much longer the second one takes to think about
than the first.**

Write the numbers into `rto-measurements.md` and commit it. It is deliberately
**not** gitignored — it is the single most valuable artefact this day produces.
And date it, because **an RTO without a date is an RTO from an architecture
that no longer exists.**

---

## Part 9 — The mistakes people actually make

Fourteen, roughly in order of how often they show up in real accounts.

**1. Calling an architecture multi-AZ when one component is not.** The NAT
gateway is the classic; a single-AZ RDS behind a multi-AZ app tier is the
runner-up. The system's availability is the availability of its least
available component on the critical path, and diagrams do not show critical
paths.

**2. Leaving `health_check_type` at `EC2`.** Silent N-1 capacity for months.
See [4.4](#44-the-most-expensive-missing-line-in-aws).

**3. Fixing #2 without setting a grace period.** Silent capacity leak becomes
loud boot loop. The two lines go together.

**4. Believing Multi-AZ RDS is a read replica.** Enabling it, doubling the
bill, and being confused that read latency did not change.

**5. Sizing for the happy path.** Two instances across two AZs is redundancy
until one AZ fails and the survivor is at 200% of its design load. See
[1.1](#11-what-an-availability-zone-actually-is).

**6. Never restoring a backup.** The most common finding in this entire repo's
subject matter, and the one with the largest gap between perceived and actual
posture. See [5.4](#54-the-three-things-that-go-wrong-with-backups).

**7. A backup vault in the region that just failed.** The copy rule is a
*separate* decision from the backup rule, and only the backup rule is required
to make a plan valid. A plan with no copy action is complete, correct, green
in the console, and regional.

**8. Backup permissions without restore permissions.** The IAM role can take
backups all year and fails at the one moment it is used, with an AccessDenied
that somebody then has to fix under pressure, in an account where the person
who can grant IAM may also be unavailable. Grant restore now; test it now.

**9. Confusing a replica with a backup.** Replication is faithful. It
replicates your bad migration, your truncating bug, and your ransomware
encryption, in under a second, to every region. **Replicas protect against
losing infrastructure. Backups protect against losing data.** You need both,
and only one of them has a version history.

**10. Turning versioning off to save money.** Instead of adding a lifecycle
rule to expire noncurrent versions. This also silently makes replication
impossible, which is discovered months later.

**11. A DNS TTL of 300 in a failover record.** Five minutes of the recovery
budget, spent before anything you do takes effect on some clients. See
[6.2](#62-ttl-is-a-request-not-a-guarantee).

**12. A failover record with no health check.** Route 53 treats the primary as
permanently healthy. The mechanism exists; the trigger is disconnected.

**13. Automating a regional failover with no gate.** Health checks fail during
partitions, bad deploys and expired certificates as readily as during regional
outages, and an ungated workflow cannot tell those apart. This is check DR-015
and it is what the naive state machine exists to demonstrate.

**14. Ending the exercise at "we failed over successfully".** Half a procedure,
a third of an RTO, and a DR environment still running at production capacity.

---

## Part 10 — Cost

**This is the first day in the repo where the correct architecture is genuinely
expensive, and where "do not do this" is sometimes the right answer.**

On Days 01–07 the secure option and the cheap option were usually the same
option: encryption is free, least privilege is free, log file validation is
free. Here they are not. Multi-AZ RDS costs exactly twice as much as
single-AZ. A warm standby is a second environment billing continuously to
serve zero traffic. Cross-region replication is billed per GB transferred
**and** per GB stored, in both regions, forever.

### 10.1 The worked example

With the shipped defaults — 2 AZs, `single` NAT, 2 × t3.micro, no RDS,
insecure examples on — `terraform output cost_breakdown` gives:

| Line | Monthly | Note |
| --- | --- | --- |
| NAT gateway ×1 | $32.85 | $0.045/hour. **Single-AZ dependency — DR-002** |
| NAT public IPv4 | $3.65 | $0.005/hour, billed since Feb 2024 |
| ALB | $16.43 | $0.0225/hour, **billed for existing regardless of traffic** |
| ALB public IPv4 (2 AZs) | $7.30 | one node, one address, per subnet |
| ALB LCU | ~$1.00 | *estimate* |
| EC2 ×2 t3.micro | $15.18 | free tier covers **one** instance, so 2 puts you over |
| EBS 2 × 8 GiB gp3 | $1.28 | |
| Detailed monitoring ×2 | $4.20 | 1-minute metrics instead of 5-minute |
| Route 53 health check | $0.50 | +$1.00/month **each** for HTTPS, string match, fast interval, latency |
| Insecure examples | $0.13 | 1 GiB volume + 1 GiB snapshot; the legacy ASG runs at 0 |
| Data (S3, DynamoDB) | ~$0.50 | *estimate* |
| **Total** | **~$83** | plus usage-based lines below |

Usage-based and therefore not countable from a plan: NAT processing
(~$0.045/GB), ALB LCUs beyond the minimum, DynamoDB throughput and PITR, S3
storage in **both** regions plus inter-region transfer at ~$0.02/GB plus
destination PUTs at ~$0.005/1,000, and backup storage in both regions.

**Two lines deserve attention because neither scales down when you stop using
them.** The NAT gateway and the ALB bill hourly for *existing*. A Day 08 stack
left idle for a month is the most expensive thing in this repo.

The February 2024 public IPv4 charge is worth internalising too: it quietly
made every multi-AZ load balancer $3.65/month more expensive **per AZ**, and
made "add a third AZ" a slightly larger decision than it used to be.

### 10.2 The three silent-growth traps

`terraform output silent_cost_growth` prints these with commands. None appears
in a `terraform plan`.

**1. Snapshots and AMIs nobody deletes.** Snapshots bill per GiB-month,
indefinitely, and are the most durable artefact most accounts produce: they
survive `terraform destroy` of the volume they came from, they survive the
instance, they survive the person who took them. And **deregistering an AMI
does not delete the snapshots behind it** — that is a separate operation almost
nobody performs.

**2. Cross-region replicas in a region nobody looks at.** The DR-specific one.
Your dashboards are scoped to your primary region. Your budget alerts are
account-wide, but your *investigation* of a budget alert starts in the region
you work in. An S3 replica with versioning on and no lifecycle rule, or a
DynamoDB replica with provisioned capacity, grows in the DR region for months
without anybody's dashboard changing.

**3. A warm standby that was scaled up for a test and never scaled back.** The
most expensive item and the least visible, because the resources are correct,
tagged, and doing exactly what they were told. **Put the scale-down in the
test, not after it.**

### 10.3 When "do not do this" is the right answer

Some workloads should not have a DR region. Saying so is a professional
position, not a cop-out, and the argument goes like this:

- A regional failure of the kind multi-region protects against happens to a
  given AWS region roughly once every few years.
- Multi-region roughly doubles your infrastructure bill and considerably more
  than doubles your engineering burden — two environments to deploy to, two
  sets of config to drift, a conflict story for every data model.
- An untested DR region has a real RTO of "however long it takes to discover
  what is broken in it", which is frequently longer than rebuilding from
  infrastructure-as-code.
- **The same money spent on testing your single-region recovery reliably buys
  a larger reduction in expected downtime.**

Write the argument down either way. A DR posture chosen deliberately and
documented is defensible at any level of investment. One arrived at by
default is not, whichever direction the default went.

---

## Part 11 — Auditing recovery posture

### 11.1 What the tool checks

`lab/python/dr_audit.py` runs sixteen checks across nine AWS services and
**two regions**. Same shape as Days 03–07: one `Finding` dataclass, the same
severity weights, `--format table|json|csv`, `--fail-on` for CI.

```bash
cd lab/python
pip install -r requirements.txt
python3 dr_audit.py --profile bootcamp --region us-east-1 --dr-region us-west-2 \
  --prefix cbc-day08
```

Three things about this tool are new on Day 08:

**`--dr-region` is not optional decoration.** A single-region audit of a
multi-region DR posture cannot see whether the copy vault exists, whether it
holds anything, or whether it is in the region you think it is. Every
DR-region object is tagged with its own region in the collected stack, and
every finding carries the region of the **resource** rather than the region you
invoked with.

**Ages are measured in minutes, not days.** A 23-hour-old recovery point must
not round to "1" and look fine when your stated RPO is 60 minutes.

**`--rpo-minutes` is the claim the audit measures against.** Set it to the
number in your DR document rather than the number you hope for. That
substitution is most of the exercise.

Three checks read **runtime** state — DR-008, DR-010, DR-016 — and their answer
depends on when you ran the tool. That is named in the JSON output as
`runtime_dependent_checks` so a consumer diffing two runs knows which checks
could legitimately change without anybody touching the account. **A dashboard
that alerts on the delta will page somebody hourly if it does not know that.**

### 11.2 Build it yourself

`lab/python/challenge/dr_audit_challenge.py` is generated from the reference:
identical imports, identical `Finding`, identical helpers, identical
renderers, identical collector, identical CLI. **The sixteen check bodies are
removed and their docstrings left in place, because the docstring is the
specification.**

```bash
cd lab/python
DR_AUDIT_MODULE=dr_audit_challenge PYTHONPATH=challenge \
  python3 -m unittest discover -s tests -v
```

47 tests, no AWS credentials required, roughly three hours of work in five
checkpoints. The header briefing covers the two things that cost people the
most time: **which checks are not independent** (six relationships, including
that DR-004's severity depends on DR-003's subject) and **the clock** (units,
and that "absent" is not "zero").

---

## The finding contract

```text
=============================================================================
DAY 08 FINDING CONTRACT — LOCKED AT CP2
=============================================================================
This block is reproduced identically in five places. Change one, change all
five: README.md, lab/README.md, lab/terraform/outputs.tf (finding_contract),
lab/python/dr_audit.py (module docstring), lab/python/tests/test_checks.py.

Weights are the repo-wide ones, identical to Days 03 through 07:
CRITICAL 25, HIGH 10, MEDIUM 4, LOW 1, INFO 0. Score is 100 minus the sum,
floored at 0. Grades: 90+ A, 75+ B, 60+ C, 40+ D, below that F.

Day 08 has NO LOW AND NO INFO CHECKS, and that is a decision rather than an
oversight. On this day every fault either costs you data or costs you time
during an outage. There is no informational gap in a recovery path — a thing
that does not matter when the region is on fire does not belong in an audit
whose whole subject is the hour the region is on fire.

STATIC STATE — after terraform apply with the shipped defaults
(create_insecure_examples = true, nat_gateway_strategy = "single",
create_rds = false, enable_vault_lock = false,
s3_replication_time_control = false, hosted_zone_id = ""), before any backup
job has run, before any restore, before any workflow execution.

  ID       SEVERITY   W   N  PTS  SOURCE RESOURCE
  -------  --------  --  --  ---  ------------------------------------------
  DR-001   CRITICAL  25   1   25  aws_autoscaling_group.single_az
  DR-002   HIGH      10   1   10  aws_nat_gateway.main - strategy "single"
  DR-003   HIGH      10   1   10  aws_autoscaling_group.single_az
  DR-004   MEDIUM     4   1    4  aws_autoscaling_group.single_az
  DR-005   CRITICAL  25   0    0  none - SILENT BY SITUATION, see below
  DR-006   HIGH      10   0    0  none - SILENT BY SITUATION, see below
  DR-007   MEDIUM     4   1    4  aws_dynamodb_table.no_pitr
  DR-008   HIGH      10   2   20  aws_backup_vault.main, aws_backup_vault.dr
  DR-009   MEDIUM     4   2    8  aws_backup_vault.main, aws_backup_vault.dr
  DR-010   CRITICAL  25   1   25  account-level singleton - no restore, ever
  DR-011   HIGH      10   0    0  none - SILENT BY DESIGN, see below
  DR-012   MEDIUM     4   1    4  aws_s3_bucket.unversioned
  DR-013   HIGH      10   1   10  aws_s3_bucket_replication_configuration.primary
  DR-014   HIGH      10   0    0  none - SILENT BY SITUATION, see below
  DR-015   CRITICAL  25   1   25  aws_sfn_state_machine.naive
  DR-016   CRITICAL  25   2   50  both Day 08 state machines - never executed
  -------  --------  --  --  ---  ------------------------------------------
  TOTALS                    15  195

  FIFTEEN findings from SIXTEEN checks. Four checks are silent here and they
  are silent for two different reasons, which is the most useful thing in this
  table: three because this particular stack cannot currently produce the
  fault (DR-005, DR-006, DR-014), and one because NO configuration of this
  stack can ever produce it (DR-011).

  Score: 100 - 195 = -95, floored to 0/100. Grade F.

  SEVERITY HISTOGRAM of the 16 checks: 5 CRITICAL, 7 HIGH, 4 MEDIUM,
  0 LOW, 0 INFO.

THE FOUR STATES

  STATE                                        FINDINGS  POINTS    SCORE  GRADE
  -------------------------------------------  --------  ------  -------  -----
  A  Static: after apply, nothing run yet            15     195    0/100      F
  B  Live: after lab steps 6a, 7 and 8 - one
     on-demand backup copied to DR, one
     workflow execution succeeded, one
     restore performed                               11     125    0/100      F
  C  Sixty-one minutes after B, WITH NOTHING
     CHANGED - the recovery points have aged
     past rpo_target_minutes                         13     145    0/100      F
  -------------------------------------------  --------  ------  -------  -----
  D  Reference build: create_insecure_examples
     = false, nat_gateway_strategy = "per_az",
     s3_replication_time_control = true,
     enable_vault_lock = true, plus a completed
     backup, a completed restore and one
     successful workflow execution                    0       0  100/100      A

  STATE C IS THE POINT OF THIS TABLE AND IT IS THE THESIS OF THE DAY.

  Between B and C, nobody deploys anything. No console click, no apply, no
  merge. Two findings appear because time passed and DR-008 measures the age
  of the newest recovery point against the RPO you declared.

  An audit that passes at 14:00 fails at 15:01 on an unchanged account.

  That is not a defect in the auditor. It is the correct behaviour, and it is
  the difference between a configuration audit and a recovery audit. RTO and
  RPO are not properties of a configuration. They are claims about a
  PROCEDURE, and a claim about a procedure decays continuously from the last
  time somebody ran it. A merge-time-only audit certifies the account as it
  was on the day somebody last changed it, and that is not the property a DR
  posture needs to have.

  With the shipped hourly backup schedule, DR-008 therefore SAWTOOTHS: silent
  for the minutes after each successful job, firing again as the recovery
  point ages past the 60-minute RPO. Two numbers that are one minute apart
  produce different audit results, and both are correct. If that is
  uncomfortable, the fix is not a looser check - it is a schedule that is
  actually faster than the RPO you claimed.

  Day 07's contract had the finding COUNT identical before and after the lab
  with a different SET. Day 08 does not repeat that trick, because forcing it
  here would have been dishonest: doing the work genuinely removes findings.
  What Day 08 has instead is a state that gets WORSE while you are asleep.

SILENT BY DESIGN — DR-011, a replication or backup copy target in the same
region as its source.

  No shipped default and no typo can produce this fault. The dr_region
  variable carries a cross-variable validation refusing dr_region ==
  aws_region; the S3 replica bucket is created under provider = aws.dr; the
  AWS Backup copy rule targets the DR vault or does not exist. There is no
  path through this Terraform that puts a DR copy in the primary region, so
  the plan refuses to produce one.

  It is not a hypothetical fault. S3 Same-Region Replication is a real and
  legitimate feature - compliance separation, log aggregation, cross-account
  isolation - and an AWS Backup copy rule will happily target a vault in the
  source region. Both get pressed into service as "DR" by people who were
  solving a different problem last week, and both produce a second copy inside
  the same blast radius.

  A check that stays silent because the stack cannot produce the fault is
  evidence that the auditor does not cry wolf.

SILENT BY SITUATION — DR-005, DR-006 and DR-014.

  DR-005 and DR-006 are the RDS checks. create_rds defaults to false, so there
  is no RDS instance to be single-AZ or to have one day of retention. The
  moment somebody sets create_rds = true with the shipped defaults, BOTH fire
  immediately, for 35 points, because rds_multi_az defaults to false and
  rds_backup_retention_days defaults to 1.

  DR-014 is the Route 53 failover-record check. The failover record sets
  require a hosted zone you own, hosted_zone_id defaults to empty, so there
  are no failover records to be missing a health check.

  NOTHING HAS TO CHANGE FOR ANY OF THESE TO STOP BEING TRUE, and in DR-005's
  case the change is one boolean typed by somebody adding a database on a
  Thursday.

THE DIFFERENCE MATTERS. Silent by design tells you something about the
auditor: it cannot fire, so its silence is a property of the tool. Silent by
situation tells you nothing about the auditor and everything about today's
account - and "we have no findings" and "we have nothing to find" are
different states that render identically in every report. Never read the
second as the first.

CHECK INTERACTIONS THAT ARE DELIBERATE, NOT BUGS

  DR-001, DR-003 and DR-004 all fire on aws_autoscaling_group.single_az and
  they are not duplicates. DR-001 is WHERE it runs - one failure domain.
  DR-003 is WHETHER IT NOTICES an application failure - health_check_type
  "EC2" means a deadlocked process is a healthy instance forever. DR-004 is
  WHETHER A REPLACEMENT CAN START AT ALL - a zero grace period is a
  termination loop. Fixing any one leaves the other two, the remediations are
  unrelated, and in most organisations they have different owners: the network
  team owns the subnets, the platform team owns the ASG, and the application
  team owns how long a boot takes.

  DR-002 IS THE ONLY CHECK THAT FIRES ON YOUR OWN CORRECTLY-INTENDED STACK
  rather than on a deliberately broken example. nat_gateway_strategy defaults
  to "single", which is a real, defensible, extremely common cost decision
  that puts a single-AZ dependency inside an architecture everybody calls
  multi-AZ. It is also the only finding in this contract that you clear by
  SPENDING MONEY rather than by fixing a mistake - roughly $36/month more.
  That is deliberate. An auditor whose findings are all strawmen teaches
  people that findings are strawmen.

  DR-008 AND DR-010 LOOK LIKE THE SAME CHECK AND ARE NOT. DR-008 asks "is
  there a recent enough backup". DR-010 asks "has anybody ever proved a backup
  can be turned back into a system". A vault full of fresh, correctly
  retained, cross-region-copied recovery points that has never had a single
  restore performed against it scores 0 on DR-008 and 25 on DR-010, and that
  is the normal state of most organisations. The failure modes DR-010 exists
  for - a rotated KMS key, a missing AMI, an instance type unavailable in the
  DR region, a deprecated engine version, a restore that works and takes nine
  hours - are all invisible in a backup report and all obvious in one restore
  test.

  DR-010 AND DR-016 ARE THE SAME IDEA ABOUT TWO DIFFERENT THINGS - restore
  versus failover - and both are reported at a level ABOVE any single
  resource. DR-010 is an account-level singleton; DR-016 is per state machine.
  Neither is attached to a data resource, deliberately: they are statements
  about the ORGANISATION, not about a bucket, and attaching them to a resource
  id invites somebody to close the finding by deleting the resource.

  DR-013 FIRES ON A CORRECTLY-CONFIGURED REPLICATION RULE. The rule works.
  Objects replicate. What is absent is the METRIC, because Replication Time
  Control is off - and without it there is no way to answer "what is my
  current replication lag", which means there is no way to state an RPO that
  is anything more than an adjective. This is the only check in the set that
  fires on something which is not broken, and it is Day 06's argument in new
  clothes: a summary you cannot check is worse than no summary, and an RPO you
  cannot measure is worse than no RPO, because you will quote it.

  DR-009 FIRES TWICE, ONCE PER VAULT, INCLUDING THE DR VAULT, and is
  deliberately not deduplicated up to the plan. A locked primary vault beside
  an unlocked DR copy vault is a real and common asymmetry, and it is exactly
  backwards: the DR vault is the one an attacker who has already compromised
  the primary account will reach for, because it is the copy that survives
  everything they just did.

  DR-016 FIRES ON THE NAIVE STATE MACHINE TOO, and after lab step 7 it is the
  only DR-016 finding left. An automated failover that has never been executed
  is untested; an automated failover that has never been executed AND has no
  kill switch, no assessment, no approval gate and no verification is untested
  in a way that will be discovered by production. DR-015 and DR-016 fire on
  the same resource for genuinely different reasons and neither remediates the
  other.
=============================================================================
```

---

## What you built

**Infrastructure**

- A 2-AZ VPC with public and private subnets, an S3 gateway endpoint, and a
  deliberately single-AZ NAT strategy so DR-002 has something true to say
  about your own stack
- An ALB across both zones with a target group whose detection arithmetic you
  can now read, feeding an ASG with `health_check_type = "ELB"` and a grace
  period that will not boot-loop
- DynamoDB with point-in-time recovery and an optional global table replica,
  S3 with versioning, lifecycle and cross-region replication, and an optional
  RDS instance for the Multi-AZ demonstration
- AWS Backup with vaults in both regions, a copy rule, tag-based selection,
  and an optional governance-mode vault lock
- A Route 53 health check, and failover records if you own a domain

**The recovery path**

- A Step Functions workflow — kill switch, assess, decide, approve, execute,
  verify, notify — with no retry on the irreversible step and four terminal
  failure states
- An SSM kill switch flippable from a phone, that the workflow's own role
  cannot write
- An active-region parameter that your application must actually read, or the
  failover is theatre
- A manual-only failback action whose docstring is the five things it cannot
  reverse
- A chaos Lambda with three failure modes, a dry run by default, and an IAM
  policy scoped by tag

**The tooling**

- `dr_audit.py`: 16 checks, 9 services, 2 regions, pure functions over a dict
  with an injected clock
- 47 tests that run without credentials
- A challenge version generated from the reference so it cannot drift

**And the thing that actually matters**

You broke your own infrastructure on purpose, timed the recovery, and wrote
the measured number next to the one you declared, in
`lab/rto-measurements.md`. If those two numbers were
close, you had already been doing this. If they were not — and for most people
they are not, by a factor of two to ten — **you now know something about your
platform that no amount of reading could have told you.**

Tomorrow is cost optimisation, where the pattern repeats in a different key:
the number in the spreadsheet and the number on the invoice are also two
different things, and only one of them is a measurement.

---

**Before you leave:** run `terraform destroy`, then work through
`lab/teardown-checklist.md`. Destroy does not remove the EBS snapshot, and the
DR region needs checking separately — cross-region resources are, by
construction, the ones that survive the thing that was supposed to remove
them.
