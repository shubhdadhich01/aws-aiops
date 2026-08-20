###############################################################################
# Day 02 — Enterprise Networking & Security Architecture
#
# What this builds:
#   1. A VPC with DNS enabled                        (the isolation boundary)
#   2. Three subnet tiers × N AZs                    (public / private-app / private-data)
#   3. Internet Gateway + NAT Gateway                (💸 the first real cost in this bootcamp)
#   4. Route tables that MAKE a subnet public or private
#   5. Network ACLs — stateless, subnet-level, with explicit ephemeral-port rules
#   6. Security groups chained by reference          (ALB → app → db)
#   7. VPC Flow Logs to CloudWatch                   (you cannot investigate what you did not log)
#   8. A free S3 gateway endpoint                    (and optional, paid, interface endpoints)
#   9. Optionally, a pile of deliberately insecure networking for the audit tool to find
#
# Everything is prefixed `cbc-day02-` and tagged Project=aws-aiops-bootcamp, Day=02.
#
# ⚠️  READ THIS BEFORE YOU APPLY
#     If enable_nat_gateway = true, this configuration costs roughly $32/month while it
#     exists. It bills per hour from creation, idle or not. Run terraform destroy at the
#     end of the session. See ../../teardown-checklist.md.
###############################################################################

locals {
  account_id = data.aws_caller_identity.current.account_id
  prefix     = var.name_prefix

  # Take the first N AZs the region offers us.
  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # Subnet CIDR math. cidrsubnet(prefix, newbits, netnum) carves a smaller block out
  # of a larger one. With a /16 and newbits=8 you get /24s:
  #   cidrsubnet("10.20.0.0/16", 8,  0) = 10.20.0.0/24
  #   cidrsubnet("10.20.0.0/16", 8, 10) = 10.20.10.0/24
  # Leaving gaps between the tiers (0-9, 10-19, 20-29) means you can add a fourth
  # public subnet later without renumbering anything. Future-you will be grateful.
  public_subnet_cidrs       = [for i, az in local.azs : cidrsubnet(var.vpc_cidr, 8, i)]
  private_app_subnet_cidrs  = [for i, az in local.azs : cidrsubnet(var.vpc_cidr, 8, i + 10)]
  private_data_subnet_cidrs = [for i, az in local.azs : cidrsubnet(var.vpc_cidr, 8, i + 20)]

  # How many NAT Gateways to create. One shared NAT is cheap and has an AZ SPOF;
  # one per AZ is what production does and costs N × $32/month.
  nat_gateway_count = var.enable_nat_gateway ? (var.single_nat_gateway ? 1 : var.az_count) : 0
}

###############################################################################
# 1. THE VPC
#
# A VPC is a logically isolated section of the AWS network that you own. Nothing
# routes into it unless you build the road. That is the whole security model:
# default deny at the topology level, before any policy is even evaluated.
#
# enable_dns_hostnames is the one people forget. Without it, instances get private
# DNS names but no public ones, and — more painfully — interface VPC endpoints and
# RDS private endpoints resolve incorrectly.
###############################################################################

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true # resolve via the Amazon DNS server at VPC_base+2
  enable_dns_hostnames = true # assign public DNS names; REQUIRED for interface endpoints

  tags = {
    Name = "${local.prefix}-vpc"
    Tier = "network"
  }
}

# CIS 5.3 / AWS Foundational Security Best Practices: the default security group
# should permit no traffic at all. Managing it here with empty rule blocks strips
# the default "allow all from self, allow all egress" pair.
#
# Note: you cannot DELETE a default security group. You can only empty it. Terraform
# "adopts" it with this resource rather than creating anything new.
resource "aws_default_security_group" "main" {
  vpc_id = aws_vpc.main.id

  # Deliberately empty: no ingress, no egress.
  # If anything in your account depends on the default SG, it will now break — which
  # is the point. Nothing should depend on the default SG.

  tags = {
    Name = "${local.prefix}-default-sg-locked-down"
  }
}

