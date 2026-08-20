#!/usr/bin/env python3
"""
VPC Security Assessment Tool  ·  CareerByteCode AWS Cloud Architecture & AIOps Bootcamp — Day 02

Inspects every VPC in a region and reports network exposure: security groups open to the
internet, overly broad or shadowed NACL rules, missing flow logs, orphaned security groups,
subnets that auto-assign public IPs, and "private" subnets that quietly route to an
Internet Gateway.

Everything this tool does is READ-ONLY. It cannot modify your account.

Usage:
    python3 vpc_assess.py --profile bootcamp
    python3 vpc_assess.py --profile bootcamp --vpc-id vpc-0abc123
    python3 vpc_assess.py --profile bootcamp --min-severity HIGH
    python3 vpc_assess.py --profile bootcamp --format json --quiet
    python3 vpc_assess.py --profile bootcamp --fail-on CRITICAL   # for CI pipelines

Required IAM permissions:
    ec2:Describe*  — the AWS-managed `SecurityAudit` or `ReadOnlyAccess` policy covers it.
    The Day 01 Terraform created a role with exactly this.
"""

from __future__ import annotations

import argparse
import csv
import ipaddress
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError, ProfileNotFound
except ImportError:
    sys.exit("boto3 is not installed. Run:  pip install -r requirements.txt")


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]

SEVERITY_ICON = {
    "CRITICAL": "🔴",
    "HIGH": "🟠",
    "MEDIUM": "🟡",
    "LOW": "🔵",
    "INFO": "⚪",
}

# Points deducted from a starting score of 100, per finding.
SEVERITY_WEIGHT = {"CRITICAL": 25, "HIGH": 10, "MEDIUM": 4, "LOW": 1, "INFO": 0}

# "Open to the world" in both address families.
WORLD_IPV4 = "0.0.0.0/0"
WORLD_IPV6 = "::/0"

# Remote administration. These two are the reason most cryptomining incidents happen.
ADMIN_PORTS = {
    22: "SSH",
    3389: "RDP",
}

# Ports that should never face the internet directly. Port → human name.
SENSITIVE_PORTS = {
    20: "FTP-data",
    21: "FTP",
    23: "Telnet",
    25: "SMTP",
    135: "MSRPC",
    137: "NetBIOS",
    138: "NetBIOS",
    139: "NetBIOS/SMB",
    445: "SMB",
    512: "rexec",
    513: "rlogin",
    514: "rsh/syslog",
    1433: "MSSQL",
    1521: "Oracle",
    2049: "NFS",
    2375: "Docker API (unencrypted)",
    2376: "Docker API (TLS)",
    2379: "etcd",
    3000: "Grafana / dev server",
    3306: "MySQL/MariaDB",
    4505: "SaltStack",
    4506: "SaltStack",
    5432: "PostgreSQL",
    5601: "Kibana",
    5900: "VNC",
    5984: "CouchDB",
    6379: "Redis",
    7001: "WebLogic",
    8020: "Hadoop NameNode",
    8080: "HTTP alt / app server",
    8086: "InfluxDB",
    8161: "ActiveMQ",
    9000: "SonarQube / PHP-FPM",
    9042: "Cassandra",
    9092: "Kafka",
    9200: "Elasticsearch",
    9300: "Elasticsearch transport",
    11211: "Memcached",
    27017: "MongoDB",
    27018: "MongoDB shard",
    50070: "Hadoop HDFS",
}

# Ports where public exposure is normal and expected.
WEB_PORTS = {80, 443}

# A rule spanning more than this many ports from the internet is effectively
# "everything" and deserves its own finding.
WIDE_RANGE_THRESHOLD = 100

# Anything at or above this is treated as "many rules" when summarising.
IP_PROTOCOL_NAMES = {"-1": "ALL", "6": "tcp", "17": "udp", "1": "icmp", "58": "icmpv6"}


# ─────────────────────────────────────────────────────────────────────────────
# Finding model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    """One thing that is wrong, and what to do about it."""
    check_id: str
    severity: str
    title: str
    resource_type: str
    resource_name: str
    detail: str
    remediation: str
    vpc_id: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def resource(self) -> str:
        return f"{self.resource_type}/{self.resource_name}"


class AssessmentReport:
    """Collects findings and turns them into output."""

    def __init__(self, account_id: str, identity_arn: str, region: str) -> None:
        self.account_id = account_id
        self.identity_arn = identity_arn
        self.region = region
        self.scanned_at = datetime.now(timezone.utc)
        self.findings: list[Finding] = []
        self.stats: dict[str, int] = {}

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def by_severity(self) -> dict[str, list[Finding]]:
        buckets: dict[str, list[Finding]] = {s: [] for s in SEVERITY_ORDER}
        for f in self.findings:
            buckets[f.severity].append(f)
        return buckets

    def counts(self) -> dict[str, int]:
        return {s: len(v) for s, v in self.by_severity().items()}

    def by_vpc(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for f in self.findings:
            counts[f.vpc_id or "-"] += 1
        return dict(counts)

    def score(self) -> int:
        """100 = clean. Each finding deducts points by severity. Floor of 0."""
        deducted = sum(SEVERITY_WEIGHT[f.severity] for f in self.findings)
        return max(0, 100 - deducted)

    def grade(self) -> str:
        s = self.score()
        for threshold, letter in ((90, "A"), (80, "B"), (70, "C"), (60, "D")):
            if s >= threshold:
                return letter
        return "F"

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "identity_arn": self.identity_arn,
            "region": self.region,
            "scanned_at": self.scanned_at.isoformat(),
            "statistics": self.stats,
            "findings_by_vpc": self.by_vpc(),
            "summary": {
                "counts": self.counts(),
                "total": len(self.findings),
                "score": self.score(),
                "grade": self.grade(),
            },
            "findings": [asdict(f) for f in self.findings],
        }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def paginate(client, method: str, key: str, **kwargs) -> Iterable[dict]:
    """
    Yield every item from a paginated EC2 API.

    EC2 Describe* calls cap out at 1000 items and hand you a NextToken. Forgetting
    to paginate is the single most common bug in home-grown audit scripts — it
    silently under-reports in exactly the large accounts where auditing matters most.
    """
    paginator = client.get_paginator(method)
    for page in paginator.paginate(**kwargs):
        yield from page.get(key, [])


