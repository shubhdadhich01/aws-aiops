"""
Day 08 — chaos.py
CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

Breaks the Day 08 stack on purpose, so the recovery can be TIMED.

WHY THIS EXISTS
---------------
Every number in a DR plan is a measurement or it is a wish. You cannot
measure a recovery you have never triggered, and you cannot trigger one
politely. This function is the trigger.

It is deliberately small and deliberately blunt. It is not a fault-injection
platform — AWS Fault Injection Service is that, it has a real
`aws:ec2:asg-insufficient-instance-capacity-error` and a genuine
AZ-availability-power-interruption action, and it costs ~$0.10 per
action-minute. This function exists so that the first time you break
something on purpose, you do it with forty lines of Python you have read
rather than a service you have not.

WHAT IT CAN DO
--------------
  terminate_instance   Terminate one running ASG instance. Tests the plain
                       replacement path: ASG notices capacity is below
                       desired, launches a replacement.

  mark_unhealthy       Call autoscaling:SetInstanceHealth with Unhealthy.
                       This is the one that separates the two health check
                       types. With health_check_type = "ELB" the ASG honours
                       it and replaces the instance. It is also the closest
                       safe analogue to "the application is broken but the
                       instance is fine", which is the failure EC2 health
                       checks cannot see.

  isolate_az           Associate a deny-all network ACL with one AZ's private
                       subnet. Instances there keep running, keep passing EC2
                       status checks, and become unreachable. The ALB stops
                       routing to them; the ASG replaces them only if
                       health_check_type is "ELB".

                       This is NOT an AZ failure. A real AZ failure takes the
                       NAT gateway, the RDS standby, the EBS control plane for
                       that zone and every cross-AZ dependency you did not
                       know you had, simultaneously, while the console is also
                       degraded. This takes the network. It is the closest you
                       can get for free, and the gap between it and the real
                       thing is worth naming out loud in the debrief.

  restore              Undo isolate_az by removing the chaos NACL association.
                       Read the failback note at the bottom of this file.

DRY RUN
-------
Defaults to dry run, from CHAOS_DRY_RUN. Day 07 argued that any irreversible
automated action needs a dry-run mode and a human gate. That argument is
stronger here, because Day 07's automation contained a threat and this one
causes an outage. Run it dry first, every time, and read the plan. The blast
radius is never quite what you assumed.

Payload:
    {"mode": "terminate_instance", "dry_run": false}
    {"mode": "isolate_az", "az": "us-east-1b", "dry_run": false}
    {"mode": "restore"}
"""

import json
import os
import random
import time

import boto3
from botocore.exceptions import ClientError

ASG_NAME = os.environ.get("ASG_NAME", "")
CHAOS_NACL_ID = os.environ.get("CHAOS_NACL_ID", "")
PRIVATE_SUBNET_IDS = [s for s in os.environ.get("PRIVATE_SUBNET_IDS", "").split(",") if s]
TOPIC_ARN = os.environ.get("TOPIC_ARN", "")
DEFAULT_DRY_RUN = os.environ.get("CHAOS_DRY_RUN", "true").lower() == "true"

VALID_MODES = ("terminate_instance", "mark_unhealthy", "isolate_az", "restore")

ec2 = boto3.client("ec2")
asg = boto3.client("autoscaling")
sns = boto3.client("sns")


def _log(**kw):
    """One JSON object per line. Day 06's argument: a log you cannot grep is a
    log you will not read at 03:00."""
    print(json.dumps(kw, default=str))


def _notify(subject, body):
    if not TOPIC_ARN:
        return
    try:
        sns.publish(TopicArn=TOPIC_ARN, Subject=subject[:100], Message=body)
    except ClientError as exc:
        # A failed notification must never fail the action, and must never be
        # silent. Day 04's rule, unchanged.
        _log(event="notify_failed", error=str(exc))