###############################################################################
# 2. INTERNET GATEWAY
#
# An IGW is a horizontally-scaled, redundant, AWS-managed component. It does two
# things: it performs 1:1 NAT between a private IPv4 address and its associated
# public IPv4 address, and it is the target that makes a route table "public".
#
# Attaching an IGW to a VPC does NOT make anything reachable. A subnet becomes
# public only when a ROUTE TABLE sends 0.0.0.0/0 to that IGW. Remember that
# sentence — it is the single most common Day 2 interview question.
###############################################################################

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${local.prefix}-igw"
  }
}

###############################################################################
# 3. SUBNETS — THREE TIERS
#
# Tier 1 · public       → has a route to the IGW. Load balancers, NAT, bastions.
# Tier 2 · private-app  → no inbound from the internet; outbound via NAT.
# Tier 3 · private-data → no internet route at all, in either direction.
#
# A subnet lives in exactly ONE Availability Zone. That is why you need at least
# two of every tier: an AZ is a failure domain, and a single-AZ subnet means a
# single-AZ application.
#
# AWS reserves 5 addresses in every subnet: network address, VPC router, DNS,
# future use, broadcast. A /24 gives you 251 usable addresses, not 256.
###############################################################################

resource "aws_subnet" "public" {
  count = var.az_count

  vpc_id            = aws_vpc.main.id
  cidr_block        = local.public_subnet_cidrs[count.index]
  availability_zone = local.azs[count.index]

  # Auto-assign a public IPv4 on launch. This is CORRECT for a genuinely public
  # tier and WRONG everywhere else. The assessment tool reports it either way
  # (VPC-015) — your job is to know which subnets are supposed to have it.
  map_public_ip_on_launch = true

  tags = {
    Name = "${local.prefix}-public-${local.azs[count.index]}"
    Tier = "public"
  }
}

resource "aws_subnet" "private_app" {
  count = var.az_count

  vpc_id                  = aws_vpc.main.id
  cidr_block              = local.private_app_subnet_cidrs[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = false

  tags = {
    Name = "${local.prefix}-private-app-${local.azs[count.index]}"
    Tier = "private-app"
  }
}

resource "aws_subnet" "private_data" {
  count = var.az_count

  vpc_id                  = aws_vpc.main.id
  cidr_block              = local.private_data_subnet_cidrs[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = false

  tags = {
    Name = "${local.prefix}-private-data-${local.azs[count.index]}"
    Tier = "private-data"
  }
}

###############################################################################
# 4. NAT GATEWAY  💸💸💸
#
# A NAT Gateway lets instances in a private subnet make OUTBOUND connections to
# the internet (yum update, API calls, pulling containers) while remaining
# unreachable from the internet. Return traffic for connections the instance
# opened is allowed back; nothing else is.
#
# ⚠️ COST — the big one. In us-east-1:
#      $0.045 per hour it exists        = ~$32.40 / month
#    + $0.045 per GB processed
#    The Elastic IP attached to it is free WHILE attached. An unattached EIP
#    costs ~$3.60/month, which is how people end up paying for nothing.
#
# The NAT Gateway itself lives in a PUBLIC subnet — it needs the IGW to reach the
# internet — but it SERVES the private subnets. Putting a NAT Gateway in a private
# subnet is a classic, silent, expensive mistake: it creates fine, and nothing
# ever works.
###############################################################################

resource "aws_eip" "nat" {
  count = local.nat_gateway_count

  domain = "vpc"

  tags = {
    Name = "${local.prefix}-nat-eip-${count.index}"
  }

  depends_on = [aws_internet_gateway.main]
}

resource "aws_nat_gateway" "main" {
  count = local.nat_gateway_count

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id # ← PUBLIC subnet. Always.

  tags = {
    Name = "${local.prefix}-nat-${local.azs[count.index]}"
  }

  # The IGW must exist and be attached before the NAT can function.
  depends_on = [aws_internet_gateway.main]
}

###############################################################################
# 5. ROUTE TABLES — WHERE "PUBLIC" AND "PRIVATE" ACTUALLY HAPPEN
#
# Every route table has an implicit local route for the VPC CIDR that you cannot
# remove or override. That is why every subnet in a VPC can always talk to every
# other subnet, regardless of route tables. Isolation between tiers comes from
# security groups and NACLs — never from routing.
#
# Routes are matched most-specific-first (longest prefix match):
#   10.20.10.0/24 → beats → 10.20.0.0/16 → beats → 0.0.0.0/0
###############################################################################

# --- Public route table: 0.0.0.0/0 → Internet Gateway ------------------------
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "${local.prefix}-rt-public"
    Tier = "public"
  }
}

