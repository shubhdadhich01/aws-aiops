###############################################################################
# Day 03 — main.tf
#
# Reading order (this file is written to be read top to bottom):
#   1. VPC, subnets, gateways, routing        -- the Day 02 pattern, rebuilt
#   2. Security groups                        -- ALB -> app, nothing else
#   3. IAM instance profile                   -- SSM, never SSH keys
#   4. Launch template                        -- IMDSv2, encrypted gp3, userdata
#   5. Application Load Balancer + target group + listeners
#   6. Auto Scaling Group                     -- the self-healing bit
#   7. Scaling policies                       -- target tracking + step
#   8. CloudWatch alarms
#   9. Deliberately broken examples           -- gated behind a variable
###############################################################################

###############################################################################
# 1. NETWORK
#
# Day 03 builds its own VPC. It does NOT read Day 02's state.
# Reason: a self-contained lab can be destroyed in one command without
# wondering whether you just deleted something Day 02 still needs. Coupling
# labs through remote state is a great way to teach people to fear terraform.
###############################################################################

resource "aws_vpc" "main" {
  cidr_block = var.vpc_cidr

  # Both required for the private DNS names that ALB target registration and
  # SSM rely on. Forgetting enable_dns_hostnames is a classic 30-minute
  # debugging session.
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${local.prefix}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.prefix}-igw" }
}

# ---------------------------------------------------------------------------
# Public subnets — one per AZ. The ALB lives here.
#
# cidrsubnet(10.30.0.0/16, 8, 0) = 10.30.0.0/24
# cidrsubnet(10.30.0.0/16, 8, 1) = 10.30.1.0/24
# ---------------------------------------------------------------------------
resource "aws_subnet" "public" {
  count = var.az_count

  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone = local.azs[count.index]

  # The ALB needs a public IP; it gets one from the ELB service, not from this
  # setting. This is on because it's a genuinely public subnet, and because
  # turning it off here would surprise anyone who launches a bastion later.
  map_public_ip_on_launch = true

  tags = {
    Name = "${local.prefix}-public-${local.azs[count.index]}"
    Tier = "public"
    # Required if you ever put an EKS cluster in this VPC. Costs nothing now.
    "kubernetes.io/role/elb" = "1"
  }
}

# ---------------------------------------------------------------------------
# Private app subnets — one per AZ, offset by 10 so the ranges read clearly.
# cidrsubnet(10.30.0.0/16, 8, 10) = 10.30.10.0/24
# ---------------------------------------------------------------------------
resource "aws_subnet" "app" {
  count = var.az_count

  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  availability_zone = local.azs[count.index]

  map_public_ip_on_launch = false

  tags = {
    Name                              = "${local.prefix}-app-${local.azs[count.index]}"
    Tier                              = "private-app"
    "kubernetes.io/role/internal-elb" = "1"
  }
}

# ---------------------------------------------------------------------------
# NAT Gateway — ONE, shared, in the first public subnet.
#
# Production would use one per AZ so an AZ failure doesn't take out egress for
# the surviving AZ. That triples the cost ($32.40 -> $97.20/month) for a lab
# you're deleting today, so: one. Know the trade-off, state it in interviews.
# ---------------------------------------------------------------------------
resource "aws_eip" "nat" {
  count  = var.enable_nat_gateway ? 1 : 0
  domain = "vpc"
  tags   = { Name = "${local.prefix}-nat-eip" }

  depends_on = [aws_internet_gateway.main]
}

resource "aws_nat_gateway" "main" {
  count = var.enable_nat_gateway ? 1 : 0

  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public[0].id

  tags = { Name = "${local.prefix}-nat" }

  # Without this, Terraform occasionally tries to create the NAT before the IGW
  # route exists and the apply fails with a useless error.
  depends_on = [aws_internet_gateway.main]
}

# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.prefix}-rt-public" }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}

