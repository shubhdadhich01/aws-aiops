###############################################################################
# Day 07 — variables.tf
#
# Repo convention: any variable that costs money says so in its description,
# with the actual figure.
#
# Day 07 has a third kind of variable that the first six days did not, and it
# is the one to read carefully. Some toggles here do not cost money and do not
# change what exists — they change WHAT THIS ACCOUNT WILL DO TO ITSELF WITHOUT
# ASKING. `enable_auto_response` and `containment_mode` (CP2) are the obvious
# ones. Treat those the way you would treat a production deploy flag, because
# that is what they are.
#
# Pricing note that applies to this entire file: security service pricing is
# usage-based, tiered, and changes more often than compute pricing. Every
# figure below is an indicative us-east-1 on-demand number at the time of
# writing. VERIFY THEM against the pricing pages before you enable anything in
# an account you care about — and especially before you enable data events.
###############################################################################

###############################################################################
# Identity & region
###############################################################################

variable "aws_region" {
  description = "AWS region for all Day 07 resources. GuardDuty and Security Hub are REGIONAL services with account-level state — enabling them here does nothing for the other twenty-odd regions, which is check SEC-002."
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
  description = "Value for the Owner tag. Use your name or team so account-wide cost reports can attribute this spend to you."
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
    Email address that receives security findings and, from CP2, a record of
    every automated containment action.

    This is the only variable with no usable default. Set it in
    terraform.tfvars before you apply.

    The same SNS confirmation trap as Days 04 and 06 applies, and it is worse
    here: an unconfirmed subscription means an automated response can isolate
    a production instance at 03:00 and nobody is told. The action succeeds,
    the notification is discarded, and the first anyone knows is a customer
    ticket. Confirm the subscription before you enable automatic response.
  DESC
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.notification_email))
    error_message = "notification_email must be a valid email address, e.g. you@example.com."
  }
}

###############################################################################
# GuardDuty — the detector
###############################################################################

variable "enable_guardduty" {
  description = <<-DESC
    COST-BEARING, usage-based, with a 30-day free trial per account per region.

    GuardDuty is not priced per detector — it is priced per volume of data it
    analyses, which means the bill is a function of how busy your account is
    rather than of anything in this Terraform. Indicative us-east-1 rates:

      CloudTrail management events   ~$4.00 per million events
      VPC Flow Logs and DNS logs     ~$1.00/GB for the first 500 GB/month
      S3 data events                 ~$0.80 per million events (see below)

    On a quiet lab account this is cents. On a production account with real
    traffic it is one of the larger line items in the security budget, and the
    number is invisible until day 31, when the free trial ends and the first
    real invoice arrives. Set a budget alarm before the trial expires, not
    after.

    A disabled or absent detector is check SEC-001.
  DESC
  type        = bool
  default     = true
}

variable "guardduty_finding_publishing_frequency" {
  description = <<-DESC
    How often GuardDuty publishes UPDATES to existing findings to EventBridge
    and Security Hub.

    Values: FIFTEEN_MINUTES, ONE_HOUR, SIX_HOURS. The AWS default is SIX_HOURS
    and it is almost always wrong for an account with automated response.

    The subtlety that catches people: this does NOT delay the first
    notification of a NEW finding — those are published within about five
    minutes regardless. It controls updates to findings that already exist.
    That still matters, because "this finding is now occurring on four more
    instances" is exactly the update you want inside fifteen minutes rather
    than six hours.

    FIFTEEN_MINUTES costs nothing extra. There is no reason not to.
  DESC
  type        = string
  default     = "FIFTEEN_MINUTES"

  validation {
    condition     = contains(["FIFTEEN_MINUTES", "ONE_HOUR", "SIX_HOURS"], var.guardduty_finding_publishing_frequency)
    error_message = "guardduty_finding_publishing_frequency must be FIFTEEN_MINUTES, ONE_HOUR or SIX_HOURS."
  }
}

