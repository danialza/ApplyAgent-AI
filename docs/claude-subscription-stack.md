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
| Volumes | `applyagentai_*` | `applyagent-claude_*` |
| LLM | `ANTHROPIC_API_KEY` (billed) | Pro/Max subscription (no credits) |

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
make claude-token            # snapshot the Pro token → .env.claude
make claude-up               # build + start on 3100 / 8100
make claude-seed-from-main   # (optional) copy your live master CV in
make claude-up               # restart so the seeded DB is picked up
```

Open http://localhost:3100.

## Day-to-day

```bash
make claude-up        # start / rebuild
make claude-down      # stop (keeps data)
make claude-logs      # tail logs
make claude-clean     # stop + delete this stack's volumes only
```

## When LLM calls start failing with an auth error

The token expired. Refresh and restart:

```bash
make claude-token
make claude-up
```

## Notes

- `.env.claude` holds your OAuth token. It's gitignored (`.env.*`).
  Never commit it.
- Model: the subscription CLI accepts the family aliases `sonnet`,
  `opus`, `haiku`. The backend maps any full id (e.g.
  `claude-sonnet-4-6`) to its alias automatically, so the UI model
  picker won't break this stack.
- Subscription calls run through the local `claude` CLI and are a bit
  slower than the raw API; `LLM_TIMEOUT_SECONDS` is bumped to 180s for
  this stack.
- The two stacks have separate databases. `make claude-seed-from-main`
  copies the API-key stack's DB into the claude stack once; after that
  they diverge.
