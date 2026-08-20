"""
Day 07 — threat_responder.py

Receives a GuardDuty finding from EventBridge and decides whether to contain.

===========================================================================
THE ARGUMENT THIS FILE IS MAKING
===========================================================================

    An automated response is a decision you are making now, to be executed
    later, by nobody, on evidence that might be wrong.

Wiring GuardDuty to a Lambda that isolates an instance is easy. It is about
forty lines and it demos beautifully. It is also not the engineering problem.

The engineering problem is what happens on the night the detector is wrong —
and it will be wrong, because GuardDuty is a probabilistic detector and your
own penetration test looks exactly like an attacker to it.

So this function is built around five constraints, and every one of them costs
something:

    1. AN ALLOW-LIST OF TYPES, NOT A SEVERITY THRESHOLD
       GuardDuty severity scores IMPACT, not CONFIDENCE. `severity >= 7`
       matches your pen test, your scanner, a researcher and a developer on
       hotel wifi, all of which are outages you caused. Finding TYPE is what
       correlates with confidence. Every entry in RESPOND_TO_TYPES is a
       decision somebody made about that specific type.

    2. A RUNTIME KILL SWITCH
       Read from SSM on EVERY invocation, no caching. One CLI command stops
       all automation with no plan, no apply and no pipeline. A kill switch
       that requires a deploy is not a kill switch.

    3. REVERSIBLE ACTIONS ONLY
       Isolate, never terminate. Detach, never delete. And RECORD WHAT WAS
       DETACHED, because "reversible in principle" and "reversible by the
       person on call at 3am who did not build this" are different claims.

    4. NON-REPUDIATION
       Every action emits the finding id, the finding type, the decision, the
       reason, and the previous state — to SNS and to its own logs. If you
       cannot reconstruct WHY a production instance was isolated at 03:00,
       you have built something nobody will let you keep.

    5. DRY-RUN AS THE DEFAULT
       Run it for a week and read what it WOULD have done. That week always
       changes the allow-list.

===========================================================================
WHAT THIS FUNCTION WILL NOT DO
===========================================================================

It will not terminate an instance. It will not delete an access key. It will
not detach an IAM policy. Not because those are never correct, but because
they are decisions a human makes on Monday with the finding in front of them
and somebody to ask — not decisions a Lambda makes at 03:00 on a probabilistic
signal with nobody watching.

The deliberately broken responder in main.tf section 10 runs this same file
with `CONTAINMENT_MODE=terminate`, which this code rejects. That rejection is
the point: the destructive path does not exist to be misconfigured into.

===========================================================================
THE SAMPLE-FINDING TRAP
===========================================================================

`aws guardduty create-sample-findings` prefixes titles with "[SAMPLE]" and
uses fake resource ids like i-99999999. That is useful — a responder can
recognise samples and refuse to act on them.

It is also the most dangerous thing in this file, because getting the test
backwards produces a responder that works perfectly in the lab and does
nothing in production, and looks identical in both. See `is_sample()`, and see
lab step 4, which makes you prove which way round yours is.
"""

import json
import os
import time
from datetime import datetime, timezone

import boto3

REGION = os.environ["AWS_REGION"]
CONTAINMENT_MODE = os.environ.get("CONTAINMENT_MODE", "dry-run")
QUARANTINE_SG = os.environ.get("QUARANTINE_SG_ID", "")
TOPIC_ARN = os.environ["CONTAINMENT_TOPIC_ARN"]
KILL_SWITCH_PARAM = os.environ.get("KILL_SWITCH_PARAM", "")
ACT_ON_SAMPLES = os.environ.get("ACT_ON_SAMPLES", "false").lower() == "true"

# The allow-list. A JSON array of finding types, or the string "SEVERITY" for
# the deliberately broken responder in section 10 — which is what check
# SEC-005 exists to find.
_raw_types = os.environ.get("RESPOND_TO_TYPES", "[]")
SEVERITY_THRESHOLD = os.environ.get("SEVERITY_THRESHOLD", "")

try:
    RESPOND_TO_TYPES = json.loads(_raw_types)
except ValueError:
    RESPOND_TO_TYPES = []

ec2 = boto3.client("ec2")
sns = boto3.client("sns")
ssm = boto3.client("ssm")


###############################################################################
# The kill switch
###############################################################################


