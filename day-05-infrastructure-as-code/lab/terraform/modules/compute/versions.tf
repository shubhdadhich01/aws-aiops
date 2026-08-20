###############################################################################
# modules/compute/versions.tf
#
# Same rule as every other module in this lab: declare what the module
# REQUIRES, never how it is CONFIGURED. No provider block lives here.
#
# On the version constraint below — `>= 5.80, < 6.0` rather than `~> 5.80`:
#
#   ~> 5.80   means >= 5.80.0, < 5.81.0   (patch only)
#   ~> 5.80.0 means >= 5.80.0, < 5.81.0   (identical; the extra digit changes
#                                          nothing, which surprises everyone)
#   ~> 5.80   at the two-component level is often WRITTEN meaning "any 5.x
#             from 5.80" — it does not mean that.
#
# For a shared MODULE, the constraint should be as wide as you can honestly
# support, because every caller has to satisfy the intersection of every
# module's constraint. A module that pins to one patch release makes itself
# uncombinable. Write the floor you actually need and the major version you
# have tested, and let the ROOT module pin precisely.
#
# The root pins. The module permits. Get that backwards and adding a second
# module to a project becomes a dependency-resolution puzzle.
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
