#!/usr/bin/env python3
"""
🎯 CHALLENGE — IAM Security Audit Tool
CareerByteCode AWS Cloud Architecture & AIOps Bootcamp · Day 01

Your job: implement the six TODO blocks below and make this tool find real problems.

The scaffolding (CLI parsing, AWS session, Finding model, report printing) is done for you.
The security logic is yours.

Run it:
    python3 iam_audit_challenge.py --profile bootcamp

Right now it runs cleanly and reports NOTHING, because every analysis function is empty.
That's the starting line.

────────────────────────────────────────────────────────────────────────────────
ORDER OF ATTACK  (do them in this order — each builds on the last)

  TODO 1  get_policy_document()      ~5 min   Fetch + decode a policy's JSON
  TODO 2  analyse_policy_document()  ~20 min  ⭐ The core rule engine
  TODO 3  audit_policies()           ~5 min   Loop every customer-managed policy
  TODO 4  audit_users()              ~20 min  MFA, key age, direct attachments
  TODO 5  audit_roles()              ~15 min  Trust policy analysis
  TODO 6  Stretch                    ~??      Add your own check

Target: after TODO 3 you should already be catching the deliberately-bad policy
that the Terraform created. That's your first win — go get it.
────────────────────────────────────────────────────────────────────────────────

If you get stuck for more than ~10 minutes on one function, open the solution at
../iam_audit.py, read ONLY that function, then come back. That's not cheating —
that's how engineers actually learn.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import unquote

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError, ProfileNotFound
except ImportError:
    sys.exit("boto3 is not installed. Run:  pip install boto3")


SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
SEVERITY_ICON = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵", "INFO": "⚪"}
SEVERITY_WEIGHT = {"CRITICAL": 25, "HIGH": 10, "MEDIUM": 4, "LOW": 1, "INFO": 0}

ADMIN_POLICY_ARNS = {
    "arn:aws:iam::aws:policy/AdministratorAccess",
    "arn:aws:iam::aws:policy/IAMFullAccess",
    "arn:aws:iam::aws:policy/PowerUserAccess",
}


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
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def resource(self) -> str:
        return f"{self.resource_type}/{self.resource_name}"


class AuditReport:
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

    def score(self) -> int:
        return max(0, 100 - sum(SEVERITY_WEIGHT[f.severity] for f in self.findings))


def days_since(dt: datetime | None) -> int | None:
    """Whole days between dt and now. None-safe."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days


def paginate(client, method: str, key: str, **kwargs) -> Iterable[dict]:
    """
    Yield every item from a paginated IAM API.

    Use this instead of calling list_users() directly — IAM returns max 100 items
    per call, and forgetting to paginate is the #1 bug in home-grown audit scripts.

        for user in paginate(iam, "list_users", "Users"):
            print(user["UserName"])
    """
    for page in client.get_paginator(method).paginate(**kwargs):
        yield from page.get(key, [])


