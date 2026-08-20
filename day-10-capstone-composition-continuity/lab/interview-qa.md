# Day 10 — Interview Questions

Ten questions someone might reasonably ask you in a systems design, SRE,
or FinOps interview after Day 10. Each has a worked answer trying to
be what a real answer sounds like rather than a bullet list.

---

## 1. What is the single most important thing Day 10 catches that no prior day catches?

An audit programme that stopped working.

Days 01-09 audit CONFIGURATION — the state of a resource at a moment
in time. If any of them runs correctly today, you have an accurate
picture of that surface today. Day 10 catches the case where NONE of
them ran today — where the whole audit apparatus is deployed, running,
and quietly stopped producing output for reasons that are invisible
from any single day's dashboard.

Concretely: the EventBridge rule got disabled by a security tool, the
Lambda hit a permissions boundary and started erroring silently, the
S3 bucket was destroyed and re-created without versioning, the
suppression file was replaced with a copy from staging. All of these
are shapes of "the runner is not running", "the runner is running but
producing broken output", or "the runner is producing output but
nobody is reading it". None of them appear in Day 03's IAM audit or
Day 08's DR audit. They only appear in a check that specifically
watches the audit programme's health, which is what all 16 CAP checks
collectively do.

---

## 2. Why is STATE C dramatically worse than STATE A?

Because STATE C is the informed operator's ignorance, compounded by
two weeks of accumulated debt.

STATE A is a fresh apply with all guardrails off. Score 54/100. The
operator can read the report and immediately identify what has not
been set up — the checks list the seven specific things missing. Bad
posture, but visible bad posture.

STATE C is thirty days after STATE B. The scheduler was working, the
alarms were wired, the archive was populated, the suppressions were
documented, the dashboard was visible. Then the scheduler silently
stopped firing about two weeks ago. Four consecutive weekly reports
piled up unread in the archive (CAP-016 fires four times at 25 points
each). One suppression's `review_by` date passed 15 days ago and was
not revisited (CAP-012, 10 points). CAP-003 fires with 10 points to
name the scheduler silence explicitly. Total: 120 points, floored at
0/100. Grade F.

The operator, meanwhile, sees a green dashboard from the last
successful run (three weeks ago), no error alarms firing (the runner
isn't running to error), no scheduler-failure notification (because
"scheduler didn't fire" is the failure mode, and there's no built-in
alarm for absence-of-activity). They believe they have cost and
security governance and they have neither.

STATE C is a worse posture than STATE A because it's ignorant AND
overconfident. STATE A is ignorant. The gap between "we have a
programme" and "we don't have a programme" is smaller than the gap
between "we know we don't have a programme" and "we think we have one
and don't".

---

## 3. Why does the runner Lambda import audit modules by convention rather than by explicit dependency?

Convention over explicit dependency lets teams add days without
re-deploying the runner. That is important because the runner is the
one Lambda that MUST have a working IAM role attached to every prior
day's audit permissions; changing it is high-friction.

The runner's `ENABLED_DAYS` env variable is a comma-separated list of
two-digit day IDs. On each invocation the runner iterates the list,
tries to import `<name>_audit` for each day (using a static mapping
in `runner.py`), and if the import succeeds, calls `run_audit()` on
it. Modules missing from the deployment package are logged and
skipped.

The trade-off is that a typo in `ENABLED_DAYS` becomes a
silently-missing audit rather than a deploy-time error. Day 10's
CAP-011 (no Athena) and CAP-010 (no dashboard) mitigate this — if
you have both, missing days become visible from Athena queries and
from the dashboard's report count. Without them, a typo can hide for
weeks.

---

## 4. Why is CAP-013 (SLA per severity) silent by design, when SLAs are a real governance failure?

Because the Terraform's type constraint on `sla_days_by_severity`
makes it structurally impossible for a stack that goes through this
Terraform to produce an undefined-per-severity SLA. The variable
declaration is:

```hcl
variable "sla_days_by_severity" {
  type = object({
    critical = number
    high     = number
    medium   = number
    low      = number
  })
  default = { critical = 1, high = 3, medium = 7, low = 30 }
  validation {
    condition = ...monotonic...
    error_message = "..."
  }
}
```

The type constraint requires all four keys. The default provides
them. The validation requires monotonic ordering. A user cannot
`terraform plan` this stack with `sla_days_by_severity = {}` — the
plan refuses.

