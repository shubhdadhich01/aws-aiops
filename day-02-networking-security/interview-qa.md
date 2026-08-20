# Interview Q&A — Day 02: Enterprise Networking & Security Architecture

15 questions that actually get asked, with the answers interviewers are listening for.

> 💡 **Meta-tip:** networking questions reward *precision*. "Security groups are stateful" is a
> memorised fact. "Security groups are stateful, so a database SG with zero egress rules still
> answers queries — it just can't initiate anything, which is exactly what you want" is
> understanding. Interviewers can hear the difference immediately.

---

### 1. What makes a subnet public?

Its route table has a route for `0.0.0.0/0` pointing at an **Internet Gateway**. That's it.
Nothing else makes a subnet public — not its name, not its tags, not whether the VPC has an
IGW attached, not `MapPublicIpOnLaunch`.

For an instance in that subnet to actually be internet-reachable you need three things
together: the IGW route, a public IP (or EIP) on the instance, and security group plus NACL
rules that permit the traffic. Miss any one and it doesn't work.

The follow-up worth pre-empting: **a subnet with no explicit route table association silently
uses the VPC's main route table.** If that main route table has an IGW route, every subnet you
forgot to associate is public. That's a real, common, invisible exposure — and it's exactly
what the Day 02 assessment tool checks for.

---

### 2. Security groups vs network ACLs — walk me through every difference.

| | Security Group | Network ACL |
|---|---|---|
| Level | ENI (instance/LB/RDS) | Subnet |
| State | **Stateful** | **Stateless** |
| Rules | Allow only | Allow **and** deny |
| Evaluation | All rules; any match allows | Numbered; **first match wins** |
| Default (custom) | Deny in, allow out | Deny both ways |
| Can reference | Other SGs, prefix lists | CIDRs only |
| Applies to | What you attach it to | Everything in the subnet |

The two consequences that matter in practice:

- **Stateless means you must allow the response.** Outbound requests get replies on ephemeral
  ports (1024–65535 for Linux and every AWS service). Forget that rule and everything times out
  while your egress rules look perfectly correct. This is the #1 cause of "I added a NACL and
  broke production."
- **First-match-wins means ordering is a security control.** A deny at rule 200 under an allow-all
  at rule 100 is dead code that looks exactly like a working control in the console.

Close with when you'd use each: security groups for essentially everything; NACLs as a coarse,
subnet-wide guardrail — blocking a known-bad CIDR, or enforcing a boundary that an individual
team can't accidentally remove.

---

### 3. When would you actually use a NACL, given security groups exist?

Three legitimate cases:

1. **Blocking a specific source.** Security groups can't deny. If you need to block one IP range,
   a NACL is the only VPC-native way.
2. **A guardrail with different ownership.** Application teams manage their own security groups.
   The network team owns the NACL. It's a control the app team cannot accidentally undo — same
   argument as an SCP versus an IAM policy.
3. **Compliance.** Some frameworks explicitly require subnet-level network controls, and
   "we have security groups" doesn't satisfy the auditor.

Say the honest part too: for most workloads, NACLs are left permissive and security groups do
the real work. Pretending otherwise is a tell.

---

### 4. Explain NAT Gateway vs Internet Gateway.

An **Internet Gateway** enables bidirectional internet connectivity for resources with public
IPs, in subnets whose route table targets it. It's free, horizontally scaled, and managed.

A **NAT Gateway** enables **outbound-only** connectivity for resources in private subnets. It
translates their private source addresses to its own Elastic IP. Return traffic for connections
they initiated comes back; nothing unsolicited gets in.

The detail that separates candidates: **the NAT Gateway lives in a public subnet but serves the
private ones.** It needs the IGW to reach the internet. Putting it in a private subnet creates
successfully, never works, and bills anyway.

Then name the cost — $0.045/hour plus $0.045/GB, roughly $32/month per gateway — because a
senior candidate knows the price of the components they design with.

---

### 5. How would you reduce NAT Gateway costs?

Answer in the order a real engineer would investigate:

1. **Find out what the traffic actually is.** Flow logs and Cost Explorer's data-transfer
   breakdown will tell you.
2. **VPC endpoints for AWS services.** S3 and DynamoDB **gateway** endpoints are free and remove
   that traffic from NAT entirely. Interface endpoints for ECR, SSM, CloudWatch, Secrets Manager
   cost ~$7.30/AZ but are usually cheaper than the NAT processing they replace, and strictly
   more secure.
