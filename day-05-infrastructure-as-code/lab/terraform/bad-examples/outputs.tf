###############################################################################
#                    ██  WRONG ON PURPOSE  ██
#
# bad-examples/outputs.tf
#
# FAULTS IN THIS FILE:
#   IAC-008  output exposes a secret without sensitive = true         HIGH
###############################################################################

# IAC-008 — a secret published as a plain output.
#
# `terraform output` prints it. `terraform apply` prints it at the end of
# every run. CI captures that stdout and keeps it in a build log that is
# usually readable by everyone in the organisation and retained for a year.
#
# Adding `sensitive = true` fixes the PRINTING, and only the printing. It
# does not encrypt the value, does not remove it from state, and does not
# stop `terraform output -raw database_password` from returning it happily to
# anyone who can run Terraform against this directory.
#
# The real question is why a password is an output at all. Outputs are for
# values other configurations and humans need in order to WIRE THINGS UP —
# IDs, ARNs, endpoints, names. If a consumer needs the secret, it should read
# it from Secrets Manager or Parameter Store with its own IAM identity, so
# that access is granted, revocable and logged. Passing it through a
# Terraform output makes it none of those things.
output "database_password" {
  description = "The database password. This output should not exist, and if it must, it should at minimum be marked sensitive."
  value       = aws_ssm_parameter.db_password.value
}

# Fine, and here so the check has to distinguish rather than flag every
# output in the file.
output "report_bucket_names" {
  description = "Names of the reporting buckets. Not a secret; a bucket name grants nothing on its own."
  value       = [for b in aws_s3_bucket.reports : b.id]
}
