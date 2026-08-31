"""Covariance of the traded vehicles — Ledoit–Wolf shrinkage on WEEKLY returns (v6 I1).

Until this module, there was no covariance matrix anywhere in CATALYX. Every risk limit in the
system — `max_position_pct`, `correlated_catalyst_cap`, the conviction tiers — was NOTIONAL, so
20% in a bucket at 55% vol and 20% at 18% vol were the same number to the rules. This computes
the missing object, and nothing more: it is read by the cap check as an extra COLUMN. Measuring
risk is evidence FOR a config edit, never the edit.

WHY WEEKLY, and why this is not a preference (D10, measured 2026-08-31 over 44 vehicles):

    mean pairwise ρ    daily 0.127  →  weekly 0.245  →  fortnightly 0.243

That is the Epps effect. The book's UCITS lines trade on LSE, XETRA, Euronext and SIX with
different hours and different liquidity, so their daily closes are not synchronous and the
sample covariance of daily returns is biased DOWN — here by roughly half. It converges at the
weekly sampling frequency. A daily matrix would report a book as half as concentrated as it is
and turn the MCTR column into a tranquilizer, so the daily ρ is computed and printed BESIDE the
weekly one with this note attached: it is there so nobody "fixes" the module by going back to
daily. `portfolio._sector_vols` stays daily on purpose — the Epps bias hits the covariance, not
nearly as much a series' own variance.

WHY SHRINKAGE, and why it is not optional here: with ~6-26 series and ~52-104 weekly
observations, T is not comfortably larger than N. The sample covariance is then badly
conditioned and its extreme eigenvalues are biased — precisely the directions an optimizer or a
risk decomposition leans on. Ledoit–Wolf (2004), "Honey, I Shrunk the Sample Covariance Matrix":
shrink S toward a structured target F (here constant correlation) by the intensity that
minimizes expected squared error, estimated from the data rather than chosen.

CLI:
    uv run python -m catalyx.scorer.covariance [--portfolio catalyx] [--weeks 104] [--json]
    uv run python -m catalyx.scorer.covariance --tickers WCLD.L,RBOT.L,BTEC.L
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

_WEEKS_DEFAULT = 104          # 2y of weekly observations; the floor below is what actually binds
_MIN_WEEKS = 52
_ANNUALIZE = 52 ** 0.5


# ── Pure math (unit-tested, no network) ──────────────────────────────────────

def to_weekly(frame):
    """Daily adjusted closes → weekly (Friday) simple returns, one column per ticker.

    Resampling on the LAST observation of each week is what removes the asynchronicity: two
    venues that closed hours apart on Tuesday have both closed by Friday.
    """
    import pandas as pd

    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame()
    f = frame.copy()
    f.index = pd.to_datetime(f.index)
    return f.resample("W-FRI").last().pct_change().dropna(how="all")


def ledoit_wolf(returns) -> dict:
    """Shrink the sample covariance toward constant correlation. Returns Σ, δ*, and the pieces.

    δ* = clamp((π − ρ) / γ / T, 0, 1) exactly as in Ledoit–Wolf (2004) §3.3: π is the sum of
    asymptotic variances of the sample covariances, ρ the sum of asymptotic covariances between
    the target's and the sample's entries, γ the misspecification of the target. Nothing here is
    tuned — the intensity is estimated, which is the whole point of using this estimator rather
    than picking a blend by feel.
    """
    import numpy as np

    X = np.asarray(returns, dtype=float)
    T, N = X.shape
    if T < 2 or N < 1:
        raise ValueError(f"need at least 2 observations and 1 series, got {T}×{N}")
    Xc = X - X.mean(axis=0)
    S = (Xc.T @ Xc) / T                                   # MLE scaling, as the LW derivation uses
    var = np.diag(S).copy()
    var[var <= 0] = 1e-12
    sd = np.sqrt(var)
    corr = S / np.outer(sd, sd)

    off = ~np.eye(N, dtype=bool)
    r_bar = float(corr[off].mean()) if N > 1 else 0.0
    F = r_bar * np.outer(sd, sd)
    np.fill_diagonal(F, var)

    if N == 1:
        return {"sigma": S, "shrinkage": 0.0, "r_bar": 0.0, "T": T, "N": N}

    # π — asymptotic variance of each sample covariance entry
    Y = Xc ** 2
    pi_mat = (Y.T @ Y) / T - S ** 2
    pi = float(pi_mat.sum())

    # ρ — the diagonal terms plus the constant-correlation cross terms
    term = (Xc ** 3).T @ Xc / T - var * S                 # θ_ii,ij for every (i, j)
    rho_diag = float(np.diag(pi_mat).sum())
    ratio = np.outer(sd, 1.0 / sd)
    cross = (r_bar / 2.0) * (ratio * term.T + ratio.T * term)
    rho = rho_diag + float(cross[off].sum())

    gamma = float(((F - S) ** 2).sum())
    delta = 0.0 if gamma <= 0 else (pi - rho) / gamma / T
    delta = float(max(0.0, min(1.0, delta)))

    return {"sigma": delta * F + (1.0 - delta) * S, "shrinkage": delta,
            "r_bar": r_bar, "T": T, "N": N}


def mean_pairwise_corr(returns) -> float | None:
    """Mean off-diagonal correlation. The number D10 measured; reported at both frequencies."""
    import numpy as np

    X = np.asarray(returns, dtype=float)
    if X.shape[0] < 3 or X.shape[1] < 2:
        return None
    c = np.corrcoef(X, rowvar=False)
    off = ~np.eye(c.shape[0], dtype=bool)
    vals = c[off]
    vals = vals[np.isfinite(vals)]
    return round(float(vals.mean()), 4) if vals.size else None


def portfolio_vol(sigma, weights) -> float:
    """Annualized portfolio vol (%) for weights in PERCENT of the book. Cash is the remainder,
    so weights summing to less than 100 correctly lower the number."""
    import numpy as np

    w = np.asarray(weights, dtype=float) / 100.0
    return float(np.sqrt(max(0.0, w @ np.asarray(sigma) @ w)) * _ANNUALIZE * 100.0)


def risk_contributions(sigma, weights) -> list[dict]:
    """Per-position marginal and total contribution to portfolio risk.

    `ctr_pct` sums to 100 across the positions (Euler decomposition of a homogeneous-degree-1
    risk measure), which is what makes "this name carries 40% of the risk while holding 20% of
    the money" a sentence with a defined meaning.
    """
    import numpy as np

    w = np.asarray(weights, dtype=float) / 100.0
    Sig = np.asarray(sigma, dtype=float)
    var = float(w @ Sig @ w)
    if var <= 0:
        return [{"weight_pct": float(x), "mctr": None, "ctr_pct": None} for x in weights]
    sd = var ** 0.5
    marginal = Sig @ w / sd
    ctr = w * marginal
    return [{"weight_pct": round(float(wi) * 100.0, 2),
             "mctr_annual_pct": round(float(m) * _ANNUALIZE * 100.0, 2),
             "ctr_pct": round(float(c) / sd * 100.0, 2)}
            for wi, m, c in zip(w, marginal, ctr)]


def cluster_risk(sigma, weights, members: list[int]) -> dict:
    """Risk carried by a SUBSET of the book — the shape a catalyst cluster needs.

    `ctr_pct` is the cluster's share of portfolio variance (`w_C·Σw / w·Σw`). Across clusters
    that OVERLAP — a position with two drivers belongs wholly to both, exactly as `exposure_eur`
    does since v5.2 — these sum to more than 100, on purpose: the question is "how much of the
    book's risk moves if this driver breaks", not "who is credited with it".
    """
    wc = [float(w) if i in set(members) else 0.0 for i, w in enumerate(weights)]
    return _subset_risk(sigma, weights, wc)


# ── Lake / price plumbing ────────────────────────────────────────────────────

def _book(portfolio_id: str, lake_dir: Path | None = None) -> list[dict]:
    """Latest model holdings: [{sector_id, primary_etf, weight_pct}]. [] if none."""
    from catalyx.store import lake

    df = lake.read_table("portfolio_holding", lake_dir=lake_dir)
    if df.empty or "portfolio_id" not in df.columns:
        return []
    df = df[df["portfolio_id"] == portfolio_id]
    if df.empty:
        return []
    df = df[df["run_id"] == max(df["run_id"].dropna().unique())]
    return [{"sector_id": r["sector_id"], "primary_etf": r["primary_etf"],
             "weight_pct": float(r["weight_pct"])} for _, r in df.iterrows()]


def _prices(tickers: list[str], weeks: int, as_of: str | None, price_fn=None):
    from catalyx.data import prices

    end = date.fromisoformat(as_of) if as_of else date.today()
    start = end - timedelta(days=int(weeks * 7 * 1.15) + 30)
    if price_fn:
        return price_fn(tickers, start.isoformat(), end.isoformat())
    # allow_fetch=False for the same reason as _sector_vols: pre_run.sh warms the cache once and
    # everything downstream reads it. A risk decomposition is not a fetch site.
    return prices.read(tickers, start.isoformat(), end.isoformat(), allow_fetch=False)


def analyze(portfolio_id: str = "catalyx", tickers: list[str] | None = None,
            weights: list[float] | None = None, weeks: int = _WEEKS_DEFAULT,
            as_of: str | None = None, price_fn=None, lake_dir: Path | None = None) -> dict:
    """The full picture for a book: per-vehicle vol, mean ρ at both frequencies, the shrunk
    matrix's portfolio vol, and each position's contribution to it."""
    holdings: list[dict] = []
    if tickers:
        w = weights or [100.0 / len(tickers)] * len(tickers)
        holdings = [{"sector_id": t, "primary_etf": t, "weight_pct": float(x)}
                    for t, x in zip(tickers, w)]
    else:
        holdings = _book(portfolio_id, lake_dir=lake_dir)
    if not holdings:
        return {"error": f"no holdings for portfolio '{portfolio_id}' (build it first)"}

    etfs = [h["primary_etf"] for h in holdings]
    frame = _prices(etfs, weeks, as_of, price_fn)
    if frame is None or getattr(frame, "empty", True):
        return {"error": "no cached prices for the book's vehicles — run scripts/pre_run.sh"}

    weekly = to_weekly(frame)
    cols = [t for t in etfs if t in weekly.columns and weekly[t].notna().sum() >= _MIN_WEEKS]
    missing = [t for t in etfs if t not in cols]
    if len(cols) < 2:
        return {"error": f"fewer than 2 vehicles have {_MIN_WEEKS}+ weeks of history",
                "missing": missing}
    weekly = weekly[cols].dropna()
    if len(weekly) < _MIN_WEEKS:
        return {"error": f"only {len(weekly)} common weeks after alignment, need {_MIN_WEEKS}",
                "missing": missing}

    lw = ledoit_wolf(weekly.values)
    kept = [h for h in holdings if h["primary_etf"] in cols]
    w_pct = [h["weight_pct"] for h in kept]

    daily = frame[cols].dropna().pct_change().dropna()
    rho_weekly = mean_pairwise_corr(weekly.values)
    rho_daily = mean_pairwise_corr(daily.values) if len(daily) > 2 else None

    import numpy as np
    vols = {t: round(float(np.std(weekly[t].values, ddof=1)) * _ANNUALIZE * 100.0, 2)
            for t in cols}
    contrib = risk_contributions(lw["sigma"], w_pct)

    return {
        "portfolio_id": portfolio_id if not tickers else None,
        "as_of": (as_of or date.today().isoformat()),
        "sampling": "weekly (W-FRI)",
        "weeks_used": int(lw["T"]), "n_vehicles": int(lw["N"]),
        "shrinkage": round(lw["shrinkage"], 4),
        "shrink_target_r_bar": round(lw["r_bar"], 4),
        "mean_pairwise_corr_weekly": rho_weekly,
        "mean_pairwise_corr_daily": rho_daily,
        "epps_gap": (None if rho_weekly is None or rho_daily is None
                     else round(rho_weekly - rho_daily, 4)),
        "epps_note": (
            "weekly sampling is not a preference: measured across the 44-vehicle universe "
            "(2026-08-31), mean ρ ran daily 0.127 → weekly 0.245 → fortnightly 0.243, i.e. the "
            "daily estimate collapses because closes across LSE/XETRA/Euronext/SIX are not "
            "synchronous (Epps effect) and it converges at weekly. `epps_gap` is THIS book's own "
            "number — a small or negative gap means these particular vehicles trade closely "
            "enough in time for the daily estimate to hold up, not that the effect is absent "
            "from the universe. The matrix uses weekly either way; the daily figure is reported "
            "so the difference stays visible instead of being rediscovered."),
        "gross_pct": round(sum(w_pct), 2),
        "portfolio_vol_annual_pct": round(portfolio_vol(lw["sigma"], w_pct), 2),
        "positions": [
            {"sector_id": h["sector_id"], "etf": h["primary_etf"],
             "vol_annual_pct": vols.get(h["primary_etf"]), **c}
            for h, c in zip(kept, contrib)
        ],
        "excluded": missing,
    }


def cluster_report(portfolio_id: str = "catalyx", weeks: int = _WEEKS_DEFAULT,
                   as_of: str | None = None, price_fn=None,
                   lake_dir: Path | None = None) -> dict:
    """Risk per catalyst cluster beside the notional exposure the cap already reads (v6 I2).

    Clusters OVERLAP by construction (a sector with two drivers is wholly in both), so neither
    the notional nor the risk column sums to 100 — the same deliberate property `exposure_eur`
    has in the real book. The cap stays notional and stays `warn`: this is the evidence, not the
    edit.
    """
    from catalyx.execution.portfolio import _sector_catalyst_map

    base = analyze(portfolio_id, weeks=weeks, as_of=as_of, price_fn=price_fn, lake_dir=lake_dir)
    if "error" in base:
        return base
    smap = _sector_catalyst_map()
    sigma_holdings = base["positions"]
    weights = [p["weight_pct"] for p in sigma_holdings]

    # rebuild the matrix once rather than threading it through the return value
    etfs = [p["etf"] for p in sigma_holdings]
    frame = _prices(etfs, weeks, as_of, price_fn)
    weekly = to_weekly(frame)[etfs].dropna()
    sigma = ledoit_wolf(weekly.values)["sigma"]

    by_cat: dict[str, list[int]] = {}
    for i, p in enumerate(sigma_holdings):
        for cid in (smap.get(p["sector_id"]) or ["uncatalyzed"]):
            by_cat.setdefault(cid, []).append(i)

    rows = []
    for cid, members in by_cat.items():
        wc = [weights[i] if i in members else 0.0 for i in range(len(weights))]
        r = _subset_risk(sigma, weights, wc)
        rows.append({"catalyst_id": cid, "n_positions": len(members),
                     "sectors": [sigma_holdings[i]["sector_id"] for i in members], **r})
    rows.sort(key=lambda r: -(r["ctr_pct"] or 0))
    return {**{k: v for k, v in base.items() if k != "positions"},
            "portfolio_vol_annual_pct": base["portfolio_vol_annual_pct"], "clusters": rows}


def cluster_risk_for(book: dict[str, tuple[str, float]], clusters: dict[str, list[str]],
                     weeks: int = _WEEKS_DEFAULT, as_of: str | None = None,
                     price_fn=None) -> dict[str, dict] | None:
    """Risk share per named cluster for an ARBITRARY euro book — what `cap_check` needs (v6 I2).

    `book` is {sector_id: (etf, eur)} and `clusters` {catalyst_id: [sector_id, …]}. Returns
    {catalyst_id: {notional_pct, ctr_pct, standalone_vol_pct}} or **None** when the covariance
    cannot be computed — a missing risk column must read as "not measured", never as a zero.
    """
    sectors = [s for s, (e, eur) in book.items() if e and float(eur) > 0]
    if len(sectors) < 2:
        return None
    etfs = [book[s][0] for s in sectors]
    gross = sum(float(book[s][1]) for s in sectors)
    if gross <= 0:
        return None
    try:
        frame = _prices(list(dict.fromkeys(etfs)), weeks, as_of, price_fn)
        weekly = to_weekly(frame)
        usable = [s for s, e in zip(sectors, etfs)
                  if e in weekly.columns and weekly[e].notna().sum() >= _MIN_WEEKS]
        if len(usable) < 2:
            return None
        cols = [book[s][0] for s in usable]
        weekly = weekly[list(dict.fromkeys(cols))].dropna()
        if len(weekly) < _MIN_WEEKS:
            return None
        sigma = ledoit_wolf(weekly.values)["sigma"]
    except Exception:                                       # pragma: no cover - defensive
        return None

    col_ix = {t: i for i, t in enumerate(weekly.columns)}
    n = len(weekly.columns)
    # two sectors can share a vehicle; their euros add on that column
    w_pct = [0.0] * n
    for s in usable:
        etf, eur = book[s]
        w_pct[col_ix[etf]] += float(eur) / gross * 100.0

    out = {}
    for cid, members in clusters.items():
        ix = sorted({col_ix[book[s][0]] for s in members if s in usable})
        if not ix:
            continue
        # a cluster's members may share a column with a non-member; weight the column by the
        # cluster's own euros in it rather than by the whole column
        wc = [0.0] * n
        for s in members:
            if s in usable:
                wc[col_ix[book[s][0]]] += float(book[s][1]) / gross * 100.0
        out[cid] = _subset_risk(sigma, w_pct, wc)
    return out


def _subset_risk(sigma, w_pct: list[float], wc_pct: list[float]) -> dict:
    import numpy as np

    w = np.asarray(w_pct, dtype=float) / 100.0
    wc = np.asarray(wc_pct, dtype=float) / 100.0
    Sig = np.asarray(sigma, dtype=float)
    var = float(w @ Sig @ w)
    standalone = float(wc @ Sig @ wc)
    return {
        "notional_pct": round(float(wc.sum()) * 100.0, 2),
        "ctr_pct": round(float(wc @ Sig @ w) / var * 100.0, 2) if var > 0 else None,
        "standalone_vol_pct": round(standalone ** 0.5 * _ANNUALIZE * 100.0, 2),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="CATALYX covariance — Ledoit–Wolf on weekly returns")
    ap.add_argument("--portfolio", default="catalyx", help="model portfolio to decompose")
    ap.add_argument("--tickers", default=None, help="comma-separated vehicles (equal weights)")
    ap.add_argument("--weeks", type=int, default=_WEEKS_DEFAULT)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--clusters", action="store_true",
                    help="risk per catalyst cluster beside the notional the cap reads (I2)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else None
    res = (cluster_report(args.portfolio, weeks=args.weeks, as_of=args.as_of)
           if args.clusters else
           analyze(args.portfolio, tickers=tickers, weeks=args.weeks, as_of=args.as_of))

    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        return
    if "error" in res:
        print(f"! {res['error']}")
        return

    print(f"CATALYX — covariance   {res['sampling']}   {res['weeks_used']}w × "
          f"{res['n_vehicles']} vehicles   as-of {res['as_of']}")
    print(f"  Ledoit-Wolf shrinkage δ={res['shrinkage']:.3f} toward constant ρ="
          f"{res['shrink_target_r_bar']:.3f}")
    gap = res.get("epps_gap")
    epps = ("Epps gap +%.3f — the daily estimate understates this book" % gap) if (gap or 0) > 0.02 \
        else "these vehicles trade closely enough in time; the gap is not material HERE"
    print(f"  mean pairwise ρ:  weekly {res['mean_pairwise_corr_weekly']}   "
          f"daily {res['mean_pairwise_corr_daily']}   ({epps})")
    print(f"  book vol {res['portfolio_vol_annual_pct']:.1f}%/yr on {res['gross_pct']:.0f}% gross\n")

    if args.clusters:
        print(f"  {'catalyst':<42} {'notional%':>9} {'risk%':>7} {'standalone':>11}  sectors")
        print(f"  {'-'*42} {'-'*9} {'-'*7} {'-'*11}")
        for r in res["clusters"]:
            print(f"  {r['catalyst_id']:<42} {r['notional_pct']:>9.1f} {r['ctr_pct']:>7.1f} "
                  f"{r['standalone_vol_pct']:>10.1f}%  {', '.join(r['sectors'])}")
        print("\n  risk% is the cluster's share of book VARIANCE. Clusters overlap (a sector with "
              "two\n  drivers is wholly in both), so neither column sums to 100 — same rule as "
              "exposure_eur.\n  The cap stays notional and stays `warn`: this is evidence FOR a "
              "config edit, not the edit.")
    else:
        print(f"  {'sector':<38} {'etf':<10} {'wt%':>6} {'vol%':>7} {'risk%':>7}")
        print(f"  {'-'*38} {'-'*10} {'-'*6} {'-'*7} {'-'*7}")
        for p in res["positions"]:
            print(f"  {p['sector_id']:<38} {p['etf']:<10} {p['weight_pct']:>6.1f} "
                  f"{(p['vol_annual_pct'] or 0):>7.1f} {(p['ctr_pct'] or 0):>7.1f}")
        print("\n  risk% = share of book variance (sums to 100). A name whose risk% exceeds its "
              "wt%\n  is carrying more of the book than it is being paid weight for.")
    if res.get("excluded"):
        print(f"\n  ! excluded for insufficient history: {', '.join(res['excluded'])}")


if __name__ == "__main__":
    main()