def name_tag(resource: dict, fallback_key: str = "") -> str:
    """Pull the Name tag off any EC2 resource, falling back to its ID."""
    for tag in resource.get("Tags", []) or []:
        if tag.get("Key") == "Name":
            return tag["Value"]
    return resource.get(fallback_key, "") or "<unnamed>"


def all_tags(resource: dict) -> dict[str, str]:
    return {t["Key"]: t["Value"] for t in (resource.get("Tags") or [])}


def protocol_name(proto: str) -> str:
    return IP_PROTOCOL_NAMES.get(str(proto), str(proto))


def port_range_label(rule: dict) -> str:
    """Human label for a security group rule's port range."""
    proto = str(rule.get("IpProtocol", "-1"))
    if proto == "-1":
        return "ALL ports / ALL protocols"
    lo = rule.get("FromPort")
    hi = rule.get("ToPort")
    if lo is None and hi is None:
        return f"{protocol_name(proto)} (all ports)"
    if lo == hi:
        return f"{protocol_name(proto)}/{lo}"
    return f"{protocol_name(proto)}/{lo}-{hi}"


def rule_ports(rule: dict) -> set[int]:
    """
    Every port a security group rule covers.

    Protocol -1 means all protocols and AWS omits FromPort/ToPort entirely, so we
    return the full range. Callers should check the protocol before assuming this
    set is small — it can be 65,536 entries.
    """
    if str(rule.get("IpProtocol", "-1")) == "-1":
        return set(range(0, 65536))
    lo = rule.get("FromPort")
    hi = rule.get("ToPort")
    if lo is None or hi is None:
        return set(range(0, 65536))
    return set(range(int(lo), int(hi) + 1))


def port_span(rule: dict) -> int:
    """How many ports this rule covers, without materialising the set."""
    if str(rule.get("IpProtocol", "-1")) == "-1":
        return 65536
    lo, hi = rule.get("FromPort"), rule.get("ToPort")
    if lo is None or hi is None:
        return 65536
    return int(hi) - int(lo) + 1


def matched_sensitive_ports(rule: dict) -> dict[int, str]:
    """Which well-known sensitive ports fall inside this rule's range."""
    if str(rule.get("IpProtocol", "-1")) == "-1":
        return dict(SENSITIVE_PORTS)
    lo, hi = rule.get("FromPort"), rule.get("ToPort")
    if lo is None or hi is None:
        return dict(SENSITIVE_PORTS)
    return {p: n for p, n in SENSITIVE_PORTS.items() if int(lo) <= p <= int(hi)}


def cidr_contains(outer: str, inner: str) -> bool:
    """True if `outer` fully contains `inner`. Used for NACL shadow detection."""
    try:
        return ipaddress.ip_network(inner, strict=False).subnet_of(
            ipaddress.ip_network(outer, strict=False)
        )
    except (ValueError, TypeError):
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Collection — one pass, then analyse offline
# ─────────────────────────────────────────────────────────────────────────────

def collect(ec2, vpc_filter: str | None, include_default_vpc: bool) -> dict[str, Any]:
    """
    Fetch everything we need in one go, then analyse in memory.

    Collecting and analysing separately keeps the API-call count predictable, makes
    the analysis functions pure and testable, and means you can pickle the collected
    data to develop rules offline without hammering AWS.
    """
    vpc_filters = [{"Name": "vpc-id", "Values": [vpc_filter]}] if vpc_filter else []

    vpcs = list(paginate(ec2, "describe_vpcs", "Vpcs", Filters=vpc_filters))
    if not include_default_vpc and not vpc_filter:
        vpcs = [v for v in vpcs if not v.get("IsDefault")]

    vpc_ids = [v["VpcId"] for v in vpcs]
    if not vpc_ids:
        return {"vpcs": [], "subnets": [], "route_tables": [], "security_groups": [],
                "nacls": [], "enis": [], "flow_logs": [], "endpoints": [],
                "igws": [], "nat_gateways": []}

    scoped = [{"Name": "vpc-id", "Values": vpc_ids}]

    data: dict[str, Any] = {
        "vpcs": vpcs,
        "subnets": list(paginate(ec2, "describe_subnets", "Subnets", Filters=scoped)),
        "route_tables": list(paginate(ec2, "describe_route_tables", "RouteTables", Filters=scoped)),
        "security_groups": list(paginate(ec2, "describe_security_groups", "SecurityGroups", Filters=scoped)),
        "nacls": list(paginate(ec2, "describe_network_acls", "NetworkAcls", Filters=scoped)),
        "enis": list(paginate(ec2, "describe_network_interfaces", "NetworkInterfaces", Filters=scoped)),
        "endpoints": list(paginate(ec2, "describe_vpc_endpoints", "VpcEndpoints", Filters=scoped)),
        "nat_gateways": [
            n for n in paginate(ec2, "describe_nat_gateways", "NatGateways",
                                Filter=[{"Name": "vpc-id", "Values": vpc_ids}])
            if n.get("State") in ("available", "pending")
        ],
        "igws": list(paginate(ec2, "describe_internet_gateways", "InternetGateways",
                              Filters=[{"Name": "attachment.vpc-id", "Values": vpc_ids}])),
    }

    # describe_flow_logs filters on resource-id, not vpc-id.
    data["flow_logs"] = list(paginate(
        ec2, "describe_flow_logs", "FlowLogs",
        Filters=[{"Name": "resource-id", "Values": vpc_ids}],
    ))

    return data


# ─────────────────────────────────────────────────────────────────────────────
# Analysis · security groups
# ─────────────────────────────────────────────────────────────────────────────

