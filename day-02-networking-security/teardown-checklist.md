# Teardown Checklist — Day 02

# 💸💸💸  READ THIS FIRST  💸💸💸

> ## Cost if left running: **~$32.40/month, minimum.**
>
> Today you created a **NAT Gateway**. It bills **$0.045 every hour it exists**, whether or not
> a single packet flows through it, from the second `terraform apply` finished.
>
> | Left running for | It costs you |
> |---|---|
> | Overnight (12 hrs) | $0.54 |
> | A weekend | $2.16 |
> | A week | $7.56 |
> | **A forgotten month** | **$32.40** |
> | Three AZs, forgotten for a month | **$97.20** |
>
> Day 01 was free and the teardown was a good habit. **From today it is your money.**
> Do not close your laptop until `terraform destroy` has completed and you have verified it.

---

## The 30-second version

```bash
cd day-02-networking-security/lab/terraform
terraform destroy      # type: yes

# Then VERIFY — this is the part people skip
aws ec2 describe-nat-gateways \
  --query 'NatGateways[?State==`available`].[NatGatewayId,VpcId]' --output table

aws ec2 describe-addresses \
  --query 'Addresses[?AssociationId==null].[PublicIp,AllocationId]' --output table
```

**Both tables must be empty.** If either has rows, keep reading.

---

## What costs money, and what doesn't

| Resource | Running cost | Action |
|---|---|---|
| 💸 **NAT Gateway** | **$0.045/hr + $0.045/GB** | ❌ **DESTROY** |
| 💸 **Elastic IP, unattached** | $0.005/hr (~$3.60/mo) | ❌ **RELEASE** |
| 💸 **Interface VPC endpoints** | $0.01/hr per AZ, each | ❌ **DESTROY** (only if you enabled them) |
| ⚠️ CloudWatch Logs (flow logs) | ~$0.50/GB ingest + storage | 🤷 Small, but delete the group |
| Elastic IP **attached** to a running NAT | free | n/a |
| VPC, subnets, route tables | free | 🤷 Either |
| Internet Gateway | free | 🤷 Either |
| Security groups, NACLs | free | 🤷 Either |
| S3 **gateway** endpoint | free | 🤷 Either |
| 😈 BAD security groups / NACL | free | ❌ **DESTROY** — an open SSH rule is a live exposure |
| 😈 BAD unlogged VPC | free | ❌ **DESTROY** — clutter |

### Recommended: full destroy

There is no reason to keep any of today's network overnight. Tomorrow's Terraform builds its
own VPC.

```bash
cd lab/terraform
terraform destroy
# type: yes
```

Expected: `Destroy complete! Resources: 51 destroyed.` It takes 2–3 minutes, mostly the NAT
Gateway detaching.

### Alternative: keep the network, kill the cost

If you want to keep the topology to study but stop paying for it:

```bash
terraform apply -var="enable_nat_gateway=false" -var="create_insecure_examples=false"
```

That releases the NAT Gateway **and** its Elastic IP, removes the deliberately insecure
resources, and leaves the free parts (VPC, subnets, routing, security groups, NACLs, flow logs,
S3 endpoint) in place. Running cost drops to approximately $0.

---

## Verification — don't trust, check