resource "aws_route_table_association" "public" {
  count = var.az_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# --- Private-app route tables: 0.0.0.0/0 → NAT Gateway -----------------------
# One route table PER AZ, even with a shared NAT. Why? Because the day you flip
# single_nat_gateway to false, the routing already has the right shape and you
# change one line instead of restructuring.
resource "aws_route_table" "private_app" {
  count = var.az_count

  vpc_id = aws_vpc.main.id

  # Conditionally add the NAT route. With enable_nat_gateway = false the private
  # subnets simply have no internet path — which is a perfectly valid, and free,
  # architecture if your workload only talks to VPC endpoints.
  dynamic "route" {
    for_each = var.enable_nat_gateway ? [1] : []
    content {
      cidr_block = "0.0.0.0/0"
      # With a single shared NAT, every AZ points at NAT #0.
      # With one NAT per AZ, each AZ points at its own — no cross-AZ data charges.
      nat_gateway_id = var.single_nat_gateway ? aws_nat_gateway.main[0].id : aws_nat_gateway.main[count.index].id
    }
  }

  tags = {
    Name = "${local.prefix}-rt-private-app-${local.azs[count.index]}"
    Tier = "private-app"
  }
}

resource "aws_route_table_association" "private_app" {
  count = var.az_count

  subnet_id      = aws_subnet.private_app[count.index].id
  route_table_id = aws_route_table.private_app[count.index].id
}

# --- Private-data route table: NO internet route whatsoever ------------------
# Local routes only. A database in here cannot be reached from the internet and
# cannot reach the internet, even if a security group is misconfigured. That is
# defense in depth: two independent controls have to fail before you are exposed.
resource "aws_route_table" "private_data" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${local.prefix}-rt-private-data"
    Tier = "private-data"
  }
}

resource "aws_route_table_association" "private_data" {
  count = var.az_count

  subnet_id      = aws_subnet.private_data[count.index].id
  route_table_id = aws_route_table.private_data.id
}

###############################################################################
# 6. NETWORK ACLs — STATELESS, SUBNET-LEVEL
#
# The two things that make NACLs different from security groups:
#
#   1. STATELESS. A NACL does not remember that you allowed the outbound request,
#      so you MUST also allow the inbound response. Response traffic arrives on an
#      ephemeral port (1024–65535 for Linux/AWS services). Forgetting the ephemeral
#      rule is the #1 cause of "my NACL broke everything and I don't know why".
#
#   2. NUMBERED AND ORDERED. Rules evaluate lowest number first, and the FIRST
#      match wins — allow or deny. A permissive rule at 100 makes a deny at 200
#      completely dead code. The assessment tool detects exactly that (VPC-013).
#
# Leave gaps between rule numbers (100, 110, 120...) so you can insert later.
###############################################################################

# --- Public NACL -------------------------------------------------------------
resource "aws_network_acl" "public" {
  vpc_id     = aws_vpc.main.id
  subnet_ids = aws_subnet.public[*].id

  tags = {
    Name = "${local.prefix}-nacl-public"
    Tier = "public"
  }
}

# Inbound: web traffic + ephemeral returns. Note there is NO inbound SSH rule —
# administrative access belongs to a bastion SG, not a subnet-wide NACL.
resource "aws_network_acl_rule" "public_in_http" {
  network_acl_id = aws_network_acl.public.id
  rule_number    = 100
  egress         = false
  protocol       = "tcp"
  rule_action    = "allow"
  cidr_block     = "0.0.0.0/0"
  from_port      = 80
  to_port        = 80
}

resource "aws_network_acl_rule" "public_in_https" {
  network_acl_id = aws_network_acl.public.id
  rule_number    = 110
  egress         = false
  protocol       = "tcp"
  rule_action    = "allow"
  cidr_block     = "0.0.0.0/0"
  from_port      = 443
  to_port        = 443
}

