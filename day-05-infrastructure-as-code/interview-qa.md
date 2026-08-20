# Day 05 — Interview Q&A

**CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp**

Fifteen questions that come up in real AWS, DevOps, platform and SRE
interviews, with the answers a senior candidate gives. Each one ends with
**what the interviewer is listening for** — because the difference between a
mid-level and a senior answer is almost never the facts.

The Terraform questions people fail are not the syntax ones. They are the
questions about state, blast radius and what happens when two people run
`apply` at the same time.

---

## 1. What is Terraform state, and why not just query AWS every time?

State is the map between the names in your configuration and the objects in
your account: `aws_vpc.this` ←→ `vpc-0a1b2c3d`. Without it Terraform cannot
know that the VPC in your code is the VPC in your account, and would propose
to create a second one on every apply.

"Just query AWS" fails on three things:

1. **Identity.** There is no AWS API for "which of these forty subnets is the
   one my code calls `aws_subnet.public["us-east-1a"]`". Tags are a
   convention, not a guarantee, and they are mutable by anyone with the
   console.
2. **Deletion.** If you remove a resource from your configuration, the API can
   never tell you it used to be managed. State is the only record that it was
   yours, which is what makes `terraform destroy` and removal-from-code work
   at all.
3. **Speed and rate limits.** A refresh of a large root module already makes
   hundreds of API calls. Rebuilding the whole graph from scratch on every
   command would be slower and would hit throttling.

State also records **every attribute of every managed resource**, which is what
makes it a security artifact — see question 4.

> **Listening for:** the deletion argument. Most candidates give the identity
> answer and stop. Knowing that state is the only evidence a resource was ever
> managed is the mark of somebody who has had to clean up after a `state rm`.

---

## 2. Set up remote state for a brand-new AWS account. Walk me through it.

The bucket that holds state has to exist before any configuration can write
state into it. So there are two phases, and the first one is deliberately
different from everything after it.

**Phase 1 — bootstrap, on local state.** A small root module with no backend
block that creates the state bucket: versioning on, default encryption on,
public access block on, ownership controls, a TLS-only bucket policy, and
`prevent_destroy` on the bucket itself. It runs on local state because there
is nowhere remote to put its state yet.

**Phase 2 — everything else.** Each environment gets a backend block pointing
at that bucket, with its own key: `day-05/dev/terraform.tfstate`,
`day-05/prod/terraform.tfstate`. One bucket, namespaced keys, so two
environments never collide.

Then the question the interviewer is really asking: **what do you do with the
bootstrap's own state file?**

- **Commit it, encrypted** (git-crypt, SOPS). It describes one bucket and holds
  no secrets. Small, auditable, recoverable. This is what I would do.
- **Migrate it into the bucket it just created.** Elegant, and every guide
  suggests it — but the bucket now holds its own state, which puts a genuine
  circular dependency in your destroy path.
- **Leave it on a laptop.** What most teams actually do, and the reason the
  bootstrap directory is the one nobody dares touch three years later.

Whichever you pick, annotate the exception in code. This lab uses an inline
`# iac-audit: allow-local-state` marker that the day's auditor reads and
honours, because a suppression belongs next to the thing being suppressed
where the next reviewer will see it — not in a `suppressions.yaml` nobody
reads.

> **Listening for:** that you noticed the chicken-and-egg at all, and that you
> have an opinion about the bootstrap state file rather than trailing off.

---

## 3. S3 native locking or a DynamoDB lock table?

Native S3 locking, on anything new.

`use_lockfile = true` makes Terraform write a `<key>.tflock` object with a
conditional put before it mutates state, and delete it afterwards. If the
object already exists the put fails with a 412 and the second run reports the
lock — with the ID, the user, the operation and the timestamp. That is the
whole mechanism. It needs **AWS provider ≥ 5.80 and Terraform ≥ 1.10**.

The DynamoDB lock table was the answer for about a decade: a table with a
`LockID` hash key, using a conditional write for the same purpose. It worked
fine. The `dynamodb_table` backend argument is now deprecated.

Two reasons this matters beyond trivia:

- **You will inherit them.** Every repository from before 2025 has one, and
  they are among the most common orphaned resources in an AWS account —
  ~$0.25/month, no tags, and nobody remembers what it was for.
- **If you are stuck below provider 5.80** — an old pipeline, a vendored
  module — you have no alternative, so you need to know how the table is
  shaped and that it belongs to the backend, not to your infrastructure.

