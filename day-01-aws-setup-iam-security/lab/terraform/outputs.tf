output "day1_security_audit_role_arn" {
  description = "Role used by the Python auditor for read-only IAM inspection."
  value       = local.security_audit_role_arn
}

output "day1_aiops_runner_role_arn" {
  description = "EC2 instance role; it may only assume the Day 01 security-audit role."
  value       = aws_iam_role.aiops_runner.arn
}

output "day1_aiops_runner_instance_id" {
  description = "EC2 instance ID hosting Python + Ollama + Qwen3."
  value       = aws_instance.aiops_runner.id
}

output "day1_aiops_runner_public_ip" {
  description = "Public IP of the AIOps EC2 runner. SSH is restricted by allowed_ssh_cidr."
  value       = aws_instance.aiops_runner.public_ip
}

output "day1_aiops_runner_ssh" {
  description = "Example SSH command for the AIOps runner."
  value       = "ssh ec2-user@${aws_instance.aiops_runner.public_ip}"
}

output "day1_ollama_model" {
  description = "Ollama model bootstrapped on the AIOps runner."
  value       = var.ollama_model
}

output "day1_ollama_endpoint" {
  description = "Ollama endpoint; intentionally local to the EC2 instance."
  value       = "http://127.0.0.1:11434"
}

output "day1_bad_policy_arn" {
  description = "Deliberately insecure customer-managed policy used as an audit fixture."
  value       = var.create_bad_policy ? aws_iam_policy.bad_example[0].arn : null
}

output "day1_bad_role_name" {
  description = "Deliberately insecure open-trust role used as an audit fixture."
  value       = var.create_bad_policy ? aws_iam_role.bad_example[0].name : null
}
