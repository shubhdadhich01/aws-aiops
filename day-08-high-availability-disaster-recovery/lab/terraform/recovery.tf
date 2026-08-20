###############################################################################
# Day 08 — recovery.tf
# Backup, restore, and the automated recovery workflow
#
# ================== WHY THIS IS A SEPARATE FILE, STILL FLAT ==================
#
# The repo convention is flat: no modules unless the layout is the subject.
# That has not changed — there are no modules here, no envs/, no indirection.
# What has changed is that main.tf reached two thousand lines of foundation,
# and THE RECOVERY PATH IS THE ONE THING IN THIS STACK THAT SOMEBODY WILL READ
# UNDER TIME PRESSURE.
#
# It gets its own file for the same reason a runbook is not an appendix. If you
# have to scroll past a NAT gateway discussion to find out what your failover
# actually does, you will not read it before the incident, and during the
# incident you will read it wrong.
#
# Day 06 documented going flat. This documents going flat AND legible.
#
# WHAT GETS BUILT
#
#   12. AWS Backup: a vault here, a vault in the DR region, a plan with a copy
#       rule, a tag-based selection, and an optional GOVERNANCE-mode lock.
#   13. Two SSM parameters: the kill switch, and the active-region flag that
#       the failover flips.
#   14. The recovery Lambda (lambda/recovery.py).
#   15. The Step Functions workflow: detect -> decide -> approve -> fail over
#       -> verify -> notify, with a kill switch first and a dry run by default.
#   16. The same workflow built badly, on purpose, for DR-015.
#
# WHAT DELIBERATELY DOES NOT GET BUILT: an automated failback. See section 15
# and the failback docstring in lambda/recovery.py. It is not an omission.
###############################################################################


###############################################################################
# 12. AWS BACKUP — and the difference between a backup and a restore
#
# A backup nobody has restored is a file. Everything in this section produces
# files. Check DR-010 is the only thing in the repo that asks whether anybody
# has ever turned one back into a system, and it is CRITICAL for that reason.
#
# THE THREE THINGS THAT GO WRONG WITH BACKUPS, in the order they are
# discovered, which is the reverse of the order they matter:
#
#   1. There is no backup.            Discovered immediately. Rare.
#   2. The backup is too old.         Discovered at restore time. Common.
#   3. The backup cannot be restored. Discovered at the worst possible moment,
#                                     and by then it is not a backup problem,
#                                     it is a business continuity problem.
#
# The third one is the interesting one and it has boring causes: the KMS key
# was rotated or deleted, the snapshot references an AMI that no longer exists,
# the restore requires an instance type not available in the DR region, the
# database engine version was deprecated, or the restore works and takes nine
# hours. None of these is visible in a backup report. All of them are visible
# in one restore test.
###############################################################################

resource "aws_backup_vault" "main" {
  count = var.enable_backup_plan ? 1 : 0

  name = "${local.prefix}-vault-${local.suffix}"

  # force_destroy so the lab tears down. In production, absolutely not: it
  # deletes every recovery point in the vault. Note that force_destroy is
  # IGNORED once a vault lock is in place, which is the entire point of a
  # vault lock and is also why the teardown checklist has a section about it.
  force_destroy = true

  tags = {
    Name = "${local.prefix}-vault-${local.suffix}"
    Role = "primary-backup-vault"
  }
}

# The DR-region vault. This is the one that matters.
#
# A vault in the region that just failed is not a recovery option. That sentence
# is obvious and the gap is still the most common one in real DR postures,
# because the copy rule is a SEPARATE decision from the backup rule and only
# the backup rule is required to make a plan valid. A plan with no copy action
# is complete, correct, green in the console, and regional.
resource "aws_backup_vault" "dr" {
  count    = var.enable_backup_plan && var.backup_copy_to_dr ? 1 : 0
  provider = aws.dr

  name          = "${local.prefix}-vault-dr-${local.suffix}"
  force_destroy = true

  tags = {
    Name = "${local.prefix}-vault-dr-${local.suffix}"
    Role = "dr-backup-vault"
  }
}

