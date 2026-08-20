#!/usr/bin/env python3
"""
test_checks.py — unit tests for Day 06's obs_audit.py.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

    cd lab/python
    python3 -m unittest discover -s tests

47 tests. No credentials, no AWS account, no network, no pytest — stdlib
unittest and nothing else. They run in well under a second, which is the
point: a test suite you can run on every save is a test suite you actually
run.

Composition
-----------
    16  FIRE      one per check, proving it catches the fault it exists for
    16  SILENT    one per check, proving it does NOT fire on correct input
    15  WHOLE     the stack totals, the score, the two silent checks, the
                  deliberate check interactions, the parsing helpers, and the
                  three renderers

The fire/silent pairing is deliberate and is worth more than either half
alone. A check with only a fire test is a check that might flag everything;
half the value of a linter is what it stays quiet about. Any check that cannot
be made to shut up on correct input is a check that will be suppressed in week
two, at which point it does nothing at all.

Why these fixtures are dictionaries and not a live account
----------------------------------------------------------
Day 05's whole-stack tests read the real ../terraform directory from disk,
because its checks parse files. Day 06's checks read AWS, and an audit suite
that needs an account is an audit suite that runs once a quarter.

So the fixtures below are a faithful snapshot of what main.tf creates, in
exactly the shape ObservabilityAuditor.collect() emits. That is the whole
reason every check is a pure function of a dict: the reasoning is testable in
a millisecond, and the only untested part is the boto3 plumbing that fetches
the dict — which is the part least likely to be wrong and most likely to be
caught the first time you run it for real.

The cost of that choice is honest: if collect() ever returns a different shape
from these fixtures, every test here passes and the tool is broken. Keeping
them in step is a manual discipline. It is the same trade as any fixture-based
suite and it is worth naming rather than pretending away.

=============================================================================
DAY 06 FINDING CONTRACT — LOCKED AT CP2
=============================================================================
This block is reproduced identically in five places. Change one, change all
five: README.md, lab/README.md, lab/terraform/outputs.tf (next_steps),
lab/python/obs_audit.py (module docstring), lab/python/tests/test_checks.py.

Weights are the repo-wide ones, identical to Days 03, 04 and 05:
CRITICAL 25, HIGH 10, MEDIUM 4, LOW 1, INFO 0. Score is 100 minus the sum,
floored at 0. Grades: 90+ A, 75+ B, 60+ C, 40+ D, below that F.

STATIC STATE — after terraform apply with the shipped defaults
(create_insecure_examples = true, enable_bedrock_invocation_logging = false),
before anything has been invoked.

  ID       SEVERITY   W   N  PTS  SOURCE RESOURCE
  -------  --------  --  --  ---  ------------------------------------------
  OBS-001  HIGH      10   1   10  aws_cloudwatch_log_group.unretained
  OBS-002  MEDIUM     4   2    8  aws_cloudwatch_log_group.unretained
                                  aws_cloudwatch_log_group.write_only
  OBS-003  CRITICAL  25   1   25  aws_cloudwatch_log_metric_filter.high_cardinality
  OBS-004  HIGH      10   1   10  aws_cloudwatch_metric_alarm.orphan
  OBS-005  MEDIUM     4   1    4  aws_cloudwatch_metric_alarm.orphan
  OBS-006  MEDIUM     4   1    4  aws_cloudwatch_metric_alarm.orphan
  OBS-007  HIGH      10   1   10  aws_cloudwatch_composite_alarm.impossible
  OBS-008  MEDIUM     4   1    4  aws_cloudwatch_dashboard.broken
  OBS-009  HIGH      10   0    0  none — SILENT BY SITUATION, see below
  OBS-010  LOW        1   1    1  aws_cloudwatch_metric_alarm.orphan
  OBS-011  CRITICAL  25   1   25  aws_lambda_function.naive_analyser
  OBS-012  HIGH      10   1   10  aws_lambda_function.naive_analyser
  OBS-013  HIGH      10   0    0  none — SILENT BY DESIGN, see below
  OBS-014  CRITICAL  25   1   25  aws_iam_role_policy.naive_analyser
  OBS-015  MEDIUM     4   1    4  aws_cloudwatch_log_group.naive_analyser
  OBS-016  MEDIUM     4   1    4  account-level Bedrock invocation logging
  -------  --------  --  --  ---  ------------------------------------------
  TOTALS                    15  144

  FIFTEEN findings from SIXTEEN checks. Check count and finding count are not
  the same number and never will be: OBS-002 fires twice, and OBS-009 and
  OBS-013 do not fire at all. If you are reconciling this table against a real
  run, reconcile the N column, not the number of rows.

  Score: 100 - 144 = -44, floored to 0/100. Grade F.

THE THREE STATES

  STATE                                        FINDINGS  POINTS    SCORE  GRADE
  -------------------------------------------  --------  ------  -------  -----
  Static: after apply, before anything runs          15     144    0/100      F
  Live: after lab steps 1-6 — incident
    generated, alarms transitioned, composite
    proven, both analysers run                       15     144    0/100      F
  After lab step 8 — bedrock_region pointed at
    another region, and the no-telemetry
    alarm's treat_missing_data changed to
    notBreaching outside Terraform                   18     174    0/100      F
  -------------------------------------------  --------  ------  -------  -----
  Reference build: create_insecure_examples =
    false AND enable_bedrock_invocation_logging
    = true                                            0       0  100/100      A

  STATIC AND LIVE ARE IDENTICAL, AND THAT IS THE POINT. obs_audit.py audits
  CONFIGURATION, not runtime. Generating a real incident, watching three
  alarms transition, paging yourself and running both analysers changes
  nothing in its output. A
  configuration auditor and a monitoring system answer different questions,
  and treating either one as the other is the category error this day exists
  to prevent.

  Setting create_insecure_examples = false on its own leaves exactly one
  finding — OBS-016 — for 4 points and 96/100, grade A. Both toggles are
  needed for 100/100, and turning invocation logging on obliges you to set
  retention and a resource policy on its destination log group. That is
  stated in the variable description and it is not optional.

  Step 8 adds THREE findings, not two: OBS-009 once, and OBS-013 twice.
  bedrock_region is a single variable feeding BOTH analysers, so pointing it
  at another region moves the good one's log data as well as the naive one's.
  That is worth noticing — the misconfiguration is in a shared setting, and a
  shared setting does not care which of your functions was carefully written.

SILENT BY DESIGN — OBS-013, log data crossing a region boundary to reach the
model. bedrock_region defaults to the empty string, which resolves to
aws_region, and the model ARN in the analyser's IAM policy is built from that
same resolved value. No combination of shipped defaults can put the logs and
the model in different regions. The check fires only if you edit a variable on
purpose, which lab step 8 asks you to do. A check that stays silent because
the stack cannot produce the misconfiguration is evidence that the auditor
does not cry wolf.

SILENT BY SITUATION — OBS-009, no liveness alarm anywhere in the region. This
is silent only because aws_cloudwatch_metric_alarm.no_telemetry happens to
exist with treat_missing_data set to breaching. Nothing structural prevents it
firing. One attribute on one alarm, changed in the console in thirty seconds,
and it fires — which is exactly what lab step 8 does.

THE DIFFERENCE MATTERS. Silent by design tells you something about the
auditor. Silent by situation tells you nothing about the auditor and
everything about today's configuration. Never read the second as the first: a
check that is silent by situation must be re-run, never assumed.

CHECK INTERACTIONS THAT ARE DELIBERATE, NOT BUGS

  OBS-001 skips the log group of any function that holds bedrock:InvokeModel;
  OBS-015 owns those. An unretained analyser log group is ONE finding, not
  two.

  OBS-002 skips log groups under /aws/lambda/. A function's own execution log
  is a diagnostic artefact, not a data feed, and having no metric filter on it
  is correct rather than negligent.

  OBS-004 exempts alarms referenced by a composite alarm's rule. The three
  metric alarms in main.tf section 5 have no actions and are correct, because
  the composite in section 7 notifies on their behalf.

  OBS-006 exempts liveness alarms — treat_missing_data set to breaching with
  a LessThan comparison. A dead-man's switch is legitimately a raw count, and
  flagging it would be the auditor crying wolf about the best alarm in the
  stack.

  OBS-004's composite exemption only counts a composite that notifies AND
  whose rule can actually fire. An alarm watched solely by an unsatisfiable
  composite is exactly as silent as an orphan — and worse, because a reviewer
  scanning for orphans sees the reference and moves on. So OBS-007 firing on a
  composite also makes OBS-004 fire on its children. In this stack that is
  precisely what happens: the orphan alarm IS referenced, by the deliberately
  impossible composite, and is still reported as notifying nobody. Cause and
  consequence, not duplicates — fixing the rule clears both.
=============================================================================
"""

