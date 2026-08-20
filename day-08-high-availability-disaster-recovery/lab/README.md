# Day 08 Lab — High Availability & Disaster Recovery

**Build it, break it, time the recovery, and find out which of your numbers
were measurements.**

This walkthrough follows `terraform output next_steps` exactly. Steps 0 through
11 here are steps 0 through 11 there; if they ever disagree, the Terraform is
right and this file has drifted.

---

## Before you start

### The one thing that will waste your afternoon

**Confirm the SNS subscription.** Not later. Now.

An unconfirmed subscription means every notification is accepted, billed, and
discarded. On this day that means the recovery workflow can fail your account
over at 03:00 and nobody is told: the failover succeeds, the approval request
is published into the void, and the execution times out waiting for a human
who never got the email.

### The cost warning, in advance

**~$83/month with the shipped defaults.** This is not a cents-per-day stack
like Days 04–07. The two largest lines — the NAT gateway and the ALB — bill
hourly for *existing* and do not scale down when you stop using them.

Run the lab in one or two sittings and tear down the same day. Read
`terraform output cost_breakdown` before you set `nat_gateway_strategy =
"per_az"` or `rds_multi_az = true`; both are correct and both roughly double a
line item.

### Layout

```
lab/
├── terraform/
│   ├── providers.tf              two providers, and why the alias is not a parameter
│   ├── variables.tf              every cost-bearing toggle priced in its description
│   ├── main.tf                   VPC, ALB, ASG, data tier, DNS, chaos
│   ├── recovery.tf               backup, kill switch, the Step Functions workflow
│   ├── outputs.tf                endpoints, the declared numbers, cost, next_steps
│   ├── terraform.tfvars.example  copy to terraform.tfvars and edit one line
│   └── lambda/
│       ├── chaos.py              breaks things on purpose
│       └── recovery.py           detect, decide, fail over, verify — and failback
├── python/
│   ├── dr_audit.py               16 checks, 9 services, 2 regions
│   ├── requirements.txt
│   ├── challenge/                the same tool with the check bodies removed
│   └── tests/                    47 tests, no credentials required
├── README.md                     you are here
├── rto-measurements.md           fill this in; it is the point of the day
├── teardown-checklist.md         destroy does not remove everything
├── interview-qa.md               12 questions with the answers that show depth
└── trainer-notes.md              timings, and where people get stuck
```

---

## Setup

```bash
cd day-08-high-availability-disaster-recovery/lab/terraform

cp terraform.tfvars.example terraform.tfvars
# Edit ONE line: notification_email. Everything else has a working default.

terraform init
terraform plan
```

Read the plan. It is around 80 resources across two regions, and the count is
worth a moment: **the recovery path has more resources than the thing it
recovers.** A kill switch, an assessment, an approval gate, an execution step,
a verification step and a notification, to perform two API calls. That ratio is
what it costs to make an irreversible automated decision responsibly.

```bash
terraform apply
```

Roughly four minutes, most of it the ALB and waiting for the ASG to report
capacity. The apply deliberately waits for instances to be **in service**
before returning — without `min_elb_capacity`, `apply` finishes while the ASG
is still launching and the first thing you do is health-check an empty target
group and conclude something is broken.

---

## Step 0 — Confirm the SNS subscription

```bash
aws sns list-subscriptions-by-topic \
  --topic-arn $(terraform output -raw sns_topic_arn) \
  --profile bootcamp --region us-east-1 \
  --query 'Subscriptions[].SubscriptionArn' --output text
```

If that prints `PendingConfirmation`, go and click the link in your email.

Terraform reports the subscription as created and the subscription ARN is
literally the string `PendingConfirmation`. There is no way to tell from the
plan, from the state, or from the console's summary view that nothing will ever
arrive.

---

## Step 1 — Wait for the targets, then look at the service

```bash
aws elbv2 describe-target-health \
  --target-group-arn $(terraform output -raw target_group_arn) \
  --profile bootcamp --region us-east-1 \
  --query 'TargetHealthDescriptions[].[Target.Id,TargetHealth.State]' --output table
```

Wait until both say `healthy`. Then:

```bash
curl http://$(terraform output -raw alb_dns_name)/
curl http://$(terraform output -raw alb_dns_name)/
curl http://$(terraform output -raw alb_dns_name)/
```

The instance id and the AZ in the response change between calls. That is the
load balancer spreading you across two zones, and it is the last time today
that everything will be simple.

**What you are looking at:** a stateless tier that is genuinely multi-AZ. If
this were the whole system, today would be over. It is not, because failover is
a data problem.

---

## Step 2 — Write down your RTO. Before you measure anything.

**Do this now, in a file, before you touch anything else.**

