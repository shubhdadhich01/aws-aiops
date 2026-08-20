# Day 02 Diagrams

Mermaid source for every diagram used in the Day 02 guide. GitHub renders these natively.
Copy any block into <https://mermaid.live> to edit, or export as PNG/SVG for slides.

---

## 1. Three-tier VPC topology ⭐

```mermaid
flowchart TB
    subgraph VPC["VPC · 10.20.0.0/16 · us-east-1"]
        direction TB
        subgraph AZA["Availability Zone A"]
            PUBA["🌐 public<br/>10.20.0.0/24"]
            APPA["⚙️ private-app<br/>10.20.10.0/24"]
            DATA["🔒 private-data<br/>10.20.20.0/24"]
        end
        subgraph AZB["Availability Zone B"]
            PUBB["🌐 public<br/>10.20.1.0/24"]
            APPB["⚙️ private-app<br/>10.20.11.0/24"]
            DATB["🔒 private-data<br/>10.20.21.0/24"]
        end
    end
    IGW["🚪 Internet Gateway"] --- PUBA
    IGW --- PUBB
    PUBA --> NAT["💸 NAT Gateway<br/>~$32/month"]
    NAT --> APPA
    NAT --> APPB
    APPA --> DATA
    APPB --> DATB
    style DATA fill:#1a44b8,color:#fff
    style DATB fill:#1a44b8,color:#fff
    style NAT fill:#e67e22,color:#fff
    style IGW fill:#27ae60,color:#fff
```

## 2. Route table anatomy

```mermaid
flowchart LR
    subgraph RT["Route table: cbc-day02-rt-private-app"]
        R1["10.20.0.0/16 → local<br/><i>implicit · cannot be removed</i>"]
        R2["0.0.0.0/0 → nat-0abc123"]
        R3["pl-63a5400a (S3) → vpce-0def456"]
    end
    R1 --> IN["Traffic inside the VPC"]
    R2 --> OUT["Outbound internet via NAT"]
    R3 --> S3["S3, without touching NAT"]
    style R1 fill:#95a5a6,color:#fff
    style R3 fill:#27ae60,color:#fff
```

## 3. NAT Gateway traffic flow

```mermaid
flowchart LR
    subgraph PRIV["private-app subnet"]
        EC2["EC2<br/>10.20.10.42<br/>no public IP"]
    end
    subgraph PUB["public subnet"]
        NATGW["💸 NAT Gateway<br/>EIP 54.x.x.x"]
    end
    EC2 -->|"1. outbound<br/>src 10.20.10.42"| NATGW
    NATGW -->|"2. rewritten<br/>src 54.x.x.x"| IGW["🚪 IGW"]
    IGW --> NET["🌐 Internet"]
    NET -.->|"3. response"| IGW
    IGW -.-> NATGW
    NATGW -.->|"4. translated back"| EC2
    NET -.->|"❌ unsolicited inbound"| BLOCK["dropped"]
    style NATGW fill:#e67e22,color:#fff
    style BLOCK fill:#c0392b,color:#fff
```

## 4. Stateful vs stateless ⭐

```mermaid
flowchart TB
    subgraph SG["🛡️ Security Group — STATEFUL"]
        S1["Instance → 443 outbound"] --> S2["Connection tracked"]
        S2 --> S3["✅ Response allowed back<br/>automatically"]
    end
    subgraph NACL["🧱 Network ACL — STATELESS"]
        N1["Instance → 443 outbound"] --> N2["Egress rule: allow 443 ✅"]
        N2 --> N3["Response arrives on<br/>ephemeral port 51234"]
        N3 --> N4["❌ DROPPED unless you also<br/>allow inbound 1024-65535"]
    end
    style S3 fill:#27ae60,color:#fff
    style N4 fill:#c0392b,color:#fff
```

## 5. Security group chaining ⭐

```mermaid
flowchart LR
    NET["🌐 Internet"] -->|":443 from 0.0.0.0/0"| ALB["alb-sg"]
    ALB -->|":8080 from alb-sg"| APP["app-sg"]
    APP -->|":5432 from app-sg"| DB["db-sg"]
    BAS["bastion-sg<br/>:22 from YOUR /32"] -->|":22 from bastion-sg"| APP
    style ALB fill:#27ae60,color:#fff
    style DB fill:#1a44b8,color:#fff
    style BAS fill:#8e44ad,color:#fff
```

## 6. Defense in depth — the packet's journey ⭐

