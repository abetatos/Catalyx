"""Exit watcher — Family 1 of the sell-signal layer: "has the risk line / the reason broken?"

The BUY stack is fully deterministic (composite → dislocation → entry_timing → regime_state).
The SELL side was not: the `risk_discipline.invalidation[]` stops authored on every movement were
written but **never read by any code**. This module is the bridge that READS them.

For each OPEN position it evaluates three inputs and rolls them into a suggested action:

  (1) PRICE STOPS — each `invalidation[]` item that carries the schema-1.1 structured eval fields
      (`comparator`/`threshold`/`consecutive_days`/`eval_ticker`) is checked DETERMINISTICALLY:
      fetch eval_ticker, count trailing breaching closes, fire only when the breach has held for the
      full `consecutive_days` window (time-independent — a stateless read of price history, the same
      answer at any cadence). A stop with `eval_ticker: null` (no clean feed, e.g. LME inventory) is
      NOT auto-evaluated — it is surfaced as a Claude-checks-with-WebSearch item.

  (2) ASSUMPTIONS — roll up `assumptions[].current_status`; a `violated` is an exit input, a
      `weakening` a watch input. (These statuses are set by Claude in the review, not here.)

  (3) REGIME — the position's sector `regime_state` from the latest run: `breaking` = the thesis is
      turning (reduce); `contested` = watch only (the regime doctrine: a single event is noise).

Plus the one thing the buy side has no mirror for: the AFTER-TAX consequence of exiting now
(`tax_engine`, Spanish CGT) — surfaced ON the recommendation, not just at `/catalyx-close`.

DOCTRINE (user-decided, docs/DESIGN_sell_signals.md §8.1): RECOMMEND-ONLY. This module reports
"inv_NN WOULD FIRE"; it writes NOTHING — not even `triggered=true`. The user marks `triggered` and
runs `/catalyx-close` by hand. Severity arbitration (§5): a fired `full_exit` stop ⇒ Exit and
overrides everything; otherwise a fired `review_and_reduce` / `breaking` / `violated` ⇒ Reduce;
an approaching stop / `contested` / `weakening` ⇒ Watch; else Hold. Python computes facts + a
suggested action; Claude judges (especially the null-eval_ticker stops, via WebSearch).

CLI:
    uv run python -m catalyx.scorer.exit_watcher [--all] [--json] [--no-persist]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from catalyx.config import weights
from catalyx.execution import tax_engine
from catalyx.scorer.dislocation import yfinance_prices
from catalyx.store import lake, movement_repo

_BUY_ACTIONS = ("open", "add")


# ── Pure stop evaluation (unit-tested, no network) ────────────────────────────

def _breaches(value: float, comparator: str, threshold: float) -> bool:
    """Is `value` on the breach side of `threshold`? below ⇒ value < threshold; above ⇒ value >."""
    if comparator == "below":
        return value < threshold
    if comparator == "above":
        return value > threshold
    return False


def trailing_breach_count(closes: list[float], comparator: str, threshold: float) -> int:
    """How many of the MOST RECENT consecutive closes are on the breach side (counting back from the
    last close, stopping at the first non-breaching one). This is the persistence measure a stop
    written as 'for N consecutive trading days' needs — and it is a stateless read of the series, so
    the verdict is identical whether the watcher runs daily, weekly, or monthly."""
    cnt = 0
    for x in reversed(closes):
        if _breaches(x, comparator, threshold):
            cnt += 1
        else:
            break
    return cnt


def evaluate_stop(closes: list[float], comparator: str | None, threshold: float | None,
                  consecutive_days: int | None, approach_pct: float) -> dict:
    """Classify a price stop against a close series. Returns a JSON-able dict with:
      status ∈ {fired, approaching, clear, unknown}
        fired       — the breach has held for the full consecutive_days window.
        approaching — currently breaching but not yet for the full window, OR not breaching but
                      within `approach_pct` of the threshold (about to cross).
        clear       — comfortably on the safe side.
        unknown     — not machine-checkable (no series / missing fields).
      plus last_close, distance_pct (signed: last vs threshold), consecutive_breaching, required.
    """
    if not closes or comparator not in ("below", "above") or threshold in (None, 0):
        return {"status": "unknown", "last_close": (round(closes[-1], 4) if closes else None),
                "threshold": threshold, "comparator": comparator,
                "consecutive_breaching": None, "consecutive_days_required": consecutive_days}
    required = int(consecutive_days) if consecutive_days else 1
    cnt = trailing_breach_count(closes, comparator, threshold)
    last = closes[-1]
    distance_pct = round((last / threshold - 1.0) * 100.0, 2)
    margin_pct = abs(last - threshold) / abs(threshold) * 100.0
    if cnt >= required:
        status = "fired"
    elif cnt >= 1:
        status = "approaching"                    # breaching now, building toward the window
    elif margin_pct <= approach_pct:
        status = "approaching"                    # safe side but hugging the line
    else:
        status = "clear"
    return {"status": status, "last_close": round(last, 4), "threshold": threshold,
            "comparator": comparator, "distance_pct": distance_pct,
            "consecutive_breaching": cnt, "consecutive_days_required": required}


# ── Pure roll-ups (unit-tested) ───────────────────────────────────────────────

def roll_up_assumptions(assumptions: list[dict]) -> dict:
    """Count assumptions by current_status. `violated` ⇒ exit input; `weakening` ⇒ watch input."""
    counts = Counter((a.get("current_status") or "unverified") for a in assumptions or [])
    violated = [a.get("id") for a in (assumptions or []) if a.get("current_status") == "violated"]
    weakening = [a.get("id") for a in (assumptions or []) if a.get("current_status") == "weakening"]
    return {
        "total": len(assumptions or []),
        "holding": counts.get("holding", 0),
        "weakening": counts.get("weakening", 0),
        "violated": counts.get("violated", 0),
        "monitoring": counts.get("monitoring", 0),
        "unverified": counts.get("unverified", 0),
        "violated_ids": violated,
        "weakening_ids": weakening,
    }


def suggest_action(fired_full_exit: bool, fired_reduce: bool, regime_state: str | None,
                   has_violated: bool, has_weakening: bool, has_approaching: bool) -> str:
    """Severity arbitration (docs/DESIGN_sell_signals.md §5). The most pre-committed / most
    fundamental trigger binds; a fired full_exit stop overrides timing entirely."""
    if fired_full_exit:
        return "exit"
    if fired_reduce or regime_state == "breaking" or has_violated:
        return "reduce"
    if has_approaching or regime_state == "contested" or has_weakening:
        return "watch"
    return "hold"


# ── Lake read: regime per sector (latest run) ─────────────────────────────────

def _regime_map(run_id: str | None, lake_dir: Path | None) -> tuple[dict, str | None]:
    """{sector_id: regime_state} for the latest (or given) run. Missing ⇒ treated as intact."""
    try:
        df = lake.read_table("sector_snapshot", lake_dir=lake_dir)
    except Exception:
        return {}, None
    if df is None or df.empty:
        return {}, None
    if run_id is None and "run_id" in df.columns:
        run_id = max(df["run_id"].dropna().unique())
    if run_id is not None and "run_id" in df.columns:
        df = df[df["run_id"] == run_id]
    out = {}
    if "sector_id" in df.columns:
        for _, r in df.iterrows():
            rs = r.get("regime_state")
            out[r.get("sector_id")] = rs if rs is not None else "intact"
    return out, run_id


# ── Risk-discipline gather: a position's invalidations + assumptions ──────────

def _risk_for_etf(etf: str, movements: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Collect invalidation[] + assumptions[] from the OPEN/ADD movements of this vehicle that carry
    a risk_discipline block, plus the attribution catalyst list. (Usually one opening movement.)"""
    invalidations, assumptions, attribution = [], [], []
    for m in movements:
        if m.get("vehicle", {}).get("etf") != etf or m.get("action") not in _BUY_ACTIONS:
            continue
        rd = m.get("risk_discipline") or {}
        invalidations.extend(rd.get("invalidation") or [])
        assumptions.extend(rd.get("assumptions") or [])
        attribution.extend(m.get("attribution") or [])
    return invalidations, assumptions, attribution


