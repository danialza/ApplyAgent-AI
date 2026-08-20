#!/usr/bin/env bash
# Copy the LIVE stack's SQLite DB into the rejection-proof validation
# stack's isolated volume, so 3300/8300 runs against realistic data
# without ever writing to the database you use daily.
set -euo pipefail
SRC_VOL="applyagentai_backend_data"
DST_VOL="applyagent-rp_backend_data_rp"

docker volume inspect "$SRC_VOL" >/dev/null 2>&1 || {
  echo "✗ live volume '$SRC_VOL' not found — start the main stack once first."; exit 1; }
docker volume create "$DST_VOL" >/dev/null

docker run --rm -v "$SRC_VOL":/src:ro -v "$DST_VOL":/dst alpine:3.20 sh -c '
  [ -f /src/app.db ] || { echo "✗ /src/app.db missing"; exit 1; }
  cp /src/app.db /dst/app.db
  [ -f /src/app.db-wal ] && cp /src/app.db-wal /dst/app.db-wal || true
  [ -f /src/app.db-shm ] && cp /src/app.db-shm /dst/app.db-shm || true
  echo "✓ seeded $(wc -c < /dst/app.db) bytes"
'
echo "Done — restart the stack: make rp-up"
