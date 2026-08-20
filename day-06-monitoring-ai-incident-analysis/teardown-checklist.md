# Day 06 — Teardown Checklist

**CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp**

> `terraform destroy` is not enough on this day, and it is not a matter of
> being careful. Three categories of cost here are **structurally invisible to
> a destroy**: resources Terraform never created, resources AWS provides no API
> to delete, and spend that was never a resource at all.

Work through this after `terraform destroy` returns successfully. It takes
about five minutes and the script at the bottom does most of it.

---

## Why this day is different

Days 01–05 billed for things that **exist**. You could list them, count them,
and `destroy` removed them. The teardown checklists for those days were about
catching stragglers.

Day 06 bills for things that **happened**.

| Category | Example | Does `destroy` handle it? |
|---|---|---|
| Normal resources | alarms, dashboards, Lambda, SNS, DynamoDB | Yes |
| Log groups Terraform created | the workload, chaos and analyser groups | Yes |
| **Log groups Terraform did not create** | anything a service made for itself | **No — it does not know they exist** |
| **Custom metrics** | everything the metric filters published | **No — there is no delete API at all** |
| **Bedrock spend** | every token you sent | **No — there was never a resource** |

That last row is the new one, and it is worth sitting with. There is nothing to
delete, nothing to find in a sweep, and nothing in a resource count that
reflects it. If you leave this lab running with automatic analysis enabled
behind a flapping alarm, no teardown checklist in the world will catch it —
only a budget will.

---

## 1. Destroy the stack

```bash
cd lab/terraform
terraform destroy -auto-approve
```

Expect roughly 40 resources destroyed. Two things to watch for in the output:

- **The Bedrock invocation logging configuration**, if you enabled it, is an
  **account-level, region-singleton** setting. Destroying it turns invocation
  logging off for *everything* in that region, not just this lab. If another
  team relies on it, you have just removed their audit trail. Check before you
  run this in a shared account.
- **DynamoDB and SQS** destroy cleanly and cost nothing at lab volumes.

---

## 2. Log groups that survived

Two kinds survive, and both are ordinary:

**Groups Terraform never created.** If you invoked a Lambda whose log group was
not declared in Terraform, Lambda created it — with retention `Never expire`.
The naive analyser's group *is* declared, so it goes; but if you experimented
with any other function it will not have.

**Groups you deliberately left unretained.** `cbc-day06-legacy-app-*` is
declared, so `destroy` removes it. Data written into it before the destroy has
already been billed for ingestion; the storage charge stops.

Check for anything left:

```bash
aws logs describe-log-groups --profile bootcamp --region us-east-1 \
  --log-group-name-prefix /cbc-day06 \
  --query 'logGroups[].{Name:logGroupName,Retention:retentionInDays,Bytes:storedBytes}' \
  --output table

aws logs describe-log-groups --profile bootcamp --region us-east-1 \
  --log-group-name-prefix /aws/lambda/cbc-day06 \
  --query 'logGroups[].{Name:logGroupName,Retention:retentionInDays,Bytes:storedBytes}' \
  --output table
```

Delete anything that comes back:

```bash
aws logs delete-log-group --log-group-name <name> \
  --profile bootcamp --region us-east-1
```

**While you are here, do the account-wide sweep.** It will find orphans from
Days 01–05 as well, and this is the single highest-value command in the whole
bootcamp for anyone with a personal AWS account:

```bash
aws logs describe-log-groups --profile bootcamp --region us-east-1 \
  --query 'logGroups[?!not_null(retentionInDays)].{Name:logGroupName,Bytes:storedBytes}' \
  --output table
```

---

## 3. Custom metrics — the ones you cannot delete

This stack published seven custom metrics:

| Metric | Dimension values |
|---|---|
| `RequestCount` | none |
| `ErrorCount` | none |
| `LatencyMillis` | none |
| `ErrorCountByType` | 4 (`DB_CONN_TIMEOUT`, `POOL_EXHAUSTED`, `CIRCUIT_OPEN`, `UPSTREAM_5XX`) |

Plus, if `create_insecure_examples` was true, one per unique request ID from
the deliberately broken filter — which is one per request the chaos function
generated.

**There is no `DeleteMetric` API.** No console button. No support ticket. A
custom metric ages out **fifteen months after its last datapoint**.

Deleting the metric filter stops *new* metrics being created. It does not
remove the ones that exist.

See what you made:

```bash
aws cloudwatch list-metrics --namespace CareerByteCode/Day06 \
  --profile bootcamp --region us-east-1 \
  --query 'length(Metrics)' --output text
```

**The arithmetic:** each of those is $0.30/month until it ages out. Seven
metrics is $2.10/month for fifteen months — about $31. If you ran the chaos
function with `lines: 4000` while the high-cardinality filter was attached, it
is four thousand metrics: **$1,200/month, for fifteen months, roughly $18,000.**