def normalise_to_list(value: Any) -> list:
    """
    IAM JSON fields are sometimes a string, sometimes a list. Always give me a list.

        "Action": "s3:GetObject"          → ["s3:GetObject"]
        "Action": ["s3:Get*", "s3:List*"] → ["s3:Get*", "s3:List*"]
        missing                           → []

    Use this on Statement, Action and Resource. Every time. Without it your tool
    will crash on real-world policies.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def print_report(report: AuditReport) -> None:
    print()
    print("╔" + "═" * 74 + "╗")
    print("║" + "IAM SECURITY AUDIT (challenge build)".center(74) + "║")
    print("╚" + "═" * 74 + "╝")
    print(f"Account : {report.account_id}")
    print(f"Identity: {report.identity_arn}")
    print(f"Scanned : {report.scanned_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    if report.stats:
        print("\n  " + " | ".join(f"{k.title()}: {v}" for k, v in report.stats.items()))

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
        print("\n  😴 Zero findings. Either your account is perfect, or your TODOs are empty.")
        print("     (It's the TODOs.)")
    print()


# ═════════════════════════════════════════════════════════════════════════════
# YOUR WORK STARTS HERE
# ═════════════════════════════════════════════════════════════════════════════

def get_policy_document(iam, policy_arn: str, version_id: str) -> dict:
    """
    TODO 1 ── Fetch a managed policy's JSON document and return it as a dict.

    Steps:
      1. Call iam.get_policy_version(PolicyArn=..., VersionId=...)
      2. Pull out response["PolicyVersion"]["Document"]
      3. ⚠️ THE TRAP: that Document is sometimes a dict (newer botocore auto-decodes)
         and sometimes a URL-encoded JSON *string* (older botocore).
         Handle both:
             if isinstance(doc, str):
                 doc = json.loads(unquote(doc))
      4. Return the dict.

    Test it standalone:
        python3 -c "import boto3;print(boto3.Session(profile_name='bootcamp').client('iam').get_policy_version(PolicyArn='<arn>',VersionId='v1'))"
    """
    # YOUR CODE HERE
    return {}


def analyse_policy_document(doc: dict, resource_type: str, resource_name: str,
                            context: str = "") -> list[Finding]:
    """
    TODO 2 ⭐ ── THE CORE RULE ENGINE. This is the heart of the tool.

    Walk every statement in the policy and return a list of Finding objects.

    Skeleton:
        findings = []
        for stmt in normalise_to_list(doc.get("Statement")):
            if not isinstance(stmt, dict):
                continue
            if stmt.get("Effect") != "Allow":
                continue          # ← Deny statements NARROW permissions. Never a finding.

            sid       = stmt.get("Sid", "<no Sid>")
            actions   = [str(a) for a in normalise_to_list(stmt.get("Action"))]
            resources = [str(r) for r in normalise_to_list(stmt.get("Resource"))]
            has_condition = bool(stmt.get("Condition"))
            ...
        return findings

    Rules to implement:

      IAM-002 · CRITICAL — full admin
        WHEN: "*" in actions AND "*" in resources
        TITLE: "Policy grants full administrative access (Action:* on Resource:*)"
        Then `continue` — no point reporting lesser issues on the same statement.

      IAM-006 · HIGH — service-wide wildcard
        WHEN: any action ends with ":*"  (e.g. "s3:*", "iam:*")  AND "*" in resources
        HINT: service_wildcards = [a for a in actions if a.endswith(":*")]

      IAM-007 · MEDIUM — unscoped resource
        WHEN: "*" in resources, but NOT the two cases above, AND there is no Condition
        WHY the no-Condition part: a Condition like aws:ResourceTag or
        aws:RequestedRegion can make Resource:* perfectly reasonable.

    Every Finding needs a real `remediation` string — write advice you'd actually
    want to receive. "Fix the policy" helps nobody.
    """
    findings: list[Finding] = []
    # YOUR CODE HERE
    return findings


def audit_policies(iam, report: AuditReport) -> None:
    """
    TODO 3 ── Scan every customer-managed policy in the account.

    Steps:
      1. policies = list(paginate(iam, "list_policies", "Policies",
                                  Scope="Local", OnlyAttached=False))
         • Scope="Local"    → customer-managed only. Without this you get ~1000
                              AWS-managed policies and a very slow, very noisy run.
         • OnlyAttached=False → include unattached ones. An unattached bad policy
                              is still a bad policy — someone will attach it.
      2. report.stats["policies"] = len(policies)
      3. For each: call get_policy_document(iam, p["Arn"], p["DefaultVersionId"])
      4. Feed it to analyse_policy_document(doc, "policy", p["PolicyName"])
      5. report.add(f) for each finding

    ✅ CHECKPOINT: after this TODO, run the tool. You should catch
       `cbc-day01-BAD-example-policy` as CRITICAL. First blood.
    """
    # YOUR CODE HERE
    pass


def audit_users(iam, report: AuditReport, max_key_age: int) -> None:
    """
    TODO 4 ── Audit IAM users.

    For each user from paginate(iam, "list_users", "Users"):

      IAM-004 · HIGH — console user without MFA
        • iam.get_login_profile(UserName=name) raises ClientError with
          Error.Code == "NoSuchEntity" when the user has NO console password.
          Catch it — that's normal, not an error:
              try:
                  iam.get_login_profile(UserName=name)
                  has_console = True
              except ClientError as exc:
                  if exc.response["Error"]["Code"] == "NoSuchEntity":
                      has_console = False
                  else:
                      raise
        • iam.list_mfa_devices(UserName=name)["MFADevices"]
        • Flag when has_console is True and the device list is empty.

      IAM-001 · CRITICAL — admin policy attached
        • iam.list_attached_user_policies(UserName=name)["AttachedPolicies"]
        • Flag any whose PolicyArn is in ADMIN_POLICY_ARNS.

      IAM-009 · MEDIUM — any policy attached directly to a user
        • Permissions belong on groups (humans) or roles (workloads).

      IAM-005 · HIGH — access key older than max_key_age days
        • iam.list_access_keys(UserName=name)["AccessKeyMetadata"]
        • Skip keys where Status != "Active"
        • age = days_since(key["CreateDate"])

      IAM-010 · MEDIUM — key never used, or idle 90+ days
        • iam.get_access_key_last_used(AccessKeyId=key_id)["AccessKeyLastUsed"]
        • A missing "LastUsedDate" means the key has NEVER been used. Delete it.

      IAM-011 · LOW — user belongs to no group
        • iam.list_groups_for_user(UserName=name)["Groups"]

    Don't forget: report.stats["users"] = len(users)
    """
    # YOUR CODE HERE
    pass


def audit_roles(iam, report: AuditReport) -> None:
    """
    TODO 5 ── Audit IAM roles, focusing on TRUST policies.

    A role's trust policy is role["AssumeRolePolicyDocument"] — the thing that says
    WHO may assume it. This is where the scariest misconfigurations live.

    First, filter out AWS's own service-linked roles or you'll drown in noise:
        roles = [r for r in paginate(iam, "list_roles", "Roles")
                 if not r["Path"].startswith("/aws-service-role/")]

    Then for each role, decode the trust doc (same string-vs-dict trap as TODO 1)
    and walk its statements:

      IAM-003 · CRITICAL — Principal is "*"
        The Principal field is annoyingly polymorphic:
            "Principal": "*"
            "Principal": {"AWS": "*"}
            "Principal": {"AWS": ["*", "arn:aws:iam::111122223333:root"]}
            "Principal": {"Service": "ec2.amazonaws.com"}      ← fine, not a finding
        Normalise it, then check for "*" among the AWS principals.
        Downgrade to HIGH if the statement HAS conditions — conditions may make it
        legitimate, but a human still needs to look.

      IAM-012 · HIGH — cross-account trust with no ExternalId
        • Extract the account ID from an ARN: arn.split(":")[4]
        • If it differs from report.account_id, it's cross-account.
        • Look for "sts:externalid" among the lowercased Condition keys.
        • Missing it = confused-deputy exposure. Explain that in the detail text.

    ✅ CHECKPOINT: `cbc-day01-BAD-open-trust-role` should light up CRITICAL.
    """
    # YOUR CODE HERE
    pass


# ═════════════════════════════════════════════════════════════════════════════
# TODO 6 (stretch) — pick one or more and implement it below
#
#   IAM-008  Inline policies on users/roles/groups          MEDIUM
#            iam.list_user_policies / list_role_policies / list_group_policies
#            Then run analyse_policy_document() on the inline doc too.
#
#   IAM-013  iam:PassRole on Resource:*                     HIGH
#            The classic privilege-escalation path: pass a powerful role to a
#            service you control, and the service does what you can't.
#
#   IAM-014  No account password policy at all              HIGH
#            iam.get_account_password_policy() → NoSuchEntity means none exists.
#
#   IAM-017  Root account access keys exist                 CRITICAL
#            iam.get_account_summary()["SummaryMap"]["AccountAccessKeysPresent"]
#
#   --format html   Write a styled HTML report you'd be happy to email a manager.
#   --ignore-file   Suppress known-accepted findings by check_id + resource.
# ═════════════════════════════════════════════════════════════════════════════


# ═════════════════════════════════════════════════════════════════════════════
# GIVEN TO YOU — entry point
# ═════════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(description="🎯 CHALLENGE: IAM Security Audit Tool")
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--max-key-age", type=int, default=90)
    args = parser.parse_args()

    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        identity = session.client("sts").get_caller_identity()
        iam = session.client("iam")
    except ProfileNotFound:
        print(f"❌ Profile '{args.profile}' not found.", file=sys.stderr)
        return 2
    except NoCredentialsError:
        print("❌ No AWS credentials. Set AWS_PROFILE or pass --profile.", file=sys.stderr)
        return 2

    report = AuditReport(identity["Account"], identity["Arn"])
    print(f"\n🔍 Auditing account {report.account_id} ...")

    for label, fn in [
        ("users", lambda: audit_users(iam, report, args.max_key_age)),
        ("roles", lambda: audit_roles(iam, report)),
        ("policies", lambda: audit_policies(iam, report)),
    ]:
        try:
            print(f"   • scanning {label} ...")
            fn()
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in ("AccessDenied", "AccessDeniedException"):
                print(f"  ⚠️  skipped {label}: access denied.", file=sys.stderr)
            else:
                raise

    print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
