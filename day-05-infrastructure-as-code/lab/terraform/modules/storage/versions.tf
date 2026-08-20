###############################################################################
# modules/storage/versions.tf
#
# No provider block. See modules/network/versions.tf for the full argument;
# the short version is that a configured provider inside a child module makes
# the module un-reusable, un-iterable and — the one that actually hurts —
# impossible to remove cleanly, because Terraform still needs the provider
# configuration in order to destroy what the module created.
#
# random is required here because bucket names are globally unique across
# every AWS account on Earth. A module that hardcodes a bucket name works
# exactly once, for exactly one person.
###############################################################################

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.80, < 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6, < 4.0"
    }
  }
}
