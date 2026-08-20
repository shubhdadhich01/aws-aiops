#!/usr/bin/env python3
"""
ha_audit.py — Day 03 resilience / high-availability auditor.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

Audits Auto Scaling Groups, launch templates, load balancers and target groups
for the configuration mistakes that turn "highly available" architectures into
single points of failure.

What it checks
--------------
    ASG-001  Capacity sanity        min/desired/max are incoherent or min < 2
    ASG-002  Single-AZ ASG          all subnets in one Availability Zone
    ASG-003  EC2 health check type  hung apps are never replaced
    ASG-004  Grace period           missing, too short, or dangerously long
    ASG-005  No scaling policies    a fixed fleet with extra steps
    ASG-006  Unhealthy targets      targets registered but failing health checks
    ASG-007  Zero healthy targets   the service is down right now
    ASG-008  No HTTPS listener      ALB serves plaintext only
    ASG-009  HTTP not redirecting   port 80 forwards instead of 301-ing to 443
    ASG-010  NLB cross-zone off     uneven AZ distribution overloads one target
    ASG-011  IMDSv1 allowed         SSRF becomes credential theft
    ASG-012  Unencrypted root EBS   automatic compliance failure
    ASG-013  Termination policy     no diversity, scale-in order is arbitrary
    ASG-014  Instances not spread   ASG spans AZs but instances do not

Usage
-----
    python3 ha_audit.py --profile bootcamp --region us-east-1
    python3 ha_audit.py --format json --quiet > findings.json
    python3 ha_audit.py --min-severity HIGH --format csv
    python3 ha_audit.py --fail-on HIGH        # non-zero exit for CI

Required IAM permissions (all read-only):
    autoscaling:DescribeAutoScalingGroups
    autoscaling:DescribePolicies
    ec2:DescribeLaunchTemplates
    ec2:DescribeLaunchTemplateVersions
    ec2:DescribeInstances
    elasticloadbalancing:DescribeLoadBalancers
    elasticloadbalancing:DescribeListeners
    elasticloadbalancing:DescribeTargetGroups
    elasticloadbalancing:DescribeTargetGroupAttributes
    elasticloadbalancing:DescribeLoadBalancerAttributes
    elasticloadbalancing:DescribeTargetHealth

The SecurityAudit or ReadOnlyAccess managed policy covers all of these.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
except ImportError:  # pragma: no cover
    print(
        "boto3 is not installed. Run:  pip install -r requirements.txt",
        file=sys.stderr,
    )
    sys.exit(2)


###############################################################################
# Severity model
#
# The score starts at 100 and each finding subtracts its severity weight.
# Floor is 0 — a score cannot go negative, because "how much worse than
# completely broken is this" is not a useful question.
#
# The weights are deliberately steep. One CRITICAL costs as much as six MEDIUMs
# because in resilience work, one thing that takes the service down outranks a
# pile of things that make it slightly worse.
###############################################################################

SEVERITY_WEIGHTS: Dict[str, int] = {
    "CRITICAL": 25,
    "HIGH": 10,
    "MEDIUM": 4,
    "LOW": 1,
    "INFO": 0,
}

SEVERITY_ORDER: List[str] = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]

# ANSI colours. Disabled automatically when stdout is not a TTY, so piping to a
# file or into `jq` does not produce escape-code soup.
_COLOURS = {
    "CRITICAL": "\033[1;91m",
    "HIGH": "\033[1;31m",
    "MEDIUM": "\033[1;33m",
    "LOW": "\033[1;36m",
    "INFO": "\033[1;90m",
    "RESET": "\033[0m",
    "BOLD": "\033[1m",
    "DIM": "\033[2m",
    "GREEN": "\033[1;32m",
}


def colour(text: str, key: str, enabled: bool = True) -> str:
    """Wrap text in an ANSI colour, or return it unchanged."""
    if not enabled or key not in _COLOURS:
        return text
    return f"{_COLOURS[key]}{text}{_COLOURS['RESET']}"


###############################################################################
# Finding
###############################################################################


@dataclass
class Finding:
    """A single audit finding.

    check_id     Stable identifier (ASG-001 ...). Never renumber these — people
                 write suppressions and dashboards against them.
    severity     One of SEVERITY_ORDER.
    resource_type / resource_id   What is broken.
    title        One line, imperative, readable in a table.
    detail       What was actually observed. Include the real values.
    remediation  What to do about it, concretely.
    evidence     Raw values so the finding is auditable without re-querying.
    """

    check_id: str
    severity: str
    resource_type: str
    resource_id: str
    title: str
    detail: str
    remediation: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    region: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITY_WEIGHTS:
            raise ValueError(
                f"{self.check_id}: unknown severity {self.severity!r}. "
                f"Expected one of {', '.join(SEVERITY_ORDER)}."
            )

    @property
    def weight(self) -> int:
        return SEVERITY_WEIGHTS[self.severity]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


###############################################################################
# Paginator helpers
#
# boto3 paginators are the correct way to do this. The wrong way — calling
# describe_* once and trusting the first page — silently misses everything past
# the first 50-100 items, which is exactly the situation where an audit matters.
###############################################################################


def paginate(client: Any, operation: str, result_key: str, **kwargs: Any) -> List[Dict[str, Any]]:
    """Collect every page of a paginated boto3 operation into one list.

    Falls back to a single direct call for operations that have no paginator
    registered (several elbv2 operations are like this).
    """
    items: List[Dict[str, Any]] = []
    try:
        if client.can_paginate(operation):
            paginator = client.get_paginator(operation)
            for page in paginator.paginate(**kwargs):
                items.extend(page.get(result_key, []))
        else:
            response = getattr(client, operation)(**kwargs)
            items.extend(response.get(result_key, []))
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation"):
            print(
                f"  ! Access denied calling {operation}. Skipping the checks that "
                f"depend on it. Attach SecurityAudit or ReadOnlyAccess to fix.",
                file=sys.stderr,
            )
            return []
        raise
    return items


def chunked(items: List[Any], size: int) -> Iterable[List[Any]]:
    """Yield fixed-size chunks. Several EC2 APIs cap the number of IDs per call."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


