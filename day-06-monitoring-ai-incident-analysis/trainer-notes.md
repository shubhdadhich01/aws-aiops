# Day 06 — Trainer Notes

**CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp**

Internal. Minute-by-minute timing, the two live demos, and the places this day
goes wrong in a room.

**Total taught: 3h 45m** including two breaks.

---

## Before the room

### The night before

- [ ] **Request Bedrock model access in your demo account.** Approval is
      usually instant but it is not guaranteed, and an un-requested model
      returns `AccessDeniedException` with a message that does not say "press
      the button". This is the single most common way this day starts badly.
- [ ] Run `terraform apply` yourself end to end. Confirm the SNS subscription.
- [ ] Run the chaos function once in `cascade` mode and once in `normal` mode
      so the dashboard has a "before" and an "after" by the time the room sees
      it. **Cold graphs kill Part 1.**
- [ ] Run **both** analysers on the same incident and save the two summaries to
      a file. Demo 2 depends on the naive one producing a wrong answer, and
      while it reliably does, you want to have seen today's actual wording
      before you stand in front of it.
- [ ] `python3 -m unittest discover -s tests` → 47/47.
- [ ] Have `findings.json` from a real run open in a second window.

### Tell the room in the first two minutes

> Everything before the break is the part with no AI in it. That is
> deliberate. If we only had ninety minutes today, we would spend all ninety
> there — and the reason is that most of what people want AI for in operations
> is already answered exactly, instantly and for free by a metric filter.

Setting that expectation early stops the "when do we get to the AI bit"
undercurrent, and it makes Part 7 land harder when it arrives.

---

## Timing

| Time | Block | Notes |
|---|---|---|
| 0:00 | **Scenario and the day's argument** | 10m |
| 0:10 | **Part 1–2** — the measurement model, log groups, retention | 25m |
| 0:35 | **Lab Steps 0–2** — apply, baseline, break it | 20m |
| 0:55 | **Part 3** — metric filters, EMF, cardinality | 20m |
| 1:15 | **Break** | 10m |
| 1:25 | **Part 4** — alarms, `treat_missing_data`, rate vs count, composites | 30m |
| 1:55 | **Lab Steps 3–5** — read the logs, watch alarms, prove the composite | 25m |
| 2:20 | **Part 5–6** — Logs Insights, dashboards | 15m |
| 2:35 | **Break** | 10m |
| 2:45 | **Part 7** — the AI section | 30m |
| 3:15 | **DEMO 1 and DEMO 2** | 20m |
| 3:35 | **Part 9–10** — cost, the auditor, the contract | 10m |
| 3:45 | End |

Self-paced learners: about 2h 40m, mostly because Steps 3 and 6 are where
people spend real thinking time.

### If you are running short

Cut in this order:

1. **Part 5's five queries** — point at the README. The queries are useful but
   they are reference material.
2. **Part 6's dashboard section** — the argument survives one sentence: *"a
   dashboard nobody opens is a screensaver with a bill."*
3. **Part 3's EMF comparison** — the table in the README carries it.

**Never cut:** `treat_missing_data`, the dead-man's switch, the composite alarm
proof, and Demo 2. Those four are the day.

---

## Block-by-block notes

### 0:00 — Scenario and argument (10m)

Read the scenario, then put the argument on the screen and leave it there:

> **A summary you cannot check is worse than no summary.**

Do not explain it yet. Say you will come back to it at 2:45 and that everything
before then is why it is true.

### 0:10 — Parts 1–2 (25m)

The retention arithmetic is the hook. Do it live rather than showing the table:

> "One gigabyte a day. Ingestion is fifty cents a gig, so fifteen dollars a
> month. Storage is three cents a gig-month — but it accumulates. After a year
> you are storing 365 gigabytes, so eleven dollars a month, and next year it is
> twenty-two, and nobody has looked at any of it since Thursday."

Then ask the room to run this against their own accounts:

