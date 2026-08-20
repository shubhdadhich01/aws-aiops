# Day 03 — Trainer Notes

> **Total: 3h 20m** including two breaks. Day 03 is the first session that
> spends the class's money, and the first with a genuinely dramatic live demo.
> Both facts shape how you run it.

---

## Before the session

### T-24 hours

- [ ] Apply the lab stack in **your** demo account so the ALB is warm and DNS
      has propagated. A cold `terraform apply` in front of the class burns 7
      minutes of dead air.
- [ ] Confirm `stress-ng` installs on the AMI — if the NAT is off in your demo
      account, the scale-out demo silently does nothing.
- [ ] Have a **second** stack applied and destroyed already so you can show the
      destroy output without waiting for it live.
- [ ] Screenshot the CloudWatch ASG metrics graph from a previous scale-out. The
      live one takes 5 minutes and you will need something to show while waiting.

### T-30 minutes

- [ ] `terraform apply` the demo stack. Verify 2 healthy targets.
- [ ] Open, in tabs: EC2 → Auto Scaling Groups, EC2 → Load Balancers →
      Target Groups → Monitoring, CloudWatch → Alarms.
- [ ] Three terminals ready: (1) commands, (2) `watch` target health,
      (3) the curl loop.
- [ ] Font size up. Dark terminal. The `watch` output is the star of this session.

### The one thing to say in the first two minutes

> "Today's lab costs about ten cents an hour and seventy-three dollars a month.
> Everyone is going to run `terraform destroy` before they close the laptop, and
> I am going to ask each of you to confirm it at the end. Days 1 and 2 were
> free. Today is not."

Say it early, say it plainly, do not bury it in slide 40. People who discover it
on a bill do not come back.

---

## Timing

| Time | Block | Format |
|---|---|---|
| 0:00–0:10 | Opening — the 3 a.m. problem | Talk |
| 0:10–0:30 | Part 1 — EC2 & launch templates | Talk + **Demo 1** |
| 0:30–0:55 | Part 2 — ASGs & scaling policies | Talk + whiteboard |
| 0:55–1:05 | ☕ Break | |
| 1:05–1:30 | Part 3 — ELB & target groups | Talk + **Demo 2** |
| 1:30–1:50 | Part 4 — Health checks & self-healing | Talk + **Demo 3** |
| 1:50–2:00 | ☕ Break | |
| 2:00–2:25 | Lab: apply & verify | Hands-on |
| 2:25–2:45 | Lab: **chaos test** | Hands-on + **Demo 4** |
| 2:45–3:00 | Lab: scale-out | Hands-on + **Demo 5** |
| 3:00–3:15 | Lab: `ha_audit.py` | Hands-on |
| 3:15–3:20 | **Teardown, confirmed out loud** | Hands-on |

> Running long? Cut the scale-out block (2:45–3:00) and demo it from a
> screenshot. **Never cut the chaos test or the teardown.** Those two are why
> the day exists.

---

## 0:00–0:10 — Opening

Do not open with a service list. Open with the problem.

> "It's 3 a.m. An instance in your fleet has a wedged JVM. It's still pingable,
> the EC2 status checks are green, and it's returning 502 to every request that
> reaches it. Your load balancer knows. Your Auto Scaling Group does not.
>
> How long does it stay broken?
>
> With the default configuration: until a human notices. Which, at 3 a.m., is
> when the first customer complains, at 8:40 the following morning.
>
> Today we build the version where the answer is sixty seconds and nobody's
> phone rings."

Then the day's shape: launch templates → ASGs → load balancers → health checks,
and then we break it on purpose.

**Ask the room:** "Who has an Auto Scaling Group in production right now?"
Follow up: "What's its `health_check_type`?" Most people do not know. That is
your hook for Part 4 and worth planting now.

---

## 0:10–0:30 — Part 1: EC2 & launch templates

Cover fast: launch configs are dead, launch templates are versioned. Do not
spend ten minutes on the comparison table — put it on screen, name the three
that matter (versioning, IMDSv2, mixed instances), move on.

**Spend the time on `metadata_options` instead.** This is the highest-value
thirty seconds of the whole day.

### 🎬 Demo 1 — IMDSv1 credential theft (5 min)

The demo that makes IMDSv2 stick. Run it against the **broken** instance.

```bash
# Get onto the deliberately broken instance
BROKEN=$(terraform output -raw broken_asg_name)
I=$(aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names "$BROKEN" --profile bootcamp --region us-east-1 \
  --query 'AutoScalingGroups[0].Instances[0].InstanceId' --output text)
aws ssm start-session --target "$I" --profile bootcamp --region us-east-1
```

