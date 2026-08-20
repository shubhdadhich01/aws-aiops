"""
Day 04 — Automated Resource Compliance Scanner
==============================================

This is the function at the centre of today's architecture. It runs in two
completely different modes depending on what woke it up, and understanding why
it needs both is the main architectural lesson of Day 04.

    SCHEDULED MODE  (EventBridge rate(1 hour) -> "Scheduled Event")
        Sweeps the whole account. Catches drift that already happened —
        including anything created while the reactive rule was broken,
        disabled, or not yet deployed. This is the backstop.

    REACTIVE MODE   (EventBridge CloudTrail rule -> "AWS API Call via CloudTrail")
        Fires within seconds of a specific API call: RunInstances,
        CreateBucket, CreateSecurityGroup, AuthorizeSecurityGroupIngress.
        Catches drift as it happens. This is the fast path.

Neither is sufficient alone. The reactive path has gaps (a disabled rule, a
region you forgot to deploy to, an API call CloudTrail delivers late); the
scheduled path is too slow to stop anything. Production compliance platforms
run both and reconcile. That is what you are building.

DESIGN NOTES WORTH READING
--------------------------
* Every AWS call here is paginated. An account with 60 instances returns them
  in pages and a non-paginating scanner silently misses the tail. Silent
  incompleteness is worse than a crash, because you trust the clean report.

* The handler returns a structured dict rather than a bare string. Async
  invocations discard the return value, but it lands in the CloudWatch log and
  it is what the test suite asserts against.

* Failures are allowed to raise. Swallowing an exception here would mean the
  DLQ never receives the event and you lose the only record that a scan was
  attempted. Fail loudly, land in the DLQ, retry deliberately. That is Step 5
  of the lab.

* Secrets do not appear in this file, and there are no secret-shaped
  environment variables on this function. Compare it to broken_function.py.
"""

import json
import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Configuration, all injected by Terraform. Nothing sensitive lives here.
# ---------------------------------------------------------------------------

SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")
REQUIRED_TAG_KEYS = [
    t.strip() for t in os.environ.get("REQUIRED_TAG_KEYS", "Project,Owner,ManagedBy").split(",") if t.strip()
]
SEVERITY_THRESHOLD = os.environ.get("SEVERITY_THRESHOLD", "LOW").upper()
RESOURCE_PREFIX = os.environ.get("RESOURCE_PREFIX", "cbc-day04")

# Ordered worst-first. Used to decide whether a finding clears the notify bar.
SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Clients are created at module scope so they survive across warm invocations.
# This is not a micro-optimisation — creating a boto3 client costs 50-200ms and
# on a function that runs hourly you would pay it every single time.
ec2 = boto3.client("ec2")
s3 = boto3.client("s3")
sns = boto3.client("sns")


# ---------------------------------------------------------------------------
# Finding model
# ---------------------------------------------------------------------------


def make_finding(severity, resource_type, resource_id, issue, remediation):
    """Build one finding. Kept as a plain dict so it serialises to JSON for SNS
    without a custom encoder."""
    return {
        "severity": severity.upper(),
        "resource_type": resource_type,
        "resource_id": resource_id,
        "issue": issue,
        "remediation": remediation,
        "detected_at": datetime.now(timezone.utc).isoformat(),
    }


def clears_threshold(severity):
    """True if this severity is at or above the configured notify threshold."""
    try:
        return SEVERITY_ORDER.index(severity.upper()) <= SEVERITY_ORDER.index(SEVERITY_THRESHOLD)
    except ValueError:
        # Unknown severity: notify rather than silently drop it.
        return True


def missing_required_tags(tag_list):
    """Given a list of {"Key": ..., "Value": ...}, return the required keys that
    are absent or present-but-empty. An empty tag value is not compliance."""
    present = {t.get("Key"): (t.get("Value") or "").strip() for t in (tag_list or [])}
    return [k for k in REQUIRED_TAG_KEYS if not present.get(k)]


# ---------------------------------------------------------------------------
# Checks — EC2 instances
# ---------------------------------------------------------------------------


