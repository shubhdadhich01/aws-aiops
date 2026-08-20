###############################################################################
# Day 01 — AWS Environment Setup & IAM Security
#
# What this builds:
#   1. Account password policy      (baseline control #3)
#   2. Two IAM groups + scoped, least-privilege customer-managed policies
#   3. A security-audit role you assume with STS  (this is the "right way")
#   4. A monthly budget with 4 alert thresholds   (cost = security telemetry)
#   5. Optionally, one deliberately terrible policy for the audit tool to find
#
# Everything is prefixed `cbc-day01-` and tagged Project=aws-aiops-bootcamp.
###############################################################################

locals {
  account_id = data.aws_caller_identity.current.account_id
  prefix     = var.name_prefix
}

###############################################################################
# 1. ACCOUNT PASSWORD POLICY
#
# One per account — it's a singleton resource. If your account already has one,
# import it instead of creating:
#   terraform import aws_iam_account_password_policy.this iam-account-password-policy
###############################################################################

resource "aws_iam_account_password_policy" "this" {
  minimum_password_length        = var.min_password_length
  require_uppercase_characters   = true
  require_lowercase_characters   = true
  require_numbers                = true
  require_symbols                = true
  allow_users_to_change_password = true
  max_password_age               = var.max_password_age_days
  password_reuse_prevention      = 24
  hard_expiry                    = false # true would lock users out entirely — needs a break-glass plan
}

###############################################################################
# 2. GROUPS + SCOPED POLICIES
#
# Permissions go on GROUPS, never on individual users. The moment you attach a
# policy straight to a user, permission drift starts.
###############################################################################

resource "aws_iam_group" "developers" {
  name = "${local.prefix}-developers"
  path = "/bootcamp/"
}

resource "aws_iam_group" "readonly" {
  name = "${local.prefix}-readonly"
  path = "/bootcamp/"
}

# --- Developer policy -------------------------------------------------------
# Read-heavy, write only to explicitly tagged dev resources, region-locked.
# Note the aws:RequestedRegion condition — a cheap, powerful blast-radius control.

data "aws_iam_policy_document" "developer" {

  statement {
    sid    = "ReadOnlyCoreServices"
    effect = "Allow"
    actions = [
      "ec2:Describe*",
      "s3:List*",
      "s3:GetBucketLocation",
      "cloudwatch:Get*",
      "cloudwatch:List*",
      "cloudwatch:Describe*",
      "logs:Describe*",
      "logs:Get*",
      "logs:FilterLogEvents",
      "iam:Get*",
      "iam:List*",
    ]
    resources = ["*"] # Describe/List APIs are account-wide by nature; scope with conditions instead
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = var.allowed_regions
    }
  }

  statement {
    sid    = "WriteOnlyToDevTaggedResources"
    effect = "Allow"
    actions = [
      "ec2:StartInstances",
      "ec2:StopInstances",
      "ec2:RebootInstances",
    ]
    resources = ["arn:aws:ec2:*:${local.account_id}:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Environment"
      values   = ["dev"]
    }
  }

  statement {
    sid    = "ManageOwnCredentials"
    effect = "Allow"
    actions = [
      "iam:ChangePassword",
      "iam:CreateAccessKey",
      "iam:DeleteAccessKey",
      "iam:UpdateAccessKey",
      "iam:ListAccessKeys",
      "iam:CreateVirtualMFADevice",
      "iam:EnableMFADevice",
      "iam:ResyncMFADevice",
    ]
    # IAM policy variable: each user can only touch their OWN credentials.
    resources = ["arn:aws:iam::${local.account_id}:user/&{aws:username}"]
  }

  statement {
    sid    = "DenyEverythingWithoutMFA"
    effect = "Deny"
    not_actions = [
      "iam:CreateVirtualMFADevice",
      "iam:EnableMFADevice",
      "iam:GetUser",
      "iam:ListMFADevices",
      "iam:ListVirtualMFADevices",
      "iam:ResyncMFADevice",
      "sts:GetSessionToken",
    ]
    resources = ["*"]
    condition {
      test     = "BoolIfExists"
      variable = "aws:MultiFactorAuthPresent"
      values   = ["false"]
    }
  }
}

resource "aws_iam_policy" "developer" {
  name        = "${local.prefix}-developer-policy"
  path        = "/bootcamp/"
  description = "Least-privilege developer access: read core services, write only to dev-tagged EC2, MFA required."
  policy      = data.aws_iam_policy_document.developer.json
}

resource "aws_iam_group_policy_attachment" "developer" {
  group      = aws_iam_group.developers.name
  policy_arn = aws_iam_policy.developer.arn
}

# --- Read-only group --------------------------------------------------------
# Uses the AWS-managed ReadOnlyAccess policy. Reusing AWS-managed policies where
# they genuinely fit is good practice — they're maintained as new services launch.

