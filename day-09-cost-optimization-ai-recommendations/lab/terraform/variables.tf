###############################################################################
# Day 09 — variables.tf
#
# Repo convention: any variable that costs money says so in its description,
# with the actual figure. Day 09 is the day the pattern was invented for.
#
# Day 08 introduced the third column — "what does the money BUY?" — because
# multi-AZ, cross-region and warm-standby all cost real money and the number
# on the line has to be readable next to the RTO improvement it delivers.
# Day 09 turns that around: many variables here toggle FREE guardrails
# (Budgets are free, Cost Anomaly Detection is free, tags are free,
# lifecycle rules are free), and the framing question changes to "what does
# NOT having this cost you next month?"
#
# Read the descriptions. The bill this stack can produce if these are all
# left at their permissive defaults is small in absolute terms and large in
# proportion to what the stack actually does, and that is the day's whole
# argument in a paragraph.
###############################################################################

###############################################################################
# Identity & region
###############################################################################

variable "aws_region" {
  description = <<-DESC
    Region for regional resources — EC2, VPC, EBS, log groups, load balancers.

    Pinned to us-east-1 by default because the auditor talks to the global
    billing APIs (Budgets, Cost Explorer, Cost Anomaly Detection, Savings
    Plans), and every one of those services is HOSTED at us-east-1 regardless
    of where the resources they describe live. A Cost Explorer client
    instantiated with region_name="eu-central-1" fails its first call with
    UnrecognizedClientException, and that is a real cross-team debugging
    session that has happened more than once.

    You can run the stack in a different region — the auditor still works,
    because it explicitly pins us-east-1 for the billing clients — but if the
    auditor is failing on ce.get_anomalies() and you have not changed
    anything, the region is what to check first.
  DESC
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must be a valid region string, e.g. us-east-1."
  }
}

variable "aws_profile" {
  description = "Named AWS CLI profile used to authenticate. Day 01 created this."
  type        = string
  default     = "bootcamp"

  validation {
    condition     = length(var.aws_profile) > 0
    error_message = "aws_profile cannot be empty. Run `aws configure --profile bootcamp` first."
  }
}

variable "owner" {
  description = <<-DESC
    Value for the Owner tag. Use your name or your team.

    This variable is not decorative. It is the string that lets the finance
    team, three months from now, look at a $47/month line on the bill and
    know whose Slack DM answers the question 'can we turn this off'. An
    account whose Cost Explorer breakdown by Owner shows 60% under the tag
    "unknown" has a cost governance problem before it has any specific waste
    problem, and check COST-004 exists for exactly that reason.

    The default is deliberately terrible. Change it.
  DESC
  type        = string
  default     = "bootcamp-student"

  validation {
    condition     = length(var.owner) >= 2 && length(var.owner) <= 64
    error_message = "owner must be between 2 and 64 characters."
  }
}

variable "notification_email" {
  description = <<-DESC
    Email address that receives budget alarms and cost anomaly notifications.

    This is the only variable with no usable default. Set it in
    terraform.tfvars before you apply.

    The SNS confirmation trap from Days 04, 06, 07 and 08 applies, and on
    this day it has a different shape than any previous day: an unconfirmed
    subscription means Cost Anomaly Detection continues to raise anomalies,
    they continue to be visible in the console (which nobody opens), and the
    monthly bill continues to grow. Nothing looks broken. Nothing pages
    anyone. The next thing to happen is a Slack message from the finance team
    on the 4th of next month.

    Cost incidents are the quietest kind, and the confirmation trap on this
    day is the reason.
  DESC
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.notification_email))
    error_message = "notification_email must be a valid email address, e.g. you@example.com."
  }
}

###############################################################################
# Deliberately broken examples — the audit's test bench
###############################################################################