Whichever mechanism: **state and locking are separate problems**. Remote state
fixes sharing. Locking fixes concurrency. A shared bucket with no locking is
arguably worse than local state, because now two people can corrupt one file
instead of each corrupting their own.

> **Listening for:** knowing native locking exists and is current, and being
> able to say what the DynamoDB table did without dismissing it as "the old
> broken way".

---

## 4. Why is read access to the state bucket a production credential?

Because state is plaintext JSON containing every attribute of every managed
resource — including the ones marked `sensitive`.

An RDS module that generates a master password puts that password in state, in
full. A `random_password` resource puts its result in state. A variable you
passed in from CI is in state. `sensitive = true` hides values from CLI
**output**; it does not encrypt them, redact them, or keep them out of the
file.

So `s3:GetObject` on the state bucket is equivalent to read access to every
secret in the estate, and it comes with none of the audit trail people assume:
no CloudTrail data events unless you turned them on, and no application log at
all.

The controls that follow from that:

- Default encryption on the bucket, ideally SSE-KMS with a customer-managed
  key — revocable, and every `Decrypt` shows up in CloudTrail.
- Public access block, all four settings, plus the account-level block.
- A bucket policy denying non-TLS requests.
- **Separate buckets or at least separate key prefixes with separate IAM per
  environment**, so that "can plan dev" does not imply "can read prod's
  secrets". This is also the strongest argument against workspaces.
- Versioning on, with a noncurrent-version expiry, so a corrupt write has a
  rollback path without the bucket growing forever.

The best answer to the whole problem is to keep secrets out of Terraform
entirely: create them out of band, and have the application fetch them at
runtime with its own instance role. Then state describes *where* the secret
is rather than *what* it is.

> **Listening for:** "including the ones marked sensitive". If a candidate
> thinks `sensitive` protects the state file, everything else they say about
> Terraform security is unreliable.

---

## 5. What does `sensitive = true` actually do?

It hides the value from CLI output. That is the complete list.

It does **not** encrypt it, does not remove it from state, does not stop
`terraform output -raw db_password` from returning it happily to anyone who
can run Terraform in that directory, and does not keep it out of a saved plan
file.

It is a display setting with a security-sounding name, and it is the single
most misunderstood feature in Terraform.

What it *is* good for: stopping a secret from being printed at the end of every
apply and captured into a CI build log that is readable by the whole
organisation and retained for a year. That is a real benefit and worth having.
It is just not confidentiality.

The correct handling, in order:

1. Do not let Terraform author the secret. Create it out of band and read it
   with a data source, or better, have the application fetch it at runtime with
   its own IAM identity so it never touches Terraform.
2. If Terraform must generate it — `random_password` — accept that state is now
   a secrets store and treat the bucket accordingly.
3. Mark it sensitive as well, because the CI log matters.

> **Listening for:** the phrase "display setting". Candidates who have only
> read the documentation say "it protects sensitive values"; candidates who
> have grepped a state file say what it actually does.

---

## 6. `count` or `for_each`? And how do you migrate an existing `count`?

`for_each` almost always, because of how the two address their instances.

`count` addresses by **position**: `aws_s3_bucket.reports[0]`, `[1]`, `[2]`.
Remove an element from the middle of the list and Terraform does not see "one
item removed" — it sees `[0]` changed name, `[1]` changed name, and `[2]` gone,
and plans to destroy and recreate all three. For an S3 bucket or an RDS
instance, "recreate" means the data is gone. That plan reads as `-/+ 3
resources` at the bottom of a long diff at 5pm on a Friday.

`for_each` addresses by **key**: `aws_s3_bucket.reports["alpha"]`. Remove one
key and exactly one resource is destroyed; nothing else in the plan moves.

`count` is correct for exactly one shape: `count = var.enabled ? 1 : 0`. The
moment the number can exceed one, use `for_each`.

**Migrating without destroying anything** is the follow-up, and there are two
ways:

```hcl
moved {
  from = aws_s3_bucket.reports[0]
  to   = aws_s3_bucket.reports["alpha"]
}
```

or `terraform state mv 'aws_s3_bucket.reports[0]' 'aws_s3_bucket.reports["alpha"]'`.

Prefer the `moved` block. It lives in code, it appears in the pull request, a
reviewer sees it, and every environment picks it up automatically. The CLI
version happens on one machine, once, and is remembered by nobody. Leave the
blocks in for a release or two, then delete them once every environment has
applied.