variable "enable_guardduty_s3_protection" {
  description = <<-DESC
    COST-BEARING (~$0.80 per million S3 data events analysed), and OFF by
    default here for a reason worth understanding.

    S3 protection is genuinely valuable — it is how GuardDuty spots
    exfiltration patterns and anomalous access to your buckets. It is also
    priced per data event, and a busy bucket generates data events at a rate
    that surprises people: an application reading a few hundred objects a
    second is ~26 million events a day.

    Turn it on deliberately, on the buckets that matter, after you have looked
    at your actual event volume. Turning it on account-wide "to be safe" is
    the single most common way a security budget doubles in a month.
  DESC
  type        = bool
  default     = false
}

###############################################################################
# Security Hub — the aggregator
###############################################################################

variable "enable_security_hub" {
  description = <<-DESC
    COST-BEARING (~$0.0010 per security check for the first 100,000 per
    account per region per month, then cheaper; plus ~$0.00003 per finding
    ingested above a free allowance).

    Security Hub does two separate things and the pricing follows the split:
    it INGESTS findings from other services (GuardDuty, Inspector, Macie, and
    anything using the ASFF format), and it RUNS its own compliance checks
    against enabled standards.

    The checks are the part that costs money and the part that surprises
    people, because the count is per control per resource per day. A hundred
    resources against a standard with two hundred controls is not a hundred
    checks — it is closer to twenty thousand a day.

    Budget roughly $2-5/month for a small lab account and considerably more
    for anything real. Read the standards variable below before you enable
    more than one.
  DESC
  type        = bool
  default     = true
}

variable "security_hub_standards" {
  description = <<-DESC
    Which Security Hub standards to enable.

    ONE. Enable one. This is the most important sentence in this file.

    Enabling all available standards on day one produces several thousand
    failed controls across a set that overlaps heavily, and the result is a
    number nobody will ever drive to zero. A compliance score nobody believes
    is worse than no compliance score, because it trains the whole team to
    scroll past the security dashboard.

    Start with aws-foundational-security-best-practices. It is the broadest,
    the most actionable, and the one whose findings map most directly onto
    things you can fix this week. Add CIS or PCI when someone actually needs
    the attestation, and expect to spend real time suppressing controls that
    do not apply.

    Valid keys here map to ARNs constructed in main.tf section 4.
  DESC
  type        = list(string)
  default     = ["aws-foundational-security-best-practices"]

  validation {
    condition = length(var.security_hub_standards) > 0 && alltrue([
      for standard in var.security_hub_standards :
      contains(["aws-foundational-security-best-practices", "cis-aws-foundations-benchmark", "pci-dss"], standard)
    ])
    error_message = "security_hub_standards must be a non-empty subset of: aws-foundational-security-best-practices, cis-aws-foundations-benchmark, pci-dss."
  }
}

###############################################################################
# CloudTrail — evidence, not logging
###############################################################################

variable "cloudtrail_multi_region" {
  description = <<-DESC
    Whether the trail captures events from every region.

    Leave this TRUE. A single-region trail is check SEC-006, and the reason is
    not completeness for its own sake: an attacker with credentials does not
    politely operate in your primary region. Creating an instance in
    ap-south-1 is exactly as easy for them as in us-east-1, and a
    single-region trail records none of it.

    Cost: the FIRST trail delivering management events is free, per account,
    including when it is multi-region. Multi-region costs nothing extra. A
    SECOND trail delivering the same management events is ~$2.00 per 100,000
    events, which is why "just make another trail for the security team" is a
    more expensive suggestion than it sounds.
  DESC
  type        = bool
  default     = true
}

variable "cloudtrail_enable_log_file_validation" {
  description = <<-DESC
    Whether CloudTrail signs its log files so tampering can be detected.

    Leave this TRUE, and understand what it is for. Without it you have
    logging. With it you have EVIDENCE — a digest file, signed hourly, that
    lets `aws cloudtrail validate-logs` prove no file was modified or deleted
    since delivery.

    That distinction only matters once, and it matters completely: during an
    incident where the question is whether an attacker with S3 write access
    edited the trail to remove their own activity. Without validation you
    cannot answer it. With it you can, and the answer is admissible.

    It is free. There is no argument for turning it off, and its absence is
    check SEC-007.
  DESC
  type        = bool
  default     = true
}

