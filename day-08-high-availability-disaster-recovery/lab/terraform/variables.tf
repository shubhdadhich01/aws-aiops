###############################################################################
# Day 08 — variables.tf
#
# Repo convention: any variable that costs money says so in its description,
# with the actual figure.
#
# Day 08 is the first day in this repo where THE CORRECT ARCHITECTURE IS
# GENUINELY EXPENSIVE. On Days 01–07 the secure option and the cheap option
# were usually the same option: encryption is free, least privilege is free,
# log file validation is free. Here they are not. Multi-AZ RDS costs exactly
# twice as much as single-AZ. A warm standby is a second environment billing
# continuously to serve zero traffic. Cross-region replication is billed per
# GB transferred AND per GB stored, in both regions, forever.
#
# So this file has a third column that the earlier days did not need: for the
# resilience toggles, the description states what the money BUYS in RTO or RPO
# terms. "$24.82/month" is not a decision. "$12.41/month more, and it turns a
# 40-minute restore-from-snapshot into a 60–120 second automatic failover" is
# a decision, and it is one that some workloads should decline.
#
# Pricing note for the whole file: indicative us-east-1 on-demand figures at
# time of writing. Verify against the pricing pages. Data transfer between
# regions in particular is the line people forget, and it is the line that
# makes cross-region replication cost more than the storage it replicates.
###############################################################################

###############################################################################
# Identity & regions
###############################################################################

variable "aws_region" {
  description = "Primary AWS region. Everything without an explicit provider argument lands here."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must be a valid region string, e.g. us-east-1."
  }
}

variable "dr_region" {
  description = <<-DESC
    Disaster-recovery region, used by the S3 replica bucket, the DynamoDB
    global table replica and (at CP2) the backup copy vault.

    COST-BEARING INDIRECTLY. Nothing here is priced per region, but every byte
    that crosses this boundary is billed as inter-region data transfer at
    ~$0.02/GB out of us-east-1, and every byte that lands is billed again as
    storage in the second region. Replication does not move data, it DUPLICATES
    it: you pay transfer once and storage twice, permanently.

    Pick a region far enough away to be a different blast radius. us-east-1 and
    us-east-2 are ~500 km apart and share very little; us-east-1 and us-west-2
    share less still. The genuine constraint is usually regulatory (data
    residency) or latency (a synchronous write across 4,000 km costs you ~70ms
    of round trip), not geology.

    MUST NOT EQUAL aws_region. A "DR region" that is the primary region is a
    second copy inside the same blast radius, which is the failure mode this
    entire day exists to talk about.
  DESC
  type        = string
  default     = "us-west-2"

  validation {
    condition     = can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]$", var.dr_region))
    error_message = "dr_region must be a valid region string, e.g. us-west-2."
  }

  # Cross-variable validation, which needs Terraform >= 1.9. This is the line
  # that makes check DR-011 SILENT BY DESIGN: no shipped default and no typo
  # can point a DR copy at the region it is copying from, because the plan
  # refuses to produce one.
  #
  # It is not a hypothetical fault. S3 Same-Region Replication is a real,
  # legitimate feature (compliance, log aggregation, account separation), and
  # an AWS Backup copy rule will happily target a vault in the source region.
  # Both get used as "DR" by people who were solving a different problem
  # yesterday, and both produce a second copy inside the same blast radius.
  validation {
    condition     = var.dr_region != var.aws_region
    error_message = "dr_region must not equal aws_region. A DR copy in the primary region is a second copy inside the same blast radius — which is the one failure this entire day exists to avoid."
  }
}

variable "aws_profile" {
  description = "Named AWS CLI profile used to authenticate both providers. Day 01 created this."
  type        = string
  default     = "bootcamp"

  validation {
    condition     = length(var.aws_profile) > 0
    error_message = "aws_profile cannot be empty. Run `aws configure --profile bootcamp` first."
  }
}

variable "owner" {
  description = "Value for the Owner tag. Use your name or team so account-wide cost reports can attribute this spend to you — and so the DR region's resources are attributable at all."
  type        = string
  default     = "bootcamp-student"

  validation {
    condition     = length(var.owner) >= 2 && length(var.owner) <= 64
    error_message = "owner must be between 2 and 64 characters."
  }
}

###############################################################################
# Notification — the one variable you MUST set
###############################################################################

variable "notification_email" {
  description = <<-DESC
    Email address that receives failover notifications and, from CP2, a record
    of every automated recovery action.

    This is the only variable with no usable default. Set it in
    terraform.tfvars before you apply.

    The SNS confirmation trap from Days 04, 06 and 07 applies, and on this day
    it has a specific shape: an unconfirmed subscription means your recovery
    workflow can fail over to the DR region at 03:00 and nobody is told. The
    failover succeeds. The notification is discarded. You discover it on
    Monday from the bill, or from the data written to a region you were not
    reading from.

    Confirm the subscription BEFORE you run the failover exercise, not after.
  DESC
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.notification_email))
    error_message = "notification_email must be a valid email address, e.g. you@example.com."
  }
}

###############################################################################
# The declared numbers — read this block before you set anything below it
#
# These two variables are the intellectual centre of the day. They are the
# numbers you WRITE DOWN. The lab then makes you MEASURE the same numbers and
# compare. In most first attempts the measurement is between two and ten times
# the declaration, and the gap is the lesson.
###############################################################################

variable "rto_target_minutes" {
  description = <<-DESC
    Your DECLARED Recovery Time Objective, in minutes: the maximum time you
    claim the service can be unavailable before recovery completes.

    This variable does not configure anything. It is not a setting. Nothing in
    AWS reads it. It exists to be written into outputs, into the failover
    workflow's notification text, and into the auditor's DR-016 check — so
    that when you time the actual failover in lab step 7 you have something to
    be wrong about.

    That is the point. RTO is a MEASUREMENT of a procedure. Setting it in a
    tfvars file feels like configuration and is closer to a New Year
    resolution. The only honest RTO is the one from the last time somebody ran
    the procedure under time pressure, and if you have never run it, you do
    not have an RTO — you have a target.

    What actually consumes RTO, roughly in order of how much people
    underestimate it:
      DNS TTL expiry               up to route53_ttl seconds, always
      Detection and decision       often longer than the technical failover
      Data reconciliation          unbounded if replication was asynchronous
      Connection pool draining     minutes, and invisible in every diagram
      Compute start                the part everyone measures, usually 2-4 min

    Default 30 minutes because it is achievable for this stack and because it
    is roughly what people guess before measuring.
  DESC
  type        = number
  default     = 30

  validation {
    condition     = var.rto_target_minutes > 0 && var.rto_target_minutes <= 1440
    error_message = "rto_target_minutes must be between 1 and 1440 (24 hours). If your real RTO is longer than a day, say so in a runbook rather than here."
  }
}

