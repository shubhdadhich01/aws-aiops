#!/usr/bin/env python3
"""
AWS IAM Security + AIOps Audit — CareerByteCode Bootcamp Day 01

Workflow:
    AWS IAM -> deterministic security rules -> findings -> risk score
            -> optional anomaly detection -> optional Ollama GenAI analysis

The IAM checks remain deterministic security controls.
The optional historical section provides statistical anomaly detection.
The optional Ollama/Llama 3.2 section is the GenAI investigation and triage layer;
it receives sanitized findings and never changes AWS resources.

TEACHING MODEL:
    DevSecOps  = deterministic IAM security rules
    AIOps      = historical anomaly detection + incident triage
    GenAI      = Llama 3.2:1b reasoning over the resulting incident context
"""
from __future__ import annotations

# =============================================================================
# 1. IMPORTS
# =============================================================================
# Standard library: CLI, reports, dates, files, JSON and statistics.
# boto3: AWS API access. Ollama is called through its local HTTP API, so no
# additional Python SDK is required.
# argparse      -> command-line arguments such as --profile and --ai
# csv/json      -> machine-readable audit reports
# os            -> read AWS/Ollama settings from environment variables
# urllib        -> call Ollama's local HTTP API without another Python dependency
import argparse, csv, json, os, sys, urllib.request, urllib.error
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, stdev
from typing import Any
from urllib.parse import unquote

# boto3 is the AWS SDK used to call IAM and STS APIs from Python.
import boto3
from botocore.exceptions import ClientError, NoCredentialsError, ProfileNotFound

# =============================================================================
# 2. SECURITY RULE CONFIGURATION
# =============================================================================
# These are the deterministic controls used by the IAM audit.
# They are intentionally explicit so students can see exactly why a finding was raised.
SEVERITY = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
WEIGHT = {"CRITICAL": 25, "HIGH": 10, "MEDIUM": 4, "LOW": 1, "INFO": 0}
# Known AWS-managed administrator-level policies.
# Matching by ARN avoids relying only on a human-readable policy name.
ADMIN = {
    "arn:aws:iam::aws:policy/AdministratorAccess",
    "arn:aws:iam::aws:policy/IAMFullAccess",
    "arn:aws:iam::aws:policy/PowerUserAccess",
}
# IAM actions that can participate in privilege escalation.
# This is a teaching rule set, not an exhaustive IAM threat-detection database.
ESCALATION = {
    "iam:passrole", "iam:createpolicyversion", "iam:setdefaultpolicyversion",
    "iam:attachuserpolicy", "iam:attachrolepolicy", "iam:attachgrouppolicy",
    "iam:putuserpolicy", "iam:putrolepolicy", "iam:putgrouppolicy",
    "iam:createaccesskey", "iam:updateassumerolepolicy", "sts:assumerole",
}

# =============================================================================
# 3. FINDING + REPORT MODELS
# =============================================================================
# A Finding is the common object produced by every security rule.
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
    def resource(self):
        return f"{self.resource_type}/{self.resource_name}"

class Report:
    """Collect findings, score the account, and hold optional AIOps results."""
    def __init__(self, account, identity):
        self.account_id, self.identity_arn = account, identity
        self.scanned_at = datetime.now(timezone.utc)
        self.findings, self.stats = [], {}
        self.anomaly = None
        self.ai_summary = None

    def add(self, *args, **kwargs):
        self.findings.append(Finding(*args, **kwargs))

    def counts(self):
        out = {s: 0 for s in SEVERITY}
        for f in self.findings:
            out[f.severity] += 1
        return out

    def score(self):
        return max(0, 100 - sum(WEIGHT[f.severity] for f in self.findings))

    def grade(self):
        s = self.score()
        return "A" if s >= 90 else "B" if s >= 80 else "C" if s >= 70 else "D" if s >= 60 else "F"

    def as_dict(self):
        return {
            "account_id": self.account_id,
            "identity_arn": self.identity_arn,
            "scanned_at": self.scanned_at.isoformat(),
            "statistics": self.stats,
            "summary": {"counts": self.counts(), "total": len(self.findings), "score": self.score(), "grade": self.grade()},
            "anomaly": self.anomaly,
            "ai_summary": self.ai_summary,
            "findings": [asdict(f) for f in self.findings],
        }

