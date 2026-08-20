#!/usr/bin/env python3
"""
🎯 CHALLENGE — VPC Security Assessment Tool
CareerByteCode AWS Cloud Architecture & AIOps Bootcamp · Day 02

Your job: implement the six TODO blocks below and make this tool find real network exposure.

The scaffolding (CLI parsing, AWS session, collection, Finding model, report printing) is
done for you. The network security logic is yours.

Run it:
    python3 vpc_assess_challenge.py --profile bootcamp

Right now it runs cleanly and reports NOTHING, because every analysis function is empty.
That's the starting line.

────────────────────────────────────────────────────────────────────────────────
ORDER OF ATTACK  (do them in this order — each builds on the last)

  TODO 1  analyse_security_group_rule()  ~20 min  ⭐ The core rule engine
  TODO 2  audit_security_groups()        ~10 min  Loop every SG + find orphans
  TODO 3  audit_nacls()                  ~15 min  Stateless rules, ordering, shadowing
  TODO 4  audit_subnets()                ~15 min  Routing truth vs subnet names
  TODO 5  audit_vpc_controls()           ~10 min  Flow logs, endpoints, NAT design
  TODO 6  Stretch                        ~??      Add your own check

Target: after TODO 2 you should already be catching cbc-day02-BAD-open-ssh-sg with four
CRITICAL findings. That's your first win — go get it.
────────────────────────────────────────────────────────────────────────────────

⚠️  ONE CONCEPT TO INTERNALISE BEFORE YOU START

    Security groups are STATEFUL and ALLOW-ONLY.
        → return traffic is automatic; there is no deny rule; nothing matches = dropped.

    NACLs are STATELESS and NUMBERED.
        → you must allow the response separately (ephemeral ports 1024-65535),
          rules evaluate lowest-number-first, and the FIRST match wins.

    Almost every finding you are about to write comes from one of those two sentences.

If you get stuck for more than ~10 minutes on one function, open the solution at
../vpc_assess.py, read ONLY that function, then come back. That's not cheating —
that's how engineers actually learn.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Iterable

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError, ProfileNotFound
except ImportError:
    sys.exit("boto3 is not installed. Run:  pip install boto3")


SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
SEVERITY_ICON = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵", "INFO": "⚪"}
SEVERITY_WEIGHT = {"CRITICAL": 25, "HIGH": 10, "MEDIUM": 4, "LOW": 1, "INFO": 0}

WORLD_IPV4 = "0.0.0.0/0"
WORLD_IPV6 = "::/0"

# Remote administration ports. These two are why most cryptomining incidents happen.
ADMIN_PORTS = {22: "SSH", 3389: "RDP"}

# Ports that should never face the internet. Extend this — it's your rule set.
SENSITIVE_PORTS = {
    21: "FTP", 23: "Telnet", 25: "SMTP", 135: "MSRPC", 139: "NetBIOS/SMB", 445: "SMB",
    1433: "MSSQL", 1521: "Oracle", 2049: "NFS", 2375: "Docker API (unencrypted)",
    2379: "etcd", 3306: "MySQL/MariaDB", 5432: "PostgreSQL", 5601: "Kibana",
    5900: "VNC", 6379: "Redis", 9042: "Cassandra", 9092: "Kafka",
    9200: "Elasticsearch", 11211: "Memcached", 27017: "MongoDB",
}

WEB_PORTS = {80, 443}
WIDE_RANGE_THRESHOLD = 100
IP_PROTOCOL_NAMES = {"-1": "ALL", "6": "tcp", "17": "udp", "1": "icmp", "58": "icmpv6"}


# ═════════════════════════════════════════════════════════════════════════════
# GIVEN TO YOU — don't change these
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class Finding:
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

    def score(self) -> int:
        return max(0, 100 - sum(SEVERITY_WEIGHT[f.severity] for f in self.findings))


def paginate(client, method: str, key: str, **kwargs) -> Iterable[dict]:
    """
    Yield every item from a paginated EC2 API.

    Use this instead of calling describe_subnets() directly — EC2 caps responses at
    1000 items and hands you a NextToken. Forgetting to paginate is the #1 bug in
    home-grown audit scripts: it silently under-reports in exactly the large accounts
    where auditing matters most.

        for sg in paginate(ec2, "describe_security_groups", "SecurityGroups"):
            print(sg["GroupId"])
    """
    for page in client.get_paginator(method).paginate(**kwargs):
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
    """Human label for a security group rule's port range, e.g. 'tcp/22' or 'tcp/1024-65535'."""
    proto = str(rule.get("IpProtocol", "-1"))
    if proto == "-1":
        return "ALL ports / ALL protocols"
    lo, hi = rule.get("FromPort"), rule.get("ToPort")
    if lo is None and hi is None:
        return f"{protocol_name(proto)} (all ports)"
    return f"{protocol_name(proto)}/{lo}" if lo == hi else f"{protocol_name(proto)}/{lo}-{hi}"


