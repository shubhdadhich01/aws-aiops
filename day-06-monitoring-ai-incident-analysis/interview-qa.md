# Day 06 — Interview & Career Guidance

**CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp**

Fifteen questions with full answers, then a rapid-fire table.

Day 06 is the day your answers start sounding different from everyone else's,
because most candidates asked about AI in operations describe a demo. The
questions below are the ones that separate someone who has shipped this from
someone who has watched a conference talk about it — and four of them are
specifically about the **limits** of LLM-generated incident analysis, because
that is what a good interviewer is actually probing for.

---

## 1. Walk me through what happens between a log line being written and a human being paged.

**The chain, and where each link fails.**

A log event arrives in a log group. A **metric filter** matches it and
publishes a datapoint into a CloudWatch metric — the filter itself is free, the
metric it publishes is $0.30/month and permanent. An **alarm** evaluates that
metric on a schedule, applying `datapoints_to_alarm` out of
`evaluation_periods`. When it transitions to ALARM it fires its actions,
typically an SNS publish, and a human gets mail.

Four places that chain breaks silently, which is really what the question is
asking:

1. **The metric filter stops matching.** A deploy renames a JSON field. The
   filter matches nothing. No datapoints.
2. **The alarm goes to `INSUFFICIENT_DATA`** because `treat_missing_data` is at
   its default. It is not red. It is grey, and grey reads as fine.
3. **The alarm has no action**, because someone built it during an incident and
   meant to wire the topic up afterwards.
4. **The SNS subscription was never confirmed.** Every publish succeeds, every
   message is discarded, the alarm history says the action was delivered.

Only the third of those is visible in a `terraform plan`. That is why the
day builds an auditor that reads AWS rather than files.

---

## 2. `treat_missing_data` has four values. Talk me through them and tell me which you would use for a checkout service.

`missing` is the default: the period is ignored and CloudWatch looks further
back. If it never finds real data the alarm sits in `INSUFFICIENT_DATA`
permanently. `notBreaching` treats absence as health. `breaching` treats
absence as the fault. `ignore` holds the last state and never transitions on
missing data.

For a checkout service I would use **at least two alarms with different
answers**:

- The **error-rate** alarm: `notBreaching`. Combined with `FILL(x, 0)` on the
  inputs, a genuinely absent period means no traffic, and no traffic is not an
  error-rate breach.
- A **liveness** alarm: `breaching`. Sum of request count, `LessThanThreshold
  1`, over a longer window than the others. Missing data *is* the breach.

That second one is the alarm most stacks do not have, and it is the only thing
that catches the service that crashed on boot, the broken log driver, or the
IAM change that revoked `logs:PutLogEvents`. Every other alarm asks whether the
data is bad; that one asks whether there is any data.

The practical warning: set its threshold from your genuinely quietest period. A
dead-man's switch that flaps every Sunday at 04:00 gets muted, and a muted
dead-man's switch is worse than none because it looks like coverage.

---

## 3. Why would you alarm on a rate rather than a count?

Because a count means different things at different times of day, and the
direction it fails in is the dangerous one.

"More than 50 errors in five minutes" fires when traffic doubles and nothing is
wrong. Worse, when the load balancer goes unhealthy and traffic collapses,
errors fall below 50 and **the alarm goes quiet during the outage**.

5% of requests failing is 5% at every hour, and it means the same thing to the
person reading the page as it did to the person who set it.

In CloudWatch you build it with metric math over two metric filters — an error
count and a request count — with an expression like
`IF(r > 0, 100 * e / r, 0)`. Two details matter: `FILL(x, 0)` so gaps become
explicit zeros, and the `IF` so a zero-traffic period does not divide by zero
and quietly fall back to `treat_missing_data`. Otherwise the behaviour of your
alarm during quiet periods is decided by a setting three lines away instead of
by the expression you wrote.

The exception: a liveness alarm is legitimately a raw count. "Fewer than N
events happened" is the entire idea.

---

## 4. What is a composite alarm for, and how do you know one works?

**For:** one page per incident instead of three. The child alarms are
diagnostic — they exist so that when you are woken you can see which signal
tripped — and the composite is the only thing with a notification action.
Without it a cascade sends error-rate mail, then latency mail, then
no-telemetry mail, and somebody writes an inbox rule.

