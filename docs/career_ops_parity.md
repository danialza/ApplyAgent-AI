# Career-Ops parity — what we do, what we skip, and why

Reference: <https://github.com/santifer/career-ops>

Career-ops is a broad job-search automation system (pipeline tracker,
offer scoring, portal scanner, application form filler, follow-ups,
LinkedIn outreach, tailored CV/cover-letter generation, interview prep).
This project's CV tailoring layer mirrors the **CV-generation modes**
(`modes/pdf.md` + `modes/latex.md`) step-for-step. Other career-ops
features are scoped separately or out of scope.

## CV tailoring pipeline — career-ops methodology vs ours

Career-ops's tailored-CV pipeline (from `modes/pdf.md` and
`modes/latex.md`):

| # | Career-ops step | Our implementation |
|---|---|---|
| 1 | Read `cv.md` as source of truth | ✅ `docs/cv_template.md` + `cv_markdown_parser` + `POST /api/cv/library/from-markdown` |
| 2 | Ask user for the JD if not in context | ✅ Section 5 JD textarea / `/api/cv/render { job_text }` |
| 3 | Extract 15-20 keywords from JD | ✅ LLM prompt Step 1, plus rule-based `find_technical_skills` |
| 4 | Detect JD language → CV language | ⚠️ Partial — LLM mirrors the JD's language in the rewritten Summary when polishing. Section labels stay English in the template. Workaround: keep your `cv.md` in your target language and the labels in the renderer template can be edited. |
| 5 | Detect company location → paper format (US=letter, world=A4) | ❌ Not implemented — template is letter-only. Easy to add via a `paper_format` request field if needed. |
| 6 | Detect role archetype → adapt framing | ✅ LLM prompt Step 2 |
| 7 | Rewrite Professional Summary, inject JD keywords | ✅ LLM prompt Step 3 (3-4 lines, no first-person, top-5 keywords) |
| 7a | "Exit narrative bridge" sentence in summary | ❌ Career-ops adds a personal bridge ("Built and sold a business. Now applying systems thinking to …"). Ours doesn't inject this template — the LLM gets the candidate's existing summary verbatim and reworks it. If you want the bridge, write it into your `cv.md` summary and the LLM will preserve the structure. |
| 8 | Select top 3-4 most relevant projects | ✅ Rule-based ranking by tag-overlap + `max_selected_projects` knob (default 4) |
| 9 | Reorder experience bullets by JD relevance | ✅ LLM prompt Step 5 (keeps bullet count, reorders + rewords) |
| 10 | Build competency grid (6-8 keyword phrases from JD) | ✅ Renderer computes `core_competencies` = up to 8 JD canonical terms ∩ candidate's skill set, emits `\section{Core Competencies \hrulefill}` |
| 11 | Inject keywords naturally; **never invent skills** | ✅ Hard rule in system prompt + bullet-count + bullet-length guards in `codex_cv_polish._bullets_compatible` + `_scrub_bold_keywords` |
| 12 | Generate LaTeX from template + personalised content | ✅ `cv_renderer.render_cv` Jinja2 template |
| 13 | Filename: `cv-{candidate}-{company}-{YYYY-MM-DD}` | ✅ `RenderCVResponse.suggested_filename` |
| 14 | Compile to PDF via `tectonic` | ✅ Backend image bakes in tectonic; `compile_pdf=true` returns base64 PDF |
| 15 | Report PDF path, page count, **keyword coverage %** | ✅ Partial — coverage % + covered/missing lists in `RenderCVResponse.keyword_coverage`; page count not reported |

## ATS rules (parity with `modes/pdf.md`)

| Rule | Status |
|---|---|
| Single-column layout | ✅ `geometry` template is one-column |
| Standard section headers | ✅ Professional Summary / Core Competencies / Technical Skills / Education / Selected Projects / Additional Projects / Professional Experience / Certifications / Publications / Languages |
| UTF-8, selectable text | ✅ `\pdfgentounicode=1` + `tectonic` output |
| No images / SVGs in body | ✅ Template has no graphics |
| No critical info in header/footer | ✅ `\pagestyle{empty}` |
| No nested tables | ✅ Native `itemize` lists |
| Keywords distributed: Summary top 5 + first bullet of each role + Skills section | ✅ LLM prompt enforces top-5 in Summary; bullets reordered with JD-relevant first; Core Competencies row mirrors career-ops's "competency grid" |

