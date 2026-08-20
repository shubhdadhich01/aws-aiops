# Day 10 — Lab Walkthrough

**Capstone: Composition & Continuity**

If you have not read the top-level `README.md`, do that first. This file
is where the actual work is done, and it assumes you know what the
four states are and what the auditor is trying to prove.

---

## What you will do, in order

Eleven numbered steps. The numbers match the `next_steps` output that
`terraform output` will print at the end of `apply`. Each step names
which check IDs it moves so you can verify by re-running the auditor.

| Step | Action                                                    | Checks that move                   |
|------|-----------------------------------------------------------|------------------------------------|
| 1    | Confirm the SNS subscription for alarms                   | — (setup only)                     |
| 2    | Run the auditor against defaults (STATE A)                | 7 findings, score 54               |
| 3    | Turn on the free guardrails one at a time                 | `CAP-001`, `CAP-004`, `CAP-005`,   |
|      |                                                           | `CAP-009`                          |
| 4    | Turn on the paid guardrails                               | `CAP-010`, `CAP-011`               |
| 5    | Add a `suppressions.yaml` file                            | `CAP-008`                          |
| 6    | Configure git remote metadata                             | `CAP-015` (once reports exist)     |
| 7    | Trigger a manual invocation to seed the archive           | — (populates archive)              |
| 8    | Re-run the auditor. This is STATE B                       | 0 findings, score 100              |
| 9    | Fake-wait 30 days. This is STATE C — the day's thesis     | `CAP-003`, `CAP-012`, `CAP-016`x4  |
| 10   | Enable reference-arch (optional, expensive)               | `CAP-014` becomes active           |
| 11   | Tear down                                                 | — (destroy)                        |

Time budget: about 2 hours for the first pass. Step 9 is the interesting
one — it involves not doing anything and using the tests to
demonstrate the decay.

---

## Step 0 — prerequisites

```bash
# You are in the right directory.
cd lab/terraform

# Your AWS CLI profile works and carries the RIGHT permissions.
aws sts get-caller-identity --profile bootcamp
aws budgets describe-budgets --account-id "$(aws sts get-caller-identity --profile bootcamp --query Account --output text)" --profile bootcamp --region us-east-1
```

If the last command returns `AccessDeniedException`, attach
`AWSBillingReadOnlyAccess` to your role. Some of the archived reports
that CAP-006 correlates come from Day 09's audit, which reads Cost
Explorer.

Copy the tfvars template:

```bash
cp terraform.tfvars.example terraform.tfvars
```

Set at minimum:

```hcl
notification_email = "your-email@example.com"
owner              = "your-name"
```

Leave all `enable_*` toggles false. STATE A is what you get when apply
completes and nothing is turned on.

---

## Step 1 — `terraform apply` and confirm the SNS subscription

```bash
tofu init
tofu apply
```

Expected tail:

```
Apply complete! Resources: 27 added, 0 changed, 0 destroyed.

Outputs:
  archive_bucket_name       = "cbc-day10-archive-abc123"
  runner_function_name      = "cbc-day10-runner"
  alarms_topic_arn          = "arn:aws:sns:us-east-1:123:cbc-day10-alarms"
  schedule_state            = "DISARMED — CAP-001 fires"
  archive_versioning_state  = "Suspended — CAP-004 fires"
  archive_lifecycle_state   = "NOT attached — CAP-005 fires"
  lambda_alarm_state        = "MISSING — CAP-009 fires"
  dashboard_state           = "MISSING — CAP-010 fires"
  athena_state              = "MISSING — CAP-011 fires"
  reference_arch_state      = "not deployed — CAP-014 silent by design"
  ...
```

The outputs are naming, deliberately, what the auditor is about to
report. Read them before you run the auditor — the checks confirm the
Terraform is honest about its state.

**Confirm the SNS subscription** — the AWS confirmation email lands
in `notification_email`. Click the confirmation link. Until you do,
the Lambda-errors alarm and the CAP-016 unread-report notifications
publish into a topic that emails an unconfirmed address.

---

## Step 2 — run the auditor. This is STATE A.

```bash
cd ../python
pip install -r requirements.txt
python capstone_audit.py --profile bootcamp --region us-east-1 \
  --archive-bucket "$(cd ../terraform && tofu output -raw archive_bucket_name)"
```