3. **Consolidate NATs in non-production.** Dev and staging can share one; the AZ SPOF is an
   acceptable trade there.
4. **Check for chatty workloads** — a container pulling a 2 GB image on every deploy is $0.09
   each time, and an ECR interface endpoint or image caching removes it.
5. **NAT instance** for sandbox only. ~$4/month on a t4g.nano, but you own patching, HA and
   scaling, and you must disable the source/destination check.

The strong version of this answer leads with endpoints, not with "use a NAT instance."

---

### 6. Design a three-tier VPC for a production web application.

- **VPC** `10.0.0.0/16` in one region. Take a `/16` even if you need a `/22` — addresses are
  free, renumbering is a quarter of work.
- **Three subnet tiers across at least two AZs:**
  - public `/24`s — ALB, NAT Gateways, nothing else
  - private-app `/24`s — EC2/ECS, outbound via NAT
  - private-data `/24`s — RDS, ElastiCache, **no internet route in either direction**
- **Routing:** public → IGW; app → NAT (one per AZ in production); data → local only.
- **Security groups chained by reference:** internet → alb-sg:443 → app-sg:8080 → db-sg:5432.
  No CIDRs between tiers. Database SG has no egress rules at all.
- **NACLs** as a coarse second layer, with the ephemeral 1024–65535 inbound rule.
- **Flow logs** on from day one, `traffic_type = ALL`, to CloudWatch or S3.
- **VPC endpoints:** S3 gateway (free) always; SSM interface endpoints so you never need a bastion.
- **Access:** Session Manager, not SSH. No inbound rule at all.
- **Everything in Terraform**, tagged for cost allocation.

Close with the trade-off, because there always is one: one NAT per AZ triples the fixed cost.
State what you'd do in production versus dev, and why.

---

### 7. Why does a private subnet still need security groups?

Because every route table has an **implicit `local` route for the VPC CIDR that you cannot
remove or override**. Every subnet in a VPC can always reach every other subnet, regardless of
route tables and regardless of tiering.

So the data tier's isolation from the internet comes from routing, but its isolation from a
compromised web server in the public subnet comes entirely from the security group. Routing
protects you from outside; security groups protect you from inside.

That's the answer to "we're in a private subnet, do we really need this rule?" — yes, because
the attacker is already inside the VPC by the time it matters.

---

### 8. What is the ephemeral port range and why does it matter?

When a client opens a connection, it picks a random high-numbered source port. The server's
response is addressed back to that port. **Linux and all AWS-managed services (NAT Gateway, ELB,
Lambda) use 1024–65535.** Windows Server 2008+ uses 49152–65535.

It matters because **NACLs are stateless**. Your outbound rule allows the request to leave, but
unless you also have an inbound rule covering the ephemeral range, the response is silently
dropped. Everything times out, and your egress rules look perfectly correct while you debug.

Security groups don't have this problem — they track connection state and allow the return
automatically. This is the single clearest practical consequence of stateful vs stateless, and
it's why the question gets asked.

---

### 9. How do you connect multiple VPCs?

| Option | Transitive | Scale | When |
|---|---|---|---|
| **VPC Peering** | ❌ no | mesh, n(n-1)/2 | 2–5 VPCs, simple, no data charge within an AZ |
| **Transit Gateway** | ✅ yes | hub-and-spoke, thousands | 5+ VPCs or hybrid. ~$36/mo/attachment + $0.02/GB |
| **PrivateLink** | n/a | one service | Expose *one service*, not the network |
| **Site-to-Site VPN** | n/a | ~1.25 Gbps/tunnel | Hybrid over the internet, encrypted |
| **Direct Connect** | n/a | 50 Mbps–100 Gbps | Dedicated circuit, predictable latency |

**Peering is not transitive.** A↔B and B↔C does not give you A↔C. This is asked constantly and
it's the reason Transit Gateway exists.

Two more things worth adding: CIDRs must not overlap for peering or TGW (which is why CIDR
planning is a day-one decision), and **PrivateLink is the right answer when a vendor or another
team needs access to one service rather than to your network** — it's the least-privilege option
of the five.

---

### 10. What are VPC Flow Logs and what would you use them for?

