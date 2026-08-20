#!/usr/bin/env python3
"""
test_checks.py — unit tests for Day 05's iac_audit.py.

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
    15  WHOLE     the stack totals, the score, the two silent-by-design
                  checks, the HCL parsing helpers, and the three renderers

The fire/silent pairing is deliberate and is worth more than either half
alone. A check with only a fire test is a check that might flag everything;
half the value of a linter is the directories it stays quiet about. Any
check that cannot be made to shut up on correct input is a check that will be
suppressed in week two, at which point it does nothing at all.

The whole-stack tests run against the REAL ../terraform directory on disk,
not a Python-string copy of it. That is what keeps the fixture and the
published finding counts honest: change bad-examples/resources.tf and these
tests fail, which is exactly what should happen, because the day README, the
lab README, envs/dev/outputs.tf and the tool docstring all quote the same
numbers.

THE FINDING CONTRACT — five places quote these, and they must agree:
    13 findings   static only, no credentials      weights 106, score 0/100
    15 findings   with credentials and the         weights 126, score 0/100
                  insecure example bucket applied
    16 findings   after Step 6 introduces drift    weights 130, score 0/100
"""

import importlib
import json
import os
import sys
import tempfile
import unittest

_PYTHON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PYTHON_DIR)
sys.path.insert(0, os.path.join(_PYTHON_DIR, "challenge"))

# Which implementation to test. Defaults to the reference; set the environment
# variable to point the whole suite at your own work while building it:
#
#     IAC_AUDIT_MODULE=iac_audit_challenge python3 -m unittest discover -s tests
#
# That is the offline feedback loop for challenge/iac_audit_challenge.py — 47
# tests, no credentials, no account, under a second.
A = importlib.import_module(os.environ.get("IAC_AUDIT_MODULE", "iac_audit"))


###############################################################################
# Paths and shared fixtures
###############################################################################

PYTHON_DIR = _PYTHON_DIR
TERRAFORM_DIR = os.path.join(os.path.dirname(PYTHON_DIR), "terraform")
BAD_EXAMPLES_DIR = os.path.join(TERRAFORM_DIR, "bad-examples")

# The contract. Change these only when the reference Terraform changes, and
# when you do, change all five places that quote them.
EXPECTED_STATIC_FINDINGS = 13
EXPECTED_STATIC_WEIGHT = 106
EXPECTED_LIVE_FINDINGS = 15
EXPECTED_LIVE_WEIGHT = 126


def directory(files, **kwargs):
    """A TerraformDirectory built from a mapping, with no filesystem at all."""
    return A.TerraformDirectory.from_mapping("fixture", files, **kwargs)


# A correct root module. Every SILENT test that needs a whole directory starts
# from this, changing only the one thing under test — so a silent test that
# fails tells you which check leaked rather than which fixture rotted.
CLEAN_PROVIDERS = """
terraform {
  required_version = ">= 1.10.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
  backend "s3" {
    bucket       = "cbc-day05-tfstate-a1b2c3"
    key          = "day-05/dev/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project   = "aws-aiops-bootcamp"
      Day       = "05"
      ManagedBy = "terraform"
      Owner     = var.owner
    }
  }
}
"""

CLEAN_MAIN = """
resource "aws_s3_bucket" "data" {
  bucket = "cbc-day05-dev-data"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_security_group" "app" {
  name   = "cbc-day05-app"
  vpc_id = var.vpc_id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/16"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
"""

CLEAN_VARIABLES = """
variable "aws_region" {
  description = "Region to deploy into."
  type        = string
  default     = "us-east-1"
}
"""

CLEAN_OUTPUTS = """
output "bucket_name" {
  description = "Name of the data bucket."
  value       = aws_s3_bucket.data.id
}
"""


def clean_directory(**overrides):
    files = {
        "providers.tf": CLEAN_PROVIDERS,
        "main.tf": CLEAN_MAIN,
        "variables.tf": CLEAN_VARIABLES,
        "outputs.tf": CLEAN_OUTPUTS,
    }
    files.update(overrides.pop("files", {}))
    return directory(files, **overrides)