Expect:

- 7 findings.
- 46 points.
- Score 54/100. Grade D.

The seven findings are: `CAP-001`, `CAP-004`, `CAP-005`, `CAP-008`,
`CAP-009`, `CAP-010`, `CAP-011`. Everything else is silent, and the
report tells you why for each silent check.

**Save the output for later comparison:**

```bash
python capstone_audit.py --profile bootcamp --region us-east-1 \
  --archive-bucket "$(cd ../terraform && tofu output -raw archive_bucket_name)" \
  --format json --quiet > state-a-findings.json
```

You will diff this against subsequent runs to prove each step
produced the finding it was supposed to.

**If you get fewer than 7 findings**, either the auditor could not
reach an API, or the Terraform did not apply. Read the auditor's
stderr progress lines to see which collector failed.

**If you get MORE than 7 findings**, the account has residue from a
prior run of Day 10 or from other work. Add `--name-prefix cbc-day10`
to filter to this lab's resources; if that does not clear it, run
`aws cloudwatch describe-alarms --profile bootcamp --region us-east-1`
and look for alarms whose names don't start with `cbc-day10-`.

---

## Step 3 — turn on the free guardrails one at a time

Point of turning them on ONE AT A TIME is to see what each does.

### Step 3a — enable the scheduler

Edit `terraform.tfvars`:

```hcl
enable_scheduler = true
```

Apply and re-audit:

```bash
cd ../terraform && tofu apply
cd ../python && python capstone_audit.py --profile bootcamp --region us-east-1 \
  --archive-bucket "$(cd ../terraform && tofu output -raw archive_bucket_name)"
```

**Expected:** 6 findings. `CAP-001` gone. Score up by 10 to 64.

The EventBridge rule is now armed, firing every 7 days. Note that
CAP-002 (interval > 7) stayed silent because 7 is the boundary
value, not above it — change `schedule_interval_days = 14` in tfvars
if you want to see CAP-002 fire.

### Step 3b — enable archive versioning

```hcl
enable_archive_versioning = true
```

Apply and re-audit. **Expected:** 5 findings. `CAP-004` gone. Score 74.

### Step 3c — enable archive lifecycle

```hcl
enable_archive_lifecycle = true
```

Apply and re-audit. **Expected:** 4 findings. `CAP-005` gone. Score 78.

### Step 3d — enable the Lambda error alarm

```hcl
enable_lambda_alarm = true
```

Apply and re-audit. **Expected:** 3 findings. `CAP-009` gone. Score 88.

---

## Step 4 — turn on the paid guardrails

### Step 4a — dashboard

```hcl
enable_dashboard = true
```

**Expected:** 2 findings. `CAP-010` gone. Score 92. Dashboard cost: $3/mo flat.

### Step 4b — Athena

```hcl
enable_athena_table = true
```

**Expected:** 1 finding. `CAP-011` gone. Score 96. Athena cost:
near-zero at the archive's size.

---

## Step 5 — add `suppressions.yaml`

The archive at this point contains no reports and no suppressions
file. `CAP-008` fires. Upload the shipped template:

```bash
cat > /tmp/suppressions.yaml <<'YAML'
# Documented exceptions with review dates.
# Every entry MUST carry a review_by field (ISO 8601 with timezone),
# or CAP-012 fires as soon as the review_by is in the past.
#
# Example (commented out):
# suppressions:
#   - check_id: COST-005
#     resource_id: vol-known-orphan-1
#     reason: "Test volume for Q3, will be removed with the workload."
#     review_by: "2025-10-01T00:00:00Z"

suppressions: []
YAML

aws s3 cp /tmp/suppressions.yaml \
  "s3://$(cd ../terraform && tofu output -raw archive_bucket_name)/suppressions.yaml" \
  --profile bootcamp
```

Re-audit. **Expected:** 0 findings. Score 100. Grade A.

**Wait — the score jumped to 100 without steps 6 or 7?** Correct.
`CAP-015` (git remote) and `CAP-016` (unread report) both require the
archive to CONTAIN reports before they can fire. It is empty at this
point. Step 7 is what populates it and makes those checks
non-hypothetical.

---

