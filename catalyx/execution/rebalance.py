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
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from catalyx.config import weights

# Fixed precedence. A row is decided once, by the FIRST rule that fires in this order — so a
# breaking regime is never quietly outranked by "but it is underweight".
PRECEDENCE = ("SELL", "REDUCE", "TRIM", "ADD", "BUY", "HOLD")
_TABLE = "rebalance"
_OVERRIDE_TABLE = "override_log"

# Words the output may never contain in an action column. Kept as data so a test can assert it:
# the prose ban of §4.4 is only real if something checks it.
BANNED_ACTION_WORDS = ("watch", "monitor", "consider", "optional", "maybe", "evaluate")


# ── Pure rules (no I/O — these are what the tests pin) ────────────────────────

def deploy_ratio(n_intact_top: int, vix: float | None, cfg: dict) -> dict:
    """How much of the committed capital the rules say should be AT WORK right now.

    `clamp(base + step·(n_intact_top − intact_min) − vix_penalty·[VIX > pause], floor, ceiling)`.

    This replaces "cash by feel". The book sitting 70% in cash was never a decision anybody
    made — it was the residue of never deciding. Here it is a number with its inputs printed,
    and VIX is the ONLY macro brake: there is no discretionary "the market feels risky" term.
    """
    d = cfg.get("deployment", {}) or {}
    base = float(d.get("base", 0.60))
    step = float(d.get("step_per_intact_sector", 0.05))
    intact_min = int(d.get("intact_min", 5))
    pause = float(d.get("vix_pause_above", 30.0))
    penalty = float(d.get("vix_penalty", 0.20))
    floor = float(d.get("floor", 0.30))
    ceiling = float(d.get("ceiling", 1.00))

    vix_brake = bool(vix is not None and vix > pause)
    raw = base + step * (int(n_intact_top) - intact_min) - (penalty if vix_brake else 0.0)
    ratio = max(floor, min(ceiling, raw))
    return {
        "ratio": round(ratio, 4), "raw": round(raw, 4),
        "n_intact_top": int(n_intact_top), "intact_min": intact_min,
        "vix": vix, "vix_pause_above": pause, "vix_brake": vix_brake,
        "floor": floor, "ceiling": ceiling,
        "why": (f"base {base:.2f} + {step:.2f}×({n_intact_top}−{intact_min})"
                + (f" − {penalty:.2f} (VIX {vix:.1f} > {pause:.0f})" if vix_brake else "")
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
        streak = int(row.get("rank_out_streak") or 0)
        need = int(sell.get("rank_out_consecutive", 2) or 2)
        if streak >= need:
            return {"action": "SELL",
                    "reason": f"ranked below top-{sell.get('rank_out_of_top')} for "
                              f"{streak} consecutive runs"}

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
        if over >= float(trim.get("overweight_pp_min", 4.0)):
            return {"action": "TRIM", "reason": f"{over:.1f}pp above target"}
        rung = profit_ladder_step(row.get("unrealized_pct"), rank, cfg.get("profit_ladder"))
        if rung:
            return {"action": "TRIM",
                    "reason": f"profit ladder: +{row.get('unrealized_pct'):.0f}% ≥ "
                              f"{rung['gain_pct_min']:.0f}%"}

    # ── ADD / BUY — the side an LLM under-uses, so the conditions are explicit.
    if gap_pp >= deadband:
        if held:
            ok_rank = rank is not None and rank <= int(add.get("rank_max", 5))
            if ok_rank and gap_pp >= float(add.get("gap_pp_min", 3.0)) \
                    and not _in(regime, add.get("regime_not")):
                return {"action": "ADD",
                        "reason": f"rank {rank}, {gap_pp:.1f}pp under target"}
        else:
            ok_rank = rank is not None and rank <= int(buy.get("rank_max", 8))
            if ok_rank and gap_pp >= float(buy.get("gap_pp_min", 4.0)) \
                    and not _in(regime, buy.get("regime_not")) \
                    and not _in(row.get("narrative_maturity"), buy.get("maturity_not")):
                return {"action": "BUY",
                        "reason": f"rank {rank}, not held, {gap_pp:.1f}pp under target"}

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


def apply_gate(action: str, trade_eur: float, tax_eur: float, net_edge: float | None,
               cfg: dict, evaluable: bool = True) -> dict:
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
                "gate_note": "after-tax gate not evaluable (calibration has no window yet) — "
                             "the rule action stands, cost shown for the record"}
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


def hhi(weights_pct: list[float]) -> float | None:
    """Herfindahl on percentage weights (0–1). Concentration as a number, not an adjective."""
    tot = sum(w for w in weights_pct if w)
    if tot <= 0:
        return None
    return round(sum((w / tot) ** 2 for w in weights_pct if w), 4)


def rank_out_streak(rank_history: list[int | None], out_of_top: int) -> int:
    """Consecutive MOST-RECENT runs a sector has ranked below `out_of_top`.

    Counted from the newest backwards and reset by any run inside the cut, so one bad print
    never accumulates into a sell signal — the same "from the newest only" convention as
    catalyst_lifecycle.consecutive_below.
    """
    streak = 0
    for r in reversed(rank_history or []):
        if r is None or r > out_of_top:
            streak += 1
        else:
            break
    return streak


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


def _rank_streaks(out_of_top: int, n_runs: int = 4, lake_dir: Path | None = None) -> dict[str, int]:
    """Per sector, how many of the most recent runs it has ranked outside the cut."""
    from catalyx.store import lake
    df = lake.read_table("sector_snapshot", lake_dir=lake_dir)
    if df.empty or "rank" not in df.columns:
        return {}
    runs = sorted(df["run_id"].unique())[-n_runs:]
    df = df[df["run_id"].isin(runs)]
    out = {}
    for sid, grp in df.groupby("sector_id"):
        grp = grp.set_index("run_id").reindex(runs)
        hist = [None if r != r else int(r) for r in grp["rank"]]     # NaN → None
        out[str(sid)] = rank_out_streak(hist, out_of_top)
    return out


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


def _vix_last() -> float | None:
    try:
        from catalyx.data import prices
        df = prices.read(["^VIX"], "2000-01-01", date.today().isoformat())
        col = df["^VIX"].dropna() if df is not None and "^VIX" in getattr(df, "columns", []) else None
        return round(float(col.iloc[-1]), 2) if col is not None and len(col) else None
    except Exception:
        return None


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
    positions = {p["sector_id"]: p for p in book.get("positions", []) if p.get("sector_id")}

    # The model book is read from the LAST RECORDED RUN, which may predate a taxonomy change.
    # A BUY recommendation for a sector that is no longer buyable is worse than no
    # recommendation: it looks actionable. Drop them from the trade list, but SAY so — a silent
    # filter would just move the error out of sight. Held positions always stay on the table.
    investable = _investable_sectors()
    dropped = [m["sector_id"] for m in model
               if investable and m["sector_id"] not in investable and m["sector_id"] not in positions]
    model = [m for m in model if m["sector_id"] not in dropped]

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
    min_windows = int((cfg.get("net_edge_gate", {}) or {}).get("min_windows_to_gate", 3))
    gate_evaluable = int(exp.get("effective_windows", 0) or 0) >= min_windows

    sell_cfg = cfg.get("sell_if_any", {}) or {}
    streaks = _rank_streaks(int(sell_cfg.get("rank_out_of_top", 12)), lake_dir=lake_dir)
    score_ranks = _snapshot_ranks(run_id, lake_dir=lake_dir) if run_id else {}

    model_by_sector = {m["sector_id"]: m for m in model}
    sectors = list(dict.fromkeys(list(model_by_sector) + list(positions)))

    rows, realized_ytd = [], float(book.get("realized_ytd_eur", 0.0))
    for sid in sectors:
        m = model_by_sector.get(sid, {})
        p = positions.get(sid)
        tax_view = (p or {}).get("tax", {}) or {}
        mv = tax_view.get("market_value_eur")
        invested = (p or {}).get("invested_eur")

        target_eur = round(float(m.get("weight_pct", 0.0)) / 100.0 * deployable, 2)
        actual_eur = round(float(mv), 2) if mv is not None else (
            round(float(invested), 2) if invested is not None else 0.0)
        denom = total_capital or 1.0
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
            "rank_out_streak": streaks.get(sid, 0),
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

        costs = cost_drag(trade_eur, tax_eur, cfg)
        bucket = calibration.bucket_of(score_ranks.get(sid))
        edge = expected_edge(trade_eur, buckets.get(bucket) if bucket else None)
        net = round(edge - costs["cost_drag_eur"], 2) if edge is not None else None
        gate = apply_gate(action, trade_eur, tax_eur, net, cfg, evaluable=gate_evaluable)
        if gate["final_action"] != action:
            decision = {"action": gate["final_action"], "reason": gate["gate_note"]}
            action, trade_eur = gate["final_action"], 0.0

        rows.append({
            "sector_id": sid, "etf": (p or {}).get("etf") or m.get("primary_etf"),
            "rank": rank, "score_rank": score_ranks.get(sid), "bucket": bucket,
            "target_pct": target_pct, "actual_pct": actual_pct, "gap_pp": gap_pp,
            "target_eur": target_eur, "actual_eur": actual_eur, "gap_eur": gap_eur,
            "rule_action": action, "reason": decision["reason"],
            "trade_eur": trade_eur,
            "unrealized_pct": tax_view.get("unrealized_pct"),
            "realized_gain_eur": realized_gain,
            "expected_edge_eur": edge, "net_edge_eur": net,
            "gate_note": gate["gate_note"],
            "regime_state": ctx["regime_state"],
            "catalyst_freshness": ((p or {}).get("catalyst_freshness") or {}).get("status"),
            "exit_action": ctx["exit_action"],
            "flags": ";".join(f for f in [
                "re-verify catalyst" if ctx["reverify_required"] else None,
                "not investable today" if sid not in investable and investable else None,
            ] if f),
            "override": None, "override_reason": None,
            **{k: costs[k] for k in ("tax_eur", "spread_eur", "cost_drag_eur")},
        })

    rows.sort(key=lambda r: (PRECEDENCE.index(r["rule_action"]) if r["rule_action"] in PRECEDENCE
                             else 9, -abs(r["trade_eur"])))

    invested_now = round(sum(r["actual_eur"] for r in rows), 2)
    buys = round(sum(r["trade_eur"] for r in rows if r["trade_eur"] > 0), 2)
    sells = round(-sum(r["trade_eur"] for r in rows if r["trade_eur"] < 0), 2)
    after = round(invested_now + buys - sells, 2)
    book_metrics = {
        "total_capital_eur": round(total_capital, 2),
        "deployed_eur": invested_now,
        "deployed_pct": round(invested_now / (total_capital or 1) * 100, 2),
        "deploy_ratio": ratio,
        "deployable_eur": deployable,
        "under_deployed_eur": round(deployable - invested_now, 2),
        "cash_eur": round(total_capital - invested_now, 2),
        "buys_eur": buys, "sells_eur": sells,
        "turnover_pct": round((buys + sells) / (total_capital or 1) * 100, 2),
        "deployed_after_eur": after,
        "deployed_after_pct": round(after / (total_capital or 1) * 100, 2),
        "hhi_sector": hhi([r["actual_pct"] for r in rows]),
        "n_actions": sum(1 for r in rows if r["rule_action"] != "HOLD"),
    }

    warnings = []
    if dropped:
        warnings.append(f"{len(dropped)} model sector(s) dropped — no longer investable under the "
                        f"current taxonomy: {', '.join(sorted(dropped))}. The model book comes from "
                        f"run {run_id}; re-run the scorer to rebuild it on today's universe.")
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

    return {
        "as_of": date.today().isoformat(), "strategy": strategy, "run_id": run_id,
        "book": book_metrics, "warnings": warnings, "overrides": overrides,
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
    rows = [{**r, "run_id": run_id, "strategy": result["strategy"], "as_of": result["as_of"],
             "deploy_ratio": result["book"]["deploy_ratio"]["ratio"],
             "computed_at": computed_at} for r in result["rows"]]
    lake.append_partition(_TABLE, pd.DataFrame(rows), {"run_id": run_id},
                          overwrite=True, lake_dir=lake_dir)
    return len(rows)


def log_override(run_id: str, sector_id: str, rule_action: str, chosen_action: str,
                 reason: str, author: str, lake_dir: Path | None = None,
                 chosen_trade_eur: float | None = None, cfg: dict | None = None) -> dict:
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
        items.append({
            "run_id": key[0], "sector_id": key[1],
            "etf": (rr.get("etf") if hasattr(rr, "get") else None),
            "author": o.get("author"), "reason": o.get("reason"),
            "rule_action": o.get("rule_action"), "chosen_action": o.get("chosen_action"),
            "rule_trade_eur": float(rr.get("trade_eur") or 0.0) if hasattr(rr, "get") else 0.0,
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


def render(res: dict) -> str:
    b = res["book"]
    r = b["deploy_ratio"]
    out = [f"CATALYX — rebalance: pipeline target vs real book ({res['strategy']}, {res['as_of']})",
           f"run: {res.get('run_id')}", ""]
    out.append(f"CAPITAL  committed {_eur(b['total_capital_eur'])} · deployed "
               f"{_eur(b['deployed_eur'])} ({b['deployed_pct']:.0f}%) · cash {_eur(b['cash_eur'])}")
    out.append(f"RULE     deploy {r['ratio']:.0%} → {_eur(b['deployable_eur'])}   [{r['why']}]")
    gap = b["under_deployed_eur"]
    if abs(gap) >= 1:
        word = "UNDER-deployed" if gap > 0 else "OVER-deployed"
        out.append(f"         {word} by {_eur(abs(gap))} vs what the rules say should be at work")
    out.append("")

    hdr = f"{'sector':<34} {'rk':>3} {'tgt%':>6} {'act%':>6} {'gap€':>8} " \
          f"{'ACTION':<7} {'trade€':>8} {'net€':>7}  reason"
    out += [hdr, "-" * len(hdr)]
    for row in res["rows"]:
        net = "—" if row["net_edge_eur"] is None else f"{row['net_edge_eur']:.0f}"
        out.append(f"{row['sector_id'][:34]:<34} {str(row['rank'] or '—'):>3} "
                   f"{row['target_pct']:>6.1f} {row['actual_pct']:>6.1f} {row['gap_eur']:>8.0f} "
                   f"{row['rule_action']:<7} {row['trade_eur']:>8.0f} {net:>7}  {row['reason']}")
    out.append("")
    out.append(f"ACTIONS  {b['n_actions']} non-HOLD · buys {_eur(b['buys_eur'])} · "
               f"sells {_eur(b['sells_eur'])} · turnover {b['turnover_pct']:.1f}% → deployed after "
               f"{b['deployed_after_pct']:.0f}%")
    c = res["calibration"]
    out.append(f"EDGE     bucket E[r] {c.get('buckets')} — {c.get('note')}")
    out.append(_summary_line(res))
    for w in res.get("warnings", []):
        out.append(f"⚠ {w}")
    out.append("")
    out.append(res["note"])
    return "\n".join(out)


def main() -> None:
    """Three commands. The default (no subcommand) is the report, so `post_run.sh` and every
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