resource "aws_route_table_association" "public" {
  count = var.az_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# One private route table per AZ. With a single shared NAT they'd all point at
# the same target, but keeping them separate means switching to per-AZ NAT
# later is a one-line change instead of a refactor.
resource "aws_route_table" "app" {
  count = var.az_count

  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.prefix}-rt-app-${local.azs[count.index]}" }
}

resource "aws_route" "app_nat" {
  count = var.enable_nat_gateway ? var.az_count : 0

  route_table_id         = aws_route_table.app[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.main[0].id
}

resource "aws_route_table_association" "app" {
  count = var.az_count

  subnet_id      = aws_subnet.app[count.index].id
  route_table_id = aws_route_table.app[count.index].id
}

###############################################################################
# 2. SECURITY GROUPS
#
# The pattern: the app SG allows traffic FROM the ALB SG by reference, not by
# CIDR. Referencing the SG means the rule stays correct no matter how the
# subnets change, and it makes the intent readable in the console.
###############################################################################

resource "aws_security_group" "alb" {
  name        = "${local.prefix}-alb-sg"
  description = "Ingress from the internet to the ALB on 80/443"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.prefix}-alb-sg" }

  lifecycle { create_before_destroy = true }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from allowed CIDR"
  cidr_ipv4         = var.allowed_ingress_cidr
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  count = var.acm_certificate_arn != "" ? 1 : 0

  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from allowed CIDR"
  cidr_ipv4         = var.allowed_ingress_cidr
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "alb_all" {
  security_group_id = aws_security_group.alb.id
  description       = "ALB to targets"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_security_group" "app" {
  name        = "${local.prefix}-app-sg"
  description = "App tier — accepts traffic only from the ALB security group"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.prefix}-app-sg" }

  lifecycle { create_before_destroy = true }
}

# THIS is the line that matters. referenced_security_group_id, not cidr_ipv4.
# No instance in this VPC can reach the app tier on port 80 unless it is the
# load balancer. Note there is deliberately NO port 22 rule anywhere — we use
# SSM Session Manager instead, so there is no SSH key to lose and no port 22
# to accidentally expose.
resource "aws_vpc_security_group_ingress_rule" "app_from_alb" {
  security_group_id            = aws_security_group.app.id
  description                  = "HTTP from the ALB only"
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 80
  to_port                      = 80
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "app_all" {
  security_group_id = aws_security_group.app.id
  description       = "Outbound for package installs and SSM"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

###############################################################################
# 3. IAM INSTANCE PROFILE
#
# AmazonSSMManagedInstanceCore lets you get a shell on an instance with
# `aws ssm start-session` — no SSH key, no bastion, no port 22, and every
# session is logged in CloudTrail. There is no good reason to still be
# distributing .pem files in 2026.
###############################################################################

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

  tags = { Name = "${local.prefix}-app-role" }
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Lets the instance push its own custom metrics and write logs. Scoped to
# CloudWatch actions that have no resource-level permissions available.
resource "aws_iam_role_policy" "cloudwatch" {
  name = "${local.prefix}-cloudwatch"
  role = aws_iam_role.app.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "cloudwatch:PutMetricData",
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogStreams"
      ]
      Resource = "*"
    }]
  })
}

resource "aws_iam_instance_profile" "app" {
  name = "${local.prefix}-app-profile-${local.suffix}"
  role = aws_iam_role.app.name
}

###############################################################################
# 4. LAUNCH TEMPLATE
#
# The five things every launch template must have:
#   1. metadata_options with http_tokens = "required"   (IMDSv2)
#   2. encrypted gp3 root volume
#   3. an instance profile (SSM, not SSH)
#   4. monitoring (1-minute metrics)
#   5. tag_specifications — because default_tags does NOT reach instances the
#      ASG launches. The ASG creates them, not Terraform.
###############################################################################