You declared `rto_target_minutes = 30` in tfvars. Now write down three
predictions, in **seconds**:

| | Scenario | Your prediction (seconds) |
| --- | --- | --- |
| (a) | Terminate one instance → back to 2 healthy targets | |
| (b) | Isolate one AZ → ALB stops routing to that zone | |
| (c) | Restore a DynamoDB table → a table the app can query | |

Fill in `rto-measurements.md` in this directory — it ships as a template with
the three rows waiting. It is deliberately **not** gitignored: it is the single
most valuable artefact this day produces.

> **This ordering is the pedagogical point of the entire day.** A number you
> write after seeing the answer is not a prediction, and every DR plan in the
> world is full of numbers written in that order.
>
> In most first attempts the measurement is between two and ten times the
> declaration. The gap is the lesson.

Prediction (c) is the one that humbles people. Think about it before you write
it: what happens after the restore finishes?

---

## Step 3 — Look at what you already spent

```bash
terraform output rto_budget_already_spent
```

```
alb_detection_seconds     = "60  (target group: interval 30 x unhealthy_threshold 2)"
alb_draining_seconds      = "30  (deregistration_delay on the target group)"
asg_grace_seconds         = "300  (a replacement is not judged for this long...)"
route53_detection_seconds = "90  (health check: request_interval 30 x failure_threshold 3)"
instance_boot_seconds     = "~90-180  (MEASURE THIS; do not accept the estimate.)"
NOTE                      = "None of the above includes detection by a human, ..."
```

That is your recovery budget consumed by **configuration alone**, before any
human or any automation acts. Compare it against your (a) prediction.

Two things to take from this output:

**The AWS default that costs the most and is tuned the least is
`deregistration_delay = 300`.** This stack sets 30. During a deliberate
failover, five minutes of connection draining is five minutes spent being
polite to connections that are about to be cut anyway. Set it to your 99th
percentile request duration plus a margin — not to 300 because that was in the
box.

**The NOTE line is the honest part.** Detection by a human, the decision to
act, and data reconciliation are absent from every number above, and in most
measured incidents those three exceed everything listed combined.

---

## Step 4 — Read the three health checks

```bash
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names $(terraform output -raw asg_name) \
  --profile bootcamp --region us-east-1 \
  --query 'AutoScalingGroups[0].[HealthCheckType,HealthCheckGracePeriod]' \
  --output text
```

Expect `ELB  300`.

Now look at the deliberately broken one:

```bash
aws autoscaling describe-auto-scaling-groups \
  --profile bootcamp --region us-east-1 \
  --query 'AutoScalingGroups[?contains(AutoScalingGroupName,`legacy`)].[AutoScalingGroupName,HealthCheckType,HealthCheckGracePeriod,length(AvailabilityZones)]' \
  --output table
```

`EC2  0  1`. Three faults in one resource, and they are **not duplicates**:

| Check | Asks | If wrong |
| --- | --- | --- |
| DR-001 | **Where** does it run? | One failure domain |
| DR-003 | Does it **notice** an application failure? | A deadlocked process is a healthy instance, forever |
| DR-004 | Can a replacement **start at all**? | A boot loop that looks like an AZ problem |

Fixing any one leaves the other two. In most organisations they have different
owners: the network team owns the subnets, the platform team owns the ASG, and
the application team owns how long a boot takes.

> **The line that matters most is `health_check_type = "ELB"`.** With the AWS
> default of `EC2`, an application that has deadlocked stays running and paid
> for indefinitely, the ALB correctly removes it from rotation, the service
> looks fine, and your effective capacity is silently N-1. Nothing alarms,
> because nothing is down. This state survives for months.

---

## Step 5 — Write something to the data tier

You cannot lose data you never wrote.

```bash
aws dynamodb put-item \
  --table-name $(terraform output -raw dynamodb_table_name) \
  --item '{"pk":{"S":"order#1"},"sk":{"S":"v1"},"note":{"S":"before failover"}}' \
  --profile bootcamp --region us-east-1

echo "before failover $(date -u +%FT%TZ)" > /tmp/cbc-day08.txt
aws s3 cp /tmp/cbc-day08.txt s3://$(terraform output -raw s3_primary_bucket)/ \
  --profile bootcamp --region us-east-1
```

---

## Step 6 — Measure your RPO instead of declaring it

### 6a. Take a backup, so the vaults are not empty

```bash
terraform output backup_rpo_ceiling
```

The plan runs hourly, and at apply time nothing has run yet. Both vaults are
empty, which is exactly the state a new DR posture is in on the day somebody
writes the RPO into a document — and it is why check DR-008 fires twice right
now.

Start one by hand and watch it:

