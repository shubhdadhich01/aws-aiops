# Day 05 Lab — Provision AWS Infrastructure using Terraform

**CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp**

Bootstrap the S3 backend, then stand up **dev and prod from the same three
local modules** with different tfvars. Then demonstrate drift end to end:
change a tag in the console → `terraform plan` detects it → fix it three ways
and explain when each is correct.

| | |
|---|---|
| **Time** | ~2h 25m core · +2h for the challenge |
| **Cost** | ~$0.05/month with every default left alone |
| **Region** | `us-east-1` · profile `bootcamp` · prefix `cbc-day05-` |
| **Audit** | **13 findings** static · **15** live · **16** after Step 6 · score **0/100** |

| Step | What | Time |
|---|---|---|
| [0](#step-0--prerequisites) | Prerequisites | 10 min |
| [1](#step-1--bootstrap-the-backend) | Bootstrap the backend | 15 min |
| [2](#step-2--wire-dev-to-the-backend-and-apply) | Wire dev to the backend and apply | 20 min |
| [3](#step-3--read-your-own-state-file) | **Read your own state file** | 10 min |
| [4](#step-4--watch-the-lock-exist) | Watch the lock exist | 10 min |
| [5](#step-5--stand-up-prod-from-the-same-modules) | Stand up prod from the same modules | 15 min |
| [6](#step-6--detect-drift-and-fix-it-three-ways) | **Detect drift, and fix it three ways** | 25 min |
| [7](#step-7--run-the-auditor) | Run the auditor | 20 min |
| [8](#step-8--the-challenge) | The challenge *(optional)* | ~2 h |
| [9](#step-9--destroy-and-verify) | Destroy and verify | 20 min |

Steps 3 and 6 are the lab. Everything else is scaffolding for them.

---

## Step 0 — Prerequisites

**~10 minutes.**

### Versions matter today, unusually

```bash
terraform version        # ≥ 1.10   (or: tofu version, ≥ 1.8)
aws --version            # v2
python3 --version        # ≥ 3.9
```

**The 1.10 floor is not decoration.** `use_lockfile` — S3-native state locking
— landed in Terraform 1.10 and needs AWS provider ≥ 5.80, and every backend in
this lab uses it. On an older binary the argument is **silently ignored**:
`init` succeeds, `apply` succeeds, and you have no locking at all. Check the
version before you debug anything else today.

```bash
aws sts get-caller-identity --profile bootcamp
```

### Python side, which needs no AWS account at all

```bash
cd lab/python
pip install -r requirements.txt          # boto3 only
python3 -m unittest discover -s tests
# Ran 47 tests in 0.24s
# OK
```

Those 47 tests run against synthetic fixtures and the real `../terraform`
directory on disk. No credentials, no network. If they fail now, they will
fail later for a reason that has nothing to do with your AWS account.

### Read the fixture before you build anything

```bash
cd ../terraform/bad-examples
cat README.md
```

Every fault in that directory is labelled with the check ID it triggers.
**Nothing in it is ever applied** — no environment references it, there is no
backend, and `terraform apply` there would fail before it did any damage. It
exists to be *parsed* by the auditor in Step 7.

---

## Step 1 — Bootstrap the backend

**~15 minutes.**

The backend cannot create itself. The bucket that holds state has to exist
before any configuration can write state into it, so this one directory runs
on **local state**, deliberately, and says so in code.

```bash
cd lab/terraform/backend-bootstrap
cp terraform.tfvars.example terraform.tfvars
# Set `owner`. Leave enable_kms_encryption = false unless you want to pay
# $1.00/month for a customer-managed key — SSE-S3 is free and still satisfies
# the audit's IAC-007.

terraform init
terraform apply
```

Read the plan before you approve it. You are creating: a bucket, versioning,
server-side encryption, a public access block, ownership controls, a TLS-only
bucket policy, a lifecycle rule expiring non-current versions, and
`prevent_destroy` on the bucket itself.

Capture the bucket name — **you need it in the next step**:

```bash
terraform output -raw state_bucket_name
# cbc-day05-tfstate-a1b2c3
terraform output next_steps
```

### Now look at what is sitting in this directory

```bash
ls -la terraform.tfstate
```

There it is. Local state, on your disk, and it is **correct here and nowhere
else in this lab**. Open `providers.tf` and find the line that says so:

```hcl
  # NO backend block here. See the essay above.
  #
  # iac-audit: allow-local-state
```

The auditor reads that marker and suppresses IAC-005 for this directory.
Every audit tool needs a way to say *"I know, and here is why"* — and the only
good place for that is in the code, greppable, next to the thing being
suppressed, where the next person to change this file will see it. A
`suppressions.yaml` in the repository root is a file nobody reads that quietly
grows.

> **Then what happens to *this* state file?** Three answers, and the day README
> argues them properly: commit it encrypted (git-crypt, SOPS); migrate it into
> the bucket it just created; or leave it on one laptop. The last one is only
> wrong when nobody decided it — which is how the bootstrap directory becomes
> the one nobody dares touch three years later.

---

## Step 2 — Wire dev to the backend and apply

**~20 minutes.**

### Edit the backend block by hand — you have no choice

```bash
cd ../envs/dev
```

Open `backend.tf` and paste your bucket name into the `bucket` line:

```hcl
terraform {
  backend "s3" {
    bucket       = "cbc-day05-tfstate-a1b2c3"   # ← EDIT THIS
    key          = "day-05/dev/terraform.tfstate"
    region       = "us-east-1"
    profile      = "bootcamp"
    encrypt      = true
    use_lockfile = true
  }
}
```

**You cannot use a variable here.** Not "should not" — cannot. Terraform reads
the backend block before it has evaluated anything else in the configuration,
so `bucket = var.state_bucket` is a hard error. Try it once, read the error,
and you will remember it forever.

The other legal option, which is how CI does it and how one repository serves
several accounts:

```bash
terraform init \
  -backend-config="bucket=cbc-day05-tfstate-a1b2c3" \
  -backend-config="key=day-05/dev/terraform.tfstate"
```

### Apply

```bash
cp terraform.tfvars.example terraform.tfvars
# Set `owner`. Leave create_insecure_examples = true — Step 7 needs it.

terraform init
terraform apply
```

`envs/dev/main.tf` is almost entirely module calls, and that is the point. The
environment directory answers *"what does dev look like"*; the modules answer
*"how is a network built"*. Mixing the two is how a repository ends up with
three subtly different VPCs nobody can diff.

### Prove the state is actually remote

```bash
ls terraform.tfstate           # should NOT exist
terraform state list | head
```

If `terraform.tfstate` is sitting in this directory, `init` did not pick up
`backend.tf` and you are running on local state. Go back and check the bucket
name.

```bash
terraform output next_steps
terraform output cost_breakdown
```

---

## Step 3 — Read your own state file

**~10 minutes. This is the step that changes how you work.**

```bash
aws s3 cp s3://<your-state-bucket>/day-05/dev/terraform.tfstate - \
  --profile bootcamp --region us-east-1 | head -60
```

Scroll through it. Every attribute of every resource, in plaintext JSON —
including attributes nobody referenced, ARNs, bucket names, subnet IDs. A
complete map of the environment.

There is no password in this one, because this stack does not create one. **Now
imagine an RDS instance in here.** The master password is in that file, in
full, next to the subnet IDs. So is anything `random_password` generated. So is
every variable you passed in from CI.

```bash
# How big is the map you just published to anyone with s3:GetObject?
aws s3 cp s3://<your-state-bucket>/day-05/dev/terraform.tfstate - \
  --profile bootcamp | python3 -c "
import json,sys
s = json.load(sys.stdin)
print('serial   :', s['serial'])
print('lineage  :', s['lineage'])
print('resources:', len(s['resources']))
"
```

Three things follow from this, and they are the rest of the day:

1. **`sensitive = true` does not help.** It hides a value from CLI *output*. It
   does not encrypt it, redact it, or keep it out of this file. It is a display
   setting with a security-sounding name, and it is the single most
   misunderstood feature in Terraform.
2. **`s3:GetObject` on this bucket is a production credential.** Whoever has it
   has read every secret in your estate without touching a single resource —
   with no CloudTrail data event unless you turned them on, and no application
   log at all.
3. **Ask the uncomfortable question:** who has that permission on your state
   bucket at work? Most people do not know. That is the finding.

> Now go back and re-read the audit checks IAC-004, IAC-006 and IAC-007. All
> three exist because of this file.

---

## Step 4 — Watch the lock exist

**~10 minutes. Two terminals.**

Remote state fixes **sharing**. Locking fixes **concurrency**. They are
separate problems, and a shared bucket with no locking is arguably worse than
local state, because now two people can corrupt one file instead of each
corrupting their own.

**Terminal 1**, from `envs/dev`:

```bash
terraform apply -auto-approve
```

**Terminal 2**, while it runs:

```bash
watch -n1 "aws s3 ls s3://<your-state-bucket>/day-05/dev/ --profile bootcamp"
```

You will see `terraform.tfstate.tflock` appear and then vanish. **That object
*is* the lock.** No DynamoDB table involved. Terraform writes it with a
conditional put before it mutates state and deletes it afterwards.

### Prove the contention, if you have a third terminal

```bash
cd lab/terraform/envs/dev && terraform plan
```

```
Error: Error acquiring the state lock
  ID:        9f3c1b2a-...
  Path:      day-05/dev/terraform.tfstate
  Operation: OperationTypeApply
  Who:       you@laptop
  Created:   2026-07-24 09:14:02 UTC
```

That error is the feature. Without it, two applies interleave writes into one
file and AWS obliges both.

> **For a decade this was a DynamoDB table** with a `LockID` hash key. It
> worked. The `dynamodb_table` backend argument is now deprecated, and every
> repository that used it carries a ~$0.25/month table that outlives the
> project by years. **You will inherit these. Do not build new ones.** Know
> what they were for — it is an interview question, and if you are stuck below
> provider 5.80 you have no alternative.

If a run is killed mid-apply the lock object survives and the next run reports
it. `terraform force-unlock <ID>` is the escape hatch — **read the lock info
first**, because force-unlocking a run that is still going is exactly how the
interleaving happens.

---

## Step 5 — Stand up prod from the same modules

**~15 minutes.**

```bash
cd ../prod
```

### First, diff before you build

```bash
diff ../dev/main.tf main.tf
diff ../dev/terraform.tfvars.example terraform.tfvars.example
```

The module blocks are the same three modules, the same shape. **The inputs
differ.** If you ever find yourself diffing two *modules* to see how
environments differ, the repository has already gone wrong.

Look at what prod turns on that dev does not: point-in-time recovery, 90-day
version retention, 30-day log retention, `force_destroy = false`. Every one of
those is a decision, in a file, in a diff, that somebody reviewed. With
workspaces, all four would live in ternaries scattered through shared code, and
the only way to know what prod looks like would be to evaluate every one of
them in your head.

### Apply — but read the gate first

```bash
cp terraform.tfvars.example terraform.tfvars
# enable_prod_environment = false by default, and that is deliberate.
```

Setting it to `true` **doubles the resource count of Day 05 in one apply**. As
configured — no NAT gateway, no instances — that doubling costs about
$0.02/month, because everything in the default footprint is free. The moment
prod is real, prod also wants `enable_nat_gateway` (+$32.40/month), instances
(+$7.59/month each) and probably flow logs.

> **Multi-environment is not expensive because a VPC costs money. It is
> expensive because every toggle you flip, you now flip twice — and the second
> one is the one nobody reviews.**

```bash
# Edit backend.tf with your bucket name, as in Step 2
terraform init
terraform apply
terraform output cost_breakdown        # read `the_doubling` and `the_asymmetry`
```

### Confirm the state files are separate

```bash
aws s3 ls s3://<your-state-bucket>/day-05/ --recursive --profile bootcamp
```

Two keys, one bucket. In a real estate, **prod's state belongs in a bucket in
the prod account** — whoever can read the state file can read prod's secrets,
which is exactly the argument from Step 3 and the strongest reason not to use
workspaces for environments.

### Optional: prove `-target` is a smell

```bash
terraform plan -target=module.network
```

It works. It is also how you end up with a state file that has been applied in
pieces and a plan that no longer matches any commit. `-target` exists for
recovering from a failed apply and for essentially nothing else. If you need it
routinely, your root module is too big — split it.

---

## Step 6 — Detect drift, and fix it three ways

**~25 minutes. This is the lab.**

```bash
cd ../dev
terraform output drift_target_log_group
# /aws/cbc-day05-dev/drift-demo
```

### 6a — Establish the baseline

```bash
terraform plan
# No changes. Your infrastructure matches the configuration.
```

Remember that sentence. You are about to make it untrue, and nothing is going
to tell you.

### 6b — Be the person who changes something in the console

In the AWS console: **CloudWatch → Log groups → `/aws/cbc-day05-dev/drift-demo`
→ Tags → change `CostCentre` from `engineering` to `finance` → Save.**

Do it in the console, not in the `.tf` file. That is the whole exercise.

It takes four seconds. It is a tag. Nobody raises a pull request for a tag.

### 6c — Notice that nothing happens

No alert. No email. Terraform does not know. **Drift detection is not a
background service** — nothing watches your account. It is a plan, and somebody
has to run one.

### 6d — Run one

```bash
terraform plan
```

```
  ~ resource "aws_cloudwatch_log_group" "drift_target" {
      ~ tags = {
          ~ "CostCentre" = "finance" -> "engineering"
        }
    }

Plan: 0 to add, 1 to change, 0 to destroy.
```

What just happened, in order:

1. **Refresh** — Terraform read every managed resource back from the AWS API
   and updated state to match reality. State now says `finance`.
2. **Diff** — it compared that against your configuration, which says
   `engineering`.
3. **Propose** — it offers to put reality back.

### 6e — Fix it three ways, and understand each

**Fix 1 — reconcile. Code wins. The default answer.**

```bash
terraform apply
# ~ tags.CostCentre: "finance" → "engineering"
terraform plan          # No changes.
```

Correct when the console change was unauthorised or a mistake.

**Fix 2 — accept reality. AWS wins.**

Change the tag in the console back to `finance`, then:

```bash
terraform plan -refresh-only
# Would update state to match: CostCentre = "finance"
terraform apply -refresh-only
```

State now agrees with AWS, and **the configuration does not**. This is the trap:

```bash
terraform plan
# ~ tags.CostCentre: "finance" -> "engineering"    ← it is back
```

> **`-refresh-only` is not a fix on its own.** It makes Terraform stop
> complaining without making the code true. If you stop here, the next person
> to run `apply` gets the surprise you deferred — and they will have no idea
> why. Follow it by editing `main.tf` to say `finance` and committing that, or
> you have not finished.

Do that now:

```bash
# In envs/dev/main.tf, change CostCentre to "finance"
terraform plan          # No changes. Now you are done.
```

**Fix 3 — stop caring about that attribute.**

```hcl
resource "aws_cloudwatch_log_group" "drift_target" {
  # ...
  lifecycle {
    ignore_changes = [tags["CostCentre"]]
  }
}
```

```bash
terraform apply
# Change it in the console again. Then:
terraform plan          # No changes. Terraform no longer looks at that tag.
```

Correct when **something else legitimately owns the attribute** — an
autoscaler, a deployment pipeline, a tagging Lambda. Wrong as a way to silence
a diff you do not want to think about, which is what it is usually used for.

### 6f — Put it back, and take the organisational lesson

Revert `main.tf` to `CostCentre = "engineering"`, remove the `ignore_changes`
block, set the console tag to `finance`, and `terraform plan` should show the
drift again. **Leave it drifted** — Step 7 wants it.

> If the same drift keeps recurring, `ignore_changes` is not the fix. The fix
> is that somebody has console write access to production and there is no
> reason for it. Read-only console plus a break-glass role beats any amount of
> `ignore_changes`.

### 6g — The scheduled version, in thirty seconds

```bash
terraform plan -detailed-exitcode ; echo "exit: $?"
# exit: 2
```

```
0   no changes
1   error
2   changes present
```

Nightly, in CI, alert on exit code 2. Three lines of YAML and it catches every
console edit nobody mentioned. **Watch the trap:** 2 means *success, with
changes*. A naive `if [ $? -ne 0 ]` turns every pending change into a red
pipeline, and people stop reading red pipelines.

---

## Step 7 — Run the auditor

**~20 minutes.**

### Static first — no credentials at all

```bash
cd ../../../python
python3 iac_audit.py --path ../terraform
```

```
  INFRASTRUCTURE AS CODE AUDIT
  CareerByteCode · Day 05 · Infrastructure as Code

  Scanned: 7 directory(ies) · 31 .tf file(s) · 50 resource(s) · ...

  CRITICAL   IAC-001   bad-examples::aws_ssm_parameter.…  Hardcoded secret in Terraform configura…
  CRITICAL   IAC-002   bad-examples::provider.aws         Static credentials hardcoded in a provi…
  HIGH       IAC-005   bad-examples::backend              Root module has no backend — state is w…
  ...
  COMPLIANCE SCORE: 0/100   F — do not point this at production data
```

**13 findings. Score 0/100.** Twelve of the sixteen checks need no AWS
credentials, which is why this belongs in a pre-commit hook and on a pull
request.

### Now the contrast that is the actual lesson

```bash
python3 iac_audit.py --path ../terraform/envs/dev
# No findings. COMPLIANCE SCORE: 100/100
```

Same tool. Same sixteen checks. A directory somebody wrote carefully. **All
thirteen findings come from `bad-examples/`**, which is applied by nothing and
exists to be parsed.

Try each of the others — `backend-bootstrap`, `envs/prod`, `modules/network`,
`modules/compute`, `modules/storage`. Every one scores 100/100.

### With credentials — the live checks

```bash
python3 iac_audit.py --path ../terraform --profile bootcamp --region us-east-1 \
  --state-bucket "$(cd ../terraform/envs/dev && terraform output -raw insecure_example_bucket)"
```

**15 findings** — IAC-006 and IAC-007 fire on the deliberately misconfigured
example bucket in `envs/dev`: no versioning, no encryption. Score still 0/100,
because the weights total 126 and the score floors at zero.

And if you left Step 6's drift in place:

**16 findings** — IAC-015 catches the `CostCentre` tag you changed in the
console.

| | Findings | Weights | Score |
|---|---|---|---|
| Static, no credentials | **13** | 106 | 0/100 |
| Live, insecure examples applied | **15** | 126 | 0/100 |
| After Step 6 drift | **16** | 130 | 0/100 |

### Two checks report nothing, on purpose

**IAC-003** — there is no committed `.tfstate` anywhere in this lab and every
`.gitignore` in the tree covers the pattern.

**IAC-004** — this repository does not ship a publicly readable S3 bucket, **not
even as a teaching example**. Being one `terraform apply` away from being a
repository that leaked somebody's data is not a lesson worth the
demonstration. The insecure example bucket in `envs/dev` has a *real* public
access block for exactly this reason — it exists to fire IAC-006 and IAC-007
and nothing else.

Both are fully implemented and fully tested. Prove it:

```bash
python3 -m unittest discover -s tests -v 2>&1 | grep -i "iac_003\|iac_004"
```

> A check set where everything fires teaches you that findings are normal. A
> check set with two deliberate zeroes teaches you that a quiet check is
> evidence.

**IAC-015 also reports nothing on a fresh apply, and it is a different kind of
nothing.** Drift does not exist until somebody changes something outside
Terraform — by definition. It is silent by *situation*, not by design, which
is why Step 6 makes it fire.

### The other output formats

```bash
python3 iac_audit.py --path ../terraform --format json --quiet > findings.json
python3 iac_audit.py --path ../terraform --min-severity HIGH --format csv
python3 iac_audit.py --path ../terraform --fail-on CRITICAL ; echo "exit: $?"
```

Note that `--min-severity HIGH` shows 5 findings and the score **stays 0/100**.
It filters the display, never the score — otherwise anyone could improve their
compliance posture by passing `--min-severity CRITICAL`, which is not an
improvement, it is a habit.

---

## Step 8 — The challenge

**~2 hours. Optional, and the part you learn the most from.**

```bash
cd challenge
python3 iac_audit_challenge.py --path ../../terraform
# 0 findings — every check is stubbed. That is the starting line.
```

Sixteen TODOs, each with the exact fields and regex targets, hints, a time
estimate and a CHECKPOINT telling you which directory it must fire on and
which directories it must stay silent on.

**Use the tests as your feedback loop.** From `lab/python`:

```bash
IAC_AUDIT_MODULE=iac_audit_challenge python3 -m unittest discover -s tests
# 47 tests. 20 failures at the start.
```

No credentials, no account, under a second. Every check has one test proving it
**fires** on bad input and one proving it stays **silent** on good input. Run
them after every TODO and you will know immediately which half you broke.

The silent half is the half people skip, and it is the half that decides
whether anyone keeps using your tool. A check that flags every directory gets
suppressed in week two, at which point it does nothing at all.

Do not read `../iac_audit.py` until you are done. You will learn nothing, and
the checks are the whole exercise.

---

## Step 9 — Destroy and verify

**~20 minutes. Read [`../teardown-checklist.md`](../teardown-checklist.md)
first, not after it fails.**

### It will fail, and that is the seatbelt working

```bash
cd ../../terraform/envs/dev
terraform destroy
```

```
Error: Instance cannot be destroyed
Resource module.storage.aws_s3_bucket.data has lifecycle.prevent_destroy set,
but the plan calls for this resource to be destroyed.
```

**Four resources in this lab carry `prevent_destroy`**: the bootstrap state
bucket, the dev data bucket, the dev insecure example bucket, and the prod data
bucket.

```bash
terraform output protected_resources
```

Two correct ways through it:

```bash
# Route 1 — you want the data gone. Three steps, deliberately.
#   1. Remove the lifecycle block from the code
#   2. terraform apply     (changes nothing in AWS — 0 to add, 0 to change)
#   3. terraform destroy

# Route 2 — you want to keep the resource.
terraform state rm 'module.storage.aws_s3_bucket.data'
terraform destroy          # the rest goes; the bucket is now unmanaged
```

`state rm` makes Terraform **forget**. It deletes nothing. The bucket keeps
existing and keeps billing, with nobody managing it — exactly right when you
are handing it to another team, and an accidental way to create an orphan when
you are not.

> ❌ **The wrong way, which is also the common one:** deleting the lifecycle
> block *because destroy keeps failing and you want it to stop failing*. It is
> the same edit as Route 1 step 1. The difference is everything — it happens at
> speed, under pressure, with no review, on a resource you have not thought
> about. Every incident that starts this way follows the same script: destroy
> fails, somebody removes the guard to unblock a pipeline, destroy succeeds
> **completely**, and the bucket with the only copy of something is gone forty
> seconds later. The person who does it always means well.

### Order matters, and it is not arbitrary

```
1. envs/prod           (if you enabled it)
2. envs/dev
3. backend-bootstrap   ← LAST. always last.
```

Destroy the bootstrap first and you have deleted the bucket holding dev's and
prod's state. Those environments still exist in AWS; Terraform can no longer
see them. Recovery is `terraform import`, one resource at a time, by hand.

### The state bucket is versioned, so `s3 rm` will not empty it

`aws s3 rm --recursive` deletes the *current* version of each object. On a
versioned bucket that adds a **delete marker** and keeps every non-current
version. The bucket is not empty; it is emptier-looking and slightly larger.

Two passes — versions, then delete markers — and the exact commands are in
[`../teardown-checklist.md`](../teardown-checklist.md). Before you delete, look
at what is in there:

```bash
aws s3api list-object-versions --bucket <your-state-bucket> --profile bootcamp \
  --query '{versions: length(Versions), markers: length(DeleteMarkers)}'
```

That number is one of the day's two silent-growth traps: **one state version
per apply, kept forever, on a bucket nobody ever looks at.**

### Verify — Terraform reporting success is not the same as the account being clean

Run the one-shot sweep from the teardown checklist. Anything you `state rm`'d
is still there, still billing, and Terraform no longer knows about it.

The other trap is on your disk:

```bash
find . -type d -name ".terraform" -exec du -sh {} +
```

**Four `.terraform/` directories in this lab**, a few hundred megabytes each of
provider binaries. Nothing ever cleans them up — not `destroy`, not `git clean`,
because they are gitignored.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `terraform init` fails on the backend | `bucket` still says `REPLACE-ME` | `cd ../../backend-bootstrap && terraform output -raw state_bucket_name` |
| `init` succeeds but `terraform.tfstate` appears locally | `backend.tf` not picked up, or Terraform < 1.10 | Check versions first, then re-run `init -reconfigure` |
| No lock object ever appears in Step 4 | Terraform < 1.10 or AWS provider < 5.80 — `use_lockfile` is silently ignored | `terraform version`; `terraform providers` |
| `Error acquiring the state lock` | A cancelled run left the lock | Read the lock info. `force-unlock <ID>` **only** if that run is genuinely dead |
| `bucket = var.x` — "Variables not allowed" | The backend block is read before variables exist | Hardcode it, or use `-backend-config` |
| Step 6 shows no diff | You edited the `.tf` file instead of the console, or tagged the wrong log group | `terraform output drift_target_log_group` |
| Drift keeps coming back after `-refresh-only` | You accepted reality in state but not in code | Edit `main.tf` and commit. `-refresh-only` is half a fix |
| Auditor reports ≠ 13 | Wrong `--path`, or `bad-examples/` was edited | `--path ../terraform` from `lab/python` |
| Auditor reports 15 without you asking | It found credentials in your environment | `env -u AWS_PROFILE -u AWS_ACCESS_KEY_ID python3 iac_audit.py --path ../terraform` |
| `destroy` fails on a non-empty bucket | `force_destroy = false` (prod) or a versioned bucket | Empty it first — two passes, see the checklist |
| `Invalid for_each argument` | Keys not known at plan time | Key off the input, not off an attribute created in the same run |

---

## What you built

- An S3 backend with **native locking**, bootstrapped from local state and
  annotated in code as to why.
- **Two environments from three modules**, differing only in tfvars.
- A drift demonstration you triggered yourself, fixed three ways, and can now
  argue about.
- A **static IaC auditor** — 16 checks, twelve of which need no credentials —
  with 47 unit tests and two checks that are silent on purpose.

**Next:** [Day 06 — Monitoring & AI-Powered Incident Analysis](../../day-06-monitoring-ai-incident-analysis/).
The infrastructure is now code; next it learns to explain its own failures.