variable "rpo_target_minutes" {
  description = <<-DESC
    Your DECLARED Recovery Point Objective, in minutes: the maximum amount of
    data, measured in TIME, you are willing to lose.

    Like rto_target_minutes this configures nothing directly, but unlike RTO it
    is bounded by things in this file that you CAN set:

      DynamoDB PITR                 ~5 minutes, continuous
      DynamoDB global tables        typically under 1 second of replication lag
      S3 CRR (default)              minutes, NOT guaranteed, no SLA
      S3 CRR + Replication Time     99.99% of objects within 15 minutes, with
        Control (RTC)               an SLA, at ~$0.015/GB extra
      RDS automated backups         5 minutes via transaction log, IF
                                    point-in-time restore is available
      Daily snapshots only          up to 24 hours

    Read that list again with the pricing in mind. The difference between "a
    few minutes, probably" and "15 minutes, contractually" is RTC, and it costs
    real money per GB. The difference between "5 minutes" and "24 hours" for
    RDS is whether backup retention is greater than zero.

    A stated RPO shorter than your slowest replication path is a fiction, and
    check DR-011 exists to catch exactly that.

    Default 60 minutes.
  DESC
  type        = number
  default     = 60

  validation {
    condition     = var.rpo_target_minutes > 0 && var.rpo_target_minutes <= 10080
    error_message = "rpo_target_minutes must be between 1 and 10080 (7 days)."
  }
}

###############################################################################
# Network
###############################################################################

variable "vpc_cidr" {
  description = "CIDR block for the primary VPC. A /16 gives room for three AZs of /24 public and /24 private subnets with most of the space unused, which is the correct amount of unused space."
  type        = string
  default     = "10.80.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0)) && tonumber(split("/", var.vpc_cidr)[1]) <= 20
    error_message = "vpc_cidr must be a valid CIDR block of /20 or larger (smaller prefix number), e.g. 10.80.0.0/16."
  }
}

variable "az_count" {
  description = <<-DESC
    How many Availability Zones the stack spans. 2 or 3.

    FREE. Spreading across AZs costs nothing in itself — the cost is in what
    you place in each one (a NAT gateway per AZ, an RDS standby) and in
    cross-AZ data transfer at ~$0.01/GB in each direction, which is $0.02/GB
    round trip and which is genuinely invisible until it is 15% of the bill.

    Two AZs survives one AZ failure. Three AZs survives one AZ failure while
    still having quorum, which matters for anything doing leader election
    (etcd, ZooKeeper, Kafka controllers, some RDS/Aurora topologies) and does
    not matter at all for a stateless web tier behind a load balancer.

    Default 2, because that is the correct answer for this stack and because
    three NAT gateways is $97/month.
  DESC
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3. One AZ is not a high-availability architecture; more than three is rarely useful and every AZ multiplies your per-AZ costs."
  }
}

variable "nat_gateway_strategy" {
  description = <<-DESC
    How private subnets reach the internet. One of: none, single, per_az.

    COST-BEARING, and this is the single most expensive line in the day.

      none     $0.00/month. Private instances cannot reach the internet at
               all. Fine for this lab if you do not need yum/dnf, and this
               stack's user-data is written so it still boots. VPC endpoints
               are the grown-up answer to this and cost ~$7.30/month per
               interface endpoint per AZ, which is cheaper than NAT once you
               need more than a couple.

      single   ~$32.85/month ($0.045/hour) plus ~$0.045/GB processed.
               ONE NAT gateway in the first AZ, shared by all private subnets.

               READ THIS TWICE: a single NAT gateway is a single-AZ dependency
               inside an architecture you are calling multi-AZ. If that AZ
               fails, instances in the OTHER AZ stay up, pass their health
               checks, serve traffic, and cannot reach the internet. The load
               balancer is happy. The dashboard is green. Everything that
               calls an external API fails.

               This is check DR-002, and it is the most common real finding on
               this list — because it is what every "cost optimisation" pass
               does to a correctly-built VPC, and the person who does it is
               not wrong about the money.

      per_az   ~$65.70/month for two AZs, ~$98.55 for three, plus processing.
               One NAT gateway per AZ, each private subnet routing to its own.
               The correct answer for production. Also removes the cross-AZ
               data transfer charge on NAT traffic, which claws back a little.

    Default "single". Deliberately. It is the wrong answer for high
    availability and the right answer for a lab bill, and leaving it as the
    default means the auditor has something true to say about your own stack
    rather than about a strawman. Set it to per_az, re-run the audit, and
    watch DR-002 disappear — that round trip is lab step 9.
  DESC
  type        = string
  default     = "single"

  validation {
    condition     = contains(["none", "single", "per_az"], var.nat_gateway_strategy)
    error_message = "nat_gateway_strategy must be one of: none, single, per_az."
  }
}

###############################################################################
# Compute tier
###############################################################################

