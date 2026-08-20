###############################################################################
# Day 05 — envs/prod/backend.tf
#
# ============================================================================
#  YOU MUST EDIT THE `bucket` LINE BELOW BEFORE `terraform init` WILL WORK.
#  Get the value from: cd ../../backend-bootstrap && terraform output \
#                        -raw state_bucket_name
# ============================================================================
#
# Same bucket as dev. DIFFERENT KEY. That is what keeps two environments that
# share one backend from ever touching each other's state:
#
#   day-05/dev/terraform.tfstate
#   day-05/prod/terraform.tfstate
#
# Same bucket is fine for a bootcamp. In a real estate, prod state belongs in
# a bucket in the PROD ACCOUNT, because whoever can read the state file can
# read prod's secrets, and "we share a bucket but the IAM policy has a prefix
# condition" is one policy edit away from not being true.
#
# The key namespace is worth designing before you have forty of them.
# `<project>/<environment>/terraform.tfstate` scales; `terraform.tfstate` at
# the root does not, and renaming a key later means a state migration.
###############################################################################

terraform {
  backend "s3" {
    # ---- EDIT THIS ----------------------------------------------------------
    bucket = "REPLACE-ME-WITH-YOUR-STATE-BUCKET"
    # -------------------------------------------------------------------------

    key = "day-05/prod/terraform.tfstate"

    region  = "us-east-1"
    profile = "bootcamp"

    encrypt = true

    # S3-native locking. AWS provider >= 5.80, Terraform >= 1.10.
    # The DynamoDB lock table is legacy; see ../dev/backend.tf.
    use_lockfile = true
  }
}
