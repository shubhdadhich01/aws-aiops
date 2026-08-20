# Day 03 — Interview Q&A

> Compute architecture and scaling is the most-asked topic in AWS interviews
> after IAM, because it is where "I read the docs" and "I have operated this"
> diverge fastest. Every answer below is written the way you should say it out
> loud: position first, reasoning second, trade-off acknowledged third.

---

## 1. Why are launch configurations deprecated, and what changed?

**Answer.** Launch configurations are immutable and unversioned — to change
anything you create a new one and update the ASG to point at it, so there is no
rollback and no diff. More importantly they cannot express anything AWS has
added since roughly 2018: IMDSv2 enforcement, mixed instance types, Spot/On-Demand
blends, T-unlimited credit specification, placement groups, capacity reservations.

Launch templates fix all of that. They are versioned, so an ASG can track
`$Latest` or pin `$Default`, and an instance refresh gives you a controlled
rolling replacement with a `min_healthy_percentage`.

**The follow-up they want:** "What breaks if you migrate?" Nothing in the ASG
itself — you swap `launch_configuration` for `launch_template` and the existing
instances keep running. They are replaced on the next scaling event or instance
refresh. The gotcha is that a launch template's `$Latest` means a template edit
can silently change what launches next, so in production you pin a version
number and bump it as a deliberate release step.

---

## 2. Explain target tracking, step scaling, and simple scaling. When do you use each?

**Answer.**

- **Target tracking** — you name a metric and a target value; AWS creates and
  manages the CloudWatch alarms and adjusts capacity to hold the target. This
  is the default choice for roughly 90% of workloads because there are no
  thresholds to tune and no cooldown to get wrong.
- **Step scaling** — you define breach ranges relative to an alarm threshold,
  and each range maps to an adjustment. "0–10% over: +1. 10%+ over: +2." Use it
  when target tracking's proportional response is too gentle for traffic that
  arrives all at once.
- **Simple scaling** — one alarm, one adjustment, then a blocking cooldown
  during which nothing else can happen. That blocking behaviour is why it is
  legacy: during a genuine spike you sit idle while the cooldown expires.

**The trade-off to name:** target tracking is slower to react to a step-function
load increase, because its alarms need multiple consecutive breaching periods.
If you need sub-minute reaction, layer step scaling on top, or use predictive
scaling if the pattern is time-based.

---

## 3. What is the difference between instance warm-up and cooldown?

**Answer.** They solve different problems and people conflate them constantly.

**Warm-up** excludes a newly launched instance's metrics from the scaling
aggregate until it elapses. A booting instance runs at 100% CPU for a minute; if
that counted toward `ASGAverageCPUUtilization`, it would drag the average up and
trigger another scale-out, which boots another instance at 100%, and so on. That
is a scaling storm.

**Cooldown** blocks *all* scaling activity for N seconds after any scaling
event. It is a blunt instrument attached to simple scaling and the ASG default.

**The one-liner:** warm-up filters the *signal*; cooldown blocks the *response*.
Target tracking and step scaling use warm-up and largely ignore cooldown, which
is one more reason to prefer them.

**Set warm-up to** measured boot-to-healthy time plus ~30 seconds. If your AMI
boots in 90s and your app warms a cache for 60s, that is 180, not 30.

---

## 4. An ASG has `health_check_type = "EC2"` and sits behind an ALB. What breaks?

**Answer.** Any failure the EC2 status checks cannot see — which is most of them.

EC2 status checks cover host hardware, network reachability, and whether the
kernel is alive. They say nothing about the application. So a hung JVM, an
OOM-killed process, a full disk, an exhausted database connection pool, or nginx
returning 502 on every request all leave the status checks green. The ALB knows
the target is failing and stops routing to it, but the ASG never replaces it.

Result: you pay full price for an instance that serves nothing, indefinitely,
and your effective capacity silently drops.

**The fix** is `health_check_type = "ELB"`, which makes the ASG honour the
target group's health check as well.