# =============================================================================
# 4. SMALL HELPERS
# =============================================================================
def add(r, check, severity, title, kind, name, detail, fix, evidence=None):
    # Most checks create a Finding with the same fields. This helper keeps that
    # repetitive object construction in one place without changing the finding model.
    r.add(check, severity, title, kind, name, detail, fix, evidence or {})

# Paginate automatically so the audit does not silently stop at the first page.
# IAM list APIs can return multiple pages. This helper hides that AWS API detail
# from the individual audit functions, so those functions stay focused on security logic.
def items(client, method, key, **kwargs):
    if client.can_paginate(method):
        for page in client.get_paginator(method).paginate(**kwargs):
            yield from page.get(key, [])
    else:
        yield from getattr(client, method)(**kwargs).get(key, [])

def one(value):
    # IAM policy JSON is inconsistent: a field can be either one string/object
    # or a list. Normalizing both forms to a list lets the rule engine use one loop.
    return [] if value is None else value if isinstance(value, list) else [value]

def age(dt):
    # Convert an AWS timestamp into a simple number of days.
    # This is used for access-key hygiene checks such as "older than 90 days".
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days

def decode(doc):
    # IAM sometimes returns policy documents URL-encoded as strings.
    # Newer clients may already return a Python dict, so accept both forms.
    return json.loads(unquote(doc)) if isinstance(doc, str) else doc

def add_inline(iam, r, kind, name, list_method, get_method):
    """Check inline policies and pass their documents to the rule engine."""
    names = list(items(iam, list_method, "PolicyNames", **{kind.title() + "Name": name}))
    for policy in names:
        add(r, "IAM-008", "MEDIUM", "Inline policy attached to identity", kind, name,
            f"Inline policy '{policy}' is attached to the {kind}.",
            "Convert the inline policy to a customer-managed policy.", {"inline_policy": policy})
        doc = getattr(iam, get_method)(**{kind.title() + "Name": name, "PolicyName": policy})["PolicyDocument"]
        analyze_policy(r, decode(doc), kind, name, f"inline policy {policy}")

# =============================================================================
# 5. POLICY ANALYSIS — DETERMINISTIC SECURITY ENGINE
# =============================================================================
# Rules: Action:* + Resource:* -> CRITICAL; service:* + Resource:* -> HIGH;
# privilege-escalation actions + Resource:* -> HIGH; broad Resource:* -> MEDIUM.
def analyze_policy(r, doc, kind, name, context=""):

    # Examine every statement in the policy independently.
    # We skip Deny statements because they restrict permissions rather than grant them.
    for stmt in one(doc.get("Statement")):
        if not isinstance(stmt, dict) or stmt.get("Effect") != "Allow":
            continue
        sid = stmt.get("Sid", "<no Sid>")
        # Pull the three most important policy fields into simple Python values.
        actions = [str(x) for x in one(stmt.get("Action"))]
        resources = [str(x) for x in one(stmt.get("Resource"))]
        a_star, r_star = "*" in actions, "*" in resources

        # Examples such as s3:* or iam:* are broader than individual API actions.
        service_wild = [a for a in actions if a.endswith(":*")]

        # These actions can enable privilege escalation when they are broadly scoped.
        escalation = {a.lower() for a in actions} & ESCALATION

        # Conditions can reduce the blast radius of Resource="*".
        condition = bool(stmt.get("Condition"))
        suffix = f" ({context})" if context else ""
        # RULE 1: full administrative access.
        # This is the clearest deterministic security rule in the audit:
        # every AWS action + every AWS resource = administrator-level capability.
        if a_star and r_star:
            add(r, "IAM-002", "CRITICAL", "Policy grants full administrative access", kind, name,
                f"statement '{sid}' allows Action=* on Resource=*{suffix}",
                "Scope actions and resources to the minimum required.", {"sid": sid, "actions": actions, "resources": resources})
            continue
        raised = False
        # RULE 2: service-wide permissions on every resource.
        # Example: s3:* on Resource=* is broad, but narrower than Action=* on *.
        if service_wild and r_star:
            raised = True
            add(r, "IAM-006", "HIGH", "Policy grants service-wide wildcard actions", kind, name,
                f"statement '{sid}' allows {', '.join(service_wild)} on Resource=*{suffix}",
                "Replace service:* with specific operations.", {"sid": sid, "wildcard_actions": service_wild})
        # RULE 3: privilege-escalation capability is dangerous when unscoped.
        # Example: iam:PassRole on Resource=* can enable a caller to abuse other roles.
        if escalation and r_star:
            raised = True
            add(r, "IAM-013", "HIGH", "Policy allows privilege-escalation actions", kind, name,
                f"statement '{sid}' allows {', '.join(sorted(escalation))} on Resource=*{suffix}",
                "Scope escalation actions to explicit resources and conditions.", {"sid": sid, "escalation_actions": sorted(escalation)})
        # RULE 4: Resource=* without a condition is broader than necessary for many use cases.
        # Do not trigger this if a stronger finding has already been raised for the same statement.
        if r_star and not a_star and not condition and not raised:
            add(r, "IAM-007", "MEDIUM", "Policy statement grants access to all resources", kind, name,
                f"statement '{sid}' uses Resource=* with no Condition{suffix}",
                "Scope the resource to explicit ARNs or add a condition.", {"sid": sid, "actions": actions[:10]})