**How you know it works:** you force it.

```bash
aws cloudwatch set-alarm-state --alarm-name <a-child> \
  --state-value ALARM --state-reason "deliberate test"
```

and watch the parent transition within seconds.

That is not belt-and-braces. The composite alarm rule language accepts
logically impossible rules. `ALARM(x) AND OK(x)` is created, billed at
$0.50/month, displays a reassuring green, and cannot transition under any
circumstances. The realistic version is subtler — an `AND` across two
conditions that never co-occur, written by someone reducing noise. There is no
validation, no plan output and no review that catches it. Forcing it takes
ninety seconds.

---

## 5. Someone adds `dimensions = { RequestId = "$.request_id" }` to a metric filter in a pull request. What do you say?

I block it, and I explain why the mistake is *permanent* rather than just
expensive.

Every distinct combination of namespace, metric name and dimension values is
one custom metric at $0.30/month. A request ID is unbounded, so a moderately
busy service creates tens of thousands of metrics in an afternoon — forty
thousand is $12,000/month.

And there is **no `DeleteMetric` API**. No console button, no support ticket. A
custom metric ages out fifteen months after its last datapoint. So the bill
outlives the fix by more than a year.

The rule I would give the team: a dimension value must come from a set you
could write on a napkin — status codes, error types, environments, regions.
Everything else stays in the log line, where Logs Insights queries it for
$0.005/GB scanned and nothing accumulates.

I would also add it to CI, because this is exactly the kind of change that
looks helpful in review. The person writing it is trying to be thorough.

---

## 6. Where does an LLM genuinely help in incident response, and where does it not?

It helps with exactly one question: **what happened here, and where should I
look first?**

That question is hard for a human at 03:00 — four hundred lines of prose, a
thread to find, and adrenaline. It is the one place the model earns its cost.

It does not help with anything countable. "How many 5xx did we serve" is
answered exactly, instantly and free by a metric filter, and the answer will
still be right next year. "When did it start" is a Logs Insights query with
`bin(1m)` that costs half a cent. "Is it still happening" is literally what an
alarm is. Using a model for those is slower, more expensive, and less reliable,
and it trains the team to distrust the tool when it gets one wrong.

The line I would draw for a team: **AI enters where the question is "what
happened", not "how many".**

And a hard boundary — never wire the output into automated remediation. The
model does not know your topology or your blast radius. An LLM's root-cause
guess driving a rollback is how you restart the wrong service, confidently and
with citations.

---

## 7. Your incident summary was confidently wrong during an incident. What do you change?

*This is the question. Everything else on this day is preparation for it.*

**First, I find out what it actually saw**, which means invocation logging had
better already be on. The prompt is gone the moment the Lambda returns and the
log window has moved on, so without a recorded prompt there is no
investigation, only speculation. If it was not on, that is finding number one
and it goes in the postmortem.

**Then I look at the sampling before I look at the model**, because in my
experience that is where the fault usually is. The classic implementation is
`events[-200:]`, and a cascade begins with one cause and ends with a thousand
consequences. Tail-only truncation feeds the model a thousand symptoms and zero
causes, and it does exactly what it was asked: it explains the symptoms.
Fluently. Confidently. And **nothing in its output suggests the answer is
missing**, because it cannot report a gap it was never told about.

The fixes, in the order I would make them:

1. **Sample head + stratified middle + tail**, never tail-only, and keep the
   original line indices.
2. **Make the tool state its coverage** — "this summary is based on 180 of
   4,300 lines" — so a reader can weigh it.
3. **Require citations and check them in code.** Every claim carries a line
   index and a verbatim fragment; a loop resolves each index and confirms the
   fragment is really there. Unverified claims get printed as UNVERIFIED, not
   dropped. A model cannot fake a quote that survives an `in` check.
4. **Give it permission to say nothing.** Add `insufficient_evidence` to the
   schema and tell it, in the prompt, that returning it is a correct answer.
   Asked "what caused this", a model will always produce a cause, because that
   is what it was asked for.
5. **Put the deterministic facts above the narrative** in the output. A reader
   who meets the numbers first reads the prose as a hypothesis about them.