Metadata about IP traffic: source, destination, source and destination ports, protocol, packet
and byte counts, and the `ACCEPT`/`REJECT` action. **Not** packet contents — for payloads you
need Traffic Mirroring.

Uses:

- **Security investigation.** After an incident, flow logs are frequently the only record of
  what talked to what.
- **Detection.** A burst of REJECTs on port 22 from one external IP is a scan. An ACCEPT from
  your database tier to an unexpected external IP is an incident in progress.
- **Troubleshooting.** REJECT tells you a security group or NACL blocked it, which halves your
  debugging time.
- **Cost analysis.** Identifying which workloads are driving NAT Gateway data processing charges.

The sentence that lands: **there is no backfill.** You enable them before the incident or you
investigate without evidence. That's why a missing flow log is a HIGH finding, not a LOW one —
the severity is about the investigation you won't be able to do.

---

### 11. How do you access an EC2 instance in a private subnet?

Answer with the modern option first:

**AWS Systems Manager Session Manager.** The SSM agent makes an *outbound* connection to the
Systems Manager service. There is no inbound security group rule, no public IP, no bastion host
and no SSH key. Access is authorised by IAM policy, and every session is logged to S3 or
CloudWatch. The instance needs either a NAT route or — better — SSM, SSMMessages and EC2Messages
interface endpoints, plus an instance profile with `AmazonSSMManagedInstanceCore`.

Then mention the alternatives and why they're second choices: a **bastion host** (a permanently
running instance with an inbound port and a key to manage), **EC2 Instance Connect Endpoint**
(SSH without a bastion, still SSH), or **Client VPN / Site-to-Site VPN** when you need general
network access rather than shell access.

The point to land: *"the best answer to 'how do you open port 22 to a private instance' is
'I don't.'"*

---

### 12. A user reports they can't reach an application. Walk me through your debugging.

Follow the packet, in order — and say you're following the packet, because the structure is
what's being assessed:

1. **Does DNS resolve?** `dig`. Is it returning the right address?
2. **Is there a route?** Does the source subnet's route table have a path to the destination?
   Is the destination subnet's route table able to reply?
3. **NACLs** — both directions, both subnets. Check the ephemeral range on the return path.
   Remember first-match-wins ordering.
4. **Security groups** — inbound on the destination. Check the *source*: is it a CIDR that
   actually covers the client, or an SG the client isn't a member of?
5. **Is the target healthy?** Load balancer target group health checks, and the health check's
   own security group path.
6. **Is the process listening?** `ss -tlnp` on the right interface, not just localhost.
7. **Host firewall** — iptables/nftables/Windows Firewall.

Then the tooling: **VPC Reachability Analyzer** does steps 2–4 automatically and tells you which
component blocked it. **Flow logs** show REJECT, which confirms it's a VPC-layer control rather
than the application.

Mentioning Reachability Analyzer is a strong signal — it's the tool people who do this for real
reach for first.

---

### 13. What's the difference between a gateway endpoint and an interface endpoint?

**Gateway endpoint:** S3 and DynamoDB only. It works by adding a prefix-list route to your route
tables — traffic to those services never leaves the AWS network and never touches your NAT
Gateway. **Free.** There is no good reason not to have the S3 one in every VPC you build.

**Interface endpoint (PrivateLink):** an ENI with a private IP in your subnet, for ~150 AWS
services and for third-party or cross-account services. Costs ~$0.01/hour per AZ plus $0.01/GB.
It has a security group, supports on-premises access over VPN/Direct Connect, and works across
VPCs — none of which gateway endpoints do.

The security point worth adding: both support **endpoint policies**, so you can restrict *which
S3 buckets* are reachable from this VPC. That's a genuinely strong data-exfiltration control
that almost nobody turns on.

---

### 14. Your database was found publicly accessible. How did that happen, and how do you prevent it recurring?

Name the plausible causes, because the interviewer wants to know you've seen this:

- The subnet's route table had an IGW route — often inherited from the **main route table**
  because nobody explicitly associated the subnet.
- `MapPublicIpOnLaunch` was true, so the instance got a public IP by default.
- The security group had `0.0.0.0/0` on 5432 or 3306, usually added "temporarily to debug".
- RDS was created with `PubliclyAccessible = true`, which is a separate setting people forget.
- IPv4 was locked down and `::/0` was left open.

