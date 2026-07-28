#!/usr/bin/env bash
# CATALYX — record a score run + emit the opportunity/regime FACTS, in one call.
#
# Shared by /catalyx-heatmap (steps 11-12) and /catalyx-review (Step 5c) — the two used to narrate
# the SAME six commands separately. Two kinds of output, handled differently:
#   • record + register-report  → write to the lake, output not needed → quiet (→ log).
#   • the 4 opportunity/regime scorers → their JSON IS consumed (Claude/the subagent writes the
#     "Opportunities & Rotation" section on top, with WebSearch judgment) → printed to stdout.
# So this collapses 6 narrated Bash calls into 1 and drops the record/register noise, while still
# surfacing exactly the facts the analysis needs. Deterministic — a good subagent target.
#
# Usage:  bash scripts/score_run.sh "<run notes>" [report_path]
set -euo pipefail
cd "$(dirname "$0")/.."

NOTES="${1:-scheduled run}"
REPORT="${2:-}"
LOG="data/reports/score_run_$(date +%Y%m%d).log"
: > "$LOG"
quiet() { echo "\$ uv run $*" >>"$LOG"; uv run "$@" >>"$LOG" 2>&1; }

echo "▶ record run + register report (verbose → $LOG)"
quiet python -m catalyx.store.snapshot_repo record --notes "$NOTES"
if [ -n "$REPORT" ]; then
  quiet python -m catalyx.store.snapshot_repo register-report "$REPORT" --type heatmap
fi

echo "── OPPORTUNITY & REGIME FACTS — consume these; the escalation / buy / rotate calls are YOURS ──"
echo ""
echo "### regime_state + persistence (catalyst_scorer --all)"
uv run python -m catalyx.scorer.catalyst_scorer --all --json
echo ""
echo "### fundamentals health (structural_monitor --all)"
uv run python -m catalyx.thesis.structural_monitor --all
echo ""
echo "### dislocation — opportunities + diversifiers (persists lake table)"
uv run python -m catalyx.scorer.dislocation --window 5 --json
echo ""
echo "### entry_timing — micro-tension + event overhangs (persists lake table)"
uv run python -m catalyx.scorer.entry_timing --all --json
