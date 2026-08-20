###############################################################################
#                    ██  WRONG ON PURPOSE  ██
#
# bad-examples/resources.tf
#
# FAULTS IN THIS FILE:
#   IAC-009  0.0.0.0/0 in a security group INGRESS rule               HIGH
#   IAC-013  stateful resource (S3) with no prevent_destroy           MEDIUM
#   IAC-014  declared resource missing required tags        MEDIUM  (x2)
#   IAC-016  count used where for_each belongs                        LOW
###############################################################################

# IAC-013 + IAC-016 + IAC-014, all in one resource, all different mistakes.
resource "aws_s3_bucket" "reports" {
  # IAC-016 — count over a multi-element list.
  #
  # Terraform addresses these by POSITION: aws_s3_bucket.reports[0], [1], [2].
  # Delete "reports-alpha" from the middle of the list and Terraform does not
  # see "one bucket removed". It sees [0] changed name, [1] changed name, and
  # [2] gone — so it plans to DESTROY AND RECREATE all three, including the
  # two you never touched. For an S3 bucket, "recreate" means the data is
  # gone.
  #
  # for_each addresses by KEY:
  #     for_each = toset(var.report_bucket_names)
  #     bucket   = each.value
  # Now removing one key removes exactly one bucket and nothing else in the
  # plan moves.
  #
  # count is correct for exactly one shape: `count = var.enabled ? 1 : 0`.
  # The moment the number can exceed one, you want for_each.
  count = length(var.report_bucket_names)

  bucket = var.report_bucket_names[count.index]

  # IAC-013 — no lifecycle { prevent_destroy = true } on a stateful resource.
  #
  # Nothing stands between `terraform destroy` and the data. Every S3 bucket,
  # RDS instance and DynamoDB table that holds something you cannot rebuild
  # from code deserves this block. It makes destroy FAIL, loudly, at plan
  # time — which is annoying exactly once, in the situation where it saves
  # you.

  # IAC-014 — no tags, in a directory whose provider sets no default_tags.
  #
  # Untagged resources cannot be attributed in Cost Explorer, cannot be found
  # by an owner, and cannot be safely cleaned up, because nobody can prove
  # whose they are. Every long-lived AWS account has a pile of these and a
  # standing agenda item about them.
}

# IAC-009 + IAC-014.
resource "aws_security_group" "wide_open" {
  name        = "cbc-day05-bad-wide-open"
  description = "Deliberately wide open. Applied by nothing."
  vpc_id      = var.vpc_id

  ingress {
    description = "SSH from the entire internet"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    # IAC-009 — 0.0.0.0/0 on an INGRESS rule.
    #
    # Port 22 open to the world is scanned within minutes of existing. Not
    # hours — the internet-wide scanners run continuously and a new public
    # IPv4 gets its first credential-stuffing attempt almost immediately.
    #
    # Note that the check looks at INGRESS only. Egress to 0.0.0.0/0 is
    # normal and present in the reference modules; a check that flagged every
    # default egress rule in the account would fire so often that people
    # would stop reading the output, and a check nobody reads is worse than
    # no check.
    #
    # The fix is not "narrow it to the office IP". The fix is Session Manager
    # and no ingress rule at all — see modules/compute, which opens nothing.
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # IAC-014 — no tags again.
}
