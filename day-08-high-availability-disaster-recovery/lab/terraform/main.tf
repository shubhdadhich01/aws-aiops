###############################################################################
# Day 08 — High Availability & Disaster Recovery
# main.tf — the multi-AZ foundation
#
# This file teaches as it goes. Read it top to bottom before you apply; the
# comments are the lesson and the resources are the exercise.
#
# WHAT GETS BUILT (CP1 — the thing that can be broken)
#
#   ┌─ us-east-1 ──────────────────────────────┐   ┌─ us-west-2 ────────────┐
#   │  VPC, 2 AZs, public + private subnets    │   │                        │
#   │    ALB ──> target group ──> ASG          │   │                        │
#   │                     │                    │   │                        │
#   │    DynamoDB (PITR) ─┼── global table ────┼──>│  DynamoDB replica      │
#   │    S3 (versioned) ──┼── CRR ─────────────┼──>│  S3 replica (versioned)│
#   │    RDS (optional)   │                    │   │                        │
#   │    Route 53 health check on the ALB      │   │                        │
#   │    Chaos Lambda + deny-all NACL          │   │                        │
#   └──────────────────────────────────────────┘   └────────────────────────┘
#
#   Plus, when create_insecure_examples = true, the same ideas built wrong,
#   for dr_audit.py to tear apart.
#
# THE ARGUMENT THIS DAY MAKES
#
#   An untested recovery path is a hypothesis, and RTO is a claim about a
#   procedure nobody has run.
#
#   Building multi-AZ is easy. It appears on every architecture diagram ever
#   drawn, and most of them are honest about the boxes and silent about the
#   arrows. The engineering problem is that the failover path is the only code
#   in the system that runs exclusively during your worst hour — which makes
#   it the least exercised code you own and the most confidently described.
#
#   So the resources below are chosen for one property above all others: THEY
#   CAN BE BROKEN AND RECOVERED WHILE YOU WATCH, cheaply, on a laptop. The
#   chaos Lambda in section 10 is not a novelty. It is the only part of this
#   file that turns the rest of it from a diagram into a measurement.
#
#   Read the health check comments in sections 3 and 4 before section 10.
#   Almost every surprising result in the lab traces back to one of the three
#   health checks doing exactly what it says and not what was assumed.
#
# COST: this is the first day in the repo where the correct architecture is
# genuinely expensive, and where "do not do this" is sometimes the right
# answer. See outputs.tf `cost_breakdown` and read it BEFORE you set
# nat_gateway_strategy = "per_az" or rds_multi_az = true.
###############################################################################


###############################################################################
# 1. THE NETWORK — where the failure domain actually lives
#
# An AZ is a FAILURE DOMAIN. A region is a BLAST RADIUS. These are different
# ideas and they need different answers.
#
#   FAILURE DOMAIN   The unit that fails together. One or more discrete data
#                    centres with independent power, cooling and networking,
#                    a couple of kilometres apart, joined by dedicated fibre
#                    with sub-millisecond latency. Losing one is a capacity
#                    event: you planned for it, the remaining AZs absorb the
#                    load, nothing about your architecture changes. AZs are
#                    close enough that SYNCHRONOUS replication is practical,
#                    which is why an RDS Multi-AZ standby can have an RPO of
#                    zero and a cross-region replica cannot.
#
#   BLAST RADIUS     The unit that fails together WHEN THE FAILURE IS NOT
#                    PHYSICAL. Regions share almost no infrastructure, but
#                    they do share control planes, IAM, deployment pipelines,
#                    configuration and people. The large outages of the last
#                    decade were mostly not floods. They were a bad config
#                    push, a capacity cascade, a dependency on a service in
#                    us-east-1 that nobody had documented. Multi-region
#                    protects you from those in proportion to how INDEPENDENT
#                    your two regions really are — and a DR region deployed
#                    by the same pipeline, five minutes later, is not very
#                    independent at all.
#
# The practical consequence: multi-AZ is the answer to almost every real
# availability requirement, it is cheap, and it is mostly automatic. Multi-
# region is the answer to a much narrower set of requirements, it is
# expensive in ways that are not on the bill, and it is mostly manual. This
# day builds both so you can see the difference in effort, not just in price.
###############################################################################

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = local.vpc_name
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${local.prefix}-igw-${local.suffix}"
  }
}

# Public subnets — one per AZ. The ALB lives here.
#
# An ALB requires subnets in AT LEAST TWO AZs. That is not a best practice
# note, it is an API constraint, and it is one of the very few places AWS
# forces multi-AZ on you rather than suggesting it. Note what it means for
# the chaos exercise: you cannot simulate an AZ failure by removing a subnet
# from the load balancer, because the API will not let you drop below two.
resource "aws_subnet" "public" {
  count = local.az_count

  vpc_id                  = aws_vpc.main.id
  cidr_block              = local.public_cidrs[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name = "${local.prefix}-public-${local.azs[count.index]}-${local.suffix}"
    Tier = "public"
  }
}

# Private subnets — one per AZ. The application instances live here.
resource "aws_subnet" "private" {
  count = local.az_count

  vpc_id            = aws_vpc.main.id
  cidr_block        = local.private_cidrs[count.index]
  availability_zone = local.azs[count.index]

  tags = {
    Name = "${local.prefix}-private-${local.azs[count.index]}-${local.suffix}"
    Tier = "private"
  }
}

# ============================ THE NAT GATEWAY TRAP ===========================
#
# This is the most common real high-availability defect in production AWS
# accounts, and the reason is that it is created by a COST OPTIMISATION rather
# than by carelessness.
#
# A NAT gateway is a zonal resource. It lives in one subnet, in one AZ. It
# costs ~$32.85/month plus ~$0.045/GB processed. The correct architecture is
# one per AZ, with each private subnet routing to the gateway in its own zone
# — which for two AZs is ~$65.70/month for a lab that serves no traffic.
#
# So somebody, entirely reasonably, deletes one of them and points both
# private route tables at the survivor. The bill halves. Every test passes.
# The architecture diagram is unchanged. And the stack now has a single-AZ
# dependency inside something everybody calls multi-AZ.
#
# The failure mode is worth picturing precisely, because it is not an outage
# and that is what makes it dangerous. AZ-a goes away. Instances in AZ-b keep
# running. They pass EC2 status checks. They pass ALB health checks, because
# the health check is an HTTP GET from the load balancer inside the VPC and
# does not traverse NAT. The dashboard is green. And every outbound call —
# to a payment provider, to an OAuth endpoint, to your own S3 bucket if you
# have no gateway endpoint, to the package repository during a deploy — fails
# with a timeout, because the route to the internet went away with AZ-a.
#
# You get an incident that reads as "third-party API is down" for the first
# twenty minutes.
#
# Defaulting to "single" here is deliberate. It means dr_audit.py's DR-002
# fires against YOUR OWN STACK rather than against a strawman, and lab step 9
# is: flip it to per_az, apply, re-run the audit, watch the finding go, look
# at the price. That round trip is the lesson. Both answers are defensible;
# only one of them is defensible SILENTLY.
#
# (The genuinely cheap correct answer, for what it is worth, is often neither:
# VPC gateway endpoints for S3 and DynamoDB are FREE and remove the largest
# source of NAT traffic in most stacks. Interface endpoints are ~$7.30/month
# per endpoint per AZ. Once you need three or four of them, per-AZ NAT is
# cheaper. Under that, endpoints win on both cost and availability.)
# =============================================================================