variable "cloudtrail_enable_data_events" {
  description = <<-DESC
    ⚠️ THE EXPENSIVE ONE. COST-BEARING (~$0.10 per 100,000 data events), and
    unlike management events there is NO free allowance.

    Data events record object-level activity — every GetObject, every PutObject
    — rather than control-plane calls. They are how you answer "which objects
    did the compromised role actually read", which is the question that decides
    whether you have a breach-notification obligation.

    They are also generated at application volume rather than at human volume.
    A bucket serving a few hundred reads a second produces ~26 million events a
    day, which is ~$26/day, which is ~$780/month, for one bucket.

    So: enable them on the buckets that hold data you would have to notify
    about, with an explicit selector, and nowhere else. Never account-wide.

    Default FALSE here because the lab does not need them and because an
    accidental account-wide enablement in a real account is a genuinely
    memorable invoice.
  DESC
  type        = bool
  default     = false
}

variable "trail_log_retention_days" {
  description = <<-DESC
    S3 lifecycle expiry for delivered CloudTrail objects.

    CloudTrail objects are small and cheap individually and they accumulate
    forever by default — the same shape as Day 06's unretained log groups, in
    a different service. 90 days is a reasonable lab and small-team default.

    Before you shorten it, check whether anything obliges you to keep longer:
    PCI DSS wants a year with three months immediately available, and several
    other frameworks are similar. Before you lengthen it, move the old objects
    to Glacier rather than paying Standard rates for evidence nobody has
    queried since 2022.

    Note this expires the OBJECTS, not the trail. The trail keeps writing.
  DESC
  type        = number
  default     = 90

  validation {
    condition     = var.trail_log_retention_days >= 7 && var.trail_log_retention_days <= 3650
    error_message = "trail_log_retention_days must be between 7 and 3650."
  }
}

###############################################################################
# Secrets Manager — credential hygiene
###############################################################################

variable "secret_rotation_days" {
  description = <<-DESC
    Rotation interval for the managed secret.

    30 days is a common default and it is not a magic number — the value of
    rotation is not the interval, it is that rotation WORKS AND IS TESTED. A
    secret rotating every seven days whose rotation Lambda has been failing
    since March is worse than one rotating annually and verified, because the
    dashboard says green.

    That failure mode is check SEC-011, and it is the reason section 6 of
    main.tf shows you where to look for it rather than just enabling rotation
    and moving on.

    COST: Secrets Manager is ~$0.40 per secret per month plus ~$0.05 per
    10,000 API calls. This stack creates two secrets, so ~$0.80/month — the
    largest predictable line item on this day.
  DESC
  type        = number
  default     = 30

  validation {
    condition     = var.secret_rotation_days >= 1 && var.secret_rotation_days <= 365
    error_message = "secret_rotation_days must be between 1 and 365."
  }
}

variable "secret_recovery_window_days" {
  description = <<-DESC
    How long a deleted secret sits recoverable before it is really gone.

    AWS enforces 7 to 30 days, or 0 for immediate deletion. This surprises
    people at teardown exactly the way KMS did on Day 04: `terraform destroy`
    returns successfully and the secret still exists, scheduled for deletion,
    and you cannot create a new secret with the same name until it clears.

    7 is the minimum non-zero value and the right choice for a lab. Do NOT set
    0 in anything real: immediate deletion of a secret is unrecoverable, and
    "we deleted the wrong secret" is a much more common incident than "we
    needed the name back within a week".
  DESC
  type        = number
  default     = 7

  validation {
    condition     = var.secret_recovery_window_days == 0 || (var.secret_recovery_window_days >= 7 && var.secret_recovery_window_days <= 30)
    error_message = "secret_recovery_window_days must be 0 (immediate, unrecoverable) or between 7 and 30."
  }
}

###############################################################################
# Quarantine
###############################################################################

variable "quarantine_vpc_id" {
  description = "VPC the quarantine security group is created in. Empty string uses the default VPC, which is fine for a lab. In production this must be the VPC your workloads actually run in — a quarantine SG in the wrong VPC cannot be attached to anything, and you will discover that during the incident."
  type        = string
  default     = ""

  validation {
    condition     = var.quarantine_vpc_id == "" || can(regex("^vpc-[0-9a-f]{8,17}$", var.quarantine_vpc_id))
    error_message = "quarantine_vpc_id must be empty or a valid VPC id, e.g. vpc-0a1b2c3d."
  }
}

