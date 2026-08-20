# Day 07 — Interview & Career Guidance

**CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp**

Fifteen questions with full answers, then a rapid-fire table.

Security questions in interviews go one of two ways. Either the candidate lists
services — "GuardDuty, Security Hub, CloudTrail, we enabled them all" — or they
talk about what happens when the system is wrong. Only the second sounds like
experience, and four of the questions below are specifically about that.

---

## 1. Walk me through what happens between GuardDuty raising a finding and something changing in the account.

**The chain, and the gates on it.**

GuardDuty analyses CloudTrail events, VPC flow logs and DNS queries, and raises
a finding. EventBridge matches it and invokes a responder. The responder makes
a decision. If it decides to act, it contains — and records what it did.

Four gates on that path, and each one exists because of a specific failure:

1. **The kill switch**, read at runtime, before anything else. If it is
   DISARMED the responder logs, notifies and stops.
2. **Is it a sample finding?** GuardDuty's test findings carry fake resource
   ids; acting on them isolates `i-99999999`, which does not exist, or worse
   matches something that does.
3. **Is the finding TYPE on the allow-list?** Not the severity — the type.
4. **Is the containment mode reversible?** An unrecognised mode is refused
   loudly rather than interpreted.

And the thing I would emphasise: **all four gates log their decision, including
when the decision is to do nothing.** "Why did nothing happen" is asked far more
often than the opposite, and a responder that only speaks when it acts cannot
answer it.

---

## 2. Your automation isolated a production instance at 3am and the finding turned out to be a false positive. What do you change?

*This is the question. Everything else today is preparation for it.*

**First, before any changes: was it reversible, and did somebody reverse it?**
If the answer is "we terminated it", the postmortem is not about tuning, it is
about the design. If it isolated and somebody restored the security groups from
the rollback command in the notification, the system worked as designed and did
the wrong thing — which is a much better position to be in.

**Then I look at the trigger, and I expect to find a severity threshold.** That
is the failure almost every time. GuardDuty severity scores impact, not
confidence — a HIGH finding is as likely to be your own penetration test, a
scanner, or a researcher as it is to be a compromise. A threshold cannot tell
them apart, so the automation contained something it had no business
containing.

The changes, in the order I would make them:

1. **Replace the threshold with an allow-list of finding TYPES.** Cryptomining
   and command-and-control DNS are rarely false positives; SSH brute-force
   against anything internet-facing is background noise. Each entry is a
   decision somebody made, reviewed like a deploy.
2. **Go back to dry-run for a week** and read what it would have done. That
   week always changes the list.
3. **Check the rollback was actually usable.** Not "reversible in principle" —
   did the notification contain the previous security groups and the exact
   command? If the person on call had to reconstruct them from memory, that is
   the real finding.
4. **Add the finding type to a suppression or an exception list**, with a
   written reason and an owner, so the same page does not recur.
5. **Check the blast radius.** If it isolated one instance in an auto-scaling
   group, did the group launch a replacement that then tripped the same
   detection? Cascade is the failure mode nobody tests for.

**What I would not do** is disable the automation. That is the instinct after a
bad night and it throws away the ninety-five per cent of cases where it worked.
The kill switch exists so you can stop it *during* the incident and think
afterwards, rather than making the permanent decision at 03:00.

---

## 3. Why is triggering on severity wrong?

Because severity answers a question you are not asking.

GuardDuty severity scores **impact** — how bad this would be if it is real. It
says nothing about how likely it is to be real. A severity-7 finding is
routinely your penetration test, your scanner, a researcher, or a developer on
hotel wifi. Same number as a genuine compromise.

So `severity >= 7` matches all of them, and four of the five are your own
people. Four outages you caused for one correct action, and the four are much
more visible than the one.

**Finding type is what correlates with confidence.**
`CryptoCurrencyMining:EC2/BitcoinTool.B!DNS` is rarely a false positive — there
is not an innocent reason for an instance to resolve a mining pool.
`UnauthorizedAccess:EC2/SSHBruteForce` against an internet-facing host is
constant background noise.