**The caveat you should volunteer:** before flipping this, verify
`health_check_grace_period` exceeds your real boot-to-healthy time. Turning on
ELB health checks with a 30-second grace period on an app that takes 3 minutes
to start produces an infinite launch/terminate loop — every instance is killed
mid-boot and replaced by another that gets killed mid-boot. That bills
continuously and never converges.

---

## 5. ALB vs NLB vs GWLB — how do you choose?

**Answer.**

| | ALB | NLB | GWLB |
|---|---|---|---|
| Layer | 7 | 4 | 3 |
| Routes on | host, path, header, method, query, source IP | protocol + port | transparent |
| Static IP | no | yes, one EIP per AZ | no |
| Source IP preserved | no (`X-Forwarded-For`) | yes | yes |
| WAF | yes | no | no |
| Cross-zone | always on, free | **off by default**, costs cross-AZ transfer | off by default |

**Decision rule:** HTTP/HTTPS/gRPC and you want any content-based routing or
WAF → ALB. Raw TCP/UDP, extreme throughput, a static IP requirement, or mTLS
passthrough → NLB. Inserting a third-party firewall or IDS inline → GWLB.

**The detail that separates candidates:** NLB cross-zone load balancing is off
by default. Each zonal node only forwards to targets in its own AZ, so uneven
target counts across AZs produce wildly uneven traffic distribution — one
instance can take 50% of traffic while four others idle. ALB has cross-zone on
permanently and free, so this is an NLB-only decision.

---

## 6. Why is `min_size = 1` not high availability?

**Answer.** One instance is one Availability Zone, so there is no redundancy at
the level that AWS actually fails at. Any of these takes you to zero capacity:
an AZ event, an instance replacement, a deployment, a health check flap, or the
ASG deciding to rebalance.

Even with self-healing configured perfectly, replacement takes 2–5 minutes —
that is a full outage every time, not a degraded window.

**The floor for HA is `min_size = 2` across at least two AZs.** And that is a
floor, not a target: with exactly 2, losing one leaves a single instance
carrying 100% of the load, which is often enough to take the survivor down too.
For anything with real traffic, N+1 across three AZs is the honest answer.

**The cost conversation they may push on:** yes, `min_size = 2` doubles baseline
compute cost. Compare that to the cost of the outage. If the workload genuinely
tolerates a 5-minute gap, say so and run at 1 — but call it what it is.

---

## 7. Walk me through exactly what happens when you terminate an instance in an ASG.

**Answer.**

1. **t+0** — `TerminateInstances` returns. The instance begins shutting down.
2. **t+15–30s** — the target group's health check fails twice consecutively.
   The target moves to `unhealthy`.
3. **t+30s** — the ALB stops routing to it and it moves to `draining`. In-flight
   requests get `deregistration_delay` seconds to finish. **User impact ends
   here.**
4. **t+30–60s** — the ASG notices it is below desired capacity and posts a
   scaling activity: "Terminating EC2 instance", then "Launching a new EC2
   instance".
5. **t+60–90s** — a new instance launches from the launch template's referenced
   version.
6. **t+90–150s** — it boots, userdata runs, the app starts.
7. **t+150s** — it registers with the target group in state `initial`. The
   health check grace period means the ASG ignores health signals until it
   elapses.
8. **t+180–330s** — two consecutive passing health checks, target goes
   `healthy`, desired capacity is restored.

**Total user-visible impact: zero,** provided you had at least one other healthy
target and `min_size >= 2`.

---

## 8. What is IMDSv2 and why does it matter for a launch template?

**Answer.** The instance metadata service at `169.254.169.254` serves, among
other things, the temporary IAM credentials for the instance's role. IMDSv1
answers an unauthenticated `GET`. IMDSv2 requires a `PUT` to obtain a session
token first, which is then sent as a header on subsequent requests.

**Why the difference matters:** SSRF vulnerabilities let an attacker make *your
server* issue a request to a URL of their choosing. With IMDSv1 that is enough
to read the credentials. With IMDSv2 it is not, because SSRF primitives almost
never let you control the HTTP method and set arbitrary headers. The 2019
Capital One breach was exactly this shape.

