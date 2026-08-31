"""Point-in-time signal backtest — PLAN v8 Fase P.

Monthly panel over the LONGEST-history vehicle per sector (US sibling preferred — measuring
the THEME's signal, not the buyable book; flow-proxy doctrine). Every signal at month-end t
uses only data <= t; forward returns are t -> t+21/63 trading days of the SAME vehicle.
Results go to lake `validation/backtest_ic` and stdout — NEVER to sector_snapshot.

Signals: mom_3m6m (current spec) · momentum_12_1 · near_52w_high · crowding_comomentum ·
cot_crowding · trends_crowding (short panel, ~4y usable). CA / flow_resid / valuation are NOT
reconstructible (no history) and stay on the live-window track.

CLI:
    uv run python -m experiments.backtest_signals [--start 2012-01-31] [--skip-trends] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).parents[1]
_SCRATCH = Path("/private/tmp/claude-501/-Users-abetatos-Documents-Projects-Catalyx/12fcd6c0-2c23-47f9-8fbb-5e3cb97b4198/scratchpad")

_BENCH = "SPY"
_MIN_CROSS = 8          # min sectors for a monthly IC
_MIN_CROSS_BY_SIG = {"cot_crowding": 4}   # structurally a 5-name sleeve (COT_MARKETS)
_MIN_HIST_D = 260       # a vehicle enters the panel once it has 52w of history
_COT_MIN_W = 156        # min trailing weeks before a COT percentile is scored
_COT_WIN_W = 260        # 5y rolling window, same as the live signal
_SUBPERIODS = [("2012-2019", None, "2019-12-31"), ("2020-2022", "2020-01-01", "2022-12-31"),
               ("2023+", "2023-01-01", None)]

# crowding signals enter the composite inverted (penalty): as_used_ic = -raw_ic
_INVERTED = {"crowding_comomentum", "cot_crowding", "trends_crowding"}


def signal_vehicles() -> dict[str, str]:
    """sector -> longest-history vehicle: first US (non-UCITS) ticker in its flow chain,
    else the UCITS primary."""
    from catalyx.data.flow_data import SECTOR_FLOW_TICKERS, _is_ucits

    out = {}
    for sid, chain in SECTOR_FLOW_TICKERS.items():
        us = [t for t in chain if not _is_ucits(t)]
        out[sid] = us[0] if us else chain[0]
    return out


def fetch_prices(tickers: list[str]) -> pd.DataFrame:
    cache = _SCRATCH / "bt_prices.parquet"
    if cache.exists():
        px = pd.read_parquet(cache)
        if set(tickers) <= set(px.columns):
            return px
    import yfinance as yf
    px = yf.download(tickers, period="max", auto_adjust=True, progress=False)["Close"]
    px = px.dropna(how="all")
    cache.parent.mkdir(parents=True, exist_ok=True)
    px.to_parquet(cache)
    return px


# ── price signals (all point-in-time: only data <= t) ────────────────────────

def sig_mom3m6m(s: pd.Series, i: int) -> float | None:
    if i < 126 or pd.isna(s.iloc[i - 126]) or pd.isna(s.iloc[i]):
        return None
    r63 = s.iloc[i] / s.iloc[i - 63] - 1
    r126 = s.iloc[i] / s.iloc[i - 126] - 1
    return 0.5625 * r63 + 0.4375 * r126


def sig_12_1(s: pd.Series, i: int) -> float | None:
    if i < 252 or pd.isna(s.iloc[i - 252]) or pd.isna(s.iloc[i - 21]):
        return None
    return s.iloc[i - 21] / s.iloc[i - 252] - 1


def sig_52w(s: pd.Series, i: int) -> float | None:
    if i < 252 or pd.isna(s.iloc[i]):
        return None
    win = s.iloc[i - 252:i + 1].dropna()
    return None if win.empty else s.iloc[i] / win.max() - 1


def fwd_ret(s: pd.Series, i: int, h: int) -> float | None:
    if i + h >= len(s) or pd.isna(s.iloc[i]) or pd.isna(s.iloc[i + h]):
        return None
    return s.iloc[i + h] / s.iloc[i] - 1


# ── comomentum at t (today's cluster map — the acknowledged grouping caveat) ──

def comomentum_at(px: pd.DataFrame, t, vehicles: dict, sibs: dict[str, list[str]]) -> dict:
    win = px.loc[:t].tail(370)                       # ~52w of dailies
    wk = win.resample("W-FRI").last().pct_change().dropna(how="all")
    if _BENCH not in wk.columns or len(wk) < 40:
        return {}
    b = wk[_BENCH]
    resid = {}
    for c in wk.columns:
        if c == _BENCH:
            continue
        j = pd.concat([wk[c], b], axis=1).dropna()
        if len(j) < 40:
            continue
        var = j.iloc[:, 1].var()
        beta = j.iloc[:, 0].cov(j.iloc[:, 1]) / var if var > 0 else 0.0
        resid[c] = j.iloc[:, 0] - beta * j.iloc[:, 1]
    out = {}
    for sid, others in sibs.items():
        t0 = vehicles.get(sid)
        rhos = []
        for o in others:
            to = vehicles.get(o)
            if t0 in resid and to in resid and to != t0:
                j = pd.concat([resid[t0], resid[to]], axis=1).dropna()
                if len(j) >= 40:
                    r = j.iloc[:, 0].corr(j.iloc[:, 1])
                    if r == r:
                        rhos.append(r)
        if rhos:
            out[sid] = float(np.mean(rhos))
    return out


# ── COT: full weekly history per market, rolling 5y percentile ───────────────

def cot_history() -> dict[str, pd.Series]:
    from catalyx.data.cot_data import COT_MARKETS, _API
    import httpx

    cache = _SCRATCH / "bt_cot.parquet"
    if cache.exists():
        df = pd.read_parquet(cache)
    else:
        rows = []
        for code, spec in COT_MARKETS.items():
            params = {"$select": "report_date_as_yyyy_mm_dd,noncomm_positions_long_all,"
                                 "noncomm_positions_short_all,open_interest_all",
                      "$where": f"cftc_contract_market_code='{code}'",
                      "$order": "report_date_as_yyyy_mm_dd ASC", "$limit": "5000"}
            for r in httpx.get(_API, params=params, timeout=60).json():
                try:
                    oi = float(r["open_interest_all"])
                    if oi <= 0:
                        continue
                    rows.append({"code": code,
                                 "date": str(r["report_date_as_yyyy_mm_dd"])[:10],
                                 "ratio": (float(r["noncomm_positions_long_all"])
                                           - float(r["noncomm_positions_short_all"])) / oi})
                except (KeyError, ValueError, TypeError):
                    continue
        df = pd.DataFrame(rows)
        df.to_parquet(cache)
    out = {}
    for code, g in df.groupby("code"):
        s = g.set_index(pd.to_datetime(g["date"]))["ratio"].sort_index()
        pct = s.rolling(_COT_WIN_W, min_periods=_COT_MIN_W).apply(
            lambda w: (w[:-1] < w[-1]).mean() * 100 + (w[:-1] == w[-1]).mean() * 50, raw=True)
        out[code] = pct
    return out


def cot_at(cot: dict[str, pd.Series], t) -> dict[str, float]:
    from catalyx.data.cot_data import COT_MARKETS

    out = {}
    for code, spec in COT_MARKETS.items():
        s = cot.get(code)
        if s is None:
            continue
        s = s.loc[:t].dropna()
        if s.empty or (t - s.index[-1]).days > 21:
            continue
        for sid in spec["sectors"]:
            out[sid] = float(s.iloc[-1])
    return out


# ── Trends: 5y weekly per term, expanding percentile (short panel) ───────────

def trends_history(sector_ids: list[str]) -> dict[str, pd.Series]:
    from catalyx.data.trends_data import TREND_TERMS, fetch_term
    import time

    cache = _SCRATCH / "bt_trends.parquet"
    if cache.exists():
        df = pd.read_parquet(cache)
        return {c: df[c].dropna() for c in df.columns}
    from pytrends.request import TrendReq
    pt = TrendReq(hl="en-US", tz=0, timeout=(10, 25))
    series = {}
    for i, (sid, term) in enumerate(TREND_TERMS.items()):
        if sid not in sector_ids:
            continue
        if i:
            time.sleep(3)
        vals = fetch_term(term, pytrends=pt)
        if vals:
            idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=len(vals), freq="W-SUN")
            series[sid] = pd.Series(vals, index=idx)
    if series:
        pd.DataFrame(series).to_parquet(cache)
    return series


def trends_at(tr: dict[str, pd.Series], t) -> dict[str, float]:
    out = {}
    for sid, s in tr.items():
        past = s.loc[:t]
        if len(past) < 52:
            continue
        recent = past.iloc[-4:].mean()
        hist = past.iloc[:-4]
        out[sid] = float(((hist < recent).mean() + (hist == recent).mean() * 0.5) * 100)
    return out


# ── panel ─────────────────────────────────────────────────────────────────────

def build_panel(start: str, skip_trends: bool = False) -> pd.DataFrame:
    from catalyx.scorer.comomentum import sector_catalysts, siblings_map

    vehicles = signal_vehicles()
    tickers = sorted(set(vehicles.values())) + [_BENCH]
    px = fetch_prices(tickers)
    px.index = pd.to_datetime(px.index).tz_localize(None)

    sibs = siblings_map(sector_catalysts())
    cot = cot_history()
    trends = {} if skip_trends else trends_history(list(vehicles))

    month_ends = px.loc[start:].resample("ME").last().index
    month_ends = [px.loc[:m].index[-1] for m in month_ends if len(px.loc[:m])]
    month_ends = sorted(set(month_ends))

    rows = []
    for t in month_ends:
        como = comomentum_at(px, t, vehicles, sibs)
        cots = cot_at(cot, t)
        trs = trends_at(trends, t) if trends else {}
        for sid, tk in vehicles.items():
            if tk not in px.columns:
                continue
            s = px[tk]
            i = s.index.get_loc(t)
            hist = s.iloc[:i + 1].dropna()
            if len(hist) < _MIN_HIST_D:
                continue
            rows.append({
                "t": t, "sector_id": sid, "ticker": tk,
                "mom_3m6m": sig_mom3m6m(s, i),
                "momentum_12_1": sig_12_1(s, i),
                "near_52w_high": sig_52w(s, i),
                "crowding_comomentum": como.get(sid),
                "cot_crowding": cots.get(sid),
                "trends_crowding": trs.get(sid),
                "fwd21": fwd_ret(s, i, 21),
                "fwd63": fwd_ret(s, i, 63),
            })
    return pd.DataFrame(rows)


# ── inference ────────────────────────────────────────────────────────────────

SIGNALS = ["mom_3m6m", "momentum_12_1", "near_52w_high",
           "crowding_comomentum", "cot_crowding", "trends_crowding"]


def monthly_ics(panel: pd.DataFrame, sig: str, horizon: str) -> pd.Series:
    out = {}
    min_cross = _MIN_CROSS_BY_SIG.get(sig, _MIN_CROSS)
    for t, g in panel.groupby("t"):
        g = g[[sig, horizon]].dropna()
        if len(g) < min_cross or g[sig].nunique() < 2 or g[horizon].nunique() < 2:
            continue
        out[t] = g[sig].rank().corr(g[horizon].rank())
    s = pd.Series(out).dropna()
    return -s if sig in _INVERTED else s          # as-used: positive = orders correctly


def block_se(ics: pd.Series, block: int = 3, n_boot: int = 2000, seed: int = 7) -> float | None:
    """Bootstrap se of the mean IC with 3-month blocks (63d windows overlap monthly steps)."""
    v = ics.values
    if len(v) < block * 2:
        return None
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(len(v) / block))
    means = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(v) - block + 1, nb)
        sample = np.concatenate([v[i:i + block] for i in idx])[:len(v)]
        means.append(sample.mean())
    return float(np.std(means))


def summarize(panel: pd.DataFrame, horizon: str = "fwd63") -> list[dict]:
    out = []
    for sig in SIGNALS:
        ics = monthly_ics(panel, sig, horizon)
        if ics.empty:
            out.append({"signal": sig, "n_months": 0})
            continue
        row = {"signal": sig, "horizon": horizon, "n_months": len(ics),
               "n_eff": round(len(ics) / 3, 1),
               "mean_ic": round(float(ics.mean()), 4),
               "se_block": round(block_se(ics) or float("nan"), 4),
               "share_positive": round(float((ics > 0).mean()), 3)}
        for label, lo, hi in _SUBPERIODS:
            sub = ics.loc[(ics.index >= (lo or ics.index.min())) &
                          (ics.index <= (hi or ics.index.max()))]
            row[f"ic_{label}"] = round(float(sub.mean()), 3) if len(sub) >= 6 else None
        out.append(row)
    return out


def signal_correlation(panel: pd.DataFrame) -> pd.DataFrame:
    """Pooled cross-sectional correlation: z per month per signal, then Spearman across
    sector-months, pairwise complete. Crowding signals sign-flipped to as-used orientation."""
    z = {}
    for sig in SIGNALS:
        col = {}
        min_cross = _MIN_CROSS_BY_SIG.get(sig, _MIN_CROSS)
        for t, g in panel.groupby("t"):
            v = g.set_index("sector_id")[sig].dropna()
            if len(v) < min_cross or v.std() == 0:
                continue
            zz = (v - v.mean()) / v.std()
            for sid, val in zz.items():
                col[(t, sid)] = -val if sig in _INVERTED else val
        z[sig] = pd.Series(col)
    df = pd.DataFrame(z)
    return df.corr(method="spearman", min_periods=200)


def gk_weights(summary: list[dict], omega: pd.DataFrame, prior_windows: float = 12.0) -> dict:
    """Grinold-Kahn w ∝ Ω⁻¹·IC over the measured signals, shrunk by per-signal credibility
    n_eff/(n_eff+prior). No grid search — closed form only."""
    sigs = [r["signal"] for r in summary if r.get("n_months", 0) >= 24
            and r["signal"] in omega.columns and not np.isnan(r.get("mean_ic", np.nan))]
    if len(sigs) < 2:
        return {}
    ic = np.array([next(r["mean_ic"] for r in summary if r["signal"] == s) for s in sigs])
    cred = np.array([next(r["n_eff"] / (r["n_eff"] + prior_windows)
                          for r in summary if r["signal"] == s) for s in sigs])
    O = omega.loc[sigs, sigs].fillna(0).values
    O = 0.9 * O + 0.1 * np.eye(len(sigs))          # ridge so a near-singular Ω cannot explode
    w = np.linalg.solve(O, ic * cred)
    return {s: round(float(x), 4) for s, x in zip(sigs, w)}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="CATALYX v8 point-in-time signal backtest")
    p.add_argument("--start", default="2012-01-31")
    p.add_argument("--horizon", default="fwd63", choices=["fwd21", "fwd63"])
    p.add_argument("--skip-trends", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    panel = build_panel(args.start, skip_trends=args.skip_trends)
    summary = summarize(panel, args.horizon)
    omega = signal_correlation(panel)
    gk = gk_weights(summary, omega)

    result = {"start": args.start, "horizon": args.horizon,
              "panel_rows": len(panel), "months": int(panel["t"].nunique()),
              "summary": summary,
              "omega": omega.round(3).to_dict(),
              "gk_raw_weights": gk,
              "caveats": [
                  "US-sibling vehicles: measures the THEME's signal, not the buyable book",
                  "comomentum clusters use TODAY's catalyst map (grouping look-ahead, accepted)",
                  "in-sample forever once used to set weights - live calibration windows are the out-of-sample scoreboard",
                  "CA / flow_resid / valuation NOT backtestable - stay on the live track",
              ]}

    # persist to the lake (its own table - never sector_snapshot)
    try:
        from catalyx.store import lake
        rows = [{**r, "start": args.start, "computed_at": date.today().isoformat()}
                for r in summary if r.get("n_months")]
        lake.append_partition("backtest_ic", pd.DataFrame(rows),
                             {"as_of": date.today().isoformat()}, overwrite=True)
    except Exception as e:  # noqa: BLE001
        result["lake_write_error"] = str(e)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    print(f"CATALYX — backtest punto-en-tiempo  [{args.start} → hoy]  "
          f"{result['months']} meses · {result['panel_rows']} sector-mes · horizonte {args.horizon}\n")
    hdr = (f"{'señal':<22}{'IC medio':>9}{'se':>7}{'n_mes':>7}{'n_eff':>7}{'%>0':>6}"
           + "".join(f"{lbl:>12}" for lbl, _, _ in _SUBPERIODS))
    print(hdr); print("-" * len(hdr))
    for r in summary:
        if not r.get("n_months"):
            print(f"{r['signal']:<22}{'sin datos':>9}"); continue
        print(f"{r['signal']:<22}{r['mean_ic']:>+9.3f}{r['se_block']:>7.3f}{r['n_months']:>7}"
              f"{r['n_eff']:>7.1f}{r['share_positive']:>6.0%}"
              + "".join(f"{(r.get('ic_' + lbl) if r.get('ic_' + lbl) is not None else float('nan')):>+12.3f}"
                        for lbl, _, _ in _SUBPERIODS))
    print("\nΩ (correlación entre señales, orientación as-used):")
    print(omega.round(2).to_string())
    print("\nPesos Grinold–Kahn crudos (Ω⁻¹·IC·cred, sin normalizar — el REPARTO relativo es lo que informa):")
    for s, w in sorted(gk.items(), key=lambda kv: -abs(kv[1])):
        print(f"  {s:<22}{w:>+8.4f}")
    print("\nCaveats:"); [print(f"  · {c}") for c in result["caveats"]]


if __name__ == "__main__":
    main()
