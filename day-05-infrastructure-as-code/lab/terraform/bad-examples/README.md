# `bad-examples/` — wrong on purpose

**Nothing in this directory is ever applied.** No environment references it, no
module sources it, and there is no backend block — `terraform apply` here would
fail before it could do any damage.

It exists to be **parsed** by [`../../python/iac_audit.py`](../../python/iac_audit.py),
so the static checks have real, readable HCL to find faults in. A test fixture
that lives in a Python string is a test fixture nobody reads; a `.tf` file you
can open in your editor with syntax highlighting is a teaching artefact.

Run the auditor against the whole tree and this directory is where almost every
static finding comes from:

```bash
cd ../../python
python3 iac_audit.py --path ../terraform
```

---

## What is wrong, and where

| File | Check | Severity | Fault |
|---|---|---|---|
| `providers.tf` | **IAC-002** | CRITICAL | Hardcoded `access_key` / `secret_key` |
| `providers.tf` | **IAC-005** | HIGH | No backend block — local state |
| `providers.tf` | **IAC-010** | MEDIUM | No `required_version` |
| `providers.tf` | **IAC-011** | MEDIUM | Provider version unpinned |
| `secrets.tf` | **IAC-001** | CRITICAL | Password written into the config |
| `resources.tf` | **IAC-009** | HIGH | `0.0.0.0/0` on an SSH ingress rule |
| `resources.tf` | **IAC-013** | MEDIUM | S3 bucket with no `prevent_destroy` |
| `resources.tf` | **IAC-014** | MEDIUM | Untagged resource (×2) |
| `resources.tf` | **IAC-016** | LOW | `count` where `for_each` belongs |
| `variables.tf` | **IAC-016** | LOW | Variable with no `type` and no `description` |
| `outputs.tf` | **IAC-008** | HIGH | Secret output without `sensitive` |
| `.gitignore` | **IAC-012** | MEDIUM | `.terraform.lock.hcl` gitignored |

**13 static findings.** The two live checks (IAC-006, IAC-007) come from the
`create_insecure_examples` bucket in `envs/dev`, which brings the total to 15
when you run with credentials.

---

## Deliberately *not* wrong here

Every fixture is scoped to the fault it demonstrates. The SSM parameter in
`secrets.tf` is tagged, so `IAC-014` does not also fire on it. `variables.tf`
contains two perfectly good variables alongside the bad one, and `outputs.tf`
contains a good output alongside the bad one.

That is not politeness. A fixture that trips four checks at once tells you
nothing about which one you broke when a test starts failing, and a check that
cannot distinguish a good variable from a bad one in the same file is a check
that will flag everything in your real repository.

---

## Two checks that find nothing here, on purpose

**IAC-003** (`terraform.tfstate` committed) and **IAC-004** (state bucket
missing its public access block) stay silent against this entire repository.

That is by design and the tests assert it. There is no committed state file
anywhere in the lab, and this repo does not ship a publicly readable S3 bucket
even as a teaching example — being one `terraform apply` away from leaking
somebody's data is not a lesson worth the demonstration.

A check set where everything fires teaches you nothing about false positives,
and false positives are precisely how audit tools get ignored.

---

## If you edit this directory

The finding counts are quoted in five places and they must all agree:

- `../../../README.md` (the day README)
- `../../README.md` (the lab README)
- `../envs/dev/outputs.tf` (`next_steps`)
- `../../python/iac_audit.py` (the module docstring)
- `../../python/tests/test_checks.py` (the whole-stack assertions)

Change a fixture, re-run `python3 -m unittest discover -s tests`, and update
all five. "13+" is not a finding count.