# ── Engine ────────────────────────────────────────────────────────────────────

def assess(cfg: dict | None = None, price_fn=None, today: date | None = None,
           persist: bool = False, run_id: str | None = None,
           movements_dir: Path | None = None, lake_dir: Path | None = None) -> dict:
    """Evaluate Family-1 exit signals for every open position. ONE price fetch for all tickers.
    Recommend-only — writes nothing to the movement files."""
    cfg = cfg or weights.exit_signals()
    price_fn = price_fn or yfinance_prices
    today = today or date.today()
    approach_pct = float(cfg.get("approach_pct", 5.0))
    lookback = int(cfg.get("lookback_days", 60))

    book = movement_repo.positions(movements_dir=movements_dir)
    holdings = book.get("holdings", [])
    realized_ytd = float(book.get("realized_eur", 0.0))
    movements = movement_repo.load_all(movements_dir=movements_dir)
    regime, used_run = _regime_map(run_id, lake_dir)

    # Collect every ticker we need: each position's vehicle (to mark P&L) + every eval_ticker.
    tickers = set()
    per_pos_meta = []
    for h in holdings:
        etf = h["etf"]
        inv, asm, attr = _risk_for_etf(etf, movements)
        tickers.add(etf)
        for iv in inv:
            et = iv.get("eval_ticker")
            if et:
                tickers.add(et)
        per_pos_meta.append((h, inv, asm, attr))

    prices = None
    if tickers:
        start = (today - _td(lookback + 10)).isoformat()
        prices = price_fn(sorted(tickers), start, today.isoformat())

    results = []
    for h, inv, asm, attr in per_pos_meta:
        etf = h["etf"]
        sector_id = h.get("sector_id")
        regime_state = regime.get(sector_id, "intact")

        # ── price stops ──
        checked, claude_check = [], []
        fired_full_exit = fired_reduce = has_approaching = False
        for iv in inv:
            sev = iv.get("severity")
            et = iv.get("eval_ticker")
            row = {"id": iv.get("id"), "severity": sev, "source": iv.get("source"),
                   "condition": iv.get("condition"), "eval_ticker": et,
                   "eval_note": iv.get("eval_note")}
            if not et:
                # not machine-checkable → Claude checks with WebSearch
                claude_check.append(row)
                continue
            closes = _col(prices, et)
            ev = evaluate_stop(closes, iv.get("comparator"), iv.get("threshold"),
                               iv.get("consecutive_days"), approach_pct)
            row.update(ev)
            checked.append(row)
            if ev["status"] == "fired":
                if sev == "full_exit":
                    fired_full_exit = True
                else:
                    fired_reduce = True
            elif ev["status"] == "approaching":
                has_approaching = True

        # ── assumptions ──
        asm_roll = roll_up_assumptions(asm)

        # ── mark-to-market + after-tax exit consequence ──
        tax = _tax_view(h, prices, realized_ytd)

        action = suggest_action(fired_full_exit, fired_reduce, regime_state,
                                bool(asm_roll["violated"]), bool(asm_roll["weakening"]),
                                has_approaching)

        results.append({
            "sector_id": sector_id, "etf": etf,
            "invested_eur": h.get("invested_eur"), "weight_pct": h.get("weight_pct"),
            "regime_state": regime_state,
            "attribution": [a.get("catalyst_id") for a in attr],
            "stops_checked": checked,
            "stops_claude_check": claude_check,
            "assumptions": asm_roll,
            "tax": tax,
            "suggested_action": action,
        })

    # sort: loudest first (exit > reduce > watch > hold)
    order = {"exit": 0, "reduce": 1, "watch": 2, "hold": 3}
    results.sort(key=lambda r: order.get(r["suggested_action"], 9))

    if persist:
        run_id = run_id or used_run
        if run_id:
            _persist_lake(run_id, today, results, lake_dir)

    return {
        "as_of": today.isoformat(), "run_id": run_id if persist else used_run,
        "n_positions": len(results),
        "realized_ytd_eur": round(realized_ytd, 2),
        "positions": results,
        "note": "Family-1 exit watch (recommend-only). Python evaluates the structured price stops "
                "+ assumption roll-up + regime cross + after-tax exit P&L; a fired full_exit stop "
                "overrides timing (§5). Null-eval_ticker stops are Claude-checked with WebSearch. "
                "Writes nothing — mark `triggered` and close via /catalyx-close yourself.",
    }


