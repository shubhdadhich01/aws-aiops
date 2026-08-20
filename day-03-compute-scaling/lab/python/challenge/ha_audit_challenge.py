#!/usr/bin/env python3
"""
ha_audit_challenge.py — build the Day 03 resilience auditor yourself.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

===============================================================================
  HOW THIS WORKS
===============================================================================

All the boring parts are done for you: the CLI, the Finding dataclass, the
paginator helpers, the scoring, and all three output renderers. What is missing
is the part that matters — the checks.

There are 9 TODOs. Each one has:
    * a time estimate
    * the exact AWS API fields you need
    * a hint if you are stuck
    * a CHECKPOINT so you know whether it worked before moving on

Total: roughly 75–95 minutes if you have not done this before.

Run it after each TODO:

    python3 ha_audit_challenge.py --profile bootcamp --region us-east-1

You are done when your output matches the reference implementation at
../ha_audit.py. Do not read that file first. You will learn nothing, and the
checks are the whole exercise.

===============================================================================
  THE CHECKS YOU ARE IMPLEMENTING
===============================================================================

    TODO 1   ASG-001  Capacity sanity          ~10 min
    TODO 2   ASG-002  Single-AZ ASG            ~8 min
    TODO 3   ASG-003  EC2 health check type    ~10 min
    TODO 4   ASG-004  Grace period             ~10 min
    TODO 5   ASG-005  No scaling policies      ~8 min
    TODO 6   ASG-011  IMDSv1 allowed           ~10 min
    TODO 7   ASG-012  Unencrypted root EBS     ~10 min
    TODO 8   ASG-008/009  ALB listeners        ~12 min
    TODO 9   ASG-006/007  Target health        ~12 min

    STRETCH  ASG-010  NLB cross-zone
    STRETCH  ASG-013  Termination policy diversity
    STRETCH  ASG-014  Instance AZ spread

===============================================================================
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
    print("boto3 is not installed. Run:  pip install -r ../requirements.txt", file=sys.stderr)
    sys.exit(2)


###############################################################################
# GIVEN — severity model. Do not change these numbers; the scoring depends on
# them and so does every comparison against the reference implementation.
###############################################################################

SEVERITY_WEIGHTS: Dict[str, int] = {
    "CRITICAL": 25,
    "HIGH": 10,
    "MEDIUM": 4,
    "LOW": 1,
    "INFO": 0,
}

SEVERITY_ORDER: List[str] = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]

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
    if not enabled or key not in _COLOURS:
        return text
    return f"{_COLOURS[key]}{text}{_COLOURS['RESET']}"


###############################################################################
# GIVEN — the Finding dataclass.
#
# Read this before you start. Every check you write returns a list of these.
###############################################################################


@dataclass
class Finding:
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
# GIVEN — paginator helpers.
#
# Use paginate() for every AWS list call. Calling describe_* directly and
# trusting the first page is the classic audit-tool bug: it works in your test
# account with 3 resources and silently misses 90% of production.
###############################################################################


def paginate(client: Any, operation: str, result_key: str, **kwargs: Any) -> List[Dict[str, Any]]:
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
            print(f"  ! Access denied calling {operation}. Skipping.", file=sys.stderr)
            return []
        raise
    return items


def chunked(items: List[Any], size: int) -> Iterable[List[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


###############################################################################
#
#   YOUR WORK STARTS HERE
#
###############################################################################


# =============================================================================
# TODO 1 — ASG-001: capacity sanity                              (~10 minutes)
# =============================================================================
#
# An Auto Scaling Group dict from describe_auto_scaling_groups contains:
#     asg["MinSize"]           int
#     asg["MaxSize"]           int
#     asg["DesiredCapacity"]   int
#     asg["AutoScalingGroupName"]  str
#
# Report a finding when ANY of these are true:
#
#   (a) MinSize < 2
#       Severity: HIGH
#       Why: one instance is one AZ. Any replacement leaves zero capacity.
#
#   (b) MaxSize <= MinSize AND MaxSize == DesiredCapacity
#       Severity: MEDIUM
#       Why: scaling policies can never add an instance. Fixed fleet.
#
#   (c) DesiredCapacity < MinSize OR DesiredCapacity > MaxSize
#       Severity: MEDIUM
#       Why: incoherent config; AWS will force it back into range.
#
# One ASG can trip more than one of these. Return a list, not a single Finding.
#
# HINT: use asg.get("MinSize", 0) rather than asg["MinSize"] — a partially
#       populated response should not crash your auditor.
#
# CHECKPOINT: with create_insecure_examples = true, cbc-day03-broken-asg
#             should produce exactly 2 ASG-001 findings (a and b), and
#             cbc-day03-asg should produce 0.
# =============================================================================


def check_capacity_sanity(asg: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 1: implement the three conditions above.

    return findings


# =============================================================================
# TODO 2 — ASG-002: single-AZ Auto Scaling Group                  (~8 minutes)
# =============================================================================
#
# Fields:
#     asg["AvailabilityZones"]   list[str], e.g. ["us-east-1a", "us-east-1b"]
#     asg["VPCZoneIdentifier"]   comma-separated subnet IDs (useful evidence)
#
# Report HIGH when the group spans fewer than 2 distinct AZs.
#
# HINT: deduplicate with set() before counting. AWS will not normally return
#       duplicates, but defensive code costs nothing.
#
# CHECKPOINT: cbc-day03-broken-asg -> 1 finding. cbc-day03-asg -> 0.
# =============================================================================


def check_single_az(asg: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 2

    return findings


# =============================================================================
# TODO 3 — ASG-003: health check type is EC2, not ELB            (~10 minutes)
# =============================================================================
#
# Fields:
#     asg["HealthCheckType"]     "EC2" or "ELB"
#     asg["TargetGroupARNs"]     list[str]  (empty if not behind an ALB/NLB)
#     asg["LoadBalancerNames"]   list[str]  (classic ELB, usually empty)
#
# Logic:
#     HealthCheckType == "ELB"                        -> no finding
#     "EC2" AND attached to a load balancer           -> HIGH
#     "EC2" AND not attached to anything              -> MEDIUM
#
# The severity split matters. A group behind a load balancer with EC2 health
# checks is actively dangerous: the LB knows the app is broken and the ASG
# refuses to act on it. A standalone group with EC2 checks is merely limited.
#
# HINT: bool(asg.get("TargetGroupARNs") or asg.get("LoadBalancerNames"))
#
# CHECKPOINT: cbc-day03-broken-asg -> 1 finding. Note it will be MEDIUM, not
#             HIGH, because the broken ASG has no target group attached — read
#             the Terraform and confirm you understand why before moving on.
# =============================================================================


def check_health_check_type(asg: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 3

    return findings


# =============================================================================
# TODO 4 — ASG-004: health check grace period                    (~10 minutes)
# =============================================================================
#
# Field:
#     asg["HealthCheckGracePeriod"]   int seconds, may be absent
#
# Three bands:
#     None or 0     -> MEDIUM  "no grace period set"
#     < 60          -> MEDIUM  "grace period is only Ns"
#     > 900         -> LOW     "grace period is Ns — very long"
#     otherwise     -> no finding
#
# Explain the consequence in the detail text. Too short means an infinite
# launch/terminate loop that bills continuously and never converges — people
# have burned four figures overnight on this. Too long means a broken instance
# serves errors for 15 minutes.
#
# HINT: `grace = asg.get("HealthCheckGracePeriod")` then check `is None` FIRST,
#       before any numeric comparison, or you will get a TypeError.
#
# CHECKPOINT: cbc-day03-broken-asg has 30s -> 1 MEDIUM finding.
#             cbc-day03-asg has 300s -> 0 findings.
# =============================================================================


def check_grace_period(asg: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 4

    return findings


# =============================================================================
# TODO 5 — ASG-005: no scaling policies attached                  (~8 minutes)
# =============================================================================
#
# You are given the full list of scaling policies in the account. Each policy
# has policy["AutoScalingGroupName"].
#
# Filter to the policies belonging to THIS group. If there are none:
#
#     MinSize == MaxSize  -> INFO    (deliberately fixed-size; a design choice)
#     otherwise           -> MEDIUM  (elastic capacity range, no way to use it)
#
# That severity split is the difference between a useful tool and one people
# ignore. Flagging a deliberately fixed group as MEDIUM trains people to
# disregard your output.
#
# HINT: [p for p in policies if p.get("AutoScalingGroupName") == name]
#
# CHECKPOINT: cbc-day03-broken-asg (min 1 == max 1) -> 1 INFO finding.
#             cbc-day03-asg has 3 policies -> 0 findings.
# =============================================================================


def check_scaling_policies(
    asg: Dict[str, Any], policies: List[Dict[str, Any]], region: str = ""
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 5

    return findings


# =============================================================================
# TODO 6 — ASG-011: IMDSv1 allowed on a launch template          (~10 minutes)
# =============================================================================
#
# lt_data is the LaunchTemplateData from describe_launch_template_versions.
#
#     lt_data["MetadataOptions"]["HttpTokens"]                "required"|"optional"
#     lt_data["MetadataOptions"]["HttpEndpoint"]              "enabled"|"disabled"
#     lt_data["MetadataOptions"]["HttpPutResponseHopLimit"]   int
#
# No finding when:
#     HttpTokens == "required"      (IMDSv2 enforced — correct)
#     HttpEndpoint == "disabled"    (IMDS off entirely — also fine)
#
# Otherwise: HIGH.
#
# Note the defaults. If MetadataOptions is absent entirely, or HttpTokens is
# absent, the effective behaviour is "optional" — IMDSv1 works. Default your
# .get() calls accordingly, or you will silently pass insecure templates.
#
# HINT: meta = lt_data.get("MetadataOptions", {}) or {}
#       tokens = meta.get("HttpTokens", "optional")
#       The `or {}` matters: AWS sometimes returns an explicit None.
#
# Write a real detail string. "IMDSv1 is enabled" teaches nothing; explain that
# an SSRF bug becomes credential theft and name the Capital One breach.
#
# CHECKPOINT: cbc-day03-broken:v1 -> 1 HIGH. cbc-day03-app:v1 -> 0.
# =============================================================================


def check_launch_template_metadata(
    lt_name: str, lt_data: Dict[str, Any], used_by: List[str], region: str = ""
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 6

    return findings


# =============================================================================
# TODO 7 — ASG-012: unencrypted EBS volumes                      (~10 minutes)
# =============================================================================
#
#     lt_data["BlockDeviceMappings"]  list of:
#         { "DeviceName": "/dev/xvda",
#           "Ebs": { "Encrypted": bool, "VolumeType": str, "KmsKeyId": str } }
#
# For each mapping that has an "Ebs" key:
#
#     Encrypted is True     -> no finding
#     Encrypted is False    -> MEDIUM  ("explicitly disabled")
#     Encrypted is absent   -> LOW     ("not specified" — inherits from the AMI
#                                       or the account default, which is not a
#                                       guarantee)
#
# Use resource_id = f"{lt_name}:{device}" so two bad volumes on one template
# produce two distinguishable findings.
#
# HINT: `if encrypted is True` — not `if encrypted`. You must distinguish
#       False from None, and truthiness collapses them.
#
# CHECKPOINT: cbc-day03-broken:v1 -> 1 MEDIUM (root volume, Encrypted=False).
# =============================================================================


def check_launch_template_encryption(
    lt_name: str, lt_data: Dict[str, Any], used_by: List[str], region: str = ""
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 7

    return findings


# =============================================================================
# TODO 8 — ASG-008 / ASG-009: ALB listener hygiene               (~12 minutes)
# =============================================================================
#
#     lb["Type"]                 "application" | "network" | "gateway"
#     lb["Scheme"]               "internet-facing" | "internal"
#     lb["LoadBalancerName"]     str
#
#     listener["Protocol"]       "HTTP" | "HTTPS" | "TCP" | ...
#     listener["Port"]           int
#     listener["DefaultActions"] list of:
#         { "Type": "forward" | "redirect" | "fixed-response",
#           "RedirectConfig": { "Protocol": "HTTPS", "Port": "443", ... } }
#
# Return early if lb["Type"] != "application" — NLBs have no HTTP semantics.
#
# ASG-008: no listener with Protocol == "HTTPS".
#     internet-facing -> HIGH
#     internal        -> MEDIUM
#
# ASG-009: for EACH HTTP listener whose default actions do NOT include a
#     redirect to HTTPS -> MEDIUM. Use resource_id f"{lb_name}:{port}".
#
# The ASG-009 condition is fiddly. A listener is compliant only if one of its
# default actions has Type == "redirect" AND RedirectConfig.Protocol == "HTTPS".
# A redirect to HTTP is not a fix. A fixed-response is not a fix.
#
# HINT:
#     redirects = any(
#         a.get("Type") == "redirect"
#         and (a.get("RedirectConfig", {}) or {}).get("Protocol") == "HTTPS"
#         for a in listener.get("DefaultActions", []) or []
#     )
#
# CHECKPOINT: with the default acm_certificate_arn = "", cbc-day03-alb should
#             produce exactly 2 findings: one ASG-008 (HIGH) and one ASG-009
#             (MEDIUM) on port 80.
# =============================================================================


def check_alb_listeners(
    lb: Dict[str, Any], listeners: List[Dict[str, Any]], region: str = ""
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 8

    return findings


# =============================================================================
# TODO 9 — ASG-006 / ASG-007: target health                      (~12 minutes)
# =============================================================================
#
#     tg["TargetGroupName"]   str
#
#     health is a list from describe_target_health:
#         { "Target": { "Id": "i-abc", "Port": 80 },
#           "TargetHealth": { "State": "healthy"|"unhealthy"|"initial"|
#                                      "draining"|"unused", "Reason": ... } }
#
# Three outcomes:
#
#   (a) health is empty -> ASG-006, LOW, "no registered targets"
#       Either orphaned config or an ASG that isn't registering. Both worth
#       knowing about, neither is an outage.
#
#   (b) zero targets in state "healthy" -> ASG-007, CRITICAL
#       This is the only CRITICAL in the whole tool. The service is down right
#       now and the load balancer is returning 503 to every request. Nothing
#       else in this audit outranks it.
#
#   (c) some healthy, some not -> ASG-006
#       HIGH if unhealthy >= healthy, otherwise MEDIUM.
#
#   Bonus: exactly 1 healthy target -> an extra ASG-006 MEDIUM. One healthy
#   target means the next failure is an outage. There is no redundancy.
#
# Build a state histogram: {"healthy": 2, "unhealthy": 1}. It makes the finding
# self-explanatory and it is the first thing anyone asks for.
#
# HINT: treat "unhealthy", "unused" and "draining" as not-healthy.
#       "initial" is a target still coming up — usually transient, so counting
#       it as unhealthy produces false alarms during a scale-out.
#
# CHECKPOINT: run this right after `terraform apply` and you may legitimately
#             see ASG-007 CRITICAL while targets are still in "initial". Wait
#             two minutes and re-run. That is not a bug in your code — it is a
#             real property of point-in-time audits, and worth internalising.
# =============================================================================


def check_target_health(
    tg: Dict[str, Any], health: List[Dict[str, Any]], region: str = ""
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 9

    return findings


# =============================================================================
# STRETCH GOALS — attempt after the nine above all pass.
# =============================================================================
#
# ASG-010  NLB cross-zone load balancing disabled.
#          You get lb and the output of describe_load_balancer_attributes.
#          Look for key "load_balancing.cross_zone.enabled". Note the value is
#          the STRING "true"/"false", not a bool. MEDIUM when false.
#
# ASG-013  Termination policy is bare ["Default"]. LOW.
#          asg["TerminationPolicies"]
#
# ASG-014  ASG configured for multiple AZs but all InService instances sit in
#          one. MEDIUM. asg["Instances"] each have "AvailabilityZone" and
#          "LifecycleState". Filter to LifecycleState == "InService" first.
# =============================================================================


def check_nlb_cross_zone(
    lb: Dict[str, Any], attributes: List[Dict[str, Any]], region: str = ""
) -> List[Finding]:
    return []  # STRETCH


def check_termination_policies(asg: Dict[str, Any], region: str = "") -> List[Finding]:
    return []  # STRETCH


def check_instance_az_spread(asg: Dict[str, Any], region: str = "") -> List[Finding]:
    return []  # STRETCH


###############################################################################
#
#   YOUR WORK ENDS HERE — everything below is given.
#
###############################################################################


class HAAuditor:
    """GIVEN — collects AWS state and calls your check functions."""

    def __init__(
        self, profile: Optional[str] = None, region: str = "us-east-1", quiet: bool = False
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

    def log(self, message: str) -> None:
        if not self.quiet:
            print(message, file=sys.stderr)

    def collect_launch_templates(self, asgs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        wanted: Dict[str, List[str]] = {}
        for asg in asgs:
            spec = asg.get("LaunchTemplate") or {}
            if not spec:
                mixed = asg.get("MixedInstancesPolicy", {}) or {}
                spec = (mixed.get("LaunchTemplate", {}) or {}).get(
                    "LaunchTemplateSpecification", {}
                ) or {}
            key = spec.get("LaunchTemplateId") or spec.get("LaunchTemplateName")
            if not key:
                continue
            version = spec.get("Version", "$Default")
            wanted.setdefault(f"{key}|{version}", []).append(
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
                    self.ec2, "describe_launch_template_versions", "LaunchTemplateVersions", **kwargs
                )
            except ClientError as exc:
                self.log(f"  ! Could not read launch template {key}: {exc}")
                continue
            for ver in versions:
                display = f"{ver.get('LaunchTemplateName', key)}:v{ver.get('VersionNumber', '?')}"
                resolved[display] = {
                    "data": ver.get("LaunchTemplateData", {}) or {},
                    "used_by": used_by,
                }
        return resolved

    def run(self) -> List[Finding]:
        self.log(f"Auditing region {self.region} ...")

        asgs = paginate(self.asg, "describe_auto_scaling_groups", "AutoScalingGroups")
        policies = paginate(self.asg, "describe_policies", "ScalingPolicies")
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

        templates = self.collect_launch_templates(asgs)
        self.stats["launch_templates"] = len(templates)
        self.log(f"  Launch templates    : {len(templates)}")

        for lt_name, payload in templates.items():
            self.findings += check_launch_template_metadata(
                lt_name, payload["data"], payload["used_by"], self.region
            )
            self.findings += check_launch_template_encryption(
                lt_name, payload["data"], payload["used_by"], self.region
            )

        lbs = paginate(self.elbv2, "describe_load_balancers", "LoadBalancers")
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
                except ClientError:
                    attrs = []
                self.findings += check_nlb_cross_zone(lb, attrs, self.region)

        tgs = paginate(self.elbv2, "describe_target_groups", "TargetGroups")
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
            except ClientError:
                continue
            self.findings += check_target_health(tg, health, self.region)

        self.log("")
        return self.findings


def calculate_score(findings: List[Finding]) -> int:
    return max(0, 100 - sum(f.weight for f in findings))


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


def filter_by_severity(findings: List[Finding], min_severity: str) -> List[Finding]:
    cutoff = SEVERITY_ORDER.index(min_severity)
    return [f for f in findings if SEVERITY_ORDER.index(f.severity) <= cutoff]


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "\u2026"


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


def render_table(
    findings: List[Finding], stats: Dict[str, int], score: int, use_colour: bool
) -> str:
    out = io.StringIO()
    w = out.write
    bar = "=" * 100
    w(f"\n{bar}\n")
    w(colour("  HIGH AVAILABILITY & RESILIENCE AUDIT  (challenge build)", "BOLD", use_colour))
    w("\n  CareerByteCode · Day 03 · Compute Architecture & Intelligent Scaling\n")
    w(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
    w(f"{bar}\n\n")
    w("  Scanned: ")
    w(
        f"{stats.get('asgs', 0)} ASG(s) · {stats.get('launch_templates', 0)} launch template(s) · "
        f"{stats.get('load_balancers', 0)} load balancer(s) · {stats.get('target_groups', 0)} target group(s)\n\n"
    )

    if not findings:
        w("  No findings.\n")
        w(colour("  If you have not implemented the TODOs yet, that is why.\n\n", "DIM", use_colour))
    else:
        counts = {sev: 0 for sev in SEVERITY_ORDER}
        for f in findings:
            counts[f.severity] += 1
        w("  " + "-" * 96 + "\n")
        w(f"  {'SEVERITY':<10} {'CHECK':<9} {'RESOURCE':<34} {'FINDING':<40}\n")
        w("  " + "-" * 96 + "\n")
        ordered = sorted(
            findings, key=lambda f: (SEVERITY_ORDER.index(f.severity), f.check_id, f.resource_id)
        )
        for f in ordered:
            sev = colour(f"{f.severity:<10}", f.severity, use_colour)
            w(f"  {sev} {f.check_id:<9} {_truncate(f.resource_id, 33):<34} {_truncate(f.title, 40):<40}\n")
        w("  " + "-" * 96 + "\n\n")
        w(colour("  DETAIL\n\n", "BOLD", use_colour))
        for i, f in enumerate(ordered, 1):
            w(f"  {i:>2}. [{colour(f.severity, f.severity, use_colour)}] {f.check_id} — {f.title}\n")
            w(f"      Resource   : {f.resource_type} / {f.resource_id}\n")
            for line in _wrap(f.detail, 88):
                w(f"      {line}\n")
            fix_lines = _wrap(f.remediation, 84)
            w(f"      {colour('Fix', 'GREEN', use_colour)}        : {fix_lines[0] if fix_lines else ''}\n")
            for line in fix_lines[1:]:
                w(f"                   {line}\n")
            w("\n")
        w("  " + "-" * 96 + "\n")
        w("  " + "  ".join(f"{colour(s, s, use_colour)}: {counts[s]}" for s in SEVERITY_ORDER) + "\n")

    w("  " + "-" * 96 + "\n")
    score_key = "GREEN" if score >= 75 else ("MEDIUM" if score >= 50 else "CRITICAL")
    w(f"  RESILIENCE SCORE: {colour(str(score) + '/100', score_key, use_colour)}   {score_grade(score)}\n")
    w(f"{bar}\n\n")
    return out.getvalue()


def render_json(findings: List[Finding], stats: Dict[str, int], score: int) -> str:
    counts = {sev: 0 for sev in SEVERITY_ORDER}
    for f in findings:
        counts[f.severity] += 1
    return json.dumps(
        {
            "audit": "ha_audit_challenge",
            "day": "03",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "resilience_score": score,
            "grade": score_grade(score),
            "scanned": stats,
            "summary": counts,
            "finding_count": len(findings),
            "findings": [f.to_dict() for f in findings],
        },
        indent=2,
        default=str,
    )


def render_csv(findings: List[Finding]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        [
            "check_id", "severity", "weight", "resource_type", "resource_id",
            "region", "title", "detail", "remediation", "evidence",
        ]
    )
    for f in sorted(findings, key=lambda x: (SEVERITY_ORDER.index(x.severity), x.check_id)):
        writer.writerow(
            [
                f.check_id, f.severity, f.weight, f.resource_type, f.resource_id,
                f.region, f.title, f.detail, f.remediation,
                json.dumps(f.evidence, default=str),
            ]
        )
    return out.getvalue()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ha_audit_challenge.py",
        description="Challenge build of the Day 03 HA auditor. Implement the TODOs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--profile", default=None, help="AWS CLI named profile.")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1).")
    parser.add_argument(
        "--min-severity", choices=SEVERITY_ORDER, default="INFO",
        help="Only report findings at this severity or worse.",
    )
    parser.add_argument(
        "--format", choices=["table", "json", "csv"], default="table", help="Output format."
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output on stderr.")
    parser.add_argument(
        "--fail-on", choices=SEVERITY_ORDER, default=None,
        help="Exit 1 if any finding is at this severity or worse.",
    )
    parser.add_argument(
        "--no-colour", "--no-color", dest="no_colour", action="store_true",
        help="Disable ANSI colour.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    use_colour = sys.stdout.isatty() and not args.no_colour and args.format == "table"

    auditor = HAAuditor(profile=args.profile, region=args.region, quiet=args.quiet)
    try:
        all_findings = auditor.run()
    except NoCredentialsError:
        print("No AWS credentials found. Try --profile bootcamp.", file=sys.stderr)
        return 2
    except ClientError as exc:
        print(f"AWS API error: {exc}", file=sys.stderr)
        return 2

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
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