# ============================== VAULT LOCK ===================================
#
# GOVERNANCE MODE ONLY, ENFORCED BY OMISSION.
#
# Read the next paragraph carefully, because it is the single most dangerous
# argument in this repo and it is dangerous by its ABSENCE.
#
# AWS Backup chooses the lock mode from whether `changeable_for_days` is set:
#
#   changeable_for_days ABSENT   -> GOVERNANCE mode. Removable by a principal
#                                   with backup:DeleteBackupVaultLockConfiguration.
#   changeable_for_days PRESENT  -> COMPLIANCE mode. After the cooling-off
#                                   window it CANNOT BE REMOVED. Not by you,
#                                   not by root, not by AWS Support. The vault
#                                   cannot be deleted while it holds recovery
#                                   points, and you pay for that storage until
#                                   the longest retention expires.
#
# So the mode is selected by the PRESENCE OF AN ARGUMENT rather than by a
# value. There is no `mode = "governance"` line to get wrong; there is a line
# whose existence changes everything, and adding it looks like adding detail.
#
# That is a genuinely poor API and it has produced real, unrecoverable,
# expensive mistakes. This stack therefore does not expose compliance mode at
# all — no variable, no toggle, no ternary that could evaluate wrong. If you
# want compliance mode in production, add the argument yourself, deliberately,
# with a colleague reading the plan, in a repository where somebody reviews it.
#
# Governance mode still buys you the thing you probably wanted: protection
# against accident, process failure, and a script that deletes recovery points
# to save money. It does not protect against an attacker who already has admin,
# which is the threat compliance mode exists for and which deserves its own
# conversation rather than a default.
# =============================================================================

resource "aws_backup_vault_lock_configuration" "main" {
  count = var.enable_backup_plan && var.enable_vault_lock ? 1 : 0

  backup_vault_name = aws_backup_vault.main[0].name

  # NOTE THE ABSENT ARGUMENT. changeable_for_days is not here, and its absence
  # is what keeps this in governance mode. Do not add it to "be more thorough".
  min_retention_days = var.backup_retention_days
  max_retention_days = var.backup_retention_days * 2
}

resource "aws_backup_vault_lock_configuration" "dr" {
  count    = var.enable_backup_plan && var.backup_copy_to_dr && var.enable_vault_lock ? 1 : 0
  provider = aws.dr

  backup_vault_name = aws_backup_vault.dr[0].name

  min_retention_days = var.backup_retention_days
  max_retention_days = var.backup_retention_days * 2
}

# ---------------------------------------------------------------------------
# The service role AWS Backup assumes.
#
# The AWS-managed policies are used here deliberately, and it is worth being
# explicit about why, because this repo has spent seven days arguing for
# least privilege. AWSBackupServiceRolePolicyForBackup is broad — it can read
# and snapshot most supported resource types across the account. A
# hand-written equivalent is achievable and is a genuine maintenance burden:
# every new service AWS adds backup support for is a policy update you will
# not make, and the failure mode is a resource that silently stops being
# backed up.
#
# So: managed policies, and the compensating control is that the role can only
# be assumed by backup.amazonaws.com and that its actions land in CloudTrail.
# Write that trade-off down in your own repo rather than leaving it implied.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "backup" {
  count = var.enable_backup_plan ? 1 : 0

  name = "${local.prefix}-backup-role-${local.suffix}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "backup.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "backup" {
  count = var.enable_backup_plan ? 1 : 0

  role       = aws_iam_role.backup[0].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
}

# RESTORE PERMISSIONS ARE A SEPARATE POLICY, and attaching them at build time
# rather than at incident time is a small decision with a large consequence.
#
# A role that can back up but not restore produces a DR posture that fails at
# exactly the moment it is used, with an AccessDenied that somebody then has to
# fix under pressure, in an account where the person who can grant IAM may also
# be unavailable. Grant restore now, test it now.
resource "aws_iam_role_policy_attachment" "restore" {
  count = var.enable_backup_plan ? 1 : 0

  role       = aws_iam_role.backup[0].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForRestores"
}