variable "instance_type" {
  description = <<-DESC
    EC2 instance type for the Auto Scaling group.

    COST-BEARING. t3.micro is ~$0.0104/hour (~$7.59/month) on-demand in
    us-east-1, and is free-tier eligible for 750 hours/month in the first 12
    months of a new account — which is exactly one instance, so a desired
    capacity of 2 puts you over the free tier even in month one.

    Burstable instances and DR interact badly in a way worth knowing: t3
    instances accrue CPU credits over time, and a warm-standby instance that
    has been idle for weeks has a full credit balance, while an instance
    launched fresh during a failover starts with only its launch credits. In
    unlimited mode (the default for t3) you are charged for the surplus at
    ~$0.05 per vCPU-hour. A failover that launches twenty instances into a
    traffic spike can therefore produce a surprising bill on top of a bad day.
  DESC
  type        = string
  default     = "t3.micro"

  validation {
    condition     = can(regex("^[a-z][0-9][a-z]*\\.[a-z0-9]+$", var.instance_type))
    error_message = "instance_type must look like a valid instance type, e.g. t3.micro."
  }
}

variable "asg_min_size" {
  description = "Minimum ASG size. Setting this to 0 saves money and means an AZ failure that takes the last instance leaves you with nothing to health-check; scale-out from zero is slower than scale-out from one."
  type        = number
  default     = 2

  validation {
    condition     = var.asg_min_size >= 0 && var.asg_min_size <= 10
    error_message = "asg_min_size must be between 0 and 10 for this lab."
  }
}

variable "asg_max_size" {
  description = "Maximum ASG size. COST-BEARING as a ceiling: this is the largest bill this stack can produce, at instance_type price times this number. Keep it small in a lab. In production, max_size that equals desired_capacity means you cannot scale out during a failover, which is a common and quiet way to turn an AZ failure into an outage."
  type        = number
  default     = 4

  validation {
    condition     = var.asg_max_size >= 1 && var.asg_max_size <= 10
    error_message = "asg_max_size must be between 1 and 10 for this lab."
  }
}

variable "asg_desired_capacity" {
  description = <<-DESC
    Desired ASG capacity. COST-BEARING: this is the number you are actually
    paying for, at instance_type price each.

    Default 2 — one per AZ. This is the smallest number that demonstrates AZ
    failure at all, and it hides a capacity trap worth naming: if two
    instances serve your traffic and one AZ fails, the survivor is now taking
    100% of the load it was sized at 50% for. "Multi-AZ" and "multi-AZ with
    enough headroom to survive losing an AZ" are different architectures with
    different bills, and the second one is what you actually promised.

    N+1 across two AZs means running at 50% utilisation. Across three AZs it
    means 67%. That is the real reason three AZs is often cheaper than two for
    anything large.
  DESC
  type        = number
  default     = 2

  validation {
    condition     = var.asg_desired_capacity >= 1 && var.asg_desired_capacity <= 10
    error_message = "asg_desired_capacity must be between 1 and 10 for this lab."
  }
}

variable "asg_health_check_type" {
  description = <<-DESC
    "ELB" or "EC2". FREE either way, and the difference is the whole lesson.

    "EC2" — the default if you omit it, and the reason this variable has a
    comment this long. The ASG replaces an instance when EC2 says the instance
    is unhealthy: the hypervisor lost it, the status checks failed, someone
    terminated it. It knows nothing about your application. A process that has
    deadlocked, an application that returns 500 to every request, a container
    that exited leaving the instance up — all of these are a HEALTHY instance
    to an EC2 health check, forever.

    "ELB" — the ASG additionally honours the target group's health check, so
    an instance that fails your HTTP check gets terminated and replaced. This
    is the line most stacks omit and it is check DR-003.

    The failure mode of omitting it is specific and worth recognising: the
    load balancer correctly stops sending traffic to the broken instance, so
    the SERVICE looks fine, and the ASG never replaces it because EC2 thinks
    it is alive. You now have an instance you are paying for that serves
    nothing, permanently, and your effective capacity is silently N-1. Nothing
    alarms. This survives for months.

    Default "ELB", because it is right. create_insecure_examples builds a
    second ASG with "EC2" so the auditor has both to compare.
  DESC
  type        = string
  default     = "ELB"

  validation {
    condition     = contains(["ELB", "EC2"], var.asg_health_check_type)
    error_message = "asg_health_check_type must be ELB or EC2."
  }
}

variable "asg_health_check_grace_period" {
  description = <<-DESC
    Seconds after an instance launches before health checks start counting
    against it. FREE.

    This is the boot-loop variable. Set it shorter than your application takes
    to become ready and you get: instance launches, ALB health check fails
    because the app is still starting, ASG terminates it as unhealthy, ASG
    launches a replacement, replacement fails for the same reason, forever.
    The ASG activity history shows a tidy loop of launch/terminate that looks
    like an AZ problem and is not.

    Worse, it is self-concealing during a real incident: under load your
    application boots SLOWER (cold caches, contended disks, a database that is
    already struggling), so a grace period that was adequate on a quiet Tuesday
    is inadequate on the one day it matters, and the ASG responds to your
    outage by killing every instance that tries to help.

    Measure it. Time a boot to first successful health check, then double it.
    This stack's user-data is trivial and ready in ~60 seconds; 300 is the
    default here because doubling is not enough of a margin when the number is
    a guess.

    Zero, or a value below 30, is check DR-004.
  DESC
  type        = number
  default     = 300

  validation {
    condition     = var.asg_health_check_grace_period >= 0 && var.asg_health_check_grace_period <= 3600
    error_message = "asg_health_check_grace_period must be between 0 and 3600 seconds."
  }
}

variable "target_group_health_check" {
  description = <<-DESC
    Target group health check tuning. FREE.

    The four numbers below multiply into your detection time, which is the
    front half of your RTO and the half nobody counts:

      time to mark unhealthy = interval x unhealthy_threshold

    With the defaults (30 x 2) that is 60 seconds before the load balancer
    stops sending traffic to a dead instance. AWS's own defaults (30 x 2 for
    ALB) are the same. Tighten to 10 x 2 and you detect in 20 seconds — at the
    cost of more health check traffic and more sensitivity to a slow response,
    which is how a garbage-collection pause becomes a deregistration.

    The asymmetry is deliberate and correct: unhealthy_threshold should be LOW
    (fail fast, the cost of a false positive is one instance out of rotation)
    and healthy_threshold should be HIGHER (recover slow, the cost of a false
    negative is traffic to something that is not ready). Stacks that set both
    to 2 are usually not thinking about either.

    `timeout` must be less than `interval`, which the API enforces and which
    is the one validation below.
  DESC
  type = object({
    path                = string
    interval            = number
    timeout             = number
    healthy_threshold   = number
    unhealthy_threshold = number
    matcher             = string
  })
  default = {
    path                = "/health"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 3
    unhealthy_threshold = 2
    matcher             = "200"
  }

  validation {
    condition     = var.target_group_health_check.timeout < var.target_group_health_check.interval
    error_message = "target_group_health_check.timeout must be strictly less than .interval — the API rejects the alternative, and a timeout longer than the interval means overlapping checks."
  }

  validation {
    condition     = var.target_group_health_check.interval >= 5 && var.target_group_health_check.interval <= 300
    error_message = "target_group_health_check.interval must be between 5 and 300 seconds."
  }
}