SECURE_STATE_BUCKET = {
    "Name": "cbc-day05-tfstate-a1b2c3",
    "Versioning": {"Status": "Enabled"},
    "Encryption": {
        "Rules": [
            {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "aws:kms"}}
        ]
    },
    "PublicAccessBlock": {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    },
    "PolicyStatus": {"IsPublic": False},
    "Grants": [],
}

# The bucket envs/dev creates when create_insecure_examples = true: no
# versioning, no encryption, and a REAL public access block. It exists to fire
# IAC-006 and IAC-007 and nothing else.
INSECURE_EXAMPLE_BUCKET = {
    "Name": "cbc-day05-dev-tfstate-insecure-a1b2c3",
    "Versioning": {},
    "Encryption": {},
    "PublicAccessBlock": {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    },
    "PolicyStatus": {"IsPublic": False},
    "Grants": [],
}


###############################################################################
# 16 FIRE tests — one per check
###############################################################################


class FireTests(unittest.TestCase):
    """Each check catches the fault it exists for."""

    def test_iac_001_fires_on_hardcoded_secret(self):
        found = A.check_hardcoded_secrets(
            directory(
                {
                    "secrets.tf": """
resource "aws_ssm_parameter" "db_password" {
  name  = "/app/db-password"
  type  = "String"
  value = "SuperSecretP@ssw0rd123"
}
"""
                }
            )
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].check_id, "IAC-001")
        self.assertEqual(found[0].severity, "CRITICAL")
        # The finding must not republish the secret it is complaining about.
        self.assertNotIn("SuperSecretP@ssw0rd123", json.dumps(found[0].to_dict()))

    def test_iac_002_fires_on_provider_credentials(self):
        found = A.check_provider_credentials(
            directory(
                {
                    "providers.tf": """
provider "aws" {
  region     = "us-east-1"
  access_key = "AKIAIOSFODNN7EXAMPLE"
  secret_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
}
"""
                }
            )
        )
        # One finding per provider block, not one per key: access_key and
        # secret_key are always the same mistake made once.
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].check_id, "IAC-002")
        self.assertEqual(found[0].severity, "CRITICAL")

    def test_iac_003_fires_on_ungitignored_state_file(self):
        found = A.check_committed_state(
            directory(
                {"main.tf": 'resource "aws_instance" "a" {\n  ami = "ami-1"\n}'},
                state_files=["terraform.tfstate"],
                gitignore_rules=["*.tfplan"],
            )
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].check_id, "IAC-003")
        self.assertEqual(found[0].severity, "CRITICAL")

    def test_iac_004_fires_on_public_state_bucket(self):
        found = A.check_state_bucket_public(
            {
                "Name": "leaky-tfstate-bucket",
                "PublicAccessBlock": {},
                "PolicyStatus": {"IsPublic": True},
                "Grants": [
                    {
                        "Grantee": {
                            "URI": "http://acs.amazonaws.com/groups/global/AllUsers"
                        },
                        "Permission": "READ",
                    }
                ],
            }
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].check_id, "IAC-004")
        self.assertEqual(found[0].severity, "CRITICAL")

    def test_iac_005_fires_on_root_module_without_backend(self):
        found = A.check_local_state(
            directory(
                {
                    "providers.tf": """
terraform {
  required_version = ">= 1.10.0"
}

provider "aws" {
  region = "us-east-1"
}
"""
                }
            )
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].check_id, "IAC-005")
        self.assertEqual(found[0].severity, "HIGH")

    def test_iac_006_fires_on_unversioned_state_bucket(self):
        found = A.check_state_bucket_versioning(INSECURE_EXAMPLE_BUCKET)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].check_id, "IAC-006")
        self.assertEqual(found[0].severity, "HIGH")

    def test_iac_007_fires_on_unencrypted_state_bucket(self):
        found = A.check_state_bucket_encryption(INSECURE_EXAMPLE_BUCKET)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].check_id, "IAC-007")
        self.assertEqual(found[0].severity, "HIGH")

    def test_iac_008_fires_on_insensitive_secret_output(self):
        found = A.check_sensitive_outputs(
            directory(
                {
                    "outputs.tf": """
output "database_password" {
  description = "Should not exist at all."
  value       = aws_ssm_parameter.db_password.value
}
"""
                }
            )
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].check_id, "IAC-008")
        self.assertEqual(found[0].severity, "HIGH")

    def test_iac_009_fires_on_open_ingress(self):
        found = A.check_open_ingress(
            directory(
                {
                    "sg.tf": """
resource "aws_security_group" "wide_open" {
  name = "wide-open"

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
"""
                }
            )
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].check_id, "IAC-009")
        self.assertEqual(found[0].severity, "HIGH")

    def test_iac_010_fires_without_required_version(self):
        found = A.check_required_version(
            directory(
                {
                    "providers.tf": """
terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
}
"""
                }
            )
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].check_id, "IAC-010")
        self.assertEqual(found[0].severity, "MEDIUM")

    def test_iac_011_fires_on_unpinned_provider(self):
        found = A.check_provider_pinning(
            directory(
                {
                    "providers.tf": """
terraform {
  required_version = ">= 1.10.0"
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}
"""
                }
            )
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].check_id, "IAC-011")
        self.assertIn("aws", found[0].evidence["unpinned_providers"])

    def test_iac_012_fires_on_gitignored_lock_file(self):
        found = A.check_lock_file(
            directory(
                {"main.tf": ""},
                gitignore_rules=[".terraform/", ".terraform.lock.hcl"],
            )
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].check_id, "IAC-012")
        self.assertEqual(found[0].severity, "MEDIUM")

    def test_iac_013_fires_on_unprotected_stateful_resource(self):
        found = A.check_prevent_destroy(
            directory(
                {
                    "main.tf": """
resource "aws_s3_bucket" "reports" {
  bucket        = "reports"
  force_destroy = true
}
"""
                }
            )
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].check_id, "IAC-013")
        self.assertEqual(found[0].severity, "MEDIUM")

    def test_iac_014_fires_on_untagged_resource(self):
        found = A.check_resource_tags(
            directory(
                {
                    "providers.tf": 'provider "aws" {\n  region = "us-east-1"\n}',
                    "main.tf": """
resource "aws_s3_bucket" "reports" {
  bucket = "reports"
}
""",
                }
            )
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].check_id, "IAC-014")
        self.assertEqual(
            found[0].evidence["missing_tags"], ["Project", "Day", "ManagedBy", "Owner"]
        )

    def test_iac_015_fires_on_drifted_tags(self):
        found = A.check_tag_drift(
            {
                "resource_type": "AWS::Logs::LogGroup",
                "resource_id": "/aws/cbc-day05-dev/drift-demo",
                "declared_tags": {"CostCentre": "engineering"},
                "deployed_tags": {"CostCentre": "finance"},
            }
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].check_id, "IAC-015")
        self.assertEqual(
            found[0].evidence["differences"]["CostCentre"]["deployed"], "finance"
        )

    def test_iac_016_fires_on_count_and_untyped_variable(self):
        found = A.check_iteration_and_variables(
            directory(
                {
                    "resources.tf": """
resource "aws_s3_bucket" "reports" {
  count  = length(var.report_bucket_names)
  bucket = var.report_bucket_names[count.index]
}
""",
                    "variables.tf": 'variable "environment_name" {\n}\n',
                }
            )
        )
        # Two arms of one check ID, and the reference stack relies on both
        # firing exactly once each.
        self.assertEqual(len(found), 2)
        self.assertEqual({f.check_id for f in found}, {"IAC-016"})
        self.assertEqual({f.severity for f in found}, {"LOW"})