One `for_each` gotcha worth mentioning unprompted: **keys must be known at
plan time**. Keying off an attribute that does not exist until apply — an ARN
of something being created in the same run — gives you *"Invalid for_each
argument"*. Key off the input that generated it instead.

> **Listening for:** the migration answer. Knowing `for_each` is better is
> table stakes; knowing you can move an existing stack onto it without an
> outage is the senior part.

---

## 7. Workspaces or a directory per environment?

Directory per environment, for anything where the environments differ in risk.

Three reasons, in order of how much they hurt:

1. **One backend, one set of credentials.** Every workspace shares the same
   backend block, so dev state and prod state live in the same bucket under
   the same permissions. Anyone who can plan dev can read prod's state, which
   contains prod's secrets. See question 4 — this is the one that matters.
2. **The differences hide in conditionals.** Environments genuinely differ, so
   the code fills with `terraform.workspace == "prod" ? "m5.large" :
   "t3.micro"`. The only way to know what prod looks like becomes mentally
   evaluating every ternary in the repository.
3. **`terraform workspace select` is one word from a disaster.** Forget it and
   you apply dev's plan to prod. There is no directory in your prompt to tell
   you which you are in.

Directory-per-environment costs some duplication — two nearly identical
`providers.tf` files — and buys a separate backend, a separate state file,
separate IAM, a separate review, and a path on screen that says which
environment you are about to change. That trade is not close.

**Workspaces are genuinely good** for short-lived per-developer or per-PR
copies of the *same* environment. Same configuration, same risk profile,
different name. That is what they were built for, and it is a good fit.

If the duplication genuinely bothers you, the answer is a shared module that
both environment directories call — which is what this lab does — not one
directory with a ternary in it.

> **Listening for:** "anyone who can plan dev can read prod's state". Most
> candidates give reason 2 or 3, which are ergonomic arguments. Reason 1 is a
> security argument and it is the one that ends the discussion.

---

## 8. Version constraint and lock file — why do you need both?

They answer different questions.

The **constraint** in `required_providers` says what is *acceptable*:
`version = "~> 5.80"` allows 5.80.x through 5.x and refuses 6.0. It is your
policy.

The **lock file**, `.terraform.lock.hcl`, records what was *selected* — the
exact version, plus checksums for every platform. It is your fact.

With a constraint and no lock file, every `terraform init` re-resolves. Two
engineers initialising a day apart get different providers, and one of them
sees a plan with changes nobody wrote — usually a new attribute with a new
default. You spend the morning proving it was not your commit.

With a lock file and no constraint, `init -upgrade` can jump you to a new major
version, which is an error at best and a silent behavioural change at worst.

The lock file also carries the **supply-chain check**: those hashes are how
Terraform verifies the provider it downloaded is the provider it downloaded
last time.

Practicalities worth volunteering:

- **Commit it.** `terraform init -upgrade` is the only thing that should ever
  change it, and that change belongs in its own reviewed pull request where a
  human reads `aws 5.80.0 -> 5.82.2`.
- If CI runs on Linux and people develop on macOS, run
  `terraform providers lock -platform=linux_amd64 -platform=darwin_arm64` or
  CI rejects the file for missing hashes.
- `>= 5.80` on its own is not a pin. It allows 6.0.

> **Listening for:** "policy versus fact", and the supply-chain point. Anyone
> can say "commit the lock file"; explaining what each mechanism protects
> against separately is the difference.

---

## 9. A resource has `prevent_destroy` and you need to destroy it. Go.

Three steps, deliberately, in daylight:

1. Remove the `lifecycle { prevent_destroy = true }` block from the code.
2. `terraform apply` — this changes nothing in AWS; it updates state's record
   of the lifecycle rules.
3. `terraform destroy`.

The alternative, when you want the resource to survive: `terraform state rm
<address>`, which makes Terraform forget it. The resource keeps existing and
keeps billing, now unmanaged, and you delete it out of band if and when you
mean to.

**The wrong answer, which is also the common one:** deleting the lifecycle
block mid-incident because destroy keeps failing and you want it to stop
failing. The block is doing exactly its job. Removing it at speed, without
review, in a rush, is how production buckets go missing — and the person doing
it always means well.

Then the follow-up they will ask: **why can't you just make it a variable?**

`prevent_destroy = var.protect_me` is a **hard error**, not a warning.
`lifecycle` is evaluated before variables resolve — Terraform needs to know
whether destroy is permitted before it has a value for anything. There is no
per-environment toggle. If dev must be destroyable and prod must not, that is
two code paths, or accepting the seatbelt in both.

