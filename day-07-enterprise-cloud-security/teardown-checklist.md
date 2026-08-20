# Day 07 — Teardown Checklist

**CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp**

> `terraform destroy` removes this stack's resources and **leaves the two most
> expensive things running**, because GuardDuty and Security Hub are
> account-and-region-level services and this stack only enabled them in one
> region. If you turned them on elsewhere — during a compliance push, or by
> following the "enable everywhere" advice in Part 7 — nothing here will find
> them.

Work through this after `terraform destroy`. The script at the bottom does most
of it, including the cross-region sweep.

---

## Why this day is different, again

| Category | Example | Does `destroy` handle it? |
|---|---|---|
| Normal resources | Lambda, SNS, SQS, SSM, security groups | Yes |
| CloudTrail trails | both of them | Yes |
| **Trail objects in S3** | everything delivered so far | **Only because `force_destroy = true`** |
| **Secrets** | both | **No — they enter a recovery window** |
| **GuardDuty / Security Hub** | in this region | Yes — **in every other region, no** |
| **A quarantine SG still attached** | an instance you isolated in a demo | **No — destroy will FAIL, and that is correct** |

Day 06's uncleanable thing was a custom metric. Day 07's is a **decision**: the
services you enabled in fifteen regions are still enabled, and the only record
that you enabled them is in somebody's memory.

---

## 1. Detach the quarantine group first

**Do this before `destroy`.** If any instance is still using the quarantine
security group, the destroy fails with `DependencyViolation` — which is correct
behaviour and an annoying way to discover it.

```bash
aws ec2 describe-instances \
  --filters "Name=instance.group-name,Values=cbc-day07-quarantine-*" \
  --query 'Reservations[].Instances[].{Id:InstanceId,SGs:SecurityGroups[].GroupName,Tags:Tags[?starts_with(Key, `Security`)]}' \
  --profile bootcamp --region us-east-1
```

Anything that comes back was isolated by the responder and never restored. The
tags carry what it was attached to before:

```bash
aws ec2 describe-tags --filters "Name=resource-id,Values=<instance-id>" \
  --query 'Tags[?Key==`SecurityContainmentPreviousSGs`].Value' \
  --profile bootcamp --region us-east-1 --output text
```

Then put them back:

```bash
aws ec2 modify-instance-attribute --instance-id <id> --groups <sg-a> <sg-b> \
  --profile bootcamp --region us-east-1
```

**This is the step that justifies the tags.** Without them you are
reconstructing security groups from memory, which is the difference between
"reversible in principle" and "reversible".

---

## 2. Destroy

```bash
cd lab/terraform
terraform destroy -auto-approve
```

Expect roughly 50 resources. Two notes on the output:

- **The trail bucket empties**, because `force_destroy = true`. That is
  convenient for a lab and the wrong default for evidence. In production you
  would remove that line and empty it deliberately, with a record of who did.
- **The secrets do not disappear.** They enter a recovery window.

---

## 3. Secrets — the recovery window

```bash
aws secretsmanager list-secrets --include-planned-deletion \
  --filters Key=name,Values=cbc-day07 \
  --query 'SecretList[].{Name:Name,DeleteAt:DeletedDate}' \
  --profile bootcamp --region us-east-1 --output table
```

They will show a `DeletedDate` 7–30 days out. That is correct behaviour, not a
failed destroy, and **you are not billed for a secret pending deletion**.

The practical consequence: you cannot create a new secret with the same name
until the window clears. If you want to re-run the lab today:

```bash
aws secretsmanager delete-secret --secret-id <arn> \
  --force-delete-without-recovery \
  --profile bootcamp --region us-east-1
```

**Never use `--force-delete-without-recovery` on anything real.** "We deleted
the wrong secret" is a far more common incident than "we needed the name back
within a week".

---

## 4. GuardDuty and Security Hub — the expensive part

This stack enabled both **in one region**. If you enabled them elsewhere, they
are still on and still billing.

### This region

`destroy` removes the detector and the Security Hub subscription. Confirm:

```bash
aws guardduty list-detectors --profile bootcamp --region us-east-1 \
  --query 'DetectorIds' --output text

aws securityhub describe-hub --profile bootcamp --region us-east-1 2>&1 | head -2
```

An empty list and an `InvalidAccessException` are the correct answers.

### Every other region — the sweep that matters

```bash
for r in $(aws ec2 describe-regions --profile bootcamp \
             --query 'Regions[].RegionName' --output text); do
  det=$(aws guardduty list-detectors --region "$r" --profile bootcamp \
          --query 'DetectorIds[0]' --output text 2>/dev/null)
  hub=$(aws securityhub describe-hub --region "$r" --profile bootcamp \
          --query 'SubscribedAt' --output text 2>/dev/null || echo "-")
  printf '%-16s guardduty=%-24s securityhub=%s\n' "$r" "${det:--}" "${hub:--}"
done
```

**Decide deliberately rather than deleting reflexively.** There is a real
argument for leaving GuardDuty on everywhere — an attacker with credentials
will happily operate in a region you have never used, and a region with no
detector is a region with no evidence. The trap is not "it is on", it is "it is
on and nobody decided".

If you are turning it off:

```bash
aws guardduty delete-detector --detector-id <id> --region <r> --profile bootcamp
aws securityhub disable-security-hub --region <r> --profile bootcamp
```

**Watch for day 31.** GuardDuty's free trial is 30 days per account per region.
If you enabled it in fifteen regions during this lab, the invoice arrives a
month later for fifteen regions at once. Set a budget alarm now, not then.

---

## 5. The trail bucket

```bash
aws s3 ls | grep cbc-day07
```

Both buckets should be gone. If one survives — the shadow bucket has no
`force_destroy` protections beyond the flag — empty and remove it:

```bash
aws s3 rm s3://<bucket> --recursive --profile bootcamp
aws s3api delete-bucket --bucket <bucket> --profile bootcamp --region us-east-1
```

**Versioned buckets need the versions deleted too**, which `s3 rm --recursive`
does not do. If the delete-bucket fails with `BucketNotEmpty` on a bucket you
just emptied, that is why. The script at the bottom handles it.

---

## 6. The IAM user and its access key

```bash
aws iam list-users --query 'Users[?starts_with(UserName, `cbc-day07`)].UserName' \
  --profile bootcamp --output text
```

`force_destroy = true` on the user means Terraform removes the key and the
inline policy with it. If a user survives, it is because something was attached
outside Terraform.

**And while you are here**, run the sweep this lab exists to teach:

```bash
aws iam list-users --query 'Users[].UserName' --profile bootcamp --output text | \
while read -r u; do
  aws iam list-access-keys --user-name "$u" --profile bootcamp \
    --query "AccessKeyMetadata[?Status=='Active'].[UserName,AccessKeyId,CreateDate]" \
    --output text
done
```

Anything older than ninety days is check SEC-013, and it will still be there
next quarter unless somebody decides otherwise.

---

## 7. The kill switch and the SSM parameter

```bash
aws ssm get-parameter --name /cbc-day07/kill-switch \
  --profile bootcamp --region us-east-1 2>&1 | head -2
```

`ParameterNotFound` is correct. If it survives, it is because `ignore_changes`
kept Terraform from managing its value — the resource itself should still be
destroyed.

---

## 8. What this day actually cost

Nothing here leaves an undeletable artefact the way Day 06's custom metrics
did. What it leaves is **usage already billed**, and the two numbers worth
looking up:

```bash
# Bedrock-style per-usage lookup: check Cost Explorer for the day you ran this.
aws ce get-cost-and-usage \
  --time-period Start=$(date -u -d '7 days ago' +%Y-%m-%d),End=$(date -u +%Y-%m-%d) \
  --granularity DAILY --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=SERVICE \
  --profile bootcamp --region us-east-1 \
  --query 'ResultsByTime[].Groups[?contains(Keys[0], `Guard`) || contains(Keys[0], `Security`)]' \
  2>/dev/null || echo "(Cost Explorer API not enabled — use the console)"
```