resource "aws_backup_plan" "main" {
  count = var.enable_backup_plan ? 1 : 0

  name = "${local.prefix}-plan-${local.suffix}"

  rule {
    rule_name         = "primary"
    target_vault_name = aws_backup_vault.main[0].name
    schedule          = var.backup_schedule

    # THIS SCHEDULE IS YOUR RPO CEILING for everything this plan protects.
    # Hourly here so a recovery point appears during the lab session. Daily is
    # normal in production, and daily means that at 04:59 you are 23h59m from
    # your last recovery point — which is a fact about your RPO regardless of
    # what any document says.

    # How long the job may wait for a slot before it is abandoned, and how long
    # it may run. A job that is abandoned is a MISSING RECOVERY POINT and it is
    # reported as a completed schedule with a failed job, which is a distinction
    # most backup dashboards render as a small orange dot.
    start_window      = 60
    completion_window = 180

    lifecycle {
      delete_after = var.backup_retention_days
    }

    # The copy rule. Separate from the backup rule, optional, and the only
    # thing that makes these recovery points survive the event they exist for.
    dynamic "copy_action" {
      for_each = var.backup_copy_to_dr ? [1] : []

      content {
        destination_vault_arn = aws_backup_vault.dr[0].arn

        lifecycle {
          delete_after = var.backup_retention_days
        }
      }
    }
  }

  tags = {
    Name = "${local.prefix}-plan-${local.suffix}"
  }
}

# Selection by TAG rather than by ARN, deliberately.
#
# An ARN list is precise and is a list somebody has to maintain. Every resource
# created after the list was written is unprotected, silently, and the person
# who created it had no reason to know the list existed. Tag-based selection
# inverts that: a resource is protected because it carries the tag your
# tagging standard already requires.
#
# The failure mode moves rather than disappearing — an untagged resource is
# unprotected — but it moves somewhere you already have controls, because you
# already care about untagged resources for cost attribution. One mechanism,
# two problems.
resource "aws_backup_selection" "tagged" {
  count = var.enable_backup_plan ? 1 : 0

  name         = "${local.prefix}-selection-${local.suffix}"
  iam_role_arn = aws_iam_role.backup[0].arn
  plan_id      = aws_backup_plan.main[0].id

  # Both conditions must match. AWS Backup ANDs multiple selection_tag blocks.
  selection_tag {
    type  = "STRINGEQUALS"
    key   = "Project"
    value = "aws-aiops-bootcamp"
  }

  selection_tag {
    type  = "STRINGEQUALS"
    key   = "Day"
    value = "08"
  }
}


###############################################################################
# 13. THE TWO PARAMETERS
#
# Both are plain SSM String parameters. Both cost nothing. Between them they
# are the entire state of your disaster recovery posture, which is either
# elegant or alarming depending on how you look at it.
###############################################################################

# The kill switch. Day 07's pattern, unchanged, and it matters more here
# because the action it stops is larger.
#
# The properties that make it useful are the ones that make it unfashionable:
# it is not in Terraform's state after you change it by hand, it can be flipped
# from a phone by somebody who has never run `terraform init`, and it takes
# effect on the next execution with no deploy. An automation whose only brake
# requires a pipeline is an automation with no brake at 03:00 on a Sunday.
resource "aws_ssm_parameter" "kill_switch" {
  name        = "/${local.prefix}/${local.suffix}/recovery-enabled"
  description = "Runtime brake for the Day 08 recovery workflow. Set to 'disabled' to stop it. Read as the FIRST state of every execution; anything other than 'enabled' aborts."
  type        = "String"
  value       = var.kill_switch_default

  # Do not fight a human who has pulled the brake. If somebody set this to
  # 'disabled' during an incident, the next `terraform apply` must not
  # helpfully set it back — which is exactly what would happen without this.
  lifecycle {
    ignore_changes = [value]
  }

  tags = {
    Name = "${local.prefix}-kill-switch-${local.suffix}"
  }
}