import importlib
import io
import json
import os
import sys
import unittest

_PYTHON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PYTHON_DIR)
sys.path.insert(0, os.path.join(_PYTHON_DIR, "challenge"))

# Which implementation to test. Defaults to the reference; set the environment
# variable to point the whole suite at your own work while building it:
#
#     OBS_AUDIT_MODULE=obs_audit_challenge python3 -m unittest discover -s tests
#
# That is the offline feedback loop for challenge/obs_audit_challenge.py — 47
# tests, no credentials, no account, under a second.
A = importlib.import_module(os.environ.get("OBS_AUDIT_MODULE", "obs_audit"))


###############################################################################
# The contract, as numbers the tests assert on.
#
# Change these only when the reference Terraform changes, and when you do,
# change all five places that quote them. /home/claude/sync_contract.py exists
# for exactly that reason and the CP6 sweep runs it.
###############################################################################

EXPECTED_STATIC_FINDINGS = 15
EXPECTED_STATIC_WEIGHT = 144
EXPECTED_STEP8_FINDINGS = 18
EXPECTED_STEP8_WEIGHT = 174
EXPECTED_PARTIAL_FINDINGS = 1
EXPECTED_PARTIAL_WEIGHT = 4


###############################################################################
# Fixtures — a faithful snapshot of lab/terraform
###############################################################################

SUFFIX = "abc123"
P = "cbc-day06"
NS = "CareerByteCode/Day06"
REGION = "us-east-1"

WORKLOAD_LG = f"/{P}/workload-{SUFFIX}"
CHAOS_LG = f"/aws/lambda/{P}-chaos-{SUFFIX}"
ANALYSER_LG = f"/aws/lambda/{P}-analyser-{SUFFIX}"
NAIVE_LG = f"/aws/lambda/{P}-naive-analyser-{SUFFIX}"
LEGACY_LG = f"/{P}/legacy-app-{SUFFIX}"
WRITEONLY_LG = f"/{P}/write-only-{SUFFIX}"

ERROR_RATE = f"{P}-error-rate-{SUFFIX}"
LATENCY = f"{P}-latency-p95-{SUFFIX}"
NO_TELEMETRY = f"{P}-no-telemetry-{SUFFIX}"
ORPHAN = f"{P}-orphan-no-action-{SUFFIX}"
COMPOSITE = f"{P}-service-degraded-{SUFFIX}"
IMPOSSIBLE = f"{P}-impossible-{SUFFIX}"

ANALYSER_FN = f"{P}-analyser-{SUFFIX}"
NAIVE_FN = f"{P}-naive-analyser-{SUFFIX}"
CHAOS_FN = f"{P}-chaos-{SUFFIX}"

TOPIC = f"arn:aws:sns:{REGION}:111122223333:{P}-alerts-{SUFFIX}"
MODEL_ARN = (
    f"arn:aws:bedrock:{REGION}::foundation-model/"
    "anthropic.claude-3-5-haiku-20241022-v1:0"
)

BEDROCK_ACTIONS = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]


def log_group(name, retention=None, stored=4096, cls="STANDARD"):
    group = {
        "logGroupName": name,
        "arn": f"arn:aws:logs:{REGION}:111122223333:log-group:{name}",
        "storedBytes": stored,
        "logGroupClass": cls,
    }
    if retention:
        group["retentionInDays"] = retention
    return group


