# Cost Guardrails — read before you launch anything

The #1 way people get hurt in an AWS bootcamp is a NAT Gateway, a forgotten ALB, or an
Auto Scaling group left running for three weeks. This page is your insurance policy.

---

## The five-minute manual budget (do this now)

```
Console → Billing and Cost Management → Budgets → Create budget
  Template: "Monthly cost budget"
  Budget amount: 10 USD          ← pick a number that would genuinely annoy you
  Email recipients: you@example.com
  Create
```

AWS will alert you at 85% actual, 100% actual, and 100% forecasted spend.

> Day 1's Terraform lab replaces this with a *managed, versioned* budget. Keep the manual one
> anyway — belt and braces.

---

## Enable IAM access to billing data

By default, non-root IAM identities cannot see billing — even admins. Turn it on once:

```
Root sign-in → Account (top-right) → IAM user and role access to Billing Information
  ✅ Activate IAM Access → Update
```

---

## What actually costs money in this bootcamp

| Service | Free tier? | Real cost if left running | Days used |
|---|---|---|---|
| **NAT Gateway** | ❌ No free tier | **~$32/month + data** ⚠️ biggest trap | 02, 03, 08 |
| Application Load Balancer | ❌ | ~$16/month + LCU | 03, 08 |
| EC2 `t3.micro` / `t2.micro` | ✅ 750 hrs/mo, 12 months | ~$8/month each after | 03, 08 |
| Elastic IP (unattached) | ❌ | ~$3.60/month each | 02, 03 |
| EBS volumes (orphaned) | ✅ 30 GB | ~$0.08/GB/month | 03, 08 |
| GuardDuty | ✅ 30-day trial | Usage-based, small in a lab | 07 |
| Security Hub | ✅ 30-day trial | Per-check, small in a lab | 07 |
| CloudWatch Logs | ✅ 5 GB ingest | $0.50/GB after | 06, 10 |
| Lambda | ✅ 1M req/month | Effectively free in labs | 04, 07, 08, 10 |
| Amazon Bedrock | ❌ | Per-token, cents per lab | 06, 09, 10 |
| S3 | ✅ 5 GB | Pennies | 05 |
| Secrets Manager | ❌ | $0.40/secret/month | 07 |

**Rule of thumb:** if it has an hourly rate and an endpoint, it bills whether or not you use it.

---

## Non-negotiable habits

1. **Tag everything.** Every resource in this repo carries:
   `Project=aws-aiops-bootcamp`, `Day=NN`, `ManagedBy=terraform`.
2. **`terraform destroy` at the end of every session.** Not tomorrow. Today.
3. **Verify, don't assume.** Run the day's `teardown-checklist.md`.
4. **One region.** Resources hiding in `eu-west-1` are resources you will never find.
5. **Check the console every Monday.** Billing → Cost Explorer → group by Service.

---

## Universal "what did I leave running?" sweep

Run this whenever you finish a session. It checks the usual suspects in your region.

```bash
export AWS_PROFILE=bootcamp AWS_REGION=us-east-1

echo "--- Running EC2 instances ---"
aws ec2 describe-instances \
  --filters Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].[InstanceId,InstanceType,Tags[?Key==`Name`].Value|[0]]' \
  --output table

echo "--- NAT Gateways (💸 the expensive one) ---"
aws ec2 describe-nat-gateways \
  --filter Name=state,Values=available \
  --query 'NatGateways[].[NatGatewayId,VpcId]' --output table

echo "--- Load Balancers ---"
aws elbv2 describe-load-balancers \
  --query 'LoadBalancers[].[LoadBalancerName,Type,State.Code]' --output table

echo "--- Unattached Elastic IPs ---"
aws ec2 describe-addresses \
  --query 'Addresses[?AssociationId==null].[PublicIp,AllocationId]' --output table

echo "--- Available (orphaned) EBS volumes ---"
aws ec2 describe-volumes --filters Name=status,Values=available \
  --query 'Volumes[].[VolumeId,Size,CreateTime]' --output table

echo "--- Non-default VPCs ---"
aws ec2 describe-vpcs --filters Name=isDefault,Values=false \
  --query 'Vpcs[].[VpcId,CidrBlock,Tags[?Key==`Name`].Value|[0]]' --output table
```

Everything above should be **empty** at the end of a session, except resources you have
deliberately decided to keep for tomorrow's lab.

Save it as a shortcut:

```bash
# add to ~/.bashrc
alias aws-sweep='bash ~/AWS-Cloud-AIOPS-BootCamp/docs/scripts/sweep.sh'
```

---

## Find everything tagged by this bootcamp (any service)

```bash
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=Project,Values=aws-aiops-bootcamp \
  --query 'ResourceTagMappingList[].ResourceARN' --output table
```

This is the single most useful teardown command in the repo. It crosses service boundaries and
catches the things `terraform destroy` missed because you created them by hand.

---

## If you see an unexpected bill

1. **Cost Explorer → group by Service, then by Region** — find *what* and *where*.
2. Delete it.
3. AWS Support → **Account and billing support** case. First-time, honest lab mistakes are very
   often forgiven as a one-time courtesy credit. Be polite and specific.