variable "create_insecure_examples" {
  description = <<-DESC
    When true (the shipped default), create a handful of resources that make
    the auditor's Static-State-A findings reproducible against your own
    account: two unattached EBS volumes, two unassociated Elastic IPs, a
    previous-generation EC2 instance, a gp2 volume, a Classic Load Balancer,
    two log groups with no retention set, and an S3 bucket with no lifecycle
    rule.

    COST-BEARING, and this is one of the two lines in this file that is
    itself a lesson in cost. The insecure examples cost about $15/month to
    keep running — the Classic ELB alone is ~$16.20/month, the unassociated
    EIPs are ~$3.60/month EACH now that AWS bills unattached public IPv4
    addresses at $0.005/hour, and the EBS volumes are ~$8/month each. That
    is roughly the same price as the whole rest of this stack combined, from
    a bench built to demonstrate waste.

    THAT IS THE POINT. If it felt costless to leave switched on, the lesson
    would not land. Set this to false BEFORE you go to bed, and DEFINITELY
    before you go on holiday. The teardown-checklist.md file lists every
    resource this flag creates so you can verify nothing survived.

    Note that turning this off removes the resources; it does NOT remove
    similar-looking resources that you or someone else in the account created
    outside this stack. That is deliberate: the auditor scans the ACCOUNT,
    and a stack-shaped view of cost is exactly the view that misses the real
    finding.
  DESC
  type        = bool
  default     = true
}

###############################################################################
# Guardrails — the guardrails variables
#
# Every variable below toggles a FREE control. Every one of them is off by
# default, deliberately, so the auditor's static-state finding count reflects
# the state an account is in on its first day: everything works, nothing
# watches, nobody is paged.
###############################################################################

variable "enable_budget" {
  description = <<-DESC
    Create an AWS Budget for the account.

    FREE. AWS provides two Zero Spend Budgets and two additional cost/usage
    budgets per account at no charge, and every subsequent budget is $0.02
    per day. This one is well within the free tier.

    A budget without a notification is a decorative object. AWS Budgets
    supports Actual (cost that has occurred) and Forecasted (cost projected
    from current run rate) thresholds, and the two answer different
    questions: Actual tells you you have already spent the money, Forecasted
    tells you you are on track to. Both are useful. Neither, alone, is
    enough — a forecasted alert without an actual alert misses the incident
    that started at 4pm on the last day of the month.

    Check COST-001 fires when this is false. Turn it on and re-run.

    Default false, deliberately, so the shipped Static-State-A finding is
    honest against your own stack rather than against a strawman.
  DESC
  type        = bool
  default     = false
}

variable "budget_monthly_limit_usd" {
  description = <<-DESC
    The dollar amount the budget alarms against, in USD, per month.

    Ignored when enable_budget is false.

    Pick this by asking "what is the number I want to know we have crossed",
    not by asking "what is a comfortable amount for this account to spend".
    A budget set to 3x current spend is a budget that alarms after the
    incident, and a budget set below current spend alarms every month for
    reasons unrelated to the incident. Both are trained-to-ignore states.

    The lab default is $20, which is roughly what this stack costs to run
    for a week with insecure examples on, and which will actually alarm.
  DESC
  type        = number
  default     = 20

  validation {
    condition     = var.budget_monthly_limit_usd > 0 && var.budget_monthly_limit_usd <= 100000
    error_message = "budget_monthly_limit_usd must be between 1 and 100000."
  }
}

variable "budget_notifications" {
  description = <<-DESC
    Notification thresholds for the budget. Each entry represents one
    subscription: a threshold percent, a comparison operator, and a
    notification type (ACTUAL or FORECASTED).

    FREE.

    The list validation below enforces that a budget carries at least ONE
    notification — which is what makes check COST-002 SILENT BY DESIGN
    against this stack. No shipped default and no typo can produce a budget
    with an empty notifications list, because the plan refuses to. That does
    not make the check useless: it still fires on budgets somebody in the
    account created via console click or another module. It just cannot fire
    on the plan you write here.

    The default list is a two-tier notification: 80% actual (warning) and
    100% actual (critical). Add a FORECASTED entry if you want to be told
    before you cross the line rather than as you cross it, which is nearly
    always what you actually want and nearly never what you first set up.
  DESC
  type = list(object({
    threshold           = number
    comparison_operator = string
    notification_type   = string
  }))
  default = [
    {
      threshold           = 80
      comparison_operator = "GREATER_THAN"
      notification_type   = "ACTUAL"
    },
    {
      threshold           = 100
      comparison_operator = "GREATER_THAN"
      notification_type   = "ACTUAL"
    },
  ]

  validation {
    condition     = length(var.budget_notifications) >= 1
    error_message = "budget_notifications must contain at least one threshold. A budget with no notifications is a decorative object. This is the validation that makes COST-002 silent by design."
  }
}