locals {
  # Userdata. Note: NO ${...} anywhere in this heredoc — Terraform would try to
  # interpolate it. Shell variables use the $NAME form, and command
  # substitution uses $(...), neither of which Terraform touches.
  user_data = <<-USERDATA
    #!/bin/bash
    set -euo pipefail
    exec > >(tee /var/log/cbc-bootstrap.log | logger -t cbc-bootstrap) 2>&1

    echo "=== CareerByteCode Day 03 bootstrap starting ==="

    TOKEN=$(curl -sf -X PUT "http://169.254.169.254/latest/api/token" \
      -H "X-aws-ec2-metadata-token-ttl-seconds: 300" || echo "")

    imds() {
      curl -sf -H "X-aws-ec2-metadata-token: $TOKEN" \
        "http://169.254.169.254/latest/meta-data/$1" 2>/dev/null || echo "unknown"
    }

    INSTANCE_ID=$(imds instance-id)
    AZ=$(imds placement/availability-zone)
    ITYPE=$(imds instance-type)
    LOCAL_IP=$(imds local-ipv4)

    # dnf needs egress. With enable_nat_gateway = false this fails, which is why
    # the whole install is wrapped in a conditional and we fall back to a
    # busybox-free static server using only what AL2023 ships.
    if dnf install -y httpd 2>/dev/null; then
      HAVE_HTTPD=yes
    else
      echo "WARNING: httpd install failed (no egress?). Falling back to python http.server."
      HAVE_HTTPD=no
    fi

    # stress-ng lives in EPEL and may not resolve. It is only used for the
    # scale-out demo, so a failure here must NOT break the web server —
    # hence a separate call with its own guard. Bundling it into the dnf
    # line above would take httpd down with it.
    dnf install -y stress-ng 2>/dev/null || \
      echo "NOTE: stress-ng unavailable. Use the shell busy-loop for the load test."

    mkdir -p /var/www/html

    # The health endpoint. This is a REAL endpoint, not "/".
    # In a real app this would check the DB pool, cache connectivity, and any
    # downstream dependency. Returning 200 from nginx while your connection
    # pool is exhausted is how you build self-healing that never heals.
    cat > /var/www/html/health <<'HEALTH'
    OK
    HEALTH

    cat > /var/www/html/index.html <<INDEX
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8">
      <title>CareerByteCode Day 03</title>
      <style>
        body { font-family: -apple-system, system-ui, sans-serif; margin: 0;
               background: #0b1220; color: #e6edf7; display: grid;
               place-items: center; min-height: 100vh; }
        .card { background: #131c2e; border: 1px solid #22314d; border-radius: 14px;
                padding: 2.5rem 3rem; box-shadow: 0 10px 40px rgba(0,0,0,.4); }
        h1 { margin: 0 0 .25rem; font-size: 1.4rem; color: #4d8dff; }
        p.sub { margin: 0 0 1.5rem; color: #8fa3c4; font-size: .9rem; }
        table { border-collapse: collapse; }
        td { padding: .4rem 1.2rem .4rem 0; font-size: .95rem; }
        td.k { color: #8fa3c4; }
        td.v { font-family: ui-monospace, monospace; color: #7ee0a1; }
      </style>
    </head>
    <body>
      <div class="card">
        <h1>Day 03 — Compute Architecture &amp; Intelligent Scaling</h1>
        <p class="sub">If you refresh and this changes, the ALB is load balancing.</p>
        <table>
          <tr><td class="k">Instance ID</td><td class="v">$INSTANCE_ID</td></tr>
          <tr><td class="k">Availability Zone</td><td class="v">$AZ</td></tr>
          <tr><td class="k">Instance type</td><td class="v">$ITYPE</td></tr>
          <tr><td class="k">Private IP</td><td class="v">$LOCAL_IP</td></tr>
        </table>
      </div>
    </body>
    </html>
    INDEX

    if [ "$HAVE_HTTPD" = "yes" ]; then
      systemctl enable --now httpd
    else
      cd /var/www/html
      nohup python3 -m http.server 80 >/var/log/cbc-http.log 2>&1 &
    fi

    echo "=== bootstrap complete on $INSTANCE_ID in $AZ ==="
  USERDATA
}

resource "aws_launch_template" "app" {
  name_prefix   = "${local.prefix}-app-"
  description   = "Day 03 app tier — IMDSv2 enforced, encrypted gp3 root"
  image_id      = data.aws_ami.al2023.id
  instance_type = var.instance_type

  # user_data must be base64. Terraform will not do this for you.
  user_data = base64encode(local.user_data)

  iam_instance_profile {
    name = aws_iam_instance_profile.app.name
  }

  vpc_security_group_ids = [aws_security_group.app.id]

  # ---- THE most important block in this file --------------------------------
  # http_tokens = "required" means IMDSv2 only. An SSRF bug in your app can do
  # a GET; it almost never can do the PUT needed to get a session token. This
  # is the difference between "someone found a bug" and "someone has your IAM
  # credentials". Capital One, 2019.
  #
  # hop_limit = 1 stops a container on the host from reaching IMDS through the
  # Docker bridge and stealing the host's role.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "enabled"
  }
  # ---------------------------------------------------------------------------

  block_device_mappings {
    device_name = "/dev/xvda"

    ebs {
      volume_size           = var.root_volume_size_gb
      volume_type           = "gp3"
      encrypted             = true # costs nothing, fails every audit without it
      delete_on_termination = true
    }
  }

  monitoring {
    enabled = var.enable_detailed_monitoring
  }

  # default_tags does NOT reach instances launched by the ASG, because the ASG
  # creates them — Terraform doesn't. These blocks are how the tags get there.
  tag_specifications {
    resource_type = "instance"
    tags = {
      Name      = "${local.prefix}-app"
      Project   = "aws-aiops-bootcamp"
      Day       = "03"
      ManagedBy = "terraform"
      Owner     = var.owner
    }
  }

  tag_specifications {
    resource_type = "volume"
    tags = {
      Name      = "${local.prefix}-app-root"
      Project   = "aws-aiops-bootcamp"
      Day       = "03"
      ManagedBy = "terraform"
      Owner     = var.owner
    }
  }

  tags = { Name = "${local.prefix}-app-lt" }

  lifecycle { create_before_destroy = true }
}

###############################################################################
# 5. APPLICATION LOAD BALANCER
###############################################################################

resource "aws_lb" "main" {
  name               = "${local.prefix}-alb-${local.suffix}"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  # Stops an accidental `terraform destroy` from... actually, no. Set to false
  # deliberately, because this lab MUST be destroyable in one command and a
  # forgotten deletion_protection flag is how people end up with a $73/month
  # surprise. In production, set this to true.
  enable_deletion_protection = false

  # ALB cross-zone load balancing is always on and free. You cannot turn it off
  # and you would not want to. NLB is the one where this is a decision.
  idle_timeout = 60

  drop_invalid_header_fields = true

  dynamic "access_logs" {
    for_each = var.enable_alb_access_logs ? [1] : []
    content {
      bucket  = aws_s3_bucket.alb_logs[0].id
      prefix  = "alb"
      enabled = true
    }
  }

  tags = { Name = "${local.prefix}-alb" }
}

resource "aws_lb_target_group" "app" {
  name        = "${local.prefix}-tg-${local.suffix}"
  port        = 80
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "instance"

  # How long to let in-flight requests finish before killing a draining target.
  # AWS defaults this to 300s, which makes every deploy and scale-in feel
  # broken. 30s is right for a stateless HTTP app.
  deregistration_delay = var.alb_deregistration_delay

  health_check {
    enabled             = true
    path                = "/health" # a real endpoint, NOT "/"
    protocol            = "HTTP"
    port                = "traffic-port"
    interval            = 15
    timeout             = 5 # MUST be less than interval
    healthy_threshold   = 2
    unhealthy_threshold = 2
    matcher             = "200"
  }

  stickiness {
    type            = "lb_cookie"
    cookie_duration = 3600
    # Off. Turn it on only if your app keeps session state on the instance —
    # and if it does, that is technical debt. Stickiness breaks even load
    # distribution and drops sessions on every scale-in.
    enabled = false
  }

  tags = { Name = "${local.prefix}-tg" }

  lifecycle { create_before_destroy = true }
}

# ---------------------------------------------------------------------------
# Listeners.
#
# With no ACM certificate (the default), HTTP:80 forwards to the target group.
# That is finding ASG-009 and it is deliberate — the auditor needs something
# real to catch.
#
# Set acm_certificate_arn and you get the correct production shape:
# HTTP:80 -> 301 redirect -> HTTPS:443 -> target group.
# ---------------------------------------------------------------------------

resource "aws_lb_listener" "http_redirect" {
  count = var.acm_certificate_arn != "" ? 1 : 0

  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }

  tags = { Name = "${local.prefix}-listener-http-redirect" }
}

resource "aws_lb_listener" "http_forward" {
  count = var.acm_certificate_arn == "" ? 1 : 0

  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }

  tags = { Name = "${local.prefix}-listener-http-forward" }
}

resource "aws_lb_listener" "https" {
  count = var.acm_certificate_arn != "" ? 1 : 0

  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }

  tags = { Name = "${local.prefix}-listener-https" }
}

