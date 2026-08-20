output "account_id" {
  description = "The AWS account these resources were created in."
  value       = data.aws_caller_identity.current.account_id
}

output "vpc_id" {
  description = "ID of the main VPC. Pass this to the assessment tool with --vpc-id."
  value       = aws_vpc.main.id
}

output "vpc_cidr" {
  description = "CIDR block of the main VPC."
  value       = aws_vpc.main.cidr_block
}

output "availability_zones" {
  description = "AZs the subnets were spread across."
  value       = local.azs
}

output "public_subnet_ids" {
  description = "Public subnet IDs — these have a route to the Internet Gateway."
  value       = aws_subnet.public[*].id
}

output "private_app_subnet_ids" {
  description = "Private application subnet IDs — outbound via NAT, no inbound from the internet."
  value       = aws_subnet.private_app[*].id
}

output "private_data_subnet_ids" {
  description = "Private data subnet IDs — no internet route in either direction."
  value       = aws_subnet.private_data[*].id
}

output "internet_gateway_id" {
  description = "ID of the Internet Gateway."
  value       = aws_internet_gateway.main.id
}

output "nat_gateway_ids" {
  description = "💸 NAT Gateway IDs. Each one bills ~$32/month for as long as it exists."
  value       = aws_nat_gateway.main[*].id
}

output "nat_gateway_public_ips" {
  description = "Public IPs your private subnets appear to come from. Useful for third-party allowlists."
  value       = aws_eip.nat[*].public_ip
}

output "security_group_ids" {
  description = "The correctly-tiered security groups, chained by reference."
  value = {
    alb     = aws_security_group.alb.id
    app     = aws_security_group.app.id
    db      = aws_security_group.db.id
    bastion = aws_security_group.bastion.id
  }
}

output "flow_log_group_name" {
  description = "CloudWatch Logs group receiving VPC flow logs."
  value       = var.enable_flow_logs ? aws_cloudwatch_log_group.flow_logs[0].name : "flow logs disabled"
}

output "s3_gateway_endpoint_id" {
  description = "Free S3 gateway endpoint. Keeps S3 traffic off the NAT Gateway."
  value       = aws_vpc_endpoint.s3.id
}

output "insecure_security_group_ids" {
  description = "😈 Deliberately insecure security groups (training targets for the assessment tool)."
  value = var.create_insecure_examples ? {
    open_ssh = aws_security_group.bad_open_ssh[0].id
    unused   = aws_security_group.bad_unused[0].id
  } : {}
}

output "insecure_vpc_id" {
  description = "😈 The second VPC with no flow logs. Training target for VPC-014."
  value       = var.create_insecure_examples ? aws_vpc.bad_unlogged[0].id : "not created"
}

output "estimated_monthly_cost_usd" {
  description = "⚠️ Rough running cost of what you just created, in us-east-1. Destroy when done."
  value = format(
    "~$%.2f/month  (NAT Gateways: %d × $32.40  |  interface endpoints: %d × $7.30/AZ  |  flow logs: ~$0.50/GB ingest)",
    (local.nat_gateway_count * 32.40) + (length(local.interface_endpoint_services) * 7.30 * var.az_count),
    local.nat_gateway_count,
    length(local.interface_endpoint_services),
  )
}

output "next_steps" {
  description = "What to do now."
  value       = <<-EOT

    ✅ Terraform applied.  VPC ${aws_vpc.main.id} (${aws_vpc.main.cidr_block}) is up.

    💸 COST NOTICE
       NAT Gateways running: ${local.nat_gateway_count}  (approximately USD ${format("%.2f", local.nat_gateway_count * 32.40)} per month)
       This bills per hour whether or not any traffic flows through it.
       When you finish the session:  terraform destroy

    1. Confirm the topology looks right:
         aws ec2 describe-subnets --filters Name=vpc-id,Values=${aws_vpc.main.id} \
           --query 'Subnets[].[Tags[?Key==`Name`]|[0].Value,CidrBlock,AvailabilityZone,MapPublicIpOnLaunch]' \
           --output table

    2. Prove which subnets are public by reading the ROUTES, not the names:
         aws ec2 describe-route-tables --filters Name=vpc-id,Values=${aws_vpc.main.id} \
           --query 'RouteTables[].[Tags[?Key==`Name`]|[0].Value,Routes[?DestinationCidrBlock==`0.0.0.0/0`].GatewayId|[0]]' \
           --output table

    3. Run the assessment tool:
         cd ../python && python3 vpc_assess.py --profile bootcamp --vpc-id ${aws_vpc.main.id}

    4. Then run it across the whole region and see what else it finds:
         python3 vpc_assess.py --profile bootcamp

    5. Fix one finding, re-run, watch the score move.

    6. 🧹 When finished for the day — this one is NOT optional:
         terraform destroy
         (then verify with ../../teardown-checklist.md)
  EOT
}