variable "enable_cost_anomaly_monitor" {
  description = <<-DESC
    Create an AWS Cost Anomaly Detection monitor for the whole account.

    FREE. Cost Anomaly Detection is entirely free and always has been, and
    the number of accounts that do not have it enabled is one of the
    quietest cost governance facts in the industry.

    A monitor is the ML detector. It watches your spend patterns for the
    first ~10 days to learn a baseline, then flags departures from that
    baseline. It requires no configuration beyond turning it on — the "AI"
    in the syllabus description is not a metaphor here, the ML runs on
    Amazon's side and you get anomaly records with root causes attached.

    A monitor with no subscription is a monitor that speaks into the void —
    see notification_email above for the confirmation trap. Turn this on
    AND set notification_email AND confirm the SNS subscription. Two out of
    three is the shape of most real accounts and it is the reason cost
    incidents surface via the finance team.

    Check COST-003 fires when this is false. Default false, so the shipped
    Static-State-A finding is honest.
  DESC
  type        = bool
  default     = false
}

variable "cost_anomaly_threshold_usd" {
  description = <<-DESC
    Absolute dollar threshold above which a Cost Anomaly Detection anomaly
    triggers a notification. Anomalies below this are still recorded in the
    console; they just do not page anyone.

    Ignored when enable_cost_anomaly_monitor is false.

    The lab default is $10. In production this is one of the numbers people
    get most wrong: too low and every seasonal fluctuation is a page, too
    high and the incident that matters ($200/day for six days = $1,200, on
    an account whose usual daily spend was $50) sits below the line for the
    first week. There is no universally correct value, and that is the
    lesson. Pick it by looking at the last three months of Cost Explorer
    and asking "what is a departure from this that I want to know about".
  DESC
  type        = number
  default     = 10

  validation {
    condition     = var.cost_anomaly_threshold_usd >= 0 && var.cost_anomaly_threshold_usd <= 100000
    error_message = "cost_anomaly_threshold_usd must be between 0 and 100000."
  }
}

###############################################################################
# CloudWatch Logs — the accumulator
#
# Log groups without retention are the slowest cost decay in the account.
# They start at nothing. They cost $0.50/GB ingested, $0.03/GB stored per
# month, and are literally uncapped. Twelve months later they are the third
# line on the bill and nobody remembers creating them.
###############################################################################

variable "log_retention_days" {
  description = <<-DESC
    Retention, in days, for CloudWatch log groups this stack creates.

    FREE the moment it is not zero.

    0 means "never expire", which is the AWS default when the attribute is
    omitted, which is what happens in every log group created by AWS SDK
    calls that did not think to set it — Lambda functions, ECS tasks, RDS
    slow query logs, ALB access logs. AWS CloudWatch Logs pricing is $0.03
    per GB per month stored, forever, and the checks that catch this
    (COST-013 here) exist because the resulting bill line grows slowly enough
    to escape every quarterly review until the year that produces "why is
    CloudWatch $8,000/month now".

    Common values: 7 for verbose debug logs, 30 for application logs
    reviewed weekly, 90 for compliance logs, 365 for audit logs required by
    a regulator. NEVER 0 unless you have a specific compliance reason and
    a written statement of what will archive them.

    Default 30 for this stack. create_insecure_examples produces two log
    groups whose retention is deliberately unset, so COST-013 fires against
    them regardless of what you put here.
  DESC
  type        = number
  default     = 30

  validation {
    condition     = var.log_retention_days == 0 || (var.log_retention_days >= 1 && var.log_retention_days <= 3653)
    error_message = "log_retention_days must be 0 (never expire, DISCOURAGED) or between 1 and 3653 (10 years)."
  }
}

###############################################################################
# S3 lifecycle — the tier that stays STANDARD
#
# A bucket without lifecycle is STANDARD storage forever, at $0.023/GB.
# STANDARD-IA is $0.0125, Glacier Instant Retrieval is $0.004, Glacier Deep
# Archive is $0.00099. The bucket a Lambda writes into and never reads from
# is the archetype: 300 GB accumulated over three years at STANDARD is $22.90
# every month, in perpetuity, for data nothing has touched since it was
# written.
###############################################################################

variable "enable_bucket_lifecycle" {
  description = <<-DESC
    Attach a lifecycle rule to this stack's S3 bucket.

    FREE. Lifecycle rules cost nothing to define and save money as they run.

    When true, the bucket transitions objects to STANDARD-IA after 30 days,
    to Glacier Instant Retrieval after 90 days, and expires them after
    365 days. Those numbers are illustrative; production values depend on
    how quickly access patterns drop off, which is what you should measure
    with an S3 Storage Lens configuration before you set them.

    Check COST-014 fires when this is false. Default false.
  DESC
  type        = bool
  default     = false
}