That's what "silent by design" means. The check's silence is a
property of the auditor: the fault is unreachable through this
Terraform.

CAP-013 fires readily when the same Python auditor is pointed at a
programme deployed via a different mechanism — a hand-crafted CDK
stack, a shell script, another org's Terraform module that made the
object optional. That's the point of shipping the check even though
it stays silent locally: the check is portable, and the fault it
catches is not hypothetical elsewhere.

---

## 5. How should an on-call team read a Day 10 finding compared to a Day 08 (DR) finding?

Different urgency profiles, deliberately.

A Day 08 finding — say, DR-006 (no cross-region backup) — describes a
resource whose next AZ or region outage will lose data. The urgency
is bounded by "how likely is that outage in the acceptable-recovery
window". You can quantify it: EBS snapshot failure rate, region
outage frequency, workload criticality. The response is a specific
Terraform change against a specific resource.

A Day 10 finding is about the AUDIT PROGRAMME, not a resource. A
Day 10 CAP-003 (scheduler silent) does not mean anything is broken in
production. It means the mechanism that would tell you if something
were broken has stopped. The urgency is "how long can you tolerate
being blind". That is usually longer than tolerating a specific
outage, because the world does not actually collapse when the audit
stops — nothing bad happens for a while — but the accumulating debt
is real.

The practical rule: Day 08 findings go to the team that owns the
resource. Day 10 findings go to whoever owns the audit programme,
which is often the same person who wrote the runner Lambda and has
long since moved to another team. That's why CAP-016 exists — to
force the ownership question by making the "nobody reads the reports"
state loud instead of quiet.

---

## 6. CAP-006 (cross-cutting risk) fires when the same ARN appears in findings from ≥2 days. Why is that CRITICAL rather than HIGH?

Because remediating one dimension of a cross-cutting risk leaves the
others, and neither team's ordinary workflow will surface the
composite.

Concrete example: an S3 bucket that fires:
- Day 04's IAM check (bucket policy allows `s3:*` from a role in
  another account) — HIGH.
- Day 09's cost check (bucket has no lifecycle rule) — MEDIUM.

Two HIGH-and-MEDIUM findings, two different teams likely: IAM/security
owns the policy, the data team owns the lifecycle. Each team sees
one finding, files it in their tracker, moves on.

The COMPOSITE, though, is worse than either alone. The IAM finding
means someone external can read the bucket. The lifecycle finding
means the bucket accumulates data indefinitely. Together, they mean
somebody external can read data the org has been accumulating for
years, and nobody has looked at what's in it lately. That is a data
exfiltration risk that neither team owns.

CAP-006 fires because the composition is worse than the components
and the composition has no natural owner. CRITICAL because the
failure mode is a class of undetected multi-year defect.

---

## 7. What does the reference-arch module NOT contain, and why?

No load balancer, no RDS, no DynamoDB, no encryption via KMS CMK.

The reference-arch is a MINIMAL composition. Its job is to demonstrate
that a workload CAN be built to score 100/100 on every prior day, and
to give CAP-014 a drift target. It is not a template for a production
architecture.

**No load balancer** because Day 07's checks include front-end
concerns (target group health, deregistration timing, cross-zone
distribution) that require a real backend to validate. The reference
would either need to include a real backend service (expanding
scope) or claim compliance without evidence.

**No RDS** because Day 08's DR checks require an RDS multi-AZ
deployment to score correctly, and a multi-AZ RDS instance costs
~$50/day. The reference would triple the shipped cost without adding
audit surface the workload's absence doesn't already cover.

**No DynamoDB** because point-in-time recovery, on-demand vs
provisioned, and backup rules are all evaluated per-table by Day 08.
A reference table without traffic doesn't demonstrate anything.

**No KMS CMK** because a CMK is $1/month per key, and using CMK
encryption instead of the free AES-256 doesn't score higher — it's a
different-shape trade-off. The reference sticks to what the shipped
Terraform can defensibly justify.

The name reflects this: it's "reference for Day 10's CAP-014", not
"reference production architecture". A production reference would be
its own module, its own cost, and its own audit expansion.

---

## 8. If you were auditing this Day 10 stack yourself, what would you add?

Three things.

**Cross-account. CAP-010's dashboard is local to one account.** In a
real org with 20 accounts, each has its own runner and its own
archive, and the ambient audit produces 20 separate views of 20
separate posture reports. What's missing is a rollup dashboard that
sums scores across accounts and flags the outlier. That's a
CAP-017-shaped check (roll-up across accounts) or a CloudWatch
cross-account dashboard, either way an expansion.

