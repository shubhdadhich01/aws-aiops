# Day 10 — Trainer Notes

Notes for the person delivering this day live. Reading these gives you
the things a first-time learner does not yet know to look for, in the
order they come up during a real delivery.

Day 10 is the CAPSTONE. The delivery has one job that dominates every
other: make STATE C dramatically visible. Everything else is context.

---

## Time budget for a live class

- **90 minutes** if you race through STATE A → STATE B and spend the
  remaining time demonstrating STATE C via the tests.
- **3 hours** if you want the class to actually finish the challenge
  scaffold and see 47/47 green on their own machines.
- **1 full day** if you enable reference-arch and demonstrate CAP-014
  against a genuine drift scenario.

The 90-minute version is the modal one. Do not skip the STATE C
demonstration in that version — it is the one memorable moment of the
capstone.

---

## The one moment that matters

**Running the STATE C test aloud.** This is the day's thesis, and
it's the moment the whole ten-day series lands or does not.

The sequence:

1. The class has reached STATE B. Score 100/100. Green everywhere.
2. You say: "the account will not stay this way."
3. You run:

   ```bash
   python3 -m unittest tests.test_checks.TestContractTotals.test_state_c_worse_than_state_a -v
   ```

4. The test output prints its docstring:

   > The day's central pedagogical demonstration.
   >
   > STATE A (all toggles off, informed bad posture): 54/100 D.
   > STATE C (30 days after B, unchanged, silent break): 0/100 F.
   >
   > Score DROPS despite no configuration change. The scheduler
   > stopped silently, reports piled up unread, one suppression
   > aged past review. Every symptom is a lack of activity.

5. Point at the assertion in the test file:

   ```python
   self.assertLess(score_c, score_a,
                   f"STATE C ({score_c}) should be worse than STATE A "
                   f"({score_a}) — that is the whole point of the day")
   ```

6. Ask: "Which of the six findings needed anyone to CHANGE anything?"
7. The answer is: none. The scheduler stopped fires. Time passed. The
   reports piled up. The suppression aged past review.

The follow-up: "so when should this audit be running?" Answer:
"on a schedule, watched by the runner. The runner is the audit
against the audit. Day 10 is what audits the runner."

---

## Common learner failures

### "The runner Lambda times out"

Default timeout is 300 seconds. On a busy account, some prior-day
audits (especially Day 09 with Cost Explorer) take 60+ seconds each.
If ENABLED_DAYS includes multiple days, they run sequentially and
can add up.

Fix: `lambda_timeout_seconds = 900` in `terraform.tfvars`. That is
Lambda's ceiling.

### "AccessDeniedException on billing APIs"

The runner's IAM role attaches both `SecurityAudit` and
`AWSBillingReadOnlyAccess`. If Day 09's audit fails with
AccessDenied on Budgets or Cost Explorer, the second one didn't
attach.

Verify:

```bash
aws iam list-attached-role-policies --profile bootcamp \
  --role-name cbc-day10-runner
```

If `AWSBillingReadOnlyAccess` isn't in the list, re-apply. Some
regions have taken minutes to propagate.

### "CAP-006 fires against the reference-arch resources"

Only if you enabled reference-arch AND you ran the runner AND the
reference-arch resources ended up in multiple days' findings. That
is usually a real cross-cutting risk, but on the reference-arch it
shouldn't happen — the module is designed to score 100 on every day.

Look at what's firing. If it's Day 09's COST-013 (log group without
retention) on the reference-arch's log group, check whether
`log_retention_days` was passed through the module correctly.
Common cause: the parent stack's variable wasn't propagated in the
module block.

### "I turn on enable_scheduler and CAP-002 fires"

You changed `schedule_interval_days` above 7. CAP-002's threshold is
`> 7`. Setting it to exactly 7 keeps CAP-002 silent; 8 or 14 fires it.

Some orgs need daily audits (`schedule_interval_days = 1`); some
tolerate the drift and prefer weekly. Both are defensible; anything
longer than weekly is usually not.

