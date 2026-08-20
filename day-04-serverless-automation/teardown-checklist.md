# Day 04 — Cost & Teardown Checklist

**Total cost while running: ~$1.01/month.** Almost nothing. Tear it down
anyway, and — more importantly — **verify** the teardown, because Day 04 is the
day you learn that `terraform destroy` does not remove everything the stack
caused to exist.

> The one thing that survives `destroy`: the CloudWatch log group Lambda
> created for `cbc-day04-broken-function`. Terraform never knew about it, so
> Terraform cannot delete it. It has retention "Never expire". That is not a
> bug in the lab — it is the lesson, and it is section 12's whole point.

---

## What is actually costing money

| Resource | While running | After destroy |
|---|---|---|
| KMS customer-managed key | **$1.00/month** (prorated hourly) | $0 after the deletion window |
| S3 bucket for CloudTrail logs | ~$0.01/month | $0 — **if you emptied it** |
| CloudWatch Logs (scanner, 7-day) | ~$0.00 | $0 |
| CloudWatch Logs (broken fn, **never expire**) | ~$0.00 now, forever | **still billing** unless you delete it |
| Lambda, SNS, SQS, EventBridge, CloudTrail #1 | $0 — permanent free tier | $0 |
| CloudWatch alarms (2) | $0 — first 10 free | $0 |

The KMS key is prorated hourly, so a three-hour session costs about **$0.004**.
Do not let the dollar figure stop you doing the lab; do let it teach you to
check.

---

## Teardown

### 1. Empty the CloudTrail bucket first

S3 will not delete a non-empty bucket, and versioning is on, so "empty" means
versions and delete markers too. Do this **before** `destroy` or the destroy
fails halfway and leaves you with a partially torn-down stack.

```bash
cd lab/terraform
BUCKET=$(terraform output -raw cloudtrail_bucket 2>/dev/null || \
         aws s3 ls --profile bootcamp | grep cbc-day04 | awk '{print $3}')
echo "$BUCKET"

aws s3 rm "s3://$BUCKET" --recursive --profile bootcamp

# Versioned buckets keep versions and delete markers after `rm`.
aws s3api list-object-versions --bucket "$BUCKET" --profile bootcamp \
  --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' \
  --output json > /tmp/versions.json
aws s3api delete-objects --bucket "$BUCKET" --profile bootcamp \
  --delete file:///tmp/versions.json 2>/dev/null || true

aws s3api list-object-versions --bucket "$BUCKET" --profile bootcamp \
  --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' \
  --output json > /tmp/markers.json
aws s3api delete-objects --bucket "$BUCKET" --profile bootcamp \
  --delete file:///tmp/markers.json 2>/dev/null || true
```

### 2. Destroy the stack

```bash
terraform destroy
# Read the plan. 46 resources should be going away.
```

### 3. Confirm Terraform believes it is gone

```bash
terraform state list        # should print nothing
terraform show              # should print "The state file is empty."
```

An empty state is Terraform's opinion, not a fact about your account. Verify it.

---

## Verification — do not skip this

### The orphaned log group (the whole point of today)

```bash
aws logs describe-log-groups --profile bootcamp \
  --log-group-name-prefix "/aws/lambda/cbc-day04" \
  --query 'logGroups[].[logGroupName,retentionInDays,storedBytes]' --output table
```

You should see `/aws/lambda/cbc-day04-broken-function-*` with a **null**
retention. Delete it by hand:

```bash
aws logs delete-log-group --profile bootcamp \
  --log-group-name /aws/lambda/cbc-day04-broken-function-<suffix>
```

Now widen the search to your entire account. Most people find log groups from
labs they ran years ago:

```bash
aws logs describe-log-groups --profile bootcamp \
  --query 'logGroups[?!retentionInDays].[logGroupName,storedBytes]' --output table
```

That command is worth keeping. Run it monthly.

### Lambda functions

```bash
aws lambda list-functions --profile bootcamp \
  --query 'Functions[?starts_with(FunctionName, `cbc-day04`)].FunctionName' \
  --output table
```

### EventBridge rules

Rules with targets cannot be deleted until the targets are removed; if destroy
failed partway you may see leftovers.

```bash
aws events list-rules --profile bootcamp \
  --name-prefix cbc-day04 --query 'Rules[].[Name,State]' --output table

# If any remain:
aws events remove-targets --rule <name> --ids <target-id> --profile bootcamp
aws events delete-rule --name <name> --profile bootcamp
```

### SNS topics and subscriptions

A `PendingConfirmation` subscription cannot be deleted by ARN and disappears
with the topic. Confirm the topic is gone:

```bash
aws sns list-topics --profile bootcamp --query 'Topics[?contains(TopicArn, `cbc-day04`)]'
```

### SQS queues

Deleted queues linger in the API for up to 60 seconds. If a name still appears
immediately after destroy, wait and re-check before panicking.

```bash
aws sqs list-queues --profile bootcamp --queue-name-prefix cbc-day04
```

### CloudTrail trail and S3 bucket

```bash
aws cloudtrail describe-trails --profile bootcamp \
  --query 'trailList[?contains(Name, `cbc-day04`)].[Name,S3BucketName]' --output table

aws s3 ls --profile bootcamp | grep cbc-day04
```

An orphaned bucket full of trail logs is the second-most-common Day 04 leftover
after the log group.

### KMS key — read this before you worry

```bash
aws kms list-aliases --profile bootcamp \
  --query 'Aliases[?contains(AliasName, `cbc-day04`)]'
```