```bash
export AWS_PROFILE=bootcamp AWS_REGION=us-east-1

echo "--- 💸 NAT Gateways (MUST be empty) ---"
aws ec2 describe-nat-gateways \
  --query 'NatGateways[?State!=`deleted`].[NatGatewayId,State,VpcId]' --output table

echo "--- 💸 Unattached Elastic IPs (MUST be empty) ---"
aws ec2 describe-addresses \
  --query 'Addresses[?AssociationId==null].[PublicIp,AllocationId,Tags[?Key==`Name`]|[0].Value]' \
  --output table

echo "--- 💸 Interface VPC endpoints (MUST be empty) ---"
aws ec2 describe-vpc-endpoints \
  --query 'VpcEndpoints[?VpcEndpointType==`Interface`].[VpcEndpointId,ServiceName,State]' \
  --output table

echo "--- Day 02 VPCs ---"
aws ec2 describe-vpcs \
  --query 'Vpcs[?Tags[?Key==`Day`&&Value==`02`]].[VpcId,CidrBlock,Tags[?Key==`Name`]|[0].Value]' \
  --output table

echo "--- Day 02 security groups ---"
aws ec2 describe-security-groups \
  --query 'SecurityGroups[?starts_with(GroupName,`cbc-day02`)].[GroupId,GroupName]' --output table

echo "--- Day 02 subnets ---"
aws ec2 describe-subnets \
  --query 'Subnets[?Tags[?Key==`Day`&&Value==`02`]].[SubnetId,CidrBlock]' --output table

echo "--- Flow log groups ---"
aws logs describe-log-groups --log-group-name-prefix "/aws/vpc/cbc-day02" \
  --query 'logGroups[].[logGroupName,storedBytes]' --output table

echo "--- Everything still tagged Day=02 ---"
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=Day,Values=02 \
  --query 'ResourceTagMappingList[].ResourceARN' --output table
```

The first three blocks are the ones that cost money. The rest are hygiene.

---

## If `terraform destroy` fails

Networking teardown fails more often than IAM teardown, because AWS refuses to delete anything
that still has a dependency. The error is always `DependencyViolation`, and it's always
something holding an ENI.

### `DependencyViolation` on a subnet or security group

Something still has a network interface in it. Find it:

```bash
VPC_ID=<your-vpc-id>

aws ec2 describe-network-interfaces --filters Name=vpc-id,Values=$VPC_ID \
  --query 'NetworkInterfaces[].[NetworkInterfaceId,Description,Status,SubnetId]' --output table
```

Common culprits and what they mean:

| Description contains | It's actually | Fix |
|---|---|---|
| `Interface for NAT Gateway` | The NAT is still deleting | Wait 2 minutes, retry |
| `VPC Endpoint Interface` | An interface endpoint | Delete the endpoint |
| `ELB app/...` | A load balancer you created manually | Delete the load balancer |
| `RDSNetworkInterface` | An RDS instance | Delete the database |
| `AWS Lambda VPC ENI` | A VPC-attached Lambda | Can take up to 20 min to release. Wait. |
| Blank / `aws-K8s` | Something created outside Terraform | Investigate before deleting |

Then retry the destroy.

### `DependencyViolation` on the Internet Gateway

An EIP is still associated with something in the VPC:

```bash
aws ec2 describe-addresses \
  --query 'Addresses[].[PublicIp,AllocationId,AssociationId,NetworkInterfaceId]' --output table
```

### Terraform state is out of sync

If you deleted something in the console first:

```bash
terraform refresh
terraform destroy
```

### Nuclear option — delete by hand, in this order

Order matters; AWS enforces the dependency chain:

```bash
VPC_ID=<your-vpc-id>

# 1. NAT Gateways (the expensive one — do this FIRST)
aws ec2 describe-nat-gateways --filter Name=vpc-id,Values=$VPC_ID \
  --query 'NatGateways[?State==`available`].NatGatewayId' --output text \
  | xargs -rn1 aws ec2 delete-nat-gateway --nat-gateway-id

# ⏳ wait ~2 minutes for them to reach 'deleted'

# 2. Release the Elastic IPs they were using
aws ec2 describe-addresses --query 'Addresses[?AssociationId==null].AllocationId' --output text \
  | xargs -rn1 aws ec2 release-address --allocation-id

# 3. VPC endpoints
aws ec2 describe-vpc-endpoints --filters Name=vpc-id,Values=$VPC_ID \
  --query 'VpcEndpoints[].VpcEndpointId' --output text \
  | xargs -r aws ec2 delete-vpc-endpoints --vpc-endpoint-ids

# 4. Detach and delete the Internet Gateway
IGW=$(aws ec2 describe-internet-gateways \
  --filters Name=attachment.vpc-id,Values=$VPC_ID \
  --query 'InternetGateways[0].InternetGatewayId' --output text)
aws ec2 detach-internet-gateway --internet-gateway-id "$IGW" --vpc-id "$VPC_ID"
aws ec2 delete-internet-gateway --internet-gateway-id "$IGW"

# 5. Then subnets, route tables, NACLs, security groups, and finally the VPC
aws ec2 delete-vpc --vpc-id "$VPC_ID"
```

