#!/usr/bin/env bash
# One-time: copy the API-key stack's SQLite DB (your live master CV,
# sources, applications) into the claude-subscription stack's volume,
# so the parallel deployment starts with the same data instead of an
# empty library.
#
# Safe to run while both stacks exist; it only touches the DESTINATION
# (claude) volume. Re-running overwrites the claude DB with a fresh
# copy of the main one.
#
# Usage: scripts/claude-seed-from-main.sh   (or: make claude-seed-from-main)
set -euo pipefail

SRC_VOL="applyagentai_backend_data"
DST_VOL="applyagent-claude_backend_data_claude"

if ! docker volume inspect "$SRC_VOL" >/dev/null 2>&1; then
  echo "✗ Source volume '$SRC_VOL' not found. Is the API-key stack built?"
  echo "  (Run 'make docker-up' once so the volume exists.)"
  exit 1
fi

# The destination volume is created lazily by 'make claude-up'. Create
# it now if needed so the copy has somewhere to land.
docker volume create "$DST_VOL" >/dev/null

echo "Copying app.db: $SRC_VOL → $DST_VOL …"
docker run --rm \
  -v "$SRC_VOL":/src:ro \
  -v "$DST_VOL":/dst \
  alpine:3.20 sh -c '
    if [ -f /src/app.db ]; then
      cp /src/app.db /dst/app.db
      # SQLite WAL/SHM sidecars, if present, keep the copy consistent.
      [ -f /src/app.db-wal ] && cp /src/app.db-wal /dst/app.db-wal || true
      [ -f /src/app.db-shm ] && cp /src/app.db-shm /dst/app.db-shm || true
      echo "✓ app.db copied ($(wc -c < /dst/app.db) bytes)."
    else
      echo "✗ /src/app.db not found in source volume."; exit 1
    fi
  '

echo "Done. Restart the claude stack to pick it up: make claude-up"