The practical form: an allow-list of types, each one added deliberately with a
written reason, reviewed like a deploy. Severity is still useful — for
prioritising a human's queue, which is what it was designed for.

---

## 4. What must never be automated?

Three categories:

**Anything that touches production data.** Deleting, encrypting or moving it on
a probabilistic signal is not containment.

**Anything that can revoke the responder's own access**, or the access of the
people who would have to fix it. The specific nightmare is automation that
detaches a policy and locks out the team that could reattach it.

**Anything whose failure mode cascades.** Terminating an instance in an
auto-scaling group launches a replacement that may trip the same detection, and
now you have a loop that scales.

The general form is the useful one: **anything a human cannot undo with one
documented command.** Not because destructive actions are never right, but
because they are decisions a human makes on Monday with the finding in front of
them and somebody to ask.

The corollary I would add: automation should be biased toward actions that
**preserve** state. Snapshot before you touch anything. Containment that
destroys the evidence has solved the wrong problem.

---

## 5. What is the difference between logging and evidence?

**Log file validation**, and it is one checkbox.

With it on, CloudTrail writes a signed digest file every hour listing the log
files delivered and their SHA-256 hashes. `aws cloudtrail validate-logs` then
proves no file was modified or deleted since delivery.

Without it you have a bucket full of JSON that anybody with `s3:PutObject`
could have edited, and no way to demonstrate otherwise.

That distinction matters exactly once and then completely: during an incident
where the question is whether an attacker with write access to your trail
bucket removed their own activity. Without validation you cannot answer it.

It is free, and I would pair it with three other things: versioning on the
bucket, so there is a previous copy of an overwritten object; a complete public
access block, because a readable trail bucket is a map of your control plane;
and **running the validation command once, now**, so you know it works before
the day you need it.

---

## 6. How do you build a kill switch, and why not just disable the EventBridge rule?

Disabling the rule is fine — it is just too slow, in the specific sense that
matters.

Changing infrastructure needs a plan, a review and a pipeline. All of that is
correct for a considered decision and useless at 03:00 when the automation is
making things worse and somebody needs it to stop now.

So: an **SSM parameter the responder reads on every invocation**, flipped with
one CLI command. Four properties:

- **Read at runtime, no caching.** Caching saves milliseconds and means a warm
  container keeps acting for minutes after somebody flipped it — during exactly
  the incident where they flipped it.
- **Fail safe.** If the parameter is unreadable, take no action. Automation
  that keeps containing production while its own control plane is broken is
  worse than automation that stops.
- **Not writable by the responder.** The brake must not be reachable by the
  thing it brakes. That is an explicit Deny on the responder's role.
- **Tested.** A kill switch nobody has ever flipped is a hypothesis. I would
  flip it deliberately during onboarding and confirm the responder stops.

You want both switches, for different jobs: the Terraform variable is the
decision, the parameter is the brake.

---

## 7. Design the IAM policy for an automated threat responder.

I would start with what it must **not** be able to do, because that is the more
interesting half and it is where I would spend the review.

An automated responder is a principal that changes your account without a
human. That makes it the most valuable thing in the account to compromise —
more valuable than most human roles, because it acts at machine speed and its
actions look normal in CloudTrail.

Explicit **Denies** for:

- `cloudtrail:StopLogging`, `DeleteTrail`, `UpdateTrail`, `PutEventSelectors` —
  it must not be able to erase the record of what it did.
- `iam:*` — otherwise every other scope is advisory, one line of privilege
  escalation away.
- Writing its own kill-switch parameter.
- `ec2:TerminateInstances`, `iam:DeleteAccessKey`, `secretsmanager:DeleteSecret`
  and friends — the irreversible actions, denied even though nothing grants
  them.

**Denies rather than omissions**, and this is the part I would want to explain:
an omission is one careless policy attachment away from not being an omission,
and an explicit Deny cannot be overridden by any Allow in any policy. It is
also what a reviewer reads to understand what the automation cannot do — "there
is no Allow for it" is a much weaker sentence.