**What I would not do** is change the model or add "be accurate" to the prompt.
The failure was an information failure, not a capability one. A bigger model
given the same truncated window produces the same wrong answer, more
persuasively.

---

## 8. How do you stop an AI-powered analyser becoming a surprise bill?

Three guards, and they are not interchangeable.

**M-of-N on the triggering alarm.** This is the one that matters, because it
stops the transition happening at all. An alarm with
`datapoints_to_alarm = 1` on a noisy metric transitions dozens of times an
hour. Forty an hour for twelve hours, at $0.16 per invocation, is $77 by
breakfast — on a system that is not broken, with no dashboard red, because the
alarm is doing exactly what you asked.

**A hard token budget.** Not because the context window is small — because
cost scales linearly with input, latency scales with input, and recall
genuinely degrades over very long context. Sending everything makes the answer
*worse* as well as slower. And the budget makes the tool honest: it reports how
much it dropped.

**An idempotency window**, so one incident is summarised once rather than once
per flap. A conditional write with a TTL against DynamoDB is enough.

I would also point out the structural difference from every other AWS cost:
**this one leaves nothing behind to delete.** There is no resource, so it never
appears in a teardown sweep and never shows up in a resource-count estimate.
That is why it needs a budget and an anomaly alert rather than a checklist.

---

## 9. What must never go into a prompt, and how do you enforce it?

The honest answer is that you cannot enumerate it, and that is the point.

A production log line contains, routinely and without anyone intending it,
bearer tokens, session cookies, connection strings, email addresses, full
request bodies and stack traces with local variables still in them. **Nobody
put them there deliberately** — which is exactly why you cannot decide not to
send data you do not know you are logging.

So I do three things and I am honest about what each one is worth:

**Redact before the prompt.** Regexes for the high-confidence shapes: AWS
access key IDs, JWTs, `token=`/`password=` assignments, credentials in URLs,
emails, long digit runs. This is the seatbelt. It will not catch a customer's
name in a free-text field or a session ID your framework invented, and I would
say so out loud rather than let anyone think it is a control.

**Fix it upstream.** The real answer is not logging the secret. Redaction is
what you do while that work is happening.

**Write down the data flow.** Three questions, answered explicitly: where does
it go — which region, and does an inference profile route it elsewhere? Who can
read it afterwards — because turning on invocation logging puts full prompts in
a log group readable by everyone with CloudWatch access? And what is the
function permitted to invoke — because `bedrock:InvokeModel` on `Resource: "*"`
is a blank cheque against every model in the account.

That last one usually happens by accident: the correctly scoped foundation-model
ARN has an **empty account field**, people write their account ID in, it matches
nothing, they get `AccessDeniedException` with no explanation, and after twenty
minutes `"*"` makes it work.

---

## 10. How would you convince a sceptical SRE team that an LLM summariser is worth having?

I would not lead with the summariser.

I would show them the **deterministic block first** — counts, error rate, p50/
p95/p99, first error with its line index, and every deploy or config change in
the window — and point out that it is plain Python, no model, and that it
answers the incident on its own maybe two times in three.

Then I would show the narrative as a *hypothesis about those numbers*, with
every claim carrying a line index and a verbatim quote, and the verification
status printed next to it. And I would run the demo where the same pipeline
produces a confident, wrong answer from a truncated window, so nobody has to
take my word for the failure mode.

A sceptical SRE team is right to be sceptical, and the way to lose them is to
oversell. What convinces them is: **it degrades to something correct.** When
the model call fails — throttled, region down, access revoked — the numbers
still ship. The narrative is additive, it is checkable, and it can be switched
off at its own SNS topic without touching the page.

If they still say no, that is a reasonable answer. The deterministic half is
90% of the value and it costs nothing.

---

## 11. A dashboard shows a flat line for a metric. What are the possibilities?

Four, and only one of them is good news:

1. **Nothing is happening**, and the metric is genuinely zero.
2. **Nothing is being published.** The metric filter stopped matching — a field
   rename in a deploy is enough. The widget renders happily; there is nothing
   to distinguish "no data" from "zero".
3. **The metric never existed.** CloudWatch dashboards do not validate metric
   references. A widget naming a namespace and metric nobody publishes renders
   an empty graph with a legend. This survives refactors indefinitely.