# The active-region flag: the application's source of truth for where writes go.
#
# THIS IS THE PART YOUR CODE HAS TO ACTUALLY HONOUR. A failover that flips a
# parameter no application reads is theatre — it changes a value, the workflow
# reports success, and every write continues to land in the region you just
# declared dead. If you take one pattern from this day into a real system, take
# this one, and then go and check that something reads it.
resource "aws_ssm_parameter" "active_region" {
  name        = "/${local.prefix}/${local.suffix}/active-region"
  description = "Which region is authoritative for writes. The failover step sets this to the DR region; failback sets it back. Applications MUST read it, or the failover is theatre."
  type        = "String"
  value       = var.aws_region

  # Same reasoning as the kill switch: a failover changed this on purpose, and
  # `terraform apply` must not quietly fail you back.
  lifecycle {
    ignore_changes = [value]
  }

  tags = {
    Name = "${local.prefix}-active-region-${local.suffix}"
  }
}


###############################################################################
# 14. THE RECOVERY LAMBDA
#
# One function, several actions, dispatched by the state machine. The design
# argument is in lambda/recovery.py's module docstring; read it before the ASL
# below, because the ASL is only interesting once you know what each task does.
###############################################################################

data "archive_file" "recovery" {
  count = var.enable_recovery_workflow ? 1 : 0

  type        = "zip"
  source_file = "${path.module}/lambda/recovery.py"
  output_path = "${path.module}/build/recovery.zip"
}

