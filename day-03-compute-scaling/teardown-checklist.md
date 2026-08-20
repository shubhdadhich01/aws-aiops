# Day 03 — Cost & Teardown Checklist

> ## 🔴 STOP. Day 03 bills you every hour it exists.
>
> Day 02 introduced the NAT Gateway. Day 03 adds an **Application Load Balancer
> and running EC2 instances** on top of it. Unlike IAM policies and security
> groups, none of this is free at rest.
>
> | If you forget for... | You pay roughly |
> |---|---|
> | 1 hour | $0.10 |
> | Overnight (12 h) | $1.18 |
> | A weekend | $7.06 |
> | A week | $16.46 |
> | A month | **$73.00** |
>
> **The whole teardown is one command. There is no excuse.**
>
> ```bash
> cd day-03-compute-scaling/lab/terraform && terraform destroy -auto-approve
> ```

---

## What is actually costing money

| Resource | Rate (us-east-1) | Free tier? | Bills when idle? |
|---|---|---|---|
| **Application Load Balancer** | $0.0225/h + $0.008/LCU-h | 750 h/mo for 12 months | **Yes — full base rate at zero traffic** |
| **NAT Gateway** | $0.045/h + $0.045/GB | ❌ Never | **Yes** |
| **EC2 t3.micro** ×2 | $0.0104/h each | 750 h/mo total for 12 months | Yes |
| **Broken NLB** (`create_insecure_examples`) | $0.0225/h | ❌ | **Yes** |
| **EBS gp3 root** ×2–3 | $0.08/GB-month | 30 GB for 12 months | Yes |
| **Elastic IP** (attached to NAT) | $0 while attached | — | Only if left unattached ($0.005/h) |
| **CloudWatch detailed monitoring** | $0.30/metric/month | 10 metrics | Yes |
| Security groups, subnets, route tables, IAM roles, launch templates | $0 | — | No |

**The two that surprise people:** an ALB with zero requests still bills the full
base rate, and a NAT Gateway has never had a free tier and never will.

---

## Teardown

### 1. Destroy the stack

```bash
cd day-03-compute-scaling/lab/terraform
terraform destroy
```

Read the plan. It should destroy roughly 45–50 resources. Then confirm.

⏱ **3–5 minutes.** The NAT Gateway (~2 min) and ALB (~2 min) dominate. Do not
Ctrl-C — a half-destroyed stack is worse than either state.

```bash
terraform destroy -auto-approve   # if you have already read the plan once
```

### 2. Confirm Terraform believes it is gone

```bash
terraform state list
```

Must return **nothing**. If anything remains, run `destroy` again — transient
dependency ordering occasionally leaves one resource behind.

---

## Verification — do not skip this

`terraform destroy` reporting success is not proof. Anything created outside
Terraform (a manual test instance, an instance launched by a suspended ASG)
survives it.

### Auto Scaling Groups

```bash
aws autoscaling describe-auto-scaling-groups \
  --profile bootcamp --region us-east-1 \
  --query 'AutoScalingGroups[?starts_with(AutoScalingGroupName, `cbc-day03`)].{Name:AutoScalingGroupName,Desired:DesiredCapacity}' \
  --output table
```
✅ Expected: empty.

### Load balancers — the expensive one

```bash
aws elbv2 describe-load-balancers \
  --profile bootcamp --region us-east-1 \
  --query 'LoadBalancers[?starts_with(LoadBalancerName, `cbc-day03`)].{Name:LoadBalancerName,Type:Type,State:State.Code}' \
  --output table
```
✅ Expected: empty. **Check this one twice.**

### Target groups

```bash
aws elbv2 describe-target-groups \
  --profile bootcamp --region us-east-1 \
  --query 'TargetGroups[?starts_with(TargetGroupName, `cbc-day03`)].TargetGroupName' \
  --output table
```
✅ Expected: empty. (Free, but orphaned target groups clutter the account.)

### NAT Gateways — the other expensive one

```bash
aws ec2 describe-nat-gateways \
  --profile bootcamp --region us-east-1 \
  --filter "Name=tag:Day,Values=03" \
  --query 'NatGateways[?State!=`deleted`].{Id:NatGatewayId,State:State}' \
  --output table
```
✅ Expected: empty. `deleted` entries linger in the API for hours — that is
fine, they do not bill. Anything in `available` or `pending` does.