def _tax_view(holding: dict, prices, realized_ytd: float) -> dict:
    """Mark the position and surface the after-tax consequence of exiting now (Spanish CGT). A loss
    is a harvestable offset (no tax). Non-EUR vehicles are flagged (FX needed) and left unmarked."""
    qty = float(holding.get("qty") or 0.0)
    invested = float(holding.get("invested_eur") or 0.0)
    px = _last(prices, holding["etf"])
    out = {"current_price": px, "market_value_eur": None, "unrealized_eur": None,
           "unrealized_pct": None, "tax_due_eur": None, "net_proceeds_eur": None,
           "harvestable_loss_eur": None, "note": None}
    if px is None or qty == 0:
        out["note"] = "no current price — cannot mark"
        return out
    mv = round(px * qty, 2)
    unreal = round(mv - invested, 2)
    out["market_value_eur"] = mv
    out["unrealized_eur"] = unreal
    out["unrealized_pct"] = round((mv / invested - 1.0) * 100.0, 2) if invested else None
    if unreal > 0:
        t = tax_engine.compute_tax(gross_gain=unreal, ytd_prior=max(0.0, realized_ytd))
        out["tax_due_eur"] = t.tax_due
        out["net_proceeds_eur"] = round(mv - t.tax_due, 2)
        out["effective_rate_pct"] = round(t.effective_rate * 100.0, 2)
    else:
        out["tax_due_eur"] = 0.0
        out["net_proceeds_eur"] = mv
        out["harvestable_loss_eur"] = round(abs(unreal), 2)
        out["note"] = "at a loss — exiting realizes a harvestable CGT offset (Spanish 2-month " \
                      "recompra rule applies if you repurchase the same vehicle)."
    return out