def metric_filter(name, metric, value="1", dimensions=None, group=None):
    transform = {
        "metricName": metric,
        "metricNamespace": NS,
        "metricValue": value,
    }
    if dimensions:
        transform["dimensions"] = dimensions
    else:
        transform["defaultValue"] = 0.0
    return {
        "filterName": name,
        "logGroupName": group or WORKLOAD_LG,
        "filterPattern": '{ $.event = "request_completed" }',
        "metricTransformations": [transform],
    }


def alarm(name, **overrides):
    """A well-formed alarm. Every override below is a deliberate fault."""
    base = {
        "AlarmName": name,
        "Namespace": NS,
        "MetricName": "ErrorCount",
        "Statistic": "Average",
        "Period": 60,
        "ComparisonOperator": "GreaterThanThreshold",
        "Threshold": 5.0,
        "EvaluationPeriods": 5,
        "DatapointsToAlarm": 3,
        "TreatMissingData": "notBreaching",
        "ActionsEnabled": True,
        "AlarmActions": [TOPIC],
        "OKActions": [],
        "InsufficientDataActions": [],
        "StateValue": "OK",
    }
    base.update(overrides)
    return base


def bedrock_policy(resources):
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": BEDROCK_ACTIONS, "Resource": resources}
        ],
    }


def analyser_function(name=ANALYSER_FN, **env_overrides):
    env = {
        "WORKLOAD_LOG_GROUP": WORKLOAD_LG,
        "REDACT_LOGS": "true",
        "MAX_INPUT_TOKENS": "12000",
        "BEDROCK_REGION": REGION,
        "SAMPLE_STRATEGY": "balanced",
    }
    env.update(env_overrides)
    return {
        "FunctionName": name,
        "Role": f"arn:aws:iam::111122223333:role/{name}",
        "ReservedConcurrentExecutions": 2,
        "Environment": {"Variables": env},
    }


def operations_dashboard():
    return {
        "DashboardName": f"{P}-operations-{SUFFIX}",
        "DashboardBody": {
            "widgets": [
                {"type": "alarm", "properties": {"alarms": [COMPOSITE]}},
                {
                    "type": "metric",
                    "properties": {
                        "region": REGION,
                        "metrics": [
                            [NS, "ErrorCount", {"id": "err", "visible": False}],
                            [NS, "RequestCount", {"id": "req", "visible": False}],
                            [{"id": "rate", "expression": "IF(req>0,100*err/req,0)"}],
                        ],
                    },
                },
                {
                    "type": "metric",
                    "properties": {
                        "region": REGION,
                        "metrics": [
                            [NS, "LatencyMillis", {"stat": "p50"}],
                            ["...", {"stat": "p95"}],
                            ["...", {"stat": "p99"}],
                        ],
                    },
                },
                {
                    "type": "metric",
                    "properties": {
                        "region": REGION,
                        "metrics": [
                            [NS, "ErrorCountByType", "ErrorType", "DB_CONN_TIMEOUT"],
                            ["...", "POOL_EXHAUSTED"],
                        ],
                    },
                },
                {"type": "log", "properties": {"query": "SOURCE 'x' | fields @message"}},
                {"type": "text", "properties": {"markdown": "how to read this"}},
            ]
        },
    }


def broken_dashboard():
    return {
        "DashboardName": f"{P}-broken-{SUFFIX}",
        "DashboardBody": {
            "widgets": [
                {
                    "type": "metric",
                    "properties": {
                        "region": REGION,
                        "metrics": [
                            [NS, "CheckoutSuccessRate", {"stat": "Average"}],
                            ["CareerByteCode/Day05", "DriftDetected", {"stat": "Sum"}],
                        ],
                    },
                }
            ]
        },
    }


def reference_stack(invocation_logging=True):
    """The stack with create_insecure_examples = false. Nothing is wrong here.

    Every SILENT test starts from this. If a check fires against it, the check
    has a false positive, and a false positive is how a tool gets muted.
    """
    stack = {
        "region": REGION,
        "log_groups": [
            log_group(WORKLOAD_LG, 7, stored=110_000),
            log_group(CHAOS_LG, 7),
            log_group(ANALYSER_LG, 7),
        ],
        "metric_filters": [
            metric_filter(f"{P}-requests", "RequestCount"),
            metric_filter(f"{P}-errors", "ErrorCount"),
            metric_filter(
                f"{P}-errors-by-type",
                "ErrorCountByType",
                dimensions={"ErrorType": "$.error_type"},
            ),
            metric_filter(f"{P}-latency", "LatencyMillis", value="$.latency_ms"),
        ],
        "subscription_filters": [],
        "metric_alarms": [
            alarm(
                ERROR_RATE,
                AlarmActions=[],
                Statistic=None,
                Metrics=[
                    {"Id": "m_errors", "ReturnData": False},
                    {"Id": "m_requests", "ReturnData": False},
                    {"Id": "e", "Expression": "FILL(m_errors, 0)"},
                    {"Id": "r", "Expression": "FILL(m_requests, 0)"},
                    {"Id": "error_rate", "Expression": "IF(r > 0, 100 * e / r, 0)",
                     "ReturnData": True},
                ],
            ),
            alarm(
                LATENCY,
                AlarmActions=[],
                Statistic=None,
                ExtendedStatistic="p95",
                MetricName="LatencyMillis",
                Threshold=2000.0,
            ),
            alarm(
                NO_TELEMETRY,
                AlarmActions=[],
                MetricName="RequestCount",
                Statistic="Sum",
                ComparisonOperator="LessThanThreshold",
                Threshold=1.0,
                EvaluationPeriods=10,
                DatapointsToAlarm=10,
                TreatMissingData="breaching",
            ),
        ],
        "composite_alarms": [
            {
                "AlarmName": COMPOSITE,
                "AlarmRule": (
                    f"ALARM({ERROR_RATE}) OR ALARM({LATENCY}) OR ALARM({NO_TELEMETRY})"
                ),
                "ActionsEnabled": True,
                "AlarmActions": [TOPIC],
                "OKActions": [TOPIC],
                "StateValue": "OK",
            }
        ],
        "dashboards": [operations_dashboard()],
        "existing_metrics": [
            {"Namespace": NS, "MetricName": "RequestCount", "Dimensions": []},
            {"Namespace": NS, "MetricName": "ErrorCount", "Dimensions": []},
            {"Namespace": NS, "MetricName": "LatencyMillis", "Dimensions": []},
            {"Namespace": NS, "MetricName": "ErrorCountByType", "Dimensions": []},
        ],
        "lambda_functions": [
            {
                "FunctionName": CHAOS_FN,
                "Role": f"arn:aws:iam::111122223333:role/{CHAOS_FN}",
                "Environment": {"Variables": {"WORKLOAD_LOG_GROUP": WORKLOAD_LG}},
            },
            analyser_function(),
        ],
        "role_policies": {
            CHAOS_FN: [
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": ["logs:PutLogEvents"],
                            "Resource": [f"{WORKLOAD_LG}:*"],
                        }
                    ],
                }
            ],
            ANALYSER_FN: [bedrock_policy([MODEL_ARN])],
        },
        "bedrock_logging": None,
    }
    if invocation_logging:
        stack["bedrock_logging"] = {
            "loggingConfig": {
                "textDataDeliveryEnabled": True,
                "cloudWatchConfig": {
                    "logGroupName": f"/{P}/bedrock-invocations-{SUFFIX}",
                    "roleArn": f"arn:aws:iam::111122223333:role/{P}-bedrock-logging",
                },
            }
        }
    return stack


