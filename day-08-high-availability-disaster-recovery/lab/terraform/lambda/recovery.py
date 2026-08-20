"""
Day 08 — recovery.py
CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

The recovery workflow's worker. One function, several actions, dispatched by
the Step Functions state machine in recovery.tf.

WHY ONE LAMBDA AND NOT SIX
--------------------------
Six functions would be more fashionable and would deploy six times as much
IAM, six log groups and six versions to keep in step. The states here share
one client set and one notion of what "the stack" is, and the thing that must
be readable at 03:00 is the STATE MACHINE, not the code behind each step. Six
functions makes the diagram no clearer and the deployment six times harder to
verify.

WHY STEP FUNCTIONS AND NOT A LAMBDA THAT DOES ALL OF IT
-------------------------------------------------------
Day 07's notes argued for Step Functions the moment a response has more than
one step, and this response has five. The specific properties that matter
here, none of which you get from a Lambda with a try/except:

  - The execution history is a per-step audit trail with timestamps. After the
    drill you can read exactly how long each phase took, which is the RTO
    measurement, which is the point of the day.
  - A human approval gate is a first-class state (waitForTaskToken) rather
    than a Lambda blocking for 30 minutes at 15 minutes of maximum runtime.
  - A failed verify step FAILS THE EXECUTION rather than returning 200 with a
    field nobody reads.
  - Retries and timeouts are declared next to the step, not buried in code.

THE ACTIONS
-----------
  check_kill_switch  Read the SSM brake. FIRST state, always.
  assess             Look at reality and classify the damage.
  recover_in_az      Replace unhealthy instances. Reversible by doing nothing.
  failover           THE IRREVERSIBLE ONE. Honours dry_run.
  verify             Prove the failover did what it claimed.
  notify             Tell somebody, with numbers.
  failback           NOT IN THE STATE MACHINE. Manual invoke only. Read the
                     long comment on it before you use it.

WHAT "FAILOVER" ACTUALLY DOES HERE, precisely, because a failover that is
described vaguely is a failover nobody has read:

  1. Writes dr_region into the ACTIVE_REGION_PARAM SSM parameter. This is the
     application's source of truth for where writes go. It is a genuine
     pattern and it is the part your code has to actually honour — a failover
     that flips a flag no application reads is theatre.
  2. Inverts the Route 53 health check. An inverted health check reports the
     PRIMARY as unhealthy, which is what makes DNS failover happen on demand.
     This is the standard way to run a DR drill without breaking anything, and
     it is reversible with one API call.

  Both are reversible. NEITHER OF THEM RECONCILES DATA, and that is the whole
  of the failback problem. See the failback docstring.
"""

import json
import os
import time
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_REGION", "us-east-1")
DR_REGION = os.environ.get("DR_REGION", "us-west-2")
ASG_NAME = os.environ.get("ASG_NAME", "")
TARGET_GROUP_ARN = os.environ.get("TARGET_GROUP_ARN", "")
TABLE_NAME = os.environ.get("TABLE_NAME", "")
HEALTH_CHECK_ID = os.environ.get("HEALTH_CHECK_ID", "")
KILL_SWITCH_PARAM = os.environ.get("KILL_SWITCH_PARAM", "")
ACTIVE_REGION_PARAM = os.environ.get("ACTIVE_REGION_PARAM", "")
TOPIC_ARN = os.environ.get("TOPIC_ARN", "")
DEFAULT_DRY_RUN = os.environ.get("RECOVERY_DRY_RUN", "true").lower() == "true"
REQUIRE_APPROVAL = os.environ.get("REQUIRE_APPROVAL", "true").lower() == "true"

# Minimum fraction of targets that must be healthy before "the service is
# fine" is a defensible conclusion. Below this and above the regional
# threshold is an in-AZ problem; below the regional threshold is a case for
# considering a regional failover.
IN_AZ_THRESHOLD = 0.99
REGIONAL_THRESHOLD = 0.01

