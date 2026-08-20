# Day 10 — Teardown Checklist

**Priority: this teardown lands on a lab account. On a shared account,
mistakes are visible and expensive.** Delete the reference-arch (if
you enabled it) FIRST, then empty the archive bucket, then run the
full destroy. `tofu destroy` handles everything if allowed to run to
completion.

---

## Priority 1 — the expensive thing (only if you enabled it)

### 1.1 Reference-arch — ~$210/month while it exists

If you set `enable_reference_arch = true` in step 10 of the lab and
did not turn it off before `tofu destroy`, this is the loudest
recurring line item.

```bash
# Preview what would go away
aws ec2 describe-instances --profile bootcamp --region us-east-1 \
  --filters 'Name=tag:Project,Values=careerbytecode-aws-bootcamp' 'Name=tag:Day,Values=10' \
  --query 'Reservations[].Instances[?State.Name!=`terminated`].[InstanceId,InstanceType]'

# Disable it via variable, apply the delta, then continue.
cd lab/terraform
# Edit terraform.tfvars: enable_reference_arch = false
tofu apply
```

This is preferable to a single `tofu destroy` because the module has
25 resources with cross-dependencies, and destroying in a controlled
apply cycle is faster and safer than dependency-resolved destroy.

**Why priority 1:** ~$7/day. Every hour it exists is money. If your
`tofu destroy` fails partway through, this is what you want gone
first.

---

## Priority 2 — empty the archive bucket

`tofu destroy` will fail on a non-empty S3 bucket by default. The
archive contains:

- Every JSON report the runner wrote (`reports/day=NN/.../*.json`).
- The `suppressions.yaml` you uploaded in step 5.
- If you turned on Athena, the `queries/` prefix with Athena result
  cache.

Empty it before the destroy:

```bash
BUCKET="$(cd lab/terraform && tofu output -raw archive_bucket_name)"
aws s3 rm --recursive --profile bootcamp "s3://$BUCKET/"

# If versioning is on, also delete all versions:
aws s3api list-object-versions --profile bootcamp --bucket "$BUCKET" \
  --query 'Versions[].[Key,VersionId]' --output text | \
  while read key version; do
    [ -n "$key" ] && aws s3api delete-object --profile bootcamp --bucket "$BUCKET" \
      --key "$key" --version-id "$version"
  done

# Delete delete markers too:
aws s3api list-object-versions --profile bootcamp --bucket "$BUCKET" \
  --query 'DeleteMarkers[].[Key,VersionId]' --output text | \
  while read key version; do
    [ -n "$key" ] && aws s3api delete-object --profile bootcamp --bucket "$BUCKET" \
      --key "$key" --version-id "$version"
  done
```

The Terraform stack uses `force_destroy = true` on the archive bucket
which should handle the emptying automatically, but the manual step
above works around cases where the versioned-object destroy hangs
(a known Terraform provider issue on some regions).

---

## Priority 3 — the ambient audit infrastructure

Everything below is destroyed cleanly by `tofu destroy`. This section
is for verifying nothing is left behind.

### 3.1 EventBridge rule + target + Lambda permission

```bash
aws events list-rules --profile bootcamp --region us-east-1 \
  --query 'Rules[?starts_with(Name, `cbc-day10-`)].[Name,ScheduleExpression,State]'
```

Expected: empty after destroy.

### 3.2 Lambda function + IAM role + inline policy

```bash
aws lambda list-functions --profile bootcamp --region us-east-1 \
  --query 'Functions[?starts_with(FunctionName, `cbc-day10-`)].[FunctionName,Runtime]'

aws iam list-roles --profile bootcamp \
  --query 'Roles[?starts_with(RoleName, `cbc-day10-`)].[RoleName]'
```

Both should be empty.

### 3.3 CloudWatch log group

Terraform deletes it, but Lambda re-creates it on first invocation if
you re-apply. Confirm gone:

```bash
aws logs describe-log-groups --profile bootcamp --region us-east-1 \
  --log-group-name-prefix /aws/lambda/cbc-day10- \
  --query 'logGroups[].logGroupName'
```

### 3.4 CloudWatch alarm

```bash
aws cloudwatch describe-alarms --profile bootcamp --region us-east-1 \
  --alarm-name-prefix cbc-day10- \
  --query 'MetricAlarms[].AlarmName'
```

### 3.5 CloudWatch dashboard

```bash
aws cloudwatch list-dashboards --profile bootcamp --region us-east-1 \
  --dashboard-name-prefix cbc-day10- \
  --query 'DashboardEntries[].DashboardName'
```

### 3.6 SNS topic

```bash
aws sns list-topics --profile bootcamp --region us-east-1 \
  --query 'Topics[?contains(TopicArn, `cbc-day10`)]'
```

If this doesn't come back empty and you try to re-apply, Terraform
will refuse because the topic name is taken. Delete manually:

```bash
aws sns delete-topic --profile bootcamp --region us-east-1 \
  --topic-arn arn:aws:sns:us-east-1:...:cbc-day10-alarms
```

### 3.7 Athena database + workgroup

```bash
aws athena list-work-groups --profile bootcamp --region us-east-1 \
  --query 'WorkGroups[?starts_with(Name, `cbc-day10-`)].[Name]'

aws athena list-databases --profile bootcamp --region us-east-1 \
  --catalog-name AwsDataCatalog \
  --query 'DatabaseList[?starts_with(Name, `cbc_day10_`)].[Name]'
```

Both should be empty. Athena workgroups sometimes hang on delete if
they have query history; `force_destroy = true` on the resource block
handles this.

---

## Priority 4 — the archive bucket itself

Last, because bucket deletion requires the bucket be empty and all
versioning cleaned up.

```bash
aws s3 ls --profile bootcamp | grep cbc-day10-archive
```

If the bucket is still there after `tofu destroy`, it's because the
versioning cleanup did not complete. Repeat step 2, then:

```bash
aws s3 rb --force --profile bootcamp "s3://$BUCKET/"
```

---

## The end-to-end tear-down

```bash
# 1. From lab/terraform.
cd lab/terraform

# 2. Disable reference-arch if it was on. Apply. Wait.
# Edit terraform.tfvars: enable_reference_arch = false
tofu apply

# 3. Empty the archive.
BUCKET="$(tofu output -raw archive_bucket_name)"
aws s3 rm --recursive --profile bootcamp "s3://$BUCKET/"

# 4. Full destroy.
tofu destroy

# 5. Verify all sections above return empty.
```

---

## The audit against your own tear-down

The last step of the tear-down is to re-run the auditor without the
archive:

```bash
cd lab/python
python capstone_audit.py --profile bootcamp --region us-east-1
```

If the auditor produces CAP-001 (no schedule) and CAP-009 (no alarm)
against a different runner in your account, that runner belongs to a
different Day 10 deployment or another engineer's work. Track it
down; don't leave orphaned audit-runners.

If the auditor produces nothing because it can't find the archive
bucket, you're done. That's the correct end state.