###############################################################################
# 16 SILENT tests — one per check
###############################################################################


class SilentTests(unittest.TestCase):
    """Each check stays quiet on input that is correct."""

    def test_iac_001_silent_on_secret_read_from_a_data_source(self):
        found = A.check_hardcoded_secrets(
            directory(
                {
                    "secrets.tf": """
data "aws_secretsmanager_secret_version" "db" {
  secret_id = "prod/db"
}

resource "aws_db_instance" "main" {
  identifier = "app"
  password   = data.aws_secretsmanager_secret_version.db.secret_string
}

variable "secret_arn" {
  description = "ARN of the secret, which is not itself a secret."
  type        = string
  default     = "arn:aws:secretsmanager:us-east-1:111122223333:secret:prod/db"
}
"""
                }
            )
        )
        self.assertEqual(found, [])

    def test_iac_002_silent_on_provider_using_a_named_profile(self):
        found = A.check_provider_credentials(
            directory(
                {
                    "providers.tf": """
provider "aws" {
  region  = "us-east-1"
  profile = "bootcamp"
}
"""
                }
            )
        )
        self.assertEqual(found, [])

    def test_iac_003_silent_when_state_file_is_gitignored(self):
        found = A.check_committed_state(
            directory(
                {"main.tf": ""},
                state_files=["terraform.tfstate"],
                gitignore_rules=["*.tfstate", "*.tfstate.*"],
            )
        )
        self.assertEqual(found, [])

    def test_iac_004_silent_on_locked_down_bucket(self):
        self.assertEqual(A.check_state_bucket_public(SECURE_STATE_BUCKET), [])

    def test_iac_005_silent_with_backend_and_with_suppression_marker(self):
        with_backend = A.check_local_state(clean_directory())
        self.assertEqual(with_backend, [])

        # The bootstrap exception: local state, declared in code, next to the
        # thing being suppressed.
        bootstrap = A.check_local_state(
            directory(
                {
                    "providers.tf": """
terraform {
  required_version = ">= 1.10.0"

  # The backend cannot create itself — this directory builds the bucket the
  # others use, so it has nowhere remote to put its own state.
  # iac-audit: allow-local-state
}

provider "aws" {
  region = "us-east-1"
}
"""
                }
            )
        )
        self.assertEqual(bootstrap, [])

    def test_iac_006_silent_on_versioned_bucket(self):
        self.assertEqual(A.check_state_bucket_versioning(SECURE_STATE_BUCKET), [])

    def test_iac_007_silent_on_encrypted_bucket(self):
        self.assertEqual(A.check_state_bucket_encryption(SECURE_STATE_BUCKET), [])

    def test_iac_008_silent_on_marked_and_on_innocent_outputs(self):
        found = A.check_sensitive_outputs(
            directory(
                {
                    "outputs.tf": """
output "database_password" {
  description = "Marked, which fixes the printing and nothing else."
  value       = aws_ssm_parameter.db.value
  sensitive   = true
}

output "state_kms_key_arn" {
  description = "A KMS key ARN grants nothing on its own."
  value       = aws_kms_key.state.arn
}

output "report_bucket_names" {
  description = "Bucket names are not secrets."
  value       = [for b in aws_s3_bucket.reports : b.id]
}
"""
                }
            )
        )
        self.assertEqual(found, [])

    def test_iac_009_silent_on_scoped_ingress_and_open_egress(self):
        found = A.check_open_ingress(
            directory(
                {
                    "sg.tf": CLEAN_MAIN
                    + """
resource "aws_vpc_security_group_egress_rule" "app_all" {
  security_group_id = aws_security_group.app.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_ingress_rule" "app_vpc" {
  security_group_id = aws_security_group.app.id
  cidr_ipv4         = "10.0.0.0/16"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}
"""
                }
            )
        )
        # Egress to the world is normal, and flagging it would fire on every
        # security group in the account.
        self.assertEqual(found, [])

    def test_iac_010_silent_when_required_version_is_in_a_second_block(self):
        # envs/dev has exactly this shape: required_version in providers.tf and
        # the backend in backend.tf. A per-block check would false-positive on
        # the second one.
        found = A.check_required_version(
            directory(
                {
                    "providers.tf": 'terraform {\n  required_version = ">= 1.10.0"\n}',
                    "backend.tf": """
terraform {
  backend "s3" {
    bucket = "cbc-day05-tfstate-a1b2c3"
    key    = "day-05/dev/terraform.tfstate"
  }
}
""",
                }
            )
        )
        self.assertEqual(found, [])

    def test_iac_011_silent_on_pinned_providers(self):
        found = A.check_provider_pinning(
            directory(
                {
                    "providers.tf": """
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.80, < 6.0"
    }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}
"""
                }
            )
        )
        self.assertEqual(found, [])

    def test_iac_012_silent_when_lock_file_is_merely_absent(self):
        # A directory nobody has run init in is not a finding, it is a
        # directory nobody has run init in.
        never_initialised = A.check_lock_file(
            directory(
                {"main.tf": ""},
                gitignore_rules=["*.tfstate", ".terraform/"],
                has_terraform_dir=False,
                has_lock_file=False,
            )
        )
        self.assertEqual(never_initialised, [])

        initialised_and_committed = A.check_lock_file(
            directory(
                {"main.tf": ""},
                gitignore_rules=["*.tfstate", ".terraform/"],
                has_terraform_dir=True,
                has_lock_file=True,
            )
        )
        self.assertEqual(initialised_and_committed, [])

    def test_iac_013_silent_on_protected_stateful_resources(self):
        found = A.check_prevent_destroy(
            directory(
                {
                    "main.tf": """
resource "aws_s3_bucket" "data" {
  bucket = "data"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_dynamodb_table" "data" {
  name         = "data"
  hash_key     = "id"
  billing_mode = "PAY_PER_REQUEST"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_cloudwatch_log_group" "logs" {
  name = "/aws/app"
}
"""
                }
            )
        )
        self.assertEqual(found, [])

    def test_iac_014_silent_with_default_tags_and_in_child_modules(self):
        # A root module whose provider sets default_tags covers every resource
        # in the directory.
        self.assertEqual(A.check_resource_tags(clean_directory()), [])

        # A child module has no provider block, inherits the caller's, and is
        # skipped entirely.
        child_module = A.check_resource_tags(
            directory(
                {
                    "versions.tf": """
terraform {
  required_version = ">= 1.10.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 5.80, < 6.0" }
  }
}
""",
                    "main.tf": 'resource "aws_s3_bucket" "data" {\n  bucket = "d"\n}',
                }
            )
        )
        self.assertEqual(child_module, [])

    def test_iac_015_silent_when_deployed_tags_match(self):
        found = A.check_tag_drift(
            {
                "resource_type": "AWS::Logs::LogGroup",
                "resource_id": "/aws/cbc-day05-dev/drift-demo",
                "declared_tags": {"CostCentre": "engineering"},
                "deployed_tags": {
                    "CostCentre": "engineering",
                    "Project": "aws-aiops-bootcamp",
                },
            }
        )
        # Extra tags in AWS are not drift. default_tags and the account's own
        # tag policies add them, and flagging those would fire everywhere.
        self.assertEqual(found, [])

    def test_iac_016_silent_on_boolean_count_and_declared_variables(self):
        found = A.check_iteration_and_variables(
            directory(
                {
                    "main.tf": """
resource "aws_nat_gateway" "this" {
  count     = var.enable_nat_gateway ? 1 : 0
  subnet_id = var.public_subnet_ids[0]
}

resource "aws_s3_bucket" "reports" {
  for_each = toset(var.report_bucket_names)
  bucket   = each.value
}
""",
                    "variables.tf": CLEAN_VARIABLES,
                }
            )
        )
        # `count = var.enabled ? 1 : 0` is the one shape count is correct for.
        self.assertEqual(found, [])


