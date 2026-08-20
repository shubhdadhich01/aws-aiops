###############################################################################
#                    ██  WRONG ON PURPOSE  ██
#
# bad-examples/secrets.tf
#
# FAULTS IN THIS FILE:
#   IAC-001  hardcoded secret / credential in .tf                    CRITICAL
###############################################################################

# IAC-001 — a password, written into the configuration, in plaintext.
#
# This lands in THREE places at once, and people usually only think about the
# first:
#
#   1. Git history, forever, in every clone and every fork.
#   2. The Terraform state file, in plaintext JSON, because state records
#      every attribute of every resource.
#   3. Any CI log that echoes the plan output.
#
# Marking something `sensitive = true` addresses none of these. It hides the
# value from CLI output. It does not encrypt it, redact it, or keep it out of
# state. This is the single most misunderstood feature in Terraform.
#
# What to do instead, in order:
#   1. Create the secret OUTSIDE Terraform and read it with a data source:
#        data "aws_secretsmanager_secret_version" "db" { secret_id = "prod/db" }
#      The value still passes through state, but Terraform never authored it
#      and it is not in your git history.
#   2. Have the application fetch it at runtime with its instance role, so it
#      never touches Terraform at all. This is the real answer.
#   3. If Terraform must generate it, use random_password and accept that
#      state is now a secrets store that needs to be encrypted and
#      access-controlled like one.
resource "aws_ssm_parameter" "db_password" {
  name = "/cbc-day05/bad-examples/db-password"

  # Deliberately the WRONG type as well: String, not SecureString, so it is
  # not even encrypted at rest in Parameter Store.
  type  = "String"
  value = "SuperSecretP@ssw0rd123"

  # Tagged, so that IAC-014 (missing required tags) does NOT also fire here.
  # A fixture that trips four checks at once tells you nothing about which
  # one you broke.
  tags = {
    Project   = "aws-aiops-bootcamp"
    Day       = "05"
    ManagedBy = "terraform"
    Owner     = "bad-examples"
  }
}