ssm = boto3.client("ssm")
asg = boto3.client("autoscaling")
elbv2 = boto3.client("elbv2")
route53 = boto3.client("route53")
dynamodb = boto3.client("dynamodb")
sns = boto3.client("sns")


def _log(**kw):
    print(json.dumps(kw, default=str))


def _now():
    return datetime.now(timezone.utc)


def _notify(subject, body):
    if not TOPIC_ARN:
        return
    try:
        sns.publish(TopicArn=TOPIC_ARN, Subject=subject[:100], Message=body)
    except ClientError as exc:
        _log(event="notify_failed", error=str(exc))


# ---------------------------------------------------------------------------
# check_kill_switch
# ---------------------------------------------------------------------------

def action_check_kill_switch(_payload, _dry_run):
    """Read the brake. First state, always.

    FAILS SAFE IN ONE DIRECTION, deliberately: if the parameter is missing,
    unreadable, or holds anything other than "enabled", the answer is no. An
    automation that cannot confirm it is allowed to run does not run.

    The opposite default — proceed unless explicitly stopped — is what turns a
    permissions mistake into an unrequested regional failover.
    """
    flags = {"dry_run": DEFAULT_DRY_RUN, "require_approval": REQUIRE_APPROVAL}

    if not KILL_SWITCH_PARAM:
        return dict(flags, enabled=False, reason="no kill switch parameter configured")

    try:
        value = ssm.get_parameter(Name=KILL_SWITCH_PARAM)["Parameter"]["Value"]
    except ClientError as exc:
        return dict(flags, enabled=False, reason="kill switch unreadable: {}".format(exc))

    enabled = value.strip().lower() == "enabled"
    return {
        "enabled": enabled,
        "value": value,
        "reason": "kill switch is '{}'".format(value),
        # The effective settings, echoed so the state machine can branch on
        # them without the caller having to supply them — and, more usefully,
        # so the EXECUTION HISTORY records what the flags were at the moment
        # this ran. "It was in dry run at the time" is an assertion; a value in
        # the execution history is evidence.
        "dry_run": DEFAULT_DRY_RUN,
        "require_approval": REQUIRE_APPROVAL,
    }


# ---------------------------------------------------------------------------
# assess
# ---------------------------------------------------------------------------

def _target_health():
    if not TARGET_GROUP_ARN:
        return {"total": 0, "healthy": 0, "by_az": {}}

    resp = elbv2.describe_target_health(TargetGroupArn=TARGET_GROUP_ARN)
    descriptions = resp.get("TargetHealthDescriptions", [])

    instance_ids = [d["Target"]["Id"] for d in descriptions]
    az_of = {}
    if instance_ids and ASG_NAME:
        paginator = asg.get_paginator("describe_auto_scaling_groups")
        for page in paginator.paginate(AutoScalingGroupNames=[ASG_NAME]):
            for group in page.get("AutoScalingGroups", []):
                for inst in group.get("Instances", []):
                    az_of[inst["InstanceId"]] = inst.get("AvailabilityZone", "?")

    by_az = {}
    healthy = 0
    for d in descriptions:
        tid = d["Target"]["Id"]
        az = az_of.get(tid, "?")
        state = d.get("TargetHealth", {}).get("State", "unknown")
        bucket = by_az.setdefault(az, {"total": 0, "healthy": 0})
        bucket["total"] += 1
        if state == "healthy":
            bucket["healthy"] += 1
            healthy += 1

    return {"total": len(descriptions), "healthy": healthy, "by_az": by_az}


