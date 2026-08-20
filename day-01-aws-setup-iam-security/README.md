# Day 01 — AWS Environment Setup & IAM Security

`Intermediate` · `Terraform + Python + AI` · `Hands-On Lab` · **3–3.5 hours**

---

## The enterprise scenario

> A brand-new enterprise AWS account must be locked down **before any workload lands** — identity,
> billing guardrails and least-privilege access from hour one.

You've just been handed the keys to a fresh AWS account. Nothing is deployed yet. In two weeks,
forty engineers will be using it. Whatever you set up today becomes the thing everyone inherits,
copies, and never questions again.

Get this wrong and you spend the next two years untangling it. That's why Day 1 is identity and
money, not servers.

---

## Learning objectives

By the end of today you will be able to:

1. Explain AWS account, Organization and OU structure, and *why* enterprises use many accounts.
2. Read and write an IAM policy JSON document and predict what it allows or denies.
3. Trace the IAM policy evaluation logic to explain why a request was allowed or denied.
4. Choose correctly between IAM users, groups and roles — and articulate why roles win.
5. Configure AWS CLI named profiles, including role assumption.
6. Put billing guardrails and budgets in place as code.
7. Build a Python (boto3) tool that audits IAM and flags least-privilege violations.

---

## Session plan

| # | Segment | Time |
|---|---|---|
| 1 | Account & organisation structure | 25 min |
| 2 | IAM deep dive — identities, policies, evaluation | 50 min |
| 3 | Least privilege in practice | 25 min |
| 4 | AWS CLI configuration & named profiles | 25 min |
| 5 | Billing protection, budgets & cost guardrails | 25 min |
| ☕ | Break | 10 min |
| 6 | **Hands-on lab** — Python IAM Security Audit Tool | 60 min |
| 7 | Interview drill + wrap-up | 20 min |

---

## Part 1 — AWS account & organisation structure

### 1.1 What an AWS account really is

An AWS account is not a login. It is a **hard isolation boundary**:

- A separate billing container.
- A separate blast radius — a mistake in one account cannot touch another.
- A separate quota pool (service limits are per-account, per-region).
- A separate identity namespace.

That last point is the one people miss. `arn:aws:iam::111122223333:role/Admin` and
`arn:aws:iam::444455556666:role/Admin` are *completely unrelated* despite the identical name.

### 1.2 Anatomy of an ARN

Every single thing in AWS has an ARN. Learn to read it and half of IAM stops being mysterious.

```
arn:aws:iam::123456789012:user/finance/alice
 │   │   │          │           │
 │   │   │          │           └── resource     (path + name)
 │   │   │          └────────────── account ID
 │   │   └───────────────────────── region       (empty for global services: IAM, S3, CloudFront)
 │   └───────────────────────────── service
 └───────────────────────────────── partition    (aws | aws-cn | aws-us-gov)
```

More examples:

```
arn:aws:s3:::my-bucket                          ← S3 bucket (no region, no account)
arn:aws:s3:::my-bucket/reports/2026/*           ← objects inside it
arn:aws:ec2:us-east-1:123456789012:instance/i-0abc123
arn:aws:iam::123456789012:role/cbc-day01-ec2-readonly
arn:aws:iam::aws:policy/ReadOnlyAccess          ← AWS-managed policy (account = "aws")
```

> 🧠 **Remember:** when the account field says `aws`, the policy is AWS-managed. When it says your
> 12-digit ID, it is customer-managed. That distinction matters for the audit tool you'll build.

### 1.3 Why enterprises don't use one account

