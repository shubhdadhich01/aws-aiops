###############################################################################
# Day 09 — main.tf
#
# The stack shape, in one paragraph. A minimal VPC with one public subnet
# and one private subnet, one small EC2 instance in the private subnet, one
# S3 bucket, one CloudWatch log group. Plus, on the guardrail side, an
# optional AWS Budget with notifications, an optional Cost Anomaly Detection
# monitor and subscription, and an optional pair of VPC gateway endpoints
# for S3 and DynamoDB. Plus, when create_insecure_examples is true, a
# collection of deliberately-broken resources (unattached EBS, unassociated
# EIPs, a t2.micro, a gp2 volume, a Classic ELB, unbounded log groups, a
# bucket without lifecycle) that give the auditor's Static-State-A findings
# something to fire against.
#
# WHAT THIS STACK DELIBERATELY DOES NOT DO
#
# It does not create RDS, DynamoDB, ASGs, Lambda, or anything else whose
# per-hour cost would dominate the bill. Day 09's whole point is that the
# expensive line items are the SMALL ones you never look at — the log
# groups, the snapshots, the unassociated EIPs — and building an $80/month
# ASG here to demonstrate anomaly detection would drown out exactly the
# category of cost the day is about.
#
# It does not include an automated remediation Lambda. Day 07's argument
# applies with more force here: a Lambda that terminates "unused" resources
# is one small false positive away from deleting production. Every fix
# for a Day 09 finding involves a human, deliberately.
###############################################################################

###############################################################################
# Data sources
###############################################################################

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-arm64"]
  }

  filter {
    name   = "state"
    values = ["available"]
  }
}

###############################################################################
# Local helpers
###############################################################################

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
  name       = "cbc-day09"

  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

###############################################################################
# VPC — small, deliberate
###############################################################################

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${local.name}-vpc"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${local.name}-igw"
  }
}

# One public subnet — for the (optional) NAT gateway and for the (insecure)
# Classic ELB. Deliberately in AZ 0 only; there is no HA story on Day 09.
resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, 0)
  availability_zone       = local.azs[0]
  map_public_ip_on_launch = false

  tags = {
    Name = "${local.name}-public"
    Tier = "public"
  }
}

# One private subnet — for the application EC2 instance.
resource "aws_subnet" "private" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, 1)
  availability_zone = local.azs[0]

  tags = {
    Name = "${local.name}-private"
    Tier = "private"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "${local.name}-public-rt"
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${local.name}-private-rt"
  }
}

resource "aws_route_table_association" "private" {
  subnet_id      = aws_subnet.private.id
  route_table_id = aws_route_table.private.id
}

###############################################################################
# NAT Gateway — optional, expensive
#
# Off by default. When you turn it on, notice what turning it on WITHOUT
# enable_vpc_endpoints does: it creates the exact pattern COST-012 fires on.
###############################################################################

resource "aws_eip" "nat" {
  count  = var.enable_nat_gateway ? 1 : 0
  domain = "vpc"

  tags = {
    Name = "${local.name}-nat-eip"
  }
}

resource "aws_nat_gateway" "main" {
  count = var.enable_nat_gateway ? 1 : 0

  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public.id

  tags = {
    Name = "${local.name}-nat"
  }

  depends_on = [aws_internet_gateway.main]
}

resource "aws_route" "private_default" {
  count = var.enable_nat_gateway ? 1 : 0

  route_table_id         = aws_route_table.private.id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.main[0].id
}

###############################################################################
# VPC gateway endpoints — the free ones
#
# S3 and DynamoDB are the only two AWS services with a GATEWAY endpoint
# (as opposed to an INTERFACE endpoint). Gateway endpoints are free.
# Interface endpoints cost ~$7.30/month per AZ.
#
# When enable_vpc_endpoints is true AND a NAT gateway exists, both are
# attached to the private route table so S3 and DynamoDB traffic bypasses
# NAT and stops billing per-GB. When there is no NAT gateway, the endpoints
# still make sense — they are the mechanism by which the private subnet
# can reach S3 at all — but there is no NAT bill to reduce, so the finding
# COST-012 stays silent regardless.
###############################################################################