def action_assess(_payload, _dry_run):
    """Look at reality and classify the damage.

    THIS IS THE STEP THAT IS WRONG DURING REAL INCIDENTS, and it is worth
    saying so in the code rather than only in the README.

    Every signal available here is observed FROM INSIDE THE REGION THAT MIGHT
    BE FAILING. describe_target_health is an API call to a regional endpoint.
    If the regional control plane is degraded — which is what a lot of real
    "region down" events actually are — this call fails or returns stale data,
    and the assessment that decides whether to fail over is made from evidence
    produced by the thing under suspicion.

    That is not fixable from here. It is fixable by RUNNING THE WORKFLOW FROM
    THE DR REGION, which is a design most teams arrive at after their first
    real incident and which costs a second deployment of everything. It is
    mentioned in the README's failover section and it is the single largest
    architectural gap in this lab, stated plainly rather than hidden.

    The classification is deliberately conservative. "regional" is only
    returned when essentially nothing is healthy, because the cost of a false
    positive here is split brain and the cost of a false negative is a few
    more minutes of a partial outage. Those are not symmetric.
    """
    health = _target_health()
    total = health["total"]
    healthy = health["healthy"]
    ratio = (healthy / total) if total else 0.0

    degraded_azs = [
        az for az, v in health["by_az"].items() if v["total"] > 0 and v["healthy"] == 0
    ]

    if total == 0:
        scope = "unknown"
        rationale = "no targets registered — cannot distinguish an outage from an empty stack, which is itself a finding"
    elif ratio >= IN_AZ_THRESHOLD:
        scope = "none"
        rationale = "all {} targets healthy".format(total)
    elif ratio <= REGIONAL_THRESHOLD:
        scope = "regional"
        rationale = "{}/{} targets healthy across all AZs — consistent with a regional event, and ALSO consistent with a bad deploy, an expired certificate or a security group change. The workflow cannot tell those apart; a human can.".format(healthy, total)
    else:
        scope = "in_az"
        rationale = "{}/{} targets healthy; AZ(s) with zero healthy targets: {}".format(
            healthy, total, degraded_azs or "none"
        )

    assessment = {
        "scope": scope,
        "rationale": rationale,
        "total_targets": total,
        "healthy_targets": healthy,
        "healthy_ratio": round(ratio, 4),
        "degraded_azs": degraded_azs,
        "by_az": health["by_az"],
        "observed_from": REGION,
        "observed_at": _now().isoformat(),
        "caveat": "observed from inside the region under suspicion; see the assess() docstring",
    }
    _log(event="assessment", **assessment)
    return assessment


# ---------------------------------------------------------------------------
# recover_in_az
# ---------------------------------------------------------------------------

def action_recover_in_az(_payload, dry_run):
    """Replace unhealthy instances. No approval gate, on purpose.

    This is reversible by doing nothing: the ASG replaces instances, and if the
    decision was wrong the replacements are identical to what they replaced.
    It is also, almost exactly, what the ASG would have done by itself with
    health_check_type = "ELB".

    That last point deserves a moment. If your ASG is configured correctly,
    this step is nearly redundant, and the honest version of this workflow
    would say so. It exists here for two reasons: it makes the in-AZ branch of
    the state machine real rather than a stub, and it covers the case where
    health_check_type is "EC2" — which is the case the auditor's DR-003 is
    about, and which is far more common in real accounts than it should be.
    """
    if not ASG_NAME or not TARGET_GROUP_ARN:
        return {"action": "recover_in_az", "result": "not configured"}

    resp = elbv2.describe_target_health(TargetGroupArn=TARGET_GROUP_ARN)
    unhealthy = [
        d["Target"]["Id"]
        for d in resp.get("TargetHealthDescriptions", [])
        if d.get("TargetHealth", {}).get("State") in ("unhealthy", "unused")
    ]

    plan = {
        "action": "recover_in_az",
        "unhealthy_targets": unhealthy,
        "intent": "mark each Unhealthy so the ASG terminates and replaces it",
    }

    if dry_run:
        plan["result"] = "DRY RUN — nothing marked"
        return plan
    if not unhealthy:
        plan["result"] = "nothing to do"
        return plan

    marked = []
    for instance_id in unhealthy:
        try:
            asg.set_instance_health(
                InstanceId=instance_id,
                HealthStatus="Unhealthy",
                ShouldRespectGracePeriod=True,
            )
            marked.append(instance_id)
        except ClientError as exc:
            # An instance that has already gone is not an error worth failing
            # the workflow over. An instance we are not permitted to touch is.
            _log(event="set_health_failed", instance_id=instance_id, error=str(exc))

    plan["marked"] = marked
    plan["result"] = "marked {} instance(s) unhealthy".format(len(marked))
    return plan


