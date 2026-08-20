# Teardown Checklist — Day 01

**Cost if left running: ~$0.00/month.** IAM, budgets and SNS are free at lab volumes.

Day 1 is the cheapest day of the bootcamp — but do the teardown anyway. The habit is the point,
and from Day 2 onward it stops being optional.

---

## Decide first: keep or destroy?

| Resource | Recommendation |
|---|---|
| Account password policy | ✅ **Keep** — it's a genuine security improvement |
| Budget + SNS alerts | ✅ **Keep** — this is your safety net for the next nine days |
| Security audit role | ✅ **Keep** — you'll reuse it |
| Groups + developer policy | 🤷 Either — harmless |
| 😈 BAD example policy | ❌ **Destroy** — never leave a `*:*` policy lying around |
| 😈 BAD open-trust role | ❌ **Destroy** — an open trust policy is a live exposure |

### Recommended: destroy only the bad ones, keep the guardrails

```bash
cd lab/terraform

# Flip the flag and re-apply — removes both deliberately-insecure resources,
# leaves your budget and audit role intact.
terraform apply -var="create_bad_policy=false"
```

### Or: full teardown

```bash
cd lab/terraform
terraform destroy
# type: yes
```

Expected: `Destroy complete! Resources: 13 destroyed.`

> ⚠️ `terraform destroy` also removes your **budget**. If you do this, create a manual budget in
> the console before starting Day 2. Do not go into Day 2 (NAT Gateways) without a budget.

---

## Verification — don't trust, check

```bash
export AWS_PROFILE=bootcamp AWS_REGION=us-east-1

echo "--- Day 01 IAM groups (expect empty after full destroy) ---"
aws iam list-groups \
  --query 'Groups[?starts_with(GroupName,`cbc-day01`)].GroupName' --output table

echo "--- Day 01 customer-managed policies ---"
aws iam list-policies --scope Local \
  --query 'Policies[?starts_with(PolicyName,`cbc-day01`)].[PolicyName,AttachmentCount]' \
  --output table

echo "--- Day 01 roles ---"
aws iam list-roles \
  --query 'Roles[?starts_with(RoleName,`cbc-day01`)].RoleName' --output table

echo "--- Budgets ---"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
aws budgets describe-budgets --account-id "$ACCOUNT_ID" \
  --query 'Budgets[].[BudgetName,BudgetLimit.Amount]' --output table

echo "--- SNS topics ---"
aws sns list-topics --query 'Topics[?contains(TopicArn,`cbc-day01`)]' --output table

echo "--- Anything still tagged for this bootcamp ---"
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=Day,Values=01 \
  --query 'ResourceTagMappingList[].ResourceARN' --output table
```

> ℹ️ IAM resources aren't taggable in the `resourcegroupstaggingapi` sense the way EC2 is, so the
> last command may return empty even before teardown. The explicit IAM queries above are the
> authoritative check for Day 1.

---

## Manual cleanup (things Terraform doesn't own)

- [ ] **Demo users you created by hand** during the lesson
  ```bash
  # Users must be fully stripped before deletion — this order matters
  USER=legacy-jenkins
  aws iam list-attached-user-policies --user-name $USER \
    --query 'AttachedPolicies[].PolicyArn' --output text | \
    xargs -rn1 aws iam detach-user-policy --user-name $USER --policy-arn
  aws iam list-user-policies --user-name $USER --query 'PolicyNames[]' --output text | \
    xargs -rn1 aws iam delete-user-policy --user-name $USER --policy-name
  aws iam list-access-keys --user-name $USER \
    --query 'AccessKeyMetadata[].AccessKeyId' --output text | \
    xargs -rn1 aws iam delete-access-key --user-name $USER --access-key-id
  aws iam delete-user --user-name $USER
  ```
- [ ] **The `bootcamp-audit` profile** in `~/.aws/config` — remove it if you destroyed the role,
      otherwise it'll fail confusingly tomorrow
- [ ] **Local report files** — `rm -rf lab/python/reports/` (they contain your account ID and ARNs)
- [ ] **Terraform state** — `terraform.tfstate` is gitignored, but confirm before pushing:
      `git status --porcelain | grep -i tfstate` should return nothing

---

## Security hygiene before you push to GitHub

Run this every single time before your first push of the day:

```bash
cd /path/to/AWS-Cloud-AIOPS-BootCamp

# Anything that looks like an access key?
grep -rEn "AKIA[0-9A-Z]{16}" . --exclude-dir=.git --exclude-dir=.terraform

# Your account ID hardcoded anywhere?
grep -rEn "\b[0-9]{12}\b" . --exclude-dir=.git --exclude-dir=.terraform \
  --include="*.tf" --include="*.py" --include="*.md"

# State or tfvars staged by accident?
git status --porcelain | grep -Ei "tfstate|tfvars$|credentials|\.env"
```

All three should return nothing. If the account-ID grep matches an example in documentation
(`123456789012`), that's fine — that's AWS's reserved documentation account ID.

---

## Sign-off

- [ ] `terraform destroy` (or `-var="create_bad_policy=false"`) completed
- [ ] Verification commands run and reviewed
- [ ] BAD policy and BAD role are **gone**
- [ ] Manual demo users deleted
- [ ] Local reports deleted
- [ ] Secret scan clean
- [ ] A budget is in place for tomorrow (either kept, or recreated manually)

**✅ Day 01 closed.** → [Day 02 — Enterprise Networking & Security Architecture](../)
