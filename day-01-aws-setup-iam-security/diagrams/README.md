# Day 01 Diagrams

Mermaid source for every diagram used in the Day 01 guide. GitHub renders these natively.
Copy any block into <https://mermaid.live> to edit, or export as PNG/SVG for slides.

---

## 1. Multi-account organisation structure

```mermaid
flowchart TD
    subgraph ORG["AWS Organization"]
        MGMT["🏛️ Management Account<br/>billing + Organizations only<br/>NO workloads"]
    end
    MGMT --> SEC["OU: Security"]
    MGMT --> INF["OU: Infrastructure"]
    MGMT --> WKL["OU: Workloads"]
    MGMT --> SBX["OU: Sandbox"]
    SEC --> LOG["Log Archive Account"]
    SEC --> AUD["Audit Account"]
    INF --> NET["Network Account"]
    INF --> SHR["Shared Services"]
    WKL --> PRD["Production"]
    WKL --> STG["Staging"]
    WKL --> DEV["Development"]
    SBX --> PLY["Playground"]
    style MGMT fill:#1a44b8,color:#fff
    style PRD fill:#c0392b,color:#fff
    style PLY fill:#27ae60,color:#fff
```

## 2. IAM identities and policies

```mermaid
flowchart TD
    subgraph ID["Identities — the WHO"]
        U["👤 User"]
        G["👥 Group"]
        R["🎭 Role"]
    end
    subgraph PERM["Permissions — the WHAT"]
        P["📜 Policy"]
    end
    P -.attached to.-> U
    P -.attached to.-> G
    P -.attached to.-> R
    U -->|member of| G
    U -->|sts:AssumeRole| R
    SVC["⚙️ AWS Service"] -->|assumes| R
    EXT["🌐 External IdP"] -->|federates into| R
    style R fill:#1a44b8,color:#fff
    style P fill:#f39c12,color:#fff
```

## 3. Policy evaluation logic ⭐

```mermaid
flowchart TD
    START([API request arrives]) --> DENY{Any explicit<br/>DENY anywhere?}
    DENY -->|Yes| NO["❌ DENIED"]
    DENY -->|No| SCP{Allowed by SCP?}
    SCP -->|No| NO2["❌ DENIED"]
    SCP -->|Yes| BOUND{Within permissions<br/>boundary?}
    BOUND -->|No| NO3["❌ DENIED"]
    BOUND -->|Yes| ALLOW{Any explicit<br/>ALLOW?}
    ALLOW -->|No| NO4["❌ DENIED<br/>implicit deny"]
    ALLOW -->|Yes| YES["✅ ALLOWED"]
    style NO fill:#c0392b,color:#fff
    style NO2 fill:#c0392b,color:#fff
    style NO3 fill:#c0392b,color:#fff
    style NO4 fill:#c0392b,color:#fff
    style YES fill:#27ae60,color:#fff
```

## 4. Trust policy vs permission policy

```mermaid
flowchart LR
    P["Principal"] -->|sts:AssumeRole| T["📜 Trust policy<br/>WHO can wear the hat?"]
    T --> R["🎭 Role"] --> PERM["📜 Permission policy<br/>WHAT can the hat do?"]
    PERM --> AWS["AWS APIs"]
    style T fill:#8e44ad,color:#fff
    style PERM fill:#f39c12,color:#fff
```

## 5. Credential resolution chain

```mermaid
flowchart TD
    A["1️⃣ CLI flags"] --> B["2️⃣ Env vars"]
    B --> C["3️⃣ ~/.aws/credentials"]
    C --> D["4️⃣ ~/.aws/config"]
    D --> E["5️⃣ Container creds"]
    E --> F["6️⃣ EC2 instance metadata"]
    F --> G["❌ NoCredentialsError"]
    style B fill:#e67e22,color:#fff
    style F fill:#27ae60,color:#fff
```

## 6. Least-privilege discovery loop

```mermaid
flowchart LR
    A["1. Start broad<br/>dev only"] --> B["2. Run the workload"]
    B --> C["3. Access Analyzer<br/>reads CloudTrail"]
    C --> D["4. Review + tighten"]
    D --> E["5. Apply to prod"]
    E --> F["6. Access Advisor<br/>quarterly"]
    F --> D
    style C fill:#1a44b8,color:#fff
```

## 7. Lab architecture

```mermaid
flowchart TB
    subgraph TF["🏗️ Terraform"]
        PP["Password policy"]
        GRP["IAM Groups"]
        POL["Scoped policies"]
        ROLE["Audit role"]
        BUD["Budget + alerts"]
        BAD["😈 BAD policy"]
    end
    subgraph PY["🐍 Python"]
        AUD["iam_audit.py"]
    end
    TF --> AWS[("AWS Account")]
    AWS -->|boto3 read-only| AUD
    AUD --> R1["📊 Table"]
    AUD --> R2["📄 JSON"]
    AUD --> R3["📈 CSV"]
    style ROLE fill:#1a44b8,color:#fff
    style BAD fill:#c0392b,color:#fff
    style AUD fill:#f39c12,color:#fff
```

## 8. Leaked credential attack chain

```mermaid
flowchart LR
    A["🔑 Key committed<br/>to GitHub"] --> B["🤖 Scraped<br/>in ~60s"]
    B --> C["🖥️ GPU fleets<br/>every region"]
    C --> D["💸 $40,000<br/>in 72 hours"]
    D --> E["📧 Budget alert<br/>= your only warning"]
    style D fill:#c0392b,color:#fff
    style E fill:#27ae60,color:#fff
```