def check_ec2_instances():
    findings = []
    paginator = ec2.get_paginator("describe_instances")

    for page in paginator.paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}]
    ):
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                instance_id = instance["InstanceId"]

                missing = missing_required_tags(instance.get("Tags"))
                if missing:
                    findings.append(
                        make_finding(
                            "MEDIUM",
                            "AWS::EC2::Instance",
                            instance_id,
                            "Missing required tags: " + ", ".join(missing),
                            "Apply the tags with: aws ec2 create-tags --resources "
                            + instance_id
                            + " --tags Key=Project,Value=aws-aiops-bootcamp",
                        )
                    )

                # Unencrypted root volumes. The EBS default-encryption account
                # setting makes this impossible to get wrong; most accounts
                # still have not switched it on.
                for bdm in instance.get("BlockDeviceMappings", []):
                    ebs = bdm.get("Ebs", {})
                    if ebs.get("VolumeId") and not ebs.get("Encrypted", False):
                        findings.append(
                            make_finding(
                                "HIGH",
                                "AWS::EC2::Volume",
                                ebs["VolumeId"],
                                "EBS volume attached to " + instance_id + " is not encrypted at rest",
                                "Enable EBS encryption by default: "
                                "aws ec2 enable-ebs-encryption-by-default. Existing volumes must be "
                                "snapshotted, copied with encryption, and re-attached.",
                            )
                        )

                # IMDSv1 still reachable — the Capital One breach vector, and
                # the same finding Day 03's auditor raises on launch templates.
                metadata = instance.get("MetadataOptions", {})
                if metadata.get("HttpTokens") == "optional":
                    findings.append(
                        make_finding(
                            "HIGH",
                            "AWS::EC2::Instance",
                            instance_id,
                            "Instance metadata service allows IMDSv1 (HttpTokens=optional)",
                            "aws ec2 modify-instance-metadata-options --instance-id "
                            + instance_id
                            + " --http-tokens required --http-endpoint enabled",
                        )
                    )

    return findings


# ---------------------------------------------------------------------------
# Checks — security groups
# ---------------------------------------------------------------------------

# Ports where a 0.0.0.0/0 ingress rule is not a judgement call.
HIGH_RISK_PORTS = {
    22: "SSH",
    23: "Telnet",
    3389: "RDP",
    3306: "MySQL",
    5432: "PostgreSQL",
    6379: "Redis",
    27017: "MongoDB",
    9200: "Elasticsearch",
    1433: "MSSQL",
}


def check_security_groups():
    findings = []
    paginator = ec2.get_paginator("describe_security_groups")

    for page in paginator.paginate():
        for group in page.get("SecurityGroups", []):
            group_id = group["GroupId"]
            group_name = group.get("GroupName", "")

            for permission in group.get("IpPermissions", []):
                open_to_world = any(r.get("CidrIp") == "0.0.0.0/0" for r in permission.get("IpRanges", []))
                if not open_to_world:
                    continue

                from_port = permission.get("FromPort")
                to_port = permission.get("ToPort")
                protocol = permission.get("IpProtocol")

                # -1 means every protocol and every port.
                if protocol == "-1":
                    findings.append(
                        make_finding(
                            "CRITICAL",
                            "AWS::EC2::SecurityGroup",
                            group_id,
                            "Security group " + group_name + " allows ALL traffic on ALL ports from 0.0.0.0/0",
                            "Revoke the rule and replace it with the specific ports and source CIDRs "
                            "the workload actually needs.",
                        )
                    )
                    continue

                for port, service in HIGH_RISK_PORTS.items():
                    if from_port is not None and to_port is not None and from_port <= port <= to_port:
                        findings.append(
                            make_finding(
                                "CRITICAL",
                                "AWS::EC2::SecurityGroup",
                                group_id,
                                service + " (port " + str(port) + ") is open to 0.0.0.0/0 in " + group_name,
                                "aws ec2 revoke-security-group-ingress --group-id "
                                + group_id
                                + " --protocol tcp --port "
                                + str(port)
                                + " --cidr 0.0.0.0/0 "
                                + "— then re-add it scoped to your own IP or a bastion security group.",
                            )
                        )

            missing = missing_required_tags(group.get("Tags"))
            # The default security group cannot be deleted and is rarely tagged;
            # reporting it every hour trains people to ignore the report.
            if missing and group_name != "default":
                findings.append(
                    make_finding(
                        "LOW",
                        "AWS::EC2::SecurityGroup",
                        group_id,
                        "Missing required tags: " + ", ".join(missing),
                        "aws ec2 create-tags --resources " + group_id + " --tags Key=Owner,Value=your-name",
                    )
                )

    return findings


# ---------------------------------------------------------------------------
# Checks — S3 buckets
# ---------------------------------------------------------------------------