# ⭐ The rule everybody forgets. Without this, outbound requests from public
# instances leave successfully and the replies are silently dropped.
resource "aws_network_acl_rule" "public_in_ephemeral" {
  network_acl_id = aws_network_acl.public.id
  rule_number    = 120
  egress         = false
  protocol       = "tcp"
  rule_action    = "allow"
  cidr_block     = "0.0.0.0/0"
  from_port      = 1024
  to_port        = 65535
}

# Outbound: allow everything. Tightening egress at the NACL layer is possible but
# painful; do egress control in security groups where it is stateful.
resource "aws_network_acl_rule" "public_out_all" {
  network_acl_id = aws_network_acl.public.id
  rule_number    = 100
  egress         = true
  protocol       = "-1"
  rule_action    = "allow"
  cidr_block     = "0.0.0.0/0"
  from_port      = 0
  to_port        = 0
}

# --- Private NACL (app + data tiers) -----------------------------------------
# Inbound is restricted to the VPC CIDR. Nothing from outside the VPC can even
# reach these subnets at layer 3, regardless of security group configuration.
resource "aws_network_acl" "private" {
  vpc_id     = aws_vpc.main.id
  subnet_ids = concat(aws_subnet.private_app[*].id, aws_subnet.private_data[*].id)

  tags = {
    Name = "${local.prefix}-nacl-private"
    Tier = "private"
  }
}

resource "aws_network_acl_rule" "private_in_vpc" {
  network_acl_id = aws_network_acl.private.id
  rule_number    = 100
  egress         = false
  protocol       = "-1"
  rule_action    = "allow"
  cidr_block     = var.vpc_cidr # ← only from inside this VPC
  from_port      = 0
  to_port        = 0
}

# Return traffic from the NAT Gateway / internet arrives on ephemeral ports.
# Restricted to TCP so we are not blanket-allowing the internet inbound.
resource "aws_network_acl_rule" "private_in_ephemeral" {
  network_acl_id = aws_network_acl.private.id
  rule_number    = 110
  egress         = false
  protocol       = "tcp"
  rule_action    = "allow"
  cidr_block     = "0.0.0.0/0"
  from_port      = 1024
  to_port        = 65535
}

resource "aws_network_acl_rule" "private_out_all" {
  network_acl_id = aws_network_acl.private.id
  rule_number    = 100
  egress         = true
  protocol       = "-1"
  rule_action    = "allow"
  cidr_block     = "0.0.0.0/0"
  from_port      = 0
  to_port        = 0
}

###############################################################################
# 7. SECURITY GROUPS — STATEFUL, ENI-LEVEL, CHAINED BY REFERENCE
#
# The most important technique on this page: a security group rule can reference
# ANOTHER SECURITY GROUP instead of a CIDR block. That gives you identity-based
# network policy — "whatever is running behind the ALB may reach the app tier" —
# which survives auto scaling, IP churn, and re-deploys without any edits.
#
#   Internet ──443──▶ [alb-sg] ──8080──▶ [app-sg] ──5432──▶ [db-sg]
#                                  ▲
#                        [bastion-sg] ──22──┘   (from YOUR IP only)
#
# Security groups are ALLOW-only. There is no deny rule. If a rule doesn't match,
# the traffic is dropped. That is the opposite of a NACL, which has both.
###############################################################################

# --- Tier 1: Load balancer ---------------------------------------------------
resource "aws_security_group" "alb" {
  name        = "${local.prefix}-alb-sg"
  description = "Public entry point. HTTP/HTTPS from the internet only."
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${local.prefix}-alb-sg"
    Tier = "public"
  }

  # Terraform replaces SGs by creating the new one first; without this it cannot,
  # because the old SG is still referenced by other SGs.
  lifecycle {
    create_before_destroy = true
  }
}

# Modern style: separate rule resources rather than inline ingress/egress blocks.
# Inline blocks are authoritative and fight with anything else that touches the SG.
resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from the internet — legitimately public"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from the internet — redirect to HTTPS at the listener"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

# Egress from the ALB is scoped to the app tier, not 0.0.0.0/0. Most people leave
# egress wide open because it's the default. Scoping it is a cheap, real control
# against data exfiltration and C2 callbacks.
resource "aws_vpc_security_group_egress_rule" "alb_to_app" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Forward to the application tier only"
  referenced_security_group_id = aws_security_group.app.id
  from_port                    = var.app_port
  to_port                      = var.app_port
  ip_protocol                  = "tcp"
}