resource "aws_eip" "nat" {
  count = local.nat_gateway_count

  domain = "vpc"

  tags = {
    Name = "${local.prefix}-nat-eip-${count.index}-${local.suffix}"
  }

  depends_on = [aws_internet_gateway.main]
}

resource "aws_nat_gateway" "main" {
  count = local.nat_gateway_count

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = {
    Name = "${local.prefix}-nat-${local.azs[count.index]}-${local.suffix}"
    # The AZ is in the tag on purpose. When you are trying to work out at 03:00
    # why one AZ has no internet, "which AZ is this gateway in" should not
    # require three console clicks.
    AvailabilityZone = local.azs[count.index]
  }

  depends_on = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "${local.prefix}-public-rt-${local.suffix}"
  }
}

resource "aws_route_table_association" "public" {
  count = local.az_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# One private route table PER AZ, even when there is only one NAT gateway.
#
# Sharing a single private route table across AZs works and is smaller to
# write. It also makes the single-NAT defect invisible: with one route table
# there is nothing in the plan that distinguishes "all AZs share a gateway"
# from "each AZ has its own". Keeping the tables separate means the route
# target is stated once per AZ, and `terraform plan` shows you the same NAT
# gateway id appearing twice. Make the defect legible.
resource "aws_route_table" "private" {
  count = local.az_count

  vpc_id = aws_vpc.main.id

  # No route at all when nat_gateway_strategy = "none". The subnets are then
  # genuinely private: VPC-local traffic works, the ALB can still health-check
  # the instances, and nothing reaches the internet. For this lab that is a
  # supported configuration, which is why the user-data installs nothing.
  dynamic "route" {
    for_each = local.nat_gateway_count > 0 ? [1] : []

    content {
      cidr_block = "0.0.0.0/0"
      # THE LINE THAT ENCODES THE TRADE-OFF. With "single", every AZ's route
      # table points at index 0 — one gateway, one AZ, one dependency. With
      # "per_az", each points at its own.
      nat_gateway_id = aws_nat_gateway.main[local.nat_gateway_count == 1 ? 0 : count.index].id
    }
  }

  tags = {
    Name = "${local.prefix}-private-rt-${local.azs[count.index]}-${local.suffix}"
  }
}

resource "aws_route_table_association" "private" {
  count = local.az_count

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# The S3 gateway endpoint. FREE, and it is the single best availability-per-
# dollar decision in this file.
#
# Without it, every S3 call from a private subnet goes out through the NAT
# gateway: billed per GB, and dependent on the AZ that gateway lives in. With
# it, S3 traffic is routed inside the VPC. Costs nothing, removes the largest
# NAT bill in most stacks, and removes an AZ dependency from your data path.
#
# It is a gateway endpoint, which means it works by adding routes to route
# tables — so it must be associated with each one, and an endpoint attached to
# the wrong route table is a silent no-op that shows up only on the bill.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${local.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = concat([aws_route_table.public.id], aws_route_table.private[*].id)

  tags = {
    Name = "${local.prefix}-s3-endpoint-${local.suffix}"
  }
}

# The deny-all network ACL used by the chaos Lambda's isolate_az mode.
#
# It is created empty of allow rules and associated with NOTHING. A NACL with
# no rules denies everything, because the implicit final rule of every NACL is
# a deny. It sits here inert until something associates it with a subnet.
#
# NACLs rather than security groups for this, for a reason worth knowing:
# security groups are STATEFUL and instance-attached, so isolating with one
# means touching every instance and the return traffic of established
# connections still flows. NACLs are STATELESS and subnet-attached, so a
# single association call takes out an entire subnet, both directions,
# immediately. That is what makes it a passable AZ analogue and it is also why
# NACLs are a genuinely dangerous tool in an automated response — Day 07's
# argument about irreversible actions applies directly.
resource "aws_network_acl" "chaos" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name    = "${local.prefix}-chaos-deny-all-${local.suffix}"
    Purpose = "chaos-engineering-do-not-attach-by-hand"
  }
}


###############################################################################
# 2. SECURITY GROUPS
#
# Nothing surprising here — Day 02 covered the design. The DR-relevant note is
# the egress rule on the app tier: it is wide open outbound, which is what
# makes the NAT gateway dependency in section 1 real. A stack with no outbound
# access has no NAT problem, and also no ability to call anything.
###############################################################################

resource "aws_security_group" "alb" {
  name        = "${local.prefix}-alb-sg-${local.suffix}"
  description = "Day 08 ALB: HTTP from the internet, forward to the app tier"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${local.prefix}-alb-sg-${local.suffix}"
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from anywhere. Plain HTTP because this lab has no certificate; see the README on why that is a teaching compromise and not a pattern."
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "alb_all" {
  security_group_id = aws_security_group.alb.id
  description       = "ALB to targets and health checks"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_security_group" "app" {
  name        = "${local.prefix}-app-sg-${local.suffix}"
  description = "Day 08 app tier: HTTP from the ALB only"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${local.prefix}-app-sg-${local.suffix}"
  }
}

resource "aws_vpc_security_group_ingress_rule" "app_from_alb" {
  security_group_id            = aws_security_group.app.id
  description                  = "HTTP from the ALB security group. Source is the GROUP, not a CIDR — the ALB's addresses change, and a CIDR here is a rule that will be wrong eventually."
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 80
  to_port                      = 80
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "app_all" {
  security_group_id = aws_security_group.app.id
  description       = "Outbound to anywhere. This is the rule that makes the NAT gateway an availability dependency."
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_security_group" "rds" {
  count = var.create_rds ? 1 : 0

  name        = "${local.prefix}-rds-sg-${local.suffix}"
  description = "Day 08 RDS: PostgreSQL from the app tier only"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${local.prefix}-rds-sg-${local.suffix}"
  }
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_app" {
  count = var.create_rds ? 1 : 0

  security_group_id            = aws_security_group.rds[0].id
  description                  = "PostgreSQL from the app tier"
  referenced_security_group_id = aws_security_group.app.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}


###############################################################################
# 3. THE LOAD BALANCER — and the first of the three health checks
#
# ======================= THREE HEALTH CHECKS, THREE JOBS =====================
#
# This is the section people skim and then spend an afternoon debugging, so it
# is stated plainly. There are three independent health checks in this stack.
# They ask different questions, they fail independently, and none of them is a
# substitute for another.
#
#   1. TARGET GROUP HEALTH CHECK  (configured below)
#      Question: should the load balancer send the next request to THIS target?
#      Mechanism: HTTP GET from the load balancer's own ENIs, inside the VPC,
#                 to the target's private address.
#      On failure: the target is deregistered from rotation. Nothing is
#                 terminated. Nothing alarms unless you built an alarm.
#      Blind to:  anything the ALB cannot reach — which includes the entire
#                 internet-facing question. It never leaves your VPC.
#
#   2. EC2 STATUS CHECK  (automatic, section 4)
#      Question: is the hypervisor alive, and is the instance's OS reachable
#                on the network?
#      On failure: the ASG replaces the instance, IF health_check_type is
#                 either EC2 or ELB — this one is always honoured.
#      Blind to:  literally everything about your application. A process that
#                 has deadlocked, a container that exited, an application
#                 returning 500 to every request: all healthy. Forever.
#
#   3. ROUTE 53 HEALTH CHECK  (section 8)
#      Question: can the outside world reach this endpoint?
#      Mechanism: HTTP from 15+ checker locations around the world, from
#                 outside your VPC, against the public endpoint.
#      On failure: DNS stops returning this record, if it participates in a
#                 failover or weighted policy.
#      Blind to:  which target is broken. It only sees the aggregate.
#      Costs:     ~$0.50/month. The only one of the three that is billed, and
#                 the only one that answers the question your users are asking.
#
# The classic confusion is between 1 and 2, and it produces a specific,
# long-lived, silent defect. See section 4.
# =============================================================================

