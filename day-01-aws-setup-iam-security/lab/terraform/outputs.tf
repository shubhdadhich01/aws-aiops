output "account_id" {
  description = "The AWS account these resources were created in."
  value       = data.aws_caller_identity.current.account_id
}

output "developers_group_name" {
  description = "Name of the developers IAM group."
  value       = aws_iam_group.developers.name
}

output "readonly_group_name" {
  description = "Name of the read-only IAM group."
  value       = aws_iam_group.readonly.name
}

output "developer_policy_arn" {
  description = "ARN of the least-privilege developer policy."
  value       = aws_iam_policy.developer.arn
}

output "audit_role_arn" {
  description = "ARN of the security audit role. Assume this to run the Python audit tool."
  value       = aws_iam_role.security_audit.arn
}

output "budget_name" {
  description = "Name of the monthly cost budget."
  value       = aws_budgets_budget.monthly.name
}

output "budget_sns_topic_arn" {
  description = "SNS topic that receives budget notifications."
  value       = aws_sns_topic.budget_alerts.arn
}

output "bad_policy_arn" {
  description = "ARN of the deliberately over-permissive policy (training target for the audit tool)."
  value       = var.create_bad_policy ? aws_iam_policy.bad_example[0].arn : "not created"
}

output "bad_role_arn" {
  description = "ARN of the deliberately open-trust role (training target for the audit tool)."
  value       = var.create_bad_policy ? aws_iam_role.bad_example[0].arn : "not created"
}

output "audit_profile_snippet" {
  description = "Append this to ~/.aws/config to create a role-assumption profile."
  value       = <<-EOT

    [profile bootcamp-audit]
    role_arn         = ${aws_iam_role.security_audit.arn}
    source_profile   = bootcamp
    region           = ${var.aws_region}
    duration_seconds = 3600
    # If you enabled the MFA condition in the trust policy, uncomment and set:
    # mfa_serial     = arn:aws:iam::${data.aws_caller_identity.current.account_id}:mfa/YOUR_USERNAME
  EOT
}

output "next_steps" {
  description = "What to do now."
  value       = <<-EOT

    ✅ Terraform applied.

    1. Confirm the SNS subscription email AWS just sent to ${var.alert_email}
    2. Append the audit profile to your AWS config:
         terraform output -raw audit_profile_snippet >> ~/.aws/config
    3. Test role assumption:
         aws sts get-caller-identity --profile bootcamp-audit
    4. Run the audit tool:
         cd ../python && python3 iam_audit.py --profile bootcamp
    5. When finished for the day:
         terraform destroy
  EOT
}