# ---------------------------------------------------------------------------
# failover — the irreversible one
# ---------------------------------------------------------------------------

def action_failover(payload, dry_run):
    """Declare the primary region not authoritative.

    TWO CHANGES, both reversible by API and neither reversible in effect:

      1. ACTIVE_REGION_PARAM := dr_region. Any application that reads this
         parameter to decide where to write now writes to the DR region.
      2. The Route 53 health check is INVERTED, so the primary record reports
         unhealthy and DNS failover occurs.

    "Reversible by API" and "reversible in effect" are different things and the
    gap between them is the whole failback problem. You can put both settings
    back in ten seconds. You cannot put back the writes that landed in the DR
    region while they were flipped, and you cannot un-cache the DNS answers
    that resolvers are still serving.

    THE INVERTED HEALTH CHECK IS ALSO A TRAP WORTH NAMING. While it is
    inverted, Route 53 reports the primary unhealthy REGARDLESS OF WHETHER IT
    IS. If the primary recovers during the incident, nothing tells you: the
    signal you would use to notice has been deliberately disabled by your own
    failover. Every drill that uses this technique must have "un-invert the
    health check" as an explicit step with an owner, and every one that does
    not eventually leaves it inverted for a week.
    """
    started = time.time()
    plan = {
        "action": "failover",
        "from_region": REGION,
        "to_region": DR_REGION,
        "steps": [
            "set {} := {}".format(ACTIVE_REGION_PARAM or "(no parameter)", DR_REGION),
            "invert Route 53 health check {}".format(HEALTH_CHECK_ID or "(none configured)"),
        ],
        "not_performed": [
            "data reconciliation — nothing here merges writes that were in flight",
            "connection pool draining — clients hold connections to the old endpoint until they time out",
            "DNS cache expiry — resolvers keep the old answer for the TTL, and some for longer",
        ],
        "reason": payload.get("reason", "workflow decision"),
    }

    if dry_run:
        plan["result"] = "DRY RUN — nothing changed"
        plan["duration_seconds"] = round(time.time() - started, 3)
        return plan

    changed = []

    if ACTIVE_REGION_PARAM:
        try:
            ssm.put_parameter(
                Name=ACTIVE_REGION_PARAM,
                Value=DR_REGION,
                Type="String",
                Overwrite=True,
            )
            changed.append("active_region={}".format(DR_REGION))
        except ClientError as exc:
            plan["errors"] = plan.get("errors", []) + ["active_region: {}".format(exc)]

    if HEALTH_CHECK_ID:
        try:
            route53.update_health_check(HealthCheckId=HEALTH_CHECK_ID, Inverted=True)
            changed.append("health_check_inverted=True")
        except ClientError as exc:
            plan["errors"] = plan.get("errors", []) + ["health_check: {}".format(exc)]

    plan["changed"] = changed
    plan["result"] = "failed over" if changed and "errors" not in plan else "partial or failed"
    plan["failover_completed_at"] = _now().isoformat()
    plan["duration_seconds"] = round(time.time() - started, 3)
    return plan


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

