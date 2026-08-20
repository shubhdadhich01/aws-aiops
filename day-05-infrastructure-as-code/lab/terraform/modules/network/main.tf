###############################################################################
# modules/network/main.tf — VPC, subnets, routing, security group
#
# Nothing in this file names a region, a profile or an environment. Everything
# variable comes in as a variable. That is what makes the SAME module able to
# build dev and prod without a copy-paste fork, which is the entire premise of
# the day.
#
# WHY for_each AND NOT count, EVERYWHERE
#
# The obvious way to build three subnets is `count = 3` over a list. Do that
# and Terraform addresses them as aws_subnet.public[0], [1], [2] — by
# POSITION. Delete the middle CIDR from the list and Terraform sees: [1]
# changed from 10.0.2.0/24 to 10.0.3.0/24, and [2] no longer exists. It will
# destroy and recreate a subnet that you did not touch, along with every
# instance in it.
#
# for_each addresses by KEY: aws_subnet.public["us-east-1b"]. Delete that key
# and exactly one subnet is destroyed. Add a key and exactly one is created.
# Nothing else in the plan moves.
#
# Rule of thumb that has never failed me: `count` is correct only for
# on/off — `count = var.enabled ? 1 : 0`. The moment the number can be
# greater than one, you want for_each. IAC-016 flags count over a
# multi-element collection for exactly this reason.
###############################################################################

###############################################################################
# 1. VPC
###############################################################################

resource "aws_vpc" "this" {
  cidr_block = var.vpc_cidr

  # Both are required for private DNS names on instances and for VPC endpoints
  # to resolve. Free. Off by default on a VPC you create through the API,
  # which surprises people who only ever used the console wizard.
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${var.name_prefix}-vpc"
  }
}

###############################################################################
# 2. Internet gateway
#
# Free to have. It is the route table entry pointing at it that makes a subnet
# public, not the gateway itself — a common interview trip-up.
###############################################################################

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-igw"
  }
}

###############################################################################
# 3. Subnets
#
# Keyed by availability zone name. `each.key` is the AZ, `each.value` is the
# CIDR. Readable in the plan output, stable across edits.
###############################################################################

resource "aws_subnet" "public" {
  for_each = var.public_subnets

  vpc_id            = aws_vpc.this.id
  cidr_block        = each.value
  availability_zone = each.key

  # Instances launched here get a public IP automatically. Deliberate for a
  # public tier; a genuine mistake anywhere else, and the reason so many
  # "private" databases turn out to be reachable.
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.name_prefix}-public-${each.key}"
    Tier = "public"
  }
}

resource "aws_subnet" "private" {
  for_each = var.private_subnets

  vpc_id            = aws_vpc.this.id
  cidr_block        = each.value
  availability_zone = each.key

  map_public_ip_on_launch = false

  tags = {
    Name = "${var.name_prefix}-private-${each.key}"
    Tier = "private"
  }
}

###############################################################################
# 4. Public routing
#
# One route table, shared by every public subnet. There is no benefit to one
# table per public subnet when the route is identical, and there is a real
# cost: three tables to keep in sync by hand the day the route changes.
###############################################################################

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-rt-public"
  }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

###############################################################################
# 5. NAT gateway — the expensive optional bit
#
# ONE NAT gateway, in the first public subnet by sorted AZ name, shared by
# every private subnet. That is a deliberate trade:
#
#   * One NAT   = ~$32/month, and an AZ failure takes egress down for private
#                 subnets in the OTHER AZs, because their route points here.
#   * One per AZ = ~$32/month EACH, and AZ-independent egress.
#
# Production with a real availability target wants one per AZ. A lab wants
# zero. Know which you are building and price it before you build it, not
# after the bill arrives.
#
# `count` is correct here — this is a genuine on/off, not a collection.
###############################################################################

locals {
  # Sorted so the choice of subnet is deterministic across plans. Relying on
  # map ordering without sorting is how a plan starts proposing to move a NAT
  # gateway for no reason.
  first_public_az        = length(var.public_subnets) > 0 ? sort(keys(var.public_subnets))[0] : null
  nat_gateway_enabled    = var.enable_nat_gateway && length(var.private_subnets) > 0
  private_route_required = local.nat_gateway_enabled
}

resource "aws_eip" "nat" {
  count = local.nat_gateway_enabled ? 1 : 0

  domain = "vpc"

  tags = {
    Name = "${var.name_prefix}-eip-nat"
  }

  # An EIP allocated but not attached to anything is billed at $0.005/hour
  # (~$3.60/month) for doing nothing at all. depends_on keeps allocation and
  # attachment in the same apply so that window stays measured in seconds.
  depends_on = [aws_internet_gateway.this]
}