That is not a scare story bolted onto a lab. It is the exact shape of the
mistake, at lab scale. The only defence is not making it, which is why
`chaos_workload.py` bounds `ERROR_TYPES` to a four-element tuple and why
OBS-003 is CRITICAL.

If the number above is large, the honest advice is: check your Cost Explorer
under CloudWatch for the next few months so the line does not surprise you, and
know that there is nothing else to do.

---

## 4. Dashboards

```bash
aws cloudwatch list-dashboards --profile bootcamp --region us-east-1 \
  --dashboard-name-prefix cbc-day06 \
  --query 'DashboardEntries[].DashboardName' --output table
```

`destroy` removes both. They are only billable beyond the first three per
account, so on a fresh account this is hygiene rather than money — but a
dashboard left behind pointing at metrics that no longer exist is a future
version of OBS-008 for whoever inherits the account.

---

## 5. Alarms

```bash
aws cloudwatch describe-alarms --alarm-name-prefix cbc-day06 \
  --profile bootcamp --region us-east-1 \
  --query 'MetricAlarms[].AlarmName' --output table

aws cloudwatch describe-alarms --alarm-types CompositeAlarm \
  --alarm-name-prefix cbc-day06 \
  --profile bootcamp --region us-east-1 \
  --query 'CompositeAlarms[].AlarmName' --output table
```

Both should be empty. If you created any by hand during Step 5 or Step 8 —
`put-metric-alarm` outside Terraform — they will still be here, because
Terraform does not know about them. That is Day 05's drift lesson arriving from
the other direction.

---

## 6. Bedrock — the one with nothing to delete

**There is no resource. There is no sweep. There is nothing to check.**

What you can do is find out what you spent:

```bash
# What did the analyser actually invoke? Its own logs know, if it still exists.
aws logs filter-log-events \
  --log-group-name /aws/lambda/cbc-day06-analyser-XXXX \
  --filter-pattern '"input_tokens"' \
  --profile bootcamp --region us-east-1 \
  --query 'events[].message' --output text 2>/dev/null | head -20
```

If you enabled invocation logging, the destination log group has every prompt
and completion — and if you *did* enable it, **check that group is gone too**,
because it contains your log content in full:

```bash
aws logs describe-log-groups --profile bootcamp --region us-east-1 \
  --log-group-name-prefix /cbc-day06/bedrock-invocations \
  --query 'logGroups[].logGroupName' --output table
```

And confirm invocation logging is actually off, especially if you turned it on
in a shared account:

```bash
aws bedrock get-model-invocation-logging-configuration \
  --profile bootcamp --region us-east-1 2>&1 | head -5
```

`ResourceNotFoundException` here is the correct answer after teardown.

**For a normal run of this lab the Bedrock spend is a few cents.** A dozen
invocations at 12,000 tokens each is roughly $0.12 with Haiku. The reason this
section exists is not that number — it is that the number is *unbounded in
principle* and leaves no trace to sweep. Set a budget.

---

## 7. The three silent-growth traps, re-checked

| Trap | Check | Fix |
|---|---|---|
| Log groups with no retention | the account-wide sweep in §2 | `put-retention-policy` or delete |
| Custom metrics that cannot be deleted | §3 | none — wait 15 months |
| An analyser behind a flapping alarm | Cost Explorer, Bedrock line | disable the EventBridge rule; fix the alarm's M-of-N |

---

## One-shot verification

Save as `verify-teardown.sh`, `chmod +x`, run after `terraform destroy`.

