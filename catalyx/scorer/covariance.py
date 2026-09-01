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


def risk_budget_weights(sigma, budgets, iters: int = 500, tol: float = 1e-12) -> dict:
    """Weights whose RISK contributions are proportional to `budgets` (v13).

    Solves `w_i · (Σw)_i ∝ b_i` — position i carries share b_i of portfolio variance — by cyclical
    coordinate descent on Spinu's (2013) convex program `min ½y'Σy − Σ bᵢ·log yᵢ`, whose
    stationary point is exactly that condition (Griveau-Billion et al. 2013). Each coordinate is
    the positive root of a scalar quadratic, so every step is closed-form: deterministic, no
    random start, no tuning constant, same run → same book, which is what keeps the model book
    reproducible from a run alone. The naive `w ← b/(Σw)` normalized each step has the same fixed
    point but OSCILLATES (verified: it fails to converge on a plain diagonal Σ in 500 steps and
    returns its own starting guess), so it is not used — a sizing step that silently returns its
    input when it fails to converge is the worst available failure.

    WHY THIS EXISTS. Every sizing rule before it read a LABEL. The correlated-catalyst cap asks
    which driver a position is *attributed* to, and it was right about the accounting and wrong
    about the risk: on the live top-12, robotics and cloud are the same declared cluster and
    measure ρ 0.28, while cloud–cyber (different clusters) measure 0.65 and water — also labelled
    ai_capex — is the book's best diversifier at ρ −0.00 against cloud. Correlation is the thing
    the labels were a proxy for, and it is measurable, so the sizing step measures it. The cap
    stays: declared exposure and measured co-movement are different questions (v10 P6).

    WHAT IT DOES NOT DO. It never touches SELECTION and never enters the composite — a
    correlation term inside the score would need an IC to justify a weight, and this is a RISK
    statement, the same footing on which `vol_tilt` sizes without ever ranking. The names in the
    book are exactly the names the composite chose.

    THE TILT IS SQRT-COMPRESSED, and that is the honest reading, not a side effect. With Σ
    diagonal the fixed point is `w ∝ sqrt(b)/σ`, so a 4:1 budget ratio lands as 2:1 in euros:
    a budget is a claim about VARIANCE and a weight is a claim about money. With the composite's
    live rank IC still noise (−0.027, 1 window) a conviction tilt that arrives compressed is the
    direction λ already argues for; when the IC earns λ, the budgets spread and the euros follow.

    Args:
        sigma: covariance matrix (any consistent period; the solution is scale-free in Σ).
        budgets: desired risk shares, any positive scale. Non-positive → that name gets 0.
    Returns:
        {"weights": [...] summing to 1, "converged": bool, "iters": int, "ctr_pct": [...]}
    """
    import numpy as np

    b = np.asarray(budgets, dtype=float)
    Sig = np.asarray(sigma, dtype=float)
    n = b.size
    if n == 0:
        return {"weights": [], "converged": True, "iters": 0, "ctr_pct": []}
    live = np.isfinite(b) & (b > 0)
    if not live.any() or Sig.shape != (n, n) or not np.isfinite(Sig).all():
        return {"weights": [0.0] * n, "converged": False, "iters": 0, "ctr_pct": [None] * n}

    bl = np.where(live, b, 0.0)
    bl = bl / bl.sum()
    var_i = np.diag(Sig).astype(float)
    if np.any(var_i[live] <= 0):
        return {"weights": [0.0] * n, "converged": False, "iters": 0, "ctr_pct": [None] * n}

    # start at vol-parity on the budget — the exact answer when Σ is diagonal, so the solver
    # begins on the manifold it would otherwise have to walk to
    y = np.where(live, np.sqrt(bl) / np.sqrt(np.where(live, var_i, 1.0)), 0.0)
    converged, used = False, 0
    for k in range(1, iters + 1):
        used, prev = k, y.copy()
        for i in range(n):
            if not live[i]:
                y[i] = 0.0
                continue
            # ∂/∂yᵢ of ½y'Σy − bᵢ·log yᵢ = 0  →  Σᵢᵢ·yᵢ² + c·yᵢ − bᵢ = 0, c = Σ_{j≠i} Σᵢⱼ·yⱼ
            c = float(Sig[i] @ y) - var_i[i] * y[i]
            y[i] = (-c + (c * c + 4.0 * var_i[i] * bl[i]) ** 0.5) / (2.0 * var_i[i])
        s = y.sum()
        if not np.isfinite(s) or s <= 0:
            break
        if np.abs(y / s - prev / max(prev.sum(), 1e-300)).max() < tol:
            converged = True
            break
    s = y.sum()
    w = y / s if np.isfinite(s) and s > 0 else np.zeros(n)

    var = float(w @ Sig @ w)
    ctr = [round(float(wi * (Sig @ w)[i]) / var * 100.0, 2) if var > 0 else None
           for i, wi in enumerate(w)]
    return {"weights": [float(x) for x in w], "converged": converged, "iters": used,
            "ctr_pct": ctr}


