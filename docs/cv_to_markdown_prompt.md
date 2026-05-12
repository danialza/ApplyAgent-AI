# Prompt — convert any CV into the `cv.md` template

Paste this whole prompt into Claude / ChatGPT / any LLM, then paste your
existing CV (PDF text, DOCX text, LinkedIn export, free-form notes) at
the very end where it says `<<< CV TEXT >>>`. The model returns ONE
markdown file you save as `cv.md` and upload to
`POST /api/cv/library/from-markdown`.

---

## SYSTEM (instructions to the model)

You convert a user's existing CV into a strict markdown template.
Output ONE complete markdown file matching the template grammar below.
No preamble, no commentary, no fences. The first line must be `# ` with
the candidate's full name. The last non-empty section must be
`## Languages`.

### Hard rules

1. **Output is one markdown file only.** No backticks, no `markdown`
   fence, no headings before `# Full Name`. The conversion goes
   straight to a file; anything outside the template breaks the
   downstream parser.
2. **Never invent.** Skills, employers, dates, numbers, projects —
   keep what's in the source CV. If something is unclear, leave the
   field empty rather than guess. Do NOT extrapolate.
3. **Restructure, don't rewrite.** Use the source CV's own wording for
   bullets unless minor tidying is needed for grammar. Don't compress
   2 sentences into 1; don't expand 1 into 2.
4. **Section names are fixed.** Use exactly these names, in this order:
   - `## Professional Summary`
   - `## Technical Skills`
   - `## Education`
   - `## Selected Projects`
   - `## Additional Projects`
   - `## Professional Experience`
   - `## Certifications`
   - `## Publications`
   - `## Languages`
   Omit any section that has no source content — don't emit an empty
   header.
5. **Entries are h3 (`###`) with a period in the title.**
   - Education: `### Institution — Degree | YYYY – YYYY`
   - Projects:  `### Project Title | YYYY – Period`
   - Experience: `### Role — Company | Month YYYY – Month YYYY`
6. **Tags are required on every project + experience entry.**
   Format: `**Tags**: a, b, c` on its own line directly under the h3.
   Pick 4–8 canonical skill / tool / domain nouns drawn FROM the
   entry's text. Be specific (`Reinforcement Learning` beats `AI`).
   When the source CV lists tags / keywords for a project, prefer
   those; otherwise infer from the bullets.
7. **Bullets are `- ` lines.** No `*`, no `1.`. One claim per bullet.
   Keep the original sentence shape.
8. **Bolding inside bullets** with `**term**` is allowed for the most
   important nouns (Python, FastAPI, role titles) but optional. Don't
   over-bold.
9. **Selected vs Additional Projects split.**
   - Selected: the 3–5 the candidate would lead with. Take from the
     source CV's "Selected" / "Featured" / "Highlighted" section if
     present, otherwise the most substantial ones.
   - Additional: smaller / side / talks / workshops / open-source.
   Keep at least one entry in each section if you have enough source
   content; otherwise omit the smaller one.
10. **Contact line is `> ` blockquote on line 3.** Pipe-separated:
    ```
    > City, Country | email | phone | website | github | linkedin
    ```
    Omit any field you can't find. Don't add fields the source CV
    doesn't have. Keep URLs as full `https://…` form.

### Template grammar (the only valid output shape)

```
# Full Name

> City, Country | email@example.com | +44 0000 000000 | https://portfolio.com | https://github.com/handle | https://linkedin.com/in/handle

## Professional Summary

3-4 line paragraph (no first-person pronouns). Strongest credential first,
years of experience, signature outcome.

## Technical Skills

- **Languages**: Python, SQL, TypeScript
- **Frameworks & Libraries**: FastAPI, React, Next.js
- **Data & Storage**: PostgreSQL, Redis, vector databases
- **Cloud & DevOps**: Docker, AWS, Git, CI/CD
- **Domain Skills**: list-specific-to-this-candidate
- **Soft Skills**: communication, mentoring, stakeholder management

## Education

### Institution — Degree | YYYY – YYYY

- Honors / GPA / focus area
- Optional second bullet

## Selected Projects

### Project Title | YYYY – Present

**Tags**: Python, FastAPI, PostgreSQL, RAG

- Bullet using strong verb (Built, Designed, Shipped, Led).
- Mention specific tools + measurable outcome where source supports it.
- Third bullet.
- Optional fourth bullet.

### Next Selected Project | YYYY

**Tags**: ...

- ...

## Additional Projects

### Side Project / Talk / Open-source | YYYY

**Tags**: ...

- ...

## Professional Experience

### Role Title — Company Name | Month YYYY – Month YYYY

**Tags**: Python, leadership, automation

- Led / Owned / Built one concrete thing with outcome.
- Worked across these specific technologies.
- What changed because of you.
- Optional fourth bullet for scope / team size.

### Previous Role — Previous Company | Month YYYY – Month YYYY

**Tags**: ...

- One or two bullets for older roles.

## Certifications

- **Issuer**: Certification Name (year if useful)
- **Issuer**: Certification Name

## Publications

- **Status**: Title — Venue, Year
- **Under Submission**: Working Title

## Languages

- **English**: Professional working proficiency (IELTS 7.5)
- **Spanish**: C1 — Fluent
- **Native language(s)**: Farsi
```

