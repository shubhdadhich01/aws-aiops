# Day 04 — Architecture Diagrams

ASCII diagrams for the whiteboard, the README and the interview. Every one of
these is drawable from memory in under two minutes, which is the only test
that matters when someone hands you a marker.

1. [Target architecture](#1-target-architecture)
2. [Two invocation paths](#2-two-invocation-paths)
3. [Where a failed event goes](#3-where-a-failed-event-goes)
4. [Invocation models and their failure behaviour](#4-invocation-models-and-their-failure-behaviour)
5. [Why the reactive rule needs CloudTrail](#5-why-the-reactive-rule-needs-cloudtrail)
6. [Permission model: role vs resource policy](#6-permission-model-role-vs-resource-policy)
7. [The recursive invocation trap](#7-the-recursive-invocation-trap)
8. [Audit findings map](#8-audit-findings-map)

---

## 1. Target architecture

```
   ┌────────────────────────────────────────────────────────────────────┐
   │                         AWS account · us-east-1                    │
   │                                                                    │
   │  ┌──────────────────┐         ┌──────────────────┐                 │
   │  │ EventBridge      │         │ CloudTrail       │                 │
   │  │ scheduled-scan   │         │ cbc-day04-trail  │───► S3 bucket   │
   │  │ rate(1 hour)     │         └────────┬─────────┘    (versioned,  │
   │  └────────┬─────────┘                  │               encrypted,  │
   │           │                            ▼               lifecycled) │
   │           │                   ┌──────────────────┐                 │
   │           │                   │ EventBridge      │                 │
   │           │                   │ reactive-scan    │                 │
   │           │                   │ (event pattern)  │                 │
   │           │                   └────────┬─────────┘                 │
   │           │  async invoke              │  async invoke             │
   │           └────────────┬───────────────┘                           │
   │                        ▼                                           │
   │        ┌───────────────────────────────────┐                       │
   │        │ Lambda cbc-day04-compliance-scanner│                      │
   │        │  runtime  python3.12               │                      │
   │        │  timeout  60s      memory  256 MB  │                      │
   │        │  reserved concurrency  2           │                      │
   │        │  tracing  Active   env  KMS-CMK    │                      │
   │        │  role  4 scoped policies           │                      │
   │        └───┬────────────┬──────────────┬────┘                      │
   │            │            │              │                          │
   │   findings │      logs  │      failure │                          │
   │            ▼            ▼              ▼                          │
   │     ┌────────────┐ ┌──────────┐ ┌───────────────┐                 │
   │     │ SNS topic  │ │ Log group│ │ SQS DLQ       │                 │
   │     │ (KMS)      │ │ 7-day    │ │ 14-day, KMS   │                 │
   │     └─────┬──────┘ │ retention│ └───────┬───────┘                 │
   │           │        └──────────┘         │                         │
   │           ▼                             ▼                         │
   │      your inbox              ┌────────────────────┐               │
   │   (CONFIRM THE LINK)         │ CloudWatch alarms  │               │
   │                              │ · scanner errors   │               │
   │                              │ · DLQ not empty    │               │
   │                              └────────────────────┘               │
   └────────────────────────────────────────────────────────────────────┘
```

---

## 2. Two invocation paths

The proactive path is the backstop. The reactive path is the fast one. You
want both, and for different reasons.

```
   PROACTIVE                                REACTIVE
   ─────────                                ────────
   rate(1 hour)                             CreateBucket / RunInstances / …
        │                                          │
        ▼                                          ▼
   scans EVERYTHING                          scans ONE resource
        │                                          │
   latency: up to 1 hour                     latency: ~15–90 seconds
   catches: drift, manual changes,           catches: the change as it happens
            anything the pattern missed
        │                                          │
        └──────────────► same handler ◄────────────┘
                       (branches on payload)

   Why both:
     the pattern will miss something (a service you did not list)
     the schedule will be too slow for something (a public bucket)
```

---

## 3. Where a failed event goes

```
  EventBridge ──► Lambda (async)
       │              │
       │              ├─ attempt 1 ─── error
       │              ├─ attempt 2 ─── error       (maximum_retry_attempts)
       │              └─ attempt 3 ─── error
       │                     │
       │                     ▼
       │            ┌──────────────────┐
       │            │ DeadLetterConfig │  event body only
       │            │  OR              │
       │            │ OnFailure dest.  │  event + response/error
       │            └────────┬─────────┘
       │                     ▼
       │              SQS cbc-day04-scanner-dlq (14 days)
       │                     │
       │                     ▼
       │            CloudWatch alarm: messages > 0 ──► SNS ──► you
       │
       └─ EventBridge could not deliver AT ALL (throttle, permission)
                │
                ▼
          target retry_policy + target dead_letter_config
                │
                ▼
          same DLQ, different failure domain

  With NONE of the above:
          event ──► ✗  gone.  Errors metric +1.  No payload. No replay.
```

---

## 4. Invocation models and their failure behaviour

```
  ┌────────────────┬──────────────────────┬────────────────────────────┐
  │ SYNCHRONOUS    │ ASYNCHRONOUS         │ POLL-BASED (stream/queue)  │
  ├────────────────┼──────────────────────┼────────────────────────────┤
  │ API Gateway    │ EventBridge          │ SQS                        │
  │ ALB            │ SNS                  │ Kinesis                    │
  │ lambda invoke  │ S3 notifications     │ DynamoDB Streams           │
  ├────────────────┼──────────────────────┼────────────────────────────┤
  │ error returned │ retried ×2 then      │ retried until expiry;      │
  │ to the caller  │ DISCARDED unless a   │ blocks the shard or the    │
  │ caller decides │ DLQ/destination is   │ queue behind it            │
  │                │ configured           │                            │
  ├────────────────┼──────────────────────┼────────────────────────────┤
  │ DLQ: N/A       │ DLQ: yes ◄── today   │ DLQ: on the QUEUE, not the │
  │                │                      │      function              │
  └────────────────┴──────────────────────┴────────────────────────────┘

  Interview trap: "does a DLQ help a synchronous invocation?"  No.
  There is a caller holding the connection. It gets the error.
```

---

## 5. Why the reactive rule needs CloudTrail

```
   WITHOUT a trail                      WITH a trail
   ───────────────                      ────────────
   API call: CreateBucket               API call: CreateBucket
        │                                    │
        ▼                                    ▼
   CloudTrail records it in            CloudTrail records it AND
   Event history (90 days,             delivers management events
   console only)                       to the default event bus
        │                                    │
        ✗ no event on the bus                ▼
        │                             EventBridge rule matches
        ▼                                    │
   rule NEVER fires.                         ▼
   No error. No log line.              Lambda invoked ~15–90s later
   The diagram is still correct.

   Cost: the FIRST trail delivering management events to S3 is free.
         Additional trails: $2 per 100,000 events.
         → the lab creates exactly one.
```

---

## 6. Permission model: role vs resource policy

Two different questions, two different objects, and confusing them is the most
common "my rule fires but nothing happens" cause.

```
                    ┌────────────────────────┐
   WHO MAY CALL IT  │                        │  WHAT IT MAY DO
   ───────────────► │   Lambda function      │ ───────────────►
                    │                        │
   resource policy  │                        │  execution role
   (aws_lambda_     │                        │  (aws_iam_role +
    permission)     └────────────────────────┘   policies)
        │                                              │
        ▼                                              ▼
   principal = events.amazonaws.com              sns:Publish  (one topic ARN)
   source_arn = <this rule's ARN>  ◄── scope it! sqs:SendMessage (the DLQ)
                                                 lambda:List*  (read-only)
                                                 kms:Decrypt   (one key)

   Missing resource policy  → rule fires, function never runs, no error
                              anywhere obvious.
   Missing source_arn       → ANY rule in ANY account may invoke you.
   Missing kms:Decrypt      → cold start fails with KMSAccessDenied and a
                              message that names KMS, not your config.
```

---

## 7. The recursive invocation trap

```
        ┌──────────────────────────────────────────────┐
        │                                              │
        ▼                                              │
   ┌─────────┐      writes object      ┌──────────┐    │
   │ Lambda  │ ──────────────────────► │ S3 bucket│    │
   └─────────┘                         └────┬─────┘    │
        ▲                                   │          │
        │        s3:ObjectCreated:*         │          │
        └───────────────────────────────────┘──────────┘

   Unreserved concurrency:
        1 → 2 → 4 → … → 1,000 parallel copies, billing continuously,
        throttling every other function in the account with it.

   reserved_concurrent_executions = 2:
        the loop still exists, but it runs at 2 wide and costs pennies
        while you notice. Physically bounded, not merely unlikely.

   Same shape with: EventBridge rule on an API call the function makes,
                    SNS topic the function publishes to and subscribes to,
                    DynamoDB stream on a table the function writes.
```

---

## 8. Audit findings map

Which broken resource produces which finding — 14 in total, and the two that
stay silent on purpose.

```
  cbc-day04-broken-role ────────────────► CMP-004  CRITICAL  Action* Resource*

  cbc-day04-broken-function ────┬───────► CMP-001  CRITICAL  no DLQ
                                ├───────► CMP-002  CRITICAL  plaintext secrets
                                ├───────► CMP-003  MEDIUM    no CMK on env
                                ├───────► CMP-005  MEDIUM    log group absent
                                ├───────► CMP-006  MEDIUM    timeout = 3s
                                ├───────► CMP-007  MEDIUM    unreserved
                                └───────► CMP-009  LOW       tracing off

  cbc-day04-broken-topic ───────┬───────► CMP-010  MEDIUM    not encrypted
                                └───────► CMP-011  CRITICAL  Principal "*"

  cbc-day04-broken-queue ───────┬───────► CMP-012  MEDIUM    not encrypted
                                └───────► CMP-013  MEDIUM    no redrive policy

  cbc-day04-broken-rule ────────┬───────► CMP-014  MEDIUM    DISABLED
                                └───────► CMP-015  LOW       target: no retry/DLQ

  SILENT BY DESIGN ────────────────────► CMP-008  (python3.12 everywhere)
                                        CMP-016  (permissions scoped by
                                                  source_arn; no function URL)

  Score: 100 − (4×25) − (8×4) − (2×1) = −34 → floored to 0/100 · grade F

  Silent on the good stack too:
    cbc-day04-compliance-scanner  · cbc-day04-findings (SourceAccount condition)
    cbc-day04-scanner-dlq (a DLQ needs no DLQ) · both good rules
```