resource "aws_iam_role" "recovery" {
  count = var.enable_recovery_workflow ? 1 : 0

  name = "${local.prefix}-recovery-role-${local.suffix}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "recovery" {
  count = var.enable_recovery_workflow ? 1 : 0

  name = "${local.prefix}-recovery-policy-${local.suffix}"
  role = aws_iam_role.recovery[0].id

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
        Sid = "ObserveOnly"
        # Everything the assess and verify steps need, and nothing that
        # changes anything. Read permissions are broad because Describe calls
        # do not support resource-level scoping for most of these; that is a
        # real limitation and not a shortcut.
        Effect = "Allow"
        Action = [
          "elasticloadbalancing:DescribeTargetHealth",
          "elasticloadbalancing:DescribeTargetGroups",
          "autoscaling:DescribeAutoScalingGroups",
          "dynamodb:DescribeTable",
          "route53:GetHealthCheck",
          "route53:GetHealthCheckStatus",
          "cloudwatch:GetMetricStatistics",
        ]
        Resource = "*"
      },
      {
        Sid    = "ReadTheBrakeAndTheFlag"
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = [
          aws_ssm_parameter.kill_switch.arn,
          aws_ssm_parameter.active_region.arn,
        ]
      },
      {
        Sid    = "FlipTheFlag"
        Effect = "Allow"
        Action = ["ssm:PutParameter"]
        # Scoped to the active-region parameter ONLY. Note what is missing:
        # this role cannot write the kill switch. An automation that can
        # re-enable its own brake does not have a brake, and that is not a
        # theoretical concern — it is the first thing a badly-written
        # "self-healing" wrapper does.
        Resource = [aws_ssm_parameter.active_region.arn]
      },
      {
        Sid    = "InvertTheHealthCheck"
        Effect = "Allow"
        Action = ["route53:UpdateHealthCheck"]
        # Route 53 health checks are global and their ARNs do not support
        # per-resource conditions in the way you would want. Scoped as tightly
        # as the API allows, which is not very, and said out loud rather than
        # pretended.
        Resource = "*"
      },
      {
        Sid      = "ReplaceUnhealthyInstances"
        Effect   = "Allow"
        Action   = ["autoscaling:SetInstanceHealth"]
        Resource = "arn:${local.partition}:autoscaling:${local.region}:${local.account_id}:autoScalingGroup:*:autoScalingGroupName/${local.asg_name}"
      },
      {
        Sid      = "Notify"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.dr.arn
      },
      {
        Sid    = "DenySelfModification"
        Effect = "Deny"
        # Day 07's four explicit Denies, adapted. The recovery role must not be
        # able to rewrite its own permissions, delete the evidence of what it
        # did, or disable the workflow that supervises it.
        Action = [
          "iam:*",
          "logs:DeleteLogGroup",
          "logs:DeleteLogStream",
          "states:DeleteStateMachine",
          "states:UpdateStateMachine",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "recovery" {
  count = var.enable_recovery_workflow ? 1 : 0

  name              = "/aws/lambda/${local.recovery_function}"
  retention_in_days = 14

  tags = {
    Name = "/aws/lambda/${local.recovery_function}"
  }
}

resource "aws_lambda_function" "recovery" {
  count = var.enable_recovery_workflow ? 1 : 0

  function_name = local.recovery_function
  role          = aws_iam_role.recovery[0].arn
  handler       = "recovery.lambda_handler"
  runtime       = "python3.12"
  timeout       = 120
  memory_size   = 256

  filename         = data.archive_file.recovery[0].output_path
  source_code_hash = data.archive_file.recovery[0].output_base64sha256

  environment {
    variables = {
      DR_REGION           = var.dr_region
      ASG_NAME            = local.asg_name
      TARGET_GROUP_ARN    = aws_lb_target_group.app.arn
      TABLE_NAME          = aws_dynamodb_table.orders.name
      HEALTH_CHECK_ID     = var.enable_route53_health_check ? aws_route53_health_check.primary[0].id : ""
      KILL_SWITCH_PARAM   = aws_ssm_parameter.kill_switch.name
      ACTIVE_REGION_PARAM = aws_ssm_parameter.active_region.name
      TOPIC_ARN           = aws_sns_topic.dr.arn
      RECOVERY_DRY_RUN    = var.recovery_dry_run ? "true" : "false"
      REQUIRE_APPROVAL    = var.require_approval_for_failover ? "true" : "false"
    }
  }

  depends_on = [aws_cloudwatch_log_group.recovery]

  tags = {
    Name = local.recovery_function
  }
}


###############################################################################
# 15. THE RECOVERY WORKFLOW
#
#   detect -> decide -> (approve) -> fail over -> verify -> notify
#
# Read the state machine, not the diagram the console draws from it. The
# console diagram is a picture of the happy path; the interesting states are
# the four that end in Fail.
#
# ========================= WHY STEP FUNCTIONS =============================
#
# Day 07 argued for Step Functions the moment a response has more than one
# step. This has five, plus a gate. The specific properties that matter:
#
#   - The execution history is a timestamped, per-step audit trail. After the
#     drill you read exactly how long each phase took. THAT IS THE RTO
#     MEASUREMENT, and it is the reason this day uses a state machine rather
#     than a Lambda with a try/except: the measurement is a free side effect
#     of the structure.
#   - The approval gate is a first-class state (waitForTaskToken) rather than
#     a Lambda blocking for thirty minutes against a fifteen-minute limit.
#   - A failed verification FAILS THE EXECUTION. A Lambda would return 200
#     with a field nobody reads.
#   - Timeouts and retries are declared next to the step they protect.
#
# ============================= FAILBACK ====================================
#
# THERE IS NO FAILBACK STATE IN THIS WORKFLOW, AND THAT IS DELIBERATE.
#
# Failing over is a decision about ROUTING and it is automatable. Failing back
# is a decision about DATA and it is not — every write that landed in the DR
# region while you were failed over has to be reconciled by something that
# knows what your writes mean, and nothing generic knows that.
#
# lambda/recovery.py has a `failback` action. It is manual-invoke only, it
# reverses the two routing changes in about ten seconds, and its docstring is a
# five-item list of what it CANNOT do. Read that list. Then notice that every
# DR exercise that ends at "we failed over successfully" has tested half a
# procedure and measured a third of an RTO.
###############################################################################

resource "aws_iam_role" "workflow" {
  count = var.enable_recovery_workflow ? 1 : 0

  name = "${local.prefix}-workflow-role-${local.suffix}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "workflow" {
  count = var.enable_recovery_workflow ? 1 : 0

  name = "${local.prefix}-workflow-policy-${local.suffix}"
  role = aws_iam_role.workflow[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "InvokeTheWorker"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = [aws_lambda_function.recovery[0].arn]
      },
      {
        Sid      = "AskForApproval"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.dr.arn
      },
    ]
  })
}