def rule_ports(rule: dict) -> set[int]:
    """
    Every port a security group rule covers.

    ⚠️ THE TRAP: when IpProtocol is "-1" (all protocols), AWS OMITS FromPort and ToPort
    entirely. rule.get("FromPort") returns None, and None <= 22 raises TypeError.
    This helper handles it — use it rather than reading FromPort yourself.
    """
    if str(rule.get("IpProtocol", "-1")) == "-1":
        return set(range(0, 65536))
    lo, hi = rule.get("FromPort"), rule.get("ToPort")
    if lo is None or hi is None:
        return set(range(0, 65536))
    return set(range(int(lo), int(hi) + 1))


def port_span(rule: dict) -> int:
    """How many ports this rule covers, without building a 65k-element set."""
    if str(rule.get("IpProtocol", "-1")) == "-1":
        return 65536
    lo, hi = rule.get("FromPort"), rule.get("ToPort")
    if lo is None or hi is None:
        return 65536
    return int(hi) - int(lo) + 1


def matched_sensitive_ports(rule: dict) -> dict[int, str]:
    """Which well-known sensitive ports fall inside this rule's range. {port: name}"""
    if str(rule.get("IpProtocol", "-1")) == "-1":
        return dict(SENSITIVE_PORTS)
    lo, hi = rule.get("FromPort"), rule.get("ToPort")
    if lo is None or hi is None:
        return dict(SENSITIVE_PORTS)
    return {p: n for p, n in SENSITIVE_PORTS.items() if int(lo) <= p <= int(hi)}


def cidr_contains(outer: str, inner: str) -> bool:
    """True if `outer` fully contains `inner`. You'll need this for NACL shadow detection."""
    try:
        return ipaddress.ip_network(inner, strict=False).subnet_of(
            ipaddress.ip_network(outer, strict=False)
        )
    except (ValueError, TypeError):
        return False


def nacl_entry_label(entry: dict) -> str:
    proto = protocol_name(entry.get("Protocol", "-1"))
    pr = entry.get("PortRange")
    if not pr:
        return f"{proto}/all"
    lo, hi = pr.get("From"), pr.get("To")
    return f"{proto}/{lo}" if lo == hi else f"{proto}/{lo}-{hi}"


