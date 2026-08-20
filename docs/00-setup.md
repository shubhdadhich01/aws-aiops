# 00 — Environment Setup (do this before Day 1)

Target time: **30–45 minutes.** Do it once, and every lab in the bootcamp just works.

---

## 1. What you need installed

| Tool | Minimum version | Check command |
|---|---|---|
| AWS CLI | v2.x | `aws --version` |
| Terraform | 1.5+ | `terraform version` |
| Python | 3.9+ | `python3 --version` |
| pip | any | `pip3 --version` |
| Git | 2.x | `git --version` |

### Linux / WSL2 (Ubuntu)

```bash
# AWS CLI v2
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip awscliv2.zip && sudo ./aws/install
aws --version

# Terraform (HashiCorp apt repo)
wget -O- https://apt.releases.hashicorp.com/gpg | \
  sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] \
  https://apt.releases.hashicorp.com $(lsb_release -cs) main" | \
  sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update && sudo apt install -y terraform python3-pip git
```

### macOS

```bash
brew install awscli terraform python git
```

### Windows

Use **WSL2 (Ubuntu)** and follow the Linux steps. Native PowerShell works too
(`winget install Amazon.AWSCLI Hashicorp.Terraform Python.Python.3.12 Git.Git`), but every
command in this bootcamp is written for bash.

---

## 2. Create your AWS account

1. Go to <https://aws.amazon.com/> → **Create an AWS Account**.
2. Provide email, account name (e.g. `yourname-bootcamp`), and a card (required even for free tier).
3. Choose the **Basic (Free)** support plan.
4. Sign in as **root** exactly once — to do the hardening in Day 1. Then never again.

> 💡 If your employer already gave you a sandbox account, use that instead — but confirm you have
> IAM, Budgets, CloudTrail, GuardDuty and Bedrock permissions.

---

## 3. Bootstrap credentials (the one-time chicken-and-egg step)

You need an identity before you can create identities. Do this **once**, as root, then discard it.

```
Root sign-in  →  IAM  →  Users  →  Create user
  Name:      bootcamp-admin
  Access:    (no console access needed for now)
  Permissions: attach policy directly → AdministratorAccess
  Create → Security credentials tab → Create access key → "Command Line Interface (CLI)"
  Download the .csv  (this is the ONLY time you will see the secret)
```

Then enable **MFA on the root user** and walk away from root:
`IAM → Security credentials (root) → Assign MFA device → Authenticator app`.

---

## 4. Configure the `bootcamp` named profile

Every lab in this repo assumes a profile called `bootcamp`.

```bash
aws configure --profile bootcamp
# AWS Access Key ID     : AKIA...
# AWS Secret Access Key : ...
# Default region name   : us-east-1
# Default output format : json
```

Verify:

```bash
aws sts get-caller-identity --profile bootcamp
```

Expected:

```json
{
    "UserId": "AIDA...",
    "Account": "123456789012",
    "Arn": "arn:aws:iam::123456789012:user/bootcamp-admin"
}
```

Make it the default for your shell session so you don't type `--profile` a hundred times:

```bash
export AWS_PROFILE=bootcamp
export AWS_REGION=us-east-1
# add both lines to ~/.bashrc or ~/.zshrc to make it permanent
```

---

## 5. Python environment

```bash
git clone https://github.com/careerbytecode/AWS-Cloud-AIOPS-BootCamp.git
cd AWS-Cloud-AIOPS-BootCamp

python3 -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install boto3 botocore tabulate rich
```

Smoke test boto3 talks to AWS:

```bash
python3 -c "import boto3; print(boto3.client('sts').get_caller_identity()['Account'])"
```

If that prints your 12-digit account ID, your toolchain is done.

---

## 6. Enable Amazon Bedrock (needed from Day 6)

Bedrock model access is **opt-in per region** and can take a few minutes to approve.
Do it now so it's ready when you get there.

```
AWS Console → Amazon Bedrock → Model access → Manage model access
  ✅ Anthropic — Claude family
  Submit
```

Verify from the CLI:

```bash
aws bedrock list-foundation-models --region us-east-1 \
  --query "modelSummaries[?contains(modelId,'claude')].modelId" --output table
```

> Bedrock is not available in every region. `us-east-1` and `us-west-2` have the widest model
> coverage — that's why this bootcamp defaults to `us-east-1`.

---

## 7. Set your safety net now

Do not skip this. Read **[cost-guardrails.md](cost-guardrails.md)** and create a budget before Day 1
Lab. Day 1 also creates budgets *as code* with Terraform — but a manual one right now protects you
from mistakes made in the next 30 minutes.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Unable to locate credentials` | Profile not set | `export AWS_PROFILE=bootcamp` or add `--profile bootcamp` |
| `An error occurred (AccessDenied)` | Policy too narrow, or wrong identity | `aws sts get-caller-identity` — are you who you think you are? |
| `ExpiredToken` | Temporary creds from an assumed role timed out | Re-assume the role / re-run `aws sso login` |
| `terraform: command not found` after install | PATH not reloaded | Open a new shell, or `hash -r` |
| Bedrock `AccessDeniedException` | Model access not granted | Console → Bedrock → Model access |
| Wrong region resources "disappear" | Console region selector | Check the top-right region dropdown matches `us-east-1` |
