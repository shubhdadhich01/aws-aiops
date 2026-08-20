# Day 10 — Capstone: Composition & Continuity

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

---

## The thesis, in one line

**An audit is not a program until it runs on Tuesday morning when
nobody clicked it.**

Days 01-09 built nine auditors, each of which catches a specific class
of configuration fault. Day 10 is the first day that is not about
CONFIGURATION at all — it is about whether the AUDITING itself is
still happening. A configuration audit that ran three months ago is a
report about an account that no longer exists. A "we have cost
governance" claim from a team whose last audit ran during a previous
person's tenure is a claim about a decision nobody re-examined, on the
next layer of the stack above Day 09.

Day 10's failure mode is quieter than every previous day's. Days 01-09
fail on OBSERVATIONS. Day 10 fails on THE ABSENCE OF OBSERVATIONS. Every
symptom of Day 10's central failure is a lack of activity, and lack of
activity is invisible unless something is specifically watching for it.

## What today is about

Day 10 is a capstone in two dimensions:

**Composition.** Each prior day's audit is a standalone tool. The
audit-runner Lambda deployed today imports each day's module by
convention and runs them on a schedule against an account. The
Terraform in `lab/terraform/` provisions the scheduler, the Lambda,
the S3 archive it writes reports into, the alarms on its errors, the
optional dashboard and Athena table for querying history, and — under
an `enable_reference_arch = true` toggle — a composed workload that by
construction passes every prior day's audit at 100/100.

**Continuity.** The auditor built today does not audit resources. It
audits the AUDIT PROGRAMME. Is a schedule defined? Is a report being
written? Is the archive durable and queryable? Is anyone reading the
reports? Are suppressions being reviewed on cadence? All 16 CAP-*
checks are about the health of the process, not about the health of
any specific resource.

That difference is why Day 10's STATE C is dramatically worse than its
STATE A — nine days of prior work do not produce Day 10's failure. A
programme that ran correctly for a month and then silently stopped is
the only shape STATE C has.

## The four states you will produce

By the end of this lab, the auditor will have shown you four scores:

| State | What it is                                                        | Findings | Points | Score | Grade |
|-------|-------------------------------------------------------------------|----------|--------|-------|-------|
| A     | Static, right after `terraform apply`, all enable_* toggles off  | 7        | 46     | 54    | D     |
| B     | Live, all toggles on, suppressions file present, git remote wired| 0        | 0      | 100   | A     |
| C     | Thirty days after B, **with nothing changed**                    | 6        | 120    | 0     | F     |
| D     | B, plus weekly triage rota with acknowledgements recorded on time| 0        | 0      | 100   | A     |

**STATE C IS DRAMATICALLY WORSE THAN STATE A.** Read that twice.

An operator in STATE A has a score of 54/100 and knows the ambient
audit programme has not been set up. Bad posture, but informed.

An operator in STATE C has a score of 0/100 and believes they have
working cost and security governance. What has silently happened:

- The EventBridge rule stopped firing about two weeks ago. Nobody
  noticed because there is no "the scheduler didn't fire"
  notification — the absence of activity is the failure mode.
- Four consecutive weekly reports piled up unread in the archive
  (CAP-016 fires 4 times at 25 points each = 100 points).
- One suppression's `review_by` date passed 15 days ago and was not
  revisited (CAP-012 fires with 10 points).
- CAP-003 fires with 10 points to name the scheduler silence
  explicitly.

Total: 120 points, floored at 0/100. Grade F.

**STATE C is the informed version of STATE A's ignorance, compounded
by two weeks of accumulated debt.** That is the whole day.

## Sixteen checks, two CRITICAL, one LOW

| ID       | Severity  | What it catches                                              |
|----------|-----------|--------------------------------------------------------------|
| CAP-001  | HIGH      | No EventBridge schedule targets the audit-runner             |
| CAP-002  | MEDIUM    | Schedule interval > 7 days                                   |
| CAP-003  | HIGH      | Last invocation age > interval * 1.5 (scheduler silent)      |
| CAP-004  | HIGH      | Audit-report archive bucket not versioned                    |
| CAP-005  | MEDIUM    | Audit-report archive has no lifecycle rule                   |
| CAP-006  | CRITICAL  | Cross-cutting risk — same ARN in ≥2 prior-day audits         |
| CAP-007  | MEDIUM    | Findings not deduplicated across consecutive reports         |
| CAP-008  | MEDIUM    | No baseline suppressions file in the archive                 |
| CAP-009  | HIGH      | No CloudWatch alarm on runner Lambda errors                  |
| CAP-010  | MEDIUM    | No CloudWatch dashboard for the programme                    |
| CAP-011  | MEDIUM    | No Athena table over the archive                             |
| CAP-012  | HIGH      | Suppressions past their `review_by` date                     |
| CAP-013  | MEDIUM    | SLA per severity not defined                                 |
| CAP-014  | MEDIUM    | Reference architecture no longer scores 100/100              |
| CAP-015  | LOW       | Reports lack `git_remote` metadata                           |
| CAP-016  | CRITICAL  | Latest report un-acknowledged past its SLA                   |