# =============================================================================
# 6. IAM USERS + CREDENTIAL HYGIENE
# =============================================================================
# Checks MFA, admin policies, direct attachments, groups and access-key age/use.
def audit_users(iam, r, max_age):
    # One user can generate multiple independent findings.
    # We intentionally keep user inspection separate from role/policy inspection
    # so the classroom can teach each IAM object independently.
    users = list(items(iam, "list_users", "Users"))
    r.stats["users"] = len(users)
    for u in users:
        name = u["UserName"]
        # get_login_profile succeeds only when the user has a console password.
        # A programmatic-only IAM user can legitimately have no login profile.
        try:
            iam.get_login_profile(UserName=name)
            console = True
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchEntity": raise
            console = False
        if console and not iam.list_mfa_devices(UserName=name).get("MFADevices"):
            add(r, "IAM-004", "HIGH", "Console user without MFA", "user", name,
                "User has console access but no MFA device.", "Register MFA and enforce MFA for console access.")
        attached = list(items(iam, "list_attached_user_policies", "AttachedPolicies", UserName=name))
        for p in attached:
            if p["PolicyArn"] in ADMIN:
                add(r, "IAM-001", "CRITICAL", "User has administrator-level policy attached", "user", name,
                    f"{p['PolicyArn']} is attached directly to the user.", "Move privileged access behind a role and require MFA.", {"policy_arn": p["PolicyArn"]})
        if attached:
            add(r, "IAM-009", "MEDIUM", "Policy attached directly to a user", "user", name,
                f"{len(attached)} managed policy/policies are attached directly.", "Use groups for humans and roles for workloads.", {"policies": [p["PolicyName"] for p in attached]})
        add_inline(iam, r, "user", name, "list_user_policies", "get_user_policy")
        if not iam.list_groups_for_user(UserName=name).get("Groups"):
            add(r, "IAM-011", "LOW", "User belongs to no group", "user", name,
                "The user has no group membership.", "Use groups to manage human-user permissions.")
        # Access-key hygiene is deliberately time-based: old or unused long-lived
        # credentials increase the attack surface even when they are still active.
        for k in iam.list_access_keys(UserName=name).get("AccessKeyMetadata", []):
            if k["Status"] != "Active": continue
            created = age(k.get("CreateDate"))
            if created and created > max_age:
                add(r, "IAM-005", "HIGH", f"Access key older than {max_age} days", "user", name,
                    f"An active access key is {created} days old.", "Rotate it or replace long-lived credentials with roles.", {"age_days": created})
            used = iam.get_access_key_last_used(AccessKeyId=k["AccessKeyId"]).get("AccessKeyLastUsed", {})
            idle = age(used.get("LastUsedDate"))
            if not used.get("LastUsedDate"):
                add(r, "IAM-010", "MEDIUM", "Access key has never been used", "user", name,
                    f"An active key created {created or 'unknown'} days ago has no recorded use.", "Disable/delete the unused key.", {"age_days": created})
            elif idle and idle > 90:
                add(r, "IAM-010", "MEDIUM", "Access key idle for over 90 days", "user", name,
                    f"The active key has been idle for {idle} days.", "Deactivate it, validate dependencies, then delete it.", {"idle_days": idle, "service": used.get("ServiceName", "unknown")})