###############################################################################
# 15 WHOLE-STACK, HELPER AND RENDERER tests
###############################################################################


def run_all_static(directories):
    findings = []
    for directory_ in directories:
        for _check_id, check in A.STATIC_CHECKS:
            findings += check(directory_)
    return findings


class WholeStackTests(unittest.TestCase):
    """Run every static check over the real lab/terraform tree on disk.

    Not a Python-string copy of it. If somebody edits bad-examples/ without
    updating the published counts, these fail — which is the entire point of
    testing against the real fixture.
    """

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(TERRAFORM_DIR):  # pragma: no cover
            raise unittest.SkipTest(f"{TERRAFORM_DIR} not found")
        cls.directories = A.discover_directories(TERRAFORM_DIR)
        cls.findings = run_all_static(cls.directories)

    def test_static_finding_count_is_thirteen(self):
        self.assertEqual(
            len(self.findings),
            EXPECTED_STATIC_FINDINGS,
            "The static finding count is quoted in five places — the day README, "
            "lab/README.md, envs/dev/outputs.tf next_steps, the iac_audit.py "
            "docstring and this file. Reconcile all five.",
        )

    def test_static_weights_and_score_match_the_contract(self):
        self.assertEqual(sum(f.weight for f in self.findings), EXPECTED_STATIC_WEIGHT)
        self.assertEqual(A.calculate_score(self.findings), 0)
        self.assertTrue(A.score_grade(0).startswith("F"))

        # --min-severity filters the DISPLAY and never the score. Otherwise
        # anyone could "improve" their compliance posture by passing
        # --min-severity CRITICAL, which is not an improvement, it is a habit.
        criticals = A.filter_by_severity(self.findings, "CRITICAL")
        self.assertEqual(len(criticals), 2)
        self.assertEqual(A.calculate_score(criticals), 50)

    def test_every_static_finding_is_well_formed_and_from_bad_examples(self):
        # Every other directory in the lab is hand-written to be clean. If one
        # of them starts producing findings, either it regressed or a check
        # became too eager — and either way somebody needs to look.
        labels = {f.resource_id.split("::")[0].split("/")[0] for f in self.findings}
        self.assertEqual(labels, {"bad-examples"})

        for finding in self.findings:
            self.assertRegex(finding.check_id, r"^IAC-0(0[1-9]|1[0-6])$")
            self.assertIn(finding.severity, A.SEVERITY_ORDER)
            self.assertTrue(finding.title and finding.detail and finding.remediation)

        # The severity is validated at construction, not at render time, so a
        # typo in a new check fails immediately rather than producing a
        # finding worth an unknown number of points.
        with self.assertRaises(ValueError):
            A.Finding(
                check_id="IAC-999",
                severity="SEVERE",
                resource_type="x",
                resource_id="y",
                title="t",
                detail="d",
                remediation="r",
            )

    def test_live_run_totals_fifteen_findings(self):
        # 13 static + IAC-006 and IAC-007 on the deliberately insecure example
        # bucket. The correctly built state bucket contributes nothing, which
        # is the other half of the demonstration.
        live = A.check_state_bucket_versioning(
            INSECURE_EXAMPLE_BUCKET
        ) + A.check_state_bucket_encryption(INSECURE_EXAMPLE_BUCKET)
        live += A.check_state_bucket_public(SECURE_STATE_BUCKET)
        live += A.check_state_bucket_versioning(SECURE_STATE_BUCKET)
        live += A.check_state_bucket_encryption(SECURE_STATE_BUCKET)
        self.assertEqual(len(live), 2)

        combined = self.findings + live
        self.assertEqual(len(combined), EXPECTED_LIVE_FINDINGS)
        self.assertEqual(sum(f.weight for f in combined), EXPECTED_LIVE_WEIGHT)
        self.assertEqual(A.calculate_score(combined), 0)

    def test_iac_003_has_zero_false_positives(self):
        """SILENT BY DESIGN. There is no committed state anywhere in the lab.

        The check runs on every directory and finds nothing, which is a
        result rather than a gap — see the fire test above for proof that it
        works.
        """
        found = []
        for directory_ in self.directories:
            found += A.check_committed_state(directory_)
        self.assertEqual(found, [])

    def test_iac_004_has_zero_false_positives(self):
        """SILENT BY DESIGN. This repository ships no public bucket.

        The insecure example bucket in envs/dev has a real public access
        block. Being one `terraform apply` away from leaking somebody's data
        is not a lesson worth the demonstration.
        """
        self.assertEqual(A.check_state_bucket_public(INSECURE_EXAMPLE_BUCKET), [])
        self.assertEqual(A.check_state_bucket_public(SECURE_STATE_BUCKET), [])

    def test_clean_directory_produces_zero_findings(self):
        """The single most important test in this file.

        A linter that cannot be satisfied is a linter that gets disabled.
        """
        self.assertEqual(run_all_static([clean_directory()]), [])