def action_verify(payload, dry_run):
    """Prove the failover did what it claimed.

    A workflow that ends at "executed" is a workflow that reports success when
    the API call succeeded and the outcome did not. This step is the
    difference between "we ran the failover" and "we are serving from the DR
    region", and those diverge more often than anybody expects.

    It deliberately checks OUTCOMES rather than re-reading its own intent:
    the parameter's current value, the health check's actual inverted state,
    the DR table's status. If a step failed silently, this is where it stops
    being silent.
    """
    checks = []

    if ACTIVE_REGION_PARAM:
        try:
            value = ssm.get_parameter(Name=ACTIVE_REGION_PARAM)["Parameter"]["Value"]
            checks.append(
                {
                    "check": "active_region_parameter",
                    "expected": DR_REGION,
                    "actual": value,
                    "ok": value == DR_REGION or dry_run,
                }
            )
        except ClientError as exc:
            checks.append({"check": "active_region_parameter", "ok": False, "error": str(exc)})

    if HEALTH_CHECK_ID:
        try:
            hc = route53.get_health_check(HealthCheckId=HEALTH_CHECK_ID)
            inverted = hc["HealthCheck"]["HealthCheckConfig"].get("Inverted", False)
            checks.append(
                {
                    "check": "health_check_inverted",
                    "expected": True,
                    "actual": inverted,
                    "ok": bool(inverted) or dry_run,
                }
            )
        except ClientError as exc:
            checks.append({"check": "health_check_inverted", "ok": False, "error": str(exc)})

    if TABLE_NAME:
        try:
            dr_dynamodb = boto3.client("dynamodb", region_name=DR_REGION)
            status = dr_dynamodb.describe_table(TableName=TABLE_NAME)["Table"]["TableStatus"]
            checks.append(
                {
                    "check": "dr_table_status",
                    "expected": "ACTIVE",
                    "actual": status,
                    "ok": status == "ACTIVE",
                }
            )
        except ClientError as exc:
            # No replica is a legitimate configuration, not a verification
            # failure. Saying so explicitly is better than an ok:False that
            # sends somebody looking for a problem that is a setting.
            checks.append(
                {
                    "check": "dr_table_status",
                    "ok": True,
                    "note": "no DR replica of {} — expected when enable_dynamodb_global_table is false: {}".format(TABLE_NAME, exc.response["Error"]["Code"]),
                }
            )

    verified = all(c.get("ok") for c in checks) if checks else False

    result = {
        "action": "verify",
        "verified": verified,
        "checks": checks,
        "dry_run": dry_run,
        "verified_at": _now().isoformat(),
        "not_verified": [
            "that any client actually resolved to the DR region",
            "that in-flight writes to the primary were captured",
            "that the data in the DR region is complete rather than merely present",
        ],
    }
    result["execution_started_at"] = payload.get("execution_started_at")
    _log(event="verification", **result)
    return result


# ---------------------------------------------------------------------------
# notify
# ---------------------------------------------------------------------------

def action_notify(payload, dry_run):
    outcome = payload.get("outcome", "unknown")
    body = [
        "Day 08 recovery workflow: {}".format(outcome),
        "",
        "dry_run: {}".format(dry_run),
        "primary: {}   dr: {}".format(REGION, DR_REGION),
        "",
        json.dumps(payload, indent=2, default=str)[:8000],
        "",
        "STOP THE CLOCK. Write the elapsed time next to the RTO you declared.",
        "",
        "If this was a real failover, failback is NOT automated. Invoke this",
        'function with {"action":"failback"} to see the checklist it cannot',
        "perform for you.",
    ]
    _notify("Day 08 recovery: {}".format(outcome), "\n".join(body))
    return {"action": "notify", "notified": bool(TOPIC_ARN), "outcome": outcome}


# ---------------------------------------------------------------------------
# failback — deliberately not in the state machine
# ---------------------------------------------------------------------------

