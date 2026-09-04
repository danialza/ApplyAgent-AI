# ApplyPilot

ApplyPilot is the local, review-first job application copilot. Give it an
application URL and it opens a dedicated visible Chrome profile, reads the job,
requests a tailored CV from the existing Master CV service, fills the form, and
stops before the final submission action.

## Local services

| Service | URL | Purpose |
| --- | --- | --- |
| ApplyPilot UI | http://localhost:3500 | Progress, questions, settings, and review |
| ApplyPilot agent | http://127.0.0.1:8500 | Browser automation and local memory |
| ApplyPilot CV API | http://127.0.0.1:8400 | Dedicated runtime; shared Master CV database and PDF tailoring |
| Existing app | http://localhost:3300 | Unchanged and independent |

## Start

Start the dedicated CV runtime, then the ApplyPilot UI and agent:

```bash
cd /Users/danial/ApplyAgent-AI-Codex/job-apply-autopilot
npm install
npm run cv-backend:up
npm run dev:all
```

Open http://localhost:3500.

The form-filling AI defaults to the locally signed-in Claude monthly
subscription. CV generation runs in its own backend on port 8400, with a
separately selectable provider and model. It reads configured API keys from the
parent project's `.env`; secrets are never returned to the browser.

The port 8400 backend and the existing backend mount the same live database
volume. Master CV data, learned facts, and application records are therefore
shared, while runtime model selection stays isolated from the app on port 3300.

## Safety and privacy

- The final Submit/Send/Complete action is hard-locked: ApplyPilot only prepares
  the form and brings Chrome forward for human review.
- CAPTCHA, security checks, and login pages pause the run for the user.
- Learned answers are stored locally in `.data/applypilot.sqlite` and mirrored to
  the existing CV service's facts store so both apps share them.
- Chrome sessions use `.data/chrome-profile`, so logins can persist locally.
- Generated PDFs are kept under `outputs/`; both paths are excluded from Git.

## Configuration

Settings can be changed from the UI. Environment defaults are documented in
`.env.example`. The CV section includes provider and model selection, target
length (1, 1.5, 2 pages, or Auto), PDF compilation, LLM polishing, aggressive
tailoring, and a keyword-coverage target. These settings are sent with every new
CV render.