def analyse_security_group_rule(rule: dict, sg: dict, direction: str) -> list[Finding]:
    """
    Inspect one IpPermission entry and return findings.

    An IpPermission looks like:
        {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
         "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "..."}],
         "Ipv6Ranges": [{"CidrIpv6": "::/0"}],
         "UserIdGroupPairs": [{"GroupId": "sg-abc"}],
         "PrefixListIds": []}

    Note that ONE permission can carry many sources. A rule with five CIDRs is one
    dict, not five — iterate the ranges, not the permissions.
    """
    findings: list[Finding] = []
    sg_id = sg["GroupId"]
    sg_name = sg.get("GroupName", sg_id)
    vpc_id = sg.get("VpcId", "")
    label = port_range_label(rule)
    proto = str(rule.get("IpProtocol", "-1"))

    ipv4_open = [r for r in rule.get("IpRanges", []) if r.get("CidrIp") == WORLD_IPV4]
    ipv6_open = [r for r in rule.get("Ipv6Ranges", []) if r.get("CidrIpv6") == WORLD_IPV6]

    def make(check_id: str, severity: str, title: str, detail: str, remediation: str,
             extra: dict | None = None) -> Finding:
        return Finding(
            check_id=check_id,
            severity=severity,
            title=title,
            resource_type="security-group",
            resource_name=f"{sg_name} ({sg_id})",
            detail=detail,
            remediation=remediation,
            vpc_id=vpc_id,
            evidence={"rule": label, "protocol": proto, "direction": direction,
                      **(extra or {})},
        )

    # ── EGRESS ───────────────────────────────────────────────────────────────
    # Wide-open egress is the AWS default, so it is a LOW, not a HIGH. It still
    # matters: it is the path a compromised instance uses to phone home and to
    # exfiltrate data.
    if direction == "egress":
        if ipv4_open and proto == "-1":
            findings.append(make(
                "VPC-008", "LOW",
                "Unrestricted egress to the internet (all protocols, all ports)",
                f"Allows outbound {label} to {WORLD_IPV4}. This is the AWS default, "
                f"which is precisely why it is rarely questioned.",
                "Scope egress to the destinations the workload actually needs: the next "
                "tier's security group, VPC endpoints, and HTTPS to specific prefix lists. "
                "Egress control is what turns a compromise into a contained compromise.",
            ))
        return findings

    # ── INGRESS ──────────────────────────────────────────────────────────────
    if not ipv4_open and not ipv6_open:
        return findings  # sourced from a CIDR, prefix list or another SG — fine

    ports = matched_sensitive_ports(rule)
    span = port_span(rule)

    # VPC-003 · everything, from everyone
    if proto == "-1" and ipv4_open:
        findings.append(make(
            "VPC-003", "CRITICAL",
            "Security group allows ALL traffic from the entire internet",
            f"Ingress rule permits every protocol on every port from {WORLD_IPV4}. "
            f"This security group provides no protection whatsoever.",
            "Delete this rule. Replace it with the specific ports the workload serves, "
            "sourced from a load-balancer security group or a narrow CIDR.",
        ))
        return findings  # nothing worse to say about this rule

    # VPC-001 / VPC-002 · remote administration from anywhere
    if ipv4_open:
        for port, service in ADMIN_PORTS.items():
            if port in rule_ports(rule) and proto in ("tcp", "6"):
                check = "VPC-001" if port == 22 else "VPC-002"
                findings.append(make(
                    check, "CRITICAL",
                    f"Security group allows {service} (port {port}) from the entire internet",
                    f"Rule {label} accepts {service} from {WORLD_IPV4}. Internet-wide scanners "
                    f"find an open port {port} within minutes of it appearing.",
                    f"Restrict the source to a corporate CIDR or a bastion security group. "
                    f"Better: delete {service} access entirely and use AWS Systems Manager "
                    f"Session Manager, which needs no inbound rule at all.",
                    {"port": port, "service": service},
                ))

    # VPC-004 · sensitive service ports from anywhere
    if ipv4_open:
        exposed = {p: n for p, n in ports.items() if p not in ADMIN_PORTS}
        if exposed and span <= WIDE_RANGE_THRESHOLD:
            listed = ", ".join(f"{p} ({n})" for p, n in sorted(exposed.items())[:6])
            findings.append(make(
                "VPC-004", "HIGH",
                "Security group exposes a sensitive service port to the internet",
                f"Rule {label} exposes: {listed}"
                + (" …" if len(exposed) > 6 else "")
                + f" to {WORLD_IPV4}. Databases, caches and search engines on the public "
                  f"internet are found by Shodan, not by luck.",
                "Move the service into a private subnet and source the rule from the "
                "application tier's security group. Nothing in this list belongs on a "
                "public interface.",
                {"exposed_ports": sorted(exposed)},
            ))

    # VPC-005 · IPv6 wide open
    if ipv6_open:
        severity = "CRITICAL" if (set(ADMIN_PORTS) & rule_ports(rule)) else "HIGH"
        findings.append(make(
            "VPC-005", severity,
            "Security group allows unrestricted IPv6 ingress (::/0)",
            f"Rule {label} accepts traffic from {WORLD_IPV6}. Teams routinely lock down "
            f"IPv4 and forget IPv6 entirely — and every modern scanner speaks both.",
            "Apply the same source restriction to Ipv6Ranges that you applied to IpRanges, "
            "or remove the IPv6 range if the workload is IPv4-only.",
        ))

    # VPC-006 · enormous port range from anywhere
    if ipv4_open and span > WIDE_RANGE_THRESHOLD and proto != "-1":
        findings.append(make(
            "VPC-006", "HIGH",
            f"Security group opens a range of {span:,} ports to the internet",
            f"Rule {label} spans {span:,} ports from {WORLD_IPV4}. A range that wide is "
            f"indistinguishable from 'allow everything' in practice.",
            "Enumerate the ports the service actually listens on. If you genuinely need "
            "an ephemeral range (passive FTP, some RTP workloads), document why and "
            "restrict the source CIDR.",
            {"port_span": span},
        ))

    # VPC-007 · anything else open to the world
    if ipv4_open and proto != "-1" and span <= WIDE_RANGE_THRESHOLD:
        covered = rule_ports(rule)
        if covered <= WEB_PORTS:
            findings.append(make(
                "VPC-007", "INFO",
                "Security group is publicly reachable on a web port",
                f"Rule {label} is open to {WORLD_IPV4}. For a load balancer this is correct "
                f"and expected — flagged so the inventory of public entry points is complete.",
                "No action needed if this is an internet-facing load balancer. If it is on "
                "an instance, put a load balancer or CloudFront in front of it.",
            ))
        elif not (set(ADMIN_PORTS) & covered) and not (set(ports) & covered):
            findings.append(make(
                "VPC-007", "MEDIUM",
                "Security group is open to the internet on a non-web port",
                f"Rule {label} accepts traffic from {WORLD_IPV4} on a port that is neither "
                f"80 nor 443. Every open port is an entry point somebody has to justify.",
                "Confirm this port must be public. If it is an application port, front it "
                "with a load balancer and source the rule from the load balancer's SG.",
            ))

    return findings


