"""Backtest: does momentum ACCELERATION deserve the 15% weight that valuation_relative wastes?

Question (from the strategy critique): the existing momentum score is ~80% a 3-6m
trailing LEVEL — it rewards "has already run" (priced-in), the opposite of the stated
edge ("before priced in"). Acceleration (2nd derivative) is proposed as the missing
timing/phase dimension.

This script tests it empirically, walk-forward, no-look-ahead:

  signal at month-end T uses only prices up to T; forward return is T -> T+1 month.

  • LEVEL       = r1m*0.20 + r3m*0.45 + r6m*0.35           (current momentum_engine blend)
  • ACCEL       = r3m*4 - r6m*2   (recent annualized pace - base annualized pace)
  • BLEND       = 0.5*z(LEVEL) + 0.5*z(ACCEL)              (cross-sectional z each month)

Outputs:
  1. Orthogonality  — is ACCEL actually new info vs LEVEL? (pooled + per-month corr)
  2. Predictive IC  — Spearman rank-IC of each signal vs forward return (mean, t, hit-rate)
  3. Quintile spread— top vs bottom quintile mean forward return
  4. Strategy sim   — monthly long top-k by each signal vs equal-weight universe (= benchmark)

Run: uv run python experiments/backtest_acceleration.py
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.simplefilter("ignore")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parents[1]))
from catalyx.data.market_data import SECTOR_TICKERS  # noqa: E402

# Primary (first) ticker per sector — same PRIMARY momentum_engine scores on.
PRIMARY = {sid: tk[0] for sid, tk in SECTOR_TICKERS.items()}

TD = {"1m": 22, "3m": 63, "6m": 126}  # trading-day lookbacks (mirror market_data)
START = "2022-01-01"


def fetch_prices() -> pd.DataFrame:
    import yfinance as yf
    tickers = sorted(set(PRIMARY.values()))
    print(f"Downloading {len(tickers)} tickers from yfinance ({START}→today)...", file=sys.stderr)
    raw = yf.download(tickers, start=START, auto_adjust=True, progress=False)
    px = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    px = px.dropna(how="all")
    # ticker -> sector_id columns
    inv = {tk: sid for sid, tk in PRIMARY.items()}
    px = px[[c for c in px.columns if c in inv]].rename(columns=inv)
    return px


def ret(series: pd.Series, lb: int) -> float | None:
    if len(series) < lb + 1:
        return None
    cur, past = series.iloc[-1], series.iloc[-1 - lb]
    if pd.isna(cur) or pd.isna(past) or past == 0:
        return None
    return cur / past - 1.0


def zscore(s: pd.Series) -> pd.Series:
    sd = s.std()
    return (s - s.mean()) / sd if sd and sd > 0 else s * 0.0


def build_panel(px: pd.DataFrame) -> pd.DataFrame:
    """One row per (rebalance month-end, sector): signals at T + forward 1m return."""
    month_ends = px.resample("ME").last().index
    rows = []
    for i, dt in enumerate(month_ends[:-1]):
        hist = px.loc[:dt]
        if len(hist) < TD["6m"] + 1:
            continue
        nxt = month_ends[i + 1]
        for sid in px.columns:
            s = hist[sid].dropna()
            r1, r3, r6 = ret(s, TD["1m"]), ret(s, TD["3m"]), ret(s, TD["6m"])
            if r1 is None or r3 is None or r6 is None:
                continue
            # forward 1m return (T -> next month-end), no look-ahead in the signal
            fwd_window = px[sid].loc[dt:nxt].dropna()
            if len(fwd_window) < 2 or fwd_window.iloc[0] == 0:
                continue
            fwd = fwd_window.iloc[-1] / fwd_window.iloc[0] - 1.0
            level = r1 * 0.20 + r3 * 0.45 + r6 * 0.35
            accel = r3 * 4 - r6 * 2
            rows.append({"date": dt, "sector": sid, "level": level,
                         "accel": accel, "fwd": fwd})
    return pd.DataFrame(rows)


def spearman(a: pd.Series, b: pd.Series) -> float:
    return a.rank().corr(b.rank())


def report(panel: pd.DataFrame) -> None:
    # cross-sectional z each month + blend
    panel = panel.copy()
    for col in ("level", "accel"):
        panel[f"z_{col}"] = panel.groupby("date")[col].transform(zscore)
    panel["blend"] = 0.5 * panel["z_level"] + 0.5 * panel["z_accel"]

    n_months = panel["date"].nunique()
    print(f"\n{'='*72}\nBACKTEST — acceleration vs momentum-level   "
          f"({n_months} monthly rebalances, {panel['sector'].nunique()} sectors)\n{'='*72}")

    # 1. Orthogonality
    pooled = panel["level"].corr(panel["accel"])
    per_month = panel.groupby("date").apply(lambda d: d["level"].corr(d["accel"]))
    print("\n1) ORTHOGONALITY  (low corr = ACCEL carries new info)")
    print(f"   pooled Pearson(level, accel)      = {pooled:+.3f}")
    print(f"   mean per-month corr               = {per_month.mean():+.3f}")

    # 2. Predictive rank-IC
    print("\n2) PREDICTIVE RANK-IC  (Spearman signal vs forward 1m return)")
    print(f"   {'signal':<10} {'mean IC':>9} {'t-stat':>8} {'hit%':>7}  (IC>0 share)")
    for sig in ("level", "accel", "blend"):
        ics = panel.groupby("date").apply(lambda d: spearman(d[sig], d["fwd"])).dropna()
        t = ics.mean() / (ics.std() / np.sqrt(len(ics))) if ics.std() > 0 else float("nan")
        hit = (ics > 0).mean() * 100
        print(f"   {sig:<10} {ics.mean():>+9.4f} {t:>8.2f} {hit:>6.1f}%")

    # 3. Quintile spread (top vs bottom by signal -> mean forward return)
    print("\n3) QUINTILE SPREAD  (mean fwd 1m return, top quintile − bottom quintile)")
    print(f"   {'signal':<10} {'topQ':>8} {'botQ':>8} {'spread':>9}")
    for sig in ("level", "accel", "blend"):
        def q_spread(d):
            if len(d) < 5:
                return None
            r = d[sig].rank(pct=True)
            return d.loc[r >= 0.8, "fwd"].mean(), d.loc[r <= 0.2, "fwd"].mean()
        qs = panel.groupby("date").apply(q_spread).dropna()
        top = np.mean([x[0] for x in qs])
        bot = np.mean([x[1] for x in qs])
        print(f"   {sig:<10} {top*100:>7.2f}% {bot*100:>7.2f}% {(top-bot)*100:>+8.2f}%")

    # 4. Strategy sim — monthly long top-k, equal weight; vs equal-weight universe
    print("\n4) STRATEGY SIM  (monthly long top-8, equal weight)")
    K = 8
    def strat_returns(sig):
        out = {}
        for dt, d in panel.groupby("date"):
            top = d.nlargest(K, sig)
            out[dt] = top["fwd"].mean()
        return pd.Series(out).sort_index()
    bench = panel.groupby("date")["fwd"].mean().sort_index()  # equal-weight all = benchmark

    def stats(r):
        cum = (1 + r).prod() - 1
        ann = (1 + r).prod() ** (12 / len(r)) - 1
        sharpe = r.mean() / r.std() * np.sqrt(12) if r.std() > 0 else float("nan")
        eq = (1 + r).cumprod()
        dd = (eq / eq.cummax() - 1).min()
        return cum, ann, sharpe, dd

    print(f"   {'strategy':<14} {'cum':>9} {'ann':>8} {'Sharpe':>8} {'maxDD':>8}")
    for name, r in [("LEVEL", strat_returns("level")),
                    ("ACCEL", strat_returns("accel")),
                    ("BLEND", strat_returns("blend")),
                    ("benchmark(EW)", bench)]:
        c, a, sh, dd = stats(r)
        print(f"   {name:<14} {c*100:>+8.1f}% {a*100:>+7.1f}% {sh:>8.2f} {dd*100:>+7.1f}%")

    print(f"\n{'='*72}")
    print("Read: ACCEL is worth the 15% if (1) corr is low (new info) AND")
    print("(2) its IC/quintile-spread is positive and the BLEND beats LEVEL alone.")
    print(f"{'='*72}\n")


if __name__ == "__main__":
    px = fetch_prices()
    print(f"Price panel: {px.shape[0]} days × {px.shape[1]} sectors "
          f"({px.index.min().date()}→{px.index.max().date()})", file=sys.stderr)
    panel = build_panel(px)
    if panel.empty:
        print("ERROR: empty panel (insufficient history)", file=sys.stderr)
        sys.exit(1)
    report(panel)