resource "aws_sfn_state_machine" "recovery" {
  count = var.enable_recovery_workflow ? 1 : 0

  name     = "${local.prefix}-recovery-${local.suffix}"
  role_arn = aws_iam_role.workflow[0].arn

  definition = jsonencode({
    Comment = "Day 08 recovery: detect, decide, approve, fail over, verify, notify. There is no automated failback; see lambda/recovery.py."
    StartAt = "CheckKillSwitch"

    States = {
      # ---- The brake, first, always ------------------------------------
      CheckKillSwitch = {
        Type       = "Task"
        Resource   = aws_lambda_function.recovery[0].arn
        Parameters = { action = "check_kill_switch" }
        ResultPath = "$.kill_switch"
        Retry = [{
          ErrorEquals     = ["States.TaskFailed", "Lambda.ServiceException", "Lambda.TooManyRequestsException"]
          IntervalSeconds = 2
          MaxAttempts     = 3
          BackoffRate     = 2
        }]
        # If the brake cannot be read, ABORT. Fail safe in one direction only.
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.error"
          Next        = "Aborted"
        }]
        Next = "KillSwitchGate"
      }

      KillSwitchGate = {
        Type = "Choice"
        Choices = [{
          Variable      = "$.kill_switch.enabled"
          BooleanEquals = false
          Next          = "Aborted"
        }]
        Default = "Assess"
      }

      # ---- Detect -------------------------------------------------------
      Assess = {
        Type       = "Task"
        Resource   = aws_lambda_function.recovery[0].arn
        Parameters = { action = "assess" }
        ResultPath = "$.assessment"
        Retry = [{
          ErrorEquals     = ["States.TaskFailed"]
          IntervalSeconds = 5
          MaxAttempts     = 2
          BackoffRate     = 2
        }]
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.error"
          Next        = "InconclusiveAssessment"
        }]
        Next = "ScopeGate"
      }

      # ---- Decide -------------------------------------------------------
      #
      # Four branches, and the "unknown" one is the one worth defending. When
      # the assessment cannot tell an outage from an empty stack, the workflow
      # does NOT guess. It stops and asks for a person. An automation that
      # treats "I could not tell" as "probably fine" is the same automation
      # that treats it as "probably a disaster" on a different Tuesday.
      ScopeGate = {
        Type = "Choice"
        Choices = [
          { Variable = "$.assessment.scope", StringEquals = "none", Next = "NoActionNeeded" },
          { Variable = "$.assessment.scope", StringEquals = "in_az", Next = "RecoverInAz" },
          { Variable = "$.assessment.scope", StringEquals = "unknown", Next = "InconclusiveAssessment" },
        ]
        Default = "ApprovalGate"
      }

      # ---- In-AZ recovery: reversible, so no gate -----------------------
      RecoverInAz = {
        Type     = "Task"
        Resource = aws_lambda_function.recovery[0].arn
        Parameters = {
          action      = "recover_in_az"
          "dry_run.$" = "$.kill_switch.dry_run"
        }
        ResultPath = "$.recovery"
        Next       = "NotifyInAzRecovery"
      }

      NotifyInAzRecovery = {
        Type     = "Task"
        Resource = aws_lambda_function.recovery[0].arn
        Parameters = {
          action      = "notify"
          outcome     = "in-AZ recovery performed; no regional failover"
          "dry_run.$" = "$.kill_switch.dry_run"
        }
        ResultPath = "$.notification"
        Next       = "Succeeded"
      }

      # ---- The gate before the irreversible thing -----------------------
      ApprovalGate = {
        Type = "Choice"
        Choices = [{
          Variable      = "$.kill_switch.require_approval"
          BooleanEquals = true
          Next          = "RequestApproval"
        }]
        Default = "ExecuteFailover"
      }

      RequestApproval = {
        Type     = "Task"
        Resource = "arn:${local.partition}:states:::sns:publish.waitForTaskToken"
        Parameters = {
          TopicArn = aws_sns_topic.dr.arn
          Subject  = "APPROVAL REQUIRED: Day 08 regional failover"
          Message = {
            instruction     = "A regional failover is proposed. Approve with: aws stepfunctions send-task-success --task-token TOKEN --task-output {} ; reject with: aws stepfunctions send-task-failure --task-token TOKEN --error Rejected"
            "assessment.$"  = "$.assessment"
            "task_token.$"  = "$$.Task.Token"
            "execution.$"   = "$$.Execution.Name"
            timeout_minutes = var.approval_timeout_minutes
            reminder        = "Approving starts an IRREVERSIBLE-IN-EFFECT change. Failback is not automated. Read lambda/recovery.py's failback docstring before you answer."
          }
        }
        # THIS TIMEOUT IS YOUR RTO WHEN APPROVAL IS REQUIRED. Not an estimate:
        # a ceiling. A timeout of 30 minutes means your worst-case approved
        # failover STARTS at minute 30.
        TimeoutSeconds = var.approval_timeout_minutes * 60
        Catch = [{
          ErrorEquals = ["States.Timeout"]
          ResultPath  = "$.error"
          Next        = "ApprovalTimedOut"
        }]
        ResultPath = "$.approval"
        Next       = "ExecuteFailover"
      }

      # ---- Execute ------------------------------------------------------
      ExecuteFailover = {
        Type     = "Task"
        Resource = aws_lambda_function.recovery[0].arn
        Parameters = {
          action      = "failover"
          "dry_run.$" = "$.kill_switch.dry_run"
          "reason.$"  = "$.assessment.rationale"
        }
        ResultPath = "$.failover"
        # NO RETRY. Deliberately. Retrying an action that may have half
        # succeeded is how you get two failovers, or a failover racing its own
        # rollback. If this step fails, a human reads the execution history.
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.error"
          Next        = "NotifyFailoverFailed"
        }]
        Next = "Verify"
      }

      # ---- Verify: the step that can call the whole thing a failure -----
      Verify = {
        Type     = "Task"
        Resource = aws_lambda_function.recovery[0].arn
        Parameters = {
          action      = "verify"
          "dry_run.$" = "$.kill_switch.dry_run"
        }
        ResultPath = "$.verification"
        Retry = [{
          ErrorEquals     = ["States.TaskFailed"]
          IntervalSeconds = 10
          MaxAttempts     = 2
          BackoffRate     = 2
        }]
        Next = "VerifyGate"
      }

      VerifyGate = {
        Type = "Choice"
        Choices = [{
          Variable      = "$.verification.verified"
          BooleanEquals = true
          Next          = "NotifySuccess"
        }]
        Default = "NotifyVerificationFailed"
      }

      # ---- Notify -------------------------------------------------------
      NotifySuccess = {
        Type     = "Task"
        Resource = aws_lambda_function.recovery[0].arn
        Parameters = {
          action      = "notify"
          outcome     = "failover executed AND verified"
          "dry_run.$" = "$.kill_switch.dry_run"
        }
        ResultPath = "$.notification"
        Next       = "Succeeded"
      }

      NotifyVerificationFailed = {
        Type     = "Task"
        Resource = aws_lambda_function.recovery[0].arn
        Parameters = {
          action      = "notify"
          outcome     = "FAILOVER EXECUTED BUT NOT VERIFIED — a human is required now"
          "dry_run.$" = "$.kill_switch.dry_run"
        }
        ResultPath = "$.notification"
        Next       = "VerificationFailed"
      }

      NotifyFailoverFailed = {
        Type     = "Task"
        Resource = aws_lambda_function.recovery[0].arn
        Parameters = {
          action      = "notify"
          outcome     = "FAILOVER STEP FAILED — state is unknown, do not retry blindly"
          "dry_run.$" = "$.kill_switch.dry_run"
        }
        ResultPath = "$.notification"
        Next       = "FailoverFailed"
      }

      # ---- Terminal states ----------------------------------------------
      Succeeded      = { Type = "Succeed" }
      NoActionNeeded = { Type = "Succeed" }

      Aborted = {
        Type  = "Fail"
        Error = "KillSwitchEngaged"
        Cause = "The recovery kill switch is not 'enabled', or could not be read. Nothing was changed."
      }

      InconclusiveAssessment = {
        Type  = "Fail"
        Error = "AssessmentInconclusive"
        Cause = "Could not classify the damage. A human is required. Guessing here is how a bad deploy becomes a regional failover."
      }

      ApprovalTimedOut = {
        Type  = "Fail"
        Error = "ApprovalTimedOut"
        Cause = "Nobody approved within the timeout. Nothing was changed. If this happens in a drill, the timeout is not the problem — the on-call response path is."
      }

      VerificationFailed = {
        Type  = "Fail"
        Error = "FailoverNotVerified"
        Cause = "The failover ran and verification did not pass. Do not assume it worked."
      }

      FailoverFailed = {
        Type  = "Fail"
        Error = "FailoverStepFailed"
        Cause = "The failover step itself failed. State may be partial. Read the execution history before doing anything else."
      }
    }
  })

  tags = {
    Name = "${local.prefix}-recovery-${local.suffix}"
  }
}


