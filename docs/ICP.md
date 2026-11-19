# Sprinto ICP & Qualification Rubric

> **Source of truth** for Step 3 (Qualify) and Step 4 (PoC). The code reads the tables and lists below
> (`outbound/icp.py`); no ICP rule is written anywhere else. Change a number here and the scorer changes.
> **Owner: edit this freely, because defending it is part of the assignment.**
>
> Format rules so the code can read this file: keep the `##` headings, keep table columns in the same
> order, one domain per bullet in the lists (text after the domain is a note).

## Why this definition (research summary)
- The case brief: SaaS, **Series A–C**, either **preparing for an audit** or **under pressure from enterprise
  customers** to get certified.
- Sprinto positions itself for cloud-first startups and mid-market teams, especially those **selling into
  enterprise**: "if customers ask for SOC 2 or ISO 27001, Sprinto fits." Buyers are security, compliance,
  engineering leaders and founders.
- Sprinto's own content says companies usually start with **SOC 2 Type 1**, then move to Type 2 and add ISO
  27001. So "first certification" and "adding a second framework" are both entry points.
- Third-party reviews put the startup tier at roughly **$8–10K/year** for a single framework. The prospect must be
  big enough to pay that, so pre-seed companies with a handful of employees are a weak fit.
- Competitors: **Vanta, Drata, Secureframe, Scrut, Thoropass, OneTrust/Tugboat**. A company already running one
  of these (e.g., trust center hosted on `trust.vanta.com` / Drata / Secureframe) is a switching sale, so score it low.

## How scoring works (who does what)
- **The LLM never assigns points.** It only answers each question in *Scoring rules* and *Hard disqualifiers*
  with `yes`, `no` or `unknown`, plus evidence `{claim, source_url, quote}`.
- **Python checks the evidence**: the `source_url` must be a page we actually fetched (or a web-search
  citation) and the `quote` must really appear in that page's text. Evidence that fails is thrown away.
- **Python applies the points**: a rule earns its points only if the answer is `yes` AND it has verified
  evidence. `no`, `unknown` or no evidence = 0. Each row is capped at its max, the total is capped at 0–100.
- So **the same signals always give the same score**.

## Hard disqualifiers (any one → `rejected`)
Checked in this order. The list checks run in Python **before** any scoring call to the LLM.

| Rule ID | Disqualifier | Python list / profile check | Question for the LLM (yes = disqualified) |
|---|---|---|---|
| DQ_COMPLIANCE_VENDOR | Is a compliance-automation vendor (Sprinto competitor) or Sprinto itself | domain is in *Known competitors* | Does this company itself sell compliance-automation software (SOC 2 / ISO 27001 automation, GRC platform)? |
| DQ_SPRINTO_CUSTOMER | Publicly listed as an existing Sprinto customer | domain is in *Known Sprinto customers*, or the site links to a trust center hosted on one of *Sprinto's own domains* (e.g. `app.sprinto.com`) | Is this company publicly named as a Sprinto customer (case study, logo, testimonial)? |
| DQ_NOT_B2B | Not a B2B software / cloud product (D2C brand, agency, retailer, consumer app with no B2B tier) | profile says `is_b2b_software = false` with evidence | Is this company NOT a B2B software / cloud product (e.g., a D2C brand, agency, retailer or consumer-only app)? |
| DQ_LATE_STAGE | Public company or clearly late-stage (IPO'd, Fortune 500 scale) | profile employee range starts above `late_stage_min_employees` | Is this company publicly listed (IPO'd), Fortune 500 scale, or clearly over 2,000 employees? |

## Known competitors (disqualifier DQ_COMPLIANCE_VENDOR)
> Also used by the scraper: a link to any of these domains (e.g. `trust.vanta.com`) is recorded as a
> "competitor tool" signal.

- vanta.com (Vanta)
- drata.com (Drata)
- secureframe.com (Secureframe)
- scrut.io (Scrut Automation)
- thoropass.com (Thoropass)
- onetrust.com (OneTrust)
- tugboatlogic.com (Tugboat Logic, part of OneTrust)
- sprinto.com (Sprinto itself)

## Sprinto's own domains
> A prospect linking to these (typically a trust center at `app.sprinto.com/...`) is already a Sprinto
> customer → DQ_SPRINTO_CUSTOMER, not the competitor penalty.

- sprinto.com

## Known Sprinto customers (disqualifier DQ_SPRINTO_CUSTOMER)
> Add a domain here when you find it as a case study or logo on sprinto.com. The agent loop also runs a
> web search for "<company> Sprinto" as a secondary check for every company that scores 40 or more.