def budget_sigma(tickers: list[str], weeks: int, min_weeks: int, as_of: str | None = None,
                 price_fn=None) -> dict:
    """Σ over an arbitrary vehicle list, with short-history rows IMPUTED rather than dropped.

    Dropping a name would shrink the very cross-section the book is being sized over — the
    `_load_shares_series` shape of bug (v10.2) one layer up. A name without enough weeks gets its
    own σ when it has any usable history (else the panel median) and the panel's LW constant
    correlation `r_bar` against everyone: the prior, never the best case (uniqueness = 1 would
    hand an unmeasured name the largest weight in the book) and never the worst.

    Returns {"sigma", "tickers", "imputed", "weeks", "r_bar", "shrinkage", "psd_clipped"} or
    {"error": ...} when fewer than 2 names are measurable — at which point there is no tape to
    read and the caller must say so rather than size off an invented matrix.
    """
    import numpy as np

    frame = _prices(list(tickers), weeks, as_of, price_fn)
    if frame is None or getattr(frame, "empty", True):
        return {"error": "no cached prices for the book's vehicles — run scripts/pre_run.sh"}
    weekly = to_weekly(frame)
    if getattr(weekly, "empty", True):
        return {"error": "price frame has no weekly observations"}

    full = [t for t in tickers
            if t in weekly.columns and int(weekly[t].notna().sum()) >= min_weeks]
    aligned = weekly[full].dropna() if full else None
    if len(full) < 2 or aligned is None or len(aligned) < min_weeks:
        return {"error": f"fewer than 2 vehicles with {min_weeks}+ aligned weeks",
                "measured": full}

    lw = ledoit_wolf(aligned.values)
    S = np.asarray(lw["sigma"], dtype=float)
    sd_full = np.sqrt(np.diag(S))
    C_full = S / np.outer(sd_full, sd_full)
    r_bar = float(lw["r_bar"])
    med_sd = float(np.median(sd_full))

    idx_full = {t: i for i, t in enumerate(full)}
    n = len(tickers)
    sd = np.empty(n)
    imputed = []
    for i, t in enumerate(tickers):
        if t in idx_full:
            sd[i] = sd_full[idx_full[t]]
            imputed.append(False)
        else:
            own = weekly[t].dropna() if t in weekly.columns else None
            # 8 weeks is not enough to estimate a CORRELATION but is enough for a variance;
            # using it beats the panel median, which would flatten a genuinely wild vehicle
            sd[i] = (float(own.std()) if own is not None and len(own) >= 8
                     and float(own.std()) > 0 else med_sd)
            imputed.append(True)

    C = np.full((n, n), r_bar, dtype=float)
    for i, ti in enumerate(tickers):
        C[i, i] = 1.0
        if ti not in idx_full:
            continue
        for j, tj in enumerate(tickers):
            if i != j and tj in idx_full:
                C[i, j] = C_full[idx_full[ti], idx_full[tj]]

    # blending a measured block with a constant-correlation border can leave C slightly
    # indefinite; clip and renormalize rather than hand a risk solver a matrix with a negative
    # eigenvalue in it. Reported, because a silently repaired matrix is a silently wrong one.
    evals, evecs = np.linalg.eigh(C)
    psd_clipped = bool(evals.min() < 1e-10)
    if psd_clipped:
        C = evecs @ np.diag(np.clip(evals, 1e-10, None)) @ evecs.T
        d = np.sqrt(np.diag(C))
        C = C / np.outer(d, d)

    return {"sigma": (C * np.outer(sd, sd)).tolist(), "tickers": list(tickers),
            "imputed": imputed, "weeks": int(len(aligned)), "r_bar": round(r_bar, 4),
            "shrinkage": round(float(lw["shrinkage"]), 4), "psd_clipped": psd_clipped,
            "n_measured": len(full)}


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
    # EUR since v10.4: the operator BEARS the FX, so it belongs in the covariance the risk
    # budget is written against. Measured on the 26 live vehicles over the same window, the
    # conversion moved mean pairwise weekly rho 0.199 -> 0.179 — it LOWERS it (the FX leg
    # partly offsets equity co-movement), which is the opposite of what "adding a common
    # factor raises correlation" predicts. rho is what n_target was read off, so the number
    # is reported, never silently absorbed.
    return prices.read_eur(tickers, start.isoformat(), end.isoformat(), allow_fetch=False)


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