def check_s3_buckets():
    findings = []

    try:
        buckets = s3.list_buckets().get("Buckets", [])
    except ClientError as exc:
        logger.warning("Could not list buckets: %s", exc)
        return findings

    for bucket in buckets:
        name = bucket["Name"]

        # Public access block. Absent config is itself the finding — AWS
        # returns NoSuchPublicAccessBlockConfiguration rather than a set of
        # false values, which is a genuinely easy mistake to code around wrong.
        try:
            config = s3.get_public_access_block(Bucket=name)["PublicAccessBlockConfiguration"]
            if not all(
                [
                    config.get("BlockPublicAcls"),
                    config.get("IgnorePublicAcls"),
                    config.get("BlockPublicPolicy"),
                    config.get("RestrictPublicBuckets"),
                ]
            ):
                findings.append(
                    make_finding(
                        "CRITICAL",
                        "AWS::S3::Bucket",
                        name,
                        "Bucket does not have all four public access block settings enabled",
                        "aws s3api put-public-access-block --bucket "
                        + name
                        + " --public-access-block-configuration "
                        + "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,"
                        + "RestrictPublicBuckets=true",
                    )
                )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "NoSuchPublicAccessBlockConfiguration":
                findings.append(
                    make_finding(
                        "CRITICAL",
                        "AWS::S3::Bucket",
                        name,
                        "Bucket has NO public access block configuration at all",
                        "aws s3api put-public-access-block --bucket " + name + " ... (see above)",
                    )
                )
            elif code in ("AccessDenied", "AllAccessDisabled"):
                logger.info("No permission to read public access block for %s", name)
            else:
                logger.warning("get_public_access_block failed for %s: %s", name, exc)

        # Default encryption. Since January 2023 AWS applies SSE-S3 to new
        # buckets automatically, so an absent configuration here usually means
        # an old bucket — which is exactly the one you want to know about.
        try:
            s3.get_bucket_encryption(Bucket=name)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ServerSideEncryptionConfigurationNotFoundError":
                findings.append(
                    make_finding(
                        "HIGH",
                        "AWS::S3::Bucket",
                        name,
                        "Bucket has no default encryption configuration",
                        "aws s3api put-bucket-encryption --bucket "
                        + name
                        + " --server-side-encryption-configuration "
                        + '\'{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}\'',
                    )
                )

        # Versioning. Not a security control on its own, but the difference
        # between an annoying ransomware incident and a fatal one.
        try:
            versioning = s3.get_bucket_versioning(Bucket=name)
            if versioning.get("Status") != "Enabled":
                findings.append(
                    make_finding(
                        "MEDIUM",
                        "AWS::S3::Bucket",
                        name,
                        "Bucket versioning is not enabled",
                        "aws s3api put-bucket-versioning --bucket "
                        + name
                        + " --versioning-configuration Status=Enabled",
                    )
                )
        except ClientError as exc:
            logger.warning("get_bucket_versioning failed for %s: %s", name, exc)

    return findings


# ---------------------------------------------------------------------------
# Reactive mode — inspect only the resource that just changed
# ---------------------------------------------------------------------------


def check_single_event(detail):
    """Handle one CloudTrail event delivered via EventBridge.

    The shape of `detail` is the CloudTrail record: eventName, userIdentity,
    requestParameters, responseElements. What you get back varies enormously by
    API, which is why each branch reads different fields.
    """
    findings = []
    event_name = detail.get("eventName", "")
    actor = detail.get("userIdentity", {}).get("arn", "unknown")
    request = detail.get("requestParameters") or {}
    response = detail.get("responseElements") or {}

    logger.info("Reactive check for %s by %s", event_name, actor)

    if event_name == "CreateSecurityGroup":
        group_id = response.get("groupId")
        if group_id:
            findings.extend(_inspect_security_group(group_id, actor))

    elif event_name == "AuthorizeSecurityGroupIngress":
        group_id = request.get("groupId")
        if group_id:
            findings.extend(_inspect_security_group(group_id, actor))

    elif event_name == "RunInstances":
        for item in (response.get("instancesSet") or {}).get("items", []):
            instance_id = item.get("instanceId")
            if instance_id:
                findings.append(
                    make_finding(
                        "INFO",
                        "AWS::EC2::Instance",
                        instance_id,
                        "Instance launched by " + actor + " — queued for tag and encryption review",
                        "The next scheduled sweep will evaluate it fully once tags have settled.",
                    )
                )

    elif event_name == "CreateBucket":
        bucket = request.get("bucketName")
        if bucket:
            findings.append(
                make_finding(
                    "HIGH",
                    "AWS::S3::Bucket",
                    bucket,
                    "Bucket created by " + actor + " — verify public access block before any object is written",
                    "aws s3api get-public-access-block --bucket " + bucket,
                )
            )

    else:
        logger.info("No reactive rule implemented for %s — ignoring", event_name)

    return findings