# =============================================================================
# 7. IAM ROLES + TRUST POLICIES
# =============================================================================
# Permission policy = what the role can do. Trust policy = who can assume it.
def audit_roles(iam, r):
    # AWS service-linked roles are managed by AWS and are intentionally excluded.
    # We focus on customer/application roles where trust configuration is reviewable.
    roles = [x for x in items(iam, "list_roles", "Roles") if not x["Path"].startswith("/aws-service-role/")]
    r.stats["roles"] = len(roles)
    for role in roles:
        name, trust = role["RoleName"], decode(role.get("AssumeRolePolicyDocument", {}))
        for stmt in one(trust.get("Statement")):
            if not isinstance(stmt, dict) or stmt.get("Effect") != "Allow": continue
            principal, cond = stmt.get("Principal", {}), stmt.get("Condition", {}) or {}
            # The trust policy answers: "WHO can assume this role?"
            # Principal:* is intentionally treated as a high-risk condition.
            if principal == "*": aws_principals = ["*"]
            elif isinstance(principal, dict): aws_principals = [str(x) for x in one(principal.get("AWS"))]
            else: aws_principals = []
            if "*" in aws_principals:
                add(r, "IAM-003", "HIGH" if cond else "CRITICAL", "Role trust policy allows any AWS principal", "role", name,
                    "The trust policy contains Principal: *.", "Restrict the principal; use conditions for legitimate cross-account access.", {"principal": principal})
                continue
            cond_keys = {str(k).lower() for op in cond.values() if isinstance(op, dict) for k in op}
            for p in aws_principals:
                parts = p.split(":"); account = parts[4] if len(parts) > 4 else ""
                if account and account != r.account_id and "sts:externalid" not in cond_keys:
                    add(r, "IAM-012", "HIGH", "Cross-account trust without ExternalId", "role", name,
                        f"Trusts external account {account} without sts:ExternalId.", "Require an ExternalId for third-party cross-account access.", {"external_account": account})
        add_inline(iam, r, "role", name, "list_role_policies", "get_role_policy")

# =============================================================================
# 8. CUSTOMER-MANAGED POLICIES
# =============================================================================
# Scan attached and unattached local policies so training can detect the
# deliberately bad Terraform policy even when it is not attached.
def audit_policies(iam, r):
    # Scan both attached AND unattached local policies.
    # The Terraform training environment deliberately creates an unattached bad policy,
    # so scanning only attached policies would miss the intended classroom finding.
    policies = list(items(iam, "list_policies", "Policies", Scope="Local", OnlyAttached=False))
    r.stats["customer_managed_policies"] = len(policies)
    for p in policies:
        try:
            doc = iam.get_policy_version(PolicyArn=p["Arn"], VersionId=p["DefaultVersionId"])["PolicyVersion"]["Document"]
            context = "unattached" if not p.get("AttachmentCount") else f"attached to {p['AttachmentCount']} identity/identities"
            before = len(r.findings)
            analyze_policy(r, decode(doc), "policy", p["PolicyName"], context)
            for f in r.findings[before:]:
                f.evidence.update({"policy_arn": p["Arn"], "attachment_count": p.get("AttachmentCount", 0)})
        except ClientError as e:
            print(f"Warning: could not read policy {p['PolicyName']}: {e.response['Error']['Code']}", file=sys.stderr)