###############################################################################
# Data tier — DynamoDB
#
# WHY DYNAMODB IS THE PRIMARY DATA STORE ON THIS DAY, and RDS is optional:
#
#   1. It has a genuine, measurable RPO story with three distinct settings —
#      no PITR (RPO = your last manual backup), PITR (RPO ~5 minutes), global
#      tables (RPO = replication lag, typically under a second and EXPOSED AS
#      A CLOUDWATCH METRIC you can graph). Very little else in AWS lets you
#      see your own RPO on a chart.
#   2. On-demand billing means an idle lab table costs cents rather than the
#      $12–25/month floor of the smallest RDS instance.
#   3. A global table replica is created in ~2 minutes. An RDS Multi-AZ
#      instance takes 15–25 minutes to create and about the same to modify,
#      which is a quarter of this day spent watching a spinner.
#
# RDS is still available behind create_rds, because "Multi-AZ RDS is not a
# read replica" is a misconception worth demonstrating on the real thing, and
# because the RDS checks in the auditor should have something to run against.
###############################################################################

variable "enable_dynamodb_pitr" {
  description = <<-DESC
    Point-in-time recovery on the DynamoDB table.

    COST-BEARING: ~$0.20 per GB-month of table size, on top of storage. On a
    lab table holding kilobytes this is effectively $0.00 and it is still the
    best value in this file.

    What it buys: continuous backup with restore to any second in the last 35
    days. RPO of roughly 5 minutes, with no schedule to maintain and no
    backup job to fail silently.

    What it does NOT buy, and this catches people: PITR restores to a NEW
    TABLE. It cannot restore in place. Your recovery procedure therefore
    includes "and then repoint every consumer at a table with a different
    name", which is application work, which is RTO, and which is why the
    restore in lab step 8 takes longer than the restore itself.

    Absence is check DR-008.
  DESC
  type        = bool
  default     = true
}

variable "enable_dynamodb_global_table" {
  description = <<-DESC
    Add a DynamoDB global table replica in dr_region.

    COST-BEARING, and the pricing shape surprises people:
      - Replicated writes are billed as rWCUs, ~1.5x the price of a normal
        write unit, per region you replicate to.
      - Storage is billed in BOTH regions.
      - Cross-region data transfer is billed on the replication traffic.
      - PITR, if enabled, is billed per replica.
    On an idle lab table with on-demand billing this is a few cents a month.
    On a write-heavy production table it can exceed the cost of the primary.

    What it buys: an RPO measured in sub-second replication lag, exposed as
    the ReplicationLatency CloudWatch metric — which is the only reason this
    day can make you MEASURE an RPO rather than declare one.

    What it costs beyond money: global tables are LAST-WRITER-WINS,
    multi-active, with no conflict resolution beyond a timestamp. If your
    application writes to both regions during a split brain, one of those
    writes is discarded silently and the loser is whichever clock was behind.
    That is a correctness property of your application, not of DynamoDB, and
    it is the reason "just make it active-active" is a bigger conversation
    than it sounds.

    Default false — it is the day's most instructive option and its most
    consequential one. Turn it on for lab step 6, then decide.
  DESC
  type        = bool
  default     = false
}

variable "dynamodb_billing_mode" {
  description = "PAY_PER_REQUEST or PROVISIONED. COST-BEARING: PAY_PER_REQUEST is ~$1.25 per million writes and ~$0.25 per million reads with no floor, which is the right answer for a lab and for spiky workloads. PROVISIONED bills for capacity you reserve whether or not you use it, which is cheaper at sustained high volume and is a way to accidentally pay for an idle DR replica's capacity forever."
  type        = string
  default     = "PAY_PER_REQUEST"

  validation {
    condition     = contains(["PAY_PER_REQUEST", "PROVISIONED"], var.dynamodb_billing_mode)
    error_message = "dynamodb_billing_mode must be PAY_PER_REQUEST or PROVISIONED."
  }
}

###############################################################################
# Data tier — RDS (optional)
###############################################################################

variable "create_rds" {
  description = <<-DESC
    Create the optional RDS instance.

    COST-BEARING: db.t3.micro is ~$0.017/hour (~$12.41/month) single-AZ in
    us-east-1, plus ~$0.115/GB-month for gp3 storage. Free-tier eligible for
    750 hours/month in the first 12 months — SINGLE-AZ ONLY. Multi-AZ is not
    free-tier eligible at any point, which is a large part of why so many
    tutorials quietly demonstrate single-AZ.

    ALSO COSTS TIME: 15–25 minutes to create, and roughly the same again if
    you later flip rds_multi_az, because converting an existing instance to
    Multi-AZ takes a snapshot, builds a standby and syncs it. Plan the lab
    around that or leave this off.

    Default false. Turn it on if you want the Multi-AZ demonstration on real
    infrastructure; leave it off and DR-005, DR-006 and DR-007 are silent BY
    SITUATION rather than by design — the distinction is documented in the
    finding contract and it matters, because "no findings" and "nothing to
    find" are different states that look identical in a report.
  DESC
  type        = bool
  default     = false
}