```bash
aws backup start-backup-job \
  --backup-vault-name $(terraform output -raw backup_vault_name) \
  --resource-arn $(terraform output -raw backup_test_resource_arn) \
  --iam-role-arn $(terraform output -raw backup_role_arn) \
  --profile bootcamp --region us-east-1
```

`backup_test_resource_arn` is the 1 GiB volume from the insecure examples.
Nothing depends on it, so a restore drill against it cannot damage anything —
which is the property you want in whatever you choose to practise on.

Watch it land, then watch the **copy** land in the DR region. The second one is
the interesting one:

```bash
aws backup list-recovery-points-by-backup-vault --backup-vault-name "$VAULT" \
  --profile bootcamp --region us-east-1 \
  --query 'RecoveryPoints[].[CreationDate,Status]' --output table

aws backup list-recovery-points-by-backup-vault \
  --backup-vault-name $(terraform output -raw backup_vault_dr_name) \
  --profile bootcamp --region us-west-2 \
  --query 'RecoveryPoints[].[CreationDate,Status]' --output table
```

> **A vault in the region that just failed is not a recovery option.** That
> sentence is obvious and the gap is still the most common one in real DR
> postures, because the copy rule is a **separate decision** from the backup
> rule and only the backup rule is required to make a plan valid. A plan with
> no copy action is complete, correct, green in the console, and regional.

### 6b. Measure the S3 replication lag

```bash
aws s3 ls s3://$(terraform output -raw s3_replica_bucket)/ \
  --profile bootcamp --region us-west-2
```

Poll it. Time it. **That interval is your S3 RPO right now**, for an idle
bucket, with no SLA behind it.

Now the point of the exercise: there is no API call that will tell you what
that lag is at any other moment. Objects replicate asynchronously, most in
seconds, some in minutes, some — under a burst — considerably longer. "Usually
fast" is true and is not an RPO.

Set `s3_replication_time_control = true` and apply:

```bash
terraform apply -var 's3_replication_time_control=true'
```

You now have a 99.99%-within-15-minutes SLA and, more usefully, CloudWatch
metrics. ~$0.015/GB.

> **This is the clearest example in the repo of paying money for
> OBSERVABILITY rather than for capability.** The data replicates either way.
> What $0.015/GB buys is the ability to say a true sentence about it.
>
> Day 06's argument in new clothes: a summary you cannot check is worse than no
> summary. An RPO you cannot measure is worse than no RPO, **because you will
> quote it.**

### 6c. Watch an RPO on a graph

```bash
terraform apply -var 'enable_dynamodb_global_table=true'
```

Two minutes. Then:

```bash
aws cloudwatch get-metric-statistics --namespace AWS/DynamoDB \
  --metric-name ReplicationLatency --statistics Average --period 60 \
  --dimensions Name=TableName,Value=$(terraform output -raw dynamodb_table_name) \
               Name=ReceivingRegion,Value=us-west-2 \
  --start-time $(date -u -d '-30 minutes' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --profile bootcamp --region us-east-1
```

**That number, in milliseconds, is your worst-case data loss if us-east-1
disappears right now.** Almost nothing else in AWS lets you watch your own RPO
on a chart, which is the main reason this day's primary data store is DynamoDB
rather than RDS.

Before you leave it on, read the trade: global tables are multi-active with
**last-writer-wins and no conflict resolution beyond a timestamp**. If your
application writes to both regions during a split brain, one of those writes is
discarded silently, and the loser is whichever clock was behind. That is a
correctness property of your application, not of DynamoDB.

---

## Step 7 — Break something and time it

### 7a. Dry run first. Every time.

```bash
aws lambda invoke --function-name $(terraform output -raw chaos_function_name) \
  --payload '{"mode":"terminate_instance","dry_run":true}' \
  --cli-binary-format raw-in-base64-out \
  --profile bootcamp --region us-east-1 /dev/stdout
```

Read the plan. It names the target, its AZ, and an `expected` field describing
what should happen. Comparing that against what actually happens is the whole
exercise.

> The dry run is not training wheels. It is how you verify that the blast
> radius is what you think it is **before the blast**, and every chaos exercise
> in a real organisation starts with one.

### 7b. Open a curl loop in a second terminal

```bash
while true; do
  date -u +%T
  curl -s -m 2 -o /dev/null -w '%{http_code}\n' \
    http://$(terraform output -raw alb_dns_name)/
  sleep 1
done
```

### 7c. For real, with a stopwatch

```bash
aws lambda invoke --function-name $(terraform output -raw chaos_function_name) \
  --payload '{"mode":"terminate_instance","dry_run":false}' \
  --cli-binary-format raw-in-base64-out \
  --profile bootcamp --region us-east-1 /dev/stdout
```