resource "aws_lb" "main" {
  name               = local.alb_name_safe
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  # An ALB is a regional, multi-AZ service by construction: AWS runs nodes in
  # each subnet you give it and puts them all behind one DNS name. You do not
  # configure its availability; you configure how many AZs it spans, and the
  # minimum is two.
  #
  # Note what is NOT here. Cross-zone load balancing is ALWAYS ON for an ALB
  # and cannot be turned off — it is an NLB and GWLB setting, where it defaults
  # to OFF and where leaving it off means each AZ's nodes only serve targets in
  # their own AZ. That difference bites during a partial failure: an NLB
  # without cross-zone balancing sends a fixed share of traffic to a zone whose
  # targets are all unhealthy. If you carry this pattern to an NLB, that is the
  # line to add.

  # FALSE here, TRUE in production, and the difference is a real DR trade-off
  # rather than a formality. Deletion protection stops `terraform destroy`,
  # which is exactly what you want on a production load balancer and exactly
  # what makes a teardown checklist necessary. The teardown checklist for this
  # day names it.
  enable_deletion_protection = false

  # Idle timeout is part of your failover behaviour and almost nobody tunes it.
  # A connection that is idle for longer than this is closed by the ALB. During
  # a failover, long-lived connections that are still open to a target you are
  # trying to drain are precisely what stops the drain from completing.
  idle_timeout = 60

  tags = {
    Name = local.alb_name_safe
  }
}

resource "aws_lb_target_group" "app" {
  name     = substr("${local.prefix}-tg-${local.suffix}", 0, 32)
  port     = 80
  protocol = "HTTP"
  vpc_id   = aws_vpc.main.id

  # Deregistration delay — the connection draining window. THIS IS PART OF
  # YOUR RTO and it is a number most people leave at the AWS default of 300
  # seconds without noticing.
  #
  # When a target is removed from rotation, the ALB stops sending it NEW
  # requests and waits this long for in-flight ones to finish. During a
  # deliberate failover, five minutes of draining is five minutes of your
  # recovery budget. During an involuntary failure it does not apply at all,
  # because the target is already gone.
  #
  # 30 seconds here: long enough for this application's requests, short enough
  # that the lab's stopwatch measures recovery rather than politeness. Set it
  # to your 99th percentile request duration plus a margin. Not to 300 because
  # that is what was in the box.
  deregistration_delay = 30

  health_check {
    enabled             = true
    path                = var.target_group_health_check.path
    protocol            = "HTTP"
    matcher             = var.target_group_health_check.matcher
    interval            = var.target_group_health_check.interval
    timeout             = var.target_group_health_check.timeout
    healthy_threshold   = var.target_group_health_check.healthy_threshold
    unhealthy_threshold = var.target_group_health_check.unhealthy_threshold
  }

  # A health check endpoint should check something. `/health` here returns 200
  # unconditionally, which is honest for a lab that serves static text and is
  # the WRONG pattern for anything real.
  #
  # The rule worth carrying away: a health check should verify the dependencies
  # THIS INSTANCE NEEDS TO SERVE A REQUEST, and nothing else. Check your
  # database connection, yes. Do not check a downstream service that is not on
  # the critical path — you have just given that service the ability to take
  # your entire fleet out of rotation simultaneously, which is a correlated
  # failure you invented yourself. That specific mistake has caused more than
  # one large public outage.
  #
  # And never make the health check expensive. It runs every `interval`
  # seconds against every target from every ALB node.

  tags = {
    Name = "${local.prefix}-tg-${local.suffix}"
  }

  # No create_before_destroy here, deliberately, and the reason is a trap worth
  # knowing: create_before_destroy on a resource with a FIXED name cannot work,
  # because the replacement is created while the original still holds the name.
  # You need name_prefix for that, and target group name_prefix is capped at
  # six characters. The launch template below does use name_prefix and does set
  # create_before_destroy, which is the combination that actually functions.
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}


###############################################################################
# 4. COMPUTE — the Auto Scaling group, and the health check line most stacks
#    omit
#
# ================= THE MOST EXPENSIVE MISSING LINE IN AWS ====================
#
#   health_check_type = "ELB"
#
# The default is "EC2". With the default, the ASG replaces an instance when
# EC2 says the instance is gone: the hypervisor failed, the status checks
# failed, someone terminated it. It knows nothing about your application.
#
# Now picture the failure that actually happens. Your application process
# deadlocks, or the JVM is in a permanent full GC, or the container exited and
# nothing restarted it, or a config push broke one instance's startup. The
# instance is running. The OS answers pings. EC2 status checks pass.
#
# What happens next, with health_check_type = "EC2":
#
#   - The target group health check fails. Correctly.
#   - The ALB deregisters that target. Correctly.
#   - Traffic goes to the healthy instances. THE SERVICE IS FINE.
#   - The ASG does nothing, because EC2 says the instance is alive.
#   - You now pay for an instance that serves zero requests, indefinitely.
#   - Your effective capacity is silently N-1.
#   - Nothing alarms, because nothing is down.
#
# This state survives for months. It is discovered during the next incident,
# when the "spare" capacity that was supposed to absorb an AZ failure turns
# out to have been dead since March.
#
# With health_check_type = "ELB", the ASG additionally honours the target
# group's verdict: the instance is marked unhealthy, terminated and replaced.
# The line costs nothing. Absence is check DR-003, and it is the single most
# common finding this auditor produces against real accounts.
#
# THE COROLLARY, which is why the grace period below matters so much: once the
# ASG honours the ALB's opinion, an application that is slow to start gets
# terminated for being slow to start, and its replacement gets terminated for
# the same reason, forever. Turning on health_check_type = "ELB" without
# setting an adequate health_check_grace_period converts a silent capacity
# leak into a loud boot loop. Both lines, or neither.
# =============================================================================

resource "aws_iam_role" "app" {
  name = "${local.prefix}-app-role-${local.suffix}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Read-only on the table, so the instance can prove the data tier is reachable
# from its own AZ. Deliberately narrow: Day 01's least-privilege argument does
# not get suspended because the day is about availability.
resource "aws_iam_role_policy" "app" {
  name = "${local.prefix}-app-policy-${local.suffix}"
  role = aws_iam_role.app.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DescribeTable"]
      Resource = aws_dynamodb_table.orders.arn
    }]
  })
}

resource "aws_iam_instance_profile" "app" {
  name = "${local.prefix}-app-profile-${local.suffix}"
  role = aws_iam_role.app.name
}

