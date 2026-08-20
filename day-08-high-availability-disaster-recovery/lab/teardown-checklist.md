# Day 08 — Teardown Checklist

**`terraform destroy` is necessary and is not sufficient.**

Day 08 is the first day in this repo with a real monthly bill (~$83 with the
shipped defaults), and the first with resources in **two regions**. Those two
facts compound: cross-region resources are, by construction, the ones that
survive the thing that was supposed to remove them.

That is true of `terraform destroy` for exactly the same reason it is true of a
regional outage — and it is why the DR region is where forgotten spend
accumulates. Your dashboards are scoped to your primary region. Your budget
alert is account-wide, but your *investigation* of a budget alert starts in the
region you work in.

Work through this in order. It takes about ten minutes and it is the difference
between a $2 lab and a $200 surprise.

---

## 0. Before you destroy: disable the automation

If you left the recovery workflow enabled and it has a real (non-dry-run)
configuration, disable it before you start pulling infrastructure out from
under it. An automation that observes its own teardown as a regional failure is
a funny story you do not want.

```bash
aws ssm put-parameter --name /cbc-day08/<suffix>/recovery-enabled \
  --value disabled --overwrite --profile bootcamp --region us-east-1
```

Also un-invert the Route 53 health check if you inverted it in step 10 or step
7f. An inverted health check that outlives the stack is $0.50/month reporting
permanently unhealthy about an endpoint that no longer exists.

```bash
aws route53 update-health-check --health-check-id <id> --no-inverted
```

---

## 1. `terraform destroy`

```bash
cd lab/terraform
terraform destroy
```

Expect four to six minutes. Two things commonly stall it:

**The ALB has deletion protection.** This stack sets
`enable_deletion_protection = false` deliberately so teardown is clean. In
production it should be `true`, which is exactly why a teardown checklist is
necessary at all.

**A vault lock blocks vault deletion.** If you set `enable_vault_lock = true`,
`force_destroy` on the vault is **ignored** — that is the entire point of a
vault lock. See section 4.

---

## 2. The DR region. Check it explicitly.

`terraform destroy` should remove the DR-region resources, because they are in
the same state file under the `aws.dr` provider alias. **Verify it anyway**,
because a provider alias mistake produces a resource Terraform does not know it
owns.

```bash
# S3 replica bucket
aws s3 ls --profile bootcamp | grep cbc-day08

# Backup vaults
aws backup list-backup-vaults --profile bootcamp --region us-west-2 \
  --query 'BackupVaultList[?starts_with(BackupVaultName,`cbc-day08`)].[BackupVaultName,NumberOfRecoveryPoints]' \
  --output table

# DynamoDB global table replica
aws dynamodb list-tables --profile bootcamp --region us-west-2 \
  --query 'TableNames[?starts_with(@,`cbc-day08`)]' --output table

# Anything at all, by tag — this is what the Region="dr" default tag is for
aws resourcegroupstaggingapi get-resources --profile bootcamp --region us-west-2 \
  --tag-filters Key=Day,Values=08 \
  --query 'ResourceTagMappingList[].ResourceARN' --output table
```

That last command is the one to keep. **The `Region = "dr"` tag on the DR
provider's `default_tags` is the cheapest DR hygiene you will ever buy**: it
makes "show me everything I own in the DR region" a tag query rather than an
archaeology project.

---

## 3. EBS snapshots — `destroy` does NOT remove these

**The single most reliable source of forgotten spend in this repo.**

`aws_ebs_snapshot.stale` is destroyed by Terraform because it is in state. But
any snapshot AWS Backup created, and any snapshot you took by hand during step
6a or step 8, is not.

Snapshots bill per GiB-month, indefinitely. They survive `terraform destroy` of
the volume they came from, they survive the instance, and they survive the
person who took them.

```bash
for R in us-east-1 us-west-2; do
  echo "=== $R ==="
  aws ec2 describe-snapshots --owner-ids self --profile bootcamp --region $R \
    --query 'Snapshots[?starts_with(Description,`cbc-day08`) || Tags[?Key==`Day` && Value==`08`]].[SnapshotId,StartTime,VolumeSize,Description]' \
    --output table
done
```

Delete what you find:

```bash
aws ec2 delete-snapshot --snapshot-id snap-xxxx --profile bootcamp --region us-east-1
```

> **And the trap that catches everybody: deregistering an AMI does NOT delete
> the snapshots behind it.** An AMI is a snapshot with a label. Removing the
> label leaves the storage, billing, forever, under a snapshot id nobody
> recognises. If you built any AMIs today, check for orphans:
>
> ```bash
> aws ec2 describe-snapshots --owner-ids self --profile bootcamp --region us-east-1 \
>   --query 'Snapshots[?starts_with(Description,`Created by CreateImage`)].[SnapshotId,StartTime]' \
>   --output table
> ```

---

## 4. Backup vaults and recovery points

A vault cannot be deleted while it holds recovery points. `force_destroy = true`
handles that — **unless a vault lock is in place, in which case it is
ignored.**

```bash
for R in us-east-1 us-west-2; do
  V=$(aws backup list-backup-vaults --profile bootcamp --region $R \
    --query 'BackupVaultList[?starts_with(BackupVaultName,`cbc-day08`)].BackupVaultName' \
    --output text)
  for VAULT in $V; do
    echo "=== $R / $VAULT ==="
    aws backup list-recovery-points-by-backup-vault --backup-vault-name "$VAULT" \
      --profile bootcamp --region $R \
      --query 'RecoveryPoints[].[RecoveryPointArn,CreationDate]' --output table
  done
done
```