Stop the clock when target health returns to 2 healthy:

```bash
watch -n 2 "aws elbv2 describe-target-health \
  --target-group-arn $(terraform output -raw target_group_arn) \
  --profile bootcamp --region us-east-1 \
  --query 'TargetHealthDescriptions[].TargetHealth.State' --output text"
```

Write the measured number next to prediction (a).

**What you will probably observe:** the curl loop never returns a non-200. The
ALB removed the terminated instance from rotation before you noticed, and the
surviving instance absorbed everything. Full capacity took two to four minutes
to come back, dominated by instance boot and the grace period.

That is a successful test, and it demonstrates the thing worth internalising:
**an AZ-level compute failure, in a correctly built stack, is a capacity event
rather than an outage.** Your users saw nothing. Your redundancy is what saw
it.

### 7d. Now isolate an AZ

```bash
aws lambda invoke --function-name $(terraform output -raw chaos_function_name) \
  --payload '{"mode":"isolate_az","dry_run":false}' \
  --cli-binary-format raw-in-base64-out \
  --profile bootcamp --region us-east-1 /dev/stdout
```

This associates a **deny-all NACL** with one AZ's private subnet. The instances
there keep running, keep passing EC2 status checks, and become unreachable.

Time how long the ALB takes to stop routing to them. It should be
`interval × unhealthy_threshold` = 60 seconds, and confirming that the
arithmetic in step 3 is real is the point.

**Then notice the limit, honestly:** this is not an AZ failure. A real one
takes the NAT gateway, the RDS standby, the EBS control plane for that zone,
and every cross-AZ dependency you did not know you had, simultaneously, while
the AWS console is also degraded. This takes the network. AWS Fault Injection
Service gets much closer, at ~$0.10 per action-minute, and is the right next
step after this lab.

### 7e. Restore, and notice which one was harder

```bash
aws lambda invoke --function-name $(terraform output -raw chaos_function_name) \
  --payload '{"mode":"restore"}' \
  --cli-binary-format raw-in-base64-out \
  --profile bootcamp --region us-east-1 /dev/stdout
```

Open `lambda/chaos.py` and compare `mode_isolate_az` against `mode_restore`.
One is an API call. The other has to work out which subnets it broke, find the
VPC's default NACL, and reason about the fact that **there is no "detach a
NACL" call — you can only replace an association.**

> That ratio is not an accident of this file. It is the shape of the whole
> problem: the outbound path is one call, and the return path has to
> reconstruct state the outbound path destroyed. Here it is trivial because the
> state is one association id. In a real failover the state is every write that
> happened while you were failed over.

### 7f. Run the recovery workflow

```bash
aws stepfunctions start-execution \
  --state-machine-arn $(terraform output -raw recovery_state_machine_arn) \
  --profile bootcamp --region us-east-1
```

It is in dry run by default. Read the execution history:

```bash
aws stepfunctions get-execution-history --execution-arn <arn> \
  --profile bootcamp --region us-east-1 \
  --query 'events[].[timestamp,type,stateEnteredEventDetails.name]' --output table
```

**Those timestamps are your RTO measurement.** That is why the workflow is a
state machine and not a Lambda with a try/except: the measurement is a free
side effect of the structure.

Now for real:

```bash
terraform apply -var 'recovery_dry_run=false'
aws stepfunctions start-execution \
  --state-machine-arn $(terraform output -raw recovery_state_machine_arn) \
  --profile bootcamp --region us-east-1
```

If the assessment classifies the damage as `regional`, an approval request
arrives by email with a task token:

```bash
aws stepfunctions send-task-success --task-token '<TOKEN>' --task-output '{}' \
  --profile bootcamp --region us-east-1
```

**Time the approval separately.** In every real drill it is the largest single
component of the RTO and the one never included in the estimate. The workflow's
`approval_timeout_minutes = 30` is not an estimate — it is a **ceiling**. Your
worst-case approved failover starts at minute 30.

### 7g. Pull the brake

```bash
aws ssm put-parameter --name $(terraform output -raw kill_switch_parameter) \
  --value disabled --overwrite --profile bootcamp --region us-east-1

aws stepfunctions start-execution \
  --state-machine-arn $(terraform output -raw recovery_state_machine_arn) \
  --profile bootcamp --region us-east-1
```

The execution fails immediately with `KillSwitchEngaged` and changes nothing.

**A kill switch nobody has flipped is a hypothesis.** Flip it. Then flip it
back:

```bash
aws ssm put-parameter --name $(terraform output -raw kill_switch_parameter) \
  --value enabled --overwrite --profile bootcamp --region us-east-1
```

