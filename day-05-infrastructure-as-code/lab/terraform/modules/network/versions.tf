###############################################################################
# modules/network/versions.tf
#
# THERE IS NO `provider` BLOCK IN THIS FILE, OR ANYWHERE IN THIS MODULE.
#
# That is the single most important rule about writing modules, and the one
# most often broken. A child module declares which providers it REQUIRES
# (below). The root module that calls it declares how those providers are
# CONFIGURED — region, profile, default_tags, assume-role.
#
# Put a configured `provider "aws" { region = "us-east-1" }` inside a child
# module and you get, in order:
#
#   1. A module nobody can reuse in another region.
#   2. A module that cannot be used with `count` or `for_each`, because
#      Terraform forbids that on modules with their own provider blocks.
#   3. A deprecation warning today and a hard error on the day you try to
#      remove the module, because Terraform must still be able to configure
#      the provider in order to destroy what it created — and the config just
#      disappeared along with the module block.
#
# Number 3 is the one that ruins an afternoon. The escape hatch exists
# (`removed` blocks, or re-adding the module temporarily) and you do not want
# to need it.
#
# The correct pattern for "this module needs a second region" is an aliased
# provider passed IN by the caller:
#
#   module "network" {
#     source    = "../../modules/network"
#     providers = { aws = aws.eu }
#   }
#
# and `configuration_aliases = [aws.eu]` here in required_providers.
###############################################################################

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.80, < 6.0"
    }
  }
}