- getastra.com (Astra Security, publicly listed as a Sprinto customer)
- fylehq.com (Fyle: case study sprinto.com/customers/fyle)
- spendflo.com (Spendflo: Sprinto case study "multi standard compliance program for Spendflo")
- recruitcrm.io (Recruit CRM: Sprinto case study; also links to a Sprinto-hosted trust center)
- docsumo.com (Docsumo: logo on sprinto.com/solutions/startups)
- everstage.com (Everstage: named on Sprinto's G2 listing as a customer)
- spotdraft.com (SpotDraft: trust center "Powered by Sprinto", found by the agent loop on 2026-11-19)
- hevodata.com (Hevo Data: privacy policy lists Sprinto for its trust center, found by the agent loop on 2026-11-19)
- testsigma.com (Testsigma: "uses Sprinto to power its Trust Center", found by the agent loop on 2026-11-19)

> rocketlane.com is also a Sprinto customer (case study sprinto.com/rocketlane-case-study) but is deliberately
> NOT listed: the scraper catches it from its own app.sprinto.com trust-center link, which shows the
> link check working.

## Rubric rows
Each row's points are capped at its max (the penalty row is capped at −20). Row maxes add up to **100**.
A row marked *critical* must not be completely unknown at the end, or the verdict is `insufficient_data`.

| Row ID | Row | Max points | Critical |
|---|---|---|---|
| ENT | Sells to enterprise / mid-market | 20 | no |
| DATA | Handles customer data | 15 | no |
| GAP | Compliance gap or in-progress signal | 25 | yes |
| STAGE | Stage fit: Series A–C | 15 | yes |
| HIRING | Hiring signal | 15 | no |
| GEO | Geography pushing compliance | 10 | no |
| COMP | Already on a competitor (penalty) | 20 | no |

## Scoring rules
`Kind`: *presence* = the quote must show the thing exists; *absence* = the evidence points at the pages that were
checked and found nothing (e.g., no trust page).

| Rule ID | Row | Points | Kind | Question for the LLM (answer yes / no / unknown, with evidence) |
|---|---|---|---|---|
| ENT_PLAN | ENT | 8 | presence | Is there an "Enterprise" or custom-priced plan / tier? |
| ENT_DEMO | ENT | 4 | presence | Is the buying motion sales-led ("Book a demo", "Talk to sales", "Contact sales")? |
| ENT_LOGOS | ENT | 4 | presence | Does the site name mid-market or enterprise customers (logos, case studies)? |
| ENT_SSO | ENT | 4 | presence | Does the product advertise SSO / SAML, role-based access, or audit logs? |
| DATA_INTEGRATIONS | DATA | 5 | presence | Does the product connect to customers' own systems (CRM, ERP, HRIS, cloud, data warehouse, etc.)? |
| DATA_SENSITIVE | DATA | 7 | presence | Does the product store or process customers' personal, financial or health data? |
| DATA_DPA | DATA | 3 | presence | Is there a DPA, sub-processor list, or data-processing / privacy commitment for business customers? |
| GAP_IN_PROGRESS | GAP | 12 | presence | Does the company say SOC 2 / ISO 27001 is "in progress", "coming soon" or planned? |
| GAP_NO_TRUST_PAGE | GAP | 8 | absence | Is there NO trust center / security page and NO SOC 2 or ISO 27001 claim on the pages checked? |
| GAP_ONE_FRAMEWORK | GAP | 8 | presence | Does the company hold exactly one of SOC 2 or ISO 27001 (room to add the second)? |
| GAP_GDPR_ONLY | GAP | 8 | presence | Does the company mention GDPR but neither SOC 2 nor ISO 27001? |
| GAP_QUESTIONNAIRE | GAP | 5 | presence | Does the company mention security questionnaires, security reviews or vendor assessments from its customers? |
| STAGE_SERIES | STAGE | 10 | presence | Is the company's latest disclosed funding round a Series A, B or C (no later round such as Series D+, and not publicly listed)? Answer by the round's name; its date does not matter here. |
| STAGE_RECENT_FUNDING | STAGE | 5 | presence | Did the company raise a funding round recently? Quote the round with its date (month and year). |
| STAGE_SIZE | STAGE | 5 | presence | Does the company have roughly 30–500 employees? |
| HIRING_SECURITY | HIRING | 10 | presence | Is the company hiring for security, compliance, GRC, IT or DevSecOps roles? |
| HIRING_ENT_SALES | HIRING | 5 | presence | Is the company hiring enterprise / mid-market account executives or sales roles? |
| GEO_US | GEO | 5 | presence | Does the company sell to US customers? |
| GEO_EU_UK | GEO | 5 | presence | Does the company sell to EU or UK customers? |
| COMP_ON_COMPETITOR | COMP | -20 | presence | Does the company use Vanta, Drata, Secureframe, Scrut, Thoropass or OneTrust (trust center, badge, "powered by")? |

## Rules that need a readable trust page
> These rules claim something is **missing** ("no SOC 2", "only one framework", "GDPR only"). "We didn't see it" is
> only proof when we could actually read where it would be. So if a trust / security / compliance page **exists but
> could not be read** (JavaScript-only, bot-blocked, robots.txt, login), Python forces these rules to `unknown`
> (0 points) instead of letting a blank page count as a compliance gap. A trust page that returns 404 does not
> block them: that *is* evidence of absence.

- GAP_NO_TRUST_PAGE
- GAP_ONE_FRAMEWORK
- GAP_GDPR_ONLY

## Rows that must earn points
> The case brief defines the ICP as companies **preparing for an audit or under pressure to get certified**. Generic
> B2B signals (enterprise plan, customer data, US/EU customers, funding) can reach 60 on their own, so without this
> rule an already fully certified company would qualify. A row listed here must earn points (a `yes` with verified
> evidence) before a company can be `qualified`:
> - every rule in the row answered `no` → `rejected` (no buying trigger);
> - no `yes` yet but some rules `unknown` → the agent loop keeps looking; still nothing → `insufficient_data`.

- GAP

## Verdicts
| Setting | Value |
|---|---|
| qualified_min_score | 60 |
| borderline_min_score | 40 |
| late_stage_min_employees | 2000 |
| funding_window_months | 24 |

- **Stage vs recency are separate questions.** A company whose latest round is a 2024 Series A is still a Series A
  company: STAGE_SERIES (+10) asks *what stage it is now*. Recent funding is a buying-timing bonus: STAGE_RECENT_FUNDING
  (+5). The STAGE row is still capped at 15 (Series 10 + recent 5 + size 5).
- **Funding window (checked in Python, not by the model):** STAGE_RECENT_FUNDING earns points only if its evidence shows
  the round's date (month + year, or a year) and that date is within `funding_window_months` of the run date. A round
  outside the window counts as `no`; a round with no visible date counts as `unknown` (0 points), because the window
  can't be checked. This keeps the rule identical for every company (e.g. a June 2024 round is 29 months old in Nov 2026).
- Any disqualifier → `rejected`
- Score **≥ qualified_min_score** and no critical row fully unknown → `qualified`
- Score **between borderline_min_score and qualified_min_score**, or a critical row fully unknown, or B2B status unknown →
  the agent loop gathers more evidence (max `MAX_AGENT_ITERATIONS`), then re-scores
- After the loop: if even "every unknown rule = yes" could not reach qualified_min_score → `rejected`;
  otherwise still a critical unknown → `insufficient_data`; otherwise score below qualified_min_score → `rejected`
- Every company that scores ≥ borderline_min_score also gets one web search for "<company> Sprinto customer"
  (secondary check for DQ_SPRINTO_CUSTOMER) before it can be `qualified`.

Every point awarded must cite evidence `{claim, source_url, quote}`. No evidence, no points.

## Who is the right PoC (Step 4)
Python ranks Hunter contacts by `Priority` (1 = best) for the company's size band, then by Hunter confidence.
A title matches if it contains any of the keywords (whole words, case-insensitive). `seniority:executive in …` means
Hunter's seniority field is "executive" **and** its department is one of the listed ones (so a VP of Marketing or
Sales never becomes the contact). If the employee count is unknown, the `1-149` band is used.

