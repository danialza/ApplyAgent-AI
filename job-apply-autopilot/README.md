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
| Existing CV API | http://127.0.0.1:8300 | Shared Master CV, facts, and PDF tailoring |
| Existing app | http://localhost:3300 | Unchanged and independent |

## Start

The existing CV API on port 8300 must already be running. Then:

```bash
cd /Users/danial/ApplyAgent-AI-Codex/job-apply-autopilot
npm install
npm run dev:all
```

Open http://localhost:3500.

The default AI mode uses the locally signed-in Claude monthly subscription. If
that command is unavailable, the agent can fall back to the Anthropic key loaded
from the parent project's `.env`. Secrets are never returned to the browser.

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
`.env.example`. `CV_LENGTH=auto` lets the existing tailor choose the best page
target per role; the UI also supports one-page, concise, and two-page modes.
