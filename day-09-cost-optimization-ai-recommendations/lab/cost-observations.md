# Day 09 — Cost Observations

**A template for what to write down as you run the auditor on a real
account.**

The auditor's output is not the observation. The auditor's output is
data. The observation is what you write in this file after looking at
that data and asking why.

This file is the Day 09 analog of Day 08's `rto-measurements.md`. On Day
08 the point was to record actual recovery times against the RTO claim,
because a recovery time is a claim about a procedure nobody has run. On
Day 09 the point is the same shape: to record actual cost data against
the "cost governance" claim, because a claim is a lagging measure of a
decision nobody re-examined.

The exercise is worth doing on a REAL account, not on this lab's stack.
This lab's stack has known answers. Your account does not.

---

## What to fill in

Delete this template's placeholders and replace with real observations.
Don't sanitise too much — the specific numbers, resource IDs and dates
are what make the observation useful when you come back to it in six
months.

---

## Observation 1 — the account you audited

    Account ID     : _______________________________________________________
    Account name   : _______________________________________________________
    Environment    : _______________________________________________________
                     (prod / staging / lab / personal)
    Monthly bill   : $_________  (from the last full month's Cost Explorer view)
    Auditor version: _______________________________________________________
    Auditor invoked: python cost_audit.py --profile _______ --region _______
    Date/time      : _______________________________________________________

## Observation 2 — the four states, mapped to reality

For a real account you won't have the tidy A/B/C/D of the lab. But you
can still ask the four questions:

    State-A analog — what does the auditor say TODAY, with nothing changed?
      Findings         : _____
      Score            : _____ / 100
      Grade            : _____
      Time to run      : _____ seconds
      Slowest API      : _____________________________________________________

    State-B analog — what could this account plausibly become in ONE SPRINT?
      Findings you can eliminate with a Terraform change     : ____________
      Findings you can eliminate with a manual delete        : ____________
      Findings you can eliminate with a policy conversation  : ____________
      Findings that will remain because they cannot fire here: ____________

    State-C analog — what will the auditor say in 30 DAYS if nothing changes?
      COST-007 (aged snapshots) : likely to fire? y/n. why?
      COST-008 (stopped inst.)  : likely to fire? y/n. why?
      COST-015 (long-running)   : likely to fire? y/n. why?
      COST-016 (untriaged CAD)  : likely to fire? y/n. why?

    State-D analog — what does an "arrived" version of this account look like?
      Do you have Savings Plans covering baseline?  y/n
      Do you have an active anomaly triage rota?    y/n
      Do you have automated snapshot aging?         y/n
      Do you have per-team chargeback?              y/n

## Observation 3 — the loudest three findings

Sorted by weight descending, list the three findings whose remediation
would most improve the score. Also list, for each, the OWNER of the
remediation — the person or team who has to do the work.

    #1  Check ID    : _______________
        Resource    : _______________________________________________________
        Weight      : _____
        Est. saving : $_________ /month
        Owner       : _______________________________________________________
        Blocker     : _______________________________________________________

    #2  Check ID    : _______________
        Resource    : _______________________________________________________
        Weight      : _____
        Est. saving : $_________ /month
        Owner       : _______________________________________________________
        Blocker     : _______________________________________________________

    #3  Check ID    : _______________
        Resource    : _______________________________________________________
        Weight      : _____
        Est. saving : $_________ /month
        Owner       : _______________________________________________________
        Blocker     : _______________________________________________________

## Observation 4 — the surprising finding

For most people, one finding on their first audit is unexpected: a
resource they don't remember creating, a bill line they can't attribute,
a category they didn't know they had. Write it down.

    Surprising finding : _______________________________________________________
    Why it surprised   : _______________________________________________________
    How it got there   : _______________________________________________________
    Cost of it staying : _______________________________________________________

## Observation 5 — the tag coverage question

    Tag coverage right now       : _____%
    Tag activation in Billing?   : y/n
    Groups Cost Explorer produces:
      By Service     y/n
      By Owner       y/n
      By Project     y/n
      By CostCenter  y/n
      By Environment y/n
    Which of the above returns "Untagged: 100%"? _______________________________

## Observation 6 — the Cost Anomaly Detection state

    Monitor exists?          : y/n
    Baseline age (>10 days?) : y/n
    Anomalies in last 30d    : _____
    Anomalies with Feedback  : _____
    Anomalies with no        : _____
      Feedback              (this number is your COST-016 population)
    Weekly review rota exists: y/n
    Who owns triage?         : _______________________________________________________

## Observation 7 — the Savings Plan question

    Active Savings Plans     : _____
    Active Reserved Inst.    : _____
    Recommended commitment   : $_________ /hour (from console)
    Baseline utilisation     : _____%
      (from get_savings_plans_utilization, if a plan exists)
    Confidence workload will : ______________
      exist in 12 months?

    Decision:
      [ ] Buy a Compute SP at recommended amount
      [ ] Buy a Compute SP at reduced amount because ______________
      [ ] Do not buy because ______________
      [ ] Defer decision until ______________

    A NON-DECISION IS A DECISION TO PAY ON-DEMAND. Write which one this is.

## Observation 8 — the follow-up date

Cost is a lagging measure. This observation will be stale in 30 days if
nothing is done. Set a follow-up:

    Follow-up date        : _______________________________________________________
    Follow-up action      : _______________________________________________________
    Success criterion     : _______________________________________________________

Recommended follow-up: put a calendar item on the follow-up date that
says "re-run cost_audit.py --profile <profile> and diff against
`cost-observations-YYYY-MM-DD.md`". If the diff shows STATE-C findings
appearing without configuration changes, that is exactly what STATE C
predicts.

---

## Aggregate observations

Once you have run this on three or more accounts, aggregate:

    Median STATE-A score      : _____ / 100
    Median STATE-C decay      : _____ points/month
    Most common finding       : COST-_____
    Least common finding      : COST-_____
    Silent-by-design checks   : always COST-002, COST-004 in this stack;
                                may vary on others
    Silent-by-situation checks: which ones fire vs stay silent tells you
                                what shape of account you tend to audit

The aggregate is what "we know our environment" looks like as an
observation rather than an assertion.