def _persist_lake(run_id: str, today: date, results: list[dict], lake_dir) -> None:
    """One flat row per position → lake table `exit_signal` (overwrite per run) for the dashboard.
    The stop lists (variable length) are flattened to counts + the loudest fired stop id."""
    import pandas as pd

    computed_at = datetime.now(timezone.utc)
    recs = []
    for r in results:
        checked = r["stops_checked"]
        fired = [s for s in checked if s.get("status") == "fired"]
        approaching = [s for s in checked if s.get("status") == "approaching"]
        loudest = next((s for s in fired if s["severity"] == "full_exit"), fired[0] if fired else None)
        tax = r["tax"]
        recs.append({
            "run_id": run_id, "computed_at": computed_at, "as_of": today.isoformat(),
            "sector_id": r["sector_id"], "etf": r["etf"],
            "invested_eur": r["invested_eur"], "weight_pct": r["weight_pct"],
            "regime_state": r["regime_state"], "suggested_action": r["suggested_action"],
            "n_stops": len(checked), "n_fired": len(fired), "n_approaching": len(approaching),
            "n_claude_check": len(r["stops_claude_check"]),
            # comma-joined ids so the dashboard can name the stops without the full per-stop list
            "fired_ids": ",".join(s["id"] for s in fired),
            "approaching_ids": ",".join(s["id"] for s in approaching),
            "claude_check_ids": ",".join(s["id"] for s in r["stops_claude_check"]),
            "loudest_fired_id": loudest["id"] if loudest else None,
            "loudest_fired_severity": loudest["severity"] if loudest else None,
            "assumptions_total": r["assumptions"]["total"],
            "assumptions_violated": r["assumptions"]["violated"],
            "assumptions_weakening": r["assumptions"]["weakening"],
            "unrealized_eur": tax["unrealized_eur"], "unrealized_pct": tax["unrealized_pct"],
            "tax_due_eur": tax["tax_due_eur"], "net_proceeds_eur": tax["net_proceeds_eur"],
            "harvestable_loss_eur": tax["harvestable_loss_eur"],
        })
    if recs:
        lake.append_partition("exit_signal", pd.DataFrame(recs), {"run_id": run_id},
                              overwrite=True, lake_dir=lake_dir)


