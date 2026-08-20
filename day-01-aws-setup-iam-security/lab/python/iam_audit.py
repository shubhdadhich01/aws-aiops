#!/usr/bin/env python3
"""
IAM Security Audit Tool  ·  CareerByteCode AWS Cloud Architecture & AIOps Bootcamp — Day 01

Audits an AWS account's IAM configuration and reports least-privilege violations,
credential hygiene problems and dangerous trust relationships.

Everything this tool does is READ-ONLY. It cannot modify your account.

Usage:
    python3 iam_audit.py --profile bootcamp
    python3 iam_audit.py --profile bootcamp --min-severity HIGH
    python3 iam_audit.py --profile bootcamp --format json --quiet
    python3 iam_audit.py --profile bootcamp --fail-on CRITICAL   # for CI pipelines

Required IAM permissions:
    The AWS-managed `SecurityAudit` policy, plus iam:GetLoginProfile and
    iam:GetAccessKeyLastUsed. The Day 01 Terraform creates exactly this role.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote

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

# Actions that let an identity escalate its own privileges. Even scoped, these
# deserve a second look during review.
PRIVILEGE_ESCALATION_ACTIONS = {
    "iam:passrole",
    "iam:createpolicyversion",
    "iam:setdefaultpolicyversion",
    "iam:attachuserpolicy",
    "iam:attachrolepolicy",
    "iam:attachgrouppolicy",
    "iam:putuserpolicy",
    "iam:putrolepolicy",
    "iam:creataccesskey",
    "iam:createaccesskey",
    "iam:updateassumerolepolicy",
    "sts:assumerole",
}

ADMIN_POLICY_ARNS = {
    "arn:aws:iam::aws:policy/AdministratorAccess",
    "arn:aws:iam::aws:policy/IAMFullAccess",
    "arn:aws:iam::aws:policy/PowerUserAccess",
}


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
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def resource(self) -> str:
        return f"{self.resource_type}/{self.resource_name}"


class AuditReport:
    """Collects findings and turns them into output."""

    def __init__(self, account_id: str, identity_arn: str) -> None:
        self.account_id = account_id
        self.identity_arn = identity_arn
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
            "scanned_at": self.scanned_at.isoformat(),
            "statistics": self.stats,
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

def days_since(dt: datetime | None) -> int | None:
    """Whole days between dt and now. Returns None if dt is None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days


def paginate(client, method: str, key: str, **kwargs) -> Iterable[dict]:
    """
    Yield every item from a paginated IAM API.

    IAM list_* calls return at most 100 items. Forgetting to paginate is the
    single most common bug in home-grown audit scripts — it silently under-reports
    in exactly the large accounts where auditing matters most.
    """
    paginator = client.get_paginator(method)
    for page in paginator.paginate(**kwargs):
        yield from page.get(key, [])