Then, on the instance:

```bash
# IMDSv1 — no token, no auth, just a GET.
curl -s http://169.254.169.254/latest/meta-data/iam/security-credentials/
curl -s http://169.254.169.254/latest/meta-data/iam/security-credentials/<role-name>
```

**Pause on that output.** Real `AccessKeyId`, `SecretAccessKey`, `Token`.

> "That is a plain GET with no headers. Any SSRF bug in your application — a
> URL preview feature, an image proxy, a webhook tester — makes your server
> issue that request and hand the response to an attacker. That is the Capital
> One breach, 2019, a hundred million records."

Now the same thing on the **good** instance:

```bash
curl -s http://169.254.169.254/latest/meta-data/iam/security-credentials/
# (empty — 401)

# IMDSv2 requires a PUT first
TOKEN=$(curl -sX PUT http://169.254.169.254/latest/api/token \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/instance-id
```

> "One line of Terraform. Costs nothing. Every current AWS SDK already does the
> PUT. There is no reason any launch template in your account allows IMDSv1, and
> the auditor you write today will find the ones that do."

---

## 0:30–0:55 — Part 2: ASGs & scaling policies

### Whiteboard: min / desired / max

Draw three horizontal lines. Ask the room what each does before you tell them.

Then draw the failure: `min = 1`. Cross out the single instance. Ask "what's the
capacity now?" Let the silence sit.

> "This is the most common gap I see in architectures labelled 'highly
> available'. Not a misconfiguration — a misunderstanding. One instance is one
> AZ. There is no availability to be high."

### Target tracking vs step vs simple

Put the decision tree from `diagrams/README.md` on screen. Spend most of the
time on **warm-up vs cooldown** — it is the distinction interviewers use to
separate people who have operated this from people who have read about it.

**The line that lands:**

> "Warm-up filters the signal. Cooldown blocks the response. If you only
> remember one sentence from this hour, that's it."

**Draw the scaling storm.** New instance boots at 100% CPU → drags the average
up → triggers another scale-out → that one boots at 100% too. Draw the spiral.
Then draw the warm-up window over it and show the spike falling outside the
aggregate.

---

## 1:05–1:30 — Part 3: ELB & target groups

The comparison table is reference material — put it up, do not read it aloud.
Three things deserve real time:

1. **NLB cross-zone off by default.** Draw 4 targets in AZ-a, 1 in AZ-b. Ask
   what share the lonely one gets. Almost everyone says 20%. It is 50%.
2. **Listener rule priority.** Lowest number first, first match wins.
3. **Health check on `/` vs `/health`.**

### 🎬 Demo 2 — the ALB is actually balancing (4 min)

```bash
ALB=$(terraform output -raw alb_dns_name)
for i in $(seq 1 12); do curl -s "http://$ALB" | grep -oE 'i-[0-9a-f]+' | head -1; done
```

Two instance IDs alternating. Then open the browser and refresh — the AZ changes
in the card. Visual, instant, no explanation needed.

Then show the target group health table and point at the AZ column.

> "Two targets, two AZs. That column is the difference between a diagram that
> says HA and a system that is."

---

## 1:30–1:50 — Part 4: health checks & self-healing

This is the intellectual core of the day. Slow down.

