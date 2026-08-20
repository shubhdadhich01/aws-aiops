<!-- ═══════════════════════════════════════════════════════════════════════════ -->
<!--                                                                             -->
<!--  CBC Repo Playbook                                                          -->
<!--                                                                             -->
<!--  This file lives in EVERY CareerByteCode GitHub repo alongside README.md.   -->
<!--  It defines the rules that keep every repo on-brand.                        -->
<!--                                                                             -->
<!--  If you're maintaining this repo or making a PR — read this once.           -->
<!--  If you're publishing a new CBC repo — follow the checklist in §11.         -->
<!--                                                                             -->
<!-- ═══════════════════════════════════════════════════════════════════════════ -->

<div align="center">

<img src="https://raw.githubusercontent.com/careerbytecode/CBC_Branding_Kit/main/01_Logos/full%20logo/careerbytecode-logo-transparent.png" alt="CareerByteCode" width="220"/>

# 📘 CBC Repo Playbook

### **The brand & structure rules for every CareerByteCode GitHub repo.**

![Version](https://img.shields.io/badge/Version-1.0-1847c8?style=for-the-badge&labelColor=0a1628)
![Applies to](https://img.shields.io/badge/Applies%20to-Every%20CBC%20repo-0ea5e9?style=for-the-badge&labelColor=0a1628)
![Read time](https://img.shields.io/badge/Read%20time-15%20min-1847c8?style=for-the-badge&labelColor=0a1628)

</div>

---

## 📋 Contents

1. [Purpose](#1-purpose)
2. [The CBC Brand Framework](#2-the-cbc-brand-framework)
3. [Repo Naming Convention](#3-repo-naming-convention)
4. [Repo Types](#4-repo-types)
5. [Mandatory Files](#5-mandatory-files-in-every-repo)
6. [README Structure — Fixed vs. Fill-In](#6-readme-structure--fixed-vs-fill-in)
7. [Placeholder Reference](#7-placeholder-reference--what-each--means)
8. [Copy-Paste Blocks](#8-copy-paste-blocks)
9. [Do's and Don'ts](#9-dos-and-donts)
10. [Voice, Tone, Formatting](#10-voice-tone-formatting)
11. [Publishing Checklist](#11-publishing-checklist)
12. [Migrating Older Repos](#12-migrating-older-repos)
13. [Governance & Contact](#13-governance--contact)

---

## 1. Purpose

CareerByteCode publishes many GitHub repos — labs, playbooks, bootcamps, portfolio projects, event materials. Without a shared standard, each one looks different, credibility feels uneven, and visitors can't tell which repos are official.

This playbook fixes that. It lives in **every** CBC repo as `playbook.md` alongside `README.md`. Follow it and every repo:

- Looks like it belongs to one credible global platform
- Presents CBC as **three things at once** — Learning · Community · Freelancing
- Gives visitors the same paths to community, events, and earning
- Ships with zero brand friction for the team

---

## 2. The CBC Brand Framework

### 2.1 The Positioning Statement

> **CareerByteCode is a Europe-registered global tech ecosystem** (Cloudnloud Technologies SRL, Belgium) — one community that is a **Learning Platform**, a **Freelancing & Earning Platform**, and a **Large Global Tech Community** at the same time. Since **2015**, we've helped **2.4M+ professionals across 105+ countries** grow from learners to leaders through real projects, mentorship, and paid opportunities.

This statement (or a shorter version of it) appears in every repo's README. Do not rewrite it.

### 2.2 The Triple Positioning

Every CBC repo presents the platform as **three things at once**:

| Pillar | Canonical URL |
|--------|---------------|
| 🎓 **Learning Platform** | https://www.careerbytecode.in/ |
| 🌐 **Large Tech Community** | https://www.careerbytecode.in/community/ |
| 💼 **Freelancing & Earning Platform** | https://www.careerbytecode.in/collaborate/ |

If a visitor lands on your repo and clicks away without seeing all three, the repo is off-brand.

### 2.3 The Master Tagline

> **Learn. Build. Collaborate. Earn.**

Appears in the About CBC footer of every repo. The shorter working tagline **"Learning Made simple"** appears in the top brand strip.

### 2.4 Brand Colours

| Colour | Hex | Usage |
|--------|-----|-------|
| **CBC Blue** (primary) | `#1847c8` | Primary CTA, links, primary badges, hero highlights |
| **CBC Navy** (deep) | `#0a1628` | Label backgrounds, dark accents |
| **CBC Sky** (accent) | `#0ea5e9` | Gradient accents, secondary badges, community pillar |
| White | `#ffffff` | Backgrounds, on-navy text |
| Neutral text | `#4a4a4a` | Body copy |

Do not introduce new brand colours. Technology-specific accents (e.g. AWS orange) may appear inside a technology section but never in CBC branding elements.

### 2.5 Metrics You Can Cite

- **2.4M+ community members**
- **105+ countries**
- **2,500+ real-world projects** (targeting 5,000+)
- **Since 2015**
- **Cloudnloud Technologies SRL, Belgium** (legal entity)

Do not invent new metrics or upgrade the numbers without clearance.

---

## 3. Repo Naming Convention

### 3.1 The Format

```
cbc-{track}-{topic}[-{qualifier}]
```

- All lowercase
- Hyphens between words
- `cbc-` prefix always
- Track name second
- Topic is specific

### 3.2 Tracks (Fixed List)

| Track | Prefix | Use For |
|-------|--------|---------|
| Cloud | `cbc-cloud-` | AWS, Azure, GCP, multi-cloud, migration, cost |
| DevOps | `cbc-devops-` | CI/CD, Terraform, Ansible, GitHub Actions, GitLab |
| Kubernetes | `cbc-k8s-` | Kubernetes, containers, service mesh, GitOps |
| AI/ML | `cbc-ai-` | LLM, GenAI, RAG, agents, MLOps, prompt engineering |
| Data | `cbc-data-` | Data engineering, pipelines, warehousing, analytics |
| Security | `cbc-security-` | DevSecOps, cloud security, compliance, IAM, offsec |
| Fullstack | `cbc-fullstack-` | React, Node, MERN, frontend, backend |
| Python | `cbc-python-` | Python fundamentals, scripting, automation |
| Career | `cbc-career-` | Self-branding, interview prep, resumes, LinkedIn |
| Community | `cbc-community-` | Chapters, events, podcasts, LinkedIn Live |
| Ops | `cbc-ops-` | Internal ops, playbooks, templates, brand |

### 3.3 Examples

| ✅ Good | ❌ Bad | Why Bad |
|--------|--------|---------|
| `cbc-cloud-aws-eks-lab` | `AWS-EKS-Lab` | Uppercase, no `cbc-` prefix |
| `cbc-devops-terraform-azure` | `terraform_azure_stuff` | Underscores, no prefix, "stuff" |
| `cbc-ai-rag-langchain-bootcamp` | `MyRAGProject` | CamelCase, no context, no prefix |
| `cbc-career-linkedin-100day-plan` | `linkedin-plan` | No `cbc-`, no track |

### 3.4 Reserved Names

`team-main`, `careerbytecode.github.io`, `.github` — do not rename. These are the org's meta-repos.

### 3.5 Length Rule

Aim for 3–5 hyphen-separated parts. If you're writing `cbc-cloud-aws-eks-terraform-hands-on-lab-v2`, split it into two repos.

---

## 4. Repo Types

Pick the type first — the mandatory sections stay the same, but optional sections differ.

| Type | Purpose | Example |
|------|---------|---------|
| **Lab** | Hands-on code you run to learn | `cbc-cloud-aws-eks-lab` |
| **Playbook** | Written guide, no code | `cbc-career-linkedin-100day-plan` |
| **Bootcamp** | Multi-day / multi-week program | `cbc-ai-rag-langchain-bootcamp` |
| **Portfolio Project** | Full working app for learners to fork | `cbc-fullstack-devscore-mern` |
| **Event Material** | Slides + code + resources from an event | `cbc-ai-agentic-devops-jul2026` |
| **Ops** | Internal templates & tools | `cbc-ops-repo-template` |

Recommended optional sections per type:

| Type | Add These Sections |
|------|-------------------|
| Lab | Prerequisites, Quick Start, Architecture Diagram, Cleanup |
| Playbook | Table of Contents, Weekly/Daily Structure, Templates |
| Bootcamp | Curriculum by Day, Schedule, Speakers, Prerequisites, Certification |
| Portfolio Project | Architecture, Demo, Deployment, Roadmap |
| Event Material | Event Date, Speaker Bio, Session Recording, Certificate Link |
| Ops | Usage Instructions, Contributor Guidelines |

---

## 5. Mandatory Files In Every Repo

| File | Purpose | Editable? |
|------|---------|-----------|
| `README.md` | The front door — placeholder scaffold + fixed brand blocks | Yes — fill placeholders; do NOT edit fixed blocks |
| `playbook.md` | This file — brand & structure rules | No — sync from template |
| `LICENSE` | Legal | Yes — pick per §5.1 |

### 5.1 License Guidance

Pick the license by what the repo contains, not by preference.

| Repo Contains | License | Why |
|---------------|---------|-----|
| Code labs, demos, working apps, scripts | **MIT** | Permissive, universally recognized |
| Written playbooks, guides, handbooks | **CC-BY-SA 4.0** | Content deserves attribution; share-alike keeps derivatives open |
| Bootcamps (code + docs) | **MIT for code, CC-BY-SA 4.0 for docs** | Split license — add `LICENSE-DOCS.md` |
| Internship / bootcamp materials to protect | **CC-BY-NC 4.0** | NC prevents rebranded resale |
| Ops / templates | **MIT** | Anyone in the community can reuse |

### 5.2 Copyright Line

Every LICENSE uses this copyright line:

```
Copyright (c) 2015-2026 CareerByteCode / Cloudnloud Technologies SRL, Belgium
```

Do not put personal names in the copyright.

---

## 6. README Structure — Fixed vs. Fill-In

The `README.md` in every CBC repo has three visual zones:

### 6.1 Top Brand Strip — **FIXED, do not edit**

The centered CBC logo, tagline, and three CBC pillar badges at the top.

### 6.2 Repo Hero + Content — **FILL WITH YOUR CONTENT**

Every `{{ PLACEHOLDER }}` is a spot for repo-specific content. Do project-wide find/replace before publishing. See §7 for what each placeholder means.

Mandatory sections in this zone:

1. Repo Hero (title, tagline, one-liner, badges)
2. About This Repo
3. What You'll Learn (or What You'll Build)
4. Repo Structure
5. Getting Started (Prerequisites + Quick Start) — for code repos
6. Who This Is For
7. Contributing
8. License

Optional sections (add if relevant):

- Architecture Diagram
- Cleanup / Teardown
- Curriculum / Modules (for bootcamps)
- Speaker Bio (for event repos)
- Demo / Screenshots
- Roadmap

### 6.3 About CBC Block — **FIXED, do not edit**

The waving capsule banner, positioning statement, metrics, three-pillar table, Sangeetha CTA, Founding Team, Connect & Collaborate, contact strip, waving footer, visitor counter, copyright.

**The only thing you change in this block is the `page_id` on the visitor badge** — it must be `page_id=careerbytecode.{{ REPO_NAME }}` where `{{ REPO_NAME }}` is your actual repo name (so each repo gets its own counter).

---

## 7. Placeholder Reference — What Each `{{ }}` Means

Do a project-wide find/replace of these before pushing.

### 7.1 Repo Identity

| Placeholder | Fill With | Example |
|-------------|-----------|---------|
| `{{ REPO_NAME }}` | The actual repo name | `cbc-cloud-aws-eks-lab` |
| `{{ REPO_TITLE }}` | Title Case name for humans | `AWS EKS Terraform Lab` |
| `{{ REPO_TAGLINE }}` | One-line hook | `Ship a production-shape EKS cluster in 45 minutes.` |
| `{{ REPO_ONE_LINER }}` | 15–25 word description | `A hands-on Terraform lab that provisions a production-shape EKS cluster on AWS, with clear modules and cleanup steps.` |
| `{{ REPO_QUOTE_OR_MISSION }}` | The "why" of this repo (blockquote) | `Kubernetes on cloud shouldn't require a two-day tutorial. Ship the cluster; understand every line.` |

### 7.2 Repo Description

| Placeholder | Fill With |
|-------------|-----------|
| `{{ REPO_LONG_DESCRIPTION_PARAGRAPH_1 }}` | First paragraph — what this repo is and who it's for |
| `{{ REPO_LONG_DESCRIPTION_PARAGRAPH_2 }}` | Second paragraph — what a visitor walks away with |

### 7.3 Repo Metadata Badges

| Placeholder | Fill With | Example |
|-------------|-----------|---------|
| `{{ TRACK }}` | Track name from §3.2 | `Cloud`, `DevOps`, `AI` |
| `{{ LEVEL }}` | Difficulty | `Beginner`, `Intermediate`, `Advanced` |
| `{{ TIME_ESTIMATE }}` | Time to complete | `45%20min`, `2%20hours`, `1%20week` (URL-encode spaces) |
| `{{ LICENSE_NAME }}` | License name | `MIT`, `CC--BY--SA%204.0` |

### 7.4 What You'll Learn Table

| Placeholder | Fill With |
|-------------|-----------|
| `{{ TOPIC_1 }}` … `{{ TOPIC_4 }}` | Topic names (4 rows) |
| `{{ OUTCOME_1 }}` … `{{ OUTCOME_4 }}` | Concrete outcomes ("You'll be able to X") |

### 7.5 Repo Structure Tree

| Placeholder | Fill With |
|-------------|-----------|
| `{{ YOUR_CONTENT_FOLDER_1 }}` … `_3` | Your actual folder names |
| `{{ FOLDER_1_DESCRIPTION }}` … `_3` | One-line description of each |

### 7.6 Prerequisites

| Placeholder | Fill With |
|-------------|-----------|
| `{{ REQ_1 }}` … `{{ REQ_3 }}` | Tool / requirement name |
| `{{ VER_1 }}` … `{{ VER_3 }}` | Version constraint |
| `{{ NOTE_1 }}` … `{{ NOTE_3 }}` | Notes / install hint |

### 7.7 Quick Start

| Placeholder | Fill With |
|-------------|-----------|
| `{{ STEP_2_TITLE }}`, `{{ STEP_2_COMMAND }}` | Step 2 (step 1 is always `git clone`) |
| `{{ STEP_3_TITLE }}`, `{{ STEP_3_COMMAND }}` | Step 3 |

### 7.8 Who This Is For

| Placeholder | Fill With |
|-------------|-----------|
| `{{ START_HERE_NEW }}` | Where new learners start (link or folder) |
| `{{ START_HERE_PRO }}` | Where working professionals go |
| `{{ START_HERE_TRAINER }}` | Where trainers/speakers go |
| `{{ START_HERE_FREELANCER }}` | Where freelancers building a portfolio start |

### 7.9 Verify Before Push

Run this to confirm no unfilled placeholders remain:

```bash
grep "{{ " README.md
```

Should return **zero matches**. If any come back, fill them.

---

## 8. Copy-Paste Blocks

The exact copy that appears verbatim in every CBC repo. Copy from here if you need to add it somewhere.

### 8.1 The Triple Positioning Block

```markdown
> **CareerByteCode is a Europe-registered global tech ecosystem** (Cloudnloud Technologies SRL, Belgium) — one community that is a **Learning Platform**, a **Freelancing & Earning Platform**, and a **Large Global Tech Community** at the same time. Since **2015**, we've helped **2.4M+ professionals across 105+ countries** grow from learners to leaders through real projects, mentorship, and paid opportunities.
```

### 8.2 The Three Pillars Table

```markdown
| Pillar | What It Means | Where To Start |
|:------:|---------------|:--------------:|
| 🎓 **Learning Platform** | Structured courses, real-world labs, live sessions, hands-on projects across Cloud, DevOps, AI/ML, Data, Kubernetes, Security. | [Explore](https://www.careerbytecode.in/) |
| 🌐 **Large Tech Community** | 2.4M+ professionals across 105+ countries. Peer support, mentorship, chapters, events, podcasts. **Join to grow your career and self-visibility.** | [Join](https://www.careerbytecode.in/community/) |
| 💼 **Freelancing & Earning Platform** | *"Whoever you are, the route is the same idea: build your brand, get mentored to earn directly, then grow through the community into our free Expert tier."* | [Start Earning](https://www.careerbytecode.in/collaborate/) |
```

### 8.3 The Contact Strip

```markdown
📧 [support@careerbytecode.in](mailto:support@careerbytecode.in) · 📱 +32 471 40 89 08 · 📲 WhatsApp +32 471 40 89 08
```

### 8.4 The Standard Badge Row (top brand strip)

```markdown
[![Learning](https://img.shields.io/badge/🎓%20Learning-Platform-1847c8?style=for-the-badge&labelColor=0a1628)](https://www.careerbytecode.in/)
[![Community](https://img.shields.io/badge/🌐%20Community-2.4M%2B%20Members-0ea5e9?style=for-the-badge&labelColor=0a1628)](https://www.careerbytecode.in/community/)
[![Freelancing](https://img.shields.io/badge/💼%20Freelancing-Earn%20With%20Us-1847c8?style=for-the-badge&labelColor=0a1628)](https://www.careerbytecode.in/collaborate/)
```

### 8.5 The Sangeetha CTA

```markdown
[![Talk to Sangeetha](https://img.shields.io/badge/💬%20Connect%20with%20Sangeetha-Director%2C%20CareerByteCode%20%2F%20Partnerships-1847c8?style=for-the-badge&labelColor=0a1628)](https://www.linkedin.com/in/careerbytecode)
```

### 8.6 The Copyright Line

```markdown
***© 2015–2026 CareerByteCode · Learn. Build. Collaborate. Earn.***

*a platform by Cloudnloud Technologies SRL, Belgium*
```

### 8.7 Waving Capsule Banners (fixed URLs)

**Header:**
```
https://capsule-render.vercel.app/api?type=waving&color=0a1628,1847c8,0ea5e9&height=180&section=header&text=About%20CareerByteCode&fontSize=42&fontColor=ffffff&animation=fadeIn&fontAlignY=40&desc=Learn.%20Build.%20Collaborate.%20Earn.&descSize=18&descAlignY=68
```

**Footer:**
```
https://capsule-render.vercel.app/api?type=waving&color=0a1628,1847c8,0ea5e9&height=120&section=footer&text=We%20don't%20just%20teach%20tech%20—%20we%20create%20leaders%20🌟&fontSize=18&fontColor=ffffff&animation=fadeIn&fontAlignY=65
```

**Visitor badge (change `{{ REPO_NAME }}` to your repo name):**
```
https://visitor-badge.laobi.icu/badge?page_id=careerbytecode.{{ REPO_NAME }}&left_color=0a1628&right_color=1847c8&left_text=Profile%20Views
```

---

## 9. Do's and Don'ts

### ✅ Do

- Use the CBC repo template — never start blank
- Name repos `cbc-{track}-{topic}` — lowercase, hyphens
- Present CBC as **three things at once** — Learning · Community · Freelancing
- Use `careerbytecode.in` (never `.com`)
- Link WhatsApp `chat.whatsapp.com/HMb4qiixPFq19QJJHXwcxz` in every repo
- Point brand-related questions to Sangeetha B (Director, CareerByteCode / Partnerships)
- Set repo topics on GitHub: `careerbytecode`, `{track}`, `learning`, `community`, `freelancing`, plus any tech tags
- Enable **Issues** and **Discussions** on every repo
- Upload the CBC social banner as the GitHub social preview image
- Update the visitor badge `page_id` to your actual repo name

### ❌ Don't

- Don't leave any `{{ PLACEHOLDER }}` unfilled — run `grep "{{ " README.md` to verify
- Don't use `careerbytecode.com` — canonical is `.in`
- Don't include the Meetup badge — the group `careerbytecode-ai-cloud-emerging-leaders` no longer exists
- Don't mention individual CBC team member names in READMEs (except the Founding Team block and Sangeetha CTA — those are approved)
- Don't invent metrics (no "3M+ members", "150+ countries") without clearance
- Don't create city / chapter / cohort / course-specific "sub-logos"
- Don't alter the top brand strip or the About CBC block
- Don't ALL CAPS or `Underscore_Names` for repo names
- Don't publish a repo with no README, no LICENSE, or no description

---

## 10. Voice, Tone, Formatting

### 10.1 Voice — What CBC Sounds Like

CBC speaks like a **friendly, senior mentor who's been through it**. Direct. Warm. Practical. Never salesy. Never academic.

| We Are | We Are Not |
|--------|------------|
| Direct, practical, opinionated | Corporate, hedged, vague |
| Warm and encouraging | Sycophantic or salesy |
| Confident about what works | Arrogant or dismissive |
| Focused on real outcomes (jobs, projects, income) | Focused on completion certificates alone |
| Community-first ("we", "together") | Ego-driven ("I", "my") |

### 10.2 Tone Examples

**✅ Do:**

> *"This lab takes 45 minutes. By the end you'll have a running EKS cluster and you'll know exactly what each Terraform module is doing."*

> *"Common mistake: skipping the IAM role. Fix: run `make setup-iam` before `terraform apply`."*

> *"If you get stuck, drop it in our WhatsApp group — someone usually answers within an hour."*

**❌ Don't:**

> *"Welcome to this amazing comprehensive learning journey which will transform your career forever…"*

> *"In this cutting-edge, industry-leading, best-in-class content…"*

> *"This is the ONLY resource you'll EVER need."*

### 10.3 Formatting Rules

| Element | Rule |
|---------|------|
| Headers | H1 for repo title only. H2 for sections. H3 for sub-sections. Never skip levels. |
| Emojis on headers | One emoji at the start of every H2. Keep consistent per section. |
| Bold | Use for key terms and CTAs. Never bold whole sentences. |
| Blockquotes | Use for taglines, quotes, and one-line highlights. |
| Tables | Preferred over long bullet lists. Every table has a header row. |
| Code blocks | Always specify language (\`\`\`bash, \`\`\`python, \`\`\`hcl). |
| Images | Center with `<p align="center">` or `<div align="center">`. Include `alt` text. |

### 10.4 Emoji Style Guide (for H2 headers)

| Section | Emoji |
|---------|-------|
| About This Repo | 📖 |
| What You'll Learn | 🎯 |
| Repo Structure | 📂 |
| Getting Started | 🛠️ |
| Prerequisites | ⚙️ |
| Quick Start | 🚀 |
| Curriculum / Modules | 🎓 |
| Who This Is For | 👥 |
| Community & Events | 🎤 |
| Earn With CBC | 💼 |
| Share & Support | 🙌 |
| Contributing | 🤝 |
| License | 📄 |
| Contact | 📬 |
| Founding Team | 👩‍💼 |
| Connect & Collaborate | 🌐 |
| About CareerByteCode | 🌍 |

---

## 11. Publishing Checklist

Print this. Follow it every time you push a CBC repo.

### 11.1 Before Creating the Repo

- [ ] Confirmed the repo type (Lab / Playbook / Bootcamp / Portfolio / Event / Ops)
- [ ] Picked a name following `cbc-{track}-{topic}`
- [ ] Confirmed the license (§5.1)

### 11.2 Creating the Repo

- [ ] Go to `https://github.com/careerbytecode/cbc-repo-template`
- [ ] Click **"Use this template"** → **"Create a new repository"**
- [ ] Owner: `careerbytecode`
- [ ] Repository name: `cbc-{track}-{topic}`
- [ ] Description: one line + link to `careerbytecode.in`
- [ ] Visibility: **Public**
- [ ] Click **"Create repository from template"**

### 11.3 Filling the Template

- [ ] Project-wide find/replace of every `{{ PLACEHOLDER }}` in `README.md`
- [ ] Updated the visitor badge `page_id=careerbytecode.{{ REPO_NAME }}` → your actual repo name
- [ ] "What You'll Learn" table filled (4 rows)
- [ ] Prerequisites + Quick Start filled
- [ ] Repo Structure tree matches your actual folders
- [ ] `{{ CURRENT_YEAR }}` set to current year in footer (if present)
- [ ] Optional sections deleted if not applicable
- [ ] All 6 mandatory content sections still present
- [ ] `grep "{{ " README.md` returns zero matches

### 11.4 GitHub Repo Settings

- [ ] **About** → description + `careerbytecode.in` link
- [ ] **Topics** → `careerbytecode`, `{track}`, `learning`, `community`, `freelancing`, plus tech tags
- [ ] **Settings → General → Features** → **Issues** and **Discussions** enabled
- [ ] **Settings → General → Social preview** → upload CBC social banner
- [ ] **Settings → Branches** → protect `main` (require PR reviews)
- [ ] Pinned on org profile if it's a flagship repo

### 11.5 Final Verification

- [ ] All CBC URLs use `.in`, not `.com`
- [ ] WhatsApp link is `chat.whatsapp.com/HMb4qiixPFq19QJJHXwcxz`
- [ ] Contact block has `support@careerbytecode.in` and `+32 471 40 89 08`
- [ ] No individual CBC team member names outside the Founding Team block + Sangeetha CTA
- [ ] LICENSE file matches what the README claims
- [ ] Meetup badge NOT included (defunct)
- [ ] Waving capsule banners rendering correctly
- [ ] Visitor counter rendering correctly

### 11.6 Announce

- [ ] Posted on LinkedIn tagging CareerByteCode + 4–5 relevant people
- [ ] Posted in WhatsApp community
- [ ] Added the repo link to `team-main`'s cross-repo directory

---

## 12. Migrating Older Repos

For repos in `github.com/careerbytecode` that pre-date this playbook (e.g. `AZURE-Cloud-Realtime-Projects`, `CBC-HYD-Fullstack-Internship`, `CBC-Ban-DevOps-Internship`).

### 12.1 Rename or Not?

| Situation | Decision |
|-----------|----------|
| Repo has < 20 stars, few external links | **Rename** to `cbc-{track}-{topic}` |
| Repo has traction, external links, is used in courses | **Keep the name**, migrate the README instead |
| `team-main`, `.github`, `careerbytecode.github.io` | **Never rename** |

If renaming, GitHub creates a redirect so old URLs still work — but external references may break.

### 12.2 Suggested Renames

| Current | Suggested |
|---------|-----------|
| `AZURE-Cloud-Realtime-Projects` | `cbc-cloud-azure-realtime-projects` |
| `CBC-HYD-Fullstack-Internship` | `cbc-fullstack-hyd-internship` |
| `CBC-Ban-DevOps-Internship` | `cbc-devops-ban-internship` |

### 12.3 The Migration Steps

1. **Backup** — clone the repo locally as a safety net
2. **Copy `README.md` template** into the repo (save old README as `README.legacy.md` temporarily)
3. **Copy `playbook.md`** into the repo
4. **Fill placeholders** using content from `README.legacy.md`
5. **Verify** with the §11 checklist
6. **Delete** `README.legacy.md`
7. **Update GitHub settings** per §11.4
8. **Announce** the refresh on WhatsApp + LinkedIn

### 12.4 Migration Prioritisation

| Priority | Repos |
|----------|-------|
| **P0** | `team-main`, `.github` (org profile page) |
| **P1** | Repos linked from the website or course materials |
| **P2** | Repos linked from LinkedIn posts / talks |
| **P3** | Bootcamp / internship repos |
| **P4** | Older labs (or consider archiving) |

One P0 or P1 per week. Announce each one.

---

## 13. Governance & Contact

### 13.1 Who Owns This Playbook

The **CBC Team** owns this playbook.

**Sangeetha B, Director, CareerByteCode / Partnerships** — escalation contact for brand-critical decisions.

- **LinkedIn:** https://www.linkedin.com/in/careerbytecode

### 13.2 How To Propose Changes

Open an issue in `cbc-repo-template`:

- **Title:** `Playbook change proposal: [what & why]`
- **Body:** Current text → Proposed text → Rationale → Impact on existing repos

Playbook updates ship as new versions.

### 13.3 When The Playbook Changes

| Change | Impact |
|--------|--------|
| Copy tweak, new example | No migration needed |
| New mandatory section | All future repos + migration wave |
| Domain change, contact change, WhatsApp link change | Full migration wave — all repos updated |
| A copy-paste block in §8 changes | Existing repos have 30 days to update |

### 13.4 Contact

| Purpose | Contact |
|---------|---------|
| 📧 Support | [support@careerbytecode.in](mailto:support@careerbytecode.in) |
| 📱 Phone | +32 471 40 89 08 |
| 📲 WhatsApp | [chat.whatsapp.com/HMb4qiixPFq19QJJHXwcxz](https://chat.whatsapp.com/HMb4qiixPFq19QJJHXwcxz) |
| 🤝 Partnerships & brand escalation | Sangeetha B, Director, CareerByteCode / Partnerships |
| 🌐 Community | [careerbytecode.in/community](https://www.careerbytecode.in/community/) |
| 💼 Collaborate & Earn | [careerbytecode.in/collaborate](https://www.careerbytecode.in/collaborate/) |
| 🎤 Verify Certificates | [careerbytecode.in/verify](https://www.careerbytecode.in/verify/) |

---

## 📝 Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-07 | Initial playbook. Canonical domain `.in`. Single-file README template pattern. Meetup group retired. |

---

<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0a1628,1847c8,0ea5e9&height=100&section=footer&text=Consistency%20is%20a%20brand%20%E2%80%94%20every%20repo%20matters&fontSize=16&fontColor=ffffff&animation=fadeIn&fontAlignY=68" width="100%"/>

***© 2015–2026 CareerByteCode · Learn. Build. Collaborate. Earn.***

*a platform by Cloudnloud Technologies SRL, Belgium*

</div>