## Step 6 — configure git remote metadata

The runner Lambda emits reports with a `git_remote` field taken from
its environment. Set it before you invoke the runner or CAP-015 will
fire against the first report you produce.

**Option A: environment variable via Lambda console.**

```bash
aws lambda update-function-configuration --profile bootcamp --region us-east-1 \
  --function-name "$(cd ../terraform && tofu output -raw runner_function_name)" \
  --environment "Variables={ARCHIVE_BUCKET=$(cd ../terraform && tofu output -raw archive_bucket_name),REGION=us-east-1,ENABLED_DAYS=09,GIT_REMOTE=github.com/careerbytecode/aws-bootcamp}"
```

**Option B: tag the Lambda.** The runner's report shape can be
extended to read Lambda tags at cold start; the current shipped
runner reads `GIT_REMOTE` from env only.

---

## Step 7 — trigger a manual invocation to seed the archive

```bash
aws lambda invoke --profile bootcamp --region us-east-1 \
  --function-name "$(cd ../terraform && tofu output -raw runner_function_name)" \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /dev/stdout | jq .
```

Verify one report landed:

```bash
aws s3 ls --recursive --profile bootcamp \
  "s3://$(cd ../terraform && tofu output -raw archive_bucket_name)/reports/"
```

Expected: one JSON at `reports/day=09/year=YYYY/month=MM/day=DD/<ts>.json`.

If the invocation returned an error, check the runner's log group:

```bash
aws logs tail --profile bootcamp --region us-east-1 --since 5m \
  "/aws/lambda/$(cd ../terraform && tofu output -raw runner_function_name)"
```

Common cause: the runner's IAM role does not include the read
permissions Day 09 needs for the account it audits.

---

## Step 8 — re-run the auditor. This is STATE B.

```bash
python capstone_audit.py --profile bootcamp --region us-east-1 \
  --archive-bucket "$(cd ../terraform && tofu output -raw archive_bucket_name)"
```

**Expected:** 0 findings. Score 100/100. Grade A.

Save it:

```bash
python capstone_audit.py --profile bootcamp --region us-east-1 \
  --archive-bucket "$(cd ../terraform && tofu output -raw archive_bucket_name)" \
  --format json --quiet > state-b-findings.json
```

**Do not tear down yet.** Step 9 is the whole point of the day.

---

## Step 9 — STATE C: unchanged programme, 30 days later

The programme is in STATE B. It will stay in STATE B until:

- **CAP-003** fires: an EventBridge invocation fails to fire and the
  newest report ages past `schedule_interval_days * 1.5 = 10.5 days`.
- **CAP-012** fires: any suppression's `review_by` date passes.
- **CAP-016** fires per unread report: after 7 days, the SLA on
  reading kicks in.

In a real deployment none of these need any action to fire. In the lab
you can either wait 30 days, or reproduce STATE C in a test:

```bash
cd lab/python
python3 -m unittest tests.test_checks.TestContractTotals.test_state_c_worse_than_state_a -v
```

Expected output:

```
test_state_c_worse_than_state_a (test_checks.TestContractTotals.test_state_c_worse_than_state_a) ...
The day's central pedagogical demonstration.

STATE A (all toggles off, informed bad posture): 54/100 D.
STATE C (30 days after B, unchanged, silent break): 0/100 F.

Score DROPS despite no configuration change. The scheduler
stopped silently, reports piled up unread, one suppression
aged past review. Every symptom is a lack of activity.
... ok

Ran 1 test in 0.001s

OK
```

The test asserts:
- Exactly 6 findings.
- Exactly 120 points (floored to 0 in the score).
- The fingerprint: CAP-003 × 1, CAP-012 × 1, CAP-016 × 4.
- `assertLess(score_c, score_a)` — the day's thesis in one line of test code.

**That is the entire day, verified programmatically.**

---

## Step 10 — enable reference-arch (optional, expensive)

```hcl
enable_reference_arch = true
```

Apply. The reference-arch module provisions a VPC, gateway endpoints,
a t3.micro instance with gp3 root, an S3 bucket with lifecycle +
versioning, and a CloudWatch log group with retention — all sized to
score 100/100 on every prior day. Cost: **~$210/month** while it
exists.

