###############################################################################
# Day 07 — Enterprise Cloud Security
# main.tf — the detection stack
#
# This file teaches as it goes. Read it top to bottom before you apply; the
# comments are the lesson and the resources are the exercise.
#
# WHAT GETS BUILT (CP1 — detection and evidence)
#
#   CloudTrail (multi-region, validated) ──> S3 bucket (versioned, encrypted,
#         │                                            public-access-blocked)
#         └──> GuardDuty ──┬──> Security Hub ──> SNS ──> email
#                          └──> (CP2: EventBridge ──> responder ──> contain)
#
#   Secrets Manager: one secret with rotation, one deliberately without
#   A quarantine security group, built now so it exists BEFORE it is needed
#
#   Plus, when create_insecure_examples = true, the same architecture built
#   badly, for sec_audit.py to tear apart.
#
# THE ARGUMENT THIS DAY MAKES
#
#   An automated response is a decision you are making now, to be executed
#   later, by nobody, on evidence that might be wrong.
#
#   Everything in this file is the part that only OBSERVES. It is deliberately
#   first, and it is deliberately the larger half. Detection is cheap to get
#   wrong in one direction — a missed finding is a bad day — and the response
#   path in CP2 is expensive to get wrong in the other, because it changes your
#   account without asking.
#
#   Read section 3's comments on severity before you read anything in CP2.
#   Every bad automated-response design starts with treating a severity score
#   as a confidence score.
#
# COST: usage-based and mostly invisible in a plan. See outputs.tf
# `cost_breakdown` and `silent_cost_growth`, and read them before you enable
# data events or S3 protection.
###############################################################################


###############################################################################
# 1. THE TRAIL BUCKET — evidence has to live somewhere defensible
#
# This bucket is not "log storage". It is the thing you will be asked to
# produce during an investigation, and every property below exists to make an
# answer possible.
#
#   VERSIONING       So a deleted or overwritten object is recoverable. An
#                    attacker with s3:PutObject on this bucket can otherwise
#                    replace a log file with a shorter one and you cannot prove
#                    it happened. Absence is check SEC-009.
#   PUBLIC ACCESS    All four blocks on. A publicly readable trail bucket is a
#   BLOCK            complete map of your account's control plane, published.
#   ENCRYPTION       SSE-S3 at minimum. Use SSE-KMS with a customer-managed key
#                    when you need revocation and an auditable Decrypt trail —
#                    the argument is the same one Day 04 made about the $1 key.
#   OWNERSHIP        BucketOwnerEnforced, which disables ACLs entirely. This is
#                    the modern default and it removes a whole category of
#                    cross-account confusion.
#   LIFECYCLE        Because CloudTrail objects accumulate forever otherwise —
#                    the same shape as Day 06's unretained log groups, in a
#                    different service.
#
# THE SMALL CHICKEN-AND-EGG: the bucket policy has to name the trail ARN, and
# the trail has to name the bucket. Terraform can only resolve one direction,
# so the policy uses an ARN CONSTRUCTED in providers.tf rather than a reference
# to the trail resource. Referencing the trail here would create a cycle.
###############################################################################

resource "aws_s3_bucket" "trail" {
  bucket = local.trail_bucket_name

  # A trail bucket with force_destroy = true is a trail bucket somebody can
  # empty with one `terraform destroy`. That is convenient for a lab and it is
  # the wrong default for evidence, so it is stated rather than assumed: the
  # teardown checklist empties it explicitly and tells you what you are doing.
  force_destroy = true

  tags = {
    Name    = "${local.prefix}-trail"
    Role    = "audit-evidence"
    Retains = "cloudtrail"
  }
}