4. **The widget has no `region`**, so it is rendering against whatever region
   the console happens to be showing.

The reason this question is worth asking is that all four look identical, and
the reassuring interpretation is the one people reach for during an incident.

Mechanically, (3) is detectable: enumerate what the widgets reference, compare
against `ListMetrics`, report the difference. It belongs in CI. (2) is caught
by a dead-man's switch on the underlying metric.

---

## 12. Logs Insights or `filter_log_events`?

Insights when a human is asking; `filter_log_events` when code is.

Logs Insights supports `stats`, `parse` and `bin`, which is what makes it the
right tool during an incident — `stats count(*) by bin(1m)` tells you when
something started, and that is usually the first thing you need. It costs
$0.005 per GB **scanned**, so the time range drives the bill, not the
cleverness of the query. Narrow the window first.

`filter_log_events` has no aggregation but it is free, synchronous and
paginated. Inside a Lambda that matters: Insights is asynchronous, so you start
a query and poll, and you are paying Lambda duration for every second of the
wait. The analyser in this lab uses `filter_log_events` for exactly that
reason.

The five Insights queries I would want anyone on call to have muscle memory
for: error count by type; error count by `bin(1m)` to find the start; a filter
for deploy and config-change events; a `stats by endpoint, status` to see
whether the failure is concentrated; and percentiles by bin.

If your logs contain no deploy markers, adding them is the highest-value
observability change available this quarter. It turns "what changed" from an
archaeology project into a one-line query.

---

## 13. What is the difference between a check that is silent because nothing is wrong and a check that is silent because it cannot fire?

This is a question about how much trust a green result deserves, and the answer
is: it depends which kind of green.

**Silent by design** means the system structurally cannot produce the fault.
In this lab, the cross-region check is silent because one Terraform variable
feeds both the log region and the model ARN, so they cannot diverge without
somebody editing it on purpose. That silence tells you something about the
*auditor* — it does not cry wolf — and it is evidence.

**Silent by situation** means it happens not to fire today. The liveness check
is silent only because one alarm is currently configured correctly. Nothing
prevents it firing; one attribute changed in the console, with no code review
and no plan, and it comes straight back.

Reading the second as the first is how a team concludes it has coverage it does
not have. Practically: a check that is silent by situation must be **re-run**,
not assumed, which is an argument for running the auditor on a schedule rather
than at merge time only.

I would also say that a check set where everything fires teaches you that
findings are normal. A check set with two deliberate zeroes teaches you that a
quiet check is evidence — and that is the more useful lesson to build a culture
on.

---

## 14. Design a monitoring stack for a new service. Where do you start?

Not with dashboards, which is where most people start.

**One:** log groups in code, with retention, created before the thing that
writes to them. This is thirty seconds of work that otherwise becomes a
permanent line on the bill.

**Two:** the four signals — traffic, errors, latency, saturation — as metrics,
extracted with metric filters from log lines the service is already writing.
Dimensions bounded to sets you could write on a napkin.

**Three:** three alarms. An error **rate** with M-of-N. A latency **percentile**,
not an average. And a **liveness** alarm with `treat_missing_data = breaching`,
because the other two are blind to a service that went dark.

**Four:** one composite alarm over those three, and it is the only thing with a
notification action. Then force a child into ALARM and confirm the composite
follows, because a composite you have not tested is decoration.

**Five:** deploy markers in the logs, so "what changed" is queryable.

**Six** — and only now — a dashboard, built to answer three named questions in
under thirty seconds on a phone, with a log widget so it ends at the evidence
rather than sending you back to the console.

AI comes after all of that, if at all. It answers one question the stack above
cannot, and it needs the stack above to be worth trusting first.

---

## 15. How do you decide whether a model's summary should be trusted in a given incident?

By reading its coverage and its grounding before reading its prose, which is
why both are printed above the prose.

**Coverage:** how many lines existed, how many were sent, and by what strategy.
A summary built from 180 of 4,300 lines is a hypothesis about 4% of the
evidence, and it should be read that way. If the strategy says "tail-only", I
would not trust a causal claim from it at all.

