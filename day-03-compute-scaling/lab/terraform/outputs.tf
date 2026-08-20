###############################################################################
# Day 03 — outputs.tf
#
# Two of these matter more than the rest:
#   next_steps                 — what to actually type after the apply
#   estimated_monthly_cost_usd — the number that stops you leaving this running
###############################################################################

###############################################################################
# The thing you'll actually open
###############################################################################

output "alb_dns_name" {
  description = "Public DNS name of the Application Load Balancer. Open this in a browser; refresh to see the instance ID change."
  value       = aws_lb.main.dns_name
}

output "alb_url" {
  description = "Ready-to-click URL for the load balancer."
  value       = var.acm_certificate_arn == "" ? "http://${aws_lb.main.dns_name}" : "https://${aws_lb.main.dns_name}"
}

output "health_check_url" {
  description = "The health endpoint the target group polls every 15 seconds."
  value       = "http://${aws_lb.main.dns_name}/health"
}

###############################################################################
# Identifiers you'll paste into CLI commands during the lab
###############################################################################

output "asg_name" {
  description = "Auto Scaling Group name — use with `aws autoscaling describe-auto-scaling-groups`."
  value       = aws_autoscaling_group.app.name
}

output "target_group_arn" {
  description = "Target group ARN — use with `aws elbv2 describe-target-health`."
  value       = aws_lb_target_group.app.arn
}

output "launch_template_id" {
  description = "Launch template ID for the app tier."
  value       = aws_launch_template.app.id
}

output "launch_template_latest_version" {
  description = "Latest launch template version number. The ASG tracks $Latest."
  value       = aws_launch_template.app.latest_version
}

output "vpc_id" {
  description = "Day 03 VPC ID. Independent of Day 02 — destroying this destroys nothing else."
  value       = aws_vpc.main.id
}

output "availability_zones" {
  description = "AZs this stack is spread across. Two or more, or it is not HA."
  value       = local.azs
}

output "app_subnet_ids" {
  description = "Private app subnet IDs the ASG launches into."
  value       = aws_subnet.app[*].id
}

output "broken_asg_name" {
  description = "Name of the deliberately broken ASG, if create_insecure_examples is true. This is what ha_audit.py should light up on."
  value       = var.create_insecure_examples ? aws_autoscaling_group.broken[0].name : "not created (create_insecure_examples = false)"
}

###############################################################################
# Cost
###############################################################################

locals {
  # us-east-1 on-demand pricing, USD. Update if AWS moves them.
  price_alb_hour             = 0.0225 # ALB base
  price_alb_lcu_hour         = 0.008  # per LCU-hour; ~1 LCU at lab traffic
  price_nat_hour             = 0.045  # NAT Gateway base (data transfer extra)
  price_nlb_hour             = 0.0225 # the deliberately-broken NLB
  price_ebs_gb_month         = 0.08   # gp3
  price_detailed_metric_month = 0.30  # per custom/detailed metric

  # Instance hourly price. Extend this map if you change instance_type.
  instance_hourly_prices = {
    "t3.nano"   = 0.0052
    "t3.micro"  = 0.0104
    "t3.small"  = 0.0208
    "t3.medium" = 0.0416
    "t2.micro"  = 0.0116
    "m5.large"  = 0.096
  }

  instance_hour_price = lookup(local.instance_hourly_prices, var.instance_type, 0.0104)

  # Instances: the main ASG at desired capacity, plus one for the broken ASG.
  total_instances = var.instance_count + (var.create_insecure_examples ? 1 : 0)

  hourly_compute = local.total_instances * local.instance_hour_price
  hourly_alb     = local.price_alb_hour + local.price_alb_lcu_hour
  hourly_nat     = var.enable_nat_gateway ? local.price_nat_hour : 0
  hourly_nlb     = var.create_insecure_examples ? local.price_nlb_hour : 0

  hourly_total = local.hourly_compute + local.hourly_alb + local.hourly_nat + local.hourly_nlb

  # Monthly = 730 hours, plus the things billed per month rather than per hour.
  monthly_ebs = local.total_instances * var.root_volume_size_gb * local.price_ebs_gb_month
  monthly_cw  = var.enable_detailed_monitoring ? 7 * local.price_detailed_metric_month : 0

  monthly_total = (local.hourly_total * 730) + local.monthly_ebs + local.monthly_cw
}

output "estimated_hourly_cost_usd" {
  description = "Approximate on-demand cost per hour while this stack is running (us-east-1, excluding data transfer). Multiply by the hours you leave it up."
  value       = format("$%.4f/hour", local.hourly_total)
}

