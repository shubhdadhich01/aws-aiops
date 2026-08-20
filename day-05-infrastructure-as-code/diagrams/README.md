# Day 05 — Architecture Diagrams

**CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp**

Every diagram for Day 05 in one place. Mermaid renders natively on GitHub and
GitLab, and in VS Code with the Markdown Preview Mermaid extension.

The ones you will actually be asked to draw at a whiteboard are **2** (state
and locking), **5** (the bootstrap chicken-and-egg) and **7** (drift, and the
three fixes). Practise those three until you can do them from memory.

---

## Contents

1. [Target architecture](#1-target-architecture)
2. [State, locking and the .tflock object](#2-state-locking-and-the-tflock-object)
3. [What a plan actually compares](#3-what-a-plan-actually-compares)
4. [Module composition — one set of modules, two environments](#4-module-composition--one-set-of-modules-two-environments)
5. [The bootstrap chicken-and-egg](#5-the-bootstrap-chicken-and-egg)
6. [count vs for_each addressing](#6-count-vs-for_each-addressing)
7. [Drift, and the three fixes](#7-drift-and-the-three-fixes)
8. [Variable precedence](#8-variable-precedence)
9. [CI/CD pipeline with OIDC](#9-cicd-pipeline-with-oidc)
10. [Audit findings map](#10-audit-findings-map)
11. [Teardown order and the prevent_destroy wall](#11-teardown-order-and-the-prevent_destroy-wall)

---

## 1. Target architecture

Three modules written once, two environments composed from them, one backend
holding both state files, and a directory of deliberate faults that is never
applied to anything.

```mermaid
flowchart TB
    subgraph BOOT["backend-bootstrap/ — LOCAL state, on purpose"]
        SB[("S3 state bucket<br/>versioned · encrypted · PAB<br/>prevent_destroy · TLS-only policy")]
        KMS["KMS CMK<br/>optional · $1.00/mo<br/>off by default"]
        KMS -.->|"encrypts"| SB
    end

    subgraph MODULES["modules/ — no provider blocks anywhere"]
        NET["network<br/>VPC · subnets by AZ (for_each)<br/>IGW · routing · optional NAT · app SG"]
        CMP["compute<br/>EC2 by for_each · SSM profile<br/>IMDSv2 required · ignore_changes = [ami]"]
        STO["storage<br/>S3 + optional DynamoDB<br/>both prevent_destroy · lifecycle rules"]
    end

    subgraph DEV["envs/dev/ — remote state, key day-05/dev/"]
        DN["module.network"]
        DC["module.compute"]
        DS["module.storage"]
        DBAD["aws_s3_bucket.insecure_state_example<br/>no versioning · no encryption<br/>PAB IS present"]
        DRIFT["aws_cloudwatch_log_group.drift_target<br/>CostCentre = engineering"]
    end

    subgraph PROD["envs/prod/ — remote state, key day-05/prod/"]
        PGATE{"enable_prod_environment<br/>false by default"}
        PN["module.network"]
        PC["module.compute"]
        PS["module.storage"]
    end

    BADEX["bad-examples/<br/>━━━━━━━━━━━━━<br/>APPLIED BY NOTHING<br/>no backend · no env references it<br/>exists to be PARSED"]

    SB -.->|"holds state for"| DEV
    SB -.->|"holds state for"| PROD

    NET --> DN
    CMP --> DC
    STO --> DS
    NET --> PN
    CMP --> PC
    STO --> PS
    PGATE -->|"true"| PN

    style BOOT fill:#fff4e6,stroke:#d9822b,stroke-width:2px
    style BADEX fill:#ffe6e6,stroke:#cc0000,stroke-width:2px
    style DBAD fill:#ffe6e6,stroke:#cc0000
    style PROD fill:#f5f5f5,stroke:#999,stroke-dasharray: 5 5
```

The dotted arrows from the state bucket are the ones people draw wrong. The
bootstrap does not *create* anything in dev or prod — it holds the file that
records what dev and prod are.

---

## 2. State, locking and the `.tflock` object

State solves sharing. Locking solves concurrency. **They are separate
problems**, and a shared bucket with no locking is arguably worse than local
state, because now two people can corrupt one file instead of each corrupting
their own.

```mermaid
sequenceDiagram
    participant A as Engineer A
    participant B as Engineer B
    participant S3 as S3 bucket
    participant AWS

    A->>S3: PutObject terraform.tfstate.tflock<br/>(conditional put — fails if it exists)
    S3-->>A: 200 — lock acquired
    A->>S3: GetObject terraform.tfstate
    A->>AWS: refresh · plan · apply

    B->>S3: PutObject terraform.tfstate.tflock
    S3-->>B: 412 PreconditionFailed
    Note over B: Error: Error acquiring the state lock<br/>Lock Info: ID, Who, Created, Operation<br/>B waits. Nothing is corrupted.

    AWS-->>A: resources created
    A->>S3: PutObject terraform.tfstate (new serial)
    A->>S3: DeleteObject terraform.tfstate.tflock
    Note over S3: lock released

    B->>S3: PutObject terraform.tfstate.tflock
    S3-->>B: 200 — lock acquired
    Note over B: B now reads A's state,<br/>not a stale copy of it
```

**`use_lockfile = true`. Requires AWS provider ≥ 5.80 and Terraform ≥ 1.10.**

For about a decade this was a DynamoDB table with a `LockID` hash key. That
argument (`dynamodb_table`) is now deprecated. Every repository that used it
carries a ~$0.25/month table that outlives the project by years. You will meet
these — do not build new ones.

If a run is killed mid-apply the lock object survives, and the next run reports
it with the ID, the user and the timestamp. `terraform force-unlock <ID>` is
the escape hatch. Read the lock info first: if the operation is still running
somewhere, force-unlocking it is how two applies end up interleaved.

---

## 3. What a plan actually compares

Three inputs, not two. The refresh is the step people forget, and it is the
one that makes drift visible.

```mermaid
flowchart LR
    CFG["CONFIGURATION<br/>the .tf files<br/><i>what you want</i>"]
    AWSR["AWS API<br/><i>what is really there</i>"]
    ST["STATE<br/>the map between them<br/><i>what Terraform believes</i>"]

    AWSR -->|"1. refresh"| ST
    CFG --> DIFF{"2. diff"}
    ST --> DIFF
    DIFF --> PLAN["PLAN<br/>+ create<br/>~ update in place<br/>-/+ destroy and recreate<br/>- destroy"]

    style AWSR fill:#fff4e6,stroke:#d9822b
    style PLAN fill:#e6f0ff,stroke:#0066cc
```

Read the symbols carefully. **`-/+` is destroy-and-recreate**, and on a
stateful resource it means data loss. It is the one that gets skimmed past at
the bottom of a 400-line plan at 5pm.

`terraform plan -refresh-only` performs step 1 and stops. `terraform plan
-refresh=false` skips step 1 entirely — faster, and blind to drift.

---

## 4. Module composition — one set of modules, two environments

The whole point of the day: dev and prod differ in **tfvars**, not in code.

```mermaid
flowchart TB
    subgraph SRC["modules/ — written once, no provider blocks"]
        NET["network"]
        CMP["compute"]
        STO["storage"]
    end

    subgraph DEVV["envs/dev/terraform.tfvars"]
        D1["enable_nat_gateway = false"]
        D2["instances = {}"]
        D3["force_destroy = true"]
        D4["log_retention_days = 7"]
        D5["create_insecure_examples = true"]
    end

    subgraph PRODV["envs/prod/terraform.tfvars"]
        P1["enable_nat_gateway = true"]
        P2["instances = { app = ... }"]
        P3["force_destroy = false"]
        P4["log_retention_days = 30"]
        P5["enable_point_in_time_recovery = true"]
    end

    SRC --> DEVV
    SRC --> PRODV
    DEVV --> DEVR["dev: ~$0.02/month"]
    PRODV --> PRODR["prod: ~$40+/month"]

    style SRC fill:#e6f0ff,stroke:#0066cc,stroke-width:2px
```

The asymmetry between those two columns living in **two tfvars files** rather
than in ternaries scattered through shared code is the entire argument for
directory-per-environment over workspaces.

The dependency graph inside one environment is built from references, not from
`depends_on`:

```mermaid
flowchart LR
    N["module.network"] -->|"public_subnet_ids"| C["module.compute"]
    N -->|"app_security_group_id"| C
    S["module.storage"]
    N -.->|"no reference =<br/>no edge = parallel"| S

    style N fill:#e6f0ff,stroke:#0066cc
```

`module.storage` has no dependency on the network, so Terraform builds them at
the same time. Adding `depends_on` there would serialise the apply for nothing.

---

## 5. The bootstrap chicken-and-egg

The backend cannot create itself. This is the diagram to draw when somebody
asks how you set up remote state in the first place.

```mermaid
flowchart TB
    Q{"Where does the state<br/>for the state bucket live?"}
    Q --> A["In the bucket itself?<br/>It does not exist yet."]
    Q --> B["Somewhere else?<br/>Same problem, one level up."]

    A --> SOL
    B --> SOL

    SOL["backend-bootstrap/ runs on LOCAL state<br/>━━━━━━━━━━━━━━━━━━━━<br/># iac-audit: allow-local-state<br/>━━━━━━━━━━━━━━━━━━━━<br/>declared in code, next to the thing<br/>being suppressed, where the next<br/>reviewer of this file will see it"]

    SOL --> CREATE["creates the S3 state bucket:<br/>versioning · SSE · PAB<br/>ownership controls · TLS-only policy<br/>prevent_destroy"]
    CREATE --> USE["envs/dev and envs/prod<br/>point their backend at it"]

    USE --> THEN{"and the bootstrap's<br/>own state file?"}
    THEN --> O1["1. Commit it, encrypted<br/>(git-crypt / SOPS)<br/>small, auditable, recoverable"]
    THEN --> O2["2. Migrate it into the bucket<br/>it just created<br/>elegant · circular on destroy"]
    THEN --> O3["3. Leave it on one laptop<br/>what most teams do<br/>why nobody dares touch it later"]

    style SOL fill:#fff4e6,stroke:#d9822b,stroke-width:2px
    style O3 fill:#ffe6e6,stroke:#cc0000
```

Option 3 is not wrong because it is option 3. It is wrong when it happens
without anybody deciding it.

---

## 6. `count` vs `for_each` addressing

The diagram that explains why `for_each` is almost always correct.

```mermaid
flowchart TB
    subgraph CNT["count = length(var.names) — addressed by POSITION"]
        C0["reports[0] = alpha"]
        C1["reports[1] = beta"]
        C2["reports[2] = gamma"]
    end

    subgraph CNT2["remove alpha from the list"]
        N0["reports[0] = beta &nbsp;&nbsp;~ RENAMED"]
        N1["reports[1] = gamma ~ RENAMED"]
        N2["reports[2] &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;- DESTROYED"]
    end

    CNT --> CNT2
    CNT2 --> BAD["-/+ 3 resources replaced<br/>including the two you never touched<br/>for an S3 bucket: the data is gone"]

    subgraph FE["for_each = toset(var.names) — addressed by KEY"]
        F0["reports[&quot;alpha&quot;]"]
        F1["reports[&quot;beta&quot;]"]
        F2["reports[&quot;gamma&quot;]"]
    end

    subgraph FE2["remove alpha from the set"]
        G0["reports[&quot;alpha&quot;] - DESTROYED"]
        G1["reports[&quot;beta&quot;] &nbsp;&nbsp;unchanged"]
        G2["reports[&quot;gamma&quot;] unchanged"]
    end

    FE --> FE2
    FE2 --> GOOD["- 1 resource destroyed<br/>nothing else in the plan moves"]

    style BAD fill:#ffe6e6,stroke:#cc0000,stroke-width:2px
    style GOOD fill:#e6ffe6,stroke:#00994d,stroke-width:2px
```

**`count` is correct for exactly one shape:** `count = var.enabled ? 1 : 0`.
Sixteen `count` expressions in this lab; fifteen are that shape; the sixteenth
is in `bad-examples/` and fires IAC-016.

Migrating an existing `count` to `for_each` without destroying anything:

```mermaid
flowchart LR
    OLD["aws_s3_bucket.reports[0]"] -->|"moved { } block<br/>or terraform state mv"| NEW["aws_s3_bucket.reports[&quot;alpha&quot;]"]
    style NEW fill:#e6ffe6,stroke:#00994d
```

Prefer the `moved` block. It lives in code, appears in the pull request, and
every environment picks it up. `state mv` happens on one machine, once, and is
remembered by nobody.

---

## 7. Drift, and the three fixes

**The diagram to be able to draw from memory.**

```mermaid
sequenceDiagram
    participant H as Human
    participant C as AWS Console
    participant AWS
    participant T as terraform plan
    participant S as State (S3)

    Note over H,S: Friday 16:40. A good reason. No ticket.
    H->>C: CostCentre: engineering → finance
    C->>AWS: TagResource
    Note over AWS: reality has moved away<br/>from the configuration

    Note over H,S: Monday
    H->>T: terraform plan
    T->>S: acquire lock, read state
    T->>AWS: refresh every managed resource
    AWS-->>T: CostCentre = finance
    T->>S: update state to match REALITY
    T->>T: diff state against CONFIGURATION
    T-->>H: ~ tags.CostCentre: "finance" → "engineering"
```

Three correct answers follow, and picking one accidentally is the only wrong
one:

```mermaid
flowchart TB
    D["drift detected"] --> Q{"who is right?"}

    Q -->|"the code is right —<br/>the change was<br/>unauthorised or a mistake"| A1["terraform apply<br/>━━━━━━━━<br/>code wins, AWS reconciled<br/><b>the default answer</b>"]

    Q -->|"reality is right —<br/>the change was correct<br/>and the code is now wrong"| A2["terraform plan -refresh-only<br/>then apply<br/>━━━━━━━━<br/>state updated to match<br/><b>then change the code and commit,<br/>or the next apply reverts you</b>"]

    Q -->|"something else<br/>legitimately owns<br/>this attribute"| A3["lifecycle · ignore_changes<br/>on tags[&quot;CostCentre&quot;]<br/>━━━━━━━━<br/>stop diffing it<br/>autoscaler · deploy pipeline<br/>tagging Lambda"]

    A1 --> R["if the same drift recurs,<br/>the console access that allows it<br/>is the actual finding"]
    A2 --> R
    A3 --> R

    style A1 fill:#e6ffe6,stroke:#00994d
    style A2 fill:#fff4e6,stroke:#d9822b
    style A3 fill:#e6f0ff,stroke:#0066cc
    style R fill:#f5f5f5,stroke:#666
```

`-refresh-only` is **not** a fix on its own. It stops Terraform complaining
without making the code true, and the next person to run `apply` gets the
surprise you deferred.

Drift detection is **not a background service**. Nothing watches your account.
It is a plan, and it only happens when somebody runs one — which is the
argument for a scheduled `plan -detailed-exitcode` in CI, alerting on exit
code 2.

---

## 8. Variable precedence

Later beats earlier. The one that surprises people is that `-var` beats a
`terraform.tfvars` sitting right there.

```mermaid
flowchart LR
    D["variable default<br/><i>used if nothing else sets it</i>"] --> E["TF_VAR_name<br/>environment variables"]
    E --> T["terraform.tfvars"]
    T --> J["terraform.tfvars.json"]
    J --> A["*.auto.tfvars<br/><i>lexical order</i>"]
    A --> C["-var / -var-file<br/><i>in the order given</i>"]
    C --> W["WINS"]

    style D fill:#f5f5f5,stroke:#999
    style W fill:#e6ffe6,stroke:#00994d,stroke-width:2px
```

That last hop is how CI overrides a developer's file — and how somebody
applies dev's values to prod with a copy-pasted command.

Note what the backend block does **not** take part in: it is read before any
variable exists, so `bucket = var.state_bucket` is a hard error. Use
`-backend-config` instead.

---

## 9. CI/CD pipeline with OIDC

```mermaid
flowchart TB
    PR["pull request opened"] --> FMT["terraform fmt -check -recursive<br/>terraform validate"]
    FMT --> OIDC1["assume READ-ONLY role<br/>via GitHub OIDC<br/>no long-lived key exists"]
    OIDC1 --> PLAN["terraform plan -detailed-exitcode -out=tfplan"]
    PLAN --> AUD["python3 iac_audit.py --path . --fail-on CRITICAL<br/><i>12 checks need no credentials at all</i>"]
    AUD --> CMT["post the plan as a PR comment"]
    CMT --> REV{"human review"}
    REV -->|"changes requested"| PR
    REV -->|"merge"| OIDC2["assume WRITE role<br/>environment approval required"]
    OIDC2 --> APPLY["terraform apply tfplan<br/><i>exactly what was reviewed</i>"]

    SCHED["scheduled: nightly"] --> DRIFT["terraform plan -detailed-exitcode"]
    DRIFT --> EX{"exit code"}
    EX -->|"0"| OK["no drift"]
    EX -->|"2"| ALERT["drift — alert"]
    EX -->|"1"| ERR["pipeline error"]

    style AUD fill:#e6f0ff,stroke:#0066cc
    style APPLY fill:#e6ffe6,stroke:#00994d
    style ALERT fill:#fff4e6,stroke:#d9822b
    style OIDC1 fill:#f0e6ff,stroke:#6600cc
    style OIDC2 fill:#f0e6ff,stroke:#6600cc
```

Three things this diagram is arguing for:

- **`apply tfplan`, not `apply`.** Bare `apply` re-plans and applies whatever
  is true *now*, not what the reviewer approved. Between review and merge,
  somebody else's change landed.
- **Two roles, not one.** Plan reads; apply writes. There is no reason a pull
  request from a fork should be able to assume a role that can write.
- **`-detailed-exitcode` returns 2 for "changes pending".** A naive
  `if [ $? -ne 0 ]` treats every pending change as a pipeline failure.

Plan files contain **every value in the diff, including sensitive ones**, in
the clear. Treat a stored plan artifact like the state file: encrypted,
short-retention, access-controlled.

---

## 10. Audit findings map

Where each of the 16 checks finds its evidence — and where two of them
deliberately find nothing.

```mermaid
flowchart LR
    subgraph BAD["bad-examples/ — parsed, never applied"]
        S1["secrets.tf → IAC-001"]
        S2["providers.tf → IAC-002, 005, 010, 011"]
        S3["outputs.tf → IAC-008"]
        S4["resources.tf → IAC-009, 013, 014 ×2, 016"]
        S5[".gitignore → IAC-012"]
        S6["variables.tf → IAC-016"]
    end

    subgraph LIVE["envs/dev — applied, needs credentials"]
        L1["insecure_state_example bucket<br/>no versioning → IAC-006<br/>no encryption → IAC-007"]
        L2["drift_target log group<br/>→ IAC-015 after Step 6 only"]
    end

    subgraph NONE["silent by design — nothing to find"]
        N1["IAC-003 — no committed .tfstate<br/>anywhere, every .gitignore covers it"]
        N2["IAC-004 — this repo ships no<br/>public bucket, not even as an example"]
    end

    BAD --> T1["13 findings<br/>weights 106<br/>score 0/100"]
    L1 --> T2["+2 → 15 findings<br/>weights 126<br/>score 0/100"]
    L2 --> T3["+1 → 16 findings<br/>weights 130<br/>score 0/100"]
    T1 --> T2 --> T3

    style BAD fill:#ffe6e6,stroke:#cc0000
    style NONE fill:#e6ffe6,stroke:#00994d
    style LIVE fill:#fff4e6,stroke:#d9822b
```

**IAC-015 is silent for a different reason from IAC-003 and IAC-004.** Those
two are silent *by design* — the fault is not present because shipping it
would be irresponsible. IAC-015 is silent *by situation*: drift does not exist
on a fresh apply, by definition, and Step 6 makes it fire on purpose.

Point the same tool at `envs/dev/` on its own and it scores **100/100**. Same
tool, same checks, a directory somebody wrote carefully. That contrast is the
demonstration.

---

## 11. Teardown order and the `prevent_destroy` wall

`terraform destroy` **will fail** on this day. That is the seatbelt working.

```mermaid
flowchart TB
    START["teardown"] --> ORDER{"correct order"}
    ORDER --> P["1. envs/prod &nbsp;&nbsp;(if you enabled it)"]
    P --> D["2. envs/dev"]
    D --> B["3. backend-bootstrap &nbsp;&nbsp;LAST"]

    D --> WALL["Error: Instance cannot be destroyed<br/>Resource has lifecycle.prevent_destroy set"]

    WALL --> CHOICE{"handle it<br/>deliberately"}
    CHOICE -->|"you want the data gone"| R1["1. remove the lifecycle block<br/>2. terraform apply<br/>3. terraform destroy<br/><i>three steps, in daylight</i>"]
    CHOICE -->|"you want to keep it"| R2["terraform state rm ADDR<br/>then delete out of band<br/><i>Terraform forgets; AWS keeps billing</i>"]

    CHOICE -->|"❌"| R3["delete the lifecycle block<br/>because destroy keeps failing<br/>and you want it to stop"]
    R3 --> LOSS["how production buckets<br/>go missing"]

    B --> EMPTY["empty the VERSIONED state bucket:<br/>versions AND delete markers<br/><i>aws s3 rm does not remove either</i>"]

    style WALL fill:#fff4e6,stroke:#d9822b,stroke-width:2px
    style R3 fill:#ffe6e6,stroke:#cc0000,stroke-width:2px
    style LOSS fill:#ffe6e6,stroke:#cc0000,stroke-width:2px
    style B fill:#e6f0ff,stroke:#0066cc
```

**Order matters and it is not arbitrary.** Destroy the bootstrap first and you
have deleted the bucket holding dev's and prod's state — those environments
still exist in AWS and Terraform can no longer see them. Recovery is
`terraform import`, one resource at a time.

**Four resources carry `prevent_destroy`** across this lab: the bootstrap state
bucket, the dev data bucket, the dev insecure example bucket, and the prod data
bucket. Each one will stop a destroy, and each one is supposed to.

Emptying a versioned bucket needs both passes — `aws s3 rm --recursive` removes
neither non-current versions nor delete markers. Full sequence with a
verification script: [`../teardown-checklist.md`](../teardown-checklist.md).

---

**See also:** the day guide [`../README.md`](../README.md), the step-by-step
[`../lab/README.md`](../lab/README.md), and
[`../interview-qa.md`](../interview-qa.md) for the questions these diagrams
answer.