def kill_switch_armed():
    """Read the switch fresh, every invocation, no caching.

    Caching this would save a few milliseconds per invocation and would mean a
    warm container keeps acting for minutes after somebody flipped the switch —
    during precisely the incident where they flipped it.

    FAIL SAFE, NOT FAIL OPEN. If the parameter is missing or unreadable, this
    returns False and the responder does nothing. An automation that keeps
    containing production while its own control plane is broken is worse than
    one that stops, and this is the one place in the file where the cautious
    default is obviously right.
    """
    if not KILL_SWITCH_PARAM:
        # No switch configured at all. That is check SEC-014, and it is
        # reported rather than silently treated as ARMED.
        print(json.dumps({"warning": "no kill switch configured (SEC-014)"}))
        return True

    try:
        value = ssm.get_parameter(Name=KILL_SWITCH_PARAM)["Parameter"]["Value"]
    except Exception as exc:  # noqa: BLE001 — deliberate, see the docstring
        print(json.dumps({
            "kill_switch": "UNREADABLE",
            "error": f"{type(exc).__name__}: {exc}",
            "decision": "failing safe — taking no action",
        }))
        return False

    return value.strip().upper() == "ARMED"


###############################################################################
# The decision
###############################################################################


def is_sample(finding):
    """True when this is a GuardDuty sample finding.

    Samples carry a "[SAMPLE]" title prefix and the `sample: true` service
    attribute. Both are checked, because the title prefix is a string somebody
    could reasonably reformat.

    GET THIS TEST THE RIGHT WAY ROUND. A responder that acts ONLY on samples
    works beautifully in the lab and does nothing at all in production, and
    the two are indistinguishable from the outside. That is why ACT_ON_SAMPLES
    is an explicit environment variable rather than an implicit assumption:
    you have to say which behaviour you meant.
    """
    if finding.get("service", {}).get("additionalInfo", {}).get("sample") is True:
        return True
    return str(finding.get("title", "")).startswith("[SAMPLE]")


def should_respond(finding):
    """Decide, and return the reason either way.

    The reason string ends up in the notification and the logs. That is not
    decoration — 'why did nothing happen' is asked far more often than 'why
    did something happen', and a responder that cannot answer it gets replaced
    by a human with a runbook.
    """
    finding_type = finding.get("type", "")
    severity = finding.get("severity", 0)

    if SEVERITY_THRESHOLD:
        # THE BROKEN PATH (SEC-005). Kept here so the deliberately broken
        # responder in section 10 runs this same file, and so you can watch
        # what a severity threshold actually matches.
        try:
            threshold = float(SEVERITY_THRESHOLD)
        except ValueError:
            return False, "SEVERITY_THRESHOLD is not a number"
        if severity >= threshold:
            return True, (
                f"severity {severity} >= threshold {threshold} — NOTE: severity "
                f"is impact, not confidence, and this rule cannot tell a real "
                f"compromise from your own penetration test"
            )
        return False, f"severity {severity} below threshold {threshold}"

    if finding_type in RESPOND_TO_TYPES:
        return True, f"finding type {finding_type} is on the allow-list"

    return False, (
        f"finding type {finding_type} is not on the allow-list of "
        f"{len(RESPOND_TO_TYPES)} reviewed type(s)"
    )


###############################################################################
# Containment
###############################################################################


def extract_instance_id(finding):
    resource = finding.get("resource", {})
    instance = resource.get("instanceDetails", {}) or {}
    return instance.get("instanceId")


def current_security_groups(instance_id):
    """The groups currently attached, recorded BEFORE anything changes.

    This is the whole rollback story. Without it, 'reversible' means somebody
    reconstructing the original security groups from memory at 03:00, and that
    is not reversible, it is optimistic.
    """
    reservations = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"]
    for reservation in reservations:
        for instance in reservation["Instances"]:
            return [g["GroupId"] for g in instance.get("SecurityGroups", [])]
    return []


def contain(finding, instance_id, reason):
    """Isolate the instance, reversibly, and record how to undo it.

    Two guards before anything changes:
      * CONTAINMENT_MODE must be a mode this file recognises. Anything else —
        including the `terminate` the broken responder is configured with — is
        refused, loudly. The destructive path does not exist to be
        misconfigured into.
      * The quarantine group must exist and must be in the instance's VPC.
        A quarantine group in the wrong VPC cannot be attached, and finding
        that out during the incident is a bad time.
    """
    if CONTAINMENT_MODE not in ("dry-run", "isolate"):
        return {
            "action": "REFUSED",
            "reason": (
                f"CONTAINMENT_MODE={CONTAINMENT_MODE!r} is not a mode this "
                f"responder implements. Destructive containment is deliberately "
                f"not offered — see the module docstring. Nothing was changed."
            ),
        }

    previous = current_security_groups(instance_id)

    rollback = (
        f"aws ec2 modify-instance-attribute --instance-id {instance_id} "
        f"--groups {' '.join(previous) if previous else '<ORIGINAL-GROUPS-UNKNOWN>'} "
        f"--region {REGION}"
    )

    if CONTAINMENT_MODE == "dry-run":
        return {
            "action": "DRY-RUN",
            "would_have": f"replaced security groups {previous} with [{QUARANTINE_SG}]",
            "instance_id": instance_id,
            "previous_security_groups": previous,
            "rollback_command": rollback,
            "reason": reason,
        }

    ec2.modify_instance_attribute(InstanceId=instance_id, Groups=[QUARANTINE_SG])

    # Tag the instance so anyone who finds it later — in the console, in a
    # cost report, in three weeks — can see what happened without reading
    # CloudTrail. An isolated instance nobody can explain gets terminated by
    # someone tidying up, along with the evidence.
    ec2.create_tags(
        Resources=[instance_id],
        Tags=[
            {"Key": "SecurityContainment", "Value": "isolated"},
            {"Key": "SecurityContainmentFinding", "Value": finding.get("id", "unknown")[:255]},
            {"Key": "SecurityContainmentAt", "Value": datetime.now(timezone.utc).isoformat()},
            {"Key": "SecurityContainmentPreviousSGs", "Value": ",".join(previous)[:255]},
        ],
    )

    return {
        "action": "ISOLATED",
        "instance_id": instance_id,
        "previous_security_groups": previous,
        "quarantine_security_group": QUARANTINE_SG,
        "rollback_command": rollback,
        "reason": reason,
        "warning": (
            "This instance is now unreachable by YOU as well — no SSH, no "
            "Session Manager. Snapshot before you investigate."
        ),
    }