variable "rds_instance_class" {
  description = "RDS instance class. COST-BEARING: db.t3.micro ~$12.41/month single-AZ, db.t3.small ~$24.82, and each doubles under Multi-AZ."
  type        = string
  default     = "db.t3.micro"

  validation {
    condition     = can(regex("^db\\.[a-z0-9]+\\.[a-z0-9]+$", var.rds_instance_class))
    error_message = "rds_instance_class must look like db.t3.micro."
  }
}

variable "rds_multi_az" {
  description = <<-DESC
    Run the RDS instance Multi-AZ.

    COST-BEARING: EXACTLY DOUBLE the instance and storage cost. db.t3.micro
    goes from ~$12.41 to ~$24.82/month. There is no partial version of this.

    What it buys, precisely: a synchronous standby in another AZ and an
    automatic DNS failover in typically 60–120 seconds. Your RPO for an AZ
    failure becomes zero, because the standby acknowledges every commit before
    the primary returns.

    WHAT IT DOES NOT BUY, and this is the misconception the day exists to
    kill: THE STANDBY SERVES NO TRAFFIC. It is not a read replica. You cannot
    query it. It does not improve read throughput, write throughput, or
    latency. It is a hot spare with a price tag equal to the thing it is
    sparing. Every team that has enabled Multi-AZ expecting read scaling has
    then been confused about why their read latency did not change.

    (Read replicas are a different feature, billed separately, asynchronous,
    promotable manually. You can have both. Most people who need one need
    both.)

    Also worth knowing: a Multi-AZ failover is not free of impact. Connections
    are dropped. In-flight transactions roll back. Applications with a
    connection pool and no retry logic experience a Multi-AZ failover as a
    brief outage, and "brief" is measured by your pool's TCP timeout, which is
    frequently longer than the failover itself.

    Absence when create_rds is true is check DR-005.
  DESC
  type        = bool
  default     = false
}

variable "rds_backup_retention_days" {
  description = <<-DESC
    Automated backup retention, in days.

    COST-BEARING but usually not much: backup storage up to the size of your
    database is free; beyond that it is ~$0.095/GB-month.

    ZERO DISABLES AUTOMATED BACKUPS ENTIRELY, and with them point-in-time
    restore. Your RPO becomes "the last manual snapshot somebody remembered to
    take", which is normally a number you find out during the incident.

    One day of retention is technically backups and practically not: a
    corruption discovered on Friday afternoon that started on Thursday morning
    is unrecoverable, and corruption is the failure mode backups are FOR. AZ
    failure is handled by Multi-AZ; hardware failure is handled by the storage
    layer; backups exist for the cases where the data was wrong and you did
    not notice immediately. Seven days is the minimum that survives a weekend
    plus a Monday of nobody looking.

    Retention of 0 or 1 is check DR-006.

    Default 1 — deliberately the wrong answer, so that the auditor has
    something true to say about your own stack. Set it to 7 and re-run.
  DESC
  type        = number
  default     = 1

  validation {
    condition     = var.rds_backup_retention_days >= 0 && var.rds_backup_retention_days <= 35
    error_message = "rds_backup_retention_days must be between 0 and 35."
  }
}

###############################################################################
# Data tier — S3 and cross-region replication
###############################################################################

variable "enable_s3_replication" {
  description = <<-DESC
    Replicate the primary S3 bucket to a bucket in dr_region.

    COST-BEARING, three ways, and people usually budget for one:
      1. Inter-region transfer on every replicated byte, ~$0.02/GB.
      2. Storage in the DR region, ~$0.023/GB-month, forever, in addition to
         the primary. Replication duplicates, it does not move.
      3. A PUT request in the destination for every object, ~$0.005 per 1,000.
         On many small objects this dominates the other two.

    REQUIRES VERSIONING on both buckets. This is not a recommendation, it is
    an API constraint, and it has a cost consequence that catches people: with
    versioning on, deletes do not delete. Every overwrite keeps the old
    version and bills for it, in both regions, until a lifecycle rule removes
    it. A replicated bucket with no lifecycle policy on noncurrent versions is
    the most reliable way to grow a storage bill in a region nobody looks at.

    REPLICATION IS ASYNCHRONOUS AND HAS NO SLA BY DEFAULT. Most objects
    replicate in seconds. Some take minutes. Under a large burst, some take
    much longer. Your RPO is therefore "usually seconds, occasionally
    unbounded" unless you enable RTC below. Check DR-011 compares your stated
    RPO against what your replication configuration can actually promise.

    Absence is check DR-010.
  DESC
  type        = bool
  default     = true
}

variable "s3_replication_time_control" {
  description = <<-DESC
    Enable S3 Replication Time Control (RTC) on the replication rule.

    COST-BEARING: ~$0.015/GB replicated, ON TOP of the transfer and storage
    costs above. Roughly doubles the per-GB cost of replication.

    What it buys: a service level agreement. 99.99% of objects replicated
    within 15 minutes, with replication metrics published to CloudWatch and an
    event when an object breaches the threshold. Without RTC you have no SLA
    and, more importantly, no METRIC — you cannot answer "what is my current
    replication lag" at all, which means you cannot answer "what is my RPO".

    This is the clearest example in the repo of paying money for
    OBSERVABILITY rather than for capability. The data replicates either way.
    What $0.015/GB buys is the ability to say a true sentence about it.

    Default false, because it is real money per GB and because turning it on
    in lab step 6 and watching the metrics appear is a better lesson than
    having it on from the start.
  DESC
  type        = bool
  default     = false
}

variable "s3_noncurrent_version_expiration_days" {
  description = "Days before noncurrent object versions are expired by lifecycle rule, in BOTH buckets. COST-BEARING in reverse: this is the variable that stops versioning-plus-replication becoming an unbounded bill. 30 days is a compromise; the right number is however long it takes you to notice a bad overwrite."
  type        = number
  default     = 30

  validation {
    condition     = var.s3_noncurrent_version_expiration_days >= 1 && var.s3_noncurrent_version_expiration_days <= 365
    error_message = "s3_noncurrent_version_expiration_days must be between 1 and 365."
  }
}

###############################################################################
# DNS and health checking
###############################################################################