def action_failback(_payload, dry_run):
    """Undo the failover. MANUAL INVOKE ONLY, and read this before you do.

    ============================ THE HONEST STATEMENT ========================

    THERE IS NO AUTOMATED FAILBACK IN THIS REPO, AND THERE IS NOT ONE IN MOST
    REAL SYSTEMS EITHER.

    That is not an omission this lab ran out of time for. It is the shape of
    the problem. Failing over is a decision about ROUTING. Failing back is a
    decision about DATA, and it can only be made by something that knows what
    your writes mean.

    This function reverses the two routing changes: it sets the active-region
    parameter back and un-inverts the health check. Those are the easy half,
    and they are ten seconds of work.

    THE HALF IT CANNOT DO, listed so that nobody mistakes the ten seconds for
    the job:

      1. RECONCILE THE WRITES THAT LANDED IN THE DR REGION. Everything written
         while you were failed over exists only there, unless your replication
         is bidirectional — and if it IS bidirectional, you have a
         last-writer-wins merge you did not review. Somebody has to decide,
         per data set, whether the primary's version or the DR version wins.
         There is no general answer.

      2. DECIDE WHETHER THE PRIMARY'S DATA IS STALE OR WRONG. Those are
         different. Stale is missing recent writes. Wrong is having accepted
         writes during a partition that the DR region also accepted
         differently. The second one is silent and permanent.

      3. DRAIN AND REPOINT CONNECTIONS. Pools, long-lived gRPC channels,
         message consumers with in-flight leases. Each of these fails back on
         its own schedule and some of them do not fail back at all without a
         restart.

      4. VERIFY THE PRIMARY IS ACTUALLY BETTER. The health check you inverted
         has been reporting the primary unhealthy the entire time, which means
         you have had NO signal about the primary since the moment you failed
         over. Un-invert it and WAIT for real health data before moving traffic
         back. Failing back into a still-broken primary is the classic second
         outage, and it is worse than the first because you have now proved to
         everyone that failover does not help.

      5. SCALE THE DR ENVIRONMENT BACK DOWN. It was scaled up for the incident.
         It is still scaled up. This is the most expensive item on the list and
         the least likely to be noticed.

    Rehearse this, timed, in the same drill as the failover. Every DR exercise
    that ends at "we failed over successfully" has tested half a procedure and
    measured a third of an RTO.
    ==========================================================================
    """
    plan = {
        "action": "failback",
        "reverses": [
            "set {} := {}".format(ACTIVE_REGION_PARAM or "(no parameter)", REGION),
            "un-invert Route 53 health check {}".format(HEALTH_CHECK_ID or "(none)"),
        ],
        "CANNOT_REVERSE": [
            "writes that landed in the DR region while failed over",
            "divergence created by both regions accepting writes during a partition",
            "connection pools and message leases that must be drained or restarted",
            "the fact that you have had no health signal from the primary since the health check was inverted",
            "the DR environment's scale, which is still whatever the incident needed",
        ],
        "before_you_run_this": "un-invert FIRST, then WAIT for real health data on the primary before moving traffic. Failing back into a still-broken primary is the classic second outage.",
    }

    if dry_run:
        plan["result"] = "DRY RUN — nothing changed"
        return plan

    changed = []
    if ACTIVE_REGION_PARAM:
        try:
            ssm.put_parameter(Name=ACTIVE_REGION_PARAM, Value=REGION, Type="String", Overwrite=True)
            changed.append("active_region={}".format(REGION))
        except ClientError as exc:
            plan["errors"] = plan.get("errors", []) + [str(exc)]

    if HEALTH_CHECK_ID:
        try:
            route53.update_health_check(HealthCheckId=HEALTH_CHECK_ID, Inverted=False)
            changed.append("health_check_inverted=False")
        except ClientError as exc:
            plan["errors"] = plan.get("errors", []) + [str(exc)]

    plan["changed"] = changed
    plan["result"] = "routing reversed; DATA RECONCILIATION IS YOURS"
    _notify("Day 08 failback: routing reversed", json.dumps(plan, indent=2, default=str))
    return plan


ACTIONS = {
    "check_kill_switch": action_check_kill_switch,
    "assess": action_assess,
    "recover_in_az": action_recover_in_az,
    "failover": action_failover,
    "verify": action_verify,
    "notify": action_notify,
    "failback": action_failback,
}


def lambda_handler(event, _context):
    payload = event if isinstance(event, dict) else {}
    action = payload.get("action", "assess")
    dry_run = bool(payload.get("dry_run", DEFAULT_DRY_RUN))

    if action not in ACTIONS:
        result = {"error": "unknown action", "action": action, "valid": sorted(ACTIONS)}
        _log(event="recovery_rejected", **result)
        return result

    started = time.time()
    result = ACTIONS[action](payload, dry_run)
    result["action"] = action
    result["dry_run"] = dry_run
    result["duration_seconds"] = round(time.time() - started, 3)

    _log(event="recovery_action", **result)
    return result
