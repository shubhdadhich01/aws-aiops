###############################################################################
# modules/compute/outputs.tf
###############################################################################

output "instance_ids" {
  description = "Map of logical instance name to EC2 instance ID."
  value       = { for name, inst in aws_instance.this : name => inst.id }
}

output "instance_private_ips" {
  description = "Map of logical instance name to private IP address."
  value       = { for name, inst in aws_instance.this : name => inst.private_ip }
}

output "instance_public_ips" {
  description = "Map of logical instance name to public IP, or an empty map when associate_public_ip is false. Public IPv4 addresses are billed at ~$3.65/month each."
  value       = var.associate_public_ip ? { for name, inst in aws_instance.this : name => inst.public_ip } : {}
}

output "instance_count" {
  description = "How many instances this module created. Zero by default."
  value       = length(aws_instance.this)
}

output "iam_role_name" {
  description = "Name of the EC2 instance role, for callers that need to attach an extra policy."
  value       = aws_iam_role.instance.name
}

output "iam_role_arn" {
  description = "ARN of the EC2 instance role."
  value       = aws_iam_role.instance.arn
}

output "instance_profile_name" {
  description = "Name of the instance profile attached to every instance."
  value       = aws_iam_instance_profile.instance.name
}

output "ami_id" {
  description = "AMI the instances were launched from. Resolved from the SSM public parameter at plan time, and ignored on subsequent plans (see the ignore_changes block in main.tf)."
  value       = local.ami_id
}

output "ssm_session_command" {
  description = "Ready-to-paste Session Manager command for the first instance, or a note when there are none. No SSH key, no port 22, no bastion."
  value = length(aws_instance.this) > 0 ? format(
    "aws ssm start-session --target %s",
    aws_instance.this[sort(keys(aws_instance.this))[0]].id
  ) : "No instances created — var.instances is empty."
}

output "estimated_monthly_cost_usd" {
  description = "Compute + gp3 storage + public IPv4 addresses per month, us-east-1 on-demand. An estimate from a static price table, not a quote."
  value       = format("%.2f", local.monthly_compute_cost + local.monthly_storage_cost + local.monthly_public_ip_cost)
}

output "cost_breakdown" {
  description = "Where the compute money goes."
  value = {
    instances = format("$%.2f/month — %d instance(s) at on-demand rates", local.monthly_compute_cost, length(var.instances))
    storage   = format("$%.2f/month — gp3 root volumes at $0.08/GB-month", local.monthly_storage_cost)
    public_ip = format("$%.2f/month — public IPv4 at $0.005/hour each, charged since Feb 2024", local.monthly_public_ip_cost)
  }
}