variable "hosted_zone_id" {
  description = <<-DESC
    OPTIONAL Route 53 public hosted zone ID. Leave empty to skip all DNS
    record creation.

    COST-BEARING IF YOU ALREADY HAVE ONE: a hosted zone is $0.50/month for the
    first 25 zones. This stack does NOT create one, deliberately — creating a
    hosted zone for a domain you do not own produces a zone that resolves for
    nobody, bills monthly, and survives teardown because people forget zones
    are resources.

    If empty (the default), the day still builds the Route 53 HEALTH CHECK
    against the ALB — health checks do not require a hosted zone — and the
    failover record sets are skipped. You get the detection half of DNS
    failover and not the record half, which is enough to learn the mechanism
    and not enough to demonstrate the TTL problem end to end. Lab step 10
    documents how to complete it if you own a domain.
  DESC
  type        = string
  default     = ""

  validation {
    condition     = var.hosted_zone_id == "" || can(regex("^Z[A-Z0-9]{4,}$", var.hosted_zone_id))
    error_message = "hosted_zone_id must be empty or a Route 53 zone ID like Z1D633PJN98FT9."
  }
}

variable "dns_record_name" {
  description = "Fully-qualified record name for the failover records, e.g. app.example.com. Ignored when hosted_zone_id is empty."
  type        = string
  default     = ""

  validation {
    condition     = var.dns_record_name == "" || can(regex("^[a-z0-9._-]+\\.[a-z]{2,}$", var.dns_record_name))
    error_message = "dns_record_name must be empty or a lowercase fully-qualified DNS name."
  }
}

variable "route53_ttl" {
  description = <<-DESC
    TTL, in seconds, on the failover record sets.

    FREE. Also, directly and unavoidably, part of your RTO.

    A resolver that fetched your record one second before you failed over will
    keep serving the old address for this many seconds. Not on average — as a
    worst case that applies to some fraction of your users no matter how fast
    the rest of your recovery is. A 300-second TTL means five minutes of your
    recovery budget is spent before anything you do has any effect on some
    clients.

    So why not set it to 1? Because TTL is also your DNS bill and your
    resolution latency: Route 53 charges ~$0.40 per million standard queries,
    and a TTL of 1 multiplies query volume by roughly 300 against a TTL of
    300. On a busy service that is real money, and every query is also a few
    milliseconds in front of every cold connection.

    The honest answer is a tiered one: 60 seconds on records that participate
    in failover, longer on records that do not. And the honest caveat is that
    TTL IS A REQUEST, NOT A GUARANTEE. Some resolvers clamp minimums. Some
    corporate resolvers cache far longer than you asked. Java, historically
    and by default, cached DNS for the life of the JVM — which is why "we
    failed over but the app servers kept connecting to the old database" is a
    story every senior engineer has a version of.

    Default 60. A value above 300 is check DR-013 when a failover record set
    exists.
  DESC
  type        = number
  default     = 60

  validation {
    condition     = var.route53_ttl >= 0 && var.route53_ttl <= 86400
    error_message = "route53_ttl must be between 0 and 86400 seconds."
  }

  # And a second, opinionated validation: a TTL may not consume more than a
  # QUARTER of the RTO you declared.
  #
  # This is enforcement instead of detection, and it is deliberately not a
  # check in dr_audit.py. The audit tells you about a TTL problem after you
  # have shipped it; the validation refuses to ship it. When you can do either,
  # do this one — an auditor finding is a ticket and a plan failure is a
  # conversation, and the conversation is cheaper.
  validation {
    condition     = var.route53_ttl <= (var.rto_target_minutes * 60) / 4
    error_message = "route53_ttl consumes more than a quarter of rto_target_minutes. DNS TTL is spent RTO before anything else happens — either lower the TTL or raise the RTO you are willing to claim."
  }
}

variable "enable_route53_health_check" {
  description = <<-DESC
    Create a Route 53 health check against the ALB.

    COST-BEARING: ~$0.50/month per health check against an AWS endpoint
    ($0.75 for non-AWS endpoints), PLUS ~$1.00/month for EACH optional
    feature: HTTPS, string matching, fast interval (10s), and latency
    measurement. A health check with HTTPS and string matching is $2.50/month,
    which is trivial once and $250/month at a hundred endpoints — and health
    checks are billed per check per month whether or not the endpoint still
    exists, which is why the teardown checklist hunts them specifically.

    THREE KINDS OF HEALTH CHECK, and this day uses all three:
      - Route 53 health check   Is the endpoint reachable FROM THE INTERNET,
                                from multiple global locations? This is the
                                only one that answers "can my users get here".
      - ALB target group check  Is this individual target responding? Answers
                                "should I send traffic to this instance".
      - EC2 status check        Is the hypervisor and the instance OS alive?
                                Answers almost nothing about your application.

    They fail independently and they are not substitutes. An instance can pass
    EC2 checks, fail the target group check, and the Route 53 check still
    passes because the OTHER instance is serving. That is correct behaviour
    and it is why you need all three.

    Default true. It is $0.50/month and it is the input to every DNS failover
    decision this day makes.
  DESC
  type        = bool
  default     = true
}

###############################################################################
# Chaos — causing failure on demand
###############################################################################

variable "enable_chaos_lambda" {
  description = <<-DESC
    Deploy the chaos Lambda that breaks things on purpose.

    ESSENTIALLY FREE: Lambda's permanent free tier is 1M requests and 400,000
    GB-seconds per month, and this function runs a handful of times.

    Day 06 introduced this pattern to generate a real incident to analyse.
    Here it exists because THE ONLY THING THAT MAKES AN RTO REAL IS BREAKING
    SOMETHING AND TIMING THE RECOVERY. A DR plan validated by reading it is a
    document review.

    Four modes, invoked with a payload (see lab step 7):
      terminate_instance   Kill an instance. Tests the plain ASG replacement
                           path.
      mark_unhealthy       autoscaling:SetInstanceHealth Unhealthy. The safe
                           analogue of "the application is broken but the
                           instance is fine" — the failure EC2 health checks
                           cannot see.
      isolate_az           Associate a deny-all NACL with one AZ's private
                           subnet. Instances there keep running, keep passing
                           EC2 status checks, and become unreachable.
      restore              Undo isolate_az. Note that it is the longest of the
                           four functions, which is the whole failback lesson
                           in miniature.

    None of these is a real AZ failure and the difference matters. A real AZ
    failure takes the NAT gateway, the RDS standby, the EBS control plane for
    that zone and any cross-AZ dependency you did not know you had, all at
    once, while the AWS console is also degraded. AWS Fault Injection Service
    has a genuine AZ-availability-power-interruption action that gets much
    closer; it costs ~$0.10 per action-minute and it is the right next step
    after this lab.
  DESC
  type        = bool
  default     = true
}

