#!/usr/bin/env bash
# Install / remove a macOS launchd agent that refreshes the Claude Pro
# OAuth token every hour, so the subscription stack never 401s mid-use.
#
# The agent runs scripts/claude-token.sh hourly, which re-pings the host
# CLI (forcing a Keychain refresh) and rewrites ~/.applyagent/claude_token.
# The running backend reads that file on every LLM call, so no container
# restart is needed.
#
#   scripts/claude-watch.sh install     (make claude-watch-install)
#   scripts/claude-watch.sh uninstall   (make claude-watch-uninstall)
#   scripts/claude-watch.sh status
set -euo pipefail

LABEL="com.applyagent.claude-token"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
INTERVAL="${CLAUDE_WATCH_INTERVAL:-3600}"   # seconds; default hourly
LOG="${HOME}/.applyagent/claude-watch.log"

cmd="${1:-status}"

case "$cmd" in
  install)
    mkdir -p "${HOME}/Library/LaunchAgents" "${HOME}/.applyagent"
    # Generate the plist with the repo path baked in. Runs the token
    # refresh under a login shell so Homebrew's PATH (claude, python3)
    # resolves, and at load time once + every INTERVAL seconds.
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>cd "${REPO}" && scripts/claude-token.sh >> "${LOG}" 2>&1</string>
  </array>
  <key>StartInterval</key><integer>${INTERVAL}</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>${LOG}</string>
  <key>StandardErrorPath</key><string>${LOG}</string>
</dict>
</plist>
EOF
    # Reload cleanly if already present.
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "✓ Installed launchd agent ${LABEL} (every ${INTERVAL}s)."
    echo "  plist: ${PLIST}"
    echo "  log:   ${LOG}"
    echo "  It refreshes the token now and hourly. No restart needed."
    ;;
  uninstall)
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "✓ Removed launchd agent ${LABEL}."
    ;;
  status)
    if [[ -f "$PLIST" ]] && launchctl list 2>/dev/null | grep -q "$LABEL"; then
      line="$(launchctl list 2>/dev/null | grep "$LABEL")"
      echo "● installed: ${LABEL}  (PID/exit: ${line%%	*} …)"
      echo "  last exit code: $(echo "$line" | awk '{print $2}') (0 = ok)"
    elif [[ -f "$PLIST" ]]; then
      echo "◐ plist present but not loaded. Try: make claude-watch-install"
    else
      echo "○ not installed. Run: make claude-watch-install"
    fi
    [[ -f "$LOG" ]] && { echo "--- last log lines ---"; tail -5 "$LOG"; } || true
    ;;
  *)
    echo "usage: $0 {install|uninstall|status}"; exit 2 ;;
esac