class HclParsingTests(unittest.TestCase):
    """The five parsing helpers every check is built on."""

    def test_strip_hcl_comments_leaves_strings_and_heredocs_alone(self):
        source = """
# a leading comment
resource "aws_s3_bucket" "b" {   // trailing comment
  bucket = "not-a-#-comment"
  /* block
     comment */
  policy = <<-EOT
    # this line is content, not a comment
  EOT
}
"""
        stripped = A.strip_hcl_comments(source)
        self.assertNotIn("a leading comment", stripped)
        self.assertNotIn("trailing comment", stripped)
        self.assertNotIn("block\n     comment", stripped)
        self.assertIn("not-a-#-comment", stripped)
        self.assertIn("# this line is content", stripped)
        # Line numbers must survive, or every reported line is wrong.
        self.assertEqual(source.count("\n"), stripped.count("\n"))

    def test_extract_blocks_is_brace_balanced_and_finds_nested_blocks(self):
        source = A.strip_hcl_comments(
            """
resource "aws_security_group" "a" {
  name = "a{b}c"

  ingress {
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    cidr_blocks = ["10.0.0.0/8"]
  }
}

resource "aws_s3_bucket" "b" {
  bucket = "b"
}
"""
        )
        resources = A.extract_blocks(source, "resource")
        self.assertEqual(len(resources), 2)
        self.assertEqual(resources[0].labels, ["aws_security_group", "a"])
        self.assertEqual(resources[0].address, "aws_security_group.a")
        self.assertEqual(resources[1].address, "aws_s3_bucket.b")
        # A brace inside a string must not end the block early.
        self.assertEqual(len(A.extract_blocks(resources[0].body, "ingress")), 2)

    def test_block_attributes_handles_objects_lists_and_heredocs(self):
        source = A.strip_hcl_comments(
            """
resource "x" "y" {
  simple = "value"
  list   = ["a", "b"]

  tags = {
    Project = "aws-aiops-bootcamp"
    Day     = "05"
  }

  body = <<-EOT
    line one
    line two
  EOT

  after = "still parsed"

  lifecycle {
    prevent_destroy = true
  }
}
"""
        )
        block = A.extract_blocks(source, "resource")[0]
        attrs = A.block_attributes(block.body)
        self.assertEqual(A.unquote_hcl(attrs["simple"]), "value")
        self.assertIn("Project", attrs["tags"])
        # A heredoc must not swallow the attribute that follows it.
        self.assertEqual(A.unquote_hcl(attrs["after"]), "still parsed")
        # Nested blocks are skipped, not flattened.
        self.assertNotIn("prevent_destroy", attrs)
        self.assertEqual(
            A.block_attributes(
                A.extract_blocks(block.body, "lifecycle")[0].body
            )["prevent_destroy"],
            "true",
        )

    def test_find_gitignore_rules_resolves_upwards_from_the_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = os.path.join(tmp, "envs", "dev")
            os.makedirs(nested)
            with open(os.path.join(tmp, ".gitignore"), "w") as handle:
                handle.write("*.tfstate\n")
            with open(os.path.join(nested, ".gitignore"), "w") as handle:
                handle.write("!keep.tfstate\n.terraform.lock.hcl\n")

            rules = A.find_gitignore_rules(nested, tmp)
            # Outermost first, so last-match-wins reproduces git's precedence.
            self.assertEqual(rules[0], "*.tfstate")
            self.assertTrue(A.gitignore_ignores(rules, "terraform.tfstate"))
            self.assertFalse(A.gitignore_ignores(rules, "keep.tfstate"))
            self.assertTrue(A.gitignore_ignores(rules, A.LOCK_FILE))
            self.assertFalse(A.gitignore_ignores(rules, "main.tf"))

    def test_is_root_module_and_has_default_tags(self):
        root = clean_directory()
        self.assertTrue(A.is_root_module(root))
        self.assertTrue(A.has_default_tags(root))

        child = directory(
            {"main.tf": 'resource "aws_s3_bucket" "b" {\n  bucket = "b"\n}'}
        )
        self.assertFalse(A.is_root_module(child))
        self.assertFalse(A.has_default_tags(child))

        untagged_root = directory(
            {"providers.tf": 'provider "aws" {\n  region = "us-east-1"\n}'}
        )
        self.assertTrue(A.is_root_module(untagged_root))
        self.assertFalse(A.has_default_tags(untagged_root))

        # And the marker, which is a whole comment line or nothing. A file
        # that merely mentions `# iac-audit: allow-local-state` in prose must
        # not suppress anything — bad-examples/providers.tf does exactly that.
        self.assertFalse(A.has_local_state_suppression(untagged_root))