**In a launch template:**

```hcl
metadata_options {
  http_tokens                 = "required"
  http_endpoint               = "enabled"
  http_put_response_hop_limit = 1
}
```

`http_put_response_hop_limit = 1` matters too: the default of 2 lets a container
running on the host reach IMDS through the Docker bridge and steal the *host's*
role.

**The rollout caveat:** every current AWS SDK handles IMDSv2 transparently, but
very old SDKs and hand-rolled `curl` calls in userdata do not. Audit for IMDSv1
usage with the `MetadataNoToken` CloudWatch metric before enforcing.

---

## 9. Your target group shows zero healthy targets. Walk me through the diagnosis.

**Answer.** In this order, because it is cheapest-first:

1. **Is the health check path right?** `/health` must exist and return a status
   code inside the `matcher`. A path that 404s fails the check even though the
   server is perfectly healthy. Check the path, port and matcher together.
2. **Security group.** The target's SG must allow the health check port *from
   the load balancer's SG*. If it is scoped to a CIDR instead, and subnets
   changed, the rule silently stops matching.
3. **Is the app actually listening?** SSM in and `curl -i localhost/health`. If
   it passes locally but fails from the LB, it is the security group or the
   port, not the app.
4. **Grace period vs boot time.** If the grace period is shorter than boot, the
   ASG kills every instance mid-boot. The symptom is a stream of new instance
   IDs in the target group and no target ever reaching `healthy`.
5. **Timeout vs interval.** Health check `timeout` must be less than `interval`.
   If the app takes 6 seconds to answer and the timeout is 5, every check fails.
6. **Subnet routing.** Private subnets with no NAT route cannot complete
   userdata package installs, so the app never starts.

**The meta-point:** every one of these is visible from `describe-target-health`'s
`Reason` and `Description` fields. Read them before guessing.

---

## 10. How do you do a zero-downtime deployment with an ASG?

**Answer.** Three options, in increasing order of control:

**Instance refresh** — built in. Set a `min_healthy_percentage` (say 90) and a
warm-up, then trigger a refresh when the launch template changes. The ASG
replaces instances in batches, waiting for each batch to pass health checks.
Simple, no extra infrastructure, but rollback means triggering another refresh
with the old template version.

**Blue/green with two target groups** — stand up a second ASG on a new launch
template, register it to a second target group, then shift the ALB's listener
weights from 100/0 to 0/100. Rollback is instant: flip the weights back.
Costs double capacity during the cutover.

**Canary via weighted target groups** — same as blue/green but you sit at 95/5
for a while and watch error rates before proceeding. This is what most mature
teams do.

**The thing that makes any of them actually zero-downtime:** a correct
`deregistration_delay`. AWS defaults it to 300 seconds, which makes deploys feel
glacial; set it to 0 and in-flight requests get killed mid-response. Set it to
just above your p99 request duration — usually 30 seconds for an HTTP API.

---

## 11. What are termination policies and why does the default one bother you?

**Answer.** When an ASG scales in it must choose which instance dies. The
`Default` policy picks, within the AZ that has the most instances, the instance
closest to the next billing hour — a hangover from per-hour billing that is
largely meaningless now that EC2 bills per second.

The practical effect is that scale-in is arbitrary. It can retire the instance
you launched five minutes ago on the new AMI and keep a three-week-old one
running.

**Better:**

```hcl
termination_policies = ["OldestLaunchTemplate", "OldestInstance", "Default"]
```

Now scale-in doubles as a slow rolling refresh — every time load drops, your
staler instances retire first. Over a week of normal traffic cycles the fleet
converges on the current template without a single deployment.

**Also worth knowing:** `AllocationStrategy` for mixed-instance groups, and
`NewestInstance` — genuinely useful when you are rolling back a bad deployment
and want the new instances gone first.

---

## 12. How would you scale on something other than CPU?

**Answer.** CPU is a proxy, and often a bad one. For a web tier, `ALBRequestCountPerTarget`
is a better predefined metric — it reacts before CPU has time to climb and it
maps directly to the thing you care about.