```bash
aws logs describe-log-groups --profile bootcamp --region us-east-1 \
  --query 'logGroups[?!not_null(retentionInDays)].{Name:logGroupName,Bytes:storedBytes}' \
  --output table
```

**Expect a reaction.** In a room of ten people, several will find log groups
from labs they did in a previous course. That moment does more than the table.

### 0:35 — Lab Steps 0–2 (20m)

Walk the room through `apply` together. Two things to call out while it runs:

- The **SNS confirmation email** — get everyone to click it *now*, and say
  plainly that an unconfirmed subscription means every publish succeeds and
  every message is discarded, with no error anywhere.
- **`depends_on` on the log group.** Ask why Terraform cannot infer it. The
  answer — the function does not reference the group — is a nice small
  reminder of Day 05's dependency-graph material.

### 0:55 — Part 3 (20m)

Spend the time on **cardinality**, not on the EMF comparison.

Put `dimensions = { RequestId = "$.request_id" }` on the screen and ask the
room whether they would approve it in review. Most will. Then walk the
arithmetic: 40,000 requests, 40,000 metrics, $12,000/month, **no delete API**,
fifteen months.

The sentence that makes it stick:

> "The bill outlives the fix by more than a year."

### 1:25 — Part 4 (30m)

The densest block. Order matters:

1. **`treat_missing_data`** — all four values, then the story: a deploy renames
   a field, the filter stops matching, the alarm goes grey and stays grey.
   Ask: *"how would you find out?"* The honest answer is you would not, which
   is the setup for (3).
2. **Rate vs count** — the two directions it fails in. Emphasise the second:
   the alarm goes quiet during the outage.
3. **The dead-man's switch** — deliberately after (1), so the room has already
   felt the problem it solves. This usually gets a visible reaction, because
   most people have never built one.
4. **Composites and the impossible rule** — set up Demo/Step 5.

### 1:55 — Lab Steps 3–5 (25m)

**Step 3 is the pedagogically important one and it is the one people skip.**

Make everyone write a sentence on paper — actual paper, or a chat message they
send to themselves — saying what they think happened, *before* anything
summarises it for them. Give them a hard eight minutes.

Then ask for hands: *"who found the deploy line?"*

In a typical room, about half do. Those who sorted ascending find it in under a
minute; those who sorted descending do not find it at all. That split is the
demonstration, and it is exactly the split you are about to reproduce with the
two analysers at 3:15. **Say so now**, so the callback lands.

Step 5, the composite proof, is quick and worth doing together:

```bash
aws cloudwatch set-alarm-state --alarm-name cbc-day06-error-rate-XXXX \
  --state-value ALARM --state-reason "deliberate test" \
  --profile bootcamp --region us-east-1
```

Then have someone read the impossible composite's rule aloud and work out why
nothing will ever move it.

### 2:45 — Part 7 (30m)

Come back to the argument on the screen. Structure:

- What the model is for (one question), and the table of what it is not for.
- **Deterministic first** — show the notification with numbers above prose, and
  say why the ordering changes what the reader does with the prose.
- **Sampling** — this is the setup for Demo 2. Do not resolve it yet; let them
  see it fail.
- Token budget: cost, latency, and the one people do not expect — recall
  degrades, so sending everything makes the answer *worse*.
- Citations, and the thirty-line verification loop.
- What never goes in a prompt.

Keep the last item short here. It gets a better hearing after the demos.

---

## DEMO 1 — the summary being right and useful (8 minutes)

**Goal:** establish that this genuinely works, so Demo 2 is a surprise rather
than a foregone conclusion. If the room already thinks it is useless, Demo 2
teaches nothing.

**Setup:** run the cascade a few minutes before, so the window is warm.

```bash
aws lambda invoke \
  --function-name cbc-day06-chaos-XXXX \
  --payload '{"mode":"cascade","lines":900}' \
  --cli-binary-format raw-in-base64-out \
  --profile bootcamp --region us-east-1 /tmp/incident.json
```

**Run the good analyser:**