```bash
#!/usr/bin/env bash
# Day 06 teardown verification.
#
# Exits non-zero if anything remains, so it can go in CI or a cleanup cron.
# Everything it reports is something `terraform destroy` cannot handle on its
# own — that is the entire reason this file exists.

set -uo pipefail

PROFILE="${AWS_PROFILE:-bootcamp}"
REGION="${AWS_REGION:-us-east-1}"
PREFIX="cbc-day06"
NS="CareerByteCode/Day06"
PROBLEMS=0

aws() { command aws --profile "$PROFILE" --region "$REGION" "$@"; }

say()  { printf '\n=== %s\n' "$1"; }
bad()  { printf '  !! %s\n' "$1"; PROBLEMS=$((PROBLEMS + 1)); }
good() { printf '  ok  %s\n' "$1"; }

say "1. Log groups created by this lab"
LEFT=$(aws logs describe-log-groups --log-group-name-prefix "/$PREFIX" \
        --query 'logGroups[].logGroupName' --output text)
LEFT="$LEFT $(aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/$PREFIX" \
        --query 'logGroups[].logGroupName' --output text)"
for group in $LEFT; do
  bad "log group remains: $group"
  printf '      aws logs delete-log-group --log-group-name %s --profile %s --region %s\n' \
    "$group" "$PROFILE" "$REGION"
done
[ -z "$(echo "$LEFT" | tr -d '[:space:]')" ] && good "no lab log groups remain"

say "2. Log groups with NO RETENTION, account-wide"
# Not scoped to this lab on purpose. This is the sweep worth running monthly.
UNRETAINED=$(aws logs describe-log-groups \
  --query 'logGroups[?!not_null(retentionInDays)].logGroupName' --output text)
if [ -n "$UNRETAINED" ]; then
  printf '  ?? groups with Never expire (not necessarily from this lab):\n'
  for group in $UNRETAINED; do printf '      %s\n' "$group"; done
  printf '      These bill at $0.03/GB-month forever. Review them.\n'
else
  good "no unretained log groups anywhere in this region"
fi

say "3. Alarms"
ALARMS=$(aws cloudwatch describe-alarms --alarm-name-prefix "$PREFIX" \
  --query 'MetricAlarms[].AlarmName' --output text)
COMPOSITES=$(aws cloudwatch describe-alarms --alarm-types CompositeAlarm \
  --alarm-name-prefix "$PREFIX" --query 'CompositeAlarms[].AlarmName' --output text)
for alarm in $ALARMS $COMPOSITES; do
  bad "alarm remains: $alarm  (created outside Terraform?)"
done
[ -z "$ALARMS$COMPOSITES" ] && good "no lab alarms remain"

say "4. Dashboards"
DASH=$(aws cloudwatch list-dashboards --dashboard-name-prefix "$PREFIX" \
  --query 'DashboardEntries[].DashboardName' --output text)
for dashboard in $DASH; do bad "dashboard remains: $dashboard"; done
[ -z "$DASH" ] && good "no lab dashboards remain"

say "5. Lambda functions and the DynamoDB table"
FNS=$(aws lambda list-functions \
  --query "Functions[?starts_with(FunctionName, '$PREFIX')].FunctionName" --output text)
for fn in $FNS; do bad "function remains: $fn"; done
[ -z "$FNS" ] && good "no lab functions remain"

TABLES=$(aws dynamodb list-tables \
  --query "TableNames[?starts_with(@, '$PREFIX')]" --output text)
for table in $TABLES; do bad "dynamodb table remains: $table"; done
[ -z "$TABLES" ] && good "no lab tables remain"

say "6. Bedrock model invocation logging"
if aws bedrock get-model-invocation-logging-configuration >/dev/null 2>&1; then
  bad "invocation logging is STILL ENABLED for this region"
  printf '      It is account-level. Confirm nobody else needs it, then:\n'
  printf '      aws bedrock delete-model-invocation-logging-configuration --profile %s --region %s\n' \
    "$PROFILE" "$REGION"
else
  good "invocation logging is off (ResourceNotFoundException is correct here)"
fi

say "7. Custom metrics — CANNOT BE DELETED, reported for awareness"
COUNT=$(aws cloudwatch list-metrics --namespace "$NS" \
  --query 'length(Metrics)' --output text 2>/dev/null || echo 0)
printf '  ?? %s custom metric(s) in namespace %s\n' "$COUNT" "$NS"
printf '     There is no DeleteMetric API. These age out 15 months after their\n'
printf '     last datapoint, at $0.30 each per month until then.\n'
if [ "$COUNT" != "0" ] && [ "$COUNT" != "None" ]; then
  python3 - "$COUNT" <<'PY' 2>/dev/null || true
import sys
n = int(sys.argv[1])
print(f"     Estimated: ${n * 0.30:,.2f}/month, ~${n * 0.30 * 15:,.2f} total.")
if n > 100:
    print("     That is the unbounded-cardinality mistake. It is permanent.")
PY
fi

say "8. Bedrock spend"
printf '  ?? Nothing to check. Bedrock is priced per token and creates no\n'
printf '     resource, so no sweep can find it. Confirm in Cost Explorer under\n'
printf '     the Bedrock service, and set a budget before the next AI lab.\n'

printf '\n'
if [ "$PROBLEMS" -eq 0 ]; then
  printf 'TEARDOWN CLEAN — %s deletable item(s) remaining.\n' "$PROBLEMS"
  printf 'Note the two sections above marked ?? are informational and cannot be fixed by deleting anything.\n'
  exit 0
else
  printf 'TEARDOWN INCOMPLETE — %s item(s) remaining. Commands are printed above.\n' "$PROBLEMS"
  exit 1
fi
```

---

## Final check

```bash
./verify-teardown.sh
```

Clean output means every **deletable** thing is gone. It does not mean the day
cost you nothing — the custom metrics are still there and the Bedrock tokens
are already spent. That gap between "clean teardown" and "no ongoing cost" is
the honest summary of Day 06, and it is the reason Day 09 exists.

**Before you close the laptop:** if you enabled Bedrock invocation logging in a
shared account, tell whoever else uses that region that you turned it off.