###############################################################################
# Handler
###############################################################################


def handler(event, context):
    finding = event.get("detail", {})
    finding_id = finding.get("id", "unknown")
    finding_type = finding.get("type", "unknown")
    severity = finding.get("severity", 0)

    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "finding_id": finding_id,
        "finding_type": finding_type,
        "severity": severity,
        "title": finding.get("title"),
        "containment_mode": CONTAINMENT_MODE,
        "allow_list_size": len(RESPOND_TO_TYPES),
        "trigger_style": "severity-threshold (SEC-005)" if SEVERITY_THRESHOLD else "type-allow-list",
    }

    # 1. The kill switch, before anything else. It is checked before the
    #    allow-list on purpose: when somebody flips it, they want everything
    #    to stop, including the parts they have not thought about.
    if not kill_switch_armed():
        record.update({"decision": "NO ACTION", "reason": "kill switch is DISARMED"})
        announce(record)
        return record

    # 2. Samples.
    if is_sample(finding) and not ACT_ON_SAMPLES:
        record.update({
            "decision": "NO ACTION",
            "reason": "this is a GuardDuty SAMPLE finding and ACT_ON_SAMPLES is false",
        })
        announce(record)
        return record

    # 3. The allow-list.
    respond, reason = should_respond(finding)
    if not respond:
        record.update({"decision": "NO ACTION", "reason": reason})
        announce(record)
        return record

    # 4. Is there anything containable?
    instance_id = extract_instance_id(finding)
    if not instance_id:
        record.update({
            "decision": "NO ACTION",
            "reason": (
                f"finding matched the allow-list but names no EC2 instance "
                f"(resource type: {finding.get('resource', {}).get('resourceType')}). "
                f"A human should look at this one."
            ),
        })
        announce(record)
        return record

    record.update(contain(finding, instance_id, reason))
    record["decision"] = record.get("action", "UNKNOWN")
    announce(record)
    return record


def announce(record):
    """Notify and log. Both, always, including when nothing happened.

    'Why did nothing happen' is asked more often than 'why did something
    happen', and a responder that only speaks when it acts cannot answer it.
    The silent path is the one people distrust.
    """
    print(json.dumps(record, default=str))
    try:
        sns.publish(
            TopicArn=TOPIC_ARN,
            Subject=f"[{record.get('decision', '?')}] {record.get('finding_type', '')}"[:100],
            Message=render(record),
        )
    except Exception as exc:  # noqa: BLE001
        # Never let a notification failure change what the responder did.
        print(json.dumps({"sns_publish_failed": f"{type(exc).__name__}: {exc}"}))


def render(record):
    lines = [
        f"AUTOMATED SECURITY RESPONSE — {record.get('decision')}",
        "",
        f"  finding      : {record.get('finding_type')}",
        f"  finding id   : {record.get('finding_id')}",
        f"  severity     : {record.get('severity')}  (IMPACT, not confidence)",
        f"  title        : {record.get('title')}",
        f"  trigger style: {record.get('trigger_style')}",
        f"  mode         : {record.get('containment_mode')}",
        "",
        f"  reason       : {record.get('reason')}",
    ]
    if record.get("instance_id"):
        lines += [
            "",
            f"  instance     : {record['instance_id']}",
            f"  previous SGs : {record.get('previous_security_groups')}",
            "",
            "  TO REVERSE THIS:",
            f"    {record.get('rollback_command')}",
        ]
    if record.get("warning"):
        lines += ["", f"  !! {record['warning']}"]
    lines += [
        "",
        "This action was taken by automation on a probabilistic detection, with",
        "no human in the loop. If it was wrong, reverse it with the command",
        "above and add the finding type to the review list.",
    ]
    return "\n".join(lines)
