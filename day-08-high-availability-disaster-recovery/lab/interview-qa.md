# Day 08 — Interview Questions

Twelve questions an interviewer actually asks about high availability and
disaster recovery, with the shallow answer, the answer that gets the offer, and
the follow-up you should expect.

The pattern across all twelve: **the shallow answer describes a configuration
and the good answer describes a measurement.**

---

## 1. What is the difference between RTO and RPO?

**Shallow:** "RTO is how long recovery takes, RPO is how much data you lose."

**Good:** Both are measured in **time**, and that is the part people miss about
RPO — you do not lose "some records", you lose "everything written since the
last recoverable point", and the size of that is a function of how often you
create recoverable points. **Your backup schedule is your RPO ceiling.** Daily
at 05:00 means that at 04:59 you are 23 hours 59 minutes from your last
recovery point, whatever the document says.

The more important distinction is that **neither is a setting**. They are
claims about a procedure. An RTO you have never measured is a target; an RTO
from the last time somebody ran the procedure under time pressure is a number.
I would want to know when a stated RTO was last measured, and by whom, before I
believed it.

**Expect the follow-up:** *"What actually consumes RTO?"* — and the answer that
lands is the one that names the parts that are not technical: detection by a
human, the decision to act, and data reconciliation. In most measured incidents
those three exceed every configured timeout combined.

---

## 2. What is the difference between an Availability Zone and a Region?

**Shallow:** "AZs are data centres in a region; regions are geographic areas."

**Good:** An AZ is a **failure domain** — the unit that fails together. A
region is a **blast radius** — the unit that fails together when the failure is
not physical.

The engineering consequence sits in the latency. AZs are a few kilometres
apart with sub-millisecond round trip, which makes **synchronous** replication
practical: that is why an RDS Multi-AZ standby can give you an RPO of zero.
Regions are far enough apart that synchronous replication costs ~70ms per
write, which no transactional workload accepts, so cross-region replication is
asynchronous, which means it has lag, which means it is an RPO rather than a
guarantee.

And the uncomfortable part: the large outages of the last decade were mostly
not floods. They were bad config pushes, capacity cascades, undocumented
dependencies, expired certificates. **Multi-region protects you from those in
exact proportion to how independent your two regions really are** — and a DR
region deployed by the same pipeline, from the same repository, five minutes
later, is not very independent at all.

**Expect the follow-up:** *"So is multi-region worth it?"* See question 11.

---

## 3. You have a multi-AZ VPC with a multi-AZ ASG behind an ALB. What is the most likely single-AZ dependency?

**Shallow:** "The database."

**Good:** The **NAT gateway**. It is zonal, it costs ~$32.85/month plus
processing, and the correct architecture is one per AZ. So somebody deletes one
during a cost review and points both private route tables at the survivor. The
bill halves, every test passes, and the diagram is unchanged.

The failure mode is what makes it dangerous: **it is not an outage.** AZ-a
goes away, instances in AZ-b keep running, pass EC2 status checks, and pass ALB
health checks — because a target group health check is an HTTP GET from the
load balancer inside the VPC and never traverses NAT. The dashboard stays
green. Every outbound call fails. You get an incident that reads as
"third-party API is down" for the first twenty minutes.

**Expect the follow-up:** *"How would you fix it cheaply?"* — and the answer
that shows judgement is that per-AZ NAT is not always the right first move.
**VPC gateway endpoints for S3 and DynamoDB are free** and remove the largest
source of NAT traffic in most stacks while also removing an AZ dependency from
the data path. Interface endpoints are ~$7.30/month per endpoint per AZ; once
you need three or four, per-AZ NAT is cheaper. Below that, endpoints win on
both cost and availability.

---

## 4. Your ASG has `health_check_type = "EC2"`. What breaks?

**Shallow:** "It won't replace unhealthy instances."