# ── small helpers over the price frame (network-free) ─────────────────────────

def _td(days: int):
    from datetime import timedelta
    return timedelta(days=days)


def _col(prices, ticker) -> list[float] | None:
    if prices is None or ticker not in getattr(prices, "columns", []):
        return None
    s = prices[ticker].dropna()
    return [float(x) for x in s.tolist()] if len(s) else None


def _last(prices, ticker) -> float | None:
    col = _col(prices, ticker)
    return round(col[-1], 4) if col else None


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="CATALYX exit watcher — Family 1 (recommend-only)")
    p.add_argument("--all", action="store_true", help="(default) assess every open position")
    p.add_argument("--no-persist", action="store_true", help="do not write the exit_signal lake table")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    r = assess(persist=not args.no_persist)
    if args.json:
        print(json.dumps(r, indent=2, ensure_ascii=False, default=str))
        return

    print(f"CATALYX — Exit watch   as_of={r['as_of']}   run={r['run_id']}   "
          f"realized YTD=€{r['realized_ytd_eur']:,.2f}\n")
    if not r["positions"]:
        print("  No open positions.")
        return
    icon = {"exit": "🔴 EXIT", "reduce": "🟠 REDUCE", "watch": "🟡 WATCH", "hold": "🟢 HOLD"}
    for s in r["positions"]:
        tax = s["tax"]
        pnl = f"{tax['unrealized_pct']:+.1f}%" if tax["unrealized_pct"] is not None else "—"
        print(f"  {icon.get(s['suggested_action'], s['suggested_action']):<11} "
              f"{s['sector_id']:<34}{s['etf']:<10} €{s['invested_eur']:>7,.0f}  "
              f"P&L {pnl:<8} regime={s['regime_state']}")
        for st in s["stops_checked"]:
            mark = {"fired": "✅ FIRED", "approaching": "⚠ approaching",
                    "clear": "· clear", "unknown": "? unknown"}.get(st["status"], st["status"])
            extra = (f"  ({st.get('consecutive_breaching')}/{st.get('consecutive_days_required')}d "
                     f"{st['comparator']} {st['threshold']}, last {st.get('last_close')})") \
                if st.get("status") in ("fired", "approaching") else ""
            print(f"        {mark:<14} {st['id']} [{st['severity']}] via {st['eval_ticker']}{extra}")
        for st in s["stops_claude_check"]:
            print(f"        🔍 Claude-check {st['id']} [{st['severity']}] ({st['source']}): {st['condition'][:70]}")
        a = s["assumptions"]
        if a["total"]:
            print(f"        assumptions: {a['holding']}✓ {a['weakening']}~weak {a['violated']}✗viol "
                  f"({a['monitoring']}mon/{a['unverified']}unv)")
        if tax["tax_due_eur"] is not None and tax["unrealized_eur"] is not None:
            if tax["unrealized_eur"] > 0:
                print(f"        exit now: net €{tax['net_proceeds_eur']:,.0f} after €{tax['tax_due_eur']:,.0f} CGT "
                      f"({tax.get('effective_rate_pct', 0)}%)")
            else:
                print(f"        exit now: loss €{tax['harvestable_loss_eur']:,.0f} (harvestable, no CGT)")
    print(f"\n  {r['note']}")


if __name__ == "__main__":
    main()
