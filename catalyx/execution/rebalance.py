"""CATALYX — rebalance engine: the pipeline's target book vs the real one, in €, after tax.

WHY THIS MODULE EXISTS (docs/PLAN_v3_lean_pipeline_rebalance.md §3.1 + §4).

The review used to end in prose: "grid is holding, watch it", "consider a small add to semis".
Two things were wrong with that. First, no number ever said HOW MUCH — the pipeline computed a
model book to two decimals and then the recommendation was an adjective. Second, an LLM asked
for a verdict drifts to whichever option cannot be blamed, which in a portfolio means holding
cash and holding losers. That is not caution, it is a systematic bias, and the fix is not a
better prompt: it is to move the verdict into a rule table evaluated in Python.

So this module answers, per sector, four questions with numbers:
  1. What does the pipeline say we should hold?    → target_eur   (model weights × deployable)
  2. What do we actually hold?                     → actual_eur   (marked to market, in EUR)
  3. What does the RULE say to do about the gap?   → rule_action  (SELL/REDUCE/TRIM/ADD/BUY/HOLD)
  4. Does that trade pay after tax and spread?     → net_edge_eur

The action enum has exactly five members plus HOLD. There is no `watch`, no `monitor`, no
`consider`, no `optional` — a verdict that does not move money is HOLD, said once, with its
reason. Deviating is allowed but must be recorded as an override (lake `override_log`) so it can
be scored against the rule it replaced.

ASYMMETRY OF THE AFTER-TAX GATE (deliberate, see `net_edge_gate` in scoring_weights.yaml).
A sale that realizes a gain pays Spanish CGT now and irreversibly, so it must clear the gate:
that is literally the user's "¿renta vender?". A purchase out of idle cash pays only the spread.
Gating purchases on an expected edge derived from a rank IC that is still statistically noise
would reimport, through the back door, exactly the conservatism this module exists to remove —
cash drag is a certain cost, the edge estimate is not. So the gate binds sales, not buys.

WHAT THIS MODULE DOES NOT DO: it never trades and never writes a Movement. It emits a table and
persists it (lake `rebalance`) so that later we can ask whether following it would have paid.
Execution stays with the human via /catalyx-open and /catalyx-close.

CLI:
    uv run python -m catalyx.execution.rebalance                # markdown table
    uv run python -m catalyx.execution.rebalance --json
    uv run python -m catalyx.execution.rebalance --strategy momentum --no-persist
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from catalyx.config import weights

# Fixed precedence. A row is decided once, by the FIRST rule that fires in this order — so a
# breaking regime is never quietly outranked by "but it is underweight".
# RE-SCORE sits with the sell-side actions on purpose: it is what a rank-out SELL degrades to
# when the rank behind it was never measured, and it moves no money. Putting it beside HOLD would
# bury a work item at the bottom of the table under the rows that do move money.
PRECEDENCE = ("SELL", "REDUCE", "TRIM", "RE-SCORE", "ADD", "BUY", "HOLD")
_TABLE = "rebalance"
_OVERRIDE_TABLE = "override_log"
_SWAP_TABLE = "rebalance_swap"
_BEST_TABLE = "rebalance_best_moves"

# Words the output may never contain in an action column. Kept as data so a test can assert it:
# the prose ban of §4.4 is only real if something checks it.
BANNED_ACTION_WORDS = ("watch", "monitor", "consider", "optional", "maybe", "evaluate")


# ── Pure rules (no I/O — these are what the tests pin) ────────────────────────

def deploy_ratio(n_intact_top: int, vix: float | None, cfg: dict) -> dict:
    """How much of the committed capital the rules say should be AT WORK right now.

    `clamp(base + step·(n_intact_top − intact_min) − vix_penalty·ramp(VIX), floor, ceiling)`.

    This replaces "cash by feel". The book sitting 70% in cash was never a decision anybody
    made — it was the residue of never deciding. Here it is a number with its inputs printed,
    and VIX is the ONLY macro brake: there is no discretionary "the market feels risky" term.

    v6 I6: the brake was a step at VIX 30 — 29.9 → 30.1 moved a fifth of the target capital,
    so a VIX oscillating around 30 oscillated the whole book. It now ramps linearly from
    `vix_ramp_start` to `vix_ramp_full`, which is the same brake without the discontinuity.
    """
    d = cfg.get("deployment", {}) or {}
    base = float(d.get("base", 0.60))
    step = float(d.get("step_per_intact_sector", 0.05))
    intact_min = int(d.get("intact_min", 5))
    penalty = float(d.get("vix_penalty", 0.20))
    floor = float(d.get("floor", 0.30))
    ceiling = float(d.get("ceiling", 1.00))
    # pre-v6 configs carry only the cliff; centre the ramp on it so they keep their stance
    pause = float(d.get("vix_pause_above", 30.0))
    ramp_start = float(d.get("vix_ramp_start", pause - 5.0))
    ramp_full = float(d.get("vix_ramp_full", pause + 5.0))

    span = max(1e-9, ramp_full - ramp_start)
    ramp = 0.0 if vix is None else max(0.0, min(1.0, (float(vix) - ramp_start) / span))
    brake = penalty * ramp
    raw = base + step * (int(n_intact_top) - intact_min) - brake
    ratio = max(floor, min(ceiling, raw))
    return {
        "ratio": round(ratio, 4), "raw": round(raw, 4),
        "n_intact_top": int(n_intact_top), "intact_min": intact_min,
        "vix": vix, "vix_ramp_start": ramp_start, "vix_ramp_full": ramp_full,
        "vix_ramp": round(ramp, 3), "vix_brake_pp": round(brake, 4),
        "vix_brake": bool(brake > 0),
        "floor": floor, "ceiling": ceiling,
        "why": (f"base {base:.2f} + {step:.2f}×({n_intact_top}−{intact_min})"
                + (f" − {brake:.2f} (VIX {vix:.1f}, ramp {ramp_start:.0f}→{ramp_full:.0f})"
                   if brake > 0 else "")
                + f" → {ratio:.0%}"),
    }


def _in(value, allowed) -> bool:
    return value is not None and value in (allowed or [])


def profit_ladder_step(unrealized_pct: float | None, rank: int | None,
                       ladder: list[dict]) -> dict | None:
    """The first ladder rung a winner has cleared, or None.

    Rungs are evaluated from the most demanding down, so a +60% position at rank 9 takes the
    50% rung rather than stopping at the 25% one. `rank_min: 6` means "only once the model has
    stopped calling it a leader" — a rule, not a mood, which is the whole point of the ladder:
    taking a partial should not depend on how the day felt.
    """
    if unrealized_pct is None:
        return None
    for rung in sorted(ladder or [], key=lambda r: -float(r.get("gain_pct_min", 0))):
        gain_min = float(rung.get("gain_pct_min", 0))
        rank_min = int(rung.get("rank_min", 0) or 0)
        if unrealized_pct >= gain_min and (rank_min == 0 or (rank or 999) >= rank_min):
            return rung
    return None


def decide_action(row: dict, cfg: dict) -> dict:
    """The decision table. Returns `{action, reason}` — one of PRECEDENCE, nothing else.

    `row` carries only facts already computed elsewhere: held, gap_pp, rank, regime_state,
    narrative_maturity, exit_action, catalyst_status, unrealized_pct, reverify_required,
    rank_out_streak. No judgment enters here, which is exactly why it lives in Python.
    """
    held = bool(row.get("held"))
    gap_pp = float(row.get("gap_pp") or 0.0)
    rank = row.get("rank")
    regime = row.get("regime_state")
    deadband = float(cfg.get("deadband_pp", 2.0))

    sell = cfg.get("sell_if_any", {}) or {}
    reduce_cfg = cfg.get("reduce_if_any", {}) or {}
    add = cfg.get("add_if", {}) or {}
    buy = cfg.get("buy_if", {}) or {}
    trim = cfg.get("trim_if", {}) or {}

    # ── SELL — the fundamental triggers. Only apply to something we actually hold.
    if held:
        if _in(row.get("exit_action"), sell.get("exit_watcher")):
            return {"action": "SELL", "reason": "exit watcher fired a full exit"}
        if _in(regime, sell.get("regime")):
            return {"action": "SELL", "reason": f"regime {regime}"}
        if _in(row.get("catalyst_status"), sell.get("catalyst_status")):
            return {"action": "SELL",
                    "reason": f"driving catalyst {row.get('catalyst_status')}"}
        # RE-SCORE — "we do not know" is a real state (plan v4 §3 B3). It comes AFTER the
        # fundamental sells (a broken thesis is a sell whatever its rank did) and BEFORE the
        # rank-streak sell, because the rank streak is exactly the verdict that missing data
        # cannot support.
        rescore = cfg.get("rescore_if", {}) or {}
        missing = int(row.get("rank_missing_runs") or 0)
        if missing >= int(rescore.get("missing_runs_min", 2) or 2):
            return {"action": "RE-SCORE",
                    "reason": f"absent from {missing} of the last "
                              f"{row.get('rank_runs') or rescore.get('lookback_runs', 4)} scored "
                              f"runs — no rank to sell on. Re-score before deciding."}

        streak = int(row.get("rank_out_streak") or 0)
        need = int(sell.get("rank_out_consecutive", 2) or 2)
        # v15.4 - a DIVERSIFIER is held to lower the book's variance, not for its rank; the
        # rank-out streak measures something it was never bought for. Everything ABOVE this line
        # (exit watcher, regime, dead driver, re-score) and below it (drawdown, the ceiling TRIM,
        # the profit ladder) still applies - the role removes ONE exit, the one on the wrong axis.
        if streak >= need and not row.get("diversifier"):
            # Name the span, not just the count: "3 consecutive cycles" covered anything from ten
            # days to two months before the cycle floor, and the reader could not tell which.
            span = row.get("rank_streak_days")
            return {"action": "SELL",
                    "reason": f"ranked below top-{sell.get('rank_out_of_top')}"
                              + (f" (#{row['score_rank']})" if row.get("score_rank") else "")
                              + f" for {streak} consecutive review cycles"
                              + (f" ({span}d)" if span else "")}

        # ── REDUCE — capital preservation, incl. the 2026-08-04 re-verify doctrine.
        if _in(row.get("exit_action"), reduce_cfg.get("exit_watcher")):
            return {"action": "REDUCE", "reason": "exit watcher: drawdown floor breached"}
        # The 2026-08-04 doctrine, exactly: a stale verdict UNDER A REAL DRAWDOWN forces the
        # protective reduce. A stale verdict on a position that is up is a re-verify — work for
        # the review, not a sale. Halving a +13% winner because its YAML is 60 days old would be
        # the conservatism bias wearing a discipline costume.
        if row.get("reverify_required") and not row.get("reverify_resolved") \
                and row.get("drawdown_tier") in ("reduce", "exit"):
            return {"action": "REDUCE",
                    "reason": "stale catalyst verdict + drawdown, not re-verified this run"}

        # ── TRIM — overweight, or a rung of the profit ladder.
        over = -gap_pp                                    # positive = above target
        # CROWDING PRICES THE MARGINAL BUYER, NOT A POSITION ALREADY HELD (v11.1).
        #
        # `max_crowding` is an ENTRY filter, and the overweight TRIM below cannot tell an
        # exclusion from a verdict: a held name the bar kept out of the model book has a 0%
        # target, so it reads as maximally overweight and is sold — not because the ranking
        # demoted it, but because it never got to be ranked. Two things make that the wrong
        # sale. Crowding measures what the NEXT buyer pays into a theme; a holder already paid
        # the old price, and the crowd is what is paying them. And crowding RISES WITH SUCCESS —
        # a theme becomes crowded by working — so the filter systematically sells winners for
        # having won (pharma_large_cap: composite 28.5 → 80.5 while crowding went 55 → 94.4).
        #
        # Crowding is not thereby ignored: it keeps its 0.135 weight in the composite, where it
        # is a penalty the name must out-rank, and a crowded holding still exits by falling out
        # of the top-N, by a sell rule, or by the conviction gate. What it no longer does is
        # veto a position twice on the same metric — once weighted, then again absolutely.
        #
        # NARROW BY CONSTRUCTION: `crowding_excluded_only` is set only when lifting the bar
        # would ACTUALLY have selected the name (`portfolio.crowding_excluded`). A holding that
        # would have missed the cut anyway is overweight for real, and is trimmed for real.
        #
        # IT SUPPRESSES THE EXIT, NOT THE CEILING. Such a row's target is re-read upstream as
        # "no opinion, capped at `max_position_pct`" rather than 0% — see `build`. So `over` is
        # zero while the position sits inside the ceiling and becomes real above it, and the
        # ordinary TRIM below both fires and SIZES correctly: back to the cap, never to the 0%
        # the entry filter produced. Crowding does not force an exit; it does not exempt a
        # position from the concentration ceiling either.
        # ORDER MATTERS, AND A TEST CAUGHT IT: this HOLD sits AFTER both trims, not before.
        # Placed first it swallowed the profit ladder — which fires on GAIN, not on the target
        # gap — so a crowding-excluded winner could never give back a rung. The suppression is
        # for the exit the entry filter manufactured, and for nothing else.
        # A STALE MODEL BOOK IS NOT A ZERO TARGET (v13.1). Before the overweight TRIM, because
        # the whole defect is that a 0% target manufactured by a config change reads as maximal
        # overweight. RE-SCORE moves no money — the same degradation a rank-based SELL takes
        # when there is no rank worth selling on.
        if row.get("stale_model_cfg"):
            return {"action": "RE-SCORE",
                    "reason": f"no model target: {row['stale_model_cfg']} — re-run the score "
                              f"before acting on this line"}
        if over >= float(trim.get("overweight_pp_min", 4.0)):
            return {"action": "TRIM", "reason": f"{over:.1f}pp above target"}
        rung = profit_ladder_step(row.get("unrealized_pct"), rank, cfg.get("profit_ladder"))
        if rung:
            return {"action": "TRIM",
                    "reason": f"profit ladder: +{row.get('unrealized_pct'):.0f}% ≥ "
                              f"{rung['gain_pct_min']:.0f}%"}
        # v15.2 — left the model book, but the SELL rule above has not fired: the out-of-cut
        # streak is under `rank_out_consecutive`. After both trims for the same reason as the
        # crowding branch below: it suppresses the exit the model-book cut manufactured, never
        # the ceiling nor the profit ladder.
        if row.get("diversifier_hold"):
            sr = row.get("score_rank")
            return {"action": "HOLD",
                    "reason": "diversifier: held to lower book variance, not for its rank"
                              + (f" (universe rank {sr})" if sr else "")
                              + " - exits by a fundamental rule, or when the best-moves plan finds"
                                " a better diversifier"}
        if row.get("hold_zone"):
            sr = row.get("score_rank")
            streak = int(row.get("rank_out_streak") or 0)
            need = int(sell.get("rank_out_consecutive", 2) or 2)
            return {"action": "HOLD",
                    "reason": "hold zone: not in the model book"
                              + (f" (universe rank {sr})" if sr else "")
                              + f"; SELL-rule streak {streak}/{need} below top-"
                                f"{sell.get('rank_out_of_top')} — exits by that rule, not by the cut"}
        if row.get("crowding_excluded_only"):
            cr = row.get("crowding_risk")
            bar = row.get("max_crowding")
            level = (f" ({cr:.1f} > {bar:.0f})"
                     if cr is not None and bar is not None else "")
            urank = row.get("score_rank")
            where = f" — held at rank {urank}" if urank is not None else " — held"
            return {"action": "HOLD",
                    "reason": f"no model target: excluded by the ENTRY crowding filter{level}"
                              f"{where}, not sold for being crowded"}

    # ── ADD / BUY — the side an LLM under-uses, so the conditions are explicit.
    if gap_pp >= deadband:
        if held:
            ok_rank = rank is not None and rank <= int(add.get("rank_max", 5))
            if ok_rank and gap_pp >= float(add.get("gap_pp_min", 3.0)) \
                    and not _in(regime, add.get("regime_not")):
                return {"action": "ADD",
                        "reason": f"rank {rank} opens the gate, {gap_pp:.1f}pp under the "
                                  f"risk-budget target"}
        else:
            ok_rank = rank is not None and rank <= int(buy.get("rank_max", 8))
            if ok_rank and gap_pp >= float(buy.get("gap_pp_min", 4.0)) \
                    and not _in(regime, buy.get("regime_not")) \
                    and not _in(row.get("narrative_maturity"), buy.get("maturity_not")):
                return {"action": "BUY",
                        "reason": f"rank {rank} opens the gate, not held, {gap_pp:.1f}pp "
                                  f"under the risk-budget target"}

    if abs(gap_pp) < deadband:
        return {"action": "HOLD", "reason": f"within the {deadband:.0f}pp deadband"}
    return {"action": "HOLD", "reason": "gap open but no rule condition met"}


def size_trade(action: str, gap_eur: float, market_value_eur: float | None, cfg: dict,
               ladder_fraction: float | None = None) -> dict:
    """€ the rule says to move, after the minimum-ticket filter.

    Sign convention: positive = buy, negative = sell. A trade smaller than `min_ticket_eur`
    is not a small trade, it is spread — it degrades to HOLD rather than printing a €40 order.
    """
    min_ticket = float(cfg.get("min_ticket_eur", 150.0))
    mv = float(market_value_eur or 0.0)
    reduce_fraction = float((cfg.get("reduce_if_any", {}) or {}).get("reduce_fraction", 0.5))

    if action in ("ADD", "BUY"):
        amount = max(0.0, gap_eur)
    elif action == "TRIM":
        amount = -(mv * float(ladder_fraction)) if ladder_fraction else -max(0.0, -gap_eur)
    elif action == "REDUCE":
        amount = -mv * reduce_fraction
    elif action == "SELL":
        amount = -mv
    else:
        return {"trade_eur": 0.0, "downgraded": False}

    if abs(amount) < min_ticket:
        # A full SELL is never downgraded for size: exiting a broken thesis is not optional
        # because the line happens to be small.
        if action != "SELL":
            return {"trade_eur": 0.0, "downgraded": True,
                    "downgrade_reason": f"€{abs(amount):.0f} below the €{min_ticket:.0f} ticket"}
    return {"trade_eur": round(amount, 2), "downgraded": False}


def cost_drag(trade_eur: float, tax_eur: float, cfg: dict, spread_bps: float | None = None,
              ) -> dict:
    """Tax + spread + fees on the traded notional. All costs, no optimism."""
    bps = float(spread_bps if spread_bps is not None else cfg.get("spread_bps", 20.0))
    spread = abs(trade_eur) * bps / 10_000.0
    fee = float(cfg.get("fee_eur", 0.0))
    total = float(tax_eur or 0.0) + spread + fee
    return {"tax_eur": round(float(tax_eur or 0.0), 2), "spread_eur": round(spread, 2),
            "fee_eur": round(fee, 2), "cost_drag_eur": round(total, 2)}


def expected_edge(trade_eur: float, bucket_pct: float | None) -> float | None:
    """`trade_eur × E[r | rank bucket]`, signed by direction.

    Buying into a bucket earns that bucket's expected return; selling out of it avoids it. The
    bucket figure arrives ALREADY shrunk toward zero by the calibration sample size, so with
    ~1 independent window this is a small number by construction — which is correct, not timid.
    """
    if bucket_pct is None:
        return None
    return round(trade_eur * bucket_pct / 100.0, 2)


def gate_status(exp: dict | None, ic: dict | None, cfg: dict) -> dict:
    """May the after-tax gate BLOCK a sale this run? A joint condition, not a window count.

    THE FAILURE THIS PREVENTS (plan v4 §0.2 D5). `min_windows_to_gate` alone armed the gate on
    arithmetic that had never been checked for direction. The bucket table it would use is built
    from the same ranking whose composite IC is currently −0.05 against an se of 0.20 — so
    `E[r|top3] (-0.056) < E[r|rest] (+0.779)`, and the moment the third independent window landed
    the gate would have begun BLOCKING sales out of the bottom bucket and WAVING THROUGH sales out
    of the top. It would have inverted the profit-taking rule, silently, on a sample the
    calibration module's own output labels `noise`.

    Three separate things must all hold, and each failure is reported by name:
      1. enough independent windows — an unmeasured quantity must never become a veto (v3);
      2. |IC| above a floor — an IC indistinguishable from zero orders nothing;
      3. IC POSITIVE — a negative IC disables the gate, it never inverts it. "Our ranking is
         backwards" is a scoring problem to fix, not a licence to trade the ranking upside down.
    """
    g = cfg.get("net_edge_gate", {}) or {}
    req = g.get("requires", {}) or {}
    min_w = int(req.get("min_independent_windows", g.get("min_windows_to_gate", 3)))
    min_ic = float(req.get("min_abs_ic", 0.20))
    need_positive = bool(req.get("ic_sign_must_be_positive", True))

    windows = int((exp or {}).get("effective_windows") or 0)
    ic_val = (ic or {}).get("ic")
    fails = []
    if windows < min_w:
        fails.append(f"~{windows} independent window(s) < {min_w}")
    if ic_val is None:
        fails.append("composite IC not measured")
    else:
        if abs(float(ic_val)) < min_ic:
            fails.append(f"|IC| {abs(float(ic_val)):.3f} < {min_ic:.2f} — the ranking orders nothing")
        if need_positive and float(ic_val) < 0:
            fails.append(f"IC {float(ic_val):+.3f} is NEGATIVE — arming would invert the rule, "
                         f"not enforce it")
    return {
        "armed": not fails, "windows": windows, "ic": ic_val,
        "ic_se": (ic or {}).get("se"), "ic_verdict": (ic or {}).get("verdict"),
        "requires": {"min_independent_windows": min_w, "min_abs_ic": min_ic,
                     "ic_sign_must_be_positive": need_positive},
        "why": ("armed: the ranking has measured, positively-signed edge" if not fails
                else "STANDS ASIDE — " + "; ".join(fails)),
    }


def apply_gate(action: str, trade_eur: float, tax_eur: float, net_edge: float | None,
               cfg: dict, evaluable: bool = True, why: str | None = None) -> dict:
    """Does the trade survive the after-tax test? Only taxable SALES are gated (see module doc).

    A loss-making sale realizes no tax (it banks a harvestable offset), so it faces only the
    spread and is not gated either — the gate exists to stop paying CGT for a benefit the data
    cannot yet demonstrate, not to stop cutting losers.

    `evaluable=False` means the expected edge is not measurable yet (no calibration window, or
    the sector has no model rank). An UNMEASURED quantity must never resolve to inaction: with
    zero windows every expected edge is 0 by construction, so a gate that binds on 0 would
    silently forbid ever taking a profit. When the gate cannot be evaluated it steps aside and
    says so, and the rule action stands.
    Same reasoning applies to a thin sample: with ~1 window the edge term is ~0, so `net` is
    just `−(tax + spread)` and EVERY taxable sale fails — an unmeasured quantity becomes a veto
    on ever taking a profit. The caller passes `evaluable=False` until calibration has
    `min_windows_to_gate` independent windows.
    """
    g = cfg.get("net_edge_gate", {}) or {}
    is_sale = trade_eur < 0
    taxable = float(tax_eur or 0.0) > 0.0
    gated = bool(g.get("applies_to_taxable_sales", True)) and is_sale and taxable
    if gated and not evaluable:
        return {"gated": False, "passes": True, "final_action": action,
                "gate_note": (why or "after-tax gate not evaluable") +
                             " — the rule action stands, cost shown for the record"}
    if not gated and (trade_eur > 0 and not g.get("applies_to_purchases", False)):
        return {"gated": False, "passes": True, "final_action": action,
                "gate_note": "purchase — gated on ticket size and the deployment floor, "
                             "not on an edge estimate that is still noise"}
    if not gated:
        return {"gated": False, "passes": True, "final_action": action,
                "gate_note": "no realized gain — only the spread is at stake"}
    passes = net_edge is not None and net_edge > 0
    return {
        "gated": True, "passes": passes,
        "final_action": action if passes else "HOLD",
        "gate_note": ("clears the after-tax gate" if passes else
                      f"does NOT pay after tax (net edge €{net_edge if net_edge is not None else 0:.0f})"),
    }


def leg_friction(trade_eur: float, tax_eur: float, cfg: dict,
                 spread_bps: float | None = None) -> dict:
    """Everything one leg of a trade costs, in €. Observable today — nothing is estimated."""
    bps = float(spread_bps if spread_bps is not None else cfg.get("spread_bps", 20.0))
    spread = abs(float(trade_eur or 0.0)) * bps / 10_000.0
    fee = float(cfg.get("fee_eur", 0.0))
    return {"tax_eur": round(float(tax_eur or 0.0), 2), "spread_eur": round(spread, 2),
            "fee_eur": round(fee, 2),
            "friction_eur": round(float(tax_eur or 0.0) + spread + fee, 2)}


def breakeven_pct(friction_eur: float, moved_eur: float) -> float | None:
    """The % the redeployed capital must OUTPERFORM by, just to cover the friction of moving it.

    THE POINT (plan v4 §2 A3). `net_edge_eur` answered "does this pay?" by multiplying the trade
    by a rank-bucket mean return one noisy window old — ±€1 on a €900 trade, printed beside a real
    €12 tax bill. That number is not wrong, it is UNFALSIFIABLE, and per D5 it turns actively
    harmful the moment the gate arms on a bucket table whose top3 currently sits BELOW its rest.

    A breakeven needs no forecast: tax bracket, spread and notional are all observable now. It
    converts the question from one the model cannot answer ("what will this earn?") into one the
    user can ("is my rank signal worth more than 1.4% over 63 days?") — and it is checkable
    against the realized spread a horizon later, which an expected-return point estimate is not.
    """
    moved = abs(float(moved_eur or 0.0))
    if moved < 1e-9:
        return None
    return round(abs(float(friction_eur or 0.0)) / moved * 100.0, 3)


def rank_edge_evidence(exp: dict | None, cfg: dict) -> dict:
    """What the lake actually measured about the spread a swap is betting on.

    Reported beside every breakeven so the hurdle is never read in a vacuum: a 1.4% hurdle is
    cheap against a signal worth 5pp and unpayable against one whose sign is not yet established.
    States the measured spread, its sample, and — when the sample is too thin or the sign is
    inverted — says NONE rather than dressing the number up.
    """
    exp = exp or {}
    raw = exp.get("raw") or {}
    top3, rest = raw.get("top3"), raw.get("rest")
    n = int(exp.get("effective_windows") or 0)
    min_w = int((cfg.get("net_edge_gate", {}) or {}).get("min_windows_to_gate", 3))
    horizon = exp.get("horizon_days")
    spread = None if (top3 is None or rest is None) else round(float(top3) - float(rest), 3)

    if spread is None:
        verdict, why = "NONE", "no calibration window has closed yet"
    elif n < min_w:
        verdict, why = "NONE", (f"~{n} independent window(s) < the {min_w} the gate requires — "
                                f"the sign is not established")
    elif spread <= 0:
        verdict, why = "ADVERSE", ("measured top3 sits BELOW rest — the rank signal has not paid "
                                   "over the windows measured so far")
    else:
        verdict, why = "MEASURED", f"over ~{n} independent {horizon}d window(s)"
    return {"spread_pp": spread, "effective_windows": n, "horizon_days": horizon,
            "verdict": verdict, "why": why,
            "line": (f"rank-bucket top3−rest = "
                     f"{'n/a' if spread is None else f'{spread:+.2f}pp'} → {verdict} ({why})")}


def swap_hurdle(gate: dict | None, disp: dict | None) -> dict:
    """How large a composite gap must be before acting on it pays its own friction (v13.3).

    THE HOLE THIS FILLS. Every row of this table already prints two numbers that cannot be
    compared: `breakeven_pct`, a real percentage of real money, and a rank, which is a position
    in a dimensionless ordering. Nothing converted one into the other, so "rank 8 → sell" was a
    verdict with no denominator — and the 2026-09-01 table sold a line at a 2.57% breakeven to
    buy one 0.55 composite-z above it, a gap the measured skill cannot pay for at any plausible
    IC. Sizing has stood aside for want of edge since λ shipped; SELECTION never did.

    The arithmetic is the same one λ rests on. A ranking's expected return from a gap is

        E[Δr] ≈ IC · Δz · σ_cs

    — `σ_cs` (`calibration.dispersion`) is how far apart outcomes actually spread, `IC` is how
    much of that ordering the score captures, `Δz` is the gap in `composite_z`. Setting that
    equal to the round-trip friction and solving gives the hurdle:

        Δz_required = breakeven_pct / (IC · σ_cs)

    THIS IS NOT A THRESHOLD, which is the whole point. It breathes with the friction of the
    actual row (a buy from cash pays ~0.20%, a rotation ~2.6% — so the same formula makes
    deploying cash ~13× easier than churning, an asymmetry nobody has to impose), with the
    measured dispersion of the regime, and with measured skill. And it has the property the
    project already demands everywhere else: **IC → 0 sends the hurdle to infinity**, so with no
    established edge NO rank-based rotation is justified. That is the same medicine `skill_lambda`
    gives sizing, finally applied to turnover.

    A COLUMN, NOT A GATE — deliberately, and for one cycle at least. Promoting it to a veto is a
    policy change on evidence that does not exist yet (the rule scorecard has no complete window),
    and the project's own v7 discipline is that a new signal ships at weight 0 until three
    independent windows have spoken. So this cancels nothing; it prints what the row would have to
    be worth. Fundamental exits — the exit watcher, a broken regime, a dead driver, a drawdown,
    a rank-out STREAK — are about the position, not about a marginal gap, and would stay outside
    the hurdle even if it were ever armed.
    """
    g, d = gate or {}, disp or {}
    ic, sigma = g.get("ic"), d.get("sigma_cs_pct")
    armed = bool(g.get("armed"))
    ic_eff = float(ic) if (armed and ic is not None and ic == ic and float(ic) > 0) else 0.0

    if sigma is None or float(sigma) <= 0:
        verdict = "UNMEASURED"
        why = d.get("note") or "cross-sectional dispersion not measured"
    elif ic_eff <= 0:
        verdict = "UNPAYABLE"
        why = (f"IC stands aside ({g.get('why')}) → the hurdle is unbounded: no composite gap "
               f"justifies paying friction to act on this ranking")
    else:
        verdict = "MEASURED"
        why = (f"IC {ic_eff:+.3f} × σ_cs {float(sigma):.2f}% — a row's gap must clear "
               f"breakeven ÷ {ic_eff * float(sigma):.3f} in composite_z")

    out = {"verdict": verdict, "ic": ic, "ic_effective": ic_eff, "ic_armed": armed,
           "sigma_cs_pct": sigma, "n_windows": d.get("n_windows"),
           "horizon_days": d.get("horizon_days"), "why": why}
    out["line"] = (f"swap hurdle → {verdict} ({why})")
    return out


def dz_required(breakeven: float | None, hurdle: dict | None) -> float | None:
    """The composite_z gap this row's friction demands. `None` = unbounded or unmeasured.

    `None` is load-bearing and must never be rendered as 0: "no gap is large enough" and "any gap
    will do" are opposite claims, and only a dash can carry the first honestly.
    """
    h = hurdle or {}
    ic, sigma = float(h.get("ic_effective") or 0.0), h.get("sigma_cs_pct")
    if breakeven is None or breakeven != breakeven or sigma is None:
        return None
    denom = ic * float(sigma)
    if denom <= 0:
        return None
    return round(abs(float(breakeven)) / denom, 3)


def selection_prior(evidence: dict | None, tilt_lambda: float | None) -> dict | None:
    """What the table's own deployment pressure rests on, when the ranking's evidence is not it.

    WHY (plan v5 §3 F1). The 2026-08-31 review printed, on one page, that its ranking orders
    nothing (IC −0.050, top3−rest −5.84pp) AND that eight trades toward that ranking must be
    executed, with §4c calling the shortfall a breach. Both can be right — the prior that being
    invested pays is not the prior that YOUR ordering pays — but the document never separated
    them, so a fair reader concludes the system contradicts itself.

    The asymmetry is deliberate policy and stays: SIZE is already neutralized by λ (B1), while
    SELECTION runs at full conviction. What was missing was saying so. This is a sentence, not a
    gate: gating the selection would be a new policy, and the policy is the user's to set.

    Returns None when the evidence is MEASURED — then the table rests on the measurement and the
    line would be noise.
    """
    ev = evidence or {}
    verdict = ev.get("verdict")
    if verdict not in ("NONE", "ADVERSE"):
        return None
    lam = 0.0 if tilt_lambda is None or tilt_lambda != tilt_lambda else float(tilt_lambda)
    return {
        "verdict": verdict,
        "tilt_lambda": lam,
        "note": (
            f"WHAT THIS TABLE RESTS ON — the ranking's measured edge is {verdict} "
            f"({ev.get('why')}). The deployment asked for below does not rest on it: the rule "
            f"picks NAMES on an edge not yet established, while the SIZE of each name is already "
            f"neutralized (λ={lam:.2f}, inverse-vol). Accepting the rows is accepting that prior "
            f"— that being invested in ranked leaders pays even before this ranking has shown it "
            f"orders. The registered alternative is an override naming the deployment shortfall."),
    }


def _swap_edge(from_sid: str | None, to_sid: str | None, be: float | None,
               z_by_sector: dict | None, hurdle: dict | None) -> dict:
    """Price one rotation against its own hurdle: gap bet, edge implied, does it pay?

    `pays: None` is a THIRD state and not a soft no — it means the hurdle is unmeasured or the
    gap is unknown, and collapsing it into False would read as "measured and rejected".
    """
    z = z_by_sector or {}
    zf = (z.get(from_sid) or {}).get("composite_z")
    zt = (z.get(to_sid) or {}).get("composite_z")
    gap = round(float(zt) - float(zf), 4) if zf is not None and zt is not None else None
    req = dz_required(be, hurdle)
    h = hurdle or {}
    ic, sigma = float(h.get("ic_effective") or 0.0), h.get("sigma_cs_pct")
    edge = round(ic * gap * float(sigma), 3) if (gap is not None and sigma and ic > 0) else None
    if h.get("verdict") == "UNMEASURED" or gap is None:
        pays = None
    elif req is None:                        # UNPAYABLE — the hurdle is unbounded
        pays = False
    else:
        pays = bool(gap >= req)
    return {"dz_gap": gap, "dz_required": req, "edge_expected_pct": edge,
            "hurdle_verdict": h.get("verdict"), "pays": pays}


def swap_ledger(rows: list[dict], cfg: dict, horizon_days: int | None = None,
                spread_bps: float | None = None, z_by_sector: dict | None = None,
                hurdle: dict | None = None) -> list[dict]:
    """Pair the € the table wants sold with the € it wants bought, and price each swap.

    Greedy, largest first: a sale funds the largest buy it can, then the next. This is not an
    execution plan — the user may fund a buy from cash instead — it is the LEDGER that makes the
    swap's hurdle explicit. Tax rides with the sell leg and is pro-rated across the legs it funds,
    because a sale's CGT is paid once however many buys it feeds.
    """
    sells = sorted(({"row": r, "left": abs(float(r.get("trade_eur") or 0.0))}
                    for r in rows if float(r.get("trade_eur") or 0.0) < 0),
                   key=lambda d: -d["left"])
    buys = sorted(({"row": r, "left": float(r.get("trade_eur") or 0.0)}
                   for r in rows if float(r.get("trade_eur") or 0.0) > 0),
                  key=lambda d: -d["left"])
    out: list[dict] = []
    for s in sells:
        sell_total = s["left"] or 1.0
        sell_tax = float(s["row"].get("tax_eur") or 0.0)
        for b in buys:
            if s["left"] < 1.0 or b["left"] < 1.0:
                continue
            moved = min(s["left"], b["left"])
            # A leg below the engine's own minimum ticket is spread, not a rotation — the same
            # threshold `size_trade` uses to refuse to print a €40 order. The unpaired remainder
            # is not lost: it lands in cash, which the CASH row already prices.
            if moved < float(cfg.get("min_ticket_eur", 150.0)):
                continue
            # CGT pro-rated to the slice of the sale this leg carries.
            tax = sell_tax * (moved / sell_total)
            f_sell = leg_friction(moved, tax, cfg, spread_bps)
            f_buy = leg_friction(moved, 0.0, cfg, spread_bps)
            friction = round(f_sell["friction_eur"] + f_buy["friction_eur"], 2)
            out.append({
                "from_sector": s["row"].get("sector_id"), "from_etf": s["row"].get("etf"),
                "to_sector": b["row"].get("sector_id"), "to_etf": b["row"].get("etf"),
                "from_action": s["row"].get("rule_action"), "to_action": b["row"].get("rule_action"),
                "moved_eur": round(moved, 2),
                "tax_eur": round(tax, 2),
                "spread_eur": round(f_sell["spread_eur"] + f_buy["spread_eur"], 2),
                "fee_eur": round(f_sell["fee_eur"] + f_buy["fee_eur"], 2),
                "friction_eur": friction,
                "breakeven_pct": breakeven_pct(friction, moved),
                "horizon_days": horizon_days,
                # v13.3 — WHAT THE SWAP IS BETTING ON, in the unit the hurdle is written in.
                # `dz_gap` is signed so positive means the buy leg outranks the sell leg, which
                # is the direction the rotation claims. The pairing already exists here and
                # nowhere else, so this is the only place the bet and its price meet.
                **_swap_edge(s["row"].get("sector_id"), b["row"].get("sector_id"),
                             breakeven_pct(friction, moved), z_by_sector, hurdle),
            })
            s["left"] -= moved
            b["left"] -= moved
    # A sale that funds no buy still moves capital to cash; a buy funded from cash pays only its
    # own spread. Both are already priced on their own row — the ledger only pairs what pairs.
    return out


def _wrap(text: str, width: int, indent: str, first: str) -> list[str]:
    """Wrap a paragraph for the fixed-width table output. `first` labels the first line."""
    import textwrap

    lines = textwrap.wrap(text, width=width) or [text]
    return [first + lines[0]] + [indent + ln for ln in lines[1:]]


def _render_rows(rows: list[dict]) -> list[str]:
    """One decision per line. Pure, so the column semantics are testable without a lake."""
    out = []
    for row in rows:
        # The hurdle, not a forecast: what this trade's friction costs as a % of the capital it
        # moves. `net€` is still in the JSON and the lake; it left the table on purpose.
        be = row.get("breakeven_pct")
        be_s = "—" if be is None else f"{be:.2f}"
        # ONE semantic, no fallback: the universe rank this run — the number every reason cites
        # and §1 of the report shows. The model-book rank stays internal (it exists only for book
        # members, so a column carrying both meant two things depending on the row).
        sr = _clean_rank(row.get("score_rank"))
        # The catalyst-score z that SIZED this row's target (v10 P8) — the CORE signal, printed
        # where it moves euros. "—" = the sector is outside the model book (nothing was sized),
        # or the book predates the tilt.
        caz = _fnum(row.get("ca_z"))
        caz_s = "—" if caz is None else f"{caz:+.1f}"
        # How old is the evidence this row is spending on. It sits beside the reason on purpose:
        # the justification and the age of what justifies it belong in one glance.
        age = row.get("data_age")
        # The composite gap this row's friction demands (v13.3). "∞" is NOT decoration: it is
        # the state where the measured skill cannot pay for acting on the ranking at all, and it
        # is a different claim from a missing measurement ("—").
        dz = row.get("dz_required")
        dz_s = ("—" if row.get("dz_hurdle_verdict") in (None, "UNMEASURED")
                or row.get("breakeven_pct") is None
                else "∞" if dz is None else f"{float(dz):.2f}")
        # A row the rule wants but no trade slot can carry this cycle. Marked on the ACTION, not
        # by zeroing the trade: the rule's ask stays visible and the constraint is what is new.
        # `*` = a scarcity queued this row for a LATER review. `**` = it exceeds a whole ramp
        # tranche, so "later" is never: the two states look identical on the table and are not.
        act = action_label(row) + ("**" if row.get("ramp_never_fits")
                                    else "*" if row.get("budget_state") == "deferred"
                                    or row.get("ramp_state") == "deferred" else "")
        out.append(f"{row['sector_id'][:30]:<30} {str(row.get('etf') or '—')[:9]:<9} "
                   f"{(str(sr) if sr is not None else '—'):>4} {caz_s:>5} "
                   f"{row['target_pct']:>6.1f} {row['actual_pct']:>6.1f} {row['gap_eur']:>8.0f} "
                   f"{act:<8} {row['trade_eur']:>8.0f} {be_s:>6} {dz_s:>7} "
                   f"{(age if age and age == age else '—'):<13} {row['reason']}")
    return out


def _clean_rank(v) -> int | None:
    """A rank is an int or it is missing. A pandas NaN slipping through an `is None` check once
    printed `still a leader (rank nan < 6)` — a claim about a rank nobody had."""
    if v is None or (isinstance(v, float) and v != v):
        return None
    return int(v)


def _fnum(v, ndigits: int = 2) -> float | None:
    """A number or None — same NaN discipline as `_clean_rank`, for the float columns a lake
    read hands back (older partitions lack the newer columns and pandas fills NaN)."""
    if v is None or (isinstance(v, float) and v != v):
        return None
    return round(float(v), ndigits)


def book_risk_delta(rows: list[dict], analyze_fn=None) -> dict | None:
    """What THIS TABLE does to book volatility and mean correlation, before vs after (v11.1).

    THE GAP THIS FILLS. Every cost on a row is a cost of the TRADE — CGT, spread, the breakeven
    the rotation must clear. None of them is a cost of the resulting BOOK. So a table could sell
    the one holding that diversifies and buy a fifth name inside the cluster already carrying
    most of the variance, and every number on the page would say the rotation was cheap. Measured
    on 2026-09-01: rotating out of `pharma_large_cap` into the model's next name took the book
    from ρ 0.188 / vol 16% to ρ 0.222 / vol 19% — a third of the book's volatility, invisible
    beside a €12.92 friction line.

    IT IS A COLUMN, NOT A GATE — the same doctrine as `data`, `ca_imputed` and the unmeasured
    cluster. It prices what the table does; it never cancels a row. `cluster_risk_pass` is the
    thing that acts on risk, and deliberately only on the BUY side.

    ONLY THE ROWS THAT WOULD ACTUALLY EXECUTE. A row the ramp or the trade budget queued is not
    part of today's book, and counting it would price a book nobody is about to hold.

    Returns None when the covariance cannot be computed — never a zero, never "unchanged".
    A book whose risk is not measurable must not read as a book whose risk does not move.
    """
    if analyze_fn is None:                                     # pragma: no cover - thin wiring
        from catalyx.scorer.covariance import analyze as analyze_fn

    def _book(after: bool) -> dict[str, float]:
        out: dict[str, float] = {}
        for r in rows:
            etf = r.get("etf")
            if not etf or r.get("sector_id") == "CASH":
                continue
            eur = float(r.get("actual_eur") or 0.0)
            if after and r.get("budget_state") != "deferred" \
                    and r.get("ramp_state") != "deferred":
                eur += float(r.get("trade_eur") or 0.0)
            if eur > 0:
                out[etf] = out.get(etf, 0.0) + eur
        return out

    before, after = _book(False), _book(True)
    if len(before) < 2 or len(after) < 2:
        return None

    def _measure(book: dict[str, float]) -> dict | None:
        gross = sum(book.values())
        if gross <= 0:
            return None
        tk = list(book)
        try:
            res = analyze_fn(tickers=tk, weights=[book[t] / gross for t in tk])
        except Exception:                                      # pragma: no cover - defensive
            return None
        vol, rho = res.get("portfolio_vol_annual_pct"), res.get("mean_pairwise_corr_weekly")
        if vol is None:
            return None
        return {"vol_pct": round(float(vol) * 100.0, 2),
                "mean_corr": None if rho is None else round(float(rho), 4),
                "n_vehicles": len(tk)}

    b, a = _measure(before), _measure(after)
    if not b or not a:
        return None
    d_vol = round(a["vol_pct"] - b["vol_pct"], 2)
    d_rho = (None if b["mean_corr"] is None or a["mean_corr"] is None
             else round(a["mean_corr"] - b["mean_corr"], 4))
    return {"before": b, "after": a, "delta_vol_pp": d_vol, "delta_mean_corr": d_rho,
            "more_correlated": bool(d_rho is not None and d_rho > 0)}


def partial_rungs(rows: list[dict], cfg: dict) -> list[dict]:
    """Distance to EACH partial-sale rung, per held position.

    WHY (plan v4 §2 A5). The ladder is sound policy but the review never showed the DISTANCE to
    it, so a partial arrived as a surprise and could not be planned around.

    Both rungs are reported, never a "nearest": they are measured in different units — one in
    points of TOTAL capital above target, the other in % gain ON THE POSITION — and collapsing
    them to one number would compare quantities that are not comparable. The ladder's rank
    condition is reported separately from its gain condition for the same reason: a rung can be
    one good week away on gain and permanently out of reach on rank, and those are not the same
    situation.
    """
    trim = cfg.get("trim_if", {}) or {}
    over_min = float(trim.get("overweight_pp_min", 4.0))
    ladder = sorted(cfg.get("profit_ladder") or [], key=lambda r: float(r.get("gain_pct_min", 0)))
    out: list[dict] = []
    for r in rows:
        if float(r.get("actual_eur") or 0.0) <= 0:
            continue
        # The rank leg means "the model has STOPPED leading this name" — that is a statement
        # about the RANKING, so it reads the universe rank. The model-book rank is None/NaN for
        # exactly the names the model dropped, i.e. blind exactly when the leg should fire.
        gain, rank = r.get("unrealized_pct"), _clean_rank(r.get("score_rank"))
        over_pp = -float(r.get("gap_pp") or 0.0)          # positive = above target

        # The next UNMET ladder rung (they are sorted ascending; a met rung is already an action).
        lad = None
        for rung in ladder:
            need_gain = float(rung.get("gain_pct_min", 0))
            need_rank = int(rung.get("rank_min", 0) or 0)
            met_gain = gain is not None and gain >= need_gain
            lad = {
                "label": (f"+{need_gain:.0f}%"
                          + (f" & rank ≥ {need_rank}" if need_rank else "")
                          + f" → trim {float(rung.get('trim_fraction', 0)) * 100:.0f}%"),
                "need_gain_pct": None if (gain is None or met_gain) else round(need_gain - gain, 2),
                "gain_met": met_gain,
                "rank_ok": need_rank == 0 or (rank is not None and rank >= need_rank),
                "rank_min": need_rank,
            }
            if not met_gain:
                break                                     # the next one you could reach

        out.append({
            "sector_id": r.get("sector_id"), "etf": r.get("etf"),
            "unrealized_pct": gain, "rank": rank, "actual_pct": r.get("actual_pct"),
            "action": r.get("rule_action"),
            "live": r.get("rule_action") in ("TRIM", "REDUCE", "SELL"),
            "ladder": lad,
            "overweight": {
                "label": f"≥ {over_min:.0f}pp above target",
                "over_pp": round(over_pp, 2),
                "need_pp": None if over_pp >= over_min else round(over_min - over_pp, 2),
                "met": over_pp >= over_min,
            },
        })
    return out


def close_target_weights(weights_pct: list[float], max_position_pct: float,
                         max_dropped_pct: float = 40.0) -> dict:
    """Rescale the surviving model weights so the TARGET BOOK closes on 100% of deployable.

    THE BUG THIS EXISTS TO KILL (plan v4 §0.2 D2). `portfolio_holding` sums to exactly 100%.
    `build()` then removes the names that are not buyable today and computed
    `target_eur = weight_pct/100 × deployable` on what was left — so the dropped weight simply
    evaporated. On 2026-08-28 that was 36.1% of the model book: the deploy rule asked for €7,000
    at work, the targets summed to €4,476, and executing EVERY rule action left the book at 38%
    against a 70% rule. The deployment ratio is the module's anti-cash-hoarding device; a missing
    renormalization made it unreachable by construction.

    Two guards, and both matter more than the rescale itself:

    `max_dropped_pct` caps how much concentration a universe cut is allowed to cause. Rescaling
    a book that lost 60% of its weight would triple the remaining positions — that is not a
    weighting decision, it is a scoring problem wearing a weighting costume, and the caller must
    see it (`incomplete`) rather than have it quietly executed.

    `max_position_pct` is re-applied AFTER the rescale via `water_fill`, because rescaling can
    push a name through the ceiling it had already cleared. Weight the cap sheds becomes CASH,
    not another position: the cap is a risk limit, and spending it elsewhere would negate it.

    Returns `weights` (same order, summing to `pool_pct` ≤ 100), plus the audit trail.
    """
    kept = sum(w for w in weights_pct if w)
    if kept <= 0:
        return {"weights": [0.0] * len(weights_pct), "pool_pct": 0.0, "residual_pct": 100.0,
                "scale": 1.0, "capped": False, "incomplete": True}
    residual = max(0.0, 100.0 - kept)
    max_scale = 100.0 / max(1e-9, 100.0 - float(max_dropped_pct))
    scale = min(100.0 / kept, max_scale)
    capped = (100.0 / kept) > max_scale
    pool_pct = kept * scale                                   # ≤ 100 by construction
    from catalyx.execution.portfolio import water_fill   # same cap logic as the model builder

    fr = water_fill(list(weights_pct), float(max_position_pct) / 100.0)
    out = [round(f * pool_pct, 4) for f in fr]
    return {"weights": out, "pool_pct": round(sum(out), 4), "residual_pct": round(residual, 2),
            "scale": round(scale, 4), "capped": capped, "incomplete": capped}


def hhi(weights_pct: list[float]) -> float | None:
    """Herfindahl on percentage weights (0–1). Concentration as a number, not an adjective."""
    tot = sum(w for w in weights_pct if w)
    if tot <= 0:
        return None
    return round(sum((w / tot) ** 2 for w in weights_pct if w), 4)


def rank_out_streak(rank_history: list[int | None], out_of_top: int) -> int:
    """Consecutive MOST-RECENT runs a sector was SCORED and ranked below `out_of_top`.

    Counted from the newest backwards and reset by any run inside the cut, so one bad print never
    accumulates into a sell signal — the same "from the newest only" convention as
    catalyst_lifecycle.consecutive_below.

    A `None` — the sector was absent from that run's `sector_snapshot` — also BREAKS the streak
    (plan v4 §0.2 D6). It used to count as "outside the cut", which meant a sector that was never
    scored accumulated a sell signal out of data nobody collected; a universe reshaping could
    manufacture a SELL on a position without a single measurement behind it. Missing is missing.
    Enough missing runs is its own state, and `rank_coverage` reports it as RE-SCORE.
    """
    streak = 0
    for r in reversed(rank_history or []):
        if r is None or r <= out_of_top:
            break
        streak += 1
    return streak


def rank_coverage(rank_history: list[int | None]) -> dict:
    """How much of the recent history actually MEASURED this sector."""
    hist = list(rank_history or [])
    missing = sum(1 for r in hist if r is None)
    return {"n_runs": len(hist), "scored": len(hist) - missing, "missing": missing,
            "missing_recent": next((i for i, r in enumerate(reversed(hist)) if r is not None),
                                   len(hist))}


# ── The trade budget — a scarce slot is not a free one (plan v6 L3) ─────────

# Priority of a money-moving row when there are more rows than slots. NOT expected return: the
# composite's rank IC is noise (−0.05), so ordering by a forecast we have measured as
# unreliable would invent precision exactly where the system already declared none. These three
# are ordered by what IS measured — risk removed, then the cost of inaction (`cash_drag`), then
# turnover, which is what Gârleanu–Pedersen (2013) says to starve first when trading is costly.
_BUDGET_TIERS = {"SELL": 0, "REDUCE": 0, "BUY": 1, "ADD": 1, "TRIM": 2}


def is_full_exit(row: dict) -> bool:
    """Does this row close the LINE, whatever the rule called it?

    `TRIM` covers two different trades and only one of them is turnover. Shaving 4pp off an
    overweight is the fine adjustment Gârleanu–Pedersen says to starve first when trading is
    costly — it stays tier 2. A TRIM against a 0% target is a full exit with another name, which
    is how every exit from a sector the model book no longer holds arrives (copper and pharma,
    2026-09-01). Filing that behind a BUY makes the slot allowance decide to keep risk, and
    "removing risk does not queue" was never about the label.
    """
    trade = float(row.get("trade_eur") or 0.0)
    if trade >= 0:
        return False
    target, actual = row.get("target_pct"), row.get("actual_eur")
    if target is None or actual is None:
        return False                                   # unknowable → not claimed as an exit
    return float(target) <= 0.0 and abs(trade) >= float(actual) - 0.01


def action_label(row: dict) -> str:
    """The verb a READER sees for this row. Presentation only — `rule_action` is the enum.

    A TRIM that closes the line is a sale of the whole position wearing the name of a fine
    adjustment (every exit from a sector the model book no longer holds arrives that way). The
    rule table, the lake, the override log and the scorecard keep reading `TRIM` — changing the
    enum would re-key every priced deviation — but a table that says "TRIM" over a line going to
    zero makes the reader hunt the reason column to learn it is an exit.
    """
    act = str(row.get("rule_action") or "")
    return "CLOSE" if act == "TRIM" and is_full_exit(row) else act


def trade_budget_plan(rows: list[dict], cfg: dict) -> dict:
    """Split the money-moving rows into what this review may execute and what it must defer.

    `fee_eur: 0.0` says a trade is free. Inside the monthly allowance that is true in accounting
    terms and false in economic ones: the slot is scarce, and the mandate spends slots on
    catalysts that arrive BETWEEN reviews, so holding some back has option value. Rows in
    `exempt_actions` are never deferred — removing risk does not queue — but they DO consume
    slots, and if they alone exhaust the budget that is reported, not hidden. `exempt_full_exits`
    extends that to a row that closes the line whatever the rule called it (`is_full_exit`),
    because a full exit arriving as TRIM is still a full exit.

    Nothing is zeroed: `rule_action` and `trade_eur` stay the rule's ask, and a deferred row is
    flagged so the deferral can be logged (author `budget`) and priced like any other deviation.
    """
    tb = (cfg.get("trade_budget") or weights.trade_budget())
    exempt = set(tb.get("exempt_actions") or ())
    exempt_exits = bool(tb.get("exempt_full_exits", True))
    free, reserve = int(tb.get("free_per_month", 10)), int(tb.get("reserve_for_events", 3))
    budget = max(0, min(int(tb.get("planned_max_per_review", 6)), free - reserve))

    def _exempt(r: dict) -> bool:
        return r.get("rule_action") in exempt or (exempt_exits and is_full_exit(r))

    # A row the ramp already queued does not compete for a slot: the scarcity that stopped it is
    # cash, and rationing slots against it would spend the allowance on rows nobody will place.
    movers = [r for r in rows if float(r.get("trade_eur") or 0.0) != 0.0
              and r.get("ramp_state") != "deferred"]
    # v12 B10 — WITHIN a tier, rank, not size. The two scarcities used to disagree: the ramp
    # fills buys in `score_rank` order and says so ("the model's own ordering, not trade size"),
    # and then the slot budget re-sorted the survivors by `-abs(trade_eur)`, so a large low-rank
    # BUY took the slot from a smaller high-rank one the ramp had just prioritised. Cash is
    # rationed before slots precisely so the two agree on WHICH names; letting them order
    # differently spends the scarcer resource against the model's own ranking. Size stays as
    # the tiebreak, where it was doing real work: between two rows of equal rank quality, the
    # bigger one buys more exposure per slot.
    ranked = sorted(movers, key=lambda r: (
        -1 if _exempt(r) else _BUDGET_TIERS.get(r.get("rule_action"), 3),
        _clean_rank(r.get("score_rank")) or 999,
        -abs(float(r.get("trade_eur") or 0.0))))

    granted, deferred = [], []
    for r in ranked:
        if _exempt(r) or len(granted) < budget:
            r["budget_state"] = "exempt" if _exempt(r) else "granted"
            granted.append(r)
        else:
            r["budget_state"] = "deferred"
            deferred.append(r)
    for r in rows:
        r.setdefault("budget_state", "n/a")           # HOLD / RE-SCORE spend nothing

    over = max(0, len(granted) - budget)
    return {
        "free_per_month": free, "reserve_for_events": reserve, "budget": budget,
        "granted": len(granted), "deferred": len(deferred), "over_budget": over,
        "deferred_eur": round(sum(abs(float(r.get("trade_eur") or 0.0)) for r in deferred), 2),
        "deferred_rows": [{"sector_id": r["sector_id"], "rule_action": r["rule_action"],
                           "trade_eur": float(r.get("trade_eur") or 0.0)} for r in deferred],
        "note": (f"{len(granted)} of {len(movers)} money-moving rows fit the {budget}-trade "
                 f"review budget ({free} free/month less {reserve} reserved for events)."
                 + (f" {len(deferred)} deferred by budget, €"
                    f"{sum(abs(float(r.get('trade_eur') or 0)) for r in deferred):,.0f} held back."
                    if deferred else "")
                 + (f" {over} risk-removal row(s) push PAST the budget — they are never deferred."
                    if over else "")),
    }


# ── The deployment ramp — scaling in is a SCHEDULE, not a hesitation (v9 R1) ─

def deployment_ramp_plan(rows: list[dict], deployed_eur: float, deployable_eur: float,
                         total_capital_eur: float, cfg: dict) -> dict:
    """Cap how far ONE review may raise the deployed share of the book.

    The deploy ratio answers "how much should be at work"; it never answered "how fast do we get
    there", so every review under-deployed asked for the WHOLE gap at once — €4,868 across six
    names in an afternoon. The ramp is the missing second half of that rule: the destination is
    unchanged (`deployable_eur`), the ROUTE is `max_step_pp` points of total capital per review.

    Three properties keep it from becoming a hiding place for cash:

    - It caps the NET (`Σ trade_eur`), so a pure rotation — sell one name, buy another — is
      unconstrained. The ramp governs scaling in, not turnover.
    - It fills in RANK order and to FULL size, so a tranche buys one conviction-weight name
      rather than six quarter-positions. A quarter-position is not a smaller bet, it is a worse
      one: the same slot, the same spread, a quarter of the exposure.
    - Deferred rows are logged (author `ramp`) and priced 21 days later like any deviation, so
      the schedule itself is falsifiable. The shortfall keeps being measured against the FULL
      `deployable_eur` — the ramp changes what this review must execute, never what it costs to
      be under-deployed, and the cash drag stays printed beside it.
    """
    d = ((cfg or {}).get("deployment") or {}).get("ramp") or {}
    step_pp = float(d.get("max_step_pp", 0.0) or 0.0)
    total = float(total_capital_eur or 0.0)
    movers = [r for r in rows if float(r.get("trade_eur") or 0.0) != 0.0]
    ask_eur = round(sum(float(r.get("trade_eur") or 0.0) for r in movers), 2)
    enabled = bool(d.get("enabled", True)) and step_pp > 0 and total > 0

    for r in rows:
        r.setdefault("ramp_state", "n/a")
    if not enabled:
        return {"enabled": False, "ask_eur": ask_eur, "deferred": 0, "deferred_rows": [],
                "note": ""}

    step_eur = round(total * step_pp / 100.0, 2)
    deployed = float(deployed_eur or 0.0)
    allowed_after = round(min(float(deployable_eur or 0.0), deployed + step_eur), 2)
    min_ticket = float((cfg or {}).get("min_ticket_eur", 150.0))

    # Sells and trims never queue: they lower deployment, which is the direction the ramp is not
    # rationing. Buys fill in rank order — the model's own ordering, not trade size.
    running, deferred = deployed, []
    for r in movers:
        if float(r["trade_eur"]) < 0:
            r["ramp_state"] = "granted"
            running = round(running + float(r["trade_eur"]), 2)
    buys = sorted([r for r in movers if float(r["trade_eur"]) > 0],
                  key=lambda r: (_clean_rank(r.get("score_rank")) or 999,
                                 -float(r.get("trade_eur") or 0.0)))
    # The tranche this review actually has, measured AFTER the sells have given their room back.
    # A buy larger than this can never be granted however the queue is ordered — that is a
    # property of the constants (`max_step_pp` 15pp vs `max_position_pct` 17.5pp), not of the
    # queue, so it is NAMED rather than filed as one more deferral. Left unnamed it reads as
    # "next review", and the row is in fact unbuyable in every review, forever.
    tranche = round(allowed_after - running, 2)
    unfittable = []
    for r in buys:
        trade = float(r["trade_eur"])
        if round(running + trade, 2) <= allowed_after:
            r["ramp_state"] = "granted"
            running = round(running + trade, 2)
        else:
            # RANK ORDER IS ABSOLUTE: a row is never skipped to MAKE ROOM for a lower-ranked one,
            # and grants are always full-size. But stopping the fill at the first row that does
            # not fit displaces NOBODY — that row is deferred either way — while throwing away
            # the remainder of the tranche this review just authorised. Live on 2026-09-01 that
            # was €1,059.67 of authorised headroom left unspent, flagged `headroom_usable: True`,
            # with two rows that fit queued behind one that did not (v13.3).
            r["ramp_state"] = "deferred"
            deferred.append(r)
            if trade > tranche:
                r["ramp_never_fits"] = True
                unfittable.append(r)

    headroom = round(allowed_after - running, 2)
    gap_to_full = round(float(deployable_eur or 0.0) - deployed, 2)
    reviews = int(math.ceil(gap_to_full / step_eur)) if step_eur > 0 and gap_to_full > 0 else 0
    return {
        "enabled": True, "max_step_pp": step_pp, "step_eur": step_eur,
        "deployed_eur": round(deployed, 2), "allowed_after_eur": allowed_after,
        "allowed_after_pct": round(allowed_after / (total or 1.0) * 100, 2),
        "ask_eur": ask_eur, "planned_after_eur": round(deployed + ask_eur, 2),
        "after_eur": round(running, 2),
        "after_pct": round(running / (total or 1.0) * 100, 2),
        # After the backfill, every remaining deferred row has already been offered this
        # headroom and did not fit — so "usable" means "authorised and unclaimed by any row on
        # this table", never "we forgot to spend it".
        "headroom_eur": headroom, "headroom_usable": bool(headroom >= min_ticket),
        "headroom_smallest_deferred_eur": (round(min(float(r["trade_eur"]) for r in deferred), 2)
                                           if deferred else None),
        "reviews_to_full": reviews,
        "deferred": len(deferred),
        "deferred_eur": round(sum(float(r["trade_eur"]) for r in deferred), 2),
        "deferred_rows": [{"sector_id": r["sector_id"], "rule_action": r["rule_action"],
                           "trade_eur": float(r["trade_eur"]),
                           "never_fits": bool(r.get("ramp_never_fits"))} for r in deferred],
        "tranche_eur": tranche,
        "unfittable": len(unfittable),
        "unfittable_rows": [{"sector_id": r["sector_id"], "trade_eur": float(r["trade_eur"]),
                             "excess_eur": round(float(r["trade_eur"]) - tranche, 2)}
                            for r in unfittable],
        "note": (f"scaling in {step_pp:.0f}pp of capital per review — this one may take the book "
                 f"to {allowed_after / (total or 1.0) * 100:.0f}% "
                 f"({_eur(allowed_after)}), the deploy rule's {_eur(deployable_eur)} arrives in "
                 f"~{reviews} review(s)."
                 + (f" {len(deferred)} row(s) queue behind the tranche, "
                    f"{_eur(sum(float(r['trade_eur']) for r in deferred))} held back: "
                    + ", ".join(r["sector_id"] for r in deferred) + "."
                    if deferred else " Every row fits this tranche.")
                 + (f" {len(unfittable)} row(s) EXCEED a whole tranche ({_eur(tranche)}) and are "
                    f"not deferred to next review — they are unbuyable at full size under "
                    f"max_step_pp={step_pp:.0f}pp until the constant or the ask changes: "
                    + ", ".join(f"{r['sector_id']} {_eur(float(r['trade_eur']))}"
                                for r in unfittable) + "."
                    if unfittable else "")),
    }


# ── The risk budget in the action table (v10 P6/P7) ─────────────────────────

def new_line_slot_pass(rows: list[dict], n_target: int, cfg: dict) -> dict:
    """A BUY of a line not held only fills a FREE line (v15.2). Mutates `rows`.

    Lines held after this table's exits = held rows that are not a SELL and not a full exit. A
    new line is allowed while that count is under `n_target`, in universe-rank order (the leader
    is never skipped to make room). The rest become HOLD with the reason: the book is full and a
    swap needs an exit rule to free a line - the same sentence for every deferred name, so it can
    be audited. ADD rows are untouched (a held line is not a new line), and so is every sale.

    It exists because the hold zone alone would leave the model free to ask for a 7th line: the
    cut that used to SELL the old name now keeps it, and the BUY of the newcomer would double the
    exposure the cut was meant to rotate. Together they make a swap a decision of the sell rules,
    not of a 0.07z wobble.
    """
    if not (cfg.get("hold_zone", {}) or {}).get("enabled", False):
        return {"enabled": False, "n_target": n_target, "lines_after_exits": None, "deferred": []}
    lines = sum(1 for r in rows
                if float(r.get("actual_eur") or 0.0) > 0
                and r.get("rule_action") != "SELL" and not is_full_exit(r))
    deferred, granted = [], []
    buys = sorted((r for r in rows if r.get("rule_action") == "BUY"
                   and float(r.get("actual_eur") or 0.0) <= 0),
                  key=lambda r: (_clean_rank(r.get("score_rank")) or 999, r["sector_id"]))
    for r in buys:
        if lines < n_target:
            lines += 1
            granted.append(r["sector_id"])
            continue
        deferred.append({"sector_id": r["sector_id"], "trade_eur": float(r["trade_eur"] or 0.0),
                         "score_rank": r.get("score_rank")})
        r["rule_action"], r["trade_eur"] = "HOLD", 0.0
        r["reason"] = (f"no free line: the book holds {n_target} lines after this table's exits "
                       f"(n_target) — a swap needs a SELL rule to free one, not a rank cut")
        for k in ("spread_eur", "cost_drag_eur"):
            r[k] = 0.0
        for k in ("breakeven_pct", "dz_required", "expected_edge_eur", "net_edge_eur"):
            r[k] = None
    return {"enabled": True, "n_target": n_target, "lines_after_exits": lines,
            "granted": granted, "deferred": deferred}


def cluster_risk_pass(rows: list[dict], clusters: list[dict], cfg: dict) -> dict:
    """Let CONCENTRATION IN RISK, not only in euros, speak in the action table.

    WHY A SECOND PASS AND NOT A RULE IN `decide_action`. A cluster's risk is a property of the
    POST-TRADE book, and the post-trade book is what `decide_action` produces — the input does
    not exist until the first pass has run. So this reads the finished table the same way
    `trade_budget_plan` and `deployment_ramp_plan` already do, and for the same reason.

    WHAT IT DOES, and the asymmetry is deliberate:
      · a cluster over its variance budget makes every ADD/BUY into that cluster a HOLD — you do
        not add to the bucket that is already carrying more risk than it was budgeted;
      · and it NAMES the member carrying the most of that risk as a trim candidate, sized
        first-order at `eur × (1 − budget/ctr)`.
    SELL and REDUCE are never touched: they remove risk, and a risk limit that could cancel a
    risk removal would be a limit arguing with itself.

    THE SIZING IS FIRST-ORDER AND SAYS SO. A variance share is not invertible per position
    without an optimizer — trimming a member changes every other member's share too. The
    proportional step is the linearization of that around the current book: it under-shoots for
    a highly-correlated cluster and over-shoots for a diversifying one, converging over reviews
    rather than in one. That is honest for a `warn`-level limit and would need replacing before
    the limit ever moved money on its own. The TRIGGER is measured risk; only the STEP is
    notional, and conflating the two is what the notional cap already gets wrong.

    ONE ACTION PER ROW PER PASS. A member whose ADD/BUY was just blocked is not ALSO offered as
    a trim candidate: declining a purchase and selling the position in the same breath is not a
    discipline, it is two rules arguing. So a cluster whose every member has a pending add is
    answered by stopping the adds — the budget's first job is to stop making it worse — and the
    trim, if one is still needed, is the NEXT review's, computed on a book that has changed.

    UNMEASURED IS NOT COMPLIANT. `over_risk is None` — no covariance — is reported as unmeasured
    and changes nothing. It is never read as "within budget".

    At `risk_enforcement: "warn"` (the shipped default) nothing here rewrites an action: the
    findings are returned for the report and the table stands. `"block"` arms it.
    """
    ccc = weights.correlated_catalyst_cap()
    budget = float(ccc.get("max_cluster_variance_pct", 35.0))
    arming = str(ccc.get("risk_enforcement", "warn")).lower()
    armed = arming == "block"

    by_sector = {r["sector_id"]: r for r in rows}
    breaching = [c for c in (clusters or []) if c.get("over_risk") is True]
    unmeasured = [c["catalyst_id"] for c in (clusters or []) if c.get("over_risk") is None]

    findings, changed = [], 0
    for c in sorted(breaching, key=lambda c: -float(c.get("risk_ctr_pct") or 0.0)):
        ctr = float(c["risk_ctr_pct"])
        members = [m["sector_id"] for m in (c.get("members_by_risk") or [])]
        blocked = []
        for sid in members or (c.get("sectors") or []):
            r = by_sector.get(sid)
            if r and r.get("rule_action") in ("ADD", "BUY"):
                blocked.append({"sector_id": sid, "rule_action": r["rule_action"],
                                "trade_eur": float(r.get("trade_eur") or 0.0)})
                if armed:
                    r["rule_action"], r["trade_eur"] = "HOLD", 0.0
                    r["reason"] = (f"{c['catalyst_id']} carries {ctr:.1f}% of book variance "
                                   f"against a {budget:.0f}% budget — no adds into a cluster "
                                   f"already over its risk")
                    changed += 1

        # The trim candidate: the member carrying the most of the cluster's risk that is not
        # already being sold down by a higher-precedence rule.
        #
        # It must be a position ACTUALLY HELD, and that is not a detail. `members_by_risk` is
        # measured on the POST-trade book, so a name the table is only proposing to buy carries
        # risk there without existing yet — and since the block above has just declined that very
        # buy, trimming it would have the budget cancelling a purchase and selling the result in
        # the same breath. The euro the trim is sized on is therefore the HELD euro, not the
        # post-trade one.
        blocked_ids = {b["sector_id"] for b in blocked}
        cand = None
        for m in (c.get("members_by_risk") or []):
            r = by_sector.get(m["sector_id"]) or {}
            if r.get("rule_action") in ("SELL", "REDUCE", "TRIM") or m["sector_id"] in blocked_ids:
                continue
            if float(r.get("actual_eur") or 0.0) <= 0:
                continue
            cand = {**m, "held_eur": round(float(r["actual_eur"]), 2)}
            break
        trim = None
        if cand and cand["held_eur"] > 0 and ctr > 0:
            step = round(cand["held_eur"] * (1.0 - budget / ctr), 2)
            if step >= float(cfg.get("min_ticket_eur", 150.0)):
                trim = {"sector_id": cand["sector_id"], "ctr_pct": cand["ctr_pct"],
                        "eur": cand["held_eur"], "trim_eur": step}
                if armed:
                    r = by_sector.get(cand["sector_id"])
                    if r is not None:
                        r["rule_action"], r["trade_eur"] = "TRIM", -step
                        r["reason"] = (f"{c['catalyst_id']} at {ctr:.1f}% of book variance "
                                       f"(budget {budget:.0f}%); this line carries "
                                       f"{float(cand['ctr_pct']):.1f}pp of it")
                        changed += 1

        findings.append({
            "catalyst_id": c["catalyst_id"], "ctr_pct": ctr, "budget_pct": budget,
            "over_by_pp": round(ctr - budget, 2),
            "notional_pct": c.get("post_pct"), "notional_over": c.get("over"),
            "rules_disagree": c.get("rules_disagree"),
            "blocked": blocked, "trim_candidate": trim,
            "members_by_risk": c.get("members_by_risk") or [],
        })

    # v12 B11 — `risk_state` used to be set to "n/a" on every row and nothing ever assigned it
    # anything else: a column the report could print but that carried no information. It now
    # says what this pass concluded about the row, which is the only way a reader can tell a row
    # the risk budget declined from one it never looked at.
    for f in findings:
        for b in f["blocked"]:
            r = by_sector.get(b["sector_id"])
            if r is not None:
                r["risk_state"] = "blocked"
        t = f.get("trim_candidate")
        if t and by_sector.get(t["sector_id"]) is not None:
            by_sector[t["sector_id"]]["risk_state"] = "trim_candidate"
        for m in f.get("members_by_risk") or []:
            r = by_sector.get(m["sector_id"])
            if r is not None:
                r.setdefault("risk_state", "over_cluster")
    unmeasured_sectors = {s for c in (clusters or []) if c.get("over_risk") is None
                          for s in (c.get("sectors") or [])}
    for sid in unmeasured_sectors:
        r = by_sector.get(sid)
        if r is not None:
            r.setdefault("risk_state", "unmeasured")
    for r in rows:
        r.setdefault("risk_state", "n/a")
    return {
        "enabled": True, "armed": armed, "enforcement": arming, "budget_pct": budget,
        "breaching": findings, "n_breaching": len(findings),
        "unmeasured": unmeasured, "rows_changed": changed,
        "note": (f"risk budget {budget:.0f}% of book variance per driver — "
                 + ("no cluster over budget" if not findings else
                    "; ".join(f"{f['catalyst_id']} {f['ctr_pct']:.1f}% "
                              f"(+{f['over_by_pp']:.1f}pp)" for f in findings))
                 + (f". {len(unmeasured)} cluster(s) UNMEASURED — reported, never assumed "
                    f"compliant: {', '.join(unmeasured)}" if unmeasured else "")
                 + ("." if armed else
                    ". Enforcement is `warn`: the table above is unchanged, these are the rows "
                    "the budget would move if it were armed.")),
    }


# ── Anti-conservatism: the cost of NOT acting (plan v4 §4 C1/C3/C4) ─────────
#
# v3 built the machinery that makes a bad action visible: a rule table, banned words, an override
# log, a suspension arithmetic. What it never built is the machinery that makes INACTION visible.
# Friction is printed to the cent on every row; the cost of leaving €6,954 idle for 73 days is
# printed nowhere. That asymmetry is not neutral — it is a thumb on the scale for doing nothing,
# every single run, and these three functions are its counterweight.

def shortfall_pp(deployed_eur: float, deployable_eur: float, total_capital_eur: float) -> float:
    """How many points of TOTAL capital the book sits below what the deploy rule asks for.

    Points of total capital, not a ratio of the target: a book at 30% against a 85% rule is
    55pp short, and that is the number the persistence rule counts. Negative = over-deployed.
    """
    denom = float(total_capital_eur or 0.0)
    if denom <= 0:
        return 0.0
    return round((float(deployable_eur or 0.0) - float(deployed_eur or 0.0)) / denom * 100.0, 2)


def absorbable_eur(deployable_eur: float, target_total_eur: float) -> dict:
    """What the model book can actually HOLD, which is not always what the deploy rule asks for.

    THE DEFECT (v10 P2). `deployable = capital × deploy_ratio` is a statement about how much
    should be at work. It is silent about whether there are enough eligible NAMES to hold it,
    and `max_position_pct` says there must be: at a 20% ceiling, five names is the fewest that
    can absorb a full book, four cap out at 80%, three at 60%. So a book the rules themselves
    cut to four names was measured against a target it could not reach by construction, and the
    persistence rule re-opened that breach every review — an infeasible demand, permanently
    breached, therefore eventually ignored. That is the same failure mode v6 L2 found in the
    correlated-catalyst cap, arriving from the other direction.

    This matters far more now than it did last month, because v10 P1 gives the rules a way to
    deliberately hold FEWER names: a conviction gate that admits four names is answering the
    user's actual question ("where do we invest, where do we not, and is some cash right?"), and
    it must not be scored as a failure to deploy.

    WHY THIS IS NOT A HIDING PLACE FOR CASH, which is the objection it has to survive:
      · the ceiling is the CAP's arithmetic, not a judgement — `n × max_position_pct`, nothing
        discretionary enters;
      · both numbers are always reported, so the gap to the deploy rule never disappears from
        the page, it just stops being called a breach nobody can answer;
      · `cash_drag` keeps pricing ALL idle cash against the benchmark and the model book,
        untouched. Cash the rules created is still cash that costs something, and the ledger
        that says so is the one that was already there.
    In short: the rule stops demanding the impossible, and keeps charging for the possible.
    """
    depl = float(deployable_eur or 0.0)
    target = max(0.0, float(target_total_eur or 0.0))
    absorbable = min(depl, target) if target > 0 else depl
    return {
        "deployable_eur": round(depl, 2),
        "target_total_eur": round(target, 2),
        "absorbable_eur": round(absorbable, 2),
        "capped_by_book": bool(target > 0 and target < depl - 0.01),
        "unreachable_eur": round(max(0.0, depl - absorbable), 2),
        "why": (f"the deploy rule asks for {_eur(depl)}; the model book's targets sum to "
                f"{_eur(target)}, so {_eur(depl - absorbable)} of it has no eligible name to go "
                f"into — the shortfall is measured against what the book can hold, and the full "
                f"gap stays printed beside it" if target > 0 and target < depl - 0.01 else
                f"the model book can absorb the whole {_eur(depl)} the deploy rule asks for"),
    }


# ── The cash stance — a decision to hold cash, said ONCE and priced (v10 P3) ─
#
# The book had two ways to be under-deployed and only one of them was a decision. Either the
# review executed the rows, or the shortfall survived and the persistence rule re-opened the same
# unanswered question every cycle — which is how a real judgement ("I want dry powder until the
# Fed meets") degraded into an argument repeated monthly and eventually skipped. There was no
# object for "I decided this, here is why, ask me again in N days".
#
# It is deliberately NOT a new document type. It is an override, in the override log, with an
# author and a reason, scored ~21 trading days later like every other deviation — because the
# thing that keeps an escape hatch honest is that it gets priced, and that machinery already
# exists. What is new is only that this one answers a BOOK-level rule rather than a row, and
# that it EXPIRES: silence is what the shortfall rule was built to break, so a stance that never
# lapsed would rebuild exactly the silence it replaced.

_CASH_STANCE_SECTOR = "__book_cash__"
_CASH_STANCE_ACTION = "CASH-STANCE"


def _stance_cfg(cfg: dict) -> dict:
    d = ((cfg or {}).get("deployment") or {}).get("cash_stance") or {}
    return {"max_days": int(d.get("max_days", 90)),
            "enabled": bool(d.get("enabled", True))}


def cash_stance(lake_dir: Path | None = None, cfg: dict | None = None,
                as_of: str | None = None) -> dict | None:
    """The live cash stance, or None. A stance older than `max_days` has lapsed by design.

    Only a HUMAN author's stance silences the rule. `budget`, `ramp` and `unrecorded` are the
    machine's own deferrals — letting a stance be written by the same automation whose output it
    excuses would close the loop on itself, and the shortfall would answer to nobody.
    """
    from catalyx.store import lake

    sc = _stance_cfg(cfg or weights.rebalance_rules())
    if not sc["enabled"]:
        return None
    ov = lake.read_table(_OVERRIDE_TABLE, lake_dir=lake_dir)
    if ov.empty or "sector_id" not in ov.columns:
        return None
    rows = ov[(ov["sector_id"] == _CASH_STANCE_SECTOR)
              & (ov.get("chosen_action") == _CASH_STANCE_ACTION)]
    if rows.empty:
        return None
    today = date.fromisoformat(as_of or date.today().isoformat())
    live = []
    for _, r in rows.iterrows():
        if str(r.get("author")) in _AUTO_DEFER_AUTHORS + ("unrecorded",):
            continue
        try:
            started = date.fromisoformat(str(r.get("logged_at") or "")[:10])
        except ValueError:                                     # pragma: no cover - defensive
            continue
        age = (today - started).days
        if age <= sc["max_days"]:
            live.append({"logged_at": str(r.get("logged_at")), "started": started.isoformat(),
                         "author": str(r.get("author")), "reason": str(r.get("reason") or ""),
                         "run_id": str(r.get("run_id")), "age_days": age,
                         "expires_on": (started + timedelta(days=sc["max_days"])).isoformat(),
                         "days_left": sc["max_days"] - age,
                         "rule_deploy_eur": abs(float(r.get("rule_trade_eur") or 0.0)) or None,
                         "chosen_deploy_eur": float(r.get("chosen_trade_eur") or 0.0)})
    if not live:
        return None
    return max(live, key=lambda s: s["logged_at"])


def log_cash_stance(run_id: str, reason: str, author: str, deploy_gap_eur: float,
                    chosen_deploy_eur: float = 0.0, lake_dir: Path | None = None,
                    cfg: dict | None = None) -> dict:
    """Write a cash stance: the decision NOT to deploy what the rule asks, with its reason.

    `deploy_gap_eur` is what the rule wanted at work (positive). It is stored as the rule's
    trade so `score_overrides` can price the stance against the benchmark it declined — the
    same difference-of-exposures arithmetic every other override is scored by, with the
    benchmark standing in for the vehicle because the capital declined a market, not a name.
    """
    return log_override(run_id, _CASH_STANCE_SECTOR, "DEPLOY", _CASH_STANCE_ACTION,
                        reason=reason, author=author,
                        chosen_trade_eur=float(chosen_deploy_eur or 0.0),
                        rule_trade_eur=abs(float(deploy_gap_eur or 0.0)),
                        lake_dir=lake_dir, cfg=cfg)


def shortfall_status(history: list[dict], cfg: dict, stance: dict | None = None) -> dict:
    """Has the book been under-deployed long enough that silence stops being an option?

    `history` is one record per RECORDED RUN, oldest first, each `{as_of, shortfall_pp}`. The
    rule: more than `max_shortfall_pp` below the deploy ratio for `max_shortfall_runs`
    consecutive runs, and the review must either execute the rows or log an override naming the
    shortfall itself. A shortfall that survives two reviews without a written reason is exactly
    what the deployment ratio was built to make visible, and it has been surviving silently.
    """
    d = (cfg or {}).get("deployment", {}) or {}
    max_pp = float(d.get("max_shortfall_pp", 10.0))
    max_runs = int(d.get("max_shortfall_runs", 2))
    hist = [h for h in (history or []) if h.get("shortfall_pp") is not None]
    streak, since = 0, None
    for h in reversed(hist):                       # newest first — count back while it breaches
        if float(h["shortfall_pp"]) > max_pp:
            streak += 1
            since = h.get("as_of") or since
        else:
            break
    current = float(hist[-1]["shortfall_pp"]) if hist else 0.0
    breached = streak >= max_runs
    # A live cash stance ANSWERS the breach; it does not delete it. The shortfall keeps its
    # number, the cash drag keeps pricing it, and the stance keeps its expiry date printed
    # beside both — so what changes is only that the review stops re-asking a question that has
    # a signed, dated, scored answer on file.
    answered = bool(breached and stance)
    return {
        "shortfall_pp": round(current, 2), "runs_breached": streak, "since": since,
        "max_shortfall_pp": max_pp, "max_shortfall_runs": max_runs,
        "breached": breached and not answered,
        "breach_raw": breached,
        "answered_by_stance": answered,
        "stance": stance,
        "note": (f"{current:.1f}pp below the deploy rule for {streak} consecutive recorded "
                 f"run(s) (limit {max_pp:.0f}pp × {max_runs}) — ANSWERED by the cash stance of "
                 f"{stance['started']} ({stance['author']}): {stance['reason']} Expires "
                 f"{stance['expires_on']} ({stance['days_left']}d), and the shortfall re-opens "
                 f"by itself that day. The drag is still priced below." if answered else
                 f"{current:.1f}pp below the deploy rule for {streak} consecutive recorded run(s) "
                 f"(limit {max_pp:.0f}pp × {max_runs}) — execute the rows or log an override "
                 f"naming the shortfall" if breached else
                 f"{current:.1f}pp below the rule, {streak} consecutive run(s) over "
                 f"{max_pp:.0f}pp (limit {max_runs})")}


def cash_drag(idle_eur: float, bench_return_pct: float | None, since: str | None,
              days: int | None = None, model_return_pct: float | None = None,
              model_id: str = "catalyx") -> dict:
    """What holding the idle cash cost — or saved — while it was idle, against two yardsticks.

    Not a reprimand — an entry in the same ledger as the €16 of spread that stops a rotation.
    The point is comparability: one of those two numbers has been printed on every row since v3
    and the other has never been printed at all, and the book has been at ~30% for months.

    TWO COUNTERFACTUALS (plan v5 §3 F3). `bench_return_pct` answers "should I have been in the
    market?"; `model_return_pct` — the model book this table implements — answers the question
    actually on trial: "should I have executed THIS table?". The second is the one the deployment
    rule is arguing for, and it already exists (it feeds `execution_alpha_pp`).

    AND THE LABEL FLIPS. A ledger that can only reprove is not a ledger: when the counterfactual
    fell, holding cash was the right call and the row says so with the sign it earned.
    """
    idle = float(idle_eur or 0.0)

    def _forgone(pct):
        return None if pct is None else round(idle * float(pct) / 100.0, 2)

    forgone, model_forgone = _forgone(bench_return_pct), _forgone(model_return_pct)
    # The headline follows whichever counterfactual we have that is closest to the decision:
    # the model book IS the policy on trial; the benchmark is the fallback.
    lead = model_forgone if model_forgone is not None else forgone
    verdict = None if lead is None else ("cost" if lead > 0 else "saved")
    head = ("CASH DRAG" if verdict == "cost" else
            "CASH THAT SAVED YOU" if verdict == "saved" else "CASH")
    lines = [f"€{idle:,.0f} idle" + (f" since {since}" if since else "")
             + (f" ({days}d)" if days else "")]
    if forgone is not None:
        lines.append(f"vs benchmark ({float(bench_return_pct):+.2f}%) → "
                     f"€{abs(forgone):,.0f} {'forgone' if forgone > 0 else 'avoided'}")
    if model_forgone is not None:
        lines.append(f"vs the `{model_id}` model book ({float(model_return_pct):+.2f}%) → "
                     f"€{abs(model_forgone):,.0f} "
                     f"{'forgone' if model_forgone > 0 else 'avoided'} — the policy this table "
                     f"implements")
    return {"idle_eur": round(idle, 2), "since": since, "days": days,
            "benchmark_return_pct": (None if bench_return_pct is None
                                     else round(float(bench_return_pct), 2)),
            "model_return_pct": (None if model_return_pct is None
                                 else round(float(model_return_pct), 2)),
            "model_id": model_id,
            "forgone_eur": forgone, "model_forgone_eur": model_forgone,
            "verdict": verdict, "headline": head,
            "note": " · ".join(lines)}


def unrecorded_deviations(prior_rows: list[dict], movements: list[dict],
                          overrides: list[dict], since: str | None,
                          until: str | None = None,
                          open_defers: list[dict] | None = None) -> list[dict]:
    """Rows the previous run told you to trade, with no movement and no override to show for it.

    An override is only logged if the narrator remembers to log it, and the cheapest way to be
    conservative is to be quiet. This makes quiet structural rather than voluntary: the NEXT run
    reads the previous run's non-HOLD rows and asks the filesystem, not the narrator, whether
    anything happened.

    `movements` are the executed records (`data/movements/*.json`), matched by `sector_id` and an
    `executed_at` inside the interval. Rows whose action moves no money (HOLD, RE-SCORE) are not
    deviations — nothing was asked of them.

    ONE DECISION, ONE DEFER (plan v5 §2 E3). The dedup used to be scoped to the PRIOR run only,
    so a standing decision — "the rule keeps saying BUY luxury and you keep not buying" — wrote a
    fresh DEFER every run: three pipeline executions in a week produced 30 rows for 10 decisions,
    and the tally measured how often the pipeline ran rather than how often a rule was declined.
    `open_defers` carries the unrecorded overrides already logged; a (sector, action) among them
    is the SAME ongoing silence until a movement in that sector settles it, and it keeps its
    original `logged_at` — the clock has to run from when you stopped acting, not from the last
    time somebody re-ran the pipeline.
    """
    moved, logged = set(), set()
    latest_move: dict[str, str] = {}
    for m in movements or []:
        at = str(m.get("executed_at") or "")[:10]
        if not at:
            continue
        sid_m = str(m.get("sector_id"))
        latest_move[sid_m] = max(latest_move.get(sid_m, ""), at)
        if (since and at < str(since)[:10]) or (until and at > str(until)[:10]):
            continue
        moved.add(sid_m)
    for o in overrides or []:
        logged.add(str(o.get("sector_id")))
    standing = set()
    for o in open_defers or []:
        sid_o, act_o = str(o.get("sector_id")), str(o.get("rule_action") or "")
        at = str(o.get("logged_at") or "")[:10]
        # A movement AFTER the defer settles it: the decision was finally taken, so the next
        # time the rule asks it is a new decision and deserves its own row.
        if latest_move.get(sid_o, "") > at:
            continue
        standing.add((sid_o, act_o))
    out = []
    for r in prior_rows or []:
        action = str(r.get("rule_action") or "")
        sid = str(r.get("sector_id") or "")
        if action in ("HOLD", "RE-SCORE", "") or not sid or sid == "CASH":
            continue
        if sid in moved or sid in logged or (sid, action) in standing:
            continue
        out.append({"sector_id": sid, "rule_action": action,
                    "trade_eur": float(r.get("trade_eur") or 0.0),
                    "run_id": r.get("run_id"), "as_of": r.get("as_of"),
                    "reason": (f"run {r.get('run_id')} said {action} {sid} "
                               f"€{abs(float(r.get('trade_eur') or 0.0)):,.0f}. "
                               f"No movement, no override.")})
    return out


# ── Assembly (I/O) ───────────────────────────────────────────────────────────

def _latest_run_id(portfolio_id: str, lake_dir: Path | None = None) -> str | None:
    from catalyx.store import lake
    df = lake.read_table("portfolio_holding", lake_dir=lake_dir)
    if df.empty or "portfolio_id" not in df.columns:
        return None
    df = df[df["portfolio_id"] == portfolio_id]
    return str(sorted(df["run_id"].unique())[-1]) if not df.empty else None


def _model_holdings(portfolio_id: str, run_id: str, lake_dir: Path | None = None) -> list[dict]:
    from catalyx.store import lake
    df = lake.read_table("portfolio_holding", lake_dir=lake_dir)
    if df.empty:
        return []
    df = df[(df["portfolio_id"] == portfolio_id) & (df["run_id"] == run_id)]
    return df.sort_values("rank_in_portfolio").to_dict("records")


def _crowding_excluded(portfolio_id: str, run_id: str,
                       lake_dir: Path | None = None) -> dict[str, dict]:
    """sector_id → crowding facts, for names the ENTRY bar ALONE kept out of the model book.

    Rebuilt rather than read from the lake because it is a statement about sectors that are
    NOT in the book, so there is no holding row to carry it. The rebuild is the same pure
    dataframe pass over the same recorded run (~0.3s, no network) and is therefore exact, not
    an approximation of what the persisted book was built from.

    FAILURE IS BIASED TOWARD THE SALE, DELIBERATELY. Any error returns {}, which restores the
    pre-v11.1 behaviour: the overweight TRIM fires. A mechanism whose job is to CANCEL a sell
    must fail toward selling — a silent exception that quietly keeps a position would be the
    same class of bug as a fallback to an unbuyable ticker.
    """
    try:
        from catalyx.execution.portfolio import build_model_holdings
        res = build_model_holdings(portfolio_id, run_id, persist=False, lake_dir=lake_dir)
    except Exception:                                          # pragma: no cover - defensive
        return {}
    return {e["sector_id"]: e for e in (res.get("crowding_excluded") or [])
            if e.get("sector_id")}


def _snapshot_ranks(run_id: str, lake_dir: Path | None = None) -> dict[str, int]:
    """Composite rank across the whole scored universe for one run.

    NOT the same thing as `rank_in_portfolio`: the model book ranks only the ~10 names it
    selected, while the calibration buckets (top3 / mid / rest) were measured on the composite
    ranking of every investable sector. Feeding a portfolio rank into a bucket built on the
    universe rank would silently compare two different orderings.
    """
    from catalyx.store import lake
    df = lake.read_table("sector_snapshot", lake_dir=lake_dir)
    if df.empty or "rank" not in df.columns:
        return {}
    df = df[df["run_id"] == run_id]
    return {str(r["sector_id"]): int(r["rank"]) for _, r in df.iterrows()
            if r["rank"] == r["rank"]}


def _snapshot_scores(run_id: str, lake_dir: Path | None = None) -> dict[str, dict]:
    """`composite_z` and the display composite per sector for one run.

    `composite_z` is the unit every selection floor is written in and the only one comparable
    across runs (a pre-schema-1.4 composite is on the old absolute scale). The swap hurdle and
    the selection margin both need the gap in THAT unit, not in display points.
    """
    from catalyx.store import lake
    df = lake.read_table("sector_snapshot", lake_dir=lake_dir)
    if df.empty:
        return {}
    df = df[df["run_id"] == run_id]
    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        z = r.get("composite_z") if "composite_z" in df.columns else None
        c = r.get("composite") if "composite" in df.columns else None
        out[str(r["sector_id"])] = {
            "composite_z": float(z) if z is not None and z == z else None,
            "composite": float(c) if c is not None and c == c else None,
        }
    return out


def _best_moves_floor() -> float | None:
    """The book's own entry floor on composite_z — `min_composite_z` of the catalyx profile."""
    from catalyx.execution.portfolio import load_profile
    f = (load_profile("catalyx").get("construction") or {}).get("min_composite_z")
    return None if f is None else float(f)