def static_stack():
    """create_insecure_examples = true, invocation logging off. The lab default."""
    stack = reference_stack(invocation_logging=False)
    stack["log_groups"] += [
        log_group(LEGACY_LG, None, stored=8192),
        log_group(WRITEONLY_LG, 7, stored=0),
        log_group(NAIVE_LG, None),
    ]
    stack["metric_filters"].append(
        metric_filter(
            f"{P}-per-request",
            "RequestsByRequestId",
            dimensions={"RequestId": "$.request_id"},
        )
    )
    stack["metric_alarms"].append(
        alarm(
            ORPHAN,
            AlarmActions=[],
            Statistic="Sum",
            Period=300,
            Threshold=50.0,
            EvaluationPeriods=1,
            DatapointsToAlarm=None,
            TreatMissingData="missing",
            StateValue="INSUFFICIENT_DATA",
        )
    )
    stack["composite_alarms"].append(
        {
            "AlarmName": IMPOSSIBLE,
            "AlarmRule": f"ALARM({ORPHAN}) AND OK({ORPHAN})",
            "ActionsEnabled": True,
            "AlarmActions": [TOPIC],
            "StateValue": "OK",
        }
    )
    stack["dashboards"].append(broken_dashboard())
    stack["existing_metrics"].append(
        {"Namespace": NS, "MetricName": "RequestsByRequestId", "Dimensions": []}
    )
    stack["lambda_functions"].append(
        analyser_function(NAIVE_FN, REDACT_LOGS="false", MAX_INPUT_TOKENS="0",
                          SAMPLE_STRATEGY="tail")
    )
    stack["role_policies"][NAIVE_FN] = [bedrock_policy("*")]
    return stack


def run_all(stack, region=REGION):
    findings = []
    for check_id, check in A.CHECKS:
        for finding in check(stack, region):
            assert finding.check_id == check_id, (
                f"{check_id} produced a finding labelled {finding.check_id}"
            )
            findings.append(finding)
    return findings


def ids(findings):
    return sorted(f.check_id for f in findings)


###############################################################################
# FIRE — 16 tests, one per check
###############################################################################


