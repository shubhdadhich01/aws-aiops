# Day 08 — Trainer Notes

Internal. Timings, where people get stuck, and the two or three moments the day
actually lives or dies on.

---

## The shape of the day

Day 08 is structurally different from Days 01–07 and it is worth knowing why
before you plan the session.

Every previous day ended with **a thing that exists**: a VPC, a pipeline, a
detector, a responder. You could point at the console and say "there it is".

**Day 08 ends with a number**, and the number is usually embarrassing. The
deliverable is not the stack — it is the gap between what the learner wrote
down in step 2 and what the stopwatch said in steps 7 and 8. If you run this
day and everybody's stack applies cleanly and nobody measures anything, you
have run a Terraform demo.

Protect the measurement. Cut content if you have to; do not cut step 2 or step
7.

---

## Timing

**3 hours 15 minutes for the guided lab.** Add 3 hours if the group is doing
the Python challenge, which most cohorts should do asynchronously.

| Block | Minutes | Notes |
| --- | --- | --- |
| Framing: the argument | 15 | The "left column / right column" table. Do not rush this |
| Setup + apply | 20 | Apply is ~4 min; the rest is tfvars and the SNS click |
| Step 2 — write the predictions | **10** | **Non-negotiable. Make them write them down** |
| Steps 3–4: budget and health checks | 25 | The three-health-checks table lands here |
| Steps 5–6: data tier and RPO | 35 | 6c (global tables) is the highlight of the day |
| Step 7: chaos | **45** | The core. Do not compress |
| Step 8: restore | 20 | The "different table name" moment |
| Step 9: fix NAT, re-audit | 15 | Before/after on the audit score |
| Step 10: DNS (optional) | 10 | Skip if nobody owns a domain |
| Debrief | 20 | See below |
| Teardown | 15 | Walk the checklist together |

If you are short: drop step 10 entirely, and demo 6c rather than having
everyone run it (a global table replica takes ~2 minutes each and the group
will drift).

---

## The three moments the day lives on

### 1. Step 2 — the predictions, written down before anything is measured

This feels like a gimmick to learners and it is the pedagogical core.

**Make it physical.** Have them write into `rto-measurements.md`, or on paper,
and have them say a number out loud. If you let them "think about it", they will
retro-fit the number after seeing the answer and learn nothing.

Typical predictions versus typical measurements:

| | Typical prediction | Typical measurement |
| --- | --- | --- |
| (a) terminate an instance | 30–60 s | **150–240 s** |
| (b) isolate an AZ | 5–10 s | **60 s** (and they are surprised it is *predictable*) |
| (c) restore a table | 60–120 s | **restore 2–5 min, then "oh"** |

(c) is the one that produces the moment. Almost nobody predicts that a PITR
restore creates a **new table** with a **different name**, so the restore
finishing is not the recovery finishing. Let them discover it; do not tell them
in advance.

### 2. Step 7d/7e — isolate, then restore

The `isolate_az` → `restore` pair is the miniature of the whole day. Have them
**open `lambda/chaos.py` side by side** and compare `mode_isolate_az` (one API
call) with `mode_restore` (thirty lines, and it has to reason about the fact
that there is no "detach a NACL" call).

The line to say out loud: *the outbound path is one call, and the return path
has to reconstruct state the outbound path destroyed.* Then connect it to
failback in step 7h.

### 3. Step 9 — DR-002 disappears, and it cost $36

This is the honesty moment. The audit finding that fires against **their own
correctly-intended stack**, not a strawman, and the only one they clear by
spending money rather than fixing a mistake.

Ask the room: *"Should this workload pay it?"* There is no right answer and the
discussion is the point. What you are teaching is that **both answers are
defensible and only one of them is defensible silently.**

---

## Where people get stuck

**Confirmed the SNS subscription? No, they did not.** Same as Days 04, 06 and
07, and worse here, because the approval gate in step 7f times out and looks
like a workflow bug. Ask the room in step 0 and again before step 7f.

**`terraform apply` appears to hang.** It is waiting for `min_elb_capacity`.
This is deliberate — without it, apply returns while the ASG is still launching
and the first thing they do is health-check an empty target group. Tell them
in advance so it does not read as a failure.

**"My curl loop never showed an error."** Correct, and it is the lesson: a
compute failure in a correctly-built multi-AZ stack is a **capacity event, not
an outage**. Have them look at target health rather than at the curl loop.
Some learners find this anticlimactic; reframe it as "your users saw nothing,
your redundancy saw everything".

**`isolate_az` seems to do nothing for a minute.** It is
`interval × unhealthy_threshold` = 60 s. Point at step 3's output. This is the
most satisfying confirmation in the day when it lands — the arithmetic they
read twenty minutes earlier turns out to be the actual elapsed time.

**Global tables take longer than expected.** ~2 minutes to create the replica,
then a few more before `ReplicationLatency` has data points. Start 6c early or
demo it.

**The audit "gives different answers".** Between step 6a's backup and step 8's
restore, findings genuinely drop. Learners read this as a broken tool. This is
STATE A → STATE B in the contract and it is the whole thesis. Have the contract
open.