def _inspect_security_group(group_id, actor):
    """Pull one security group and apply the open-to-world rules immediately.

    This is deliberately a re-describe rather than trusting the event payload:
    CloudTrail's requestParameters tell you what was ASKED for, not what the
    resulting state is. Always read the resource.
    """
    findings = []
    try:
        groups = ec2.describe_security_groups(GroupIds=[group_id]).get("SecurityGroups", [])
    except ClientError as exc:
        logger.warning("Could not describe %s: %s", group_id, exc)
        return findings

    for group in groups:
        name = group.get("GroupName", "")
        for permission in group.get("IpPermissions", []):
            if not any(r.get("CidrIp") == "0.0.0.0/0" for r in permission.get("IpRanges", [])):
                continue

            protocol = permission.get("IpProtocol")
            from_port = permission.get("FromPort")
            to_port = permission.get("ToPort")

            if protocol == "-1":
                findings.append(
                    make_finding(
                        "CRITICAL",
                        "AWS::EC2::SecurityGroup",
                        group_id,
                        "REACTIVE: " + actor + " opened ALL ports to 0.0.0.0/0 on " + name,
                        "Revoke immediately: aws ec2 revoke-security-group-ingress --group-id "
                        + group_id
                        + " --protocol -1 --cidr 0.0.0.0/0",
                    )
                )
                continue

            port_desc = str(from_port) if from_port == to_port else str(from_port) + "-" + str(to_port)
            service = HIGH_RISK_PORTS.get(from_port, "")
            severity = "CRITICAL" if service else "HIGH"
            label = (" (" + service + ")") if service else ""

            findings.append(
                make_finding(
                    severity,
                    "AWS::EC2::SecurityGroup",
                    group_id,
                    "REACTIVE: " + actor + " opened port " + port_desc + label + " to 0.0.0.0/0 on " + name,
                    "aws ec2 revoke-security-group-ingress --group-id "
                    + group_id
                    + " --protocol "
                    + str(protocol)
                    + " --port "
                    + str(from_port)
                    + " --cidr 0.0.0.0/0",
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------


def publish_findings(findings, mode):
    """Send anything at or above the threshold to SNS as one message.

    One message per scan, not one per finding. A compliance tool that sends
    forty separate emails gets a mail rule pointed at it within a week, and
    then it may as well not exist.
    """
    notifiable = [f for f in findings if clears_threshold(f["severity"])]
    if not notifiable:
        logger.info("No findings at or above %s — nothing to publish", SEVERITY_THRESHOLD)
        return 0

    if not SNS_TOPIC_ARN:
        logger.warning("SNS_TOPIC_ARN is not set — skipping publish")
        return 0

    counts = {}
    for finding in notifiable:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    summary = ", ".join(sev + ": " + str(counts[sev]) for sev in SEVERITY_ORDER if sev in counts)

    lines = [
        "Compliance scan (" + mode + " mode) found " + str(len(notifiable)) + " issue(s).",
        "",
        "Summary — " + summary,
        "",
        "-" * 68,
    ]
    for finding in notifiable:
        lines.extend(
            [
                "",
                "[" + finding["severity"] + "] " + finding["resource_type"],
                "  Resource : " + finding["resource_id"],
                "  Issue    : " + finding["issue"],
                "  Fix      : " + finding["remediation"],
            ]
        )
    lines.extend(["", "-" * 68, "", "Scanner: " + RESOURCE_PREFIX + "-compliance-scanner", "Mode: " + mode])

    subject = "[" + RESOURCE_PREFIX + "] " + str(len(notifiable)) + " compliance finding(s) — " + mode

    sns.publish(
        TopicArn=SNS_TOPIC_ARN,
        # SNS subjects are hard-capped at 100 characters and the API rejects
        # anything longer outright, taking your whole notification with it.
        Subject=subject[:100],
        Message="\n".join(lines),
    )
    logger.info("Published %d finding(s) to SNS", len(notifiable))
    return len(notifiable)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def lambda_handler(event, context):
    detail_type = event.get("detail-type", "")
    source = event.get("source", "")

    logger.info("Invoked. source=%s detail-type=%s", source, detail_type)

    # Step 5 of the lab uses this to force a failure and exercise the DLQ.
    # A real function would never contain this, obviously.
    if event.get("force_failure"):
        raise RuntimeError(
            "force_failure was set in the event payload. This is the deliberate "
            "DLQ test from Step 5 of the lab — the event should now land in the "
            "dead letter queue."
        )

    if detail_type == "AWS API Call via CloudTrail":
        mode = "reactive"
        findings = check_single_event(event.get("detail", {}))
    else:
        mode = "scheduled"
        findings = []
        findings.extend(check_ec2_instances())
        findings.extend(check_security_groups())
        findings.extend(check_s3_buckets())

    published = publish_findings(findings, mode)

    by_severity = {}
    for finding in findings:
        by_severity[finding["severity"]] = by_severity.get(finding["severity"], 0) + 1

    result = {
        "mode": mode,
        "total_findings": len(findings),
        "published": published,
        "by_severity": by_severity,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }

    logger.info("Result: %s", json.dumps(result))
    return result
