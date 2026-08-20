# Interview Q&A — Day 01: AWS Setup & IAM Security

15 questions that actually get asked, with the answers interviewers are listening for.

> 💡 **Meta-tip:** for every IAM question, the strongest answers name a *trade-off* and a
> *real experience*. "We used roles instead of users because rotating 40 access keys across three
> teams had already caused two outages" beats any textbook definition.

---

### 1. What's the difference between an IAM user, group and role?

A **user** is a permanent identity with long-lived credentials. A **group** is a container for
users that carries policies — it has no credentials and cannot authenticate. A **role** is a set
of permissions that a trusted principal *assumes*, receiving temporary credentials from STS.

The answer they want: **roles are preferred for almost everything**, because credentials expire.
A leaked access key works until someone notices; leaked role credentials work for an hour.

Users are for break-glass access and legacy systems that can't federate. Everything else — EC2,
Lambda, humans via Identity Center, cross-account access, CI/CD — should use roles.

---

### 2. Walk me through IAM policy evaluation. Why might a request be denied?

Order of evaluation:

1. **Explicit `Deny`** anywhere (identity policy, resource policy, SCP, boundary, session policy) → denied. Always. First and final.
2. **SCP** — if the OU's guardrail doesn't permit the action → denied.
3. **Permissions boundary** — if set and doesn't permit → denied.
4. **Explicit `Allow`** in an identity-based or resource-based policy → allowed.
5. Otherwise **implicit deny** — the default for everything.

The three sentences that show mastery:
- Default is deny.
- Explicit deny always wins, regardless of ordering.
- Guardrails (SCPs, boundaries) can only *narrow* — they never grant.

---

### 3. What's the difference between an SCP and an IAM policy?

An IAM policy **grants**. An SCP **limits**. An SCP by itself gives nobody any permission — it
defines the maximum any principal in that OU or account could ever have.

Effective permissions = SCP ∩ IAM policy.

Consequence people forget: an SCP applies to account root and administrators too. That's exactly
why it's useful — it's the only control an account admin can't remove.

---

### 4. Explain the difference between a trust policy and a permission policy.

Every role has both.

- **Trust policy** (`AssumeRolePolicyDocument`): *who* may assume this role. It's the only policy
  type with a `Principal` field.
- **Permission policy**: *what* the role can do once assumed.

Classic failure: the permission policy is perfect but the trust policy doesn't name your principal,
so `sts:AssumeRole` returns `AccessDenied` and the permissions never come into play.

---

### 5. What is the confused deputy problem and how do you prevent it?

A third party (say a monitoring SaaS) has a role in your account. Another of their customers tricks
them into using your ARN, and the SaaS — the "deputy" — accesses your resources on the attacker's
behalf. It never authenticated as the attacker; it was just confused about who it was acting for.

Prevention: require an **`sts:ExternalId`** condition in the trust policy, using a value you and
the vendor agree out-of-band. For AWS services, use `aws:SourceArn` and `aws:SourceAccount`.

---

### 6. How do you implement least privilege in practice?

Not by guessing. The workflow:

1. Start permissive **in dev only**.
2. Run the real workload; CloudTrail records every API call.
3. **IAM Access Analyzer → policy generation** reads CloudTrail and writes a policy for exactly the
   actions used.
4. Review and tighten: scope resources to ARNs, add conditions (`aws:ResourceTag`,
   `aws:RequestedRegion`, `aws:PrincipalOrgID`, `aws:SecureTransport`).
5. Promote to prod.
6. Re-check quarterly with **Access Advisor** — anything "not accessed in the tracking period" gets deleted.

Least privilege has four dimensions, not one: **action, resource, condition, time**.

---

### 7. What's wrong with `{"Effect":"Allow","Action":"*","Resource":"*"}`?

It's `AdministratorAccess`. It can delete your CloudTrail, create new admin users, disable
GuardDuty, and empty your S3 buckets. There's no blast-radius containment: one compromised
credential equals total account compromise.

Also worth saying: **even unattached, it's a finding.** It sits there until someone attaches it
"just to unblock a deploy."

---

### 8. How do you secure the root user?

- Enable MFA (hardware token for production accounts).
- Delete all root access keys — there is no legitimate use for them.
- Use a shared mailbox, not an individual's email, so the account survives that person leaving.
- Store the password in a corporate vault with break-glass procedure and audit logging.
- Alarm on root usage: CloudTrail → EventBridge → SNS.
- Enable IAM access to billing so nobody *needs* root for day-to-day work.

Only a handful of tasks genuinely require root: closing the account, changing the support plan,
some S3/SQS resource policy edge cases, and restoring IAM access if you lock yourself out.

---

### 9. How do you give an EC2 instance access to S3?

Create a role with a trust policy for `ec2.amazonaws.com`, attach a scoped S3 permission policy,
and attach the role to the instance via an **instance profile**. The SDK picks up credentials from
the Instance Metadata Service (IMDS) automatically.

Never put access keys in user data, environment variables, or an AMI.

Bonus points: mention **IMDSv2** (session-token required, `HttpTokens=required`), which mitigates
the SSRF attacks that made IMDSv1 famous.

---

### 10. What is a permissions boundary and when would you use it?

