# `modules/storage`

An application data bucket, an optional DynamoDB table, and a log group with
retention actually set.

The point of this module in Day 05 is not S3. It is **`prevent_destroy`, and
what happens when you mean it.**

---

## Usage

```hcl
module "storage" {
  source = "../../modules/storage"

  name_prefix       = "cbc-day05-dev"
  enable_versioning = true

  noncurrent_version_expiration_days = 30
  abort_incomplete_upload_days       = 7

  create_data_table = false
}
```

---

## Inputs

| Name | Type | Default | Cost | Description |
|---|---|---|---|---|
| `name_prefix` | `string` | — | — | Prefix; a 6-char random suffix is appended to the bucket name. |
| `enable_versioning` | `bool` | `true` | free to enable, unbounded to ignore | Keep every object version. |
| `noncurrent_version_expiration_days` | `number` | `30` | — | The valve on the above. |
| `abort_incomplete_upload_days` | `number` | `7` | — | Kills invisible, billable failed uploads. |
| `force_destroy` | `bool` | `false` | — | Let destroy empty a non-empty bucket. Loaded gun. |
| `create_data_table` | `bool` | `false` | ~$0.00 idle | A DynamoDB table, on-demand billing. |
| `enable_point_in_time_recovery` | `bool` | `false` | $0.20/GB-mo | Continuous backups, 35-day window. |
| `log_retention_days` | `number` | `7` | — | Never leave retention unset. |

---

## Outputs

| Name | Description |
|---|---|
| `bucket_name` / `bucket_arn` | The data bucket |
| `bucket_regional_domain_name` | For things wanting an endpoint |
| `versioning_enabled` | Whether versioning is on |
| `table_name` / `table_arn` | `null` when `create_data_table = false` |
| `log_group_name` | Log group with explicit retention |
| `protected_resources` | Everything carrying `prevent_destroy` |
| `estimated_monthly_cost_usd` | Monthly cost as configured |
| `cost_breakdown` | Line by line, including the silent-growth items |

---

## `prevent_destroy`, and the rule you cannot design around

Both stateful resources in this module carry:

```hcl
lifecycle {
  prevent_destroy = true
}
```

`terraform destroy` on an environment that uses this module **will fail** — at
plan time, before touching anything:

```
Error: Instance cannot be destroyed

  on ../../modules/storage/main.tf line 41:
  41: resource "aws_s3_bucket" "data" {

Resource module.storage.aws_s3_bucket.data has lifecycle.prevent_destroy set,
but the plan calls for this resource to be destroyed.
```

That is the desired behaviour. It is also the thing that will trip you up at
teardown — deliberately. Step 9 of the lab is dealing with it *on purpose*, and
[`teardown-checklist.md`](../../../../teardown-checklist.md) has the procedure.

### The rule: it takes a literal, not a variable

```hcl
prevent_destroy = var.protect_data   # hard error
```

```
Error: Variables not allowed
Variables may not be used here.
```

`lifecycle` is evaluated before variables are resolved, so there is no toggle
and there never will be one. You either mean it, or you remove the block and
apply that removal as a reviewed change.

### The failure mode this is actually about

An engineer runs `terraform destroy` on staging. It fails on `prevent_destroy`.
They are in a hurry. They delete the `lifecycle` block, apply, and destroy —
and it works. Three weeks later somebody runs the same play against a directory
that turns out to be prod, and the seatbelt is not there any more, because it
was removed as a one-line "unblock the pipeline" commit that nobody reviewed.

**The correct sequence, every time:**

1. Remove the `lifecycle` block in a commit that says so, on its own.
2. `terraform apply` — this changes nothing in AWS; it only updates state so
   Terraform stops refusing.
3. `terraform destroy`.

Or, when you want the resource to *survive* the destroy:

```bash
terraform state rm 'module.storage.aws_s3_bucket.data'
terraform destroy
```

Terraform forgets the bucket; the bucket keeps existing; you now own it by
hand. That is a legitimate answer and you should write down that you did it.

---

## Why `PAY_PER_REQUEST` and not `PROVISIONED`

Provisioned capacity on a table nobody is using bills for capacity nobody is
using. The 25 WCU / 25 RCU free tier is the single most common source of "I
thought DynamoDB was free" — it is per account, not per table, and the third
table you create is the one that starts costing money.

On-demand: $1.25 per million writes, $0.25 per million reads, $0.25/GB-month
of stored rows. An idle table bills for its rows and nothing else.

**This is not a Terraform state lock table.** Locking in this lab is S3-native
(`use_lockfile`); see
[`backend-bootstrap/main.tf`](../../backend-bootstrap/main.tf) for why the lock
table is now legacy.

---

## The two ways this bucket grows while you sleep

| Trap | Why you never notice | Fix |
|---|---|---|
| Non-current object versions | Versioning keeps every overwrite forever; the console object list shows only current versions | `noncurrent_version_expiration` |
| Incomplete multipart uploads | They do **not appear in the object listing at all**, and are billed as storage indefinitely | `abort_incomplete_multipart_upload` |

Both rules are in `main.tf`. Both are three lines. Both are missing from most
buckets you will ever inherit.

---

## When *not* to use this module

If you need a bucket for static website assets, or a CloudFront origin, or a
data lake with partitioned prefixes and Glue catalogues — this is not that
module, and bending it into one by adding six more booleans is how modules turn
into configuration languages nobody can read.

Write a second module. Two clear modules beat one clever one.