# A listener rule, purely so you can see how priority evaluation works.
# Lowest number first, first match wins, default action is the fallback.
resource "aws_lb_listener_rule" "health_direct" {
  listener_arn = var.acm_certificate_arn == "" ? aws_lb_listener.http_forward[0].arn : aws_lb_listener.https[0].arn
  priority     = 100

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }

  condition {
    path_pattern {
      values = ["/health", "/health/*"]
    }
  }

  tags = { Name = "${local.prefix}-rule-health" }
}

# ---------------------------------------------------------------------------
# Optional ALB access-log bucket
# ---------------------------------------------------------------------------
data "aws_elb_service_account" "main" {
  count = var.enable_alb_access_logs ? 1 : 0
}

resource "aws_s3_bucket" "alb_logs" {
  count = var.enable_alb_access_logs ? 1 : 0

  bucket = "${local.prefix}-alb-logs-${local.account_id}-${local.suffix}"

  # force_destroy so `terraform destroy` doesn't fail on a non-empty bucket.
  # NEVER do this in production.
  force_destroy = true

  tags = { Name = "${local.prefix}-alb-logs" }
}

resource "aws_s3_bucket_public_access_block" "alb_logs" {
  count = var.enable_alb_access_logs ? 1 : 0

  bucket                  = aws_s3_bucket.alb_logs[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "alb_logs" {
  count = var.enable_alb_access_logs ? 1 : 0

  bucket = aws_s3_bucket.alb_logs[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = data.aws_elb_service_account.main[0].arn }
      Action    = "s3:PutObject"
      Resource  = "${aws_s3_bucket.alb_logs[0].arn}/alb/AWSLogs/${local.account_id}/*"
    }]
  })
}

