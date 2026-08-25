###############################################################################
# EC2 instance role
#
# The EC2 machine gets NO SecurityAudit permission directly.
# Its only AWS application permission is sts:AssumeRole on the dedicated audit
# role above.
###############################################################################

data "aws_iam_policy_document" "aiops_runner_trust" {
  statement {
    sid     = "AllowEC2"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "aiops_runner" {
  name               = "${local.prefix}-aiops-runner"
  path               = "/bootcamp/"
  description        = "EC2 execution role that may assume the Day 01 security-audit role."
  assume_role_policy = data.aws_iam_policy_document.aiops_runner_trust.json
}

data "aws_iam_policy_document" "aiops_runner_permissions" {
  statement {
    sid       = "AssumeOnlyDay01AuditRole"
    effect    = "Allow"
    actions   = ["sts:AssumeRole"]
    resources = [local.security_audit_role_arn]
  }
}

resource "aws_iam_role_policy" "aiops_runner_permissions" {
  name   = "${local.prefix}-assume-audit-role"
  role   = aws_iam_role.aiops_runner.id
  policy = data.aws_iam_policy_document.aiops_runner_permissions.json
}

resource "aws_iam_instance_profile" "aiops_runner" {
  name = "${local.prefix}-aiops-runner"
  role = aws_iam_role.aiops_runner.name
}
