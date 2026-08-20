# Day 05 — Trainer Notes

**CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp**

Internal. Not for learners.

**3h 30m taught.** Six live demos. The two that carry the day are **Demo 1**
(reading a state file) and **Demo 4** (console drift). If you are running short
on time, cut Part 6 and the CI/CD section before you cut either of those.

---

## Before the session

### T-24 hours

- [ ] Run the whole lab yourself, from an empty directory, in a **fresh
      account or a fresh prefix**. The bootstrap is the step that breaks, and
      it breaks differently depending on what already exists.
- [ ] Confirm your Terraform is ≥ 1.10 and the AWS provider resolves ≥ 5.80.
      `use_lockfile` does not exist below either, and the failure message is
      unhelpful — it just ignores the argument and does not lock.
- [ ] Apply `envs/dev` with `create_insecure_examples = true`. Demo 1 and the
      15-finding audit both depend on it.
- [ ] Run the auditor and confirm **13 / 15 / 0-out-of-100**. If your numbers
      differ, somebody edited `bad-examples/` and five documents are now wrong.
- [ ] `python3 -m unittest discover -s tests` → **47 passed**.
- [ ] Pre-warm every `terraform init`. Provider download is 400+ MB and it will
      run over conference wifi at exactly the wrong moment.
- [ ] Have **two terminals** ready, side by side, with large fonts. Demo 2
      needs both simultaneously.

### T-30 minutes

- [ ] `aws sts get-caller-identity --profile bootcamp` in every terminal.
- [ ] Set `PS1` to show the **directory**. Half the day is an argument about
      which directory you are in; do not undermine it with a bare `$`.
- [ ] Open `bad-examples/README.md` in a tab. You will refer to it three times.
- [ ] Have the state bucket name in your paste buffer. You will need it in
      Demos 1, 2 and 5.
- [ ] Clear your scrollback. Demo 1 shows a real state file on a projector —
      check what is above it first.

### The one thing to say in the first two minutes

> "Today is not about Terraform syntax. You can look that up. Today is about
> one file — the state file — and the fact that it is a plaintext list of every
> secret your infrastructure has ever touched, sitting in a bucket, protected
> by nothing except an IAM policy somebody wrote in a hurry.
>
> Everything else we do today is a consequence of that file existing. Where it
> lives, who can read it, what happens when two people write to it at once,
> and how you find out when reality has quietly stopped matching it."

Then do not explain state for twenty minutes. **Do Demo 1 within the first
forty, while they still think you are exaggerating.**

---

## Timing

| Time | Section | Demo |
|---|---|---|
| 0:00–0:10 | Opening — why IaC, and the honest failure modes | |
| 0:10–0:40 | Part 1 — the workflow, and state | 🎬 **1 — read your own state file** |
| 0:40–1:00 | Part 2 — the bootstrap chicken-and-egg, backends, locking | 🎬 2 — watch the `.tflock` |
| 1:00–1:10 | **Break** | |
| 1:10–1:40 | Part 3 — modules, and when not to write one | |
| 1:40–2:05 | Part 4 — environments; workspaces vs directories | 🎬 3 — prod from the same modules |
| 2:05–2:15 | **Break** | |
| 2:15–2:45 | Part 5 — drift | 🎬 **4 — console drift, end to end** |
| 2:45–3:10 | Part 6 — auditing IaC statically | 🎬 5 — 13 findings, then 100/100 |
| 3:10–3:25 | Part 7 — CI/CD, teardown, `prevent_destroy` | 🎬 6 — the destroy that fails |
| 3:25–3:30 | Close and Day 06 hand-off | |

Runs long by ten minutes every time. The compressible parts are Part 3
(modules — they will read the READMEs) and Part 7's CI/CD half.

---

## 0:00–0:10 — Opening

Frame the scenario from the day README and then ask the room:

> "Who has changed something in the AWS console in the last month that
> Terraform manages?"

Roughly half the hands go up, and about half of those look guilty. That is the
day's tension and you should name it now:

> "None of you did that because you are careless. You did it because the
> console was open and it was faster. The question today is not how to stop
> doing it. It is how to **find out** that it happened."