resource "aws_s3_bucket_ownership_controls" "trail" {
  bucket = aws_s3_bucket.trail.id

  rule {
    # ACLs disabled entirely. CloudTrail still delivers correctly — it uses
    # bucket-owner-full-control semantics, which BucketOwnerEnforced makes
    # automatic — and you lose a class of misconfiguration you can no longer
    # make by accident.
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "trail" {
  bucket = aws_s3_bucket.trail.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "trail" {
  bucket = aws_s3_bucket.trail.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "trail" {
  bucket = aws_s3_bucket.trail.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    # Bucket keys cut KMS request costs by up to 99% when you move to SSE-KMS.
    # Harmless with AES256 and it means the switch later is one line.
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "trail" {
  bucket = aws_s3_bucket.trail.id

  depends_on = [aws_s3_bucket_versioning.trail]

  rule {
    id     = "expire-trail-objects"
    status = "Enabled"

    filter {}

    expiration {
      days = var.trail_log_retention_days
    }

    # Versioning is on, so expiring the current version only hides it — the
    # noncurrent version keeps billing. Both rules or neither; this is the
    # single most common reason an S3 lifecycle policy "does not work".
    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# ---------------------------------------------------------------------------
# 1a. The bucket policy CloudTrail requires.
#
# Two statements, and both are mandatory:
#
#   GetBucketAcl on the BUCKET  — CloudTrail checks it can write before it
#                                 starts, and the check is a GetBucketAcl.
#                                 Omit this and trail creation fails with
#                                 "InsufficientS3BucketPolicyException", which
#                                 does not mention GetBucketAcl.
#   PutObject on the PREFIX     — scoped to AWSLogs/<account-id>/*, not to the
#                                 whole bucket.
#
# `aws:SourceArn` on both is what stops the confused-deputy problem: without
# it, ANY account's CloudTrail could be pointed at your bucket and write into
# it. That is not theoretical — it is the same class of issue AWS added
# SourceArn conditions across the whole service surface to close.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "trail_bucket" {
  statement {
    sid    = "AWSCloudTrailAclCheck"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.trail.arn]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
  }

  statement {
    sid    = "AWSCloudTrailWrite"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.trail.arn}/AWSLogs/${local.account_id}/*"]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
  }

  # Deny anything that is not TLS. A short statement that closes a real gap
  # and that a lot of auditors look for by name.
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.trail.arn,
      "${aws_s3_bucket.trail.arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "trail" {
  bucket = aws_s3_bucket.trail.id
  policy = data.aws_iam_policy_document.trail_bucket.json

  # The public access block must exist first, or there is a window in which a
  # bucket policy is attached to a bucket that has not yet been locked down.
  depends_on = [aws_s3_bucket_public_access_block.trail]
}


###############################################################################
# 2. CLOUDTRAIL — the difference between logging and evidence
#
# Almost every account has a trail. Far fewer have evidence.
#
#   LOG FILE VALIDATION is what makes the difference. With it on, CloudTrail
#   writes a signed digest file every hour listing the log files delivered and
#   their hashes. `aws cloudtrail validate-logs` then proves that no file was
#   modified or deleted since delivery.
#
#   That matters exactly once, and then completely: during an incident where
#   the question is whether an attacker with S3 write access edited the trail
#   to remove their own activity. Without validation you cannot answer. With
#   it you can, and the answer holds up.
#
#   It is free. Its absence is check SEC-007.
#
#   MULTI-REGION is the other half. An attacker with credentials does not
#   politely operate in your primary region — creating an instance in
#   ap-south-1 is exactly as easy for them. A single-region trail records none
#   of it. That is check SEC-006.
#
# COST: the FIRST trail delivering management events is free per account,
# including multi-region. A SECOND trail delivering the same events is ~$2.00
# per 100,000 events, which is why "the security team wants their own trail" is
# a more expensive request than it sounds. Section 10 builds one deliberately.
#
# DATA EVENTS are off by default here and the variable description explains
# why at length. Short version: they are generated at application volume rather
# than human volume, and there is no free allowance.
###############################################################################

resource "aws_cloudtrail" "main" {
  name           = local.trail_name
  s3_bucket_name = aws_s3_bucket.trail.id

  is_multi_region_trail = var.cloudtrail_multi_region

  # Global service events — IAM, STS, CloudFront — are emitted in us-east-1
  # regardless of where you are. Without this, a trail outside us-east-1
  # records no IAM activity at all, which is the activity you most want.
  include_global_service_events = true

  enable_log_file_validation = var.cloudtrail_enable_log_file_validation
  enable_logging             = true

  dynamic "event_selector" {
    for_each = var.cloudtrail_enable_data_events ? [1] : []

    content {
      read_write_type           = "All"
      include_management_events = true

      # Deliberately scoped to ONE bucket, not to "arn:aws:s3:::*/*". The
      # wildcard form is a single character longer and is the difference
      # between a few dollars and a four-figure surprise.
      data_resource {
        type   = "AWS::S3::Object"
        values = ["${aws_s3_bucket.trail.arn}/"]
      }
    }
  }

  depends_on = [aws_s3_bucket_policy.trail]

  tags = {
    Name = "${local.prefix}-trail"
    Role = "audit-evidence"
  }
}


###############################################################################
# 3. GUARDDUTY — and the sentence the whole day turns on
#
# ================== SEVERITY IS IMPACT, NOT CONFIDENCE ==================
#
# GuardDuty severity is a number from 1 to 8.9, bucketed:
#
#   Low       1.0 - 3.9
#   Medium    4.0 - 6.9
#   High      7.0 - 8.9
#
# It scores HOW BAD THIS WOULD BE IF IT IS REAL. It does not score how likely
# it is to be real. Those are different questions and conflating them is the
# root of almost every bad automated-response design.
#
# A HIGH finding can be, and routinely is:
#   * your own penetration test
#   * a vulnerability scanner your security team runs on a schedule
#   * a security researcher probing a public endpoint
#   * a developer who ran something odd from a coffee shop
#   * a genuine compromise
#
# All five produce the same severity. If your automation triggers on
# `severity >= 7`, all five get the same response — and four of them are your
# own people, which means four of them are an outage you caused.
#
# What actually correlates with confidence: the finding TYPE. `UnauthorizedAccess:
# EC2/SSHBruteForce` is noisy and frequently benign. `CryptoCurrencyMining:
# EC2/BitcoinTool.B!DNS` is rarely a false positive. Automation belongs on
# specific types you have decided about individually — a written allow-list of
# finding types — never on a severity threshold. CP2 builds that allow-list and
# check SEC-005 is what fires when it is missing.
#
# ============================== SAMPLE FINDINGS ==============================
#
# `aws guardduty create-sample-findings` generates one finding of each type on
# demand, which is what makes this lab possible without attacking anything.
#
# Be clear about how they differ from real ones, because the lab depends on it:
#   * the resource identifiers are FAKE — instance i-99999999, and so on
#   * they arrive instantly rather than after a detection window
#   * their titles are prefixed with "[SAMPLE]"
#
# That last one is genuinely useful and genuinely dangerous. Useful, because a
# responder can be told to recognise samples and act in dry-run. Dangerous,
# because a responder that filters on the prefix in the WRONG direction does
# nothing at all in production and looks perfectly healthy in the lab. Step 4
# of the lab makes you check which way round yours is.
###############################################################################

resource "aws_guardduty_detector" "main" {
  count = var.enable_guardduty ? 1 : 0

  enable                       = true
  finding_publishing_frequency = var.guardduty_finding_publishing_frequency

  tags = {
    Name = "${local.prefix}-detector"
  }
}

# S3 protection, as a separate feature resource rather than the deprecated
# `datasources` block inside the detector. The provider still accepts the old
# form and will warn; new code should not use it, because features are added
# to this API far more often than the detector schema changes.
resource "aws_guardduty_detector_feature" "s3_protection" {
  count = var.enable_guardduty ? 1 : 0

  detector_id = aws_guardduty_detector.main[0].id
  name        = "S3_DATA_EVENTS"
  status      = var.enable_guardduty_s3_protection ? "ENABLED" : "DISABLED"
}


###############################################################################
# 4. SECURITY HUB — the aggregator, and the number nobody drives to zero
#
# Security Hub does two separable things:
#
#   INGESTS findings from GuardDuty, Inspector, Macie, Config and anything
#   speaking ASFF, so there is one place to look instead of six.
#
#   RUNS its own compliance checks against enabled standards, producing
#   findings of its own.
#
# The second is where the money and the disillusionment both come from.
#
# Enable every available standard on day one and you get several thousand
# failed controls across sets that overlap heavily — the same "S3 bucket should
# block public access" control appearing three times under three names. The
# result is a compliance percentage nobody believes and nobody will ever drive
# to zero, and a team that learns to scroll past the security dashboard. That
# is a worse outcome than having no dashboard, because it looks like coverage.
#
# ONE standard. Foundational Security Best Practices, because it is the
# broadest and the most directly actionable. Add CIS or PCI when somebody
# actually needs the attestation, and budget real time for suppressing controls
# that do not apply to you.
#
# COST: ~$0.0010 per security check for the first 100,000 per account per
# region per month. Checks are counted per control per resource per day, so the
# number is driven by how many resources you have, not by how many standards
# sound useful.
#
# ORDERING: the GuardDuty-to-Security-Hub integration is enabled automatically
# when both services are on in the same region. It is not a resource you
# create — which is why there is nothing here to wire them together, and why
# people go looking for it.
###############################################################################

resource "aws_securityhub_account" "main" {
  count = var.enable_security_hub ? 1 : 0

  # Deliberately FALSE. The default is to enable every standard AWS considers
  # default, which is exactly the mistake described above. Enabling standards
  # explicitly, one at a time, in the resource below, is the whole point.
  enable_default_standards = false

  # Consolidated control findings: one finding per control rather than one per
  # control per standard. With one standard enabled it changes nothing; the day
  # you add a second it is the difference between 400 findings and 1,200.
  control_finding_generator = "SECURITY_CONTROL"

  auto_enable_controls = true
}

locals {
  security_hub_standard_arns = {
    "aws-foundational-security-best-practices" = "arn:${local.partition}:securityhub:${local.region}::standards/aws-foundational-security-best-practices/v/1.0.0"
    "cis-aws-foundations-benchmark"            = "arn:${local.partition}:securityhub:${local.region}::standards/cis-aws-foundations-benchmark/v/1.4.0"
    "pci-dss"                                  = "arn:${local.partition}:securityhub:${local.region}::standards/pci-dss/v/3.2.1"
  }
}

resource "aws_securityhub_standards_subscription" "enabled" {
  for_each = var.enable_security_hub ? toset(var.security_hub_standards) : toset([])

  standards_arn = local.security_hub_standard_arns[each.value]

  depends_on = [aws_securityhub_account.main]
}


###############################################################################
# 5. SNS — where findings, and later containment actions, are announced
#
# One topic at CP1. CP2 adds a second for containment actions, for the same
# reason Day 06 split alerts from summaries: a notification saying "we detected
# something" and a notification saying "we have changed your production
# account" have different audiences, different urgency and different retention
# requirements.
###############################################################################

resource "aws_sns_topic" "security" {
  name = "${local.prefix}-security-${local.suffix}"

  tags = {
    Name = "${local.prefix}-security"
  }
}

resource "aws_sns_topic_subscription" "security_email" {
  topic_arn = aws_sns_topic.security.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

# The topic policy. Without it, EventBridge cannot publish here — and the
# failure is silent: the rule matches, the target is invoked, the publish is
# denied, and nothing reaches you. EventBridge does not retry into a
# permissions error in a way you will notice.
data "aws_iam_policy_document" "security_topic" {
  statement {
    sid    = "AllowEventBridgePublish"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.security.arn]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "security" {
  arn    = aws_sns_topic.security.arn
  policy = data.aws_iam_policy_document.security_topic.json
}


###############################################################################
# 6. SECRETS MANAGER — and the failure mode that looks like success
#
# Rotation is not valuable because of the interval. It is valuable because it
# WORKS AND IS VERIFIED.
#
# The failure mode worth knowing: rotation is configured, the rotation Lambda
# throws on every invocation, and the console shows a rotation schedule with a
# next-rotation date that keeps moving. Nothing is red. `RotationEnabled` is
# `true`. And the credential has not changed since March.
#
# The tell is `LastRotatedDate`. If it is absent, or far older than
# `RotationRules.AutomaticallyAfterDays` implies it should be, rotation is
# broken — and that is check SEC-011, which is the check most likely to fire in
# a real account that believes it is fine.
#
# Where to look:
#   aws secretsmanager describe-secret --secret-id <id> \
#     --query '{Enabled:RotationEnabled,Last:LastRotatedDate,Rules:RotationRules}'
#
# THE DAY 06 CALLBACK, and it is the important one on this page:
#
#   Rotating a credential does nothing if the old value is sitting in a
#   CloudWatch log group with no retention.
#
#   Day 06's OBS-011 was about log content reaching a model. This is the same
#   data, reached from the other side: an application that logged its
#   connection string once, on startup, in March, into a log group set to Never
#   expire. The secret has rotated eleven times since. The March value is still
#   there, still readable by anyone with CloudWatch read access, and rotation
#   has quietly given you eleven credentials to worry about instead of one.
#
#   Rotation is not a substitute for not logging it. Check both.
#
# COST: ~$0.40 per secret per month plus ~$0.05 per 10,000 API calls. Two
# secrets here, so ~$0.80/month — the largest predictable line item on Day 07.
###############################################################################

resource "aws_secretsmanager_secret" "app" {
  name        = "${local.prefix}/app-credentials-${local.suffix}"
  description = "Managed application credential. Rotation is configured; the lab verifies it actually ran."

  recovery_window_in_days = var.secret_recovery_window_days

  tags = {
    Name     = "${local.prefix}-app-credentials"
    Rotation = "managed"
  }
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id

  # A placeholder value, and worth saying out loud: this value is written into
  # the Terraform STATE FILE in plaintext. Day 05 made this argument at length
  # and it applies with more force to a secret than to anything else. In
  # production the initial value is set outside Terraform — by the rotation
  # Lambda's first run, or by a human with the console — and Terraform manages
  # only the container.
  secret_string = jsonencode({
    username = "app_user"
    password = "REPLACE-ME-ROTATION-WILL-OVERWRITE-THIS"
    engine   = "postgres"
    host     = "placeholder.invalid"
  })

  lifecycle {
    # Once rotation runs, the value in AWS is not the value in state, and that
    # is correct. Without this, every `terraform apply` would reset the secret
    # to the placeholder above — which is a genuinely spectacular outage and a
    # very common one.
    ignore_changes = [secret_string]
  }
}

resource "aws_secretsmanager_secret_rotation" "app" {
  secret_id           = aws_secretsmanager_secret.app.id
  rotate_immediately  = false
  rotation_lambda_arn = aws_lambda_function.rotator.arn

  rotation_rules {
    automatically_after_days = var.secret_rotation_days
  }

  depends_on = [aws_lambda_permission.rotator_secretsmanager]
}

# ---------------------------------------------------------------------------
# 6a. The rotation Lambda, and the four-step protocol nobody explains
#
# Secrets Manager rotation is not "a function that changes the password". It is
# a state machine that invokes your function FOUR TIMES per rotation with a
# different `Step` each time, and the whole design exists so that a rotation
# which fails halfway leaves a working credential behind.
#
#   createSecret  Generate the new value and store it as AWSPENDING.
#                 The current value keeps its AWSCURRENT label and keeps
#                 working. Must be idempotent — this step is retried.
#   setSecret     Push AWSPENDING to the actual service: ALTER USER, the
#                 provider's API, whatever. THIS IS THE STEP THAT DOES THE
#                 REAL WORK and the one this lab's function stubs out.
#   testSecret    Use AWSPENDING to actually connect. If this throws, rotation
#                 stops here and AWSCURRENT is untouched — which is the entire
#                 point of the protocol.
#   finishSecret  Move the AWSCURRENT label to the pending version. Only now
#                 does the new credential become the one applications get.
#
# The failure that looks like success: a function that implements createSecret
# and finishSecret but stubs setSecret. Rotation "succeeds" every time,
# LastRotatedDate updates, the console is green — and the credential in the
# database never changed, so the value your application now fetches does not
# work. You have built a scheduled outage.
#
# So: this function is a TEACHING IMPLEMENTATION and says so in its docstring.
# It performs a genuine four-step rotation of the JSON value, with setSecret
# and testSecret marked, loudly, as the places your real logic goes.
# ---------------------------------------------------------------------------

data "archive_file" "rotator" {
  type        = "zip"
  source_file = "${path.module}/lambda/secret_rotator.py"
  output_path = "${path.module}/build/secret_rotator.zip"
}

resource "aws_cloudwatch_log_group" "rotator" {
  name              = "/aws/lambda/${local.prefix}-rotator-${local.suffix}"
  retention_in_days = 14

  tags = {
    Name = "${local.prefix}-rotator"
  }
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "rotator" {
  name               = "${local.prefix}-rotator-${local.suffix}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json

  tags = {
    Name = "${local.prefix}-rotator"
  }
}

data "aws_iam_policy_document" "rotator" {
  statement {
    sid    = "RotateThisSecretOnly"
    effect = "Allow"

    actions = [
      "secretsmanager:DescribeSecret",
      "secretsmanager:GetSecretValue",
      "secretsmanager:PutSecretValue",
      "secretsmanager:UpdateSecretVersionStage",
    ]

    # Scoped to one secret. A rotation function with
    # secretsmanager:* on Resource "*" can read every secret in the account,
    # which makes it a far more valuable thing to compromise than the secret
    # it was written to rotate.
    resources = [aws_secretsmanager_secret.app.arn]

    condition {
      test     = "StringEquals"
      variable = "secretsmanager:resource/AllowRotationLambdaArn"
      values   = ["arn:${local.partition}:lambda:${local.region}:${local.account_id}:function:${local.prefix}-rotator-${local.suffix}"]
    }
  }

  statement {
    sid       = "GenerateRandomValues"
    effect    = "Allow"
    actions   = ["secretsmanager:GetRandomPassword"]
    resources = ["*"]
  }

  statement {
    sid       = "WriteItsOwnLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.rotator.arn}:*"]
  }
}

resource "aws_iam_role_policy" "rotator" {
  name   = "${local.prefix}-rotator-policy"
  role   = aws_iam_role.rotator.id
  policy = data.aws_iam_policy_document.rotator.json
}

resource "aws_lambda_function" "rotator" {
  function_name = "${local.prefix}-rotator-${local.suffix}"
  role          = aws_iam_role.rotator.arn
  handler       = "secret_rotator.handler"
  runtime       = "python3.12"

  filename         = data.archive_file.rotator.output_path
  source_code_hash = data.archive_file.rotator.output_base64sha256

  memory_size = 256
  timeout     = 60

  environment {
    variables = {
      SECRETS_MANAGER_ENDPOINT = "https://secretsmanager.${local.region}.amazonaws.com"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.rotator,
    aws_iam_role_policy.rotator,
  ]

  tags = {
    Name = "${local.prefix}-rotator"
    Role = "secret-rotation"
  }
}

resource "aws_lambda_permission" "rotator_secretsmanager" {
  statement_id   = "AllowSecretsManagerInvoke"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.rotator.function_name
  principal      = "secretsmanager.amazonaws.com"
  source_account = local.account_id
}


###############################################################################
# 7. THE RESPONDER'S PERMISSIONS, AND THE THINGS IT MUST NOT BE ABLE TO DO
#
# ------------------------------------------------------------------ 7.1
# THE KILL SWITCH
#
# An SSM parameter, read by the responder on every invocation, with no cache.
#
# Why a parameter and not the `enable_auto_response` variable: that variable
# needs a `terraform apply`, which needs a plan, a review and a pipeline. All
# of that is correct for a considered decision and useless at 03:00 when the
# automation is making things worse and somebody needs it to stop NOW.
#
#   aws ssm put-parameter --name /cbc-day07/kill-switch --value DISARMED \
#     --type String --overwrite
#
# One command. No apply. Takes effect on the next invocation.
#
# You want BOTH switches. The variable is the decision; the parameter is the
# brake. A responder with no runtime disable is check SEC-014, and the reason
# it is HIGH rather than MEDIUM is that its absence is only discovered during
# the incident where it was needed.
#
# TEST IT. A kill switch nobody has ever flipped is a hypothesis. Lab step 7
# makes you flip it and confirm the responder actually stops.
###############################################################################

resource "aws_ssm_parameter" "kill_switch" {
  name        = "/${local.prefix}/kill-switch"
  description = "ARMED or DISARMED. Read by the threat responder on every invocation. Flip it with put-parameter; no terraform apply required."
  type        = "String"
  value       = var.kill_switch_default

  lifecycle {
    # Once a human flips this during an incident, Terraform must not flip it
    # back on the next apply. That would be a genuinely spectacular way to
    # re-enable automation somebody deliberately stopped — probably during the
    # remediation, probably from a pipeline nobody was watching.
    ignore_changes = [value]
  }

  tags = {
    Name = "${local.prefix}-kill-switch"
    Role = "runtime-control"
  }
}

# ---------------------------------------------------------------------------
# 7.2. A SEPARATE TOPIC FOR CONTAINMENT ACTIONS
#
# Findings go to one topic; things this account did to itself go to another.
# The same argument Day 06 made for splitting alerts from AI summaries, with
# higher stakes:
#
#   DIFFERENT URGENCY. "We detected something" can wait for the morning.
#   "We have isolated an instance in production" cannot.
#   DIFFERENT AUDIENCE. Detection is a security team subject. Containment is a
#   service-owner subject, because their thing just stopped serving traffic.
#   DIFFERENT RETENTION. Containment messages are the human-readable half of
#   the non-repudiation story. They are what somebody reads in the postmortem.
#
# On one topic, the containment message arrives in the middle of forty finding
# notifications and is read on Thursday.
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "containment" {
  name = "${local.prefix}-containment-${local.suffix}"

  tags = {
    Name = "${local.prefix}-containment"
    Data = "records-actions-taken-without-a-human"
  }
}

resource "aws_sns_topic_subscription" "containment_email" {
  topic_arn = aws_sns_topic.containment.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

# ---------------------------------------------------------------------------
# 7.3. The responder role.
#
# Read the DENY statements before the Allow statements. They are the more
# interesting half and they are what makes this role safe to hold.
#
# An automated responder is, by construction, a principal that can change your
# account without a human. That makes it the single most valuable thing in the
# account to compromise — more valuable than most human roles, because it acts
# at machine speed and its actions look normal in CloudTrail.
#
# So three things it must never be able to do, expressed as explicit Denies
# rather than as absences, because an absence is one careless policy
# attachment away from not being an absence:
#
#   1. MODIFY OR STOP THE TRAIL. An attacker who reaches this role should not
#      also be able to delete the record of having done so. This is the
#      difference between an incident and an incident you cannot investigate.
#   2. MODIFY ITS OWN ROLE OR POLICY. Otherwise the narrow scope below is
#      advisory: any compromise begins with a one-line privilege escalation.
#   3. CHANGE THE KILL SWITCH. The brake must not be reachable by the thing it
#      brakes.
#
# An explicit Deny cannot be overridden by any Allow, in any policy, ever.
# That property is why these three are Denies and not merely omissions, and
# check SEC-008 is what fires when a responder role lacks them.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "responder" {
  name               = "${local.prefix}-responder-${local.suffix}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json

  tags = {
    Name = "${local.prefix}-responder"
    Role = "automated-response"
  }
}

data "aws_iam_policy_document" "responder" {
  statement {
    sid    = "ReadInstancesToDecideAndRecord"
    effect = "Allow"

    actions = [
      "ec2:DescribeInstances",
      "ec2:DescribeSecurityGroups",
    ]

    resources = ["*"]
  }

  statement {
    sid    = "ContainReversiblyOnly"
    effect = "Allow"

    actions = [
      # Replace security groups. Reversible, and the previous groups are
      # recorded before the change.
      "ec2:ModifyInstanceAttribute",
      # Tag the instance so a human three weeks later can see what happened
      # without reading CloudTrail.
      "ec2:CreateTags",
    ]

    resources = ["arn:${local.partition}:ec2:${local.region}:${local.account_id}:instance/*"]
  }

  statement {
    sid       = "ReadTheKillSwitch"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.kill_switch.arn]
  }

  statement {
    sid       = "AnnounceWhatItDid"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.containment.arn]
  }

  statement {
    sid       = "WriteItsOwnLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.responder.arn}:*"]
  }

  # ---- the three Denies ---------------------------------------------------

  statement {
    sid    = "DenyTamperingWithTheEvidence"
    effect = "Deny"

    actions = [
      "cloudtrail:StopLogging",
      "cloudtrail:DeleteTrail",
      "cloudtrail:UpdateTrail",
      "cloudtrail:PutEventSelectors",
    ]

    resources = ["*"]
  }

  statement {
    sid    = "DenySelfEscalation"
    effect = "Deny"

    actions = [
      "iam:*",
      "sts:AssumeRole",
    ]

    resources = ["*"]
  }

  statement {
    sid    = "DenyDisablingItsOwnBrake"
    effect = "Deny"

    actions = [
      "ssm:PutParameter",
      "ssm:DeleteParameter",
      "ssm:DeleteParameters",
    ]

    resources = ["*"]
  }

  # And the destructive actions it must never take, denied explicitly even
  # though nothing above grants them. Belt and braces, on purpose: this is the
  # statement a reviewer reads to understand what the automation CANNOT do,
  # and "there is no Allow for it" is a much weaker sentence than "there is a
  # Deny".
  statement {
    sid    = "DenyIrreversibleContainment"
    effect = "Deny"

    actions = [
      "ec2:TerminateInstances",
      "ec2:StopInstances",
      "ec2:DeleteSecurityGroup",
      "iam:DeleteAccessKey",
      "iam:UpdateAccessKey",
      "secretsmanager:DeleteSecret",
    ]

    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "responder" {
  name   = "${local.prefix}-responder-policy"
  role   = aws_iam_role.responder.id
  policy = data.aws_iam_policy_document.responder.json
}


###############################################################################
# 8. THE RESPONDER, AND THE WIRING THAT DECIDES WHEN IT RUNS
#
# ------------------------------------------------------------------ 8.1
# WHY A LAMBDA AND NOT STEP FUNCTIONS
#
# Step Functions is the better answer for containment workflows in general,
# and it is the right answer as soon as your response has more than one step:
# an explicit state machine, a visual execution history you can hand to an
# auditor, per-state retries, and a built-in wait state for "notify, wait for
# human approval, then act".
#
# This lab uses a Lambda because the response here is ONE decision and ONE
# action, and a single-state state machine is ceremony that obscures the
# argument. The moment you add "snapshot the volume, wait for the snapshot,
# then isolate", switch — and the honest reason to switch is not elegance, it
# is that a multi-step response implemented as one Lambda has no story for
# what happens when step two fails after step one succeeded.
#
# ------------------------------------------------------------------ 8.2
# FILTERING AT THE BROKER, NOT IN THE CODE
#
# The rule below matches GuardDuty findings and nothing else. It could also
# filter on severity or type in the event pattern — EventBridge supports it —
# and it deliberately does not.
#
# The decision belongs in ONE place, and that place is the responder, because
# the responder is the thing that can explain itself. A finding filtered out
# by an event pattern produces no invocation, no log line and no notification:
# it is indistinguishable from the rule being broken. A finding rejected by
# `should_respond()` produces a record saying which allow-list it missed and
# why.
#
# "Why did nothing happen" is asked far more often than "why did something
# happen", and only one of these designs can answer it.
#
# The cost of that choice is an invocation per finding. At GuardDuty finding
# volumes that is free, and it buys a system that can account for itself.
###############################################################################

resource "aws_cloudwatch_log_group" "responder" {
  name              = "/aws/lambda/${local.prefix}-responder-${local.suffix}"
  retention_in_days = 30

  tags = {
    Name = "${local.prefix}-responder"
    Role = "non-repudiation"
  }
}

data "archive_file" "responder" {
  type        = "zip"
  source_file = "${path.module}/lambda/threat_responder.py"
  output_path = "${path.module}/build/threat_responder.zip"
}

resource "aws_lambda_function" "responder" {
  function_name = "${local.prefix}-responder-${local.suffix}"
  role          = aws_iam_role.responder.arn
  handler       = "threat_responder.handler"
  runtime       = "python3.12"

  filename         = data.archive_file.responder.output_path
  source_code_hash = data.archive_file.responder.output_base64sha256

  memory_size = 256
  timeout     = 60

  # A hard cap. Nothing about containment benefits from running fifty copies
  # at once, and a GuardDuty finding storm — which is a thing that happens
  # during a real incident, when one compromise generates findings across
  # dozens of resources — should not turn into fifty simultaneous instance
  # modifications.
  reserved_concurrent_executions = 2

  environment {
    variables = {
      CONTAINMENT_MODE      = var.containment_mode
      QUARANTINE_SG_ID      = aws_security_group.quarantine.id
      CONTAINMENT_TOPIC_ARN = aws_sns_topic.containment.arn
      KILL_SWITCH_PARAM     = aws_ssm_parameter.kill_switch.name
      RESPOND_TO_TYPES      = jsonencode(var.respond_to_finding_types)
      ACT_ON_SAMPLES        = "false"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.responder,
    aws_iam_role_policy.responder,
  ]

  tags = {
    Name = "${local.prefix}-responder"
    Role = "automated-response"
  }
}

resource "aws_cloudwatch_event_rule" "guardduty_findings" {
  name        = "${local.prefix}-guardduty-to-responder-${local.suffix}"
  description = "Every GuardDuty finding reaches the responder, which decides and explains itself. Filtering here instead would make 'nothing happened' indistinguishable from 'the rule is broken'."

  state = var.enable_auto_response ? "ENABLED" : "DISABLED"

  event_pattern = jsonencode({
    source      = ["aws.guardduty"]
    detail-type = ["GuardDuty Finding"]
  })

  tags = {
    Name = "${local.prefix}-guardduty-to-responder"
  }
}

resource "aws_cloudwatch_event_target" "responder" {
  rule      = aws_cloudwatch_event_rule.guardduty_findings.name
  target_id = "responder"
  arn       = aws_lambda_function.responder.arn

  retry_policy {
    maximum_retry_attempts       = 2
    maximum_event_age_in_seconds = 900
  }

  dead_letter_config {
    arn = aws_sqs_queue.responder_dlq.arn
  }
}

# A finding that failed to reach the responder and then vanished is
# indistinguishable from a finding that was correctly ignored. On Day 04 that
# argument was about compliance reports; here it is about a detection nobody
# ever saw. A responder with no failure path is check SEC-016.
resource "aws_sqs_queue" "responder_dlq" {
  name                      = "${local.prefix}-responder-dlq-${local.suffix}"
  message_retention_seconds = 1209600

  tags = {
    Name = "${local.prefix}-responder-dlq"
  }
}

data "aws_iam_policy_document" "responder_dlq" {
  statement {
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.responder_dlq.arn]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.guardduty_findings.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "responder_dlq" {
  queue_url = aws_sqs_queue.responder_dlq.id
  policy    = data.aws_iam_policy_document.responder_dlq.json
}

resource "aws_lambda_permission" "eventbridge_responder" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.responder.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.guardduty_findings.arn
}

# Findings also go to a human, on the detection topic, regardless of whether
# the responder acted. Automation that replaces notification rather than
# supplementing it is how a team stops knowing what its account is doing.
resource "aws_cloudwatch_event_target" "findings_to_sns" {
  rule      = aws_cloudwatch_event_rule.guardduty_findings.name
  target_id = "notify"
  arn       = aws_sns_topic.security.arn

  input_transformer {
    input_paths = {
      severity = "$.detail.severity"
      type     = "$.detail.type"
      title    = "$.detail.title"
      id       = "$.detail.id"
    }

    # Severity is labelled as impact right here, in the notification, because
    # this is where somebody reads the number at 03:00 and decides how alarmed
    # to be. Section 3 makes the argument; this is where it has to land.
    input_template = <<-TEMPLATE
      "GuardDuty finding: <title>"
      "type     : <type>"
      "severity : <severity>  (IMPACT if real, NOT confidence that it is real)"
      "id       : <id>"
      "The automated responder decides separately, on a reviewed allow-list of finding TYPES."
    TEMPLATE
  }

  # A DLQ on the NOTIFICATION target too, not just on the compute one. A
  # finding that failed to reach a human is exactly as invisible as one that
  # failed to reach the responder, and it is the more common failure — an SNS
  # topic policy that does not permit events.amazonaws.com fails silently.
  dead_letter_config {
    arn = aws_sqs_queue.responder_dlq.arn
  }

  depends_on = [aws_sns_topic_policy.security]
}


###############################################################################
# 9. THE QUARANTINE SECURITY GROUP — built before it is needed
#
# The containment action CP2 performs is "replace this instance's security
# groups with the quarantine group". That group has to already exist, in the
# right VPC, before the incident — creating it during a response means your
# automation needs `ec2:CreateSecurityGroup`, which is a much larger permission
# than it needs and a much better one to steal.
#
# WHY THIS GROUP HAS NO RULES AT ALL
#
# An `aws_security_group` with no ingress and no egress blocks has no rules.
# AWS adds a default allow-all egress rule when a group is created through the
# API; Terraform removes it, because Terraform manages the rule set exactly as
# written. Here that is exactly what we want: an instance in this group can
# neither receive nor initiate traffic.
#
# Which raises the question the lab makes you answer: an isolated instance is
# also unreachable by YOU. No SSH, no SSM Session Manager, no agent check-in.
# You have contained the incident and destroyed your ability to investigate it
# from inside the box.
#
# The real-world answer is usually a quarantine group with egress to the SSM
# endpoints only, so Session Manager still works while everything else is cut.
# That is a genuinely better design and it is left as a lab exercise rather
# than built here, because the version with no rules is the one that makes the
# trade-off obvious the first time you try to connect.
###############################################################################

resource "aws_security_group" "quarantine" {
  name        = local.quarantine_sg_name
  description = "Total isolation. No ingress, no egress. Attached by the automated responder; detached by a human."
  vpc_id      = local.vpc_id

  tags = {
    Name    = local.quarantine_sg_name
    Role    = "containment"
    Warning = "an instance in this group is unreachable by you as well"
  }
}


###############################################################################
# 10. DELIBERATELY BROKEN EXAMPLES
#
# Gated behind create_insecure_examples, default true, exactly as on Days 04,
# 05 and 06. These exist so sec_audit.py has real findings rather than a clean
# account and a green score that teaches nothing.
#
# Every resource here is a mistake somebody has actually shipped.
###############################################################################

# ---------------------------------------------------------------------------
# 10a. A second trail, single-region, with no log file validation → SEC-006, SEC-007
#
# The origin story is always the same and it is never careless: the security
# team wanted their own trail, or an application team needed events in their
# own bucket, and nobody checked the two boxes that turn logging into evidence.
#
# It also costs money. The first trail delivering management events is free;
# this is the second, at ~$2.00 per 100,000 events. On a busy account that is
# a real line item for a trail nobody reads.
# ---------------------------------------------------------------------------
resource "aws_cloudtrail" "shadow" {
  count = var.create_insecure_examples ? 1 : 0

  name           = "${local.prefix}-shadow-trail-${local.suffix}"
  s3_bucket_name = aws_s3_bucket.shadow[0].id

  # SEC-006: single region. An attacker operating in ap-south-1 is invisible.
  is_multi_region_trail = false

  # SEC-006 continued: and no global service events, so no IAM activity either.
  include_global_service_events = false

  # SEC-007: no log file validation. This is logging, not evidence. If someone
  # with s3:PutObject edits these files there is no way to prove it.
  enable_log_file_validation = false

  enable_logging = true

  depends_on = [aws_s3_bucket_policy.shadow]

  tags = {
    Name    = "${local.prefix}-shadow-trail"
    Finding = "SEC-006,SEC-007"
  }
}

# The bucket it delivers to: no versioning, no public access block, no
# TLS-only policy → SEC-009.
#
# Every one of those is an absence rather than a wrong value, which is why
# none of them appears in a code review as a line somebody wrote. The bucket
# looks fine. It is fine, right up until somebody with s3:PutObject replaces
# a log file with a shorter one and there is no previous version to compare.
resource "aws_s3_bucket" "shadow" {
  count = var.create_insecure_examples ? 1 : 0

  bucket        = "${local.prefix}-shadow-${local.account_id}-${local.suffix}"
  force_destroy = true

  tags = {
    Name    = "${local.prefix}-shadow"
    Finding = "SEC-009"
  }
}

data "aws_iam_policy_document" "shadow_bucket" {
  count = var.create_insecure_examples ? 1 : 0

  statement {
    sid    = "AWSCloudTrailAclCheck"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.shadow[0].arn]
  }

  statement {
    sid    = "AWSCloudTrailWrite"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.shadow[0].arn}/AWSLogs/${local.account_id}/*"]
  }
}

resource "aws_s3_bucket_policy" "shadow" {
  count = var.create_insecure_examples ? 1 : 0

  bucket = aws_s3_bucket.shadow[0].id
  policy = data.aws_iam_policy_document.shadow_bucket[0].json
}

# ---------------------------------------------------------------------------
# 10b. A secret with no rotation at all → SEC-010
#
# Created once, during a migration, by someone who meant to come back to it.
# There is no rotation schedule, no owner tag, and the value has not changed
# since it was created. Every audit finds several of these and every team is
# surprised by the count.
# ---------------------------------------------------------------------------
resource "aws_secretsmanager_secret" "legacy" {
  count = var.create_insecure_examples ? 1 : 0

  name        = "${local.prefix}/legacy-api-key-${local.suffix}"
  description = "DELIBERATELY BROKEN (SEC-010). No rotation configured. Created during a migration; nobody came back to it."

  recovery_window_in_days = var.secret_recovery_window_days

  tags = {
    Name    = "${local.prefix}-legacy-api-key"
    Finding = "SEC-010"
  }
}

resource "aws_secretsmanager_secret_version" "legacy" {
  count = var.create_insecure_examples ? 1 : 0

  secret_id     = aws_secretsmanager_secret.legacy[0].id
  secret_string = jsonencode({ api_key = "legacy-value-never-rotated" })

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# ---------------------------------------------------------------------------
# 10c. An IAM user with a long-lived access key → SEC-013
#
# The most common finding in any real account, and the one people defend the
# hardest. "It is only used by the build server." "It is scoped down." "We will
# migrate it next quarter."
#
# The problem is not the permissions. It is that a long-lived key is a
# credential that can be copied, and once copied there is nothing about its use
# that looks different from legitimate use. Every other credential in a modern
# AWS account — instance profiles, IRSA, OIDC federation from CI — is
# short-lived and bound to a workload identity. This one is a string in
# somebody's environment.
#
# The check reports age because age is the only thing measurable from outside.
# The real finding is that the key exists at all.
# ---------------------------------------------------------------------------
resource "aws_iam_user" "legacy_service" {
  count = var.create_insecure_examples ? 1 : 0

  name          = "${local.prefix}-legacy-service-${local.suffix}"
  force_destroy = true

  tags = {
    Name    = "${local.prefix}-legacy-service"
    Finding = "SEC-013"
  }
}

resource "aws_iam_access_key" "legacy_service" {
  count = var.create_insecure_examples ? 1 : 0

  user = aws_iam_user.legacy_service[0].name
}

# Deliberately minimal permissions. The finding is about the CREDENTIAL SHAPE,
# not about what it can do, and giving it something dangerous would teach the
# wrong lesson and create a real risk in a real account.
data "aws_iam_policy_document" "legacy_service" {
  count = var.create_insecure_examples ? 1 : 0

  statement {
    effect    = "Allow"
    actions   = ["s3:ListAllMyBuckets"]
    resources = ["*"]
  }
}

resource "aws_iam_user_policy" "legacy_service" {
  count = var.create_insecure_examples ? 1 : 0

  name   = "${local.prefix}-legacy-service-policy"
  user   = aws_iam_user.legacy_service[0].name
  policy = data.aws_iam_policy_document.legacy_service[0].json
}

# ---------------------------------------------------------------------------
# 10d-10g. THE NAIVE RESPONDER → SEC-005, SEC-008, SEC-012, SEC-014, SEC-016
#
# This runs the IDENTICAL zip file as the responder in section 8. Same Python,
# same handler, same everything. It differs in three environment variables and
# one IAM policy — and that is the point, exactly as it was on Day 06.
#
# Nobody writes a bad threat responder. The code in lambda/threat_responder.py
# is the same code in both functions and it contains all the right machinery:
# a type allow-list, a kill switch, reversible-only containment, recorded
# rollback. Every one of those is switched off here by configuration.
#
# Which is how it happens. Somebody sets SEVERITY_THRESHOLD during a demo
# because building the allow-list needed a meeting. Somebody widens the IAM
# policy because the scoped one failed on a Friday. Somebody drops the DLQ
# because the queue policy was fiddly. None of those changes touches a line of
# code, so none of them gets a code review, and all of them are still there a
# year later.
#
# sec_audit.py finds misconfiguration rather than bad code because
# misconfiguration is what actually ships.
#
# Note what the shared code does when it meets CONTAINMENT_MODE=terminate: it
# REFUSES, loudly, and changes nothing. The destructive path does not exist to
# be misconfigured into. The check still fires — an intent to terminate is a
# finding whether or not the code obeys it — and lab step 6 makes you read the
# refusal in the logs.
# ---------------------------------------------------------------------------

# 10d. A responder role that can tamper with the evidence and with itself
#      → SEC-008
data "aws_iam_policy_document" "naive_responder" {
  count = var.create_insecure_examples ? 1 : 0

  statement {
    sid    = "FarTooMuch"
    effect = "Allow"

    actions = [
      "ec2:*",
      # DELIBERATELY BROKEN (SEC-008). A responder that can update the trail
      # can delete the record of what it did. A responder that can change its
      # own policy has no scope at all — the narrow permissions are advisory
      # from the moment this line exists.
      "cloudtrail:*",
      "iam:*",
      "ssm:*",
      "sns:Publish",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = ["*"]
  }
}

resource "aws_iam_role" "naive_responder" {
  count = var.create_insecure_examples ? 1 : 0

  name               = "${local.prefix}-naive-responder-${local.suffix}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json

  tags = {
    Name    = "${local.prefix}-naive-responder"
    Finding = "SEC-008"
  }
}

resource "aws_iam_role_policy" "naive_responder" {
  count = var.create_insecure_examples ? 1 : 0

  name   = "${local.prefix}-naive-responder-policy"
  role   = aws_iam_role.naive_responder[0].id
  policy = data.aws_iam_policy_document.naive_responder[0].json
}

# 10e. Its log group, so the function does not create one without retention.
#      The broken example is broken deliberately in specific ways; leaving an
#      unretained log group behind is Day 06's lesson, not this one.
resource "aws_cloudwatch_log_group" "naive_responder" {
  count = var.create_insecure_examples ? 1 : 0

  name              = "/aws/lambda/${local.prefix}-naive-responder-${local.suffix}"
  retention_in_days = 30

  tags = {
    Name = "${local.prefix}-naive-responder"
  }
}

# 10f. The function: severity threshold, destructive mode, no kill switch
#      → SEC-005, SEC-012, SEC-014
resource "aws_lambda_function" "naive_responder" {
  count = var.create_insecure_examples ? 1 : 0

  function_name = "${local.prefix}-naive-responder-${local.suffix}"
  role          = aws_iam_role.naive_responder[0].arn
  handler       = "threat_responder.handler"
  runtime       = "python3.12"

  filename         = data.archive_file.responder.output_path
  source_code_hash = data.archive_file.responder.output_base64sha256

  memory_size = 256
  timeout     = 60

  # No reserved concurrency either. A finding storm during a real incident
  # invokes as many copies as GuardDuty can produce findings.

  environment {
    variables = {
      CONTAINMENT_TOPIC_ARN = aws_sns_topic.containment.arn
      QUARANTINE_SG_ID      = aws_security_group.quarantine.id

      # SEC-005: a severity threshold instead of a reviewed allow-list of
      # finding types. 7.0 is "High", which sounds responsible and matches
      # your penetration test, your scanner, a researcher and a developer on
      # hotel wifi. Four outages you caused, for one real detection.
      SEVERITY_THRESHOLD = "7.0"
      RESPOND_TO_TYPES   = "[]"

      # SEC-012: an intent to terminate. The shared code refuses and logs why,
      # which is the correct behaviour and does NOT make the configuration
      # acceptable — the next person to "fix" the responder will implement it.
      CONTAINMENT_MODE = "terminate"

      # SEC-014: no KILL_SWITCH_PARAM at all. Nothing stops this without a
      # deploy, which means nothing stops it at 03:00.

      # And it acts on samples, which is the trap in the other direction: this
      # one works beautifully in the lab.
      ACT_ON_SAMPLES = "true"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.naive_responder,
    aws_iam_role_policy.naive_responder,
  ]

  tags = {
    Name    = "${local.prefix}-naive-responder"
    Finding = "SEC-005,SEC-012,SEC-014"
  }
}

# 10g. A rule wired to it with no DLQ (SEC-016), created DISABLED (SEC-015).
#
# The DISABLED state is the more insidious of the two. It looks completely
# normal in the console, it produces no errors, and everybody believes the
# automation is running. It is Day 04's CMP-014 wearing a security hat, and
# the only way to find it is to look — or to run a check that looks for you.
resource "aws_cloudwatch_event_rule" "naive_responder" {
  count = var.create_insecure_examples ? 1 : 0

  name        = "${local.prefix}-naive-guardduty-rule-${local.suffix}"
  description = "DELIBERATELY BROKEN (SEC-015, SEC-016). Created DISABLED, wired to a target with no dead-letter queue and no retry policy."

  state = "DISABLED"

  event_pattern = jsonencode({
    source      = ["aws.guardduty"]
    detail-type = ["GuardDuty Finding"]
  })

  tags = {
    Name    = "${local.prefix}-naive-guardduty-rule"
    Finding = "SEC-015,SEC-016"
  }
}

resource "aws_cloudwatch_event_target" "naive_responder" {
  count = var.create_insecure_examples ? 1 : 0

  rule      = aws_cloudwatch_event_rule.naive_responder[0].name
  target_id = "naive-responder"
  arn       = aws_lambda_function.naive_responder[0].arn

  # SEC-016: no retry_policy and no dead_letter_config. A finding that fails
  # to reach the responder vanishes, and a vanished detection is
  # indistinguishable from one that was correctly ignored.
}

resource "aws_lambda_permission" "naive_responder" {
  count = var.create_insecure_examples ? 1 : 0

  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.naive_responder[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.naive_responder[0].arn
}
