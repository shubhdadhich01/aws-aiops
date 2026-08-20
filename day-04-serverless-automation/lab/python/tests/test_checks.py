#!/usr/bin/env python3
"""
test_checks.py — 47 tests for serverless_audit.py.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

Run them:

    cd lab/python
    python3 -m unittest discover -s tests -v      # stdlib, nothing to install
    python3 -m pytest tests -q                    # if you prefer pytest

No credentials, no account, no network. Every check in serverless_audit.py is
a pure function over dictionaries, so the whole suite runs in well under a
second on a laptop on a train — which is the only reason anyone actually runs
tests before pushing.

WHAT IS BEING TESTED, AND WHY IT IS IN TWO HALVES
-------------------------------------------------
Every check gets two tests:

    TestChecksFire      the check reports the problem it exists to report
    TestChecksSilent    the check says NOTHING about a correctly built resource

The second half is the half people skip, and it is the half that decides
whether anyone keeps using your tool. An auditor that flags the reference
architecture it ships with gets muted in a week, and after that it does not
matter how good the first half was.

Two checks — CMP-008 (deprecated runtime) and CMP-016 (public function) — must
produce ZERO findings against the entire Day 04 stack. TestWholeStack asserts
that explicitly, alongside the exact total of 14 findings and a compliance
score of 0/100. If you change the Terraform, these are the tests that tell you
the documentation is now lying.

The fixtures below mirror lab/terraform/main.tf with create_insecure_examples
= true. When you change one, change the other.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import serverless_audit as sa  # noqa: E402


REGION = "us-east-1"
ACCOUNT = "123456789012"

GOOD_DLQ_ARN = f"arn:aws:sqs:{REGION}:{ACCOUNT}:cbc-day04-scanner-dlq-a1b2"


###############################################################################
# Fixtures — the Day 04 stack, in dictionaries
###############################################################################


def good_function():
    """cbc-day04-compliance-scanner: everything section 12 gets wrong, right."""
    return {
        "FunctionName": "cbc-day04-compliance-scanner-a1b2",
        "Role": f"arn:aws:iam::{ACCOUNT}:role/cbc-day04-scanner-role-a1b2",
        "Runtime": "python3.12",
        "Timeout": 60,
        "MemorySize": 256,
        "DeadLetterConfig": {"TargetArn": GOOD_DLQ_ARN},
        "TracingConfig": {"Mode": "Active"},
        "KMSKeyArn": f"arn:aws:kms:{REGION}:{ACCOUNT}:key/11111111-2222-3333",
        "Environment": {
            "Variables": {
                "SNS_TOPIC_ARN": f"arn:aws:sns:{REGION}:{ACCOUNT}:cbc-day04-findings-a1b2",
                "REQUIRED_TAG_KEYS": "Project,Owner,ManagedBy",
                "SEVERITY_THRESHOLD": "MEDIUM",
                "RESOURCE_PREFIX": "cbc-day04-",
                "LOG_LEVEL": "INFO",
            }
        },
    }


def broken_function():
    """cbc-day04-broken-function: five findings in one resource, plus its role."""
    return {
        "FunctionName": "cbc-day04-broken-function-a1b2",
        "Role": f"arn:aws:iam::{ACCOUNT}:role/cbc-day04-broken-role-a1b2",
        "Runtime": "python3.12",
        "Timeout": 3,
        "MemorySize": 128,
        "TracingConfig": {"Mode": "PassThrough"},
        "Environment": {
            "Variables": {
                "API_KEY": "sk-live-NOT-A-REAL-KEY-abcdef123456",
                "DB_PASSWORD": "hunter2-also-not-real",
                "DB_HOST": "prod-db.internal.example.com",
            }
        },
    }


def log_groups():
    """Only the scanner has a declared log group. That absence is CMP-005."""
    return {
        "/aws/lambda/cbc-day04-compliance-scanner-a1b2": {
            "logGroupName": "/aws/lambda/cbc-day04-compliance-scanner-a1b2",
            "retentionInDays": 7,
            "storedBytes": 4096,
        }
    }


def good_role_policies():
    """Scoped actions. Resource "*" alone is not a finding — only "*" on "*"."""
    return [
        {
            "name": "cbc-day04-scanner-read",
            "type": "inline",
            "document": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "ReadOnlyDiscovery",
                        "Effect": "Allow",
                        "Action": [
                            "lambda:ListFunctions",
                            "sns:GetTopicAttributes",
                            "sqs:GetQueueAttributes",
                        ],
                        "Resource": "*",
                    }
                ],
            },
        }
    ]


def broken_role_policies():
    return [
        {
            "name": "cbc-day04-broken-policy",
            "type": "inline",
            "document": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "ThisIsAdministratorAccessWithExtraSteps",
                        "Effect": "Allow",
                        "Action": "*",
                        "Resource": "*",
                    }
                ],
            },
        }
    ]


def good_topic():
    """Wildcard principal narrowed by AWS:SourceAccount — correct, not a finding."""
    return {
        "TopicArn": f"arn:aws:sns:{REGION}:{ACCOUNT}:cbc-day04-findings-a1b2",
        "KmsMasterKeyId": "alias/aws/sns",
        "Policy": (
            '{"Version":"2012-10-17","Statement":[{"Sid":"AllowOwnAccountPublish",'
            '"Effect":"Allow","Principal":{"AWS":"*"},"Action":"SNS:Publish",'
            '"Resource":"arn:aws:sns:us-east-1:123456789012:cbc-day04-findings-a1b2",'
            '"Condition":{"StringEquals":{"AWS:SourceAccount":"123456789012"}}}]}'
        ),
    }


def broken_topic():
    return {
        "TopicArn": f"arn:aws:sns:{REGION}:{ACCOUNT}:cbc-day04-broken-topic-a1b2",
        "Policy": (
            '{"Version":"2012-10-17","Statement":[{"Sid":"AnyoneAtAllMayPublish",'
            '"Effect":"Allow","Principal":{"AWS":"*"},"Action":"SNS:Publish",'
            '"Resource":"arn:aws:sns:us-east-1:123456789012:cbc-day04-broken-topic-a1b2"}]}'
        ),
    }


def good_queue():
    """The scanner DLQ. Encrypted, and exempt from CMP-013 for being a DLQ."""
    return {
        "QueueUrl": f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/cbc-day04-scanner-dlq-a1b2",
        "QueueArn": GOOD_DLQ_ARN,
        "KmsMasterKeyId": "alias/aws/sqs",
        "MessageRetentionPeriod": "1209600",
    }


def broken_queue():
    return {
        "QueueUrl": f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/cbc-day04-broken-queue-a1b2",
        "QueueArn": f"arn:aws:sqs:{REGION}:{ACCOUNT}:cbc-day04-broken-queue-a1b2",
        "SqsManagedSseEnabled": "false",
        "MessageRetentionPeriod": "86400",
    }


def good_rule():
    return {
        "Name": "cbc-day04-scheduled-scan-a1b2",
        "State": "ENABLED",
        "ScheduleExpression": "rate(1 hour)",
    }


def good_targets():
    return [
        {
            "Id": "ComplianceScannerScheduled",
            "Arn": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:cbc-day04-compliance-scanner-a1b2",
            "RetryPolicy": {
                "MaximumRetryAttempts": 2,
                "MaximumEventAgeInSeconds": 3600,
            },
            "DeadLetterConfig": {"Arn": GOOD_DLQ_ARN},
        }
    ]


def broken_rule():
    return {
        "Name": "cbc-day04-broken-rule-a1b2",
        "State": "DISABLED",
        "ScheduleExpression": "rate(1 day)",
    }


def broken_targets():
    return [
        {
            "Id": "BrokenTargetNoDlq",
            "Arn": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:cbc-day04-broken-function-a1b2",
        }
    ]


def service_principal_policy():
    """What aws_lambda_permission actually produces. Must never fire CMP-016."""
    return (
        '{"Version":"2012-10-17","Id":"default","Statement":[{'
        '"Sid":"AllowExecutionFromEventBridgeSchedule","Effect":"Allow",'
        '"Principal":{"Service":"events.amazonaws.com"},'
        '"Action":"lambda:InvokeFunction",'
        '"Resource":"arn:aws:lambda:us-east-1:123456789012:function:cbc-day04-compliance-scanner-a1b2",'
        '"Condition":{"ArnLike":{"AWS:SourceArn":'
        '"arn:aws:events:us-east-1:123456789012:rule/cbc-day04-scheduled-scan-a1b2"}}}]}'
    )


def audit_whole_stack():
    """Run all 16 checks over the whole fixture stack, exactly as run() does."""
    findings = []
    known_dlqs = {GOOD_DLQ_ARN}

    for function, policies, concurrency in (
        (good_function(), good_role_policies(), 2),
        (broken_function(), broken_role_policies(), None),
    ):
        findings += sa.check_dead_letter_queue(function, REGION)
        findings += sa.check_plaintext_secrets(function, REGION)
        findings += sa.check_env_encryption(function, REGION)
        findings += sa.check_execution_role(function, policies, REGION)
        findings += sa.check_log_group(function, log_groups(), REGION)
        findings += sa.check_timeout(function, REGION)
        findings += sa.check_reserved_concurrency(function, concurrency, REGION)
        findings += sa.check_runtime(function, REGION)
        findings += sa.check_tracing(function, REGION)
        findings += sa.check_public_access(
            function, {}, service_principal_policy(), REGION
        )

    for topic in (good_topic(), broken_topic()):
        findings += sa.check_sns_encryption(topic, REGION)
        findings += sa.check_sns_topic_policy(topic, REGION)

    for queue in (good_queue(), broken_queue()):
        findings += sa.check_sqs_encryption(queue, REGION)
        findings += sa.check_sqs_redrive(queue, known_dlqs, REGION)

    for rule, targets in ((good_rule(), good_targets()), (broken_rule(), broken_targets())):
        findings += sa.check_rule_state(rule, REGION)
        findings += sa.check_rule_targets(rule, targets, REGION)

    return findings


def ids(findings):
    return sorted(f.check_id for f in findings)


###############################################################################
# 1-16 · Every check fires on the input it exists to catch
###############################################################################


class TestChecksFire(unittest.TestCase):
    def test_cmp001_fires_when_no_dead_letter_target(self):
        findings = sa.check_dead_letter_queue(broken_function(), REGION)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].check_id, "CMP-001")
        self.assertEqual(findings[0].severity, "CRITICAL")

    def test_cmp002_fires_on_secret_shaped_environment_variables(self):
        findings = sa.check_plaintext_secrets(broken_function(), REGION)
        self.assertEqual(len(findings), 1, "one finding per function, not per variable")
        self.assertEqual(findings[0].severity, "CRITICAL")
        flagged = findings[0].evidence["variables"]
        self.assertIn("API_KEY", flagged)
        self.assertIn("DB_PASSWORD", flagged)
        self.assertNotIn("DB_HOST", flagged, "DB_HOST is not a secret")

    def test_cmp003_fires_when_env_vars_use_the_default_key(self):
        findings = sa.check_env_encryption(broken_function(), REGION)
        self.assertEqual(ids(findings), ["CMP-003"])
        self.assertEqual(findings[0].severity, "MEDIUM")

    def test_cmp004_fires_on_wildcard_action_and_resource(self):
        findings = sa.check_execution_role(
            broken_function(), broken_role_policies(), REGION
        )
        self.assertEqual(ids(findings), ["CMP-004"])
        self.assertEqual(findings[0].resource_type, "AWS::IAM::Role")

    def test_cmp005_fires_when_missing_and_when_retention_is_never(self):
        # Route one: no log group at all. Lambda will create it untracked.
        findings = sa.check_log_group(broken_function(), log_groups(), REGION)
        self.assertEqual(ids(findings), ["CMP-005"])
        self.assertIn("cbc-day04-broken-function", findings[0].resource_id)

        # Route two: the group exists but nothing ever expires out of it.
        groups = {
            "/aws/lambda/cbc-day04-broken-function-a1b2": {
                "logGroupName": "/aws/lambda/cbc-day04-broken-function-a1b2",
                "storedBytes": 999999,
            }
        }
        findings = sa.check_log_group(broken_function(), groups, REGION)
        self.assertEqual(ids(findings), ["CMP-005"])
        self.assertIn("Never expire", findings[0].title)

    def test_cmp006_fires_on_the_three_second_default(self):
        findings = sa.check_timeout(broken_function(), REGION)
        self.assertEqual(ids(findings), ["CMP-006"])

    def test_cmp007_fires_when_concurrency_is_unreserved(self):
        findings = sa.check_reserved_concurrency(broken_function(), None, REGION)
        self.assertEqual(ids(findings), ["CMP-007"])

    def test_cmp008_fires_on_a_deprecated_runtime(self):
        function = good_function()
        function["Runtime"] = "python3.8"
        findings = sa.check_runtime(function, REGION)
        self.assertEqual(ids(findings), ["CMP-008"])
        self.assertEqual(findings[0].severity, "HIGH")

    def test_cmp009_fires_when_tracing_is_passthrough(self):
        findings = sa.check_tracing(broken_function(), REGION)
        self.assertEqual(ids(findings), ["CMP-009"])
        self.assertEqual(findings[0].severity, "LOW")

    def test_cmp010_fires_on_an_unencrypted_topic(self):
        findings = sa.check_sns_encryption(broken_topic(), REGION)
        self.assertEqual(ids(findings), ["CMP-010"])

    def test_cmp011_fires_on_an_unconditioned_wildcard_principal(self):
        findings = sa.check_sns_topic_policy(broken_topic(), REGION)
        self.assertEqual(ids(findings), ["CMP-011"])
        self.assertEqual(findings[0].severity, "CRITICAL")

    def test_cmp012_fires_when_sse_is_the_string_false(self):
        findings = sa.check_sqs_encryption(broken_queue(), REGION)
        self.assertEqual(ids(findings), ["CMP-012"])

    def test_cmp013_fires_on_a_queue_with_no_redrive_policy(self):
        findings = sa.check_sqs_redrive(broken_queue(), {GOOD_DLQ_ARN}, REGION)
        self.assertEqual(ids(findings), ["CMP-013"])

    def test_cmp014_fires_on_a_disabled_rule(self):
        findings = sa.check_rule_state(broken_rule(), REGION)
        self.assertEqual(ids(findings), ["CMP-014"])

    def test_cmp015_fires_per_unprotected_target(self):
        rule = broken_rule()
        targets = broken_targets() + [{"Id": "SecondBareTarget", "Arn": "arn:aws:lambda:x"}]
        findings = sa.check_rule_targets(rule, targets, REGION)
        self.assertEqual(len(findings), 2, "one finding per target, not per rule")
        self.assertEqual(set(ids(findings)), {"CMP-015"})

    def test_cmp016_fires_on_a_function_url_with_no_auth(self):
        url_config = {"AuthType": "NONE", "FunctionUrl": "https://abc.lambda-url.aws/"}
        findings = sa.check_public_access(good_function(), url_config, None, REGION)
        self.assertEqual(ids(findings), ["CMP-016"])
        self.assertEqual(findings[0].severity, "CRITICAL")


###############################################################################
# 17-32 · Every check stays silent on a correctly built resource
#
# This is the half that decides whether your auditor survives contact with a
# team. A tool that reports the reference architecture as broken gets muted.
###############################################################################


class TestChecksSilent(unittest.TestCase):
    def test_cmp001_silent_for_both_dlq_mechanisms(self):
        self.assertEqual(sa.check_dead_letter_queue(good_function(), REGION), [])

        # Destinations (OnFailure) is the newer mechanism and satisfies the
        # same requirement. Flagging it would be a false positive.
        function = broken_function()
        function["EventInvokeConfig"] = {
            "DestinationConfig": {"OnFailure": {"Destination": GOOD_DLQ_ARN}}
        }
        self.assertEqual(sa.check_dead_letter_queue(function, REGION), [])

    def test_cmp002_silent_on_arns_pointers_and_allowlisted_names(self):
        self.assertEqual(sa.check_plaintext_secrets(good_function(), REGION), [])

        # TOKEN_TTL_SECONDS is not a token. SECRET_ARN is the correct pattern,
        # not a violation of it. Matching on substrings alone would flag both.
        function = good_function()
        function["Environment"]["Variables"].update(
            {
                "TOKEN_TTL_SECONDS": "900",
                "AUTH_URL": "https://example.com/oauth",
                "SECRET_ARN": f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:db-x",
            }
        )
        self.assertEqual(sa.check_plaintext_secrets(function, REGION), [])

    def test_cmp003_silent_with_a_key_and_with_no_variables_at_all(self):
        self.assertEqual(sa.check_env_encryption(good_function(), REGION), [])

        # Nothing to encrypt is not a finding, even with no key configured.
        function = good_function()
        function.pop("Environment")
        function.pop("KMSKeyArn")
        self.assertEqual(sa.check_env_encryption(function, REGION), [])

    def test_cmp004_silent_on_scoped_actions(self):
        findings = sa.check_execution_role(
            good_function(), good_role_policies(), REGION
        )
        self.assertEqual(findings, [], 'Resource "*" alone is not the finding')

    def test_cmp005_silent_when_retention_is_set(self):
        self.assertEqual(
            sa.check_log_group(good_function(), log_groups(), REGION), []
        )

    def test_cmp006_silent_on_a_deliberate_timeout(self):
        self.assertEqual(sa.check_timeout(good_function(), REGION), [])

    def test_cmp007_silent_on_reserved_concurrency_including_zero(self):
        self.assertEqual(sa.check_reserved_concurrency(good_function(), 2, REGION), [])
        self.assertEqual(
            sa.check_reserved_concurrency(good_function(), 0, REGION),
            [],
            "0 means throttled to a stop — configured, not missing",
        )

    def test_cmp008_silent_on_a_supported_runtime(self):
        self.assertEqual(sa.check_runtime(good_function(), REGION), [])

    def test_cmp009_silent_when_tracing_is_active(self):
        self.assertEqual(sa.check_tracing(good_function(), REGION), [])

    def test_cmp010_silent_on_the_aws_managed_key(self):
        self.assertEqual(sa.check_sns_encryption(good_topic(), REGION), [])

    def test_cmp011_silent_when_a_condition_scopes_the_principal(self):
        self.assertEqual(sa.check_sns_topic_policy(good_topic(), REGION), [])

    def test_cmp012_silent_on_sqs_managed_sse(self):
        queue = broken_queue()
        queue["SqsManagedSseEnabled"] = "true"
        self.assertEqual(sa.check_sqs_encryption(queue, REGION), [])
        self.assertEqual(sa.check_sqs_encryption(good_queue(), REGION), [])

    def test_cmp013_silent_when_a_redrive_policy_exists(self):
        queue = broken_queue()
        queue["RedrivePolicy"] = (
            '{"deadLetterTargetArn":"' + GOOD_DLQ_ARN + '","maxReceiveCount":5}'
        )
        self.assertEqual(sa.check_sqs_redrive(queue, set(), REGION), [])

    def test_cmp014_silent_on_enabled_and_cloudtrail_managed_states(self):
        self.assertEqual(sa.check_rule_state(good_rule(), REGION), [])
        rule = good_rule()
        rule["State"] = "ENABLED_WITH_ALL_CLOUDTRAIL_MANAGEMENT_EVENTS"
        self.assertEqual(
            sa.check_rule_state(rule, REGION),
            [],
            "test the ENABLED prefix, not equality",
        )

    def test_cmp015_silent_when_the_target_has_retry_and_dlq(self):
        self.assertEqual(
            sa.check_rule_targets(good_rule(), good_targets(), REGION), []
        )

    def test_cmp016_silent_on_a_scoped_service_principal(self):
        findings = sa.check_public_access(
            good_function(), {}, service_principal_policy(), REGION
        )
        self.assertEqual(findings, [], "aws_lambda_permission is the correct pattern")


###############################################################################
# 33-36 · The whole stack, and the two checks that must say nothing about it
###############################################################################


class TestWholeStack(unittest.TestCase):
    def test_stack_produces_exactly_fourteen_findings(self):
        findings = audit_whole_stack()
        self.assertEqual(
            len(findings),
            14,
            "the docs in variables.tf and outputs.tf quote this number",
        )
        self.assertEqual(
            ids(findings),
            [
                "CMP-001",
                "CMP-002",
                "CMP-003",
                "CMP-004",
                "CMP-005",
                "CMP-006",
                "CMP-007",
                "CMP-009",
                "CMP-010",
                "CMP-011",
                "CMP-012",
                "CMP-013",
                "CMP-014",
                "CMP-015",
            ],
        )

    def test_stack_scores_zero_out_of_one_hundred(self):
        findings = audit_whole_stack()
        self.assertEqual(sa.calculate_score(findings), 0)
        self.assertTrue(sa.score_grade(0).startswith("F"))

    def test_cmp008_has_zero_false_positives_on_the_stack(self):
        self.assertEqual(
            [f for f in audit_whole_stack() if f.check_id == "CMP-008"],
            [],
            "both functions pin python3.12 — silent by design",
        )

    def test_cmp016_has_zero_false_positives_on_the_stack(self):
        self.assertEqual(
            [f for f in audit_whole_stack() if f.check_id == "CMP-016"],
            [],
            "no function URLs, and permissions are scoped by SourceArn",
        )


###############################################################################
# 37-40 · Scoring and the --min-severity contract
###############################################################################


class TestScoring(unittest.TestCase):
    def test_severity_weights_match_the_contract(self):
        self.assertEqual(
            sa.SEVERITY_WEIGHTS,
            {"CRITICAL": 25, "HIGH": 10, "MEDIUM": 4, "LOW": 1, "INFO": 0},
        )

    def test_a_clean_account_scores_one_hundred(self):
        self.assertEqual(sa.calculate_score([]), 100)
        self.assertTrue(sa.score_grade(100).startswith("A"))

    def test_score_floors_at_zero_rather_than_going_negative(self):
        findings = sa.check_dead_letter_queue(broken_function(), REGION) * 10
        self.assertEqual(sa.calculate_score(findings), 0)

    def test_min_severity_filters_the_display_and_never_the_score(self):
        findings = audit_whole_stack()
        score = sa.calculate_score(findings)
        shown = sa.filter_by_severity(findings, "CRITICAL")
        self.assertEqual(len(shown), 4)
        self.assertEqual(
            sa.calculate_score(findings),
            score,
            "filtering the display must not flatter the score",
        )


###############################################################################
# 41 · The Finding model
###############################################################################


class TestFindingModel(unittest.TestCase):
    def test_finding_rejects_an_unknown_severity(self):
        with self.assertRaises(ValueError):
            sa.Finding(
                check_id="CMP-999",
                severity="SPICY",
                resource_type="AWS::Lambda::Function",
                resource_id="x",
                title="t",
                detail="d",
                remediation="r",
            )


###############################################################################
# 42-43 · The DLQ exemption, both routes
#
# A dead letter queue having no dead letter queue of its own is correct. Get
# this wrong and every DLQ in the account produces a finding nobody can action.
###############################################################################


class TestDlqExemption(unittest.TestCase):
    def test_a_known_dlq_arn_is_exempt_from_cmp013(self):
        queue = good_queue()
        queue["QueueArn"] = f"arn:aws:sqs:{REGION}:{ACCOUNT}:catch-all"
        self.assertEqual(
            sa.check_sqs_redrive(queue, {queue["QueueArn"]}, REGION),
            [],
            "something already treats this queue as a dead letter target",
        )

    def test_a_dlq_named_queue_is_exempt_even_with_no_known_arns(self):
        self.assertEqual(
            sa.check_sqs_redrive(good_queue(), set(), REGION),
            [],
            "the source queue may live in another stack entirely",
        )


###############################################################################
# 44-45 · Policy parsing helpers
###############################################################################


class TestHelpers(unittest.TestCase):
    def test_parse_policy_accepts_dict_json_and_urlencoded(self):
        expected = {"Version": "2012-10-17"}
        self.assertEqual(sa.parse_policy(expected), expected)
        self.assertEqual(sa.parse_policy('{"Version":"2012-10-17"}'), expected)
        self.assertEqual(
            sa.parse_policy("%7B%22Version%22%3A%222012-10-17%22%7D"),
            expected,
            "IAM returns URL-encoded JSON",
        )
        self.assertEqual(sa.parse_policy(None), {})
        self.assertEqual(sa.parse_policy("not json at all"), {})

    def test_as_list_normalises_the_string_or_list_problem(self):
        self.assertEqual(sa.as_list("s3:GetObject"), ["s3:GetObject"])
        self.assertEqual(sa.as_list(["a", "b"]), ["a", "b"])
        self.assertEqual(sa.as_list(None), [])


###############################################################################
# 46-47 · Output renderers
###############################################################################


class TestRenderers(unittest.TestCase):
    def test_json_and_csv_carry_every_finding(self):
        import json

        findings = audit_whole_stack()
        payload = json.loads(sa.render_json(findings, {"functions": 2}, 0))
        self.assertEqual(payload["audit"], "serverless_audit")
        self.assertEqual(payload["day"], "04")
        self.assertEqual(payload["compliance_score"], 0)
        self.assertEqual(payload["finding_count"], 14)
        self.assertEqual(len(payload["findings"]), 14)

        rows = sa.render_csv(findings).strip().splitlines()
        self.assertEqual(len(rows), 15, "14 findings plus a header row")
        self.assertTrue(rows[0].startswith("check_id,severity,weight"))

    def test_table_reports_the_score_and_every_finding(self):
        findings = audit_whole_stack()
        table = sa.render_table(
            findings,
            {"functions": 2, "roles": 2, "topics": 2, "queues": 2, "rules": 2},
            sa.calculate_score(findings),
            use_colour=False,
        )
        self.assertIn("SERVERLESS COMPLIANCE AUDIT", table)
        self.assertIn("COMPLIANCE SCORE: 0/100", table)
        for check_id in ("CMP-001", "CMP-011", "CMP-015"):
            self.assertIn(check_id, table)
        self.assertNotIn("\033[", table, "colour must be off when asked")


if __name__ == "__main__":
    unittest.main(verbosity=2)