Do not oversell IaC. The honest pitch:

- IaC gives you **review**, **repeatability** and a **record**.
- It costs you a state file that is now the most sensitive artifact you own,
  a locking problem, a bootstrap problem, and a new class of outage where the
  tool destroys something because you renamed a variable.

**Say the cost out loud.** A room that has been told IaC is free stops
listening the first time a plan says `-/+`.

---

## 0:10–0:40 — Part 1: the workflow, and state

Ten minutes on init/plan/apply/destroy — fast, they mostly know it. Land three
points:

1. **`init` does three jobs**, and downloading providers is the least
   interesting. It also writes the lock file and configures the backend.
2. **`plan` refreshes first, then diffs.** That refresh is the entirety of
   drift detection. Draw the three-box diagram (config / AWS / state).
3. **The dependency graph comes from references**, not `depends_on`. Show
   `envs/dev/main.tf`: `subnet_ids = module.network.public_subnet_ids` is the
   edge, and there is no `depends_on` in the directory.

Mention `-target` briefly and dismiss it properly:

> "It works. It applies part of your configuration and leaves state internally
> consistent but no longer a description of a complete apply. The scary part
> of the plan you skipped does not go away — it gets applied later, by
> somebody who was not in this conversation."

Then move to state, and get to the demo fast.

### 🎬 Demo 1 — read your own state file (6 min)

**The demo that changes how people work. Do not skip it and do not rush it.**

Set it up by asking, genuinely:

> "Terraform state. Who has actually opened one?"

Usually one or two hands.

```bash
cd lab/terraform/envs/dev
terraform output -raw data_bucket_name        # something real exists

# Now read the state file. Not a copy. THE state file.
aws s3 cp s3://cbc-day05-tfstate-abc123/day-05/dev/terraform.tfstate - \
  --profile bootcamp --region us-east-1 | head -60
```

Scroll slowly. Point at things:

- Every attribute of every resource, including ones nobody referenced.
- ARNs, bucket names, subnet IDs — a complete map of the environment.
- The `serial` number, and the `lineage` UUID.

Then the line that lands it:

> "There is no password in this one, because I did not put one in. Now imagine
> this stack had an RDS instance. The master password is in here. In full. In
> plaintext. Right there next to the subnet IDs.
>
> Anyone with `s3:GetObject` on this bucket has just read every secret in your
> estate, and did not touch a single resource to do it. No CloudTrail data
> event unless you turned them on. No application log at all."

Pause. Then:

> "So — who has `s3:GetObject` on your state bucket at work?"

Nobody knows. That is the correct answer and it is the point.

**Follow immediately with the `sensitive = true` correction**, while they are
receptive:

```bash
grep -c sensitive <(aws s3 cp s3://.../terraform.tfstate - --profile bootcamp)
```

> "`sensitive = true` hides a value from CLI **output**. It does not encrypt
> it, does not redact it, does not remove it from this file. It is a display
> setting with a security-sounding name. This is the single most
> misunderstood feature in Terraform, and about a third of you were relying
> on it ten minutes ago."

**If the demo fails:** you are probably in the wrong directory or the bucket
name is stale. Have a saved, redacted state file open in a second tab as a
fallback — but try the live one first; reading a real file off a real bucket
is most of the impact.

---

## 0:40–1:00 — Part 2: the bootstrap, backends, locking

Open `envs/dev/backend.tf` and read the header comments with them. It is
already the lecture; do not re-deliver it from slides.

Cover in this order:

1. **The backend block cannot use variables.** Not "should not". Show the
   error if you have time — everybody tries this once.
2. **`use_lockfile`**, what it does mechanically, and the ≥ 5.80 / ≥ 1.10
   requirement.
3. **The DynamoDB table is legacy.** Say it clearly: *know it, do not build
   it.* Ask who has one in their account. Several will.
4. **The chicken-and-egg.** Open `backend-bootstrap/providers.tf`, find the
   `# iac-audit: allow-local-state` marker, and make the argument about
   suppressions living next to the thing suppressed.

