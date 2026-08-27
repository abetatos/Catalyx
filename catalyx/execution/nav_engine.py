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
    """Default price_fn: adjusted-close DataFrame (index=date, columns=tickers).

    yfinance's `end` is EXCLUSIVE, which silently drops the last day of every window —
    e.g. asking [start, today] never returns today's close, so a curve that should end
    today loses its final (and on a 1-trading-day window, its ONLY) point. We push end
    out by one calendar day so the caller's `end` is treated inclusively.
    """
    import pandas as pd
    import yfinance as yf

    end_excl = (date.fromisoformat(end[:10]) + timedelta(days=1)).isoformat()
    data = yf.download(tickers, start=start, end=end_excl, progress=False, auto_adjust=True)
    closes = data["Close"] if isinstance(data.columns, pd.MultiIndex) or "Close" in getattr(data, "columns", []) else data
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(tickers[0])
    return closes


# ── FX → EUR (the book, and every comparison, is denominated in EUR) ──────────
#
# yfinance serves each ETF's price in its LISTING currency (4COP.DE/IQQH.DE=EUR,
# USPY.L=USD, SEMI.L=GBP, SPY=USD). Summing those natively is meaningless for a
# EUR investor, so every price series is converted to EUR before any NAV math —
# NAV(t) = Σ w_i · [p_i(t)·fx_i(t)] / [p_i(t0)·fx_i(t0)]. CLAUDE.md: all P&L in EUR.

def _default_ccy_fn(tickers: list[str]) -> dict[str, str]:
    """{ticker: listing currency}. Best-effort via yfinance; unknown → 'EUR' (no conversion)."""
    import yfinance as yf
    out: dict[str, str] = {}
    for t in tickers:
        try:
            out[t] = (yf.Ticker(t).fast_info.get("currency") or "EUR")
        except Exception:
            out[t] = "EUR"
    return out


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
            "series": rows}


def compute_real_nav(portfolio_id: str, start: str | None = None, as_of: str | None = None,
                     benchmark: str | None = None, price_fn=None, persist: bool = True,
                     lake_dir: Path | None = None, eur: bool = True, ccy_fn=None, fx_fn=None) -> dict:
    """NAV series of the REAL book (from the movement files → net holdings), in EUR.

    Unlike the model leg, the real curve is anchored to the ACTUAL EUR cost basis, not to the
    entry-date market close: NAV(t) = 100 · Σ_i qty_i·price_i(t)_EUR / Σ_i invested_eur_i. So the
    last point's return_pct IS the true mark-to-market P&L you'd see in the broker.

    Also decomposes the FX effect: `fx_pnl_eur` = how much of the current P&L comes purely from
    EUR/USD & EUR/GBP moves since each position was opened (qty·price_now_native·(fx_now−fx_entry)),
    so a EUR loss can be split into asset performance vs currency."""
    import pandas as pd
    from catalyx.store import movement_repo

    convert = eur and (ccy_fn is not None or price_fn is None)
    price_fn = price_fn or yfinance_prices
    rh = movement_repo.positions()
    holdings = rh.get("holdings", [])
    if not holdings:
        return {"portfolio_id": portfolio_id, "error": "no open real positions"}

    movs = movement_repo.load_all()
    if start is None:
        start = min((m["executed_at"][:10] for m in movs), default=date.today().isoformat())
    end = as_of or date.today().isoformat()
    # earliest open date per ETF → the FX reference date for that holding's currency attribution
    entry_by_etf: dict[str, str] = {}
    for m in sorted(movs, key=lambda x: x.get("executed_at", "")):
        et = m.get("etf")
        if et and et not in entry_by_etf:
            entry_by_etf[et] = m["executed_at"][:10]

    etfs = [h["etf"] for h in holdings]
    tickers = list(dict.fromkeys(etfs + ([benchmark] if benchmark else [])))
    native = price_fn(tickers, start, end)

    # currency + FX series (kept explicit here so we can attribute the FX effect)
    ccy_map = (ccy_fn or _default_ccy_fn)(tickers) if convert else {t: "EUR" for t in tickers}
    needed = {("GBP" if c in ("GBp", "GBX") else c) for c in ccy_map.values()} - {"EUR"}
    fx_by_ccy = (fx_fn or _default_fx_fn)(needed, start, end) if (convert and needed) else {}
    prices = _to_eur(native, ccy_map, fx_by_ccy) if convert else native

    # ── cost-basis-anchored EUR NAV series ───────────────────────────────────
    total_cost = sum(float(h.get("invested_eur", 0.0)) for h in holdings)
    px_cols = [h["etf"] for h in holdings if h["etf"] in getattr(prices, "columns", [])]
    port: list[dict] = []
    if total_cost > 0 and px_cols:
        pxe = prices[px_cols].ffill().dropna(how="all")
        value = pd.Series(0.0, index=pxe.index)
        covered_cost = 0.0
        for h in holdings:
            et = h["etf"]
            if et in pxe.columns:
                value = value + float(h.get("qty", 0.0)) * pxe[et].ffill()
                covered_cost += float(h.get("invested_eur", 0.0))
        value = value + (total_cost - covered_cost)          # unpriced holdings held flat at cost
        nav_series = 100.0 * value / total_cost
        port = [{"date": (ts.date().isoformat() if hasattr(ts, "date") else str(ts)),
                 "nav": round(float(v), 4)} for ts, v in nav_series.items()]
    else:
        port = holdings_nav(holdings, prices)                 # fallback (no cost/qty)

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

    computed_at = datetime.now(timezone.utc)
    rows = []
    for p in port:
        bnav = bench_by_date.get(p["date"])
        rows.append({
            "portfolio_id": portfolio_id, "kind": "real", "mode": None, "run_id": None,
            "config_version": None, "date": p["date"], "nav": p["nav"],
            "return_pct": round(p["nav"] - 100.0, 4),
            "benchmark_etf": benchmark, "benchmark_nav": bnav,
            "vs_benchmark_pct": round(p["nav"] - bnav, 4) if bnav is not None else None,
            "computed_at": computed_at,
        })

    if persist and rows:
        _persist_nav_rows(portfolio_id, None, rows, lake_dir=lake_dir)

    last = rows[-1] if rows else None
    pnl_eur = round(total_cost * (last["nav"] / 100.0 - 1.0), 2) if last else None
    return {"portfolio_id": portfolio_id, "kind": "real", "start": start, "end": end,
            "points": len(rows), "benchmark": benchmark, "cost_eur": round(total_cost, 2),
            "value_eur": round(total_cost * last["nav"] / 100.0, 2) if last else None,
            "pnl_eur": pnl_eur,
            "fx_pnl_eur": round(fx_pnl_eur, 2), "fx_breakdown": fx_breakdown,
            "last_nav": last["nav"] if last else None,
            "last_return_pct": last["return_pct"] if last else None,
            "series": rows}