def _asg_instances():
    """Running, InService instances in the ASG, with their AZ."""
    if not ASG_NAME:
        return []
    paginator = asg.get_paginator("describe_auto_scaling_groups")
    out = []
    for page in paginator.paginate(AutoScalingGroupNames=[ASG_NAME]):
        for group in page.get("AutoScalingGroups", []):
            for inst in group.get("Instances", []):
                if inst.get("LifecycleState") == "InService":
                    out.append(
                        {
                            "instance_id": inst["InstanceId"],
                            "az": inst.get("AvailabilityZone", "?"),
                            "health": inst.get("HealthStatus", "?"),
                        }
                    )
    return out


def _subnets_by_az():
    """Map AZ -> private subnet id, for the subnets this stack owns."""
    if not PRIVATE_SUBNET_IDS:
        return {}
    resp = ec2.describe_subnets(SubnetIds=PRIVATE_SUBNET_IDS)
    return {s["AvailabilityZone"]: s["SubnetId"] for s in resp.get("Subnets", [])}


def _current_association(subnet_id):
    """The NACL association id currently covering this subnet.

    Every subnet always has exactly one NACL association — the VPC default if
    nothing else. There is no 'detach a NACL' call; you REPLACE the
    association. That asymmetry is why `restore` needs to know which NACL was
    there before, and it is a small, honest example of why failback is harder
    than failover.
    """
    resp = ec2.describe_network_acls(
        Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
    )
    for acl in resp.get("NetworkAcls", []):
        for assoc in acl.get("Associations", []):
            if assoc.get("SubnetId") == subnet_id:
                return assoc["NetworkAclAssociationId"], acl["NetworkAclId"], acl.get("IsDefault", False)
    return None, None, False


def _default_nacl_for_vpc(vpc_id):
    resp = ec2.describe_network_acls(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "default", "Values": ["true"]},
        ]
    )
    acls = resp.get("NetworkAcls", [])
    return acls[0]["NetworkAclId"] if acls else None


def mode_terminate_instance(dry_run, _payload):
    instances = _asg_instances()
    if not instances:
        return {"action": "terminate_instance", "result": "no InService instances found"}

    victim = random.choice(instances)
    plan = {
        "action": "terminate_instance",
        "target": victim["instance_id"],
        "az": victim["az"],
        "expected": (
            "ASG detects capacity below desired within ~1 minute and launches a "
            "replacement. Time from termination to the replacement passing its "
            "target group health check is the number you are measuring."
        ),
    }
    if dry_run:
        plan["result"] = "DRY RUN — nothing terminated"
        return plan

    ec2.terminate_instances(InstanceIds=[victim["instance_id"]])
    plan["result"] = "terminated"
    plan["terminated_at"] = time.time()
    return plan


def mode_mark_unhealthy(dry_run, _payload):
    instances = _asg_instances()
    if not instances:
        return {"action": "mark_unhealthy", "result": "no InService instances found"}

    victim = random.choice(instances)
    plan = {
        "action": "mark_unhealthy",
        "target": victim["instance_id"],
        "az": victim["az"],
        "expected": (
            "If the ASG health_check_type is ELB, the instance is replaced. If it "
            "is EC2, this call is honoured too — but the equivalent REAL failure, "
            "an application returning 503 while the OS is fine, would not be, and "
            "that is the distinction to hold on to."
        ),
    }
    if dry_run:
        plan["result"] = "DRY RUN — health status unchanged"
        return plan

    asg.set_instance_health(
        InstanceId=victim["instance_id"],
        HealthStatus="Unhealthy",
        ShouldRespectGracePeriod=False,
    )
    plan["result"] = "marked Unhealthy"
    plan["marked_at"] = time.time()
    return plan


