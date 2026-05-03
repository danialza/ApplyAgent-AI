# AI Job-CV Matching Agent — Frontend

Next.js 14 (App Router) + TypeScript + Tailwind CSS UI for the matcher.
Uploads PDF/DOCX CVs, accepts a pasted job description, displays a ranked
result set with score breakdowns, missing skills, strongest points,
improvement suggestions, and semantic-evidence chunks.

## Stack

- Next.js 14 (App Router) + React 18
- TypeScript (strict)
- Tailwind CSS 3
- Native `fetch` (no Axios dependency)

## Project layout

```
frontend/
├── app/
│   ├── layout.tsx          # Root layout, metadata, global styles
│   ├── page.tsx            # Main page (orchestrator, client component)
│   └── globals.css         # Tailwind directives + .card / .section-title primitives
├── components/
│   ├── CVUpload.tsx        # Drag & drop / file picker, multi-file POST
│   ├── UploadedCVList.tsx  # List + delete
│   ├── JobInput.tsx        # Textarea + Analyse button
│   ├── MatchResults.tsx    # Ranked CV cards
│   ├── ScoreCard.tsx       # Overall + sub-score progress bars
│   ├── MissingSkills.tsx   # Matched / missing skill chips
│   ├── StrongestPoints.tsx # Strongest points + improvement tips
│   ├── RecommendationPanel.tsx
│   ├── LoadingState.tsx
│   └── ErrorMessage.tsx
├── lib/
│   ├── api.ts              # fetch wrappers, ApiError
│   └── types.ts            # Mirrors backend pydantic schemas
├── tailwind.config.ts
├── postcss.config.mjs
├── next.config.mjs
├── tsconfig.json
├── package.json
└── .env.local.example
```

## Run locally

```bash
cd frontend
cp .env.local.example .env.local      # set NEXT_PUBLIC_API_URL if not the default
npm install
npm run dev
```

Open <http://localhost:3000>. The backend must be running at the URL set in
`NEXT_PUBLIC_API_URL` (default `http://127.0.0.1:8000`). Start it with:

```bash
cd ../backend
uvicorn app.main:app --reload
```

## Environment

`.env.local`:

| Variable              | Default                  | Purpose                              |
|-----------------------|--------------------------|--------------------------------------|
| `NEXT_PUBLIC_API_URL` | `http://127.0.0.1:8000`  | FastAPI backend base URL (no slash)  |

If you change the backend port, update this and restart `npm run dev`.

## How it talks to the backend

| Action                  | API call                                            |
|-------------------------|-----------------------------------------------------|
| Upload CVs              | `POST /api/cvs/upload` (multipart)                  |
| List existing CVs       | `GET  /api/cvs`                                     |
| Delete a CV             | `DELETE /api/cvs/{cv_id}`                           |
| Analyse pasted JD       | `POST /api/match` `{ job_text }`                    |
| Preview JD from URL     | `POST /api/jobs/from-url` `{ url }`                 |
| Match from JD URL       | `POST /api/match/from-url` `{ url }` (used implicitly via the URL preview path) |
| Bulk match (CSV)        | `POST /api/match/batch-csv` (multipart `file`)      |
| Run agent (full pipeline) | `POST /api/agent/run`                             |

CORS: the backend reads `APP_CORS_ORIGINS` (comma-separated). Default
includes `http://localhost:3000` so the dev server works out of the box.

## Manual job input

Section "2. Add a job description" is a tabbed picker covering all four
input shapes the backend supports:

| Tab            | What it does                                                                            |
|----------------|------------------------------------------------------------------------------------------|
| **Paste text** | A textarea — same as before. Goes to `POST /api/match`.                                   |
| **From URL**   | Public job-posting URL → scraper → matcher (`POST /api/match/from-url`).                  |
| **From file**  | Upload a PDF, DOCX, or TXT JD (≤ 5 MB) → matcher (`POST /api/match/from-file`).            |
| **Bulk CSV**   | 100-row CSV → best CV per row, exportable (`POST /api/match/batch-csv`).                   |

Tab content is mounted on demand so each tab keeps its own draft state
(e.g. the textarea doesn't lose your draft if you peek at the URL tab).
The bulk CSV tab keeps its own self-contained results table — every
other tab feeds extracted text into the shared results panel below.

Fallback chain is documented in each tab's hint text:

- URL scraping respects `robots.txt`, throttles per host, and never
  bypasses logins, paywalls, or anti-bot systems. If extraction fails,
  the error banner suggests the **From file** or **Paste text** tabs.
- File extraction returns a clear `error` for unsupported types,
  oversize uploads, and empty/scanned-image PDFs.
- This tool is for personal job search and portfolio demonstration.

## Bulk CSV flow

Section "4. Bulk import (CSV)" lets you upload a CSV with multiple jobs
and instantly see the best-matching CV for each. Required column:
`description`. Optional: `job_title`, `company`, `location`, `url`,
`salary`, `employment_type`. Hard-capped at 100 rows per upload.

Results render in a sortable-friendly table (best CV, score chip,
missing-skill chips, strongest points, link). Click **Export results CSV**
to download the table for offline review or import into a tracker.

## Agent dashboard

Section "5. Agent run (full pipeline)" runs the entire backend pipeline
in one click and renders progress + results inline:

1. **Progress trace** — five live tiles (`profile`, `queries`,
   `discovery`, `ranking`, `tailoring`) with per-step status icons and
   detail strings (e.g. "27 jobs", "3 tailoring bundles").
2. **Smart queries** — the queries the agent generated from your
   profile, plus role / skill / tool tag chips.
3. **Discovered jobs** — top results from the public sources, each with
   its source badge and `matched_terms` chips.
4. **Ranked** — best CV per job with a tier-coloured score chip and
   missing-skill chips.
5. **Tailoring suggestions** — for the top-N jobs, structured panels
   showing skills to add / emphasize, ATS keywords, sections to add,
   and (collapsible) bullet-rewrite candidates.

Knobs:

- **Discover** — cap on jobs pulled per source (default 30).
- **Rank** — cap on jobs scored against the CV pool (default 15).
- **Tailor** — cap on full tailoring bundles produced (default 5).

### Edit queries & tags before running

Click **Edit queries & tags** to open the inline editor. The editor
fetches the current profile-derived defaults from
`GET /api/profile/queries` so you start from the auto-derived baseline,
then lets you:

- Add / remove / rewrite the query strings (one per line, free text).
- Edit the tag chip lists for **roles**, **skills**, **tools**,
  **domains**.
- Edit the platform-specific tag baskets (**linkedin**, **indeed**,
  **general**) — useful when one platform indexes by different
  vocabulary than the others.
- Hit **Reset to profile** to discard edits and go back to the
  auto-derived defaults.

When edits are present, the next agent run sends them as `queries` /
`tags` overrides; the backend's `queries` step records them as such in
the progress trace.

The dashboard requires at least one uploaded CV — the backend
auto-builds the unified profile from existing CVs the first time the
agent runs.

## UI states

- **Empty**: dashed placeholder when no CVs / no results.
- **Loading**: spinner with contextual label (loading CVs, analysing JD).
- **Error**: dismissable red banner at the top of the page.
- **Disabled**: Analyse button stays disabled until at least one CV is
  uploaded and the textarea has 20+ characters.

## Build

```bash
npm run build
npm start
```
