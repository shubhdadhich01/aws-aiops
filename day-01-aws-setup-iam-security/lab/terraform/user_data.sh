#!/bin/bash
set -euxo pipefail

exec > >(tee /var/log/cbc-day01-user-data.log | logger -t cbc-day01-user-data -s 2>/dev/console) 2>&1

# -----------------------------------------------------------------------------
# 1. OS packages
# -----------------------------------------------------------------------------
dnf update -y
dnf install -y git python3 python3-pip

mkdir -p /opt/cbc-day01
chown ec2-user:ec2-user /opt/cbc-day01

# -----------------------------------------------------------------------------
# 2. Ollama + local Qwen model
# -----------------------------------------------------------------------------
if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
fi

mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf <<'OLLAMAEOF'
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
OLLAMAEOF

systemctl daemon-reload
systemctl enable --now ollama
systemctl restart ollama

for i in {1..60}; do
  if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

sudo -u ec2-user ollama pull '${ollama_model}'

# -----------------------------------------------------------------------------
# 3. Application repository + Python environment
# -----------------------------------------------------------------------------
if [ -n '${git_repository_url}' ]; then
  rm -rf /opt/cbc-day01/app
  sudo -u ec2-user git clone --branch '${git_branch}' '${git_repository_url}' /opt/cbc-day01/app

  if [ -f /opt/cbc-day01/app/requirements.txt ]; then
    sudo -u ec2-user python3 -m venv /opt/cbc-day01/venv
    sudo -u ec2-user /opt/cbc-day01/venv/bin/pip install --upgrade pip
    sudo -u ec2-user /opt/cbc-day01/venv/bin/pip install -r /opt/cbc-day01/app/requirements.txt
  fi
fi

# -----------------------------------------------------------------------------
# 4. AWS CLI/profile behavior
#
# The EC2 instance receives only sts:AssumeRole permissions. boto3 then uses
# the instance metadata credentials to assume the dedicated SecurityAudit role.
# -----------------------------------------------------------------------------
mkdir -p /home/ec2-user/.aws
cat > /home/ec2-user/.aws/config <<'AWSConfigEOF'
[profile bootcamp-audit]
role_arn = ${security_audit_role_arn}
credential_source = Ec2InstanceMetadata
region = ${aws_region}
AWSConfigEOF

chown -R ec2-user:ec2-user /home/ec2-user/.aws
chmod 0700 /home/ec2-user/.aws
chmod 0600 /home/ec2-user/.aws/config

# -----------------------------------------------------------------------------
# 5. Runtime environment used by the demo helper
# -----------------------------------------------------------------------------
cat > /opt/cbc-day01/environment <<'ENVEOF'
export AWS_PROFILE=bootcamp-audit
export AWS_REGION=${aws_region}
export OLLAMA_URL=http://127.0.0.1:11434
export OLLAMA_MODEL='${ollama_model}'
export AUDIT_ROLE_ARN='${security_audit_role_arn}'
ENVEOF

chown ec2-user:ec2-user /opt/cbc-day01/environment

# -----------------------------------------------------------------------------
# 6. One-command demo helper
# -----------------------------------------------------------------------------
cat > /usr/local/bin/cbc-day01-audit <<'RUNEOF'
#!/bin/bash
set -euo pipefail

source /opt/cbc-day01/environment
cd /opt/cbc-day01/app

PYTHON=python3
[ -x /opt/cbc-day01/venv/bin/python ] && PYTHON=/opt/cbc-day01/venv/bin/python

exec $PYTHON iam_aiops_audit.py \
  --profile "$AWS_PROFILE" \
  --anomaly \
  --ai \
  --model-id "$OLLAMA_MODEL" \
  --ollama-url "$OLLAMA_URL"
RUNEOF

chmod 0755 /usr/local/bin/cbc-day01-audit

# -----------------------------------------------------------------------------
# 7. Operator notes
# -----------------------------------------------------------------------------
cat > /opt/cbc-day01/README.txt <<'READMEEOF'
CareerByteCode Day 01 AIOps Runner

Components:
  - Python + boto3
  - IAM SecurityAudit role
  - Ollama + Qwen3

Run the complete demo:
  cbc-day01-audit

Ollama listens only on:
  http://127.0.0.1:11434

TCP/11434 is intentionally NOT exposed in the EC2 security group.
READMEEOF

chown ec2-user:ec2-user /opt/cbc-day01/README.txt

echo "CBC Day 01 AIOps runner bootstrap complete"