def _best_moves_drivers(sectors: set) -> dict:
    """{sector: {driver, …}} the way the standing cap reads it (`movement_repo.cap_check`): a HELD
    line by its effective attribution — including a driver the review deliberately declined —
    and a name not held yet by the structural drivers of its sector study. One definition of
    "cluster", so the risk price is judged against the same budget the table warns about."""
    from catalyx.execution import portfolio
    from catalyx.store import movement_repo as mr
    from catalyx.store import structural_catalyst_repo as scr
    smap, merged = portfolio._sector_catalyst_map(), scr.merged_map()
    structural = {str(d.get("id")) for d in scr._load_all() if d.get("id")}
    held: dict = {}
    for m in mr.load_all():
        if m["action"] in mr._BUY_ACTIONS:
            held.setdefault(str(m.get("sector_id") or ""), m)
    out = {}
    for sid in sectors:
        mov = held.get(sid)
        raw = ({merged.get(a["catalyst_id"], a["catalyst_id"]) for a in mr.effective_attribution(mov)[0]}
               if mov is not None else {merged.get(c, c) for c in (smap.get(sid) or [])})
        out[sid] = raw & structural
    return out


def _best_moves_eligible(run_id: str, cfg: dict, investable: set,
                         lake_dir: Path | None = None) -> set:
    """Names a BUY could reach today (v15.3): investable, through the CA entry gate (an IMPUTED
    CA is not judged), in a regime and maturity `buy_if` admits. The vehicle is checked by the
    caller. Same gate the model book applies — the column chooses inside the mandate."""
    from catalyx.execution.portfolio import load_profile
    from catalyx.store import lake
    df = lake.read_table("sector_snapshot", lake_dir=lake_dir)
    if df.empty:
        return set()
    df = df[df["run_id"] == run_id]
    gate = float(((load_profile("catalyx").get("construction") or {}).get("entry_gate") or {})
                 .get("min_catalyst_alignment", 0.0) or 0.0)
    buy = cfg.get("buy_if", {}) or {}
    out = set()
    for _, r in df.iterrows():
        sid = str(r["sector_id"])
        if investable and sid not in investable:
            continue
        ca, imp = r.get("catalyst_alignment"), r.get("ca_imputed")
        if not (imp is True or (ca == ca and ca is not None and float(ca) >= gate)):
            continue
        if _in(r.get("regime_state"), buy.get("regime_not")) or \
                _in(r.get("narrative_maturity"), buy.get("maturity_not")):
            continue
        out.add(sid)
    return out