Worth adding: `prevent_destroy` does not protect against everything. It stops
`terraform destroy` and it stops a plan that would replace the resource. It
does **not** stop somebody deleting the bucket in the console, and it does not
survive `state rm`.

> **Listening for:** the three-step answer, and the reaction to "make it a
> variable". A candidate who says "you'd use a variable for that" has not hit
> the error, which means they have not managed a stateful resource.

---

## 10. How do you detect drift, and what do you do about it?

**Detect:** `terraform plan`. The first thing a plan does is refresh — read
every managed resource back from the AWS API and update state to match reality
— and then diff that against your configuration. Anything AWS reports that your
code does not ask for shows up as a proposed change back.

The thing to say next, because it is what people get wrong: **drift detection
is not a background service**. Nothing watches your account. It is a plan, and
it only happens when somebody runs one. So you run one on a schedule:

```bash
terraform plan -detailed-exitcode    # 0 = no changes, 1 = error, 2 = changes
```

Nightly, in CI, alerting on exit code 2. Three lines of YAML, and it catches
the console edits nobody mentioned.

**Fix — three correct answers, and choosing accidentally is the only wrong
one:**

| Fix | Effect | When |
|---|---|---|
| `terraform apply` | Code wins; AWS is reconciled | The change was unauthorised or a mistake. The default. |
| `plan -refresh-only` then apply | Reality wins; state updated | The change was correct and the code is now wrong |
| `lifecycle { ignore_changes = [...] }` | Stop diffing the attribute | Something else legitimately owns it — an autoscaler, a deploy pipeline, a tagging Lambda |

`-refresh-only` is **not a fix on its own**. It makes Terraform stop
complaining without making the code true. Follow it by changing the code and
committing, or the next `apply` reverts you and the cycle repeats.

And the organisational answer: if the same drift keeps recurring, the console
write access that allows it is the actual finding. Read-only console plus a
break-glass role is a bigger fix than any amount of `ignore_changes`.

> **Listening for:** all three fixes, and the fact that `-refresh-only` needs a
> code change behind it. Candidates who name only `terraform apply` have never
> been on the wrong side of the argument with the person who made the change.

---

## 11. Distinguish `terraform destroy`, `state rm`, `taint` and `import`.

| Command | Touches AWS? | Touches state? | Result |
|---|---|---|---|
| `terraform destroy` | **Yes — deletes** | Removes entries | The resource is gone |
| `terraform state rm` | No | Removes entry | Terraform forgets; the resource runs on, unmanaged, still billing |
| `terraform apply -replace=ADDR` | Yes — on the next apply | Marks for replacement | Destroy and recreate one resource |
| `terraform import` | No — reads only | **Adds** an entry | An existing AWS object becomes managed |

Two things to get right:

**`state rm` is the one people misuse.** It is the correct tool for handing a
resource to another team's Terraform, for splitting one root module into two,
and as a teardown escape when `prevent_destroy` is protecting something you
want to keep. It is the wrong tool for "the plan is doing something I do not
understand", where all it does is create an orphan that still bills.

**`taint` is deprecated.** `terraform apply -replace=ADDRESS` supersedes it,
and it is better: `taint` mutated state immediately, so you could not see the
consequence until the next plan, whereas `-replace` shows you the replacement
in the plan before you approve it. Mentioning that `taint` still works but is
the old way is a good signal.

For `import`, the modern form is an **import block**, not the CLI command:

```hcl
import {
  to = aws_s3_bucket.legacy
  id = "my-existing-bucket"
}
```

Reviewable in a pull request, and `terraform plan
-generate-config-out=generated.tf` will write you a configuration skeleton.
Same argument as `moved` blocks: things that happen in code get reviewed;
things that happen at one person's CLI do not.

> **Listening for:** knowing `state rm` leaves a billing orphan, and knowing
> `taint` has been superseded. The `import` block is a bonus that dates the
> candidate's experience as recent.

---

## 12. Why must a child module not contain a `provider` block?

Because a child module declares which providers it **requires**; the root
module declares how they are **configured** — region, profile, `default_tags`,
assume-role.

Put a configured `provider "aws" { region = "us-east-1" }` inside a child
module and you get, in order:

1. A module nobody can reuse in another region.
2. A module that cannot be used with `count` or `for_each`, because Terraform
   forbids that on modules with their own provider blocks.
