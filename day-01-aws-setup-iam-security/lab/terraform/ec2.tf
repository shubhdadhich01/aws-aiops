###############################################################################
# EC2 AIOps runner
#
# Bootstrap lives in user_data.sh. Terraform only passes values into the
# external template; the shell logic is intentionally not embedded here.
###############################################################################

resource "aws_instance" "aiops_runner" {
  ami                         = data.aws_ssm_parameter.al2023_ami.value
  instance_type               = var.aiops_instance_type
  subnet_id                   = local.aiops_subnet_id
  vpc_security_group_ids      = [aws_security_group.aiops_runner.id]
  iam_instance_profile        = aws_iam_instance_profile.aiops_runner.name
  associate_public_ip_address = true
  key_name                    = var.aiops_key_name != "" ? var.aiops_key_name : null

  user_data = templatefile("${path.module}/user_data.sh", {
    ollama_model            = var.ollama_model
    git_repository_url      = var.git_repository_url
    git_branch              = var.git_branch
    security_audit_role_arn = local.security_audit_role_arn
    aws_region              = data.aws_region.current.region
  })

  user_data_replace_on_change = true

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.aiops_root_volume_gb
    encrypted             = true
    delete_on_termination = true
  }

  tags = {
    Name = "${local.prefix}-aiops-runner"
    Role = "AIOpsRunner"
  }

  depends_on = [
    aws_iam_role_policy.aiops_runner_permissions,
    aws_iam_role_policy_attachment.security_audit,
    aws_iam_role_policy_attachment.audit_extra,
  ]
}
