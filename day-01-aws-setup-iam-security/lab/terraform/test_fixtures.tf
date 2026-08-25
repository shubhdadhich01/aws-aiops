###############################################################################
# Deliberately insecure training fixtures
#
# These are NOT attached to users or workloads. They exist so the Python audit
# engine has deterministic policy and trust-policy problems to discover.
###############################################################################

resource "aws_iam_policy" "bad_example" {
  count = var.create_bad_policy ? 1 : 0

  name        = "${local.prefix}-BAD-example-policy"
  path        = "/bootcamp/"
  description = "DELIBERATELY INSECURE training target for the IAM audit tool Never attach this"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "FullAdminOops"
        Effect   = "Allow"
        Action   = "*"
        Resource = "*"
      },
      {
        Sid      = "ServiceWideWildcard"
        Effect   = "Allow"
        Action   = ["s3:*", "iam:*", "kms:*"]
        Resource = "*"
      },
      {
        Sid      = "PassRoleToAnything"
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = "*"
      }
    ]
  })
}

data "aws_iam_policy_document" "bad_trust" {
  count = var.create_bad_policy ? 1 : 0

  statement {
    sid     = "TrustsTheEntireInternet"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
  }
}

resource "aws_iam_role" "bad_example" {
  count = var.create_bad_policy ? 1 : 0

  name               = "${local.prefix}-BAD-open-trust-role"
  path               = "/bootcamp/"
  description        = "DELIBERATELY INSECURE open trust policy training target only"
  assume_role_policy = data.aws_iam_policy_document.bad_trust[0].json
}