**Good:** It replaces instances **EC2** says are unhealthy — hypervisor
failure, failed status checks — and knows nothing about your application.

Walk the actual failure: the application process deadlocks. The instance is
running, the OS answers, EC2 status checks pass. The target group health check
fails **correctly**, the ALB deregisters the target **correctly**, traffic goes
to the healthy instances and **the service is fine**. The ASG does nothing.
You now pay for an instance serving zero requests, indefinitely, your effective
capacity is silently N-1, and **nothing alarms because nothing is down.**

This survives for months and is discovered during the next incident, when the
spare capacity that was supposed to absorb an AZ failure turns out to have been
dead since March.

**Expect the follow-up:** *"So just set it to ELB?"* — and the answer that
shows you have done this is: **yes, and set the grace period in the same
change.** Turning on ELB health checks without an adequate
`health_check_grace_period` converts a silent capacity leak into a loud boot
loop: the ASG terminates instances for being slow to start, and their
replacements for the same reason, forever. Both lines, or neither.

---

## 5. Is Multi-AZ RDS a read replica?

**Shallow:** "It's a standby you can read from."

**Good:** No, and this is the most common misconception in AWS data tiers.
**The standby serves no traffic.** You cannot query it. It does not improve
read throughput, write throughput, or latency in any way. It is a hot spare
that costs exactly as much as the thing it is sparing.

What it does buy is a synchronous standby and an automatic DNS failover in
typically 60–120 seconds, with an RPO of zero for an AZ failure.

Read replicas are a **different feature**: asynchronous, promotable manually,
billed separately. You can have both, and most people who need one need both.

**Expect the follow-up:** *"So Multi-AZ means no downtime?"* — no. **A Multi-AZ
failover drops every connection and rolls back every in-flight transaction.**
An application with a connection pool and no retry logic experiences it as an
outage whose length is set by the pool's TCP timeout, which is very often
longer than the 60–120 second failover it was supposed to hide.

---

## 6. Your S3 bucket replicates cross-region. What is your RPO for that bucket?

**Shallow:** "A few seconds, replication is fast."

**Good:** **Unknown**, by default, and that is the honest answer.

S3 cross-region replication is asynchronous and, without Replication Time
Control, has **no SLA and no metrics**. Most objects arrive in seconds; some
take minutes; under a large burst some take considerably longer. "Usually fast"
is true and is not an RPO. An RPO is a number you can defend, and there is no
API call that will tell you what your current replication lag is.

RTC costs ~$0.015/GB on top of transfer and storage, and buys a
99.99%-within-15-minutes SLA **plus CloudWatch metrics**. The metrics are the
part that matters.

**The thing worth saying out loud:** that is money spent on **observability**,
not on capability. The data replicates either way. What the money buys is the
ability to say a true sentence about it — and an RPO you cannot measure is
worse than no RPO, because you will quote it.

**Expect the follow-up:** *"What else surprises people about CRR?"* Three
things. Versioning is mandatory on both buckets (an API constraint, not advice),
and it means deletes stop deleting and every version bills in both regions
forever unless you add a lifecycle rule. **Replication is not retroactive** —
turning it on replicates objects created after that moment, so the DR bucket
can hold three weeks of data while the primary holds three years. And **delete
markers do not replicate by default**, which is usually the safe choice and
means the two buckets are not mirrors of each other.

---

## 7. What is the difference between a replica and a backup?

**Shallow:** "A backup is a point-in-time copy, a replica is live."

**Good:** They protect against different things and neither substitutes for
the other.

**Replicas protect against losing infrastructure. Backups protect against
losing data.**

Replication is faithful, and that is the problem. It replicates your bad
migration, your truncating bug, and your ransomware encryption — in under a
second, to every region you replicate to. A global table replica is not a
recovery option for corruption; it is a second copy of the corruption.

