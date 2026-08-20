# Day 09 — Teardown Checklist

**Priority: this teardown lands on a lab account. On a shared account,
mistakes are visible and expensive.** Delete the loudest recurring
charges first, so that if the destroy fails partway through, the residue
is quiet.

Every resource in this lab was created by Terraform. `tofu destroy` will
handle it if allowed to run to completion. What this checklist adds:

- The order in which you should verify each resource is actually gone,
  because `terraform destroy` reports success for a resource whose
  destroy call returned 200 without checking whether the AWS API
  eventually removed the underlying object.
- A manual fallback for each resource whose Terraform destroy can fail
  transiently.
- The specific "you thought you deleted it and you did not" gotchas.

---

## Priority 1 — the recurring charges to kill FIRST

**Do these before anything else** even if you plan to keep the rest of
the stack around. Every hour these exist is money.

### 1.1 Classic Load Balancer — ~$16.20/month

```bash
# Preview
aws elb describe-load-balancers --profile bootcamp --region us-east-1 \
  --query 'LoadBalancerDescriptions[?starts_with(LoadBalancerName, `cbc-day09`)].[LoadBalancerName]' \
  --output text

# Terraform will delete it correctly. Manual fallback:
aws elb delete-load-balancer --profile bootcamp --region us-east-1 \
  --load-balancer-name cbc-day09-classic
```

**Why priority 1:** Classic ELBs bill for the base hour whether or not
anything talks to them. $16.20/month per ELB is the loudest per-hour
line item this stack can produce. If your Terraform destroy fails and
you close the tab, this is what will still be charging you a month
later.

### 1.2 Elastic IPs (both associated and orphan) — $3.60/month EACH

```bash
# Preview
aws ec2 describe-addresses --profile bootcamp --region us-east-1 \
  --query 'Addresses[?not_null(AllocationId)].[AllocationId,AssociationId,PublicIp]' \
  --output table

# Terraform will release them. Manual fallback (one at a time):
aws ec2 release-address --profile bootcamp --region us-east-1 \
  --allocation-id eipalloc-xxxxxxxxxxxxxxxxx
```

**Why priority 1:** Since February 2024, unattached EIPs bill at
$0.005/hour ($3.60/month) EACH. This stack creates two orphan EIPs by
default. The AWS API sometimes returns transient errors on release, and
Terraform destroys are not always retried automatically, so this is a
common residue.

### 1.3 NAT gateway (if you enabled it in step 5) — ~$32.85/month

```bash
# Preview
aws ec2 describe-nat-gateways --profile bootcamp --region us-east-1 \
  --query 'NatGateways[?State==`available`].[NatGatewayId,VpcId]' \
  --output table

# Terraform destroys it. Manual fallback:
aws ec2 delete-nat-gateway --profile bootcamp --region us-east-1 \
  --nat-gateway-id nat-xxxxxxxxxxxxxxxxx
```

**Why priority 1:** NAT gateways bill at $0.045/hour ($32.85/month)
plus $0.045/GB processed. Higher-priority than the ELB if you enabled
it. It also takes about a minute to fully delete, so budget for that.

---

## Priority 2 — the accumulating storage lines

These charge per GB or per object. Small today, larger over time.

### 2.1 EBS snapshots (if you created any in step 10)

```bash
# List snapshots this stack might have created.
aws ec2 describe-snapshots --owner-ids self --profile bootcamp --region us-east-1 \
  --query 'Snapshots[?starts_with(Description, `manual snapshot for testing`)].[SnapshotId,StartTime,Description]' \
  --output table

# Terraform does not manage snapshots you took manually. Delete:
aws ec2 delete-snapshot --profile bootcamp --region us-east-1 \
  --snapshot-id snap-xxxxxxxxxxxxxxxxx
```

**Why priority 2:** $0.05/GB/month. Individually small, but they persist
forever if you do not delete them, and this is exactly the residue
`COST-007` catches. Do not leave lab residue that would fire your own
audit.