def rank_size_spearman(rows: list[dict]) -> float | None:
    """Does the ORDER of the ranking show up in the SIZE of the positions? (v13.3)

    Reported because it is routinely assumed and was, on 2026-09-01, −0.03: the largest target
    was the 4th name and the top-ranked one was 5th by size. That is CORRECT by design — λ=0
    means the ranking sizes nothing and the risk budget sizes everything — but the table's own
    reasons said "rank 4, not held" beside a euro figure rank had no part in. A number that is
    assumed and never printed is the kind that goes wrong quietly.
    """
    pairs = [(_clean_rank(r.get("score_rank")), float(r.get("target_pct") or 0.0))
             for r in rows if float(r.get("target_pct") or 0.0) > 0]
    pairs = [(a, b) for a, b in pairs if a is not None]
    n = len(pairs)
    if n < 3:
        return None

    def _ranks(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        rk = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                rk[order[k]] = avg
            i = j + 1
        return rk

    a, b = _ranks([p[0] for p in pairs]), _ranks([p[1] for p in pairs])
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return round(num / den, 3) if den else None


def selection_margin(z_by_sector: dict[str, dict], selected: set[str],
                     investable: set[str] | None = None) -> dict | None:
    """How close the top-N cut actually was, in `composite_z` (v13.3).

    THE NUMBER THE WHOLE BOOK HANGS ON AND NOBODY PRINTED. With λ=0 the ranking does not size
    anything, so 100% of what the model contributes is WHICH names are inside the cut — and on
    2026-09-01 the last name in beat the first name out by 0.4 display points. A boundary that
    tight is a coin flip, and a table that never says so invites the reader to treat the cut as
    a finding. It is reported beside `swap_hurdle` on purpose: the two answer the same question
    from opposite ends — how big is the gap, and how big would it have to be to be worth paying
    for.
    """
    pool = {sid: d.get("composite_z") for sid, d in (z_by_sector or {}).items()
            if d.get("composite_z") is not None
            and (investable is None or sid in investable)}
    inside = [(z, sid) for sid, z in pool.items() if sid in selected]
    outside = [(z, sid) for sid, z in pool.items() if sid not in selected]
    if not inside or not outside:
        return None
    last_in = min(inside)
    first_out = max(outside)
    margin = round(last_in[0] - first_out[0], 4)
    return {"last_in": last_in[1], "last_in_z": round(last_in[0], 4),
            "first_out": first_out[1], "first_out_z": round(first_out[0], 4),
            "margin_z": margin,
            "note": (f"the cut was decided by {margin:+.3f} composite_z — {last_in[1]} is in, "
                     f"{first_out[1]} is out")}


def _cycle_runs(dates: list[str], n_runs: int, min_gap_days: int) -> list[str]:
    """The last `n_runs` review CYCLES among `dates` (`YYYYMMDD`, ascending).

    v4.3 collapsed two runs of the same afternoon into one observation. Same defect, one scale up:
    the four runs behind copper's SELL were 06-30, 07-05, 07-28 and 08-28 — gaps of five days to a
    month — so `rank_out_consecutive: 2` meant "ten days" or "two months" depending on how busy
    the quarter had been. A threshold whose meaning depends on your working rhythm is not frozen,
    whatever section of the config it sits in. Walking from the most recent backwards keeps the
    latest reading (the one the decision is about) rather than an arbitrary anchor.
    """
    kept: list[str] = []
    last: date | None = None
    for d in reversed(dates):
        try:
            cur = date(int(d[:4]), int(d[4:6]), int(d[6:8]))
        except (ValueError, IndexError):                       # pragma: no cover - defensive
            continue
        if last is None or (last - cur).days >= min_gap_days:
            kept.append(d)
            last = cur
        if len(kept) == n_runs:
            break
    return list(reversed(kept))


def _rank_streaks(out_of_top: int, n_runs: int = 4, lake_dir: Path | None = None,
                  min_gap_days: int = 21) -> dict[str, dict]:
    """Per sector: the out-of-cut streak AND how many of those runs actually scored it.

    ONE RUN PER REVIEW CYCLE. Run ids are `run_<YYYYMMDD>_<HHMMSS>`, so re-running the pipeline
    twice in an afternoon used to write two "consecutive runs" — and `rank_out_consecutive: 2`
    meant a single day of iteration could manufacture a SELL by itself. Runs closer together than
    `min_gap_days` are one cycle; see `_cycle_runs` for why the day-level fix was not enough.
    """
    from catalyx.store import lake
    df = lake.read_table("sector_snapshot", lake_dir=lake_dir)
    if df.empty or "rank" not in df.columns:
        return {}
    by_date: dict[str, str] = {}
    for rid in sorted(df["run_id"].unique()):
        by_date[str(rid)[4:12]] = str(rid)                  # later run of a date wins
    dates = _cycle_runs(sorted(by_date), n_runs, min_gap_days)
    runs = [by_date[d] for d in dates]
    df = df[df["run_id"].isin(runs)]
    out = {}
    for sid, grp in df.groupby("sector_id"):
        grp = grp.set_index("run_id").reindex(runs)
        hist = [None if r != r else int(r) for r in grp["rank"]]     # NaN → None
        streak = rank_out_streak(hist, out_of_top)
        out[str(sid)] = {"streak": streak, "history": hist,
                         # Calendar span the streak actually covers, so the reason can say it.
                         "streak_days": _span_days(dates[-streak:]) if streak else 0,
                         **rank_coverage(hist)}
    return out


def _span_days(dates: list[str]) -> int | None:
    """Calendar days between the first and last `YYYYMMDD` in the list."""
    if len(dates) < 2:
        return 0
    try:
        first, last = dates[0], dates[-1]
        return (date(int(last[:4]), int(last[4:6]), int(last[6:8]))
                - date(int(first[:4]), int(first[4:6]), int(first[6:8]))).days
    except (ValueError, IndexError):                           # pragma: no cover - defensive
        return None


def _data_age_by_sector() -> dict[str, dict]:
    """{sector_id: {status, label}} — how old is the evidence behind this sector's score.

    WHY (plan v5 §2 E1). The review printed 41 overdue indicators in §8 and, 130 lines earlier,
    ordered €1,020 into `luxury_goods` — whose `catalyst_alignment` of 70.4 IS the intensity of
    `struct_china_luxury_recovery`, two of whose indicators had not been observed since
    2025-09-30. Both facts were on the page; neither knew about the other.

    A sector inherits the WORST status among its active catalysts: a book is only as current as
    the stalest driver it is paying for. `uncatalyzed` sectors return nothing — there is no
    structural catalyst whose age could qualify the row, and inventing one would be worse than
    the blank.

    This QUALIFIES a row. It never votes: `decide_action` does not read it, by design. The
    freshness doctrine is that stale data is a reason to RE-VERIFY, not to stop acting — turning
    a maintenance failure into a trading prohibition is the conservative bias Phase C exists to
    fight.
    """
    try:
        from catalyx.execution import portfolio
        from catalyx.scorer import freshness
        from catalyx.store import structural_catalyst_repo as scr

        by_cat = freshness.by_catalyst()
        # `resolve()` reloads every YAML on each call; the map is the same read done once. Per
        # sector × per catalyst that was 2.9s, against 53ms for the whole freshness audit.
        merged = scr.merged_map()
        order = {"fresh": 0, "stale": 1, "blind": 2}
        out: dict[str, dict] = {}
        for sid, cids in (portfolio._sector_catalyst_map() or {}).items():
            # Follow a `merged_into` first: the surviving catalyst holds the live indicators, and
            # reading the absorbed one's file would report a staleness nobody can ever fix.
            rows = [by_cat.get(scr.resolve(c, merged) or c) for c in (cids or [])]
            rows = [r for r in rows if r]
            if rows:
                out[str(sid)] = max(rows, key=lambda r: order.get(r["status"], 0))
        return out
    except Exception:                                          # pragma: no cover - defensive
        return {}


def _catalyst_status(catalyst_ids: list[str]) -> str | None:
    """Worst status among a position's driving catalysts (worst binds — one invalidated driver
    is enough to make the position's thesis a different question)."""
    from catalyx.store import catalyst_repo, structural_catalyst_repo

    order = {"invalidated": 0, "dormant": 1, "archived": 2, "merged": 3, "active": 9}
    worst, worst_rank = None, 99
    for cid in catalyst_ids or []:
        rec = None
        try:
            rec = structural_catalyst_repo.get_catalyst(cid) or catalyst_repo.get_catalyst(cid)
        except Exception:
            rec = None
        st = (rec or {}).get("status")
        if st and order.get(st, 9) < worst_rank:
            worst, worst_rank = st, order.get(st, 9)
    return worst


def _investable_sectors() -> set[str]:
    """Sectors investable TODAY. Delegates to run_state so there is one definition of the word."""
    try:
        from catalyx.store import run_state
        return run_state.investable_sectors()
    except Exception:
        return set()


def _buyable_vehicle(sector_id: str) -> str | None:
    """The UCITS ticker for a sector TODAY, or None. Resolved at table time, never read off the
    stored `portfolio_holding.primary_etf` — that ticker is frozen at the run that built the
    model book, so a universe cut leaves the € recommendation pointing at a vehicle the account
    cannot buy. See `snapshot_repo.primary_etf`."""
    try:
        from catalyx.store import snapshot_repo
        return snapshot_repo.primary_etf(sector_id)
    except Exception:
        return None


def _substitutes(run_id: str | None, exclude: set[str], investable: set[str], n: int,
                 lake_dir: Path | None = None) -> list[dict]:
    """The next `n` investable, buyable sectors by composite that the book does not already hold.

    A name dropped for being unbuyable should not shrink the book — the model asked for
    `max_positions` lines and the universe has 26 investable sectors to fill them from. Taking
    the substitute from the same run's `sector_snapshot` keeps the selection rule identical to
    `portfolio.build_model_holdings` (composite descending, one vehicle each); only the entry
    point differs. Without this, a universe cut silently converts a 10-name book into a 6-name
    one and calls the concentration a decision.
    """
    from catalyx.store import lake

    if not run_id or n <= 0:
        return []
    df = lake.read_table("sector_snapshot", lake_dir=lake_dir)
    if df.empty or "run_id" not in df.columns:
        return []
    df = df[df["run_id"] == run_id]
    if df.empty or "composite" not in df.columns:
        return []
    out, seen_etf = [], set()
    for _, r in df.sort_values("composite", ascending=False).iterrows():
        if len(out) >= n:
            break
        sid = str(r["sector_id"])
        if sid in exclude or (investable and sid not in investable):
            continue
        etf = _buyable_vehicle(sid)
        if not etf or etf in seen_etf:
            continue
        seen_etf.add(etf)
        out.append({"sector_id": sid, "primary_etf": etf,
                    "composite": float(r["composite"]) if r["composite"] == r["composite"] else 0.0,
                    "narrative_maturity": r.get("narrative_maturity"),
                    "regime_state": r.get("regime_state") or "intact",
                    "substituted": True})
    return out


def _vix_last() -> float | None:
    try:
        from catalyx.data import prices
        df = prices.read(["^VIX"], "2000-01-01", date.today().isoformat())
        col = df["^VIX"].dropna() if df is not None and "^VIX" in getattr(df, "columns", []) else None
        return round(float(col.iloc[-1]), 2) if col is not None and len(col) else None
    except Exception:
        return None


def _shortfall_history(lake_dir: Path | None = None, n_runs: int = 8) -> list[dict]:
    """One record per RECORDED run (latest run of each date), oldest first, with its shortfall.

    Deduped by date for the same reason `_rank_streaks` is: two runs on one afternoon are one
    review, and a persistence rule that counts them as two would fire a day early.
    """
    from catalyx.store import lake

    try:
        df = lake.read_table(_TABLE, lake_dir=lake_dir)
    except Exception:
        return []
    need = {"run_id", "book_total_capital_eur", "book_deployable_eur", "book_cash_actual_eur"}
    if df.empty or not need <= set(df.columns):
        return []
    # Keyed by the REVIEW date (`as_of`), not by run id: two rebalance computations on one
    # afternoon are one review, and a persistence rule that counted them as two would fire a
    # run early — the same defect v4.3 fixed in `_rank_streaks`.
    by_date: dict[str, dict] = {}
    for rid in sorted(str(x) for x in df["run_id"].unique()):
        r = df[df["run_id"] == rid].iloc[0]
        total = float(r["book_total_capital_eur"] or 0.0)
        cash = float(r["book_cash_actual_eur"] or 0.0)
        as_of = str(r["as_of"])[:10] if "as_of" in df.columns and r.get("as_of") is not None \
            else f"{rid[4:8]}-{rid[8:10]}-{rid[10:12]}"
        by_date[as_of] = {"run_id": rid, "as_of": as_of,
                          "deployed_eur": round(total - cash, 2),
                          "deployable_eur": float(r["book_deployable_eur"] or 0.0),
                          "shortfall_pp": shortfall_pp(total - cash, r["book_deployable_eur"],
                                                       total)}
    return [by_date[d] for d in sorted(by_date)[-n_runs:]]


def _benchmark_return_pct(since: str | None, until: str | None,
                          ticker: str = "SPY") -> float | None:
    """The benchmark's own return over [since, until]. None when the window is unusable.

    SPY is the measuring stick every model book already reports against — it is not a purchase
    recommendation (it is not UCITS), it is the market return the idle cash declined.
    """
    if not since:
        return None
    try:
        from catalyx.data import prices
        df = prices.read([ticker], str(since)[:10],
                         str(until or date.today().isoformat())[:10])
        col = df[ticker].dropna() if df is not None and ticker in getattr(df, "columns", []) else None
        if col is None or len(col) < 2 or not float(col.iloc[0]):
            return None
        return round((float(col.iloc[-1]) / float(col.iloc[0]) - 1.0) * 100.0, 2)
    except Exception:
        return None


def _model_return_pct(since: str | None, until: str | None, model_id: str = "catalyx",
                      lake_dir: Path | None = None) -> float | None:
    """The model book's own return over [since, until], from the persisted NAV curve.

    This is the counterfactual that matches the decision (plan v5 F3): the question the cash row
    is on trial for is not "should I have been in the market?" but "should I have executed THIS
    table?", and the `catalyx` book is exactly that policy. No price fetch — the curve is already
    in the lake, rebuilt by `post_run.sh` every run.

    ONE MODE, ALWAYS. `portfolio_nav` holds backtest / live / forward rows under the SAME
    portfolio_id, at overlapping dates and on different NAV bases (this book: ~124 backtest vs
    ~103 live). Reading the window without pinning the mode took the first row from one series
    and the last from another and reported **−16.88%** where `live` returned **+0.20%** — a
    €1,179 "saving" that never happened. It is the same defect v3.5 fixed in `portfolio_compare`;
    the fix has to be repeated at every read, because the table cannot express the constraint.
    `live` is the record, `backtest` is reference-only, and MIXING them is never right.
    """
    from catalyx.store import lake

    try:
        df = lake.read_table("portfolio_nav", lake_dir=lake_dir)
        if df.empty or not since:
            return None
        d = df[df["portfolio_id"] == model_id].copy()
        d["date"] = d["date"].astype(str).str[:10]
        d = d[(d["date"] >= str(since)[:10])
              & (d["date"] <= str(until or date.today().isoformat())[:10])]
        if "mode" in d.columns:
            for mode in ("live", None, "backtest"):
                sub = d[d["mode"].isna()] if mode is None else d[d["mode"] == mode]
                if len(sub) >= 2:
                    d = sub
                    break
            else:
                return None
        d = d.sort_values("date")
        if len(d) < 2 or not float(d["nav"].iloc[0]):
            return None
        return round((float(d["nav"].iloc[-1]) / float(d["nav"].iloc[0]) - 1.0) * 100.0, 2)
    except Exception:                                          # pragma: no cover - defensive
        return None


def _prior_run(run_id: str | None, lake_dir: Path | None = None) -> tuple[str | None, list[dict]]:
    """(run_id, rows) of the most recent recorded run STRICTLY BEFORE `run_id`."""
    from catalyx.store import lake

    try:
        df = lake.read_table(_TABLE, lake_dir=lake_dir)
    except Exception:
        return None, []
    if df.empty or "run_id" not in df.columns:
        return None, []
    prior = sorted(str(x) for x in df["run_id"].unique() if not run_id or str(x) < str(run_id))
    if not prior:
        return None, []
    rid = prior[-1]
    return rid, df[df["run_id"] == rid].to_dict("records")


def _overrides_for_run(run_id: str | None, lake_dir: Path | None = None) -> list[dict]:
    from catalyx.store import lake

    try:
        df = lake.read_table(_OVERRIDE_TABLE, lake_dir=lake_dir)
    except Exception:
        return []
    if df.empty or "run_id" not in df.columns or not run_id:
        return []
    return df[df["run_id"] == run_id].to_dict("records")


def _unrecorded_defers(lake_dir: Path | None = None) -> list[dict]:
    """Every auto-logged DEFER already on the clock, across all runs — the standing decisions a
    new run must not re-log (plan v5 E3)."""
    from catalyx.store import lake

    try:
        df = lake.read_table(_OVERRIDE_TABLE, lake_dir=lake_dir)
    except Exception:                                          # pragma: no cover - defensive
        return []
    if df.empty or "author" not in df.columns:
        return []
    return df[df["author"] == "unrecorded"].to_dict("records")


def _positions_by_sector(rows: list[dict]) -> dict[str, dict]:
    """Merge a sector's LOTS into one position. Keyed by sector, because the table is.

    `movement_repo.positions()` is keyed by ETF and must stay that way — two listings of one
    fund are two lots with their own quantity, cost basis and quoting currency, and NAV has to
    mark each on its own price. But the rebalance table asks a SECTOR question ("how much of the
    book is in semis"), and it used to answer it with `{p["sector_id"]: p for p in ...}`, where a
    second lot in the same sector silently overwrites the first — last one wins, the other lot's
    money simply vanishes from the comparison.

    That went live on 2026-09-01. The semis add was executed on SEC0.DE (the Xetra EUR line)
    while the held lot sat on SEMI.L (the LSE GBP line) — the same fund, one economic position.
    The table read `act% 4.6` from the SEMI.L lot alone and printed `ADD €564` for money that was
    already in the sector: a recommendation to buy the same exposure twice. Nothing else was
    wrong — the fill, the movement file and `positions()` were all correct.

    Merge rules, and each errs on the side of ACTING rather than of a comfortable number:
      • `invested_eur` sums. That is the whole point.
      • `market_value_eur` sums only if EVERY lot has one, else None. Adding a marked lot to a
        cost-basis lot would be the currency-mixing bug this project already shipped once (v2.25);
        None is a fact the caller handles (it falls back to summed invested), a blend is not.
      • `suggested_action` and `drawdown` take the MOST SEVERE across lots. A merge that hides a
        lot's exit signal behind a healthier lot would be a mechanism that cancels a sale, and
        those must fail toward the sale.
      • `etf` names the lot the table should trade: the one matching today's `primary_etf` if any
        lot does, else the largest by cost basis. Naming a vehicle nobody would buy today is how
        a stale universe reaches the order ticket.
      • `attribution` concatenates — `_catalyst_status` reads the union, and a driver behind any
        lot is a driver behind the position.
    """
    from catalyx.store.snapshot_repo import primary_etf

    by_sid: dict[str, list[dict]] = {}
    for r in rows:
        sid = r.get("sector_id")
        if sid:
            by_sid.setdefault(sid, []).append(r)

    dd_sev = {"exit": 3, "reduce": 2, "warn": 1, "clear": 0, None: -1}
    out: dict[str, dict] = {}
    for sid, lots in by_sid.items():
        if len(lots) == 1:
            out[sid] = lots[0]
            continue
        merged = dict(max(lots, key=lambda x: float(x.get("invested_eur") or 0.0)))
        merged["invested_eur"] = sum(float(x.get("invested_eur") or 0.0) for x in lots)

        mvs = [((x.get("tax") or {}).get("market_value_eur")) for x in lots]
        tax = dict(merged.get("tax") or {})
        tax["market_value_eur"] = (sum(float(v) for v in mvs)
                                   if mvs and all(v is not None for v in mvs) else None)
        merged["tax"] = tax

        acts = [x.get("suggested_action") for x in lots if x.get("suggested_action") in PRECEDENCE]
        merged["suggested_action"] = (min(acts, key=PRECEDENCE.index) if acts
                                      else next((x.get("suggested_action") for x in lots
                                                 if x.get("suggested_action")), None))

        dds = [x.get("drawdown") for x in lots if x.get("drawdown")]
        if dds:
            merged["drawdown"] = max(dds, key=lambda d: dd_sev.get(d.get("tier"), -1))

        try:
            pe = primary_etf(sid)
        except Exception:
            pe = None
        match = next((x for x in lots if x.get("etf") == pe), None)
        merged["etf"] = (match or merged).get("etf")

        attrs: list = []
        for x in lots:
            attrs.extend(x.get("attribution") or [])
        if attrs:
            merged["attribution"] = attrs

        merged["lots"] = [{"etf": x.get("etf"), "invested_eur": x.get("invested_eur")}
                          for x in lots]
        out[sid] = merged
    return out


def build(strategy: str = "catalyx", cfg: dict | None = None, run_id: str | None = None,
          exit_fn=None, lake_dir: Path | None = None, total_capital: float | None = None,
          expected_fn=None, overrides_fn=None) -> dict:
    """Target-vs-actual with a rule action and an after-tax net edge per sector."""
    from catalyx.scorer import calibration, exit_watcher

    cfg = cfg or weights.rebalance_rules()
    total_capital = total_capital if total_capital is not None else \
        (weights.total_capital_eur() or 0.0)
    run_id = run_id or _latest_run_id(strategy, lake_dir=lake_dir)
    model = _model_holdings(strategy, run_id, lake_dir=lake_dir) if run_id else []

    exit_fn = exit_fn or (lambda: exit_watcher.assess(persist=False, lake_dir=lake_dir))
    book = exit_fn()
    positions = _positions_by_sector(book.get("positions", []) or [])

    # The model book is read from the LAST RECORDED RUN, which may predate a taxonomy change or
    # a universe cut. A BUY recommendation for a sector that is no longer buyable is worse than
    # no recommendation: it looks actionable. Two independent reasons a model name can fail here,
    # and BOTH are checked — the sector-level `investable` flag lives in the taxonomy and can lag
    # `etf_universe.yaml`, which is why 2026-08-28 printed `BUY biotech €891` against IBB for a
    # sector the taxonomy called investable. Held positions always stay on the table.
    investable = _investable_sectors()
    dropped, no_vehicle = [], []
    for m in model:
        sid = m["sector_id"]
        if sid in positions:
            continue
        if investable and sid not in investable:
            dropped.append(sid)
        elif _buyable_vehicle(sid) is None:
            dropped.append(sid)
            no_vehicle.append(sid)
    dropped_weight_pct = round(sum(float(m.get("weight_pct") or 0.0) for m in model
                                   if m["sector_id"] in dropped), 2)
    dropped_weights = sorted((float(m.get("weight_pct") or 0.0) for m in model
                              if m["sector_id"] in dropped), reverse=True)
    model = [m for m in model if m["sector_id"] not in dropped]

    # SUBSTITUTE before rescaling: a dropped name's weight goes to the next investable, buyable
    # sector by composite, not to the incumbents. Only what substitution cannot cover is
    # rescaled (`close_target_weights`), so a universe cut cannot masquerade as a conviction
    # increase in the six names that happened to survive it.
    subs = _substitutes(run_id, {m["sector_id"] for m in model} | set(positions), investable,
                        n=len(dropped), lake_dir=lake_dir)
    for w, s in zip(dropped_weights, subs):
        s["weight_pct"] = w
    model = model + subs

    # Re-rank the book that ACTUALLY exists. `rank_in_portfolio` is the model's own ordering and
    # the `add_if`/`buy_if` ceilings read it as "does the model still call this a leader". After
    # removing 4 of 10 names those stored ranks describe a book nobody holds — cybersecurity at
    # "rank 6 of 10" is rank 4 of the 6 that survived. Ranks are re-derived on the same key
    # `portfolio.build_model_holdings` selected on — `composite_z` where the run carries it,
    # not the 1-decimal display composite. See `sector_scorer.rank_key`.
    from catalyx.scorer.sector_scorer import rank_key
    model.sort(key=rank_key(model))
    for i, m in enumerate(model, 1):
        m["rank_in_portfolio"] = i

    # Close the book: rescale what substitution could not replace, re-cap per position.
    # The re-cap runs on DEPLOYED-base weights, so it needs the cap on that base too (v13.2) —
    # applying the total-capital ceiling here would undo the fix `portfolio.cap_frac` makes.
    max_dropped = float(cfg.get("max_dropped_pct", 40.0))
    try:
        from catalyx.execution.portfolio import cap_frac, load_profile
        close_cap = cap_frac({**(load_profile(strategy).get("construction") or {}),
                              "max_position_pct": float(cfg.get("max_position_pct", 12.0))}) * 100.0
    except Exception:                                          # pragma: no cover - defensive
        close_cap = float(cfg.get("max_position_pct", 12.0))
    closed = close_target_weights([float(m.get("weight_pct") or 0.0) for m in model],
                                  close_cap, max_dropped)
    for m, w in zip(model, closed["weights"]):
        m["weight_pct_effective"] = w

    # Deployment: how much of the committed capital should be at work, per rule. Counted AFTER
    # the investable filter — a leader we cannot buy is not a reason to deploy more capital.
    depl = cfg.get("deployment", {}) or {}
    intact_rank_max = int(depl.get("intact_rank_max", 8))
    n_intact = sum(1 for m in model
                   if int(m.get("rank_in_portfolio", 99)) <= intact_rank_max
                   and (m.get("regime_state") or "intact") == "intact")
    ratio = deploy_ratio(n_intact, _vix_last(), cfg)
    deployable = round(total_capital * ratio["ratio"], 2)

    exp = (expected_fn or (lambda: calibration.expected_returns(
        lake_dir=lake_dir,
        prior_windows=float((cfg.get("net_edge_gate", {}) or {})
                            .get("shrinkage_prior_windows", 6.0)))))()
    buckets = exp.get("buckets", {})

    # The gate may only BLOCK once the edge term is measured on enough independent windows.
    # Below that it prints the cost and stands aside — see apply_gate's docstring.
    try:
        ic_stat = calibration.composite_ic(lake_dir=lake_dir)
    except Exception:                                          # pragma: no cover - defensive
        ic_stat = {}
    gate = gate_status(exp, ic_stat, cfg)
    gate_evaluable = gate["armed"]

    # v13.3 — the swap hurdle. Same gate as the after-tax veto (enough windows, |IC| above the
    # floor, positive sign), because a hurdle computed off an unarmed IC would be a number
    # pretending to be a measurement. A COLUMN this cycle: it prints, it never blocks.
    try:
        disp = calibration.dispersion(lake_dir=lake_dir)
    except Exception:                                          # pragma: no cover - defensive
        disp = {}
    hurdle = swap_hurdle(gate, disp)

    data_age = _data_age_by_sector()
    # Observed round-trip cost per vehicle; an absent entry inherits the global (plan v5 G1).
    try:
        spread_by_ticker = weights.spread_bps_by_ticker()
    except Exception:                                          # pragma: no cover - defensive
        spread_by_ticker = {}
    sell_cfg = cfg.get("sell_if_any", {}) or {}
    streaks = _rank_streaks(int(sell_cfg.get("rank_out_of_top", 12)), lake_dir=lake_dir)
    score_ranks = _snapshot_ranks(run_id, lake_dir=lake_dir) if run_id else {}
    z_by_sector = _snapshot_scores(run_id, lake_dir=lake_dir) if run_id else {}
    # What the pipeline believed about each line WHEN IT WAS OPENED. `decide_action` reads level
    # (rank), regime and the exit watcher — never the DIRECTION the score has travelled since —
    # so a thesis can decay 23 composite points while its rank holds and no rule sees it. This
    # ships as a column first (v13.3); turning drift into an exit is a rule change and needs its
    # own evidence.
    try:
        from catalyx.execution.position_metrics import _entry_context, score_drift
        from catalyx.store import movement_repo as _mr
        entry_ctx = _entry_context(_mr.load_all())
    except Exception:                                          # pragma: no cover - defensive
        entry_ctx, score_drift = {}, None
    crowd_excl = _crowding_excluded(strategy, run_id, lake_dir=lake_dir) if run_id else {}

    # IS THE PERSISTED MODEL BOOK STILL THE ONE THIS CONFIG WOULD BUILD? (v13.1)
    #
    # `_crowding_excluded` rebuilds the selection with TODAY's config over the RECORDED run, and
    # its docstring called that "exact, not an approximation of what the persisted book was built
    # from". True only while the config has not moved. When it has, the two disagree in the one
    # direction that costs money: the persisted book still EXCLUDES a name (built under the old
    # bar) while the rebuild now INCLUDES it, so `crowd_excl` is empty, the v12.1 suppression
    # never fires, and the held name reads as maximally overweight against a 0% target. Lifting
    # `max_crowding` 85 → 100 did exactly that live: pharma_large_cap — the position that change
    # exists to admit — flipped to `TRIM −€556`. A config edit alone manufactured a sale.
    #
    # A model book built under different rules is not a target of zero; it is "we do not know",
    # and this file already has the verdict for that state. So a held name absent from a STALE
    # model book degrades to RE-SCORE, which moves no money, exactly as a rank-based SELL does
    # when the sector has no rank worth selling on. It resolves itself on the next score run.
    stale_cfg = None
    if model:
        try:
            from catalyx.execution.portfolio import config_version
            built = {str(m.get("config_version")) for m in model if m.get("config_version")}
            now = config_version(strategy)
            if built and now not in built:
                stale_cfg = f"model book built under config {sorted(built)[0][:8]}, now {now[:8]}"
        except Exception:                                      # pragma: no cover - defensive
            stale_cfg = None

    model_by_sector = {m["sector_id"]: m for m in model}
    sectors = list(dict.fromkeys(list(model_by_sector) + list(positions)))
    hold_zone_on = bool((cfg.get("hold_zone", {}) or {}).get("enabled", False))
    hold_zone_names: list[str] = []
    sell_need = int(sell_cfg.get("rank_out_consecutive", 2) or 2)
    # v15.4 - why each open line is held. A failure reads as "no role declared", i.e. the full
    # rule table: a mechanism that can cancel a sale must fail toward the sale.
    try:
        from catalyx.store import movement_repo as _mrr
        roles = _mrr.sector_roles()
    except Exception:                                          # pragma: no cover - defensive
        roles = {}
    diversifier_names: list[str] = []

    rows, realized_ytd = [], float(book.get("realized_ytd_eur", 0.0))
    for sid in sectors:
        m = model_by_sector.get(sid, {})
        p = positions.get(sid)
        tax_view = (p or {}).get("tax", {}) or {}
        mv = tax_view.get("market_value_eur")
        invested = (p or {}).get("invested_eur")

        # `weight_pct_effective` is the CLOSED weight (post substitution + rescale + cap); the
        # raw `weight_pct` is the frozen model weight and no longer sums to the book.
        target_eur = round(float(m.get("weight_pct_effective",
                                       m.get("weight_pct", 0.0)) or 0.0) / 100.0 * deployable, 2)
        actual_eur = round(float(mv), 2) if mv is not None else (
            round(float(invested), 2) if invested is not None else 0.0)
        denom = total_capital or 1.0
        # A HELD name the ENTRY crowding filter kept out of the model book has NO model target,
        # which is not the same statement as a target of 0% (v12.1). Reading "the model declined
        # to express an opinion" as "the model says zero" is the category error this file already
        # removed twice — an imputed CA is not a failed CA, an unmeasured cluster is not a
        # compliant one. So the target becomes "whatever is held, ceilinged at
        # `max_position_pct`": the gap closes to zero inside the ceiling, and above it the
        # ordinary overweight TRIM fires AND sizes correctly — back to the cap, not to zero.
        # Without this the suppression in `decide_action` would also disable the only mechanism
        # that trims for SIZE, and the position could grow to any share of the book unchecked.
        if sid in crowd_excl and p is not None:
            cap_eur = denom * float(cfg.get("max_position_pct", 100.0)) / 100.0
            target_eur = round(min(actual_eur, cap_eur), 2)
        # v15.2 — the same reading for a held name the 6-name cut left out while the SELL rule's
        # streak has not completed: "no opinion yet", not "zero". See scoring_weights `hold_zone`.
        in_hold_zone = (hold_zone_on and p is not None and sid not in model_by_sector
                        and sid not in crowd_excl and not stale_cfg
                        and int((streaks.get(sid) or {}).get("missing", 0) or 0) <
                        int((cfg.get("rescore_if", {}) or {}).get("missing_runs_min", 2) or 2)
                        and int((streaks.get(sid) or {}).get("streak", 0) or 0) < sell_need)
        if in_hold_zone:
            cap_eur = denom * float(cfg.get("max_position_pct", 100.0)) / 100.0
            target_eur = round(min(actual_eur, cap_eur), 2)
            hold_zone_names.append(sid)
        # v15.4 - a diversifier outside the model book has no model opinion, not a zero one: the
        # same reading as the crowding and hold-zone branches above (and the same ceiling), but
        # permanent - it does not expire with the SELL streak, because that streak is not what
        # it is held for. Inside the model book it is sized like any other line.
        is_div = p is not None and (roles.get(sid) or {}).get("role") == "diversifier"
        div_hold = (is_div and sid not in model_by_sector and not stale_cfg)
        if div_hold:
            diversifier_names.append(sid)
            if not (in_hold_zone or sid in crowd_excl):
                cap_eur = denom * float(cfg.get("max_position_pct", 100.0)) / 100.0
                target_eur = round(min(actual_eur, cap_eur), 2)
        target_pct = round(target_eur / denom * 100.0, 2)
        actual_pct = round(actual_eur / denom * 100.0, 2)
        gap_pp = round(target_pct - actual_pct, 2)
        gap_eur = round(target_eur - actual_eur, 2)

        rank = int(m["rank_in_portfolio"]) if m.get("rank_in_portfolio") is not None else None
        ctx = {
            "held": p is not None,
            "gap_pp": gap_pp,
            "rank": rank,
            "regime_state": (p or m).get("regime_state") or "intact",
            "narrative_maturity": m.get("narrative_maturity"),
            "exit_action": (p or {}).get("suggested_action"),
            "catalyst_status": _catalyst_status((p or {}).get("attribution") or []),
            "unrealized_pct": tax_view.get("unrealized_pct"),
            "reverify_required": ((p or {}).get("drawdown") or {}).get("reverify_required"),
            "drawdown_tier": ((p or {}).get("drawdown") or {}).get("tier"),
            "rank_out_streak": (streaks.get(sid) or {}).get("streak", 0),
            "rank_streak_days": (streaks.get(sid) or {}).get("streak_days"),
            "rank_missing_runs": (streaks.get(sid) or {}).get("missing", 0),
            "rank_runs": (streaks.get(sid) or {}).get("n_runs"),
            # The universe rank — what every rendered `rk` column shows and every rank-out
            # reason names. `rank` (model-book) stays internal: it exists only for book members.
            "score_rank": score_ranks.get(sid),
            # v11.1 — set only when lifting `max_crowding` would ACTUALLY have selected this
            # name. See `decide_action`: it suppresses the overweight TRIM and nothing else.
            "crowding_excluded_only": sid in crowd_excl,
            "hold_zone": in_hold_zone,
            "diversifier": is_div,
            "diversifier_hold": div_hold,
            "crowding_risk": (crowd_excl.get(sid) or {}).get("crowding_risk"),
            "max_crowding": (crowd_excl.get(sid) or {}).get("max_crowding"),
            # v13.1 — held, but absent from a model book built under a config that has since
            # changed. Not a 0% target: an unknown one. See `stale_cfg` above.
            "stale_model_cfg": (stale_cfg if (stale_cfg and p is not None
                                              and sid not in model_by_sector) else None),
        }
        decision = decide_action(ctx, cfg)
        action = decision["action"]

        rung = profit_ladder_step(ctx["unrealized_pct"], rank, cfg.get("profit_ladder")) \
            if action == "TRIM" else None
        sized = size_trade(action, gap_eur, mv, cfg,
                           ladder_fraction=(rung or {}).get("trim_fraction"))
        trade_eur = sized["trade_eur"]
        if sized.get("downgraded"):
            action, decision = "HOLD", {"action": "HOLD", "reason": sized["downgrade_reason"]}

        # Tax on the realized slice: sell proportionally out of the average cost basis.
        realized_gain, tax_eur = 0.0, 0.0
        if trade_eur < 0 and mv and invested:
            frac = min(1.0, abs(trade_eur) / float(mv))
            realized_gain = round(frac * (float(mv) - float(invested)), 2)
            if realized_gain > 0:
                from catalyx.execution import tax_engine
                tax_eur = tax_engine.compute_tax(gross_gain=realized_gain,
                                                 ytd_prior=max(0.0, realized_ytd)).tax_due

        # The vehicle is resolved NOW, not read off the frozen model row (plan v4 §0.2 D3).
        # A held position keeps the ticker actually owned — that is a fact, not a recommendation.
        # It is resolved BEFORE the costs because the spread is a property of the ticker, not of
        # the sector: one flat 20bps printed the same b/e on 7 of 8 rows (plan v5 G1).
        vehicle_today = _buyable_vehicle(sid)
        etf = (p or {}).get("etf") or vehicle_today
        vehicle_at_run = m.get("primary_etf")

        costs = cost_drag(trade_eur, tax_eur, cfg, spread_bps=spread_by_ticker.get(str(etf)))
        bucket = calibration.bucket_of(score_ranks.get(sid))
        edge = expected_edge(trade_eur, buckets.get(bucket) if bucket else None)
        net = round(edge - costs["cost_drag_eur"], 2) if edge is not None else None
        gated = apply_gate(action, trade_eur, tax_eur, net, cfg, evaluable=gate_evaluable,
                           why=gate["why"])
        if gated["final_action"] != action:
            decision = {"action": gated["final_action"], "reason": gated["gate_note"]}
            action, trade_eur = gated["final_action"], 0.0

        rows.append({
            "sector_id": sid, "etf": etf,
            "vehicle_at_run": vehicle_at_run if vehicle_at_run != etf else None,
            "rank": rank, "score_rank": score_ranks.get(sid), "bucket": bucket,
            # The catalyst score behind this row (v10 P8): the level, and the z that SIZED the
            # target via the CA conviction tilt. The row that orders euros shows the CA moving
            # them — a sector outside the model book has neither (it was not sized at all).
            "catalyst_alignment": _fnum(m.get("catalyst_alignment"), 1),
            "ca_z": _fnum(m.get("ca_z")),
            "target_pct": target_pct, "actual_pct": actual_pct, "gap_pp": gap_pp,
            "target_eur": target_eur, "actual_eur": actual_eur, "gap_eur": gap_eur,
            # Room left under the per-position ceiling once this row is at its target — the third
            # number Step 9's forced choice needs (plan v4 §4 C2), so "execute a smaller size" is
            # priced rather than guessed.
            "cap_headroom_eur": round(max(0.0, total_capital * float(cfg["max_position_pct"]) / 100.0
                                          - max(target_eur, actual_eur)), 2),
            "rule_action": action, "reason": decision["reason"],
            "trade_eur": trade_eur,
            "unrealized_pct": tax_view.get("unrealized_pct"),
            "realized_gain_eur": realized_gain,
            "expected_edge_eur": edge, "net_edge_eur": net,
            # The number the DECISION table shows (plan v4 §2 A3). expected_edge/net_edge stay in
            # the JSON and the lake — they are the calibration panel's input — but they left the
            # table, because a forecast the data cannot support does not belong beside a real tax
            # bill. This one is arithmetic on observables: friction ÷ capital actually moved.
            "breakeven_pct": breakeven_pct(costs["cost_drag_eur"], trade_eur),
            # v13.3 — the composite_z gap this row's own friction demands, from the MEASURED
            # dispersion and the MEASURED IC. `None` with verdict UNPAYABLE means no gap is
            # large enough; `None` with UNMEASURED means nobody has looked. Column, not gate.
            "dz_required": dz_required(breakeven_pct(costs["cost_drag_eur"], trade_eur), hurdle),
            "dz_hurdle_verdict": hurdle.get("verdict"),
            "composite_z": (z_by_sector.get(sid) or {}).get("composite_z"),
            # What the model believed at entry vs now. Held rows only — a BUY has no entry to
            # drift from, and a zero there would read as "the thesis is intact".
            **({k: v for k, v in (score_drift(entry_ctx.get(sid),
                                              {"composite": (z_by_sector.get(sid) or {}).get("composite"),
                                               "rank": score_ranks.get(sid)}) or {}).items()
                if k in ("composite_at_entry", "composite_now", "composite_drift", "drift_note")}
               if (p is not None and score_drift) else
               {"composite_at_entry": None, "composite_now": None, "composite_drift": None,
                "drift_note": None}),
            "gate_note": gated["gate_note"],
            "regime_state": ctx["regime_state"],
            "catalyst_freshness": ((p or {}).get("catalyst_freshness") or {}).get("status"),
            # How old is the data behind the score this row is spending on (plan v5 §2 E1)?
            # `catalyst_freshness` above only exists for OPEN positions, so a BUY — the row that
            # commits new capital — used to arrive with no qualification of its evidence at all.
            "data_age": (data_age.get(sid) or {}).get("label"),
            "data_age_status": (data_age.get(sid) or {}).get("status"),
            "exit_action": ctx["exit_action"],
            "flags": ";".join(f for f in [
                "re-verify catalyst" if ctx["reverify_required"] else None,
                "not investable today" if sid not in investable and investable else None,
                "substituted into the book" if m.get("substituted") else None,
                (f"model book named {vehicle_at_run} — not buyable today, using {etf}"
                 if vehicle_at_run and vehicle_at_run != etf and not p else None),
                "no buyable UCITS vehicle" if not etf else None,
            ] if f),
            "override": None, "override_reason": None,
            **{k: costs[k] for k in ("tax_eur", "spread_eur", "cost_drag_eur")},
        })

    try:
        n_lines = int(weights.book_shape()["n_target"])
    except Exception:                                          # pragma: no cover - defensive
        n_lines = 6
    slots = new_line_slot_pass(rows, n_lines, cfg)

    # RISK AND CONCENTRATION, before the scarcities ration anything (v10 P6/P7). It runs on the
    # finished table because a cluster's risk is a property of the POST-trade book, which does
    # not exist until the first pass has produced it — the same reason the budget and the ramp
    # are post-passes. At `risk_enforcement: "warn"` it changes no action; it reports what the
    # budget WOULD move, which is what a cycle of evidence is made of.
    try:
        from catalyx.store import movement_repo
        clusters = movement_repo.cap_check(
            [{"sector_id": r["sector_id"], "etf": r["etf"], "trade_eur": r["trade_eur"]}
             for r in rows])
    except Exception:                                          # pragma: no cover - defensive
        clusters = []
    risk_budget = cluster_risk_pass(rows, clusters, cfg)

    # Sorted AFTER the risk pass so a row the budget rewrote lands in its new precedence class.
    rows.sort(key=lambda r: (PRECEDENCE.index(r["rule_action"]) if r["rule_action"] in PRECEDENCE
                             else 9, -abs(r["trade_eur"])))
    invested_now = round(sum(r["actual_eur"] for r in rows), 2)
    # CASH first, then SLOTS. How far this ONE review may move the deployed share (v9 R1) — it
    # queues rows, never rewrites them, and the shortfall below still measures against the full
    # `deployable`. It runs BEFORE the trade budget because a slot spent on a row the ramp is
    # about to queue is a slot spent on nothing: with the budget first, six BUYs consumed the
    # allowance and pushed a TRIM out of the table that the ramp would then have let through.
    ramp = deployment_ramp_plan(rows, invested_now, deployable, total_capital, cfg)
    budget = trade_budget_plan(rows, cfg)
    buys = round(sum(r["trade_eur"] for r in rows if r["trade_eur"] > 0), 2)
    sells = round(-sum(r["trade_eur"] for r in rows if r["trade_eur"] < 0), 2)
    after = round(invested_now + buys - sells, 2)
    # THE BOOK CLOSES. `Σ target% + cash target% = 100` and `Σ actual% + cash actual% = 100`,
    # both by construction — that identity is what makes "the ideal book vs mine" a comparison
    # rather than two unrelated lists, and its absence is what let the deployment rule ask for
    # 70% while the targets summed to 45% (plan v4 §0.2 D2). Pinned by a test.
    target_total = round(sum(r["target_eur"] for r in rows), 2)
    denom = total_capital or 1.0
    cash_target = round(total_capital - target_total, 2)
    cash_actual = round(total_capital - invested_now, 2)
    book_metrics = {
        "total_capital_eur": round(total_capital, 2),
        "deployed_eur": invested_now,
        "deployed_pct": round(invested_now / denom * 100, 2),
        "deploy_ratio": ratio,
        "deployable_eur": deployable,
        "target_eur": target_total,
        "target_pct": round(target_total / denom * 100, 2),
        "cash_target_eur": cash_target,
        "cash_target_pct": round(cash_target / denom * 100, 2),
        "cash_eur": cash_actual,
        "cash_actual_pct": round(cash_actual / denom * 100, 2),
        "under_deployed_eur": round(deployable - invested_now, 2),
        # What the CASH row's action is worth: the € the table wants moved out of cash and into
        # the rows above. Reported as an ACTION, not as a footnote (plan v4 §4 C1).
        "cash_action_eur": round(cash_actual - cash_target, 2),
        "buys_eur": buys, "sells_eur": sells,
        "turnover_pct": round((buys + sells) / denom * 100, 2),
        "deployed_after_eur": after,
        "deployed_after_pct": round(after / denom * 100, 2),
        "hhi_sector": hhi([r["actual_pct"] for r in rows]),
        "n_actions": sum(1 for r in rows if r["rule_action"] != "HOLD"),
        "model": {
            "n_holdings": len(model),
            "dropped": sorted(dropped),
            "dropped_weight_pct": dropped_weight_pct,
            "no_vehicle": sorted(no_vehicle),
            "substituted": [s["sector_id"] for s in subs],
            "residual_pct": closed["residual_pct"],
            "rescale": closed["scale"],
            "incomplete": closed["incomplete"],
            # What sizing regime produced these targets. None on books built before v4.5.
            "tilt_lambda": next((float(m["tilt_lambda"]) for m in model
                                 if m.get("tilt_lambda") == m.get("tilt_lambda")     # NaN-safe
                                 and m.get("tilt_lambda") is not None), None),
            # The declared prior sizing the book by catalyst score (v10 P8). None on books
            # built before it — the tilt line then simply does not print.
            "ca_tilt_delta": next((float(m["ca_tilt_delta"]) for m in model
                                   if _fnum(m.get("ca_tilt_delta")) is not None), None),
            # The selection band and the names it put there (2026-09-02). A book whose marginal
            # slot was decided by covariance rather than by rank has to say so on its own row —
            # otherwise the reason reads "rank N" for a name the ranking did not choose.
            "selection_band_z": next((float(m["selection_band_z"]) for m in model
                                      if _fnum(m.get("selection_band_z")) is not None), None),
            "selected_by_risk": sorted(str(m["sector_id"]) for m in model
                                       if bool(m.get("selection_by_risk"))),
        },
    }

    warnings = []
    if dropped:
        why = f"{len(dropped)} model sector(s) dropped ({dropped_weight_pct:.1f}% of the model " \
              f"book): {', '.join(sorted(dropped))}."
        if no_vehicle:
            why += (f" Of those, {', '.join(sorted(no_vehicle))} pass the taxonomy's "
                    f"`investable` flag but have NO buyable UCITS vehicle in etf_universe.yaml.")
        if subs:
            why += (f" Substituted in by composite: {', '.join(s['sector_id'] for s in subs)}.")
        if closed["residual_pct"]:
            why += (f" {closed['residual_pct']:.1f}% could not be substituted and was rescaled "
                    f"across the incumbents (×{closed['scale']:.3f}).")
        why += f" The model book comes from run {run_id}; re-run the scorer to rebuild it."
        warnings.append(why)
    if closed["incomplete"] and model:
        warnings.append(f"MODEL BOOK INCOMPLETE — more than {max_dropped:.0f}% of the model "
                        f"weight was unbuyable and could not be substituted. The rescale was "
                        f"CAPPED so the survivors are not silently concentrated; the target book "
                        f"deliberately closes below the deploy rule. This is a scoring problem "
                        f"(re-score on today's universe), not a weighting one.")
    n_model_universe = len(model) + len(dropped)
    if investable and n_model_universe and abs(len(investable) - n_model_universe) > 0:
        warnings.append(f"rank-based SELL triggers read ranks recorded when the scored universe "
                        f"had a different size — 'outside the top-{sell_cfg.get('rank_out_of_top')}' "
                        f"is not the same cut across universes. Treat a rank-streak SELL as a "
                        f"prompt to re-score, not as a settled verdict.")

    # The override tally travels WITH the table on purpose: the review's summary has to state
    # what past deviations cost before it proposes a new one (plan §4.4). Guarded — a scoring
    # failure must not take down the recommendation it annotates.
    try:
        ov = (overrides_fn or (lambda: score_overrides(lake_dir=lake_dir, cfg=cfg)))()
        overrides = {"tally": ov["tally"], "total_net_eur": ov["total_net_eur"],
                     "n_scored": len(ov["scored"]), "n_pending": len(ov["pending"]),
                     "claude": ov["claude"]}
    except Exception as exc:                                   # pragma: no cover - defensive
        overrides = {"error": str(exc)}

    # The swap ledger and the partials block: the two questions the user asked the table to
    # answer — "¿renta vender?" and "¿parciales?" — neither of which needs a forecast.
    swaps = swap_ledger(rows, cfg, horizon_days=exp.get("horizon_days"),
                        z_by_sector=z_by_sector, hurdle=hurdle)
    evidence = rank_edge_evidence(exp, cfg)
    partials = partial_rungs(rows, cfg)
    # What the table does to the BOOK, beside what each row costs to trade (v11.1).
    risk_delta = book_risk_delta(rows)

    # v15.3 — every candidate trade in one currency. A COLUMN: it reads the table, moves nothing.
    # Priced against the book as HELD (before this table's rows), because the question it answers
    # is "what is the best use of the next trade slot", not "what does the rule table leave".
    best = {}
    if (cfg.get("best_moves", {}) or {}).get("enabled", False):
        try:
            from catalyx.execution import best_moves
            lam = (book_metrics.get("model") or {}).get("tilt_lambda") or 0.0
            ic_target = float((weights.portfolio_weighting() or {}).get("tilt_ic_target", 0.20)) \
                if hasattr(weights, "portfolio_weighting") else 0.20
            shape = weights.book_shape()
            sig_cs = (disp or {}).get("sigma_cs_pct")
            if sig_cs is None:
                raise ValueError("dispersion σ_cs not measured — the alpha leg cannot be priced")
            best = best_moves.build(
                rows, z_by_sector, cfg, capital=total_capital, cash_eur=cash_actual,
                n_target=n_lines, slots=int(budget.get("budget", 6)),
                neutral_eur=total_capital * float(shape["neutral_weight_pct"]) / 100.0,
                ic=float(lam) * ic_target, sigma_cs=float(sig_cs) / 100.0,
                horizon_days=int(exp.get("horizon_days") or 63),
                realized_ytd=realized_ytd, spread_bps=spread_by_ticker,
                default_bps=float(cfg.get("spread_bps", 20.0)),
                eligible=_best_moves_eligible(run_id, cfg, investable, lake_dir=lake_dir),
                vehicle_fn=_buyable_vehicle,
                ic_se=(ic_stat or {}).get("se"),
                drivers=_best_moves_drivers(set(z_by_sector)),
                budget_pct=float(weights.correlated_catalyst_cap().get(
                    "max_cluster_variance_pct", 35.0)),
                min_z=_best_moves_floor(),
                diversifiers={s for s, r in roles.items()
                              if r.get("role") == "diversifier" and s in positions})
        except Exception as exc:
            best = {"error": f"{type(exc).__name__}: {exc}"}

    # ── What NOT acting costs (plan v4 §4 C1/C3/C4) ─────────────────────────────────────────
    as_of = date.today().isoformat()
    # The current run is not in the lake yet — it persists after this returns — so it is appended
    # by hand; otherwise the persistence rule would always be one run behind the book it reads.
    # Deduped by REVIEW DATE only. Not by run_id: a review can re-use an earlier score run, and
    # excluding that run_id would silently delete the previous review from its own history — which
    # is precisely the kind of quiet reset the persistence rule exists to prevent.
    # v10 P2 — the shortfall is measured against what the model book can ACTUALLY hold. Both
    # bases are computed and both are reported; only which one the persistence rule counts is
    # configurable, and the raw gap to the deploy rule never leaves the page.
    absorb = absorbable_eur(deployable, target_total)
    basis = str((cfg.get("deployment", {}) or {}).get("shortfall_basis", "absorbable")).lower()
    short_basis_eur = absorb["absorbable_eur"] if basis == "absorbable" else deployable
    hist = [h for h in _shortfall_history(lake_dir=lake_dir) if h.get("as_of") != as_of]
    hist.append({"run_id": run_id, "as_of": as_of,
                 "shortfall_pp": shortfall_pp(invested_now, short_basis_eur, total_capital)})
    stance = cash_stance(lake_dir=lake_dir, cfg=cfg, as_of=as_of)
    short = shortfall_status(hist, cfg, stance=stance)
    short["history"] = hist[-6:]
    short["basis"] = basis
    short["absorbable"] = absorb
    # The gap to the RAW deploy rule, always, whatever the basis. A rule that can only be
    # measured one way is a rule that can be gamed by choosing the way.
    short["shortfall_vs_deployable_pp"] = shortfall_pp(invested_now, deployable, total_capital)

    # Idle SINCE the book last changed, which is a movement date, not a run date: a review that
    # recommends and is not executed does not restart the clock — that is the whole point.
    try:
        from catalyx.store import movement_repo
        movements = movement_repo.load_all()
    except Exception:                                          # pragma: no cover - defensive
        movements = []
    last_move = max((str(m.get("executed_at") or "")[:10] for m in movements), default=None) or None
    idle_since = last_move or (short.get("since") or None)
    try:
        days_idle = (date.fromisoformat(as_of) - date.fromisoformat(idle_since)).days \
            if idle_since else None
    except ValueError:                                         # pragma: no cover - defensive
        days_idle = None
    drag = cash_drag(cash_actual, _benchmark_return_pct(idle_since, as_of), idle_since, days_idle,
                     model_return_pct=_model_return_pct(idle_since, as_of, strategy,
                                                        lake_dir=lake_dir),
                     model_id=strategy)

    # The deviation nobody wrote down. Read from the filesystem, not from the narrator.
    prior_run_id, prior_rows = _prior_run(run_id, lake_dir=lake_dir)
    prior_as_of = str((prior_rows[0].get("as_of") if prior_rows else "") or "")[:10] or None
    unrecorded = unrecorded_deviations(prior_rows, movements,
                                       _overrides_for_run(prior_run_id, lake_dir=lake_dir),
                                       since=prior_as_of, until=as_of,
                                       open_defers=_unrecorded_defers(lake_dir=lake_dir))

    if short["breached"]:
        # With a ramp declared, what this review must execute is the TRANCHE, not the whole gap —
        # so the breach is answered by executing the granted rows. What the ramp does NOT do is
        # delete the shortfall: it is still measured against the full `deployable` and still
        # printed with the cash drag beside it, which is what keeps the schedule a cost and not a
        # free pass. A row queued by the ramp is already logged and priced (author `ramp`).
        warnings.append(f"DEPLOYMENT SHORTFALL — {short['note']}. "
                        + (f"The ramp answers it on a schedule: execute this review's granted "
                           f"rows ({_eur(ramp['after_eur'])}, "
                           f"{ramp['after_pct']:.0f}% deployed) and the breach is "
                           f"answered; the remaining gap closes in ~{ramp['reviews_to_full']} "
                           f"review(s). Declining a GRANTED row is still an override."
                           if ramp.get("enabled") else
                           "Declining a row IS the override; leaving the shortfall unaddressed "
                           "for another run is a decision nobody signed."))
    if risk_budget.get("n_breaching"):
        warnings.append("RISK BUDGET — " + risk_budget["note"])
    if absorb.get("capped_by_book") and basis == "absorbable":
        warnings.append(
            f"BOOK CANNOT ABSORB THE DEPLOY RULE — {absorb['why']}. The shortfall above is "
            f"measured against {_eur(absorb['absorbable_eur'])}; against the raw deploy rule it "
            f"is {short['shortfall_vs_deployable_pp']:.1f}pp. Widening the book means clearing "
            f"the conviction gate with more names, not raising the position cap.")
    if unrecorded:
        warnings.append(f"{len(unrecorded)} UNRECORDED DEVIATION(S) from run {prior_run_id}: "
                        + ", ".join(f"{u['rule_action']} {u['sector_id']}" for u in unrecorded)
                        + ". No movement, no override — logged as DEFER by `unrecorded`.")

    return {
        "as_of": as_of, "strategy": strategy, "run_id": run_id,
        "book": book_metrics, "warnings": warnings, "overrides": overrides,
        "swaps": swaps, "rank_edge_evidence": evidence, "partials": partials,
        # v13.3 — the three diagnostics that make the ranking's own economics visible.
        "swap_hurdle": hurdle, "dispersion": disp,
        "rank_size_spearman": rank_size_spearman(rows),
        "selection_margin": selection_margin(
            z_by_sector, {m["sector_id"] for m in model}, investable),
        "book_risk_delta": risk_delta,
        # What the deployment pressure rests on, when the ranking's own edge is not it (v5 F1).
        "selection_prior": selection_prior(evidence,
                                           (book_metrics.get("model") or {}).get("tilt_lambda")),
        "shortfall": short, "cash_drag": drag, "trade_budget": budget,
        "deployment_ramp": ramp,
        # Concentration in RISK beside concentration in euros (v10 P6/P7). `catalyst_cap` is the
        # notional rule that has always been checked; `risk_budget` is the variance rule now
        # running beside it. They are reported separately because where they DISAGREE is the
        # evidence that decides which one survives the cycle.
        "risk_budget": risk_budget, "catalyst_cap": clusters,
        "best_moves": best,
        "diversifiers": {s: dict(r) for s, r in roles.items()
                         if r.get("role") == "diversifier" and s in positions},
        "hold_zone": {"enabled": bool(slots.get("enabled")),
                      "held": sorted(hold_zone_names),
                      "slots": slots},
        "unrecorded": unrecorded, "prior_run_id": prior_run_id,
        "gate": gate, "composite_ic": ic_stat,
        # The entire partial-sale vocabulary, in one place, so the table can state it before the
        # verdicts instead of leaving the reader to infer what "TRIM" moved.
        "min_ticket_eur": float(cfg.get("min_ticket_eur", 150.0)),
        "fractions": {
            "sell": 1.0,
            "reduce": float((cfg.get("reduce_if_any", {}) or {}).get("reduce_fraction", 0.5)),
            "trim": "back to target",
            "ladder_trim": next((float(r.get("trim_fraction")) for r in (cfg.get("profit_ladder") or [])
                                 if r.get("trim_fraction")), None),
        },
        "calibration": {k: exp.get(k) for k in
                        ("effective_windows", "shrink", "horizon_days", "raw", "buckets", "note")},
        "rows": rows,
        "note": "Recommend-only. Python decides the ACTION from scoring_weights.yaml "
                "`rebalance_rules`; the human executes via /catalyx-open and /catalyx-close. "
                "Any deviation must be recorded as an override (lake `override_log`).",
    }


def persist(result: dict, lake_dir: Path | None = None) -> int:
    """One row per sector into lake `rebalance`, keyed by run — so a recommendation stays
    auditable: did we follow it, and did it pay?"""
    import pandas as pd

    from catalyx.store import lake

    run_id = result.get("run_id")
    if not run_id or not result.get("rows"):
        return 0
    computed_at = datetime.now(timezone.utc).isoformat()
    b = result["book"]
    # The book-level constants ride along on every row so the CASH and TOTAL lines can be
    # rebuilt from the lake alone. `review_report` renders §3 from this table without re-running
    # the engine, and a target book that does not carry its own cash side does not close.
    const = {"deploy_ratio": b["deploy_ratio"]["ratio"],
             "book_total_capital_eur": b["total_capital_eur"],
             "book_deployable_eur": b["deployable_eur"],
             "book_cash_target_eur": b["cash_target_eur"],
             "book_cash_actual_eur": b["cash_eur"],
             "book_cash_action_eur": b["cash_action_eur"],
             # WHICH sizing regime produced these targets — a target book read back six months
             # from now must say whether its dispersion was earned or assumed.
             "book_tilt_lambda": (b.get("model") or {}).get("tilt_lambda"),
             # …and whether the ranking or the covariance chose the marginal slot.
             "book_selection_band_z": (b.get("model") or {}).get("selection_band_z"),
             "book_selected_by_risk": ", ".join(
                 (b.get("model") or {}).get("selected_by_risk") or []) or None,
             # The cost of NOT acting, persisted beside the cost of acting. `review_report` and
             # the run digest render both from this table and neither may fetch a price to do it.
             "book_cash_drag_eur": (result.get("cash_drag") or {}).get("forgone_eur"),
             "book_cash_idle_since": (result.get("cash_drag") or {}).get("since"),
             "book_cash_idle_days": (result.get("cash_drag") or {}).get("days"),
             "book_bench_return_pct": (result.get("cash_drag") or {}).get("benchmark_return_pct"),
             # The counterfactual that matches the decision: the model book this table implements
             # (plan v5 F3). `verdict` is `cost` or `saved` — the label flips with the sign.
             "book_model_return_pct": (result.get("cash_drag") or {}).get("model_return_pct"),
             "book_cash_model_forgone_eur": (result.get("cash_drag") or {}).get(
                 "model_forgone_eur"),
             "book_cash_verdict": (result.get("cash_drag") or {}).get("verdict"),
             "book_shortfall_pp": (result.get("shortfall") or {}).get("shortfall_pp"),
             "book_shortfall_runs": (result.get("shortfall") or {}).get("runs_breached"),
             "book_shortfall_breached": (result.get("shortfall") or {}).get("breached"),
             # v10 P2/P3 — WHICH bar the shortfall was measured against, whether the book could
             # reach the deploy rule at all, and whether a signed cash stance is answering it.
             # A shortfall read back in six months must say what it was measured against: the
             # basis is part of the number, and a row that carries only the number is unreadable
             # the moment the basis can differ.
             "book_shortfall_basis": (result.get("shortfall") or {}).get("basis"),
             "book_shortfall_vs_deployable_pp": (result.get("shortfall") or {}).get(
                 "shortfall_vs_deployable_pp"),
             "book_absorbable_eur": ((result.get("shortfall") or {}).get("absorbable")
                                     or {}).get("absorbable_eur"),
             "book_shortfall_breach_raw": (result.get("shortfall") or {}).get("breach_raw"),
             "book_cash_stance_author": ((result.get("shortfall") or {}).get("stance")
                                         or {}).get("author"),
             "book_cash_stance_expires": ((result.get("shortfall") or {}).get("stance")
                                          or {}).get("expires_on"),
             "book_cash_stance_reason": ((result.get("shortfall") or {}).get("stance")
                                         or {}).get("reason"),
             # The schedule this table was cut to. Without it a row read back later says DEFER
             # with no trace of which scarcity queued it (v9 R1).
             "book_ramp_step_pp": (result.get("deployment_ramp") or {}).get("max_step_pp"),
             "book_ramp_allowed_after_eur": (result.get("deployment_ramp") or {}).get(
                 "allowed_after_eur"),
             "book_ramp_after_pct": (result.get("deployment_ramp") or {}).get("after_pct"),
             "book_ramp_reviews_to_full": (result.get("deployment_ramp") or {}).get(
                 "reviews_to_full"),
             # v13.3 — the tranche this review actually had, and the rows that exceed a WHOLE
             # tranche. A row read back later that says DEFER must be able to say whether it was
             # queued for next review or is unbuyable at full size in every review.
             "book_ramp_tranche_eur": (result.get("deployment_ramp") or {}).get("tranche_eur"),
             "book_ramp_headroom_eur": (result.get("deployment_ramp") or {}).get("headroom_eur"),
             "book_ramp_unfittable": (result.get("deployment_ramp") or {}).get("unfittable"),
             # v13.3 — the ranking's own economics. Persisted at book level because all three are
             # properties of the RUN, not of a sector, and the dashboard rebuilds from the lake
             # alone: a page that re-derived them would be showing a different afternoon's numbers.
             "book_swap_hurdle_verdict": (result.get("swap_hurdle") or {}).get("verdict"),
             "book_swap_hurdle_ic": (result.get("swap_hurdle") or {}).get("ic"),
             "book_swap_hurdle_ic_effective": (result.get("swap_hurdle") or {}).get(
                 "ic_effective"),
             "book_sigma_cs_pct": (result.get("dispersion") or {}).get("sigma_cs_pct"),
             "book_sigma_cs_windows": (result.get("dispersion") or {}).get("n_windows"),
             "book_rank_size_spearman": result.get("rank_size_spearman"),
             "book_selection_margin_z": (result.get("selection_margin") or {}).get("margin_z"),
             "book_selection_last_in": (result.get("selection_margin") or {}).get("last_in"),
             "book_selection_first_out": (result.get("selection_margin") or {}).get("first_out")}
    rows = [{**r, **const, "run_id": run_id, "strategy": result["strategy"],
             "as_of": result["as_of"], "computed_at": computed_at} for r in result["rows"]]
    lake.append_partition(_TABLE, pd.DataFrame(rows), {"run_id": run_id},
                          overwrite=True, lake_dir=lake_dir)
    # The swap ledger, at its own grain (v13.3). It is the only place the table states that a
    # rotation does not clear its own hurdle, and it lived exclusively in the CLI render — so the
    # dashboard showed the rows a rotation produces and never the verdict on the rotation. An
    # EMPTY ledger is written too: "this table implies no rotation" is a fact, and leaving the
    # last run's pairings standing would answer a question about today with yesterday's trades.
    lake.append_partition(_SWAP_TABLE,
                          pd.DataFrame([{**w, "run_id": run_id, "strategy": result["strategy"],
                                         "as_of": result["as_of"], "computed_at": computed_at}
                                        for w in (result.get("swaps") or [])]
                                       or [{"run_id": run_id, "strategy": result["strategy"],
                                            "as_of": result["as_of"],
                                            "computed_at": computed_at}]),
                          {"run_id": run_id}, overwrite=True, lake_dir=lake_dir)
    _persist_best_moves(result, computed_at, lake_dir=lake_dir)
    _persist_risk_price(result, computed_at, lake_dir=lake_dir)
    _log_unrecorded(result, lake_dir=lake_dir)
    _log_budget_defers(result, lake_dir=lake_dir)
    return len(rows)


def _persist_best_moves(result: dict, computed_at: str, lake_dir: Path | None = None) -> int:
    """The best-moves column at its own grain (v15.3), so its picks can be priced at 63 days like
    every other column: did the top move beat the book it would have replaced? Every priced move
    of the first round is not kept — the top five plus the plan — and `in_plan` marks the greedy
    sequence. An errored or disabled column writes NOTHING: an empty partition would read as "no
    move pays", which is a different statement from "the column could not be computed"."""
    import pandas as pd

    from catalyx.store import lake

    bm = result.get("best_moves") or {}
    if not bm or bm.get("error"):
        return 0
    plan_keys = {(s["sell"], s["buy"]) for s in bm.get("steps") or []}
    rows = []
    for rank, m in enumerate(bm.get("top") or [], 1):
        rows.append({**m, "rank": rank, "in_plan": (m["sell"], m["buy"]) in plan_keys,
                     "delta": bm.get("delta"), "erp": bm.get("erp"), "ic": bm.get("ic"),
                     "ic_se": bm.get("ic_se"), "sigma_cs": bm.get("sigma_cs"),
                     "horizon_days": bm.get("horizon_days"), "n_moves": bm.get("n_moves"),
                     "n_positive": bm.get("n_positive"),
                     "book_beta": (bm.get("variance_split") or {}).get("book_beta"),
                     "book_systematic_pct": (bm.get("variance_split") or {}).get("systematic_pct")})
    if not rows:
        return 0
    run_id = result["run_id"]
    lake.append_partition(_BEST_TABLE,
                          pd.DataFrame([{**r, "run_id": run_id, "strategy": result["strategy"],
                                         "as_of": result["as_of"], "computed_at": computed_at}
                                        for r in rows]),
                          {"run_id": run_id}, overwrite=True, lake_dir=lake_dir)
    return len(rows)


def _persist_risk_price(result: dict, computed_at: str, lake_dir: Path | None = None) -> int:
    """The RISK PRICE frontier and the diversifiers' health, for the dashboard (v15.5). One row per
    k and one per declared diversifier. An errored column writes nothing - same rule as the moves."""
    import pandas as pd

    from catalyx.store import lake

    bm = result.get("best_moves") or {}
    if not bm or bm.get("error"):
        return 0
    run_id = result["run_id"]
    base = {"run_id": run_id, "strategy": result["strategy"], "as_of": result["as_of"],
            "computed_at": computed_at}
    n = 0
    rp = bm.get("risk_price")
    if rp and rp.get("frontier"):
        before = sorted((rp.get("clusters_before") or {}).items(), key=lambda kv: -kv[1])
        rows = []
        for r in rp["frontier"]:
            rows.append({**base, "k": r["k"], "legs": r["legs"], "d_return_eur": r["d_return_eur"],
                         "cost_eur": r["cost_eur"], "vol_after_pct": r["vol_after_pct"],
                         "worst_cluster": r["worst_cluster"], "worst_share_pct": r["worst_share_pct"],
                         "within_budget": bool(r["within_budget"]),
                         "moves": "; ".join((f"{m['sell']}>{m['buy']}" if m["kind"] == "swap"
                                             else f"cash>{m['buy']}") for m in r["moves"]),
                         "budget_pct": rp["budget_pct"], "vol_before_pct": rp["vol_before_pct"],
                         "k_star": rp["k_star"], "closest_k": rp["closest_k"],
                         "unreachable": bool(rp["unreachable"]),
                         "worst_before_cluster": before[0][0] if before else None,
                         "worst_before_pct": before[0][1] if before else None})
        lake.append_partition("rebalance_risk_price", pd.DataFrame(rows), {"run_id": run_id},
                              overwrite=True, lake_dir=lake_dir)
        n += len(rows)
    dh = bm.get("diversifier_health") or []
    if dh:
        roles = result.get("diversifiers") or {}
        rows = [{**base, **d, "role_as_of": (roles.get(d["sector_id"]) or {}).get("as_of")}
                for d in dh]
        lake.append_partition("rebalance_diversifier", pd.DataFrame(rows), {"run_id": run_id},
                              overwrite=True, lake_dir=lake_dir)
        n += len(rows)
    return n


_AUTO_DEFER_AUTHORS = ("budget", "ramp")


def _retract_stale_auto_defers(run_id: str, queued: set[str],
                               lake_dir: Path | None = None) -> int:
    """Drop machine-authored DEFERs for rows this table no longer queues. Humans are untouched."""
    from catalyx.store import lake

    existing = lake.read_table(_OVERRIDE_TABLE, lake_dir=lake_dir)
    if existing.empty or not {"run_id", "author", "sector_id"} <= set(existing.columns):
        return 0
    sub = existing[existing["run_id"] == run_id]
    if sub.empty:
        return 0
    kept = sub[~sub["author"].isin(_AUTO_DEFER_AUTHORS) | sub["sector_id"].isin(queued)]
    if len(kept) == len(sub):
        return 0
    lake.append_partition(_OVERRIDE_TABLE, kept, {"run_id": run_id},
                          overwrite=True, lake_dir=lake_dir)
    return len(sub) - len(kept)


def _log_budget_defers(result: dict, lake_dir: Path | None = None) -> int:
    """Record rows this run deferred for lack of a trade slot, authored `budget` (plan v6 L4).

    Deliberately NOT `unrecorded`: that author means "nobody wrote the decision down", and a
    budget deferral is the rule working, not silence. Filing them together would fill the
    deviation tally with rows nobody chose — the exact contamination v5 built the tally to
    avoid. They are still scored like any deviation, which is the point: ~21 trading days later
    `override_edge` says what the constraint cost, so the budget itself is falsifiable.

    Logged against the CURRENT run (this run made the call), which also means next run's
    `unrecorded_deviations` finds an override for that sector and does not re-file it as silence.
    """
    # A machine-authored DEFER describes THIS table, so one left behind by an earlier cut of the
    # same score run is retracted rather than left to be scored: re-running the rebalance after a
    # rule change can grant a row a previous cut queued, and a log saying "deferred" about a row
    # the user then executes is a false record that `override_edge` would price as real. Human
    # authorship is never touched — a person's decision is not the machine's to withdraw.
    queued = {d["sector_id"] for d in
              ((result.get("trade_budget") or {}).get("deferred_rows") or [])
              + ((result.get("deployment_ramp") or {}).get("deferred_rows") or [])}
    _retract_stale_auto_defers(result["run_id"], queued, lake_dir=lake_dir)

    # A row whose decision is already on the log is not filed again. Re-running the rebalance for
    # the same score run used to append a second DEFER per queued row, and a machine author must
    # never file a verdict on a row a PERSON already answered — either way the tally would count
    # one decision twice and the override edge would be scored against a phantom.
    already = {str(r.get("sector_id")) for r in _overrides_for_run(result["run_id"],
                                                                   lake_dir=lake_dir)}
    n = 0
    for d in (result.get("trade_budget") or {}).get("deferred_rows") or []:
        if d["sector_id"] in already:
            continue
        try:
            log_override(result["run_id"], d["sector_id"], d["rule_action"], "DEFER",
                         reason=(f"No trade slot: {d['rule_action']} {d['sector_id']} "
                                 f"€{abs(d['trade_eur']):,.0f} fell outside this review's budget "
                                 f"of {(result['trade_budget'] or {}).get('budget')} trades."),
                         author="budget", chosen_trade_eur=0.0, lake_dir=lake_dir)
            already.add(d["sector_id"])
            n += 1
        except Exception:                                      # pragma: no cover - defensive
            continue
    ramp = result.get("deployment_ramp") or {}
    for d in ramp.get("deferred_rows") or []:
        if d["sector_id"] in already:
            continue
        try:
            log_override(result["run_id"], d["sector_id"], d["rule_action"], "DEFER",
                         reason=(f"Deployment ramp: {d['rule_action']} {d['sector_id']} "
                                 f"€{abs(d['trade_eur']):,.0f} queues behind this review's "
                                 f"{ramp.get('max_step_pp')}pp tranche "
                                 f"(deployed to {ramp.get('allowed_after_pct')}%)."),
                         author="ramp", chosen_trade_eur=0.0, lake_dir=lake_dir)
            already.add(d["sector_id"])
            n += 1
        except Exception:                                      # pragma: no cover - defensive
            continue
    return n


def _log_unrecorded(result: dict, lake_dir: Path | None = None) -> int:
    """Write the previous run's silent deviations into the override log as DEFERs.

    The cheapest way to be conservative is to be quiet, and until now quiet was free: an override
    existed only if the narrator chose to write one, and after three reviews with non-HOLD rows
    the log was empty. This makes the record structural — the deviation is logged by the run that
    DETECTS it, against the run that recommended it, so `override_edge` prices it ~21 trading days
    later exactly like a deliberate one.

    Idempotent by construction: `unrecorded_deviations` skips any sector that already has an
    override for that run, so a second call finds nothing left to write.
    """
    prior = result.get("prior_run_id")
    if not prior:
        return 0
    n = 0
    for u in result.get("unrecorded") or []:
        try:
            log_override(prior, u["sector_id"], u["rule_action"], "DEFER",
                         reason=u["reason"] + " Detected by the run of "
                                f"{result.get('as_of')}; nobody wrote this decision down.",
                         author="unrecorded", chosen_trade_eur=0.0, lake_dir=lake_dir)
            n += 1
        except Exception:                                      # pragma: no cover - defensive
            continue
    return n


def log_override(run_id: str, sector_id: str, rule_action: str, chosen_action: str,
                 reason: str, author: str, lake_dir: Path | None = None,
                 chosen_trade_eur: float | None = None, cfg: dict | None = None,
                 rule_trade_eur: float | None = None) -> dict:
    """Record a deviation from the rule. The ONLY sanctioned way to be conservative.

    Scored later against the action it replaced (`override_edge_eur`), which is what keeps the
    escape hatch honest: if overrides lose money on aggregate, the review's own rule is that
    Claude stops proposing them and only the user may.

    `chosen_trade_eur` is what was ACTUALLY moved (0 for a HOLD or a DEFER). It is stored rather
    than inferred because the scoring is a difference of exposures, and a deferral that quietly
    became "0" by default would score as a decision nobody made.
    """
    import pandas as pd

    from catalyx.store import lake

    ocfg = (cfg or weights.rebalance_rules()).get("overrides", {}) or {}
    allowed = ocfg.get("authors_allowed") or ["user", "claude"]
    if author not in allowed:
        raise ValueError(f"author {author!r} may not override — allowed: {', '.join(allowed)}. "
                         f"(Claude's privilege is suspended by the scored tally, not by argument: "
                         f"`rebalance overrides`.)")
    if ocfg.get("reason_required", True) and not (reason or "").strip():
        raise ValueError("an override with no reason is not an override — give the evidence that "
                         "the rule is missing")
    row = {"run_id": run_id, "sector_id": sector_id, "rule_action": rule_action,
           "chosen_action": chosen_action,
           "chosen_trade_eur": float(chosen_trade_eur or 0.0),
           # Normally the rule's ask is read back off the rebalance row at scoring time, which is
           # the right source — it is the table's own record, not the narrator's memory of it. A
           # BOOK-level override (the cash stance) has no rebalance row of its own, so it carries
           # what it declined. None keeps the old behaviour for every row-level override.
           "rule_trade_eur": (None if rule_trade_eur is None else float(rule_trade_eur)),
           "reason": reason, "author": author,
           "logged_at": datetime.now(timezone.utc).isoformat()}
    existing = lake.read_table(_OVERRIDE_TABLE, lake_dir=lake_dir)
    prior = existing[existing["run_id"] == run_id].to_dict("records") \
        if not existing.empty and "run_id" in existing.columns else []
    lake.append_partition(_OVERRIDE_TABLE, pd.DataFrame(prior + [row]), {"run_id": run_id},
                          overwrite=True, lake_dir=lake_dir)
    return row


# ── Override scoring — what the escape hatch actually cost (plan §4.3) ───────
#
# An override log that is never scored is a comment field. The point of writing the deviation
# down is that ~a month later the price says who was right, in euros, and the answer is not up
# for discussion in the next review.
#
# The comparison is a difference of EXPOSURES, not of narratives: the rule wanted to move
# `rule_trade_eur`, we moved `chosen_trade_eur`, and the gap rode the vehicle's EUR return since
# that run. Everything else about the two worlds is identical, so their P&L difference is
# exactly that gap times that return.

def override_edge(rule_trade_eur: float | None, chosen_trade_eur: float | None,
                  forward_return_pct: float | None) -> float | None:
    """€ the deviation gained (+) or lost (−) versus the action it replaced.

    Sign convention follows `trade_eur` (positive = buy). Declining a −€500 SELL is +€500 of
    retained exposure: if the vehicle then fell 10%, the override cost €50. Declining a +€500
    BUY is −€500 of exposure: the same 10% fall makes the override worth +€50.
    """
    if forward_return_pct is None:
        return None
    delta = float(chosen_trade_eur or 0.0) - float(rule_trade_eur or 0.0)
    return round(delta * float(forward_return_pct) / 100.0, 2)


def author_tally(scored: list[dict]) -> dict:
    """Per author: how many scored overrides, and their net € versus the rule."""
    out: dict[str, dict] = {}
    for r in scored:
        a = r.get("author") or "unknown"
        t = out.setdefault(a, {"n": 0, "net_eur": 0.0, "wins": 0})
        t["n"] += 1
        t["net_eur"] = round(t["net_eur"] + float(r.get("override_edge_eur") or 0.0), 2)
        t["wins"] += 1 if float(r.get("override_edge_eur") or 0.0) > 0 else 0
    return out


def claude_override_suspended(tally: dict, cfg: dict) -> dict:
    """Has Claude lost the right to propose overrides?

    Two guards, both deliberate: a MINIMUM SAMPLE (one bad call is not evidence, and suspending
    on it would just be a different superstition) and a cumulative € threshold. The suspension is
    the only self-limiting rule in the table, and it is arithmetic — nothing here reads a verdict.
    """
    rules = (cfg.get("overrides", {}) or {}).get("claude_suspended_if", {}) or {}
    t = tally.get("claude") or {"n": 0, "net_eur": 0.0}
    min_scored = int(rules.get("min_scored", 5))
    floor_eur = float(rules.get("net_edge_eur_below", 0.0))
    if int(t["n"]) < min_scored:
        return {"suspended": False, "why": f"claude: {t['n']}/{min_scored} scored overrides — "
                                           f"not enough to judge"}
    if float(t["net_eur"]) < floor_eur:
        return {"suspended": True,
                "why": f"claude overrides net €{t['net_eur']:,.0f} over {t['n']} scored — below "
                       f"€{floor_eur:,.0f}. Only the user may override until this clears."}
    return {"suspended": False,
            "why": f"claude overrides net €{t['net_eur']:,.0f} over {t['n']} scored"}


def _run_as_of(run_id: str | None, fallback: str | None = None) -> str | None:
    """The date a run was recorded — from the rebalance row if we have one, else parsed out of
    `run_YYYYMMDD_HHMMSS`."""
    if fallback:
        return str(fallback)[:10]
    m = re.search(r"(\d{4})(\d{2})(\d{2})", run_id or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _stance_benchmark(run_id: str, reb) -> str | None:
    """The vehicle a cash stance is priced against: the benchmark of the run's own strategy.

    Cash that declined the deploy rule declined a MARKET, not a name, so the benchmark is the
    honest counterfactual — and it is the same yardstick `cash_drag` already prints, which is
    what keeps the two numbers about the same decision comparable.
    """
    strategy = "catalyx"
    try:
        if reb is not None and not reb.empty and "run_id" in reb.columns:
            hit = reb[reb["run_id"] == run_id]
            if not hit.empty and "strategy" in hit.columns:
                strategy = str(hit.iloc[0]["strategy"]) or strategy
    except Exception:                                          # pragma: no cover - defensive
        pass
    try:
        from catalyx.execution import portfolio
        return str(portfolio.load_profile(strategy).get("benchmark_etf") or "SPY")
    except Exception:                                          # pragma: no cover - defensive
        return "SPY"


def score_overrides(lake_dir: Path | None = None, cfg: dict | None = None,
                    price_fn=None, as_of: str | None = None, ccy_fn=None, fx_fn=None) -> dict:
    """Score every logged override old enough to have a forward window; tally by author.

    An override younger than `score_after_trading_days` is reported as PENDING with its age, not
    scored — a five-day price difference is a coin, and letting it into the tally would make the
    suspension rule fire on noise.
    """
    from catalyx.execution import nav_engine
    from catalyx.store import lake

    cfg = cfg or weights.rebalance_rules()
    ocfg = cfg.get("overrides", {}) or {}
    need_days = int(ocfg.get("score_after_trading_days", 21))
    as_of = as_of or date.today().isoformat()

    ov = lake.read_table(_OVERRIDE_TABLE, lake_dir=lake_dir)
    if ov.empty:
        return {"as_of": as_of, "scored": [], "pending": [], "tally": {},
                "total_net_eur": 0.0,
                "claude": {"suspended": False, "why": "no overrides logged yet"},
                "note": "No override has been logged. Every non-HOLD row was either executed as "
                        "the rule said, or the deviation was not recorded — which is the one "
                        "outcome this table cannot audit."}

    reb = lake.read_table(_TABLE, lake_dir=lake_dir)
    rule_rows = {(str(r["run_id"]), str(r["sector_id"])): r
                 for _, r in reb.iterrows()} if not reb.empty else {}

    items = []
    for _, o in ov.iterrows():
        key = (str(o["run_id"]), str(o["sector_id"]))
        rr = rule_rows.get(key, {})
        # A BOOK-level override (the cash stance, v10 P3) has no rebalance row of its own: the
        # capital it declined was not declining a NAME, it was declining the market. So it is
        # priced against the benchmark, with the € it declined carried on the override itself.
        # Same difference-of-exposures arithmetic as every other row — only the vehicle differs,
        # and it differs because the decision did.
        is_stance = key[1] == _CASH_STANCE_SECTOR
        etf = _stance_benchmark(key[0], reb) if is_stance else (
            rr.get("etf") if hasattr(rr, "get") else None)
        logged_rule = o.get("rule_trade_eur")
        has_logged = logged_rule is not None and logged_rule == logged_rule   # NaN-safe
        items.append({
            "run_id": key[0], "sector_id": key[1],
            "etf": etf,
            "author": o.get("author"), "reason": o.get("reason"),
            "rule_action": o.get("rule_action"), "chosen_action": o.get("chosen_action"),
            "rule_trade_eur": (float(logged_rule) if has_logged else
                               (float(rr.get("trade_eur") or 0.0) if hasattr(rr, "get") else 0.0)),
            "chosen_trade_eur": float(o.get("chosen_trade_eur") or 0.0),
            "rule_cost_eur": float(rr.get("cost_drag_eur") or 0.0) if hasattr(rr, "get") else None,
            "from_date": _run_as_of(key[0], rr.get("as_of") if hasattr(rr, "get") else None),
        })

    tickers = sorted({i["etf"] for i in items if i["etf"]})
    px = None
    if tickers:
        start = min([i["from_date"] for i in items if i["from_date"]] or [as_of])
        fn = price_fn or nav_engine.yfinance_prices
        try:
            native = fn(tickers, start, as_of)
            px = nav_engine._eur_prices(native, start, as_of,
                                        ccy_fn or nav_engine._default_ccy_fn,
                                        fx_fn or nav_engine._default_fx_fn)
        except Exception:
            px = None

    scored, pending = [], []
    for it in items:
        ret, days = None, None
        if px is not None and it["etf"] in getattr(px, "columns", []) and it["from_date"]:
            col = px[it["etf"]].dropna()
            window = col[col.index >= it["from_date"]] if len(col) else col
            if len(window) >= 2:
                days = len(window) - 1
                first, last = float(window.iloc[0]), float(window.iloc[-1])
                ret = round((last / first - 1.0) * 100.0, 3) if first else None
        it["forward_return_pct"], it["trading_days"] = ret, days
        it["override_edge_eur"] = override_edge(it["rule_trade_eur"], it["chosen_trade_eur"], ret)
        if days is not None and days >= need_days and it["override_edge_eur"] is not None:
            scored.append(it)
        else:
            it["status"] = (f"{days or 0}/{need_days} trading days"
                            if it["etf"] else "no vehicle on the rule row — cannot price")
            pending.append(it)

    tally = author_tally(scored)
    return {"as_of": as_of, "scored": scored, "pending": pending, "tally": tally,
            "total_net_eur": round(sum(t["net_eur"] for t in tally.values()), 2),
            "claude": claude_override_suspended(tally, cfg),
            "note": "override_edge_eur = (chosen − rule exposure) × EUR return since the run. "
                    "`rule_cost_eur` (the CGT + spread the rule would have paid) is shown beside "
                    "it, never added: deferring tax is not earning it."}


# ── The rule scorecard — the table audits itself too (plan v4 §3 B4) ────────
#
# `calibration` measures the RANKING. `score_overrides` measures the DEVIATIONS. Nothing measured
# the table itself, and that asymmetry quietly made it unfalsifiable: every time a human departed
# from the rules the departure was priced, while the rules kept their authority by never being
# scored. This closes the loop — same horizon discipline, same refusal to read a verdict off a
# sample too small to have one.
#
# It never changes a threshold. `rebalance_rules.frozen` still means what it says: a scorecard is
# evidence for a config edit with a CHANGELOG line, never a mid-review adjustment.

# Which way the money moved. A rule that says SELL is RIGHT when the vehicle then falls, so the
# raw forward return has to be signed by the action before it can be read as skill.
MONEY_IN = ("ADD", "BUY")
MONEY_OUT = ("SELL", "REDUCE", "TRIM")


def action_direction(action: str) -> int | None:
    """+1 the rule put money in · −1 it took money out · None it moved none (HOLD, RE-SCORE)."""
    if action in MONEY_IN:
        return 1
    if action in MONEY_OUT:
        return -1
    return None


def _mean_se(values: list[float]) -> tuple[float | None, float | None]:
    n = len(values)
    if n == 0:
        return None, None
    mean = sum(values) / n
    if n < 2:
        return round(mean, 3), None
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return round(mean, 3), round((var ** 0.5) / (n ** 0.5), 3)


def decision_scorecard(scored: list[dict], cfg: dict | None = None) -> dict:
    """Per rule_action: n, mean forward return, the HOLD baseline, and the SIGNED rule edge.

    `scored` is one record per priced row: `{rule_action, forward_return_pct, as_of}`.

    HOLD is the baseline on purpose — the question a rule table has to answer is not "did the
    names go up" (that is beta, and the deploy ratio owns it) but "did acting beat leaving the
    book alone", which is the only comparison the table can claim credit for.
    """
    c = (cfg or {}).get("scorecard", {}) or {}
    min_n = int(c.get("min_n", 5))
    min_windows = int(c.get("min_effective_windows", 2))
    horizon = int(c.get("horizon_days", 63))

    by_action: dict[str, list[float]] = {}
    for r in scored or []:
        v = r.get("forward_return_pct")
        if v is None or v != v:
            continue
        by_action.setdefault(str(r.get("rule_action")), []).append(float(v))

    # Non-overlapping run dates — five runs inside one horizon are one observation, exactly as
    # `calibration.aggregate` counts them.
    starts = sorted({str(r.get("as_of"))[:10] for r in scored or [] if r.get("as_of")})
    effective, last = 0, None
    for st in starts:
        try:
            d = date.fromisoformat(st)
        except ValueError:
            continue
        if last is None or (d - last).days >= horizon:
            effective += 1
            last = d

    hold_mean, _ = _mean_se(by_action.get("HOLD", []))
    rows = []
    for action in PRECEDENCE:
        vals = by_action.get(action, [])
        if not vals:
            continue
        mean, se = _mean_se(vals)
        direction = action_direction(action)
        vs_hold = None if (mean is None or hold_mean is None) else round(mean - hold_mean, 3)
        edge = None if (vs_hold is None or direction is None) else round(vs_hold * direction, 3)
        if direction is None:
            verdict = "baseline" if action == "HOLD" else "no money moved"
        elif effective < min_windows:
            verdict = f"~{effective} independent window(s) — not scoreable yet"
        elif len(vals) < min_n:
            verdict = f"n={len(vals)} < {min_n} — not scoreable yet"
        elif edge is None or se is None:
            verdict = "unmeasured"
        elif abs(edge) < 2 * se:
            verdict = "noise"
        else:
            verdict = "the rule paid" if edge > 0 else "the rule cost"
        rows.append({"action": action, "n": len(vals), "mean_forward_pct": mean, "se": se,
                     "vs_hold_pp": vs_hold, "rule_edge_pp": edge, "direction": direction,
                     "verdict": verdict})
    scoreable = effective >= min_windows
    return {"rows": rows, "hold_mean_pct": hold_mean, "n_scored": sum(r["n"] for r in rows),
            "effective_windows": effective, "horizon_days": horizon,
            "min_n": min_n, "min_effective_windows": min_windows, "scoreable": scoreable,
            "note": ("rule_edge_pp = (mean forward return − the HOLD baseline) × the direction the "
                     "rule moved money, so POSITIVE always means the rule was right. "
                     + (f"~{effective} non-overlapping {horizon}d window(s) — below the "
                        f"{min_windows} this table needs before any row earns a verdict."
                        if not scoreable else
                        f"~{effective} non-overlapping {horizon}d window(s)."))}


def score_decisions(lake_dir: Path | None = None, cfg: dict | None = None, price_fn=None,
                    as_of: str | None = None, ccy_fn=None, fx_fn=None) -> dict:
    """Price every recorded rule_action over a COMPLETE forward horizon and tally by action.

    Exactly parallel to `score_overrides`, and deliberately so: the deviations and the rules they
    deviate from are now audited by the same clock, on the same lake, with the same honesty about
    sample size. An incomplete window is reported as pending, never scored — a five-day price
    difference is a coin.
    """
    from catalyx.execution import nav_engine
    from catalyx.store import lake

    cfg = cfg or weights.rebalance_rules()
    horizon = int((cfg.get("scorecard", {}) or {}).get("horizon_days", 63))
    as_of = as_of or date.today().isoformat()
    today = date.fromisoformat(as_of)

    try:
        reb = lake.read_table(_TABLE, lake_dir=lake_dir)
    except Exception:
        reb = None
    if reb is None or reb.empty:
        return {"as_of": as_of, "scored": [], "pending": [], "horizon_days": horizon,
                "scorecard": decision_scorecard([], cfg),
                "note": "no rebalance run recorded — nothing to score"}

    items = []
    for _, r in reb.iterrows():
        run_as_of = _run_as_of(str(r.get("run_id")), str(r.get("as_of") or "")[:10] or None)
        if not run_as_of:
            continue
        items.append({"run_id": str(r.get("run_id")), "sector_id": str(r.get("sector_id")),
                      "etf": r.get("etf"), "rule_action": str(r.get("rule_action")),
                      "trade_eur": float(r.get("trade_eur") or 0.0), "as_of": run_as_of})

    # No complete window → no price fetch. The scorecard is computed on every run and for months
    # the honest answer will be "not yet"; paying for a download to print that would be a fixed
    # cost on a fixed non-answer.
    horizon_cut = (today - timedelta(days=horizon)).isoformat()
    any_complete = any(i["as_of"] <= horizon_cut for i in items)
    tickers = sorted({str(i["etf"]) for i in items
                      if i["etf"] and str(i["etf"]) != "nan"}) if any_complete else []
    px = None
    if tickers:
        start = min(i["as_of"] for i in items)
        fn = price_fn or nav_engine.yfinance_prices
        try:
            native = fn(tickers, start, as_of)
            px = nav_engine._eur_prices(native, start, as_of,
                                        ccy_fn or nav_engine._default_ccy_fn,
                                        fx_fn or nav_engine._default_fx_fn)
        except Exception:                                      # pragma: no cover - defensive
            px = None

    scored, pending = [], []
    for it in items:
        try:
            end = (date.fromisoformat(it["as_of"]) + timedelta(days=horizon)).isoformat()
        except ValueError:                                     # pragma: no cover - defensive
            continue
        complete = end <= today.isoformat()
        ret = None
        if complete and px is not None and it["etf"] in getattr(px, "columns", []):
            col = px[str(it["etf"])].dropna()
            win = col[(col.index >= it["as_of"]) & (col.index <= end)] if len(col) else col
            if len(win) >= 2 and float(win.iloc[0]):
                ret = round((float(win.iloc[-1]) / float(win.iloc[0]) - 1.0) * 100.0, 3)
        it["forward_return_pct"] = ret
        it["window_complete"] = complete
        if ret is not None:
            scored.append(it)
        else:
            it["status"] = ("window open — "
                            f"{max(0, (date.fromisoformat(end) - today).days)}d to go"
                            if not complete else
                            ("no vehicle on the row — cannot price" if not it["etf"]
                             else "no usable price history in the window"))
            pending.append(it)

    return {"as_of": as_of, "horizon_days": horizon, "scored": scored, "pending": pending,
            "scorecard": decision_scorecard(scored, cfg),
            "note": "The table is now audited on the same clock as the deviations from it. This "
                    "scores the rules; it never changes one — `rebalance_rules.frozen` still "
                    "means a threshold moves by config edit and a CHANGELOG line."}


def best_moves_scorecard(scored: list[dict], cfg: dict | None = None) -> dict:
    """Did the RETURN legs of a best move predict what the two vehicles then did? (v15.5)

    Per move: predicted = d_premium + d_alpha (€, before risk and cost); realized =
    eur x (r_buy - r_sell) over the window - a buy from cash is measured against cash, which is
    how the utility priced it. `error` = realized - predicted. The risk leg is NOT scored here: it
    is a statement about variance, which one 63d path cannot confirm, and the column would only
    pretend. Grouped by `in_plan`, because the plan is what a rule would act on; ranks 2-5 are
    context. Same honesty as `decision_scorecard`: a verdict needs `min_n` moves AND
    `min_effective_windows` non-overlapping horizons, and five runs inside one window are one
    observation. `calibrated` means |mean error| < 2 se - it is a statement about the SIZE of the
    prediction, and a column earns a weight only after it is also right about the SIGN (`hit_pct`).
    """
    c = (cfg or {}).get("scorecard", {}) or {}
    min_n = int(c.get("min_n", 5))
    min_windows = int(c.get("min_effective_windows", 2))
    horizon = int(c.get("horizon_days", 63))

    starts = sorted({str(r.get("as_of"))[:10] for r in scored or [] if r.get("as_of")})
    effective, last = 0, None
    for st in starts:
        try:
            d = date.fromisoformat(st)
        except ValueError:
            continue
        if last is None or (d - last).days >= horizon:
            effective += 1
            last = d

    groups: dict[str, list[dict]] = {}
    for r in scored or []:
        if r.get("realized_eur") is None or r.get("predicted_eur") is None:
            continue
        groups.setdefault("in plan" if r.get("in_plan") else "not in plan", []).append(r)

    rows = []
    for name in ("in plan", "not in plan"):
        g = groups.get(name) or []
        if not g:
            continue
        err = [float(r["realized_eur"]) - float(r["predicted_eur"]) for r in g]
        mean_err, se = _mean_se(err)
        pred, _ = _mean_se([float(r["predicted_eur"]) for r in g])
        real, _ = _mean_se([float(r["realized_eur"]) for r in g])
        hits = sum(1 for r in g if (float(r["realized_eur"]) > 0) == (float(r["predicted_eur"]) > 0))
        if effective < min_windows:
            verdict = f"~{effective} independent window(s) - not scoreable yet"
        elif len(g) < min_n:
            verdict = f"n={len(g)} < {min_n} - not scoreable yet"
        elif mean_err is None or se is None:
            verdict = "unmeasured"
        elif abs(mean_err) < 2 * se:
            verdict = "calibrated (error inside its own noise)"
        else:
            verdict = ("under-predicted" if mean_err > 0 else "over-predicted")
        rows.append({"group": name, "n": len(g), "mean_predicted_eur": pred,
                     "mean_realized_eur": real, "mean_error_eur": mean_err, "se": se,
                     "hit_pct": round(100.0 * hits / len(g), 1), "verdict": verdict})
    return {"rows": rows, "effective_windows": effective, "horizon_days": horizon,
            "n_scored": sum(r["n"] for r in rows),
            "note": "Scores the return legs only (premium + alpha), never the risk leg. A column "
                    "stays a column until this reads calibrated AND right on sign over >= "
                    f"{min_windows} independent {horizon}d windows."}


def score_best_moves(lake_dir: Path | None = None, cfg: dict | None = None, price_fn=None,
                     as_of: str | None = None, ccy_fn=None, fx_fn=None) -> dict:
    """Price every persisted best move over a COMPLETE forward window (v15.5)."""
    from catalyx.execution import nav_engine
    from catalyx.store import lake

    cfg = cfg or weights.rebalance_rules()
    horizon = int((cfg.get("scorecard", {}) or {}).get("horizon_days", 63))
    as_of = as_of or date.today().isoformat()
    today = date.fromisoformat(as_of)
    try:
        df = lake.read_table(_BEST_TABLE, lake_dir=lake_dir)
    except Exception:
        df = None
    if df is None or df.empty:
        return {"as_of": as_of, "horizon_days": horizon, "scored": [], "pending": [],
                "scorecard": best_moves_scorecard([], cfg),
                "note": "no best-moves run recorded - nothing to score"}

    def _s(v):
        return None if v is None or v != v else str(v)

    items = []
    for _, r in df.iterrows():
        run_as_of = _run_as_of(str(r.get("run_id")), _s(r.get("as_of")))
        if not run_as_of:
            continue
        items.append({"run_id": str(r.get("run_id")), "as_of": run_as_of, "kind": _s(r.get("kind")),
                      "sell": _s(r.get("sell")), "buy": _s(r.get("buy")),
                      "sell_etf": _s(r.get("sell_etf")), "buy_etf": _s(r.get("buy_etf")),
                      "eur": float(r.get("eur") or 0.0), "in_plan": bool(r.get("in_plan")),
                      "rank": int(r.get("rank") or 0),
                      "predicted_eur": float(r.get("d_premium_eur") or 0.0)
                      + float(r.get("d_alpha_eur") or 0.0),
                      "net_eur": r.get("net_eur")})

    horizon_cut = (today - timedelta(days=horizon)).isoformat()
    tickers = sorted({t for i in items if i["as_of"] <= horizon_cut
                      for t in (i["sell_etf"], i["buy_etf"]) if t})
    px = None
    if tickers:
        start = min(i["as_of"] for i in items)
        fn = price_fn or nav_engine.yfinance_prices
        try:
            native = fn(tickers, start, as_of)
            px = nav_engine._eur_prices(native, start, as_of, ccy_fn or nav_engine._default_ccy_fn,
                                        fx_fn or nav_engine._default_fx_fn)
        except Exception:                                      # pragma: no cover - defensive
            px = None

    def _fwd(etf, a, b):
        if px is None or not etf or etf not in getattr(px, "columns", []):
            return None
        col = px[str(etf)].dropna()
        win = col[(col.index >= a) & (col.index <= b)] if len(col) else col
        if len(win) >= 2 and float(win.iloc[0]):
            return float(win.iloc[-1]) / float(win.iloc[0]) - 1.0
        return None

    scored, pending = [], []
    for it in items:
        end = (date.fromisoformat(it["as_of"]) + timedelta(days=horizon)).isoformat()
        complete = end <= today.isoformat()
        r_in = _fwd(it["buy_etf"], it["as_of"], end) if complete else None
        r_out = _fwd(it["sell_etf"], it["as_of"], end) if (complete and it["kind"] == "swap") else 0.0
        if r_in is not None and r_out is not None:
            it["realized_eur"] = round(it["eur"] * (r_in - r_out), 2)
            scored.append(it)
        else:
            it["status"] = (f"window open - {max(0, (date.fromisoformat(end) - today).days)}d to go"
                            if not complete else
                            ("no vehicle on the row - cannot price"
                             if not it["buy_etf"] else "no usable price history in the window"))
            pending.append(it)
    return {"as_of": as_of, "horizon_days": horizon, "scored": scored, "pending": pending,
            "scorecard": best_moves_scorecard(scored, cfg)}


def render_best_moves_scorecard(res: dict) -> str:
    sc = res.get("scorecard") or {}
    out = [f"BEST MOVES SCORECARD - did the return legs predict the next "
           f"{res.get('horizon_days')}d? ({res.get('as_of')})", ""]
    if not sc.get("rows"):
        out.append(f"  nothing scoreable yet: {len(res.get('pending') or [])} move(s) pending, "
                   f"no complete {res.get('horizon_days')}d window.")
        return "\n".join(out)
    out.append(f"  {'group':<12}{'n':>4}{'pred EUR':>10}{'real EUR':>10}{'error':>9}{'+-se':>7}"
               f"{'sign hit':>9}  verdict")
    for r in sc["rows"]:
        f = lambda v: "-" if v is None else f"{v:+.1f}"
        out.append(f"  {r['group']:<12}{r['n']:>4}{f(r['mean_predicted_eur']):>10}"
                   f"{f(r['mean_realized_eur']):>10}{f(r['mean_error_eur']):>9}"
                   f"{('-' if r['se'] is None else format(r['se'], '.1f')):>7}"
                   f"{r['hit_pct']:>8.0f}%  {r['verdict']}")
    out += ["", f"  {sc['note']}"]
    if res.get("pending"):
        out.append(f"  {len(res['pending'])} move(s) pending a complete window.")
    return "\n".join(out)


def render_scorecard(res: dict) -> str:
    sc = res.get("scorecard") or {}
    out = [f"RULE SCORECARD — what the table's own actions earned "
           f"({res.get('horizon_days')}d forward, {res.get('as_of')})", ""]
    if not sc.get("rows"):
        out.append(f"  nothing scoreable yet: {len(res.get('pending') or [])} row(s) pending, "
                   f"no complete {res.get('horizon_days')}d window.")
        return "\n".join(out)
    out.append(f"  {'action':<9}{'n':>4}{'mean fwd':>10}{'vs HOLD':>10}{'rule edge':>11}  verdict")
    for r in sc["rows"]:
        def _pp(v, suffix=""):
            return "—" if v is None else f"{v:+.2f}{suffix}"
        out.append(f"  {r['action']:<9}{r['n']:>4}{_pp(r['mean_forward_pct'], '%'):>10}"
                   f"{_pp(r['vs_hold_pp'], 'pp'):>10}{_pp(r['rule_edge_pp'], 'pp'):>11}  "
                   f"{r['verdict']}")
    out.append("")
    out.append(f"  {sc.get('note')}")
    if res.get("pending"):
        out.append(f"  {len(res['pending'])} row(s) pending a complete window.")
    return "\n".join(out)


def render_overrides(res: dict) -> str:
    out = [f"OVERRIDE LOG — deviations from the rule, scored ({res['as_of']})", ""]
    if not res["scored"] and not res["pending"]:
        out.append(res["note"])
        return "\n".join(out)
    if res["scored"]:
        hdr = f"{'sector':<28} {'by':<6} {'rule→chosen':<18} {'Δ€':>7} {'ret%':>7} {'edge€':>7}"
        out += [hdr, "-" * len(hdr)]
        for r in res["scored"]:
            arrow = f"{r['rule_action']}→{r['chosen_action']}"
            out.append(f"{r['sector_id'][:28]:<28} {str(r['author'])[:6]:<6} {arrow:<18} "
                       f"{r['chosen_trade_eur'] - r['rule_trade_eur']:>7.0f} "
                       f"{(r['forward_return_pct'] or 0):>7.2f} {r['override_edge_eur']:>7.0f}")
        out.append("")
    for author, t in sorted(res["tally"].items()):
        out.append(f"TALLY    {author}: {t['n']} scored · {t['wins']} beat the rule · "
                   f"net €{t['net_eur']:,.0f}")
    if res["pending"]:
        shown = "; ".join(f"{p['sector_id']} {p.get('status', '')}" for p in res["pending"][:4])
        out.append(f"PENDING  {len(res['pending'])} not yet scored ({shown})")
    out.append(f"CLAUDE   {res['claude']['why']}")
    out.append("")
    out.append(res["note"])
    return "\n".join(out)


# ── Render ───────────────────────────────────────────────────────────────────

def _eur(v) -> str:
    return "—" if v is None else f"€{v:,.0f}"


def _summary_line(res: dict) -> str:
    """The three numbers the review's executive summary MUST carry (plan §4.4): where the book
    stands against the deployment rule, how many actions the table produced, and what past
    deviations from it have cost. Generated here rather than asked of the write-up, because a
    summary that is composed by hand is a summary that quietly drops the inconvenient number."""
    b, o = res["book"], res.get("overrides") or {}
    r = b["deploy_ratio"]
    tally = o.get("tally") or {}
    if not tally:
        ov_txt = f"{o.get('n_pending', 0)} pending, none scored yet"
    else:
        ov_txt = " · ".join(f"{a} {t['n']}× net €{t['net_eur']:,.0f}" for a, t in sorted(tally.items()))
        if o.get("n_pending"):
            ov_txt += f" (+{o['n_pending']} pending)"
    return (f"SUMMARY  deployed {b['deployed_pct']:.0f}% vs rule {r['ratio']:.0%} "
            f"(floor {float(r.get('floor', 0)):.0%}) · {b['n_actions']} rule actions · "
            f"overrides: {ov_txt}")


def _render_ticket(res: dict) -> list[str]:
    """The rows to execute THIS iteration, in order, with what each one costs to place.

    The rest of the table is the model's destination and its reasoning. This block is the subset
    that survived both scarcities — the trade slots and the deployment ramp — which is the only
    part that should be typed into a broker today.
    """
    rows = res.get("rows") or []
    live = [r for r in rows
            if float(r.get("trade_eur") or 0.0) != 0.0
            and r.get("budget_state") != "deferred" and r.get("ramp_state") != "deferred"]
    queued = [r for r in rows
              if float(r.get("trade_eur") or 0.0) != 0.0
              and (r.get("budget_state") == "deferred" or r.get("ramp_state") == "deferred")]
    if not live and not queued:
        return []
    out = ["THIS ITERATION — the rows to execute now; everything below is what they rest on"]
    # Sells first: they raise the cash the buys spend, and a buy placed before its funding sale
    # is a buy on margin nobody authorised.
    for r in sorted(live, key=lambda r: (float(r.get("trade_eur") or 0.0) > 0,
                                         PRECEDENCE.index(r["rule_action"])
                                         if r["rule_action"] in PRECEDENCE else 9)):
        t = float(r["trade_eur"])
        cost = float(r.get("cost_drag_eur") or 0.0)
        out.append(f"  {('SELL' if t < 0 else 'BUY '):<5} {r['sector_id'][:30]:<30} "
                   f"{str(r.get('etf') or '—')[:9]:<9} {_eur(abs(t)):>9}"
                   f"   [{action_label(r)}]"
                   + (f" · friction {_eur(cost)}" if cost else ""))
    if not live:
        out.append("  (nothing — every money-moving row is queued behind a scarcity below)")
    if queued:
        out.append("  QUEUED to the next review, logged and priced like any deviation: "
                   + ", ".join(f"{action_label(r)} {r['sector_id']} {_eur(float(r['trade_eur']))}"
                               for r in queued))
    return out + [""]


def render(res: dict) -> str:
    b = res["book"]
    r = b["deploy_ratio"]
    out = [f"CATALYX — rebalance: pipeline target vs real book ({res['strategy']}, {res['as_of']})",
           f"run: {res.get('run_id')}", ""]
    out.append(f"CAPITAL  committed {_eur(b['total_capital_eur'])} · deployed "
               f"{_eur(b['deployed_eur'])} ({b['deployed_pct']:.0f}%) · cash {_eur(b['cash_eur'])}")
    out.append(f"RULE     deploy {r['ratio']:.0%} → {_eur(b['deployable_eur'])}   [{r['why']}]")
    mdl = b.get("model") or {}
    if mdl.get("dropped"):
        out.append(f"MODEL    {mdl['n_holdings']} names · {mdl['dropped_weight_pct']:.1f}% of the "
                   f"model book was unbuyable → {len(mdl['substituted'])} substituted, "
                   f"{mdl['residual_pct']:.1f}% rescaled (×{mdl['rescale']:.3f})")
    lam = mdl.get("tilt_lambda")
    if lam is not None:
        out.append(f"TILT     λ={lam:.2f} — "
                   + ("the model picks the NAMES; sizing is neutral (inverse-vol) until the "
                      "ranking's IC earns a tilt. Gross deployment is unchanged."
                      if lam < 0.05 else
                      f"{lam:.0%} of the model's conviction tilt applied, {1 - lam:.0%} shrunk "
                      f"toward the neutral book"))
    ca_delta = mdl.get("ca_tilt_delta")
    if ca_delta is not None:
        out.append(f"CA-TILT  δ={ca_delta:.2f} — the catalyst score sizes the book directly "
                   f"(declared prior, never disabled; caZ column below). Priced per window by "
                   f"calibration --ca-tilt; a negative tally recalibrates HOW CA is computed.")
    gap = b["under_deployed_eur"]
    if abs(gap) >= 1:
        word = "UNDER-deployed" if gap > 0 else "OVER-deployed"
        out.append(f"         {word} by {_eur(abs(gap))} vs what the rules say should be at work")
    # Friction is priced to the cent on every row below; until now the cost of leaving the cash
    # alone was priced nowhere, which is a standing thumb on the scale for doing nothing.
    drag = res.get("cash_drag") or {}
    if drag.get("idle_eur"):
        out += _wrap(drag["note"], width=96, indent=" " * 20,
                     first=f"{drag.get('headline', 'CASH'):<20}")
    short = res.get("shortfall") or {}
    if short.get("breached"):
        out.append(f"SHORTFALL {short['note']}")
    tb = res.get("trade_budget") or {}
    if tb.get("deferred") or tb.get("over_budget"):
        out += _wrap(tb["note"], width=96, indent=" " * 20, first=f"{'BUDGET':<20}")
    rp = res.get("deployment_ramp") or {}
    if rp.get("enabled"):
        out += _wrap(rp["note"], width=96, indent=" " * 20, first=f"{'RAMP':<20}")
    out.append("")

    # THE ORDER TICKET. Everything below this line is the reasoning; this is the doing. It exists
    # because the table answers two different questions at once — where the book is going, and
    # what to execute today — and a reader who conflates them either over-trades or freezes.
    out += _render_ticket(res)

    # The partial-sale vocabulary, stated ONCE before any verdict is read: three fractions are
    # the whole language (plan v4 §2 A5), and a reader who does not know them cannot tell a
    # "TRIM" that halves a line from one that shaves 4pp off it.
    rf = float((res.get("fractions") or {}).get("reduce", 0.5)) * 100
    lf = res.get("fractions", {}).get("ladder_trim")
    out.append("COLUMNS  rk = rank in this run's full ranking (— = not scored this run) · "
               "caZ = catalyst-score z among the model book's names — the declared prior that "
               "sized the target (— = outside the book, nothing sized) · "
               "b/e% = friction ÷ capital moved · "
               "Δz req = the composite_z gap that friction demands, = b/e ÷ (IC · σ_cs); "
               "∞ = the measured IC stands aside, so no gap justifies the friction. It is a "
               "COLUMN: it blocks nothing, and while it reads ∞ it says the same thing about "
               "every row — it starts telling rows apart the day the IC arms · "
               "data = age of the catalyst evidence behind "
               "the score (qualifies the row, never vetoes it) · a trailing * on the action = "
               "queued by a scarcity (trade slot or deployment ramp), logged and priced like any "
               "deviation; ** = the row exceeds a whole ramp tranche, so it is not queued for "
               "later — it is unbuyable at full size until a constant or the ask changes "
               "— the rows to execute today are the THIS ITERATION block above")
    out.append(f"SIZING   SELL = 100% of the line · CLOSE = a TRIM that takes the line to zero · "
               f"REDUCE = {rf:.0f}% · TRIM = back to target"
               + (f" (or {float(lf) * 100:.0f}% on a ladder rung)" if lf else ""))
    out.append("")

    hdr = f"{'sector':<30} {'vehicle':<9} {'rk':>4} {'caZ':>5} {'tgt%':>6} {'act%':>6} " \
          f"{'gap€':>8} {'ACTION':<8} {'trade€':>8} {'b/e%':>6} {'Δz req':>7} " \
          f"{'data':<13} reason"
    out += [hdr, "-" * len(hdr)]
    out += _render_rows(res["rows"])
    # The CASH row is a POSITION, priced like any other: the table is a closed book or it is two
    # unrelated lists. Its action is the € the rules want moved out of cash and into the rows
    # above — printed as an action, never as a footnote (plan v4 §4 C1).
    cash_act = b["cash_action_eur"]
    cash_verb = "DEPLOY" if cash_act > 0 else ("RAISE" if cash_act < 0 else "HOLD")
    out.append(f"{'CASH':<30} {'—':<9} {'—':>4} {'—':>5} "
               f"{b['cash_target_pct']:>6.1f} {b['cash_actual_pct']:>6.1f} "
               f"{-cash_act:>8.0f} {cash_verb:<8} {-cash_act:>8.0f} {'—':>6} {'—':>7} {'—':<13} "
               f"rule holds {b['cash_target_pct']:.0f}% in cash; you hold "
               f"{b['cash_actual_pct']:.0f}% — already allocated on the rows above; "
               f"declining a row IS the override, not the cash")
    out.append(f"{'TOTAL':<30} {'':<9} {'':>4} {'':>5} "
               f"{b['target_pct'] + b['cash_target_pct']:>6.1f} "
               f"{b['deployed_pct'] + b['cash_actual_pct']:>6.1f} "
               f"{0:>8.0f}")
    out.append("")
    rp = res.get("deployment_ramp") or {}
    out.append(f"ACTIONS  {b['n_actions']} non-HOLD · buys {_eur(b['buys_eur'])} · "
               f"sells {_eur(b['sells_eur'])} · turnover {b['turnover_pct']:.1f}% → deployed after "
               f"{b['deployed_after_pct']:.0f}%"
               + (f" if all of it ran; {rp['after_pct']:.0f}% after THIS iteration's tranche"
                  if rp.get("enabled") and rp.get("deferred") else ""))
    # What the LAST run asked for and never got. Read from the movements on disk, not from the
    # review's own account of itself.
    unrec = res.get("unrecorded") or []
    if unrec:
        out.append("")
        out.append(f"UNRECORDED DEVIATIONS — run {res.get('prior_run_id')} recommended "
                   f"{len(unrec)} action(s) that produced no movement and no override. Each is "
                   f"logged as a DEFER authored `unrecorded` and priced in ~21 trading days.")
        for u in unrec:
            out.append(f"  {u['rule_action']:<7}{u['sector_id']:<34}"
                       f"€{abs(u['trade_eur']):>8,.0f}  not executed, not overridden")
    c = res["calibration"]

    # What the table does to the BOOK, printed beside what its rows cost to TRADE (v11.1).
    # A column, never a gate: it prices the rotation's effect on risk and cancels nothing.
    brd = res.get("book_risk_delta")
    if brd:
        bb, aa = brd["before"], brd["after"]
        rho = ("" if brd.get("delta_mean_corr") is None else
               f" · mean ρ {bb['mean_corr']:.3f} → {aa['mean_corr']:.3f} "
               f"({brd['delta_mean_corr']:+.3f})")
        out.append("")
        out.append(f"BOOK RISK — what THIS TABLE does to the book it leaves behind "
                   f"(executable rows only; queued rows excluded)")
        out.append(f"  vol {bb['vol_pct']:.1f}% → {aa['vol_pct']:.1f}% "
                   f"({brd['delta_vol_pp']:+.1f}pp){rho}")
        if brd.get("more_correlated"):
            out.append("  ⚠ this table leaves the book MORE correlated than it found it. No row "
                       "is blocked by that — it is the cost the friction lines do not carry.")

    if res.get("best_moves"):
        from catalyx.execution import best_moves as _bm
        out.extend(_bm.render(res["best_moves"]))

    swaps = res.get("swaps") or []
    if swaps:
        ev = res.get("rank_edge_evidence") or {}
        h = swaps[0].get("horizon_days")
        out.append("")
        out.append(f"SWAP LEDGER — what each rotation costs to make, and the hurdle it must clear"
                   f"{f' over {h}d' if h else ''}")
        for w in swaps:
            out.append(f"  {str(w['from_action']):<6} {str(w['from_sector'])[:32]:<32} "
                       f"{_eur(w['moved_eur']):>9}  →  {str(w['to_action']):<4} "
                       f"{str(w['to_sector'])[:32]}")
            out.append(f"         friction €{w['friction_eur']:,.2f} "
                       f"(CGT €{w['tax_eur']:,.2f} + spread €{w['spread_eur']:,.2f})"
                       f"  →  BREAKEVEN {w['breakeven_pct']:.2f}%: {w['to_sector']} must beat "
                       f"{w['from_sector']} by that much{f' over {h}d' if h else ''}")
            # v13.3 — the bet, in the same unit as the hurdle. Without this the ledger prices
            # the ROTATION and never prices the CLAIM the rotation is making.
            gap, req, pays = w.get("dz_gap"), w.get("dz_required"), w.get("pays")
            if gap is not None:
                verdict = ("hurdle UNMEASURED" if w.get("hurdle_verdict") == "UNMEASURED"
                           else "DOES NOT PAY — the hurdle is unbounded while the ranking's "
                                "measured edge stands aside" if req is None
                           else ("PAYS" if pays else "DOES NOT PAY")
                           + f" — needs Δz ≥ {req:.2f}")
                out.append(f"         the bet: composite_z gap {gap:+.2f} "
                           f"({w['to_sector']} − {w['from_sector']})"
                           + (f", edge implied {w['edge_expected_pct']:+.2f}%"
                              if w.get("edge_expected_pct") is not None else "")
                           + f"  →  {verdict}")
        total_f = round(sum(w["friction_eur"] for w in swaps), 2)
        total_m = round(sum(w["moved_eur"] for w in swaps), 2)
        unpaired = round(b["sells_eur"] - total_m, 2)
        out.append(f"  TOTAL  {_eur(total_m)} rotated · friction €{total_f:,.2f} · "
                   f"weighted breakeven {breakeven_pct(total_f, total_m):.2f}%"
                   + (f" · {_eur(unpaired)} of the sells pairs with no buy above the "
                      f"€{float(res.get('min_ticket_eur') or 0):.0f} ticket and lands "
                      f"in cash" if unpaired >= 1 else ""))
        out.append(f"  EVIDENCE for that spread: {ev.get('line')}")
        hh = res.get("swap_hurdle") or {}
        if hh:
            out.append(f"  HURDLE {hh.get('why')}")
            d = res.get("dispersion") or {}
            if d.get("sigma_cs_pct") is not None:
                out.append(f"         σ_cs = {d['sigma_cs_pct']:.2f}% "
                           f"(median of {d['n_windows']} complete {d['horizon_days']}d "
                           f"window(s), p25 {d['p25']:.2f} · p75 {d['p75']:.2f}) — "
                           f"E[Δr] ≈ IC · Δz · σ_cs, so Δz_required = breakeven ÷ (IC · σ_cs). "
                           f"It is not a constant: it falls with friction, rises as skill or "
                           f"dispersion fall, and goes to ∞ when the IC stands aside.")
        out.append(f"  The rule fires on its own trigger, not on an expected return. The "
                   f"breakeven is the claim you are accepting: that the rank signal is worth "
                   f"more than the friction. That claim is testable a horizon later.")

    if res.get("partials"):
        out.append("")
        out.append("PARTIALS — distance to each rung (a partial should never arrive as a surprise)")
        lab = next((p["ladder"]["label"] for p in res["partials"] if p.get("ladder")), "—")
        out.append(f"  {'sector':<30} {'gain':>7} {'rk':>3}  {'ladder ' + lab:<44}"
                   f"{'overweight ' + res['partials'][0]['overweight']['label']:<26} ")
        for p in res["partials"]:
            g = "—" if p["unrealized_pct"] is None else f"{p['unrealized_pct']:+.1f}%"
            lad = p.get("ladder")
            if not lad:
                lad_s = "no ladder configured"
            else:
                # The rank leg is NOT a pass/fail on quality — the rung fires once the model has
                # STOPPED leading the name. Rank 1 failing it is the rule working, not a problem.
                if lad["rank_ok"]:
                    rk_s = "model no longer leads it ✓"
                elif p["rank"] is None:
                    rk_s = "no rank this run"
                else:
                    rk_s = f"still a leader (rank {p['rank']} < {lad['rank_min']})"
                gain_s = ("gain MET" if lad["gain_met"] else
                          f"needs {lad['need_gain_pct']:+.1f}%" if lad["need_gain_pct"] is not None
                          else "gain unknown")
                lad_s = f"{gain_s} · {rk_s}"
            ow = p["overweight"]
            ow_s = (f"MET ({ow['over_pp']:+.1f}pp)" if ow["met"]
                    else f"needs {ow['need_pp']:+.1f}pp more")
            live = f"  → {p['action']} LIVE" if p["live"] else ""
            out.append(f"  {str(p['sector_id'])[:30]:<30} {g:>7} "
                       f"{str(p['rank'] or '—'):>3}  {lad_s:<44}{ow_s:<24}{live}")

    # v13.3 — WHAT THE MODEL ACTUALLY CONTRIBUTES, printed instead of assumed. With λ=0 the
    # ranking sizes nothing, so the whole of the model's contribution is the top-N cut; these
    # three lines say how close that cut was, whether order shows up in size at all, and which
    # theses have decayed since they were bought. None of them moves a euro.
    sm = res.get("selection_margin")
    rho = res.get("rank_size_spearman")
    drifted = sorted([r for r in res.get("rows") or []
                      if r.get("composite_drift") is not None],
                     key=lambda r: float(r["composite_drift"]))
    if sm or rho is not None or drifted:
        out.append("")
        lam = float(res.get("tilt_lambda") or 0.0)
        out.append(f"SELECTION — with λ={lam:.2f} the ranking does not size the book, so what the "
                   f"model contributes IS the cut")
        if sm:
            out.append(f"  boundary   {sm['note']}")
        if rho is not None:
            out.append(f"  rank↔size  Spearman {rho:+.2f} between rank and target weight — "
                       + ("order does not show up in size (correct at λ=0: the risk budget "
                          "sizes, the ranking selects)" if abs(rho) < 0.5
                          else "order and size still move together"))
        if drifted:
            out.append(f"  drift      composite at entry → now, for the lines held. NO RULE "
                       f"READS THIS COLUMN — `decide_action` sees level, regime and the exit "
                       f"watcher, never direction.")
            for r in drifted:
                out.append(f"             {str(r['sector_id'])[:30]:<30} "
                           f"{float(r['composite_at_entry']):>5.1f} → "
                           f"{float(r['composite_now']):>5.1f}  "
                           f"({float(r['composite_drift']):+.1f})")

    out.append("")
    out.append(f"EDGE     bucket E[r] {c.get('buckets')} — {c.get('note')}")
    out.append(f"         (kept for calibration; it does NOT drive the table — see BREAKEVEN)")
    g = res.get("gate") or {}
    if g:
        ic = g.get("ic")
        out.append(f"GATE     after-tax gate {'ARMED' if g.get('armed') else 'STANDS ASIDE'} · "
                   f"composite IC {'n/a' if ic is None else f'{ic:+.3f}'}"
                   + (f" (se {g['ic_se']:.3f} → {g.get('ic_verdict')})" if g.get("ic_se") else "")
                   + f" · ~{g.get('windows')} window(s)")
        out.append(f"         {g.get('why')}")
    sp = res.get("selection_prior")
    if sp:
        out.append("")
        out += _wrap(sp["note"], width=96, indent="         ", first="PRIOR    ")
    out.append(_summary_line(res))
    for w in res.get("warnings", []):
        out.append(f"⚠ {w}")
    out.append("")
    out.append(res["note"])
    return "\n".join(out)


def main() -> None:
    """Five commands. The default (no subcommand) is the report, so `post_run.sh` and every
    existing call site keep working unchanged."""
    argv = sys.argv[1:]
    sub = argv[0] if argv and not argv[0].startswith("-") else None

    if sub == "override":
        ap = argparse.ArgumentParser(prog="rebalance override",
                                     description="Record a deviation from the rule action. "
                                                 "A DEFER is a deviation — log it.")
        ap.add_argument("command")
        ap.add_argument("sector_id")
        ap.add_argument("chosen_action", help="what was actually done (incl. HOLD / DEFER)")
        ap.add_argument("--reason", required=True, help="the evidence the rule is missing")
        ap.add_argument("--author", default="user", choices=["user", "claude"])
        ap.add_argument("--run-id", default=None, help="default: latest recorded rebalance run")
        ap.add_argument("--rule-action", default=None,
                        help="default: read from the lake `rebalance` row")
        ap.add_argument("--trade-eur", type=float, default=0.0,
                        help="€ actually moved (0 for HOLD/DEFER)")
        a = ap.parse_args(argv)

        from catalyx.store import lake
        reb = lake.read_table(_TABLE)
        run_id = a.run_id or (str(sorted(reb["run_id"].unique())[-1])
                              if not reb.empty and "run_id" in reb.columns else None)
        rule_action = a.rule_action
        if rule_action is None and not reb.empty:
            match = reb[(reb["run_id"] == run_id) & (reb["sector_id"] == a.sector_id)]
            rule_action = str(match.iloc[0]["rule_action"]) if len(match) else None
        if not run_id or not rule_action:
            raise SystemExit(f"no rebalance row for {a.sector_id} in run {run_id!r} — run the "
                             f"report first, or pass --run-id/--rule-action explicitly.")
        if rule_action == a.chosen_action:
            raise SystemExit(f"{a.sector_id}: chosen action equals the rule ({rule_action}) — "
                             f"that is compliance, not an override. Nothing logged.")
        row = log_override(run_id, a.sector_id, rule_action, a.chosen_action, a.reason,
                           a.author, chosen_trade_eur=a.trade_eur)
        print(json.dumps(row, indent=2, default=str))
        return

    if sub == "cash-stance":
        ap = argparse.ArgumentParser(
            prog="rebalance cash-stance",
            description="Decide, once and in writing, to hold cash the deploy rule wants at "
                        "work. Logged as an override, scored against the benchmark it declined, "
                        "and it EXPIRES — after which the shortfall re-opens by itself.")
        ap.add_argument("command")
        ap.add_argument("--reason", default=None,
                        help="why. Required to write one; omit to SHOW the live stance.")
        ap.add_argument("--author", default="user", choices=["user", "claude"])
        ap.add_argument("--run-id", default=None, help="default: latest recorded rebalance run")
        ap.add_argument("--deploy-gap-eur", type=float, default=None,
                        help="€ the rule wants deployed (default: this run's cash action)")
        ap.add_argument("--deployed-eur", type=float, default=0.0,
                        help="€ actually being deployed instead (default 0)")
        ap.add_argument("--json", action="store_true")
        a = ap.parse_args(argv)

        cfg = weights.rebalance_rules()
        if not a.reason:
            live = cash_stance(cfg=cfg)
            print(json.dumps(live, indent=2, default=str) if a.json else
                  ("No live cash stance — the deployment shortfall answers to nobody right now."
                   if not live else
                   f"CASH STANCE by {live['author']}, opened {live['started']} "
                   f"({live['age_days']}d ago)\n  {live['reason']}\n"
                   f"  expires {live['expires_on']} — {live['days_left']}d left, then the "
                   f"shortfall re-opens by itself."))
            return

        from catalyx.store import lake
        reb = lake.read_table(_TABLE)
        run_id = a.run_id or (str(sorted(reb["run_id"].unique())[-1])
                              if not reb.empty and "run_id" in reb.columns else None)
        if not run_id:
            raise SystemExit("no recorded rebalance run — run the report first.")
        gap = a.deploy_gap_eur
        if gap is None and not reb.empty:
            row = reb[reb["run_id"] == run_id]
            gap = float(row.iloc[0].get("book_cash_action_eur") or 0.0) if len(row) else 0.0
        row = log_cash_stance(run_id, a.reason, a.author, float(gap or 0.0),
                              chosen_deploy_eur=a.deployed_eur, cfg=cfg)
        sc = _stance_cfg(cfg)
        print(json.dumps(row, indent=2, default=str) if a.json else
              f"Cash stance recorded on run {run_id} by {a.author}: "
              f"€{abs(float(gap or 0.0)):,.0f} declined, €{a.deployed_eur:,.0f} deployed.\n"
              f"  It silences the shortfall rule for {sc['max_days']} days and is scored "
              f"against the benchmark ~21 trading days from now.")
        return

    if sub == "overrides":
        ap = argparse.ArgumentParser(prog="rebalance overrides",
                                     description="Score past overrides against the rule they "
                                                 "replaced, and tally by author.")
        ap.add_argument("command")
        ap.add_argument("--json", action="store_true")
        a = ap.parse_args(argv)
        res = score_overrides()
        print(json.dumps(res, indent=2, default=str) if a.json else render_overrides(res))
        return

    if sub == "scorecard":
        ap = argparse.ArgumentParser(prog="rebalance scorecard",
                                     description="Score the TABLE's own actions over a complete "
                                                 "forward window, against the HOLD baseline.")
        ap.add_argument("command")
        ap.add_argument("--json", action="store_true")
        a = ap.parse_args(argv)
        res = score_decisions()
        print(json.dumps(res, indent=2, default=str) if a.json else render_scorecard(res))
        return

    if sub == "best-moves-scorecard":
        ap = argparse.ArgumentParser(prog="rebalance best-moves-scorecard",
                                     description="Price the persisted best moves over a complete "
                                                 "forward window (return legs only).")
        ap.add_argument("command")
        ap.add_argument("--json", action="store_true")
        a = ap.parse_args(argv)
        res = score_best_moves()
        print(json.dumps(res, indent=2, default=str) if a.json
              else render_best_moves_scorecard(res))
        return

    ap = argparse.ArgumentParser(description="Target vs actual book, with rule actions and "
                                             "after-tax net edge per sector.")
    ap.add_argument("--strategy", default="catalyx", help="model portfolio id (default: catalyx)")
    ap.add_argument("--run-id", default=None, help="model run to compare against (default: latest)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--no-persist", action="store_true", help="do not write lake `rebalance`")
    args = ap.parse_args(argv)

    res = build(strategy=args.strategy, run_id=args.run_id)
    if not args.no_persist:
        persist(res)
    print(json.dumps(res, indent=2, default=str) if args.json else render(res))


if __name__ == "__main__":
    sys.exit(main())