resource "aws_vpc_endpoint" "s3" {
  count = var.enable_vpc_endpoints ? 1 : 0

  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${local.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = {
    Name = "${local.name}-s3-endpoint"
  }
}

resource "aws_vpc_endpoint" "dynamodb" {
  count = var.enable_vpc_endpoints ? 1 : 0

  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${local.region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = {
    Name = "${local.name}-dynamodb-endpoint"
  }
}

###############################################################################
# Security group — the smallest one that lets the instance boot
###############################################################################

resource "aws_security_group" "app" {
  name        = "${local.name}-app-sg"
  description = "Application instance — outbound only, no ingress"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "Allow all outbound; no inbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${local.name}-app-sg"
  }
}

###############################################################################
# Application instance — the small, correct one
#
# One t3.micro (or whatever instance_type is) with a gp3 root volume.
# The point of having any instance at all is that COST-015 needs SOMETHING
# to see running for the long-running-instance check to be exercisable.
###############################################################################

resource "aws_instance" "app" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.private.id
  vpc_security_group_ids = [aws_security_group.app.id]

  root_block_device {
    volume_type = var.root_volume_type
    volume_size = var.root_volume_size_gb
    encrypted   = true

    tags = {
      Name = "${local.name}-app-root"
    }
  }

  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  tags = {
    Name = "${local.name}-app"
  }
}

###############################################################################
# CloudWatch log group — the correct one
#
# One log group with retention set. create_insecure_examples adds two more
# with retention UNSET so COST-013 has something to fire on.
###############################################################################

resource "aws_cloudwatch_log_group" "app" {
  name              = "/aws/cbc-day09/app"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${local.name}-app-logs"
  }
}

###############################################################################
# S3 bucket — the correct one
#
# One bucket, encrypted by default, versioned, with a lifecycle rule when
# enable_bucket_lifecycle is true. Without the flag, the bucket ships
# STANDARD-forever, and COST-014 fires on it.
###############################################################################

resource "aws_s3_bucket" "artifacts" {
  bucket        = "${local.name}-artifacts-${local.account_id}"
  force_destroy = true

  tags = {
    Name = "${local.name}-artifacts"
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  count = var.enable_bucket_lifecycle ? 1 : 0

  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "tier-and-expire"
    status = "Enabled"

    filter {}

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 90
      storage_class = "GLACIER_IR"
    }

    expiration {
      days = 365
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

###############################################################################
# AWS Budget — the free guardrail
#
# When enable_budget is true, create a monthly cost budget with the
# thresholds from budget_notifications. Silent by design against COST-002
# because the variable's validation requires at least one notification.
###############################################################################

resource "aws_budgets_budget" "monthly" {
  count = var.enable_budget ? 1 : 0

  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_monthly_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = var.budget_notifications
    content {
      comparison_operator        = notification.value.comparison_operator
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value.notification_type
      subscriber_email_addresses = [var.notification_email]
    }
  }

  tags = {
    Name = "${local.name}-monthly-budget"
  }
}

###############################################################################
# SNS topic — for cost anomaly alerts
###############################################################################

resource "aws_sns_topic" "cost" {
  name = "${local.name}-cost-alerts"

  tags = {
    Name = "${local.name}-cost-alerts"
  }
}

resource "aws_sns_topic_policy" "cost" {
  arn = aws_sns_topic.cost.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowCostAnomalyDetection"
        Effect = "Allow"
        Principal = {
          Service = "costalerts.amazonaws.com"
        }
        Action   = "SNS:Publish"
        Resource = aws_sns_topic.cost.arn
      },
    ]
  })
}

resource "aws_sns_topic_subscription" "cost_email" {
  topic_arn = aws_sns_topic.cost.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

###############################################################################
# Cost Anomaly Detection — the free ML detector
#
# The `aws_ce_anomaly_monitor` resource creates the ML monitor at the
# account level. The `aws_ce_anomaly_subscription` resource wires anomaly
# events to the SNS topic (which forwards them to the email address).
#
# Both must exist to have a working detector. A monitor without a
# subscription is the "speaks to itself" pattern from the day's thesis.
###############################################################################

resource "aws_ce_anomaly_monitor" "account" {
  count = var.enable_cost_anomaly_monitor ? 1 : 0

  name              = "${local.name}-account-monitor"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"

  tags = {
    Name = "${local.name}-account-monitor"
  }
}

resource "aws_ce_anomaly_subscription" "account" {
  count = var.enable_cost_anomaly_monitor ? 1 : 0

  name      = "${local.name}-account-subscription"
  frequency = "DAILY"

  monitor_arn_list = [
    aws_ce_anomaly_monitor.account[0].arn,
  ]

  subscriber {
    type    = "SNS"
    address = aws_sns_topic.cost.arn
  }

  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      match_options = ["GREATER_THAN_OR_EQUAL"]
      values        = [tostring(var.cost_anomaly_threshold_usd)]
    }
  }

  depends_on = [aws_sns_topic_policy.cost]

  tags = {
    Name = "${local.name}-account-subscription"
  }
}

###############################################################################
# INSECURE EXAMPLES — the audit's test bench
#
# Everything below this comment exists only when create_insecure_examples
# is true. Each block includes a comment naming the specific COST-* check
# it exercises. Deleting the block deletes the corresponding fault.
###############################################################################

