###############################################################################
# modules/network/outputs.tf
#
# Outputs are the module's return values, and they are also its coupling
# surface. Every output you publish is a thing a caller can depend on and a
# thing you can no longer change freely. Publish what callers genuinely need
# and stop.
#
# Note what is NOT here: no raw resource objects. Returning
# `value = aws_vpc.this` exports every attribute including ones that will
# appear and disappear across provider versions, and it makes the module's
# API the provider's API. Return IDs, ARNs and maps.
###############################################################################

output "vpc_id" {
  description = "ID of the VPC."
  value       = aws_vpc.this.id
}

output "vpc_cidr_block" {
  description = "CIDR block of the VPC, for callers that need to scope a security group rule to it."
  value       = aws_vpc.this.cidr_block
}

output "public_subnet_ids" {
  description = "Map of availability zone to public subnet ID. A map, not a list, so callers can address a specific AZ without counting."
  value       = { for az, subnet in aws_subnet.public : az => subnet.id }
}

output "private_subnet_ids" {
  description = "Map of availability zone to private subnet ID. Empty when no private subnets were requested."
  value       = { for az, subnet in aws_subnet.private : az => subnet.id }
}

output "public_subnet_id_list" {
  description = "Public subnet IDs as a sorted list, for resources whose argument is a list (load balancers, for one)."
  value       = [for az in sort(keys(aws_subnet.public)) : aws_subnet.public[az].id]
}

output "app_security_group_id" {
  description = "ID of the application security group. Ingress is VPC-internal only."
  value       = aws_security_group.app.id
}

output "internet_gateway_id" {
  description = "ID of the internet gateway."
  value       = aws_internet_gateway.this.id
}

output "nat_gateway_id" {
  description = "ID of the NAT gateway, or null when enable_nat_gateway is false. Callers should not assume this is populated."
  value       = local.nat_gateway_enabled ? aws_nat_gateway.this[0].id : null
}

output "nat_gateway_enabled" {
  description = "Whether a NAT gateway was actually created. True costs ~$32.40/month plus data processing."
  value       = local.nat_gateway_enabled
}

output "availability_zones" {
  description = "Sorted list of availability zones this network spans."
  value       = sort(distinct(concat(keys(var.public_subnets), keys(var.private_subnets))))
}

output "flow_logs_enabled" {
  description = "Whether VPC flow logs were created."
  value       = var.enable_flow_logs
}

output "estimated_monthly_cost_usd" {
  description = "What this module costs per month as configured, us-east-1. Modules that cost money should say so; the caller aggregates."
  value = format(
    "%.2f",
    (local.nat_gateway_enabled ? 32.40 : 0.0) + (var.enable_flow_logs ? 0.50 : 0.0)
  )
}