## Keyword-injection examples (parity with `modes/pdf.md`)

Career-ops's exact examples — all of these are produced by our LLM
polish when `use_llm: true`:

| JD says | CV originally says | Career-ops rewrite (and ours) |
|---|---|---|
| "RAG pipelines" | "LLM workflows with retrieval" | "RAG pipeline design and LLM orchestration workflows" |
| "MLOps" | "observability, evals, error handling" | "MLOps and observability: evals, error handling, cost monitoring" |
| "stakeholder management" | "collaborated with team" | "stakeholder management across engineering, operations, and business" |

Hard rule (same as career-ops): **never add a skill the candidate
doesn't have.** Only reformulate existing experience with the JD's
vocabulary. Enforced by `_scrub_bold_keywords` (drops any bold keyword
not present in the library or the JD) and `_bullets_compatible`
(rejects a polish where bullet count or per-bullet length drifts beyond
0.4×–2.0× of the original).

## What we DON'T do (career-ops's broader scope)

| Career-ops feature | Status here |
|---|---|
| `data/applications.md` tracker | ❌ Not implemented |
| Offer evaluation / scoring (`modes/oferta.md` A-F blocks, archetype score, STAR proof points) | ⚠️ Partial — `/api/match` returns a 5-factor score breakdown, but career-ops's deeper archetype/STAR scoring isn't here |
| Portal scanner with dedup history | ⚠️ Partial — `/api/jobs/discover` hits public APIs (RemoteOK / Remotive / HN); no per-portal scan history |
| Live application form filler (`modes/apply.md`) | ❌ Not implemented |
| LinkedIn outreach scripts (`modes/contacto.md`) | ✅ `/api/generate` produces `linkedin_message` |
| Follow-up emails | ❌ Not implemented |
| Interview prep (`modes/interview-prep.md`) | ❌ Not implemented |
| HTML PDF generation via Playwright | ❌ Out of scope; we use tectonic + LaTeX |
| Canva CV integration | ❌ Out of scope |
| Multi-language modes (`modes/de`, `fr`, `ja`, `pt`, `ru`) | ❌ Partial — LLM mirrors JD language in the polished text but section labels are hard-coded English |

## Verifying the LLM is doing all 7 steps

1. **Check connectivity**: `GET /api/cv/llm-status` — should report `reachable: true` with model name.
2. **Tail backend logs** during render:
   ```bash
   docker compose --env-file .env.ports logs -f backend | grep -E "cv_polish|chat_completions|extraction"
   ```
3. **Inspect the response**: `RenderCVResponse.used_llm` is `true` if the polish step ran. `llm_skip_reason` is set if it was skipped (no key, schema mismatch, bullet-count drift).
4. **Compare outputs**: render the same library + JD pair twice, once with `use_llm: false` and once with `use_llm: true`. The polished version reorders bullets, rewords with JD vocab, and rewrites the Summary; the deterministic version only reorders projects/experience by tag overlap and bolds matched terms.
5. **Read the prompt**: `backend/app/services/codex_cv_polish.py` — the `_USER_TEMPLATE` constant spells out all 7 career-ops steps as the user message sent to the model. If you want to tighten it further (e.g. add the exit-narrative bridge, force a specific language, demand quantification), edit that string.

## Roadmap to full parity

Quick wins (small PRs):
- Paper format flag: accept `paper: letter | a4` in `RenderCVRequest`, swap `\documentclass[…,letterpaper]` vs `[…,a4paper]`.
- Page count in `RenderCVResponse`: parse `tectonic` stdout or count `\page` events.
- Exit-narrative bridge: optional field in the library (`narrative_bridge: str`) that the LLM is told to preserve at the end of the Summary.

Larger work (separate modules):
- Application tracker (`applications.md` equivalent — extend `MatchRun` / per-job state).
- Offer evaluation report (career-ops's deep eval blocks A-F).
- Interview-prep mode (STAR generation from match strongest_points).