###############################################################################
# VPC & networking — the endpoints that cost nothing
###############################################################################

variable "vpc_cidr" {
  description = "CIDR block for the VPC. A /24 is enough for this stack — no ASG scale, no data tier, just enough network to hang a NAT gateway from so COST-012 has something to see."
  type        = string
  default     = "10.90.0.0/24"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr must be a valid CIDR block, e.g. 10.90.0.0/24."
  }
}

variable "enable_vpc_endpoints" {
  description = <<-DESC
    Create GATEWAY endpoints for S3 and DynamoDB in the VPC.

    FREE. Gateway endpoints for S3 and DynamoDB are the two free endpoints
    in AWS, and every other cost decision about VPC networking should start
    with "did we turn these on". Interface endpoints (for KMS, Secrets
    Manager, and everything else) cost ~$7.30/month per AZ per endpoint,
    which is a real trade against NAT costs.

    Without these, any private-subnet traffic to S3 or DynamoDB traverses
    the NAT gateway and is billed at $0.045/GB. With them, the same traffic
    stays on the VPC network and is billed at $0.00/GB. On a busy stack
    that reads from S3 (backup restores, log shipping, artifact downloads,
    yum/dnf updates via the AL2023 mirror in S3), this line item is the
    single largest cost that a five-minute config change removes.

    Check COST-012 fires when a VPC has a NAT gateway AND no S3/DynamoDB
    gateway endpoints — because the pattern is almost always accidental
    rather than deliberate. Default false, so COST-012 fires against your
    own stack until you turn it on and re-run.
  DESC
  type        = bool
  default     = false
}

variable "enable_nat_gateway" {
  description = <<-DESC
    Create a NAT gateway in the public subnet so private-subnet resources
    have outbound internet.

    COST-BEARING. ~$32.85/month ($0.045/hour) plus $0.045/GB processed.

    Off by default in this lab because the stack does not launch anything
    that needs outbound internet — the whole point of Day 09 is to look at
    the bill, and a NAT gateway that exists only to make check COST-012
    fire is expensive teaching material.

    Turn it on when you want to see COST-012 in action (a NAT gateway with
    no S3/DynamoDB gateway endpoints alongside it), then turn it OFF again
    when you are done. Leaving a NAT gateway running overnight for a lab
    that ended yesterday is exactly the pattern this day exists to catch.
  DESC
  type        = bool
  default     = false
}

###############################################################################
# Compute — kept minimal, deliberately
###############################################################################

variable "instance_type" {
  description = <<-DESC
    EC2 instance type for this stack's application instance.

    COST-BEARING. t3.micro is ~$0.0104/hour (~$7.59/month) in us-east-1.

    Default t3.micro because that is the current-generation, correct choice.
    create_insecure_examples ALSO creates a t2.micro alongside — the
    previous-generation predecessor — so COST-009 has something to fire on
    without breaking your correct instance.

    A previous-generation instance is not always wrong. Sometimes t2's
    burst-on-demand model fits a workload better than t3's baseline-plus-
    unlimited model. But it is nearly always UNEXAMINED, and unexamined
    is the fault COST-009 flags. Fixing it may mean typing "t3" instead of
    "t2"; it may equally mean writing a comment that explains why t2 was
    kept. Both are valid; silence is not.
  DESC
  type        = string
  default     = "t3.micro"

  validation {
    condition     = can(regex("^[a-z][0-9][a-z]*\\.[a-z0-9]+$", var.instance_type))
    error_message = "instance_type must look like a valid instance type, e.g. t3.micro."
  }
}

variable "root_volume_type" {
  description = <<-DESC
    EBS volume type for the root disk on the application instance.

    COST-BEARING, and this is the one where the correct answer is strictly
    cheaper AND strictly faster.

    gp2  ~$0.10/GB/month, 3 IOPS per GB baseline (min 100, max 16000),
         bursts to 3000. The Amazon Linux default for years. Superseded.
    gp3  ~$0.08/GB/month, 3000 IOPS baseline (regardless of size), 125 MB/s
         throughput baseline. Faster minimum performance, ~20% cheaper.

    There is no workload for which gp2 is preferable to gp3 today, and yet
    the majority of EBS volumes in the majority of accounts are still gp2,
    because migration is manual and nobody has a reason to do it during a
    quiet week. Check COST-010 exists to be that reason.

    Default gp3. create_insecure_examples ALSO creates a gp2 volume so
    COST-010 has something to fire on.
  DESC
  type        = string
  default     = "gp3"

  validation {
    condition     = contains(["gp2", "gp3", "io1", "io2", "st1", "sc1"], var.root_volume_type)
    error_message = "root_volume_type must be one of: gp2, gp3, io1, io2, st1, sc1."
  }
}

