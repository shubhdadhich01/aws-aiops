# Trainer Notes — Day 02

**Total: 3h 30m** including one 10-minute break. Timings assume 8–15 students on Zoom with
screen share, and that everyone completed Day 01 and still has a working `bootcamp` profile.

> 💸 **Say the cost sentence in the first five minutes and again before the lab.** Today is the
> first day students can leave something running that bills them. A student who discovers a $32
> charge three weeks later does not come back for Day 08.

---

## Before class (T-24 hours)

- [ ] Message the group: *"Day 2 creates a NAT Gateway (~$32/month). Confirm your Day 1 budget
      alert still exists before class."*
- [ ] Ask everyone to have `curl -s https://checkip.amazonaws.com` output ready
- [ ] Run `terraform apply` yourself once, so you know the outputs and the timing
- [ ] Open tabs: VPC console (Resource Map view), a real production VPC diagram if you have one,
      the NAT Gateway pricing page, `shodan.io`
- [ ] Have the default VPC of your demo account **untouched** — you'll assess it live at the end
- [ ] Check `terraform destroy` from last time actually completed. Nothing undermines the cost
      lecture like your own leftover NAT Gateway appearing on screen.

### Demo account setup (5 min, once)

```bash
# A security group with the classic mistake, in the default VPC
aws ec2 create-security-group --group-name legacy-jenkins-sg \
  --description "left over from a POC in 2023" --vpc-id <default-vpc-id>

aws ec2 authorize-security-group-ingress --group-id <sg-id> \
  --protocol tcp --port 22 --cidr 0.0.0.0/0

aws ec2 authorize-security-group-ingress --group-id <sg-id> \
  --protocol tcp --port 3306 --cidr 0.0.0.0/0
```

Running the tool against a *pre-existing mess* lands harder than running it against a mess you
just built on purpose. Students recognise the shape of it from their own accounts.

---

## Minute-by-minute

| Time | Segment | Mode | Notes |
|---|---|---|---|
| 0:00–0:08 | Recap Day 1, bridge to today, **the cost warning** | Talk | "Yesterday: who. Today: where." Say the $32 number out loud. |
| 0:08–0:38 | **Part 1** VPC fundamentals & CIDR planning | Talk + whiteboard | Draw the subnet plan by hand. Do the /24 = 251 usable maths live. |
| 0:38–1:03 | **Part 2** Route tables ⭐ | Talk + console | The "what makes a subnet public" moment. Protect this segment. |
| 1:03–1:33 | **Part 3** IGW, NAT, endpoints + cost | Talk + pricing page | Show real pricing on screen. Do the 3-AZ = $97/month maths. |
| 1:33–2:13 | **Part 4** Security Groups vs NACLs ⭐⭐ | Talk + live demo | The most interview-relevant 40 minutes of the bootcamp so far. |
| 2:13–2:23 | ☕ Break | | Tell them to run `terraform init` during the break. |
| 2:23–2:48 | **Part 5** Tiering, Session Manager, endpoints | Talk + console | Session Manager demo if you have an instance handy. |
| 2:48–3:48 | **Lab** | Hands-on | Terraform 20 min, Python 40 min. |
| 3:48–4:05 | Interview drill + **live teardown** | Discussion | ⚠️ Do the teardown ON SCREEN, together, before anyone leaves. |

> If you're short on time, compress Part 1 to 20 minutes and Part 5 to 15. **Never** compress
> Part 4 or the teardown. Part 4 is the interview material; the teardown is their money.

---

## Live demos to run (not slides)

### Demo 1 — The VPC Resource Map (Part 2, 5 min) ⭐

`VPC console → your VPC → Resource map tab`. This view did not exist until 2023 and it is the
single best teaching artefact AWS has ever shipped for networking.

Click a subnet. Watch the route table and its target highlight. Click the private-data subnet
and show that its route table has **no** line going to the IGW. Then ask:

> "If I renamed this subnet to `public-subnet-1`, what would change?"

The answer — nothing — is the whole of Part 2 in one question.

### Demo 2 — Stateless NACL breakage (Part 4, 10 min) ⭐⭐

**The best demo of the day.** You need one EC2 instance in a public subnet with SSH access.

1. `curl https://example.com` from the instance → works.
2. Add a NACL to that subnet with **only**: inbound allow tcp/22 from your IP, outbound allow all.
3. `curl https://example.com` again → **hangs, then times out.**
4. Ask: *"Outbound is allow-all. The request definitely left. Why is there no response?"*
5. Let them struggle for 30 seconds. Someone will say "ports".
6. Add inbound allow tcp/1024–65535 → works instantly.

Nobody who watches this forgets stateless. Nobody who only reads a table remembers it.

### Demo 3 — The shadowed rule (Part 4, 5 min)

Add to the same NACL:
- Rule 100: allow all from 0.0.0.0/0
- Rule 200: deny tcp/22 from 0.0.0.0/0

Then SSH in. It works. Ask *"why didn't rule 200 stop me?"*

Show the console — both rules are green, both look active, and one of them is dead code.
Then say: *"the tool you're about to build detects exactly this. It's called VPC-013."*

### Demo 4 — Security group chaining (Part 4, 6 min)

In the console, add an inbound rule to a security group and, in the Source field, start typing
`sg-`. Watch the dropdown appear.

> "Everyone has seen this field. Almost nobody has used it. This is the single most useful
> thing in security groups and it's one dropdown away."