Two separate scores, never mixed: **relevance** (the priority below: does this role own the compliance purchase?) and
**reliability** (Hunter's email confidence). Relevance always wins; confidence only breaks ties inside one priority,
and contacts below `min_confidence` rank after those above it. Contacts without a first name are never chosen. If no
contact matches any row, the LLM may interpret unusual titles against this table, and it must answer "nobody" when no
one plausibly owns the decision; the result is then `no_poc`, never a random executive.

| Size band | Priority | Title | Title keywords |
|---|---|---|---|
| 1-149 | 1 | CTO | cto, chief technology officer |
| 1-149 | 2 | Co-founder / CEO | co founder, cofounder, founder, ceo, chief executive officer |
| 1-149 | 3 | VP / Head of Engineering | vp engineering, vp of engineering, vice president engineering, vice president of engineering, head of engineering, director of engineering, engineering director |
| 150-100000 | 1 | CISO / Head of Security | ciso, chief information security officer, head of security, vp security, vp of security, director of security, security lead |
| 150-100000 | 2 | VP Engineering / CTO | vp engineering, vp of engineering, vice president of engineering, head of engineering, cto, chief technology officer |
| 150-100000 | 3 | Head of IT / Compliance | head of it, it director, director of it, vp it, head of compliance, compliance manager, compliance lead, grc |
| 150-100000 | 4 | Co-founder / CEO | co founder, cofounder, founder, ceo, chief executive officer |
| any | 5 | Head of Ops / COO | coo, chief operating officer, head of operations, vp operations, vp of operations, director of operations |
| any | 6 | Any executive in IT / management | seniority:executive in executive it engineering security management operations |

| Setting | Value |
|---|---|
| min_confidence | 70 |

## Typical pain points by persona (Step 5 picks 2, grounded in evidence)
- **CTO / Eng leader:** engineers pulled off roadmap to collect audit evidence; security questionnaires slowing deals.
- **Founder / CEO:** enterprise deals stalled waiting on SOC 2; audit cost and timeline uncertainty.
- **Security / Compliance lead:** spreadsheets across multiple frameworks; continuous monitoring after the audit.
- **Ops / COO:** vendor risk reviews, policy management, employee onboarding/offboarding controls.