def mode_isolate_az(dry_run, payload):
    if not CHAOS_NACL_ID:
        return {"action": "isolate_az", "result": "no chaos NACL configured"}

    by_az = _subnets_by_az()
    if not by_az:
        return {"action": "isolate_az", "result": "no private subnets configured"}

    az = payload.get("az") or sorted(by_az)[0]
    subnet_id = by_az.get(az)
    if not subnet_id:
        return {
            "action": "isolate_az",
            "result": "unknown AZ",
            "az": az,
            "known": sorted(by_az),
        }

    assoc_id, previous_nacl, is_default = _current_association(subnet_id)
    plan = {
        "action": "isolate_az",
        "az": az,
        "subnet": subnet_id,
        "association": assoc_id,
        "previous_nacl": previous_nacl,
        "previous_was_default": is_default,
        "expected": (
            "Targets in this AZ stop responding to ALB health checks. They keep "
            "passing EC2 status checks. Detection takes interval x "
            "unhealthy_threshold seconds before the ALB stops routing to them."
        ),
    }
    if dry_run:
        plan["result"] = "DRY RUN — association unchanged"
        return plan
    if not assoc_id:
        plan["result"] = "no association found; nothing changed"
        return plan

    ec2.replace_network_acl_association(
        AssociationId=assoc_id, NetworkAclId=CHAOS_NACL_ID
    )
    plan["result"] = "isolated"
    plan["isolated_at"] = time.time()
    return plan


def mode_restore(dry_run, _payload):
    """Failback. Note how much less code the breaking took than the fixing.

    That ratio is not an accident of this file. It is the shape of the whole
    problem: the outbound path is one API call and the return path has to
    reconstruct state that the outbound path destroyed. Here it is trivial
    because the state is one association id. In a real failover the state is
    every write that happened while you were failed over.
    """
    by_az = _subnets_by_az()
    if not by_az:
        return {"action": "restore", "result": "no private subnets configured"}

    restored = []
    for az, subnet_id in sorted(by_az.items()):
        assoc_id, current_nacl, is_default = _current_association(subnet_id)
        if current_nacl != CHAOS_NACL_ID:
            continue

        subnet = ec2.describe_subnets(SubnetIds=[subnet_id])["Subnets"][0]
        default_nacl = _default_nacl_for_vpc(subnet["VpcId"])
        entry = {"az": az, "subnet": subnet_id, "target_nacl": default_nacl}
        if dry_run:
            entry["result"] = "DRY RUN — association unchanged"
        elif assoc_id and default_nacl:
            ec2.replace_network_acl_association(
                AssociationId=assoc_id, NetworkAclId=default_nacl
            )
            entry["result"] = "restored"
            entry["restored_at"] = time.time()
        else:
            entry["result"] = "could not resolve default NACL; restore by hand"
        restored.append(entry)

    return {
        "action": "restore",
        "restored": restored,
        "result": "nothing was isolated" if not restored else "processed",
    }


HANDLERS = {
    "terminate_instance": mode_terminate_instance,
    "mark_unhealthy": mode_mark_unhealthy,
    "isolate_az": mode_isolate_az,
    "restore": mode_restore,
}


def lambda_handler(event, _context):
    payload = event if isinstance(event, dict) else {}
    mode = payload.get("mode", "terminate_instance")
    dry_run = bool(payload.get("dry_run", DEFAULT_DRY_RUN))

    if mode not in VALID_MODES:
        result = {"error": "unknown mode", "mode": mode, "valid": list(VALID_MODES)}
        _log(event="chaos_rejected", **result)
        return {"statusCode": 400, "body": json.dumps(result)}

    started = time.time()
    result = HANDLERS[mode](dry_run, payload)
    result["dry_run"] = dry_run
    result["mode"] = mode
    result["duration_seconds"] = round(time.time() - started, 3)
    result["started_epoch"] = started

    _log(event="chaos_executed", **result)

    if not dry_run:
        _notify(
            "Day 08 chaos: {}".format(mode),
            "Start your stopwatch NOW.\n\n"
            + json.dumps(result, indent=2, default=str)
            + "\n\nWrite down the time you expect recovery to take BEFORE you "
            "look at the console. The gap between that number and the measured "
            "one is the point of this exercise.\n",
        )

    return {"statusCode": 200, "body": json.dumps(result, default=str)}