The Allows are then boring: describe instances, modify instance attribute and
create tags scoped to instance ARNs, read one SSM parameter, publish to one
topic, write its own logs. If I cannot describe the Allow set in one sentence,
it is too wide.

---

## 8. `RotationEnabled` is true. Is rotation working?

No — that field only means a schedule exists.

**`LastRotatedDate` is the only field that means it ran.** Absent, or far older
than `AutomaticallyAfterDays` implies, means rotation has been failing silently
since whenever.

The failure mode is worth describing because it is so comfortable: rotation is
configured, the rotation Lambda throws on every invocation, and the console
shows a next-rotation date that keeps moving. Nothing is red. The credential
has not changed since March.

The usual root cause is a rotation function that implements `createSecret` and
`finishSecret` and stubs out `setSecret` — the step that pushes the new value
to the actual service. Every rotation then "succeeds", `LastRotatedDate`
updates, and the credential in the database never changes. **You have built a
scheduled outage that passes its own compliance check**, and it fires the day
somebody fixes the stub.

The check is one command, and I would put it in a scheduled audit:

```bash
aws secretsmanager describe-secret --secret-id <id> \
  --query '{Enabled:RotationEnabled,Last:LastRotatedDate,Rules:RotationRules}'
```

---

## 9. Walk me through the four-step rotation protocol.

Secrets Manager invokes the rotation function four times per rotation with a
different `Step`. The whole design exists so that a rotation which fails
halfway leaves a working credential behind.

**createSecret** — generate the new value and store it labelled `AWSPENDING`.
`AWSCURRENT` is untouched and applications keep working. **Must be idempotent**,
because this step is retried, and generating a fresh password on each retry
means the value you tested is not the value you finish with.

**setSecret** — push `AWSPENDING` to the actual service. `ALTER USER`, the
provider's API, whatever the credential is for. This is the real work and the
step people stub.

**testSecret** — connect using `AWSPENDING`. If it raises, rotation stops and
`AWSCURRENT` is never moved, so the old credential still works and nobody is
paged. That is the entire reason there are four steps instead of one.

**finishSecret** — move the `AWSCURRENT` label to the pending version.
Atomically: `update_secret_version_stage` with both `MoveToVersionId` and
`RemoveFromVersionId` in one call. Two calls leaves a window with no
`AWSCURRENT` at all, and every application fetching during that window fails —
short, intermittent, and one of the harder bugs to reproduce.

`AWSPREVIOUS` is applied automatically to the old version, which gives you
exactly one generation of rollback.

---

## 10. How would you enable Security Hub in a new account?

With **one** standard.

Enabling every available standard on day one produces several thousand failed
controls across sets that overlap heavily — the same "block public access"
control under three names — and a compliance percentage nobody believes and
nobody will ever drive to zero. The team learns to scroll past the dashboard,
which is a worse outcome than having no dashboard, because it looks like
coverage.

So: `aws-foundational-security-best-practices`, because it is the broadest and
most directly actionable. Work the failures down over a few weeks. Suppress the
controls that genuinely do not apply **with a written reason and an owner** —
an unexplained suppression is indistinguishable from an oversight six months
later. Only then consider CIS or PCI, and only when somebody needs the
attestation.

Two mechanics: turn on consolidated control findings, so adding a second
standard later does not triple your finding count for the same problems; and
know that the GuardDuty integration is automatic when both are enabled in a
region — there is no resource wiring them together, which is why people go
looking for one.

On cost: checks are priced per control **per resource per day**, so the number
scales with your resource count, not with how many standards sound useful.

---

## 11. What is the cheapest way to make CloudTrail expensive?

Turn on data events account-wide.

Management events are control-plane calls at human-and-automation volume, and
the first trail carrying them is free. Data events are object-level —
every `GetObject`, every `PutObject` — generated at **application** volume, with
**no free allowance**, at about $0.10 per 100,000.