```bash
aws lambda invoke \
  --function-name cbc-day06-analyser-XXXX \
  --payload '{"alarmName":"demo","lookback_minutes":30}' \
  --cli-binary-format raw-in-base64-out \
  --profile bootcamp --region us-east-1 /tmp/good.json

python3 -c "
import json; d = json.load(open('/tmp/good.json'))
a = d['analysis']
print('SUMMARY  :', a['summary'])
print('CAUSE    :', a['root_cause'])
print('NEXT     :', a['recommended_next_check'])
print('SAMPLING :', d['sampling']['strategy'], d['sampling']['coverage_pct'], '%')
print('GROUNDING:', a['grounding'])
for c in a['claims']:
    print(('  OK  ' if c['verified'] else '  BAD '), c['claim'], '->', c['cite'])
"
```

**Narrate in this order:**

1. **The numbers block first.** "No model has been involved yet. Error rate,
   p95, first error, and — look — the change events. That last line is a Python
   `if` statement, not intelligence, and on most incidents it is the answer."
2. **Then the narrative.** It should name the pool change and the deploy ID.
3. **Then the claims.** Point at a line index. **Actually go and open that log
   line in another window.** This is the single most persuasive thirty seconds
   of the day — the citation resolves to a real line and the quoted fragment is
   really there.
4. **Then the coverage.** "It saw 22% of the window and it says so."

**Ask the room:** *"who wrote a sentence that matches this?"* Usually about
half, and the half who found the deploy line are the same half. Note that out
loud.

---

## DEMO 2 — the same pipeline, confidently wrong (10 minutes)

**This is the most valuable five minutes of the day. Protect the time for it.**

**Goal:** the room watches an identical model, on an identical incident, from
an identical zip file, produce a fluent and completely wrong answer — with
nothing in the output to warn them.

**Before you run it, make the stakes explicit:**

> "This is the same code. Same file, same handler, same model, same incident.
> Four environment variables are different. Watch what it says and, more
> importantly, watch how it says it."

**Run the naive analyser on the same window:**

```bash
aws lambda invoke \
  --function-name cbc-day06-naive-analyser-XXXX \
  --payload '{"alarmName":"demo-naive"}' \
  --cli-binary-format raw-in-base64-out \
  --profile bootcamp --region us-east-1 /tmp/naive.json

python3 -c "
import json; d = json.load(open('/tmp/naive.json'))
a = d['analysis']
print('SUMMARY  :', a['summary'])
print('CAUSE    :', a['root_cause'])
print('CONF     :', a['confidence'])
print('SAMPLING :', d['sampling']['strategy'])
print('WARNING  :', d['sampling'].get('warning'))
"
```

**What you will see**, reliably: a well-structured summary blaming the database
— connection pool exhaustion, timeouts, a suggestion to look at the database's
capacity or connection limits. Often `confidence: high`. Every claim it makes
will be **true**. The conclusion will be **wrong**.

**Now do the three things that make this land:**

**One — put the two summaries side by side.** Same model, same incident. Ask
the room which one they would have acted on at 03:00. They will admit, if you
give them room, that both read as authoritative.

**Two — show why.** Print the first line of what each analyser was given:

```bash
python3 -c "
import json
for name, path in (('good', '/tmp/good.json'), ('naive', '/tmp/naive.json')):
    d = json.load(open(path))
    s = d['sampling']
    print(name, '->', s['strategy'], '|', s['sampled_lines'], 'of', s['total_lines'])
"
```

The naive one sampled the tail. The deploy line is in the first 1% of the
window. **It was never shown the cause.**

Then say the sentence the whole day is built around:

> "It did not lie. It answered the question it was given, using all the
> evidence it had. The evidence was wrong, and **there is nothing in its output
> that could tell you that** — because a model cannot report a gap it was never
> told about."

**Three — connect it back to Step 3.** The half of the room who sorted
descending made exactly the same mistake, for exactly the same reason, forty
minutes ago. That is not a coincidence and it is not a criticism — tail-first
is the natural instinct. The difference is that a human scrolling up eventually
finds the deploy line, and a truncated prompt never can.