# --- Tier 2: Application -----------------------------------------------------
resource "aws_security_group" "app" {
  name        = "${local.prefix}-app-sg"
  description = "Application tier. Reachable only from the ALB and the bastion."
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${local.prefix}-app-sg"
    Tier = "private-app"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# ⭐ SG-to-SG reference. No CIDR anywhere. Scale the ALB to 40 nodes across new
# subnets and this rule keeps working with zero changes.
resource "aws_vpc_security_group_ingress_rule" "app_from_alb" {
  security_group_id            = aws_security_group.app.id
  description                  = "App port from the load balancer security group"
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = var.app_port
  to_port                      = var.app_port
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "app_ssh_from_bastion" {
  security_group_id            = aws_security_group.app.id
  description                  = "SSH from the bastion security group only"
  referenced_security_group_id = aws_security_group.bastion.id
  from_port                    = 22
  to_port                      = 22
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "app_to_db" {
  security_group_id            = aws_security_group.app.id
  description                  = "Database port to the data tier"
  referenced_security_group_id = aws_security_group.db.id
  from_port                    = var.db_port
  to_port                      = var.db_port
  ip_protocol                  = "tcp"
}

# The app tier does need general outbound for package installs and API calls.
# In a hardened build you would replace this with VPC endpoints and delete it.
resource "aws_vpc_security_group_egress_rule" "app_https_out" {
  security_group_id = aws_security_group.app.id
  description       = "HTTPS out for package repos, APIs and AWS endpoints"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

# --- Tier 3: Database --------------------------------------------------------
resource "aws_security_group" "db" {
  name        = "${local.prefix}-db-sg"
  description = "Data tier. One ingress rule, from the app tier, on one port. No egress."
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${local.prefix}-db-sg"
    Tier = "private-data"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "db_from_app" {
  security_group_id            = aws_security_group.db.id
  description                  = "Database port from the application security group only"
  referenced_security_group_id = aws_security_group.app.id
  from_port                    = var.db_port
  to_port                      = var.db_port
  ip_protocol                  = "tcp"
}

# Note: NO egress rules at all on the db SG. A database has no legitimate reason
# to initiate outbound connections. Stateful return traffic for inbound queries
# still works — that is what "stateful" means.

# --- Bastion -----------------------------------------------------------------
resource "aws_security_group" "bastion" {
  name        = "${local.prefix}-bastion-sg"
  description = "Jump host. SSH from one trusted CIDR. This is the CORRECT pattern."
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${local.prefix}-bastion-sg"
    Tier = "public"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Compare this rule with cbc-day02-BAD-open-ssh-sg further down. Same port,
# same protocol, one character of difference in the CIDR — and one of them is a
# CRITICAL finding while the other is fine.
resource "aws_vpc_security_group_ingress_rule" "bastion_ssh" {
  security_group_id = aws_security_group.bastion.id
  description       = "SSH from the trusted admin CIDR only"
  cidr_ipv4         = var.trusted_admin_cidr
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "bastion_to_app_ssh" {
  security_group_id            = aws_security_group.bastion.id
  description                  = "SSH onward to the application tier"
  referenced_security_group_id = aws_security_group.app.id
  from_port                    = 22
  to_port                      = 22
  ip_protocol                  = "tcp"
}

###############################################################################
# 8. VPC ENDPOINTS
#
# Two flavours, and the difference matters for both cost and design:
#
#   GATEWAY endpoint    (S3, DynamoDB only)  — FREE. It is a ROUTE, added to your
#                       route tables. Traffic to S3 never touches the NAT Gateway,
#                       so you stop paying $0.045/GB to talk to a service that
#                       lives in the same region.
#
#   INTERFACE endpoint  (~every other service) — an ENI with a private IP in your
#                       subnet, powered by PrivateLink. ~$0.01/hour PER AZ plus
#                       $0.01/GB. Real money; enable deliberately.
#
# For most workloads the S3 gateway endpoint pays for the whole networking design
# on its own. Add it on day one, every time.
###############################################################################

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"

  # A gateway endpoint works by injecting a prefix-list route into route tables.
  # If you forget to associate the route table, the endpoint exists and does nothing.
  route_table_ids = concat(
    [aws_route_table.private_data.id],
    aws_route_table.private_app[*].id,
  )

  tags = {
    Name = "${local.prefix}-vpce-s3"
  }
}

# 💸 Optional and off by default. Interface endpoints let private instances reach
# Systems Manager without any NAT Gateway at all — the modern replacement for a
# bastion host. Genuinely better security; genuinely costs ~$22/month for three
# endpoints across two AZs.
locals {
  interface_endpoint_services = var.enable_interface_endpoints ? [
    "ssm",
    "ssmmessages",
    "ec2messages",
  ] : []
}

resource "aws_security_group" "vpce" {
  count = var.enable_interface_endpoints ? 1 : 0

  name        = "${local.prefix}-vpce-sg"
  description = "HTTPS from inside the VPC to interface endpoints."
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${local.prefix}-vpce-sg"
  }
}

resource "aws_vpc_security_group_ingress_rule" "vpce_https" {
  count = var.enable_interface_endpoints ? 1 : 0

  security_group_id = aws_security_group.vpce[0].id
  description       = "HTTPS from within the VPC"
  cidr_ipv4         = var.vpc_cidr
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_endpoint" "interface" {
  for_each = toset(local.interface_endpoint_services)

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private_app[*].id
  security_group_ids  = [aws_security_group.vpce[0].id]
  private_dns_enabled = true # requires enable_dns_hostnames on the VPC

  tags = {
    Name = "${local.prefix}-vpce-${each.value}"
  }
}

###############################################################################
# 9. VPC FLOW LOGS
#
# Flow logs record metadata about IP traffic: src, dst, ports, protocol, packets,
# bytes, and ACCEPT / REJECT. They do NOT capture packet contents.
#
# Why this is a security control and not an ops nicety: after an incident, flow
# logs are frequently the ONLY evidence of what talked to what. You cannot
# retroactively enable them. A VPC without flow logs is a VPC whose incidents you
# will never fully explain — which is why the assessment tool rates VPC-014 HIGH.
#
# Destinations: CloudWatch Logs (queryable now, ~$0.50/GB ingest) or S3 (cheap,
# queryable with Athena). Production usually does both.
###############################################################################

resource "aws_cloudwatch_log_group" "flow_logs" {
  count = var.enable_flow_logs ? 1 : 0

  name              = "/aws/vpc/${local.prefix}-flow-logs"
  retention_in_days = var.flow_logs_retention_days

  tags = {
    Name = "${local.prefix}-flow-logs"
  }
}

data "aws_iam_policy_document" "flow_logs_trust" {
  statement {
    sid     = "AllowFlowLogsServiceToAssume"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }

    # Confused-deputy protection — the same pattern you learned on Day 01,
    # now applied to a service principal instead of a third-party account.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "flow_logs" {
  count = var.enable_flow_logs ? 1 : 0

  name               = "${local.prefix}-flow-logs-role"
  path               = "/bootcamp/"
  description        = "Allows the VPC Flow Logs service to write to CloudWatch Logs."
  assume_role_policy = data.aws_iam_policy_document.flow_logs_trust.json
}

data "aws_iam_policy_document" "flow_logs_write" {
  count = var.enable_flow_logs ? 1 : 0

  statement {
    sid    = "WriteFlowLogsToCloudWatch"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
    ]
    # Scoped to this log group and its streams — not "*", which is what the
    # AWS documentation example unhelpfully suggests.
    resources = [
      aws_cloudwatch_log_group.flow_logs[0].arn,
      "${aws_cloudwatch_log_group.flow_logs[0].arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "flow_logs" {
  count = var.enable_flow_logs ? 1 : 0

  name   = "${local.prefix}-flow-logs-write"
  role   = aws_iam_role.flow_logs[0].id
  policy = data.aws_iam_policy_document.flow_logs_write[0].json
}

resource "aws_flow_log" "main" {
  count = var.enable_flow_logs ? 1 : 0

  vpc_id                   = aws_vpc.main.id
  traffic_type             = "ALL" # ACCEPT + REJECT. REJECT is where the attacks are.
  iam_role_arn             = aws_iam_role.flow_logs[0].arn
  log_destination_type     = "cloud-watch-logs"
  log_destination          = aws_cloudwatch_log_group.flow_logs[0].arn
  max_aggregation_interval = 60 # seconds; 60 costs slightly more than 600 but detects faster

  tags = {
    Name = "${local.prefix}-flow-log"
  }
}

###############################################################################
# 10. THE DELIBERATELY INSECURE EXAMPLES  😈
#
# Everything below exists ONLY so the Python assessment tool has something to
# find. There is no compute in any of it, so it costs $0 — but every single one
# of these is a real misconfiguration that has caused a real breach somewhere.
#
# Gated behind create_insecure_examples. Turn it off and the tool should come
# back nearly clean; that contrast is the lesson.
###############################################################################

# --- 😈 #1: the security group that ruins companies --------------------------
# 0.0.0.0/0 on 22 is how the majority of "we got cryptomined" stories start.
resource "aws_security_group" "bad_open_ssh" {
  count = var.create_insecure_examples ? 1 : 0

  name        = "${local.prefix}-BAD-open-ssh-sg"
  description = "DELIBERATELY INSECURE — training target. Never attach this to anything."
  vpc_id      = aws_vpc.main.id

  tags = {
    Name    = "${local.prefix}-BAD-open-ssh-sg"
    Purpose = "training-target"
  }
}

resource "aws_vpc_security_group_ingress_rule" "bad_ssh_world" {
  count = var.create_insecure_examples ? 1 : 0

  security_group_id = aws_security_group.bad_open_ssh[0].id
  description       = "🔴 SSH from the entire internet — VPC-001"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "bad_rdp_world" {
  count = var.create_insecure_examples ? 1 : 0

  security_group_id = aws_security_group.bad_open_ssh[0].id
  description       = "🔴 RDP from the entire internet — VPC-002"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 3389
  to_port           = 3389
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "bad_all_traffic" {
  count = var.create_insecure_examples ? 1 : 0

  security_group_id = aws_security_group.bad_open_ssh[0].id
  description       = "🔴 ALL protocols, ALL ports, from anywhere — VPC-003"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1" # -1 means every protocol; ports are ignored
}

resource "aws_vpc_security_group_ingress_rule" "bad_postgres_world" {
  count = var.create_insecure_examples ? 1 : 0

  security_group_id = aws_security_group.bad_open_ssh[0].id
  description       = "🟠 PostgreSQL exposed to the internet — VPC-004"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 5432
  to_port           = 5432
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "bad_ipv6_world" {
  count = var.create_insecure_examples ? 1 : 0

  security_group_id = aws_security_group.bad_open_ssh[0].id
  description       = "🟠 ::/0 — people firewall IPv4 and forget IPv6 entirely — VPC-005"
  cidr_ipv6         = "::/0"
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "bad_wide_range" {
  count = var.create_insecure_examples ? 1 : 0

  security_group_id = aws_security_group.bad_open_ssh[0].id
  description       = "🟠 A 30,000-port range from anywhere — VPC-006"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 30000
  to_port           = 60000
  ip_protocol       = "tcp"
}

# --- 😈 #2: the orphan ------------------------------------------------------
# Attached to nothing. Harmless today; attached "temporarily to debug something"
# next quarter. Every account accumulates dozens of these.
resource "aws_security_group" "bad_unused" {
  count = var.create_insecure_examples ? 1 : 0

  name        = "${local.prefix}-BAD-unused-sg"
  description = "DELIBERATELY INSECURE — orphaned security group, attached to no ENI. VPC-009."
  vpc_id      = aws_vpc.main.id

  tags = {
    Name    = "${local.prefix}-BAD-unused-sg"
    Purpose = "training-target"
  }
}

resource "aws_vpc_security_group_ingress_rule" "bad_unused_mysql" {
  count = var.create_insecure_examples ? 1 : 0

  security_group_id = aws_security_group.bad_unused[0].id
  description       = "🟠 MySQL open to the world, on a group nobody is watching"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 3306
  to_port           = 3306
  ip_protocol       = "tcp"
}

# --- 😈 #3: the NACL with dead code -----------------------------------------
# Rule 100 allows everything. Rule 200 denies SSH. Rule 200 will NEVER be
# evaluated, because NACLs stop at the first match. This is the single most
# common NACL mistake and it is invisible in the console unless you read the
# rule numbers carefully.
resource "aws_subnet" "bad_quarantine" {
  count = var.create_insecure_examples ? 1 : 0

  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 90)
  availability_zone       = local.azs[0]
  map_public_ip_on_launch = true

  tags = {
    Name    = "${local.prefix}-BAD-internal-app-subnet"
    Tier    = "private" # ← the name and the tag both say private...
    Purpose = "training-target"
  }
}

# ...but the route table sends 0.0.0.0/0 straight to the Internet Gateway.
# A subnet's tier is determined by its ROUTES, never by its name or its tags.
# VPC-016.
resource "aws_route_table" "bad_quarantine" {
  count = var.create_insecure_examples ? 1 : 0

  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name    = "${local.prefix}-BAD-rt-mislabelled"
    Purpose = "training-target"
  }
}

resource "aws_route_table_association" "bad_quarantine" {
  count = var.create_insecure_examples ? 1 : 0

  subnet_id      = aws_subnet.bad_quarantine[0].id
  route_table_id = aws_route_table.bad_quarantine[0].id
}

resource "aws_network_acl" "bad_open" {
  count = var.create_insecure_examples ? 1 : 0

  vpc_id     = aws_vpc.main.id
  subnet_ids = [aws_subnet.bad_quarantine[0].id]

  tags = {
    Name    = "${local.prefix}-BAD-open-nacl"
    Purpose = "training-target"
  }
}

resource "aws_network_acl_rule" "bad_in_allow_all" {
  count = var.create_insecure_examples ? 1 : 0

  network_acl_id = aws_network_acl.bad_open[0].id
  rule_number    = 100
  egress         = false
  protocol       = "-1"
  rule_action    = "allow"
  cidr_block     = "0.0.0.0/0" # 🟡 VPC-011 — the NACL is doing nothing at all
  from_port      = 0
  to_port        = 0
}

resource "aws_network_acl_rule" "bad_in_dead_deny" {
  count = var.create_insecure_examples ? 1 : 0

  network_acl_id = aws_network_acl.bad_open[0].id
  rule_number    = 200
  egress         = false
  protocol       = "tcp"
  rule_action    = "deny" # 🟡 VPC-013 — unreachable. Rule 100 already matched.
  cidr_block     = "0.0.0.0/0"
  from_port      = 22
  to_port        = 22
}

resource "aws_network_acl_rule" "bad_in_open_db" {
  count = var.create_insecure_examples ? 1 : 0

  network_acl_id = aws_network_acl.bad_open[0].id
  rule_number    = 300
  egress         = false
  protocol       = "tcp"
  rule_action    = "allow" # 🟠 VPC-012 — MongoDB from the internet, at the subnet layer
  cidr_block     = "0.0.0.0/0"
  from_port      = 27017
  to_port        = 27017
}

resource "aws_network_acl_rule" "bad_out_all" {
  count = var.create_insecure_examples ? 1 : 0

  network_acl_id = aws_network_acl.bad_open[0].id
  rule_number    = 100
  egress         = true
  protocol       = "-1"
  rule_action    = "allow"
  cidr_block     = "0.0.0.0/0"
  from_port      = 0
  to_port        = 0
}

# --- 😈 #4: the VPC nobody is logging ---------------------------------------
# A second, tiny VPC with no flow logs and an untouched default security group.
# Costs nothing — a VPC with no gateways and no compute is free — and gives the
# tool a clean VPC-014 and VPC-010 to report.
resource "aws_vpc" "bad_unlogged" {
  count = var.create_insecure_examples ? 1 : 0

  cidr_block           = "10.99.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = false

  tags = {
    Name    = "${local.prefix}-BAD-unlogged-vpc"
    Purpose = "training-target"
  }
}

resource "aws_subnet" "bad_unlogged" {
  count = var.create_insecure_examples ? 1 : 0

  vpc_id                  = aws_vpc.bad_unlogged[0].id
  cidr_block              = "10.99.0.0/24"
  availability_zone       = local.azs[0]
  map_public_ip_on_launch = true # 🟡 VPC-015 on a subnet with no IGW at all

  tags = {
    Name    = "${local.prefix}-BAD-unlogged-subnet"
    Purpose = "training-target"
  }
}