A KMS key is **not deleted immediately**. It enters `PendingDeletion` for the
window you configured (`kms_deletion_window_days`, 7 by default; AWS allows
7–30). This is deliberate — an immediately deletable key is an irreversible
data-loss button.

**You are not billed for a key in PendingDeletion.** Nothing to do but wait.

```bash
aws kms describe-key --key-id <id> --profile bootcamp \
  --query 'KeyMetadata.[KeyState,DeletionDate]'
```

### IAM roles and policies (free, but tidy)

```bash
aws iam list-roles --profile bootcamp \
  --query 'Roles[?starts_with(RoleName, `cbc-day04`)].RoleName' --output table
```

Leaving `cbc-day04-broken-role` behind is genuinely worse than leaving most
things behind: it grants `Action: "*"` on `Resource: "*"` and its assume-role
policy has no `SourceAccount` condition.

---

## The one-shot sweep

Save this as `verify-teardown.sh` and run it after every destroy:

```bash
#!/usr/bin/env bash
# Day 04 teardown verification
PROFILE=${1:-bootcamp}
REGION=${2:-us-east-1}
P="--profile $PROFILE --region $REGION"
FOUND=0

check () {  # $1 = label, $2 = command output
  if [ -n "$2" ] && [ "$2" != "None" ] && [ "$2" != "[]" ]; then
    echo "  ✗ $1:"; echo "$2" | sed 's/^/      /'; FOUND=1
  else
    echo "  ✓ $1 clear"
  fi
}

echo "Day 04 teardown check — $PROFILE / $REGION"

check "Lambda functions" "$(aws lambda list-functions $P \
  --query 'Functions[?starts_with(FunctionName, `cbc-day04`)].FunctionName' --output text)"

check "EventBridge rules" "$(aws events list-rules $P \
  --name-prefix cbc-day04 --query 'Rules[].Name' --output text)"

check "SNS topics" "$(aws sns list-topics $P \
  --query 'Topics[?contains(TopicArn, `cbc-day04`)].TopicArn' --output text)"

check "SQS queues" "$(aws sqs list-queues $P \
  --queue-name-prefix cbc-day04 --query 'QueueUrls' --output text)"

check "CloudTrail trails" "$(aws cloudtrail describe-trails $P \
  --query 'trailList[?contains(Name, `cbc-day04`)].Name' --output text)"

check "S3 buckets" "$(aws s3api list-buckets $P \
  --query 'Buckets[?starts_with(Name, `cbc-day04`)].Name' --output text)"

check "IAM roles" "$(aws iam list-roles --profile $PROFILE \
  --query 'Roles[?starts_with(RoleName, `cbc-day04`)].RoleName' --output text)"

check "CloudWatch alarms" "$(aws cloudwatch describe-alarms $P \
  --alarm-name-prefix cbc-day04 --query 'MetricAlarms[].AlarmName' --output text)"

check "LOG GROUPS (the one that survives destroy)" "$(aws logs describe-log-groups $P \
  --log-group-name-prefix /aws/lambda/cbc-day04 --query 'logGroups[].logGroupName' --output text)"

echo
echo "Account-wide log groups with NO retention (not just Day 04):"
aws logs describe-log-groups $P \
  --query 'logGroups[?!retentionInDays].[logGroupName,storedBytes]' --output table

[ $FOUND -eq 0 ] && echo "✓ Day 04 is clean." || echo "✗ Leftovers above. Delete them."
exit $FOUND
```

---

## Then check the bill

Cost Explorer lags 24 hours; the KMS charge is prorated hourly and will appear
as a fraction of a dollar.

```bash
aws ce get-cost-and-usage --profile bootcamp \
  --time-period Start=$(date -u -d '3 days ago' +%Y-%m-%d),End=$(date -u +%Y-%m-%d) \
  --granularity DAILY --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=SERVICE \
  --query 'ResultsByTime[].Groups[?Metrics.UnblendedCost.Amount != `0`].[Keys[0],Metrics.UnblendedCost.Amount]' \
  --output table
```

What you want to see the week after: **Key Management Service** dropping to
zero, and **CloudWatch** not appearing at all. If CloudWatch is still there,
you left a log group behind — go back two sections.

---

## If you must leave it running

Perfectly reasonable at ~$1/month. Make it cheaper and safer first:

```hcl
# terraform.tfvars
enable_kms_encryption = false   # removes the entire $1.00
enable_scheduled_scan = false   # stops the hourly sweep (still ~$0 either way)
log_retention_days    = 1       # nothing accumulates
create_insecure_examples = false  # removes the admin-wildcard role
```

That last one matters most. `cbc-day04-broken-role` grants administrator
access to a function whose source code is in a public git repository. Leaving
the stack up with `create_insecure_examples = true` is the one genuinely risky
way to end today.

```bash
terraform apply   # 46 → ~24 resources
```

---

## Final checklist

- [ ] CloudTrail S3 bucket emptied, **including versions and delete markers**
- [ ] `terraform destroy` completed with no errors
- [ ] `terraform state list` is empty
- [ ] `verify-teardown.sh` exits 0
- [ ] **The `cbc-day04-broken-function` log group is deleted by hand**
- [ ] Account-wide "no retention" log group sweep run, and acted on
- [ ] KMS key shows `PendingDeletion` (this is correct — no charge)
- [ ] `cbc-day04-broken-role` is gone
- [ ] Cost Explorer checked 48 hours later, KMS back to zero
- [ ] Budget alarm from Day 01 still armed