> "Every audit tool needs a way to say 'I know, and here is why'. The only
> good place for that is in the code, greppable, next to the thing being
> suppressed — where the next person to change this file will see it. A
> `suppressions.yaml` in the repo root is a file nobody reads that quietly
> grows."

Then ask the question that always produces a good discussion:

> "So where does the bootstrap's own state file live?"

Let them argue for two minutes. Land on: commit it encrypted, migrate it, or
leave it on a laptop — and *the last one is only wrong when nobody decided it*.

### 🎬 Demo 2 — watch the lock exist (5 min)

Two terminals, side by side. **Practise this one; the timing is tight.**

```bash
# Terminal 1 — start an apply that takes a moment
cd lab/terraform/envs/dev
terraform apply -auto-approve

# Terminal 2 — WHILE IT RUNS
watch -n1 "aws s3 ls s3://cbc-day05-tfstate-abc123/day-05/dev/ --profile bootcamp"
```

They watch `terraform.tfstate.tflock` appear and then vanish.

> "That object **is** the lock. No DynamoDB table. Terraform writes it with a
> conditional put — if it already exists, the put fails with a 412 and the
> second run tells you who holds it, what they are doing, and since when."

If you have time, prove the contention. Third terminal, during the apply:

```bash
cd lab/terraform/envs/dev && terraform plan
# Error: Error acquiring the state lock
#   ID, Path, Operation, Who, Created
```

> "That error is not a bug. It is the feature. Without it, two applies
> interleave writes into one file and AWS obliges both of them."

Mention `force-unlock` and its danger in one sentence: read the lock info
first, because force-unlocking a run that is still going is exactly how the
interleaving happens.

---

## 1:00–1:10 — Break

Tell them to run `terraform apply` in `envs/dev` now if they have not, so it is
warm for Part 4.

---

## 1:10–1:40 — Part 3: modules

The most compressible section. They can read `modules/network/README.md`.

Three things must be said out loud:

**1. No provider blocks in child modules.** Give all three consequences, and
dwell on the third — the hard error on removal — because it is the one that
ruins an afternoon and the one that is not in the documentation summary.

**2. `for_each` over `count`.** Draw the position-vs-key diagram on the board.
Use the S3 example specifically:

> "You delete `reports-alpha` from the middle of the list. Terraform plans
> `-/+ 3 resources`. Two of those buckets you never touched, and for an S3
> bucket, 'recreate' means the data is gone. That plan is thirty lines from
> the bottom of a four-hundred-line diff, at five on a Friday."

Then the recovery, because it is the part they will actually need:

```hcl
moved {
  from = aws_s3_bucket.reports[0]
  to   = aws_s3_bucket.reports["alpha"]
}
```

> "In code, in the pull request, reviewed, and every environment picks it up.
> `terraform state mv` does the same thing on one machine, once, and is
> remembered by nobody."

**3. When NOT to write a module.** This is the section that separates the day
from a tutorial. Push back on module enthusiasm explicitly:

> "A module with one caller is an indirection with no reuse to justify it. If
> half the body is `count = var.is_prod ? 1 : 0`, you have two things wearing
> one name. Duplication is cheaper to read than a conditional you have to
> mentally evaluate."

Test to give them: **can you write the README before the code?**

---

## 1:40–2:05 — Part 4: environments

Open `envs/dev/providers.tf` and read the workspaces essay with them.

Deliver the three arguments in order and **make sure argument 1 lands hardest**,
because it is the security one and the other two are ergonomics:

> "Every workspace shares one backend block. Dev state and prod state in one
> bucket, under one set of permissions. Anyone who can plan dev can read prod's
> state — and we spent twenty minutes an hour ago establishing what is in a
> state file."

Then argument 3, which gets the laugh and the nod:

> "`terraform workspace select` is one word away from applying dev's plan to
> prod, and there is nothing in your prompt to tell you which one you are in.
> There are public postmortems about that missing word."

Be fair to workspaces: short-lived per-PR copies of the *same* environment.
Same config, same risk profile, different name. Good fit, built for it.

### 🎬 Demo 3 — prod from the same modules (6 min)

```bash
cd lab/terraform/envs/prod
diff ../dev/main.tf main.tf | head -30
```

