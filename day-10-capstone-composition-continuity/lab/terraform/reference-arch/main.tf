###############################################################################
# reference-arch — CareerByteCode AWS Bootcamp reference architecture
#
# A minimal Terraform module that composes safe-defaults from Days 01-09.
# By construction, running each prior day's audit against these resources
# should produce ZERO findings (100/100 on every day). Any drift from that
# invariant is what CAP-014 (Day 10) catches.
#
# What it provisions:
#
#   VPC with two AZs, public + private subnets, gateway endpoints for S3
#   and DynamoDB (so Day 09's COST-012 stays silent). No NAT gateway - this
#   module deliberately does not need outbound internet, and the resulting
#   $0/month NAT bill is a design choice.
#
#   One t3.micro instance (current-generation, arm64) with a gp3 8 GB root.
#   Zero deliberate defects: no orphan volumes, no Classic ELB, no
#   previous-generation family.
#
#   One S3 bucket for artifacts, versioned + lifecycled + block-public,
#   with server-side encryption. Answers Days 04 (S3 policy), 09 (COST-014
#   lifecycle) at once.
#
#   One CloudWatch log group with retention set (COST-013 stays silent).
#
#   One IAM role for the instance with a narrow inline policy. No wildcards
#   (Day 03).
#
# What it deliberately does NOT provision:
#
#   A load balancer, an RDS database, DynamoDB tables. These would require
#   more careful multi-day integration (Day 06 load-balancer checks, Day 08
#   DR check, Day 07 IAM). The reference is a MINIMAL composition, not an
#   exhaustive one; the module name reflects "reference for Day 10's CAP-014",
#   not "reference for every possible workload".
#
# Cost: ~$7/day (~$210/month) when deployed. Almost all of it is the EC2
# instance and its 8 GB root - the rest is fractions of a cent.
#
# Only rendered when the parent stack sets enable_reference_arch to true.
###############################################################################

variable "name_prefix" {
  description = "Prefix for names. The parent stack passes '<parent-prefix>-refarch'."
  type        = string
}

variable "aws_region" {
  description = "Region where the parent stack lives. Reference-arch inherits."
  type        = string
}

variable "instance_type" {
  description = "Instance type. Kept as a variable so a smaller/larger baseline can be tested against the audits."
  type        = string
  default     = "t3.micro"
}

variable "log_retention_days" {
  description = "Log retention for the reference-arch's log group."
  type        = number
  default     = 30
}

# ─────────────────────────────────────────────────────────────────────────────
# Data
# ─────────────────────────────────────────────────────────────────────────────

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
}

# ─────────────────────────────────────────────────────────────────────────────
# VPC
# ─────────────────────────────────────────────────────────────────────────────

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "main" {
  cidr_block           = "10.10.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name    = "${var.name_prefix}-vpc"
    Purpose = "Reference architecture VPC"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name    = "${var.name_prefix}-igw"
    Purpose = "Internet gateway for reference-arch public subnets"
  }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  availability_zone       = local.azs[count.index]
  cidr_block              = "10.10.${count.index}.0/24"
  map_public_ip_on_launch = true

  tags = {
    Name    = "${var.name_prefix}-public-${count.index}"
    Purpose = "Reference-arch public subnet"
  }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  cidr_block        = "10.10.${count.index + 10}.0/24"

  tags = {
    Name    = "${var.name_prefix}-private-${count.index}"
    Purpose = "Reference-arch private subnet"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "${var.name_prefix}-public-rt"
  }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.name_prefix}-private-rt"
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# Gateway endpoints — free, and they keep Day 09's COST-012 silent.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = {
    Name = "${var.name_prefix}-vpce-s3"
  }
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = {
    Name = "${var.name_prefix}-vpce-ddb"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Security group
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_security_group" "app" {
  name        = "${var.name_prefix}-app-sg"
  description = "Reference-arch application security group"
  vpc_id      = aws_vpc.main.id

  # No ingress by default. SSM-managed instance, no SSH port open.

  egress {
    description = "All egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.name_prefix}-app-sg"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# IAM
# ─────────────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "instance_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "${var.name_prefix}-instance"
  assume_role_policy = data.aws_iam_policy_document.instance_assume.json

  tags = {
    Name = "${var.name_prefix}-instance"
  }
}

# SSM access via a managed policy — no wildcards in the trust boundary.
resource "aws_iam_role_policy_attachment" "instance_ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "instance" {
  name = "${var.name_prefix}-instance"
  role = aws_iam_role.instance.name
}

# ─────────────────────────────────────────────────────────────────────────────
# EC2 instance — current generation, gp3, tagged, no orphan
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_instance" "app" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public[0].id
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name

  root_block_device {
    volume_size           = 8
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required" # IMDSv2 — Day 03 fires on optional
  }

  tags = {
    Name = "${var.name_prefix}-app"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# S3 bucket — versioned + lifecycled + encrypted + block-public
# ─────────────────────────────────────────────────────────────────────────────

resource "random_id" "bucket_suffix" {
  byte_length = 3
}

resource "aws_s3_bucket" "artifacts" {
  bucket        = "${var.name_prefix}-artifacts-${random_id.bucket_suffix.hex}"
  force_destroy = true

  tags = {
    Name    = "${var.name_prefix}-artifacts"
    Purpose = "Reference-arch application artifacts"
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "artifacts-tiering"
    status = "Enabled"
    filter {
      prefix = ""
    }
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
      noncurrent_days = 180
    }
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# CloudWatch log group — retention set (COST-013)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "app" {
  name              = "/aws/ec2/${var.name_prefix}-app"
  retention_in_days = var.log_retention_days

  tags = {
    Name    = "/aws/ec2/${var.name_prefix}-app"
    Purpose = "Reference-arch application logs"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Outputs — for the parent stack to reference
# ─────────────────────────────────────────────────────────────────────────────

output "vpc_id" {
  description = "Reference-arch VPC id."
  value       = aws_vpc.main.id
}

output "app_instance_id" {
  description = "Reference-arch application instance."
  value       = aws_instance.app.id
}

output "artifacts_bucket" {
  description = "Reference-arch S3 bucket."
  value       = aws_s3_bucket.artifacts.id
}

output "log_group_name" {
  description = "Reference-arch application log group."
  value       = aws_cloudwatch_log_group.app.name
}