output "estimated_monthly_cost_usd" {
  description = <<-DESC
    Approximate cost if you leave this running for a 730-hour month.
    Excludes data transfer and request charges, which are pennies at lab scale.
    EC2 may be $0 if you are inside the 12-month free tier's 750 t3.micro hours.

    If this number surprises you: run `terraform destroy`.
  DESC
  value       = format("$%.2f/month", local.monthly_total)
}

output "cost_breakdown" {
  description = "Line-by-line monthly estimate so you can see where the money goes."
  value = {
    application_load_balancer = format("$%.2f", local.hourly_alb * 730)
    nat_gateway               = var.enable_nat_gateway ? format("$%.2f", local.price_nat_hour * 730) : "$0.00 (disabled)"
    ec2_instances             = format("$%.2f  (%d x %s, may be $0 on free tier)", local.hourly_compute * 730, local.total_instances, var.instance_type)
    broken_nlb                = var.create_insecure_examples ? format("$%.2f  (delete create_insecure_examples to remove)", local.price_nlb_hour * 730) : "$0.00 (not created)"
    ebs_root_volumes          = format("$%.2f", local.monthly_ebs)
    cloudwatch_detailed       = var.enable_detailed_monitoring ? format("$%.2f", local.monthly_cw) : "$0.00 (disabled)"
    TOTAL                     = format("$%.2f", local.monthly_total)
  }
}

output "cheaper_mode_hint" {
  description = "How to cut the bill if you need this up for more than an afternoon."
  value       = <<-HINT
    Set in terraform.tfvars:
      enable_nat_gateway         = false   -> saves $32.40/month
      create_insecure_examples   = false   -> saves ~$24/month (NLB + 1 instance)
      enable_detailed_monitoring = false   -> saves ~$2.10/month
      instance_count             = 1       -> saves ~$7.49/month (but no HA)

    Better idea: leave the defaults, finish the lab in one sitting, and run
    `terraform destroy`. Three hours of this stack costs about $0.30.
  HINT
}

###############################################################################
# next_steps — the output people actually read
###############################################################################

output "next_steps" {
  description = "Copy-paste command sequence for the rest of the lab."
  value       = <<-STEPS

    ============================================================================
      Day 03 — Compute Architecture & Intelligent Scaling
      Stack is up. Estimated cost: ${format("$%.4f/hour", local.hourly_total)} / ${format("$%.2f/month", local.monthly_total)}
    ============================================================================

    1. OPEN THE APP  (wait ~2 min after apply for targets to go healthy)

         open http://${aws_lb.main.dns_name}
         curl -s http://${aws_lb.main.dns_name} | grep -o 'i-[0-9a-f]*'

       Refresh a few times. The instance ID should change — that is the ALB
       round-robining across AZs.

    2. CONFIRM BOTH TARGETS ARE HEALTHY

         aws elbv2 describe-target-health \
           --target-group-arn ${aws_lb_target_group.app.arn} \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           --query 'TargetHealthDescriptions[].{Id:Target.Id,AZ:Target.AvailabilityZone,State:TargetHealth.State}' \
           --output table

    3. CHAOS TEST — kill an instance and watch it come back

         VICTIM=$(aws autoscaling describe-auto-scaling-groups \
           --auto-scaling-group-names ${aws_autoscaling_group.app.name} \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           --query 'AutoScalingGroups[0].Instances[0].InstanceId' --output text)

         aws ec2 terminate-instances --instance-ids $VICTIM \
           --profile ${var.aws_profile} --region ${var.aws_region}

       Then watch, in another terminal:

         watch -n 5 "aws elbv2 describe-target-health \
           --target-group-arn ${aws_lb_target_group.app.arn} \
           --profile ${var.aws_profile} --region ${var.aws_region} \
           --query 'TargetHealthDescriptions[].TargetHealth.State' --output text"

       Keep curling the ALB the whole time. You should see zero failed requests.

    4. RUN THE RESILIENCE AUDITOR

         cd ../python
         pip install -r requirements.txt
         python3 ha_audit.py --profile ${var.aws_profile} --region ${var.aws_region}

       Expect 10+ findings. The broken ASG (${var.create_insecure_examples ? "cbc-day03-broken-asg-*" : "disabled"})
       is there on purpose. Read every finding before you fix anything.

    5. TRY THE OUTPUT FORMATS

         python3 ha_audit.py --format json --quiet > findings.json
         python3 ha_audit.py --format csv --min-severity HIGH
         python3 ha_audit.py --fail-on HIGH ; echo "exit code: $?"

    6. DESTROY. THIS IS NOT OPTIONAL.

         terraform destroy -auto-approve

       Then verify with the checklist: ../../teardown-checklist.md

    ============================================================================
  STEPS
}
