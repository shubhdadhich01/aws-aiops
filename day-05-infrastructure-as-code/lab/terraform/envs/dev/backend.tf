###############################################################################
# Day 05 — envs/dev/backend.tf
#
# ============================================================================
#  YOU MUST EDIT THE `bucket` LINE BELOW BEFORE `terraform init` WILL WORK.
#  Get the value from: cd ../../backend-bootstrap && terraform output \
#                        -raw state_bucket_name
# ============================================================================
#
# WHY THE BACKEND EXISTS AT ALL
#
# Terraform's state file is the map between the names in your code
# (aws_vpc.this) and the real objects in AWS (vpc-0a1b2c3d). Without it,
# Terraform has no idea that the VPC in your code is the VPC in your account —
# it would propose to create a second one on every apply.
#
# Local state means that map lives on ONE laptop. Consequences, all of which
# happen in real teams:
#
#   * Your colleague runs apply, has no state, and creates a duplicate stack.
#   * You both run apply at once and interleave writes into the same
#     resources. Terraform has no idea; AWS obliges.
#   * Your laptop dies, and with it the only record of what exists. Recovery
#     is `terraform import`, one resource at a time, by hand.
#
# Remote state fixes the sharing problem. LOCKING fixes the concurrency
# problem, and they are separate problems — a shared bucket with no locking
# is arguably worse than local state, because now two people can corrupt one
# file instead of each corrupting their own.
#
# STATE IS A SECRETS LIABILITY
#
# Everything Terraform knows about a resource is in state, in plaintext JSON.
# RDS master passwords. Generated private keys. Any variable you passed in.
# `sensitive = true` hides a value from CLI OUTPUT — it does not encrypt it,
# redact it, or keep it out of the state file. Anyone with s3:GetObject on
# this bucket has read every secret in your estate without touching a single
# resource. Encrypt the bucket, block public access, and treat the read
# permission as a production credential, because it is one.
#
# NATIVE S3 LOCKING — use_lockfile
#
# `use_lockfile = true` makes Terraform write a <key>.tflock object with a
# conditional put before it mutates state, and delete it afterwards. That is
# the whole mechanism. It needs AWS provider >= 5.80 and Terraform >= 1.10.
#
# For a decade the answer was a DynamoDB table with a LockID hash key. It
# worked. It is now legacy: `dynamodb_table` is deprecated, and every repo
# that used it carries a $0.25/month table that outlives the project. You will
# meet these. Do not build new ones. Know what they were for, because that is
# an interview question, and because if you are stuck below provider 5.80 you
# have no alternative.
#
# WHAT THE BACKEND BLOCK CANNOT DO
#
# It cannot use variables, locals, or any expression. Not "should not" —
# CANNOT. Terraform reads this block before it has evaluated anything else in
# the configuration, so `bucket = var.state_bucket` is a hard error. Everyone
# tries it once.
#
# Your two legal options are: hardcode it (below), or supply it at init time:
#
#   terraform init \
#     -backend-config="bucket=cbc-day05-tfstate-abc123" \
#     -backend-config="key=day-05/dev/terraform.tfstate"
#
# Partial configuration via -backend-config is how CI does it, and how one
# repo serves several accounts.
###############################################################################

terraform {
  backend "s3" {
    # ---- EDIT THIS ----------------------------------------------------------
    bucket = "REPLACE-ME-WITH-YOUR-STATE-BUCKET"
    # -------------------------------------------------------------------------

    # The key is the path inside the bucket. Namespacing by day and
    # environment is what lets one bucket serve the whole bootcamp without
    # two environments ever colliding.
    key = "day-05/dev/terraform.tfstate"

    region  = "us-east-1"
    profile = "bootcamp"

    # Encrypt state in transit and at rest on write. The bucket also has
    # default encryption; this is belt and braces, and it costs nothing.
    encrypt = true

    # S3-native state locking. Requires AWS provider >= 5.80, Terraform >= 1.10.
    # Replaces the legacy DynamoDB lock table entirely.
    use_lockfile = true

    # NOT SET, DELIBERATELY:
    #   dynamodb_table = "..."   # legacy, deprecated, superseded by the above
  }
}