def audit_security_groups(data: dict, report: AssessmentReport) -> None:
    """Security groups: internet exposure, IPv6 gaps, orphans, and the default SG."""
    sgs = data["security_groups"]
    report.stats["security_groups"] = len(sgs)

    # Which security groups are actually attached to something? An ENI is the only
    # thing a security group can be attached to — instances, load balancers, RDS,
    # Lambda-in-VPC and endpoints all get there via an ENI.
    attached_ids: set[str] = set()
    for eni in data["enis"]:
        for grp in eni.get("Groups", []) or []:
            attached_ids.add(grp["GroupId"])

    # A group can also be referenced by another group's rules. Deleting one of those
    # breaks the referencing rule, so we downgrade the finding when it happens.
    referenced_ids: set[str] = set()
    for sg in sgs:
        for direction in ("IpPermissions", "IpPermissionsEgress"):
            for rule in sg.get(direction, []) or []:
                for pair in rule.get("UserIdGroupPairs", []) or []:
                    if pair.get("GroupId") and pair["GroupId"] != sg["GroupId"]:
                        referenced_ids.add(pair["GroupId"])

    for sg in sgs:
        sg_id = sg["GroupId"]
        sg_name = sg.get("GroupName", sg_id)
        vpc_id = sg.get("VpcId", "")

        for rule in sg.get("IpPermissions", []) or []:
            for f in analyse_security_group_rule(rule, sg, "ingress"):
                report.add(f)

        for rule in sg.get("IpPermissionsEgress", []) or []:
            for f in analyse_security_group_rule(rule, sg, "egress"):
                report.add(f)

        # ── VPC-010 · the default security group ─────────────────────────────
        # CIS 5.3: the default SG of every VPC should permit no traffic. It cannot
        # be deleted, so the control is "strip every rule from it".
        if sg_name == "default":
            ingress = sg.get("IpPermissions", []) or []
            egress = sg.get("IpPermissionsEgress", []) or []
            if ingress or egress:
                report.add(Finding(
                    check_id="VPC-010",
                    severity="MEDIUM",
                    title="Default security group still has rules",
                    resource_type="security-group",
                    resource_name=f"default ({sg_id})",
                    detail=f"The default security group has {len(ingress)} ingress and "
                           f"{len(egress)} egress rule(s). Anything launched without an "
                           f"explicit security group silently lands in this one.",
                    remediation="Remove every rule from the default security group so that "
                                "accidentally-default resources get no connectivity at all. "
                                "In Terraform: aws_default_security_group with empty blocks.",
                    vpc_id=vpc_id,
                    evidence={"ingress_rules": len(ingress), "egress_rules": len(egress)},
                ))

        # ── VPC-009 · orphaned security group ────────────────────────────────
        elif sg_id not in attached_ids:
            is_referenced = sg_id in referenced_ids
            report.add(Finding(
                check_id="VPC-009",
                severity="LOW",
                title="Security group is attached to no network interface",
                resource_type="security-group",
                resource_name=f"{sg_name} ({sg_id})",
                detail=f"No ENI uses this security group."
                       + (" It IS referenced by another security group's rules, so deleting "
                          "it would break that rule." if is_referenced else
                          " Nothing references it either — it is pure clutter."),
                remediation="Delete it if genuinely unused. Orphaned groups accumulate, and "
                            "the risk is that someone attaches one 'temporarily to debug' "
                            "without reading its rules.",
                vpc_id=vpc_id,
                evidence={"referenced_by_other_sg": is_referenced},
            ))


# ─────────────────────────────────────────────────────────────────────────────
# Analysis · network ACLs
# ─────────────────────────────────────────────────────────────────────────────

def nacl_entry_label(entry: dict) -> str:
    proto = protocol_name(entry.get("Protocol", "-1"))
    pr = entry.get("PortRange")
    if not pr:
        return f"{proto}/all"
    lo, hi = pr.get("From"), pr.get("To")
    return f"{proto}/{lo}" if lo == hi else f"{proto}/{lo}-{hi}"


def nacl_entry_covers(earlier: dict, later: dict) -> bool:
    """
    True if `earlier` matches everything `later` would match.

    NACLs evaluate lowest rule number first and stop at the FIRST match. So if an
    earlier rule covers a later one, the later rule is dead code — it can never be
    reached, no matter what it says.
    """
    e_proto = str(earlier.get("Protocol", "-1"))
    l_proto = str(later.get("Protocol", "-1"))
    if e_proto != "-1" and e_proto != l_proto:
        return False

    e_cidr = earlier.get("CidrBlock") or earlier.get("Ipv6CidrBlock")
    l_cidr = later.get("CidrBlock") or later.get("Ipv6CidrBlock")
    if not e_cidr or not l_cidr or not cidr_contains(e_cidr, l_cidr):
        return False

    e_range = earlier.get("PortRange")
    l_range = later.get("PortRange")
    if e_range is None:
        return True   # earlier covers all ports
    if l_range is None:
        return False  # later spans all ports, earlier does not
    return e_range["From"] <= l_range["From"] and e_range["To"] >= l_range["To"]