class FireTests(unittest.TestCase):
    """Each check catches the fault it exists for."""

    def test_obs_001_fires_on_log_group_without_retention(self):
        stack = reference_stack()
        stack["log_groups"].append(log_group(LEGACY_LG, None))
        found = A.check_log_retention(stack, REGION)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "HIGH")
        self.assertEqual(found[0].resource_id, LEGACY_LG)

    def test_obs_002_fires_on_log_group_nothing_reads(self):
        stack = reference_stack()
        stack["log_groups"].append(log_group(WRITEONLY_LG, 7))
        found = A.check_write_only_log_group(stack, REGION)
        self.assertEqual([f.resource_id for f in found], [WRITEONLY_LG])
        self.assertEqual(found[0].severity, "MEDIUM")

    def test_obs_003_fires_on_request_id_dimension(self):
        stack = reference_stack()
        stack["metric_filters"].append(
            metric_filter(
                f"{P}-per-request",
                "RequestsByRequestId",
                dimensions={"RequestId": "$.request_id"},
            )
        )
        found = A.check_metric_filter_cardinality(stack, REGION)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "CRITICAL")
        self.assertIn("RequestId", found[0].evidence["dimensions"])

    def test_obs_004_fires_on_alarm_with_no_actions(self):
        stack = reference_stack()
        stack["metric_alarms"].append(alarm(ORPHAN, AlarmActions=[], OKActions=[]))
        found = A.check_alarm_without_action(stack, REGION)
        self.assertEqual([f.resource_id for f in found], [ORPHAN])
        self.assertEqual(found[0].severity, "HIGH")

    def test_obs_005_fires_when_treat_missing_data_is_default(self):
        stack = reference_stack()
        stack["metric_alarms"].append(alarm(ORPHAN, TreatMissingData=None))
        found = A.check_treat_missing_data(stack, REGION)
        self.assertEqual([f.resource_id for f in found], [ORPHAN])

    def test_obs_006_fires_on_raw_count_threshold(self):
        stack = reference_stack()
        stack["metric_alarms"].append(
            alarm(ORPHAN, Statistic="Sum", Threshold=50.0,
                  ComparisonOperator="GreaterThanThreshold")
        )
        found = A.check_alarm_on_raw_count(stack, REGION)
        self.assertEqual([f.resource_id for f in found], [ORPHAN])
        self.assertEqual(found[0].evidence["Statistic"], "Sum")

    def test_obs_007_fires_on_unsatisfiable_composite_rule(self):
        stack = reference_stack()
        stack["metric_alarms"].append(alarm(ORPHAN))
        stack["composite_alarms"].append(
            {
                "AlarmName": IMPOSSIBLE,
                "AlarmRule": f"ALARM({ORPHAN}) AND OK({ORPHAN})",
                "ActionsEnabled": True,
                "AlarmActions": [TOPIC],
            }
        )
        found = A.check_composite_alarm_satisfiable(stack, REGION)
        self.assertEqual([f.resource_id for f in found], [IMPOSSIBLE])
        self.assertTrue(
            any("simultaneously" in p for p in found[0].evidence["problems"])
        )

    def test_obs_008_fires_on_dashboard_metric_that_does_not_exist(self):
        stack = reference_stack()
        stack["dashboards"].append(broken_dashboard())
        found = A.check_dashboard_metrics_exist(stack, REGION)
        self.assertEqual(len(found), 1)
        self.assertIn(
            f"{NS}/CheckoutSuccessRate", found[0].evidence["missing_metrics"]
        )

    def test_obs_009_fires_when_nothing_treats_missing_data_as_breaching(self):
        stack = reference_stack()
        for a in stack["metric_alarms"]:
            a["TreatMissingData"] = "notBreaching"
        found = A.check_liveness_alarm_exists(stack, REGION)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "HIGH")

    def test_obs_010_fires_on_one_of_one_alarm(self):
        stack = reference_stack()
        stack["metric_alarms"].append(
            alarm(ORPHAN, EvaluationPeriods=1, DatapointsToAlarm=None)
        )
        found = A.check_single_datapoint_alarm(stack, REGION)
        self.assertEqual([f.resource_id for f in found], [ORPHAN])
        self.assertEqual(found[0].severity, "LOW")

    def test_obs_011_fires_when_redaction_is_off(self):
        stack = reference_stack()
        stack["lambda_functions"].append(analyser_function(NAIVE_FN, REDACT_LOGS="false"))
        stack["role_policies"][NAIVE_FN] = [bedrock_policy([MODEL_ARN])]
        found = A.check_prompt_redaction(stack, REGION)
        self.assertEqual([f.resource_id for f in found], [NAIVE_FN])
        self.assertEqual(found[0].severity, "CRITICAL")

    def test_obs_012_fires_when_there_is_no_token_budget(self):
        stack = reference_stack()
        stack["lambda_functions"].append(
            analyser_function(NAIVE_FN, MAX_INPUT_TOKENS="0")
        )
        stack["role_policies"][NAIVE_FN] = [bedrock_policy([MODEL_ARN])]
        found = A.check_token_budget(stack, REGION)
        self.assertEqual([f.resource_id for f in found], [NAIVE_FN])

    def test_obs_013_fires_when_the_model_is_in_another_region(self):
        stack = reference_stack()
        stack["lambda_functions"] = [analyser_function(BEDROCK_REGION="eu-west-1")]
        found = A.check_cross_region_model(stack, REGION)
        self.assertEqual([f.resource_id for f in found], [ANALYSER_FN])
        self.assertEqual(found[0].evidence["BEDROCK_REGION"], "eu-west-1")

    def test_obs_014_fires_on_wildcard_bedrock_resource(self):
        stack = reference_stack()
        stack["lambda_functions"].append(analyser_function(NAIVE_FN))
        stack["role_policies"][NAIVE_FN] = [bedrock_policy("*")]
        found = A.check_bedrock_resource_scope(stack, REGION)
        self.assertEqual([f.resource_id for f in found], [NAIVE_FN])
        self.assertEqual(found[0].severity, "CRITICAL")

    def test_obs_015_fires_when_the_analyser_log_group_is_unretained(self):
        stack = reference_stack()
        stack["lambda_functions"].append(analyser_function(NAIVE_FN))
        stack["role_policies"][NAIVE_FN] = [bedrock_policy([MODEL_ARN])]
        stack["log_groups"].append(log_group(NAIVE_LG, None))
        found = A.check_analyser_log_retention(stack, REGION)
        self.assertEqual([f.resource_id for f in found], [NAIVE_LG])
        self.assertTrue(found[0].evidence["exists"])

    def test_obs_016_fires_when_invocation_logging_is_off(self):
        stack = reference_stack(invocation_logging=False)
        found = A.check_model_invocation_logging(stack, REGION)
        self.assertEqual(len(found), 1)
        self.assertIn(ANALYSER_FN, found[0].evidence["bedrock_invoking_functions"])


###############################################################################
# SILENT — 16 tests, one per check
###############################################################################