A bucket serving a few hundred reads a second produces roughly 26 million
events a day. That is ~$26/day, ~$780/month, for one bucket. `arn:aws:s3:::*/*`
in a selector is one character longer than the scoped version and can be a
five-figure difference.

They are worth having in the right place: data events are how you answer "which
objects did the compromised role actually read", which is the question that
decides whether you have a breach-notification obligation. So enable them on
the buckets whose contents you would have to notify about, with an explicit
selector, and nowhere else.

The same shape applies to GuardDuty S3 protection, priced per million data
events analysed. "Turn it on everywhere to be safe" is how a security budget
doubles in a month.

---

## 12. GuardDuty is enabled. Are you covered?

Only in that region — and that is the question behind the question.

GuardDuty and Security Hub are **regional services with account-level state**.
Enabling them in `us-east-1` does nothing for the other twenty-odd regions, and
an attacker with credentials does not politely operate in your primary region.
Creating an instance in `ap-south-1` is exactly as easy for them.

So the answer is: enable it in every region, including the ones you do not use,
precisely because those are the ones nobody watches. Use an organisation
delegated administrator with auto-enable for new accounts and new regions, so
it does not depend on somebody remembering.

Then the counter-trap, which is the cost side of the same fact: somebody
enables everything everywhere during a compliance push and the account keeps
paying for detection in fifteen regions that have never held a resource. Both
directions are real. The reconciliation is to enable everywhere and then
actually look at the bill after the free trial ends.

The other half of "are you covered": findings that reach nobody are not
coverage. A backlog of untriaged findings is operationally indistinguishable
from having no detection, and more expensive.

---

## 13. Your audit tool passed last month and fails this month. Nothing changed. How?

Because some checks read **runtime state**, not configuration, and time is part
of the input.

The clearest case is access key age. The key existed last month and exists now.
Nobody deployed, nobody touched the console, there is no drift. It crossed
ninety days. Same account, same configuration, different answer.

Untriaged findings behave the same way — a finding raised last week and left
alone becomes a finding about the backlog — and so does rotation history, where
a secret that has not rotated becomes overdue by doing nothing.

Two consequences I would draw:

**Run the auditor on a schedule, not only at merge time.** A merge-time-only
audit certifies the account as it was on the day somebody last changed it, and
a point-in-time pass is not a property that persists.

**Name the time-dependent checks in the output.** Our JSON emits a
`runtime_dependent_checks` list, because somebody diffing two runs needs to know
which differences mean "somebody changed something" and which mean "a week
passed". Without that, the first false alarm teaches people to ignore the diff.

---

## 14. Two audit runs both report eleven findings. Same account?

Not necessarily, and this is why I would never trend the count alone.

On our Day 07 stack, immediately after apply there are eleven findings. After
generating some detections and forcing one rotation there are still eleven —
and it is a different set. One check cleared, because rotation actually ran.
Another appeared, because there are now findings sitting untriaged. Same total,
six points apart, different problem.

A dashboard that shows "11" and a trend line is worse than no dashboard,
because it reports stability that is not there.

What I would build instead: diff on the **set** of check IDs and resource IDs,
report added and removed separately, and treat a change in composition as an
event even when the total is flat. And I would exclude the time-dependent
checks from the "somebody changed something" alert, or the first Monday after a
key turns ninety days old generates a false alarm that trains people to ignore
it.

---

## 15. When would you use Step Functions instead of a Lambda for automated response?

The moment the response has more than one step.

A single decision and a single action is a Lambda, and a one-state state
machine is ceremony that obscures what is happening.

But as soon as it is "snapshot the volume, wait for the snapshot to complete,
then isolate, then notify" — switch. And the honest reason is not elegance: a
multi-step response implemented as one Lambda **has no story for what happens
when step two fails after step one succeeded.** You are left with a snapshot in
progress, an uncontained instance and a function that already returned.

What Step Functions buys, specifically:

- An **explicit state machine** with per-state retry and catch, so partial
  failure is a designed path rather than an exception.
- An **execution history** you can hand to an auditor. "Show me what happened
  in response to this finding" is a screenshot rather than a log-mining
  exercise, and that matters more in security than almost anywhere else.