###############################################################################
# 6. AUTO SCALING GROUP — the self-healing bit
###############################################################################

resource "aws_autoscaling_group" "app" {
  name = "${local.prefix}-asg-${local.suffix}"

  # min_size = desired = instance_count (default 2).
  # min_size of 1 is not high availability. One instance is one AZ.
  min_size         = var.instance_count
  desired_capacity = var.instance_count
  max_size         = var.asg_max_size

  # EVERY private subnet -> every AZ. This one line is the HA guarantee.
  vpc_zone_identifier = aws_subnet.app[*].id

  target_group_arns = [aws_lb_target_group.app.arn]

  # ---- The setting that actually makes this self-healing -------------------
  # "EC2" (the AWS default) only watches EC2 system/instance status checks.
  # A hung JVM, an OOM-killed process, nginx returning 502 — all of those leave
  # the EC2 status checks green, so the ASG never replaces the instance and it
  # serves errors forever.
  #
  # "ELB" makes the ASG honour the target group's /health check as well.
  health_check_type = "ELB"

  # If this is shorter than boot-to-healthy time you get an infinite
  # launch/terminate loop that bills all night and never converges.
  health_check_grace_period = var.health_check_grace_period
  # -------------------------------------------------------------------------

  launch_template {
    id      = aws_launch_template.app.id
    version = "$Latest"
  }

  # Scale-in order. The default policy retires whichever instance is closest to
  # the next billing hour, which is arbitrary. This ordering makes scale-in
  # double as a slow rolling deploy: oldest launch template version dies first,
  # then oldest instance.
  termination_policies = ["OldestLaunchTemplate", "OldestInstance", "Default"]

  # Replace instances gradually when the launch template changes, keeping at
  # least 50% of capacity in service. Without this, changing the launch
  # template does nothing until instances happen to be replaced.
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 50
      instance_warmup        = var.instance_warmup_seconds
    }
    triggers = ["launch_template"]
  }

  # Wait for instances to actually pass the ELB health check before `apply`
  # reports success. Slower applies, but a green apply means a working service.
  wait_for_capacity_timeout = "10m"
  min_elb_capacity          = var.instance_count

  metrics_granularity = "1Minute"
  enabled_metrics = [
    "GroupMinSize",
    "GroupMaxSize",
    "GroupDesiredCapacity",
    "GroupInServiceInstances",
    "GroupPendingInstances",
    "GroupTerminatingInstances",
    "GroupTotalInstances"
  ]

  tag {
    key                 = "Name"
    value               = "${local.prefix}-asg"
    propagate_at_launch = false
  }

  # desired_capacity is owned by the scaling policies at runtime. Without this,
  # every `terraform apply` in CI resets you to 2 instances — potentially in
  # the middle of your traffic peak.
  lifecycle {
    create_before_destroy = true
    ignore_changes        = [desired_capacity]
  }

  depends_on = [
    aws_route.app_nat,
    aws_iam_role_policy_attachment.ssm
  ]
}