3. A deprecation warning today, and a **hard error on the day you remove the
   module** — because Terraform must still be able to configure the provider in
   order to destroy what it created, and the configuration just disappeared
   along with the module block.

Number 3 is the one that ruins an afternoon. The escape hatches exist —
`removed` blocks, or temporarily re-adding the module — and you do not want to
need them.

The correct pattern for "this module needs a second region" is an **aliased
provider passed in by the caller**:

```hcl
# root
module "network_eu" {
  source    = "../../modules/network"
  providers = { aws = aws.eu }
}

# module/versions.tf
terraform {
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      version               = ">= 5.80, < 6.0"
      configuration_aliases = [aws.eu]
    }
  }
}
```

> **Listening for:** reason 3. Reasons 1 and 2 are in the documentation.
> Reason 3 is what you learn by trying to delete a module in a hurry.

---

## 13. When would you tell someone *not* to write a module?

Module-writing is the most over-applied skill in Terraform, and a repository
full of single-use modules is harder to read than the resources would have
been.

Do not write one when:

- **It has one caller.** A module with a single caller is a directory boundary
  and an indirection with no reuse to justify either. Inline it until there is
  a second caller — and "there might be one later" is not a second caller.
- **It is a thin wrapper over one resource.** `module "bucket"` creating one
  `aws_s3_bucket` and passing fourteen variables straight through has added
  fourteen variables and removed nothing. The resource was already the
  abstraction.
- **The two callers are not actually the same thing.** If half the module body
  is `count = var.is_prod ? 1 : 0`, you have two things wearing one name.
  Write two modules, or accept the duplication — duplication is cheaper to
  read than a conditional you have to mentally evaluate.
- **You are wrapping a good registry module to change one default.** Call it
  directly and set the input.

A useful test: **can you write the README before the code?** If you cannot
describe the interface without describing the internals, it does not have an
interface yet.

The related sizing question, which usually follows: one enormous root module
with two hundred resources means one state file, one lock, one blast radius,
and a plan that takes six minutes and is never read properly. Split by
**lifecycle and by ownership** — things that change together, and things one
team owns, belong together.

> **Listening for:** any answer at all. Most candidates have only been asked
> how to write modules, so a considered "here is when not to" reads as
> experience rather than enthusiasm.

---

## 14. Design a CI/CD pipeline for Terraform. Where does it break?

**On a pull request:** `fmt -check` and `validate`, then assume a **read-only**
role via OIDC, then `plan -detailed-exitcode -out=tfplan`, then a static audit
(`iac_audit.py --fail-on CRITICAL`, which needs no credentials at all), then
post the plan as a PR comment for a human.

**On merge to main:** assume a **write** role via OIDC, require an environment
approval, and run `terraform apply tfplan` — the saved plan, not a fresh one.

**On a schedule:** `plan -detailed-exitcode`, alert on exit code 2. That is
your drift detection.

Where it breaks, in the order it will happen to you:

- **`terraform apply` with no plan file.** It re-plans, and applies whatever is
  true *now* rather than what the reviewer approved. Between review and merge,
  somebody else's change landed. Pass the plan file.
- **Exit code 2 treated as failure.** `-detailed-exitcode` returns 2 for
  "succeeded, with changes". A naive `if [ $? -ne 0 ]` turns every pending
  change into a red pipeline, and people stop reading red pipelines.
- **Long-lived access keys in CI secrets.** Federate with OIDC instead — the
  runner exchanges a short-lived token for temporary credentials, so there is
  no key to leak, none to rotate, and none to appear in a log. Scope the trust
  policy to the repository **and** the branch; `repo:org/*:*` accepts a pull
  request from a fork.
- **Plan artifacts stored carelessly.** A plan file contains every value in the
  diff, including sensitive ones, in the clear. Treat it like the state file:
  encrypted, short retention, access-controlled.
- **Lock contention.** Two pipelines against one state file: the second gets a
  lock error, which is correct behaviour and reads as a flaky build. Serialise
  per environment with a concurrency group rather than lengthening timeouts.
- **A cancelled job leaving a stale lock.** `force-unlock` is the fix; read the
  lock info first, because force-unlocking a run that is still going is how two
  applies interleave.
- **One role for everything.** Plan reads, apply writes. There is no reason a
  fork's pull request should be able to assume a role that can write.

> **Listening for:** `apply tfplan` and the exit-code-2 trap. Both are things
> you only know from having built one.

---

## 15. Where does AI fit into Infrastructure as Code, and where do you keep it out?

**Where it fits, in rough order of value:**