def audit_nacls(data: dict, report: AssessmentReport) -> None:
    """Network ACLs: blanket allows, sensitive ports, and unreachable rules."""
    nacls = data["nacls"]
    report.stats["network_acls"] = len(nacls)

    for nacl in nacls:
        nacl_id = nacl["NetworkAclId"]
        nacl_name = name_tag(nacl, "NetworkAclId")
        vpc_id = nacl.get("VpcId", "")
        is_default = nacl.get("IsDefault", False)
        associations = nacl.get("Associations", []) or []
        associated_subnets = [a["SubnetId"] for a in associations if a.get("SubnetId")]

        # An unassociated NACL enforces nothing, so its problems are theoretical.
        # Downgrade rather than ignore — someone will associate it eventually.
        in_use = bool(associated_subnets)
        label = f"{nacl_name} ({nacl_id})"

        entries = sorted(
            [e for e in nacl.get("Entries", []) if not e.get("Egress")],
            key=lambda e: e.get("RuleNumber", 0),
        )

        for idx, entry in enumerate(entries):
            rule_no = entry.get("RuleNumber")
            if rule_no == 32767:
                continue  # the implicit "* deny all" rule AWS appends. Always present.

            action = entry.get("RuleAction")
            cidr = entry.get("CidrBlock") or entry.get("Ipv6CidrBlock") or ""
            open_to_world = cidr in (WORLD_IPV4, WORLD_IPV6)
            proto = str(entry.get("Protocol", "-1"))
            entry_label = nacl_entry_label(entry)

            # ── VPC-011 · allow everything from everywhere ───────────────────
            if action == "allow" and open_to_world and proto == "-1":
                report.add(Finding(
                    check_id="VPC-011",
                    severity="MEDIUM" if in_use else "LOW",
                    title="Network ACL allows all traffic from the internet",
                    resource_type="network-acl",
                    resource_name=label,
                    detail=f"Inbound rule {rule_no} allows every protocol and port from {cidr}"
                           + (f", applied to {len(associated_subnets)} subnet(s)." if in_use
                              else " (this NACL is associated with no subnet).")
                           + (" This is the AWS default NACL behaviour." if is_default else ""),
                    remediation="A NACL that allows everything is a NACL that does nothing. Use "
                                "it as a coarse, stateless second layer: allow 80/443 plus the "
                                "ephemeral range 1024-65535 inbound, and deny known-bad CIDRs.",
                    vpc_id=vpc_id,
                    evidence={"rule_number": rule_no, "cidr": cidr, "is_default": is_default,
                              "associated_subnets": associated_subnets},
                ))

            # ── VPC-012 · sensitive port allowed from the internet ───────────
            if action == "allow" and open_to_world and proto != "-1":
                pr = entry.get("PortRange") or {}
                lo, hi = pr.get("From", 0), pr.get("To", 65535)
                exposed = {p: n for p, n in SENSITIVE_PORTS.items() if lo <= p <= hi}
                exposed.update({p: n for p, n in ADMIN_PORTS.items() if lo <= p <= hi})
                if exposed:
                    listed = ", ".join(f"{p} ({n})" for p, n in sorted(exposed.items())[:6])
                    report.add(Finding(
                        check_id="VPC-012",
                        severity="HIGH" if in_use else "MEDIUM",
                        title="Network ACL permits a sensitive port from the internet",
                        resource_type="network-acl",
                        resource_name=label,
                        detail=f"Inbound rule {rule_no} ({entry_label}) allows {cidr} and covers: "
                               f"{listed}{' …' if len(exposed) > 6 else ''}.",
                        remediation="Remove the port from the NACL allow range. Remember NACLs are "
                                    "evaluated before security groups — a permissive NACL means the "
                                    "SG is your only remaining control.",
                        vpc_id=vpc_id,
                        evidence={"rule_number": rule_no, "cidr": cidr,
                                  "exposed_ports": sorted(exposed)},
                    ))

            # ── VPC-013 · unreachable rule ───────────────────────────────────
            for earlier in entries[:idx]:
                if earlier.get("RuleNumber") == 32767:
                    continue
                if nacl_entry_covers(earlier, entry):
                    report.add(Finding(
                        check_id="VPC-013",
                        severity="MEDIUM",
                        title="Network ACL rule is unreachable (shadowed by a lower-numbered rule)",
                        resource_type="network-acl",
                        resource_name=label,
                        detail=f"Inbound rule {rule_no} ('{action}' {entry_label} from {cidr}) can "
                               f"never be evaluated: rule {earlier.get('RuleNumber')} "
                               f"('{earlier.get('RuleAction')}' {nacl_entry_label(earlier)} from "
                               f"{earlier.get('CidrBlock') or earlier.get('Ipv6CidrBlock')}) already "
                               f"matches everything it would match. NACLs stop at the first match.",
                        remediation=f"Renumber rule {rule_no} below {earlier.get('RuleNumber')}, or "
                                    f"delete it. A deny rule sitting under a blanket allow is dead "
                                    f"code that looks like a control — the worst kind of security "
                                    f"configuration.",
                        vpc_id=vpc_id,
                        evidence={"shadowed_rule": rule_no,
                                  "shadowing_rule": earlier.get("RuleNumber")},
                    ))
                    break  # one explanation per rule is enough


# ─────────────────────────────────────────────────────────────────────────────
# Analysis · subnets, routing, VPC-level controls
# ─────────────────────────────────────────────────────────────────────────────