For one afternoon on a quiet account this is cents, and mostly inside the free
trial. The reason to look is not this bill — it is to know what the shape looks
like before you enable it on something with real traffic.

---

## One-shot verification

Save as `verify-teardown.sh`, `chmod +x`, run after `terraform destroy`.

```bash
#!/usr/bin/env bash
# Day 07 teardown verification.
#
# Exits non-zero if anything remains. The cross-region sweep is the part that
# matters — it is the only section that finds the thing `terraform destroy`
# structurally cannot.

set -uo pipefail

PROFILE="${AWS_PROFILE:-bootcamp}"
REGION="${AWS_REGION:-us-east-1}"
PREFIX="cbc-day07"
PROBLEMS=0

aws() { command aws --profile "$PROFILE" "$@"; }

say()  { printf '\n=== %s\n' "$1"; }
bad()  { printf '  !! %s\n' "$1"; PROBLEMS=$((PROBLEMS + 1)); }
good() { printf '  ok  %s\n' "$1"; }
note() { printf '  ??  %s\n' "$1"; }

say "1. Instances still wearing the quarantine security group"
STUCK=$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=instance.group-name,Values=${PREFIX}-quarantine-*" \
  --query 'Reservations[].Instances[].InstanceId' --output text 2>/dev/null)
if [ -n "$STUCK" ]; then
  for i in $STUCK; do
    bad "instance $i is still isolated — destroy will fail with DependencyViolation"
    PREV=$(aws ec2 describe-tags --region "$REGION" \
      --filters "Name=resource-id,Values=$i" \
      --query 'Tags[?Key==`SecurityContainmentPreviousSGs`].Value' --output text)
    printf '      previous groups were: %s\n' "${PREV:-UNKNOWN — reconstruct by hand}"
    printf '      aws ec2 modify-instance-attribute --instance-id %s --groups %s --region %s\n' \
      "$i" "$(echo "$PREV" | tr ',' ' ')" "$REGION"
  done
else
  good "no instances left in quarantine"
fi

say "2. Lab resources in $REGION"
for check in \
  "lambda|list-functions|Functions[?starts_with(FunctionName, \`$PREFIX\`)].FunctionName" \
  "events|list-rules|Rules[?starts_with(Name, \`$PREFIX\`)].Name" \
  "sns|list-topics|Topics[?contains(TopicArn, \`$PREFIX\`)].TopicArn"
do
  svc="${check%%|*}"; rest="${check#*|}"; op="${rest%%|*}"; q="${rest#*|}"
  left=$(aws "$svc" "$op" --region "$REGION" --query "$q" --output text 2>/dev/null)
  for item in $left; do bad "$svc remains: $item"; done
done
CT=$(aws cloudtrail describe-trails --region "$REGION" \
  --query "trailList[?starts_with(Name, \`$PREFIX\`)].Name" --output text 2>/dev/null)
for t in $CT; do bad "trail remains: $t"; done
[ -z "$CT" ] && good "no lab trails remain"

say "3. Secrets in the recovery window"
PENDING=$(aws secretsmanager list-secrets --region "$REGION" --include-planned-deletion \
  --filters Key=name,Values="$PREFIX" \
  --query 'SecretList[].Name' --output text 2>/dev/null)
if [ -n "$PENDING" ]; then
  for s in $PENDING; do
    note "secret pending deletion: $s"
  done
  printf '      This is correct, not a failure. You are NOT billed for these.\n'
  printf '      You cannot reuse the names until the window clears.\n'
else
  good "no secrets pending deletion"
fi

say "4. S3 buckets"
BUCKETS=$(aws s3api list-buckets --query "Buckets[?starts_with(Name, \`$PREFIX\`)].Name" \
  --output text 2>/dev/null)
for b in $BUCKETS; do
  bad "bucket remains: $b"
  printf '      Versioned — delete the versions, not just the objects:\n'
  printf '      aws s3api delete-objects --bucket %s --delete "$(aws s3api list-object-versions \\\n' "$b"
  printf '        --bucket %s --query \x27{Objects: Versions[].{Key:Key,VersionId:VersionId}}\x27)"\n' "$b"
done
[ -z "$BUCKETS" ] && good "no lab buckets remain"

say "5. IAM users and the kill switch"
USERS=$(aws iam list-users --query "Users[?starts_with(UserName, \`$PREFIX\`)].UserName" \
  --output text 2>/dev/null)
for u in $USERS; do bad "iam user remains: $u"; done
[ -z "$USERS" ] && good "no lab iam users remain"

if aws ssm get-parameter --name "/$PREFIX/kill-switch" --region "$REGION" >/dev/null 2>&1; then
  bad "kill switch parameter remains: /$PREFIX/kill-switch"
else
  good "kill switch parameter is gone"
fi

say "6. GuardDuty and Security Hub — EVERY region"
printf '  This is the section terraform destroy structurally cannot do.\n\n'
ENABLED=0
for r in $(aws ec2 describe-regions --query 'Regions[].RegionName' --output text 2>/dev/null); do
  det=$(aws guardduty list-detectors --region "$r" --query 'DetectorIds[0]' \
          --output text 2>/dev/null)
  hub=$(aws securityhub describe-hub --region "$r" --query 'SubscribedAt' \
          --output text 2>/dev/null)
  if [ -n "$det" ] && [ "$det" != "None" ]; then
    ENABLED=$((ENABLED + 1))
    printf '  ??  %-16s guardduty=%s securityhub=%s\n' "$r" "$det" "${hub:-off}"
  elif [ -n "$hub" ] && [ "$hub" != "None" ]; then
    ENABLED=$((ENABLED + 1))
    printf '  ??  %-16s guardduty=off securityhub=%s\n' "$r" "$hub"
  fi
done
if [ "$ENABLED" -eq 0 ]; then
  good "neither service is enabled in any region"
else
  printf '\n      %s region(s) still have detection enabled.\n' "$ENABLED"
  printf '      That may be CORRECT — an attacker will happily use a region you\n'
  printf '      never do, and a region with no detector has no evidence. The trap\n'
  printf '      is not that it is on; it is that nobody decided.\n'
  printf '      GuardDuty is free for 30 days PER REGION. If you enabled several\n'
  printf '      today, the invoice arrives for all of them at once next month.\n'
fi

say "7. Long-lived access keys, account-wide"
printf '  Not scoped to this lab on purpose. This is the sweep worth running monthly.\n'
aws iam list-users --query 'Users[].UserName' --output text 2>/dev/null | tr '\t' '\n' | \
while read -r u; do
  [ -z "$u" ] && continue
  aws iam list-access-keys --user-name "$u" \
    --query "AccessKeyMetadata[?Status=='Active'].[UserName,AccessKeyId,CreateDate]" \
    --output text 2>/dev/null
done | while read -r user key created; do
  [ -z "${user:-}" ] && continue
  printf '      %-40s %s  created %s\n' "$user" "$key" "$created"
done

printf '\n'
if [ "$PROBLEMS" -eq 0 ]; then
  printf 'TEARDOWN CLEAN — 0 deletable item(s) remaining.\n'
  printf 'Sections marked ?? need a DECISION, not a delete.\n'
  exit 0
else
  printf 'TEARDOWN INCOMPLETE — %s item(s) remaining. Commands are printed above.\n' "$PROBLEMS"
  exit 1
fi
```

---

## Final check

```bash
./verify-teardown.sh
```

Clean output means every deletable thing is gone. It does **not** mean you are
finished: the `??` sections are the ones that need a decision rather than a
command, and on this day those are the expensive ones.

**Before you close the laptop:** if you enabled GuardDuty or Security Hub in
regions you do not normally use, write down that you did, and where. The most
expensive artefact of this day is a set of enabled services whose only record
is somebody's memory.