###############################################################################
# The deliberately-broken examples
###############################################################################

variable "create_insecure_examples" {
  description = <<-DESC
    FREE, with one exception noted below.

    Creates deliberately misconfigured security resources so sec_audit.py has
    real findings to report. Same pattern as Day 04's broken function, Day 05's
    bad-examples/ directory and Day 06's naive analyser.

    What it creates is listed in main.tf section 10. The short version:

      * a second CloudTrail, single-region, with no log file
        file validation                                  -> SEC-006, SEC-007
      * its bucket, with no versioning and no public
        access block                                     -> SEC-009
      * a secret with no rotation configured at all       -> SEC-010
      * an IAM user with a long-lived access key          -> SEC-013 eventually;
        see the contract on why it is silent today
      * the SAME responder zip deployed with a severity
        threshold instead of an allow-list, an intent to
        terminate, no kill switch and a wide-open role   -> SEC-005, SEC-008,
                                                            SEC-012, SEC-014
      * a rule wired to it, created DISABLED, with no
        dead-letter queue                                -> SEC-015, SEC-016

    The exception to "free": the second trail is a SECOND trail delivering
    management events, and only the first is free. On a quiet lab account that
    is pennies; on a busy one it is ~$2.00 per 100,000 events. The teardown
    checklist deletes it explicitly.

    Set to false for a clean reference build. Set to true (the default) for the
    teaching experience: exactly 11 findings, 137 points and a compliance score
    of 0/100 immediately after apply. The full breakdown is the finding
    contract, quoted in the outputs, both READMEs, sec_audit.py and its tests.

    Never set this true in an account that holds anything real. The wide-open
    responder role really does grant iam:* and cloudtrail:* to a function whose
    source is in a public git repository.
  DESC
  type        = bool
  default     = true
}

###############################################################################
# The response half — the variables that change what this account does to
# itself without asking
#
# Everything above this line configures OBSERVATION. Everything below it
# configures ACTION. Read each description before you change it, and treat
# these the way you would treat a production deploy flag, because that is what
# they are.
###############################################################################

variable "enable_auto_response" {
  description = <<-DESC
    Whether the EventBridge rule that invokes the responder is ENABLED.

    This is the apply-time switch. There is also a RUNTIME switch — the kill
    switch in section 8 — and you want both, for different reasons:

      This variable    requires a `terraform apply` to change. Right for a
                       considered decision, wrong for 03:00.
      The kill switch  is an SSM parameter the responder reads on every
                       invocation. One CLI command, no plan, no apply, no
                       pipeline. Right for 03:00.

    A team that only has the first one discovers, during the incident where
    the automation is making things worse, that turning it off requires a pull
    request.

    Note the trap in the other direction: a rule created in the DISABLED state
    that everyone believes is enabled is its own outage. It looks completely
    normal in the console. That is check SEC-015, and it is Day 04's CMP-014
    wearing a different hat.
  DESC
  type        = bool
  default     = true
}

variable "containment_mode" {
  description = <<-DESC
    What the responder actually DOES when a finding matches.

      dry-run   Log and notify what it WOULD have done. Changes nothing.
      isolate   Replace the instance's security groups with the quarantine
                group, recording the previous groups so a human can put them
                back. Reversible.

    There is deliberately no `terminate` and no `delete` option. That is not
    an oversight and it is the position this day argues for:

      Containment is a decision made by automation on probabilistic evidence
      with nobody watching. Every action it can take must be undoable by one
      documented command. Deleting an access key, terminating an instance or
      revoking a role is a decision a human makes on Monday, with the
      finding in front of them and somebody to ask.

    The deliberately broken responder in section 10 has a destructive mode, so
    check SEC-012 has something to find. Do not copy it.

    START IN dry-run. Run it for a week. Read what it would have done. Only
    then move to isolate — and expect the week of dry-run output to change
    your allow-list, because it always does.
  DESC
  type        = string
  default     = "dry-run"

  validation {
    condition     = contains(["dry-run", "isolate"], var.containment_mode)
    error_message = "containment_mode must be dry-run or isolate. Destructive modes are deliberately not offered; see the description."
  }
}