###############################################################################
# 7. SCALING POLICIES
###############################################################################

# ---- Target tracking: the default choice for ~90% of workloads --------------
# You name a metric and a target; AWS creates and manages the CloudWatch alarms
# and holds the target. No thresholds to tune, no cooldown to get wrong.
resource "aws_autoscaling_policy" "cpu_target_tracking" {
  name                   = "${local.prefix}-cpu-target-tracking"
  autoscaling_group_name = aws_autoscaling_group.app.name
  policy_type            = "TargetTrackingScaling"

  # Metrics from instances younger than this are EXCLUDED from the aggregate,
  # so a booting instance's 100% CPU cannot trigger another scale-out.
  # This is warm-up, not cooldown. Know the difference; it gets asked.
  estimated_instance_warmup = var.instance_warmup_seconds

  target_tracking_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ASGAverageCPUUtilization"
    }
    target_value = var.target_cpu_utilization

    # Target tracking will scale OUT and IN by default. Set this to true if
    # scale-in is disruptive (long-running jobs, in-memory state) and you'd
    # rather handle it with a scheduled action.
    disable_scale_in = false
  }
}

# ---- Request-count target tracking: usually a better signal than CPU --------
# CPU is a proxy. Requests-per-target is the thing you actually care about, and
# it reacts before CPU has time to climb. Commented ON here so you can compare
# the two in CloudWatch during the lab.
resource "aws_autoscaling_policy" "request_target_tracking" {
  name                   = "${local.prefix}-request-target-tracking"
  autoscaling_group_name = aws_autoscaling_group.app.name
  policy_type            = "TargetTrackingScaling"

  estimated_instance_warmup = var.instance_warmup_seconds

  target_tracking_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"

      # The resource label format is:
      #   app/<alb-name>/<alb-id>/targetgroup/<tg-name>/<tg-id>
      # arn_suffix gives you exactly those halves. Building this string by hand
      # from the full ARN is a classic 20-minute mistake.
      resource_label = "${aws_lb.main.arn_suffix}/${aws_lb_target_group.app.arn_suffix}"
    }
    target_value = 1000.0
  }
}

