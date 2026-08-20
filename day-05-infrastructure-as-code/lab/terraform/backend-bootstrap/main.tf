###############################################################################
# Day 05 — backend-bootstrap/main.tf
#
# One job: create the S3 bucket that every other directory in this lab will
# use as its Terraform backend. Nothing else belongs in here.
#
# WHAT THIS DELIBERATELY DOES NOT CREATE
#
#   A DynamoDB lock table.
#
# For a decade the canonical remote-state setup was "S3 bucket + DynamoDB
# table with a LockID hash key", because S3 had no way to say "I claim this
# object, nobody else touch it". As of AWS provider 5.80 and Terraform 1.10,
# the S3 backend supports NATIVE locking via `use_lockfile = true`: Terraform
# writes a <key>.tflock object using a conditional put and deletes it on
# release. One less resource, one less IAM policy, one less thing to forget
# when you add a second environment, and no more $0.25/month table that
# outlives the project by three years.
#
# The DynamoDB table is now legacy. You will still meet it in every existing
# repo you inherit, and `dynamodb_table` still works and is deprecated. Know
# what it was for; do not build a new one. If you are stuck below provider
# 5.80 you have no choice — and that, not nostalgia, is the reason to know it.
#
# ORDER OF OPERATIONS FOR THE WHOLE LAB
#
#   1. cd backend-bootstrap && terraform init && terraform apply   (local state)
#   2. copy the bucket name from the outputs into envs/dev/backend.tf
#   3. cd ../envs/dev && terraform init && terraform apply         (remote state)
#   4. same for envs/prod
#
# Teardown runs exactly backwards. envs first, bootstrap LAST. Destroy the
# bucket while dev still has state in it and you have not destroyed dev — you
# have orphaned it, and now the only record of what exists is your memory.
###############################################################################

###############################################################################
# 1. The state bucket
#
# This is the single most important bucket in the account and the one you can
# never safely delete. Treat it accordingly.
###############################################################################

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket_name

  # force_destroy = true tells Terraform "delete every object, including every
  # version, then delete the bucket." That is a loaded gun pointed at the file
  # that describes your entire estate. It is false by default here and you
  # should leave it false everywhere that is not this lab.
  force_destroy = var.state_bucket_force_destroy

  lifecycle {
    # prevent_destroy makes `terraform destroy` FAIL — loudly, at plan time,
    # before it does anything. That is the entire point. It is a seatbelt, and
    # like a seatbelt it is annoying exactly once, in the situation where it
    # saves you.
    #
    # It also means the teardown at the end of this lab does not "just work",
    # and dealing with that deliberately is Step 9. Read
    # ../../../teardown-checklist.md BEFORE you reach for this block in anger.
    # Deleting a prevent_destroy line mid-incident so the apply goes through
    # is how production buckets get removed by people who meant well.
    prevent_destroy = true
  }
}

###############################################################################
# 2. Versioning
#
# Non-negotiable. State files are the only artefact in AWS where "restore
# yesterday's copy" is the difference between a ten-minute recovery and
# reconstructing your infrastructure from memory. A corrupted or truncated
# state file with versioning off is an outage.
#
# The bill for this is small and PERMANENT: every apply writes a new version
# and S3 keeps the old one forever. The lifecycle rule in section 4 is what
# stops that becoming a line item. Versioning without an expiration rule is
# the classic silent-growth trap of this day.
###############################################################################

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

###############################################################################
# 3. Encryption
#
# Two reasons this matters more than it looks:
#
#   a) Your state file contains every attribute of every resource, including
#      RDS master passwords, generated secrets, private keys and any variable
#      you marked `sensitive`. `sensitive = true` hides a value from the CLI
#      OUTPUT. It does not encrypt it, redact it, or keep it out of state. It
#      is a console-printing hint, nothing more.
#   b) Anyone with s3:GetObject on this bucket has read every one of those
#      secrets, in plaintext, without touching a single resource.
#
# SSE-S3 (AES256) is free and satisfies the check. A customer-managed KMS key
# costs $1/month and buys you something real: a separate grant policy, key
# rotation, and the ability to REVOKE access to the state file without
# touching the bucket policy. On a production backend, pay the dollar.
###############################################################################

resource "aws_kms_key" "state" {
  count = var.enable_kms_encryption ? 1 : 0

  description             = "CMK for the ${local.prefix} Terraform state bucket"
  deletion_window_in_days = var.kms_deletion_window_days
  enable_key_rotation     = true
}

resource "aws_kms_alias" "state" {
  count = var.enable_kms_encryption ? 1 : 0

  name          = "alias/${local.prefix}-tfstate"
  target_key_id = aws_kms_key.state[0].key_id
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = var.enable_kms_encryption ? "aws:kms" : "AES256"
      kms_master_key_id = var.enable_kms_encryption ? aws_kms_key.state[0].arn : null
    }

    # S3 Bucket Keys cut KMS request charges by up to 99% by caching a
    # data key at the bucket level. Free. Only meaningful with aws:kms.
    bucket_key_enabled = var.enable_kms_encryption
  }
}

###############################################################################
# 4. Lifecycle — the valve on silent growth
#
# Versioning is on, so this is where the old versions actually go away.
# Without this rule the bucket grows by one full state file on every single
# apply, forever, and nobody ever notices because 200 KB is invisible until
# it is 40,000 of them.
###############################################################################

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  # Explicit dependency: applying a lifecycle rule that talks about
  # noncurrent versions to a bucket where versioning has not finished
  # enabling is a race Terraform will happily lose.
  depends_on = [aws_s3_bucket_versioning.state]

  rule {
    id     = "expire-noncurrent-state-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_expiration_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = var.abort_incomplete_upload_days
    }
  }
}

###############################################################################
# 5. Public access block
#
# Four separate switches, all on. This is the difference between "the state
# bucket has a private ACL" and "the state bucket CANNOT be made public, by
# anyone, by accident, ever". The second one is what you want on the object
# that lists every resource, subnet ID and secret in your account.
#
# IAC-004 checks for exactly this and stays silent here on purpose. Being
# silent is the point: a check set where everything fires teaches you nothing
# about false positives, and false positives are how audit tools get ignored.
###############################################################################

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

###############################################################################
# 6. Ownership controls
#
# BucketOwnerEnforced disables ACLs entirely. ACLs are the older, weirder
# access mechanism that predates bucket policies and is responsible for a
# large share of historical S3 leaks. Turning them off is free and removes a
# whole category of mistake.
###############################################################################

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

###############################################################################
# 7. Deny unencrypted transport
#
# A bucket policy that refuses any request not made over TLS. Costs nothing,
# takes four lines, and closes the "someone configured an old SDK with
# http://" hole permanently. Note this is a DENY with a condition — it
# restricts, it never grants. Nobody gains access from this policy.
###############################################################################

data "aws_iam_policy_document" "state_tls_only" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = ["s3:*"]

    resources = [
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state_tls_only.json

  # The public access block must land first. Attaching a policy with a "*"
  # principal to a bucket whose block_public_policy is not yet in place is
  # the one ordering mistake that can briefly widen access.
  depends_on = [aws_s3_bucket_public_access_block.state]
}