The auditor now has drift to check. Run one of the prior-day audits
against the reference-arch:

```bash
cd ../../day-09-cost-optimization-ai-recommendations/lab/python
python cost_audit.py --profile bootcamp --region us-east-1 --prefix cbc-day10-refarch
```

Expected: 0 findings, 100/100. That's what "reference" means.

If you change something in the reference-arch (say, turn off
versioning on its S3 bucket), the next run of Day 09's audit against
`cbc-day10-refarch` will produce a finding. Day 10's `CAP-014` catches
that state.

**Disable it before you continue** unless you plan to keep it around:

```hcl
enable_reference_arch = false
```

Apply.

---

## Step 11 — tear down

See `teardown-checklist.md` for the priority-ordered list. Two things
to emphasise here:

**Disable `enable_reference_arch` FIRST**, if you had it on. That
module contains the most expensive resources; letting `tofu destroy`
handle it in one pass is fine but slow, and if the destroy fails
partway, you want the reference-arch already gone.

**Empty the archive bucket** before destroy. Terraform's default is to
refuse to destroy a non-empty bucket. The archive contains the JSON
reports plus (if you turned on Athena) the `queries/` prefix from
Athena's result cache.

```bash
aws s3 rm --recursive --profile bootcamp \
  "s3://$(tofu output -raw archive_bucket_name)/"

tofu destroy
```

---

## Common failures

**"CAP-001 fires when I have a schedule."**

The auditor filters rules by name prefix (`cbc-day10-`) AND by whether
they target the runner Lambda. A rule named differently, or a rule
targeting a different Lambda, does not count. Check with:

```bash
aws events list-rules --profile bootcamp --region us-east-1 \
  --query 'Rules[?starts_with(Name, `cbc-day10-`)].[Name,ScheduleExpression,State]'
```

**"CAP-004 stays firing even after `enable_archive_versioning = true`."**

Give it a minute. The S3 versioning status API is eventually
consistent. Re-run in 30 seconds.

**"CAP-006 fires against a resource I already fixed."**

`CAP-006` looks at findings in the ARCHIVE. Fixing the underlying
resource does not retroactively clean the archive. Next audit run
will not include the finding for the fixed resource; CAP-006 recovers
on the run AFTER that.

**"CAP-016 fires 4 times."**

That's a STATE C manifestation. Either you actually left the archive
alone for 30 days (well done, the demonstration is real), or the test
fixture is what you looked at. To resolve: acknowledge each report by
tagging its object:

```bash
KEY="$(aws s3api list-objects-v2 --profile bootcamp --bucket "$BUCKET" --prefix reports/ --query 'reverse(sort_by(Contents, &LastModified))[0].Key' --output text)"
aws s3api put-object-tagging --profile bootcamp --bucket "$BUCKET" --key "$KEY" \
  --tagging 'TagSet=[{Key=Acknowledged,Value=true}]'
```

**"The Lambda times out."**