variable "root_volume_size_gb" {
  description = "EBS root volume size in GB. Kept at the minimum useful size — the whole point of this stack is that a smaller volume costs less, and this is not the day to demonstrate that with an accident."
  type        = number
  default     = 8

  validation {
    condition     = var.root_volume_size_gb >= 8 && var.root_volume_size_gb <= 100
    error_message = "root_volume_size_gb must be between 8 and 100 for this lab."
  }
}

###############################################################################
# Age thresholds — the numbers the auditor uses
#
# These do not configure any AWS resource. They tell the auditor which
# resources are "old enough to be waste" and which are just "recent".
# They are exposed here so the same numbers appear in the outputs and in
# the auditor's --help, rather than being one hardcoded constant in Python.
###############################################################################

variable "volume_orphan_days" {
  description = <<-DESC
    An unattached EBS volume older than this is a COST-005 finding.

    FREE.

    Why 7? Anything younger might be a resource somebody detached this
    morning intending to reattach this afternoon. Anything older is
    forgotten. The check is written to be reproducible — you WILL see
    unattached volumes from insecure examples fire COST-005 during the
    lab — so it does not depend on this default being high; it depends on
    the auditor being told about a value it can cite.
  DESC
  type        = number
  default     = 7

  validation {
    condition     = var.volume_orphan_days >= 1 && var.volume_orphan_days <= 365
    error_message = "volume_orphan_days must be between 1 and 365."
  }
}

variable "eip_orphan_days" {
  description = <<-DESC
    An unassociated Elastic IP older than this is a COST-006 finding.

    FREE to declare — but note the fault it detects has been billable since
    February 2024. Unattached Elastic IPs are $0.005/hour (~$3.60/month)
    EACH, and public IPv4 addresses generally are now billed the same way
    whether they are attached to something or not. An account with 20
    forgotten EIPs across regions is $70+/month for nothing, and the
    "how did that get there" answer is usually "an old NAT gateway from a
    stack that was destroyed with terraform destroy --refuse-to-destroy".
  DESC
  type        = number
  default     = 7

  validation {
    condition     = var.eip_orphan_days >= 1 && var.eip_orphan_days <= 365
    error_message = "eip_orphan_days must be between 1 and 365."
  }
}

variable "snapshot_retention_days" {
  description = <<-DESC
    An EBS snapshot older than this is a COST-007 finding.

    FREE to declare. The FAULT it detects — snapshots accumulating past
    their useful life — is $0.05/GB/month for standard EBS snapshots and
    slightly less for Archive tier, and it is one of the most reliably
    silent cost decays in a mature account: every automated backup rule
    keeps writing, and unless a companion rule ages the old ones out,
    yesterday's backup exists forever.

    The default 90 is illustrative. Your value depends on RPO discussions
    from Day 08 (what you need to be able to restore FROM), on regulatory
    retention (what you are REQUIRED to keep), and on the cost of storage
    growing linearly with account age. Pick it deliberately and re-audit
    when the answer changes.
  DESC
  type        = number
  default     = 90

  validation {
    condition     = var.snapshot_retention_days >= 1 && var.snapshot_retention_days <= 3653
    error_message = "snapshot_retention_days must be between 1 and 3653 (10 years)."
  }
}

