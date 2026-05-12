# Your Full Name

> City, Country | you@example.com | +00 0000 0000 | https://your-portfolio.com | https://github.com/your-handle | https://linkedin.com/in/your-handle

<!--
=============================================================================
GENERIC CV TEMPLATE — fill in once, upload via:
    POST /api/cv/library/from-markdown   (multipart: file=@cv.md)
Or in the UI: section 5 → "Upload cv.md".

Works for any field: software, design, marketing, research, engineering,
product, ops. Section names below are domain-neutral on purpose. Add
or remove projects / experience / certifications freely; the tailored
renderer picks the best subset per job description.

Rules:
  * Section headers are h2 (`## …`). Names: Professional Summary,
    Technical Skills, Education, Selected Projects, Additional Projects,
    Professional Experience, Certifications, Publications, Languages.
    Don't rename them.
  * Inside Projects / Experience use h3 (`### Title | Period`).
  * `**Tags**: a, b, c` drives JD-matching ranking. Be specific.
  * Plain `- ` bullets become highlights.
=============================================================================
-->

## Professional Summary

Write a 3-4 line summary (first-person implied, no "I"). Lead with your strongest credential or role identity. Mention years of experience, the kinds of problems you ship work on, and a concrete signature outcome if you have one. The tailored renderer will rewrite this per job description using the JD's vocabulary while keeping every claim grounded in this file.

## Technical Skills

<!--
Group skills under bold labels. The matcher and tailored renderer treat
each group as a separate axis. Use as many or as few groups as you
need — drop empty ones, add a new one if a JD demands a missing
category. Canonical groups recruiters look for (in this rough order):

  Languages                — programming languages only
  Frameworks & Libraries   — web/app frameworks, UI libs
  AI / ML & Data Science   — models, libraries, techniques (optional)
  Data & Storage           — DBs, warehouses, caches, vector stores
  APIs & Integration       — REST, GraphQL, gRPC, queues, webhooks
  Cloud & DevOps           — cloud providers, containers, CI/CD
  Infrastructure & Observability  — IaC, orchestration, metrics
  Testing & Quality        — pytest, Jest, Playwright, linters
  Security                 — auth, secrets, common standards
  Tools & Platforms        — Git, Linear/Jira, Figma, Notion
  Architecture & Patterns  — microservices, event-driven, DDD
  Methodologies            — Agile, Scrum, TDD, code review
  Domain Skills            — what you DO, not what you USE
  Soft Skills              — communication, mentoring, leadership

Keep entries comma-separated. Don't pad with skills you can't defend in
an interview — the matcher will surface them and ATS scanners weight
them. Best to list 4–10 items per group, not 30.
-->

- **Languages**: Python, SQL, JavaScript, TypeScript
- **Frameworks & Libraries**: FastAPI, React, Next.js, Tailwind
- **AI / ML & Data Science**: PyTorch, scikit-learn, RAG, embeddings, fine-tuning
- **Data & Storage**: PostgreSQL, Redis, FAISS, S3, vector databases
- **APIs & Integration**: REST, GraphQL, gRPC, webhooks, message queues
- **Cloud & DevOps**: Docker, AWS, GCP, GitHub Actions, CI/CD
- **Infrastructure & Observability**: Kubernetes, Terraform, Prometheus, Grafana
- **Testing & Quality**: pytest, Jest, Playwright, code review
- **Security**: OAuth2, JWT, OWASP top-10, secrets management
- **Tools & Platforms**: Git, Linear, Jira, Figma, Notion
- **Architecture & Patterns**: microservices, event-driven, DDD, REST design
- **Methodologies**: Agile, Scrum, TDD, pair programming
- **Domain Skills**: list the specific skills the JDs you target actually demand
- **Soft Skills**: communication, mentoring, stakeholder management

## Core Competencies

<!--
OPTIONAL but powerful for ATS / JD alignment. Stretch skills you're
willing to claim on a tailored CV even when no single project bullet
showcases them. Self-rate each on a 1..5 scale; the tailored renderer
only injects items whose rating is at or above the per-render
threshold (default 3) AND whose name matches a JD term. So nothing
ever auto-appears that you can't defend in an interview.

  5/5 = expert, ship daily
  4/5 = strong working knowledge, used in real projects
  3/5 = comfortable, used in side projects / coursework
  2/5 = familiar with concepts, light hands-on
  1/5 = aspirational, learning now

Format: `- **Name**: N/5 — short rationale`. The rationale is yours;
it never lands in the rendered CV, it's just a reminder for you.
Omit this section entirely if you don't want stretch claims.
-->

- **Distributed systems**: 4/5 — designed event-driven services at $LASTROLE
- **Kubernetes**: 3/5 — operate dev clusters, not production
- **Rust**: 2/5 — read code, write small CLIs

## Education

### University Name — Degree Title | YYYY – YYYY

- Honors / GPA / relevant focus if useful
- One more bullet if worth surfacing

## Selected Projects

### Project Name | YYYY – Present

**Tags**: Python, FastAPI, PostgreSQL, RAG

- Describe what you built and the visible outcome. Strong verb first (Built, Designed, Shipped, Migrated).
- Mention the techniques + tools you actually used; the matcher boosts entries whose tags overlap the JD.
- Quantify when honest numbers exist (users, requests/sec, latency, cost saved).
- Optional fourth bullet for impact or lesson learned.

### Another Project | YYYY

**Tags**: relevant, tags, here

- Bullet 1
- Bullet 2
- Bullet 3

## Additional Projects

### Side Project / Smaller Work | YYYY

**Tags**: Python, Docker

- Short description of what it does and why it's interesting.
- Optional second bullet.

### Workshop / Talk / Open-source Contribution | YYYY

**Tags**: teaching, open-source

- Organised / delivered / contributed — describe scale + audience + topic.

## Professional Experience

### Role Title — Company Name | Month YYYY – Month YYYY

**Tags**: Python, FastAPI, leadership, automation

- Led / Designed / Shipped one concrete thing with the visible outcome.
- Owned another piece of the stack — mention the technologies you actually used.
- What changed because of you, not just what you touched.
- Optional fourth bullet for context (team size, scope, scale).

### Previous Role — Previous Company | Month YYYY – Month YYYY

**Tags**: relevant, tags

- One or two bullets is fine for older roles.
- Older roles get progressively less detail.

## Certifications

- **Issuer**: Certification Name (year if it matters)
- **Coursera**: Specialization Name
- **AWS**: Solutions Architect — Associate

## Publications

- **Published**: Paper Title — Venue, Year
- **Under Submission**: Working Title

## Languages

<!--
Free-form list, one language per line. Conventions you can mix:
  * Plain levels:  Native / Fluent / Professional working / Limited / Elementary
  * CEFR scale:    A1 / A2 / B1 / B2 / C1 / C2
  * ILR scale:     0 / 1 / 2 / 3 / 4 / 5
  * Certifications: add in parens, e.g. "IELTS 8.0", "DALF C1", "JLPT N2"
-->

- **English**: Professional working proficiency (IELTS 7.5)
- **Spanish**: C1 — Fluent
- **Mandarin**: B1 — Working
- **Native language(s)**: Persian / Farsi