###############################################################################
# 16. THE SAME WORKFLOW, BUILT BADLY
#
# Gated behind create_insecure_examples. This is what DR-015 is for.
#
# It is one state. It calls failover. It has:
#   - no kill switch, so there is no way to stop it without a deploy
#   - no assessment, so it fails over whenever it is triggered, for any reason
#   - no approval gate, so nobody decides
#   - no verification, so it reports success when the API call succeeded
#   - RECOVERY_DRY_RUN forced to false in its own Lambda, so there is no
#     rehearsal mode at all
#
# Every one of these omissions is defensible in isolation and somebody argued
# for each of them, in good faith, on the grounds that the whole point of
# automation is that it works when nobody is awake. That argument is not
# stupid. It is just incomplete: the thing it optimises for is the case where
# the signal is correct, and the entire risk lives in the other case.
#
# Note that it shares the SAME zip file as the good workflow. The code is
# identical. The difference is entirely in configuration and in the states
# around it — which is the point Day 07 made about the naive responder and
# which is worth making twice, because "we reviewed the code" is not the same
# sentence as "we reviewed the deployment".
###############################################################################

resource "aws_lambda_function" "naive_recovery" {
  count = var.create_insecure_examples && var.enable_recovery_workflow ? 1 : 0

  function_name = "${local.prefix}-naive-recovery-${local.suffix}"
  role          = aws_iam_role.recovery[0].arn
  handler       = "recovery.lambda_handler"
  runtime       = "python3.12"
  timeout       = 120
  memory_size   = 256

  filename         = data.archive_file.recovery[0].output_path
  source_code_hash = data.archive_file.recovery[0].output_base64sha256

  environment {
    variables = {
      DR_REGION           = var.dr_region
      ASG_NAME            = local.asg_name
      TARGET_GROUP_ARN    = aws_lb_target_group.app.arn
      TABLE_NAME          = aws_dynamodb_table.orders.name
      HEALTH_CHECK_ID     = var.enable_route53_health_check ? aws_route53_health_check.primary[0].id : ""
      KILL_SWITCH_PARAM   = ""
      ACTIVE_REGION_PARAM = aws_ssm_parameter.active_region.name
      TOPIC_ARN           = aws_sns_topic.dr.arn
      RECOVERY_DRY_RUN    = "false"
      REQUIRE_APPROVAL    = "false"
    }
  }

  tags = {
    Name                   = "${local.prefix}-naive-recovery-${local.suffix}"
    "cbc:insecure-example" = "true"
  }
}

resource "aws_sfn_state_machine" "naive" {
  count = var.create_insecure_examples && var.enable_recovery_workflow ? 1 : 0

  name     = "${local.prefix}-naive-failover-${local.suffix}"
  role_arn = aws_iam_role.workflow[0].arn

  definition = jsonencode({
    Comment = "DELIBERATELY BAD. One state, no brake, no assessment, no approval, no verification. This is check DR-015."
    StartAt = "Failover"

    States = {
      Failover = {
        Type     = "Task"
        Resource = aws_lambda_function.naive_recovery[0].arn
        Parameters = {
          action  = "failover"
          dry_run = false
          reason  = "triggered"
        }
        End = true
      }
    }
  })

  tags = {
    Name                   = "${local.prefix}-naive-failover-${local.suffix}"
    "cbc:insecure-example" = "true"
  }
}

resource "aws_iam_role_policy" "workflow_naive" {
  count = var.create_insecure_examples && var.enable_recovery_workflow ? 1 : 0

  name = "${local.prefix}-workflow-naive-policy-${local.suffix}"
  role = aws_iam_role.workflow[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "InvokeTheNaiveWorker"
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = [aws_lambda_function.naive_recovery[0].arn]
    }]
  })
}