```hcl
predefined_metric_specification {
  predefined_metric_type = "ALBRequestCountPerTarget"
  resource_label = "app/my-alb/abc123/targetgroup/my-tg/def456"
}
```

The `resource_label` format trips people up: it is
`<alb-arn-suffix>/<tg-arn-suffix>`, which in Terraform is
`"${aws_lb.main.arn_suffix}/${aws_lb_target_group.app.arn_suffix}"`. Building it
by hand from the full ARN is a classic twenty-minute mistake.

For a queue-backed worker tier, scale on **backlog per instance** — an SQS
`ApproximateNumberOfMessagesVisible` divided by in-service instance count, as a
custom metric with a target tracking policy. That is the canonical example of a
custom metric done right, because raw queue depth scales with traffic while
backlog-per-instance is the thing that stays constant when you are correctly
provisioned.

For memory, you need the CloudWatch agent — EC2 does not report memory natively.

---

## 13. What is cross-zone load balancing and when does it cost you money?

**Answer.** Cross-zone load balancing lets each of the load balancer's zonal
nodes forward to targets in *any* AZ, rather than only its own.

- **ALB:** always on, cannot be disabled, and the cross-AZ data transfer is free.
- **NLB:** off by default. When enabled, cross-AZ data transfer is charged at
  roughly $0.01/GB in each direction.
- **CLB:** off by default, free when enabled.

**Why it matters:** without it, traffic is distributed evenly across *zones*,
not across *targets*. With 4 targets in AZ-a and 1 in AZ-b, that single instance
receives 50% of all traffic. During a scale event, an instance replacement, or a
partial AZ failure, distributions become uneven exactly when you can least
afford it.

**The judgement call:** for an NLB carrying high-volume east-west traffic, the
cross-AZ charges are real and you might instead ensure even target distribution
per AZ. For anything else, turn it on — the incident costs more than the data
transfer.

---

## 14. Design a compute tier for a service with a daily traffic pattern: 10× peak at 09:00, quiet overnight.

**Answer.** Layered, because no single mechanism handles both the predictable
and the unpredictable parts.

1. **Baseline:** ASG with `min_size = 2` across three AZs, launch template with
   IMDSv2 and encrypted volumes, ALB with ELB-type health checks.
2. **Target tracking** on `ALBRequestCountPerTarget` as the always-on safety
   net. This handles anything unpredictable.
3. **Scheduled scaling** to raise `min_size` at 08:30 — *before* the ramp, not
   during it. Reactive scaling always lags the load by the time it takes to boot
   an instance; for a known 09:00 spike you should already have the capacity.
4. **Predictive scaling** if you have 14+ days of history. It forecasts from the
   weekly pattern and provisions ahead of the curve, and it composes with target
   tracking rather than replacing it.
5. **Overnight:** let target tracking scale in to `min_size`. Do not schedule it
   to zero — you lose the ability to absorb anything unexpected, and cold-start
   at 03:00 for an unexpected batch job is a bad night.

**Cost lever to mention:** a mixed instances policy with 70% Spot above the
baseline. The scheduled ramp is predictable enough that Spot interruption risk
is manageable, and it typically cuts peak compute cost by 60%+.

**Metric to name:** you are optimising for the ratio of provisioned to used
capacity, and the honest target is 60–70% average utilisation, not 90%. Above
that you have no headroom for the scale-out latency itself.

---

## 15. Your ASG is in a launch loop — new instances constantly launching and terminating. Diagnose it.

**Answer.** Almost always one of four things, and the ASG's scaling activities
tell you which within thirty seconds:

```bash
aws autoscaling describe-scaling-activities \
  --auto-scaling-group-name my-asg --max-records 10 \
  --query 'Activities[].{Time:StartTime,Status:StatusCode,Cause:Description}'
```

1. **Grace period shorter than boot time.** The most common cause. Instances are
   terminated before they finish starting, so a replacement launches, and it
   gets killed too. Symptom: `Cause` reads "instance was taken out of service in
   response to an ELB system health check failure" on instances only a minute or
   two old. Fix: raise `health_check_grace_period`.