class SilentTests(unittest.TestCase):
    """Each check stays quiet on input that is correct.

    Every one of these starts from reference_stack(), which is the stack with
    create_insecure_examples = false. Nothing in it is wrong, and a check that
    cannot stay silent against it will be muted by its users within a fortnight.
    """

    def test_obs_001_silent_when_every_group_has_retention(self):
        self.assertEqual(A.check_log_retention(reference_stack(), REGION), [])

    def test_obs_002_silent_on_lambda_groups_and_filtered_groups(self):
        # The workload group has four metric filters; the two /aws/lambda/
        # groups are execution logs and correctly have none.
        self.assertEqual(A.check_write_only_log_group(reference_stack(), REGION), [])

    def test_obs_003_silent_on_a_bounded_dimension(self):
        # ErrorType takes four values, from a tuple in chaos_workload.py.
        self.assertEqual(
            A.check_metric_filter_cardinality(reference_stack(), REGION), []
        )

    def test_obs_004_silent_on_alarms_covered_by_a_working_composite(self):
        # The three alarms in main.tf section 5 have no actions ON PURPOSE.
        self.assertEqual(A.check_alarm_without_action(reference_stack(), REGION), [])

    def test_obs_005_silent_when_every_alarm_sets_it_explicitly(self):
        self.assertEqual(A.check_treat_missing_data(reference_stack(), REGION), [])

    def test_obs_006_silent_on_metric_math_and_percentile_alarms(self):
        self.assertEqual(A.check_alarm_on_raw_count(reference_stack(), REGION), [])

    def test_obs_007_silent_on_a_satisfiable_or_rule(self):
        self.assertEqual(
            A.check_composite_alarm_satisfiable(reference_stack(), REGION), []
        )

    def test_obs_008_silent_when_every_referenced_metric_exists(self):
        self.assertEqual(
            A.check_dashboard_metrics_exist(reference_stack(), REGION), []
        )

    def test_obs_009_silent_when_a_dead_mans_switch_exists(self):
        self.assertEqual(A.check_liveness_alarm_exists(reference_stack(), REGION), [])
        # And silent on a region with no alarms at all, which has a bigger
        # problem than this check; reporting it would be noise on top of noise.
        empty = reference_stack()
        empty["metric_alarms"] = []
        self.assertEqual(A.check_liveness_alarm_exists(empty, REGION), [])

    def test_obs_013_silent_when_the_model_is_in_the_same_region(self):
        # Silent by DESIGN against this stack — see the contract. One variable
        # feeds both the client region and the model ARN, so the two cannot
        # diverge without somebody editing it on purpose.
        self.assertEqual(A.check_cross_region_model(reference_stack(), REGION), [])

    def test_obs_010_silent_on_three_of_five(self):
        self.assertEqual(A.check_single_datapoint_alarm(reference_stack(), REGION), [])

    def test_obs_011_silent_when_redaction_is_on(self):
        self.assertEqual(A.check_prompt_redaction(reference_stack(), REGION), [])

    def test_obs_012_silent_when_a_budget_is_set(self):
        self.assertEqual(A.check_token_budget(reference_stack(), REGION), [])

    def test_obs_014_silent_on_a_scoped_model_arn(self):
        self.assertEqual(A.check_bedrock_resource_scope(reference_stack(), REGION), [])

    def test_obs_015_silent_when_the_analyser_group_has_retention(self):
        self.assertEqual(
            A.check_analyser_log_retention(reference_stack(), REGION), []
        )

    def test_obs_016_silent_when_nothing_invokes_a_model(self):
        stack = reference_stack(invocation_logging=False)
        stack["lambda_functions"] = [
            f for f in stack["lambda_functions"] if f["FunctionName"] == CHAOS_FN
        ]
        stack["role_policies"].pop(ANALYSER_FN)
        # No model-invoking function means no invocation log is needed, and
        # reporting one would be the auditor inventing work.
        self.assertEqual(A.check_model_invocation_logging(stack, REGION), [])


###############################################################################
# WHOLE STACK — 15 tests
###############################################################################


class WholeStackTests(unittest.TestCase):
    """The contract, asserted. These are the numbers five documents quote."""

    def test_static_totals_match_the_contract(self):
        findings = run_all(static_stack())
        self.assertEqual(len(findings), EXPECTED_STATIC_FINDINGS)
        self.assertEqual(
            sum(f.weight for f in findings), EXPECTED_STATIC_WEIGHT
        )
        self.assertEqual(A.calculate_score(findings), 0)
        self.assertTrue(A.score_grade(0).startswith("F"))
        # Fifteen findings from sixteen checks: OBS-002 twice, two silent.
        self.assertEqual(ids(findings).count("OBS-002"), 2)
        self.assertNotIn("OBS-009", ids(findings))
        self.assertNotIn("OBS-013", ids(findings))

    def test_every_finding_is_well_formed(self):
        for finding in run_all(static_stack()):
            with self.subTest(check=finding.check_id):
                self.assertIn(finding.severity, A.SEVERITY_ORDER)
                self.assertRegex(finding.check_id, r"^OBS-0\d\d$")
                self.assertTrue(finding.resource_type.startswith("AWS::"))
                self.assertTrue(finding.resource_id)
                self.assertGreater(len(finding.detail), 80)
                self.assertGreater(len(finding.remediation), 80)
                self.assertIsInstance(finding.evidence, dict)
                self.assertEqual(finding.region, REGION)
                # A finding must survive a round trip through JSON, because
                # --format json is how this reaches a pipeline.
                json.dumps(finding.to_dict(), default=str)

    def test_reference_build_produces_zero_findings(self):
        """THE test. create_insecure_examples = false plus invocation logging on.

        A tool that cannot return a clean result on clean input is a tool
        nobody can ever finish fixing things with. Every remediation in this
        file has to be reachable, and this asserts the destination exists.
        """
        findings = run_all(reference_stack(invocation_logging=True))
        self.assertEqual(findings, [])
        self.assertEqual(A.calculate_score(findings), 100)
        self.assertTrue(A.score_grade(100).startswith("A"))

    def test_the_other_contract_rows_match(self):
        # Lab step 8: the model moved to another region AND the dead-man's
        # switch neutered outside Terraform.
        step8 = static_stack()
        for a in step8["metric_alarms"]:
            if a["AlarmName"] == NO_TELEMETRY:
                a["TreatMissingData"] = "notBreaching"
        for fn in step8["lambda_functions"]:
            variables = (fn.get("Environment") or {}).get("Variables") or {}
            if "BEDROCK_REGION" in variables:
                variables["BEDROCK_REGION"] = "eu-west-1"
        findings = run_all(step8)
        self.assertEqual(len(findings), EXPECTED_STEP8_FINDINGS)
        self.assertEqual(sum(f.weight for f in findings), EXPECTED_STEP8_WEIGHT)
        # OBS-013 fires TWICE: bedrock_region is one variable feeding both
        # analysers, and a shared setting does not care which of your
        # functions was carefully written.
        self.assertEqual(ids(findings).count("OBS-013"), 2)

        # Partial clean: insecure examples off, invocation logging still off.
        partial = run_all(reference_stack(invocation_logging=False))
        self.assertEqual(len(partial), EXPECTED_PARTIAL_FINDINGS)
        self.assertEqual(sum(f.weight for f in partial), EXPECTED_PARTIAL_WEIGHT)
        self.assertEqual(ids(partial), ["OBS-016"])
        self.assertEqual(A.calculate_score(partial), 96)

    def test_min_severity_filters_display_but_never_the_score(self):
        findings = run_all(static_stack())
        shown = A.filter_by_severity(findings, "HIGH")
        self.assertEqual(len(shown), 7)
        self.assertLess(len(shown), len(findings))
        # The score must reflect EVERY finding. Otherwise people "improve"
        # their posture by passing --min-severity CRITICAL.
        self.assertEqual(A.calculate_score(findings), 0)
        self.assertTrue(all(f.severity in ("CRITICAL", "HIGH") for f in shown))