**Grounding:** how many of its claims were verified. Every claim carries a log
line index and a verbatim fragment, and code checks the fragment really appears
at that line. Three of four verified is a different document from four of four,
and one unverified claim usually means the interesting one is the invented one.

**Whether it declined.** A summary that returns `insufficient_evidence` is
doing its job. If a tool never returns it, that is a red flag about the tool,
not a compliment.

Then the check that costs thirty seconds and settles it: **open one cited line
and look at it.** The citations exist to be followed. A summary nobody ever
follows a citation from is a summary nobody is really checking, and at that
point you have rebuilt the thing this whole design was trying to avoid.

---

## Rapid fire

| Question | Answer |
|---|---|
| Default log group retention? | Never expire. Nobody chose it. |
| Cost of a metric filter? | Free at any volume. The metric it publishes is $0.30/month. |
| Can you delete a custom metric? | No. It ages out 15 months after its last datapoint. |
| Standard vs high-resolution alarm? | $0.10 vs $0.30 per month; only standard is in the free ten. |
| Composite alarm price? | $0.50/month, not covered by the free ten. |
| Free dashboards? | Three. $3.00/month each after. |
| Log ingestion / storage price? | $0.50/GB in, $0.03/GB-month stored. |
| Logs Insights price? | $0.005 per GB scanned. Range drives the bill. |
| `INFREQUENT_ACCESS` log class trade? | Half price; loses metric filters, subscription filters and Live Tail. Cannot be changed after creation. |
| Which `treat_missing_data` is the default? | `missing` — and it is the one most likely to hide an outage. |
| Which one builds a dead-man's switch? | `breaching`. |
| M of N meaning? | M breaching datapoints out of the last N evaluation periods. |
| Why not just average over N periods? | Averaging hides the shape; three catastrophic minutes average away. |
| Why does `extended_statistic` return nothing? | The metric filter publishes `value = "1"`, so there is no statistic set. |
| `default_value` and `dimensions` together? | Not allowed by AWS. Dimensioned filters genuinely have gaps. |
| Foundation model ARN account field? | **Empty.** The model is not yours. |
| Inference profile ARN account field? | Present — and you need both ARNs in the policy. |
| Which IAM action does Converse need? | `bedrock:InvokeModel`. There is no `bedrock:Converse`. |
| Rough Haiku 3.5 input price? | ~$0.0008 per 1,000 tokens. Verify — prices move. |
| Why cap prompt size if the context window is huge? | Cost, latency, and recall degradation over long context. |
| Best sampling strategy for a cascade? | Head + stratified middle + tail. Never tail-only. |
| Why keep original line indices in the sample? | So citations resolve to real lines and can be checked. |
| How do you catch a fabricated citation? | Resolve the index, check the quoted fragment is really in that line. |
| What should an analyser do when the model call fails? | Ship the deterministic facts anyway. They were never in doubt. |
| Where should the AI summary NOT go? | On the dashboard, and into automated remediation. |
| Why a separate SNS topic for summaries? | Different data sensitivity, mute policy and reliability bar. |
| Bedrock invocation logging default here? | Off — enabling it creates a data-access problem you must also solve. |
| What survives `terraform destroy` on this day? | Log groups Terraform did not create, and custom metrics. |
| Anything that cannot be deleted at all? | Custom metrics. 15 months. |

---

## What to actually say in an interview

Three things make a Day 06 answer sound like experience rather than reading:

**Name the silent failure.** Anyone can describe an alarm. Describing the alarm
that has been in `INSUFFICIENT_DATA` since March, and why nobody noticed, is
the thing that lands.

**Give the cost with its shape.** Not "logs can get expensive" but "$0.50/GB
in, $0.03/GB-month forever, and a 1 GB/day service with no retention is
$110/month in storage after a year and still climbing".

**Be the person who is sceptical about the AI in the right way.** Not "LLMs
hallucinate" — everyone says that and it ends the conversation. Say: *"the
failure I have actually seen is tail-only truncation. The model gets a thousand
consequences and zero causes and explains the consequences perfectly, and there
is nothing in the output to say the cause was missing. So I make it cite line
indices and I check the citations in code."*

That last one is a real engineering answer to a real failure mode, and very few
candidates have one.
