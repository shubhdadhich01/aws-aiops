# `modules/network`

VPC, subnets, routing and one application security group. Everything else in
Day 05 sits on top of this.

The module is deliberately unopinionated about *where* it runs. No region, no
profile, no environment name, no provider block. The caller supplies all of
that; this module builds the same shape wherever you point it.

---

## Usage

```hcl
module "network" {
  source = "../../modules/network"

  name_prefix = "cbc-day05-dev"
  vpc_cidr    = "10.20.0.0/16"

  public_subnets = {
    "us-east-1a" = "10.20.1.0/24"
    "us-east-1b" = "10.20.2.0/24"
  }

  private_subnets = {
    "us-east-1a" = "10.20.11.0/24"
    "us-east-1b" = "10.20.12.0/24"
  }

  # ~$32.40/month. Leave false unless private subnets genuinely need egress.
  enable_nat_gateway = false
}
```

---

## Inputs

| Name | Type | Default | Cost | Description |
|---|---|---|---|---|
| `name_prefix` | `string` | — | — | Prefix for every resource name. Required. |
| `vpc_cidr` | `string` | — | — | VPC CIDR, `/24` or larger. Required. |
| `public_subnets` | `map(string)` | — | — | AZ name → CIDR. At least one. |
| `private_subnets` | `map(string)` | `{}` | — | AZ name → CIDR. May be empty. |
| `enable_nat_gateway` | `bool` | `false` | **~$32.40/mo + $0.045/GB** | Egress for private subnets. |
| `enable_flow_logs` | `bool` | `false` | ~$0.50/mo in a lab | Flow logs to CloudWatch. |
| `flow_log_retention_days` | `number` | `7` | — | Retention. Never leave it unset. |
| `app_ingress_port` | `number` | `80` | — | Port the app SG accepts from inside the VPC. |

Subnet maps are **keyed by availability zone name**, not by index. That is the
whole reason `for_each` beats `count` here — see below.

---

## Outputs

| Name | Description |
|---|---|
| `vpc_id` | VPC ID |
| `vpc_cidr_block` | VPC CIDR, for scoping rules in the caller |
| `public_subnet_ids` | Map: AZ → subnet ID |
| `private_subnet_ids` | Map: AZ → subnet ID (empty if none) |
| `public_subnet_id_list` | Sorted list, for arguments that want a list |
| `app_security_group_id` | Application security group |
| `internet_gateway_id` | IGW ID |
| `nat_gateway_id` | NAT ID or `null` |
| `nat_gateway_enabled` | Whether a NAT was actually created |
| `availability_zones` | Sorted AZs this network spans |
| `flow_logs_enabled` | Whether flow logs were created |
| `estimated_monthly_cost_usd` | This module's share of the bill |

---

## Why `for_each` and not `count`

Build three subnets with `count = 3` over a list and Terraform addresses them
by position:

```
aws_subnet.public[0]   10.20.1.0/24
aws_subnet.public[1]   10.20.2.0/24
aws_subnet.public[2]   10.20.3.0/24
```

Now remove the middle CIDR. Terraform does not see "one subnet deleted". It
sees index `[1]` changed from `10.20.2.0/24` to `10.20.3.0/24`, and index `[2]`
gone. Result: **two** subnets destroyed and recreated, one of which you never
touched, along with every instance in it.

With `for_each` over a map the address is the key:

```
aws_subnet.public["us-east-1a"]
aws_subnet.public["us-east-1b"]
aws_subnet.public["us-east-1c"]
```

Remove `us-east-1b` and exactly one subnet is destroyed. Nothing else moves.

**Rule of thumb:** `count` is correct only for on/off — `count = var.x ? 1 : 0`.
The moment the number can exceed one, you want `for_each`. This module uses
`count` in exactly two places (the NAT gateway and the flow-log resources) and
both are genuine on/off toggles.

`IAC-016` in the day's auditor flags `count` used over a multi-element
collection.

---

## The provider block that is not here

There is no `provider "aws"` block in this module, and `versions.tf` explains
at length why. Short version: a configured provider inside a child module
makes the module unusable with `count`/`for_each`, pins it to one region, and
— worst — makes the module impossible to cleanly *remove*, because Terraform
still needs the provider configuration in order to destroy what it created.

If a module genuinely needs a different provider (a second region, a different
account), the caller passes one in:

```hcl
module "network_eu" {
  source    = "../../modules/network"
  providers = { aws = aws.eu }
  # ...
}
```

and this module declares `configuration_aliases = [aws.eu]` in
`required_providers`.

---

## Cost notes

| Resource | Cost |
|---|---|
| VPC, subnets, route tables, IGW, security groups | **$0.00** — always free |
| NAT Gateway | **~$32.40/month** + $0.045/GB processed, billed from creation |
| Elastic IP attached to the NAT | $0.00 while attached |
| Elastic IP **allocated and unattached** | ~$3.60/month for nothing |
| Flow logs | ~$0.50/GB ingested + CloudWatch Logs storage |

The NAT gateway is the most expensive single toggle in the bootcamp. It bills
from the moment it exists, whether or not one packet crosses it, and it is the
number one source of "why is my sandbox account $90 this month".

---

## When *not* to use this module

If you need one VPC, ever, in one account, and it will never be recreated —
write it inline in the root module and skip the indirection. A module that has
exactly one caller is not reuse, it is a layer of misdirection between you and
the resource you are trying to read. Extract it on the second caller, not in
anticipation of one.