# =============================================================================
# 9. IAM GROUPS + ACCOUNT PASSWORD POLICY
# =============================================================================
def audit_groups(iam, r):
    groups = list(items(iam, "list_groups", "Groups")); r.stats["groups"] = len(groups)
    for g in groups:
        add_inline(iam, r, "group", g["GroupName"], "list_group_policies", "get_group_policy")

def audit_account(iam, r):
    # Account password policy is a singleton AWS-level control, not a per-user resource.
    try: p = iam.get_account_password_policy()["PasswordPolicy"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            add(r, "IAM-014", "HIGH", "No account password policy configured", "account", r.account_id,
                "The account uses AWS defaults.", "Configure a strong IAM password policy."); return
        raise
    minimum = p.get("MinimumPasswordLength", 0)
    if minimum < 14:
        add(r, "IAM-015", "MEDIUM", "Password policy minimum length below 14", "account", r.account_id,
            f"Minimum password length is {minimum}.", "Set minimum_password_length to 14 or higher.", {"minimum_password_length": minimum})
    if not p.get("MaxPasswordAge"):
        add(r, "IAM-016", "LOW", "Password policy does not enforce rotation", "account", r.account_id,
            "Console passwords do not expire.", "Set an appropriate password-age limit.")

# =============================================================================
# 10. AIOPS — HISTORICAL ANOMALY DETECTION
# =============================================================================
# This is a statistical baseline, not ML. It answers: "Did the IAM posture
# change unusually compared with recent audit runs?"
def audit_metrics(r):
    # Convert the audit into a small time-series. These metrics are what the
    # historical anomaly detector compares across repeated audit runs.
    c = r.counts()
    return {"score": r.score(), "total": len(r.findings), "critical": c["CRITICAL"], "high": c["HIGH"], "medium": c["MEDIUM"]}

def anomaly_check(r, path):
    # This is statistical anomaly detection, not a trained ML model.
    # We establish a simple historical baseline and flag unusually large deviations.
    try: history = json.loads(path.read_text()) if path.exists() else []
    except (OSError, json.JSONDecodeError): history = []
    if len(history) >= 3:
        current, signals = audit_metrics(r), []
        for key in ("score", "critical", "high", "total"):
            vals = [float(x.get("metrics", {}).get(key, 0)) for x in history]
            avg, sd, value = mean(vals), stdev(vals), current[key]
            bad = value < avg - max(5, 2 * sd) if key == "score" else value > avg + max(1, 2 * sd)
            if bad: signals.append({"metric": key, "current": value, "baseline_mean": round(avg, 2), "baseline_stddev": round(sd, 2)})
        r.anomaly = {"status": "anomalous" if signals else "normal", "signals": signals}
    else:
        r.anomaly = {"status": "baseline_unavailable", "message": "Run at least 3 previous audits first."}
    path.parent.mkdir(parents=True, exist_ok=True)
    history.append({"timestamp": r.scanned_at.isoformat(), "metrics": audit_metrics(r)})
    path.write_text(json.dumps(history[-30:], indent=2))

# =============================================================================
# 11. AIOPS — OLLAMA GENAI TRIAGE
# =============================================================================
# The LLM receives sanitized findings only. It correlates, prioritizes and
# proposes investigation/remediation order; it does not modify AWS.
def ai_triage(r, ollama_url, model_id):
    """Use Llama only for incident analysis, not security detection."""
    # Keep the local LLM workload small. The Python rule engine remains the source
    # of truth; Llama explains and prioritizes only the two most important findings.
    important = [
        {
            "check": f.check_id,
            "severity": f.severity,
            "type": f.resource_type,
            "resource": f.resource_name,
            "problem": f.detail[:180],
            "fix": f.remediation[:180],
        }
        for f in r.findings
        if f.severity in {"CRITICAL", "HIGH"}
    ][:2]

    if not important:
        return {
            "status": "no_ai_triage_needed",
            "incident_summary": "No CRITICAL or HIGH findings were detected.",
            "overall_priority": "LOW",
            "findings": [],
            "investigation_order": [],
            "remediation_plan": [],
        }

    # Short prompt: preserve exact resources, explain impact, and give a fix.
    prompt = f"""You are an AWS IAM incident analyst.
The audit engine already detected the findings. Do not change their severity or invent resources.
For each finding, explain the problem and give one concrete fix. Then give the investigation order.
Return JSON only.

FINDINGS:
{json.dumps(important, separators=(',', ':'))}"""

    schema = {
        "type": "object",
        "properties": {
            "incident_summary": {"type": "string"},
            "overall_priority": {
                "type": "string",
                "enum": ["CRITICAL", "HIGH", "LOW"],
            },
            "findings": {
                "type": "array",
                "maxItems": 2,
                "items": {
                    "type": "object",
                    "properties": {
                        "check": {"type": "string"},
                        "resource": {"type": "string"},
                        "problem": {"type": "string"},
                        "solution": {"type": "string"},
                    },
                    "required": ["check", "resource", "problem", "solution"],
                },
            },
            "investigation_order": {
                "type": "array",
                "maxItems": 2,
                "items": {"type": "string"},
            },
            "remediation_plan": {
                "type": "array",
                "maxItems": 2,
                "items": {"type": "string"},
            },
        },
        "required": [
            "incident_summary",
            "overall_priority",
            "findings",
            "investigation_order",
            "remediation_plan",
        ],
    }

    payload = json.dumps({
        "model": model_id,
        "stream": False,
        "think": False,
        "format": schema,
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": 0, "num_predict": 100},
    }).encode("utf-8")

    request = urllib.request.Request(
        f"{ollama_url.rstrip('/')}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(
            f"Ollama returned HTTP {exc.code} from {ollama_url}: {body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not connect to Ollama at {ollama_url}: {exc}"
        ) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError(
            "Ollama inference exceeded 60 seconds; deterministic audit results remain valid."
        ) from exc

    content = result.get("message", {}).get("content", "")
    if not content:
        raise RuntimeError("Ollama returned an empty AI response")

    try:
        ai_result = json.loads(content)
    except json.JSONDecodeError:
        return {"status": "unstructured_response", "raw_response": content}

    # Re-apply source-of-truth resource/severity from the deterministic engine.
    expected = {
        f.check_id: f
        for f in r.findings
        if f.severity in {"CRITICAL", "HIGH"}
    }
    for item in ai_result.get("findings", []):
        source = expected.get(item.get("check"))
        if source:
            item["resource"] = source.resource_name
            item["severity"] = source.severity
            item["resource_type"] = source.resource_type

    ai_result["status"] = "ok"
    return ai_result

# =============================================================================
# 12. REPORTING
# =============================================================================
def print_report(r, min_severity):
    # Human-readable output for the classroom and terminal.
    # JSON/CSV below are intended for automation and downstream tooling.
    limit = SEVERITY.index(min_severity)
    print("\n" + "=" * 76); print("IAM SECURITY + AIOPS AUDIT".center(76)); print("=" * 76)
    print(f"Account : {r.account_id}\nIdentity: {r.identity_arn}\nScore   : {r.score()}/100 ({r.grade()})")
    print("  " + " | ".join(f"{k}: {v}" for k, v in r.stats.items()))
    for s in SEVERITY:
        if not r.counts()[s] or SEVERITY.index(s) > limit: continue
        print(f"\n--- {s} ({r.counts()[s]}) ---")
        for f in r.findings:
            if f.severity == s: print(f"[{f.check_id}] {f.title}\n  {f.resource}\n  {f.detail}\n  Fix: {f.remediation}\n")
    if r.anomaly: print("--- AIOPS ANOMALY ---\n" + json.dumps(r.anomaly, indent=2))
    if r.ai_summary:
        print("\n--- AIOPS GENAI TRIAGE ---")
        print(json.dumps(r.ai_summary, indent=2, default=str) if isinstance(r.ai_summary, dict) else r.ai_summary)

def write_reports(r, out_dir, fmt):
    # Machine-readable reports make the audit useful outside the terminal:
    # JSON can feed automation; CSV can be inspected in spreadsheets.
    out_dir.mkdir(parents=True, exist_ok=True); stamp = r.scanned_at.strftime("%Y%m%d_%H%M%S")
    written = []
    if fmt in {"json", "all"}:
        p = out_dir / f"iam_aiops_{stamp}.json"; p.write_text(json.dumps(r.as_dict(), indent=2, default=str)); written.append(p)
    if fmt in {"csv", "all"}:
        p = out_dir / f"iam_aiops_{stamp}.csv"
        with p.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh); w.writerow(["check_id", "severity", "title", "resource", "detail", "remediation"])
            order = {s: i for i, s in enumerate(SEVERITY)}
            for f in sorted(r.findings, key=lambda x: (order[x.severity], x.check_id)): w.writerow([f.check_id, f.severity, f.title, f.resource, f.detail, f.remediation])
        written.append(p)
    return written