###############################################################################
# Check implementations
#
# Every check is a pure function: dict in, list[Finding] out. No AWS calls, no
# printing. That means they can be unit-tested against synthetic fixtures with
# no credentials, which is exactly what tests/test_checks.py does.
###############################################################################


def check_capacity_sanity(asg: Dict[str, Any], region: str = "") -> List[Finding]:
    """ASG-001 — min/desired/max coherence and the min_size >= 2 rule."""
    findings: List[Finding] = []
    name = asg.get("AutoScalingGroupName", "unknown")
    min_size = asg.get("MinSize", 0)
    max_size = asg.get("MaxSize", 0)
    desired = asg.get("DesiredCapacity", 0)

    if min_size < 2:
        findings.append(
            Finding(
                check_id="ASG-001",
                severity="HIGH",
                resource_type="AutoScalingGroup",
                resource_id=name,
                title="Minimum size below 2 — not highly available",
                detail=(
                    f"min_size is {min_size}. A single instance lives in a single "
                    f"Availability Zone. Any instance replacement, AZ event or "
                    f"deployment leaves the service with zero capacity."
                ),
                remediation=(
                    "Set min_size to at least 2 and spread the ASG across at least "
                    "two AZs. This is the floor for calling anything 'HA'."
                ),
                evidence={"MinSize": min_size, "DesiredCapacity": desired, "MaxSize": max_size},
                region=region,
            )
        )

    if max_size <= min_size and max_size == desired:
        findings.append(
            Finding(
                check_id="ASG-001",
                severity="MEDIUM",
                resource_type="AutoScalingGroup",
                resource_id=name,
                title="max_size equals desired capacity — scaling is impossible",
                detail=(
                    f"max_size={max_size}, desired={desired}. Any scaling policy "
                    f"attached to this group can never add an instance. The group "
                    f"is a fixed-size fleet wearing an Auto Scaling costume."
                ),
                remediation=(
                    "Raise max_size to at least 2x desired capacity so scale-out "
                    "has somewhere to go."
                ),
                evidence={"MinSize": min_size, "DesiredCapacity": desired, "MaxSize": max_size},
                region=region,
            )
        )

    if desired < min_size or desired > max_size:
        findings.append(
            Finding(
                check_id="ASG-001",
                severity="MEDIUM",
                resource_type="AutoScalingGroup",
                resource_id=name,
                title="Desired capacity is outside min/max bounds",
                detail=(
                    f"desired={desired} is not between min={min_size} and "
                    f"max={max_size}. The group will be forced back into range, "
                    f"which usually means an unexpected scale event."
                ),
                remediation="Correct the capacity values so min <= desired <= max.",
                evidence={"MinSize": min_size, "DesiredCapacity": desired, "MaxSize": max_size},
                region=region,
            )
        )

    return findings


def check_single_az(asg: Dict[str, Any], region: str = "") -> List[Finding]:
    """ASG-002 — the ASG spans only one Availability Zone."""
    name = asg.get("AutoScalingGroupName", "unknown")
    azs = sorted(set(asg.get("AvailabilityZones", []) or []))

    if len(azs) >= 2:
        return []

    return [
        Finding(
            check_id="ASG-002",
            severity="HIGH",
            resource_type="AutoScalingGroup",
            resource_id=name,
            title="Auto Scaling Group is confined to a single Availability Zone",
            detail=(
                f"This group can only launch into {azs or ['<none>']}. An AZ-level "
                f"event takes the entire group down, and the ASG cannot recover "
                f"because it has nowhere else to launch."
            ),
            remediation=(
                "Add subnets from at least one more AZ to vpc_zone_identifier. "
                "This is a one-line Terraform change and the single highest-value "
                "resilience fix available."
            ),
            evidence={
                "AvailabilityZones": azs,
                "VPCZoneIdentifier": asg.get("VPCZoneIdentifier", ""),
            },
            region=region,
        )
    ]


def check_health_check_type(asg: Dict[str, Any], region: str = "") -> List[Finding]:
    """ASG-003 — health_check_type is EC2 on a load-balanced group."""
    name = asg.get("AutoScalingGroupName", "unknown")
    hc_type = asg.get("HealthCheckType", "EC2")
    attached = bool(asg.get("TargetGroupARNs") or asg.get("LoadBalancerNames"))

    if hc_type == "ELB":
        return []

    if attached:
        severity = "HIGH"
        detail = (
            "This group is registered with a load balancer but health_check_type "
            "is 'EC2', so the ASG only watches EC2 system and instance status "
            "checks. A hung application, an OOM-killed process, or a web server "
            "returning 502 leaves those checks green — the instance is never "
            "replaced and serves errors indefinitely."
        )
    else:
        severity = "MEDIUM"
        detail = (
            "health_check_type is 'EC2' and this group has no load balancer "
            "attached, so there is no application-level health signal at all. "
            "Only a dead kernel triggers replacement."
        )

    return [
        Finding(
            check_id="ASG-003",
            severity=severity,
            resource_type="AutoScalingGroup",
            resource_id=name,
            title="Health check type is EC2, not ELB",
            detail=detail,
            remediation=(
                "Set health_check_type = \"ELB\" and make sure the target group's "
                "health check hits a real application endpoint (/health), not '/'. "
                "Confirm health_check_grace_period exceeds boot-to-healthy time "
                "before you change this, or you risk a launch loop."
            ),
            evidence={
                "HealthCheckType": hc_type,
                "TargetGroupARNs": asg.get("TargetGroupARNs", []),
                "LoadBalancerNames": asg.get("LoadBalancerNames", []),
            },
            region=region,
        )
    ]