# ---- Step scaling: for when target tracking is too gentle -------------------
# Target tracking adds capacity proportionally. Step scaling lets you say
# "10% over target: +1. 30% over: +2. 50% over: +3." Use it for traffic that
# arrives all at once — flash sales, scheduled batch, viral events.
resource "aws_autoscaling_policy" "cpu_step_scale_out" {
  name                   = "${local.prefix}-cpu-step-scale-out"
  autoscaling_group_name = aws_autoscaling_group.app.name
  policy_type            = "StepScaling"
  adjustment_type        = "ChangeInCapacity"

  estimated_instance_warmup = var.instance_warmup_seconds

  # Bounds are relative to the ALARM THRESHOLD, not absolute values.
  # With an alarm at 70%: 0-10 means 70-80%, 10-30 means 80-100%.
  step_adjustment {
    metric_interval_lower_bound = 0
    metric_interval_upper_bound = 10
    scaling_adjustment          = 1
  }

  step_adjustment {
    metric_interval_lower_bound = 10
    scaling_adjustment          = 2
  }
}

###############################################################################
# 8. CLOUDWATCH ALARMS
###############################################################################

resource "aws_cloudwatch_metric_alarm" "high_cpu" {
  alarm_name          = "${local.prefix}-high-cpu"
  alarm_description   = "Fires the step scale-out policy when average CPU exceeds 70% for 2 minutes."
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 2
  threshold           = 70
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.app.name
  }

  alarm_actions = [aws_autoscaling_policy.cpu_step_scale_out.arn]

  tags = { Name = "${local.prefix}-high-cpu" }
}

# This is the alarm that tells you the self-healing FAILED. If healthy hosts
# drops below the minimum, the ASG could not replace instances fast enough —
# capacity shortage, bad AMI, broken userdata, or a launch loop.
resource "aws_cloudwatch_metric_alarm" "unhealthy_hosts" {
  alarm_name          = "${local.prefix}-unhealthy-hosts"
  alarm_description   = "Healthy target count is below the ASG minimum. Self-healing is not keeping up."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HealthyHostCount"
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 2
  threshold           = var.instance_count
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = aws_lb_target_group.app.arn_suffix
  }

  tags = { Name = "${local.prefix}-unhealthy-hosts" }
}

resource "aws_cloudwatch_metric_alarm" "target_5xx" {
  alarm_name          = "${local.prefix}-target-5xx"
  alarm_description   = "Targets are returning 5xx. The instances are up but the app is broken."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
  }

  tags = { Name = "${local.prefix}-target-5xx" }
}

###############################################################################
# 9. DELIBERATELY BROKEN EXAMPLES
#
# Gated behind create_insecure_examples. These exist so ha_audit.py finds real
# misconfigurations in a real account instead of you having to imagine them.
#
# Every one of these is a mistake I have seen in a production account.
###############################################################################