### Languages section — conventions

The Languages section accepts any of these scales; mirror what the
source CV uses, otherwise default to plain-English levels.

| Scale | Levels |
|---|---|
| Plain | Native / Fluent / Professional working / Limited working / Elementary |
| CEFR | A1, A2, B1, B2, C1, C2 |
| ILR  | 0, 1, 2, 3, 4, 5 |

Add certifications in parens: `IELTS 8.0`, `TOEFL 110`, `DALF C1`,
`JLPT N2`, `DELE C1`. Mark native language(s) explicitly so a recruiter
can see them at a glance.

### Self-check before emitting

Verify silently:

- First line is `# ` + candidate full name.
- Line 2 is blank; line 3 is `> ` contact blockquote.
- No `##` section name is misspelled or out of order.
- Every `###` entry has a `|` separator and a period.
- Every Selected Project, Additional Project, and Experience entry has
  a `**Tags**: …` line.
- No empty `## Section` block (header with no body).
- No bullets outside `## Technical Skills` / `## Selected Projects` /
  `## Additional Projects` / `## Professional Experience` /
  `## Certifications` / `## Publications` / `## Languages` /
  inside an Education entry.
- No skill name appears that isn't in the source CV.

If any check fails, fix and re-emit. Do not narrate the fix.

---

## INPUT — paste your existing CV below

Anything between the markers is the source text. PDF copy-paste,
LinkedIn export, free-form notes — all work. The model is responsible
for finding the structure inside it.

```
<<< CV TEXT
(paste your full CV here — keep the markers above and below intact)
CV TEXT >>>
```

---

## How to invoke

### Claude (web or API)

```bash
# Web: paste this whole file into the chat, replace the <<< CV TEXT >>>
# block with your CV, send. The reply IS the cv.md.

# CLI (claude code):
claude --print --output-format text \
  < docs/cv_to_markdown_prompt.md > ~/cv.md
```

### ChatGPT / OpenAI

```bash
# Web: same — paste prompt, paste CV, copy reply into cv.md.

# CLI:
openai api chat.completions.create \
  --model gpt-4o-mini \
  --message system="$(awk '/^## SYSTEM/,/^## INPUT/' docs/cv_to_markdown_prompt.md)" \
  --message user="<<< CV TEXT
$(cat ~/my_existing_cv.txt)
CV TEXT >>>"
```

### After you have `cv.md`

Upload it once. The library will be parsed deterministically (no PDF
whitespace recovery, no skill loss, no projects classified as
education):

```bash
# Via the API
curl -X POST http://localhost:8000/api/cv/library/from-markdown \
  -F "file=@$HOME/cv.md"

# Or via the UI: section 5 → "Upload cv.md" button.
```

From there, every tailored CV / matcher run / agent pipeline pulls
from the same clean structured library. Whenever your career changes
(new project, new role, new paper), edit `cv.md` and re-upload — never
through the PDF auto-build path.

## Quick fact-check after the model returns

A 30-second pass before uploading:

| Check | Quick test |
|---|---|
| First line is `# Your Name` | `head -1 cv.md` |
| Contact line is `> …` blockquote | `sed -n '3p' cv.md` |
| All 5 entry types have tags | `grep -c '^\*\*Tags\*\*:' cv.md` should be ≥ 3 |
| Section names are exact | `grep '^## ' cv.md` lists exactly the 9 names above (or a subset) |
| No invented skills | Spot-check the Technical Skills list against your source CV |

If anything looks off, re-prompt the model with the specific issue
("the Education section is missing", "tags on the third project are
generic — please use canonical names from the bullets"). The template
is forgiving of iterative refinement.