class RendererTests(unittest.TestCase):
    """The three output formats. Every one of them is somebody's integration."""

    def setUp(self):
        # Built by hand rather than by running a check: these tests are about
        # the renderers, and a renderer test that fails because a check
        # regressed tells you the wrong thing. It also means they still pass
        # against challenge/iac_audit_challenge.py before any TODO is done.
        self.findings = [
            A.Finding(
                check_id="IAC-006",
                severity="HIGH",
                resource_type="AWS::S3::Bucket",
                resource_id="cbc-day05-dev-tfstate-insecure-a1b2c3",
                title="State bucket has no versioning",
                detail="Versioning status is absent, so there is no rollback path.",
                remediation="Enable versioning and expire noncurrent versions.",
                evidence={"versioning": {}},
                region="us-east-1",
            ),
            A.Finding(
                check_id="IAC-004",
                severity="CRITICAL",
                resource_type="AWS::S3::Bucket",
                resource_id="leaky-tfstate-bucket",
                title="Terraform state bucket is publicly accessible",
                detail="No public access block and the policy evaluates as public.",
                remediation="Turn on all four public access block settings.",
                evidence={"missing_settings": ["BlockPublicAcls"]},
                region="us-east-1",
            ),
        ]
        self.stats = {"directories": 7, "files": 31, "resources": 50}

    def test_render_table_carries_the_day_banner_and_the_score(self):
        out = A.render_table(self.findings, self.stats, 0, use_colour=False)
        self.assertIn("INFRASTRUCTURE AS CODE AUDIT", out)
        self.assertIn("CareerByteCode · Day 05 · Infrastructure as Code", out)
        self.assertIn("COMPLIANCE SCORE: 0/100", out)
        self.assertIn("IAC-006", out)
        # use_colour=False must mean no escape codes, or piping to a file
        # produces soup.
        self.assertNotIn("\033[", out)

    def test_render_json_is_valid_and_complete(self):
        payload = json.loads(A.render_json(self.findings, self.stats, 0))
        # "iac_audit" from the reference, "iac_audit_challenge" from the
        # generated challenge build — a JSON payload should say which tool
        # produced it, including when that tool is half-finished.
        self.assertTrue(payload["audit"].startswith("iac_audit"))
        self.assertEqual(payload["day"], "05")
        self.assertEqual(payload["finding_count"], len(self.findings))
        self.assertEqual(payload["compliance_score"], 0)
        self.assertEqual(payload["scanned"]["directories"], 7)
        self.assertEqual(
            {f["check_id"] for f in payload["findings"]}, {"IAC-004", "IAC-006"}
        )

    def test_render_csv_has_a_header_and_one_row_per_finding(self):
        rows = A.render_csv(self.findings).strip().splitlines()
        self.assertEqual(len(rows), len(self.findings) + 1)
        self.assertTrue(rows[0].startswith("check_id,severity,weight"))
        # CRITICAL sorts before HIGH, as in the table.
        self.assertTrue(rows[1].startswith("IAC-004,CRITICAL,25"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