Show that the module calls are the same three modules. Then:

```bash
diff ../dev/terraform.tfvars.example terraform.tfvars.example
```

> "There is the entire difference between dev and prod. Point-in-time
> recovery. Ninety-day version retention. Thirty-day logs. `force_destroy =
> false`. Every one of those is a **decision**, in a file, in a diff, that
> somebody reviewed.
>
> With workspaces, all four of those live in ternaries scattered through
> shared code, and the only way to know what prod looks like is to evaluate
> every one of them in your head."

If time allows, `terraform apply` prod and show `terraform output
cost_breakdown`, specifically `the_doubling`:

> "Multi-environment is not expensive because a VPC costs money. It is
> expensive because every toggle you flip, you now flip twice — and the second
> one is the one nobody reviews."

---

## 2:05–2:15 — Break

Ask them to leave `envs/dev` applied. Demo 4 needs it.

---

## 2:15–2:45 — Part 5: drift

**The centre of the day.** Everything before this was setup.

Five minutes of framing, then twenty on the demo and the discussion it starts.

Open with the question from the opening, called back:

> "Half of you changed something in the console this month. Let us find out
> what that looks like from Terraform's side."

### 🎬 Demo 4 — console drift, end to end (10–12 min)

**Have the console already open on the log group before you start.** Hunting
for it live kills the pacing.

```bash
cd lab/terraform/envs/dev
terraform output drift_target_log_group
# /aws/cbc-day05-dev/drift-demo
```

**Step 1 — establish the baseline.** Plan first, in front of them:

```bash
terraform plan
# No changes. Your infrastructure matches the configuration.
```

> "Remember that sentence. We are about to make it untrue, and nothing is
> going to tell us."

**Step 2 — be the person who changes something.** In the console: CloudWatch →
Log groups → the drift target → Tags → change `CostCentre` from `engineering`
to `finance`. Narrate it in character:

> "Finance asked for it. It is a tag. It took four seconds. I am not going to
> raise a pull request for a tag."

**Step 3 — nothing happens.** Sit in the silence for a beat.

> "No alert. No email. Terraform does not know. Nothing is watching. Drift
> detection is not a background service — it is a plan, and somebody has to
> run one."

**Step 4 — run one.**

```bash
terraform plan
```

```
  ~ resource "aws_cloudwatch_log_group" "drift_target" {
      ~ tags = {
          ~ "CostCentre" = "finance" -> "engineering"
        }
    }
```

Walk them through what just happened, slowly, because this is the mental model
the whole day is for: refresh read reality into state, then state was diffed
against the configuration, and the proposal is to put reality back.

**Step 5 — the three fixes.** Do not just list them. Ask the room which is
correct, then reveal that all three are, depending:

```bash
terraform apply                 # code wins — the default answer
terraform plan -refresh-only    # reality wins — then CHANGE THE CODE
# lifecycle { ignore_changes = [tags["CostCentre"]] }   # stop caring
```

Emphasise the trap in the middle one:

> "`-refresh-only` is not a fix on its own. It makes Terraform stop
> complaining without making the code true. If you stop there, the next person
> to run `apply` gets the surprise you deferred — and they will not know why."

**Step 6 — the organisational point**, which is the real lesson:

> "If this keeps happening, the fix is not `ignore_changes`. The fix is that
> somebody has console write access to production and there is no reason for
> it. Read-only console plus a break-glass role beats any amount of
> `ignore_changes`."

**Step 7 — the scheduled plan**, in thirty seconds:

```bash
terraform plan -detailed-exitcode ; echo "exit: $?"
# exit: 2  → there are changes
```

> "Nightly, in CI, alert on exit code 2. Three lines of YAML, and it catches
> every console edit nobody mentioned. Watch the trap though — 2 means
> *success, with changes*. A naive `if [ $? -ne 0 ]` turns every pending
> change into a red pipeline, and people stop reading red pipelines."

---

## 2:45–3:10 — Part 6: auditing IaC statically

Lead with the argument, not the tool:

> "Every static finding is one you can fix before `apply` runs, which is the
> only time fixing it is cheap. By the time GuardDuty can see the problem, the
> resource exists, three things depend on it, and the fix needs a change
> window."