variable "chaos_dry_run" {
  description = <<-DESC
    Chaos Lambda default mode. When true, the function logs exactly what it
    WOULD do and changes nothing.

    Day 07 argued that anything irreversible needs a dry run and a human gate.
    That argument applies with more force here, because Day 07's automation
    contained a threat and this one causes an outage. The dry run is not
    training wheels — it is how you verify that the blast radius is what you
    think it is BEFORE the blast, and every chaos exercise in a real
    organisation starts with one.

    Set to false, or pass {"dry_run": false} in the invocation payload, when
    you are ready to actually break something in lab step 7.
  DESC
  type        = bool
  default     = true
}

###############################################################################
# Deliberately broken examples
###############################################################################

variable "create_insecure_examples" {
  description = <<-DESC
    Build the deliberately misconfigured resources that give dr_audit.py
    something to find.

    Costs a few cents: a second ASG at min_size 0, an unversioned S3 bucket, a
    second DynamoDB table with no PITR, and (at CP2) a recovery workflow with
    no dry-run gate and a backup plan with no vault lock. The second ASG is
    created at desired capacity 0 so it costs nothing to exist.

    Default true, matching Days 04–07. Every finding in the contract is
    reproducible from a default apply of this stack, which is the property
    that makes the numbers checkable rather than aspirational.

    Set to false when you want to demonstrate the clean state — and note that
    a clean state on this day is NOT zero findings, because several checks are
    about things the stack cannot fix for you, like whether a failover has ever
    been tested. See the finding contract.
  DESC
  type        = bool
  default     = true
}

###############################################################################
# CP2 — Backup, and the difference between a backup and a restore
#
# A backup nobody has restored is a file. That sentence is the whole of this
# section and most of DR-010.
#
# Every variable below buys retention or copies. NONE of them buys a tested
# restore, because nothing you can set in a tfvars file can. That is a
# procedure, performed by a person, timed with a clock, and it is the only
# evidence that the money above it bought anything.
###############################################################################

variable "enable_backup_plan" {
  description = <<-DESC
    Create the AWS Backup vault, plan and selection.

    COST-BEARING: warm backup storage is ~$0.05/GB-month for EBS in us-east-1,
    plus a second ~$0.05/GB-month in the DR region for every copy, plus
    inter-region transfer on the copy itself. Restores are billed too, at
    ~$0.02/GB for EBS — which is small and is worth naming, because "restore
    testing costs money" is one of the reasons restore testing does not happen.

    WHY AWS BACKUP RATHER THAN NATIVE SNAPSHOT SCHEDULES. Both work. Native
    lifecycle policies (DLM for EBS, automated backups for RDS) are cheaper to
    reason about and have no extra service in the path. AWS Backup wins here
    for three specific reasons, and if none of them apply to you, use the
    native ones:

      1. ONE VAULT, ONE POLICY, MANY SERVICES. EBS, RDS, DynamoDB, EFS and FSx
         under one retention rule and one audit surface. A DR posture assembled
         from five services' native schedules is five things to verify.
      2. CROSS-REGION COPY IS A FIRST-CLASS RULE, not a Lambda somebody wrote.
      3. VAULT LOCK. Native snapshots can be deleted by anyone with the API
         permission, including an attacker who has just encrypted your
         production data and would like your recovery options to be limited.
         There is no native equivalent.

    Default true.
  DESC
  type        = bool
  default     = true
}

variable "backup_schedule" {
  description = <<-DESC
    Cron expression for the backup rule, in UTC.

    THIS EXPRESSION IS YOUR RPO CEILING. Daily at 05:00 means that at 04:59 you
    are 23 hours 59 minutes from your last recovery point, and no amount of
    replication elsewhere changes that for the resources this plan protects.
    If your stated RPO is 60 minutes and your schedule is daily, one of those
    two numbers is a fiction — which is check DR-008 asked as arithmetic
    instead of as a question.

    Default is hourly, which is unusual for production and correct for a lab
    where you want to see a recovery point appear during the session.
  DESC
  type        = string
  default     = "cron(0 * * * ? *)"

  validation {
    condition     = can(regex("^cron\\(.+\\)$", var.backup_schedule))
    error_message = "backup_schedule must be a cron() expression, e.g. cron(0 * * * ? *)."
  }
}

variable "backup_retention_days" {
  description = "How long recovery points are kept. COST-BEARING: warm storage bills per GB-month for the whole retention window, in every region a copy exists. 7 days is the minimum that survives a weekend plus a Monday of nobody looking; 35 is the usual compliance floor."
  type        = number
  default     = 7

  validation {
    condition     = var.backup_retention_days >= 1 && var.backup_retention_days <= 365
    error_message = "backup_retention_days must be between 1 and 365."
  }
}

variable "backup_copy_to_dr" {
  description = <<-DESC
    Copy every recovery point to a vault in dr_region.

    COST-BEARING: inter-region transfer on the copy (~$0.02/GB) plus a second
    full copy of warm storage in the DR region, for the whole retention window.
    Roughly doubles the backup line.

    It is also the only thing that makes the backups survive the event they
    exist for. A vault in the region that just failed is not a recovery option.
    That sentence sounds obvious and is the single most common gap in
    real DR postures, because the copy rule is a separate decision from the
    backup rule and only one of them is required to make the plan valid.

    Default true.
  DESC
  type        = bool
  default     = true
}