resource "aws_nat_gateway" "this" {
  count = local.nat_gateway_enabled ? 1 : 0

  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public[local.first_public_az].id

  tags = {
    Name = "${var.name_prefix}-nat"
  }

  depends_on = [aws_internet_gateway.this]
}

###############################################################################
# 6. Private routing
#
# One route table per private subnet — here the tables are NOT identical in
# principle (a per-AZ NAT design gives each a different target), so keeping
# them separate from day one avoids a painful refactor later.
###############################################################################

resource "aws_route_table" "private" {
  for_each = var.private_subnets

  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-rt-private-${each.key}"
  }
}

resource "aws_route" "private_nat" {
  # Only exists when there is a NAT gateway to point at. Without it the
  # private subnets are genuinely private: no egress, no cost, and everything
  # inside the VPC still works.
  for_each = local.private_route_required ? var.private_subnets : {}

  route_table_id         = aws_route_table.private[each.key].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this[0].id
}

resource "aws_route_table_association" "private" {
  for_each = aws_subnet.private

  subnet_id      = each.value.id
  route_table_id = aws_route_table.private[each.key].id
}

###############################################################################
# 7. Application security group
#
# Ingress is scoped to the VPC CIDR. There is no 0.0.0.0/0 ingress anywhere in
# this module and there never will be — IAC-009 checks for it and stays clean
# against this stack on purpose.
#
# Egress to 0.0.0.0/0 IS present and is normal. Locking down egress is a real
# control in high-security environments, but a check that flags every default
# egress rule in every security group in the account fires so often that
# people stop reading the output. IAC-009 therefore only looks at ingress.
###############################################################################

resource "aws_security_group" "app" {
  name        = "${var.name_prefix}-sg-app"
  description = "Application tier: inbound from inside the VPC only"
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-sg-app"
  }

  lifecycle {
    # Security groups cannot be deleted while an ENI still references them.
    # create_before_destroy makes replacement work: build the new group,
    # move the references, then remove the old one. Without it, any change
    # that forces replacement deadlocks against its own dependents.
    create_before_destroy = true
  }
}

# Separate rule resources rather than inline ingress/egress blocks. Inline
# blocks make the security group the sole owner of its rules, so anything
# added out of band gets reverted on the next apply — which sounds good until
# you are trying to add one rule from a second module and cannot.
resource "aws_vpc_security_group_ingress_rule" "app_vpc" {
  security_group_id = aws_security_group.app.id
  description       = "Application port, from inside the VPC only"

  cidr_ipv4   = aws_vpc.this.cidr_block
  from_port   = var.app_ingress_port
  to_port     = var.app_ingress_port
  ip_protocol = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "app_all" {
  security_group_id = aws_security_group.app.id
  description       = "All outbound"

  cidr_ipv4   = "0.0.0.0/0"
  ip_protocol = "-1"
}

###############################################################################
# 8. Flow logs — optional, priced, off by default
###############################################################################

resource "aws_cloudwatch_log_group" "flow" {
  count = var.enable_flow_logs ? 1 : 0

  name              = "/aws/vpc/${var.name_prefix}-flow"
  retention_in_days = var.flow_log_retention_days
}

data "aws_iam_policy_document" "flow_assume" {
  count = var.enable_flow_logs ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "flow_write" {
  count = var.enable_flow_logs ? 1 : 0

  statement {
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
    ]

    # Scoped to this log group and its streams. Not "*". A flow log role with
    # logs:* on Resource "*" can read every log group in the account,
    # including the ones holding application errors with tokens in them.
    resources = [
      aws_cloudwatch_log_group.flow[0].arn,
      "${aws_cloudwatch_log_group.flow[0].arn}:*",
    ]
  }
}

resource "aws_iam_role" "flow" {
  count = var.enable_flow_logs ? 1 : 0

  name               = "${var.name_prefix}-flow-logs"
  assume_role_policy = data.aws_iam_policy_document.flow_assume[0].json
}

resource "aws_iam_role_policy" "flow" {
  count = var.enable_flow_logs ? 1 : 0

  name   = "${var.name_prefix}-flow-logs-write"
  role   = aws_iam_role.flow[0].id
  policy = data.aws_iam_policy_document.flow_write[0].json
}

resource "aws_flow_log" "this" {
  count = var.enable_flow_logs ? 1 : 0

  vpc_id                   = aws_vpc.this.id
  traffic_type             = "ALL"
  log_destination_type     = "cloud-watch-logs"
  log_destination          = aws_cloudwatch_log_group.flow[0].arn
  iam_role_arn             = aws_iam_role.flow[0].arn
  max_aggregation_interval = 600

  tags = {
    Name = "${var.name_prefix}-flow-log"
  }
}