**The challenge file's DR-002.** By far the hardest of the sixteen. It is a
four-hop graph traversal (route table → NAT route → gateway's subnet → that
subnet's AZ), not a field lookup, and the challenge briefing says so. Expect
20 minutes and expect one or two people to need the hint that they should
report **once per VPC**.

---

## The contract states, and how to use them in the room

| State | Findings | Points | When |
| --- | --- | --- | --- |
| A — static | 15 | 195 | Immediately after apply |
| B — after 6a, 7, 8 | 11 | 125 | Mid-lab |
| C — 61 min after B | 13 | 145 | **Nothing changed** |
| D — reference build | 0 | 0 | The target |

**State C is the one to spend time on.** If your session runs long enough, you
can demonstrate it live: run the audit after step 8, wait, run it again. Two
findings appear because time passed.

If you do not have the wall-clock time, run it with `--rpo-minutes 1` instead
and it fires immediately. That is a legitimate demonstration of the same
mechanism and it is honest to say you are compressing the clock.

The sentence to land:

> **An audit that passes at 14:00 fails at 15:01 on an unchanged account.**
> That is not a defect. It is the difference between a configuration audit and
> a recovery audit.

---

## Cost management for a cohort

**This is the expensive day.** ~$83/month per learner stack at defaults, and
the NAT gateway and ALB bill hourly for existing.

Options, in order of preference:

1. **One shared demo stack** that you apply and destroy, with learners
   following along and running only the read-only commands and the Python.
2. **`nat_gateway_strategy = "none"`** for individual stacks. Saves ~$36/month
   and the lab still works — the user-data installs nothing on purpose. You
   lose the step 9 discussion, so run that on the demo stack.
3. **Individual stacks, torn down the same day.** Fine if you have a hard rule
   about it and you walk the teardown checklist together at the end.

Whatever you choose, **walk the teardown checklist as a group activity**, not
as homework. Section 3 (snapshots) and section 2 (the DR region) are the two
that actually cost money, and both are invisible from a `terraform destroy`
success message.

---

## Things to say out loud that are not in the README

**On the assessment being made from inside the failing region.** The `assess`
step calls regional endpoints in the region under suspicion. This is the
largest architectural gap in the lab, it is documented in the code, and the
real fix — running the workflow from the DR region — costs a second deployment
of everything. Worth naming as "the thing you would do after your first real
incident".

**On why the recovery path has more resources than the thing it recovers.**
Learners notice this in the plan and read it as overengineering. It is not: a
kill switch, an assessment, an approval gate, an execution, a verification and
a notification, to perform two API calls, is what it costs to make an
irreversible automated decision responsibly.

**On the vault lock API.** The mode is selected by the **presence** of
`changeable_for_days`, not by a value. There is no `mode = "governance"` line
to get wrong; there is a line whose mere existence makes the lock permanent.
This is a genuinely poor API and it has produced real, unrecoverable bills.
Good ninety-second aside on "APIs where the dangerous option looks like the
thorough one".

**On DR-013 firing on something that is not broken.** The replication rule
works. What is missing is the metric. Some learners will call this a false
positive; it is the day's sharpest point about observability, and it is worth
defending in the room: *the data replicates either way; what the money buys is
the ability to say a true sentence about it.*

---

## Debrief questions

Twenty minutes, in this order.

1. **"What was your (c) prediction, and what did you measure?"** Go round the
   room. The distribution is the lesson.
2. **"Which of the three chaos modes felt least like a real failure?"**
   `isolate_az`, and they are right — it takes the network and a real AZ
   failure takes the NAT gateway, the RDS standby, the EBS control plane and
   every cross-AZ dependency at once, while the console is also degraded.
3. **"Should this workload pay $36/month for the second NAT gateway?"** No
   right answer. Push for the reasoning.
4. **"When was the last time your production system's failover was tested?"**
   The silence is the point. Follow with: *and what is your RTO document based
   on?*
5. **"What would you change about this audit before running it on
   production?"** Good answers: schedule it rather than running it at merge
   time; scope the runtime-dependent checks separately; feed `--rpo-minutes`
   from the actual DR document.

---

## Links into the rest of the repo

- **Day 03** built the ASG and ALB. Day 08 breaks them. If anyone asks why
  Day 03 did not cover health check types, the answer is that it did — Day 08
  is where it gets tested rather than configured.
- **Day 05**'s remote state and the `random_password` argument reappear in the
  optional RDS block. Same argument, unchanged.
- **Day 06**'s chaos-Lambda pattern is reused, and its "a summary you cannot
  check is worse than no summary" is the direct ancestor of DR-013.
- **Day 07**'s kill switch, dry run, and "an automated response is a decision
  you are making now" carry forward verbatim, with the observation that Day 07's
  automation contained a threat and Day 08's declares a region dead.
- **Day 09** (cost) opens with the same structure in a different key: the
  number in the spreadsheet and the number on the invoice are two different
  things, and only one is a measurement.

---

## The one-sentence version, if you have five minutes and a whiteboard

> Building multi-AZ is easy and every diagram shows it. The failover path is
> the only code in your system that runs exclusively during your worst hour,
> which makes it the least exercised code you own and the most confidently
> described — so today we break it on purpose, hold a stopwatch, and find out
> which of your numbers were measurements.