class SilenceIsEvidenceTests(unittest.TestCase):
    """The two checks that stay quiet against the lab stack, and why.

    Silent by design tells you something about the auditor. Silent by
    situation tells you nothing about the auditor and everything about today's
    configuration. Reading the second as the first is how a team concludes it
    has coverage it does not have.
    """

    def test_silent_by_design_and_silent_by_situation_are_different_things(self):
        # Both are quiet against the lab stack. They are quiet for reasons that
        # deserve opposite amounts of trust, and the contrast is the lesson.
        self.assertEqual(A.check_cross_region_model(static_stack(), REGION), [])
        self.assertEqual(A.check_liveness_alarm_exists(static_stack(), REGION), [])

        # OBS-013 is silent BY DESIGN: the stack cannot produce the fault,
        # because one variable feeds both the client region and the model ARN.
        # Making it fire takes a deliberate edit — lab step 8 — and it then
        # fires TWICE, because that variable is shared by both analysers.
        edited = static_stack()
        for fn in edited["lambda_functions"]:
            variables = (fn.get("Environment") or {}).get("Variables") or {}
            if "BEDROCK_REGION" in variables:
                variables["BEDROCK_REGION"] = "eu-west-1"
        self.assertEqual(len(A.check_cross_region_model(edited, REGION)), 2)

        # OBS-009 is silent BY SITUATION: only because ONE alarm happens to be
        # built correctly today. Nothing structural prevents it firing, and one
        # attribute changed in the console — no code review, no plan — brings
        # it straight back.
        neutered = static_stack()
        for a in neutered["metric_alarms"]:
            if a["AlarmName"] == NO_TELEMETRY:
                a["TreatMissingData"] = "notBreaching"
        self.assertEqual(len(A.check_liveness_alarm_exists(neutered, REGION)), 1)


class InteractionTests(unittest.TestCase):
    """The check interactions the contract calls deliberate.

    Each of these would look like a bug to somebody reading one check in
    isolation, which is exactly why they are written down and tested.
    """

    def test_obs_001_defers_to_obs_015_on_model_invoking_log_groups(self):
        stack = static_stack()
        naive_findings = [
            f for f in run_all(stack) if f.resource_id == NAIVE_LG
        ]
        # ONE finding, not two. OBS-015 owns it; OBS-001 steps aside.
        self.assertEqual([f.check_id for f in naive_findings], ["OBS-015"])
        self.assertNotIn(
            NAIVE_LG, [f.resource_id for f in A.check_log_retention(stack, REGION)]
        )

    def test_obs_004_exemption_requires_a_composite_that_actually_works(self):
        # Covered by a working composite -> silent.
        stack = reference_stack()
        stack["metric_alarms"].append(alarm(ORPHAN, AlarmActions=[]))
        stack["composite_alarms"][0]["AlarmRule"] += f" OR ALARM({ORPHAN})"
        self.assertEqual(A.check_alarm_without_action(stack, REGION), [])

        # Covered ONLY by an unsatisfiable composite -> still silent in
        # practice, so the check must still fire. This is the interaction
        # between OBS-007 and OBS-004: cause and consequence, not duplicates.
        broken = reference_stack()
        broken["metric_alarms"].append(alarm(ORPHAN, AlarmActions=[]))
        broken["composite_alarms"].append(
            {
                "AlarmName": IMPOSSIBLE,
                "AlarmRule": f"ALARM({ORPHAN}) AND OK({ORPHAN})",
                "ActionsEnabled": True,
                "AlarmActions": [TOPIC],
            }
        )
        self.assertEqual(
            [f.resource_id for f in A.check_alarm_without_action(broken, REGION)],
            [ORPHAN],
        )

        # And a composite with no action of its own is not cover either.
        silent_parent = reference_stack()
        silent_parent["metric_alarms"].append(alarm(ORPHAN, AlarmActions=[]))
        silent_parent["composite_alarms"].append(
            {
                "AlarmName": f"{P}-quiet-parent",
                "AlarmRule": f"ALARM({ORPHAN})",
                "ActionsEnabled": True,
                "AlarmActions": [],
            }
        )
        self.assertEqual(
            [f.resource_id for f in A.check_alarm_without_action(silent_parent, REGION)],
            [ORPHAN],
        )

    def test_obs_006_exempts_the_liveness_alarm_it_would_otherwise_flag(self):
        # no-telemetry alarms on the Sum of a raw count. That is what a
        # dead-man's switch IS, and flagging it would be crying wolf about the
        # best alarm in the stack.
        stack = reference_stack()
        self.assertEqual(A.check_alarm_on_raw_count(stack, REGION), [])

        # Take away only the breaching setting and the same alarm is now a
        # badly-designed count alarm, and the check says so.
        for a in stack["metric_alarms"]:
            if a["AlarmName"] == NO_TELEMETRY:
                a["TreatMissingData"] = "notBreaching"
                a["ComparisonOperator"] = "GreaterThanThreshold"
        self.assertEqual(
            [f.resource_id for f in A.check_alarm_on_raw_count(stack, REGION)],
            [NO_TELEMETRY],
        )


