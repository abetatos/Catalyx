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

# LIVE only. The `model --backtest-days 180` curve was rebuilt for all 4 strategies every run,
# but track_record.yaml states plainly that projecting today's holdings backwards is HYPOTHETICAL,
# never a track record — so it cost 4 price windows a run to redraw a curve no decision may use.
# It is still available on demand: `nav_engine model <id> --backtest-days N`.
echo "▶ live NAV vs SPY per strategy (the real walk-forward track record):"
for p in catalyx momentum equal_weight low_crowding; do
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

# The end of the chain, and the only part that names € amounts. Everything above measures; this
# decides. It is LAST because it consumes the freshly built model book (portfolio build-all) and
# the freshly marked real book, and it is on stdout in full because it IS the review's Step 6/9
# table — the user reads these rows and executes with /catalyx-open and /catalyx-close.
# Measurement before decision. position_metrics does not recommend anything — it writes the
# per-position facts (EUR P&L split price/FX, drawdown from peak, days held, score drift vs the
# score_context the position was OPENED on) and the book shape (HHI, FX exposure, vol/Sharpe/beta,
# model overlap) into the lake, so next month these numbers can be compared to themselves instead
# of re-derived in a conversation. It runs before rebalance because it explains the rows rebalance
# is about to act on. Plan §3.4.
echo ""
echo "── POSITION & BOOK METRICS — measurement only ──"
echo ""
uv run python -m catalyx.execution.position_metrics

echo ""
echo "── REBALANCE — pipeline target vs the real book, in €, after tax ──"
echo ""
uv run python -m catalyx.execution.rebalance --strategy catalyx

echo ""
echo "✅ post-run refresh complete. Report each strategy's vs_benchmark_pct from the digest above,"
echo "   then work the rebalance table row by row: execute per rule, or log an override with a reason."