Put the three-health-signal diagram (`diagrams/README.md` #7) on screen. Walk it:
EC2 system check, EC2 instance check, target group check — and then the ASG
setting that decides which of them it listens to.

### 🎬 Demo 3 — the hung app that never gets replaced (8 min)

**Start this demo now and let it run in the background while you keep talking.**
It needs five minutes of wall clock, and you want the payoff at 1:48, not 1:55.

```bash
# Kill the web server on the BROKEN ASG's instance (health_check_type = EC2)
aws ssm send-command --instance-ids "$I" \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["systemctl stop httpd"]' \
  --profile bootcamp --region us-east-1 --output text --query Command.CommandId
```

Keep teaching. At the five-minute mark, come back:

```bash
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names "$BROKEN" --profile bootcamp --region us-east-1 \
  --query 'AutoScalingGroups[0].Instances[].{Id:InstanceId,Health:HealthStatus,State:LifecycleState}' \
  --output table
```

`Healthy`. `InService`. The app is dead.

> "The instance is serving nothing. The ASG thinks it's fine, because the only
> thing it's watching is whether the kernel is alive. You're paying full price
> for an instance that answers no requests, and this will continue until a human
> intervenes.
>
> `health_check_type = "EC2"` is the AWS default. That default is wrong for
> every load-balanced tier that has ever existed."

Then show the good ASG's config side by side.

**Grace period.** Cover it immediately after, because the correction has a trap:

> "Before anyone goes and flips this to ELB in production tonight — check your
> grace period first. Turn on ELB health checks with a 30-second grace period on
> an app that takes three minutes to start, and every instance gets killed
> mid-boot and replaced by another one that gets killed mid-boot. That bills
> continuously and never converges. I've seen a four-figure weekend from
> exactly this."

---

## 2:00–2:25 — Lab: apply & verify

Point everyone at `lab/README.md` steps 0–3.

**Circulate. The three things that go wrong:**

| Symptom | Cause | Say this |
|---|---|---|
| `apply` sitting on `aws_autoscaling_group` for 5+ min | `min_elb_capacity` waiting for ELB health | "That's deliberate. Terraform won't say success until your instances actually pass the load balancer health check. Wait." |
| ALB name collision | Leftover stack | "Destroy the old one. The random suffix should have prevented this." |
| Targets `unhealthy` after 5 min | Userdata failed | Walk them to `aws ssm start-session` and `/var/log/cbc-bootstrap.log` |

**While people wait for apply,** ask the room to open `terraform output cost_breakdown`
and read the numbers aloud. Repetition on cost is not nagging; it is the habit
you are building.

---

## 2:25–2:45 — 🎬 Demo 4: the chaos test

**This is the moment of the day. Do it as a group, synchronised.**

Get everyone to set up terminals 2 and 3 first (`watch` on target health, and
the curl loop counting failures). Verify everyone has `failed=0` and climbing
`ok=`.

Then, together:

```bash
VICTIM=$(aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names "$(terraform output -raw asg_name)" \
  --profile bootcamp --region us-east-1 \
  --query 'AutoScalingGroups[0].Instances[0].InstanceId' --output text)
aws ec2 terminate-instances --instance-ids "$VICTIM" --profile bootcamp --region us-east-1
```

**Narrate the timeline as it happens.** People will not notice the important
part unless you point at it:

- t+20s: "Target's unhealthy."
- t+35s: "Draining. The ALB has already stopped sending it traffic. Look at your
  curl counter — still climbing."
- t+60s: "New instance launching. Still zero failures."
- t+180s: "Healthy. Back to two."

Then the closing line:

> "You just lost half your compute capacity and the failure counter never moved.
> Nobody paged. Nobody woke up. That is the entire point of Day 03, and you have
> now watched it happen rather than taking a diagram's word for it."

Then read the ASG's own account of it:

```bash
aws autoscaling describe-scaling-activities \
  --auto-scaling-group-name "$(terraform output -raw asg_name)" \
  --profile bootcamp --region us-east-1 --max-records 4 \
  --query 'Activities[].{Time:StartTime,Status:StatusCode,Cause:Description}' --output table
```

> "That `Cause` field is written in plain English. It is the first place you
> look in any real scaling incident, and most people never find it."

---

## 2:45–3:00 — 🎬 Demo 5: scale-out

⚠️ **Start the load immediately.** It takes 3–6 minutes to produce a scale-out.
Kick it off, then fill the wait with the CloudWatch console.

```bash
IDS=$(aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names "$(terraform output -raw asg_name)" \
  --profile bootcamp --region us-east-1 \
  --query 'AutoScalingGroups[0].Instances[].InstanceId' --output text)

aws ssm send-command --instance-ids $IDS \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["nohup stress-ng --cpu 2 --timeout 600s >/dev/null 2>&1 &"]' \
  --profile bootcamp --region us-east-1 --query Command.CommandId --output text
```

While waiting, walk the CloudWatch alarm the target-tracking policy created —
people are always surprised AWS generated it for them.

Then narrate the chain when it fires:

> "CPU crosses 50. Three consecutive one-minute periods. Alarm goes ALARM. Policy
> raises desired capacity. Instance launches — ninety seconds. Boots — sixty
> more. And then it sits outside the metric aggregate for a hundred and eighty
> seconds of warm-up.
>
> That last part is why we set warm-up to 180 and not 30. Set it to 30 and the
> new instance's own boot spike would count toward the average, and you'd
> trigger another scale-out. That's the storm we drew on the whiteboard."

**If it does not fire in time:** show the screenshot you prepared. Do not let
the room sit watching a flat graph.

---

## 3:00–3:15 — `ha_audit.py`

```bash
cd ../python && python3 ha_audit.py --profile bootcamp --region us-east-1
```

Ten-plus findings. Walk the table on screen, then pick **three** to read in full
detail — `ASG-003`, `ASG-011`, `ASG-007`. Do not read all eleven; people stop
listening at four.

Show the score, then show the design decision:

```bash
python3 ha_audit.py --min-severity CRITICAL
```

> "Notice the score didn't change. `--min-severity` filters what you see, not
> what's counted. If filtering improved the score, everyone would just pass
> `--min-severity CRITICAL` and declare victory. A metric you can game is not a
> metric."

Then the CI angle:

```bash
python3 ha_audit.py --fail-on HIGH --quiet; echo "exit code: $?"
```

> "That's your pipeline gate. Exit 1 blocks the merge."

Point at the challenge file and set expectations: 9 TODOs, 75–95 minutes, do not
read the solution first.

---

## 3:15–3:20 — Teardown, confirmed out loud

**Do not let this be a footnote.** Put the cost table on screen.

```bash
cd ../terraform && terraform destroy -auto-approve
```

While it runs (3–5 min), go round the room. Ask each person to say the number
they see in `terraform output estimated_monthly_cost_usd` before it disappears.
Then:

```bash
terraform state list   # must be empty
```

And point them at `teardown-checklist.md`'s one-shot verification script.

> "Destroy reporting success is not proof. Anything you created by hand today
> survives it. Run the verification script. And check Cost Explorer *tomorrow* —
> today's data is 24 hours behind."

---

## Questions you will get

| Question | Short answer |
|---|---|
| "Why not just use Fargate/EKS?" | Often you should. But the ASG/ALB/health-check model *is* the underlying primitive — ECS services and EKS node groups are built on exactly these concepts. Learn it here and the managed versions are obvious. |
| "Can I use Spot?" | Yes — mixed instances policy, typically 70% Spot above an On-Demand baseline. Needs a 2-minute interruption handler. Day 09 covers the cost side. |
| "Why 50% CPU and not 80%?" | Because scale-out takes 3–5 minutes. At 80% you have no headroom to survive the latency of your own scaling. 60–70% average utilisation is the honest production target. |
| "Isn't `most_recent = true` on the AMI dangerous?" | Yes, and the comment in `providers.tf` says so. Production pins via SSM Parameter Store. Lab prefers always-current. |
| "Why one NAT and not one per AZ?" | Cost — three NATs is $97/month. Production uses one per AZ so an AZ failure doesn't kill egress for the survivors. Know the trade-off, state it in interviews. |
| "Do I need `ignore_changes = [desired_capacity]`?" | If you have scaling policies, yes. Otherwise every CI run resets capacity, possibly mid-peak. |
| "What if my app takes 10 minutes to start?" | Raise the grace period, and use a lifecycle hook so the wait is explicit rather than implied. Also: fix the app. |

---

## Common learner mistakes

| Mistake | Fix |
|---|---|
| Reads `ha_audit.py` before attempting the challenge | Say up front that the scaffolding is identical and only the checks differ. Reading it costs them the whole exercise. |
| Terminates an instance from the EC2 console instead of the CLI | Works fine, but they miss the `describe-scaling-activities` step. Redirect them to it. |
| Panics when `apply` takes 6 minutes | Explain `min_elb_capacity` before they start, not after. |
| Fixes findings by setting `create_insecure_examples = false` and calls it done | That is step 7a. Push them to also understand *why* `ASG-008`/`ASG-009` remain. |
| Leaves the stack running "to finish the challenge later" | Have them destroy, and re-apply tomorrow. Seven minutes and $0. |
| Confuses warm-up and cooldown in the recap | Repeat the one-liner. Repeat it again at the end of the day. |

---

## Closing (30 seconds)

> "Day 02 gave you a network. Today you put compute in it that heals itself. You
> terminated an instance on purpose and nobody noticed, which is the highest
> compliment you can pay an architecture.
>
> Tomorrow, Day 04, we stop running compute at all — Lambda, EventBridge, and a
> compliance scanner that runs itself on a schedule and costs about eleven cents
> a month.
>
> Everyone destroyed? Say it out loud."

---

| ← Day README | Lab | Interview Q&A |
|---|---|---|
| [Day 03](README.md) | [lab/](lab/README.md) | [interview-qa.md](interview-qa.md) |

---

*CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp · Learning Made Simple*