### Running instances

```bash
aws ec2 describe-instances \
  --profile bootcamp --region us-east-1 \
  --filters "Name=tag:Day,Values=03" "Name=instance-state-name,Values=running,pending,stopping,stopped" \
  --query 'Reservations[].Instances[].{Id:InstanceId,State:State.Name,Type:InstanceType}' \
  --output table
```
✅ Expected: empty.

> ⚠️ **`stopped` instances still cost money** — the EBS root volume bills at
> $0.08/GB-month whether the instance runs or not. Terminate, do not stop.

### Unattached Elastic IPs

```bash
aws ec2 describe-addresses \
  --profile bootcamp --region us-east-1 \
  --query 'Addresses[?AssociationId==null].{IP:PublicIp,Alloc:AllocationId}' \
  --output table
```
✅ Expected: empty. An unattached EIP costs $0.005/h (~$3.60/month) — AWS
charges you specifically for hoarding IPv4.

Release any strays:
```bash
aws ec2 release-address --allocation-id eipalloc-xxxxx --profile bootcamp --region us-east-1
```

### Orphaned EBS volumes

```bash
aws ec2 describe-volumes \
  --profile bootcamp --region us-east-1 \
  --filters "Name=status,Values=available" \
  --query 'Volumes[].{Id:VolumeId,Size:Size,Created:CreateTime}' \
  --output table
```
✅ Expected: empty. `available` means detached and billing for nothing.

### The VPC itself

```bash
aws ec2 describe-vpcs \
  --profile bootcamp --region us-east-1 \
  --filters "Name=tag:Day,Values=03" \
  --query 'Vpcs[].{Id:VpcId,Cidr:CidrBlock}' --output table
```
✅ Expected: empty. A VPC is free, but a lingering one means something inside it
blocked deletion — and that something may not be.

### Launch templates and IAM roles (free, but tidy)

```bash
aws ec2 describe-launch-templates --profile bootcamp --region us-east-1 \
  --query 'LaunchTemplates[?starts_with(LaunchTemplateName, `cbc-day03`)].LaunchTemplateName' --output table

aws iam list-roles --profile bootcamp \
  --query 'Roles[?starts_with(RoleName, `cbc-day03`)].RoleName' --output table
```

### CloudWatch alarms (free at this count, but they clutter)

```bash
aws cloudwatch describe-alarms --profile bootcamp --region us-east-1 \
  --alarm-name-prefix cbc-day03 \
  --query 'MetricAlarms[].AlarmName' --output table
```

---

## The one-shot sweep

Copy-paste this. It checks everything above and tells you plainly whether you
are clean.

```bash
#!/usr/bin/env bash
# Day 03 teardown verification
P="--profile bootcamp --region us-east-1"
CLEAN=1
check () {
  local label="$1"; shift
  local out
  out=$(eval "$@" 2>/dev/null)
  if [ -z "$out" ] || [ "$out" = "[]" ] || [ "$out" = "None" ]; then
    printf "  ✅ %-28s clean\n" "$label"
  else
    printf "  ❌ %-28s STILL EXISTS: %s\n" "$label" "$out"
    CLEAN=0
  fi
}

echo "Day 03 teardown verification"
echo "----------------------------"
check "Auto Scaling Groups" "aws autoscaling describe-auto-scaling-groups $P --query 'AutoScalingGroups[?starts_with(AutoScalingGroupName,\`cbc-day03\`)].AutoScalingGroupName' --output text"
check "Load balancers"      "aws elbv2 describe-load-balancers $P --query 'LoadBalancers[?starts_with(LoadBalancerName,\`cbc-day03\`)].LoadBalancerName' --output text"
check "Target groups"       "aws elbv2 describe-target-groups $P --query 'TargetGroups[?starts_with(TargetGroupName,\`cbc-day03\`)].TargetGroupName' --output text"
check "NAT Gateways"        "aws ec2 describe-nat-gateways $P --filter Name=tag:Day,Values=03 --query 'NatGateways[?State!=\`deleted\`].NatGatewayId' --output text"
check "Running instances"   "aws ec2 describe-instances $P --filters Name=tag:Day,Values=03 Name=instance-state-name,Values=running,pending,stopped --query 'Reservations[].Instances[].InstanceId' --output text"
check "Unattached EIPs"     "aws ec2 describe-addresses $P --query 'Addresses[?AssociationId==\`null\`].AllocationId' --output text"
check "Available volumes"   "aws ec2 describe-volumes $P --filters Name=status,Values=available --query 'Volumes[].VolumeId' --output text"
check "Day 03 VPC"          "aws ec2 describe-vpcs $P --filters Name=tag:Day,Values=03 --query 'Vpcs[].VpcId' --output text"
check "Launch templates"    "aws ec2 describe-launch-templates $P --query 'LaunchTemplates[?starts_with(LaunchTemplateName,\`cbc-day03\`)].LaunchTemplateName' --output text"
echo "----------------------------"
[ "$CLEAN" = "1" ] && echo "All clear. Nothing from Day 03 is billing." \
                   || echo "⚠️  Something survived. Fix it before you close the laptop."
```

