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

# v7 candidate-signal snapshots, best-effort BEFORE the run records (each degrades to None).
# Trends is NOT here — rate-limited source; refresh monthly via `python -m catalyx.data.trends_data`.
# indicator_sources runs DRY (facts for the review) — applying observations mutates catalyst
# YAMLs, which stays with the scan (`--apply` there, then intensity write-back).
echo "▶ candidate-signal snapshots (COT · valuation; best-effort → $LOG)"
quiet python -m catalyx.data.cot_data || true
quiet python -m catalyx.data.valuation_data || true

echo "▶ record run + register report (verbose → $LOG)"
quiet python -m catalyx.store.snapshot_repo record --notes "$NOTES"
if [ -n "$REPORT" ]; then
  quiet python -m catalyx.store.snapshot_repo register-report "$REPORT" --type heatmap
fi

# entry_timing is SCOPED to the sectors a decision could touch this cycle (held ∪ top-N, from the
# pre-run state digest). It answers "is this a good moment to enter THIS position" — scoring ~50
# sectors to read 15 of them was pure cost. dislocation stays full-universe on purpose: its whole
# job is cross-sectional (correlation, contagion vs idiosyncratic, low-correlation diversifiers),
# so narrowing its input would change the ANSWER, not just the price.
STATE="data/reports/state_$(date +%Y%m%d).json"
SCOPE=""
if [ -f "$STATE" ]; then
  SCOPE=$(python3 -c "import json,sys;d=json.load(open('$STATE'));print(','.join(d['work_list']['sectors_decision_relevant']))" 2>/dev/null || true)
fi

# DIGESTS, not raw JSON (v4, plan D-a). These four calls used to emit ~190 KB of JSON per run, of
# which the review reads a handful of fields per sector; the rest is per-event trace that is already
# in the lake and re-derivable with one scoped `--json` call when a single sector is being debugged.
echo "── OPPORTUNITY & REGIME FACTS — consume these; the escalation / buy / rotate calls are YOURS ──"
echo ""
echo "### auto-observable indicators — live vs stored (DRY; apply via the scan)"
uv run python -m catalyx.data.indicator_sources || true
echo ""
echo "### regime_state + persistence (catalyst_scorer --all)"
uv run python -m catalyx.scorer.catalyst_scorer --all --digest
echo ""
echo "### fundamentals health (structural_monitor --all)"
uv run python -m catalyx.thesis.structural_monitor --all
echo ""
echo "### dislocation — opportunities + diversifiers (full universe by design; persists lake table)"
uv run python -m catalyx.scorer.dislocation --window 5
echo ""
if [ -n "$SCOPE" ]; then
  echo "### entry_timing — micro-tension + event overhangs (scoped to held ∪ top-N)"
  uv run python -m catalyx.scorer.entry_timing --sectors "$SCOPE"
else
  echo "### entry_timing — micro-tension + event overhangs (no state digest → full universe)"
  echo "    (run \`bash scripts/pre_run.sh\` first to scope this step)"
  uv run python -m catalyx.scorer.entry_timing --all
fi