def show_nav(portfolio_id: str, lake_dir: Path | None = None) -> dict:
    df = lake.read_table(_NAV_TABLE, lake_dir=lake_dir)
    if df.empty or "portfolio_id" not in df.columns:
        return {"portfolio_id": portfolio_id, "series": []}
    df = df[df["portfolio_id"] == portfolio_id].sort_values("date")
    return {"portfolio_id": portfolio_id, "series": df.to_dict(orient="records")}


# ── CLI ──────────────────────────────────────────────────────────────────────

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
        vsb = r["last_vs_benchmark_pct"]
        ret = r["last_return_pct"]
        vsb_str = f"{vsb:+}" if vsb is not None else "n/a"
        ret_str = f"{ret:+}%" if ret is not None else "n/a (no price data)"
        print(f"  {r['portfolio_id']}  {r['start']} → {r['end']}  ({r['points']} pts)")
        print(f"  last NAV={r['last_nav']}  return={ret_str}  vs {r['benchmark']}={vsb_str}")
    elif args.cmd == "live":
        r = compute_live_nav(args.portfolio_id, inception=args.inception, as_of=args.as_of)
        if r.get("error"):
            print(f"  {args.portfolio_id}: {r['error']}")
            return
        vsb = r["last_vs_benchmark_pct"]
        ret = r["last_return_pct"]
        vsb_str = f"{vsb:+}" if vsb is not None else "n/a"
        ret_str = f"{ret:+}%" if ret is not None else "n/a (no price data)"
        print(f"  {r['portfolio_id']} (live)  inception={r['inception']}  rebalances={r['rebalances']}  "
              f"{r['start']} → {r['end']}  ({r['points']} pts)")
        print(f"  last NAV={r['last_nav']}  return={ret_str}  vs {r['benchmark']}={vsb_str}")
    elif args.cmd == "real":
        r = compute_real_nav(args.portfolio_id, start=args.start, as_of=args.as_of,
                             benchmark=args.benchmark)
        if r.get("error"):
            print(f"  {args.portfolio_id}: {r['error']}")
            return
        print(f"  {r['portfolio_id']} (real)  {r['start']} → {r['end']}  ({r['points']} pts)  "
              f"last NAV={r['last_nav']}  return={r['last_return_pct']:+}%")
        if r.get("cost_eur") is not None:
            print(f"    cost=€{r['cost_eur']}  value=€{r['value_eur']}  P&L=€{r['pnl_eur']:+}  "
                  f"(of which FX: €{r['fx_pnl_eur']:+})")
            for b in r.get("fx_breakdown", []):
                print(f"      {b['etf']:9} {b['ccy']}  fx {b['fx_entry']}→{b['fx_now']}  "
                      f"FX P&L=€{b['fx_pnl_eur']:+}")
    elif args.cmd == "show":
        r = show_nav(args.portfolio_id)
        for row in r["series"]:
            print(f"  {row['date']}  nav={row['nav']:>8}  ret={row.get('return_pct'):>7}%  "
                  f"vs_bench={row.get('vs_benchmark_pct')}")


if __name__ == "__main__":
    main()