def check_grace_period(asg: Dict[str, Any], region: str = "") -> List[Finding]:
    """ASG-004 — health check grace period missing, too short, or too long."""
    findings: List[Finding] = []
    name = asg.get("AutoScalingGroupName", "unknown")
    grace = asg.get("HealthCheckGracePeriod")

    if grace is None or grace == 0:
        findings.append(
            Finding(
                check_id="ASG-004",
                severity="MEDIUM",
                resource_type="AutoScalingGroup",
                resource_id=name,
                title="No health check grace period set",
                detail=(
                    "Health checks apply from the moment the instance launches. "
                    "If the application takes any time at all to start, the ASG "
                    "kills the instance mid-boot, launches a replacement, and "
                    "kills that one too — an infinite launch loop that bills "
                    "continuously and never converges."
                ),
                remediation=(
                    "Set health_check_grace_period to measured boot-to-healthy "
                    "time x 1.5. For a simple AL2023 + web server AMI, 300 "
                    "seconds is generous and safe."
                ),
                evidence={"HealthCheckGracePeriod": grace},
                region=region,
            )
        )
    elif grace < 60:
        findings.append(
            Finding(
                check_id="ASG-004",
                severity="MEDIUM",
                resource_type="AutoScalingGroup",
                resource_id=name,
                title=f"Health check grace period is only {grace}s",
                detail=(
                    f"{grace} seconds is shorter than almost any real boot "
                    f"sequence. Instances that have not finished starting will be "
                    f"marked unhealthy and terminated, producing a launch loop."
                ),
                remediation=(
                    "Time an actual launch from RunInstances to first passing "
                    "health check, then set the grace period to 1.5x that."
                ),
                evidence={"HealthCheckGracePeriod": grace},
                region=region,
            )
        )
    elif grace > 900:
        findings.append(
            Finding(
                check_id="ASG-004",
                severity="LOW",
                resource_type="AutoScalingGroup",
                resource_id=name,
                title=f"Health check grace period is {grace}s — very long",
                detail=(
                    f"A genuinely broken instance will serve errors for up to "
                    f"{grace // 60} minutes before the ASG considers replacing it. "
                    f"That is a long MTTR for a self-healing system."
                ),
                remediation=(
                    "Unless the application genuinely needs this long to warm up, "
                    "reduce the grace period. If it does, consider a lifecycle "
                    "hook instead so the wait is explicit."
                ),
                evidence={"HealthCheckGracePeriod": grace},
                region=region,
            )
        )

    return findings


def check_scaling_policies(
    asg: Dict[str, Any], policies: List[Dict[str, Any]], region: str = ""
) -> List[Finding]:
    """ASG-005 — no scaling policies attached to the group."""
    name = asg.get("AutoScalingGroupName", "unknown")
    mine = [p for p in policies if p.get("AutoScalingGroupName") == name]

    if mine:
        return []

    min_size = asg.get("MinSize", 0)
    max_size = asg.get("MaxSize", 0)
    # If min == max the group is deliberately fixed-size; that is a design
    # choice, not a bug. Report it as INFO rather than crying wolf.
    severity = "INFO" if min_size == max_size else "MEDIUM"

    return [
        Finding(
            check_id="ASG-005",
            severity=severity,
            resource_type="AutoScalingGroup",
            resource_id=name,
            title="No scaling policies attached",
            detail=(
                f"This group has min={min_size}, max={max_size} and no scaling "
                f"policy. It will never change capacity in response to load. "
                f"You are paying for peak provisioning at all times, or you are "
                f"under-provisioned at peak — usually both, at different hours."
            ),
            remediation=(
                "Attach a target-tracking policy on ASGAverageCPUUtilization or "
                "ALBRequestCountPerTarget. Target tracking is the right default: "
                "AWS manages the alarms and there are no thresholds to tune."
            ),
            evidence={"MinSize": min_size, "MaxSize": max_size, "PolicyCount": 0},
            region=region,
        )
    ]


def check_termination_policies(asg: Dict[str, Any], region: str = "") -> List[Finding]:
    """ASG-013 — no diversity in termination policy ordering."""
    name = asg.get("AutoScalingGroupName", "unknown")
    policies = asg.get("TerminationPolicies", []) or []

    if len(policies) > 1 or (policies and policies[0] != "Default"):
        return []

    return [
        Finding(
            check_id="ASG-013",
            severity="LOW",
            resource_type="AutoScalingGroup",
            resource_id=name,
            title="Termination policy is bare 'Default'",
            detail=(
                "On scale-in, the default policy picks the instance closest to "
                "the next billing hour among those in the AZ with the most "
                "instances. That is effectively arbitrary — it can retire your "
                "newest instance and keep a stale one running for weeks."
            ),
            remediation=(
                'Set termination_policies = ["OldestLaunchTemplate", '
                '"OldestInstance", "Default"]. Scale-in then doubles as a slow '
                "rolling refresh: stale instances retire first."
            ),
            evidence={"TerminationPolicies": policies},
            region=region,
        )
    ]