# COST-005: two unattached EBS volumes.
# gp3, small, unattached. Each is ~$0.64/month for storage alone, and
# together they demonstrate that a check firing twice on the same problem
# is not double-counting — it is two separate resources costing money
# independently.
resource "aws_ebs_volume" "orphan_a" {
  count = var.create_insecure_examples ? 1 : 0

  availability_zone = local.azs[0]
  size              = 8
  type              = "gp3"
  encrypted         = true

  tags = {
    Name    = "${local.name}-orphan-a"
    Purpose = "Deliberately unattached to exercise COST-005"
  }
}

resource "aws_ebs_volume" "orphan_b" {
  count = var.create_insecure_examples ? 1 : 0

  availability_zone = local.azs[0]
  size              = 8
  type              = "gp3"
  encrypted         = true

  tags = {
    Name    = "${local.name}-orphan-b"
    Purpose = "Deliberately unattached to exercise COST-005"
  }
}

# COST-006: two unassociated Elastic IPs.
# Since February 2024, unattached EIPs bill at ~$0.005/hour ($3.60/month)
# EACH. Two here is deliberate — the check should show one finding per
# resource, not one per account, and the STATE A finding count reflects
# that.
resource "aws_eip" "orphan_a" {
  count  = var.create_insecure_examples ? 1 : 0
  domain = "vpc"

  tags = {
    Name    = "${local.name}-orphan-eip-a"
    Purpose = "Deliberately unassociated to exercise COST-006"
  }
}

resource "aws_eip" "orphan_b" {
  count  = var.create_insecure_examples ? 1 : 0
  domain = "vpc"

  tags = {
    Name    = "${local.name}-orphan-eip-b"
    Purpose = "Deliberately unassociated to exercise COST-006"
  }
}

# COST-009: a previous-generation EC2 instance.
# t2.micro sitting alongside the correct t3.micro. Same workload profile
# (or close enough), strictly slower baseline, essentially the same price.
# The finding says "consider t3"; the remediation is one word.
resource "aws_instance" "previous_gen" {
  count = var.create_insecure_examples ? 1 : 0

  ami                    = data.aws_ami.al2023.id
  instance_type          = "t2.micro"
  subnet_id              = aws_subnet.private.id
  vpc_security_group_ids = [aws_security_group.app.id]

  root_block_device {
    volume_type = "gp2" # deliberately, see COST-010 below
    volume_size = 8
    encrypted   = true

    tags = {
      Name = "${local.name}-previous-gen-root"
    }
  }

  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  tags = {
    Name    = "${local.name}-previous-gen"
    Purpose = "Deliberately previous-generation to exercise COST-009 and (root) COST-010"
  }
}

# COST-011: a Classic Load Balancer.
# ~$16.20/month ($0.025/hour) plus $0.008/GB processed. Deprecated for
# almost every use case in favour of ALB and NLB. The most common reason
# it still exists in an account is a stack that was created before ALB
# launched in 2016 and has never been rebuilt.
resource "aws_elb" "classic" {
  count = var.create_insecure_examples ? 1 : 0

  name    = "${local.name}-classic"
  subnets = [aws_subnet.public.id]

  security_groups = [aws_security_group.app.id]

  listener {
    instance_port     = 80
    instance_protocol = "http"
    lb_port           = 80
    lb_protocol       = "http"
  }

  health_check {
    healthy_threshold   = 2
    unhealthy_threshold = 2
    timeout             = 3
    target              = "TCP:80"
    interval            = 30
  }

  tags = {
    Name    = "${local.name}-classic-elb"
    Purpose = "Deliberately Classic (v1) to exercise COST-011"
  }
}

# COST-013: two log groups with retention unset.
# retention_in_days is deliberately omitted, so the log group defaults to
# "Never Expire". Two of them to demonstrate the check fires per resource.
resource "aws_cloudwatch_log_group" "unbounded_a" {
  count = var.create_insecure_examples ? 1 : 0

  name = "/aws/cbc-day09/unbounded-a"
  # retention_in_days deliberately omitted

  tags = {
    Name    = "${local.name}-unbounded-a"
    Purpose = "Deliberately unbounded retention to exercise COST-013"
  }
}

resource "aws_cloudwatch_log_group" "unbounded_b" {
  count = var.create_insecure_examples ? 1 : 0

  name = "/aws/cbc-day09/unbounded-b"
  # retention_in_days deliberately omitted

  tags = {
    Name    = "${local.name}-unbounded-b"
    Purpose = "Deliberately unbounded retention to exercise COST-013"
  }
}