variable "respond_to_finding_types" {
  description = <<-DESC
    The ALLOW-LIST of GuardDuty finding types the responder will act on.

    An allow-list of TYPES, not a severity threshold. This is the single most
    important design decision on this day and section 3 of main.tf is the
    argument for it:

      GuardDuty severity scores IMPACT, not CONFIDENCE. A HIGH finding is
      routinely your own penetration test, your own scanner, a researcher, or
      a developer on hotel wifi. Trigger on `severity >= 7` and all of those
      get contained — four outages you caused, for one real detection.

    Finding TYPE is what correlates with confidence. Cryptomining findings are
    rarely false positives. SSH brute-force findings against an
    internet-facing host are near-constant background noise.

    So: every entry in this list is a decision somebody made about that
    specific type, and adding one should require the same review as a deploy.
    An empty list means the responder acts on nothing, which is a safe
    default and not a useful one.

    Triggering on severity instead of a type list is check SEC-005.
  DESC
  type        = list(string)
  default = [
    "CryptoCurrencyMining:EC2/BitcoinTool.B!DNS",
    "Backdoor:EC2/C&CActivity.B!DNS",
    "Trojan:EC2/BlackholeTraffic",
    "UnauthorizedAccess:EC2/MaliciousIPCaller.Custom",
  ]

  validation {
    condition     = length(var.respond_to_finding_types) > 0
    error_message = "respond_to_finding_types cannot be empty. If you want no automated response, set enable_auto_response = false instead — it says what you mean."
  }
}

variable "kill_switch_default" {
  description = <<-DESC
    Starting position of the runtime kill switch.

      ARMED     The responder acts (subject to containment_mode).
      DISARMED  The responder reads the switch, logs that it was invoked,
                notifies, and changes nothing.

    Default ARMED, because a kill switch that ships disarmed is a kill switch
    nobody tests. You want to have flipped it at least once, deliberately,
    before the night you need it.

    Flip it without an apply:

      aws ssm put-parameter --name /cbc-day07/kill-switch --value DISARMED \
        --type String --overwrite --profile bootcamp --region us-east-1

    The responder re-reads this on EVERY invocation. There is no cache and no
    warm-start shortcut, which costs a few milliseconds and buys you a switch
    that works the moment you flip it.

    A responder with no runtime disable at all is check SEC-014.
  DESC
  type        = string
  default     = "ARMED"

  validation {
    condition     = contains(["ARMED", "DISARMED"], var.kill_switch_default)
    error_message = "kill_switch_default must be ARMED or DISARMED."
  }
}

variable "max_access_key_age_days" {
  description = <<-DESC
    Age at which an active IAM access key becomes a finding (SEC-013).

    90 is the common threshold and the number most compliance frameworks use.
    Treat it as a smoke alarm rather than a target: the problem with a
    long-lived access key is not that it is old, it is that it is a copyable
    string that looks identical to legitimate use once copied. A 30-day-old
    key that has leaked is worse than a 400-day-old key that has not.

    This variable is also the day's demonstration of a check that is SILENT BY
    SITUATION. The lab creates an access key, and it is hours old, so the
    check finds nothing. Nobody has to change anything for that to stop being
    true — the calendar is enough. Lab step 8b sets this to 0 to make the
    point without waiting three months.
  DESC
  type        = number
  default     = 90

  validation {
    condition     = var.max_access_key_age_days >= 0 && var.max_access_key_age_days <= 3650
    error_message = "max_access_key_age_days must be between 0 and 3650."
  }
}

variable "stale_finding_age_days" {
  description = "How long a GuardDuty finding may sit in an active workflow state before it becomes a finding of its own (SEC-003). 7 days is generous. The check exists because the most common failure of a detection programme is not missing detections — it is a backlog nobody triages, which is indistinguishable from having no detection at all."
  type        = number
  default     = 7

  validation {
    condition     = var.stale_finding_age_days >= 1 && var.stale_finding_age_days <= 365
    error_message = "stale_finding_age_days must be between 1 and 365."
  }
}