Two of these (`CAP-013`, `CAP-014`) are **silent by design** against
this stack — the Terraform structurally cannot produce the fault.
Seven are **silent by situation** in STATE A because a freshly-applied
stack cannot produce them yet; three of the seven (`CAP-003`, `CAP-012`,
`CAP-016`) become the STATE C decay findings on the same programme
thirty days later without anything having changed.

**Two CRITICALs** on this day:
- `CAP-006` catches CROSS-CUTTING RISK — a resource with defects across
  ≥2 audit surfaces (e.g. Day 03 IAM overshare + Day 08 no-backup on
  the same table). No single team's audit sees the whole picture; this
  check is the composition.
- `CAP-016` catches UNREAD REPORTS — the day's central thesis. It is
  the direct next-layer-up analog of Day 09's COST-016, which caught
  "nobody reads AWS's cost anomalies". Two consecutive days, two
  layers of the stack, one failure mode.

The one LOW (`CAP-015`, `git_remote` metadata missing) is here because
it's a housekeeping detail — reports without version metadata are
harder to compare across time, but the audit itself still functions.

## What you will build

The Terraform stack, at `lab/terraform/`, provisions:

- One S3 bucket for the audit-report archive, with block-public,
  AES-256 encryption, and toggleable versioning + lifecycle.
- One IAM role for the audit-runner Lambda with `SecurityAudit` +
  `AWSBillingReadOnlyAccess` attached, plus a narrow inline policy
  scoped to writing `reports/*` in the archive and its own log group.
- One Lambda function (arm64, python3.12) that imports each configured
  day's audit module, runs it, and writes one JSON report per
  invocation to `reports/day=NN/year=YYYY/month=MM/day=DD/<ts>.json` —
  a partitioned S3 layout that Athena queries directly.
- One CloudWatch log group with retention set to 30 days by default (so
  the runner itself doesn't fire Day 09's COST-013).
- One SNS topic + email subscription for the Lambda error alarm and
  CAP-016 unread-report notifications.
- One EventBridge rule (optional, `enable_scheduler`) firing on
  `schedule_interval_days`.
- One CloudWatch alarm on the Lambda's error metric (optional,
  `enable_lambda_alarm`).
- One CloudWatch dashboard summarising the last run (optional,
  `enable_dashboard`).
- One Athena database + workgroup over the archive (optional,
  `enable_athena_table`).
- A reference-arch module (optional, `enable_reference_arch`) that
  composes safe-defaults from Days 01-09 into a workload that scores
  100/100 on every prior day. `CAP-014` uses this as its drift target.

The Terraform stack has 22 variables, 27 resources in the main stack,
and 25 resources in the reference-arch module.

## What you will run

The Python auditor (`lab/python/capstone_audit.py`) reads the ambient
audit state through boto3 — the EventBridge schedule, the archive
bucket's versioning and lifecycle, the CloudWatch alarms and
dashboards, the Athena databases, the S3 objects representing archived
reports, the suppression file, the runner Lambda's tags and env — and
produces one of three outputs (table, JSON, or CSV) plus a compliance
score.

Everything the checks reason about arrives through `collect()` into a
normalised dict. Every check is a pure function over that dict, so all
16 checks are testable without AWS credentials. The 47 unit tests in
`lab/python/tests/test_checks.py` prove STATE A, STATE B and STATE C
are the exact scores the contract claims.

```
$ python3 capstone_audit.py --profile bootcamp --region us-east-1 \
    --archive-bucket cbc-day10-archive-abc123
Collecting ambient-audit posture...
  · EventBridge schedule targeting the runner
  · S3 archive, suppressions, reports
  · CloudWatch alarms, dashboards, Athena
Running checks...

====================================================================================================
  CAPSTONE AUDIT
  CareerByteCode · Day 10 · Composition & Continuity
  2025-06-01 12:00:00 UTC
====================================================================================================

  Scanned: 0 schedule rule(s) · 0 archived report(s) · 0 suppression entr(y/ies) ·
  0 alarm(s) · 0 dashboard(s) · 0 Athena db(s)

  ------------------------------------------------------------------------------------------------
  SEVERITY   CHECK      RESOURCE                          FINDING
  ------------------------------------------------------------------------------------------------
  HIGH       CAP-001    account/123456789012              No EventBridge schedule targets the au...
  HIGH       CAP-004    cbc-day10-archive-abc123          Audit archive bucket is not versioned
  HIGH       CAP-009    cbc-day10-runner                  No CloudWatch error alarm on the audit...
  MEDIUM     CAP-005    cbc-day10-archive-abc123          Audit archive bucket has no lifecycle ...
  MEDIUM     CAP-008    account/123456789012              No suppressions file present in the a...
  MEDIUM     CAP-010    account/123456789012              No CloudWatch dashboard for the audit ...
  MEDIUM     CAP-011    account/123456789012              No Athena database over the audit-rep...
  ------------------------------------------------------------------------------------------------

  ...
  COMPLIANCE SCORE: 54/100   D — would fail an audit
====================================================================================================
```