# =============================================================================
# 13. CLI + MAIN ORCHESTRATOR
# --anomaly enables the historical detector; --ai enables the local Ollama triage layer.
# =============================================================================
# main() only wires the sections together. The audit logic stays in the
# smaller functions above so each section can be taught independently.
def main():
    # main() is intentionally orchestration-only. It should be easy for students
    # to read this function as a workflow without digging through every audit rule.
    p = argparse.ArgumentParser(description="AWS IAM security audit with optional AIOps analysis")
    p.add_argument("--profile", default=os.getenv("AWS_PROFILE")); p.add_argument("--region", default=os.getenv("AWS_REGION", "ap-south-1"))
    p.add_argument("--max-key-age", type=int, default=90); p.add_argument("--min-severity", choices=SEVERITY, default="LOW")
    p.add_argument("--format", choices=["table", "json", "csv", "all"], default="table"); p.add_argument("--output-dir", default="reports")
    p.add_argument("--anomaly", action="store_true"); p.add_argument("--history-file", default="reports/iam_history.json")
    p.add_argument("--ai", action="store_true"); p.add_argument("--model-id", default="llama3.2:1b"); p.add_argument("--ollama-url", default=os.getenv("OLLAMA_URL", "http://localhost:11434")); p.add_argument("--fail-on", choices=SEVERITY)
    a = p.parse_args()
    try:
        session = boto3.Session(profile_name=a.profile, region_name=a.region); identity = session.client("sts").get_caller_identity(); iam = session.client("iam")
    except (ProfileNotFound, NoCredentialsError, ClientError) as e:
        print(f"Authentication error: {e}", file=sys.stderr); return 2
    r = Report(identity["Account"], identity["Arn"]); print(f"\nAuditing AWS account {r.account_id} ...")
    checks = [
        ("account settings", lambda: audit_account(iam, r)),
        ("users", lambda: audit_users(iam, r, a.max_key_age)),
        ("groups", lambda: audit_groups(iam, r)),
        ("roles", lambda: audit_roles(iam, r)),
        ("policies", lambda: audit_policies(iam, r)),
    ]
    for label, fn in checks:
        print(f"  scanning {label} ...")
        try: fn()
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}: print(f"  skipped {label}: access denied", file=sys.stderr)
            else: raise
    if a.anomaly: anomaly_check(r, Path(a.history_file))
    if a.ai:
        if not a.model_id: print("--ai requires --model-id or AIOPS_MODEL_ID", file=sys.stderr); return 2
        try: r.ai_summary = ai_triage(r, a.ollama_url, a.model_id)
        except RuntimeError as e: print(f"Ollama AI triage failed: {e}", file=sys.stderr); return 2
    if a.format == "table" or a.format == "all": print_report(r, a.min_severity)
    elif a.format == "json": print(json.dumps(r.as_dict(), indent=2, default=str))
    if a.format in {"json", "csv", "all"}:
        for f in write_reports(r, Path(a.output_dir), a.format): print(f"Report: {f}")
    if a.fail_on:
        limit = SEVERITY.index(a.fail_on)
        if any(SEVERITY.index(f.severity) <= limit for f in r.findings): print(f"CI FAILURE: {a.fail_on} finding detected."); return 1
    return 0

if __name__ == "__main__": sys.exit(main())
