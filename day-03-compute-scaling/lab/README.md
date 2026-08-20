# Lab — Deploy Highly Available Infrastructure

> Stand up a self-healing, auto-scaling, load-balanced compute tier — then break
> it on purpose and watch it put itself back together.

| | |
|---|---|
| **Duration** | ~95 minutes |
| **Region** | `us-east-1` |
| **Profile** | `bootcamp` |
| **Prefix** | `cbc-day03-` |
| **⚠️ Cost** | **~$0.098/hour · ~$73/month if left running.** ALB + NAT Gateway + EC2. |

> ## ⚠️ Read this first
> Day 03 is the first lab where **doing nothing costs money**. An Application
> Load Balancer bills ~$16.20/month whether or not a single request hits it.
> Add the NAT Gateway (~$32.40/month) and two instances, and a forgotten
> weekend costs roughly **$12**. A forgotten month costs **$73**.
>
> Finish the lab, then run `terraform destroy`. Three hours of this stack is
> about **$0.30**. There is no reason to leave it up.

---

## Contents

| Step | What | Time |
|---|---|---|
| 0 | [Prerequisites](#step-0--prerequisites) | 5 min |
| 1 | [Read the Terraform before you run it](#step-1--read-the-terraform-before-you-run-it) | 10 min |
| 2 | [Apply](#step-2--apply) | 10 min |
| 3 | [Verify the load balancer](#step-3--verify-the-load-balancer) | 5 min |
| 4 | [🔥 Chaos test — kill an instance](#step-4--chaos-test--kill-an-instance) | 15 min |
| 5 | [Trigger a real scale-out](#step-5--trigger-a-real-scale-out) | 15 min |
| 6 | [Run the resilience auditor](#step-6--run-the-resilience-auditor) | 10 min |
| 7 | [Fix the findings](#step-7--fix-the-findings) | 20 min |
| 8 | [Destroy](#step-8--destroy-not-optional) | 5 min |
| ★ | [The challenge](#the-challenge) | 75–95 min |

---

## Step 0 — Prerequisites

```bash
# Credentials work?
aws sts get-caller-identity --profile bootcamp

# Tooling present?
terraform version          # >= 1.5
python3 --version          # >= 3.9
python3 -c "import boto3"  # no output = installed
```

If the last one errors:

```bash
cd python && pip install -r requirements.txt
```

**Set a billing alarm if you have not already.** Day 01 covered this. Today is
the day it earns its keep.

---

## Step 1 — Read the Terraform before you run it

This is not filler. Twenty minutes of reading saves an afternoon of debugging,
and half the interview questions on this topic are answered by these files.

```bash
cd terraform
```

Read in this order:

| File | What to look for |
|---|---|
| `providers.tf` | Why we filter `aws_availability_zones` on `opt-in-status`. Why the AMI is a data source and not a hardcoded ID. |
| `variables.tf` | Every cost-bearing variable says its price in the description. Note the `validation` block that rejects `az_count = 1`. |
| `main.tf` § 4 | The launch template. Find `http_tokens = "required"` and `encrypted = true`. Find the `tag_specifications` blocks and read the comment about why `default_tags` cannot reach them. |
| `main.tf` § 6 | The ASG. Find `health_check_type = "ELB"`. Find `ignore_changes = [desired_capacity]`. |
| `main.tf` § 7 | Three scaling policies. Compare the target-tracking and step-scaling shapes. |
| `main.tf` § 9 | The deliberately broken resources. Every `⚠️ WRONG ON PURPOSE` comment is a real mistake from a real account. |

Then set up your variables:

```bash
cp terraform.tfvars.example terraform.tfvars
# Edit `owner` to your name. Leave everything else at defaults for now.
```

---

## Step 2 — Apply

```bash
terraform init
terraform plan -out=day03.tfplan
```

**Read the plan.** You should see roughly 45–50 resources. Specifically confirm:

- `aws_launch_template.app` has `http_tokens = "required"`
- `aws_autoscaling_group.app` has `health_check_type = "ELB"`
- `aws_launch_template.broken[0]` has `http_tokens = "optional"` — that is the
  deliberate one

```bash
terraform apply day03.tfplan
```

⏱ **This takes 4–7 minutes.** Most of it is the ALB provisioning and the ASG
waiting for `min_elb_capacity` — Terraform will not report success until your
instances actually pass the load balancer health check. That is deliberate: a
green apply means a working service, not just created resources.

When it finishes, read the `next_steps` output. And read this one:

```bash
terraform output estimated_monthly_cost_usd
terraform output cost_breakdown
```

---

## Step 3 — Verify the load balancer

```bash
ALB=$(terraform output -raw alb_dns_name)
echo "http://$ALB"
```

Open it in a browser. You should see a dark card with an instance ID and AZ.

**Now prove it is actually load balancing:**

```bash
for i in $(seq 1 10); do
  curl -s "http://$ALB" | grep -oE 'i-[0-9a-f]+' | head -1
done
```

You should see two different instance IDs alternating. If you only ever see
one, either only one target is healthy or you are hitting a cached DNS answer
for a single ALB node — try again after a few seconds.

**Check target health directly:**

```bash
aws elbv2 describe-target-health \
  --target-group-arn "$(terraform output -raw target_group_arn)" \
  --profile bootcamp --region us-east-1 \
  --query 'TargetHealthDescriptions[].{Id:Target.Id,AZ:Target.AvailabilityZone,State:TargetHealth.State}' \
  --output table
```

Expected: two targets, `healthy`, in two different AZs.

> **If targets are `unhealthy`:** wait two minutes — the grace period is 300s
> and userdata takes a while. If they are still unhealthy after five minutes,
> get a shell and look:
> ```bash
> INSTANCE=$(aws autoscaling describe-auto-scaling-groups \
>   --auto-scaling-group-names "$(terraform output -raw asg_name)" \
>   --profile bootcamp --region us-east-1 \
>   --query 'AutoScalingGroups[0].Instances[0].InstanceId' --output text)
> aws ssm start-session --target "$INSTANCE" --profile bootcamp --region us-east-1
> # then inside:
> sudo tail -50 /var/log/cbc-bootstrap.log
> curl -i localhost/health
> ```
> No SSH key needed. That is the point of the SSM instance profile.

---

## Step 4 — 🔥 Chaos test — kill an instance

**This is the step that makes Day 03 worth doing.** Everything so far was
provisioning. Now find out whether you actually built self-healing.

### 4a. Start watching, in a second terminal

```bash
# Terminal 2 — target health, refreshing every 5 seconds
watch -n 5 "aws elbv2 describe-target-health \
  --target-group-arn '$(terraform output -raw target_group_arn)' \
  --profile bootcamp --region us-east-1 \
  --query 'TargetHealthDescriptions[].{Id:Target.Id,AZ:Target.AvailabilityZone,State:TargetHealth.State}' \
  --output table"
```

```bash
# Terminal 3 — hammer the ALB and count failures
ALB=$(terraform output -raw alb_dns_name)
FAIL=0; OK=0
while true; do
  if curl -sf -m 3 "http://$ALB/health" >/dev/null; then
    OK=$((OK+1))
  else
    FAIL=$((FAIL+1))
  fi
  printf "\rok=%d  failed=%d" "$OK" "$FAIL"
  sleep 1
done
```

### 4b. Kill one

```bash
# Terminal 1
VICTIM=$(aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names "$(terraform output -raw asg_name)" \
  --profile bootcamp --region us-east-1 \
  --query 'AutoScalingGroups[0].Instances[0].InstanceId' --output text)

echo "Terminating $VICTIM"
aws ec2 terminate-instances --instance-ids "$VICTIM" \
  --profile bootcamp --region us-east-1 \
  --query 'TerminatingInstances[].{Id:InstanceId,From:PreviousState.Name,To:CurrentState.Name}' \
  --output table
```

### 4c. Watch what happens

| Time | Terminal 2 (target health) | Terminal 3 (curl loop) |
|---|---|---|
| t+0 | still 2 healthy | ok climbing |
| t+15–30s | victim → `unhealthy` | **still climbing** |
| t+30–45s | victim → `draining` then gone | still climbing |
| t+60s | new target appears, `initial` | still climbing |
| t+150–300s | new target → `healthy` | still climbing |

**The number that matters is `failed=` in terminal 3. It should be 0.**

You just lost 50% of your compute and no user noticed. That is the entire
promise of this architecture, and you have now personally verified it rather
than taking a diagram's word for it.

### 4d. Read the ASG's account of events

```bash
aws autoscaling describe-scaling-activities \
  --auto-scaling-group-name "$(terraform output -raw asg_name)" \
  --profile bootcamp --region us-east-1 \
  --max-records 6 \
  --query 'Activities[].{Time:StartTime,Status:StatusCode,Cause:Description}' \
  --output table
```

You will see `Terminating EC2 instance` followed by
`Launching a new EC2 instance`. The `Cause` field explains the ASG's reasoning
in plain English — it is the first place to look in any real scaling incident.

### 4e. Now do it the wrong way, for contrast

The broken ASG has `health_check_type = "EC2"`. Simulate a hung application on
it rather than a terminated instance:

```bash
BROKEN=$(terraform output -raw broken_asg_name)
BAD_INSTANCE=$(aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names "$BROKEN" \
  --profile bootcamp --region us-east-1 \
  --query 'AutoScalingGroups[0].Instances[0].InstanceId' --output text)

# Stop the web server. The instance stays up; the app is dead.
aws ssm send-command --instance-ids "$BAD_INSTANCE" \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["systemctl stop httpd"]' \
  --profile bootcamp --region us-east-1 --output text --query 'Command.CommandId'
```

Wait five minutes. Check the instance. **It is still there and still
"healthy"** as far as the ASG is concerned, because the EC2 status checks pass
and nothing is watching the application.

That is what `health_check_type = "EC2"` buys you: an instance that serves
nothing, forever, at full price. Screenshot this — it is the best answer you
will ever give to "tell me about a time you found a subtle production issue."

---

## Step 5 — Trigger a real scale-out

Target tracking holds average CPU near 50%. Push it above that.

```bash
ASG=$(terraform output -raw asg_name)
IDS=$(aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names "$ASG" --profile bootcamp --region us-east-1 \
  --query 'AutoScalingGroups[0].Instances[].InstanceId' --output text | tr '\t' ',')

aws ssm send-command --instance-ids ${IDS//,/ } \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["nohup stress-ng --cpu 2 --timeout 600s >/dev/null 2>&1 &"]' \
  --profile bootcamp --region us-east-1 --query 'Command.CommandId' --output text
```

> If `stress-ng` is not installed (NAT was disabled), use a shell busy-loop
> instead:
> ```
> --parameters 'commands=["for i in 1 2; do nohup sh -c \"while :; do :; done\" >/dev/null 2>&1 & done; sleep 600; pkill -f while"]'
> ```

Now watch capacity:

```bash
watch -n 20 "aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names '$ASG' --profile bootcamp --region us-east-1 \
  --query 'AutoScalingGroups[0].{Desired:DesiredCapacity,InService:length(Instances[?LifecycleState==\`InService\`])}' \
  --output table"
```

⏱ **Be patient — 3 to 6 minutes.** The chain is:

1. CloudWatch collects a 1-minute datapoint
2. Target tracking's alarm needs 3 consecutive breaching periods
3. The policy raises `DesiredCapacity`
4. A new instance launches (~90s) and boots (~60s)
5. It stays out of the metric aggregate for the 180s warm-up

**This is why `instance_warmup_seconds` matters.** Set it to 30 and the new
instance's own boot-time CPU spike would count toward the average, triggering
another scale-out, then another. That is the scaling storm the docs warn about,
and now you can explain it from having reasoned through the timing.

When the load stops, scale-in takes ~10–15 minutes. Target tracking is
deliberately conservative about removing capacity.

---

## Step 6 — Run the resilience auditor

```bash
cd ../python
python3 ha_audit.py --profile bootcamp --region us-east-1
```

Expect **10 or more findings**. That is correct — the Terraform seeded them
on purpose.

| Check | Where it comes from |
|---|---|
| `ASG-001` ×2 | `cbc-day03-broken-asg`: min_size 1, and max == desired |
| `ASG-002` | `cbc-day03-broken-asg`: single subnet, single AZ |
| `ASG-003` | `cbc-day03-broken-asg`: `health_check_type = "EC2"` |
| `ASG-004` | `cbc-day03-broken-asg`: 30-second grace period |
| `ASG-005` | `cbc-day03-broken-asg`: no scaling policy attached |
| `ASG-008` | main ALB: no HTTPS listener |
| `ASG-009` | main ALB: HTTP:80 forwards instead of redirecting |
| `ASG-010` | `cbc-day03-broken-nlb`: cross-zone disabled |
| `ASG-011` | `cbc-day03-broken-lt`: `HttpTokens = "optional"` |
| `ASG-012` | `cbc-day03-broken-lt`: unencrypted root volume |
| `ASG-013` | `cbc-day03-broken-asg`: bare `["Default"]` termination policy |

**Read every finding's `Fix` line before touching anything.** The point is not
to get the score up; it is to recognise these shapes when you meet them in an
account nobody documented.

### Try the other outputs

```bash
# Machine-readable, for a dashboard or a ticket-creation script
python3 ha_audit.py --format json --quiet > findings.json
python3 -c "import json;d=json.load(open('findings.json'));print(d['resilience_score'], d['summary'])"

# Spreadsheet, for the security review meeting nobody wants to attend
python3 ha_audit.py --format csv --quiet > findings.csv

# Only the things that matter today
python3 ha_audit.py --min-severity HIGH

# CI gate — non-zero exit blocks the merge
python3 ha_audit.py --fail-on HIGH --quiet ; echo "exit code: $?"
```

> **Note the deliberate design decision:** `--min-severity` filters what is
> *displayed*, but the score always reflects *every* finding. Otherwise people
> "improve" their posture by passing `--min-severity CRITICAL`, and the metric
> becomes theatre.

---

## Step 7 — Fix the findings

Now flip the switch and watch the score move.

```bash
cd ../terraform
```

Edit `terraform.tfvars`:

```hcl
create_insecure_examples = false
```

```bash
terraform plan    # read this diff — it IS the lesson
terraform apply -auto-approve

cd ../python
python3 ha_audit.py --profile bootcamp --region us-east-1
```

You should drop to roughly **2 findings** (`ASG-008` and `ASG-009` — the
missing HTTPS listener), and the score should jump into the 80s.

### Fix the last two, properly

`ASG-008`/`ASG-009` need a real TLS certificate, which needs a domain. If you
own one:

```bash
aws acm request-certificate \
  --domain-name lab.example.com \
  --validation-method DNS \
  --profile bootcamp --region us-east-1
# validate via DNS, then:
```

```hcl
# terraform.tfvars
acm_certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/..."
```

```bash
terraform apply -auto-approve
cd ../python && python3 ha_audit.py --profile bootcamp
```

**Score 100.** If you do not own a domain, that is fine — understand *why* the
finding exists and move on. A finding you understand and consciously accept is
a legitimate outcome; a finding you suppressed to make a number look good is
not.

---

## Step 8 — Destroy (not optional)

```bash
cd ../terraform
terraform destroy -auto-approve
```

⏱ 3–5 minutes. The NAT Gateway and ALB take the longest.

```bash
# Verify nothing survived
aws autoscaling describe-auto-scaling-groups --profile bootcamp --region us-east-1 \
  --query 'AutoScalingGroups[?starts_with(AutoScalingGroupName, `cbc-day03`)].AutoScalingGroupName'

aws elbv2 describe-load-balancers --profile bootcamp --region us-east-1 \
  --query 'LoadBalancers[?starts_with(LoadBalancerName, `cbc-day03`)].LoadBalancerName'

aws ec2 describe-nat-gateways --profile bootcamp --region us-east-1 \
  --filter "Name=tag:Day,Values=03" \
  --query 'NatGateways[?State!=`deleted`].NatGatewayId'
```

All three must return `[]`. Full verification: [`../teardown-checklist.md`](../teardown-checklist.md).

---

## The challenge

Now build the auditor yourself.

```bash
cd ../python/challenge
python3 ha_audit_challenge.py --profile bootcamp --region us-east-1
```

It runs and reports nothing, because every check function returns an empty
list. There are **9 TODOs** with time estimates, hints and CHECKPOINT markers,
plus 3 stretch goals.

**Do not read `../ha_audit.py` first.** The scaffolding is identical — CLI,
`Finding`, paginators, scoring, all three renderers are given. The checks are
the entire exercise, and reading the answer costs you the only part that
teaches anything.

Bring the stack back up first, or you will have nothing to audit:

```bash
cd ../../terraform && terraform apply -auto-approve
```

And take it down again when you are finished.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `apply` hangs at "Still creating... aws_autoscaling_group" | `min_elb_capacity` is waiting for instances to pass ELB health checks | Wait up to 10 min. If it times out, targets are genuinely unhealthy — SSM in and check `/var/log/cbc-bootstrap.log` |
| Targets stuck `unhealthy` | Userdata failed (usually no egress) | Confirm `enable_nat_gateway = true`, or check the app SG allows :80 from the ALB SG |
| Targets stuck `initial` forever | Health check path wrong, or app not listening | `curl localhost/health` on the instance via SSM |
| `curl` to ALB returns 503 | Zero healthy targets | Same as above. `ha_audit.py` reports this as `ASG-007 CRITICAL` |
| ALB name collision on apply | Left-over stack from a previous run | The `random_string` suffix should prevent this; if not, `terraform destroy` the old one |
| Scale-out never happens | Load too low, or `max_size == desired` | Check the CloudWatch alarm state; confirm `asg_max_size > instance_count` |
| `ssm start-session` fails | Instance profile not attached, or no egress to SSM endpoints | Confirm `aws_iam_role_policy_attachment.ssm` applied and NAT is on |
| `destroy` fails on the ALB log bucket | Bucket not empty | `force_destroy = true` is set; re-run destroy |

---

| ← Day README | Diagrams | Teardown |
|---|---|---|
| [Day 03](../README.md) | [diagrams/](../diagrams/README.md) | [teardown-checklist.md](../teardown-checklist.md) |

---

*CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp · Learning Made Simple*