def build_route_table_index(data: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """
    Map subnet-id → its effective route table, plus vpc-id → main route table.

    A subnet with no explicit association uses its VPC's MAIN route table. Missing
    that rule is how people convince themselves a subnet is private when it is not.
    """
    by_subnet: dict[str, dict] = {}
    main_by_vpc: dict[str, dict] = {}

    for rt in data["route_tables"]:
        for assoc in rt.get("Associations", []) or []:
            if assoc.get("Main"):
                main_by_vpc[rt["VpcId"]] = rt
            elif assoc.get("SubnetId"):
                by_subnet[assoc["SubnetId"]] = rt

    return by_subnet, main_by_vpc


def route_table_for_subnet(subnet: dict, by_subnet: dict, main_by_vpc: dict) -> dict | None:
    return by_subnet.get(subnet["SubnetId"]) or main_by_vpc.get(subnet["VpcId"])


def has_igw_default_route(rt: dict | None) -> bool:
    if not rt:
        return False
    for route in rt.get("Routes", []) or []:
        if route.get("DestinationCidrBlock") == WORLD_IPV4 and \
                str(route.get("GatewayId", "")).startswith("igw-"):
            return True
    return False


def has_nat_default_route(rt: dict | None) -> bool:
    if not rt:
        return False
    for route in rt.get("Routes", []) or []:
        if route.get("DestinationCidrBlock") == WORLD_IPV4 and route.get("NatGatewayId"):
            return True
    return False


PRIVATE_NAME_HINTS = ("private", "internal", "data", "db", "database", "backend", "app")


def audit_subnets(data: dict, report: AssessmentReport) -> None:
    """Subnets: auto-assigned public IPs, and 'private' subnets that route to an IGW."""
    subnets = data["subnets"]
    report.stats["subnets"] = len(subnets)

    by_subnet, main_by_vpc = build_route_table_index(data)

    for subnet in subnets:
        subnet_id = subnet["SubnetId"]
        subnet_name = name_tag(subnet, "SubnetId")
        vpc_id = subnet["VpcId"]
        tags = all_tags(subnet)
        label = f"{subnet_name} ({subnet_id})"

        rt = route_table_for_subnet(subnet, by_subnet, main_by_vpc)
        is_public = has_igw_default_route(rt)
        rt_name = name_tag(rt, "RouteTableId") if rt else "<none>"
        explicit_assoc = subnet_id in by_subnet

        # Does anything about this subnet claim to be private?
        haystack = f"{subnet_name} {tags.get('Tier', '')} {tags.get('Name', '')}".lower()
        claims_private = any(hint in haystack for hint in PRIVATE_NAME_HINTS)
        claims_public = "public" in haystack or "dmz" in haystack

        # ── VPC-016 · the lie ────────────────────────────────────────────────
        # A subnet's tier is determined by its ROUTES. Never by its name, never by
        # its tags. This finding is where those two disagree.
        if is_public and claims_private and not claims_public:
            report.add(Finding(
                check_id="VPC-016",
                severity="HIGH",
                title="Subnet is named/tagged private but routes to an Internet Gateway",
                resource_type="subnet",
                resource_name=label,
                detail=f"Route table '{rt_name}' sends {WORLD_IPV4} to an Internet Gateway, so this "
                       f"is a PUBLIC subnet — regardless of what it is called. Anything launched "
                       f"here with a public IP is directly internet-reachable."
                       + ("" if explicit_assoc else
                          " It inherits this from the VPC's MAIN route table, which is why nobody "
                          "noticed."),
                remediation="Either rename the subnet to reflect reality, or remove the IGW route "
                            "and give it a NAT route instead. Then check what is already running "
                            "in it. Never leave the name and the routing disagreeing — the next "
                            "engineer will trust the name.",
                vpc_id=vpc_id,
                evidence={"route_table": rt.get("RouteTableId") if rt else None,
                          "explicitly_associated": explicit_assoc, "tags": tags},
            ))

        # ── VPC-015 · auto-assign public IPv4 ────────────────────────────────
        if subnet.get("MapPublicIpOnLaunch"):
            if is_public and claims_public:
                report.add(Finding(
                    check_id="VPC-015",
                    severity="LOW",
                    title="Subnet auto-assigns public IPv4 addresses (expected for a public tier)",
                    resource_type="subnet",
                    resource_name=label,
                    detail="MapPublicIpOnLaunch is true and the subnet genuinely routes to an IGW. "
                           "Flagged for inventory completeness, not as a defect.",
                    remediation="No action if this tier is meant to be public. Consider turning it "
                                "off anyway and assigning EIPs explicitly, so 'gets a public IP' "
                                "is always a deliberate decision rather than a default.",
                    vpc_id=vpc_id,
                    evidence={"is_public_routed": True},
                ))
            else:
                report.add(Finding(
                    check_id="VPC-015",
                    severity="MEDIUM",
                    title="Subnet auto-assigns public IPv4 addresses",
                    resource_type="subnet",
                    resource_name=label,
                    detail="MapPublicIpOnLaunch is true, so every instance launched here gets a "
                           "public IP by default — "
                           + ("and the subnet routes to an Internet Gateway, so those instances "
                              "are directly reachable from the internet."
                              if is_public else
                              "even though the subnet has no Internet Gateway route. The IPs are "
                              "useless here, which means the setting is a mistake waiting to "
                              "become a problem the day someone adds an IGW route."),
                    remediation="Set MapPublicIpOnLaunch to false. Public addressing should be an "
                                "explicit per-resource decision (an EIP or a load balancer), never "
                                "a subnet default.",
                    vpc_id=vpc_id,
                    evidence={"is_public_routed": is_public},
                ))


def audit_vpc_controls(data: dict, report: AssessmentReport) -> None:
    """VPC-level controls: flow logs, gateway endpoints, egress design, NAT resilience."""
    vpcs = data["vpcs"]
    report.stats["vpcs"] = len(vpcs)

    flow_logs_by_vpc: dict[str, list[dict]] = defaultdict(list)
    for fl in data["flow_logs"]:
        flow_logs_by_vpc[fl.get("ResourceId", "")].append(fl)

    endpoints_by_vpc: dict[str, list[dict]] = defaultdict(list)
    for ep in data["endpoints"]:
        endpoints_by_vpc[ep.get("VpcId", "")].append(ep)

    nats_by_vpc: dict[str, list[dict]] = defaultdict(list)
    for nat in data["nat_gateways"]:
        nats_by_vpc[nat.get("VpcId", "")].append(nat)

    subnets_by_vpc: dict[str, list[dict]] = defaultdict(list)
    for s in data["subnets"]:
        subnets_by_vpc[s["VpcId"]].append(s)

    by_subnet, main_by_vpc = build_route_table_index(data)

    for vpc in vpcs:
        vpc_id = vpc["VpcId"]
        vpc_name = name_tag(vpc, "VpcId")
        label = f"{vpc_name} ({vpc_id})"

        # ── VPC-014 · no flow logs ───────────────────────────────────────────
        logs = flow_logs_by_vpc.get(vpc_id, [])
        active = [f for f in logs if f.get("FlowLogStatus") == "ACTIVE"]
        if not active:
            report.add(Finding(
                check_id="VPC-014",
                severity="HIGH",
                title="VPC Flow Logs are not enabled",
                resource_type="vpc",
                resource_name=label,
                detail=("No flow log is configured for this VPC." if not logs else
                        f"{len(logs)} flow log(s) exist but none are ACTIVE "
                        f"(status: {', '.join(sorted({f.get('FlowLogStatus', '?') for f in logs}))})."),
                remediation="Enable flow logs with traffic_type = ALL to CloudWatch Logs or S3. "
                            "You cannot enable them retroactively: after an incident, flow logs are "
                            "frequently the only record of what talked to what. Set a short "
                            "retention if cost is the concern — some retention beats none.",
                vpc_id=vpc_id,
                evidence={"flow_log_count": len(logs)},
            ))

        # ── VPC-017 · no S3 gateway endpoint ─────────────────────────────────
        eps = endpoints_by_vpc.get(vpc_id, [])
        has_s3_gateway = any(
            ep.get("VpcEndpointType") == "Gateway" and ep.get("ServiceName", "").endswith(".s3")
            for ep in eps
        )
        if not has_s3_gateway:
            report.add(Finding(
                check_id="VPC-017",
                severity="LOW",
                title="No S3 gateway VPC endpoint",
                resource_type="vpc",
                resource_name=label,
                detail="S3 traffic from private subnets leaves via the NAT Gateway and traverses "
                       "the public internet path. Gateway endpoints are FREE and keep that traffic "
                       "on the AWS network.",
                remediation="Create a Gateway endpoint for com.amazonaws.<region>.s3 and associate "
                            "it with your private route tables. It costs nothing, removes "
                            "$0.045/GB of NAT processing charges, and lets you attach an endpoint "
                            "policy restricting which buckets are reachable.",
                vpc_id=vpc_id,
                evidence={"endpoint_count": len(eps)},
            ))

        # ── VPC-018 / VPC-019 · egress design ────────────────────────────────
        nats = nats_by_vpc.get(vpc_id, [])
        vpc_subnets = subnets_by_vpc.get(vpc_id, [])

        private_subnets = []
        for s in vpc_subnets:
            rt = route_table_for_subnet(s, by_subnet, main_by_vpc)
            if not has_igw_default_route(rt):
                private_subnets.append((s, rt))

        if private_subnets and not nats:
            no_egress = [s for s, rt in private_subnets if not has_nat_default_route(rt)]
            if no_egress and not eps:
                report.add(Finding(
                    check_id="VPC-018",
                    severity="LOW",
                    title="Private subnets have no outbound path and no VPC endpoints",
                    resource_type="vpc",
                    resource_name=label,
                    detail=f"{len(no_egress)} subnet(s) have neither an Internet Gateway route, a "
                           f"NAT Gateway route, nor any VPC endpoint. Workloads here cannot patch, "
                           f"pull images, or call AWS APIs.",
                    remediation="This is fine — good, even — if intentional. If it is not, add "
                                "either a NAT Gateway (~$32/month) or VPC endpoints for the "
                                "specific services needed. Endpoints are usually cheaper and "
                                "strictly more secure.",
                    vpc_id=vpc_id,
                    evidence={"subnets_without_egress": [s["SubnetId"] for s in no_egress]},
                ))

        if len(nats) == 1:
            nat_az = next(
                (s.get("AvailabilityZone") for s in vpc_subnets
                 if s["SubnetId"] == nats[0].get("SubnetId")), "unknown"
            )
            dependents = [s["SubnetId"] for s, rt in private_subnets if has_nat_default_route(rt)]
            azs_served = {
                s.get("AvailabilityZone") for s, rt in private_subnets
                if has_nat_default_route(rt)
            }
            if len(azs_served) > 1:
                report.add(Finding(
                    check_id="VPC-019",
                    severity="LOW",
                    title="All private subnets depend on a single NAT Gateway",
                    resource_type="vpc",
                    resource_name=label,
                    detail=f"One NAT Gateway in {nat_az} serves {len(dependents)} subnet(s) across "
                           f"{len(azs_served)} AZs. If that AZ fails, every private subnet in the "
                           f"VPC loses outbound connectivity — including the ones in healthy AZs. "
                           f"Cross-AZ NAT traffic is also billed at $0.01/GB on top of processing.",
                    remediation="For production, run one NAT Gateway per AZ and point each AZ's "
                                "private route table at its local NAT (~$32/month each). For a lab, "
                                "one is the right trade-off — just know that you made it.",
                    vpc_id=vpc_id,
                    evidence={"nat_az": nat_az, "azs_served": sorted(a for a in azs_served if a)},
                ))


# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────

def print_report(report: AssessmentReport, min_severity: str) -> None:
    threshold = SEVERITY_ORDER.index(min_severity)

    print()
    print("╔" + "═" * 74 + "╗")
    print("║" + "VPC SECURITY ASSESSMENT  ·  CareerByteCode Bootcamp Day 02".center(74) + "║")
    print("╚" + "═" * 74 + "╝")
    print(f"Account : {report.account_id}")
    print(f"Region  : {report.region}")
    print(f"Identity: {report.identity_arn}")
    print(f"Scanned : {report.scanned_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print()
    print("  " + " | ".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in report.stats.items()))

    buckets = report.by_severity()
    for severity in SEVERITY_ORDER:
        items = buckets[severity]
        if not items or SEVERITY_ORDER.index(severity) > threshold:
            continue

        label = f" {SEVERITY_ICON[severity]} {severity} ({len(items)}) "
        print()
        print(label.center(76, "─"))
        for f in items:
            print(f"\n[{f.check_id}] {f.title}")
            print(f"          Resource : {f.resource}")
            if f.vpc_id:
                print(f"          VPC      : {f.vpc_id}")
            print(f"          Detail   : {f.detail}")
            print(f"          Fix      : {f.remediation}")

    by_vpc = report.by_vpc()
    if len(by_vpc) > 1:
        print()
        print(" FINDINGS BY VPC ".center(76, "─"))
        for vpc_id, count in sorted(by_vpc.items(), key=lambda kv: -kv[1]):
            print(f"  {vpc_id:<24} {count}")

    print()
    print(" SUMMARY ".center(76, "═"))
    for severity in SEVERITY_ORDER:
        count = len(buckets[severity])
        if count or severity != "INFO":
            print(f"  {SEVERITY_ICON[severity]} {severity:<10} {count}")
    print("  " + "─" * 16)
    print(f"  TOTAL       {len(report.findings)}")
    print(f"  Network security score: {report.score()}/100  (grade {report.grade()})")
    print()


def write_json(report: AssessmentReport, out_dir: Path, stamp: str) -> Path:
    path = out_dir / f"vpc_assess_{stamp}.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, default=str))
    return path


def write_csv(report: AssessmentReport, out_dir: Path, stamp: str) -> Path:
    path = out_dir / f"vpc_assess_{stamp}.csv"
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["check_id", "severity", "title", "vpc_id", "resource_type",
                         "resource_name", "detail", "remediation"])
        order = {s: i for i, s in enumerate(SEVERITY_ORDER)}
        for f in sorted(report.findings, key=lambda x: (order[x.severity], x.check_id)):
            writer.writerow([f.check_id, f.severity, f.title, f.vpc_id, f.resource_type,
                             f.resource_name, f.detail, f.remediation])
    return path


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Assess AWS VPC network configuration for exposure and missing controls.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--profile", default=os.environ.get("AWS_PROFILE"),
                   help="AWS CLI named profile (default: $AWS_PROFILE)")
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"),
                   help="AWS region to assess. VPCs are regional — one region per run.")
    p.add_argument("--vpc-id",
                   help="Assess only this VPC (default: every VPC in the region)")
    p.add_argument("--include-default-vpc", action="store_true",
                   help="Include the AWS-created default VPC, which is permissive by design")
    p.add_argument("--min-severity", choices=SEVERITY_ORDER, default="LOW",
                   help="Only print findings at or above this severity (default: LOW)")
    p.add_argument("--format", choices=["table", "json", "csv", "all"], default="all",
                   help="Output format (default: all)")
    p.add_argument("--output-dir", default="reports",
                   help="Directory for JSON/CSV reports (default: ./reports)")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress the console table (useful when piping JSON)")
    p.add_argument("--fail-on", choices=SEVERITY_ORDER,
                   help="Exit with code 1 if any finding at or above this severity exists (CI mode)")
    return p