resource "aws_iam_group_policy_attachment" "readonly" {
  group      = aws_iam_group.readonly.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

###############################################################################
# 3. SECURITY AUDIT ROLE
#
# Two policies, and they do completely different jobs:
#   - assume_role_policy  = the TRUST policy   → WHO can wear the hat
#   - attached policies   = PERMISSION policy  → WHAT the hat can do
###############################################################################

data "aws_iam_policy_document" "audit_trust" {
  statement {
    sid     = "AllowThisAccountToAssume"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }

    # Belt and braces: require MFA for the assumption.
    # Comment this block out if you have not yet set up an MFA device.
    condition {
      test     = "Bool"
      variable = "aws:MultiFactorAuthPresent"
      values   = ["true"]
    }
  }
}

resource "aws_iam_role" "security_audit" {
  name                 = "${local.prefix}-security-audit"
  path                 = "/bootcamp/"
  description          = "Read-only role used by the Day 01 Python IAM audit tool."
  assume_role_policy   = data.aws_iam_policy_document.audit_trust.json
  max_session_duration = 3600 # 1 hour — short sessions are a security control
}

# AWS-managed SecurityAudit policy: read-only across security-relevant services.
resource "aws_iam_role_policy_attachment" "security_audit" {
  role       = aws_iam_role.security_audit.name
  policy_arn = "arn:aws:iam::aws:policy/SecurityAudit"
}

# The audit tool needs a couple of IAM reads that SecurityAudit doesn't include.
data "aws_iam_policy_document" "audit_extra" {
  statement {
    sid    = "IamReadForAuditTool"
    effect = "Allow"
    actions = [
      "iam:GetAccountSummary",
      "iam:GetAccountPasswordPolicy",
      "iam:GetLoginProfile",
      "iam:GetAccessKeyLastUsed",
      "iam:GenerateCredentialReport",
      "iam:GetCredentialReport",
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

###############################################################################
# 4. BUDGET + ALERTS
#
# Cost is security telemetry. A compromised key shows up as spend long before it
# shows up anywhere else you're looking.
###############################################################################

resource "aws_sns_topic" "budget_alerts" {
  name = "${local.prefix}-budget-alerts"
}

resource "aws_sns_topic_subscription" "budget_email" {
  topic_arn = aws_sns_topic.budget_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
  # ⚠️ You must click the confirmation link AWS emails you, or alerts go nowhere.
}

# Allow the AWS Budgets service to publish to this topic.
data "aws_iam_policy_document" "sns_budget" {
  statement {
    sid     = "AllowBudgetsToPublish"
    effect  = "Allow"
    actions = ["SNS:Publish"]
    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }
    resources = [aws_sns_topic.budget_alerts.arn]
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "budget_alerts" {
  arn    = aws_sns_topic.budget_alerts.arn
  policy = data.aws_iam_policy_document.sns_budget.json
}

resource "aws_budgets_budget" "monthly" {
  name         = "${local.prefix}-monthly-budget"
  budget_type  = "COST"
  limit_amount = var.budget_limit_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # 50% actual — "am I on track?"
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts.arn]
  }

  # 80% actual — "investigate now"
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts.arn]
  }

  # 100% actual — "something is wrong"
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts.arn]
  }

  # ⭐ 100% FORECASTED — the one that actually saves you.
  # Fires before the money is spent, not after.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts.arn]
  }
}

###############################################################################
# 5. THE DELIBERATELY BAD POLICY  😈
#
# This exists ONLY so the Python audit tool has something to find. It is never
# attached to any user, group or role — an unattached policy grants nothing.
#
# In a real account, a policy like this is a critical finding even unattached,
# because someone will eventually attach it.
###############################################################################

resource "aws_iam_policy" "bad_example" {
  count = var.create_bad_policy ? 1 : 0

  name        = "${local.prefix}-BAD-example-policy"
  path        = "/bootcamp/"
  description = "DELIBERATELY INSECURE — training target for the IAM audit tool. Never attach this."

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

# A second target: a role with a dangerously open trust policy.
data "aws_iam_policy_document" "bad_trust" {
  count = var.create_bad_policy ? 1 : 0

  statement {
    sid     = "TrustsTheEntireInternet"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = ["*"] # 🔴 anyone, anywhere. Never do this.
    }
    # Note: no ExternalId, no MFA condition, no source account restriction.
  }
}

resource "aws_iam_role" "bad_example" {
  count = var.create_bad_policy ? 1 : 0

  name               = "${local.prefix}-BAD-open-trust-role"
  path               = "/bootcamp/"
  description        = "DELIBERATELY INSECURE — open trust policy, training target only."
  assume_role_policy = data.aws_iam_policy_document.bad_trust[0].json
  # No permission policies attached, so assuming it grants nothing.
  # The finding is about the trust policy, not the permissions.
}