variable "instance_stopped_days" {
  description = <<-DESC
    An EC2 instance in the stopped state for longer than this is a COST-008
    finding.

    FREE to declare, and the fault it flags is more expensive than most
    people expect. A stopped EC2 instance does not bill for compute — that
    part everyone knows. It DOES bill for every EBS volume attached to it,
    for its Elastic IP (if any), and for its private IP after October 2024
    if it has a persistent public one. A stopped m5.4xlarge with a 500 GB
    gp3 root volume is $40/month of storage and $3.60/month of EIP for a
    machine executing nothing.

    "We only need this instance during month-end" is a real requirement
    and it has a real answer: `terraform apply -var=`` create_month_end=true,
    or an AMI you re-launch from, or a spot instance you accept losing.
    "stopped" is nearly always the wrong answer to that requirement.
  DESC
  type        = number
  default     = 30

  validation {
    condition     = var.instance_stopped_days >= 1 && var.instance_stopped_days <= 3653
    error_message = "instance_stopped_days must be between 1 and 3653."
  }
}

variable "previous_gen_instance_families" {
  description = <<-DESC
    Instance families the auditor flags as previous-generation for COST-009.

    Defaults to the currently-superseded lineage: t2, m3, m4, m5, c3, c4,
    c5, r3, r4. Each of these has a strict successor whose per-hour cost is
    roughly the same or lower and whose baseline performance is
    substantially higher (t3, m6/m7, c6/c7, r6/r7).

    An instance in one of these families is not necessarily wrong — an m5
    launched three years ago on a Reserved Instance is exactly what it
    should be, and swapping it for an m7 forfeits money you have already
    paid. The check flags them for CONSIDERATION, not for automatic
    replacement, which is what the auditor's remediation language reflects.

    Exposed as a variable so that when AWS releases the next generation and
    m6/m7 join this list, the check does not need a code change.
  DESC
  type        = list(string)
  default     = ["t2", "m3", "m4", "m5", "c3", "c4", "c5", "r3", "r4"]

  validation {
    condition     = length(var.previous_gen_instance_families) >= 1
    error_message = "previous_gen_instance_families must contain at least one family."
  }
}

variable "long_running_instance_days" {
  description = <<-DESC
    An EC2 instance running (not stopped) for longer than this without any
    Savings Plan or Reserved Instance coverage is a COST-015 finding.

    FREE to declare. The fault it flags is roughly 30% of the price you
    could have paid instead — a Compute Savings Plan for the baseline
    consumption of an always-on workload is the single largest cost
    optimisation in most accounts, and it is the one that leaves nothing
    running that was not already running.

    Default 30 days. Below this, a workload might still be probationary
    (an experiment, a demo, a proof of concept nobody has closed yet), and
    committing to a year of Savings Plan is genuinely wrong. Above it,
    the case for a commitment is arithmetic rather than editorial.
  DESC
  type        = number
  default     = 30

  validation {
    condition     = var.long_running_instance_days >= 1 && var.long_running_instance_days <= 3653
    error_message = "long_running_instance_days must be between 1 and 3653."
  }
}

variable "tag_coverage_threshold_percent" {
  description = <<-DESC
    Minimum percentage of resources (of the kinds this auditor examines)
    that must carry ALL of the critical cost allocation tags: Project, Owner.
    Coverage below this fires COST-004.

    FREE.

    Default 90. A resource without Owner is a bill without a stakeholder;
    a resource without Project is a bill nothing rolls up into; both are
    findings the finance team will send at the end of the month rather
    than during it.

    Setting it to 100 is defensible for a production account, and 80 is
    defensible for an account with a large legacy footprint that is being
    tagged incrementally. Setting it below 50 is essentially turning the
    check off, which is a decision worth naming.
  DESC
  type        = number
  default     = 90

  validation {
    condition     = var.tag_coverage_threshold_percent >= 1 && var.tag_coverage_threshold_percent <= 100
    error_message = "tag_coverage_threshold_percent must be between 1 and 100."
  }
}

variable "anomaly_triage_days" {
  description = <<-DESC
    A cost anomaly that has been open (without Feedback) for longer than
    this fires COST-016 — the day's central check.

    FREE.

    Default 7. The point of the check is not that a triage was slow; it is
    that no triage happened at all. Seven days is a working week — a week
    in which somebody, in principle, could have opened the console, looked
    at the anomaly, and marked it "Planned Activity" (a promo campaign that
    justified the spike), "Yes" (a real cost issue) or "No" (a false alarm).
    None of those responses require action; they require ATTENTION.

    An account whose anomalies have been open for 30, 60, 90 days is not an
    account that missed an incident. It is an account whose monitor is
    speaking to itself, which is Day 09's thesis in one line. The lab's
    state-C demonstration exists to make this concrete.
  DESC
  type        = number
  default     = 7

  validation {
    condition     = var.anomaly_triage_days >= 1 && var.anomaly_triage_days <= 365
    error_message = "anomaly_triage_days must be between 1 and 365."
  }
}