def normalise_to_list(value: Any) -> list:
    """IAM JSON fields may be a string or a list. Always give me a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def get_policy_document(iam, policy_arn: str, version_id: str) -> dict:
    """
    Fetch and decode a managed policy's JSON document.

    The API returns the document URL-encoded when the client is old, and already
    decoded as a dict on newer botocore. Handle both, always.
    """
    resp = iam.get_policy_version(PolicyArn=policy_arn, VersionId=version_id)
    doc = resp["PolicyVersion"]["Document"]
    if isinstance(doc, str):
        doc = json.loads(unquote(doc))
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# Policy analysis — the core rule engine
# ─────────────────────────────────────────────────────────────────────────────

def analyse_policy_document(doc: dict, resource_type: str, resource_name: str,
                            context: str = "") -> list[Finding]:
    """
    Inspect a policy JSON document and return findings.

    Detects, in order of severity:
      IAM-002  Action:* AND Resource:*                  → CRITICAL (full admin)
      IAM-006  service:* wildcard (e.g. s3:*)           → HIGH
      IAM-013  iam:PassRole on Resource:*               → HIGH (privilege escalation)
      IAM-007  Resource:* with specific actions         → MEDIUM
    """
    findings: list[Finding] = []
    suffix = f" ({context})" if context else ""

    for stmt in normalise_to_list(doc.get("Statement")):
        if not isinstance(stmt, dict):
            continue
        if stmt.get("Effect") != "Allow":
            continue  # Deny statements narrow permissions — never a finding

        sid = stmt.get("Sid", "<no Sid>")
        actions = [str(a) for a in normalise_to_list(stmt.get("Action"))]
        resources = [str(r) for r in normalise_to_list(stmt.get("Resource"))]
        has_condition = bool(stmt.get("Condition"))

        action_star = any(a == "*" for a in actions)
        resource_star = any(r == "*" for r in resources)
        service_wildcards = [a for a in actions if a.endswith(":*")]
        lower_actions = {a.lower() for a in actions}

        # ---- IAM-002 · full administrative access --------------------------
        if action_star and resource_star:
            findings.append(Finding(
                check_id="IAM-002",
                severity="CRITICAL",
                title="Policy grants full administrative access (Action:* on Resource:*)",
                resource_type=resource_type,
                resource_name=resource_name,
                detail=f"statement '{sid}' allows Action=* on Resource=*{suffix}",
                remediation="Scope actions to the specific APIs required and resources to explicit ARNs.",
                evidence={"sid": sid, "actions": actions, "resources": resources},
            ))
            continue  # already the worst possible finding for this statement

        higher_finding_raised = False

        # ---- IAM-006 · service-wide wildcard -------------------------------
        if service_wildcards and resource_star:
            higher_finding_raised = True
            findings.append(Finding(
                check_id="IAM-006",
                severity="HIGH",
                title="Policy grants service-wide wildcard actions on all resources",
                resource_type=resource_type,
                resource_name=resource_name,
                detail=f"statement '{sid}' allows {', '.join(sorted(service_wildcards))} on Resource=*{suffix}",
                remediation="Replace service:* with the specific operations used. "
                            "Use IAM Access Analyzer policy generation against CloudTrail.",
                evidence={"sid": sid, "wildcard_actions": service_wildcards},
            ))

        # ---- IAM-013 · privilege escalation --------------------------------
        escalation = lower_actions & PRIVILEGE_ESCALATION_ACTIONS
        if escalation and resource_star:
            higher_finding_raised = True
            findings.append(Finding(
                check_id="IAM-013",
                severity="HIGH",
                title="Policy allows privilege-escalation actions on all resources",
                resource_type=resource_type,
                resource_name=resource_name,
                detail=f"statement '{sid}' allows {', '.join(sorted(escalation))} on Resource=*{suffix}",
                remediation="Scope iam:PassRole to specific role ARNs and add an iam:PassedToService condition.",
                evidence={"sid": sid, "escalation_actions": sorted(escalation)},
            ))

        # ---- IAM-007 · unscoped resource -----------------------------------
        if (resource_star and not action_star and not has_condition
                and not higher_finding_raised):
            findings.append(Finding(
                check_id="IAM-007",
                severity="MEDIUM",
                title="Policy statement grants access to all resources (Resource:*)",
                resource_type=resource_type,
                resource_name=resource_name,
                detail=f"statement '{sid}' uses Resource=* with no Condition{suffix}",
                remediation="Scope to explicit ARNs, or add a Condition "
                            "(aws:ResourceTag, aws:RequestedRegion, aws:PrincipalOrgID).",
                evidence={"sid": sid, "actions": actions[:10]},
            ))

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# Audit sections
# ─────────────────────────────────────────────────────────────────────────────

def audit_users(iam, report: AuditReport, max_key_age: int) -> None:
    """Users: admin access, MFA, key age, key usage, direct policies, groups."""
    users = list(paginate(iam, "list_users", "Users"))
    report.stats["users"] = len(users)

    for user in users:
        name = user["UserName"]

        # --- console access + MFA (IAM-004) ---------------------------------
        has_console = True
        try:
            iam.get_login_profile(UserName=name)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "NoSuchEntity":
                has_console = False  # programmatic-only user — perfectly normal
            else:
                raise

        mfa_devices = iam.list_mfa_devices(UserName=name).get("MFADevices", [])
        if has_console and not mfa_devices:
            report.add(Finding(
                check_id="IAM-004",
                severity="HIGH",
                title="Console user without MFA",
                resource_type="user",
                resource_name=name,
                detail="User has a console password but no MFA device registered.",
                remediation="Register a virtual or hardware MFA device, "
                            "and add a Deny-without-MFA statement to their policy.",
                evidence={"console_access": True, "mfa_devices": 0},
            ))

        # --- attached managed policies (IAM-001, IAM-009) -------------------
        attached = iam.list_attached_user_policies(UserName=name).get("AttachedPolicies", [])
        for pol in attached:
            if pol["PolicyArn"] in ADMIN_POLICY_ARNS:
                report.add(Finding(
                    check_id="IAM-001",
                    severity="CRITICAL",
                    title="User has administrator-level policy attached",
                    resource_type="user",
                    resource_name=name,
                    detail=f"{pol['PolicyArn']} attached directly to the user.",
                    remediation="Move admin access behind a role assumed with MFA. "
                                "Keep at most one sealed break-glass user.",
                    evidence={"policy_arn": pol["PolicyArn"]},
                ))

        if attached:
            report.add(Finding(
                check_id="IAM-009",
                severity="MEDIUM",
                title="Policy attached directly to a user instead of via a group",
                resource_type="user",
                resource_name=name,
                detail=f"{len(attached)} managed policy/policies attached directly: "
                       f"{', '.join(p['PolicyName'] for p in attached)}",
                remediation="Attach policies to groups (for humans) or roles (for workloads). "
                            "Direct attachment causes permission drift.",
                evidence={"policies": [p["PolicyName"] for p in attached]},
            ))

        # --- inline policies (IAM-008) --------------------------------------
        inline_names = iam.list_user_policies(UserName=name).get("PolicyNames", [])
        for inline_name in inline_names:
            report.add(Finding(
                check_id="IAM-008",
                severity="MEDIUM",
                title="Inline policy attached to identity",
                resource_type="user",
                resource_name=name,
                detail=f"Inline policy '{inline_name}' — inline policies are invisible to "
                       f"policy-level audits and have no version history.",
                remediation="Convert to a customer-managed policy and attach it.",
                evidence={"inline_policy": inline_name},
            ))
            doc = iam.get_user_policy(UserName=name, PolicyName=inline_name)["PolicyDocument"]
            if isinstance(doc, str):
                doc = json.loads(unquote(doc))
            for f in analyse_policy_document(doc, "user", name, f"inline policy {inline_name}"):
                report.add(f)

        # --- group membership (IAM-011) -------------------------------------
        groups = iam.list_groups_for_user(UserName=name).get("Groups", [])
        if not groups:
            report.add(Finding(
                check_id="IAM-011",
                severity="LOW",
                title="User belongs to no group",
                resource_type="user",
                resource_name=name,
                detail="Permissions can only have been granted directly, which does not scale.",
                remediation="Add the user to a group that carries the appropriate policies.",
                evidence={"group_count": 0},
            ))

        # --- access keys (IAM-005, IAM-010) ---------------------------------
        for key in iam.list_access_keys(UserName=name).get("AccessKeyMetadata", []):
            key_id = key["AccessKeyId"]
            if key["Status"] != "Active":
                continue

            age = days_since(key.get("CreateDate"))
            if age is not None and age > max_key_age:
                report.add(Finding(
                    check_id="IAM-005",
                    severity="HIGH",
                    title=f"Access key older than {max_key_age} days",
                    resource_type="user",
                    resource_name=name,
                    detail=f"Key {key_id} is {age} days old.",
                    remediation="Rotate the key, or better: replace long-lived keys with "
                                "IAM roles / IAM Identity Center.",
                    evidence={"access_key_id": key_id, "age_days": age},
                ))

            last_used = iam.get_access_key_last_used(AccessKeyId=key_id).get("AccessKeyLastUsed", {})
            last_used_date = last_used.get("LastUsedDate")
            idle = days_since(last_used_date)

            if last_used_date is None:
                report.add(Finding(
                    check_id="IAM-010",
                    severity="MEDIUM",
                    title="Access key has never been used",
                    resource_type="user",
                    resource_name=name,
                    detail=f"Key {key_id} was created {age} days ago and has never made an API call.",
                    remediation="Delete it. An unused credential is pure attack surface.",
                    evidence={"access_key_id": key_id, "age_days": age},
                ))
            elif idle is not None and idle > 90:
                report.add(Finding(
                    check_id="IAM-010",
                    severity="MEDIUM",
                    title="Access key idle for over 90 days",
                    resource_type="user",
                    resource_name=name,
                    detail=f"Key {key_id} last used {idle} days ago "
                           f"({last_used.get('ServiceName', 'unknown service')}).",
                    remediation="Deactivate, wait for complaints, then delete.",
                    evidence={"access_key_id": key_id, "idle_days": idle},
                ))


def audit_roles(iam, report: AuditReport) -> None:
    """Roles: open trust policies, cross-account trust without ExternalId, inline policies."""
    roles = [r for r in paginate(iam, "list_roles", "Roles")
             if not r["Path"].startswith("/aws-service-role/")]  # service-linked roles are AWS-managed
    report.stats["roles"] = len(roles)

    for role in roles:
        name = role["RoleName"]
        trust = role.get("AssumeRolePolicyDocument", {})
        if isinstance(trust, str):
            trust = json.loads(unquote(trust))

        for stmt in normalise_to_list(trust.get("Statement")):
            if not isinstance(stmt, dict) or stmt.get("Effect") != "Allow":
                continue

            principal = stmt.get("Principal", {})
            conditions = stmt.get("Condition", {})
            condition_keys = {
                k.lower()
                for op in conditions.values() if isinstance(op, dict)
                for k in op
            }

            # Principal may be "*", {"AWS": "*"}, or {"AWS": ["*", ...]}
            aws_principals: list[str] = []
            if principal == "*":
                aws_principals = ["*"]
            elif isinstance(principal, dict):
                aws_principals = [str(p) for p in normalise_to_list(principal.get("AWS"))]

            # --- IAM-003 · wide-open trust ----------------------------------
            if "*" in aws_principals:
                severity = "HIGH" if conditions else "CRITICAL"
                report.add(Finding(
                    check_id="IAM-003",
                    severity=severity,
                    title="Role trust policy allows any AWS principal (Principal: *)",
                    resource_type="role",
                    resource_name=name,
                    detail="Any AWS account on earth can attempt to assume this role."
                           + (" Conditions are present, which limits it — review them carefully."
                              if conditions else " There are NO conditions."),
                    remediation="Restrict Principal to specific account or role ARNs. "
                                "If it must stay open, require sts:ExternalId and aws:PrincipalOrgID.",
                    evidence={"principal": principal, "conditions": list(condition_keys)},
                ))
                continue

            # --- IAM-012 · cross-account trust without ExternalId -----------
            for p in aws_principals:
                if ":" not in p:
                    continue
                parts = p.split(":")
                principal_account = parts[4] if len(parts) > 4 else ""
                if principal_account and principal_account != report.account_id:
                    if "sts:externalid" not in condition_keys:
                        report.add(Finding(
                            check_id="IAM-012",
                            severity="HIGH",
                            title="Cross-account trust without an ExternalId condition",
                            resource_type="role",
                            resource_name=name,
                            detail=f"Trusts external account {principal_account} with no sts:ExternalId. "
                                   f"This is the classic 'confused deputy' exposure.",
                            remediation="Add a Condition requiring StringEquals on sts:ExternalId, "
                                        "using a value agreed out-of-band with the third party.",
                            evidence={"external_account": principal_account, "principal": p},
                        ))

        # --- inline policies on roles (IAM-008) -----------------------------
        for inline_name in iam.list_role_policies(RoleName=name).get("PolicyNames", []):
            report.add(Finding(
                check_id="IAM-008",
                severity="MEDIUM",
                title="Inline policy attached to identity",
                resource_type="role",
                resource_name=name,
                detail=f"Inline policy '{inline_name}' on role.",
                remediation="Convert to a customer-managed policy.",
                evidence={"inline_policy": inline_name},
            ))
            doc = iam.get_role_policy(RoleName=name, PolicyName=inline_name)["PolicyDocument"]
            if isinstance(doc, str):
                doc = json.loads(unquote(doc))
            for f in analyse_policy_document(doc, "role", name, f"inline policy {inline_name}"):
                report.add(f)


def audit_policies(iam, report: AuditReport) -> None:
    """Every customer-managed policy in the account, attached or not."""
    policies = list(paginate(iam, "list_policies", "Policies", Scope="Local", OnlyAttached=False))
    report.stats["customer_managed_policies"] = len(policies)

    for policy in policies:
        arn = policy["Arn"]
        name = policy["PolicyName"]
        try:
            doc = get_policy_document(iam, arn, policy["DefaultVersionId"])
        except ClientError as exc:
            print(f"  ⚠️  could not read policy {name}: {exc.response['Error']['Code']}",
                  file=sys.stderr)
            continue

        context = "unattached" if policy.get("AttachmentCount", 0) == 0 else \
                  f"attached to {policy['AttachmentCount']} identity/identities"
        for f in analyse_policy_document(doc, "policy", name, context):
            f.evidence["policy_arn"] = arn
            f.evidence["attachment_count"] = policy.get("AttachmentCount", 0)
            report.add(f)


def audit_groups(iam, report: AuditReport) -> None:
    """Groups: inline policies and empty groups."""
    groups = list(paginate(iam, "list_groups", "Groups"))
    report.stats["groups"] = len(groups)

    for group in groups:
        name = group["GroupName"]
        for inline_name in iam.list_group_policies(GroupName=name).get("PolicyNames", []):
            report.add(Finding(
                check_id="IAM-008",
                severity="MEDIUM",
                title="Inline policy attached to identity",
                resource_type="group",
                resource_name=name,
                detail=f"Inline policy '{inline_name}' on group.",
                remediation="Convert to a customer-managed policy.",
                evidence={"inline_policy": inline_name},
            ))


def audit_account_settings(iam, report: AuditReport) -> None:
    """Account-wide controls: password policy strength."""
    try:
        policy = iam.get_account_password_policy()["PasswordPolicy"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchEntity":
            report.add(Finding(
                check_id="IAM-014",
                severity="HIGH",
                title="No account password policy configured",
                resource_type="account",
                resource_name=report.account_id,
                detail="The account uses AWS defaults, which permit weak passwords.",
                remediation="Apply a password policy: 14+ chars, complexity, 90-day max age, "
                            "reuse prevention 24. The Day 01 Terraform does this.",
                evidence={},
            ))
            return
        raise

    if policy.get("MinimumPasswordLength", 0) < 14:
        report.add(Finding(
            check_id="IAM-015",
            severity="MEDIUM",
            title="Password policy minimum length below 14 characters",
            resource_type="account",
            resource_name=report.account_id,
            detail=f"Minimum length is {policy.get('MinimumPasswordLength')}. CIS benchmark requires 14.",
            remediation="Set minimum_password_length = 14 or higher.",
            evidence={"minimum_password_length": policy.get("MinimumPasswordLength")},
        ))

    if not policy.get("MaxPasswordAge"):
        report.add(Finding(
            check_id="IAM-016",
            severity="LOW",
            title="Password policy does not enforce rotation",
            resource_type="account",
            resource_name=report.account_id,
            detail="Console passwords never expire.",
            remediation="Set max_password_age to 90 days.",
            evidence={},
        ))


# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────

def print_report(report: AuditReport, min_severity: str) -> None:
    threshold = SEVERITY_ORDER.index(min_severity)

    print()
    print("╔" + "═" * 74 + "╗")
    print("║" + "IAM SECURITY AUDIT  ·  CareerByteCode Bootcamp Day 01".center(74) + "║")
    print("╚" + "═" * 74 + "╝")
    print(f"Account : {report.account_id}")
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
            print(f"          Detail   : {f.detail}")
            print(f"          Fix      : {f.remediation}")

    print()
    print(" SUMMARY ".center(76, "═"))
    for severity in SEVERITY_ORDER:
        count = len(buckets[severity])
        if count or severity != "INFO":
            print(f"  {SEVERITY_ICON[severity]} {severity:<10} {count}")
    print("  " + "─" * 16)
    print(f"  TOTAL       {len(report.findings)}")
    print(f"  Security score: {report.score()}/100  (grade {report.grade()})")
    print()


def write_json(report: AuditReport, out_dir: Path, stamp: str) -> Path:
    path = out_dir / f"iam_audit_{stamp}.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, default=str))
    return path


def write_csv(report: AuditReport, out_dir: Path, stamp: str) -> Path:
    path = out_dir / f"iam_audit_{stamp}.csv"
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["check_id", "severity", "title", "resource_type",
                         "resource_name", "detail", "remediation"])
        order = {s: i for i, s in enumerate(SEVERITY_ORDER)}
        for f in sorted(report.findings, key=lambda x: order[x.severity]):
            writer.writerow([f.check_id, f.severity, f.title, f.resource_type,
                             f.resource_name, f.detail, f.remediation])
    return path


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Audit AWS IAM for least-privilege and credential-hygiene violations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--profile", default=os.environ.get("AWS_PROFILE"),
                   help="AWS CLI named profile (default: $AWS_PROFILE)")
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"),
                   help="AWS region (IAM is global, but STS needs one)")
    p.add_argument("--max-key-age", type=int, default=90,
                   help="Flag active access keys older than N days (default: 90)")
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

    # --- connect --------------------------------------------------------
    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        identity = session.client("sts").get_caller_identity()
        iam = session.client("iam")
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

    report = AuditReport(identity["Account"], identity["Arn"])

    if not args.quiet:
        print(f"\n🔍 Auditing account {report.account_id} ...")

    # --- run all sections ------------------------------------------------
    sections = [
        ("account settings", lambda: audit_account_settings(iam, report)),
        ("users", lambda: audit_users(iam, report, args.max_key_age)),
        ("groups", lambda: audit_groups(iam, report)),
        ("roles", lambda: audit_roles(iam, report)),
        ("policies", lambda: audit_policies(iam, report)),
    ]
    for label, fn in sections:
        try:
            if not args.quiet:
                print(f"   • scanning {label} ...")
            fn()
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation"):
                print(f"  ⚠️  skipped {label}: access denied. "
                      f"Attach the SecurityAudit policy to this identity.", file=sys.stderr)
            else:
                raise

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
