# `modules/compute`

EC2 instances addressed by logical name, plus the instance profile they run
with. Nothing else.

**Default input is an empty map, so applying this module with defaults creates
one IAM role and costs $0.00.** A module whose defaults spend money is a module
that will spend money by accident.

---

## Usage

```hcl
module "compute" {
  source = "../../modules/compute"

  name_prefix        = "cbc-day05-dev"
  subnet_ids         = module.network.public_subnet_ids
  security_group_ids = [module.network.app_security_group_id]

  instances = {
    "web-a" = {
      instance_type     = "t3.micro"
      availability_zone = "us-east-1a"
      root_volume_gb    = 8
    }
  }

  associate_public_ip = true   # ~$3.65/month per address
}
```

---

## Inputs

| Name | Type | Default | Cost | Description |
|---|---|---|---|---|
| `name_prefix` | `string` | — | — | Prefix for every resource name. |
| `instances` | `map(object)` | `{}` | **~$7.59/mo per t3.micro** | Logical name → `{ instance_type, availability_zone, root_volume_gb }` |
| `subnet_ids` | `map(string)` | — | — | AZ → subnet ID, straight from `modules/network`. |
| `security_group_ids` | `list(string)` | — | — | SGs to attach. This module creates none. |
| `associate_public_ip` | `bool` | `false` | **~$3.65/mo each** | Public IPv4, billed since Feb 2024 |
| `enable_ssm` | `bool` | `true` | free | Session Manager instead of SSH |
| `root_volume_encrypted` | `bool` | `true` | free | EBS encryption with the AWS-managed key |
| `user_data` | `string` | `""` | — | **Never put secrets here.** See below. |

`root_volume_gb` uses `optional(number, 8)` — an optional object attribute with
a default. This is why `type` matters: with `any`, a caller omitting the key
gets a null-attribute error at apply time instead of a default at plan time.

---

## Outputs

| Name | Description |
|---|---|
| `instance_ids` | Map: logical name → instance ID |
| `instance_private_ips` | Map: logical name → private IP |
| `instance_public_ips` | Map, or `{}` when `associate_public_ip = false` |
| `instance_count` | How many instances exist |
| `iam_role_name` / `iam_role_arn` | For attaching extra policies from the caller |
| `instance_profile_name` | The profile on every instance |
| `ami_id` | Resolved AMI |
| `ssm_session_command` | Paste-ready `aws ssm start-session` for the first instance |
| `estimated_monthly_cost_usd` | Compute + storage + public IPs |
| `cost_breakdown` | The three line items separately |

---

## Three decisions worth arguing about

### 1. The AMI comes from an SSM public parameter, and then it is ignored

Hardcoding an AMI ID gives you a module that works in `us-east-1` and fails in
`eu-west-1` with `InvalidAMIID.NotFound`, because AMI IDs are per-region. So the
module resolves it:

```hcl
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}
```

But that parameter changes every time AWS publishes a new build — so without
intervention, some unrelated `terraform apply` three weeks from now proposes to
**replace every running instance**. Hence:

```hcl
lifecycle {
  ignore_changes = [ami]
}
```

This is the correct use of `ignore_changes`: you have decided AMI updates happen
through a deliberate rebuild, not as a surprise in somebody else's plan.

It is **not** a way to silence a diff you do not understand. Every attribute you
add to `ignore_changes` is an attribute where the code has officially stopped
describing reality — and `ignore_changes` is one of the three drift responses
covered in the day's lab precisely because choosing it carelessly is how
configuration rots.

### 2. `user_data` is not a secrets mechanism

User data is readable by anything that can reach the instance metadata service,
it appears in the console, and it is stored **in Terraform state in the clear**.
Marking the variable `sensitive` would hide it from CLI output and change none
of that.

Put secrets in SSM Parameter Store or Secrets Manager and have the instance
fetch them at boot using its instance profile.

### 3. IMDSv2 is required, not optional

```hcl
metadata_options {
  http_tokens                 = "required"
  http_put_response_hop_limit = 1
}
```

`"optional"` means IMDSv1 still works, which means it is not required at all.
IMDSv1 is the token-less version that turned server-side request forgery into
credential theft in a long list of well-known breaches. The hop limit of 1 stops
a container on the host reaching metadata through the Docker bridge.

---

## Cost notes

| Item | Cost |
|---|---|
| `t3.micro` on-demand | ~$7.59/month (750 free hours for 12 months, one instance, new accounts only) |
| gp3 root volume | $0.08/GB-month — an 8 GB root is $0.64/month |
| Public IPv4 address | **$0.005/hour ≈ $3.65/month, each**, attached or not, since Feb 2024 |
| IAM role and instance profile | $0.00 |
| Session Manager | $0.00 |

The `hourly_prices` map in `main.tf` is a static table and **will** drift from
real pricing. It exists so the module can produce an estimate at plan time,
which is worth more than an accurate number you only get on the invoice.
Unknown instance types fall back to the `t3.micro` rate.

---

## When *not* to use this module

If what you actually need is a self-healing, load-balanced tier that replaces
failed instances on its own, you want an Auto Scaling group and a launch
template — that is [Day 03](../../../../day-03-compute-scaling/), and this
module is not a substitute for it. Named EC2 instances are the right shape for
a bastion, a licence server, a build agent, or a lab. They are the wrong shape
for anything that must survive an instance dying at 3 a.m.