### 🎬 Demo 5 — 13 findings, then 100/100 (5 min)

```bash
cd lab/python
python3 iac_audit.py --path ../terraform --no-colour
# 13 findings. COMPLIANCE SCORE: 0/100
```

Let them read the table. Then — and this is the beat that matters:

```bash
python3 iac_audit.py --path ../terraform/envs/dev --no-colour
# No findings. COMPLIANCE SCORE: 100/100
```

> "Same tool. Same sixteen checks. A directory somebody wrote carefully. All
> thirteen findings come from `bad-examples/`, which is applied by nothing and
> exists to be parsed."

**Then the point of the whole design** — the two silent checks:

> "IAC-003 and IAC-004 find nothing, and both are fully implemented and fully
> tested. IAC-004 looks for a publicly readable state bucket. We did not ship
> one, not even as a teaching example, because a repo that does is one
> `terraform apply` away from being a repo that leaked somebody's data.
>
> A check set where everything fires teaches you that findings are normal. A
> check set with two deliberate zeroes teaches you that a quiet check is
> evidence."

Distinguish IAC-015 explicitly — learners conflate the three every time:

> "IAC-015 also reports nothing, and it is a **different kind** of nothing.
> Drift does not exist on a fresh apply, by definition. It is silent by
> situation, not by design — and we made it fire twenty minutes ago."

Re-run with credentials to show 15, then mention 16 after Demo 4's drift.

If there is time, `--no-credentials` framing:

```bash
env -u AWS_PROFILE python3 iac_audit.py --path ../terraform --quiet --format json | \
  python3 -c "import json,sys; print(json.load(sys.stdin)['finding_count'])"
```

> "Twelve of sixteen checks, no credentials, no network. That is your
> pre-commit hook."

Point them at the challenge file and the `IAC_AUDIT_MODULE` feedback loop, and
mention the 47 tests run in under a second.

---

## 3:10–3:25 — Part 7: CI/CD, teardown, `prevent_destroy`

Five minutes on the pipeline diagram. Land three things only:

1. **`apply tfplan`, not `apply`.** Bare apply re-plans and applies what is
   true now, not what was reviewed.
2. **`-detailed-exitcode` returns 2 for changes.** Same trap as Demo 4.
3. **OIDC, not long-lived keys.** *"No key exists to leak, none to rotate,
   none to appear in a log — and IAC-002 cannot fire."*

### 🎬 Demo 6 — the destroy that fails (5 min)

```bash
cd lab/terraform/envs/dev
terraform destroy
```

```
Error: Instance cannot be destroyed
Resource module.storage.aws_s3_bucket.data has lifecycle.prevent_destroy set
```

**Do not fix it yet.** Ask the room what they would do. Somebody will say
"delete the lifecycle block". Thank them, because they are right about the
mechanism and this is the whole lesson:

> "That is exactly the correct edit. It is also exactly the wrong moment. The
> difference between step 1 of the correct procedure and losing a production
> bucket is not the edit — it is whether you did it deliberately, in a pull
> request, having thought about it, or at speed because a pipeline was red and
> you wanted it green.
>
> Every incident that starts this way follows the same script: destroy fails,
> somebody removes the guard to unblock, destroy succeeds *completely*, and
> the bucket with the only copy of something is gone forty seconds later. The
> person who does it always means well."

Then the two correct routes:

```bash
# Route 1 — you want it gone: remove the block, apply, then destroy
# Route 2 — you want it kept:
terraform state rm 'module.storage.aws_s3_bucket.data'
terraform destroy
```

> "`state rm` does not delete anything. Terraform forgets. The bucket keeps
> running and keeps billing, with nobody managing it. Sometimes that is
> exactly right. Sometimes it is how you make an orphan."

Close with the order and the versioned-bucket trap:

> "Envs first, bootstrap **last**. Destroy the bootstrap first and you have
> deleted the bucket holding dev's and prod's state — those environments still
> exist and Terraform can no longer see them.
>
> And the state bucket is versioned, so `aws s3 rm --recursive` will not empty
> it. It adds delete markers and keeps every version. Two passes: versions,
> then markers. The checklist has the commands."

