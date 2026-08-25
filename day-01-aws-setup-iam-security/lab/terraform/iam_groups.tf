###############################################################################
# IAM groups and least-privilege developer/read-only access
###############################################################################

resource "aws_iam_group" "developers" {
  name = "${local.prefix}-developers"
  path = "/bootcamp/"
}

resource "aws_iam_group" "readonly" {
  name = "${local.prefix}-readonly"
  path = "/bootcamp/"
}

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
    resources = ["*"]

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
  description = "Least-privilege developer access for the Day 01 lab."
  policy      = data.aws_iam_policy_document.developer.json
}

resource "aws_iam_group_policy_attachment" "developer" {
  group      = aws_iam_group.developers.name
  policy_arn = aws_iam_policy.developer.arn
}

resource "aws_iam_group_policy_attachment" "readonly" {
  group      = aws_iam_group.readonly.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}
