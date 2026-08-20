# Day 05 — Cost & Teardown Checklist

**CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp**

> **Read this before you run `terraform destroy`, not after it fails.**
>
> It will fail. **Four resources in this lab carry `prevent_destroy`**, and
> that is the seatbelt working, not a bug. There is a right way to handle it
> and a fast way, and the fast way is how production buckets go missing.

---

## Contents

1. [What is actually costing money](#what-is-actually-costing-money)
2. [The four protected resources](#the-four-protected-resources)
3. [The `prevent_destroy` wall — two correct ways through it](#the-prevent_destroy-wall--two-correct-ways-through-it)
4. [Teardown, in order](#teardown-in-order)
5. [Emptying a versioned bucket](#emptying-a-versioned-bucket)
6. [Verification — do not skip this](#verification--do-not-skip-this)
7. [The one-shot sweep](#the-one-shot-sweep)
8. [The costs that are not on the bill](#the-costs-that-are-not-on-the-bill)
9. [If you must leave it running](#if-you-must-leave-it-running)

---

## What is actually costing money

**~$0.05/month with every default left alone.** Day 05 is the cheapest day in
the bootcamp in dollars, and the most expensive in commitments.

| Item | Cost | Notes |
|---|---|---|
| S3 state storage | ~$0.01 | a few hundred KB at $0.023/GB-month |
| S3 requests | ~$0.01 | PUT/GET per plan and apply |
| S3-native locking | $0.00 | a `.tflock` object written and deleted |
| VPC, subnets, IGW, route tables, SGs | $0.00 | always free |
| S3 data bucket + CloudWatch log group | ~$0.02 | at lab volumes |
| Insecure example bucket | ~$0.00 | empty |
| `envs/prod` | $0.00 | gated off by default |
| **Total** | **~$0.05** | |

Everything expensive is off by default and priced in its own variable
description. If you turned any of these on, that is where your money went:

| Toggle | Cost | Where |
|---|---|---|
| `enable_nat_gateway` | **+$32.40/month** each, billed from creation regardless of traffic, plus $0.045/GB | `envs/*` |
| `instances` | +$7.59/month per `t3.micro` on-demand | `envs/*` |
| `enable_kms_encryption` | +$1.00/month for the CMK, plus $0.03/10k requests | `backend-bootstrap` |
| `enable_flow_logs` | +$0.50/month at lab traffic; three figures on a busy VPC | `envs/*` |
| `enable_prod_environment` | doubles the resource count | `envs/prod` |

**The NAT gateway is the one to check first.** It is the only resource in this
lab that can quietly cost more than a coffee, and it bills from the moment it
exists whether anything routes through it or not.

```bash
aws ec2 describe-nat-gateways \
  --filter "Name=state,Values=available" \
  --query 'NatGateways[].[NatGatewayId,VpcId,CreateTime]' \
  --output table --profile bootcamp
```

---

## The four protected resources

`prevent_destroy` is set on every resource in this lab that holds something you
cannot rebuild from code:

| # | Resource | Directory | Why |
|---|---|---|---|
| 1 | `aws_s3_bucket.state` | `backend-bootstrap` | Losing it loses the map between your code and your account |
| 2 | `module.storage.aws_s3_bucket.data` | `envs/dev` | The data bucket. Also `force_destroy = true` in dev only, so the lab can be torn down |
| 3 | `aws_s3_bucket.insecure_state_example` | `envs/dev` | Present so IAC-013 does **not** fire here — this fixture exists to trip IAC-006 and IAC-007 and nothing else |
| 4 | `module.storage.aws_s3_bucket.data` | `envs/prod` | Same as dev, but `force_destroy = false` — emptying a prod bucket should be a separate act by a human who typed the name |

Two more appear if you set `create_data_table = true` in either environment:
the DynamoDB tables carry it too.

Ask Terraform rather than trusting this table:

```bash
cd lab/terraform/envs/dev  && terraform output protected_resources
cd ../prod                 && terraform output protected_resources
```

---

## The `prevent_destroy` wall — two correct ways through it

You will see this:

```
Error: Instance cannot be destroyed

  on ../../modules/storage/main.tf line 36:
  36: resource "aws_s3_bucket" "data" {

Resource module.storage.aws_s3_bucket.data has lifecycle.prevent_destroy set,
but the plan calls for this resource to be destroyed.
```

### Way 1 — you want the data gone

Three steps, deliberately:

```bash
# 1. Remove the lifecycle block from the code.
#    modules/storage/main.tf, or comment it out.

# 2. Apply. This changes NOTHING in AWS — it updates state's record of the
#    lifecycle rules. Read the plan and confirm it says "0 to add, 0 to
#    change, 0 to destroy".
terraform apply

# 3. Now destroy.
terraform destroy
```

Three steps, in daylight, each one reviewable. If this were a real
environment, step 1 would be a pull request.

### Way 2 — you want to keep the resource

```bash
terraform state rm 'module.storage.aws_s3_bucket.data'
terraform destroy          # the rest goes; the bucket is now unmanaged
```

`state rm` makes Terraform **forget** the resource. It does not delete
anything. The bucket keeps existing, keeps billing, and now has nobody
managing it — which is exactly right when you are handing it to another team,
and an accidental way to create an orphan when you are not.

Write down what you orphaned. Then either import it somewhere or delete it out
of band, on purpose.

### ❌ The way that loses production buckets

Deleting the `lifecycle` block because destroy keeps failing and you want it
to stop failing.

It is the same edit as step 1 above. The difference is everything: **it happens
at speed, under pressure, with no review, on a resource you have not thought
about**, and it is usually done by somebody with the best intentions who is
just trying to unblock a pipeline.

The block exists precisely so that this moment is annoying. It is annoying
exactly once — in the situation where it saves you. If you find yourself
deleting one in a hurry, that is the signal to stop and get a second person on
the call, not the signal to type faster.

Real incidents that start this way follow the same script: the destroy fails,
somebody removes the guard to get the pipeline green, the destroy succeeds
completely, and the bucket that had the only copy of something is gone forty
seconds later.

---

## Teardown, in order

**Order matters, and it is not arbitrary.**

```
1. envs/prod          (only if you set enable_prod_environment = true)
2. envs/dev
3. backend-bootstrap  ← LAST. always last.
```

Destroy the bootstrap first and you have deleted the bucket holding dev's and
prod's state. Those environments still exist in AWS; Terraform can no longer
see them. Recovery is `terraform import`, one resource at a time, by hand,
after you have worked out what "one resource" means by reading the console.

### 1. Prod, if you enabled it

```bash
cd lab/terraform/envs/prod
terraform destroy
# → fails on the protected data bucket. Handle it with Way 1 or Way 2 above.
# Note: force_destroy = false here, so an S3 bucket with objects in it will
# also refuse to delete until you empty it. That is deliberate.
```

### 2. Dev

```bash
cd ../dev
terraform destroy
# → fails on TWO protected buckets: the module's data bucket and the
#   insecure example bucket.
```

Confirm what is left:

```bash
terraform state list        # should be empty when you are done
```

### 3. Backend-bootstrap — last, and it needs the extra step

The state bucket is versioned. `terraform destroy` cannot delete a bucket that
still has objects in it, and a versioned bucket keeps objects you thought you
deleted. **Empty it first — see the next section — then:**

```bash
cd ../../backend-bootstrap
# Remove prevent_destroy from main.tf, apply, then:
terraform destroy
```

If you migrated the bootstrap's state into the bucket it created, you now have
the circular dependency the day README warned about: the state describing the
bucket lives in the bucket. Pull the state file down locally
(`terraform init -migrate-state` back to local, or just `aws s3 cp` it and use
a local backend) before you empty anything.

---

## Emptying a versioned bucket

**This is the step people skip, and then wonder why `destroy` still fails.**

`aws s3 rm --recursive` deletes the *current* version of each object. On a
versioned bucket that does not remove the object — it adds a **delete marker**
and keeps every non-current version. The bucket is not empty; it is now
emptier-looking and slightly larger.

You need two passes: versions **and** delete markers.

```bash
BUCKET=cbc-day05-tfstate-abc123          # ← your state bucket
PROFILE=bootcamp

# See what is really in there before you delete it
aws s3api list-object-versions --bucket "$BUCKET" --profile "$PROFILE" \
  --query '{versions: length(Versions), markers: length(DeleteMarkers)}'

# Pass 1 — every non-current and current VERSION
aws s3api list-object-versions --bucket "$BUCKET" --profile "$PROFILE" \
  --output json --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' \
  > /tmp/versions.json
aws s3api delete-objects --bucket "$BUCKET" --profile "$PROFILE" \
  --delete file:///tmp/versions.json

# Pass 2 — every DELETE MARKER
aws s3api list-object-versions --bucket "$BUCKET" --profile "$PROFILE" \
  --output json --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' \
  > /tmp/markers.json
aws s3api delete-objects --bucket "$BUCKET" --profile "$PROFILE" \
  --delete file:///tmp/markers.json

# Confirm
aws s3api list-object-versions --bucket "$BUCKET" --profile "$PROFILE" \
  --query '{versions: length(Versions), markers: length(DeleteMarkers)}'
# → both null or 0
```

`delete-objects` takes at most 1000 keys per call, so on a bucket with a long
history you will loop. If the JSON comes back as `{"Objects": null}` there is
nothing left of that kind and `delete-objects` will error — that is your
signal to stop, not a problem.

**Do not skip the `list-object-versions` before you delete.** On the state
bucket, that command is also the most direct demonstration of the silent-growth
trap: one version per apply, kept forever, on a bucket nobody ever looks at.

---

## Verification — do not skip this

`terraform destroy` reporting success means Terraform believes it is done. It
does not mean the account is clean. Anything created outside Terraform —
including anything you `state rm`'d — is still there.

### The state bucket

```bash
aws s3api list-buckets --profile bootcamp \
  --query 'Buckets[?starts_with(Name, `cbc-day05`)].Name' --output table
```

### NAT gateways — the expensive one

```bash
aws ec2 describe-nat-gateways --profile bootcamp \
  --filter "Name=state,Values=available" \
  --query 'NatGateways[].[NatGatewayId,VpcId]' --output table
```

A NAT gateway keeps billing until it reaches `deleted`, and `deleting` can take
several minutes. Check twice.

### Elastic IPs — free while attached, charged while idle

```bash
aws ec2 describe-addresses --profile bootcamp \
  --query 'Addresses[?AssociationId==null].[PublicIp,AllocationId]' --output table
```

An unassociated EIP costs ~$3.60/month. A destroyed NAT gateway leaves its EIP
behind if the EIP was created outside the module.

### VPCs

```bash
aws ec2 describe-vpcs --profile bootcamp \
  --filters "Name=tag:Project,Values=aws-aiops-bootcamp" "Name=tag:Day,Values=05" \
  --query 'Vpcs[].[VpcId,CidrBlock]' --output table
```

If one survives, something inside it is holding a dependency — usually an ENI
from an instance that is still terminating, or a security group referenced by
another group.

### EC2 instances

```bash
aws ec2 describe-instances --profile bootcamp \
  --filters "Name=tag:Day,Values=05" \
            "Name=instance-state-name,Values=running,stopped,stopping" \
  --query 'Reservations[].Instances[].[InstanceId,State.Name,InstanceType]' \
  --output table
```

**Stopped instances still cost money** — the EBS volume bills at $0.08/GB-month
whether the instance runs or not.

### CloudWatch log groups — the classic orphan

```bash
aws logs describe-log-groups --profile bootcamp \
  --log-group-name-prefix "/aws/cbc-day05" \
  --query 'logGroups[].[logGroupName,retentionInDays,storedBytes]' --output table
```

The drift-target log group is the one to look for. It is free at lab volumes
and it will outlive this project if you leave it.

### KMS key, if you enabled it

```bash
aws kms list-aliases --profile bootcamp \
  --query 'Aliases[?starts_with(AliasName, `alias/cbc-day05`)]' --output table
```

A scheduled-for-deletion CMK **keeps billing $1.00/month for the whole waiting
period** — 7 to 30 days, and 30 is the default. Terraform schedules deletion;
it cannot delete immediately. Set the shortest window you can:

```bash
aws kms schedule-key-deletion --key-id <id> --pending-window-in-days 7 \
  --profile bootcamp
```

Cancel a mistake with `aws kms cancel-key-deletion`.

### DynamoDB tables

```bash
aws dynamodb list-tables --profile bootcamp \
  --query 'TableNames[?contains(@, `cbc-day05`)]' --output table
```

Also worth a look for a **legacy lock table** from an older tutorial —
`terraform-lock`, `tf-state-lock`, and similar. This lab does not create one,
and if you find one, it is somebody else's orphan.

### Orphaned provider caches — on your disk, not in AWS

```bash
find . -type d -name ".terraform" -exec du -sh {} +
```

There are **four** `.terraform/` directories in this lab, each a few hundred
megabytes of provider binaries. Nothing ever cleans them up — not `destroy`,
not `git clean`, because they are gitignored. Most people running this for the
first time find several gigabytes from projects that ended years ago.

```bash
find . -type d -name ".terraform" -prune -exec rm -rf {} +
```

---

## The one-shot sweep

Save as `verify-teardown.sh`, `chmod +x`, run from anywhere.

```bash
#!/usr/bin/env bash
# Day 05 teardown verification — CareerByteCode
# Read-only. Reports; deletes nothing.

set -uo pipefail
PROFILE="${AWS_PROFILE:-bootcamp}"
REGION="${AWS_REGION:-us-east-1}"
PREFIX="cbc-day05"
FOUND=0

echo "============================================================"
echo "  Day 05 teardown verification"
echo "  profile: $PROFILE   region: $REGION   prefix: $PREFIX"
echo "============================================================"

check () {
  local label="$1"; shift
  local result
  result=$("$@" 2>/dev/null)
  if [ -n "$result" ] && [ "$result" != "None" ] && [ "$result" != "[]" ]; then
    echo ""
    echo "  [FOUND] $label"
    echo "$result" | sed 's/^/      /'
    FOUND=$((FOUND + 1))
  else
    echo "  [clean] $label"
  fi
}

check "S3 buckets" \
  aws s3api list-buckets --profile "$PROFILE" \
    --query "Buckets[?starts_with(Name, '$PREFIX')].Name" --output text

check "NAT gateways (EXPENSIVE)" \
  aws ec2 describe-nat-gateways --profile "$PROFILE" --region "$REGION" \
    --filter "Name=state,Values=available,pending" \
    --query 'NatGateways[].NatGatewayId' --output text

check "Unassociated Elastic IPs" \
  aws ec2 describe-addresses --profile "$PROFILE" --region "$REGION" \
    --query 'Addresses[?AssociationId==null].PublicIp' --output text

check "VPCs" \
  aws ec2 describe-vpcs --profile "$PROFILE" --region "$REGION" \
    --filters "Name=tag:Day,Values=05" \
    --query 'Vpcs[].VpcId' --output text

check "EC2 instances" \
  aws ec2 describe-instances --profile "$PROFILE" --region "$REGION" \
    --filters "Name=tag:Day,Values=05" \
              "Name=instance-state-name,Values=running,stopped,stopping" \
    --query 'Reservations[].Instances[].InstanceId' --output text

check "EBS volumes" \
  aws ec2 describe-volumes --profile "$PROFILE" --region "$REGION" \
    --filters "Name=tag:Day,Values=05" \
    --query 'Volumes[].VolumeId' --output text

check "CloudWatch log groups" \
  aws logs describe-log-groups --profile "$PROFILE" --region "$REGION" \
    --log-group-name-prefix "/aws/$PREFIX" \
    --query 'logGroups[].logGroupName' --output text

check "DynamoDB tables" \
  aws dynamodb list-tables --profile "$PROFILE" --region "$REGION" \
    --query "TableNames[?contains(@, '$PREFIX')]" --output text

check "KMS aliases (\$1.00/month each while pending deletion)" \
  aws kms list-aliases --profile "$PROFILE" --region "$REGION" \
    --query "Aliases[?starts_with(AliasName, 'alias/$PREFIX')].AliasName" \
    --output text

check "Security groups" \
  aws ec2 describe-security-groups --profile "$PROFILE" --region "$REGION" \
    --filters "Name=tag:Day,Values=05" \
    --query 'SecurityGroups[].GroupId' --output text

echo ""
echo "------------------------------------------------------------"
if [ "$FOUND" -eq 0 ]; then
  echo "  CLEAN — nothing tagged Day=05 remains."
else
  echo "  $FOUND category(ies) still have resources. See above."
  echo "  NAT gateways and pending-deletion KMS keys are the ones"
  echo "  that cost real money. Deal with those first."
fi
echo "------------------------------------------------------------"

echo ""
echo "Local disk — provider caches nothing ever cleans up:"
find . -type d -name ".terraform" -exec du -sh {} + 2>/dev/null || echo "  none"
```

Anything still tagged `Day=05` after a clean destroy was either created outside
Terraform, `state rm`'d, or is still deleting. Check the third possibility
before assuming the first.

---

## The costs that are not on the bill

Day 05 is cheap in dollars and expensive in commitments. Three of them, and
none appears on this month's invoice.

**1. A state bucket you can never safely delete.** It carries `prevent_destroy`
because losing it means losing the map between your code and your account. It
will outlive this project. Somebody will inherit it, and be afraid of it, and
be right to be.

**2. Versioning quietly retaining every state file version forever.** You *want*
versioning on — it is the rollback path when an apply writes a corrupt state.
The consequence is that every apply writes a new version and keeps the old one,
on a bucket nobody looks at. The `noncurrent_version_expiration` rule in
`backend-bootstrap` is the only thing standing between you and a bucket that
grows for five years. A busy team runs hundreds of applies a month.

```bash
aws s3api list-object-versions --bucket "$BUCKET" --prefix day-05/ \
  --query 'length(Versions)' --profile bootcamp
```

**3. A multi-environment build that doubles every resource.** `envs/prod` is
gated off and creates nothing. Flip `enable_prod_environment = true` and the
whole stack exists a second time — about $0.02/month as configured, because the
default footprint is free. But the moment prod is *real*, prod also wants a
NAT gateway, instances and flow logs. Multi-environment is not expensive
because a VPC costs money. It is expensive because **every toggle you flip, you
now flip twice, and the second one is the one nobody reviews.**

And the fourth, which is not AWS's problem: **orphaned `.terraform/` provider
caches**, four of them in this lab, several hundred megabytes each, on your
disk, forever.

---

## If you must leave it running

If you are continuing to Day 06 tomorrow, leaving Day 05 up is fine — it is
five cents a month. Set the floor deliberately rather than by accident:

```bash
# In envs/dev/terraform.tfvars and envs/prod/terraform.tfvars
enable_nat_gateway = false     # the only thing here that costs real money
instances          = {}        # no compute
enable_flow_logs   = false

# In backend-bootstrap/terraform.tfvars
enable_kms_encryption = false  # SSE-S3 is free and still satisfies IAC-007
```

Then confirm rather than assume:

```bash
cd lab/terraform/envs/dev && terraform output cost_breakdown
```

And set a budget alarm if Day 01's is not still in place:

```bash
aws budgets describe-budgets --account-id "$(aws sts get-caller-identity \
  --query Account --output text --profile bootcamp)" --profile bootcamp
```

The state bucket is the thing to keep. Days 06–10 do not need it, but you will
want it if you come back to extend this lab — and re-bootstrapping is five
minutes you do not need to spend twice.

---

**See also:** [`README.md`](README.md) for the cost narrative in context,
[`diagrams/README.md`](diagrams/README.md) §11 for the teardown-order diagram,
and `terraform output protected_resources` in each environment for the
authoritative list of what will block your destroy.