```mermaid
flowchart TD
    subgraph ORG["AWS Organization"]
        MGMT["🏛️ Management Account<br/>billing + Organizations only<br/>NO workloads"]
    end

    MGMT --> SEC["OU: Security"]
    MGMT --> INF["OU: Infrastructure"]
    MGMT --> WKL["OU: Workloads"]
    MGMT --> SBX["OU: Sandbox"]

    SEC --> LOG["Log Archive Account<br/>CloudTrail · Config"]
    SEC --> AUD["Audit Account<br/>Security Hub · GuardDuty"]
    INF --> NET["Network Account<br/>Transit Gateway · DNS"]
    INF --> SHR["Shared Services<br/>CI/CD · AMIs"]
    WKL --> PRD["Production"]
    WKL --> STG["Staging"]
    WKL --> DEV["Development"]
    SBX --> PLY["Playground<br/>strict SCP + budget"]

    style MGMT fill:#1a44b8,color:#fff
    style PRD fill:#c0392b,color:#fff
    style PLY fill:#27ae60,color:#fff
```

| Concept | Simple explanation |
|---|---|
| **Organization** | The umbrella that groups accounts under one bill and one policy engine. |
| **Management account** | The root of the tree. Pays the bill. Should contain *no workloads* — it's too powerful to risk. |
| **Organizational Unit (OU)** | A folder for accounts. You attach policies to OUs, not individual accounts. |
| **Service Control Policy (SCP)** | A **guardrail**, not a grant. It sets the *maximum* permissions anything in that OU can ever have — including account admins. |
| **Member account** | A normal account that belongs to the Organization. |

**Analogy:** the Organization is an office building. OUs are floors. Accounts are locked rooms.
An SCP is the building's fire code — even if you own a room, you cannot install a gas stove in it.

### 1.4 SCPs vs IAM policies — the one that confuses everybody

An SCP **never grants** anything. It only takes away. Effective permission is the *intersection*:

```mermaid
flowchart LR
    A["SCP<br/>(what's possible in this OU)"] --> C{"AND"}
    B["IAM policy<br/>(what's granted to this identity)"] --> C
    C --> D["✅ Effective permissions"]
    style D fill:#27ae60,color:#fff
```

So if an SCP denies `ec2:*` in the Sandbox OU, then even a user with `AdministratorAccess` in a
sandbox account **cannot launch an EC2 instance.** That is the entire point.

> 🎓 **Bootcamp note:** you almost certainly have a single standalone account, so you won't create
> an Organization today. But you will be asked about this in every AWS interview, so learn the model.

---

## Part 2 — IAM deep dive

### 2.1 The four things IAM lets you create

```mermaid
flowchart TD
    subgraph ID["Identities — the WHO"]
        U["👤 User<br/>a human/app<br/>long-lived credentials"]
        G["👥 Group<br/>a bag of users<br/>cannot log in itself"]
        R["🎭 Role<br/>a hat anyone can wear<br/>temporary credentials"]
    end
    subgraph PERM["Permissions — the WHAT"]
        P["📜 Policy<br/>JSON document<br/>Allow / Deny statements"]
    end
    P -.attached to.-> U
    P -.attached to.-> G
    P -.attached to.-> R
    U -->|member of| G
    U -->|sts:AssumeRole| R
    SVC["⚙️ AWS Service<br/>EC2 / Lambda"] -->|assumes| R
    EXT["🌐 External IdP<br/>Okta / Entra ID"] -->|federates into| R

    style R fill:#1a44b8,color:#fff
    style P fill:#f39c12,color:#fff
```

| Thing | What it is | Credentials | Use it for |
|---|---|---|---|
| **User** | A permanent identity | Password + access keys (**long-lived** ⚠️) | Break-glass admin only. Ideally: nothing. |
| **Group** | A collection of users | None — groups can't authenticate | Assigning the same policies to many people |
| **Role** | A set of permissions any trusted principal can *assume* | Temporary, auto-rotating (15 min–12 hr) | **Everything else.** EC2, Lambda, humans via SSO, cross-account |
| **Policy** | JSON that says allow/deny | n/a | Defining permissions |

**The single most important sentence on this page:**
> Roles beat users because roles hand out credentials that expire. A leaked access key works
> forever; leaked role credentials work for an hour.

