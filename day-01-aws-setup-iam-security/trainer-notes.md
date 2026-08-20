# Trainer Notes — Day 01

**Total: 3h 20m** including one 10-minute break. Timings assume 8–15 students on Zoom with
screen share, and that everyone completed `docs/00-setup.md` beforehand.

---

## Before class (T-24 hours)

- [ ] Send `docs/00-setup.md` and ask students to confirm `aws sts get-caller-identity` works
- [ ] Warn them: a payment card is required to create an AWS account
- [ ] Have a **throwaway demo account** ready with deliberate mess in it (see "Demo account setup")
- [ ] Have your own `terraform apply` already run once, so you know the outputs
- [ ] Open tabs: IAM console, Billing console, IAM policy simulator, AWS docs on evaluation logic

### Demo account setup (10 min, once)

Seed the demo account so the live audit finds interesting things:

```bash
aws iam create-user --user-name legacy-jenkins
aws iam attach-user-policy --user-name legacy-jenkins \
  --policy-arn arn:aws:iam::aws:policy/AdministratorAccess
aws iam create-access-key --user-name legacy-jenkins    # never used → IAM-010
aws iam put-user-policy --user-name legacy-jenkins \
  --policy-name inline-mess \
  --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"s3:*","Resource":"*"}]}'
```

That's four findings across three severities in thirty seconds. Far more persuasive than slides.

---

## Minute-by-minute

| Time | Segment | Mode | Notes |
|---|---|---|---|
| 0:00–0:10 | Welcome, roadmap, how the 10 days build to the capstone | Talk | Show the repo tree. Set the "we tear down every day" expectation now. |
| 0:10–0:35 | **Part 1** Account & Org structure | Talk + diagram | Multi-account is interview gold. Don't over-invest — they have one account. |
| 0:35–1:25 | **Part 2** IAM deep dive | Talk + live demo | ⭐ The most important 50 minutes of the day. |
| 1:25–1:50 | **Part 3** Least privilege in practice | Live refactor | Do the bad→good policy rewrite live, typing. |
| 1:50–2:00 | ☕ Break | | Tell them to check the budget email confirmation. |
| 2:00–2:25 | **Part 4** CLI & named profiles | Live terminal | Everyone follows along in their own terminal. |
| 2:25–2:50 | **Part 5** Billing guardrails | Talk + console | Tell a real leaked-key story. It lands harder than any diagram. |
| 2:50–3:50 | **Lab** | Hands-on | Terraform 20 min, Python 40 min. |
| 3:50–4:10 | Interview drill + wrap | Discussion | Pick 5 questions from `interview-qa.md`, cold-call gently. |

> If you're short on time, compress Part 1 to 15 minutes and protect the lab. Students remember
> what they built, not what you said.

---

## Live demos to run (not slides)

### Demo 1 — ARN anatomy (Part 1, 3 min)

```bash
aws sts get-caller-identity
aws iam list-roles --query 'Roles[0:3].Arn' --output text
```
Read three real ARNs aloud, field by field. Ask the class to name the account field in each.

### Demo 2 — Explicit deny beats allow (Part 2, 8 min) ⭐

The single best demo of the day. Use the **IAM Policy Simulator**
(`IAM → Policy simulator`) on your demo user:

1. Simulate `s3:GetObject` → **allowed** (they have `s3:*`)
2. Add an inline deny for `s3:GetObject` on one bucket
3. Simulate again → **denied**, and the simulator names the denying statement

Students who see this never forget rule #2.

### Demo 3 — Trust vs permission policy (Part 2, 5 min)

Create a role with a trust policy for `lambda.amazonaws.com`, then try to assume it as yourself:

```bash
aws sts assume-role --role-arn <arn> --role-session-name test
```

The `AccessDenied` is the lesson: your permissions are irrelevant if the *trust* doesn't name you.
Ask "whose fault is this — the permission policy or the trust policy?" before revealing.

### Demo 4 — Credential resolution order (Part 4, 4 min)

```bash
export AWS_ACCESS_KEY_ID=AKIABOGUS
aws sts get-caller-identity --profile bootcamp     # ❌ fails despite the profile
unset AWS_ACCESS_KEY_ID
aws sts get-caller-identity --profile bootcamp     # ✅ works
```

This exact bug will hit at least three students during the lab. Inoculate them now.

### Demo 5 — Access Advisor (Part 3, 4 min)

`IAM → Roles → pick any role → Access Advisor tab`. Point at the "Not accessed" rows.
"Every one of those is a permission you can delete this afternoon."

---

## Where students get stuck (and the fix)

| Symptom | Real cause | Say this |
|---|---|---|
| `NoCredentialsError` | Stale env vars | "Run `env \| grep AWS` and read it out to me." |
| Terraform `EntityAlreadyExists` | Ran apply twice, or account already has a password policy | `terraform import aws_iam_account_password_policy.this iam-account-password-policy` |
| Budget email never arrives | SNS subscription unconfirmed | "Check spam. It's from `no-reply@sns.amazonaws.com`." |
| Assume-role fails | MFA condition in the trust policy, no MFA device | Comment out the MFA condition block, re-apply |
| `AccessDenied` on `iam:List*` | Running as a non-admin | Attach `SecurityAudit` |
| Tool reports 0 findings | `create_bad_policy = false` | Set it true, re-apply |
| Policy JSON parse errors | Forgot `unquote()` | Point them at the string-vs-dict trap in the lab README |

---

## Discussion prompts that actually generate discussion

1. *"Your company has 200 engineers. Do you create 200 IAM users? Defend your answer."*
   → Leads naturally to federation and Identity Center.
2. *"An SCP denies `s3:DeleteBucket`. A user has `AdministratorAccess`. Can they delete a bucket?"*
   → Tests whether the guardrail-vs-grant model landed.
3. *"Is an unattached bad policy a real finding?"*
   → Genuinely debatable. Best answer: yes, because it's a loaded gun on the table.
4. *"Why is `Resource: *` sometimes unavoidable?"*
   → Describe/List APIs are account-scoped. Conditions are the answer, not ARNs.
5. *"You inherit an account with 40 IAM users and no MFA. Monday morning. What's your order of operations?"*
   → Great capstone-thinking warm-up.

---

## Assessment — what "done" looks like

Students have genuinely got Day 1 if they can:

- [ ] Read an unfamiliar policy and say what it permits, in one sentence
- [ ] Trace an `AccessDenied` through the evaluation flow without prompting
- [ ] Explain trust policy vs permission policy without hedging
- [ ] Run their audit tool and explain each finding it produced
- [ ] Explain why role credentials are safer than access keys

**Red flag:** a student who says "I just copied the solution." Sit with them for five minutes on
`analyse_policy_document()` — that one function is the conceptual centre of the day.

---

## Bridge to Day 02

> "Today we decided **who** can act in this account. Tomorrow we decide **where traffic can
> flow** — VPCs, subnets, security groups, NACLs. Same principle, different layer: default deny,
> then open the minimum. And tomorrow's lab is the same shape as today's — a Python tool that
> audits what we built."

Also flag: **Day 2 introduces NAT Gateways, which are the first thing in this bootcamp that
actually costs real money.** Set the expectation now.