The default timeout is 300 seconds. Some accounts with many resources
take longer. Increase `lambda_timeout_seconds` in `terraform.tfvars`
up to 900 (Lambda's ceiling).

**"CAP-014 fires despite the reference-arch scoring 100 on every prior audit."**

The check compares against `stack["reference_arch_findings_by_day"]`,
which the auditor populates by RUNNING each prior day's audit against
reference-arch resources. If the day's module is not present in the
runner's deployment package, the audit for that day is silently
skipped. Check the runner's log group for "module for day XX not
present" messages.

---

## Where to go next

- If everything worked: `interview-qa.md` has ten questions with
  worked answers that verify you understood the composition thesis.
- If the auditor is misbehaving, `trainer-notes.md` has the common
  learner failures with resolution steps.
- If you want to run this on a real account (recommended),
  `audit-cadence-observations.md` is a template for recording what
  you actually see. That is what makes the material stick.

---

## The LOCKED contract

<!-- CONTRACT-BEGIN -->
DAY 10 FINDING CONTRACT — LOCKED AT CP2
=============================================================================
This block is reproduced identically in five places. Change one, change all
five: README.md, lab/README.md, lab/terraform/outputs.tf (finding_contract),
lab/python/capstone_audit.py (module docstring), lab/python/tests/test_checks.py.

Weights are the repo-wide ones, identical to Days 03 through 09:
CRITICAL 25, HIGH 10, MEDIUM 4, LOW 1, INFO 0. Score is 100 minus the sum,
floored at 0. Grades: 90+ A, 75+ B, 60+ C, 40+ D, below that F.

Day 10 uses all four severities except INFO, matching Day 09. There is one
LOW (CAP-015, git remote metadata) because the finding is a housekeeping
detail that makes reports traceable but doesn't degrade the audit's
correctness. There are TWO CRITICALs (CAP-006 cross-cutting risk, CAP-016
unread report) because those are the two failure modes where the audit
programme itself is broken rather than just incomplete.

STATIC STATE — after terraform apply with the shipped defaults
(all enable_* toggles false, no suppressions file uploaded, no reports
in the archive yet).

  ID       SEVERITY   W   N  PTS  SOURCE RESOURCE
  -------  --------  --  --  ---  ---------------------------------------------
  CAP-001  HIGH      10   1   10  account - no EventBridge schedule
  CAP-002  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
  CAP-003  HIGH      10   0    0  none - SILENT BY SITUATION, see below
  CAP-004  HIGH      10   1   10  aws_s3_bucket.archive - versioning suspended
  CAP-005  MEDIUM     4   1    4  aws_s3_bucket.archive - no lifecycle rule
  CAP-006  CRITICAL  25   0    0  none - SILENT BY SITUATION, see below
  CAP-007  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
  CAP-008  MEDIUM     4   1    4  account - no suppressions.yaml
  CAP-009  HIGH      10   1   10  aws_lambda_function.runner - no error alarm
  CAP-010  MEDIUM     4   1    4  account - no CloudWatch dashboard
  CAP-011  MEDIUM     4   1    4  account - no Athena table over archive
  CAP-012  HIGH      10   0    0  none - SILENT BY SITUATION, see below
  CAP-013  MEDIUM     4   0    0  none - SILENT BY DESIGN, see below
  CAP-014  MEDIUM     4   0    0  none - SILENT BY DESIGN, see below
  CAP-015  LOW        1   0    0  none - SILENT BY SITUATION, see below
  CAP-016  CRITICAL  25   0    0  none - SILENT BY SITUATION, see below
  -------  --------  --  --  ---  ---------------------------------------------
  TOTALS                    7   46

  SEVEN findings from SIXTEEN checks. Nine checks are silent here —
  two by design (CAP-013, CAP-014) and seven by situation (CAP-002,
  CAP-003, CAP-006, CAP-007, CAP-012, CAP-015, CAP-016).

  Score: 100 - 46 = 54/100. Grade D.

  SEVERITY HISTOGRAM of the 16 checks: 2 CRITICAL, 5 HIGH, 8 MEDIUM,
  1 LOW, 0 INFO.

THE FOUR STATES

  STATE                                        FINDINGS  POINTS    SCORE  GRADE
  -------------------------------------------  --------  ------  -------  -----
  A  Static: apply done, all toggles off,
     no history, no suppressions                     7      46   54/100      D
  B  Live: all toggles on, suppressions file
     present with review dates, git remote
     configured, at least one report written         0       0  100/100      A
  C  Thirty days after B, WITH NOTHING
     CHANGED - scheduler stopped 14 days ago,
     four weekly reports piled up unread,
     one suppression past review                     6     120    0/100      F
  -------------------------------------------  --------  ------  -------  -----
  D  Reference build: everything in B, plus
     weekly triage rota where each report is
     acknowledged within its SLA and
     suppressions are reviewed on cadence            0       0  100/100      A

  STATE C IS DRAMATICALLY WORSE THAN STATE A. Read that twice.

  An operator in STATE A has a score of 54/100 and knows the ambient
  audit programme has not been set up. Bad posture, but informed.

  An operator in STATE C has a score of 0/100 and believes they have
  working cost and security governance. The runner is deployed, the
  alarms are wired, the dashboard exists, the archive is populated,
  the suppressions are documented. What has silently happened is:

    - The EventBridge rule stopped firing about two weeks ago. Nobody
      notices because there is no "the scheduler didn't fire"
      notification - the absence of activity is the failure mode.
    - Four consecutive weekly reports piled up unread in the archive
      (CAP-016 fires 4 times at 25 points each = 100 points). Nobody
      triaged them because the weekly review meeting was cancelled
      for month-end.
    - One suppression's review_by date passed 15 days ago. Nobody
      revisited it (CAP-012 fires with 10 points). The exception is
      now an ignored finding without an active decision.
    - CAP-003 fires with 10 points to name the scheduler silence
      explicitly.

  Total: 120 points, floored at 0/100. Grade F.

  STATE C IS THE INFORMED VERSION OF THE STATE-A OPERATOR'S IGNORANCE,
  compounded by two weeks of accumulated debt. This is the day's
  thesis. Days 01-09 audit CONFIGURATION - the state of a resource
  at a moment in time. Day 10 audits PROCESS - whether the
  configuration auditing is still happening at all.

  A process that used to work and stopped is a worse posture than a
  process that was never started. STATE C is the shape of an
  organisation that "did FinOps" for a quarter and then stopped
  without noticing.

SILENT BY DESIGN — CAP-013 (SLA per severity not defined) and CAP-014
(reference-arch drift).

  CAP-013: The sla_days_by_severity variable's type constraint requires
  all four severity keys (critical, high, medium, low) to be present in
  the object literal. The default value provides all four. The
  validation block requires them to be monotonically non-decreasing.
  There is no path through this Terraform that produces a stack with
  an undefined-per-severity SLA. So the check stays silent against
  this stack. It will fire immediately on a deployment that imports
  the module and passes sla_days_by_severity = {} or on a real
  organisation that has "an SLA" but where the ambiguity between
  severities is where the missed acknowledgements accumulate.

  CAP-014: When enable_reference_arch = false, no resource in this
  stack claims to be a reference. The check cannot fire because it
  has nothing to compare against. When enable_reference_arch = true
  the check becomes a real comparison — does running each prior day's
  audit against the composed module produce zero findings, as the
  module claims. Answering "yes" every time is the definition of
  "reference"; the first "no" is CAP-014 firing.

  Both silent-by-design classifications are structural facts about
  this stack, not judgements about the account.

SILENT BY SITUATION — CAP-002, CAP-003, CAP-006, CAP-007, CAP-012, CAP-016.

  CAP-002 (schedule interval > 7 days): silent because no schedule
  exists in STATE A. schedule_interval_days is 7 by default; when
  enable_scheduler goes true, the rate() expression is
  rate(7 days), and CAP-002 stays silent because 7 is the boundary,
  not above it. Change schedule_interval_days to 14 and this check
  fires without touching enable_scheduler.

  CAP-003 (last invocation age > interval * 1.5): silent because no
  invocations have happened. In STATE B (after one manual invocation
  to seed the archive), the check is silent because the invocation is
  fresh. In STATE C the check fires because the last invocation is
  now older than 10.5 days (interval 7 * 1.5), and nothing has
  re-fired the scheduler.

  CAP-006 (cross-cutting risk): silent because there are no prior-day
  findings in the archive. Requires at least two report objects
  referencing the same ARN across different days. Silent forever on
  an audit-runner that only enables day 09 (the shipped default);
  becomes possible once ENABLED_DAYS is expanded.

  CAP-007 (findings not deduplicated across audits): silent because
  no reports exist yet. Once reports exist, this checks whether the
  same finding appears in consecutive reports with different resource
  IDs due to normalisation drift.

  CAP-012 (suppressions past review): silent because no suppressions
  exist. Once suppressions.yaml is uploaded and its entries have
  review_by fields, this fires as those dates pass. STATE C's
  manifestation.

  CAP-016 (report unread past SLA): silent because no reports exist.
  Once reports exist and time passes, this fires as the newest report's
  age crosses report_unread_days without an acknowledgement API call.
  This is the CRITICAL that carries the day's thesis.

  CAP-015 is the git-remote-metadata check on the newest report in
  the archive. In STATE A there are no reports at all, so the check
  has nothing to inspect - silent by situation. Once STATE B is
  reached and reports start landing, CAP-015 fires immediately if the
  runner Lambda was deployed without ENABLED_GIT_REMOTE or a
  GitRemote tag.

  NOTHING HAS TO CHANGE FOR ANY OF THESE TO STOP BEING SILENT except
  the passage of time and the population of the archive.

THE DIFFERENCE MATTERS. Silent-by-design tells you something about
the auditor: it cannot fire, so its silence is a property of the
tool. Silent-by-situation tells you nothing about the auditor and
everything about today's account. "We have no findings" and "we
have nothing to find" are different states that render identically
in every report. Never read the second as the first.

CHECK INTERACTIONS THAT ARE DELIBERATE, NOT BUGS

  CAP-001 AND CAP-003 LOOK LIKE THE SAME CHECK AND ARE NOT. CAP-001
  catches the absence of a schedule at ALL. CAP-003 catches a schedule
  that EXISTS but hasn't fired. The first fault is "we never set up
  automation"; the second is "automation stopped, nobody noticed".
  On this stack CAP-001 fires in STATE A and CAP-003 fires in
  STATE C, and they are the two sides of the same governance failure
  at two different lifecycles.

  CAP-004 AND CAP-005 ARE THE SAME IDEA AT DIFFERENT ANGLES. CAP-004
  asks "can I answer when did this finding first appear" — that
  requires versioning. CAP-005 asks "will this archive itself become
  expensive over time" — that requires lifecycle. Both are S3-bucket
  properties, both fire independently, both are trivial one-line
  Terraform fixes. Bucketing them together as "S3 hygiene" would
  confuse two different questions.

  CAP-008 AND CAP-012 ARE A LIFECYCLE. CAP-008 fires when there is no
  suppression file at all — the account has never articulated the
  exceptions it wants the auditor to skip. CAP-012 fires when the
  exceptions are present but stale — the account articulated them
  once and never revisited. On a mature account, CAP-008 fires briefly
  after the first audit and then never again; CAP-012 fires on cadence
  as review dates expire. These are the two phases of "exception
  management works".

  CAP-009 AND CAP-016 ARE THE SAME PATTERN AT TWO LAYERS OF THE STACK.
  CAP-009 asks "does the Lambda have an error alarm" — a technical
  failure of the runner. CAP-016 asks "does anybody read the reports"
  — an organisational failure to consume the runner's output. A
  stack where CAP-009 is silent (alarm exists) and CAP-016 is
  firing (nobody reads reports) is technically working audit
  infrastructure that produces no organisational value. That is the
  shape of most cost programmes.

  CAP-010 AND CAP-011 ARE THE SAME QUESTION AT TWO TIME HORIZONS.
  CAP-010 (dashboard) asks "is there ONE URL a stakeholder can
  click today to see the current state". CAP-011 (Athena) asks "can
  an operator answer HISTORICAL questions about what the state used
  to be". Both are queryability questions, in tension with each
  other: dashboards give right-now, Athena gives history-back-to-
  whenever. Neither substitutes for the other.

  CAP-006 IS THE ONLY CHECK THAT LOOKS AT MORE THAN ONE DAY'S
  FINDINGS AT ONCE, and it is deliberately narrow. It only fires when
  the same ARN appears in findings from TWO OR MORE prior-day audits.
  A resource with a Day 03 IAM overshare AND a Day 08 no-backup
  finding is a resource with cross-cutting risk — remediating one
  leaves the other, and shipping either fix without the other is
  shipping a partial improvement. Ordinary within-day findings are
  not what this check is for; the whole prior-day audit surface
  already covers those.

  CAP-016 IS ONE OF TWO CRITICALS BECAUSE IT IS THE ONLY CHECK WHOSE
  FAILURE MEANS "THE WHOLE PROGRAMME HAS STOPPED WORKING". Every
  other Day 10 finding is a specific infrastructure defect. CAP-016
  is the meta-check: the machine is running, the alerts are firing,
  nobody is reading them. A stack where every other check is green
  and CAP-016 is red is an organisation that has built cost
  governance and then stopped using it, which is one of the largest
  failure modes in the industry.

  THIS IS THE SAME STRUCTURAL POINT DAY 09 MADE with COST-016, on the
  next layer up. Day 09 caught "nobody reads AWS's cost anomalies".
  Day 10 catches "nobody reads YOUR audit's reports". The same
  failure mode, two layers of the stack, two consecutive days making
  it undeniable.
<!-- CONTRACT-END -->
