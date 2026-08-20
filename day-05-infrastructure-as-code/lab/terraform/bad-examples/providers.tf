###############################################################################
#                    ██  WRONG ON PURPOSE  ██
#
# bad-examples/providers.tf
#
# NOTHING IN THIS DIRECTORY IS EVER APPLIED. No env references it, no module
# sources it, and there is no backend, so `terraform apply` here would fail
# before it did any damage. It exists to be PARSED by ../../python/iac_audit.py
# so the static checks have real, readable HCL to find faults in.
#
# Every fault below is labelled with the check ID it triggers. If you change
# this file, the finding counts in the day README, lab/README.md, envs/dev
# outputs and tests/test_checks.py all stop being true — reconcile them.
#
# FAULTS IN THIS FILE:
#   IAC-002  provider block with hardcoded access_key / secret_key   CRITICAL
#   IAC-005  no backend block — this root module uses local state    HIGH
#   IAC-010  terraform block has no required_version                 MEDIUM
#   IAC-011  provider version unpinned in required_providers         MEDIUM
###############################################################################

terraform {
  # IAC-010 — no required_version.
  #
  # Without a floor, somebody runs this with whatever binary is on their PATH.
  # A 1.4 binary hits `optional()` in an object type and reports a syntax
  # error, and you spend an afternoon on a bug that is a version mismatch.
  # Worse in the other direction: a newer binary WRITES A NEWER STATE FORMAT,
  # and everyone still on the old version is now locked out of the state file
  # until they upgrade too. State format upgrades are one-way.

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # IAC-011 — no version constraint.
      #
      # This resolves to whatever the newest AWS provider is at the moment
      # `terraform init` runs. Two engineers initialising a day apart get
      # different providers, and one of them sees a plan with changes nobody
      # wrote. Major versions remove arguments: a 6.x provider against
      # configuration written for 5.x is not a warning, it is an error at
      # best and a silent behavioural change at worst.
    }
  }
}

provider "aws" {
  region = "us-east-1"

  # IAC-002 — hardcoded credentials in a provider block. CRITICAL.
  #
  # These are syntactically valid AWS key formats and are NOT real keys —
  # AKIAIOSFODNN7EXAMPLE is the literal example key from the AWS
  # documentation. They are here so the auditor's regex has something to
  # match. Never write real ones anywhere, ever, for any reason.
  #
  # What actually happens when you do: the key lands in git history, and
  # deleting the commit does not remove it, because the object still exists
  # in every clone and in every fork. GitHub's secret scanning finds public
  # ones in seconds; so do the bots that mine public repos, and the median
  # time from push to a crypto-mining instance in your account is measured in
  # MINUTES, not hours. The only remediation is rotation, immediately.
  #
  # The correct answers, in order of preference:
  #   1. An OIDC role in CI. No long-lived key exists to leak.
  #   2. An IAM role on the instance or task.
  #   3. `profile = "bootcamp"` and a named profile in ~/.aws/credentials.
  #   4. AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY environment variables.
  # There is no fifth answer.
  access_key = "AKIAIOSFODNN7EXAMPLE"
  secret_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

  # ALSO MISSING: default_tags.
  #
  # This is what makes IAC-014 fire on the resources in resources.tf. In a
  # directory whose provider sets default_tags, untagged resources are
  # covered and the check correctly stays quiet.
}

# IAC-005 — there is no `backend` block anywhere in this directory, so
# Terraform would write terraform.tfstate to local disk.
#
# One laptop holds the only map between your code and your account. Your
# colleague applies, sees no state, and creates a duplicate stack. You both
# apply at once and interleave writes with no locking. The laptop dies and
# recovery is `terraform import`, one resource at a time, by hand.
#
# The ONE legitimate exception is a backend-bootstrap directory, because the
# backend cannot create itself. That directory declares the exception
# explicitly with an inline `# iac-audit: allow-local-state` marker, which is
# the suppression mechanism every audit tool needs: a way to say "I know, and
# here is why", in code, greppable, next to the thing being suppressed.