Then draw the ALB → app → db chain and ask what has to change when the app scales from 2 to 200
instances. (Nothing.)

### Demo 5 — Shodan (Part 4, 3 min)

Open `shodan.io` and search `port:3306 country:"IN"` (or your country). Scroll.

> "Every one of these is a MySQL server on the public internet. None of these people intended
> that. Most of them are one security group rule away from being fine."

Three minutes, and the abstract becomes visceral. Keep it brief and don't click into anything.

### Demo 6 — Assess the default VPC (wrap-up, 5 min)

Run the finished tool against your demo account's default VPC on screen:

```bash
python3 vpc_assess.py --profile demo --include-default-vpc --min-severity MEDIUM
```

No flow logs. Default SG with rules. Every subnet auto-assigning public IPs. A wide-open NACL.

> "This exists in every region of every AWS account, including yours, right now. Nobody made
> these decisions — that's the point. Unconfigured is not the same as safe."

---

## Where students get stuck (and the fix)

| Symptom | Real cause | Say this |
|---|---|---|
| `InvalidParameterValue` on `trusted_admin_cidr` | Left it as `0.0.0.0/0` | "That's the validation block working. Read the error — it's telling you why." |
| Terraform hangs 2–3 min on NAT | Normal provisioning time | "That's the $32/month resource being born. Wait." |
| `InvalidSubnet.Conflict` | `vpc_cidr` overlaps an existing VPC | Change `vpc_cidr` to `10.30.0.0/16` in tfvars |
| `TypeError: NoneType and int` in Python | Read `FromPort` on an `IpProtocol: -1` rule | "AWS omits the ports when the protocol is -1. Handle that case first." |
| `Unknown parameter "Filters"` on NAT | `describe_nat_gateways` uses `Filter`, singular | "It's the only Describe call that does this. Everyone hits it once." |
| Tool reports 0 findings | `create_insecure_examples = false` | Set it true, re-apply |
| Tool flags their own alb/app/db SGs as unused | No EC2 instances exist | "Correct behaviour. Nothing is attached because you built no compute today." |
| `DependencyViolation` on destroy | ENIs still in the subnet | Check for a lingering endpoint or a manually-created instance |
| Confused about which layer blocked traffic | Hasn't internalised the order | Walk the defense-in-depth diagram: route → NACL → SG → host |

---

## Discussion prompts that actually generate discussion

1. *"You have three AZs. How many NAT Gateways do you build in production, and how do you
   justify $97/month to your manager?"*
   → Forces the availability-vs-cost trade-off out into the open. There's no single right answer,
   which is exactly why it's a good prompt.
2. *"Your database is in a private subnet with no internet route. Is a security group on it
   still necessary?"*
   → Yes — the local route means every subnet in the VPC can reach it. Great test of whether
   Part 2.2 landed.
3. *"Given security groups exist, when would you ever bother with a NACL?"*
   → Blocking a specific malicious IP range; a coarse guardrail a team can't accidentally
   remove; compliance requirements that mandate subnet-level controls.
4. *"An engineer says 'I need SSH from anywhere, I work from cafés.' What do you offer them?"*
   → Session Manager, a VPN, or Client VPN. Never the `0.0.0.0/0` rule. Good practice at saying
   no *with an alternative*, which is most of security work.
5. *"Is a security group attached to nothing a real finding?"*
   → Genuinely debatable — same shape as Day 1's unattached-policy question. Best answer: yes,
   because someone will attach it "temporarily to debug" without reading its rules.

---

## Assessment — what "done" looks like

Students have genuinely got Day 2 if they can:

- [ ] Look at a route table and say instantly whether a subnet is public
- [ ] Explain stateful vs stateless without hedging, and name the ephemeral port range
- [ ] Explain why a NACL deny rule numbered 200 under an allow at 100 does nothing
- [ ] Write a security group rule that references another security group, and say why
- [ ] Explain why the data-tier security group needs no egress rules
- [ ] State the NAT Gateway hourly cost from memory
- [ ] Name three ways to avoid needing a NAT Gateway

**Red flag:** a student who says "the private subnet is private because I called it private."
Sit with them and open the route table together. That single misunderstanding causes real
production exposure and it's worth five minutes of one-on-one time.

---

## ⚠️ End-of-class teardown ritual

**Do this on screen, together, before anyone drops off the call.** Not as homework.

```bash
cd day-02-networking-security/lab/terraform
terraform destroy
# type: yes
```

Then, everyone runs and reads out their own result:

```bash
aws ec2 describe-nat-gateways \
  --query 'NatGateways[?State==`available`].NatGatewayId' --output text

aws ec2 describe-addresses \
  --query 'Addresses[?AssociationId==null].PublicIp' --output text
```

Both must be empty. Ask each student to type "clear" in the chat. Do not close the call until
every student has.

The second command matters more than people expect: releasing a NAT Gateway does **not**
release its Elastic IP, and an unattached EIP quietly bills ~$3.60/month.

---

## Bridge to Day 03

> "We now have a network with tiers that can't reach each other except where we said so.
> Tomorrow we put something *in* it — and we make that something survive an instance dying at
> 3 a.m. without anyone waking up. Auto Scaling, load balancers, health checks and self-healing
> compute. The security groups you chained today are exactly what makes tomorrow's Auto Scaling
> group work without editing a single rule when it scales."

Also flag: **Day 3 runs actual EC2 instances**, so today's cost conversation continues. Tell
them the same teardown ritual applies, and that from here on it's part of every session.