2. **Broken userdata.** The app never starts, so the health check never passes.
   Symptom: same as above, but raising the grace period does not help. Fix: SSM
   into a live one before it dies and read `/var/log/cloud-init-output.log`.
3. **Bad AMI or launch template version.** `$Latest` picked up a change nobody
   intended. Fix: pin the version and roll back.
4. **Health check path/port/matcher mismatch.** The app is fine; the check is
   wrong.

**The financial urgency to convey:** this bills continuously and never
converges. A launch loop on a large instance type across a weekend is a
four-figure invoice for zero delivered service, plus the outage. Set an alarm on
`GroupTerminatingInstances` so you find out in minutes rather than on the bill.

**The fast mitigation while you diagnose:** suspend the `ReplaceUnhealthy`
process (`aws autoscaling suspend-processes --scaling-processes ReplaceUnhealthy`).
That stops the bleeding without deleting anything, and gives you a live broken
instance to look at.

---

## Rapid-fire

| Question | Answer |
|---|---|
| Launch config or launch template? | Launch template. Always. Configs are legacy and cannot express IMDSv2 or mixed instances. |
| Minimum ASG size for HA? | 2, across 2+ AZs. |
| Default `health_check_type`? | `EC2` — and it is the wrong default for anything load-balanced. |
| Which health check type catches a hung app? | `ELB`. |
| Warm-up vs cooldown in one line? | Warm-up filters the signal; cooldown blocks the response. |
| Default scaling policy choice? | Target tracking. |
| Is ALB cross-zone free? | Yes, and it cannot be disabled. |
| Is NLB cross-zone free? | No — cross-AZ data transfer applies, and it is off by default. |
| Which LB preserves the client source IP? | NLB (and GWLB). ALB uses `X-Forwarded-For`. |
| Which LB gives you a static IP? | NLB, one EIP per AZ. |
| Which LB works with AWS WAF? | ALB only. |
| ALB listener rule evaluation order? | Ascending priority, lowest number first, first match wins. |
| What does `deregistration_delay` do? | How long a draining target has to finish in-flight requests. Default 300; set ~30. |
| Health check on `/` — problem? | Passes while the app is broken. Use a real `/health` that checks dependencies. |
| `http_tokens = "required"` means? | IMDSv2 only. Blocks SSRF-based credential theft. |
| Why `hop_limit = 1`? | Stops containers reaching IMDS through the Docker bridge. |
| Default EBS encryption in a launch template? | Inherited/unset — not guaranteed. Set `encrypted = true` explicitly. |
| ASG spans 2 AZs but all instances in one — why? | Capacity shortage for that instance type in the other AZ, or no free subnet IPs. |
| How do you get a shell without SSH? | SSM Session Manager via `AmazonSSMManagedInstanceCore`. |
| What stops a launch loop immediately? | `suspend-processes --scaling-processes ReplaceUnhealthy`. |
| Instance refresh `min_healthy_percentage` default? | 90. |
| Which termination policy acts as a rolling refresh? | `OldestLaunchTemplate` first. |
| ALB base cost? | ~$0.0225/hour (~$16.20/month) plus $0.008/LCU-hour. |
| Does an idle ALB cost money? | Yes. Base hourly charge applies with zero requests. |
| Best scaling metric for a web tier? | `ALBRequestCountPerTarget`, not CPU. |
| Best scaling metric for a queue worker? | Backlog per instance (custom metric). |
| Does EC2 report memory to CloudWatch? | No. You need the CloudWatch agent. |
| What does `ignore_changes = [desired_capacity]` prevent? | Terraform resetting capacity that scaling policies own. |
| Predictive scaling minimum history? | 14 days. |

---

| ← Day README | Trainer notes | Teardown |
|---|---|---|
| [Day 03](README.md) | [trainer-notes.md](trainer-notes.md) | [teardown-checklist.md](teardown-checklist.md) |

---

*CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp · Learning Made Simple*
