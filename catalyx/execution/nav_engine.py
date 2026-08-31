"""Portfolio NAV-over-time engine (Fase D.2).

Turns a set of holdings (weights per ETF) into a NAV time series indexed to 100 at
inception — a buy-and-hold of that snapshot. Works for BOTH model portfolios (from
`portfolio.build_model_holdings`) and the real book (from `movement_repo.positions`),
because both reduce to {etf: weight}. The price source is injectable: `price_fn(tickers,
start, end) -> DataFrame[date × ticker]` (adjusted close). The default uses yfinance;
tests inject a synthetic frame so the math is verified with no network.

NAV(t) = base × [ Σ_i w_i · p_i(t)/p_i(t0)  +  cash·1 ],   cash = 1 − Σ w_i (held flat).

Persisted to the lake table `portfolio_nav`, one file per portfolio (overwritten on
recompute — NAV is a derived materialization, not a source observation).

CLI:
    uv run python -m catalyx.execution.nav_engine model <portfolio_id> [--as-of YYYY-MM-DD]
    uv run python -m catalyx.execution.nav_engine show <portfolio_id>
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from catalyx.config import weights as weights_cfg
from catalyx.store import lake

_NAV_TABLE = "portfolio_nav"


# ── Price source (injectable) ────────────────────────────────────────────────

def yfinance_prices(tickers: list[str], start: str, end: str):
    """Default price_fn: adjusted-close DataFrame (index=date, columns=tickers), NATIVE ccy.

    Served from the shared lake cache (`catalyx.data.prices`), which fetches from yfinance only
    the dates it does not already hold. Kept under this name because it IS the default price_fn
    of every NAV call and of `dislocation`/`entry_timing`/`exit_watcher` downstream — routing it
    here makes one run of the pipeline read ONE consistent price snapshot instead of ~15
    independent fetches that could each see a different close (v3 Phase 1, PLAN §2.1).

    The end-exclusive yfinance quirk (asking [start, today] never returned today's close) is now
    handled once inside `prices.yfinance_fetch`, not per call site.
    """
    from catalyx.data import prices

    return prices.read(list(tickers), start, end)


# ── FX → EUR (the book, and every comparison, is denominated in EUR) ──────────
#
# yfinance serves each ETF's price in its LISTING currency (4COP.DE/IQQH.DE=EUR,
# USPY.L=USD, SEMI.L=GBP, SPY=USD). Summing those natively is meaningless for a
# EUR investor, so every price series is converted to EUR before any NAV math —
# NAV(t) = Σ w_i · [p_i(t)·fx_i(t)] / [p_i(t0)·fx_i(t0)]. CLAUDE.md: all P&L in EUR.

def _default_ccy_fn(tickers: list[str]) -> dict[str, str]:
    """{ticker: listing currency}. Cached in the lake — a listing currency never changes, but
    this used to cost one `yf.Ticker(t).fast_info` round-trip PER TICKER on every NAV/exit run."""
    from catalyx.data import prices

    return prices.currencies(list(tickers))


def _default_fx_fn(currencies, start: str, end: str) -> dict:
    """{base_ccy: Series of EUR per 1 unit of that ccy, indexed by date}.

    EURUSD=X quotes USD per EUR, so EUR-per-USD = 1/EURUSD; likewise 1/EURGBP for GBP.
    Pence (GBp/GBX) reuse the GBP series and are scaled ×0.01 at conversion time."""
    pair_for = {"USD": "EURUSD=X", "GBP": "EURGBP=X"}
    series: dict = {}
    for ccy in currencies:
        pair = pair_for.get(ccy)
        if not pair:
            continue
        px = yfinance_prices([pair], start, end)
        col = px[pair] if pair in getattr(px, "columns", []) else px.iloc[:, 0]
        series[ccy] = 1.0 / col
    return series


def _to_eur(prices, ccy_map: dict, fx_by_ccy: dict):
    """Convert each column of a price frame from its listing currency to EUR in place-of-copy.
    A currency with no FX series (couldn't be fetched) is left native rather than dropped."""
    if prices is None or len(prices) == 0:
        return prices
    out = prices.copy()
    for t in list(getattr(out, "columns", [])):
        ccy = (ccy_map.get(t) or "EUR")
        pence = ccy in ("GBp", "GBX")
        base = "GBP" if pence else ccy
        col = out[t]
        if base != "EUR":
            fx = fx_by_ccy.get(base)
            if fx is None:
                continue
            col = col * fx.reindex(out.index).ffill().bfill()
        if pence:
            col = col * 0.01
        out[t] = col
    return out


def _eur_prices(prices, start: str, end: str, ccy_fn, fx_fn):
    """Full native→EUR conversion of a price frame: look up each column's currency, fetch the
    needed FX series, apply. No-op for an all-EUR frame."""
    if prices is None or len(prices) == 0:
        return prices
    tickers = list(getattr(prices, "columns", []))
    ccy_map = ccy_fn(tickers)
    needed = {("GBP" if c in ("GBp", "GBX") else c) for c in ccy_map.values()} - {"EUR"}
    fx_by_ccy = fx_fn(needed, start, end) if needed else {}
    return _to_eur(prices, ccy_map, fx_by_ccy)


# ── Real-book ledger: TWR vs MWR ─────────────────────────────────────────────
#
# WHY THIS EXISTS (2026-08-28, user-raised)
#     The real curve used to take the qty held TODAY and project it BACKWARDS to the first
#     movement, dividing by today's total cost. The book was built in three tranches (€1500 on
#     06-05, +€1000 on 06-08, +€500 on 06-16), so that curve modelled €3000 of exposure from day
#     one and gave SEMI.L — bought on the 16th — a full position on the 5th. Every risk metric
#     derived from it (vol, Sharpe, maxDD, beta/corr vs SPY in `position_metrics._book_metrics`)
#     described a portfolio that was never held. Measured against the real ledger the error was
#     not cosmetic: return +1.53% vs a true +4.15%, Sharpe 0.92 vs 0.78.
#
#     The fix is to stop deriving the PATH from a snapshot. A book that receives contributions
#     needs two different numbers, and the old curve silently conflated them:
#
#       TWR (time-weighted) — neutralizes external cash flows, so a deposit is not mistaken for
#           performance. This is the curve, and the ONLY series risk metrics or a SPY comparison
#           may be computed from. Answers: is the selection any good?
#       MWR (money-weighted, an IRR over the actual flows) — answers: what did MY money earn,
#           given when it went in? Endpoint only; not a series.
#
#     Neither replaces the broker view (market value vs cost basis), which stays exactly as it
#     was and is reported alongside. Three questions, three numbers, each labelled.
#
#     Nothing here needs to be captured daily: qty comes from the movement files (written the day
#     a trade happens) and prices/FX come from the cache, which backfills any history yfinance
#     still serves. The whole series is re-derivable from scratch at any time.

def daily_ledger(movements: list[dict], index) -> tuple:
    """Movements → (qty per ETF per day, external EUR flow per day), both indexed by `index`.

    Positions are stepped in on the first trading day on/after `executed_at` — a trade booked on
    a weekend belongs to the next session, not to the previous close. `flow` is the cash that
    crossed the account boundary that day (buys +, sells −); it is what TWR must neutralize and
    what MWR discounts. Uses ALL movements, including fully closed ones, so a position that was
    opened and sold still shapes the stretch of curve during which it was actually held — the
    snapshot-based curve erased those, which is survivorship bias in the track record.
    """
    import pandas as pd

    from catalyx.store.movement_repo import _BUY_ACTIONS, _SELL_ACTIONS

    etfs: list[str] = []
    for m in movements:
        et = (m.get("vehicle") or {}).get("etf") or m.get("etf")
        if et and et not in etfs:
            etfs.append(et)
    qty = pd.DataFrame(0.0, index=index, columns=etfs)
    flow = pd.Series(0.0, index=index)
    if len(index) == 0:
        return qty, flow

    for m in sorted(movements, key=lambda x: str(x.get("executed_at") or "")):
        et = (m.get("vehicle") or {}).get("etf") or m.get("etf")
        action = m.get("action")
        if not et or action not in _BUY_ACTIONS + _SELL_ACTIONS:
            continue
        on = str(m.get("executed_at") or "")[:10]
        if not on:
            continue
        stamp = pd.Timestamp(on)
        if getattr(index, "tz", None) is not None:
            stamp = stamp.tz_localize(index.tz)
        hits = index[index >= stamp]
        if len(hits) == 0:                       # executed after the price window ends
            continue
        d0 = hits[0]
        n = float(m.get("qty") or 0.0)
        if action in _SELL_ACTIONS:
            # Cap the sale at what is actually held, exactly as movement_repo.positions() does.
            # These files are hand-authored; an over-sized close would otherwise leave a NEGATIVE
            # qty, and the curve would silently mark a short position that never existed.
            held = float(qty.loc[d0, et])
            if n - held > 1e-9:
                print(f"[nav_engine] WARNING {m.get('id')}: {action} of {n} {et} exceeds held "
                      f"{held:.6f} — capping to held.", file=sys.stderr)
                n = max(held, 0.0)
            n = -n
        qty.loc[d0:, et] += n
        flow.loc[d0] += (1.0 if action in _BUY_ACTIONS else -1.0) * float(m.get("amount_eur") or 0.0)
    return qty, flow


def execution_price_checks(movements: list[dict], prices, tol_pct: float = 1.5) -> list[dict]:
    """Flag movements whose recorded `price` is far from the execution date's close.

    The TWR anchors each new position at the price actually paid, so a mis-recorded fill is not a
    cosmetic blemish — it is booked as a real first-day gain or loss. Three of the first five
    movements in this book were logged at the PREVIOUS session's close (IUHE.AS matched it to the
    cent), which charged the curve a −6.9% day-one drop on 4COP.DE that never happened to anyone.
    Recorded qty is derived from that same price, so the ambiguity is real and only the broker
    statement can settle it — hence a warning that names the alternative, never a silent repair.
    """
    out: list[dict] = []
    cols = getattr(prices, "columns", [])
    if prices is None or len(cols) == 0:
        return out
    for m in movements:
        et = (m.get("vehicle") or {}).get("etf") or m.get("etf")
        px_rec = m.get("price")
        on = str(m.get("executed_at") or "")[:10]
        if not et or et not in cols or not px_rec or not on:
            continue
        s = prices[et].dropna()
        by_date = {str(i)[:10]: float(v) for i, v in s.items()}
        same = by_date.get(on)
        if same is None:
            continue
        drift = (float(px_rec) - same) / same * 100.0
        if abs(drift) <= tol_pct:
            continue
        prior = [v for k, v in sorted(by_date.items()) if k < on]
        matches_prior = bool(prior) and abs(float(px_rec) - prior[-1]) / prior[-1] * 100.0 <= 0.5
        out.append({
            "movement_id": m.get("id"), "etf": et, "executed_at": on,
            "recorded_price": round(float(px_rec), 4), "close_on_date": round(same, 4),
            "drift_pct": round(drift, 2),
            "matches_prior_close": matches_prior,
            "note": ("recorded price matches the PRIOR session's close — likely logged from the "
                     "last known quote rather than the fill; qty is derived from it, so both the "
                     "share count and the day-one return are suspect"
                     if matches_prior else "recorded price differs materially from that day's close"),
        })
    return out


def twr_series(value, flow, base: float = 100.0) -> list[dict]:
    """Time-weighted NAV from a daily market value and the day's external flow.

        r_t = V_t / (V_{t−1} + F_t) − 1

    START-of-day convention: money that arrives on day t is put to work that same day, at the
    price actually paid (F_t is the cash that left the account, and V_t marks the shares it
    bought at the close). The alternative — booking flows at the close, r_t = (V_t − F_t)/V_{t−1}
    — silently drops the first day of every new position, and that is not a rounding difference:
    4COP.DE was bought at 60.62 on 2026-06-05 and closed at 56.41 the same day, a real −6.9% on
    €1000 that the end-of-day convention erases. Between two conventions, the one that cannot
    flatter the record by construction is the right default.

    Days before the book has any value contribute no return (NAV sits flat at `base`), so an
    account starting from zero does not produce a spurious +∞ on its first purchase.
    """
    navs: list[dict] = []
    nav, prev = base, 0.0
    for ts, v in value.items():
        v = float(v)
        f = float(flow.get(ts, 0.0))
        denom = prev + f
        if denom > 1e-9:
            nav *= v / denom
        prev = v
        d = ts.date().isoformat() if hasattr(ts, "date") else str(ts)
        navs.append({"date": d, "nav": round(nav, 4)})
    return navs


def xirr(flows: list[tuple], guess_lo: float = -0.9999, guess_hi: float = 100.0) -> float | None:
    """Annualized money-weighted return (IRR) from [(date, amount)] — contributions NEGATIVE,
    terminal market value POSITIVE. Bisection on NPV: no derivatives, no divergence, and it
    simply returns None when the flows do not bracket a root (e.g. all one sign).
    """
    if len(flows) < 2:
        return None
    ordered = sorted(flows, key=lambda x: x[0])
    t0 = ordered[0][0]
    days = [( (d - t0).days, a) for d, a in ordered]
    if not any(a < 0 for _, a in days) or not any(a > 0 for _, a in days):
        return None

    def npv(rate: float) -> float:
        return sum(a / (1.0 + rate) ** (t / 365.0) for t, a in days)

    lo, hi = guess_lo, guess_hi
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return round((lo + hi) / 2.0, 6)


# ── Core NAV math ────────────────────────────────────────────────────────────

def holdings_nav(holdings: list[dict], prices, base: float = 100.0) -> list[dict]:
    """Buy-and-hold NAV series from holdings ({primary_etf|etf, weight_pct}) and a price frame.

    Missing/short price columns are dropped and their weight becomes cash (held flat), so a
    single unresolvable ETF never poisons the whole series. Returns [{date, nav}] ascending.
    """
    import pandas as pd

    weights = {}
    for h in holdings:
        etf = h.get("primary_etf") or h.get("etf")
        if etf:
            weights[etf] = weights.get(etf, 0.0) + float(h.get("weight_pct", 0.0)) / 100.0
    if prices is None or len(prices) == 0 or not weights:
        return []

    cols = [t for t in weights if t in getattr(prices, "columns", [])]
    if not cols:
        return []
    px = prices[cols].ffill()
    px = px.dropna(how="all")                       # drop leading rows where NOTHING traded yet
    if px.empty:
        return []
    # Only tickers with a real price at the window START form the curve; the rest (e.g.
    # newly-listed ETFs with no history over the window) are held flat as cash. This stops
    # a single short-history ETF from poisoning the whole series via row-wise dropna.
    base_row = px.iloc[0]
    included = [t for t in cols if pd.notna(base_row[t])]
    if not included:
        return []
    px = px[included].ffill().dropna()
    if px.empty:
        return []

    rel = px / px.iloc[0]
    invested = pd.Series(0.0, index=px.index)
    for t in included:
        invested = invested + rel[t] * weights[t]
    cash = 1.0 - sum(weights[t] for t in included)
    nav = base * (invested + cash)

    out = []
    for ts, v in nav.items():
        d = ts.date().isoformat() if hasattr(ts, "date") else str(ts)
        out.append({"date": d, "nav": round(float(v), 4)})
    return out


# ── Persistence (mode-scoped) ────────────────────────────────────────────────

def _persist_nav_rows(portfolio_id: str, mode: str | None, rows: list[dict],
                      lake_dir: Path | None = None) -> None:
    """Write NAV rows for ONE portfolio, replacing only its (portfolio_id, mode) slice so the
    backtest, live, forward and real curves for that portfolio coexist instead of clobbering
    each other. The partition is keyed by {portfolio_id}, so the written frame must contain
    ONLY this portfolio's rows (all its modes) — never other portfolios' (that would duplicate
    them across partition files on read). `mode=None` (real book) replaces the no-mode slice."""
    import pandas as pd

    new = pd.DataFrame(rows)
    existing = lake.read_table(_NAV_TABLE, lake_dir=lake_dir)
    keep = pd.DataFrame()
    if not existing.empty and "portfolio_id" in existing.columns:
        mine = existing[existing["portfolio_id"] == portfolio_id]
        if not mine.empty:
            m = mine.get("mode") if "mode" in mine.columns else None
            drop = (m.isna() if mode is None else (m == mode)) if m is not None else (mode is None)
            keep = mine[~drop] if hasattr(drop, "__len__") else (mine.iloc[0:0] if drop else mine)
    combined = pd.concat([keep, new], ignore_index=True) if not keep.empty else new
    if combined.empty:
        return
    lake.append_partition(_NAV_TABLE, combined, {"portfolio_id": portfolio_id},
                          overwrite=True, lake_dir=lake_dir)


# ── Model-portfolio NAV ──────────────────────────────────────────────────────

def _run_date(run_id: str | None) -> str | None:
    """run_YYYYMMDD_HHMMSS → YYYY-MM-DD."""
    if not run_id:
        return None
    for tok in run_id.split("_"):
        if len(tok) == 8 and tok.isdigit():
            return f"{tok[:4]}-{tok[4:6]}-{tok[6:]}"
    return None


def compute_model_nav(portfolio_id: str, run_id: str | None = None, as_of: str | None = None,
                      backtest_days: int | None = None, price_fn=None, persist: bool = True,
                      lake_dir: Path | None = None, eur: bool = True, ccy_fn=None, fx_fn=None) -> dict:
    """Compute (and persist) the NAV series of a model portfolio's holdings vs its benchmark.

    `backtest_days`: if set, measure the CURRENT holdings over the trailing window
    (today − N days → today) — a buy-and-hold backtest that shows immediately whether the
    book would have beaten the market (vs benchmark_etf, e.g. SPY). Otherwise the series
    starts at the run date and accrues forward.

    `eur`: convert every price series to EUR before the NAV math (default; the book is EUR).
    Conversion runs on the default yfinance price path, or when a `ccy_fn` is injected — so
    tests that inject only a synthetic `price_fn` stay FX-free.
    """
    from catalyx.execution import portfolio as pf

    convert = eur and (ccy_fn is not None or price_fn is None)
    price_fn = price_fn or yfinance_prices
    shown = pf.show_holdings(portfolio_id, run_id=run_id, lake_dir=lake_dir)
    holdings = shown.get("holdings", [])
    if not holdings:
        return {"portfolio_id": portfolio_id, "error": "no holdings — build the portfolio first"}
    run_id = shown["run_id"]

    mode = "backtest" if backtest_days else "forward"
    end = as_of or date.today().isoformat()
    if backtest_days:
        start = (date.today() - timedelta(days=backtest_days)).isoformat()
    else:
        start = _run_date(run_id) or date.today().isoformat()

    try:
        profile = pf.load_profile(portfolio_id)
        benchmark = profile.get("benchmark_etf")
    except FileNotFoundError:
        benchmark = None

    etfs = [h["primary_etf"] for h in holdings if h.get("primary_etf")]
    tickers = list(dict.fromkeys(etfs + ([benchmark] if benchmark else [])))
    prices = price_fn(tickers, start, end)
    if convert:
        prices = _eur_prices(prices, start, end, ccy_fn or _default_ccy_fn, fx_fn or _default_fx_fn)

    port = holdings_nav(holdings, prices)
    bench = holdings_nav([{"etf": benchmark, "weight_pct": 100.0}], prices) if benchmark else []
    bench_by_date = {b["date"]: b["nav"] for b in bench}

    cfg_ver = holdings[0].get("config_version")
    computed_at = datetime.now(timezone.utc)
    rows = []
    for p in port:
        bnav = bench_by_date.get(p["date"])
        rows.append({
            "portfolio_id": portfolio_id, "kind": "model", "mode": mode, "run_id": run_id,
            "config_version": cfg_ver, "date": p["date"], "nav": p["nav"],
            "return_pct": round(p["nav"] - 100.0, 4),
            "benchmark_etf": benchmark, "benchmark_nav": bnav,
            "vs_benchmark_pct": round(p["nav"] - bnav, 4) if bnav is not None else None,
            "computed_at": computed_at,
        })

    if persist and rows:
        _persist_nav_rows(portfolio_id, mode, rows, lake_dir=lake_dir)

    last = rows[-1] if rows else None
    return {"portfolio_id": portfolio_id, "run_id": run_id, "start": start, "end": end,
            "points": len(rows), "benchmark": benchmark,
            "last_nav": last["nav"] if last else None,
            "last_return_pct": last["return_pct"] if last else None,
            "last_vs_benchmark_pct": last["vs_benchmark_pct"] if last else None,
            # The benchmark's OWN return, alongside the differential. `vs_benchmark_pct` is
            # `nav − benchmark_nav`, i.e. a DIFFERENCE in index points — and printing it under a
            # bare "SPY" label read as the benchmark's return, inverting the sign of the only
            # number that answers "is any of this working?". On 2026-08-27 the CLI said
            # "[SPY -5.39%]" while SPY had returned +4.44% and the book was 5.39pp BEHIND it;
            # the misreading reached CHANGELOG v3.5 as prose. Both numbers now ship, both named.
            "last_benchmark_return_pct": (round(last["benchmark_nav"] - 100.0, 4)
                                          if last and last.get("benchmark_nav") is not None
                                          else None),
            "series": rows}


def _runs_with_holdings(portfolio_id: str, since: str | None, lake_dir: Path | None):
    """Runs that have holdings for this portfolio, on/after `since` (inception). Collapses
    multiple same-day runs to the LATEST run that day (intra-day recomputes aren't real
    rebalances). Returns [(run_date, run_id)] ascending by date."""
    from catalyx.execution import portfolio as pf

    df = lake.read_table(pf._HOLDING_TABLE, lake_dir=lake_dir)
    if df.empty or "portfolio_id" not in df.columns:
        return []
    df = df[df["portfolio_id"] == portfolio_id]
    if df.empty:
        return []
    by_day: dict[str, str] = {}
    for rid in sorted(df["run_id"].dropna().unique()):
        d = _run_date(rid)
        if not d or (since and d < since):
            continue
        by_day[d] = rid                      # later run_id wins for the day (sorted asc)
    return sorted(by_day.items())


def compute_live_nav(portfolio_id: str, inception: str | None = None, as_of: str | None = None,
                     price_fn=None, persist: bool = True, lake_dir: Path | None = None,
                     eur: bool = True, ccy_fn=None, fx_fn=None) -> dict:
    """Walk-forward, no-look-ahead track record (mode='live').

    Each score_run on/after inception is a rebalance point. For consecutive runs k → k+1, the
    holdings ACTUALLY chosen at run_k are valued over [date_k, date_{k+1}] with real prices and
    the segment return is CHAINED onto the running NAV; the latest run accrues to today. Unlike
    `mode=backtest` (today's holdings projected backwards — hypothetical), this only ever uses
    holdings that were live in each interval, so it is a genuine forward equity curve.

    Persisted with mode='live' (the `backtest` rows are left untouched, kept for reference).
    """
    from catalyx.execution import portfolio as pf

    convert = eur and (ccy_fn is not None or price_fn is None)
    price_fn = price_fn or yfinance_prices
    inception = inception or weights_cfg.track_record_inception()
    runs = _runs_with_holdings(portfolio_id, inception, lake_dir)
    if not runs:
        return {"portfolio_id": portfolio_id,
                "error": f"no holdings on/after inception {inception!r} — build portfolios first"}

    try:
        benchmark = pf.load_profile(portfolio_id).get("benchmark_etf")
    except FileNotFoundError:
        benchmark = None

    # one price pull for the whole window + every ETF ever held, then slice per segment.
    holdings_by_run = {rid: pf.show_holdings(portfolio_id, run_id=rid, lake_dir=lake_dir).get("holdings", [])
                       for _, rid in runs}
    end = as_of or date.today().isoformat()
    # Anchor the curve at INCEPTION, not at the first run date. The first run can land on a
    # non-trading day (e.g. a weekend recompute) with no market day between it and `end` — the
    # curve would then collapse to a single point and read forever as 'accruing'. inception is a
    # real trading day (= the first live position), so valuing the first run's holdings from there
    # gives a genuine NAV=100 anchor + today's point (≥2). No price look-ahead: inception's close
    # is known on inception day; we only attribute the first run's (already-chosen) holdings to it.
    start = min(inception, runs[0][0]) if inception else runs[0][0]
    all_etfs = {h.get("primary_etf") for hs in holdings_by_run.values() for h in hs if h.get("primary_etf")}
    tickers = list(dict.fromkeys(list(all_etfs) + ([benchmark] if benchmark else [])))
    prices = price_fn(tickers, start, end)
    if convert:
        prices = _eur_prices(prices, start, end, ccy_fn or _default_ccy_fn, fx_fn or _default_fx_fn)

    running = 100.0
    chained: list[dict] = []
    for i, (d_k, rid) in enumerate(runs):
        seg_start = start if i == 0 else d_k                 # first segment anchored at inception
        seg_end = runs[i + 1][0] if i + 1 < len(runs) else end
        seg_px = prices.loc[seg_start:seg_end] if prices is not None and len(prices) else prices
        seg = holdings_nav(holdings_by_run[rid], seg_px)     # indexed 100 at seg_start
        if not seg:
            continue
        for j, pt in enumerate(seg):
            if i > 0 and j == 0:
                continue                                      # boundary dup with prev segment's last point
            chained.append({"date": pt["date"], "nav": round(running * pt["nav"] / 100.0, 4)})
        running = running * seg[-1]["nav"] / 100.0

    # benchmark: a single continuous buy-and-hold over the whole window (directly comparable)
    bench = holdings_nav([{"etf": benchmark, "weight_pct": 100.0}], prices) if benchmark else []
    bench_by_date = {b["date"]: b["nav"] for b in bench}

    computed_at = datetime.now(timezone.utc)
    rows = []
    for p in chained:
        bnav = bench_by_date.get(p["date"])
        rows.append({
            "portfolio_id": portfolio_id, "kind": "model", "mode": "live", "run_id": runs[-1][1],
            "config_version": None, "date": p["date"], "nav": p["nav"],
            "return_pct": round(p["nav"] - 100.0, 4),
            "benchmark_etf": benchmark, "benchmark_nav": bnav,
            "vs_benchmark_pct": round(p["nav"] - bnav, 4) if bnav is not None else None,
            "computed_at": computed_at,
        })

    if persist:
        # persist even when empty → clears any stale live slice (e.g. an earlier inception)
        _persist_nav_rows(portfolio_id, "live", rows, lake_dir=lake_dir)

    last = rows[-1] if rows else None
    return {"portfolio_id": portfolio_id, "mode": "live", "inception": inception,
            "rebalances": len(runs), "start": start, "end": end, "points": len(rows),
            "benchmark": benchmark,
            "last_nav": last["nav"] if last else None,
            "last_return_pct": last["return_pct"] if last else None,
            "last_vs_benchmark_pct": last["vs_benchmark_pct"] if last else None,
            # The benchmark's OWN return, alongside the differential. `vs_benchmark_pct` is
            # `nav − benchmark_nav`, i.e. a DIFFERENCE in index points — and printing it under a
            # bare "SPY" label read as the benchmark's return, inverting the sign of the only
            # number that answers "is any of this working?". On 2026-08-27 the CLI said
            # "[SPY -5.39%]" while SPY had returned +4.44% and the book was 5.39pp BEHIND it;
            # the misreading reached CHANGELOG v3.5 as prose. Both numbers now ship, both named.
            "last_benchmark_return_pct": (round(last["benchmark_nav"] - 100.0, 4)
                                          if last and last.get("benchmark_nav") is not None
                                          else None),
            "series": rows}


def compute_real_nav(portfolio_id: str, start: str | None = None, as_of: str | None = None,
                     benchmark: str | None = None, price_fn=None, persist: bool = True,
                     lake_dir: Path | None = None, eur: bool = True, ccy_fn=None, fx_fn=None) -> dict:
    """NAV series of the REAL book (from the movement LEDGER, not a snapshot), in EUR.

    The curve is **time-weighted** (`twr_series`): each day is scored on the positions actually
    held that day, with the day's contribution/withdrawal removed before the return is taken. So
    `nav`/`return_pct` answer "how did my selection do", uncontaminated by WHEN money went in.
    See the ledger section above for why the previous snapshot-projected curve was wrong.

    Three different questions, three numbers, none of them a substitute for another:
      • `twr_pct`   — the curve's endpoint. Comparable to the benchmark and to the model leg.
      • `mwr_pct`   — IRR over the real flows: what YOUR money earned given its timing.
      • `pnl_eur` / `return_pct_vs_cost` — market value vs cost basis: the broker's view.

    Also decomposes the FX effect: `fx_pnl_eur` = how much of the current P&L comes purely from
    EUR/USD & EUR/GBP moves since each position was opened (qty·price_now_native·(fx_now−fx_entry)),
    so a EUR loss can be split into asset performance vs currency."""
    import pandas as pd
    from catalyx.store import movement_repo

    convert = eur and (ccy_fn is not None or price_fn is None)
    price_fn = price_fn or yfinance_prices
    rh = movement_repo.positions()
    holdings = rh.get("holdings", [])
    movs = movement_repo.load_all()
    if not movs:
        return {"portfolio_id": portfolio_id, "error": "no movements recorded"}

    if start is None:
        start = min((m["executed_at"][:10] for m in movs), default=date.today().isoformat())
    end = as_of or date.today().isoformat()
    # earliest open date per ETF → the FX reference date for that holding's currency attribution
    entry_by_etf: dict[str, str] = {}
    for m in sorted(movs, key=lambda x: x.get("executed_at", "")):
        et = (m.get("vehicle") or {}).get("etf") or m.get("etf")
        if et and et not in entry_by_etf:
            entry_by_etf[et] = m["executed_at"][:10]

    # EVERY ETF ever held, not just the open ones — a closed position still owns the stretch of
    # curve during which it was held (dropping it would flatter the record by construction).
    etfs = list(entry_by_etf)
    tickers = list(dict.fromkeys(etfs + ([benchmark] if benchmark else [])))
    # A short lookback BEFORE the first movement: `execution_price_checks` needs the prior
    # session's close to recognise a fill logged from a stale quote. Sliced off before any NAV
    # math, so it never shifts the curve's anchor.
    probe_start = (date.fromisoformat(start) - timedelta(days=10)).isoformat()
    native_ext = price_fn(tickers, probe_start, end)
    native = native_ext.loc[start:] if len(getattr(native_ext, "index", [])) else native_ext

    # currency + FX series (kept explicit here so we can attribute the FX effect)
    ccy_map = (ccy_fn or _default_ccy_fn)(tickers) if convert else {t: "EUR" for t in tickers}
    needed = {("GBP" if c in ("GBp", "GBX") else c) for c in ccy_map.values()} - {"EUR"}
    fx_by_ccy = (fx_fn or _default_fx_fn)(needed, start, end) if (convert and needed) else {}
    prices = _to_eur(native, ccy_map, fx_by_ccy) if convert else native

    # ── time-weighted EUR NAV series, from the daily ledger ──────────────────
    total_cost = sum(float(h.get("invested_eur", 0.0)) for h in holdings)
    px_cols = [t for t in etfs if t in getattr(prices, "columns", [])]
    port: list[dict] = []
    value = flow = None
    if px_cols and len(getattr(prices, "index", [])):
        pxe = prices[px_cols].ffill().dropna(how="all")
        qty, flow = daily_ledger(movs, pxe.index)
        cols = [c for c in qty.columns if c in pxe.columns]
        value = (qty[cols] * pxe[cols].ffill()).sum(axis=1)
        # An ETF with no price column cannot be marked; hold its net contributed cash flat so it
        # neither vanishes from the book nor invents a return it did not earn.
        unpriced = [c for c in qty.columns if c not in pxe.columns]
        if unpriced:
            spent = pd.Series(0.0, index=pxe.index)
            for m in movs:
                et = (m.get("vehicle") or {}).get("etf") or m.get("etf")
                if et in unpriced:
                    stamp = pd.Timestamp(str(m["executed_at"])[:10])
                    if getattr(pxe.index, "tz", None) is not None:
                        stamp = stamp.tz_localize(pxe.index.tz)
                    hit = pxe.index[pxe.index >= stamp]
                    if len(hit):
                        sgn = 1.0 if m.get("action") in ("open", "add") else -1.0
                        spent.loc[hit[0]:] += sgn * float(m.get("amount_eur") or 0.0)
            value = value + spent
        port = twr_series(value, flow)
    if not port:
        port = holdings_nav(holdings, prices)                 # fallback (no prices at all)

    # benchmark: buy-and-hold, EUR, indexed 100 at start (directly comparable)
    bench = holdings_nav([{"etf": benchmark, "weight_pct": 100.0}], prices) if benchmark else []
    bench_by_date = {b["date"]: b["nav"] for b in bench}

    # ── FX-effect attribution at the endpoint ────────────────────────────────
    def _fx_at(ccy_base: str, on: str) -> float | None:
        s = fx_by_ccy.get(ccy_base)
        if s is None:
            return 1.0 if ccy_base == "EUR" else None
        s2 = s[s.index <= (on + " 23:59:59")] if len(s) else s
        return float(s2.iloc[-1]) if len(s2) else (float(s.iloc[0]) if len(s) else None)

    fx_pnl_eur = 0.0
    fx_breakdown: list[dict] = []
    for h in holdings:
        et = h["etf"]
        ccy = ccy_map.get(et, "EUR")
        base = "GBP" if ccy in ("GBp", "GBX") else ccy
        scale = 0.01 if ccy in ("GBp", "GBX") else 1.0
        if base == "EUR" or et not in getattr(native, "columns", []):
            continue
        pn = native[et].ffill().dropna()
        if pn.empty:
            continue
        price_now = float(pn.iloc[-1]) * scale
        fx_now = _fx_at(base, end)
        fx_entry = _fx_at(base, entry_by_etf.get(et, start))
        if fx_now is None or fx_entry is None:
            continue
        contrib = float(h.get("qty", 0.0)) * price_now * (fx_now - fx_entry)
        fx_pnl_eur += contrib
        fx_breakdown.append({"etf": et, "ccy": base, "fx_entry": round(fx_entry, 4),
                             "fx_now": round(fx_now, 4), "fx_pnl_eur": round(contrib, 2)})

    # value/flow per date, so the persisted curve carries the ledger it was derived from
    val_by_date, flow_by_date = {}, {}
    if value is not None:
        for ts, v in value.items():
            d = ts.date().isoformat() if hasattr(ts, "date") else str(ts)
            val_by_date[d] = float(v)
            flow_by_date[d] = float(flow.get(ts, 0.0)) if flow is not None else 0.0

    computed_at = datetime.now(timezone.utc)
    rows = []
    net_contributed = 0.0
    for p in port:
        bnav = bench_by_date.get(p["date"])
        net_contributed += flow_by_date.get(p["date"], 0.0)
        rows.append({
            "portfolio_id": portfolio_id, "kind": "real", "mode": None, "run_id": None,
            "config_version": None, "date": p["date"], "nav": p["nav"],
            "return_pct": round(p["nav"] - 100.0, 4),
            "benchmark_etf": benchmark, "benchmark_nav": bnav,
            "vs_benchmark_pct": round(p["nav"] - bnav, 4) if bnav is not None else None,
            # ledger columns: `nav`/`return_pct` are TIME-WEIGHTED, so they deliberately do NOT
            # equal the broker's P&L. These three make that difference auditable per date.
            "value_eur": round(val_by_date[p["date"]], 2) if p["date"] in val_by_date else None,
            "flow_eur": round(flow_by_date.get(p["date"], 0.0), 2) if val_by_date else None,
            "net_contributed_eur": round(net_contributed, 2) if val_by_date else None,
            "computed_at": computed_at,
        })

    last = rows[-1] if rows else None
    # Broker view: marked value of the OPEN book vs what it cost. Closed positions have left the
    # ledger's qty by construction, so their result lives in `realized_eur`, not here.
    value_eur = last.get("value_eur") if last else None
    if value_eur is None and last:                            # holdings_nav fallback
        value_eur = round(total_cost * last["nav"] / 100.0, 2)
    pnl_eur = round(value_eur - total_cost, 2) if value_eur is not None else None

    # MWR: every external flow as it happened, plus the terminal value as the closing inflow.
    mwr = None
    if value is not None and flow is not None and value_eur is not None:
        cash = [(ts.date() if hasattr(ts, "date") else ts, -float(f))
                for ts, f in flow.items() if abs(float(f)) > 1e-9]
        if cash:
            end_ts = value.index[-1]
            cash.append((end_ts.date() if hasattr(end_ts, "date") else end_ts, float(value_eur)))
            mwr = xirr(cash)

    # MWR is an endpoint, not a series — carried on the final row so the dashboard can read it
    # from `portfolio_nav` without re-deriving the flows.
    if last is not None:
        last["mwr_pct"] = round(mwr * 100.0, 2) if mwr is not None else None
    if persist and rows:
        _persist_nav_rows(portfolio_id, None, rows, lake_dir=lake_dir)

    return {"portfolio_id": portfolio_id, "kind": "real", "start": start, "end": end,
            "points": len(rows), "benchmark": benchmark, "cost_eur": round(total_cost, 2),
            "value_eur": value_eur, "pnl_eur": pnl_eur,
            # three questions, three numbers — see the docstring
            "twr_pct": last["return_pct"] if last else None,
            "mwr_pct": round(mwr * 100.0, 2) if mwr is not None else None,
            "return_pct_vs_cost": round(pnl_eur / total_cost * 100.0, 2)
                                  if (pnl_eur is not None and total_cost) else None,
            "realized_eur": rh.get("realized_eur"),
            "execution_price_warnings": execution_price_checks(movs, native_ext),
            "fx_pnl_eur": round(fx_pnl_eur, 2), "fx_breakdown": fx_breakdown,
            "last_nav": last["nav"] if last else None,
            "last_return_pct": last["return_pct"] if last else None,
            "last_vs_benchmark_pct": last["vs_benchmark_pct"] if last else None,
            # The benchmark's OWN return, alongside the differential. `vs_benchmark_pct` is
            # `nav − benchmark_nav`, i.e. a DIFFERENCE in index points — and printing it under a
            # bare "SPY" label read as the benchmark's return, inverting the sign of the only
            # number that answers "is any of this working?". On 2026-08-27 the CLI said
            # "[SPY -5.39%]" while SPY had returned +4.44% and the book was 5.39pp BEHIND it;
            # the misreading reached CHANGELOG v3.5 as prose. Both numbers now ship, both named.
            "last_benchmark_return_pct": (round(last["benchmark_nav"] - 100.0, 4)
                                          if last and last.get("benchmark_nav") is not None
                                          else None),
            "series": rows}


def show_nav(portfolio_id: str, lake_dir: Path | None = None) -> dict:
    df = lake.read_table(_NAV_TABLE, lake_dir=lake_dir)
    if df.empty or "portfolio_id" not in df.columns:
        return {"portfolio_id": portfolio_id, "series": []}
    df = df[df["portfolio_id"] == portfolio_id].sort_values("date")
    return {"portfolio_id": portfolio_id, "series": df.to_dict(orient="records")}


# ── CLI ──────────────────────────────────────────────────────────────────────

def _bench_line(r: dict) -> str:
    """`return=X%  SPY +Y%  →  vs benchmark ±Zpp` — the differential is never printed alone.

    `vs_benchmark_pct` is a difference in index points, and a bare benchmark label in front of it
    reads as the benchmark's own return. That misreading is how a book 5.4pp BEHIND SPY came to
    be described as ahead of it. Three numbers, three names, one line.
    """
    ret, bench, vsb = (r.get("last_return_pct"), r.get("last_benchmark_return_pct"),
                       r.get("last_vs_benchmark_pct"))
    out = f"return={ret:+}%" if ret is not None else "return=n/a (no price data)"
    if bench is not None:
        out += f"  {r.get('benchmark')} {bench:+}%"
    if vsb is not None:
        out += f"  →  vs benchmark {vsb:+}pp"
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="CATALYX portfolio NAV engine (Fase D.2)")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("model", help="Compute a model portfolio's NAV vs benchmark")
    m.add_argument("portfolio_id")
    m.add_argument("--run-id", default=None)
    m.add_argument("--as-of", default=None)
    m.add_argument("--backtest-days", type=int, default=None,
                   help="Trailing backtest window (e.g. 180) — current holdings vs market over last N days")
    lv = sub.add_parser("live", help="Walk-forward track record (chains each run's holdings from inception)")
    lv.add_argument("portfolio_id")
    lv.add_argument("--inception", default=None, help="Override inception date (default: config track_record.yaml)")
    lv.add_argument("--as-of", default=None)
    la = sub.add_parser("live-all", help="Walk-forward track record for EVERY model portfolio, "
                                        "in ONE pass (post_run.sh's call)")
    la.add_argument("--as-of", default=None)
    la.add_argument("--only", default=None, help="comma-separated portfolio ids (default: all)")
    rl = sub.add_parser("real", help="Compute the real book's NAV from the movement files")
    rl.add_argument("portfolio_id")
    rl.add_argument("--start", default=None)
    rl.add_argument("--as-of", default=None)
    rl.add_argument("--benchmark", default=None)
    s = sub.add_parser("show", help="Show a portfolio's stored NAV series")
    s.add_argument("portfolio_id")
    args = p.parse_args()

    if args.cmd == "model":
        r = compute_model_nav(args.portfolio_id, run_id=args.run_id, as_of=args.as_of,
                              backtest_days=args.backtest_days)
        if r.get("error"):
            print(f"  {args.portfolio_id}: {r['error']}")
            return
        print(f"  {r['portfolio_id']}  {r['start']} → {r['end']}  ({r['points']} pts)")
        print(f"  last NAV={r['last_nav']}  {_bench_line(r)}")
    elif args.cmd == "live":
        r = compute_live_nav(args.portfolio_id, inception=args.inception, as_of=args.as_of)
        if r.get("error"):
            print(f"  {args.portfolio_id}: {r['error']}")
            return
        print(f"  {r['portfolio_id']} (live)  inception={r['inception']}  rebalances={r['rebalances']}  "
              f"{r['start']} → {r['end']}  ({r['points']} pts)")
        print(f"  last NAV={r['last_nav']}  {_bench_line(r)}")
    elif args.cmd == "live-all":
        # `post_run.sh` used to run this loop as four separate processes: four interpreter
        # startups, four import graphs and four passes over the same parquet, to answer four
        # questions about one price cache. Same numbers, one process.
        from catalyx.execution import portfolio as pf
        ids = [x.strip() for x in args.only.split(",")] if args.only else pf.list_profiles()
        for pid in ids:
            r = compute_live_nav(pid, as_of=args.as_of)
            if r.get("error"):
                print(f"  {pid}: {r['error']}")
                continue
            print(f"  {r['portfolio_id']} (live)  inception={r['inception']}  "
                  f"rebalances={r['rebalances']}  {r['start']} → {r['end']}  ({r['points']} pts)")
            print(f"  last NAV={r['last_nav']}  {_bench_line(r)}")
    elif args.cmd == "real":
        r = compute_real_nav(args.portfolio_id, start=args.start, as_of=args.as_of,
                             benchmark=args.benchmark)
        if r.get("error"):
            print(f"  {args.portfolio_id}: {r['error']}")
            return
        print(f"  {r['portfolio_id']} (real)  {r['start']} → {r['end']}  ({r['points']} pts)")
        # Three questions, three numbers. They are SUPPOSED to differ; a single "return" for a
        # book that receives contributions has to lie about at least two of them.
        mwr = r.get("mwr_pct")
        bench, vsb = r.get("last_benchmark_return_pct"), r.get("last_vs_benchmark_pct")
        tail = (f"   {r['benchmark']} {bench:+}%  →  vs benchmark {vsb:+}pp"
                if bench is not None and vsb is not None else "")
        print(f"    TWR (selection)               = {r['twr_pct']:+}%{tail}")
        print(f"    MWR (your money, IRR ann.)    = {mwr:+}%" if mwr is not None
              else "    MWR (your money, IRR ann.)    = n/a")
        print(f"    vs cost   (broker view)       = {r['return_pct_vs_cost']:+}%")
        if r.get("cost_eur") is not None:
            print(f"    cost=€{r['cost_eur']}  value=€{r['value_eur']}  P&L=€{r['pnl_eur']:+}  "
                  f"(of which FX: €{r['fx_pnl_eur']:+})")
            for b in r.get("fx_breakdown", []):
                print(f"      {b['etf']:9} {b['ccy']}  fx {b['fx_entry']}→{b['fx_now']}  "
                      f"FX P&L=€{b['fx_pnl_eur']:+}")
        for w in r.get("execution_price_warnings", []):
            print(f"    [!] {w['etf']} {w['executed_at']}: recorded €{w['recorded_price']} vs "
                  f"close €{w['close_on_date']} ({w['drift_pct']:+}%)"
                  + ("  — matches the PRIOR session's close" if w["matches_prior_close"] else ""))
    elif args.cmd == "show":
        r = show_nav(args.portfolio_id)
        for row in r["series"]:
            print(f"  {row['date']}  nav={row['nav']:>8}  ret={row.get('return_pct'):>7}%  "
                  f"vs_bench={row.get('vs_benchmark_pct')}")


if __name__ == "__main__":
    main()
