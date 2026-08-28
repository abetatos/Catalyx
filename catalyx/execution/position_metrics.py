"""CATALYX — per-position and book-level metrics, persisted (v3 Phase 2 §3.4).

WHY THIS EXISTS
    The book was measured in exactly two ways: `invested_eur` (what went in) and a NAV curve (how
    the whole thing moved). Everything between those two — why a position is down, how long it has
    been down, whether the pipeline still believes the thesis it was opened on, how concentrated
    the book is, how much of it is a currency bet nobody chose — was either absent or re-derived by
    hand in a review and never written down. A number that is recomputed conversationally each
    month cannot be compared to itself across months, which is the only thing that makes it useful.

    Three of these are load-bearing and were not available anywhere before:

    1. **The price/FX split.** A EUR investor holding a GBP or USD vehicle owns two positions: the
       sector thesis and the currency. `unrealized_eur` alone cannot tell you which one is working
       — the 2026-08-04 bug was exactly this confusion in a cruder form (SEMI.L looked −24%, its
       real EUR drawdown was −11%). The decomposition here is exact by construction:
           price_eur = qty × (P_now − P_entry) × fx_entry
           fx_eur    = qty × P_now × (fx_now − fx_entry)
       These two plus a named `basis_residual_eur` (fees + cost-basis rounding) sum to the actual
       EUR P&L. No term is estimated and none is silently dropped.

    2. **Score drift.** Every movement carries a point-in-time `score_context` — the composite and
       rank the pipeline gave that sector on the day it was opened. Comparing it to today's run is
       a thesis-decay number: a position whose composite fell 20 points is a different bet from the
       one that was made, whatever the price has done. Price tells you the market's opinion; drift
       tells you your own model's.

    3. **Max drawdown from PEAK, not from cost.** `exit_watcher` measures against the cost basis
       because its stops are written that way. A position that ran +40% and gave back to +5% shows
       a healthy +5% there and a −25% round trip here. Both are true; only the second explains why
       the position feels bad.

    Read-only over the lake and the Tier-1 files, one shared price fetch, recommend-nothing: this
    module measures, it never decides. The decisions live in `rebalance.py`.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

_TABLE = "position_metrics"
_BOOK_TABLE = "book_metrics"
_TRADING_DAYS = 252


# ── Pure metrics (no I/O — these are what the tests pin) ─────────────────────

def pnl_split(qty: float, entry_price: float | None, now_price: float | None,
              entry_fx: float | None, now_fx: float | None,
              unrealized_eur: float | None) -> dict:
    """Split EUR P&L into the price move, the FX move, and a named residual.

    The residual is fees plus cost-basis rounding: `invested_eur` is the cash that actually left
    the account, so it embeds the fee, while `entry_price × qty × fx` does not. Reporting it as a
    third named term is the honest option — folding it into "price" would quietly credit the
    thesis with a brokerage cost.
    """
    out = {"pnl_price_eur": None, "pnl_fx_eur": None, "basis_residual_eur": None}
    if None in (entry_price, now_price, entry_fx, now_fx) or unrealized_eur is None:
        return out
    price_eur = qty * (float(now_price) - float(entry_price)) * float(entry_fx)
    fx_eur = qty * float(now_price) * (float(now_fx) - float(entry_fx))
    out["pnl_price_eur"] = round(price_eur, 2)
    out["pnl_fx_eur"] = round(fx_eur, 2)
    out["basis_residual_eur"] = round(float(unrealized_eur) - price_eur - fx_eur, 2)
    return out


def daily_returns(navs: list[float]) -> list[float]:
    return [navs[i] / navs[i - 1] - 1.0
            for i in range(1, len(navs)) if navs[i - 1]]


def annualized_vol(returns: list[float]) -> float | None:
    """Sample stdev × √252. Needs ≥3 returns — a 2-point 'volatility' is a line segment."""
    if len(returns) < 3:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return round(math.sqrt(var) * math.sqrt(_TRADING_DAYS) * 100.0, 2)


def sharpe(returns: list[float], rf_annual: float = 0.0) -> float | None:
    """Annualized Sharpe with a flat risk-free. None when vol is zero or the sample is too short —
    an undefined ratio must not print as 0.0, which reads as 'measured, and mediocre'."""
    if len(returns) < 3:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    excess = mean - rf_annual / _TRADING_DAYS
    return round(excess / sd * math.sqrt(_TRADING_DAYS), 2)


def max_drawdown(navs: list[float]) -> dict:
    """Worst peak-to-trough of the series, and where it happened (indices)."""
    if len(navs) < 2:
        return {"max_drawdown_pct": None, "peak_idx": None, "trough_idx": None}
    peak, peak_i, worst, worst_pair = navs[0], 0, 0.0, (0, 0)
    for i, v in enumerate(navs):
        if v > peak:
            peak, peak_i = v, i
        dd = (v / peak - 1.0) if peak else 0.0
        if dd < worst:
            worst, worst_pair = dd, (peak_i, i)
    return {"max_drawdown_pct": round(worst * 100.0, 2),
            "peak_idx": worst_pair[0], "trough_idx": worst_pair[1]}


def beta(returns: list[float], bench: list[float]) -> float | None:
    """Cov(r, b) / Var(b) over the overlapping window."""
    n = min(len(returns), len(bench))
    if n < 3:
        return None
    r, b = returns[-n:], bench[-n:]
    mr, mb = sum(r) / n, sum(b) / n
    var_b = sum((x - mb) ** 2 for x in b)
    if var_b == 0:
        return None
    cov = sum((r[i] - mr) * (b[i] - mb) for i in range(n))
    return round(cov / var_b, 2)


def correlation(a: list[float], b: list[float]) -> float | None:
    """Pearson over the overlapping window."""
    n = min(len(a), len(b))
    if n < 3:
        return None
    x, y = a[-n:], b[-n:]
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((v - mx) ** 2 for v in x))
    sy = math.sqrt(sum((v - my) ** 2 for v in y))
    if not sx or not sy:
        return None
    return round(sum((x[i] - mx) * (y[i] - my) for i in range(n)) / (sx * sy), 2)


def tracking_error(returns: list[float], bench: list[float]) -> float | None:
    """Annualized stdev of the return DIFFERENCE — how far the real book drifts from the thing it
    is supposed to be tracking."""
    n = min(len(returns), len(bench))
    if n < 3:
        return None
    diff = [returns[-n:][i] - bench[-n:][i] for i in range(n)]
    return annualized_vol(diff)


def active_share(actual_pct: dict[str, float], target_pct: dict[str, float]) -> float | None:
    """½ Σ|w_actual − w_target|, the textbook definition.

    Careful with the reading: the standard formula assumes both books sum to 100%, and ours do not
    — the real book is expressed as a % of TOTAL capital, so an under-deployed book differs from
    a fully deployed model partly just by holding cash. That is a legitimate difference (idle cash
    IS a bet against the model), but it means this number answers "how far apart are the two
    books" and NOT "how much of the model do we own". For the second question use
    `model_overlap` — reported beside it rather than folded in, because one number pretending to
    answer both is how a book ends up looking diversified and being concentrated.
    """
    keys = set(actual_pct) | set(target_pct)
    if not keys:
        return None
    return round(sum(abs(actual_pct.get(k, 0.0) - target_pct.get(k, 0.0))
                     for k in keys) / 2.0, 2)


def model_overlap(actual_pct: dict[str, float], target_pct: dict[str, float]) -> float | None:
    """Σ min(actual, target) / Σ target — the share of the model book actually held.

    100% = every euro the model wants is in place. 0% = we own none of it. Unlike `active_share`
    this is well-defined when the two books have different totals, which is always true here.
    """
    total_target = sum(v for v in target_pct.values() if v)
    if not total_target:
        return None
    held = sum(min(actual_pct.get(k, 0.0), v) for k, v in target_pct.items())
    return round(max(0.0, held) / total_target * 100.0, 2)


def hhi(weights_pct: list[float]) -> float | None:
    """Herfindahl over position weights, 0–10000. One position = 10000."""
    ws = [w for w in weights_pct if w]
    if not ws:
        return None
    total = sum(ws)
    if not total:
        return None
    return round(sum((w / total * 100.0) ** 2 for w in ws), 1)


def score_drift(entry: dict | None, now: dict | None) -> dict:
    """Composite and rank now vs at entry. A missing side is unknown, never zero drift — the
    whole point is to notice a thesis the model quietly stopped believing."""
    out = {"composite_at_entry": None, "composite_now": None, "composite_drift": None,
           "rank_at_entry": None, "rank_now": None, "rank_drift": None, "drift_note": None}
    e, n = entry or {}, now or {}
    # Say WHICH side is missing. "—" that could mean either "we never recorded what we believed
    # when we bought this" or "the current run does not score it" is two different problems
    # wearing the same dash, and only the first is fixable with `movement_repo ingest`.
    if not e:
        out["drift_note"] = "no score_context at entry — run `movement_repo ingest --write-back`"
    elif not n:
        out["drift_note"] = "sector not in the current run"
    for field, key in (("composite", "composite"), ("rank", "rank")):
        a, b = e.get(key), n.get(key)
        out[f"{field}_at_entry"] = a
        out[f"{field}_now"] = b
        if a is not None and b is not None and a == a and b == b:
            # Rank drift is signed so that POSITIVE always means "worse than at entry" for both:
            # a composite that fell, or a rank number that grew.
            out[f"{field}_drift"] = round(float(b) - float(a), 2) if field == "composite" \
                else int(b) - int(a)
    return out


def fx_exposure(rows: list[dict]) -> dict:
    """% of the marked book by listing currency. A EUR investor who is 60% USD-listed holds a
    currency position they never wrote a thesis for."""
    total = sum(float(r.get("market_value_eur") or 0.0) for r in rows)
    if not total:
        return {}
    by: dict[str, float] = {}
    for r in rows:
        ccy = (r.get("currency") or "EUR").upper()
        ccy = "GBP" if ccy in ("GBP", "GBX", "GBp".upper()) else ccy
        by[ccy] = by.get(ccy, 0.0) + float(r.get("market_value_eur") or 0.0)
    return {k: round(v / total * 100.0, 1) for k, v in sorted(by.items(), key=lambda x: -x[1])}


# ── Assembly (I/O) ───────────────────────────────────────────────────────────

def _entry_facts(movements: list[dict]) -> dict[str, dict]:
    """Weighted-average NATIVE entry price + first entry date per ETF, from the buy movements."""
    from catalyx.store.movement_repo import _BUY_ACTIONS

    acc: dict[str, dict] = {}
    for m in sorted(movements, key=lambda x: x.get("executed_at") or ""):
        if m["action"] not in _BUY_ACTIONS:
            continue
        etf = m["vehicle"]["etf"]
        qty, px = float(m.get("qty") or 0.0), m.get("price")
        a = acc.setdefault(etf, {"qty": 0.0, "notional_native": 0.0, "first_entry": None,
                                 "currency": m["vehicle"].get("currency")})
        if px is not None:
            a["qty"] += qty
            a["notional_native"] += qty * float(px)
        a["first_entry"] = a["first_entry"] or (m.get("executed_at") or "")[:10]
    for a in acc.values():
        a["entry_price_native"] = round(a["notional_native"] / a["qty"], 6) if a["qty"] else None
    return acc


def _snapshot_now(run_id: str | None, lake_dir: Path | None = None) -> dict[str, dict]:
    from catalyx.store import lake

    df = lake.read_table("sector_snapshot", lake_dir=lake_dir)
    if df.empty or not run_id:
        return {}
    df = df[df["run_id"] == run_id]
    return {str(r["sector_id"]): {"composite": r.get("composite"), "rank": r.get("rank")}
            for _, r in df.iterrows()}


def _entry_context(movements: list[dict]) -> dict[str, dict]:
    """The point-in-time score the pipeline gave each sector when it was opened."""
    out: dict[str, dict] = {}
    for m in sorted(movements, key=lambda x: x.get("executed_at") or ""):
        sc = m.get("score_context") or {}
        if m["sector_id"] not in out and (sc.get("composite") is not None or sc.get("rank")):
            out[m["sector_id"]] = {"composite": sc.get("composite"), "rank": sc.get("rank")}
    return out


def _nav_series(portfolio_id: str, mode: str | None = None,
                lake_dir: Path | None = None) -> list[dict]:
    """One NAV curve, not three interleaved ones.

    `portfolio_nav` holds `backtest`, `live` and `forward` rows under the SAME portfolio_id. Sorting
    the lot by date splices two curves together and manufactures ±18% daily moves out of nothing —
    the first cut of this module reported a 95% tracking error that way. Always pick a mode, and
    keep the last row per date (a re-run rewrites, it does not append a second reality).
    """
    from catalyx.store import lake

    df = lake.read_table("portfolio_nav", lake_dir=lake_dir)
    if df.empty:
        return []
    df = df[df["portfolio_id"] == portfolio_id]
    if mode is not None and "mode" in df.columns:
        df = df[df["mode"] == mode]
    if df.empty:
        return []
    sort_cols = [c for c in ("date", "computed_at") if c in df.columns]
    df = df.sort_values(sort_cols).drop_duplicates(subset=["date"], keep="last")
    return df.to_dict("records")


def build(run_id: str | None = None, lake_dir: Path | None = None, exit_fn=None,
          price_fn=None, as_of: str | None = None) -> dict:
    """Per-position + book metrics for the current real book."""
    from catalyx.execution import nav_engine
    from catalyx.scorer import exit_watcher
    from catalyx.store import movement_repo

    as_of = as_of or date.today().isoformat()
    book = (exit_fn or (lambda: exit_watcher.assess(persist=False, lake_dir=lake_dir)))()
    run_id = run_id or book.get("run_id")
    positions = book.get("positions", [])

    movements = movement_repo.load_all()
    entries = _entry_facts(movements)
    entry_ctx = _entry_context(movements)
    now_ctx = _snapshot_now(run_id, lake_dir=lake_dir)
    held = movement_repo.positions()
    qty_by_etf = {h["etf"]: h["qty"] for h in held.get("holdings", [])}
    cost_by_etf = {h["etf"]: h.get("avg_cost") for h in held.get("holdings", [])}

    # ONE price window for every held vehicle, native and EUR. The native frame gives the price
    # leg of the split; the EUR frame gives the value path (peak drawdown, vol since entry).
    tickers = sorted(qty_by_etf)
    start = min([e.get("first_entry") or as_of for e in entries.values()] or [as_of])
    native = eur = None
    if tickers:
        try:
            fn = price_fn or nav_engine.yfinance_prices
            native = fn(tickers, start, as_of)
            eur = nav_engine._eur_prices(native, start, as_of,
                                         nav_engine._default_ccy_fn, nav_engine._default_fx_fn)
        except Exception:
            native = eur = None

    rows = []
    for p in positions:
        etf, sid = p.get("etf"), p.get("sector_id")
        e = entries.get(etf, {})
        tax = p.get("tax") or {}
        qty = float(qty_by_etf.get(etf) or 0.0)
        mv, unreal = tax.get("market_value_eur"), tax.get("unrealized_eur")

        now_native = now_eur = None
        path: list[float] = []
        if native is not None and etf in getattr(native, "columns", []):
            col = native[etf].dropna()
            now_native = float(col.iloc[-1]) if len(col) else None
        if eur is not None and etf in getattr(eur, "columns", []):
            col = eur[etf].dropna()
            first = e.get("first_entry")
            win = col[col.index >= first] if first else col
            path = [float(v) for v in win]
            now_eur = path[-1] if path else None

        # entry_fx is IMPLIED by the cost basis (EUR/unit ÷ native/unit), so no FX history is
        # needed and the rate used is the one actually paid. An EUR listing is pinned to 1.0 —
        # otherwise fee rounding would print a phantom currency effect on a domestic vehicle.
        entry_px = e.get("entry_price_native")
        is_eur = (e.get("currency") or "EUR").upper() == "EUR"
        avg_cost_eur = cost_by_etf.get(etf)
        entry_fx = 1.0 if is_eur else (
            (float(avg_cost_eur) / float(entry_px)) if avg_cost_eur and entry_px else None)
        now_fx = 1.0 if is_eur else (
            (now_eur / now_native) if now_eur and now_native else None)

        split = pnl_split(qty, entry_px, now_native, entry_fx, now_fx, unreal)
        rets = daily_returns(path)
        dd = max_drawdown(path)
        days_held = None
        if e.get("first_entry"):
            days_held = (date.fromisoformat(as_of) - date.fromisoformat(e["first_entry"])).days

        fresh = p.get("catalyst_freshness") or {}
        rows.append({
            "run_id": run_id, "as_of": as_of, "sector_id": sid, "etf": etf,
            "currency": e.get("currency"), "qty": round(qty, 6),
            "invested_eur": p.get("invested_eur"), "market_value_eur": mv,
            "unrealized_eur": unreal, "unrealized_pct": tax.get("unrealized_pct"),
            **split,
            "days_held": days_held, "first_entry": e.get("first_entry"),
            "return_since_entry_pct": round((path[-1] / path[0] - 1) * 100, 2)
            if len(path) >= 2 and path[0] else None,
            "vol_since_entry_pct": annualized_vol(rets),
            "max_drawdown_from_peak_pct": dd["max_drawdown_pct"],
            **score_drift(entry_ctx.get(sid), now_ctx.get(sid)),
            "catalyst_freshness": fresh.get("status"),
            "catalyst_review_age_days": fresh.get("review_age_days"),
            "regime_state": p.get("regime_state"),
            "exit_action": p.get("suggested_action"),
            "drawdown_tier": (p.get("drawdown") or {}).get("tier"),
        })

    return {"as_of": as_of, "run_id": run_id, "positions": rows,
            "book": _book_metrics(rows, run_id, lake_dir=lake_dir),
            "note": "Measurement only — no recommendation, no action. `pnl_price_eur` + "
                    "`pnl_fx_eur` + `basis_residual_eur` sum to the EUR P&L exactly; the residual "
                    "is fees and cost-basis rounding, not a modelling error."}


def _book_metrics(rows: list[dict], run_id: str | None, lake_dir: Path | None = None) -> dict:
    from catalyx.config import weights

    total_capital = weights.total_capital_eur() or 0.0
    marked = sum(float(r.get("market_value_eur") or 0.0) for r in rows)
    invested = sum(float(r.get("invested_eur") or 0.0) for r in rows)

    real = _nav_series("real", lake_dir=lake_dir)
    navs = [float(r["nav"]) for r in real if r.get("nav") is not None]
    bench = [float(r["benchmark_nav"]) for r in real if r.get("benchmark_nav") is not None]
    r_ret, b_ret = daily_returns(navs), daily_returns(bench)

    # LIVE only: the backtest curve is hypothetical by `track_record.yaml`'s own statement, so
    # tracking the real book against it would measure drift from a book that was never held.
    model = _nav_series("catalyx", mode="live", lake_dir=lake_dir)
    m_by_date = {str(r["date"]): float(r["nav"]) for r in model if r.get("nav") is not None}
    shared = [str(r["date"]) for r in real if str(r.get("date")) in m_by_date]
    te = None
    if len(shared) >= 4:
        rn = {str(r["date"]): float(r["nav"]) for r in real if r.get("nav") is not None}
        te = tracking_error(daily_returns([rn[d] for d in shared]),
                            daily_returns([m_by_date[d] for d in shared]))

    # Active share against the model book, both as a % of TOTAL capital — so the cash the model
    # would have deployed counts as a difference, which is exactly what it is.
    target, actual = {}, {}
    if total_capital:
        from catalyx.store import lake
        ph = lake.read_table("portfolio_holding", lake_dir=lake_dir)
        if not ph.empty and run_id:
            sel = ph[(ph["portfolio_id"] == "catalyx") & (ph["run_id"] == run_id)]
            for _, h in sel.iterrows():
                target[str(h["sector_id"])] = float(h.get("weight_pct") or 0.0)
        for r in rows:
            actual[str(r["sector_id"])] = float(r.get("market_value_eur") or 0.0) \
                / total_capital * 100.0

    return {
        "total_capital_eur": round(total_capital, 2),
        "invested_eur": round(invested, 2),
        "marked_eur": round(marked, 2),
        "deployed_pct": round(marked / total_capital * 100, 2) if total_capital else None,
        "cash_eur": round(total_capital - marked, 2) if total_capital else None,
        "unrealized_eur": round(marked - invested, 2),
        "n_positions": len(rows),
        "hhi": hhi([float(r.get("market_value_eur") or 0.0) for r in rows]),
        "fx_exposure_pct": fx_exposure(rows),
        "vol_pct": annualized_vol(r_ret),
        "sharpe": sharpe(r_ret),
        "max_drawdown_pct": max_drawdown(navs)["max_drawdown_pct"],
        "beta_vs_spy": beta(r_ret, b_ret),
        # Reported BESIDE beta on purpose. beta = corr × (vol_book / vol_bench), so a book that is
        # twice as volatile and half correlated prints a beta of exactly 1.00 — which reads as
        # "we move with the index" and is the opposite of the truth. The pair is the fact; beta
        # alone is a coincidence waiting to be misread.
        "corr_vs_spy": correlation(r_ret, b_ret),
        "tracking_error_vs_model_pct": te,
        "active_share_pct": active_share(actual, target) if target else None,
        "model_overlap_pct": model_overlap(actual, target) if target else None,
        "nav_points": len(navs),
    }


def persist(result: dict, lake_dir: Path | None = None) -> int:
    """One row per position + one book row, keyed by run — so every metric here can be compared
    to itself next month instead of being re-derived in a conversation."""
    import pandas as pd

    from catalyx.store import lake

    run_id = result.get("run_id")
    if not run_id or not result.get("positions"):
        return 0
    stamped = datetime.now(timezone.utc).isoformat()
    rows = [{**r, "computed_at": stamped} for r in result["positions"]]
    lake.append_partition(_TABLE, pd.DataFrame(rows), {"run_id": run_id},
                          overwrite=True, lake_dir=lake_dir)
    b = {k: (json.dumps(v) if isinstance(v, dict) else v)
         for k, v in result["book"].items()}
    lake.append_partition(_BOOK_TABLE,
                          pd.DataFrame([{**b, "run_id": run_id, "as_of": result["as_of"],
                                         "computed_at": stamped}]),
                          {"run_id": run_id}, overwrite=True, lake_dir=lake_dir)
    return len(rows)


# ── Render ───────────────────────────────────────────────────────────────────

def _f(v, d=1, suffix=""):
    return "—" if v is None or v != v else f"{float(v):,.{d}f}{suffix}"


def render(res: dict) -> str:
    b = res["book"]
    out = [f"CATALYX — position & book metrics ({res['as_of']}, run {res.get('run_id')})", ""]
    hdr = (f"{'sector':<30} {'d held':>6} {'P&L €':>8} {'price':>8} {'FX':>7} "
           f"{'peak DD':>8} {'vol':>6} {'Δscore':>7} {'fresh':<11} {'exit':<7}")
    out += [hdr, "-" * len(hdr)]
    for r in res["positions"]:
        out.append(f"{str(r['sector_id'])[:30]:<30} {_f(r['days_held'], 0):>6} "
                   f"{_f(r['unrealized_eur'], 0):>8} {_f(r['pnl_price_eur'], 0):>8} "
                   f"{_f(r['pnl_fx_eur'], 0):>7} "
                   f"{_f(r['max_drawdown_from_peak_pct'], 1):>8} "
                   f"{_f(r['vol_since_entry_pct'], 0):>6} "
                   f"{_f(r['composite_drift'], 1):>7} "
                   f"{str(r['catalyst_freshness'] or '—'):<11} "
                   f"{str(r['exit_action'] or '—'):<7}")
    out += ["", f"BOOK     {b['n_positions']} positions · marked €{b['marked_eur']:,.0f} · "
                f"deployed {_f(b['deployed_pct'], 0)}% · unrealized €{b['unrealized_eur']:,.0f}",
            f"RISK     vol {_f(b['vol_pct'], 1)}% · Sharpe {_f(b['sharpe'], 2)} · "
            f"maxDD {_f(b['max_drawdown_pct'], 1)}% · vs SPY beta {_f(b['beta_vs_spy'], 2)} "
            f"corr {_f(b['corr_vs_spy'], 2)} ({b['nav_points']} NAV points)",
            f"SHAPE    HHI {_f(b['hhi'], 0)} · model overlap {_f(b['model_overlap_pct'], 1)}% "
            f"(active share {_f(b['active_share_pct'], 1)}%) · tracking error "
            f"{_f(b['tracking_error_vs_model_pct'], 1)}%",
            f"FX       {b['fx_exposure_pct'] or '—'}", "", res["note"]]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Per-position and book metrics for the real book.")
    ap.add_argument("--run-id", default=None, help="scoring run to measure drift against")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-persist", action="store_true")
    args = ap.parse_args()

    res = build(run_id=args.run_id)
    if not args.no_persist:
        persist(res)
    print(json.dumps(res, indent=2, default=str) if args.json else render(res))


if __name__ == "__main__":
    sys.exit(main())