class HelperTests(unittest.TestCase):
    """The parsing helpers, which are where the subtle bugs live."""

    def test_policy_helpers_accept_every_shape_aws_returns(self):
        # as_list: a single string and a list of one are the same document,
        # and forgetting that is how wildcard detection develops a blind spot.
        self.assertEqual(A.as_list("bedrock:InvokeModel"), ["bedrock:InvokeModel"])
        self.assertEqual(A.as_list(None), [])
        self.assertEqual(A.as_list(["a", "b"]), ["a", "b"])

        # parse_policy: dict, JSON string, and URL-encoded JSON string.
        document = {"Statement": [{"Effect": "Allow", "Action": "bedrock:InvokeModel",
                                   "Resource": "*"}]}
        self.assertEqual(A.parse_policy(document), document)
        self.assertEqual(A.parse_policy(json.dumps(document)), document)
        from urllib.parse import quote
        self.assertEqual(A.parse_policy(quote(json.dumps(document))), document)
        self.assertEqual(A.parse_policy("not json at all"), {})
        self.assertEqual(A.parse_policy(None), {})

        granted, resources = A.policy_grants_bedrock_invoke(document)
        self.assertTrue(granted)
        self.assertEqual(resources, ["*"])

        # A Deny is not a grant, however loudly it names the action.
        denied = {"Statement": [{"Effect": "Deny", "Action": "bedrock:InvokeModel",
                                 "Resource": "*"}]}
        self.assertEqual(A.policy_grants_bedrock_invoke(denied), (False, []))

    def test_dashboard_refs_follow_the_ellipsis_shorthand(self):
        refs = A._dashboard_metric_refs(operations_dashboard()["DashboardBody"])
        # p50/p95/p99 are three series from ONE ellipsis chain, and the
        # ErrorCountByType widget adds two more. A parser that ignores "..."
        # reports every such widget as broken.
        self.assertEqual(refs.count((NS, "LatencyMillis")), 3)
        self.assertEqual(refs.count((NS, "ErrorCountByType")), 2)
        # The pure metric-math entry references series ids, not a metric.
        self.assertNotIn((NS, "rate"), refs)
        # alarm, log and text widgets contribute nothing.
        self.assertTrue(all(ns == NS for ns, _mn in refs))

    def test_composite_rule_problems_finds_both_faults_and_no_others(self):
        known = {ORPHAN, ERROR_RATE, LATENCY}
        self.assertEqual(A.composite_rule_problems(f"ALARM({ORPHAN})", known), [])
        self.assertEqual(
            A.composite_rule_problems(f"ALARM({ERROR_RATE}) OR ALARM({LATENCY})", known),
            [],
        )
        # Contradiction inside an AND chain.
        self.assertTrue(
            A.composite_rule_problems(f"ALARM({ORPHAN}) AND OK({ORPHAN})", known)
        )
        # The same two states are perfectly satisfiable once an OR appears.
        self.assertEqual(
            A.composite_rule_problems(f"ALARM({ORPHAN}) OR OK({ORPHAN})", known), []
        )
        # A dangling reference leaves the composite in INSUFFICIENT_DATA.
        problems = A.composite_rule_problems("ALARM(deleted-last-year)", known)
        self.assertTrue(any("unknown alarm" in p for p in problems))


class RendererTests(unittest.TestCase):
    """The three output formats. Each one is somebody's integration point."""

    # Built directly rather than by running the checks, on purpose. A renderer
    # test that depends on the checks tells you nothing when a check is broken
    # — it just fails alongside it — and it makes this suite useless as a
    # feedback loop for challenge/obs_audit_challenge.py, where the checks are
    # deliberately empty. The severity histogram below reproduces the finding
    # contract independently: 3 CRITICAL, 4 HIGH, 7 MEDIUM, 1 LOW = 15
    # findings and 144 points, arrived at without touching a single check.
    CONTRACT_SHAPE = [
        ("OBS-003", "CRITICAL"), ("OBS-011", "CRITICAL"), ("OBS-014", "CRITICAL"),
        ("OBS-001", "HIGH"), ("OBS-004", "HIGH"), ("OBS-007", "HIGH"),
        ("OBS-012", "HIGH"),
        ("OBS-002", "MEDIUM"), ("OBS-002", "MEDIUM"), ("OBS-005", "MEDIUM"),
        ("OBS-006", "MEDIUM"), ("OBS-008", "MEDIUM"), ("OBS-015", "MEDIUM"),
        ("OBS-016", "MEDIUM"),
        ("OBS-010", "LOW"),
    ]

    def setUp(self):
        self.findings = [
            A.Finding(
                check_id=check_id,
                severity=severity,
                resource_type="AWS::CloudWatch::Alarm",
                resource_id=f"{P}-resource-{n}",
                title=f"Synthetic finding {n}",
                detail="d" * 120,
                remediation="r" * 120,
                evidence={"n": n},
                region=REGION,
            )
            for n, (check_id, severity) in enumerate(self.CONTRACT_SHAPE)
        ]
        self.assertEqual(len(self.findings), EXPECTED_STATIC_FINDINGS)
        self.assertEqual(
            sum(f.weight for f in self.findings), EXPECTED_STATIC_WEIGHT
        )
        self.stats = {
            "log_groups": 6, "metric_filters": 5, "alarms": 4,
            "composite_alarms": 2, "dashboards": 2, "functions": 3,
            "custom_metrics": 5,
        }

    def test_render_table_carries_the_day_banner_and_the_score(self):
        out = A.render_table(self.findings, self.stats, 0, use_colour=False)
        self.assertIn("OBSERVABILITY AUDIT", out)
        self.assertIn("CareerByteCode · Day 06 · Monitoring & AI Incident Analysis", out)
        self.assertIn("COMPLIANCE SCORE: 0/100", out)
        self.assertIn("F — do not point this at production data", out)
        self.assertIn("6 log group(s)", out)
        # No escape codes when colour is off, or piping into a file produces soup.
        self.assertNotIn("\033", out)
        # Severity ordering: CRITICAL rows before LOW rows.
        self.assertLess(out.index("CRITICAL"), out.rindex("LOW"))

    def test_render_json_is_valid_and_complete(self):
        payload = json.loads(A.render_json(self.findings, self.stats, 0))
        self.assertEqual(payload["audit"], "obs_audit")
        self.assertEqual(payload["day"], "06")
        self.assertEqual(payload["compliance_score"], 0)
        self.assertEqual(payload["finding_count"], EXPECTED_STATIC_FINDINGS)
        self.assertEqual(len(payload["findings"]), EXPECTED_STATIC_FINDINGS)
        self.assertEqual(payload["summary"]["CRITICAL"], 3)
        self.assertEqual(payload["scanned"]["log_groups"], 6)

    def test_render_csv_has_a_header_and_one_row_per_finding(self):
        import csv as _csv

        rows = list(_csv.reader(io.StringIO(A.render_csv(self.findings))))
        self.assertEqual(rows[0][:3], ["check_id", "severity", "weight"])
        self.assertEqual(len(rows), EXPECTED_STATIC_FINDINGS + 1)
        # The weight column must match the severity, because a spreadsheet is
        # where somebody will re-derive the score by hand.
        for row in rows[1:]:
            self.assertEqual(int(row[2]), A.SEVERITY_WEIGHTS[row[1]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