**Close the demo with the fixes, quickly**, because now they mean something:

- head + stratified + tail, never tail-only
- state the coverage in the output
- require citations and check them in code
- give it permission to say `insufficient_evidence`
- numbers above prose

### If the naive analyser accidentally gets it right

It happens occasionally — a short window, or the cascade generated fewer lines
than the tail budget, so the tail contained the head. Two recoveries:

**Recover with volume.** Re-run the chaos with more lines so the tail cannot
reach the start:

```bash
aws lambda invoke --function-name cbc-day06-chaos-XXXX \
  --payload '{"mode":"cascade","lines":4000,"window_minutes":25}' \
  --cli-binary-format raw-in-base64-out \
  --profile bootcamp --region us-east-1 /tmp/big.json
```

**Or recover with honesty**, which is often better: point out that the naive
implementation is not *always* wrong — it is wrong *unpredictably*, which is
worse, because you cannot tell from the output which kind of day it is having.

### The optional third run — permission to say nothing

If you have two spare minutes, this is a good closer:

```bash
aws lambda invoke --function-name cbc-day06-chaos-XXXX \
  --payload '{"mode":"cascade","include_cause":false}' \
  --cli-binary-format raw-in-base64-out \
  --profile bootcamp --region us-east-1 /tmp/nocause.json
```

Then run the **good** analyser on it. With `insufficient_evidence` in the
schema and a prompt that says returning it is correct, it will often say it
cannot identify a cause. Point out that this is the behaviour you had to
deliberately build a route to — because a model asked "what caused this" will
otherwise always produce a cause, since that is what it was asked for.

---

## 3:35 — Cost and the auditor (10m)

Run the auditor live:

```bash
cd lab/python && python3 obs_audit.py --profile bootcamp --region us-east-1
```

**15 findings, 144 points, 0/100, grade F.**

Then the two things worth saying about that number:

**One — the score is meant to be zero.** Three CRITICAL findings are 75 points
on their own. The floor is not a bug; once you are at zero there is no useful
distinction between "very broken" and "even more broken".

**Two — static and live are identical.** Point out that they just generated a
real incident, watched three alarms transition, paged themselves and ran two
analysers, and **the auditor's output did not change at all**. It audits
configuration; monitoring watches runtime. Treating either one as the other is
the category error the day is about.

Finish with the two silent checks and the difference between silent by design
and silent by situation. It is a thirty-second point and it is the one people
quote back to you months later.

---

## Where this day goes wrong in a room

| Symptom | Cause | Fix |
|---|---|---|
| `AccessDeniedException` from Bedrock | Model access not requested | Console → Bedrock → Model access. Have a screenshot ready. |
| Nobody gets alarm mail | Unconfirmed SNS subscription | Get everyone clicking at 0:35, not at 1:55. |
| Dashboard is empty | Chaos not run yet, or widget missing `region` | Run the baseline before the room arrives. |
| Alarms stay in `INSUFFICIENT_DATA` | Backdated log events fill the graph but alarms evaluate on wall-clock | Say so up front; it is a genuinely confusing interaction. |
| Analyser times out | `analyser_lambda_timeout_seconds` lowered | Default is 180 for a reason: a query plus a model call. |
| Everyone jumps ahead to Step 6 | Step 3 feels like busywork | Make the sentence-on-paper mandatory. The demo depends on it. |
| Room disengages during Part 4 | It is dense and it is all settings | Break it with the `describe-log-groups` moment from 0:10 if you have not used it yet. |
| Someone argues LLMs are useless here | Usually a good-faith position | Agree with 90% of it. The deterministic half is most of the value. Then run Demo 1. |

---

## The three sentences to leave them with

1. **"Never expire" is not a setting anyone chose.** It is what happens when
   nobody decides, and it bills forever.
2. **The best alarm in most stacks is the one nobody has built** — the one that
   treats missing data as breaching.
3. **A summary you cannot check is worse than no summary**, because it is
   actionable and wrong, and the person reading it at 03:00 will act on it.