Prevention, layered:

- **Preventive:** SCPs blocking `ec2:AuthorizeSecurityGroupIngress` with `0.0.0.0/0` on admin
  ports; data subnets with no IGW route at all; Terraform modules that make the safe path the
  default path.
- **Detective:** an automated scanner in CI that fails the build (*"I've built exactly this —
  it's a boto3 tool with a `--fail-on CRITICAL` flag"*), plus AWS Config rules and Security Hub.
- **Responsive:** EventBridge on `AuthorizeSecurityGroupIngress` → Lambda that auto-revokes
  world-open admin rules and notifies the team.

This is the question where you talk about the Day 02 lab explicitly. It's the strongest thing
you can bring to it.

---

### 15. Design network security for a regulated multi-account environment.

Structure it:

- **Multi-account** via Organizations — separate accounts for network, security/log-archive,
  shared services, and each environment. Blast radius is an account boundary.
- **Centralised egress.** A network account hosts Transit Gateway plus a shared egress VPC with
  NAT Gateways and firewall inspection. Workload VPCs have no IGW at all — they route
  `0.0.0.0/0` to the TGW. That single decision removes an entire class of accidental exposure.
- **AWS Network Firewall** or a third-party appliance in the inspection VPC for east-west and
  egress filtering, with domain allowlisting.
- **PrivateLink** for cross-account service exposure, so teams share *services*, never networks.
- **SCPs** as guardrails: deny detaching flow logs, deny creating IGWs in workload accounts,
  deny `0.0.0.0/0` on admin ports, deny unapproved regions.
- **Centralised DNS** — Route 53 Resolver rules and endpoints in the network account, with
  query logging.
- **Flow logs from every VPC** to the log-archive account's S3 with Object Lock, queried via
  Athena.
- **IPAM** for CIDR allocation, so overlap is structurally impossible rather than a convention.
- **Everything as code**, with `tfsec`/`checkov` in CI and a scanner that fails builds on
  critical findings.

Close with the trade-off: centralised egress adds latency and makes the network account a
critical dependency, so it needs its own HA design and a very clear on-call owner. Every
architecture answer should end with what it costs you — that's what distinguishes a senior
answer from a memorised one.

---

## Rapid-fire round

| Question | Answer |
|---|---|
| Is a VPC regional or zonal? | **Regional** — it spans all AZs. A **subnet** is zonal. |
| How many IPs does AWS reserve per subnet? | **5** — network, VPC router, DNS, future use, broadcast. A /24 gives 251 usable. |
| Smallest and largest subnet? | /28 (16 addresses) to /16 (65,536). |
| Can you change a VPC's primary CIDR? | No. You can **add** secondary CIDRs; you cannot change or shrink the primary. |
| Are security groups stateful? | Yes. NACLs are stateless. |
| Can a security group deny traffic? | No — allow-only. Anything unmatched is dropped. |
| How are NACL rules evaluated? | Lowest rule number first; **first match wins**; implicit deny at `*`. |
| Ephemeral port range to allow? | **1024–65535** (Linux and all AWS services). |
| Where does a NAT Gateway live? | A **public** subnet. It serves the private ones. |
| NAT Gateway cost? | ~$0.045/hr (~$32/month) + $0.045/GB processed. |
| Is VPC peering transitive? | **No.** That's what Transit Gateway is for. |
| Which endpoints are free? | **Gateway** endpoints — S3 and DynamoDB only. |
| Do flow logs capture packet contents? | No, metadata only. Use Traffic Mirroring for payloads. |
| Can you enable flow logs retroactively? | No. There is no backfill. |
| Max security groups per ENI? | 5 by default (raisable to 16); 60 rules in and 60 out per group. |
| Default NACL behaviour? | The **AWS-created** one allows everything both ways. A **custom** one denies everything both ways. |
| Default security group behaviour? | Allows all traffic from itself; allows all outbound. CIS says strip it. |
| What's the IPv6 equivalent of a NAT Gateway? | An **egress-only Internet Gateway**. Free. |
| Which tool tells you why a packet was blocked? | **VPC Reachability Analyzer** — it names the blocking component. |
| Cost of an unattached Elastic IP? | ~$0.005/hr (~$3.60/month). Attached to a running resource: free. |