What distinguishes a backup is that it has **version history**: a point you can
go back to *before* the thing that was wrong. That is why PITR and retention
windows matter, and why one day of RDS backup retention is technically backups
and practically not — corruption discovered on Friday that began Thursday is
unrecoverable.

**Expect the follow-up:** *"So how much retention?"* Seven days is the minimum
that survives a weekend plus a Monday of nobody looking. And note which failure
each control owns: AZ failure is Multi-AZ's job, hardware failure is the
storage layer's, and backups exist for the case where the data was wrong and
nobody noticed immediately.

---

## 8. How do you know your backups work?

**Shallow:** "We monitor the backup jobs and they all succeed."

**Good:** You do not, until somebody restores one. **A backup nobody has
restored is a file.**

Every failure mode that matters is invisible in a backup report and obvious
after one restore: the KMS key was rotated or deleted; the AMI the recovery
point references no longer exists; the instance type is not available in the DR
region; the engine version has been deprecated and cannot be launched; the IAM
role has **backup** permissions and not **restore** permissions.

And one that is not a failure at all: the restore works and takes nine hours.
That is an RTO, discovered rather than declared.

The strongest version of the answer names the metric: I would want a **restore
test on the calendar**, into the **DR region**, timed, with the number written
next to the RTO in the document — and dated, because a restore test more than a
quarter old is describing an architecture that has since changed.

**Expect the follow-up:** *"What would you audit for?"* Two checks that look
like one and are not. "Is there a recent enough recovery point" and "has
anybody ever restored one". A vault full of fresh, correctly retained,
cross-region-copied recovery points that has never been restored from passes
the first and fails the second, and **that is the normal state of most
organisations.**

---

## 9. Would you automate a regional failover?

**Shallow:** "Yes, that's the whole point of automation."

**Good:** In-AZ recovery, yes, without a gate — it is reversible by doing
nothing and the ASG mostly does it already. A **regional** failover, no, not
without a human, and the reason is asymmetry rather than distrust of
automation.

The evidence a failover decision runs on is health checks, and **health checks
lie during exactly the network conditions that make you want to fail over.** A
transient partition, a bad deploy, and an expired certificate all look like a
regional outage from inside. An automated failover triggered by one of those is
how you get split brain, and split brain in a last-writer-wins data store is
silent, permanent data loss.

So: the cost of a false negative is a few more minutes of a partial outage. The
cost of a false positive is a divergent dataset. **Those are not symmetric**,
and the design should reflect it.

What I would build instead: a kill switch read as the first state and failing
closed; an assessment that returns "I could not tell" as a real answer rather
than guessing; an approval gate before anything irreversible; a dry-run mode
passed by reference rather than hardcoded; and a verification step that can
**fail the execution** rather than reporting success because an API call
returned 200.

**Expect the follow-up:** *"But the whole point is that it works at 3am when
nobody answers."* That argument is real and I would not dismiss it. My answer
is that an approval gate converts your RTO from "90 seconds" into "however long
it takes to wake somebody" — and if that is your answer, **say so in the RTO**
rather than hiding it behind a state machine. What is not defensible is having
a gate you have never tested the response path for.

---

## 10. Why is DNS TTL part of your RTO?

**Shallow:** "Because DNS takes time to propagate."

**Good:** Because a resolver that fetched your record one second before the
failover keeps serving the old address for the **full TTL** — not on average,
but as a worst case, for some fraction of your users, no matter how fast
everything else was. A 300-second TTL means five minutes of your recovery
budget is gone before anything you did has any effect on those clients.

And it does not stop there. Route 53's own detection is
`failure_threshold × request_interval` — 3 × 30 = 90 seconds before it will
even consider the endpoint unhealthy. Add the TTL and you are at 150 seconds
before anything else has happened.

**The part that ruins the arithmetic: TTL is a request, not a guarantee.**
Resolvers clamp minimums. Corporate resolvers cache far longer than you asked.
Java, historically and by default, cached DNS resolutions for the life of the
JVM — which is the origin of "we failed over successfully but the app servers
kept connecting to the old database", a story every senior engineer has a
version of.