Note what did **not** happen: `terraform apply` did not put it back on its own.
The parameter carries `lifecycle { ignore_changes = [value] }`, because if
somebody pulled the brake during an incident, the next apply must not helpfully
undo it.

### 7h. Read the failback you cannot automate

```bash
aws lambda invoke \
  --function-name $(terraform output -raw recovery_function_name) \
  --payload '{"action":"failback","dry_run":true}' \
  --cli-binary-format raw-in-base64-out \
  --profile bootcamp --region us-east-1 /dev/stdout
```

Read the `CANNOT_REVERSE` list in the response. Five items, and they are the
actual content of the failback problem:

1. Writes that landed in the DR region while you were failed over
2. Divergence created by both regions accepting writes during a partition
3. Connection pools and message leases that must be drained or restarted
4. **The fact that you have had no health signal from the primary since the
   health check was inverted**
5. The DR environment's scale, which is still whatever the incident needed

Item 4 is the one that produces second outages. While the health check is
inverted, Route 53 reports the primary unhealthy **regardless of whether it
is** — the signal you would use to notice recovery has been deliberately
disabled by your own failover.

> **Failing over is a decision about ROUTING. Failing back is a decision about
> DATA**, and nothing generic knows what your writes mean. Every DR exercise
> that ends at "we failed over successfully" has tested half a procedure and
> measured a third of an RTO.

---

## Step 8 — Restore something

A backup nobody has restored is a file.

```bash
aws dynamodb restore-table-to-point-in-time \
  --source-table-name $(terraform output -raw dynamodb_table_name) \
  --target-table-name $(terraform output -raw dynamodb_table_name)-restored \
  --use-latest-restorable-time \
  --profile bootcamp --region us-east-1
```

Time it to `ACTIVE`. Then notice the part nobody counts:

> **The restored table has a DIFFERENT NAME.** Your application cannot use it
> until something repoints every consumer. That work is RTO too, it is
> application work performed under pressure, and it is where the RTO of a data
> restore actually goes.

Compare against prediction (c). This is usually the largest gap of the three.

Delete the restored table when you are done — it bills like any other table.

```bash
aws dynamodb delete-table \
  --table-name $(terraform output -raw dynamodb_table_name)-restored \
  --profile bootcamp --region us-east-1
```

**Also do a real AWS Backup restore at least once**, into the DR region. That
is where the failure modes live that a backup report cannot show you: a rotated
KMS key, a missing AMI, an instance type unavailable in that region, a
deprecated engine version, an IAM role with backup permissions and not restore
permissions. All invisible in a report. All obvious after one restore.

---

## Step 9 — Fix the NAT gateway and watch a finding disappear

Run the audit first, so you have a before:

```bash
cd ../python
pip install -r requirements.txt
python3 dr_audit.py --profile bootcamp --region us-east-1 \
  --dr-region us-west-2 --prefix cbc-day08
```

Expect **15 findings, 195 points, 0/100, grade F** — assuming you have not yet
done step 6a's backup or step 8's restore. If you have, you will see fewer;
that is the contract's STATE B and it is documented below.

Now:

```bash
cd ../terraform
terraform apply -var 'nat_gateway_strategy=per_az'
cd ../python
python3 dr_audit.py --profile bootcamp --region us-east-1 \
  --dr-region us-west-2 --prefix cbc-day08 --min-severity HIGH
```

DR-002 is gone. Then look at the price:

```bash
cd ../terraform && terraform output cost_breakdown
```

~$36/month more, for a NAT gateway in the second AZ plus its public IPv4
address.

> **DR-002 is the only check in the contract that fires on your own
> correctly-intended stack rather than on a deliberately broken example, and
> the only one you clear by SPENDING MONEY rather than by fixing a mistake.**
>
> That is deliberate. An auditor whose findings are all strawmen teaches people
> that findings are strawmen.

Decide honestly whether this workload should pay it. Both answers are
defensible. Only one of them is defensible **silently**.

---

## Step 10 — If you own a domain, complete the DNS half

Skip this if you do not. The Route 53 **health check** exists either way —
health checks do not require a hosted zone — so you have the detection half of
DNS failover without the record half.

```bash
terraform apply \
  -var 'hosted_zone_id=Z...' \
  -var 'dns_record_name=app.example.com' \
  -var 'route53_ttl=60'
```

Then dig the record, invert the health check, and **time how long the old
answer keeps coming back**:

```bash
dig +short app.example.com
aws route53 update-health-check \
  --health-check-id $(terraform output -raw route53_health_check_id) \
  --inverted
watch -n 1 dig +short app.example.com
```

That interval is TTL, and it is spent RTO. Then **un-invert it**, explicitly,
because while it is inverted you have no health signal from the primary at all:

```bash
aws route53 update-health-check \
  --health-check-id $(terraform output -raw route53_health_check_id) \
  --no-inverted
```

> Try setting `route53_ttl = 900` and running `terraform plan`. It fails, with
> an error rather than a warning, because the variable carries a cross-variable
> validation refusing a TTL above a quarter of `rto_target_minutes`.
>
> That is **enforcement instead of detection**, and it is deliberately *not* an
> audit check. An audit tells you about a TTL problem after you have shipped
> it; a validation refuses to ship it. When you can do either, do this one — an
> auditor finding is a ticket and a plan failure is a conversation.

---

## Step 11 — Tear down

```bash
terraform destroy
```

Then work through **`teardown-checklist.md`**. `destroy` does not remove the
EBS snapshot, and cross-region resources need the DR region checked separately.

**Cross-region resources are, by construction, the ones that survive the thing
that was supposed to remove them.** That is true of `terraform destroy` for
exactly the same reason it is true of a regional outage, and it is why the DR
region is where forgotten spend accumulates.

---

## Building the auditor yourself

```bash
cd ../python
DR_AUDIT_MODULE=dr_audit_challenge PYTHONPATH=challenge \
  python3 -m unittest discover -s tests -v
```

47 tests, no credentials, roughly three hours in five checkpoints. The
challenge file is **generated from the reference** — identical imports,
identical `Finding`, identical helpers, identical renderers, identical
collector, identical CLI — with the sixteen check bodies removed and their
docstrings left in place, because the docstring is the specification.

The header briefing covers the two things that cost people the most time:

**Which checks are not independent.** Six relationships, including that
DR-004's severity depends on DR-003's subject, that DR-008 and DR-009 both
iterate the same vault list (so two vaults means two findings from each), and
that DR-015 and DR-016 share a precondition — a bug in
`workflow_irreversible_actions()` silences both at once.

**The clock.** Never call `datetime.now()`; call `_now(stack)`. DR-008 uses
minutes and DR-016 uses days, and getting the units the wrong way round is a
test failure that looks like a logic error. And `absent` is not `zero`: an
absent recovery point time is a finding, and an undateable execution is a skip.

---

## The finding contract

