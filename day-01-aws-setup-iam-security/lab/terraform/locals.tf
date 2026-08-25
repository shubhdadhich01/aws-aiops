locals {
  account_id = data.aws_caller_identity.current.account_id
  prefix     = var.name_prefix

  # Keep both role ARNs as strings so the two trust relationships do not create
  # a Terraform dependency cycle:
  #   EC2 role policy -> audit role
  #   audit role trust -> EC2 role
  security_audit_role_arn = "arn:aws:iam::${local.account_id}:role/bootcamp/${local.prefix}-security-audit"
  aiops_runner_role_arn   = "arn:aws:iam::${local.account_id}:role/bootcamp/${local.prefix}-aiops-runner"

  aiops_subnet_id = var.aiops_subnet_id != "" ? var.aiops_subnet_id : data.aws_subnets.default.ids[0]
}
