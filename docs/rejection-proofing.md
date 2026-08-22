# Rejection-proofing: the recruiter scorecard

Nine upgrades aimed at the two gates that actually reject applications —
the ATS/keyword gate and the human 7-second scan. Validated on the
isolated stack at **3300 / 8300** (`make rp-up`), which has its own
database so the metrics harvester can never write to the master CV you
use daily.

## What each one does

| # | Upgrade | Where |
|---|---|---|
| 1 | **Metric gate** — master-CV quantification health with a target (40%); warns before you tailor anything | `GET /api/cv/metrics/density`, Master CV banner |
| 2 | **Evidence-graded coverage** — every JD skill is `evidenced` (proven in a bullet), `mentioned` (skills-line only), or `missing` | `cv_scorecard.coverage_breakdown` |
| 3 | **Hard gates** — the JD's `qualifications` / `education_requirements` (years, degree) are finally *used*, not discarded | `cv_scorecard.hard_gates` |
| 4 | **Required vs preferred** — split, because missing a required skill is fatal and missing a preferred one is not | scorecard `coverage` |
| 5 | **Verb / hedge lint** — flags weak openers and "familiar with"-style phrasing; the polish prompt now also *prevents* them | `cv_scorecard.bullet_audit` + polish rule |
| 6 | **7-second recruiter scan** — an LLM reads ONLY the top of page one and returns read-on / unsure / bin plus concrete fixes | `cv_scorecard.recruiter_scan` |
| 7 | **Fit score + verdict** — weighted 0-100 with a go/no-go call | `cv_scorecard._fit` |
| 8 | **Seniority calibration** — does the CV's language read at the level the JD asks for? | `cv_scorecard.seniority_check` |
| 9 | **Cover-letter AI-tell cleanup** — bans "excited to", "passionate about", "delve", "leverage my", … | `cover_letter.py` |

## Why coverage changed

The old number was `keyword in latex.lower()` — a keyword sitting in the
skills line scored exactly like one backed by an achievement. You could
hit 100% by listing words. Recruiters read the skills line, look for
proof in the bullets, and reject what they can't find. The scorecard now
separates the two, and the fit score weights **evidenced** required
skills at 40 points versus 10 for merely naming them.

## JD steering — what the tailor may and may not do

The default contract is **Aggressive tailor** (`enhance_tailor=true`). It
may add plausible adjacent skills and implementation details, expand thin
bullets, adopt the JD's vocabulary, and add conservative estimates when a
number makes the result more useful. Estimated values must be visibly
marked with `~` or a range. An exact metric already present in the source
must not be replaced with a different value.

Project names, employer names, professional role titles, dates, degrees,
and institutions are immutable in both modes. The polish layer must not
manufacture a different career identity, employer, promotion, or timeline.

Set `enhance_tailor=false` for the conservative path. That mode only
reframes and reorders facts already supported by the Master CV; it does not
add skills or metrics.

## Running it

```bash
make rp-seed   # copy the live DB into this stack's own volume
make rp-up     # build + start on 3300 / 8300
```

Open http://localhost:3300. The scorecard appears under the render
result; the metric gate sits in the Master CV panel.
