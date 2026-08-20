# Day 07 — Diagrams

Every diagram for Day 07, in one file, so there is one place to fix them.

All of these are validated by actually parsing them, not by looking at them.
The check is in the CP6 sweep.

---

## 1. The whole stack

Detection on the left, evidence underneath, response on the right. The three
things guarding the response path — the allow-list, the kill switch and the
Denies on the responder role — are drawn as gates rather than as boxes,
because that is what they are.

```mermaid
flowchart LR
  subgraph DETECT["Detection"]
    GD["GuardDuty detector<br/>publishes every 15 min"]
    SH["Security Hub<br/>ONE standard"]
  end

  subgraph EVIDENCE["Evidence"]
    CT["CloudTrail<br/>multi-region + validated"]
    S3["S3 bucket<br/>versioned, blocked, TLS-only"]
  end

  subgraph RESPOND["Response"]
    EB["EventBridge rule<br/>matches ALL findings"]
    G1{"on the TYPE<br/>allow-list?"}
    G2{"kill switch<br/>ARMED?"}
    G3{"mode is<br/>reversible?"}
    ACT["isolate: swap security groups<br/>record the previous ones<br/>tag the instance"]
    NOPE["log the decision<br/>and say WHY"]
  end

  GD --> SH
  GD --> EB
  CT --> S3
  CT -.->|"analysed by"| GD
  EB --> G2
  G2 -->|"DISARMED"| NOPE
  G2 -->|"ARMED"| G1
  G1 -->|"no"| NOPE
  G1 -->|"yes"| G3
  G3 -->|"no"| NOPE
  G3 -->|"yes"| ACT
  ACT --> SNS2["SNS: containment<br/>with the rollback command"]
  NOPE --> SNS2
  EB --> SNS1["SNS: findings"]
```

---

## 2. Severity is impact, not confidence

The single most important idea on this day, drawn because the table version
does not land as hard.

```mermaid
flowchart TB
  Q["A GuardDuty finding arrives<br/>severity 7.5, HIGH"]

  Q --> A["your penetration test"]
  Q --> B["your own vulnerability scanner"]
  Q --> C["a researcher probing a public endpoint"]
  Q --> D["a developer on hotel wifi"]
  Q --> E["an actual compromise"]

  A --> T["severity >= 7<br/>CONTAIN"]
  B --> T
  C --> T
  D --> T
  E --> T

  T --> OUT["4 outages you caused<br/>1 correct action"]

  E --> TYPE["allow-list on TYPE<br/>CryptoCurrencyMining, C2 DNS,<br/>known-malicious caller"]
  TYPE --> GOOD["1 correct action<br/>0 outages"]
```

---

## 3. What the responder is allowed to do, and what it is denied

The Denies are the interesting half. An explicit Deny cannot be overridden by
any Allow, in any policy, ever — which is why these are Denies and not merely
absences.

```mermaid
flowchart LR
  R["responder role"]

  R --> A1["ALLOW ec2:DescribeInstances<br/>ec2:DescribeSecurityGroups"]
  R --> A2["ALLOW ec2:ModifyInstanceAttribute<br/>ec2:CreateTags<br/>on instances only"]
  R --> A3["ALLOW ssm:GetParameter<br/>on the kill switch only"]
  R --> A4["ALLOW sns:Publish<br/>on one topic"]

  R --> D1["DENY cloudtrail:StopLogging<br/>DeleteTrail, UpdateTrail<br/>it must not erase its own actions"]
  R --> D2["DENY iam:*<br/>otherwise every scope above<br/>is advisory"]
  R --> D3["DENY ssm:PutParameter<br/>the brake must not be reachable<br/>by the thing it brakes"]
  R --> D4["DENY TerminateInstances<br/>DeleteAccessKey, DeleteSecret<br/>reversible actions only"]
```

---

## 4. Reversible containment, and what it records

```mermaid
sequenceDiagram
  autonumber
  participant EB as EventBridge
  participant L as responder
  participant SSM as SSM parameter
  participant EC2 as EC2
  participant SNS as SNS containment

  EB->>L: GuardDuty finding
  L->>SSM: read kill switch, every invocation, no cache
  SSM-->>L: ARMED
  Note over L: unreadable would mean FAIL SAFE:<br/>take no action
  L->>L: is it a sample? is it on the allow-list?
  L->>EC2: DescribeInstances - record the CURRENT security groups
  EC2-->>L: [sg-aaa, sg-bbb]
  Note over L: this is the entire rollback story.<br/>Without it, "reversible" means<br/>somebody remembering at 3am.
  L->>EC2: ModifyInstanceAttribute - groups = [quarantine]
  L->>EC2: CreateTags - finding id, timestamp, previous groups
  L->>SNS: decision, reason, and the exact rollback command
  Note over SNS: also published when it does NOTHING.<br/>"Why did nothing happen" is asked<br/>more often than the opposite.
```

