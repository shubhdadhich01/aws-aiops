# Day 03 — Architecture Diagrams

All Mermaid sources for Day 03 in one place. These render natively on GitHub. Copy any
block into [mermaid.live](https://mermaid.live) to edit or export a PNG for slides.

| # | Diagram | Use it to explain |
|---|---|---|
| 1 | [Target architecture](#1-target-architecture) | What the lab builds end to end |
| 2 | [Self-healing loop](#2-the-self-healing-loop) | How ELB health checks drive ASG replacement |
| 3 | [Scaling policy decision tree](#3-scaling-policy-decision-tree) | Choosing target tracking vs step vs scheduled |
| 4 | [Warm-up vs cooldown timeline](#4-warm-up-vs-cooldown-timeline) | Why boot-time CPU causes scaling storms |
| 5 | [ALB request routing](#5-alb-request-routing) | Listener rules, priority, default action |
| 6 | [ALB vs NLB vs GWLB](#6-load-balancer-selection) | Picking the right LB in a design review |
| 7 | [Health check chain](#7-health-check-chain) | Where each health signal originates |
| 8 | [Chaos test timeline](#8-chaos-test-timeline) | What learners will actually observe |
| 9 | [Audit findings map](#9-audit-findings-map) | What `ha_audit.py` inspects |

---

## 1. Target architecture

```mermaid
flowchart TB
    User([Internet Users])

    subgraph VPC["cbc-day03-vpc  10.30.0.0/16"]
        IGW[Internet Gateway]

        subgraph PUB["Public subnets"]
            direction LR
            PubA["public-a<br/>10.30.0.0/24<br/>us-east-1a"]
            PubB["public-b<br/>10.30.1.0/24<br/>us-east-1b"]
        end

        ALB{{"Application Load Balancer<br/>internet-facing<br/>HTTP :80 -> redirect 443"}}

        subgraph PRIV["Private app subnets"]
            direction LR
            AppA["app-a<br/>10.30.10.0/24<br/>us-east-1a"]
            AppB["app-b<br/>10.30.11.0/24<br/>us-east-1b"]
        end

        NAT[NAT Gateway]

        subgraph ASG["Auto Scaling Group  min 2 / desired 2 / max 4"]
            direction LR
            I1["EC2 t3.micro<br/>AZ-a"]
            I2["EC2 t3.micro<br/>AZ-b"]
        end

        TG["Target Group :80<br/>health check /health"]
    end

    CW[CloudWatch<br/>ASGAverageCPUUtilization]

    User --> IGW --> ALB
    ALB --> TG
    TG --> I1
    TG --> I2
    I1 -.-> AppA
    I2 -.-> AppB
    PubA --- ALB
    PubB --- ALB
    AppA --> NAT --> IGW
    AppB --> NAT
    ASG <--> CW
    TG -. "ELB health check<br/>feeds ASG" .-> ASG
```

---

## 2. The self-healing loop

```mermaid
sequenceDiagram
    participant TG as Target Group
    participant ASG as Auto Scaling Group
    participant LT as Launch Template
    participant EC2 as EC2

    Note over TG: Health check /health every 15s
    TG->>TG: 2 consecutive failures
    TG->>ASG: Target marked unhealthy
    Note over ASG: health_check_type = "ELB"<br/>grace period elapsed?
    ASG->>EC2: TerminateInstanceInAutoScalingGroup
    ASG->>LT: Read latest version
    LT->>EC2: RunInstances (new instance)
    EC2->>TG: Register target
    Note over TG: Grace period + 2 healthy checks
    TG->>ASG: Target healthy
    Note over ASG: Desired capacity restored
```

---

## 3. Scaling policy decision tree

```mermaid
flowchart TD
    Q1{Do you have one metric<br/>with a known good value?}
    Q1 -->|Yes| TT[Target Tracking<br/>e.g. CPU at 50%]
    Q1 -->|No| Q2{Do you need different<br/>reactions at different<br/>breach sizes?}
    Q2 -->|Yes| ST[Step Scaling<br/>+1 at 60%, +3 at 80%]
    Q2 -->|No| Q3{Is the load pattern<br/>predictable by clock?}
    Q3 -->|Yes| SS[Scheduled Scaling<br/>+ Predictive]
    Q3 -->|No| SIM[Simple Scaling<br/>legacy - avoid]

    TT --> D[Start here.<br/>90% of workloads.]
    ST --> D2[Use when target<br/>tracking is too slow.]
```

---

## 4. Warm-up vs cooldown timeline

Two instances launch at t=0. The new instance's CPU spikes to 100% during boot.

```mermaid
gantt
    title Instance warm-up excludes boot-time CPU from the aggregate
    dateFormat  s
    axisFormat %S

    section Instance boots
    RunInstances to running     :a1, 0, 45s
    OS + app startup (CPU 100%) :a2, 45, 105s
    Registered, passing checks  :a3, 150, 30s

    section Warm-up window (180s)
    Metrics IGNORED by policy   :crit, w1, 0, 180s

    section Without warm-up
    False scale-out triggered   :crit, b1, 60, 40s
    Another instance boots      :b2, 100, 150s
    Scaling storm               :crit, b3, 250, 100s
```

**Read it this way:** during the warm-up window the booting instance contributes nothing
to `ASGAverageCPUUtilization`. Remove the warm-up and its 100% boot CPU drags the average
up, which triggers another scale-out, which boots another instance at 100%, and so on.

---

## 5. ALB request routing

```mermaid
flowchart LR
    R[Request] --> P10{Priority 10<br/>path /api/*}
    P10 -->|match| TGA[api target group]
    P10 -->|no| P20{Priority 20<br/>host admin.*}
    P20 -->|match| TGB[admin target group]
    P20 -->|no| DEF[Default action<br/>web target group]
```

Rules evaluate in ascending priority order. **First match wins** — evaluation stops there.
The default action is only reached when no rule matches.

---

## 6. Load balancer selection

```mermaid
flowchart TD
    S{What is the traffic?}
    S -->|HTTP / HTTPS / gRPC| L7{Need WAF, path or<br/>host routing?}
    S -->|Raw TCP / UDP / TLS| L4{Need a static IP or<br/>millions of req/s?}
    S -->|Must pass through a<br/>firewall / IDS appliance| GWLB[Gateway Load Balancer<br/>GENEVE :6081]

    L7 -->|Yes| ALB[Application Load Balancer]
    L7 -->|No, but still HTTP| ALB
    L4 -->|Yes| NLB[Network Load Balancer]
    L4 -->|No| NLB

    ALB --> A1["Cross-zone: ON, free<br/>Source IP: X-Forwarded-For<br/>~16.20 USD/mo + LCU"]
    NLB --> N1["Cross-zone: OFF by default<br/>Source IP: preserved<br/>~16.20 USD/mo + NLCU"]
```

---

## 7. Health check chain

Three independent health signals. People confuse them constantly.

```mermaid
flowchart TB
    subgraph EC2HC["EC2 status checks - AWS infrastructure"]
        SYS["System status<br/>host hardware, network"]
        INST["Instance status<br/>OS reachable, kernel alive"]
    end

    subgraph TGHC["Target group health check - your app"]
        HTTP["GET /health every 15s<br/>expect 200"]
    end

    ASGD{"ASG health_check_type"}

    SYS --> ASGD
    INST --> ASGD
    HTTP --> ASGD

    ASGD -->|"= EC2 (default)"| E1["Only listens to<br/>EC2 status checks.<br/>Hung app NOT replaced."]
    ASGD -->|"= ELB (correct)"| E2["Listens to EC2 checks<br/>AND target group.<br/>Hung app IS replaced."]

    E1 --> BAD["Broken instance serves<br/>errors indefinitely"]
    E2 --> GOOD["Replaced in ~60s"]
```

---

## 8. Chaos test timeline

What learners observe after `aws ec2 terminate-instances`.

```mermaid
timeline
    title Instance termination to full recovery
    t+0s   : terminate-instances returns
    t+15s  : Target group health check fails
    t+30s  : Target state unhealthy then draining
           : ALB stops routing to it - user impact ends here
    t+45s  : ASG activity - Terminating EC2 instance
    t+60s  : ASG activity - Launching a new EC2 instance
    t+90s  : New instance state running
    t+150s : Userdata complete, httpd listening
    t+180s : Target state initial then healthy
    t+185s : Desired capacity restored, 2 healthy targets
```

---

## 9. Audit findings map

What `ha_audit.py` reads and what it can conclude.

```mermaid
flowchart LR
    subgraph API["boto3 API calls"]
        A1["autoscaling:<br/>DescribeAutoScalingGroups"]
        A2["autoscaling:<br/>DescribePolicies"]
        A3["ec2:<br/>DescribeLaunchTemplateVersions"]
        A4["elbv2:<br/>DescribeLoadBalancers"]
        A5["elbv2:<br/>DescribeListeners"]
        A6["elbv2:<br/>DescribeTargetGroups<br/>DescribeTargetHealth"]
    end

    subgraph CHK["Checks"]
        C1["ASG-001 capacity sanity"]
        C2["ASG-002 single-AZ ASG"]
        C3["ASG-003 EC2 health check type"]
        C4["ASG-004 grace period"]
        C5["ASG-005 no scaling policies"]
        C6["ASG-006 unhealthy targets"]
        C7["ASG-007 zero healthy targets"]
        C8["ASG-008 no HTTPS listener"]
        C9["ASG-009 HTTP not redirecting"]
        C10["ASG-010 NLB cross-zone off"]
        C11["ASG-011 IMDSv1 allowed"]
        C12["ASG-012 unencrypted root EBS"]
        C13["ASG-013 no termination diversity"]
        C14["ASG-014 instances not spread"]
    end

    A1 --> C1
    A1 --> C2
    A1 --> C3
    A1 --> C4
    A1 --> C13
    A1 --> C14
    A2 --> C5
    A3 --> C11
    A3 --> C12
    A4 --> C10
    A5 --> C8
    A5 --> C9
    A6 --> C6
    A6 --> C7

    CHK --> SCORE["Severity-weighted score<br/>CRITICAL 25 / HIGH 10<br/>MEDIUM 4 / LOW 1 / INFO 0"]
```

---

*CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp · Learning Made Simple*