**If you enabled `enable_vault_lock`:** this stack only ever creates a
**governance-mode** lock, which is removable:

```bash
aws backup delete-backup-vault-lock-configuration \
  --backup-vault-name <vault> --profile bootcamp --region us-east-1
```

**If you hand-added `changeable_for_days` anywhere**, you created a
**compliance-mode** lock, and after the cooling-off window it cannot be removed
by you, by root, or by AWS Support. The vault cannot be deleted while it holds
recovery points, and you will pay for that storage until the longest retention
expires.

That is why this stack exposes no variable that could do it. If you did it
anyway, your only lever is time.

---

## 5. Route 53 health checks

**Billed per check per month whether or not the endpoint still exists.**
$0.50 for an AWS endpoint, $0.75 for a non-AWS one, plus ~$1.00 for each
optional feature. Cheap once; $250/month at a hundred forgotten endpoints.

```bash
aws route53 list-health-checks --profile bootcamp \
  --query 'HealthChecks[].[Id,HealthCheckConfig.FullyQualifiedDomainName,HealthCheckConfig.Inverted]' \
  --output table
```

Any row whose FQDN is a load balancer that no longer exists is pure waste.

```bash
aws route53 delete-health-check --health-check-id <id> --profile bootcamp
```

---

## 6. Hosted zones, if you created one

This stack deliberately does **not** create a hosted zone, because a zone for a
domain you do not own resolves for nobody, bills $0.50/month, and survives
teardown because people do not think of zones as resources.

If you created one yourself for step 10, delete the records first, then the
zone.

```bash
aws route53 list-hosted-zones --profile bootcamp \
  --query 'HostedZones[].[Id,Name,ResourceRecordSetCount]' --output table
```

---

## 7. Elastic IPs

A NAT gateway's EIP is released with the gateway. An EIP left **unattached** is
billed at ~$3.65/month, and since February 2024 an in-use public IPv4 address
is billed too.

```bash
for R in us-east-1 us-west-2; do
  aws ec2 describe-addresses --profile bootcamp --region $R \
    --query 'Addresses[?AssociationId==null].[PublicIp,AllocationId]' --output table
done
```

---

## 8. CloudWatch log groups

Terraform creates the chaos and recovery log groups explicitly with 14-day
retention, so they are destroyed. But **a Lambda that creates its own log group
creates it with NEVER EXPIRE**, and if you invoked anything outside Terraform's
knowledge you may have one.

```bash
aws logs describe-log-groups --profile bootcamp --region us-east-1 \
  --log-group-name-prefix /aws/lambda/cbc-day08 \
  --query 'logGroups[].[logGroupName,retentionInDays,storedBytes]' --output table
```

A `null` in the retention column is a log group that will bill forever.

---

## 9. The restored DynamoDB table

Step 8 created `<table>-restored`. A PITR restore creates a **new table**, and
a new table bills like any other.

```bash
aws dynamodb list-tables --profile bootcamp --region us-east-1 \
  --query 'TableNames[?contains(@,`cbc-day08`)]' --output table
```

---

## 10. Step Functions execution history

Free to store, and it is the most valuable artefact of the day. **Export it
before you destroy**, not after — the history goes with the state machine.

```bash
aws stepfunctions list-executions \
  --state-machine-arn <arn> --profile bootcamp --region us-east-1

aws stepfunctions get-execution-history --execution-arn <arn> \
  --profile bootcamp --region us-east-1 \
  --query 'events[].[timestamp,type,stateEnteredEventDetails.name]' \
  --output table > failover-evidence/execution-history.txt
```

`failover-evidence/` is gitignored. **`rto-measurements.md` is not** — that is
the artefact worth keeping, and it should be dated.

---

## 11. Final sweep

```bash
for R in us-east-1 us-west-2; do
  echo "=== $R ==="
  aws resourcegroupstaggingapi get-resources --profile bootcamp --region $R \
    --tag-filters Key=Day,Values=08 \
    --query 'ResourceTagMappingList[].ResourceARN' --output text | tr '\t' '\n'
done
```

Empty output in both regions means you are done. **This only works because
every resource carries the `Day = 08` default tag, including the DR ones** —
which is the argument for tagging discipline made concrete. An untagged DR
resource is exactly the one nobody finds when they go looking for spend six
months later.

---

## 12. Check the bill in 48 hours

Cost Explorer lags by up to a day. Look again on the day after tomorrow,
filtered to the `Day = 08` tag, in **both** regions.

```
Cost Explorer → Filters → Tag → Day = 08 → Group by: Region
```

If the DR region shows a non-zero line after teardown, something is still
there. Come back to section 2.

---

## What this checklist is really teaching

Every item above is a resource that outlives the thing that created it. That is
not a quirk of Terraform; it is the defining property of the resources DR is
built from.

Backups exist to survive the deletion of their source. Cross-region replicas
exist to survive the loss of their region. Vault locks exist to survive an
administrator. Snapshots exist to survive the volume.

**The same durability that makes them useful is what makes them accumulate**,
and the same distance that makes a DR region safe is what makes it invisible.
A teardown checklist is not tidiness — it is the operational half of the same
design decision.
