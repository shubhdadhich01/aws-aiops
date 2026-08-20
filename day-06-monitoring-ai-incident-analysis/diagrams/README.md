# Day 06 — Diagrams

Every diagram for Day 06, in one file, so there is one place to fix them.

All of these are validated by actually parsing them, not by looking at them.
The check is in the CP6 sweep and it is worth stealing: a Mermaid block that
does not parse renders as a grey error box in GitHub, and nobody notices until
a learner does.

---

## 1. The whole stack

The deterministic half is on top. The AI half hangs off the side, deliberately
— it observes the alerting path rather than sitting inside it, for the reasons
in `lab/terraform/main.tf` section 6.

```mermaid
flowchart TB
  subgraph GEN["Generating an incident"]
    CHAOS["chaos workload Lambda<br/>emits a realistic cascade on demand"]
  end

  subgraph DET["Deterministic — exact, instant, free"]
    LG["workload log group<br/>retention 7 days, STANDARD class"]
    MF1["metric filter: RequestCount<br/>default_value 0"]
    MF2["metric filter: ErrorCount<br/>default_value 0"]
    MF3["metric filter: ErrorCountByType<br/>dimension bounded to 4 values"]
    MF4["metric filter: LatencyMillis<br/>extracts a VALUE, so p95 works"]
    A1["alarm: error RATE<br/>metric math, 3 of 5, notBreaching"]
    A2["alarm: p95 latency<br/>3 of 5, notBreaching"]
    A3["alarm: no telemetry<br/>10 of 10, BREACHING"]
    COMP["composite alarm<br/>the only thing that pages"]
    SNS1["SNS: alerts"]
    DASH["dashboard<br/>3 questions, in order"]
  end

  subgraph AI["Generative — one question the above cannot answer"]
    EB["EventBridge rule<br/>ALARM transitions only"]
    IDEM["DynamoDB idempotency<br/>one summary per alarm per window"]
    ANA["analyser Lambda<br/>facts first, then a grounded narrative"]
    BR["Amazon Bedrock<br/>scoped to ONE model ARN"]
    SNS2["SNS: summaries<br/>separate topic, contains log content"]
    DLQ["SQS DLQ"]
  end

  CHAOS --> LG
  LG --> MF1 --> A1
  LG --> MF2 --> A1
  LG --> MF3
  LG --> MF4 --> A2
  MF1 --> A3
  A1 --> COMP
  A2 --> COMP
  A3 --> COMP
  COMP --> SNS1
  MF1 --> DASH
  MF3 --> DASH
  MF4 --> DASH
  COMP --> EB
  EB --> ANA
  EB -.->|invocation failed twice| DLQ
  ANA --> IDEM
  ANA -->|reads a window| LG
  ANA -->|redacted, budgeted sample| BR
  BR -->|JSON with citations| ANA
  ANA --> SNS2
```

---

## 2. What the incident actually looks like

The shape that matters is the last two phases. Latency **falls** when the
circuit breaker opens, because failing fast is fast. On a latency-only
dashboard that reads as recovery.

```mermaid
flowchart LR
  P0["0. deploy lands<br/>pool 50 to 5<br/>ONE line, INFO"]
  P1["1. calm<br/>5 connections is enough"]
  P2["2. slow<br/>queueing, latency climbs<br/>ZERO errors"]
  P3["3. timeouts<br/>first ERROR lines"]
  P4["4. retry storm<br/>1 failure becomes 3 requests<br/>the vertical part of the graph"]
  P5["5. breaker opens<br/>latency DROPS<br/>looks like recovery"]
  P6["6. customers see 503"]

  P0 --> P1 --> P2 --> P3 --> P4 --> P5 --> P6

  P2 -.->|"error-rate alarm still OK"| N1["why the latency alarm exists"]
  P5 -.->|"latency alarm returns to OK"| N2["why the error-rate alarm exists"]
```

---

## 3. `treat_missing_data`, which is the setting people get wrong

```mermaid
flowchart TB
  Q["A period arrives with NO datapoint.<br/>What should the alarm do?"]
  Q --> M["missing — THE DEFAULT"]
  Q --> NB["notBreaching"]
  Q --> B["breaching"]
  Q --> I["ignore"]

  M --> M1["Look further back for real data.<br/>Find none, sit in INSUFFICIENT_DATA<br/>forever. Grey, quiet, useless."]
  NB --> NB1["Absence means health.<br/>Right for an error count."]
  B --> B1["Absence IS the bad news.<br/>This is the dead-man's switch."]
  I --> I1["Hold the last known state.<br/>Right for a genuinely bursty metric."]

  M1 --> WARN["The default is the option most<br/>likely to hide an outage.<br/>Check OBS-005."]
```

---

## 4. Three ways to get a metric out of an application

```mermaid
flowchart LR
  APP["your application"]

  APP -->|"writes a log line"| LOGS["CloudWatch Logs"]
  LOGS -->|"metric filter<br/>FREE to run, ~1 min lag"| CW["CloudWatch metric"]
  APP -->|"logs an EMF blob<br/>extracted at ingestion"| CW
  APP -->|"PutMetricData<br/>$0.01 per 1,000 calls<br/>on your hot path"| CW

  CW --> COST["Every distinct namespace +<br/>name + dimension VALUES<br/>= 1 custom metric<br/>= $0.30 per month<br/>= CANNOT BE DELETED"]
```