### 2.2 Policy anatomy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadCompanyReports",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::company-reports",
        "arn:aws:s3:::company-reports/*"
      ],
      "Condition": {
        "IpAddress": { "aws:SourceIp": "203.0.113.0/24" },
        "Bool":      { "aws:SecureTransport": "true" }
      }
    }
  ]
}
```

| Field | Meaning | Gotcha |
|---|---|---|
| `Version` | Policy language version. **Always `2012-10-17`.** | `2008-10-17` silently disables variables and conditions |
| `Sid` | Statement ID — a label for humans | Optional, but auditors love it |
| `Effect` | `Allow` or `Deny` | Deny always wins |
| `Action` | API calls, `service:Operation` | `s3:Get*` wildcards are allowed |
| `Resource` | Which ARNs it applies to | `"*"` is the red flag your audit tool will hunt |
| `Condition` | Extra requirements | The most under-used security feature in AWS |

**Bucket vs objects — the classic S3 trap:**

```
arn:aws:s3:::company-reports      → the BUCKET  (needed for s3:ListBucket)
arn:aws:s3:::company-reports/*    → the OBJECTS (needed for s3:GetObject)
```

List them both, or your users will get baffling `AccessDenied` errors on one operation but not
the other.

### 2.3 Policy types — where policies attach

```mermaid
flowchart TD
    A["📜 Identity-based policy<br/>attached to user/group/role<br/><i>'Alice can read S3'</i>"]
    B["📜 Resource-based policy<br/>attached to the resource<br/><i>'This bucket allows Alice'</i>"]
    C["🚧 SCP<br/>attached to OU/account<br/>ceiling, never a grant"]
    D["🚧 Permissions boundary<br/>attached to user/role<br/>ceiling for that identity"]
    E["⏱️ Session policy<br/>passed at AssumeRole time<br/>ceiling for that session"]

    style C fill:#e67e22,color:#fff
    style D fill:#e67e22,color:#fff
    style E fill:#e67e22,color:#fff
```

Orange = **guardrail** (limits). White = **grant** (permits). You need at least one grant, and no
guardrail may be blocking it.

### 2.4 Policy evaluation logic — memorise this flow

Every single API call goes through this. When someone asks *"why did I get AccessDenied?"*, this
diagram is the answer.

```mermaid
flowchart TD
    START([API request arrives]) --> DENY{Any explicit<br/>DENY anywhere?}
    DENY -->|Yes| NO["❌ DENIED<br/>explicit deny always wins"]
    DENY -->|No| SCP{Allowed by SCP?}
    SCP -->|No| NO2["❌ DENIED<br/>outside the org guardrail"]
    SCP -->|Yes| BOUND{Within permissions<br/>boundary?}
    BOUND -->|No| NO3["❌ DENIED"]
    BOUND -->|Yes| ALLOW{Any explicit<br/>ALLOW?}
    ALLOW -->|No| NO4["❌ DENIED<br/>implicit deny — the default"]
    ALLOW -->|Yes| YES["✅ ALLOWED"]

    style NO fill:#c0392b,color:#fff
    style NO2 fill:#c0392b,color:#fff
    style NO3 fill:#c0392b,color:#fff
    style NO4 fill:#c0392b,color:#fff
    style YES fill:#27ae60,color:#fff
```

Three rules that cover 95% of real questions:

1. **Default is deny.** No policy mentions you? Denied.
2. **An explicit `Deny` beats every `Allow`.** No exceptions, no ordering tricks.
3. **Guardrails narrow, they never widen.** An SCP cannot give you a permission your IAM policy lacks.

#### Worked example

Alice is in the `Developers` group.

- Group policy: `Allow s3:*` on `*`
- An SCP on her OU: `Deny s3:DeleteBucket` on `*`
- Her permissions boundary: `Allow s3:Get*, s3:List*`

Can Alice run `s3:PutObject`?

| Check | Result |
|---|---|
| Explicit deny? | No (`PutObject` isn't `DeleteBucket`) |
| Allowed by SCP? | Yes |
| Within boundary? | ❌ **No** — the boundary only allows `Get*` and `List*` |

**Answer: DENIED.** The group policy grants it, but the boundary is a ceiling she can't exceed.

Now: can Alice run `s3:DeleteBucket`? Denied at step 1 — explicit deny, game over immediately.

### 2.5 Trust policies — the second policy every role has

A role has **two** policies, and mixing them up is the most common beginner error.

```mermaid
flowchart LR
    P["Principal<br/>(EC2 / Lambda / user / other account)"]
    T["📜 Trust policy<br/><b>WHO can wear this hat?</b>"]
    R["🎭 Role"]
    PERM["📜 Permission policy<br/><b>WHAT can the hat do?</b>"]
    AWS["AWS APIs"]

    P -->|sts:AssumeRole| T --> R --> PERM --> AWS
    style T fill:#8e44ad,color:#fff
    style PERM fill:#f39c12,color:#fff
```

**Trust policy** (who may assume it) — note it uses `Principal`, which identity-based policies never do:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "ec2.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
```

**Permission policy** (what it can do once assumed):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:GetObject"],
    "Resource": "arn:aws:s3:::app-config/*"
  }]
}
```

**Cross-account trust — with the confused-deputy guard:**

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "AWS": "arn:aws:iam::444455556666:root" },
    "Action": "sts:AssumeRole",
    "Condition": {
      "StringEquals": { "sts:ExternalId": "cbc-shared-secret-2026" },
      "Bool":         { "aws:MultiFactorAuthPresent": "true" }
    }
  }]
}
```

> ⚠️ A trust policy naming `"AWS": "arn:aws:iam::123456789012:root"` trusts the **whole account**,
> not just its root user. Anyone in that account with `sts:AssumeRole` permission can get in.
> A trust policy with `"Principal": {"AWS": "*"}` and no conditions is a **critical** finding —
> your audit tool will flag exactly this.

---

## Part 3 — Least privilege in practice

### 3.1 The definition

> Grant **only** the permissions required to perform a task, **only** on the resources required,
> **only** under the conditions required, and **only** for as long as required.

Four dimensions: *action*, *resource*, *condition*, *time*. Most people only think about the first.

### 3.2 Refactoring a bad policy

**❌ What people actually write:**

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "*",
    "Resource": "*"
  }]
}
```

This is `AdministratorAccess` in a trench coat. It can delete your CloudTrail, create an admin
user, and empty your S3 buckets.

**⚠️ Slightly better, still bad:**

```json
{ "Effect": "Allow", "Action": "s3:*", "Resource": "*" }
```

Now it's "only" full control of every bucket in the account — including your Terraform state bucket
and your logs.

**✅ What least privilege looks like:**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadAppConfigObjects",
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::cbc-app-config/prod/*"
    },
    {
      "Sid": "ListOnlyThatPrefix",
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::cbc-app-config",
      "Condition": { "StringLike": { "s3:prefix": "prod/*" } }
    },
    {
      "Sid": "DenyUnencryptedTransport",
      "Effect": "Deny",
      "Action": "s3:*",
      "Resource": "*",
      "Condition": { "Bool": { "aws:SecureTransport": "false" } }
    }
  ]
}
```

Same job. One bucket, one prefix, read-only, TLS-only.

### 3.3 How to actually discover the right permissions

Nobody writes a correct least-privilege policy from memory. The workflow is:

```mermaid
flowchart LR
    A["1. Start broad<br/>in dev only"] --> B["2. Run the workload"]
    B --> C["3. IAM Access Analyzer<br/>reads CloudTrail<br/>generates a policy"]
    C --> D["4. Review + tighten<br/>add conditions"]
    D --> E["5. Apply to prod"]
    E --> F["6. Re-check<br/>Access Advisor<br/>every quarter"]
    F --> D
    style C fill:#1a44b8,color:#fff
```

Two AWS tools do the heavy lifting:

- **IAM Access Analyzer → policy generation:** reads your CloudTrail history and writes the policy
  for the actions actually used.
- **IAM Access Advisor:** shows *last accessed* time per service, per identity. Anything with
  "Not accessed in the tracking period" is a permission you can delete today.

```bash
# Which services has this role actually used?
JOB=$(aws iam generate-service-last-accessed-details \
        --arn arn:aws:iam::123456789012:role/cbc-day01-ec2-readonly \
        --query JobId --output text)
aws iam get-service-last-accessed-details --job-id "$JOB" \
  --query 'ServicesLastAccessed[?TotalAuthenticatedEntities>`0`].[ServiceName,LastAuthenticated]' \
  --output table
```

### 3.4 The Day 1 baseline checklist

| # | Control | Why |
|---|---|---|
| 1 | MFA on root | Root can do anything, including closing the account |
| 2 | No root access keys | There is no legitimate use for them |
| 3 | Strong account password policy | 14+ chars, complexity, 90-day rotation, no reuse |
| 4 | MFA on every human user | Blocks the entire credential-stuffing class of attack |
| 5 | No inline policies | They don't version, don't reuse, and hide from audits |
| 6 | Permissions via groups/roles, never per-user | Otherwise permissions drift within weeks |
| 7 | Access keys rotated < 90 days | The audit tool measures this |
| 8 | Budgets + billing alerts | Cost is a security signal — crypto-miners show up as spend |
| 9 | CloudTrail on in all regions | Day 7, but decide it now |
| 10 | Break-glass admin, MFA'd, sealed | For the day SSO breaks |

---

## Part 4 — AWS CLI configuration & named profiles

### 4.1 Where credentials come from

boto3 and the AWS CLI resolve credentials in a fixed order. **First match wins** — this explains
almost every "but I set the profile!" bug.

```mermaid
flowchart TD
    A["1️⃣ CLI flags<br/>--profile, --region"] --> B["2️⃣ Env vars<br/>AWS_ACCESS_KEY_ID<br/>AWS_PROFILE"]
    B --> C["3️⃣ ~/.aws/credentials"]
    C --> D["4️⃣ ~/.aws/config"]
    D --> E["5️⃣ Container creds<br/>ECS task role"]
    E --> F["6️⃣ Instance Metadata<br/>EC2 instance profile"]
    F --> G["❌ NoCredentialsError"]
    style B fill:#e67e22,color:#fff
    style F fill:#27ae60,color:#fff
```

> 🐛 **Debug tip:** stale `AWS_ACCESS_KEY_ID` env vars beat your profile every time.
> `env | grep AWS` is the first thing to run when credentials misbehave.

### 4.2 The two config files

`~/.aws/credentials` — secrets only:

```ini
[default]
aws_access_key_id     = AKIAIOSFODNN7EXAMPLE
aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY

[bootcamp]
aws_access_key_id     = AKIAI44QH8DHBEXAMPLE
aws_secret_access_key = je7MtGbClwBF/2Zp9Utk/h3yCo8nvbEXAMPLEKEY
```

`~/.aws/config` — everything else. **Note the `profile ` prefix here but not in `credentials`.**

```ini
[default]
region = us-east-1
output = json

[profile bootcamp]
region = us-east-1
output = json

[profile bootcamp-audit]
role_arn        = arn:aws:iam::123456789012:role/cbc-day01-security-audit
source_profile  = bootcamp
mfa_serial      = arn:aws:iam::123456789012:mfa/bootcamp-admin
duration_seconds = 3600
region          = us-east-1
```

`bootcamp-audit` is a **role-assumption profile**. It has no keys of its own — it borrows
`bootcamp`'s keys to call `sts:AssumeRole`, prompts for your MFA code, and caches the temporary
credentials. This is exactly how enterprises work day to day.

### 4.3 Commands you'll use constantly

```bash
# Who am I right now?  ← run this before every destructive command
aws sts get-caller-identity --profile bootcamp

# List configured profiles
aws configure list-profiles

# Show which credential source is winning
aws configure list --profile bootcamp

# Use a profile for a whole shell session
export AWS_PROFILE=bootcamp
export AWS_REGION=us-east-1

# Same call through the assumed role — note the ARN changes to assumed-role/...
aws sts get-caller-identity --profile bootcamp-audit
```

### 4.4 The `--query` flag (JMESPath) — worth ten minutes of your life

The CLI can filter server responses locally. This turns walls of JSON into answers.

```bash
# All IAM users, name + creation date, as a table
aws iam list-users \
  --query 'Users[].[UserName,CreateDate]' --output table

# Only users created before 2025
aws iam list-users \
  --query 'Users[?CreateDate<`2025-01-01`].UserName' --output text

# Every customer-managed policy that grants "*" — the one-liner version of today's lab
aws iam list-policies --scope Local \
  --query 'Policies[].[PolicyName,Arn]' --output table

# Access keys older than you'd like
aws iam list-access-keys --user-name bootcamp-admin \
  --query 'AccessKeyMetadata[].[AccessKeyId,Status,CreateDate]' --output table
```

| Syntax | Meaning |
|---|---|
| `Users[]` | Flatten the list |
| `[?Condition]` | Filter |
| `.[A,B]` | Project these fields |
| `\|[0]` | Take the first element of a sub-result |
| `` `value` `` | A literal (backticks) |

### 4.5 SSO / Identity Center (what your employer probably uses)

```bash
aws configure sso
# SSO start URL : https://my-org.awsapps.com/start
# SSO region    : us-east-1
# → browser opens, you pick account + permission set

aws sso login --profile bootcamp-sso
aws sts get-caller-identity --profile bootcamp-sso
```

No long-lived keys anywhere on the laptop. That's the destination; access keys are the training wheels.

---

## Part 5 — Billing protection & cost guardrails

### 5.1 Why this is a Day 1 security topic

Cost is a **security telemetry signal**. The classic compromise story:

```mermaid
flowchart LR
    A["🔑 Access key<br/>committed to GitHub"] --> B["🤖 Bot scrapes it<br/>within ~60 seconds"]
    B --> C["🖥️ Launches GPU fleets<br/>in every region"]
    C --> D["💸 $40,000 bill<br/>in 72 hours"]
    D --> E["📧 Budget alert<br/>= your only warning"]
    style D fill:#c0392b,color:#fff
    style E fill:#27ae60,color:#fff
```

Nobody watches EC2 dashboards at 3 a.m. Everybody reads an email that says *"you've spent 85% of
your budget on day 4."*

### 5.2 The layers

| Layer | Tool | What it does |
|---|---|---|
| **Detect** | AWS Budgets | Alerts at % of actual or forecasted spend |
| **Detect** | Cost Anomaly Detection | ML baseline, alerts on unusual spikes (Day 9) |
| **Attribute** | Cost allocation tags | Answers *whose* spend is this |
| **Prevent** | SCPs | Block expensive regions/instance families outright |
| **Prevent** | Service Quotas | Cap how many of a thing can exist |
| **React** | Budget Actions | Auto-attach a deny policy when a threshold trips |

### 5.3 Budget thresholds that actually work

| Threshold | Type | Why |
|---|---|---|
| 50% | ACTUAL | Early "am I on track?" ping |
| 80% | ACTUAL | Time to investigate |
| 100% | ACTUAL | Something is wrong |
| 100% | **FORECASTED** | ⭐ The one that saves you — fires *before* you overspend |

Forecasted alerts are the important ones. Actual alerts tell you about money you've already lost.

### 5.4 Cost allocation tags

Tags do nothing for billing until you **activate** them in the Billing console
(`Billing → Cost allocation tags → activate`). Then they appear as grouping dimensions in Cost
Explorer. Activate these on Day 1:

`Project` · `Environment` · `Owner` · `CostCenter` · `Day` · `ManagedBy`

> ⏳ Activated tags only apply to costs incurred **from that point forward**. There is no backfill.
> This is a genuinely painful lesson to learn in month three.

---

## Part 6 — What you're building today

```mermaid
flowchart TB
    subgraph TF["🏗️ Terraform — the guardrails"]
        PP["Account password policy<br/>14 chars · complexity · rotation"]
        GRP["IAM Groups<br/>cbc-day01-developers<br/>cbc-day01-readonly"]
        POL["Customer-managed policies<br/>scoped, least-privilege"]
        ROLE["IAM Role<br/>cbc-day01-security-audit<br/>+ trust policy"]
        BUD["AWS Budget<br/>50/80/100% actual<br/>100% forecast"]
        BAD["😈 cbc-day01-BAD-example-policy<br/>deliberately over-permissive<br/><i>so the audit tool has prey</i>"]
    end

    subgraph PY["🐍 Python — the auditor"]
        AUD["iam_audit.py"]
    end

    TF --> AWS[("AWS Account")]
    AWS -->|boto3 read-only| AUD
    AUD --> R1["📊 Console table"]
    AUD --> R2["📄 JSON report"]
    AUD --> R3["📈 CSV for the SOC"]

    style ROLE fill:#1a44b8,color:#fff
    style BAD fill:#c0392b,color:#fff
    style AUD fill:#f39c12,color:#fff
```

The Terraform builds a small but realistic identity baseline **and one deliberately terrible
policy**, so that when you run the auditor it has something to find. Finding zero problems teaches
you nothing.

---

## 🧪 Hands-On Lab

👉 **[lab/README.md](lab/README.md)** — Python IAM Security Audit Tool

> Build a Python (boto3) tool that audits IAM users, roles and policies and flags least-privilege
> violations.

Two paths:
- 🎯 **Challenge:** [`lab/python/challenge/iam_audit_challenge.py`](lab/python/challenge/iam_audit_challenge.py) — signatures + docstrings, logic is yours
- ✅ **Solution:** [`lab/python/iam_audit.py`](lab/python/iam_audit.py) — full working tool

---

## Common mistakes on Day 1

| Mistake | What happens | Fix |
|---|---|---|
| Using root for daily work | One phish = total account loss | MFA root, lock it in a safe, use IAM/SSO |
| Access keys on a laptop, no rotation | Leaked keys work forever | Roles + SSO; rotate < 90 days |
| Attaching policies directly to users | Permission drift within weeks | Groups and roles only |
| Inline policies | Invisible to audits, no reuse, no versions | Customer-managed policies |
| `"Resource": "*"` everywhere | Blast radius = whole account | Scope to ARNs; add conditions |
| Confusing trust and permission policies | Role exists but nothing can assume it | Trust = *who*, permission = *what* |
| Forgetting the S3 bucket-vs-object ARN pair | `ListBucket` works, `GetObject` doesn't | List both ARNs |
| No budget | Silent five-figure bill | Budget before first resource |
| Building in the wrong region | Resources you can never find | `export AWS_REGION` and check the console dropdown |

---

## Day 1 completion checklist

- [ ] Root user has MFA and no access keys
- [ ] `bootcamp` CLI profile works — `aws sts get-caller-identity` returns your account
- [ ] `bootcamp-audit` role-assumption profile works
- [ ] Terraform applied: password policy, groups, policies, role, budget
- [ ] `iam_audit.py` runs and produces a report
- [ ] The audit tool **found** the deliberately bad policy 🎯
- [ ] You can explain the policy evaluation flow without looking at the diagram
- [ ] You can explain the difference between a trust policy and a permission policy
- [ ] Budget alert email confirmed (check spam)
- [ ] [teardown-checklist.md](teardown-checklist.md) completed

---

## Extras

- 🎤 [interview-qa.md](interview-qa.md) — 15 interview questions with model answers
- 👨‍🏫 [trainer-notes.md](trainer-notes.md) — delivery plan, timings, live-demo scripts
- 🧹 [teardown-checklist.md](teardown-checklist.md) — leave nothing running

## Further reading

- [IAM policy evaluation logic](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic.html)
- [IAM security best practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
- [AWS Organizations best practices](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_best-practices.html)
- [Well-Architected — Security Pillar](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/welcome.html)

---

**Next:** Day 02 — Enterprise Networking & Security Architecture