### 2.2 S3 bucket objects (if any)

```bash
# List objects.
aws s3 ls s3://cbc-day09-artifacts-xxxxxxxx --profile bootcamp

# Empty the bucket before destroying it. Terraform's default behaviour
# is to refuse to destroy a non-empty bucket. Do this manually:
aws s3 rm --recursive s3://cbc-day09-artifacts-xxxxxxxx --profile bootcamp
```

**Why priority 2:** The bucket itself is free; the objects are $0.023/GB/
month at STANDARD (or less if you enabled the lifecycle). Empty first,
then destroy.

### 2.3 CloudWatch log groups

```bash
# List.
aws logs describe-log-groups --profile bootcamp --region us-east-1 \
  --log-group-name-prefix /aws/cbc-day09 \
  --query 'logGroups[].[logGroupName,storedBytes,retentionInDays]' \
  --output table

# Terraform deletes them. Manual fallback:
aws logs delete-log-group --profile bootcamp --region us-east-1 \
  --log-group-name /aws/cbc-day09/app
```

**Why priority 2:** $0.03/GB/month for storage. Zero for empty groups,
non-zero for anything that received traffic.

---

## Priority 3 — the compute layer

The instance bills for as long as it is running. Terminate.

### 3.1 EC2 instances

```bash
# List.
aws ec2 describe-instances --profile bootcamp --region us-east-1 \
  --filters 'Name=tag:Project,Values=cbc-day09' \
  --query 'Reservations[].Instances[].[InstanceId,InstanceType,State.Name,Tags[?Key==`Name`].Value | [0]]' \
  --output table

# Terraform destroys them. Manual fallback:
aws ec2 terminate-instances --profile bootcamp --region us-east-1 \
  --instance-ids i-xxxxxxxxxxxxxxxxx
```

**Why priority 3:** ~$0.01/hour per instance for the shipped defaults.
Small individually, but you cannot destroy the VPC while instances still
attach ENIs.

### 3.2 EBS volumes (root + orphan)

```bash
aws ec2 describe-volumes --profile bootcamp --region us-east-1 \
  --filters 'Name=tag:Project,Values=cbc-day09' \
  --query 'Volumes[].[VolumeId,Size,VolumeType,State]' \
  --output table
```

Terraform will delete anything it created. Root volumes attached to
instances that were terminated with `DeleteOnTermination=true` (the
default in this stack) are gone with the instance. Orphan volumes are
destroyed explicitly.

---

## Priority 4 — the governance objects

These are free to leave, but leaving them means the next lab uses this
day's residue. Clean up.

### 4.1 Cost Anomaly Detection subscription and monitor

```bash
# Subscriptions first (they hold references to the monitor).
aws ce get-anomaly-subscriptions --profile bootcamp --region us-east-1 \
  --query 'AnomalySubscriptions[?starts_with(SubscriptionName, `cbc-day09`)].[SubscriptionArn]' \
  --output text

aws ce delete-anomaly-subscription --profile bootcamp --region us-east-1 \
  --subscription-arn arn:aws:ce::123456789012:anomalysubscription/xxxxxxxxxxxxxxxxx

# Then the monitor.
aws ce get-anomaly-monitors --profile bootcamp --region us-east-1 \
  --query 'AnomalyMonitors[?starts_with(MonitorName, `cbc-day09`)].[MonitorArn]' \
  --output text

aws ce delete-anomaly-monitor --profile bootcamp --region us-east-1 \
  --monitor-arn arn:aws:ce::123456789012:anomalymonitor/xxxxxxxxxxxxxxxxx
```

Terraform will remove both. The manual fallback exists because these
APIs sometimes return `ValidationException` on delete if a subscription
still references a monitor — do subscriptions first, then monitors.

### 4.2 Budget

