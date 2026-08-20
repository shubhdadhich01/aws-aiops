# Recovery measurements

**Fill this in. Commit it. Date it.**

This file is deliberately **not** gitignored, and it is the single most
valuable artefact Day 08 produces. Everything else in this directory can be
rebuilt from the Terraform. This cannot — it is the only record of what your
recovery path actually did, as opposed to what it was configured to do.

> **An RTO without a date is an RTO from an architecture that no longer
> exists.** If you take one habit from today, take this file.

---

## Run details

| | |
|---|---|
| Date (UTC) | |
| Who ran it | |
| Account / environment | |
| Primary region | |
| DR region | |
| `nat_gateway_strategy` | |
| `asg_desired_capacity` | |
| Declared `rto_target_minutes` | |
| Declared `rpo_target_minutes` | |
| Anything unusual about the day | |

---

## Step 2 — predictions

**Write these before you touch anything.** In seconds. Do not come back and
edit them after you have seen the answers; the gap is the entire point, and a
number revised after the fact is not a prediction.

| | Scenario | Predicted (s) |
|---|---|---|
| (a) | Terminate one instance → back to full healthy capacity | |
| (b) | Isolate one AZ → ALB stops routing to that zone | |
| (c) | Restore a DynamoDB table → a table the application can query | |

---

## Steps 7–8 — measurements

| | Scenario | Predicted (s) | Measured (s) | Ratio | What ate the time |
|---|---|---|---|---|---|
| (a) | Terminate one instance | | | | |
| (b) | Isolate one AZ | | | | |
| (c) | Restore a table | | | | |

For (c), record the two halves separately — they are different problems and
only one of them is AWS's:

| | Seconds |
|---|---|
| Restore command → table `ACTIVE` | |
| Table `ACTIVE` → application could actually use it | |

That second row is the one nobody predicts. A point-in-time restore creates a
**new table with a different name**, so the restore finishing is not the
recovery finishing.

---

## Step 7f — the recovery workflow

Read these off the Step Functions execution history. The per-step timestamps
**are** the measurement — that is the reason the workflow is a state machine
and not a Lambda with a try/except.

| State | Entered | Exited | Duration (s) |
|---|---|---|---|
| CheckKillSwitch | | | |
| Assess | | | |
| RequestApproval | | | |
| ExecuteFailover | | | |
| Verify | | | |
| **Total execution** | | | |

**Approval time, separately:** ______ seconds.

Record it on its own line because in every real drill it is the largest single
component of the RTO and the one never included in the estimate. Note also
that `approval_timeout_minutes` is a **ceiling**, not an estimate — a timeout
of 30 means your worst-case approved failover *starts* at minute 30.

---

## Step 6 — measured RPO

| Mechanism | Measured lag | How measured |
|---|---|---|
| S3 cross-region replication | | polled `s3 ls` on the replica bucket |
| S3 with Replication Time Control | | CloudWatch `ReplicationLatency` |
| DynamoDB global table | | CloudWatch `ReplicationLatency` |
| AWS Backup copy job (primary → DR vault) | | recovery point `CreationDate` in each vault |

**Declared RPO:** ______ minutes.
**Slowest measured path:** ______.

If the second number exceeds the first, the declared RPO is a fiction, and
that is check DR-011's territory made concrete. Either the schedule gets
faster or the claim gets honest.

---

## Step 9 — audit before and after

| Run | Findings | Points | Score | Grade |
|---|---|---|---|---|
| After apply (static) | | | | |
| After the backup, drill and restore | | | | |
| After `nat_gateway_strategy = per_az` | | | | |

Cost of clearing DR-002: $______ /month. **Should this workload pay it?**

Write the answer down, with the reasoning:

```
```

Both answers are defensible. Only one of them is defensible silently.

---

## What did not work

The most useful section, and the one people leave blank. List everything that
surprised you, including the things that were your own fault.

Prompts, if you need them:

- Did anything fail with a permissions error? (A role that can back up but not
  restore fails at exactly the moment it is used.)
- Did the SNS subscription turn out to be unconfirmed?
- Did a restore reference something that no longer existed — a KMS key, an AMI,
  an instance type, an engine version?
- Did the curl loop show an error at all, or did redundancy absorb everything?
- Did anything take an order of magnitude longer than expected?
- Did you leave the Route 53 health check inverted?

```
```

---

## What this changes

One or two sentences. What would you do differently to your **real** systems
on Monday?

```
```

---

## Next review date

**______** — and put it in a calendar, not in this file.

A restore test or a failover drill more than a quarter old is describing an
architecture that has since changed. Check DR-016 uses ninety days as its
freshness window for exactly that reason, and it is a floor rather than a
target.