```mermaid
flowchart TD
    A["🌐 Packet from the internet"] --> B{"Route table<br/>is there even a path?"}
    B -->|"no route"| X1["❌ unreachable"]
    B -->|"route exists"| C{"NACL inbound<br/>stateless, first match wins"}
    C -->|"deny / no match"| X2["❌ dropped at the subnet"]
    C -->|"allow"| D{"Security group<br/>stateful, allow-only"}
    D -->|"no matching rule"| X3["❌ dropped at the ENI"]
    D -->|"allow"| E{"Host firewall<br/>iptables / nftables"}
    E -->|"deny"| X4["❌ dropped at the OS"]
    E -->|"allow"| F["✅ reaches the application"]
    style X1 fill:#c0392b,color:#fff
    style X2 fill:#c0392b,color:#fff
    style X3 fill:#c0392b,color:#fff
    style X4 fill:#c0392b,color:#fff
    style F fill:#27ae60,color:#fff
```

## 7. VPC endpoints — three paths to S3

```mermaid
flowchart LR
    subgraph VPC["Your VPC"]
        EC2["EC2 in private subnet"]
    end
    EC2 -->|"❌ the expensive way"| NAT["💸 NAT Gateway<br/>$0.045/GB"] --> IGW["IGW"] --> S3PUB["S3 public endpoint"]
    EC2 -->|"✅ gateway endpoint · FREE"| VPCE["Route table entry<br/>pl-xxxx → vpce-xxxx"] --> S3["S3"]
    EC2 -->|"✅ interface endpoint · $0.01/hr/AZ"| ENI["ENI in your subnet<br/>PrivateLink"] --> SSM["SSM · Secrets Manager · KMS"]
    style NAT fill:#c0392b,color:#fff
    style VPCE fill:#27ae60,color:#fff
    style ENI fill:#1a44b8,color:#fff
```

## 8. NACL rule shadowing

```mermaid
flowchart TD
    P["📦 Inbound packet<br/>tcp/22 from 1.2.3.4"] --> R100{"Rule 100<br/>ALLOW all from 0.0.0.0/0"}
    R100 -->|"✅ MATCH — evaluation stops here"| PASS["Packet allowed"]
    R100 -.->|"never reached"| R200{"Rule 200<br/>DENY tcp/22 from 0.0.0.0/0"}
    R200 -.->|"never reached"| RSTAR["Rule *<br/>implicit DENY"]
    R200 --- DEAD["💀 DEAD CODE<br/>looks like a control<br/>in the console"]
    style PASS fill:#c0392b,color:#fff
    style DEAD fill:#c0392b,color:#fff
    style R200 fill:#95a5a6,color:#fff
    style RSTAR fill:#95a5a6,color:#fff
```

## 9. Lab architecture

```mermaid
flowchart TB
    subgraph TF["🏗️ Terraform"]
        VPC["VPC + 6 subnets"]
        RT["Route tables<br/>public / app / data"]
        NATG["💸 NAT Gateway"]
        SG["4 chained SGs"]
        NACL["2 NACLs"]
        FL["Flow Logs"]
        EP["S3 endpoint"]
        BAD["😈 broken networking"]
    end
    subgraph PY["🐍 Python"]
        TOOL["vpc_assess.py<br/>19 checks"]
    end
    TF --> AWS[("AWS VPC")]
    AWS -->|"ec2:Describe* read-only"| TOOL
    TOOL --> R1["📊 Table"]
    TOOL --> R2["📄 JSON"]
    TOOL --> R3["📈 CSV"]
    style NATG fill:#e67e22,color:#fff
    style BAD fill:#c0392b,color:#fff
    style TOOL fill:#f39c12,color:#fff
    style EP fill:#27ae60,color:#fff
```

## 10. Connecting VPCs — peering is not transitive

```mermaid
flowchart TB
    subgraph MESH["VPC Peering — no transitivity"]
        A1["VPC A"] <-->|"peering"| B1["VPC B"]
        B1 <-->|"peering"| C1["VPC C"]
        A1 -.->|"❌ cannot reach"| C1
    end
    subgraph HUB["Transit Gateway — hub and spoke"]
        A2["VPC A"] <--> TGW["Transit Gateway"]
        B2["VPC B"] <--> TGW
        C2["VPC C"] <--> TGW
        ONP["🏢 On-premises<br/>via VPN / DX"] <--> TGW
    end
    style TGW fill:#1a44b8,color:#fff
```

## 11. Bastion host vs Session Manager

```mermaid
flowchart LR
    subgraph OLD["❌ Bastion pattern"]
        U1["Engineer"] -->|"ssh :22"| BAS["Bastion in public subnet<br/>public IP · inbound rule · SSH key"]
        BAS -->|"ssh :22"| I1["Private instance"]
    end
    subgraph NEW["✅ Session Manager"]
        U2["Engineer"] -->|"IAM-authorised"| SSM["Systems Manager"]
        I2["Private instance<br/>NO public IP<br/>NO inbound rule"] -->|"outbound only"| SSM
    end
    style BAS fill:#c0392b,color:#fff
    style I2 fill:#27ae60,color:#fff
```