resource "aws_launch_template" "app" {
  name_prefix   = "${local.prefix}-lt-"
  image_id      = local.ami_id
  instance_type = var.instance_type

  iam_instance_profile {
    arn = aws_iam_instance_profile.app.arn
  }

  vpc_security_group_ids = [aws_security_group.app.id]

  # IMDSv2 required. Not a DR control, but Day 07's habit does not lapse
  # because the topic changed.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  monitoring {
    # Detailed monitoring: 1-minute metrics instead of 5-minute, ~$2.10 per
    # instance per month. It is on because a 5-minute metric cannot detect a
    # failure inside a 30-minute RTO with any margin — you would spend a sixth
    # of your recovery budget waiting for the first data point. If your RTO is
    # measured in hours, turn it off and save the money.
    enabled = true
  }

  block_device_mappings {
    device_name = "/dev/xvda"

    ebs {
      volume_size           = 8
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  # No package installation, on purpose. AL2023 ships python3, so this boots
  # with nat_gateway_strategy = "none" and with no internet access at all.
  # A user-data script that needs the internet is a recovery path that depends
  # on the internet, which is a dependency you will discover during the one
  # event where it is unavailable.
  #
  # Note: plain $VAR and $(...) below. Terraform interpolates ${...}, so shell
  # braces would be evaluated by Terraform and fail.
  user_data = base64encode(<<-USERDATA
    #!/bin/bash
    set -euo pipefail

    TOKEN=$(curl -sS -X PUT "http://169.254.169.254/latest/api/token" \
      -H "X-aws-ec2-metadata-token-ttl-seconds: 300")
    IID=$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" \
      http://169.254.169.254/latest/meta-data/instance-id)
    AZ=$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" \
      http://169.254.169.254/latest/meta-data/placement/availability-zone)

    mkdir -p /opt/cbc
    cat > /opt/cbc/server.py <<'PYEOF'
    import http.server
    import os
    import socketserver
    import time

    BOOT = time.time()
    IID = os.environ.get("CBC_IID", "unknown")
    AZ = os.environ.get("CBC_AZ", "unknown")


    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/health"):
                body = "ok {} {} uptime={:.0f}s\n".format(IID, AZ, time.time() - BOOT)
                code = 200
            else:
                body = (
                    "CareerByteCode Day 08\n"
                    "instance: {}\n"
                    "az:       {}\n"
                    "uptime:   {:.0f}s\n"
                    "\n"
                    "Refresh repeatedly and watch the AZ change. That is the load\n"
                    "balancer doing its job. Now break one and time the recovery.\n"
                ).format(IID, AZ, time.time() - BOOT)
                code = 200
            payload = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt, *args):
            # The ALB health-checks every target every `interval` seconds from
            # every ALB node. Logging each one buries anything interesting.
            if not self.path.startswith("/health"):
                super().log_message(fmt, *args)


    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("", 80), Handler) as httpd:
        httpd.serve_forever()
    PYEOF

    cat > /etc/systemd/system/cbc-app.service <<SVCEOF
    [Unit]
    Description=CareerByteCode Day 08 demo app
    After=network-online.target

    [Service]
    Environment=CBC_IID=$IID
    Environment=CBC_AZ=$AZ
    ExecStart=/usr/bin/python3 /opt/cbc/server.py
    Restart=always
    RestartSec=2

    [Install]
    WantedBy=multi-user.target
    SVCEOF

    systemctl daemon-reload
    systemctl enable --now cbc-app.service
  USERDATA
  )

  # Launch template tags do NOT reach the instances. Two separate tag
  # specifications are required, and this catches everyone once: default_tags
  # on the provider tags the TEMPLATE, not the instances the template launches.
  # An ASG full of untagged instances is an untraceable line on the bill.
  tag_specifications {
    resource_type = "instance"

    tags = {
      Name      = "${local.prefix}-app-${local.suffix}"
      Project   = "aws-aiops-bootcamp"
      Day       = "08"
      ManagedBy = "terraform"
      Owner     = var.owner
    }
  }

  tag_specifications {
    resource_type = "volume"

    tags = {
      Name      = "${local.prefix}-app-vol-${local.suffix}"
      Project   = "aws-aiops-bootcamp"
      Day       = "08"
      ManagedBy = "terraform"
      Owner     = var.owner
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_autoscaling_group" "app" {
  name                = local.asg_name
  vpc_zone_identifier = aws_subnet.private[*].id

  min_size         = var.asg_min_size
  max_size         = var.asg_max_size
  desired_capacity = var.asg_desired_capacity

  target_group_arns = [aws_lb_target_group.app.arn]

  # The two lines this whole section is about.
  health_check_type         = var.asg_health_check_type
  health_check_grace_period = var.asg_health_check_grace_period

  # AZ REBALANCING, which is automatic and is worth understanding anyway,
  # because it is the only failback in this stack that happens without you.
  #
  # When an AZ fails, the ASG launches replacements wherever it can — which is
  # the surviving zone. When the failed AZ returns, the ASG notices the
  # imbalance and rebalances by LAUNCHING FIRST AND TERMINATING SECOND, which
  # means it briefly runs above desired capacity. That is correct and it
  # surprises people who see instance count exceed desired and assume a bug.
  #
  # It also does not always finish. Rebalancing is best-effort and is
  # suppressed while a scaling activity or an instance refresh is in progress.
  # A fleet that ends the incident with every instance in one zone, and stays
  # that way, has recovered availability and quietly lost the redundancy it
  # recovered with. Check that after the exercise; nothing will tell you.

  # Wait for instances to actually be in service before Terraform declares
  # success. Without this, `apply` returns while the ASG is still launching,
  # and the first thing you do is health-check a target group that has no
  # healthy targets and conclude something is broken.
  wait_for_capacity_timeout = "10m"
  min_elb_capacity          = var.asg_min_size

  # Terminate the oldest instance first when scaling in. During a rolling
  # recovery this is what stops you keeping the instances that were up during
  # the incident and discarding the fresh ones.
  termination_policies = ["OldestInstance"]

  # Instance refresh: a rolling replacement triggered by a launch template
  # change. This is the mechanism by which a config fix reaches a fleet during
  # an incident, and it has an availability parameter of its own —
  # min_healthy_percentage 50 across two instances means one at a time.
  instance_refresh {
    strategy = "Rolling"

    preferences {
      min_healthy_percentage = 50
      instance_warmup        = var.asg_health_check_grace_period
    }
  }

  launch_template {
    id      = aws_launch_template.app.id
    version = "$Latest"
  }

  # ASG tags are their own resource type with their own propagation flag. They
  # do not come from default_tags either.
  tag {
    key                 = "Name"
    value               = local.asg_name
    propagate_at_launch = false
  }

  tag {
    key                 = "Project"
    value               = "aws-aiops-bootcamp"
    propagate_at_launch = true
  }

  tag {
    key                 = "Day"
    value               = "08"
    propagate_at_launch = true
  }

  tag {
    key                 = "ManagedBy"
    value               = "terraform"
    propagate_at_launch = true
  }

  tag {
    key                 = "Owner"
    value               = var.owner
    propagate_at_launch = true
  }

  # Same reasoning as the target group: a fixed `name` and
  # create_before_destroy are incompatible. If you need zero-downtime ASG
  # replacement, switch to name_prefix and reference
  # aws_autoscaling_group.app.name everywhere instead of local.asg_name.
}


###############################################################################
# 5. THE DATA TIER — DynamoDB, and where the RPO actually comes from
#
# Failover is a data problem, not a compute problem.
#
# Standing up compute in another AZ or another region takes minutes and is
# almost entirely automatic. Everything above this comment is the easy part.
# The RTO goes somewhere else: into reconciling state, into DNS caches, into
# connection pools, into deciding whether the writes that happened during the
# outage are recoverable, and into the meeting where somebody has to say out
# loud whether the data is trustworthy.
#
# WHY DYNAMODB IS THE PRIMARY STORE HERE. Three reasons, in order:
#
#   1. It is the only store in AWS where you can WATCH YOUR OWN RPO ON A
#      GRAPH. A global table publishes ReplicationLatency to CloudWatch. That
#      number, in seconds, is your worst-case data loss if the source region
#      disappears right now. Almost nothing else exposes this so directly, and
#      a day about RPO being a measurement should use the thing you can
#      measure.
#   2. Three genuinely distinct RPO postures, switchable in minutes: none
#      (RPO = last manual backup), PITR (RPO ~5 minutes), global table
#      (RPO = replication lag, typically sub-second).
#   3. On-demand billing means an idle lab table costs cents. RDS has a
#      $12/month floor before it has stored a single row.
#
# RDS is available behind create_rds because "Multi-AZ is not a read replica"
# is worth demonstrating on the real thing. See section 7.
###############################################################################

resource "aws_dynamodb_table" "orders" {
  name         = local.table_name
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  # Streams are REQUIRED for global tables. Not recommended — required, with
  # NEW_AND_OLD_IMAGES specifically. Enable them unconditionally so that
  # turning on enable_dynamodb_global_table later is a two-minute change
  # rather than a table replacement, because changing stream configuration on
  # a table that already has replicas is a conversation with support.
  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES"

  point_in_time_recovery {
    # ~$0.20/GB-month. Continuous backup, restore to any second in the last 35
    # days.
    #
    # THE PART THAT CATCHES PEOPLE: a PITR restore creates a NEW TABLE. It
    # cannot restore in place, and it cannot restore into an existing table.
    # So the recovery procedure is not "restore the table" — it is "restore to
    # a new table, then repoint every consumer at a different name, then deal
    # with whatever wrote to the old table in the meantime". That is
    # application work performed under pressure, and it is where the RTO of a
    # data restore actually goes. Time it in lab step 8 and see.
    enabled = var.enable_dynamodb_pitr
  }

  server_side_encryption {
    # AWS-owned key. Free. Sufficient here. A customer-managed key adds
    # revocation and an auditable Decrypt trail, and adds ~$1/month plus a
    # cross-region key story that is genuinely awkward — a DR replica cannot
    # decrypt with a key that does not exist in the DR region, which is a
    # recovery failure that only appears during recovery.
    enabled = true
  }

  # Global table replicas. Each replica block creates a full copy of the table
  # in another region, with bidirectional, multi-active, LAST-WRITER-WINS
  # replication.
  #
  # Read that last part again. There is no conflict resolution beyond a
  # timestamp. If both regions accept a write to the same item during a split
  # brain, one of them is silently discarded and the survivor is whichever
  # clock was ahead. Whether that is acceptable is a property of your
  # application's semantics, not of DynamoDB — and it is the reason "just go
  # active-active" is a six-month project rather than a checkbox.
  dynamic "replica" {
    for_each = var.enable_dynamodb_global_table ? [var.dr_region] : []

    content {
      region_name = replica.value
      # PITR is per replica and billed per replica. A replica without it is a
      # DR copy you cannot roll back — fine for regional failover, useless
      # against the corruption case.
      point_in_time_recovery = var.enable_dynamodb_pitr
    }
  }

  tags = {
    Name = local.table_name
    Role = "primary-data-store"
  }

  lifecycle {
    ignore_changes = [
      # Replica management outside Terraform (a failover promoting a replica,
      # for instance) should not produce a plan that tries to undo it. This is
      # a small example of a large DR truth: your IaC and your incident
      # response WILL disagree, and the question is only whether the
      # disagreement is planned for.
      replica,
    ]
  }
}


###############################################################################
# 6. S3 AND CROSS-REGION REPLICATION — asynchronous, which means it is an RPO
#
# CRR does not give you zero data loss. It gives you a number, and by default
# it does not even give you that, because without Replication Time Control
# there is no SLA and no metric. "Most objects replicate within seconds" is
# true and is not an RPO. An RPO is a number you can defend.
#
# Three properties worth internalising:
#
#   VERSIONING IS MANDATORY on both source and destination. This is an API
#   constraint. It also means deletes stop deleting: every overwrite retains
#   the old version, billed, in both regions, until a lifecycle rule removes
#   it. A replicated bucket with no noncurrent-version lifecycle rule is the
#   most reliable way to grow a storage bill in a region nobody looks at.
#
#   REPLICATION IS NOT RETROACTIVE. Turning it on replicates objects created
#   AFTER that moment. Everything already in the bucket stays where it is
#   until you run S3 Batch Replication explicitly. Teams discover this during
#   the failover, when the DR bucket turns out to contain three weeks of data
#   and the primary contains three years.
#
#   DELETES DO NOT REPLICATE BY DEFAULT. Delete markers are excluded unless
#   you opt in. That is usually the SAFE default — it means an accidental mass
#   delete in the primary does not propagate — but it means your two buckets
#   diverge permanently and neither is a mirror of the other. Decide which
#   behaviour you want and write down why.
###############################################################################

resource "aws_s3_bucket" "primary" {
  bucket = local.primary_bucket

  # force_destroy true so the lab tears down cleanly. In production this is a
  # loaded gun: it makes `terraform destroy` delete every object and every
  # version without asking. The teardown checklist covers the alternative.
  force_destroy = true

  tags = {
    Name = local.primary_bucket
    Role = "primary-data"
  }
}

resource "aws_s3_bucket_versioning" "primary" {
  bucket = aws_s3_bucket.primary.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "primary" {
  bucket = aws_s3_bucket.primary.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "primary" {
  bucket = aws_s3_bucket.primary.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "primary" {
  bucket = aws_s3_bucket.primary.id

  # The rule that stops versioning-plus-replication becoming unbounded. Applies
  # to the primary; there is an identical one on the replica, because a
  # lifecycle rule is per bucket and a DR bucket with no rule is a DR bucket
  # that grows forever.
  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.s3_noncurrent_version_expiration_days
    }

    # Incomplete multipart uploads are invisible in the console's object list
    # and are billed. They are one of the classic silent-growth line items.
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.primary]
}

# ---------------------------------------------------------------------------
# The DR-region replica bucket. Note `provider = aws.dr` on every resource
# below — it is per resource, it cannot be interpolated, and omitting it
# silently creates the bucket in the PRIMARY region, which produces a
# replication configuration that is valid, applies cleanly, and gives you a
# second copy inside the same blast radius. That failure is invisible in a
# plan unless you read the region in the ARN.
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "replica" {
  provider = aws.dr

  bucket        = local.replica_bucket
  force_destroy = true

  tags = {
    Name = local.replica_bucket
    Role = "dr-replica"
  }
}

resource "aws_s3_bucket_versioning" "replica" {
  provider = aws.dr

  bucket = aws_s3_bucket.replica.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "replica" {
  provider = aws.dr

  bucket = aws_s3_bucket.replica.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "replica" {
  provider = aws.dr

  bucket = aws_s3_bucket.replica.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "replica" {
  provider = aws.dr

  bucket = aws_s3_bucket.replica.id

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.s3_noncurrent_version_expiration_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.replica]
}

# ---------------------------------------------------------------------------
# The replication role. S3 assumes this to read from the source and write to
# the destination — which means replication runs with ITS OWN permissions, not
# yours, and a replication rule that silently stops working because someone
# tightened a bucket policy is a real and common failure.
#
# It fails SILENTLY. There is no alarm by default. The replication status of
# an object is a per-object attribute nobody queries. Check DR-011 exists
# because "CRR is configured" and "CRR is working" are different facts and
# only the first one is visible in a plan.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "replication" {
  count = var.enable_s3_replication ? 1 : 0

  name = local.replication_role

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "replication" {
  count = var.enable_s3_replication ? 1 : 0

  name = "${local.prefix}-replication-policy-${local.suffix}"
  role = aws_iam_role.replication[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadSourceBucket"
        Effect   = "Allow"
        Action   = ["s3:GetReplicationConfiguration", "s3:ListBucket"]
        Resource = [aws_s3_bucket.primary.arn]
      },
      {
        Sid    = "ReadSourceObjects"
        Effect = "Allow"
        Action = [
          "s3:GetObjectVersionForReplication",
          "s3:GetObjectVersionAcl",
          "s3:GetObjectVersionTagging",
        ]
        Resource = ["${aws_s3_bucket.primary.arn}/*"]
      },
      {
        Sid    = "WriteDestinationObjects"
        Effect = "Allow"
        Action = [
          "s3:ReplicateObject",
          "s3:ReplicateDelete",
          "s3:ReplicateTags",
        ]
        Resource = ["${aws_s3_bucket.replica.arn}/*"]
      },
    ]
  })
}

resource "aws_s3_bucket_replication_configuration" "primary" {
  count = var.enable_s3_replication ? 1 : 0

  bucket = aws_s3_bucket.primary.id
  role   = aws_iam_role.replication[0].arn

  rule {
    id       = "replicate-all-to-dr"
    status   = "Enabled"
    priority = 0

    filter {}

    delete_marker_replication {
      # Disabled: a mass delete in the primary does not propagate to DR. That
      # is a deliberate choice and it means the two buckets are NOT mirrors.
      # Whichever way you set this, write down why, because the person doing
      # the recovery will assume the other one.
      status = "Disabled"
    }

    destination {
      bucket        = aws_s3_bucket.replica.arn
      storage_class = "STANDARD"

      # Replication Time Control: the difference between "usually fast" and a
      # 15-minute SLA with CloudWatch metrics. ~$0.015/GB on top of everything
      # else. Metrics are what turn your RPO from an adjective into a number.
      dynamic "replication_time" {
        for_each = var.s3_replication_time_control ? [1] : []

        content {
          status = "Enabled"

          time {
            minutes = 15
          }
        }
      }

      # RTC requires metrics to be enabled, and metrics are what you actually
      # wanted. The API rejects replication_time without this block.
      dynamic "metrics" {
        for_each = var.s3_replication_time_control ? [1] : []

        content {
          status = "Enabled"

          event_threshold {
            minutes = 15
          }
        }
      }
    }
  }

  # Replication configuration requires versioning to exist first, on BOTH
  # buckets, and Terraform cannot infer the destination dependency because it
  # is expressed only as an ARN string inside the rule.
  depends_on = [
    aws_s3_bucket_versioning.primary,
    aws_s3_bucket_versioning.replica,
  ]
}


###############################################################################
# 7. RDS (OPTIONAL) — where "Multi-AZ is not a read replica" gets demonstrated
#
# Off by default. It costs ~$12.41/month single-AZ, exactly double that
# Multi-AZ, and 15–25 minutes of wall-clock time to create. Turn it on if you
# want the demonstration on real infrastructure.
#
# WHAT MULTI-AZ IS: a synchronous standby in another AZ, and an automatic DNS
# failover in typically 60–120 seconds. Every commit is acknowledged by the
# standby before the primary returns. Your RPO for an AZ failure is zero.
#
# WHAT IT IS NOT: a read replica. The standby serves no traffic. You cannot
# query it. It does not improve read throughput, write throughput, or latency
# in any way. It is a hot spare that costs exactly as much as the thing it is
# sparing. Teams enable it expecting read scaling and are then puzzled that
# nothing got faster.
#
# AND THE PART THAT IS ALSO NOT FREE: a Multi-AZ failover drops every
# connection and rolls back every in-flight transaction. An application with a
# connection pool and no retry logic experiences it as an outage whose length
# is set by the pool's TCP timeout — which is very often longer than the
# 60–120 second failover it was supposed to hide. "We have Multi-AZ so we have
# no downtime" is false in a way that only shows up during the failover.
###############################################################################

resource "aws_db_subnet_group" "main" {
  count = var.create_rds ? 1 : 0

  name       = "${local.prefix}-db-subnets-${local.suffix}"
  subnet_ids = aws_subnet.private[*].id

  # A DB subnet group MUST span at least two AZs even for a single-AZ
  # instance. AWS is making you pre-declare where a standby could go, which
  # means the only thing standing between single-AZ and Multi-AZ is one
  # boolean and a doubled bill.
  tags = {
    Name = "${local.prefix}-db-subnets-${local.suffix}"
  }
}

resource "random_password" "db" {
  count = var.create_rds ? 1 : 0

  length  = 24
  special = true
  # RDS rejects these in a master password. Discovering that at minute 18 of a
  # 20-minute create is a specific kind of annoying.
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "aws_db_instance" "main" {
  count = var.create_rds ? 1 : 0

  identifier     = "${local.prefix}-db-${local.suffix}"
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.rds_instance_class

  allocated_storage     = 20
  max_allocated_storage = 50
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "cbcday08"
  username = "cbcadmin"

  # THE PASSWORD LANDS IN STATE, IN PLAINTEXT. Day 05 made this argument at
  # length and it has not changed: `random_password` is a resource, its result
  # is an attribute, and attributes are stored. The state file is the secret.
  #
  # The production answer is manage_master_user_password = true, which hands
  # the credential to Secrets Manager and keeps it out of state entirely, at
  # ~$0.40/month. It is off here only so this stack has no per-resource cost
  # when create_rds is false, and this comment is the price of that choice.
  password = random_password.db[0].result

  db_subnet_group_name   = aws_db_subnet_group.main[0].name
  vpc_security_group_ids = [aws_security_group.rds[0].id]

  # The line the whole section is about. Default false: deliberately the wrong
  # answer, so DR-005 has something true to say about your own stack.
  multi_az = var.rds_multi_az

  # Default 1 day: technically backups, practically not. Corruption noticed on
  # Friday that began on Wednesday is unrecoverable. DR-006.
  backup_retention_period      = var.rds_backup_retention_days
  backup_window                = "07:00-08:00"
  maintenance_window           = "sun:08:30-sun:09:30"
  copy_tags_to_snapshot        = true
  auto_minor_version_upgrade   = true
  performance_insights_enabled = false

  # FALSE, and this is a deliberate lab compromise that is wrong everywhere
  # else. `skip_final_snapshot = true` means `terraform destroy` deletes the
  # database with no final backup. It is set true here so teardown is clean and
  # free; in production it is how a database ceases to exist during a refactor.
  skip_final_snapshot = true
  deletion_protection = false

  # Export the logs that let you see a failover happen. Without them the
  # failover is a gap in your metrics and a guess.
  enabled_cloudwatch_logs_exports = ["postgresql"]

  tags = {
    Name = "${local.prefix}-db-${local.suffix}"
    Role = "primary-relational"
  }
}


###############################################################################
# 8. ROUTE 53 — the health check, and why TTL is part of your RTO
#
# The health check is created unconditionally (it is $0.50/month and it is the
# input to every DNS failover decision). The failover RECORD SETS require a
# hosted zone you own, so they are gated on hosted_zone_id.
#
# This stack does NOT create a hosted zone. Creating one for a domain you do
# not control produces a zone that resolves for nobody, bills $0.50/month, and
# survives teardown because people do not think of zones as resources. If you
# own a domain, set hosted_zone_id and dns_record_name and the failover pair
# below completes the picture.
#
# ============================ TTL IS SPENT RTO ===============================
#
# When you fail over, a resolver that fetched your record one second earlier
# keeps serving the old address for the full TTL. Not on average — as a worst
# case, for some fraction of your users, no matter how fast everything else
# was. A 300-second TTL means five minutes of your recovery budget is gone
# before anything you did has any effect on those clients.
#
# So why not 1 second? Because TTL is also your DNS bill (~$0.40 per million
# queries, and a TTL of 1 multiplies query volume by roughly 300 against a TTL
# of 300) and a few milliseconds in front of every cold connection.
#
# And the caveat that ruins the arithmetic: TTL IS A REQUEST, NOT A GUARANTEE.
# Resolvers clamp minimums. Corporate resolvers cache far longer than you
# asked. Java, historically and by default, cached DNS resolutions for the
# life of the JVM — which is the origin of "we failed over successfully but
# the application servers kept connecting to the old database", a story every
# senior engineer has a version of.
#
# The consequence for design: DNS failover is a coarse, slow, best-effort
# mechanism. It is fine for shifting human traffic between regions. It is a
# poor mechanism for anything that needs to be fast or exact, which is why
# in-AZ failover uses load balancer target health and not DNS.
# =============================================================================

resource "aws_route53_health_check" "primary" {
  count = var.enable_route53_health_check ? 1 : 0

  fqdn              = aws_lb.main.dns_name
  port              = 80
  type              = "HTTP"
  resource_path     = var.target_group_health_check.path
  failure_threshold = 3

  # 30 seconds is the standard interval. "Fast" (10s) is available and costs
  # an extra ~$1.00/month per check. failure_threshold x request_interval is
  # your DNS-side detection time: 3 x 30 = 90 seconds here, before Route 53
  # will even consider the endpoint unhealthy. Add the TTL. That is your DNS
  # failover floor, and it is already 150 seconds before anything else has
  # happened.
  request_interval = 30

  # HTTPS and string matching are each ~$1.00/month extra. String matching in
  # particular is worth the dollar on anything real: without it, a health
  # check passes as long as the endpoint returns 200 — including the 200 your
  # load balancer returns from a maintenance page, and including the 200 an
  # application returns while every downstream call is failing.
  measure_latency = false

  tags = {
    Name = local.health_check_label
  }
}

# --- Failover record sets, only when you own a zone ------------------------

resource "aws_route53_record" "primary" {
  count = local.create_dns_records ? 1 : 0

  zone_id = var.hosted_zone_id
  name    = var.dns_record_name
  type    = "A"

  set_identifier = "primary"

  failover_routing_policy {
    type = "PRIMARY"
  }

  # An alias to the ALB. Alias records are free to query, resolve to the
  # load balancer's current addresses, and — importantly — an alias to an ALB
  # has an AWS-managed TTL of 60 seconds that you cannot set. So on THIS
  # record the TTL argument is not even available; route53_ttl applies to the
  # secondary below. That asymmetry is worth noticing: aliases quietly give
  # you a reasonable TTL, and the moment you use a plain A or CNAME record for
  # a failover target you own the TTL problem again.
  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = true
  }

  # A failover record without a health check is check DR-012, and it is worse
  # than useless: Route 53 considers a PRIMARY record with no health check to
  # be permanently healthy, so it never fails over. You have built the
  # mechanism and disabled the trigger.
  health_check_id = var.enable_route53_health_check ? aws_route53_health_check.primary[0].id : null
}

resource "aws_route53_record" "secondary" {
  count = local.create_dns_records ? 1 : 0

  zone_id = var.hosted_zone_id
  name    = var.dns_record_name
  type    = "A"

  set_identifier = "secondary"

  failover_routing_policy {
    type = "SECONDARY"
  }

  # There is no DR compute tier at CP1, so the secondary points at a
  # documented placeholder address. CP2 replaces this with the recovery
  # workflow's target.
  #
  # THIS IS NOT A COSMETIC PLACEHOLDER. A SECONDARY record pointing at
  # something that does not serve your application is the most common broken
  # DR configuration there is, and it is invisible until the day it is used.
  # It fails over correctly, instantly, and to nothing. The audit's DR-016
  # ("has this failover ever been tested") exists because configuration review
  # cannot catch this and one test can.
  ttl     = var.route53_ttl
  records = ["192.0.2.1"]
}


###############################################################################
# 9. NOTIFICATION
#
# One topic, used by the chaos Lambda now and by the recovery workflow at CP2.
###############################################################################

resource "aws_sns_topic" "dr" {
  name = "${local.prefix}-dr-${local.suffix}"

  tags = {
    Name = "${local.prefix}-dr-${local.suffix}"
  }
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.dr.arn
  protocol  = "email"
  endpoint  = var.notification_email

  # UNCONFIRMED UNTIL YOU CLICK THE LINK. Terraform reports this as created
  # and the subscription ARN is literally the string "PendingConfirmation".
  # Every message published before you click is accepted, billed, and
  # discarded. On this day that means a failover can complete at 03:00 with
  # nobody told. Confirm it before lab step 7. `next_steps` checks it for you.
}


###############################################################################
# 10. CHAOS — the only part of this file that turns a diagram into a number
#
# Everything above is a hypothesis. This is the experiment.
#
# The function's own docstring is the design document; read
# lambda/chaos.py before you invoke it. It defaults to dry run, for the reason
# Day 07 spent a section on: an automated action that changes production is a
# decision you are making now to be executed later by nobody, and the dry run
# is where you find out that the blast radius is not the one you pictured.
###############################################################################

data "archive_file" "chaos" {
  count = var.enable_chaos_lambda ? 1 : 0

  type        = "zip"
  source_file = "${path.module}/lambda/chaos.py"
  output_path = "${path.module}/build/chaos.zip"
}

resource "aws_iam_role" "chaos" {
  count = var.enable_chaos_lambda ? 1 : 0

  name = "${local.prefix}-chaos-role-${local.suffix}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "chaos" {
  count = var.enable_chaos_lambda ? 1 : 0

  name = "${local.prefix}-chaos-policy-${local.suffix}"
  role = aws_iam_role.chaos[0].id

  # Deliberately narrow, and narrow in a way that is worth studying. A chaos
  # tool is an outage generator with an IAM role. The blast radius of a bug in
  # chaos.py is exactly this policy and not one action more.
  #
  # Note in particular that ec2:TerminateInstances is scoped by a tag
  # condition. Without it, a typo in the instance-selection logic could
  # terminate anything in the account, and the failure mode of a chaos tool
  # with excessive permissions is indistinguishable from an attacker.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Logs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:${local.partition}:logs:${local.region}:${local.account_id}:*"
      },
      {
        Sid    = "ReadTopology"
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeSubnets",
          "ec2:DescribeNetworkAcls",
          "autoscaling:DescribeAutoScalingGroups",
        ]
        Resource = "*"
      },
      {
        Sid      = "TerminateTaggedInstancesOnly"
        Effect   = "Allow"
        Action   = ["ec2:TerminateInstances"]
        Resource = "arn:${local.partition}:ec2:${local.region}:${local.account_id}:instance/*"
        Condition = {
          StringEquals = {
            "aws:ResourceTag/Project" = "aws-aiops-bootcamp"
            "aws:ResourceTag/Day"     = "08"
          }
        }
      },
      {
        Sid      = "SetInstanceHealth"
        Effect   = "Allow"
        Action   = ["autoscaling:SetInstanceHealth"]
        Resource = "arn:${local.partition}:autoscaling:${local.region}:${local.account_id}:autoScalingGroup:*:autoScalingGroupName/${local.asg_name}"
      },
      {
        Sid      = "SwapNaclAssociations"
        Effect   = "Allow"
        Action   = ["ec2:ReplaceNetworkAclAssociation"]
        Resource = "*"
      },
      {
        Sid      = "Notify"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.dr.arn
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "chaos" {
  count = var.enable_chaos_lambda ? 1 : 0

  name              = "/aws/lambda/${local.chaos_function}"
  retention_in_days = 14

  tags = {
    Name = "/aws/lambda/${local.chaos_function}"
  }
}

resource "aws_lambda_function" "chaos" {
  count = var.enable_chaos_lambda ? 1 : 0

  function_name = local.chaos_function
  role          = aws_iam_role.chaos[0].arn
  handler       = "chaos.lambda_handler"
  runtime       = "python3.12"
  timeout       = 60
  memory_size   = 256

  filename         = data.archive_file.chaos[0].output_path
  source_code_hash = data.archive_file.chaos[0].output_base64sha256

  environment {
    variables = {
      ASG_NAME           = local.asg_name
      CHAOS_NACL_ID      = aws_network_acl.chaos.id
      PRIVATE_SUBNET_IDS = join(",", aws_subnet.private[*].id)
      TOPIC_ARN          = aws_sns_topic.dr.arn
      CHAOS_DRY_RUN      = var.chaos_dry_run ? "true" : "false"
    }
  }

  # The log group is created explicitly above rather than implicitly by the
  # first invocation, so that retention is set from the start. A Lambda that
  # creates its own log group creates it with NEVER EXPIRE, and Day 06's cost
  # section is about what that does over eighteen months.
  depends_on = [aws_cloudwatch_log_group.chaos]

  tags = {
    Name = local.chaos_function
  }
}


###############################################################################
# 11. DELIBERATELY BROKEN — the resources dr_audit.py is built to find
#
# Gated behind create_insecure_examples, default true, matching Days 04–07.
#
# Every one of these is something seen in a real account. None of them is a
# strawman. They are here so that the audit numbers in the finding contract
# are reproducible from a default apply rather than asserted.
###############################################################################

# ---------------------------------------------------------------------------
# A "highly available" ASG that is not. Every deliberate defect from section 4
# in one resource:
#   - vpc_zone_identifier lists ONE subnet, so every instance is in one AZ
#   - health_check_type = "EC2", so application failure is invisible
#   - health_check_grace_period = 0, so any slow boot is a termination loop
#   - no target group, so nothing external ever checks it
#
# desired_capacity = 0, so it costs nothing to exist. This is the single most
# useful property of the insecure examples in this repo: a misconfiguration
# you can audit without paying to run it.
# ---------------------------------------------------------------------------
resource "aws_autoscaling_group" "single_az" {
  count = var.create_insecure_examples ? 1 : 0

  name                = "${local.prefix}-legacy-asg-${local.suffix}"
  vpc_zone_identifier = [aws_subnet.private[0].id]

  min_size         = 0
  max_size         = 2
  desired_capacity = 0

  health_check_type         = "EC2"
  health_check_grace_period = 0

  launch_template {
    id      = aws_launch_template.app.id
    version = "$Latest"
  }

  tag {
    key                 = "Name"
    value               = "${local.prefix}-legacy-asg-${local.suffix}"
    propagate_at_launch = false
  }

  tag {
    key                 = "Project"
    value               = "aws-aiops-bootcamp"
    propagate_at_launch = true
  }

  tag {
    key                 = "Day"
    value               = "08"
    propagate_at_launch = true
  }

  tag {
    key                 = "cbc:insecure-example"
    value               = "true"
    propagate_at_launch = true
  }
}

# ---------------------------------------------------------------------------
# A bucket that somebody intended to replicate. No versioning, therefore no
# replication is possible — the API would reject the rule outright.
#
# This is the shape the real defect takes: a DR requirement, a bucket created
# for it, and a replication rule that was never added because the first attempt
# failed with a versioning error on a Friday. The bucket exists, it has a name
# containing "dr", and it contains nothing that will ever leave the region.
# DR-009 and DR-010.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "unversioned" {
  count = var.create_insecure_examples ? 1 : 0

  bucket        = "${local.prefix}-dr-archive-${local.account_id}-${local.suffix}"
  force_destroy = true

  tags = {
    Name                   = "${local.prefix}-dr-archive-${local.account_id}-${local.suffix}"
    "cbc:insecure-example" = "true"
    Purpose                = "intended-for-dr-replication-never-configured"
  }
}