- **Explaining a plan.** A 400-line diff summarised as "this replaces the RDS
  instance because `engine_version` changed, which will take the database
  down" is genuinely useful, and it is the review step people rush.
- **Drift triage.** Given a nightly `plan -detailed-exitcode` returning 2 plus
  the CloudTrail events from the same window, correlating "who changed this and
  when" is exactly the tedious correlation work models are good at.
- **First-draft modules and documentation.** Scaffolding, variable
  descriptions, README tables. Cheap to check, tedious to write.
- **Natural-language questions over state.** "Which buckets have no versioning"
  answered from a state file rather than from forty console tabs.
- **Remediation suggestions on audit findings** — which is where Day 06 takes
  this, feeding CloudWatch logs to Bedrock for incident summarisation.

**Where I keep it out:**

- **Anywhere near `apply`.** No model gets credentials that can mutate
  infrastructure. The failure mode is not "it writes bad HCL" — that gets
  caught by plan. The failure mode is a confident, plausible, wrong change
  applied at 3am by an automation nobody was watching.
- **Anything touching state directly.** State edits need to be deliberate and
  attributable.
- **As the only reviewer.** A model summarising a plan is an aid to the human
  reading it, not a replacement for them. The moment the summary is trusted
  instead of the diff, you have automated away the review while keeping the
  ceremony.
- **Feeding it the state file.** State is a secrets store. Do not paste it into
  anything, including an internal model, without knowing exactly where the
  prompt is logged.

The general shape I would defend: **AI on the read path, humans on the write
path.** Explanation, correlation, summarisation and drafting are read-path
work. Mutation is not.

> **Listening for:** a real boundary rather than enthusiasm. "AI on the read
> path, humans on the write path" is a position; "AI can help with everything"
> is not an answer.

---

## Rapid-fire

| Question | Answer |
|---|---|
| Can the backend block use variables? | No. Hard error. Use `-backend-config` at init |
| What does `use_lockfile` need? | AWS provider ≥ 5.80, Terraform ≥ 1.10 |
| Is the DynamoDB lock table still required? | No. Deprecated. Know it for legacy repos |
| Does `sensitive = true` encrypt anything? | No. It hides CLI output |
| Is state encrypted by default? | The bucket may be. The file's contents are plaintext JSON |
| `-detailed-exitcode` return values? | 0 no changes, 1 error, 2 changes pending |
| What does `-/+` mean in a plan? | Destroy and recreate. On stateful resources, data loss |
| `count` is correct when? | `count = var.enabled ? 1 : 0`, and only then |
| Must `for_each` keys be known at plan time? | Yes. Otherwise "Invalid for_each argument" |
| Refactor without destroy? | `moved` block, or `terraform state mv` |
| Adopt an existing resource? | `import` block (≥ 1.5), or `terraform import` |
| Is `taint` current? | No. Use `apply -replace=ADDRESS` |
| `state rm` — does the resource get deleted? | No. Terraform forgets it; it keeps billing |
| Can `prevent_destroy` take a variable? | No. Literal only — lifecycle is evaluated first |
| Provider block in a child module? | Never. Pass an aliased provider in |
| Commit `.terraform.lock.hcl`? | Yes. Always |
| Commit `terraform.tfvars`? | No. Commit the `.example` |
| `~> 5.80` allows what? | 5.80.x through 5.x. Not 6.0 |
| Does `>= 5.80` pin anything? | No. It allows 6.0 |
| Which wins, `-var` or `terraform.tfvars`? | `-var`. Command line beats files |
| Where do `*.auto.tfvars` sit? | Above tfvars, below `-var`. Lexical order among themselves |
| Is drift detection automatic? | No. It is a plan somebody has to run |
| Three fixes for drift? | apply · `-refresh-only` + code change · `ignore_changes` |
| When is `-target` acceptable? | Recovering a failed apply, or a provider bug with a ticket |
| Workspaces are good for? | Short-lived copies of the *same* environment |
| Egress to `0.0.0.0/0` — a finding? | No. Normal. Flag ingress only |
| What creates a dependency edge? | A reference. Not `depends_on` |
| Destroy order in this lab? | envs first, `backend-bootstrap` **last** |

---

**See also:** the day guide [`README.md`](README.md), the diagrams in
[`diagrams/README.md`](diagrams/README.md), and the audit checks in
[`lab/python/iac_audit.py`](lab/python/iac_audit.py) — several of these
questions are that tool's docstrings, written out.
