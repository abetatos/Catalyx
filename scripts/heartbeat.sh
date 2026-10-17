#!/usr/bin/env bash
# CATALYX — unattended heartbeat (the launchd target). NO LLM, NO WebSearch, no credits:
# everything here is the deterministic backbone. Its job is to keep the cheap data fresh and
# decide WHETHER A HUMAN IS NEEDED — the intelligence layer stays the Claude Code session
# (permanent architecture decision), so the ping's answer is "open a session", never "traded".
#
#   1. v7 candidate snapshots: COT + fund valuation (best-effort, degrade to None)
#   2. Google Trends — only when the latest snapshot is >28d old (rate-limited source)
#   3. Auto-observable indicators --apply (public series via the official indicator_update
#      path, dedup-safe) + intensity write-back so stored scores track the observations
#   4. pre_run.sh --check — the silent verdict: rule actions, exit flags, stale verdicts,
#      lifecycle, ±10% book move, 45d review ceiling, VIX ramp
#   5. exit 10 → macOS notification; exit 0 → silence (silence is a RESULT)
#
# Install (once):  bash scripts/heartbeat.sh --install   (loads the launchd agent, Tue+Fri 08:12)
# Manual run:      bash scripts/heartbeat.sh
set -uo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
# The three CATALYX_* overrides below exist so the tests can drive this script without touching
# the network, the real log or the notification centre. Production never sets them.
LOG="${CATALYX_HEARTBEAT_LOG:-data/reports/heartbeat_$(date +%Y%m%d).log}"
CHECK_CMD="${CATALYX_CHECK_CMD:-bash scripts/pre_run.sh --check}"
PLIST_SRC="scripts/launchd/com.catalyx.heartbeat.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.catalyx.heartbeat.plist"

if [ "${1:-}" = "--install" ]; then
  mkdir -p "$HOME/Library/LaunchAgents"
  sed -e "s|__REPO__|$REPO|g" -e "s|__HOME__|$HOME|g" "$PLIST_SRC" > "$PLIST_DST"
  launchctl bootout "gui/$(id -u)" "$PLIST_DST" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
  echo "installed: $PLIST_DST (Tue+Fri 08:12 local). Remove: launchctl bootout gui/\$(id -u) $PLIST_DST"
  exit 0
fi

: > "$LOG"
run() { echo "\$ $*" >>"$LOG"; "$@" >>"$LOG" 2>&1 || echo "  (non-fatal: $* failed)" >>"$LOG"; }
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"

# Tests set CATALYX_HEARTBEAT_SKIP_DATA=1 to skip the network steps 1-3 (RULE5 stays 0).
RULE5=0
if [ "${CATALYX_HEARTBEAT_SKIP_DATA:-0}" != "1" ]; then
  # 1-2 — candidate-signal snapshots (each degrades to None; a failure never blocks the verdict)
  run "$UV" run python -m catalyx.data.cot_data
  run "$UV" run python -m catalyx.data.valuation_data
  LATEST_TRENDS=$(ls data/snapshots/trends_snapshot_*.json 2>/dev/null | sort | tail -1)
  if [ -z "$LATEST_TRENDS" ] || [ -n "$(find "$LATEST_TRENDS" -mtime +28 2>/dev/null)" ]; then
    run "$UV" run python -m catalyx.data.trends_data
  fi

  # 3 — public-series observations through the official write path, then write-back
  RULE5=$("$UV" run python -m catalyx.data.indicator_sources --apply 2>>"$LOG" | tee -a "$LOG" | grep -c "Rule 5" || true)
  run "$UV" run python -m catalyx.scorer.intensity_engine --all --write-back
fi

# 4 — the verdict
CHECK_OUT=$(bash -c "$CHECK_CMD" 2>>"$LOG")
CHECK_RC=$?
echo "$CHECK_OUT" >>"$LOG"

MSG=""
# Only rc 0 (quiet) and rc 10 (alerts) are verdicts. Anything else means the check itself did
# not run — on 2026-09-04..10-16 that was rc 127 (`uv` not on launchd's PATH), printed as
# "quiet" for six weeks. A broken watchdog must say so.
case "$CHECK_RC" in
  0)  ;;
  10) MSG="$CHECK_OUT" ;;
  *)  MSG="  • HEARTBEAT BROKEN: the check exited $CHECK_RC — no verdict was computed. Log: $LOG" ;;
esac
if [ "${RULE5:-0}" -gt 0 ]; then
  MSG="$MSG
  • $RULE5 auto-indicator(s) moved >10% vs stored (Rule 5) — see $LOG"
fi

# 5 — speak only when the numbers say so
if [ -n "$MSG" ]; then
  echo "$MSG"
  if [ "${CATALYX_NO_NOTIFY:-0}" != "1" ] && command -v osascript >/dev/null 2>&1; then
    osascript -e "display notification \"$(echo "$MSG" | grep -m1 '•' | sed 's/\"//g' | cut -c1-120)\" with title \"CATALYX — review worth running\" sound name \"Glass\"" || true
  fi
  exit 10
fi
echo "· quiet — nothing needs a review ($(date +%F))"
exit 0
