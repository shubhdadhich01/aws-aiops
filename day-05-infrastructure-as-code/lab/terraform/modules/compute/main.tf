###############################################################################
# modules/compute/main.tf — EC2 instances, addressed by name
#
# Default input is an EMPTY MAP. Applied with defaults, this module creates an
# IAM role and nothing else, and costs $0.00. That is not laziness; a module
# whose defaults spend money is a module that will spend money by accident.
###############################################################################

###############################################################################
# 1. AMI lookup
#
# The AMI ID for "Amazon Linux 2023, x86_64" is DIFFERENT IN EVERY REGION and
# changes every time AWS publishes a new build. Hardcoding one is the classic
# way to write a module that works in us-east-1 and mysteriously fails in
# eu-west-1 with "InvalidAMIID.NotFound".
#
# The SSM public parameter always resolves to the current release.
###############################################################################

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  ami_id = nonsensitive(data.aws_ssm_parameter.al2023.value)

  # Rough on-demand prices, us-east-1, USD/hour. Not authoritative — a static
  # table in a module WILL drift from reality. It is here so the module can
  # produce a cost estimate at plan time, which is worth more than a precise
  # number you only get after the fact. Anything not listed falls back to the
  # t3.micro rate and is flagged as an estimate.
  hourly_prices = {
    "t3.micro"  = 0.0104
    "t3.small"  = 0.0208
    "t3.medium" = 0.0416
    "t3.large"  = 0.0832
    "t4g.micro" = 0.0084
    "t4g.small" = 0.0168
    "m5.large"  = 0.0960
    "c6i.large" = 0.0850
  }

  monthly_compute_cost = sum(concat(
    [0.0],
    [
      for name, cfg in var.instances :
      lookup(local.hourly_prices, cfg.instance_type, 0.0104) * 730.0
    ]
  ))

  monthly_storage_cost = sum(concat(
    [0.0],
    [for name, cfg in var.instances : cfg.root_volume_gb * 0.08]
  ))

  # $0.005/hour per public IPv4 address, charged since February 2024 whether
  # the address is attached or not.
  monthly_public_ip_cost = var.associate_public_ip ? length(var.instances) * 0.005 * 730.0 : 0.0
}

###############################################################################
# 2. Instance profile
#
# Created unconditionally, even when var.instances is empty. It costs nothing,
# and creating it up front means adding the first instance later does not
# require an IAM change in the same apply as a compute change — which is one
# fewer thing to explain in a change review.
###############################################################################

data "aws_iam_policy_document" "assume" {
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
  name               = "${var.name_prefix}-ec2"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "ssm" {
  count = var.enable_ssm ? 1 : 0

  role = aws_iam_role.instance.name
  # AWS-managed. Grants exactly what Session Manager needs and nothing more.
  # Writing your own equivalent is a fine exercise and a poor use of an
  # afternoon.
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "instance" {
  name = "${var.name_prefix}-ec2"
  role = aws_iam_role.instance.name
}

###############################################################################
# 3. Instances
#
# for_each over the map. each.key is the logical name, each.value the config.
# Adding "worker-03" to the map adds one instance; removing "worker-01"
# removes exactly that one. No renumbering, no collateral replacement.
###############################################################################

resource "aws_instance" "this" {
  for_each = var.instances

  ami           = local.ami_id
  instance_type = each.value.instance_type

  # Fails loudly at plan time if the caller asks for an AZ that has no subnet,
  # instead of silently landing the instance somewhere unexpected.
  subnet_id = var.subnet_ids[each.value.availability_zone]

  vpc_security_group_ids      = var.security_group_ids
  iam_instance_profile        = aws_iam_instance_profile.instance.name
  associate_public_ip_address = var.associate_public_ip
  user_data                   = var.user_data != "" ? var.user_data : null

  root_block_device {
    volume_size = each.value.root_volume_gb
    # gp3 is cheaper than gp2 for the same size and gives 3,000 IOPS baseline
    # for free. There has been no reason to launch a new gp2 volume since
    # December 2020, and estates are still full of them.
    volume_type = "gp3"
    encrypted   = var.root_volume_encrypted

    # Without this the root volume SURVIVES instance termination as an
    # unattached snapshot-less volume, billed at $0.08/GB-month forever. It
    # defaults to true for the root device, and to FALSE for every additional
    # volume, which is the trap.
    delete_on_termination = true
  }

  metadata_options {
    http_endpoint = "enabled"
    # IMDSv2 required. IMDSv1 is the token-less version that made SSRF into
    # credential theft in a long list of well-known breaches. "optional" means
    # IMDSv1 still works, which means it is not required at all.
    http_tokens = "required"
    # A hop limit of 1 stops a container on the host from reaching the
    # metadata service through the Docker bridge.
    http_put_response_hop_limit = 1
  }

  lifecycle {
    # The AMI ID changes every time AWS publishes a new Amazon Linux build.
    # Without this, an unrelated `terraform apply` three weeks from now
    # proposes to REPLACE every running instance because the data source
    # resolved to a newer image.
    #
    # This is the correct, deliberate use of ignore_changes: you have decided
    # that AMI updates happen through a controlled rebuild, not as a surprise
    # in someone else's plan. It is NOT a way to silence a diff you do not
    # understand — that use is how configuration quietly stops matching code.
    ignore_changes = [ami]
  }

  tags = {
    Name = "${var.name_prefix}-${each.key}"
    Role = each.key
  }
}