def check_instance_az_spread(asg: Dict[str, Any], region: str = "") -> List[Finding]:
    """ASG-014 — the ASG spans AZs but its running instances do not."""
    name = asg.get("AutoScalingGroupName", "unknown")
    configured_azs = sorted(set(asg.get("AvailabilityZones", []) or []))
    instances = asg.get("Instances", []) or []

    if len(configured_azs) < 2 or not instances:
        return []

    in_service = [i for i in instances if i.get("LifecycleState") == "InService"]
    if not in_service:
        return []

    actual_azs = sorted({i.get("AvailabilityZone", "?") for i in in_service})

    if len(actual_azs) >= 2:
        # Spread exists. Check it isn't badly lopsided.
        counts: Dict[str, int] = {}
        for inst in in_service:
            az = inst.get("AvailabilityZone", "?")
            counts[az] = counts.get(az, 0) + 1
        if len(in_service) >= 4 and max(counts.values()) > 0.75 * len(in_service):
            return [
                Finding(
                    check_id="ASG-014",
                    severity="LOW",
                    resource_type="AutoScalingGroup",
                    resource_id=name,
                    title="Instances are heavily skewed toward one AZ",
                    detail=(
                        f"Instance distribution is {counts}. More than 75% of "
                        f"capacity sits in one AZ, so losing that AZ removes most "
                        f"of the fleet at once and the survivors must absorb the "
                        f"whole load while replacements boot."
                    ),
                    remediation=(
                        "Check for capacity constraints on this instance type in "
                        "the under-used AZ, and consider a mixed instances policy "
                        "so the ASG can rebalance."
                    ),
                    evidence={"InstancesPerAZ": counts, "ConfiguredAZs": configured_azs},
                    region=region,
                )
            ]
        return []

    return [
        Finding(
            check_id="ASG-014",
            severity="MEDIUM",
            resource_type="AutoScalingGroup",
            resource_id=name,
            title="All running instances are in one AZ despite multi-AZ config",
            detail=(
                f"The group is configured for {configured_azs} but every "
                f"in-service instance is in {actual_azs}. The architecture "
                f"diagram says multi-AZ; the running reality does not. Usually "
                f"this means a capacity shortage for this instance type in the "
                f"other AZ, or a subnet with no free IP addresses."
            ),
            remediation=(
                "Check subnet free IP counts and instance type availability per "
                "AZ. Consider a mixed instances policy so the ASG can fall back "
                "to a comparable instance type instead of piling into one AZ."
            ),
            evidence={
                "ConfiguredAZs": configured_azs,
                "ActualAZs": actual_azs,
                "InServiceCount": len(in_service),
            },
            region=region,
        )
    ]


def check_launch_template_metadata(
    lt_name: str, lt_data: Dict[str, Any], used_by: List[str], region: str = ""
) -> List[Finding]:
    """ASG-011 — IMDSv1 still permitted on a launch template."""
    meta = lt_data.get("MetadataOptions", {}) or {}
    tokens = meta.get("HttpTokens", "optional")
    endpoint = meta.get("HttpEndpoint", "enabled")

    if endpoint == "disabled" or tokens == "required":
        return []

    return [
        Finding(
            check_id="ASG-011",
            severity="HIGH",
            resource_type="LaunchTemplate",
            resource_id=lt_name,
            title="IMDSv1 is allowed (HttpTokens is not 'required')",
            detail=(
                f"HttpTokens={tokens!r}. IMDSv1 answers an unauthenticated GET to "
                f"169.254.169.254, so any SSRF bug in the application — a "
                f"URL-fetching feature, an image proxy, a webhook tester — can "
                f"read this instance's IAM role credentials. This is the shape of "
                f"the 2019 Capital One breach."
            ),
            remediation=(
                "Set metadata_options { http_tokens = \"required\" } and "
                "http_put_response_hop_limit = 1. The change costs nothing and "
                "requires no application change for any current AWS SDK. Roll it "
                "out with an instance refresh."
            ),
            evidence={
                "HttpTokens": tokens,
                "HttpEndpoint": endpoint,
                "HttpPutResponseHopLimit": meta.get("HttpPutResponseHopLimit"),
                "UsedByAutoScalingGroups": used_by,
            },
            region=region,
        )
    ]


def check_launch_template_encryption(
    lt_name: str, lt_data: Dict[str, Any], used_by: List[str], region: str = ""
) -> List[Finding]:
    """ASG-012 — root (or any) EBS volume not encrypted."""
    findings: List[Finding] = []
    mappings = lt_data.get("BlockDeviceMappings", []) or []

    for mapping in mappings:
        ebs = mapping.get("Ebs")
        if not ebs:
            continue
        device = mapping.get("DeviceName", "unknown")
        encrypted = ebs.get("Encrypted")

        # Encrypted absent means "inherit" — the AMI's setting, or the account's
        # EBS-encryption-by-default flag. That is not a guarantee, so we flag it
        # at a lower severity rather than ignoring it.
        if encrypted is True:
            continue

        severity = "MEDIUM" if encrypted is False else "LOW"
        state = "explicitly disabled" if encrypted is False else "not specified"

        findings.append(
            Finding(
                check_id="ASG-012",
                severity=severity,
                resource_type="LaunchTemplate",
                resource_id=f"{lt_name}:{device}",
                title=f"EBS encryption {state} on {device}",
                detail=(
                    f"Volume {device} in launch template {lt_name} has "
                    f"Encrypted={encrypted!r}. Every instance launched from this "
                    f"template writes application data, logs and any cached "
                    f"secrets to unencrypted storage. This is an automatic "
                    f"failure under PCI DSS, HIPAA, SOC 2 and CIS benchmarks."
                ),
                remediation=(
                    "Set encrypted = true in the block_device_mappings ebs block. "
                    "Encryption with the default aws/ebs key costs nothing and has "
                    "no measurable performance impact. Also enable EBS encryption "
                    "by default at the account level so this cannot regress."
                ),
                evidence={
                    "DeviceName": device,
                    "Encrypted": encrypted,
                    "VolumeType": ebs.get("VolumeType"),
                    "KmsKeyId": ebs.get("KmsKeyId"),
                    "UsedByAutoScalingGroups": used_by,
                },
                region=region,
            )
        )

    return findings


