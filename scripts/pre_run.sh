#!/usr/bin/env bash
# CATALYX — deterministic PRE-run chain. Runs FIRST, before any WebSearch.
#
# WHY THIS EXISTS (v3 Phase 1, docs/PLAN_v3_lean_pipeline_rebalance.md §2.7):
#   A review used to OPEN with ~40 searches and only discover the book's real problems (a −21%
#   position, a stale fundamental verdict, a spent catalyst still scoring) after the expensive
#   phases had already run. Everything below is computable offline from the lake and the Tier-1
#   files, so it runs first and for free — and its digest tells the scan and the study refresh
#   WHICH catalysts and sectors are worth a search. Facts before questions.
#
#   Everything here is pure-deterministic (no reasoning, no WebSearch), which also makes it a
#   safe unattended/cron target: run it weekly as a cheap heartbeat and it pings only when a
#   rule action, a stale verdict, or a lifecycle transition actually appears.
#
# Verbose output → data/reports/pre_run_<date>.log; the digest the review consumes → stdout.
#
# Usage:  bash scripts/pre_run.sh [--offline] [--check]
#           --offline  skip every network fetch and serve the warm price cache only
#           --check    QUIET heartbeat: run the same chain into the log, then print ONLY if
#                      something needs a human — a rule action, a stale catalyst verdict, a
#                      pending lifecycle transition, or a book move past the alert threshold.
#                      Exit 0 = nothing to do, 10 = attention needed. This is the cron target:
#                      an unattended job that speaks every week teaches you to ignore it, so
#                      this one is silent by construction until the numbers say otherwise.
set -euo pipefail
cd "$(dirname "$0")/.."

OFFLINE=0
CHECK=0
for a in "$@"; do
  [ "$a" = "--offline" ] && OFFLINE=1 && export CATALYX_PRICES_OFFLINE=1
  [ "$a" = "--check" ] && CHECK=1
done

LOG="data/reports/pre_run_$(date +%Y%m%d).log"
: > "$LOG"
quiet() { echo "\$ uv run $*" >>"$LOG"; uv run "$@" >>"$LOG" 2>&1; }
# In --check mode every narrated step goes to the log instead of stdout; only the alert block
# at the end may speak.
say()  { [ "$CHECK" = "1" ] && echo "$@" >>"$LOG" || echo "$@"; }
show() { if [ "$CHECK" = "1" ]; then quiet "$@"; else uv run "$@"; fi; }

if [ "$OFFLINE" = "1" ]; then
  say "▶ offline mode — serving the warm price cache, no network"
else
  # ONE fetch for the whole run. Every downstream scorer/NAV call reads this cache, so the run
  # is a consistent snapshot of a single price date instead of ~15 independent fetches.
  say "▶ price cache refresh (shared by every scorer + NAV)"
  show python -m catalyx.data.prices refresh

  say "▶ market data + flow snapshots (verbose → $LOG)"
  quiet python -m catalyx.data.market_data
  quiet python -m catalyx.data.flow_data --write
fi

say ""
say "── RUN STATE — the deterministic facts. Search only what the work list names. ──"
say ""
show python -m catalyx.store.run_state --write

say ""
say "▶ catalyst lifecycle (dry run — apply inside the review after the scan supplies reversals)"
show python -m catalyx.scorer.catalyst_lifecycle

say ""
# Replaces the "⚠ PRE-CALIBRATION: 0 closed positions" banner, which could never clear (it was
# tied to closing 50 positions — years away at this cadence). Calibration needs no closes, only
# a run old enough to have forward history, so it accumulates one window per review from here.
say "▶ scoring calibration (measured rank IC per dimension — accumulates into the lake)"
show python -m catalyx.scorer.calibration --write

say ""
# The escape hatch, audited. Every deviation from a rule action is in lake `override_log`; this
# prices the ones old enough to have a forward window and tallies them BY AUTHOR. It runs in the
# pre-run, before any reasoning, so the review opens knowing what its own past deviations cost —
# and so Claude's suspension (net-negative over >= min_scored) is a fact on the table rather than
# a claim made after the fact. Plan §4.3.
say "▶ override tally (deviations from the rule, scored against it)"
show python -m catalyx.execution.rebalance overrides

if [ "$CHECK" = "1" ]; then
  # The heartbeat verdict. Reads the state file this run just wrote plus a fresh rebalance (the
  # price cache is already warm, so it costs no extra fetch) and decides whether a human is
  # needed. Silence here is a RESULT, not a failure — see the --check note at the top.
  uv run python - "$LOG" <<'PY'
import json, subprocess, sys
from datetime import date
from pathlib import Path

state = Path(f"data/reports/state_{date.today():%Y%m%d}.json")
if not state.exists():
    print("⚠ pre_run --check: run_state wrote no state file — see the log"); sys.exit(10)
st = json.loads(state.read_text(encoding="utf-8"))
att, book = st.get("attention", {}), st.get("book", {})

n_actions, summary = 0, None
try:
    out = subprocess.run([sys.executable, "-m", "catalyx.execution.rebalance",
                          "--json", "--no-persist"], capture_output=True, text=True, check=True)
    reb = json.loads(out.stdout)
    n_actions = int(reb["book"]["n_actions"])
    r = reb["book"]["deploy_ratio"]
    summary = (f"deployed {reb['book']['deployed_pct']:.0f}% vs rule {r['ratio']:.0%} · "
               f"{n_actions} rule actions")
except Exception as exc:
    summary = f"rebalance unavailable ({exc})"

move = book.get("unrealized_pct")
alerts = []
if n_actions:
    alerts.append(f"{n_actions} rule action(s) on the book — {summary}")
flagged = att.get("positions_needing_action") or []
if flagged:
    # It is a LIST of sector ids, not a count — naming them is the whole value of the ping.
    names = flagged if isinstance(flagged, list) else [str(flagged)]
    alerts.append(f"{len(names)} position(s) the exit watcher flags: {', '.join(map(str, names))}")
if att.get("stale_verdicts"):
    alerts.append(f"{att['stale_verdicts']} catalyst verdict(s) past the freshness limit")
if att.get("pending_lifecycle"):
    alerts.append(f"{att['pending_lifecycle']} pending lifecycle transition(s)")
if move is not None and abs(float(move)) >= 10:
    alerts.append(f"book at {float(move):+.1f}% unrealized — past the ±10% pull-forward threshold")

if not alerts:
    print(f"· quiet — nothing needs a review ({summary})")
    sys.exit(0)
print("── CATALYX heartbeat: a review is worth running ──")
for a in alerts:
    print(f"  • {a}")
print(f"\n  Facts: {state}  ·  full log: {sys.argv[1]}")
print("  Next: /catalyx-review scheduled")
sys.exit(10)
PY
  exit $?
fi

echo ""
echo "✅ pre-run complete. Next: /catalyx-scan, scoped to work_list.must_reverify + should_reverify."