The design consequence is that **DNS failover is a coarse, slow, best-effort
mechanism.** Fine for shifting human traffic between regions; a poor mechanism
for anything that needs to be fast or exact, which is why in-AZ failover uses
load balancer target health rather than DNS.

**Expect the follow-up:** *"So why not TTL of 1?"* Cost and latency — Route 53
bills ~$0.40 per million queries and a TTL of 1 multiplies query volume by
roughly 300 against a TTL of 300. The practical answer is tiered: 60 seconds on
records that participate in failover, longer on records that do not.

---

## 11. Pilot light, warm standby, or active-active?

**Shallow:** "Warm standby is the good middle ground."

**Good:** It depends on the workload, and the answer I would defend most often
is **none of them.**

The ladder, with honest numbers for a small stack: backup-and-restore ~$85 a
month with an RTO of hours across regions; pilot light ~$95 with 1–4 hours;
warm standby ~$160 with 10–30 minutes; active-active ~$180 plus engineering
with near zero.

The costs that are not on the invoice are the ones that decide it. A warm
standby roughly doubles the deployment surface and the configuration drift, and
it is exercised only during an incident — a standby that has been receiving
deploys for eight months and traffic for zero minutes has unknown behaviour
under load. Active-active requires **a conflict story for every data model you
own**, because global tables are last-writer-wins with nothing else, and that is
fine for an append-only event log and is data loss with extra steps for an
inventory level.

So the position I would take into a design review:

> A regional failure of the kind multi-region protects against happens to a
> given region roughly once every few years. **Many organisations should choose
> the cheap option and spend the difference on testing it.** An untested warm
> standby and a tested backup-and-restore have very different real RTOs from
> their advertised ones, and the difference usually runs in favour of the cheap
> one.

**Expect the follow-up:** *"How do you decide?"* By asking what the business
actually loses per hour of downtime, and comparing it to the annualised cost of
the tier. If nobody can answer the first question, that is the finding.

---

## 12. Your DR audit passed last month and fails today. Nobody changed anything. Is the tool broken?

**Shallow:** "Something must have changed, or the tool has a bug."

**Good:** No — that is **correct behaviour**, and it is the difference between
a configuration audit and a recovery audit.

RTO and RPO are not properties of a configuration. They are claims about a
**procedure**, and a claim about a procedure decays continuously from the last
time somebody ran the procedure. A check that compares the age of your newest
recovery point against your stated RPO will pass at 14:00 and fail at 15:01 on
an account nobody has touched. So will a check on how long ago the last
failover test succeeded.

A merge-time-only audit certifies the account **as it was on the day somebody
last changed it**, and that is not the property a DR posture needs to have.
These checks belong on a schedule.

**The practical consequence** worth mentioning: with a backup schedule slower
than your stated RPO, such a check **sawtooths** — silent for the minutes after
each successful job, firing again as the recovery point ages. Two runs a minute
apart give different answers and both are correct. If that is uncomfortable,
the fix is not a looser check. It is a schedule that is actually faster than
the RPO you claimed, or an RPO you can defend.

**Expect the follow-up:** *"How do you stop that paging people constantly?"*
Mark the runtime-dependent checks explicitly in the output — this repo's
auditor emits `runtime_dependent_checks` in its JSON for exactly that reason —
so a consumer diffing two runs knows which checks could legitimately change
without anybody touching the account. Alert on the **trend and the threshold**,
not on the delta.

---

## The pattern

Read back through the twelve. In every one, the shallow answer describes what
is configured and the good answer describes what has been measured.

That is not a coincidence, and it is what the interviewer is testing for.
Anyone can read a console. The question underneath all twelve is whether you
have ever **broken something on purpose and held a stopwatch**, because that is
the only way any of these answers stops being a recital.