Save it as `verify-teardown.sh`, `chmod +x`, run it.

---

## Then check the bill

Terraform state and API queries tell you what exists. Cost Explorer tells you
what you were actually charged — and it is the only one that catches something
created in a region you forgot about.

```bash
aws ce get-cost-and-usage \
  --time-period Start=$(date -u -d '2 days ago' +%Y-%m-%d),End=$(date -u +%Y-%m-%d) \
  --granularity DAILY --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=SERVICE \
  --profile bootcamp \
  --query 'ResultsByTime[].{Date:TimePeriod.Start,Costs:Groups[?Metrics.UnblendedCost.Amount!=`0`].{S:Keys[0],A:Metrics.UnblendedCost.Amount}}'
```

> Cost Explorer data lags by up to 24 hours. Check again tomorrow. Today's
> reading is not proof of anything.

Console equivalent: **Billing → Cost Explorer → filter by tag `Day = 03`**.

---

## If you must leave it running

Sometimes you want to come back to the lab tomorrow. Cheapest configuration
that still teaches something:

```hcl
# terraform.tfvars
enable_nat_gateway         = false   # -$32.40/mo  (breaks package installs)
create_insecure_examples   = false   # -$24.00/mo  (removes NLB + 1 instance)
enable_detailed_monitoring = false   # -$2.10/mo
instance_count             = 1       # -$7.49/mo   (and it is no longer HA)
```

```bash
terraform apply -auto-approve
terraform output estimated_monthly_cost_usd
```

That gets you to roughly **$24/month** — still not free, because the ALB is
irreducible. **Destroying and re-applying tomorrow takes seven minutes and costs
nothing.** That is almost always the better answer.

### Scale to zero without destroying

If you want to keep the VPC and ALB but stop paying for compute:

```bash
aws autoscaling update-auto-scaling-group \
  --auto-scaling-group-name "$(terraform output -raw asg_name)" \
  --min-size 0 --desired-capacity 0 \
  --profile bootcamp --region us-east-1
```

⚠️ Terraform will fight you on the next `apply` for `min_size` (only
`desired_capacity` has `ignore_changes`). And the ALB still bills $16.20/month.
This buys you very little. Just destroy it.

---

## Final checklist

- [ ] `terraform destroy` completed without errors
- [ ] `terraform state list` returns nothing
- [ ] No `cbc-day03-` Auto Scaling Groups
- [ ] **No `cbc-day03-` load balancers** (ALB and the broken NLB)
- [ ] No `cbc-day03-` target groups
- [ ] No NAT Gateways in `available` or `pending`
- [ ] No running, pending or stopped instances tagged `Day=03`
- [ ] No unattached Elastic IPs
- [ ] No `available` (detached) EBS volumes
- [ ] Day 03 VPC gone
- [ ] Billing alarm from Day 01 still armed
- [ ] Cost Explorer checked **tomorrow**, not just today

---

| ← Day README | Lab | Trainer notes |
|---|---|---|
| [Day 03](README.md) | [lab/](lab/README.md) | [trainer-notes.md](trainer-notes.md) |

---

*CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp · Learning Made Simple*