---

## 5. The four-step rotation protocol

The design exists so that a rotation which fails halfway leaves a WORKING
credential behind.

```mermaid
flowchart LR
  C["createSecret<br/>generate, store as AWSPENDING<br/>MUST be idempotent"]
  S["setSecret<br/>push to the real service<br/>THE REAL WORK"]
  T["testSecret<br/>connect using AWSPENDING"]
  F["finishSecret<br/>move AWSCURRENT, atomically"]

  C --> S --> T --> F

  T -->|"raises"| STOP["rotation stops<br/>AWSCURRENT untouched<br/>the old credential still works"]

  S -.->|"stubbed out"| TRAP["every rotation SUCCEEDS<br/>LastRotatedDate updates<br/>the console is green<br/>and the credential never changed"]
  TRAP --> OUTAGE["a scheduled outage<br/>that passes its own check"]
```

---

## 6. Why RotationEnabled is not the field to look at

```mermaid
flowchart TB
  Q["Is rotation working?"]
  Q --> R1["RotationEnabled: true"]
  R1 --> M1["means: a SCHEDULE EXISTS"]
  M1 --> M2["says nothing about<br/>whether it has ever run"]

  Q --> R2["LastRotatedDate"]
  R2 --> M3["absent -> it has never run"]
  R2 --> M4["far older than the interval<br/>-> it has been failing since then"]
  R2 --> M5["recent -> it works"]

  M2 --> SEC["SEC-011 reads the second field.<br/>It is the check most likely to fire<br/>in an account that believes it is fine."]
  M3 --> SEC
  M4 --> SEC
```

---

## 7. Where the money goes

Nothing here is priced per resource. Everything is priced per unit of activity,
which means `terraform plan` cannot tell you the number.

```mermaid
flowchart TB
  subgraph COUNTABLE["Countable in a plan"]
    SM["Secrets Manager<br/>$0.40 per secret per month"]
  end

  subgraph VOLUME["Priced by what they analyse"]
    GD["GuardDuty<br/>~$4.00 per million CloudTrail events<br/>~$1.00/GB flow and DNS logs<br/>FREE for 30 days, then not"]
    SH["Security Hub<br/>~$0.0010 per security check<br/>per control per resource PER DAY"]
    DE["CloudTrail data events<br/>~$0.10 per 100,000<br/>NO free allowance"]
  end

  GD --> TRAP1["enabled in 15 regions<br/>during a compliance push<br/>and never revisited"]
  SH --> TRAP2["every standard enabled<br/>on day one"]
  DE --> TRAP3["one busy bucket<br/>~26 million events/day<br/>~$780/month"]
```

---

## 8. Which check owns which fault

Five of the sixteen checks are not independent. The arrows are why a secret
with no rotation produces one finding rather than two.

```mermaid
flowchart TB
  S1["secret, RotationEnabled false"]
  S2["secret, RotationEnabled true,<br/>LastRotatedDate absent"]
  R1["role: allows ec2:*, DENIES<br/>Terminate/Delete/cloudtrail/iam"]
  R2["role: allows ec2:*, cloudtrail:*,<br/>iam:* with no Deny"]
  L1["Lambda with no containment<br/>permissions and no kill switch"]
  L2["Lambda with ec2:ModifyInstanceAttribute<br/>and no kill switch"]

  S1 --> O10["SEC-010 fires<br/>SEC-011 steps aside"]
  S2 --> O11["SEC-011 fires<br/>SEC-010 steps aside"]
  R1 --> N1["SEC-008 SILENT<br/>the Denies are read first"]
  R2 --> O8["SEC-008 fires"]
  L1 --> N2["SEC-014 SILENT<br/>it cannot act, so it needs no brake"]
  L2 --> O14["SEC-014 fires"]
```

---

## Validating these

```bash
cd /path/with/node_modules
node check_mermaid.mjs day-07-enterprise-cloud-security/diagrams/README.md \
                      day-07-enterprise-cloud-security/README.md
```

The validator loads each block under a JSDOM global and calls
`mermaid.parse()`. On Node 22, do not assign to `global.navigator` — it is
getter-only and the error does not mention Mermaid.