Ninety per cent of custom metrics should be metric filters. Most of the rest
should be EMF. `PutMetricData` is a specialist tool that gets reached for first
because it is the one in the SDK docs.

---

## 5. Sampling, which decides whether the answer can be right

```mermaid
flowchart TB
  WIN["5,000 log lines in the window"]

  WIN --> NAIVE["events[-200:]<br/>the one-line implementation"]
  WIN --> GOOD["head + stratified middle + tail"]

  NAIVE --> N1["200 consequences<br/>0 causes"]
  N1 --> N2["The model explains the<br/>consequences, fluently,<br/>and blames the database."]
  N2 --> N3["Confident. Structured.<br/>Actionable. Wrong.<br/>Nothing in the output hints<br/>that the answer is missing."]

  GOOD --> G1["25% head — deploys, config<br/>changes, the first WARN"]
  GOOD --> G2["50% evenly spaced — keeps<br/>the SHAPE of the escalation"]
  GOOD --> G3["25% tail — the current state"]
  G1 --> G4["The cause is in the sample,<br/>and the output states<br/>what fraction was seen."]
  G2 --> G4
  G3 --> G4
```

---

## 6. The grounding loop

This is the day's argument, as code. Everything before `verify` is ordinary.
`verify` is what turns "sounds right" into "is checkable".

```mermaid
sequenceDiagram
  autonumber
  participant EB as EventBridge
  participant L as analyser Lambda
  participant CW as CloudWatch Logs
  participant M as Bedrock
  participant S as SNS summaries

  EB->>L: alarm entered ALARM
  L->>L: idempotency lock, or stop here
  L->>CW: filter_log_events over the window
  CW-->>L: raw events
  L->>L: deterministic_facts — counts, rates, p95,<br/>first error, CHANGE EVENTS
  Note over L: These numbers are already the answer<br/>maybe two times in three.
  L->>L: sample head + middle + tail, keeping ORIGINAL indices
  L->>L: redact, then trim to the token budget
  L->>M: system contract + facts + numbered lines
  M-->>L: JSON: claims, each with a line index and a verbatim quote
  L->>L: verify_claims — resolve every index,<br/>check every quote against the real line
  Note over L: Failures are MARKED, not dropped.<br/>Hiding them is the same mistake as trusting them.
  L->>S: numbers first, narrative second, unverified flagged
```

---

## 7. Where the money goes

The dashed box is the one that breaks the pattern of Days 01 to 05: it is
priced per request and leaves nothing behind to delete.

```mermaid
flowchart TB
  subgraph EXISTS["Billed for existing — countable in terraform plan"]
    AL["standard alarms<br/>$0.10 each per month<br/>first 10 free"]
    CA["composite alarms<br/>$0.50 each per month<br/>NOT in the free ten"]
    DB["dashboards<br/>$3.00 each per month<br/>first 3 free"]
  end

  subgraph HAPPENS["Billed for happening — invisible to terraform"]
    ING["log ingestion<br/>$0.50 per GB"]
    ST["log storage<br/>$0.03 per GB-month<br/>FOREVER without retention"]
    CM["custom metrics<br/>$0.30 each per month<br/>NO DELETE API<br/>15 months to age out"]
    LI["Logs Insights<br/>$0.005 per GB scanned"]
  end

  subgraph MODEL["Billed per token, nothing to delete"]
    BR["Bedrock<br/>per 1,000 input and output tokens"]
  end

  ING --> ST
  LI -.->|"the query is half a cent"| BR
  BR -.->|"the model is 30 to 120x the query"| BR
```

---

## 8. Which check owns which fault

Four of the sixteen checks are not independent. Drawing it is quicker than
explaining it, and the arrows are the reason a single unretained log group
produces one finding rather than two.

```mermaid
flowchart TB
  LG1["log group, no retention"]
  LG2["log group, no retention,<br/>belongs to a model-invoking function"]
  LG3["log group under /aws/lambda/"]
  AL1["alarm, no actions,<br/>covered by a WORKING composite"]
  AL2["alarm, no actions,<br/>covered only by a BROKEN composite"]
  AL3["alarm on a raw Sum,<br/>treat_missing_data breaching, LessThan"]

  LG1 --> O1["OBS-001 fires"]
  LG2 --> O15["OBS-015 fires<br/>OBS-001 steps aside"]
  LG3 --> S1["OBS-002 silent<br/>an execution log is not a data feed"]
  AL1 --> S2["OBS-004 silent<br/>the composite pages for it"]
  AL2 --> O4["OBS-004 fires<br/>AND OBS-007 fires on the composite"]
  AL3 --> S3["OBS-006 silent<br/>a dead-man's switch IS a raw count"]

  O4 -.->|"cause and consequence,<br/>not duplicates"| O4
```

---

## Validating these

```bash
cd /path/with/node_modules
node check_mermaid.mjs day-06-monitoring-ai-incident-analysis/diagrams/README.md \
                      day-06-monitoring-ai-incident-analysis/README.md
```

The validator loads each block under a JSDOM global and calls
`mermaid.parse()`. One caveat worth writing down because it costs half an hour
the first time: on Node 22, `global.navigator` is getter-only. Assigning to it
throws before Mermaid ever loads, and the error does not mention Mermaid.