variable "enable_vault_lock" {
  description = <<-DESC
    Apply a vault lock to the backup vaults.

    FREE. Also potentially PERMANENT — read all of this before you set it true.

    Vault lock makes recovery points immutable: nobody, including the account
    root and including you, can shorten retention or delete a recovery point
    before its retention expires. It is the control that survives a compromised
    administrator, which is the threat model backups are actually for once you
    take ransomware seriously.

    TWO MODES, and the difference is not a detail:

      governance  Can be removed by a principal with
                  backup:DeleteBackupVaultLockConfiguration. Protects against
                  accident and process failure. Does not protect against an
                  attacker with admin. This is the correct mode for a lab and
                  for most production accounts.

      compliance  CANNOT BE REMOVED. Not by you, not by root, not by AWS
                  Support. After the cooling-off period (minimum 3 days,
                  configurable up to 30) it is permanent for the life of the
                  vault, and the vault cannot be deleted while it holds
                  recovery points. You will pay for that storage until the
                  longest retention expires. If you set 365-day retention in
                  compliance mode on a lab account, you have bought a year of
                  storage and there is no undo.

    AND THE MODE IS SELECTED BY THE PRESENCE OF AN ARGUMENT, NOT BY A VALUE.
    AWS Backup reads `changeable_for_days`: absent means governance, present
    means compliance. There is no `mode = "governance"` line to get right —
    there is a line whose mere existence changes everything, and adding it
    looks like adding detail. That is a poor API and it has produced real,
    unrecoverable, expensive mistakes. This stack therefore never sets it, and
    exposes no variable that could.

    THIS STACK ONLY EVER USES GOVERNANCE MODE. There is no variable to select
    compliance, deliberately, because the failure mode of a typo is a bill you
    cannot cancel. When you want compliance mode in production, write it
    yourself, deliberately, with the cooling-off period set and a colleague
    reading the plan.

    Default FALSE, which means DR-009 fires against your own stack — twice,
    once per vault. Turn it on and re-run the audit.
  DESC
  type        = bool
  default     = false
}

###############################################################################
# CP2 — The recovery workflow
#
# Day 07's argument, carried forward and made heavier:
#
#   An automated response is a decision you are making now, to be executed
#   later, by nobody, on evidence that might be wrong.
#
# Day 07's automation contained a threat. This one declares a region dead.
# The evidence it acts on is health checks, and health checks lie during
# exactly the network conditions that make you want to fail over. An automated
# regional failover triggered by a transient partition is how you get split
# brain, and split brain in a last-writer-wins data store is silent data loss.
#
# So: kill switch, dry run, approval gate for anything irreversible, and a
# verify step that can fail the workflow rather than declaring success.
###############################################################################

variable "enable_recovery_workflow" {
  description = "Deploy the Step Functions recovery workflow and its Lambda. ESSENTIALLY FREE: Step Functions standard workflows are ~$0.025 per 1,000 state transitions with 4,000 free per month, and this workflow is about a dozen transitions per execution. The Lambda is inside the permanent free tier."
  type        = bool
  default     = true
}

variable "recovery_dry_run" {
  description = <<-DESC
    Default dry-run mode for the recovery Lambda. When true, the failover step
    logs precisely what it would change and changes nothing.

    Leave it true until you have read one dry-run output end to end. The
    failover action inverts a Route 53 health check and rewrites the
    active-region parameter that your application reads to decide where writes
    go — both reversible, both consequential, and neither obvious from the
    state machine diagram.
  DESC
  type        = bool
  default     = true
}

variable "require_approval_for_failover" {
  description = <<-DESC
    Require a human to approve before the workflow executes a regional
    failover. In-AZ recovery (replacing unhealthy instances) never requires
    approval, because it is what the ASG would do anyway and it is reversible
    by doing nothing.

    THE ARGUMENT FOR TRUE: a regional failover is not reversible by doing
    nothing. Once writes land in the DR region, failback requires
    reconciliation that no automation in this repo performs. The decision
    deserves a person.

    THE ARGUMENT FOR FALSE, which is real and which you will hear: the whole
    point of automation is that it works at 03:00 when nobody answers. An
    approval gate converts your RTO from "90 seconds" into "however long it
    takes to wake somebody", and if that is your answer you should say so in
    the RTO rather than hiding it behind a state machine.

    Both positions are defensible. What is not defensible is having a gate you
    have never tested the response path for — an approval request published to
    an unconfirmed SNS subscription is a workflow that times out during your
    incident. Confirm the subscription. Then run the drill and time how long
    the approval actually took.

    Default true. Setting it false is check DR-015 territory only if the dry
    run is ALSO off; the check looks for a workflow that can execute an
    irreversible action with neither brake.
  DESC
  type        = bool
  default     = true
}

variable "approval_timeout_minutes" {
  description = "How long the workflow waits for a human before giving up. FREE. This number IS your RTO when approval is required, and it is a ceiling rather than an estimate: a timeout of 30 means your worst-case approved failover starts at minute 30. Pick it by asking how long your on-call actually takes to acknowledge, not by asking how long you would like them to take."
  type        = number
  default     = 30

  validation {
    condition     = var.approval_timeout_minutes >= 1 && var.approval_timeout_minutes <= 1440
    error_message = "approval_timeout_minutes must be between 1 and 1440."
  }
}

variable "kill_switch_default" {
  description = <<-DESC
    Initial value of the runtime kill switch SSM parameter: "enabled" or
    "disabled". FREE (standard SSM parameters cost nothing).

    Day 07's pattern, unchanged: a brake that can be pulled from a phone,
    without a deploy, by somebody who does not have Terraform installed. The
    workflow reads it as its FIRST state and aborts if it is not "enabled".

    It fails SAFE in one direction only, and the direction is a real design
    choice: if the parameter is missing or unreadable, the workflow aborts.
    An automation that cannot confirm it is allowed to run does not run.

    Default "enabled", so the drill in lab step 7 works without extra setup.
    In production, ship it disabled and turn it on deliberately after the first
    successful drill.
  DESC
  type        = string
  default     = "enabled"

  validation {
    condition     = contains(["enabled", "disabled"], var.kill_switch_default)
    error_message = "kill_switch_default must be enabled or disabled."
  }
}