def check_target_health(
    tg: Dict[str, Any], health: List[Dict[str, Any]], region: str = ""
) -> List[Finding]:
    """ASG-006 / ASG-007 — unhealthy or zero healthy targets."""
    findings: List[Finding] = []
    tg_name = tg.get("TargetGroupName", "unknown")

    if not health:
        findings.append(
            Finding(
                check_id="ASG-006",
                severity="LOW",
                resource_type="TargetGroup",
                resource_id=tg_name,
                title="Target group has no registered targets",
                detail=(
                    "No targets are registered. Either this target group is "
                    "orphaned left-over configuration, or the ASG that should be "
                    "registering into it is misconfigured."
                ),
                remediation=(
                    "Attach the target group to an Auto Scaling Group via "
                    "target_group_arns, or delete it if it is unused."
                ),
                evidence={"TargetCount": 0},
                region=region,
            )
        )
        return findings

    states: Dict[str, int] = {}
    unhealthy_ids: List[str] = []
    for entry in health:
        state = entry.get("TargetHealth", {}).get("State", "unknown")
        states[state] = states.get(state, 0) + 1
        if state in ("unhealthy", "unused", "draining"):
            unhealthy_ids.append(entry.get("Target", {}).get("Id", "?"))

    healthy = states.get("healthy", 0)
    total = len(health)

    if healthy == 0:
        findings.append(
            Finding(
                check_id="ASG-007",
                severity="CRITICAL",
                resource_type="TargetGroup",
                resource_id=tg_name,
                title="Zero healthy targets — this service is down right now",
                detail=(
                    f"{total} target(s) registered, none healthy. State breakdown: "
                    f"{states}. The load balancer has nowhere to send traffic and "
                    f"is returning 503 to every request."
                ),
                remediation=(
                    "Check the health check path actually exists and returns the "
                    "expected matcher code. Check the target security group allows "
                    "the health check port FROM the load balancer security group. "
                    "Check the application is listening on the traffic port. Then "
                    "check the ASG grace period is not killing instances mid-boot."
                ),
                evidence={"States": states, "TotalTargets": total},
                region=region,
            )
        )
    elif unhealthy_ids:
        unhealthy_count = len(unhealthy_ids)
        severity = "HIGH" if unhealthy_count >= healthy else "MEDIUM"
        findings.append(
            Finding(
                check_id="ASG-006",
                severity=severity,
                resource_type="TargetGroup",
                resource_id=tg_name,
                title=f"{unhealthy_count} of {total} targets are not healthy",
                detail=(
                    f"State breakdown: {states}. Affected targets: "
                    f"{', '.join(unhealthy_ids[:5])}"
                    f"{' ...' if unhealthy_count > 5 else ''}. Surviving healthy "
                    f"targets are absorbing the full load."
                ),
                remediation=(
                    "Inspect the instances directly with SSM Session Manager and "
                    "curl the health path locally. If it passes locally but fails "
                    "from the LB, the problem is the security group or the port."
                ),
                evidence={"States": states, "UnhealthyTargets": unhealthy_ids[:20]},
                region=region,
            )
        )

    if healthy == 1 and total >= 1:
        findings.append(
            Finding(
                check_id="ASG-006",
                severity="MEDIUM",
                resource_type="TargetGroup",
                resource_id=tg_name,
                title="Only one healthy target — no redundancy",
                detail=(
                    "A single healthy target means the next failure is an outage. "
                    "There is no capacity to absorb a health check flap, a deploy, "
                    "or an AZ event."
                ),
                remediation=(
                    "Raise the ASG min_size to at least 2 across at least 2 AZs."
                ),
                evidence={"HealthyCount": 1, "TotalTargets": total},
                region=region,
            )
        )

    return findings


def check_alb_listeners(
    lb: Dict[str, Any], listeners: List[Dict[str, Any]], region: str = ""
) -> List[Finding]:
    """ASG-008 / ASG-009 — HTTPS listener missing, or HTTP not redirecting."""
    findings: List[Finding] = []

    if lb.get("Type") != "application":
        return findings

    lb_name = lb.get("LoadBalancerName", "unknown")
    scheme = lb.get("Scheme", "unknown")

    https_listeners = [l for l in listeners if l.get("Protocol") == "HTTPS"]
    http_listeners = [l for l in listeners if l.get("Protocol") == "HTTP"]

    if not https_listeners:
        # An internal ALB in a private VPC is a lower risk than one facing the
        # internet, but plaintext east-west traffic is still a finding.
        severity = "HIGH" if scheme == "internet-facing" else "MEDIUM"
        findings.append(
            Finding(
                check_id="ASG-008",
                severity=severity,
                resource_type="LoadBalancer",
                resource_id=lb_name,
                title="No HTTPS listener on this Application Load Balancer",
                detail=(
                    f"This {scheme} ALB has listeners on "
                    f"{[l.get('Port') for l in listeners] or 'no ports'} and none "
                    f"of them terminate TLS. Every request and response, including "
                    f"credentials, session cookies and API tokens, crosses the "
                    f"network in plaintext."
                ),
                remediation=(
                    "Request a free public certificate from ACM, add an HTTPS:443 "
                    "listener using ssl_policy ELBSecurityPolicy-TLS13-1-2-2021-06, "
                    "and convert the HTTP listener to a 301 redirect."
                ),
                evidence={
                    "Scheme": scheme,
                    "ListenerPorts": [l.get("Port") for l in listeners],
                    "Protocols": sorted({l.get("Protocol", "?") for l in listeners}),
                },
                region=region,
            )
        )

    for listener in http_listeners:
        actions = listener.get("DefaultActions", []) or []
        action_types = [a.get("Type") for a in actions]

        redirects_to_https = any(
            a.get("Type") == "redirect"
            and (a.get("RedirectConfig", {}) or {}).get("Protocol") == "HTTPS"
            for a in actions
        )

        if redirects_to_https:
            continue

        findings.append(
            Finding(
                check_id="ASG-009",
                severity="MEDIUM",
                resource_type="Listener",
                resource_id=f"{lb_name}:{listener.get('Port')}",
                title="HTTP listener serves traffic instead of redirecting to HTTPS",
                detail=(
                    f"The listener on port {listener.get('Port')} has default "
                    f"action(s) {action_types}. Clients that arrive over HTTP stay "
                    f"on HTTP for the whole session. A redirect is not a security "
                    f"control by itself, but serving the app on port 80 guarantees "
                    f"plaintext traffic exists."
                ),
                remediation=(
                    'Replace the default action with type = "redirect", '
                    'redirect { port = "443", protocol = "HTTPS", '
                    'status_code = "HTTP_301" }. Then add HSTS at the application.'
                ),
                evidence={
                    "Port": listener.get("Port"),
                    "DefaultActionTypes": action_types,
                    "ListenerArn": listener.get("ListenerArn"),
                },
                region=region,
            )
        )

    return findings