def main() -> int:
    args = build_parser().parse_args()

    # --- connect ---------------------------------------------------------
    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        identity = session.client("sts").get_caller_identity()
        ec2 = session.client("ec2")
    except ProfileNotFound:
        print(f"❌ Profile '{args.profile}' not found. Try: aws configure --profile {args.profile}",
              file=sys.stderr)
        return 2
    except NoCredentialsError:
        print("❌ No AWS credentials found. Set AWS_PROFILE or pass --profile.", file=sys.stderr)
        return 2
    except ClientError as exc:
        print(f"❌ Could not authenticate: {exc}", file=sys.stderr)
        return 2

    report = AssessmentReport(identity["Account"], identity["Arn"], args.region)

    if not args.quiet:
        scope = args.vpc_id or f"all VPCs in {args.region}"
        print(f"\n🔍 Assessing {scope} in account {report.account_id} ...")
        print("   • collecting VPCs, subnets, route tables, security groups, NACLs, ENIs ...")

    # --- collect ---------------------------------------------------------
    try:
        data = collect(ec2, args.vpc_id, args.include_default_vpc)
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("AccessDenied", "UnauthorizedOperation", "AccessDeniedException"):
            print("❌ Access denied on ec2:Describe*. Attach the SecurityAudit or "
                  "ReadOnlyAccess policy to this identity.", file=sys.stderr)
            return 2
        raise

    if not data["vpcs"]:
        hint = ("" if args.include_default_vpc or args.vpc_id else
                " (default VPCs are skipped — pass --include-default-vpc to include them)")
        print(f"\n⚠️  No VPCs found in {args.region}{hint}.\n", file=sys.stderr)
        return 0

    # --- analyse ---------------------------------------------------------
    sections = [
        ("security groups", lambda: audit_security_groups(data, report)),
        ("network ACLs", lambda: audit_nacls(data, report)),
        ("subnets and routing", lambda: audit_subnets(data, report)),
        ("VPC-level controls", lambda: audit_vpc_controls(data, report)),
    ]
    for label, fn in sections:
        if not args.quiet:
            print(f"   • analysing {label} ...")
        fn()

    # --- output ----------------------------------------------------------
    if args.format in ("table", "all") and not args.quiet:
        print_report(report, args.min_severity)

    if args.format == "json" and args.quiet:
        print(json.dumps(report.to_dict(), indent=2, default=str))

    written: list[Path] = []
    if args.format in ("json", "csv", "all"):
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = report.scanned_at.strftime("%Y%m%d_%H%M%S")
        if args.format in ("json", "all"):
            written.append(write_json(report, out_dir, stamp))
        if args.format in ("csv", "all"):
            written.append(write_csv(report, out_dir, stamp))

    if written and not args.quiet:
        print("Reports written:")
        for path in written:
            print(f"  {path}")
        print()

    # --- CI exit code -----------------------------------------------------
    if args.fail_on:
        limit = SEVERITY_ORDER.index(args.fail_on)
        if any(SEVERITY_ORDER.index(f.severity) <= limit for f in report.findings):
            if not args.quiet:
                print(f"❌ Failing: findings at or above {args.fail_on} were detected.")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