def build_route_table_index(data: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """
    Returns (route_table_by_subnet_id, main_route_table_by_vpc_id).

    ⚠️ CRITICAL CONCEPT: a subnet with no EXPLICIT route table association silently
    uses its VPC's MAIN route table. If you only look at explicit associations you
    will conclude that subnets are private when they are not. This is exactly the
    mistake that produces publicly-exposed databases.
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
    """
    A subnet is PUBLIC if and only if its route table sends 0.0.0.0/0 to an igw-*.
    Not if it is named public. Not if it is tagged public. Only this.
    """
    if not rt:
        return False
    return any(
        r.get("DestinationCidrBlock") == WORLD_IPV4 and str(r.get("GatewayId", "")).startswith("igw-")
        for r in rt.get("Routes", []) or []
    )


def has_nat_default_route(rt: dict | None) -> bool:
    if not rt:
        return False
    return any(
        r.get("DestinationCidrBlock") == WORLD_IPV4 and r.get("NatGatewayId")
        for r in rt.get("Routes", []) or []
    )


def collect(ec2, vpc_filter: str | None, include_default_vpc: bool) -> dict[str, Any]:
    """Given to you: one collection pass, so your analysis functions are pure and testable."""
    vpc_filters = [{"Name": "vpc-id", "Values": [vpc_filter]}] if vpc_filter else []
    vpcs = list(paginate(ec2, "describe_vpcs", "Vpcs", Filters=vpc_filters))
    if not include_default_vpc and not vpc_filter:
        vpcs = [v for v in vpcs if not v.get("IsDefault")]

    vpc_ids = [v["VpcId"] for v in vpcs]
    empty = {"vpcs": [], "subnets": [], "route_tables": [], "security_groups": [],
             "nacls": [], "enis": [], "flow_logs": [], "endpoints": [],
             "igws": [], "nat_gateways": []}
    if not vpc_ids:
        return empty

    scoped = [{"Name": "vpc-id", "Values": vpc_ids}]
    return {
        "vpcs": vpcs,
        "subnets": list(paginate(ec2, "describe_subnets", "Subnets", Filters=scoped)),
        "route_tables": list(paginate(ec2, "describe_route_tables", "RouteTables", Filters=scoped)),
        "security_groups": list(paginate(ec2, "describe_security_groups", "SecurityGroups", Filters=scoped)),
        "nacls": list(paginate(ec2, "describe_network_acls", "NetworkAcls", Filters=scoped)),
        "enis": list(paginate(ec2, "describe_network_interfaces", "NetworkInterfaces", Filters=scoped)),
        "endpoints": list(paginate(ec2, "describe_vpc_endpoints", "VpcEndpoints", Filters=scoped)),
        "flow_logs": list(paginate(ec2, "describe_flow_logs", "FlowLogs",
                                   Filters=[{"Name": "resource-id", "Values": vpc_ids}])),
        "nat_gateways": [n for n in paginate(ec2, "describe_nat_gateways", "NatGateways",
                                             Filter=[{"Name": "vpc-id", "Values": vpc_ids}])
                         if n.get("State") in ("available", "pending")],
        "igws": list(paginate(ec2, "describe_internet_gateways", "InternetGateways",
                              Filters=[{"Name": "attachment.vpc-id", "Values": vpc_ids}])),
    }


def print_report(report: AssessmentReport) -> None:
    print()
    print("╔" + "═" * 74 + "╗")
    print("║" + "VPC SECURITY ASSESSMENT (challenge build)".center(74) + "║")
    print("╚" + "═" * 74 + "╝")
    print(f"Account : {report.account_id}")
    print(f"Region  : {report.region}")
    print(f"Identity: {report.identity_arn}")
    print(f"Scanned : {report.scanned_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    if report.stats:
        print("\n  " + " | ".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in report.stats.items()))

    buckets = report.by_severity()
    for severity in SEVERITY_ORDER:
        items = buckets[severity]
        if not items:
            continue
        print()
        print(f" {SEVERITY_ICON[severity]} {severity} ({len(items)}) ".center(76, "─"))
        for f in items:
            print(f"\n[{f.check_id}] {f.title}")
            print(f"          Resource : {f.resource}")
            print(f"          Detail   : {f.detail}")
            print(f"          Fix      : {f.remediation}")

    print()
    print(" SUMMARY ".center(76, "═"))
    for severity in SEVERITY_ORDER:
        print(f"  {SEVERITY_ICON[severity]} {severity:<10} {len(buckets[severity])}")
    print(f"\n  TOTAL: {len(report.findings)}   Score: {report.score()}/100")
    if not report.findings:
        print("\n  😴 Zero findings. Either your network is perfect, or your TODOs are empty.")
        print("     (It's the TODOs.)")
    print()


# ═════════════════════════════════════════════════════════════════════════════
# YOUR WORK STARTS HERE
# ═════════════════════════════════════════════════════════════════════════════

def analyse_security_group_rule(rule: dict, sg: dict, direction: str) -> list[Finding]:
    """
    TODO 1 ⭐ ── THE CORE RULE ENGINE. This is the heart of the tool.  (~20 min)

    You get ONE IpPermission entry. Return a list of Finding objects for it.

    An IpPermission looks like this:

        {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
         "IpRanges":         [{"CidrIp": "0.0.0.0/0", "Description": "..."}],
         "Ipv6Ranges":       [{"CidrIpv6": "::/0"}],
         "UserIdGroupPairs": [{"GroupId": "sg-abc"}],
         "PrefixListIds":    []}

    ⚠️ ONE permission can carry MANY sources. A rule with five CIDRs is one dict,
       not five. Iterate the ranges, not the permissions.

    Skeleton:
        findings  = []
        sg_id     = sg["GroupId"]
        sg_name   = sg.get("GroupName", sg_id)
        vpc_id    = sg.get("VpcId", "")
        label     = port_range_label(rule)
        proto     = str(rule.get("IpProtocol", "-1"))

        ipv4_open = [r for r in rule.get("IpRanges", [])   if r.get("CidrIp")   == WORLD_IPV4]
        ipv6_open = [r for r in rule.get("Ipv6Ranges", []) if r.get("CidrIpv6") == WORLD_IPV6]

        if direction == "egress":
            ...          # only VPC-008 lives here, then return
        if not ipv4_open and not ipv6_open:
            return findings   # sourced from a CIDR / prefix list / another SG — fine
        ...
        return findings

    Rules to implement:

      VPC-003 · CRITICAL — everything from everyone
        WHEN: proto == "-1" and ipv4_open
        Then `return findings` — there is nothing worse to say about this rule.

      VPC-001 / VPC-002 · CRITICAL — SSH (22) / RDP (3389) from 0.0.0.0/0
        WHEN: ipv4_open and the port is in rule_ports(rule) and proto is tcp
        HINT: proto for TCP can arrive as "tcp" OR "6". Check both.
        HINT: iterate ADMIN_PORTS.items(); check id is "VPC-001" for 22, "VPC-002" for 3389.

      VPC-004 · HIGH — a sensitive service port from 0.0.0.0/0
        WHEN: matched_sensitive_ports(rule) is non-empty, excluding the admin ports
              you already reported, AND port_span(rule) <= WIDE_RANGE_THRESHOLD
        WHY the span guard: a 30,000-port range trips every sensitive port at once
        and would produce a useless wall of text. That case is VPC-006 instead.

      VPC-005 · HIGH — ::/0 ingress
        WHEN: ipv6_open
        Escalate to CRITICAL if an admin port is in range.
        WHY this check exists: teams lock down IPv4 and forget IPv6 completely.
        Every modern scanner speaks both.

      VPC-006 · HIGH — a huge port range from 0.0.0.0/0
        WHEN: ipv4_open and port_span(rule) > WIDE_RANGE_THRESHOLD and proto != "-1"

      VPC-007 · MEDIUM / INFO — anything else open to the world
        If rule_ports(rule) is a subset of WEB_PORTS → INFO (an ALB is supposed to
        be public; you're recording it for the inventory, not scolding anyone).
        Otherwise, if it isn't already covered above → MEDIUM.

      VPC-008 · LOW — unrestricted EGRESS (direction == "egress", proto "-1", 0.0.0.0/0)
        Low, because it's the AWS default — which is exactly why nobody questions it.
        Say something useful about data exfiltration in the remediation.

    Every Finding needs a real `remediation` string — advice you'd actually want to
    receive at 2am. "Fix the security group" helps nobody. Name the AWS feature.
    """
    findings: list[Finding] = []
    # YOUR CODE HERE
    return findings


def audit_security_groups(data: dict, report: AssessmentReport) -> None:
    """
    TODO 2 ── Loop every security group, plus two group-level checks.  (~10 min)

    Steps:
      1. sgs = data["security_groups"];  report.stats["security_groups"] = len(sgs)

      2. For each sg, feed both directions into your TODO 1 function:
             for rule in sg.get("IpPermissions", []) or []:
                 for f in analyse_security_group_rule(rule, sg, "ingress"):
                     report.add(f)
             for rule in sg.get("IpPermissionsEgress", []) or []:
                 for f in analyse_security_group_rule(rule, sg, "egress"):
                     report.add(f)

      3. VPC-010 · MEDIUM — the DEFAULT security group still has rules
         WHEN: sg["GroupName"] == "default" and it has any ingress or egress rules.
         WHY: you cannot delete a default SG, only empty it. Anything launched
         without an explicit SG silently lands in this one. CIS benchmark 5.3.

      4. VPC-009 · LOW — orphaned security group
         An SG can only ever be attached to an ENI. Build the attached set first:
             attached = {g["GroupId"] for eni in data["enis"]
                                      for g in (eni.get("Groups") or [])}
         Then flag any non-default SG whose id is not in that set.

         🎓 NUANCE WORTH IMPLEMENTING: an SG can also be REFERENCED by another SG's
         rules (UserIdGroupPairs). Deleting one of those breaks the referencing rule.
         Build a `referenced` set too and say so in the detail text. Good tools tell
         you why the obvious fix might be wrong.

    ✅ CHECKPOINT: run the tool now. `cbc-day02-BAD-open-ssh-sg` should light up with
       VPC-001, VPC-002, VPC-003, VPC-004, VPC-005 and VPC-006. First blood.

       Note: your alb/app/db/bastion SGs will ALSO show as VPC-009 orphans — you
       built no EC2 instances today, so nothing is attached to them. That is correct
       behaviour, not a bug in your code.
    """
    # YOUR CODE HERE
    pass


def audit_nacls(data: dict, report: AssessmentReport) -> None:
    """
    TODO 3 ── Network ACLs: the stateless, ordered layer.  (~15 min)

    A NACL entry looks like:
        {"RuleNumber": 100, "Protocol": "-1", "RuleAction": "allow", "Egress": False,
         "CidrBlock": "0.0.0.0/0", "PortRange": {"From": 22, "To": 22}}

    Note "Protocol" is a STRING of the IANA number: "-1" all, "6" tcp, "17" udp.
    Note PortRange is ABSENT when the protocol is "-1".

    For each nacl in data["nacls"]:

      Set-up:
        • entries = sorted([e for e in nacl["Entries"] if not e["Egress"]],
                           key=lambda e: e["RuleNumber"])
        • SKIP RuleNumber 32767 — that's the implicit "* deny all" AWS always appends.
        • in_use = bool([a["SubnetId"] for a in nacl.get("Associations", [])
                         if a.get("SubnetId")])
          An unassociated NACL enforces nothing, so downgrade its findings one level
          rather than dropping them. Someone will associate it eventually.

      VPC-011 · MEDIUM — blanket allow
        WHEN: RuleAction == "allow", CidrBlock is 0.0.0.0/0, Protocol == "-1"
        A NACL that allows everything is a NACL that does nothing.

      VPC-012 · HIGH — sensitive port allowed from the internet
        WHEN: allow + 0.0.0.0/0 + a specific protocol whose PortRange covers a port
              in SENSITIVE_PORTS or ADMIN_PORTS.
        Say in the detail that NACLs are evaluated BEFORE security groups, so a
        permissive NACL means the SG is the only remaining control.

      VPC-013 · MEDIUM ⭐ — the unreachable rule
        This is the interesting one, and it's the one that catches real bugs.

        NACLs stop at the FIRST match. So if rule 100 allows everything from
        0.0.0.0/0, then rule 200's "deny SSH" can NEVER be evaluated. It is dead
        code that looks exactly like a security control — the worst possible kind
        of configuration.

        Algorithm:
            for idx, entry in enumerate(entries):
                for earlier in entries[:idx]:
                    if covers(earlier, entry):
                        report a finding, then `break`

        Write your own `covers(earlier, later)` helper. It should return True when:
            • earlier's protocol is "-1", or equals later's protocol, AND
            • cidr_contains(earlier_cidr, later_cidr)   ← helper given to you, AND
            • earlier's port range fully spans later's
              (earlier PortRange absent = covers all ports;
               later PortRange absent while earlier has one = NOT covered)

    ✅ CHECKPOINT: `cbc-day02-BAD-open-nacl` should produce VPC-011 (rule 100),
       VPC-013 (rule 200 is dead), and VPC-012 (rule 300 exposes MongoDB).
    """
    # YOUR CODE HERE
    pass


def audit_subnets(data: dict, report: AssessmentReport) -> None:
    """
    TODO 4 ── Subnets and routing: where names lie and routes tell the truth.  (~15 min)

    Set-up (helpers are given to you above):
        by_subnet, main_by_vpc = build_route_table_index(data)
        rt        = route_table_for_subnet(subnet, by_subnet, main_by_vpc)
        is_public = has_igw_default_route(rt)

    VPC-016 · HIGH ⭐ — the lie
        A subnet is PUBLIC if and only if its route table sends 0.0.0.0/0 to an
        igw-*. Not if it is named public. Not if it is tagged public.

        Flag any subnet where is_public is True but the name or Tier tag suggests
        it is private. Build a haystack and check hints:

            tags   = all_tags(subnet)
            hay    = f"{name_tag(subnet)} {tags.get('Tier','')}".lower()
            private_hints = ("private", "internal", "data", "db", "backend")
            claims_private = any(h in hay for h in private_hints)
            claims_public  = "public" in hay or "dmz" in hay

        Bonus detail worth adding: if the subnet had NO explicit route table
        association, it inherited the VPC's MAIN route table — mention that, because
        it is why nobody noticed.

    VPC-015 · MEDIUM / LOW — MapPublicIpOnLaunch
        WHEN: subnet["MapPublicIpOnLaunch"] is True.
        Severity is contextual, and getting this right is what separates a useful
        tool from a noisy one:
            • genuinely public tier (is_public AND claims_public) → LOW, "expected"
            • anything else                                      → MEDIUM
        For the MEDIUM case where the subnet has NO IGW route, explain that the
        public IPs are useless today and become a live exposure the moment someone
        adds an IGW route.

    Don't forget: report.stats["subnets"] = len(data["subnets"])
    """
    # YOUR CODE HERE
    pass


def audit_vpc_controls(data: dict, report: AssessmentReport) -> None:
    """
    TODO 5 ── VPC-wide controls: logging, endpoints, egress design.  (~10 min)

    VPC-014 · HIGH — no flow logs
        data["flow_logs"] entries have "ResourceId" (the vpc-id) and "FlowLogStatus".
        Index them by ResourceId, then flag any VPC with no ACTIVE flow log.

        WHY HIGH: you cannot enable flow logs retroactively. After an incident they
        are frequently the only record of what talked to what. Put that sentence in
        your remediation — it is the argument that actually gets budget approved.

    VPC-017 · LOW — no S3 gateway endpoint
        Look through data["endpoints"] for one where
            VpcEndpointType == "Gateway" and ServiceName endswith ".s3"
        Gateway endpoints are FREE. Without one, every S3 request from a private
        subnet is billed $0.045/GB of NAT processing to reach a service in the
        same region.

    VPC-019 · LOW — single NAT Gateway serving multiple AZs
        If len(nat_gateways_in_vpc) == 1 and more than one AZ's private subnets
        route to it, that AZ is a single point of failure for the whole VPC's
        outbound connectivity. Use has_nat_default_route(rt) to find dependents.

    VPC-018 · LOW (optional) — private subnets with no egress path at all
        No IGW route, no NAT route, and no VPC endpoints. Sometimes deliberate and
        correct; sometimes someone destroyed the NAT to save money and forgot.

    Don't forget: report.stats["vpcs"] = len(data["vpcs"])
    """
    # YOUR CODE HERE
    pass


# ═════════════════════════════════════════════════════════════════════════════
# TODO 6 (stretch) — pick one or more and implement it below
#
#   VPC-020  Peering connections with over-broad routes                MEDIUM
#            describe_vpc_peering_connections; a peering route of 0.0.0.0/0 or a
#            whole /8 means the peer can reach far more than intended.
#
#   VPC-021  Security group rules with no Description                  LOW
#            Every rule should say WHY it exists. Undocumented rules never get
#            deleted, because nobody dares.
#
#   VPC-022  ENI with a public IP in a subnet you thought was private  HIGH
#            describe_network_interfaces → Association.PublicIp
#
#   VPC-023  Flow logs configured but retention is unlimited           LOW
#            Cross-reference logs:DescribeLogGroups retentionInDays. Unlimited
#            retention on a chatty VPC is a five-figure surprise.
#
#   VPC-024  Unattached Elastic IPs                                    LOW
#            describe_addresses → no AssociationId. ~$3.60/month each, for nothing.
#            (This one has paid for itself in almost every account it's been run in.)
#
#   --format html      A styled report you'd be happy to email a manager.
#   --diagram          Emit Mermaid source for the topology you just assessed.
#                      Feed the JSON report to Amazon Bedrock on Day 06 and have it
#                      write the executive summary for you.
# ═════════════════════════════════════════════════════════════════════════════


# ═════════════════════════════════════════════════════════════════════════════
# GIVEN TO YOU — entry point
# ═════════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(description="🎯 CHALLENGE: VPC Security Assessment Tool")
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--vpc-id")
    parser.add_argument("--include-default-vpc", action="store_true")
    args = parser.parse_args()

    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        identity = session.client("sts").get_caller_identity()
        ec2 = session.client("ec2")
    except ProfileNotFound:
        print(f"❌ Profile '{args.profile}' not found.", file=sys.stderr)
        return 2
    except NoCredentialsError:
        print("❌ No AWS credentials. Set AWS_PROFILE or pass --profile.", file=sys.stderr)
        return 2

    report = AssessmentReport(identity["Account"], identity["Arn"], args.region)
    print(f"\n🔍 Assessing {args.vpc_id or 'all VPCs'} in account {report.account_id} ...")

    try:
        data = collect(ec2, args.vpc_id, args.include_default_vpc)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("AccessDenied", "UnauthorizedOperation"):
            print("❌ Access denied on ec2:Describe*. Attach SecurityAudit.", file=sys.stderr)
            return 2
        raise

    if not data["vpcs"]:
        print("\n⚠️  No VPCs found (default VPCs are skipped unless you pass "
              "--include-default-vpc).\n", file=sys.stderr)
        return 0

    for label, fn in [
        ("security groups", lambda: audit_security_groups(data, report)),
        ("network ACLs", lambda: audit_nacls(data, report)),
        ("subnets and routing", lambda: audit_subnets(data, report)),
        ("VPC-level controls", lambda: audit_vpc_controls(data, report)),
    ]:
        print(f"   • analysing {label} ...")
        fn()

    print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