# BROKEN #1: IMDSv1 allowed + unencrypted root volume.
resource "aws_launch_template" "broken" {
  count = var.create_insecure_examples ? 1 : 0

  name_prefix   = "${local.prefix}-broken-"
  description   = "DELIBERATELY BROKEN — IMDSv1 allowed, unencrypted root volume"
  image_id      = data.aws_ami.al2023.id
  instance_type = var.instance_type

  vpc_security_group_ids = [aws_security_group.app.id]

  # ⚠️ WRONG ON PURPOSE. "optional" means IMDSv1 still works, so any SSRF bug
  # in the app hands over this instance's IAM credentials. Detected as ASG-011.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "optional"
    http_put_response_hop_limit = 2
  }

  # ⚠️ WRONG ON PURPOSE. encrypted = false. Costs nothing to fix, fails every
  # compliance framework. Detected as ASG-012.
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = 8
      volume_type           = "gp2" # also outdated: gp3 is cheaper and faster
      encrypted             = false
      delete_on_termination = true
    }
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name      = "${local.prefix}-broken"
      Project   = "aws-aiops-bootcamp"
      Day       = "03"
      ManagedBy = "terraform"
      Owner     = var.owner
    }
  }

  tags = { Name = "${local.prefix}-broken-lt" }
}

# BROKEN #2: single-AZ ASG, min_size 1, EC2 health checks, tiny grace period,
# no scaling policy, no termination policy diversity.
#
# This is the single most common "we have HA" architecture that does not.
resource "aws_autoscaling_group" "broken" {
  count = var.create_insecure_examples ? 1 : 0

  name = "${local.prefix}-broken-asg-${local.suffix}"

  # ⚠️ min_size = 1. One instance. Detected as ASG-001.
  min_size         = 1
  desired_capacity = 1
  # ⚠️ max_size == desired_capacity. The scaling policy — if there were one —
  # could never act. Detected as ASG-001.
  max_size = 1

  # ⚠️ ONE subnet. One AZ. This is not high availability, it is a single point
  # of failure with extra YAML. Detected as ASG-002.
  vpc_zone_identifier = [aws_subnet.app[0].id]

  # ⚠️ "EC2" not "ELB". A hung app is never replaced. Detected as ASG-003.
  health_check_type = "EC2"

  # ⚠️ 30 seconds, shorter than boot time. Detected as ASG-004.
  health_check_grace_period = 30

  # ⚠️ Single default termination policy. Detected as ASG-013.
  termination_policies = ["Default"]

  # ⚠️ No target_group_arns — not behind a load balancer at all.
  # ⚠️ No aws_autoscaling_policy attached. Detected as ASG-005.

  launch_template {
    id      = aws_launch_template.broken[0].id
    version = "$Latest"
  }

  tag {
    key                 = "Name"
    value               = "${local.prefix}-broken-asg"
    propagate_at_launch = false
  }

  tag {
    key                 = "cbc:intentionally-broken"
    value               = "true"
    propagate_at_launch = true
  }

  lifecycle { create_before_destroy = true }
}

# BROKEN #3: an NLB with cross-zone load balancing disabled.
#
# NLB cross-zone is OFF by default. Each AZ's node only sends to targets in its
# own AZ, so an uneven distribution means one instance takes a wildly
# disproportionate share of traffic. People find this during an incident.
# Detected as ASG-010.
#
# An NLB with no listeners and no targets costs the base hourly rate
# (~$0.0225/h, ~$16/month) — hence gated, and hence the loud teardown note.
resource "aws_lb" "broken_nlb" {
  count = var.create_insecure_examples ? 1 : 0

  name               = "${local.prefix}-broken-nlb-${local.suffix}"
  internal           = true
  load_balancer_type = "network"
  subnets            = aws_subnet.app[*].id

  # ⚠️ WRONG ON PURPOSE. Should be true.
  enable_cross_zone_load_balancing = false
  enable_deletion_protection       = false

  tags = {
    Name                       = "${local.prefix}-broken-nlb"
    "cbc:intentionally-broken" = "true"
  }
}

# BROKEN #4: the main ALB's HTTP:80 listener forwards instead of redirecting,
# and there is no HTTPS listener at all — but only when acm_certificate_arn is
# empty, which is the default. See aws_lb_listener.http_forward above.
# Detected as ASG-008 (no HTTPS listener) and ASG-009 (HTTP not redirecting).