resource "aws_s3_bucket_public_access_block" "unversioned" {
  count = var.create_insecure_examples ? 1 : 0

  bucket = aws_s3_bucket.unversioned[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------------------
# A table holding session state with no PITR and no replica. The justification
# at the time was "it is only sessions, we can regenerate it" — which is true
# right up to the point where the sessions table also holds the shopping cart.
#
# DR-008.
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "no_pitr" {
  count = var.create_insecure_examples ? 1 : 0

  name         = "${local.prefix}-sessions-${local.suffix}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "session_id"

  attribute {
    name = "session_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name                   = "${local.prefix}-sessions-${local.suffix}"
    "cbc:insecure-example" = "true"
  }
}

# ---------------------------------------------------------------------------
# A snapshot taken by hand, once, during a migration, and never since.
#
# It is a real, valid, restorable EBS snapshot. It is also the entire disaster
# recovery posture of an alarming number of small production environments, and
# it has two defects the auditor looks for: it is a MANUAL snapshot with no
# schedule behind it (so its age grows without bound), and it exists only in
# the primary region (so it does not survive the failure it is supposedly for).
#
# DR-007 measures its age against rpo_target_minutes. It will fire immediately,
# because the snapshot is of an empty volume created at apply time and the
# check is about the schedule, not the bytes.
# ---------------------------------------------------------------------------
resource "aws_ebs_volume" "stale_source" {
  count = var.create_insecure_examples ? 1 : 0

  availability_zone = local.azs[0]
  size              = 1
  type              = "gp3"
  encrypted         = true

  tags = {
    Name                   = "${local.prefix}-legacy-data-${local.suffix}"
    "cbc:insecure-example" = "true"
  }
}

resource "aws_ebs_snapshot" "stale" {
  count = var.create_insecure_examples ? 1 : 0

  volume_id   = aws_ebs_volume.stale_source[0].id
  description = "cbc-day08 manual snapshot — taken once, never scheduled, never copied to the DR region, never restored"

  tags = {
    Name                   = "${local.prefix}-manual-snapshot-${local.suffix}"
    "cbc:insecure-example" = "true"
    "cbc:last-restored"    = "never"
  }
}
