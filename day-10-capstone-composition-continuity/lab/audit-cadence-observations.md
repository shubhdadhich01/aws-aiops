# Day 10 — Audit Cadence Observations

**A template for what to write down as you run the auditor against a
real ambient audit programme.**

The auditor's output is not the observation. The auditor's output is
data. The observation is what you write in this file after looking at
that data and asking why.

This file is the Day 10 analog of Day 09's `cost-observations.md`. On
Day 09 the point was to record actual cost data against the "cost
governance" claim. On Day 10 the point is to record actual audit-
cadence data against the "ambient audit programme" claim. Both are
lagging measures of decisions somebody made once and never revisited.

The exercise is worth doing on a REAL programme, not on this lab's
stack. This lab's stack has known answers. Your programme does not.

---

## What to fill in

Delete the template's placeholders and replace with real observations.

---

## Observation 1 — the programme you audited

    Programme name : _______________________________________________________
    Owner team     : _______________________________________________________
    Age of the     : _______________________________________________________
      programme      (when was the runner Lambda first deployed?)
    Age of the     : _______________________________________________________
      OWNER          (how long has the current owner been on the team?)
    Auditor version: _______________________________________________________
    Date/time      : _______________________________________________________

## Observation 2 — the four states, mapped to reality

For a real programme you will not have the tidy A/B/C/D of the lab.
You will have something between A and C, drifted in ways specific to
your org. Map the four questions:

    STATE-A analog — what does the auditor say TODAY?
      Findings         : _____
      Score            : _____ / 100
      Grade            : _____
      Time to run      : _____ seconds
      Slowest collector: _____________________________________________________

    STATE-B analog — what could this programme plausibly become in ONE SPRINT?
      Findings you can eliminate with a Terraform change     : ____________
      Findings you can eliminate with a suppressions.yaml edit: ___________
      Findings you can eliminate with a triage-rota conversation: _________
      Findings that will remain because they cannot fire here : ____________

    STATE-C analog — what will the auditor say in 30 DAYS if nothing changes?
      CAP-003 (scheduler silent) : likely to fire? y/n. why?
      CAP-012 (suppressions stale): likely to fire? y/n. why?
      CAP-016 (unread reports)   : likely to fire? y/n. why?

    STATE-D analog — what does an "arrived" programme look like?
      Do you have a weekly triage rota?             y/n
      Are ALL suppressions reviewed on cadence?     y/n
      Is the runner monitored for errors?           y/n
      Is the archive queryable via Athena?          y/n
      Is there a rollup dashboard?                  y/n

## Observation 3 — the loudest three findings

Sorted by weight descending, list the three findings whose remediation
would most improve the score. For each, name the OWNER of the
remediation.

    #1  Check ID  : _______________
        Resource  : _______________________________________________________
        Weight    : _____
        Owner     : _______________________________________________________
        Blocker   : _______________________________________________________

    #2  Check ID  : _______________
        Resource  : _______________________________________________________
        Weight    : _____
        Owner     : _______________________________________________________
        Blocker   : _______________________________________________________

    #3  Check ID  : _______________
        Resource  : _______________________________________________________
        Weight    : _____
        Owner     : _______________________________________________________
        Blocker   : _______________________________________________________

## Observation 4 — the unread reports

If CAP-016 is firing, this is the single most important observation.

    How many unread reports    : _____
    Age of the oldest unread   : ______________ (days)
    Age of the newest unread   : ______________ (days)
    Date of the LAST report    : ______________
      that anyone acknowledged
    Estimated cumulative debt  : $_________ of decisions not made,
                                if each report represents ~$X of
                                deferred remediation

    Question to answer:
      Who is going to read the next report?
      _______________________________________________________
      _______________________________________________________
      _______________________________________________________

    If the answer is "nobody has ownership", CAP-016 is the least
    of your problems. The programme has no owner. Everything else
    is downstream.

## Observation 5 — the scheduler state

    Scheduler configured?             : y/n
    Last invocation                   : ______________ (UTC)
    Interval                          : _____ days
    Days since last invocation        : _____
    Interval * 1.5 threshold          : _____ days
    CAP-003 firing?                   : y/n

    If CAP-003 is firing, what's the cause?
      [ ] EventBridge rule was disabled by security tooling
      [ ] Lambda hit permissions boundary
      [ ] Lambda hit resource limits
      [ ] Archive bucket was inaccessible for a while
      [ ] Nobody knows
      [ ] Other: ______________

## Observation 6 — the suppressions state

    Suppression count             : _____
    Suppressions past review_by   : _____
    Median review_by staleness    : ______________ (days past)
    Suppressions with no reason   : _____
    Suppressions with no          : _____
      created_by / reviewed_by
    CAP-012 firing?               : y/n

    Question to answer:
      For each suppression past review, do you STILL know why it's
      suppressed? If the answer is "no, we inherited it and can't
      remember", it's not a suppression — it's a finding you're
      ignoring without an active decision to ignore.

## Observation 7 — the cross-cutting question

    CAP-006 firing?                       : y/n
    Number of cross-cutting resources     : _____
    For the top 3, which days correlate?  :
      #1 : Days _____ and _____ on ______________________
      #2 : Days _____ and _____ on ______________________
      #3 : Days _____ and _____ on ______________________

    For each: who owns the cross-cutting remediation?
      #1 : _______________________________________________
      #2 : _______________________________________________
      #3 : _______________________________________________

    If the answer is "nobody", the composition has no home. Assign
    it explicitly to a person before running the next audit.

## Observation 8 — the follow-up date

    Follow-up date        : ______________
    Follow-up action      : ______________________________________
    Success criterion     : ______________________________________

Recommended follow-up: put a calendar item on the follow-up date
that says "re-run capstone_audit.py and diff against
`audit-cadence-observations-YYYY-MM-DD.md`". If the diff shows
STATE-C findings appearing without configuration changes, that is
exactly what STATE C predicts.

---

## Aggregate observations

Once you have run this on three or more programmes, aggregate:

    Median STATE-A score     : _____ / 100
    Median STATE-C decay     : _____ points/month
    Most common finding      : CAP-_____
    Least common finding     : CAP-_____
    Programmes with active   : _____ of _____
      unread reports (CAP-016)
    Programmes with stale    : _____ of _____
      suppressions (CAP-012)

The aggregate is what "we know our environment" looks like as an
observation rather than an assertion. Anything above 30% unread-
report rate across an org is a signal that the ambient audit
programme is a process without an owner, industry-wide. The Day
09 → Day 10 crescendo is designed to make that argument concrete —
your aggregate observations from this template are the evidence
for or against.