## Prerequisites

You are ready if:

- You have completed Days 01-09.
- Your `bootcamp` AWS CLI profile carries `SecurityAudit`,
  `ReadOnlyAccess`, AND `AWSBillingReadOnlyAccess`. The last one is
  what most cost/audit labs skip.
- You have `terraform` or `tofu` version ≥ 1.5.
- You have `boto3` installed via `pip install -r lab/python/requirements.txt`.
- You have set `notification_email` in `terraform.tfvars` to an inbox
  you actually read. This day depends on emails arriving — differently
  from prior days, where you could ignore the SNS confirmation.

## Quick start

```bash
cd lab/terraform
cp terraform.tfvars.example terraform.tfvars

# Edit terraform.tfvars — set at minimum:
#   notification_email = "your-email@example.com"
#   owner              = "your-name"

tofu init
tofu apply

# Confirm the SNS subscription in your inbox. Nothing works until you do.

cd ../python
pip install -r requirements.txt
python capstone_audit.py --profile bootcamp --region us-east-1 \
  --archive-bucket "$(cd ../terraform && tofu output -raw archive_bucket_name)"
```

Then read `lab/README.md` and walk through the eleven-step sequence,
which takes about 2 hours if you do everything including the
reference-arch and STATE C demonstration.

## Repository layout

```
day-10-capstone-composition-continuity/
├── README.md                          # this file
├── sync_contract.py                   # verify or write the contract to all copies
└── lab/
    ├── README.md                      # the walkthrough — read this next
    ├── interview-qa.md                # ten interview questions with worked answers
    ├── teardown-checklist.md          # priority-ordered cleanup
    ├── trainer-notes.md               # instructor notes
    ├── audit-cadence-observations.md  # template for real-programme observations
    ├── terraform/
    │   ├── providers.tf, variables.tf, main.tf, outputs.tf
    │   ├── terraform.tfvars.example, .gitignore
    │   ├── lambda/
    │   │   └── runner.py              # the ambient audit orchestrator
    │   └── reference-arch/
    │       └── main.tf                # composed Days 01-09 safe-defaults module
    └── python/
        ├── capstone_audit.py          # 16 CAP checks, three renderers, argparse CLI
        ├── requirements.txt, .gitignore
        ├── tests/
        │   ├── __init__.py
        │   └── test_checks.py         # 47 unit tests, credentials-free
        └── challenge/
            ├── generate_challenge.py  # regenerator (--check verifies drift)
            └── capstone_audit_challenge.py  # generated scaffold
```

## What "challenge mode" is

`lab/python/challenge/capstone_audit_challenge.py` is the reference
file with every check body stubbed out. The docstrings are the
specification. Point the test runner at it:

```bash
cd lab/python
CAPSTONE_AUDIT_MODULE=capstone_audit_challenge PYTHONPATH=challenge \
  python3 -m unittest discover -s tests -v
```

You will see 23 failures and 24 passes. The 24 are the "silent" tests
(empty return matches empty return) plus renderers plus helpers plus
silent-by-design invariants for CAP-013 and CAP-014. Every "fires"
test fails, along with the contract totals and the score. Your job is
to make them green.

Time budget: about 2-3 hours if you work through it methodically. The
long ones are CAP-006 (cross-day ARN correlation), CAP-007 (finding
normalisation drift detection), CAP-012 (parsing suppressions and
comparing `review_by` against the injected clock), and CAP-016 (the
CRITICAL, whose logic is small but whose message needs to be right).

## What "the contract" is, and why the same block appears in five files

The finding contract is duplicated into five files by design, so a
reader of any one of them sees the same totals, states, and
interactions in the same words. Duplication is a maintenance risk;
`sync_contract.py` exists to make it cheap:

```bash
# Verify all copies are identical (default). Exit 1 on drift.
python3 sync_contract.py

# Rewrite all copies to match the source-of-truth (capstone_audit.py's docstring).
python3 sync_contract.py --write

# Print the extracted source-of-truth to stdout.
python3 sync_contract.py --show
```

The five copy sites are: this file (below), `lab/README.md`,
`lab/terraform/outputs.tf` (`finding_contract` output),
`lab/python/capstone_audit.py` (module docstring — the source-of-truth),
and `lab/python/tests/test_checks.py` (module docstring).

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

---

## Now go read `lab/README.md`

The lab walkthrough is where the actual work is. It follows the same
11-step sequence as the `next_steps` output from `terraform output`,
and each step names which check IDs you should see move.

---

_© CareerByteCode. Instructor coordination via Sonali Kurade. Director:
Sangeetha._
