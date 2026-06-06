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
from datetime import date, datetime, timezone
from pathlib import Path

from catalyx.store import lake

_NAV_TABLE = "portfolio_nav"


# ── Price source (injectable) ────────────────────────────────────────────────

def yfinance_prices(tickers: list[str], start: str, end: str):
    """Default price_fn: adjusted-close DataFrame (index=date, columns=tickers)."""
    import pandas as pd
    import yfinance as yf

    data = yf.download(tickers, start=start, end=end, progress=False, auto_adjust=True)
    closes = data["Close"] if isinstance(data.columns, pd.MultiIndex) or "Close" in getattr(data, "columns", []) else data
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(tickers[0])
    return closes


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
                      lake_dir: Path | None = None) -> dict:
    """Compute (and persist) the NAV series of a model portfolio's holdings vs its benchmark.

    `backtest_days`: if set, measure the CURRENT holdings over the trailing window
    (today − N days → today) — a buy-and-hold backtest that shows immediately whether the
    book would have beaten the market (vs benchmark_etf, e.g. SPY). Otherwise the series
    starts at the run date and accrues forward.
    """
    from datetime import timedelta

    from catalyx.execution import portfolio as pf

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
        import pandas as pd
        lake.append_partition(_NAV_TABLE, pd.DataFrame(rows), {"portfolio_id": portfolio_id},
                              overwrite=True, lake_dir=lake_dir)

    last = rows[-1] if rows else None
    return {"portfolio_id": portfolio_id, "run_id": run_id, "start": start, "end": end,
            "points": len(rows), "benchmark": benchmark,
            "last_nav": last["nav"] if last else None,
            "last_return_pct": last["return_pct"] if last else None,
            "last_vs_benchmark_pct": last["vs_benchmark_pct"] if last else None,
            "series": rows}


def compute_real_nav(portfolio_id: str, start: str | None = None, as_of: str | None = None,
                     benchmark: str | None = None, price_fn=None, persist: bool = True,
                     lake_dir: Path | None = None) -> dict:
    """NAV series of the REAL book (from the movement files → net holdings). Same math as the
    model leg, so the two curves are directly comparable (execution alpha)."""
    from catalyx.store import movement_repo

    price_fn = price_fn or yfinance_prices
    rh = movement_repo.positions()
    holdings = rh.get("holdings", [])
    if not holdings:
        return {"portfolio_id": portfolio_id, "error": "no open real positions"}

    if start is None:
        movs = movement_repo.load_all()
        start = min((m["executed_at"][:10] for m in movs), default=date.today().isoformat())
    end = as_of or date.today().isoformat()

    etfs = [h["etf"] for h in holdings]
    tickers = list(dict.fromkeys(etfs + ([benchmark] if benchmark else [])))
    prices = price_fn(tickers, start, end)

    port = holdings_nav(holdings, prices)
    bench = holdings_nav([{"etf": benchmark, "weight_pct": 100.0}], prices) if benchmark else []
    bench_by_date = {b["date"]: b["nav"] for b in bench}
    computed_at = datetime.now(timezone.utc)
    rows = []
    for p in port:
        bnav = bench_by_date.get(p["date"])
        rows.append({
            "portfolio_id": portfolio_id, "kind": "real", "run_id": None,
            "config_version": None, "date": p["date"], "nav": p["nav"],
            "return_pct": round(p["nav"] - 100.0, 4),
            "benchmark_etf": benchmark, "benchmark_nav": bnav,
            "vs_benchmark_pct": round(p["nav"] - bnav, 4) if bnav is not None else None,
            "computed_at": computed_at,
        })

    if persist and rows:
        import pandas as pd
        lake.append_partition(_NAV_TABLE, pd.DataFrame(rows), {"portfolio_id": portfolio_id},
                              overwrite=True, lake_dir=lake_dir)

    last = rows[-1] if rows else None
    return {"portfolio_id": portfolio_id, "kind": "real", "start": start, "end": end,
            "points": len(rows), "benchmark": benchmark,
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
    elif args.cmd == "real":
        r = compute_real_nav(args.portfolio_id, start=args.start, as_of=args.as_of,
                             benchmark=args.benchmark)
        if r.get("error"):
            print(f"  {args.portfolio_id}: {r['error']}")
            return
        print(f"  {r['portfolio_id']} (real)  {r['start']} → {r['end']}  ({r['points']} pts)  "
              f"last NAV={r['last_nav']}  return={r['last_return_pct']:+}%")
    elif args.cmd == "show":
        r = show_nav(args.portfolio_id)
        for row in r["series"]:
            print(f"  {row['date']}  nav={row['nav']:>8}  ret={row.get('return_pct'):>7}%  "
                  f"vs_bench={row.get('vs_benchmark_pct')}")


if __name__ == "__main__":
    main()