**Recovery-time visibility.** CAP-003 fires when the scheduler is
silent, but it doesn't say why. Adding a metric on the runner's
`Duration` (how long each invocation takes) would let you distinguish
"the runner is throttled by AWS-side limits" from "the runner is
failing on a specific API call in one day". Both produce CAP-003
findings; the remediations are different.

**Suppression audit trail.** The suppressions file is a flat YAML
with `check_id`, `resource_id`, `reason`, `review_by`. What's
missing is who wrote each entry and when. In practice, suppressions
in a mature org get inherited across team handoffs, and "who decided
to accept this exception" becomes unanswerable a few years in. A
`created_by` and `last_reviewed_by` per entry would fix that,
matching how mature CVE tracking works.

None of these are in the shipped Day 10 because the day's scope is
already 27 resources and 16 checks. All three are reasonable
follow-ups.

---

## 9. Why does the runner write to a partitioned S3 key layout?

Because Athena reads it directly without a crawler.

The key pattern is:

```
reports/day=NN/year=YYYY/month=MM/day=DD/<ts>.json
```

Athena's default partition scheme uses `key=value` in the path. An
external table over this bucket with `PARTITIONED BY (day int,
year int, month int, day int)` (yes, the awkward name collision, use
different column names or rename `day` to `audit_day`) will pick up
partitions automatically from a `MSCK REPAIR TABLE` or via partition
projection.

That means:

- No Glue crawler needed. Glue crawlers cost money and add latency.
- Queries filtering by day (`WHERE audit_day = 9`) scan only the
  matching partition, which for a weekly cadence is one JSON per week
  — kilobytes.
- The workgroup's result cache goes in a separate `queries/` prefix
  under the same bucket, sharing the lifecycle rule so query results
  age out with everything else.

If you have a Glue catalog you'd rather use, the layout works there
too. What you gain from this scheme is that a fresh Athena set-up
doesn't need any additional infrastructure to be queryable — the
S3-only path is enough.

---

## 10. Compare this day to Day 09. What's the same and what's different?

**Same:**

- Same 16-check structure, same weights (25/10/4/1/0), same score-out-of-100.
- Same `collect()` / pure-function-check architecture with a
  normalised dict.
- Same `now` injection so RUNTIME_CHECKS produce reproducible STATE C.
- Same silent-by-design vs silent-by-situation distinction with
  explicit test coverage for both.
- Same `sync_contract.py` and challenge scaffold mechanisms.
- Same 47 test count.

**Different at three levels.**

**Subject matter.** Day 09 audits COSTS. Day 10 audits AUDITS. Day 09
checks that Cost Anomaly Detection exists and is being read. Day 10
checks that the audit runner exists and its reports are being read.
Two consecutive days making the same argument — "nobody reads the
output" is the modal failure — at two different layers of the stack.

**Severity distribution.** Day 09 has one CRITICAL (COST-016, unread
anomalies). Day 10 has TWO CRITICAL (CAP-006 cross-cutting risk,
CAP-016 unread reports). The extra CRITICAL exists because on the
capstone day, "the whole programme has stopped" is a bigger failure
than any individual finding, and cross-cutting risk is a failure
mode that only becomes visible at the composition layer.

**The pedagogy of STATE C.** Day 09's STATE C is 3 findings / 33 pts
/ 67/100, grade C. STATE C is worse than STATE B (100) but better
than STATE A (27). The message is "cost governance decays".

Day 10's STATE C is 6 findings / 120 pts (floored) / 0/100, grade
F. STATE C is DRAMATICALLY worse than STATE A (54). The message is
"a broken programme is worse than no programme", and it's a
mathematically bigger point because Day 10's CAP-016 fires PER
UNREAD REPORT, so decay compounds instead of asymptoting.

That is the capstone-shaped payoff: the whole 10-day series
crescendos on the demonstration that the same failure mode
(silent decay of a working programme) escalates as you go higher in
the stack. Day 08's DR-008 fires within an hour. Day 09's COST-016
fires within a month. Day 10's CAP-016 fires within a week AND
compounds. That escalation is the argument that ambient audit
programmes need to be treated as products, not projects — which is
the argument the whole bootcamp has been building towards.
