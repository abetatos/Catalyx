"""Dislocation lens (price-vs-fundamentals gap) — opportunities + rotation, from ONE engine.

Everything CATALYX measures is the gap between PRICE (opinion/momentum) and FUNDAMENTALS
(regime/thesis). `regime_state` reads that gap on the bearish side (price turns against a thesis).
This module reads the SAME gap for capital deployment, via one shared correlation/beta engine over
yfinance returns, and emits two lenses:

  OPPORTUNITY (panic dip): a sector whose PRICE fell hard but whose FUNDAMENTALS are `intact` and
    whose own catalyst is still confirmed — and whose drop is largely explained by CONTAGION
    (co-movement with the market/risk-off), not by something specific to it. "It fell because risk
    assets fell, not because its thesis broke" → candidate BUY. Copper in the 2026-06-05 AI selloff
    is the canonical case: regime intact, catalyst_alignment ~96, yet down hard with the tape.

  DIVERSIFIER (rotation target): when a cluster is stressed (contested/breaking, or worst-drawdown),
    the healthy sectors with the LOWEST correlation to that cluster — where to rotate so you are not
    just re-buying the same correlated bet (the experiment's "illusory diversification" fix).

Same correlation/beta matrix, opposite use of correlation. Python computes the decomposition FACTS
(drawdown, beta, contagion-explained vs idiosyncratic residual, correlation); the BUY/AVOID/ROTATE
call is Claude's, in the skill, with WebSearch macro context. Nothing here auto-trades.

CLI:
    uv run python -m catalyx.scorer.dislocation [--run-id ...] [--lookback 90] [--window 5]
                                                [--benchmark SPY] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from catalyx.store import lake

_BENCHMARK = "SPY"


# ── Price source (injectable) ────────────────────────────────────────────────

def yfinance_prices(tickers: list[str], start: str, end: str):
    """Adjusted-close DataFrame (index=date, columns=tickers). Mirrors nav_engine."""
    import pandas as pd
    import yfinance as yf

    data = yf.download(tickers, start=start, end=end, progress=False, auto_adjust=True)
    closes = data["Close"] if isinstance(data.columns, pd.MultiIndex) or "Close" in getattr(data, "columns", []) else data
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(tickers[0])
    return closes


# ── Pure math (unit-tested, no network) ──────────────────────────────────────

def beta(asset_returns, market_returns) -> float | None:
    """OLS beta of asset to market over the aligned overlap. None if undefined."""
    import pandas as pd

    j = pd.concat([asset_returns, market_returns], axis=1).dropna()
    if len(j) < 6:
        return None
    var = j.iloc[:, 1].var()
    if var is None or var == 0:
        return None
    return round(float(j.cov().iloc[0, 1] / var), 3)


def decompose(window_return: float, beta_val: float | None, market_window_return: float) -> dict:
    """Split a drawdown into the part explained by CONTAGION (beta × market move) and the
    IDIOSYNCRATIC residual. `contagion_fraction` ∈ [0,1] = how much of the drop the market
    co-movement explains (1.0 = the whole drop is contagion → 'pure panic' candidate; a large
    residual = something sector-specific → Claude must investigate before calling it an opportunity).
    """
    if beta_val is None:
        return {"expected_pct": None, "idiosyncratic_pct": None, "contagion_fraction": None}
    expected = beta_val * market_window_return
    idiosyncratic = window_return - expected
    frac = None
    if window_return < 0 and expected < 0:
        frac = max(0.0, min(1.0, expected / window_return))  # both negative → ratio of explained
    elif window_return < 0 <= expected:
        frac = 0.0  # market up but sector down → not contagion at all
    return {
        "expected_pct": round(expected * 100, 2),
        "idiosyncratic_pct": round(idiosyncratic * 100, 2),
        "contagion_fraction": round(frac, 2) if frac is not None else None,
    }


# ── Lake read: latest sectors + regime + catalyst ────────────────────────────

def _load_sectors(run_id: str | None, lake_dir: Path | None) -> tuple[list[dict], str | None]:
    df = lake.read_table("sector_snapshot", lake_dir=lake_dir)
    if df.empty:
        return [], None
    if run_id is None and "run_id" in df.columns:
        run_id = max(df["run_id"].dropna().unique())
    if run_id is not None:
        df = df[df["run_id"] == run_id]
    out = []
    for _, r in df.iterrows():
        out.append({
            "sector_id": r.get("sector_id"),
            "primary_etf": r.get("primary_etf"),
            "regime_state": r.get("regime_state") if r.get("regime_state") is not None else "intact",
            "catalyst_alignment": float(r.get("catalyst_alignment") or 0.0),
            "composite": float(r.get("composite") or 0.0),
            "momentum": float(r.get("momentum") or 0.0),
        })
    # primary_etf can be NaN (a float) for studyless sectors — NaN is truthy, so filter on type;
    # also drop placeholder strings with spaces (e.g. "NO PURE-PLAY ETF") that aren't real tickers
    return [s for s in out if isinstance(s["primary_etf"], str)
            and s["primary_etf"].strip() and " " not in s["primary_etf"].strip()], run_id


# ── Engine ───────────────────────────────────────────────────────────────────

def analyze(run_id: str | None = None, lookback_days: int = 90, window_days: int = 5,
            benchmark: str = _BENCHMARK, drawdown_threshold: float = -3.0,
            min_catalyst_alignment: float = 70.0, min_composite: float = 55.0,
            max_diversifier_corr: float = 0.5, persist: bool = False,
            anchor_sectors: list[str] | None = None,
            price_fn=None, lake_dir: Path | None = None) -> dict:
    """Compute both lenses from one correlation/beta engine. Returns a JSON-able dict.

    If `persist`, materialize one flat row per analyzed sector to the lake table `dislocation`
    (keyed by run_id, overwritten) so the static GitHub-Pages dashboard can read it in-browser.
    """
    import pandas as pd

    price_fn = price_fn or yfinance_prices
    sectors, used_run = _load_sectors(run_id, lake_dir)
    if not sectors:
        return {"error": "no sector_snapshot in lake (record a run first)"}

    etfs = list(dict.fromkeys(s["primary_etf"] for s in sectors))
    tickers = list(dict.fromkeys(etfs + [benchmark]))
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    prices = price_fn(tickers, start, end)
    if prices is None or len(prices) == 0:
        return {"error": "no price data"}
    prices = prices.dropna(how="all")
    rets = prices.pct_change()

    have_mkt = benchmark in prices.columns and prices[benchmark].dropna().shape[0] > window_days + 1
    mkt_window = None
    if have_mkt:
        mp = prices[benchmark].dropna()
        mkt_window = float(mp.iloc[-1] / mp.iloc[-1 - window_days] - 1.0)

    corr = rets.corr() if rets.shape[1] > 1 else None

    rows = []
    for s in sectors:
        etf = s["primary_etf"]
        if etf not in prices.columns:
            continue
        px = prices[etf].dropna()
        if len(px) < window_days + 2:
            continue
        wret = float(px.iloc[-1] / px.iloc[-1 - window_days] - 1.0)
        b = beta(rets[etf], rets[benchmark]) if have_mkt else None
        dec = decompose(wret, b, mkt_window) if (b is not None and mkt_window is not None) else \
            {"expected_pct": None, "idiosyncratic_pct": None, "contagion_fraction": None}
        rows.append({
            "sector_id": s["sector_id"], "primary_etf": etf,
            "drawdown_pct": round(wret * 100, 2),
            "beta_to_market": b,
            "contagion_explained_pct": dec["expected_pct"],
            "idiosyncratic_pct": dec["idiosyncratic_pct"],
            "contagion_fraction": dec["contagion_fraction"],
            "regime_state": s["regime_state"],
            "catalyst_alignment": round(s["catalyst_alignment"], 1),
            "composite": round(s["composite"], 1),
        })

    # ── OPPORTUNITY lens: fell hard + intact + catalyst confirmed + composite floor ──
    # The composite floor (min_composite) is non-negotiable to our philosophy: a panic dip is only
    # an opportunity if the sector is one we'd actually own on the FULL blend (catalyst + momentum +
    # flow + crowding). A high catalyst_alignment alone — with weak momentum/flow or heavy crowding
    # dragging the composite down — is NOT a buy; flagging it would contradict the model's ranking.
    opportunities = []
    for r in rows:
        if (r["drawdown_pct"] <= drawdown_threshold and r["regime_state"] == "intact"
                and r["catalyst_alignment"] >= min_catalyst_alignment
                and r["composite"] >= min_composite):
            r2 = dict(r)
            r2["opportunity_score"] = round(abs(r["drawdown_pct"]) * (r["catalyst_alignment"] / 100.0), 1)
            opportunities.append(r2)
    opportunities.sort(key=lambda x: x["opportunity_score"], reverse=True)

    # ── DIVERSIFIER lens: healthy + low correlation to the reference cluster ──
    # Default cluster = the STRESSED sectors (regime / worst-drawdown) → "where to rotate when the
    # market is selling X". If `anchor_sectors` is given (e.g. the real book's holdings), the cluster
    # is THOSE sectors → "where to rotate so I'm not re-buying what I already own" (portfolio rotation).
    if anchor_sectors:
        stressed = set(anchor_sectors) & {r["sector_id"] for r in rows}
    else:
        stressed = {r["sector_id"] for r in rows if r["regime_state"] in ("contested", "breaking")}
        if not stressed and rows:  # no regime stress → use the worst-drawdown quartile as the cluster
            cut = sorted(r["drawdown_pct"] for r in rows)[max(0, len(rows) // 4 - 1)]
            stressed = {r["sector_id"] for r in rows if r["drawdown_pct"] <= cut}
    etf_by_sector = {r["sector_id"]: r["primary_etf"] for r in rows}
    stressed_etfs = [etf_by_sector[s] for s in stressed if s in etf_by_sector]

    diversifiers = []
    if corr is not None and stressed_etfs:
        for r in rows:
            if r["sector_id"] in stressed or r["regime_state"] != "intact" or r["composite"] < min_composite:
                continue
            etf = r["primary_etf"]
            cvals = [corr.loc[etf, se] for se in stressed_etfs
                     if etf in corr.index and se in corr.columns and etf != se and pd.notna(corr.loc[etf, se])]
            if not cvals:
                continue
            mean_corr = round(float(sum(cvals) / len(cvals)), 2)
            if mean_corr <= max_diversifier_corr:
                r2 = dict(r)
                r2["mean_corr_to_stressed"] = mean_corr
                r2["diversifier_score"] = round(r["composite"] * (1.0 - mean_corr), 1)
                diversifiers.append(r2)
        diversifiers.sort(key=lambda x: x["diversifier_score"], reverse=True)

    mkt_window_pct = round(mkt_window * 100, 2) if mkt_window is not None else None

    if persist and rows and used_run:
        # anchored (portfolio) rotation goes to its own table so it never overwrites the
        # market-wide dislocation run the heatmap/overview consume.
        table = "portfolio_rotation" if anchor_sectors else "dislocation"
        _persist_lake(used_run, window_days, benchmark, mkt_window_pct,
                      rows, opportunities, diversifiers, lake_dir, table=table)

    return {
        "run_id": used_run, "window_days": window_days, "lookback_days": lookback_days,
        "benchmark": benchmark, "market_window_pct": mkt_window_pct,
        "stressed_cluster": sorted(stressed),
        "opportunities": opportunities, "diversifiers": diversifiers,
        "n_sectors": len(rows),
        "note": "Python surfaces the decomposition; the BUY/ROTATE call is Claude's (verify the "
                "idiosyncratic residual has no hidden cause before treating a dip as panic).",
    }


def _persist_lake(run_id, window_days, benchmark, market_window_pct, rows, opportunities,
                  diversifiers, lake_dir, table: str = "dislocation") -> None:
    """One flat row per analyzed sector → lake `table` (overwrite per run)."""
    import pandas as pd

    opp_by = {o["sector_id"]: o for o in opportunities}
    div_by = {d["sector_id"]: d for d in diversifiers}
    computed_at = datetime.now(timezone.utc)
    recs = []
    for r in rows:
        o = opp_by.get(r["sector_id"])
        d = div_by.get(r["sector_id"])
        recs.append({
            "run_id": run_id, "computed_at": computed_at, "window_days": window_days,
            "benchmark": benchmark, "market_window_pct": market_window_pct,
            "sector_id": r["sector_id"], "primary_etf": r["primary_etf"],
            "regime_state": r["regime_state"], "catalyst_alignment": r["catalyst_alignment"],
            "composite": r["composite"], "drawdown_pct": r["drawdown_pct"],
            "beta_to_market": r["beta_to_market"],
            "contagion_explained_pct": r["contagion_explained_pct"],
            "idiosyncratic_pct": r["idiosyncratic_pct"], "contagion_fraction": r["contagion_fraction"],
            "lens": "opportunity" if o else ("diversifier" if d else "neither"),
            "opportunity_score": o.get("opportunity_score") if o else None,
            "diversifier_score": d.get("diversifier_score") if d else None,
            "mean_corr_to_stressed": d.get("mean_corr_to_stressed") if d else None,
        })
    lake.append_partition(table, pd.DataFrame(recs), {"run_id": run_id},
                          overwrite=True, lake_dir=lake_dir)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="CATALYX dislocation lens — opportunities + rotation")
    p.add_argument("--run-id", default=None)
    p.add_argument("--lookback", type=int, default=90)
    p.add_argument("--window", type=int, default=5)
    p.add_argument("--benchmark", default=_BENCHMARK)
    p.add_argument("--drawdown", type=float, default=-3.0, help="min drawdown%% to flag (negative)")
    p.add_argument("--no-persist", action="store_true", help="do not write the dislocation lake table")
    p.add_argument("--anchor-sectors", default=None,
                   help="comma-separated sector_ids to anchor rotation to (e.g. the real book's "
                        "holdings) → diversifiers low-correlated to THOSE; writes table portfolio_rotation")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    anchor = [s.strip() for s in args.anchor_sectors.split(",")] if args.anchor_sectors else None
    r = analyze(run_id=args.run_id, lookback_days=args.lookback, window_days=args.window,
                benchmark=args.benchmark, drawdown_threshold=args.drawdown,
                persist=not args.no_persist, anchor_sectors=anchor)
    if args.json:
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return
    if r.get("error"):
        print(f"  {r['error']}")
        return

    print(f"CATALYX — Dislocation lens   run={r['run_id']}   {r['window_days']}d window   "
          f"market({r['benchmark']})={r['market_window_pct']}%\n")
    print("OPPORTUNITIES (fell hard · fundamentals intact · catalyst confirmed → buy the panic?)")
    print(f"  {'sector':<34}{'etf':<9}{'draw%':>7}{'beta':>6}{'contag%':>9}{'idio%':>7}{'catAlign':>9}{'score':>7}")
    for o in r["opportunities"]:
        print(f"  {o['sector_id']:<34}{str(o['primary_etf']):<9}{o['drawdown_pct']:>7}{str(o['beta_to_market']):>6}"
              f"{str(o['contagion_explained_pct']):>9}{str(o['idiosyncratic_pct']):>7}{o['catalyst_alignment']:>9}{o['opportunity_score']:>7}")
    if not r["opportunities"]:
        print("  (none)")
    print(f"\nDIVERSIFIERS (healthy · low correlation to stressed {r['stressed_cluster'] or '—'} → rotate here?)")
    print(f"  {'sector':<34}{'etf':<9}{'comp':>6}{'corr':>6}{'score':>7}")
    for d in r["diversifiers"]:
        print(f"  {d['sector_id']:<34}{str(d['primary_etf']):<9}{d['composite']:>6}{d['mean_corr_to_stressed']:>6}{d['diversifier_score']:>7}")
    if not r["diversifiers"]:
        print("  (none)")
    print(f"\n  {r['note']}")


if __name__ == "__main__":
    main()