A managed policy attached to a user or role that sets the **maximum** permissions that identity can
have. It grants nothing on its own — effective permission is the intersection of the identity
policy and the boundary.

Canonical use case: **safe delegation.** You want your dev team to create their own IAM roles for
their Lambdas, but you don't want them minting admins. Give them `iam:CreateRole` with a condition
requiring that every role they create carries your boundary policy. They get autonomy; the ceiling
is yours.

---

### 11. How would you audit an AWS account you just inherited?

Have a structured answer — this is a favourite senior-level question.

1. **Credential report** — `aws iam generate-credential-report` → every user, MFA status, key age, last use.
2. **Access Advisor** on every role and user — what's actually being used.
3. **IAM Access Analyzer** — external and public access to S3, KMS, roles, Secrets Manager.
4. **CloudTrail** — is it on, in all regions, log file validation enabled, immutable destination?
5. **Trusted Advisor / Security Hub** — CIS and AWS Foundational Security Best Practices scores.
6. **Cost Explorer** — spend is a security signal; unexplained spend means unexplained resources.
7. **Automate it** — "I've written a boto3 tool that does the IAM portion of this and produces a
   scored, severity-ranked report" ← this is where you talk about the Day 01 lab.

---

### 12. Access keys vs IAM roles — when is an access key ever acceptable?

Almost never on AWS compute — use the instance/task/function role. Legitimate remaining cases:

- On-premises servers or another cloud that can't federate (and even then, prefer IAM Roles
  Anywhere with X.509 certificates).
- A break-glass admin identity, sealed in a vault.
- Some legacy tooling that predates role support.

If you must use keys: rotate under 90 days, scope them tightly, alarm on their use from unexpected
IPs, and never let them near a git repository.

---

### 13. What's the difference between identity-based and resource-based policies?

**Identity-based** attaches to a user, group or role: *"Alice can read bucket X."*
**Resource-based** attaches to the resource itself and has a `Principal` field: *"Bucket X allows Alice."*

Two practical differences worth naming:

- Not every service supports resource-based policies. S3, KMS, SQS, SNS, Lambda, Secrets Manager
  and IAM roles do. EC2 does not.
- For **cross-account access within the same account's resources**, a resource-based policy alone
  can be sufficient — the caller's account doesn't need a matching identity policy for S3, though
  it does for most other services. Knowing that nuance signals real experience.

---

### 14. Why is a budget a Day 1 security control, not a finance task?

Because cost is telemetry. The dominant AWS incident pattern is a leaked access key scraped from
GitHub within seconds, used to launch GPU instances in every region. Nobody watches EC2 dashboards
at 3 a.m. — but everyone reads an email that says *"you've hit 85% of your monthly budget on day 4."*

Set thresholds at 50/80/100% actual **and 100% forecasted**. The forecasted one is the important
one: it fires before the money is gone.

---

### 15. Design the IAM strategy for a 200-person engineering org across dev, staging and prod.

Structure the answer:

- **Multi-account** via AWS Organizations, split by environment and blast radius: management,
  log archive, audit, shared services, network, then per-environment workload accounts.
- **SCPs at the OU level** as guardrails: deny unused regions, deny disabling CloudTrail/GuardDuty,
  deny root usage, deny deleting KMS keys.
- **Zero IAM users.** IAM Identity Center federated to the corporate IdP (Okta/Entra ID), with
  permission sets mapped to groups. Access is a group membership, not a credential.
- **Workloads use roles** — instance profiles, task roles, Lambda execution roles, OIDC roles for
  GitHub Actions (no long-lived CI keys).
- **Permissions boundaries** so teams can self-serve role creation without escalating.
- **Everything as code** — Terraform, peer-reviewed, with `tfsec`/`checkov` in CI.
- **Continuous audit** — Access Analyzer, Security Hub, and a scheduled Lambda running the kind of
  IAM audit tool built on Day 01.

Close with the trade-off, because there always is one: multi-account adds operational overhead —
you need landing-zone automation (Control Tower or equivalent) or the account sprawl becomes its
own problem.

---

## Rapid-fire round

| Question | Answer |
|---|---|
| Is IAM regional or global? | Global. IAM endpoints live in `us-east-1` but identities are account-wide. |
| Max managed policies per role? | 10 by default (quota can be raised to 20). |
| Max IAM users per account? | 5,000 — a strong hint that users aren't the intended scaling model. |
| Default STS session duration? | 1 hour; role max is configurable up to 12 hours. |
| Can an SCP grant permissions? | No. Never. It only limits. |
| What does `Version: "2012-10-17"` do? | Selects the policy language version. It's the only one you should use — `2008-10-17` disables policy variables. |
| Two ARNs needed for S3 object access? | The bucket (`arn:aws:s3:::b`) for `ListBucket`, and objects (`arn:aws:s3:::b/*`) for `GetObject`. |
| How do you find unused permissions? | IAM Access Advisor / service-last-accessed data. |
| What is IMDSv2 and why does it matter? | Session-token-based instance metadata; mitigates SSRF credential theft. |
| Where do CloudTrail logs go for tamper resistance? | A dedicated log-archive account, S3 with Object Lock, log file validation on. |