- A **wait-for-human-approval** state. That is the pattern I would reach for
  when the action is destructive and somebody has decided it is worth
  automating anyway: the machine does the reversible parts immediately and
  blocks on the irreversible one until a human clicks.

The last one is the real answer to "what if we genuinely need terminate". Not
an environment variable — a state.

---

## Rapid fire

| Question | Answer |
|---|---|
| GuardDuty severity range? | 1.0–8.9. Low <4, Medium <7, High 7+. |
| Does severity mean confidence? | **No.** Impact if real. |
| What correlates with confidence? | The finding **type**. |
| GuardDuty free trial? | 30 days per account per region. |
| GuardDuty pricing shape? | Per GB / per million events **analysed**. |
| Is GuardDuty regional or global? | Regional, with account-level state. Enable everywhere. |
| Security Hub pricing shape? | Per security check — per control per resource per day. |
| How many standards on day one? | **One.** |
| Why not all of them? | Overlapping controls, a number nobody drives to zero, a muted dashboard. |
| GuardDuty → Security Hub wiring? | Automatic when both are on in a region. No resource. |
| Free CloudTrail trails? | The first one delivering management events. |
| Second trail cost? | ~$2.00 per 100,000 management events. |
| Data event cost? | ~$0.10 per 100,000, **no free allowance**. |
| What makes a trail evidence? | Log file validation — hourly signed digests. |
| Command to prove it? | `aws cloudtrail validate-logs --trail-arn ... --start-time ...` |
| Why multi-region? | An attacker does not politely stay in your primary region. |
| Why global service events? | IAM and STS emit in us-east-1 regardless of where you are. |
| Why version the trail bucket? | Validation says a file changed; versioning shows what it said. |
| Why `aws:SourceArn` in the bucket policy? | Confused deputy — any account's trail could otherwise write to it. |
| Secrets Manager price? | ~$0.40 per secret per month. |
| Which field proves rotation ran? | `LastRotatedDate`. Not `RotationEnabled`. |
| The four rotation steps? | createSecret, setSecret, testSecret, finishSecret. |
| Which step do people stub? | `setSecret` — and every rotation then "succeeds". |
| Why must finishSecret be one call? | Two leaves a window with no `AWSCURRENT`. |
| Does rotation fix a leaked old value? | No. Check your log groups. |
| Reversible containment for an instance? | Swap security groups for a quarantine group; record the old ones. |
| Reversible containment for a key? | Attach a deny policy. Do not delete. |
| What makes containment reversible in practice? | Recording the previous state **before** changing it. |
| Where should the kill switch live? | Runtime — SSM parameter, read every invocation, no cache. |
| Fail open or fail safe? | Fail **safe**. Unreadable switch means take no action. |
| Can the responder write the kill switch? | No. Explicit Deny. |
| Why Denies not omissions? | A Deny cannot be overridden by any Allow, ever. |
| Filter findings at the broker or in code? | In code — so "nothing happened" has a reason. |
| Default containment mode? | `dry-run`. For a week. |
| Sample finding tell? | `[SAMPLE]` title prefix and fake resource ids. |
| Which checks depend on when you ran them? | Untriaged findings, rotation history, key age. |
| Same finding count, same account? | **No.** Diff the set, not the total. |

---

## What to actually say in an interview

**Lead with the failure, not the service list.** Everyone can name GuardDuty.
Very few can describe what happens when it is wrong, and that is the whole job.

**Have the severity sentence ready.** *"GuardDuty severity is impact, not
confidence — a HIGH finding is as likely to be our own pen test as a
compromise, so we allow-list on finding type, not on a threshold."* That one
sentence signals that you have operated this rather than configured it.

**Say what you refuse to automate, and why.** Candidates who describe
increasingly elaborate auto-remediation sound junior. Candidates who say *"we
isolate, we never terminate, because a Lambda at 3am on a probabilistic signal
should not be making decisions a human cannot undo with one command"* sound
like they have been on call.