def check_nlb_cross_zone(
    lb: Dict[str, Any], attributes: List[Dict[str, Any]], region: str = ""
) -> List[Finding]:
    """ASG-010 — NLB with cross-zone load balancing disabled."""
    if lb.get("Type") != "network":
        return []

    lb_name = lb.get("LoadBalancerName", "unknown")
    attrs = {a.get("Key"): a.get("Value") for a in attributes}
    enabled = attrs.get("load_balancing.cross_zone.enabled", "false")

    if str(enabled).lower() == "true":
        return []

    azs = [z.get("ZoneName") for z in lb.get("AvailabilityZones", []) or []]

    return [
        Finding(
            check_id="ASG-010",
            severity="MEDIUM",
            resource_type="LoadBalancer",
            resource_id=lb_name,
            title="Network Load Balancer has cross-zone load balancing disabled",
            detail=(
                f"Cross-zone is off (the NLB default). Each of the {len(azs)} "
                f"zonal nodes ({azs}) only forwards to targets in its own AZ. If "
                f"target counts differ between AZs — which they will, during any "
                f"scale event or instance replacement — traffic distribution "
                f"becomes wildly uneven. One instance in a lightly-populated AZ "
                f"can receive 50% of all traffic while several others idle."
            ),
            remediation=(
                "Set enable_cross_zone_load_balancing = true. It costs cross-AZ "
                "data transfer ($0.01/GB each way), which is almost always cheaper "
                "than the incident. Note ALB has this on permanently and free — "
                "this is an NLB-only decision."
            ),
            evidence={
                "CrossZoneEnabled": enabled,
                "AvailabilityZones": azs,
                "Scheme": lb.get("Scheme"),
            },
            region=region,
        )
    ]


###############################################################################
# The auditor — collection + orchestration
###############################################################################