```text
=============================================================================
DAY 08 FINDING CONTRACT — LOCKED AT CP2
=============================================================================
This block is reproduced identically in five places. Change one, change all
five: README.md, lab/README.md, lab/terraform/outputs.tf (finding_contract),
lab/python/dr_audit.py (module docstring), lab/python/tests/test_checks.py.

Weights are the repo-wide ones, identical to Days 03 through 07:
CRITICAL 25, HIGH 10, MEDIUM 4, LOW 1, INFO 0. Score is 100 minus the sum,
floored at 0. Grades: 90+ A, 75+ B, 60+ C, 40+ D, below that F.

Day 08 has NO LOW AND NO INFO CHECKS, and that is a decision rather than an
oversight. On this day every fault either costs you data or costs you time
during an outage. There is no informational gap in a recovery path — a thing
that does not matter when the region is on fire does not belong in an audit
whose whole subject is the hour the region is on fire.

STATIC STATE — after terraform apply with the shipped defaults
(create_insecure_examples = true, nat_gateway_strategy = "single",
create_rds = false, enable_vault_lock = false,
s3_replication_time_control = false, hosted_zone_id = ""), before any backup
job has run, before any restore, before any workflow execution.

  ID       SEVERITY   W   N  PTS  SOURCE RESOURCE
  -------  --------  --  --  ---  ------------------------------------------
  DR-001   CRITICAL  25   1   25  aws_autoscaling_group.single_az
  DR-002   HIGH      10   1   10  aws_nat_gateway.main - strategy "single"
  DR-003   HIGH      10   1   10  aws_autoscaling_group.single_az
  DR-004   MEDIUM     4   1    4  aws_autoscaling_group.single_az
  DR-005   CRITICAL  25   0    0  none - SILENT BY SITUATION, see below
  DR-006   HIGH      10   0    0  none - SILENT BY SITUATION, see below
  DR-007   MEDIUM     4   1    4  aws_dynamodb_table.no_pitr
  DR-008   HIGH      10   2   20  aws_backup_vault.main, aws_backup_vault.dr
  DR-009   MEDIUM     4   2    8  aws_backup_vault.main, aws_backup_vault.dr
  DR-010   CRITICAL  25   1   25  account-level singleton - no restore, ever
  DR-011   HIGH      10   0    0  none - SILENT BY DESIGN, see below
  DR-012   MEDIUM     4   1    4  aws_s3_bucket.unversioned
  DR-013   HIGH      10   1   10  aws_s3_bucket_replication_configuration.primary
  DR-014   HIGH      10   0    0  none - SILENT BY SITUATION, see below
  DR-015   CRITICAL  25   1   25  aws_sfn_state_machine.naive
  DR-016   CRITICAL  25   2   50  both Day 08 state machines - never executed
  -------  --------  --  --  ---  ------------------------------------------
  TOTALS                    15  195

  FIFTEEN findings from SIXTEEN checks. Four checks are silent here and they
  are silent for two different reasons, which is the most useful thing in this
  table: three because this particular stack cannot currently produce the
  fault (DR-005, DR-006, DR-014), and one because NO configuration of this
  stack can ever produce it (DR-011).

  Score: 100 - 195 = -95, floored to 0/100. Grade F.

  SEVERITY HISTOGRAM of the 16 checks: 5 CRITICAL, 7 HIGH, 4 MEDIUM,
  0 LOW, 0 INFO.

THE FOUR STATES

  STATE                                        FINDINGS  POINTS    SCORE  GRADE
  -------------------------------------------  --------  ------  -------  -----
  A  Static: after apply, nothing run yet            15     195    0/100      F
  B  Live: after lab steps 6a, 7 and 8 - one
     on-demand backup copied to DR, one
     workflow execution succeeded, one
     restore performed                               11     125    0/100      F
  C  Sixty-one minutes after B, WITH NOTHING
     CHANGED - the recovery points have aged
     past rpo_target_minutes                         13     145    0/100      F
  -------------------------------------------  --------  ------  -------  -----
  D  Reference build: create_insecure_examples
     = false, nat_gateway_strategy = "per_az",
     s3_replication_time_control = true,
     enable_vault_lock = true, plus a completed
     backup, a completed restore and one
     successful workflow execution                    0       0  100/100      A

  STATE C IS THE POINT OF THIS TABLE AND IT IS THE THESIS OF THE DAY.

  Between B and C, nobody deploys anything. No console click, no apply, no
  merge. Two findings appear because time passed and DR-008 measures the age
  of the newest recovery point against the RPO you declared.

  An audit that passes at 14:00 fails at 15:01 on an unchanged account.

  That is not a defect in the auditor. It is the correct behaviour, and it is
  the difference between a configuration audit and a recovery audit. RTO and
  RPO are not properties of a configuration. They are claims about a
  PROCEDURE, and a claim about a procedure decays continuously from the last
  time somebody ran it. A merge-time-only audit certifies the account as it
  was on the day somebody last changed it, and that is not the property a DR
  posture needs to have.

  With the shipped hourly backup schedule, DR-008 therefore SAWTOOTHS: silent
  for the minutes after each successful job, firing again as the recovery
  point ages past the 60-minute RPO. Two numbers that are one minute apart
  produce different audit results, and both are correct. If that is
  uncomfortable, the fix is not a looser check - it is a schedule that is
  actually faster than the RPO you claimed.

  Day 07's contract had the finding COUNT identical before and after the lab
  with a different SET. Day 08 does not repeat that trick, because forcing it
  here would have been dishonest: doing the work genuinely removes findings.
  What Day 08 has instead is a state that gets WORSE while you are asleep.

SILENT BY DESIGN — DR-011, a replication or backup copy target in the same
region as its source.

  No shipped default and no typo can produce this fault. The dr_region
  variable carries a cross-variable validation refusing dr_region ==
  aws_region; the S3 replica bucket is created under provider = aws.dr; the
  AWS Backup copy rule targets the DR vault or does not exist. There is no
  path through this Terraform that puts a DR copy in the primary region, so
  the plan refuses to produce one.

  It is not a hypothetical fault. S3 Same-Region Replication is a real and
  legitimate feature - compliance separation, log aggregation, cross-account
  isolation - and an AWS Backup copy rule will happily target a vault in the
  source region. Both get pressed into service as "DR" by people who were
  solving a different problem last week, and both produce a second copy inside
  the same blast radius.

  A check that stays silent because the stack cannot produce the fault is
  evidence that the auditor does not cry wolf.

SILENT BY SITUATION — DR-005, DR-006 and DR-014.

  DR-005 and DR-006 are the RDS checks. create_rds defaults to false, so there
  is no RDS instance to be single-AZ or to have one day of retention. The
  moment somebody sets create_rds = true with the shipped defaults, BOTH fire
  immediately, for 35 points, because rds_multi_az defaults to false and
  rds_backup_retention_days defaults to 1.

  DR-014 is the Route 53 failover-record check. The failover record sets
  require a hosted zone you own, hosted_zone_id defaults to empty, so there
  are no failover records to be missing a health check.

  NOTHING HAS TO CHANGE FOR ANY OF THESE TO STOP BEING TRUE, and in DR-005's
  case the change is one boolean typed by somebody adding a database on a
  Thursday.

THE DIFFERENCE MATTERS. Silent by design tells you something about the
auditor: it cannot fire, so its silence is a property of the tool. Silent by
situation tells you nothing about the auditor and everything about today's
account - and "we have no findings" and "we have nothing to find" are
different states that render identically in every report. Never read the
second as the first.

CHECK INTERACTIONS THAT ARE DELIBERATE, NOT BUGS

  DR-001, DR-003 and DR-004 all fire on aws_autoscaling_group.single_az and
  they are not duplicates. DR-001 is WHERE it runs - one failure domain.
  DR-003 is WHETHER IT NOTICES an application failure - health_check_type
  "EC2" means a deadlocked process is a healthy instance forever. DR-004 is
  WHETHER A REPLACEMENT CAN START AT ALL - a zero grace period is a
  termination loop. Fixing any one leaves the other two, the remediations are
  unrelated, and in most organisations they have different owners: the network
  team owns the subnets, the platform team owns the ASG, and the application
  team owns how long a boot takes.

  DR-002 IS THE ONLY CHECK THAT FIRES ON YOUR OWN CORRECTLY-INTENDED STACK
  rather than on a deliberately broken example. nat_gateway_strategy defaults
  to "single", which is a real, defensible, extremely common cost decision
  that puts a single-AZ dependency inside an architecture everybody calls
  multi-AZ. It is also the only finding in this contract that you clear by
  SPENDING MONEY rather than by fixing a mistake - roughly $36/month more.
  That is deliberate. An auditor whose findings are all strawmen teaches
  people that findings are strawmen.

  DR-008 AND DR-010 LOOK LIKE THE SAME CHECK AND ARE NOT. DR-008 asks "is
  there a recent enough backup". DR-010 asks "has anybody ever proved a backup
  can be turned back into a system". A vault full of fresh, correctly
  retained, cross-region-copied recovery points that has never had a single
  restore performed against it scores 0 on DR-008 and 25 on DR-010, and that
  is the normal state of most organisations. The failure modes DR-010 exists
  for - a rotated KMS key, a missing AMI, an instance type unavailable in the
  DR region, a deprecated engine version, a restore that works and takes nine
  hours - are all invisible in a backup report and all obvious in one restore
  test.

  DR-010 AND DR-016 ARE THE SAME IDEA ABOUT TWO DIFFERENT THINGS - restore
  versus failover - and both are reported at a level ABOVE any single
  resource. DR-010 is an account-level singleton; DR-016 is per state machine.
  Neither is attached to a data resource, deliberately: they are statements
  about the ORGANISATION, not about a bucket, and attaching them to a resource
  id invites somebody to close the finding by deleting the resource.

  DR-013 FIRES ON A CORRECTLY-CONFIGURED REPLICATION RULE. The rule works.
  Objects replicate. What is absent is the METRIC, because Replication Time
  Control is off - and without it there is no way to answer "what is my
  current replication lag", which means there is no way to state an RPO that
  is anything more than an adjective. This is the only check in the set that
  fires on something which is not broken, and it is Day 06's argument in new
  clothes: a summary you cannot check is worse than no summary, and an RPO you
  cannot measure is worse than no RPO, because you will quote it.

  DR-009 FIRES TWICE, ONCE PER VAULT, INCLUDING THE DR VAULT, and is
  deliberately not deduplicated up to the plan. A locked primary vault beside
  an unlocked DR copy vault is a real and common asymmetry, and it is exactly
  backwards: the DR vault is the one an attacker who has already compromised
  the primary account will reach for, because it is the copy that survives
  everything they just did.

  DR-016 FIRES ON THE NAIVE STATE MACHINE TOO, and after lab step 7 it is the
  only DR-016 finding left. An automated failover that has never been executed
  is untested; an automated failover that has never been executed AND has no
  kill switch, no assessment, no approval gate and no verification is untested
  in a way that will be discovered by production. DR-015 and DR-016 fire on
  the same resource for genuinely different reasons and neither remediates the
  other.
=============================================================================
```

---

## What to take away

Three sentences, in order of how much they will change how you work.

**An untested recovery path is a hypothesis.** Everything else on this page is
a consequence of that.

**The failover path is the only code in your system that runs exclusively
during your worst hour**, which makes it structurally the least exercised code
you own and the most confidently described.

**An audit that passes at 14:00 fails at 15:01 on an unchanged account** — and
that is correct behaviour, not a bug, because RTO and RPO are claims about a
procedure and a claim about a procedure decays from the last time somebody ran
it.

Commit `rto-measurements.md`, dated. An RTO without a date is an RTO from an
architecture that no longer exists.