### "STATE B score is 96 instead of 100"

You forgot step 6 or step 7 of the lab. Either:
- `CAP-015` is firing: the runner Lambda has no `GIT_REMOTE` env var,
  so its reports don't carry the metadata.
- The archive contains a report from BEFORE you set `GIT_REMOTE`, and
  the latest report inherits that (CAP-015 checks the newest).

Fix: update the Lambda env, invoke once more to produce a fresh
report, re-audit.

### "I ran STATE C via the tests and 47 tests pass, but I want to see it live"

Set the clock forward on the fixture, sure. Or age the archive by
manually re-writing an S3 object's LastModified time via a re-put:

```bash
# Get the report content
CONTENT="$(aws s3 cp s3://$BUCKET/reports/day=09/... -)"
# Re-put with a specific date in the invoked_at field — you're
# modifying the JSON, not the S3 metadata (that's not editable)
echo "$CONTENT" | jq '.invoked_at = "2025-05-01T00:00:00Z"' | \
  aws s3 cp - s3://$BUCKET/reports/day=09/...
```

The auditor reads `invoked_at` from the report content, not from S3
LastModified. So editing the JSON directly ages the report from the
auditor's perspective. Not something to do in production; useful for
demonstration.

---

## What to say vs what to type

**Read aloud, don't demonstrate:**
- The 16 CAP check list (too long for a slide).
- The `check_no_schedule` implementation — the class can read it from
  the reference.
- The runner Lambda's IAM policy — narrow scope is the point, the
  code is just JSON.

**Demonstrate, don't read aloud:**
- STATE A → STATE B via 3-4 `tofu apply` cycles (5 minutes each). Do
  this AS THE CLASS WATCHES so the delta from each change is visible.
- The STATE C test (5 seconds; the impact is in what the class
  realises).
- The reference-arch drift scenario if you're doing the 3-hour
  version — turn off versioning on the reference-arch's bucket, run
  Day 09's audit, see the finding, then let Day 10's CAP-014 catch
  it on the next runner invocation.

**Skip if time is short:**
- Step 5 (suppressions.yaml). The mechanism is obvious from the
  variable's description.
- Step 10 (reference-arch). Its point is a cost-heavy demonstration
  the class can rebuild from `main.tf` if they need it.
- The full teardown. Point at the checklist and let them do it after.

---

## Slide-independent narrative structure

If your platform doesn't do slides well, this delivery works from the
terminal only.

1. **Open the top-level README.** Read the thesis line. Read the
   four-states table. 5 minutes.
2. **Show the outputs.tf finding_contract.** Read the STATE A row.
   Read the STATE C row. Point at the "STATE C IS DRAMATICALLY
   WORSE" paragraph. 5 minutes.
3. **`cd lab/terraform && cat terraform.tfvars.example`.** Point out
   the `enable_*` toggles. 3 minutes.
4. **`tofu apply` in the background.** (5 minutes; pre-apply if
   possible.)
5. **`cd ../python && cat capstone_audit.py | grep '^def check_'`.**
   Point out 16 checks. Read ONE docstring — recommend
   `check_report_unread` because it's the day's thesis in a
   docstring. 5 minutes.
6. **Run the auditor.** STATE A. Read the table together. 10 minutes.
7. **Turn on the guardrails one at a time.** 4-6 apply cycles. Read
   the diff between runs. 20 minutes.
8. **Upload suppressions.yaml and invoke the runner.** STATE B.
   10 minutes.
9. **THE STATE C MOMENT.** 10 minutes.
10. **Q&A on the interview questions.** 15 minutes.

Total: ~90 minutes.

For the 3-hour version, add:

11. **Point at the challenge scaffold.** Have the class open it and
    read one docstring. Give them 60 minutes to fix one check of
    their choice. 60 minutes.
12. **Walk through `check_scheduler_silent`.** Read the reference
    together. 30 minutes.

---

## The check-relationship map to draw on the whiteboard

Six interactions the tests explicitly cover. Draw them:

```
   CAP-001 ─────── CAP-003        (absence vs decorative)
      │              │
      HIGH          HIGH
      STATE A       STATE C

   CAP-004 ─────── CAP-005        (versioning vs lifecycle, same bucket)
      │              │
      HIGH          MEDIUM

   CAP-008 ─────── CAP-012        (no-file vs stale-entries)
      │              │
      MEDIUM        HIGH
      STATE A       STATE C

   CAP-009 ─────── CAP-016        (technical vs organisational, layered)
      │              │
      HIGH          CRITICAL

   CAP-016 (per unread report, DELIBERATELY NOT deduplicated)

   CAP-013, CAP-014 (SILENT BY DESIGN in this stack)
```

Once the class sees the layered pattern, Day 10 stops feeling
arbitrary and starts feeling like a natural composition of the
patterns they saw in Days 03-09.

---

## When learners ask about SIEM integration

They will. The reflex when someone sees an audit-runner is "why not
send this to Splunk / DataDog / their SIEM of choice".

The honest answer: you can. The archive is JSON in S3, which any
modern SIEM ingests. Adding a Firehose from the archive bucket to
your SIEM's ingest endpoint would work. Nothing about the runner's
design forecloses this.

What NOT to do: replace the archive with a direct-to-SIEM push. The
archive-first design serves three purposes:

1. **Durability.** SIEMs go down. The archive is your source of
   truth.
2. **Queryability without SIEM cost.** Athena over S3 is $5/TB
   scanned; SIEMs charge per GB ingested. Historical questions get
   asked in Athena.
3. **CAP-016's mechanism.** "Was this report read" is a question the
   archive-tag approach answers. A SIEM alert getting acknowledged
   in the SIEM is not the same fact; you'd have to build the tag-
   sync yourself.

Feed the SIEM from the archive, in addition. Don't replace one with
the other.

---

## When learners ask about multi-account

The runner is single-account. That's the honest scope of Day 10.

The multi-account version is a follow-on lab: a runner deployed to a
central account with `AssumeRole` into each audited account, cross-
account S3 replication for the archive, and a rollup dashboard. All
of it is straightforward; none of it is Day 10.

If time allows, sketch the shape:

```
   Central account
   ├── runner Lambda (assumes cross-account roles)
   ├── central archive bucket
   ├── central dashboard
   └── central Athena workgroup

   Each audited account
   ├── audit role (allow AssumeRole from central runner)
   └── (no per-account infrastructure)
```

That is a lot less infrastructure per audited account, at the cost
of a central account that must be permissive enough to touch
everything. A classic trade-off; both shapes are defensible.

---

## Things that go wrong live but shouldn't

**Terraform state locking**. If a class has 20 learners applying
simultaneously to different accounts, that's fine. If they're
sharing a state file, it's a problem. Make sure each learner has
their own state.

**AWS CLI version**. Athena workgroup features vary by CLI version.
If a learner's `aws athena list-work-groups` fails with an unknown-
parameter error, upgrade to the current CLI.

**Region drift**. `aws configure get region --profile bootcamp` should
return `us-east-1`. Some learners set the region via `AWS_REGION`
env at some point and it stays set; verify explicitly.

**Lambda arm64 unavailability**. The runner is arm64. Some regions
don't have Graviton Lambdas. If a learner is in one such region,
change `architectures = ["x86_64"]` in `main.tf`. This should not
come up in `us-east-1`.

---

## What to note down after each delivery

For your own iteration on this day:

- Which learner was most surprised by STATE C's score. That reaction
  is the pedagogy landing; note it.
- Which check the class needed the most re-explanation on. My money
  is on CAP-006 (cross-cutting risk), because it requires holding
  two mental models simultaneously.
- Whether any learner asked about SIEM integration or multi-account.
  If most do, consider swapping in one of those follow-on labs.
- Any AWS API drift since the last delivery. CloudWatch and Athena
  APIs move faster than the rest of AWS.

Update the docstrings and re-run `sync_contract.py --write`. That is
the mechanism.