class HAAuditor:
    """Collects AWS state and runs every check against it."""

    def __init__(
        self,
        profile: Optional[str] = None,
        region: str = "us-east-1",
        quiet: bool = False,
    ) -> None:
        self.region = region
        self.quiet = quiet
        self.findings: List[Finding] = []
        self.stats: Dict[str, int] = {
            "asgs": 0,
            "launch_templates": 0,
            "load_balancers": 0,
            "target_groups": 0,
        }

        session_kwargs: Dict[str, Any] = {"region_name": region}
        if profile:
            session_kwargs["profile_name"] = profile

        try:
            self.session = boto3.Session(**session_kwargs)
            self.asg = self.session.client("autoscaling")
            self.ec2 = self.session.client("ec2")
            self.elbv2 = self.session.client("elbv2")
        except (BotoCoreError, NoCredentialsError) as exc:
            print(f"Could not create an AWS session: {exc}", file=sys.stderr)
            sys.exit(2)

    # -- logging ------------------------------------------------------------

    def log(self, message: str) -> None:
        if not self.quiet:
            print(message, file=sys.stderr)

    # -- collection ---------------------------------------------------------

    def collect_asgs(self) -> List[Dict[str, Any]]:
        return paginate(self.asg, "describe_auto_scaling_groups", "AutoScalingGroups")

    def collect_policies(self) -> List[Dict[str, Any]]:
        return paginate(self.asg, "describe_policies", "ScalingPolicies")

    def collect_launch_templates(
        self, asgs: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """Return {template_name: {"data": <version data>, "used_by": [asg names]}}.

        We resolve the exact version each ASG references rather than assuming
        $Latest — auditing a version nothing actually runs is worse than useless
        because it produces confident, wrong findings.
        """
        wanted: Dict[str, List[str]] = {}

        for asg in asgs:
            spec = asg.get("LaunchTemplate") or {}
            if not spec:
                mixed = asg.get("MixedInstancesPolicy", {}) or {}
                spec = (mixed.get("LaunchTemplate", {}) or {}).get(
                    "LaunchTemplateSpecification", {}
                ) or {}

            lt_id = spec.get("LaunchTemplateId")
            lt_name = spec.get("LaunchTemplateName")
            version = spec.get("Version", "$Default")
            key = lt_id or lt_name
            if not key:
                continue

            entry_key = f"{key}|{version}"
            wanted.setdefault(entry_key, []).append(
                asg.get("AutoScalingGroupName", "unknown")
            )

        resolved: Dict[str, Dict[str, Any]] = {}

        for entry_key, used_by in wanted.items():
            key, version = entry_key.split("|", 1)
            kwargs: Dict[str, Any] = {"Versions": [version]}
            if key.startswith("lt-"):
                kwargs["LaunchTemplateId"] = key
            else:
                kwargs["LaunchTemplateName"] = key

            try:
                versions = paginate(
                    self.ec2,
                    "describe_launch_template_versions",
                    "LaunchTemplateVersions",
                    **kwargs,
                )
            except ClientError as exc:
                self.log(f"  ! Could not read launch template {key}: {exc}")
                continue

            for ver in versions:
                display = (
                    f"{ver.get('LaunchTemplateName', key)}"
                    f":v{ver.get('VersionNumber', '?')}"
                )
                resolved[display] = {
                    "data": ver.get("LaunchTemplateData", {}) or {},
                    "used_by": used_by,
                }

        return resolved

    def collect_load_balancers(self) -> List[Dict[str, Any]]:
        return paginate(self.elbv2, "describe_load_balancers", "LoadBalancers")

    def collect_target_groups(self) -> List[Dict[str, Any]]:
        return paginate(self.elbv2, "describe_target_groups", "TargetGroups")

    # -- orchestration ------------------------------------------------------

    def run(self) -> List[Finding]:
        self.log(f"Auditing region {self.region} ...")

        # --- Auto Scaling Groups -------------------------------------------
        asgs = self.collect_asgs()
        policies = self.collect_policies()
        self.stats["asgs"] = len(asgs)
        self.log(f"  Auto Scaling Groups : {len(asgs)}")

        for asg in asgs:
            self.findings += check_capacity_sanity(asg, self.region)
            self.findings += check_single_az(asg, self.region)
            self.findings += check_health_check_type(asg, self.region)
            self.findings += check_grace_period(asg, self.region)
            self.findings += check_scaling_policies(asg, policies, self.region)
            self.findings += check_termination_policies(asg, self.region)
            self.findings += check_instance_az_spread(asg, self.region)

        # --- Launch templates ----------------------------------------------
        templates = self.collect_launch_templates(asgs)
        self.stats["launch_templates"] = len(templates)
        self.log(f"  Launch templates    : {len(templates)}")

        for lt_name, payload in templates.items():
            data = payload["data"]
            used_by = payload["used_by"]
            self.findings += check_launch_template_metadata(
                lt_name, data, used_by, self.region
            )
            self.findings += check_launch_template_encryption(
                lt_name, data, used_by, self.region
            )

        # --- Load balancers -------------------------------------------------
        lbs = self.collect_load_balancers()
        self.stats["load_balancers"] = len(lbs)
        self.log(f"  Load balancers      : {len(lbs)}")

        for lb in lbs:
            arn = lb.get("LoadBalancerArn")
            if not arn:
                continue

            if lb.get("Type") == "application":
                listeners = paginate(
                    self.elbv2, "describe_listeners", "Listeners", LoadBalancerArn=arn
                )
                self.findings += check_alb_listeners(lb, listeners, self.region)

            if lb.get("Type") == "network":
                try:
                    attrs = self.elbv2.describe_load_balancer_attributes(
                        LoadBalancerArn=arn
                    ).get("Attributes", [])
                except ClientError as exc:
                    self.log(f"  ! Could not read attributes for {arn}: {exc}")
                    attrs = []
                self.findings += check_nlb_cross_zone(lb, attrs, self.region)

        # --- Target groups ---------------------------------------------------
        tgs = self.collect_target_groups()
        self.stats["target_groups"] = len(tgs)
        self.log(f"  Target groups       : {len(tgs)}")

        for tg in tgs:
            arn = tg.get("TargetGroupArn")
            if not arn:
                continue
            try:
                health = self.elbv2.describe_target_health(TargetGroupArn=arn).get(
                    "TargetHealthDescriptions", []
                )
            except ClientError as exc:
                self.log(f"  ! Could not read target health for {arn}: {exc}")
                continue
            self.findings += check_target_health(tg, health, self.region)

        self.log("")
        return self.findings


###############################################################################
# Scoring
###############################################################################


def calculate_score(findings: List[Finding]) -> int:
    """100 minus the sum of severity weights, floored at 0.

    Floored, not negative: once you are at zero there is no useful distinction
    between 'very broken' and 'even more broken'. Fix something and re-run.
    """
    score = 100 - sum(f.weight for f in findings)
    return max(0, score)


def score_grade(score: int) -> str:
    if score >= 90:
        return "A — production-ready"
    if score >= 75:
        return "B — solid, minor gaps"
    if score >= 60:
        return "C — real resilience gaps"
    if score >= 40:
        return "D — will not survive an AZ event"
    return "F — not highly available in any meaningful sense"


###############################################################################
# Output formats
###############################################################################


def filter_by_severity(findings: List[Finding], min_severity: str) -> List[Finding]:
    cutoff = SEVERITY_ORDER.index(min_severity)
    return [f for f in findings if SEVERITY_ORDER.index(f.severity) <= cutoff]


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "\u2026"


def render_table(
    findings: List[Finding], stats: Dict[str, int], score: int, use_colour: bool
) -> str:
    out = io.StringIO()
    w = out.write

    bar = "=" * 100
    w(f"\n{bar}\n")
    w(colour("  HIGH AVAILABILITY & RESILIENCE AUDIT", "BOLD", use_colour))
    w("\n  CareerByteCode · Day 03 · Compute Architecture & Intelligent Scaling\n")
    w(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
    w(f"{bar}\n\n")

    w("  Scanned: ")
    w(
        f"{stats.get('asgs', 0)} ASG(s) · "
        f"{stats.get('launch_templates', 0)} launch template(s) · "
        f"{stats.get('load_balancers', 0)} load balancer(s) · "
        f"{stats.get('target_groups', 0)} target group(s)\n\n"
    )

    if not findings:
        w(colour("  No findings. Nothing to fix at this severity level.\n\n", "GREEN", use_colour))
    else:
        counts = {sev: 0 for sev in SEVERITY_ORDER}
        for f in findings:
            counts[f.severity] += 1

        w("  " + "-" * 96 + "\n")
        w(f"  {'SEVERITY':<10} {'CHECK':<9} {'RESOURCE':<34} {'FINDING':<40}\n")
        w("  " + "-" * 96 + "\n")

        ordered = sorted(
            findings,
            key=lambda f: (SEVERITY_ORDER.index(f.severity), f.check_id, f.resource_id),
        )

        for f in ordered:
            sev = colour(f"{f.severity:<10}", f.severity, use_colour)
            w(
                f"  {sev} {f.check_id:<9} "
                f"{_truncate(f.resource_id, 33):<34} "
                f"{_truncate(f.title, 40):<40}\n"
            )

        w("  " + "-" * 96 + "\n\n")

        w(colour("  DETAIL\n\n", "BOLD", use_colour))
        for i, f in enumerate(ordered, 1):
            w(
                f"  {i:>2}. [{colour(f.severity, f.severity, use_colour)}] "
                f"{f.check_id} — {f.title}\n"
            )
            w(f"      Resource   : {f.resource_type} / {f.resource_id}\n")
            for line in _wrap(f.detail, 88):
                w(f"      {line}\n")
            w(f"      {colour('Fix', 'GREEN', use_colour)}        : ")
            fix_lines = _wrap(f.remediation, 84)
            w(f"{fix_lines[0] if fix_lines else ''}\n")
            for line in fix_lines[1:]:
                w(f"                   {line}\n")
            w("\n")

        w("  " + "-" * 96 + "\n")
        summary = "  ".join(
            f"{colour(sev, sev, use_colour)}: {counts[sev]}" for sev in SEVERITY_ORDER
        )
        w(f"  {summary}\n")

    w("  " + "-" * 96 + "\n")
    grade = score_grade(score)
    score_key = "GREEN" if score >= 75 else ("MEDIUM" if score >= 50 else "CRITICAL")
    w(
        f"  RESILIENCE SCORE: "
        f"{colour(str(score) + '/100', score_key, use_colour)}   {grade}\n"
    )
    w(f"{bar}\n\n")

    return out.getvalue()


def _wrap(text: str, width: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            if current:
                lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def render_json(findings: List[Finding], stats: Dict[str, int], score: int) -> str:
    counts = {sev: 0 for sev in SEVERITY_ORDER}
    for f in findings:
        counts[f.severity] += 1

    payload = {
        "audit": "ha_audit",
        "day": "03",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "resilience_score": score,
        "grade": score_grade(score),
        "scanned": stats,
        "summary": counts,
        "finding_count": len(findings),
        "findings": [f.to_dict() for f in findings],
    }
    return json.dumps(payload, indent=2, default=str)


def render_csv(findings: List[Finding]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        [
            "check_id",
            "severity",
            "weight",
            "resource_type",
            "resource_id",
            "region",
            "title",
            "detail",
            "remediation",
            "evidence",
        ]
    )
    for f in sorted(
        findings, key=lambda x: (SEVERITY_ORDER.index(x.severity), x.check_id)
    ):
        writer.writerow(
            [
                f.check_id,
                f.severity,
                f.weight,
                f.resource_type,
                f.resource_id,
                f.region,
                f.title,
                f.detail,
                f.remediation,
                json.dumps(f.evidence, default=str),
            ]
        )
    return out.getvalue()


###############################################################################
# CLI
###############################################################################


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ha_audit.py",
        description=(
            "Audit AWS Auto Scaling Groups, launch templates and load balancers "
            "for high-availability and resilience misconfigurations."
        ),
        epilog=(
            "Examples:\n"
            "  ha_audit.py --profile bootcamp --region us-east-1\n"
            "  ha_audit.py --format json --quiet > findings.json\n"
            "  ha_audit.py --min-severity HIGH --format csv\n"
            "  ha_audit.py --fail-on HIGH   # exit 1 if any HIGH or worse\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--profile",
        default=None,
        help="AWS CLI named profile. Day 01 created 'bootcamp'.",
    )
    parser.add_argument(
        "--region",
        default="us-east-1",
        help="AWS region to audit (default: us-east-1).",
    )
    parser.add_argument(
        "--min-severity",
        choices=SEVERITY_ORDER,
        default="INFO",
        help="Only report findings at this severity or worse (default: INFO).",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json", "csv"],
        default="table",
        help="Output format (default: table).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output on stderr. Use when piping stdout.",
    )
    parser.add_argument(
        "--fail-on",
        choices=SEVERITY_ORDER,
        default=None,
        help=(
            "Exit with code 1 if any finding is at this severity or worse. "
            "Use in CI to block a merge."
        ),
    )
    parser.add_argument(
        "--no-colour",
        "--no-color",
        dest="no_colour",
        action="store_true",
        help="Disable ANSI colour even on a TTY.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    use_colour = sys.stdout.isatty() and not args.no_colour and args.format == "table"

    auditor = HAAuditor(profile=args.profile, region=args.region, quiet=args.quiet)

    try:
        all_findings = auditor.run()
    except NoCredentialsError:
        print(
            "No AWS credentials found. Try --profile bootcamp, or run "
            "`aws configure --profile bootcamp`.",
            file=sys.stderr,
        )
        return 2
    except ClientError as exc:
        print(f"AWS API error: {exc}", file=sys.stderr)
        return 2

    # The score always reflects EVERY finding, regardless of --min-severity.
    # Filtering the display should never flatter the score; otherwise people
    # "improve" their posture by passing --min-severity CRITICAL.
    score = calculate_score(all_findings)
    shown = filter_by_severity(all_findings, args.min_severity)

    if args.format == "json":
        print(render_json(shown, auditor.stats, score))
    elif args.format == "csv":
        print(render_csv(shown), end="")
    else:
        print(render_table(shown, auditor.stats, score, use_colour))

    if args.fail_on:
        cutoff = SEVERITY_ORDER.index(args.fail_on)
        if any(SEVERITY_ORDER.index(f.severity) <= cutoff for f in all_findings):
            if not args.quiet:
                print(
                    f"Failing: at least one finding at severity {args.fail_on} "
                    f"or worse.",
                    file=sys.stderr,
                )
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