> ⚠️ After a manual cleanup, your Terraform state is lying to you. Either
> `terraform state rm` the affected resources, or delete `terraform.tfstate` and start Day 03
> fresh. Never run `terraform apply` against a state file you know is wrong.

---

## Manual cleanup (things Terraform doesn't own)

- [ ] **Security groups you created by hand** during the lesson or demos
  ```bash
  aws ec2 describe-security-groups \
    --query 'SecurityGroups[?GroupName!=`default`].[GroupId,GroupName,VpcId]' --output table
  ```
- [ ] **Flow log CloudWatch group**, if you kept the network but want the storage gone
  ```bash
  aws logs delete-log-group --log-group-name "/aws/vpc/cbc-day02-flow-logs"
  ```
- [ ] **Local report files** — `rm -rf lab/python/reports/` (they contain your account ID, VPC
      IDs and security group IDs)
- [ ] **Python virtualenv** — `rm -rf lab/python/.venv` if you're done
- [ ] **Terraform state** — gitignored, but confirm before pushing:
      `git status --porcelain | grep -i tfstate` should return nothing

---

## Security hygiene before you push to GitHub

Run this every single time before your first push of the day:

```bash
cd /path/to/AWS-Cloud-AIOPS-BootCamp

# Anything that looks like an access key?
grep -rEn "AKIA[0-9A-Z]{16}" . --exclude-dir=.git --exclude-dir=.terraform

# Your account ID or a real public IP hardcoded anywhere?
grep -rEn "\b[0-9]{12}\b" . --exclude-dir=.git --exclude-dir=.terraform \
  --include="*.tf" --include="*.py" --include="*.md"

# Real VPC / subnet / security group IDs committed?
grep -rEn "\b(vpc|subnet|sg|nat|igw|eni|vpce)-[0-9a-f]{8,17}\b" . \
  --exclude-dir=.git --exclude-dir=.terraform --include="*.tf" --include="*.tfvars"

# State, tfvars or your real IP staged by accident?
git status --porcelain | grep -Ei "tfstate|tfvars$|credentials|\.env"
```

All four should return nothing. Matches on the documentation ranges are fine:
`123456789012` (AWS's reserved documentation account), `203.0.113.x`, `198.51.100.x` and
`192.0.2.x` (RFC 5737 documentation IPs).

> ⚠️ **Your `terraform.tfvars` contains your real home/office public IP.** It's gitignored —
> confirm that's still true before every push. That's PII about you, not just config.

---

## Sign-off

- [ ] 💸 `terraform destroy` completed (or `-var="enable_nat_gateway=false"` applied)
- [ ] 💸 `describe-nat-gateways` returns **no** gateways in `available` state
- [ ] 💸 `describe-addresses` returns **no** unattached Elastic IPs
- [ ] 💸 `describe-vpc-endpoints` returns **no** Interface endpoints
- [ ] BAD security groups and BAD NACL are **gone** (open SSH is a live exposure, not just clutter)
- [ ] Manually created security groups deleted
- [ ] Flow log group deleted or knowingly kept
- [ ] Local reports deleted
- [ ] Secret and resource-ID scan clean
- [ ] Budget still in place for tomorrow — **Day 03 runs actual EC2 instances**

**✅ Day 02 closed.** → [Day 03 — Compute Architecture & Intelligent Scaling](../day-03-compute-scaling/)
