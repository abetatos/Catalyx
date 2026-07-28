#!/usr/bin/env bash
# CATALYX — deterministic post-run refresh chain.
#
# Runs AFTER a score run is recorded (sector_snapshot in the lake). Rebuilds the 4 model
# portfolios, their NAV vs SPY (trailing backtest + live walk-forward), the real-book NAV,
# and the rotation targets anchored to held sectors.
#
# WHY THIS EXISTS: the review's Step 5b used to narrate ~9 separate Bash calls, each dumping
# verbose output into the conversation. This collapses them into ONE call. The genuinely verbose
# steps (portfolio build-all, dislocation) go to a log; the compact NAV summary lines (last NAV /
# return / vs-SPY per strategy) stay on stdout AS the digest the review reports. Pure-deterministic
# (no reasoning/WebSearch), so it is safe to run unattended and is a good hook/subagent target.
# Called by /catalyx-review Step 5b.
#
# Usage:  bash scripts/post_run.sh
set -euo pipefail
cd "$(dirname "$0")/.."

LOG="data/reports/post_run_$(date +%Y%m%d).log"
: > "$LOG"
quiet() { echo "\$ uv run $*" >>"$LOG"; uv run "$@" >>"$LOG" 2>&1; }

echo "▶ build 4 model portfolios (verbose → $LOG)"
quiet python -m catalyx.execution.portfolio build-all

echo "▶ NAV vs SPY per strategy (compact digest):"
for p in catalyx momentum equal_weight low_crowding; do
  uv run python -m catalyx.execution.nav_engine model "$p" --backtest-days 180
  uv run python -m catalyx.execution.nav_engine live "$p"
done

echo "▶ real-book NAV vs SPY:"
uv run python -m catalyx.execution.nav_engine real real --benchmark SPY

echo "▶ rotation targets (anchored to held sectors; verbose → $LOG)"
held=$(uv run python -m catalyx.store.movement_repo positions 2>>"$LOG" \
  | python3 -c "import sys,json;print(','.join(sorted({h['sector_id'] for h in json.load(sys.stdin).get('holdings',[])})))" 2>>"$LOG" || true)
if [ -n "${held:-}" ]; then
  quiet python -m catalyx.scorer.dislocation --anchor-sectors "$held"
  echo "   ✓ rotation anchored to: $held"
else
  echo "   – no open positions → rotation skipped"
fi

echo "✅ post-run refresh complete. Report each strategy's vs_benchmark_pct from the digest above."
