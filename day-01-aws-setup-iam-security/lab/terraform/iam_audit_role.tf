###############################################################################
# Dedicated read-only IAM audit role
#
# EC2 instance role -> sts:AssumeRole -> this role -> IAM read operations
###############################################################################

data "aws_iam_policy_document" "audit_trust" {
  statement {
    sid     = "AllowAIOpsRunnerToAssume"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    # Trust the account, then narrow the caller to the exact EC2 role using
    # aws:PrincipalArn. This avoids a Terraform dependency cycle between the
    # EC2 role policy and this role's trust policy.
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:PrincipalArn"
      values   = [local.aiops_runner_role_arn]
    }

    dynamic "condition" {
      for_each = var.require_audit_role_mfa ? [1] : []

      content {
        test     = "Bool"
        variable = "aws:MultiFactorAuthPresent"
        values   = ["true"]
      }
    }
  }
}

resource "aws_iam_role" "security_audit" {
  name                 = "${local.prefix}-security-audit"
  path                 = "/bootcamp/"
  description          = "Read-only role used by the Day 01 Python IAM audit tool."
  assume_role_policy   = data.aws_iam_policy_document.audit_trust.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "security_audit" {
  role       = aws_iam_role.security_audit.name
  policy_arn = "arn:aws:iam::aws:policy/SecurityAudit"
}

data "aws_iam_policy_document" "audit_extra" {
  statement {
    sid    = "IamReadForAuditTool"
    effect = "Allow"
    actions = [
      "iam:GetAccountPasswordPolicy",
      "iam:GetLoginProfile",
      "iam:GetAccessKeyLastUsed",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "audit_extra" {
  name        = "${local.prefix}-audit-extra-reads"
  path        = "/bootcamp/"
  description = "Extra read-only IAM permissions required by the Day 01 audit tool."
  policy      = data.aws_iam_policy_document.audit_extra.json
}

resource "aws_iam_role_policy_attachment" "audit_extra" {
  role       = aws_iam_role.security_audit.name
  policy_arn = aws_iam_policy.audit_extra.arn
}
