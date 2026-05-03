#!/usr/bin/env bash
# Pick free host ports for the frontend + backend and write them
# (plus the matching CORS origin and NEXT_PUBLIC_API_URL) into a small
# env file that docker compose can consume via `--env-file`.
#
# Usage:
#     scripts/pick-ports.sh                   # prints to stdout
#     scripts/pick-ports.sh --write           # writes .env.ports
#
# Defaults: 3000 / 8000. When taken, walks up to the next free port.
# Cap at +50 so the script can't loop forever in pathological cases.

set -euo pipefail

PREFERRED_FRONTEND="${PREFERRED_FRONTEND:-3000}"
PREFERRED_BACKEND="${PREFERRED_BACKEND:-8000}"
MAX_OFFSET=50

is_busy() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
  else
    # Fallback: try to bind via Python.
    python3 - "$port" <<'PY' 2>/dev/null && return 1 || return 0
import socket, sys
s = socket.socket()
s.settimeout(0.2)
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
    s.close()
except OSError:
    sys.exit(1)
PY
  fi
}

pick_port() {
  local preferred="$1"
  local port="$preferred"
  local tries=0
  while is_busy "$port"; do
    port=$((port + 1))
    tries=$((tries + 1))
    if [ "$tries" -gt "$MAX_OFFSET" ]; then
      echo "ERROR: no free port found near $preferred (tried up to $((preferred + MAX_OFFSET)))" >&2
      exit 1
    fi
  done
  echo "$port"
}

FRONTEND_PORT=$(pick_port "$PREFERRED_FRONTEND")
BACKEND_PORT=$(pick_port "$PREFERRED_BACKEND")

# Compose the URLs that depend on the picked ports.
NEXT_PUBLIC_API_URL="http://localhost:${BACKEND_PORT}"
BACKEND_CORS_ORIGINS="http://localhost:${FRONTEND_PORT}"

OUT=$(cat <<EOF
FRONTEND_PORT=${FRONTEND_PORT}
BACKEND_PORT=${BACKEND_PORT}
NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
BACKEND_CORS_ORIGINS=${BACKEND_CORS_ORIGINS}
EOF
)

if [ "${1:-}" = "--write" ]; then
  echo "$OUT" > .env.ports
  echo "Wrote .env.ports:" >&2
  echo "$OUT" >&2
else
  echo "$OUT"
fi