```bash
aws budgets describe-budgets --profile bootcamp --region us-east-1 \
  --account-id "$(aws sts get-caller-identity --profile bootcamp --query Account --output text)" \
  --query 'Budgets[?starts_with(BudgetName, `cbc-day09`)].[BudgetName]' \
  --output text

# Manual fallback:
aws budgets delete-budget --profile bootcamp --region us-east-1 \
  --account-id "$(aws sts get-caller-identity --profile bootcamp --query Account --output text)" \
  --budget-name cbc-day09-monthly
```

### 4.3 SNS topic and subscription

```bash
# List.
aws sns list-topics --profile bootcamp --region us-east-1 \
  --query 'Topics[?contains(TopicArn, `cbc-day09`)]' \
  --output text

# Manual fallback:
aws sns delete-topic --profile bootcamp --region us-east-1 \
  --topic-arn arn:aws:sns:us-east-1:123456789012:cbc-day09-cost
```

---

## Priority 5 — the networking layer

Terraform will handle everything here in the correct order. This is the
list for verification, not for manual deletion.

- Route table associations
- Route tables
- Internet Gateway (must be detached from VPC before deletion)
- Subnets
- VPC endpoints (if you attached them in step 5)
- Security groups (default is not deletable)
- VPC (must be empty of everything above before deletion)

If Terraform destroy hangs on any of these, the cause is usually:

- **An ENI still attached** to something Terraform did not manage. Look
  in the EC2 console → Network Interfaces filtered by the VPC, and
  detach/delete manually.
- **A default security group with a rule** that references another SG in
  the VPC. Terraform destroys custom SGs but leaves the default one,
  and the default one cannot be deleted while the VPC exists. That is
  fine.

---

## The end-to-end tear-down

```bash
# 1. From the terraform directory.
cd lab/terraform

# 2. Preview.
tofu destroy -target=aws_elb.classic     # if it still exists
tofu destroy -target=aws_eip.orphan_a    # if it still exists
tofu destroy -target=aws_eip.orphan_b    # if it still exists

# 3. Full destroy.
tofu destroy

# 4. Verify. All of these should return empty.
aws ec2 describe-instances --filters 'Name=tag:Project,Values=cbc-day09' --profile bootcamp --region us-east-1 --query 'Reservations[].Instances[?State.Name!=`terminated`].[InstanceId]'
aws ec2 describe-volumes   --filters 'Name=tag:Project,Values=cbc-day09' --profile bootcamp --region us-east-1 --query 'Volumes[].[VolumeId]'
aws ec2 describe-addresses --profile bootcamp --region us-east-1 --query 'Addresses[?Tags && contains(Tags[?Key==`Project`].Value | [0] || `x`, `cbc-day09`)].[AllocationId]'
aws elb describe-load-balancers --profile bootcamp --region us-east-1 --query 'LoadBalancerDescriptions[?starts_with(LoadBalancerName, `cbc-day09`)].[LoadBalancerName]'
aws logs describe-log-groups --profile bootcamp --region us-east-1 --log-group-name-prefix /aws/cbc-day09 --query 'logGroups[].[logGroupName]'
aws budgets describe-budgets --account-id "$(aws sts get-caller-identity --profile bootcamp --query Account --output text)" --profile bootcamp --region us-east-1 --query 'Budgets[?starts_with(BudgetName, `cbc-day09`)].[BudgetName]'
aws ce get-anomaly-monitors --profile bootcamp --region us-east-1 --query 'AnomalyMonitors[?starts_with(MonitorName, `cbc-day09`)].[MonitorName]'
```

Any of the above returning a non-empty result is residue. Follow the
priority-1-through-4 sections to clean it up.

---

## The audit against your own tear-down

The last step of the tear-down is to re-run the auditor without the
prefix filter:

```bash
cd lab/python
python cost_audit.py --profile bootcamp --region us-east-1
```

If any finding names a `cbc-day09` resource, that resource survived the
tear-down. Track it down and delete it.

If no `cbc-day09` finding appears BUT you see findings against OTHER
resources on the account, congratulations — you have found real work.
The Terraform stack was a demonstration, but the auditor was always
about the actual account. This is what "make the tool useful outside
the lab" looks like.
