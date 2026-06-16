# Claude-subscription stack

A second, fully isolated deployment that runs the **same app with the
same features**, but drives the LLM through your **Claude Pro/Max
subscription** instead of an `ANTHROPIC_API_KEY` — so polish, ranking,
notes extraction, etc. cost **no API credits**.

It runs side-by-side with the normal API-key stack. Nothing about the
day-to-day stack (ports `3000` / `8000`) changes.

| | API-key stack | Claude-subscription stack |
|---|---|---|
| Compose file | `docker-compose.yml` | `docker-compose.claude.yml` |
| Frontend | http://localhost:3000 | http://localhost:3100 |
| Backend | http://localhost:8000 | http://localhost:8100 |
| Containers | `ai-job-cv-*` | `ai-job-cv-*-claude` |
| Database | `applyagentai_backend_data` | **same volume — shared** |
| LLM | `ANTHROPIC_API_KEY` (billed) | Pro/Max subscription (no credits) |
| Model | `claude-sonnet-4-6` | `opus` (subscription alias) |

Both stacks share **one** SQLite database, so your master CV, sources,
and applications are always in sync — edit on either port, see it on
both. WAL mode + a busy timeout (set in `app/db/database.py`) make
concurrent access safe for single-user use. The shared volume is
declared `external`, so `make claude-clean` can never delete your data.

## How auth works

On macOS the Claude Code CLI keeps the Pro/Max session as an **OAuth
token** in the Keychain (`Claude Code-credentials`). A Linux container
can't read the Keychain, so we snapshot that token into `.env.claude`
and pass it to the backend as `CLAUDE_CODE_OAUTH_TOKEN`. The backend
shells out to `claude --print --model sonnet`, which authenticates with
that token — no API key, no per-call billing.

The token is **short-lived (~a few hours)**. The host CLI refreshes it
automatically; we just re-copy the current value when needed.

## First run

```bash
make claude-up               # refreshes the token, builds + starts 3100/8100
make claude-watch-install    # hourly token auto-refresh (no more 401s)
```

Open http://localhost:3100. The master CV is already there — the DB is
shared with the API-key stack.

## Day-to-day

```bash
make claude-up        # start / rebuild
make claude-down      # stop (keeps data)
make claude-logs      # tail logs
make claude-clean     # stop + delete this stack's volumes only
```

## Token auto-refresh (no more 401s)

The Pro OAuth token is short-lived (~hours). Two things keep it fresh
automatically:

1. The backend reads the token from a live file
   (`~/.applyagent/claude_token`, mounted read-only) on **every** LLM
   call — so refreshing the file takes effect with **no container
   restart**.
2. `make claude-watch-install` installs a launchd agent that rewrites
   that file every hour (it pings the host CLI to force a Keychain
   refresh first).

```bash
make claude-watch-install     # turn on hourly refresh
make claude-watch-status      # check it
make claude-watch-uninstall   # turn it off
```

If you ever see a 401 before the watcher kicks in, refresh by hand —
no restart needed:

```bash
make claude-token
```

## Notes

- `.env.claude` holds your OAuth token. It's gitignored (`.env.*`).
  Never commit it.
- Model: the subscription CLI accepts the family aliases `sonnet`,
  `opus`, `haiku`. The backend maps any full id (e.g.
  `claude-sonnet-4-6`) to its alias automatically, so the UI model
  picker won't break this stack.
- Subscription calls run through the local `claude` CLI and are a bit
  slower than the raw API — `opus` headless polish of the full library
  runs a few minutes, so `LLM_TIMEOUT_SECONDS` is bumped to 600s for
  this stack.
- The two stacks **share one database** (the `applyagentai_backend_data`
  volume), so data is always in sync. It's declared `external` in the
  claude compose file, so `make claude-clean` removes only this stack's
  own caches — never your DB.
- Avoid running a tailored render on **both** ports at the exact same
  moment; WAL + an 8s busy timeout handle normal single-user switching,
  but two simultaneous heavy writes can still contend.