---

## 3:25–3:30 — Close

Three sentences:

> "The state file is the most sensitive artifact you own. Drift is not detected
> by anything unless you run a plan. And the seatbelt that makes your destroy
> fail is doing its job — the day you remove one in a hurry is the day you find
> out what it was protecting.
>
> Tomorrow the infrastructure learns to explain its own failures."

Point at the checklist in the day README and at `teardown-checklist.md`. Say
plainly: **read the teardown checklist before you run destroy, not after it
fails.**

---

## Questions that come up every cohort

**"Can I just commit the state file?"**
No — and the reason is not merge conflicts, though those are real and
unresolvable by hand. It is that state is plaintext secrets. Committing it
publishes them to everyone with repository access, permanently, in a file
people stop noticing after the first week.

**"Why not put the state bucket in the same Terraform?"**
Because it does not exist yet when Terraform needs to write state into it.
That is Part 2, and it is worth re-deriving on the board if two people ask.

**"Is OpenTofu fine?"**
Yes. Everything today works on OpenTofu ≥ 1.8. `tofu` is a drop-in for `tofu
fmt`, `init`, `plan`, `apply`. The licence question is not one to have in class
— note it exists and move on.

**"Terraform Cloud / HCP Terraform / Spacelift / Atlantis?"**
All solve state, locking and the plan-in-a-PR workflow for you, and are worth
using. Learn the mechanism first — the day you have to debug one of them, you
will be debugging exactly what we built today.

**"How big should a root module be?"**
Small enough that a plan is read properly. Split by lifecycle and by
ownership: things that change together, and things one team owns, belong
together. Two hundred resources in one state file is one lock, one blast
radius and a six-minute plan nobody reads.

**"What if two people need to apply at once?"**
They do not. That is what the lock is for. If it happens often enough to hurt,
split the root module — the contention is telling you the blast radius is too
big.

**"Should the auditor block the pipeline?"**
`--fail-on CRITICAL` on the pull request, and nothing stricter to begin with.
A linter that blocks on MEDIUM in week one is a linter that gets a
`# noqa`-equivalent in week two and gets deleted in week three.

---

## Failure modes in the room

| Symptom | Cause | Fix |
|---|---|---|
| `terraform init` fails on the backend | `bucket` still says `REPLACE-ME` | `terraform output -raw state_bucket_name` in `backend-bootstrap` |
| Backend init succeeds but state is local | Terraform < 1.10, or provider < 5.80, silently ignoring `use_lockfile` | Check versions before you debug anything else |
| `Error acquiring the state lock` | Somebody's cancelled run | Read the lock info. `force-unlock` only if the run is genuinely dead |
| Demo 4 shows no diff | Tag changed on the wrong log group, or they edited the `.tf` instead of the console | `terraform output drift_target_log_group` |
| Auditor reports ≠ 13 | Somebody edited `bad-examples/`, or `--path` is wrong | `--path ../terraform` from `lab/python` |
| Auditor reports 15 without credentials | It found credentials in the environment | `env -u AWS_PROFILE -u AWS_ACCESS_KEY_ID` |
| `destroy` fails and they panic | Working as designed | Demo 6. Do not let them delete the block in a hurry |
| Bootstrap `destroy` fails on a non-empty bucket | Versioned bucket, delete markers | Teardown checklist, two-pass empty |

---

## What to cut when you are running late

In this order:

1. **Part 7's CI/CD half** (5 min) — the diagram is in the README and it is the
   most self-explanatory section of the day.
2. **Part 3's module structure walkthrough** (8 min) — they will read
   `modules/network/README.md`. Keep the *"when not to write one"* argument;
   cut the file-by-file tour.
3. **Demo 3** (6 min) — replace with the `diff` of the two tfvars files on
   screen and say the sentence about the doubling.
4. **Demo 2** (5 min) — describe the `.tflock` mechanism instead. Weakest of
   the six, and the only one that needs two terminals in sync.

**Never cut:** Demo 1, Demo 4, the two silent-by-design checks, or the
`prevent_destroy` conversation in Demo 6. Those four are the day.
