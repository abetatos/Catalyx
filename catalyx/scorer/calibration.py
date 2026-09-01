"""Scoring calibration — does the composite actually predict forward returns?

WHY (v3 Phase 2, docs/PLAN_v3_lean_pipeline_rebalance.md §3.3):
    Every heatmap carried the banner "⚠ PRE-CALIBRATION: weights unvalidated (0 closed
    positions)", and it was never going to clear: calibration had been tied to closing 50
    positions, which at this book's cadence is years away. Meanwhile the answer was already
    sitting in the lake — every recorded run stores a ranking, and time supplies the outcome.
    A run only has to be old enough to have forward history; no closes required.

    (`snapshot_repo.validate_run` was the first attempt at this and had never once executed —
    it crashed on a NaN `primary_etf` and then on pandas' `method="spearman"` routing through
    scipy, which is not a dependency. Both fixed 2026-08-27. This module is the durable version:
    per-DIMENSION, persisted, and honest about its own sample size.)

WHAT IT MEASURES, per run and per scoring dimension:
    rank_ic      Spearman rank correlation between the dimension's score at run time and the
                 realized forward return of each sector's vehicle. +1 = perfect ordering,
                 0 = no information, −1 = exactly inverted.
    se           ≈ 1/√(n−1), the standard error of a rank correlation. PRINTED ALONGSIDE ALWAYS.
    verdict      `noise` when |IC| < 2·se — with n≈26 that is |IC| < 0.4, so most single-run
                 readings are honestly indistinguishable from zero and must not move a weight.
    top_k spread mean forward return of the top-k minus the rest — the tradable version of IC.

TWO METHODOLOGICAL CHOICES THAT MATTER
  1. Vehicles are remapped to what is BUYABLE TODAY (`snapshot_repo._primary_etf`), not the
     ticker stored in the old snapshot. Runs before the 2026-08-27 universe rewrite reference
     US non-UCITS ETFs a Spanish retail investor cannot hold, so their returns measure a book
     that could never have been owned. Restricted to `investable` sectors for the same reason.
  2. `horizon_days` gives every run an EQUAL-LENGTH window instead of "from run date until
     today". Measuring all runs to a common end date makes an old run look better or worse purely
     for having been earlier, and makes the windows nest inside one another.

WHAT IT DOES NOT DO — it never changes a weight. Overlapping windows in one market regime are
close to a SINGLE observation however many runs they span; `aggregate()` reports the effective
sample so that is visible. Changing `scoring_weights.yaml` stays a deliberate human commit.

CLI:
    uv run python -m catalyx.scorer.calibration                    # every run with history
    uv run python -m catalyx.scorer.calibration --run-id <id>
    uv run python -m catalyx.scorer.calibration --horizon-days 63 --write
    uv run python -m catalyx.scorer.calibration --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]
_TAXONOMY = _REPO_ROOT / "catalyx" / "config" / "sector_taxonomy.yaml"
_TABLE = "calibration"

# The scored dimensions, and how each ENTERS the composite. `inverted` means the composite uses
# (100 − score), so the sign of its contribution is the NEGATIVE of the raw correlation — the
# one place a careless reading flips a conclusion, so it is data, not a comment.
DIMENSIONS = {
    "composite": False,
    "momentum": False,
    "catalyst_alignment": False,
    "flow_confirmation": False,
    "crowding_risk": True,
}

# v7 M6 — candidate signals: weight 0 in the composite, measured with the same IC/se/verdict
# machinery so evidence accumulates from the day the column ships. Promotion to a weight stays
# a human config edit (PLAN_v7 §5). Absent columns (older runs) are skipped silently.
CANDIDATE_DIMENSIONS = {
    "momentum_12_1": False,
    "near_52w_high": False,
    "ca_unpriced": False,
    "flow_resid": False,
    "inst_sponsorship": False,
    "crowding_comomentum": True,   # crowding is a penalty: it would enter inverted
    "cot_crowding": True,
    "trends_crowding": True,
    "crowding_measured": True,
    # v11 P5/P6. `ca_logit` is CA on a scale that still separates the nine sectors the level
    # scale packs into 1.7 points — if it carries IC that CA does not, the resolution at the
    # ceiling was doing work. `event_tape_z` is the first column in the chain that is not read
    # from a document: it asks whether the market moved the way each event claimed.
    "ca_logit": False,
    "event_tape_z": False,
}
DIMENSIONS = {**DIMENSIONS, **CANDIDATE_DIMENSIONS}

DEFAULT_HORIZON_DAYS = 63          # ≈3 months of calendar days — the book's decision horizon
MIN_SECTORS = 8                    # below this, a rank correlation is not worth reporting
TOP_K = 5

# Rank buckets for the €-denominated edge estimate (rebalance.py). An IC is a correlation and
# cannot be multiplied by a trade size; a bucket's mean forward return can. Buckets are coarse on
# purpose — with ~26 sectors and a handful of windows, anything finer is fitting noise.
BUCKETS: list[tuple[str, int, int | None]] = [
    ("top3",  1,  3),
    ("mid",   4,  10),
    ("rest",  11, None),
]
_BUCKET_TABLE = "calibration_bucket"


# ── Pure statistics (unit-tested, no I/O) ────────────────────────────────────

def rank_ic(scores: list[float], returns: list[float]) -> float | None:
    """Spearman = Pearson on ranks. Computed via pandas' `.rank()` (ties averaged, exactly as
    scipy would) so this needs no scipy — the dependency that had silently killed the original."""
    import pandas as pd

    if scores is None or returns is None or len(scores) != len(returns) or len(scores) < 3:
        return None
    s, r = pd.Series(scores, dtype="float64"), pd.Series(returns, dtype="float64")
    ok = s.notna() & r.notna()
    if ok.sum() < 3 or s[ok].nunique() < 2 or r[ok].nunique() < 2:
        return None                                   # a constant column has no ordering
    val = s[ok].rank().corr(r[ok].rank())
    return None if val != val else round(float(val), 3)   # NaN-safe


def ic_standard_error(n: int) -> float | None:
    """≈ 1/√(n−1). The whole point of carrying it: with n=26, SE≈0.20, so a single-run IC of
    −0.18 is INSIDE one standard error of zero and means nothing on its own."""
    if not n or n < 3:
        return None
    return round(1.0 / ((n - 1) ** 0.5), 3)


def ic_verdict(ic: float | None, se: float | None) -> str:
    """`noise` / `weak` / `signal` — a guard against reading a single number as a finding."""
    if ic is None or se is None:
        return "insufficient"
    if abs(ic) < 2 * se:
        return "noise"
    if abs(ic) < 3 * se:
        return "weak"
    return "signal"


def contribution_ic(ic: float | None, inverted: bool) -> float | None:
    """The IC of the dimension AS THE COMPOSITE USES IT.

    `crowding_risk` enters as (100 − crowding), so a raw +0.2 (more-crowded did better) means
    the composite's crowding penalty contributed −0.2. Getting this backwards inverts the
    conclusion about the only dimension that is inverted, so it is computed, never eyeballed.
    """
    if ic is None:
        return None
    return round(-ic, 3) if inverted else ic


def top_k_spread(scores: list[float], returns: list[float], k: int = TOP_K) -> dict:
    """Mean forward return of the top-k by score, minus the rest — IC's tradable twin."""
    import pandas as pd

    df = pd.DataFrame({"s": scores, "r": returns}).dropna()
    if len(df) < k + 2:
        return {"top_k": None, "rest": None, "spread": None, "k": k}
    df = df.sort_values("s", ascending=False)
    top, rest = df.head(k)["r"].mean(), df.tail(len(df) - k)["r"].mean()
    return {"top_k": round(float(top) * 100, 2), "rest": round(float(rest) * 100, 2),
            "spread": round(float(top - rest) * 100, 2), "k": k}


def bucket_returns(scores: list[float], returns: list[float],
                   buckets=BUCKETS) -> dict[str, float | None]:
    """Mean forward return (%) per rank bucket, ranked by score descending.

    This is the piece a € figure can be built on: `expected_edge_eur = trade_eur × E[r|bucket]`.
    A bucket with fewer than 2 members returns None rather than a one-sector "expectation".
    """
    import pandas as pd

    df = pd.DataFrame({"s": scores, "r": returns}).dropna()
    if df.empty:
        return {name: None for name, _, _ in buckets}
    df = df.sort_values("s", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1
    out: dict[str, float | None] = {}
    for name, lo, hi in buckets:
        sel = df[(df["rank"] >= lo) & (df["rank"] <= (hi if hi is not None else len(df)))]
        out[name] = round(float(sel["r"].mean()) * 100, 3) if len(sel) >= 2 else None
    return out


def bucket_of(rank: int | None, buckets=BUCKETS) -> str | None:
    """Which bucket a model rank falls in. None (unranked / not in the model book) → None."""
    if rank is None:
        return None
    for name, lo, hi in buckets:
        if rank >= lo and (hi is None or rank <= hi):
            return name
    return None


def shrink_factor(effective_windows: int, prior_windows: float) -> float:
    """`n_eff / (n_eff + prior)` — how much of a measured bucket return to believe.

    With one non-overlapping window and a prior of 6, this is 0.14: the engine may act on a
    seventh of what it measured. That is the honest translation of "n=1" into a number, and it
    is what stops a noise-grade IC from justifying a tax-realizing trade.
    """
    n = max(0, int(effective_windows))
    return round(n / (n + float(prior_windows)), 4) if (n + prior_windows) > 0 else 0.0


def expected_returns(lake_dir: Path | None = None, prior_windows: float = 6.0) -> dict:
    """Shrunk expected forward return (%) per rank bucket, read from the accumulated lake table.

    Returns `{buckets: {name: pct}, raw: {...}, effective_windows, shrink, horizon_days, note}`.
    Every consumer gets the shrink factor and the window count alongside the number, so nobody
    can quote the expectation without its sample size.
    """
    from catalyx.store import lake

    empty = {"buckets": {name: 0.0 for name, _, _ in BUCKETS}, "raw": {}, "runs": 0,
             "effective_windows": 0, "shrink": 0.0, "horizon_days": DEFAULT_HORIZON_DAYS,
             "note": "no calibration windows yet — expected edge is 0 by construction"}
    try:
        df = lake.read_table(_BUCKET_TABLE, lake_dir=lake_dir)
    except Exception:
        return empty
    if df.empty:
        return empty

    horizon = int(df["horizon_days"].dropna().iloc[0]) if "horizon_days" in df.columns \
        and not df["horizon_days"].dropna().empty else DEFAULT_HORIZON_DAYS
    starts = sorted({str(v)[:10] for v in df["start"].dropna()})
    effective, last = 0, None
    for s in starts:
        d = date.fromisoformat(s)
        if last is None or (d - last).days >= horizon:
            effective += 1
            last = d
    shrink = shrink_factor(effective, prior_windows)

    raw, shrunk = {}, {}
    for name, _, _ in BUCKETS:
        col = df[df["bucket"] == name]["mean_fwd_pct"].dropna() if "bucket" in df.columns \
            else []
        mean = round(float(sum(col) / len(col)), 3) if len(col) else None
        raw[name] = mean
        shrunk[name] = round((mean or 0.0) * shrink, 3)
    return {"buckets": shrunk, "raw": raw, "runs": int(df["run_id"].nunique()),
            "effective_windows": effective, "shrink": shrink, "horizon_days": horizon,
            "note": f"mean bucket return over ~{effective} non-overlapping {horizon}d window(s), "
                    f"shrunk toward 0 by {shrink:.2f} (prior {prior_windows:g} windows)"}


def composite_ic(lake_dir: Path | None = None, dimension: str = "composite") -> dict:
    """The measured rank IC of one scoring dimension, with its standard error and verdict.

    `expected_returns` answers "what did each bucket earn?"; this answers the prior question,
    "does the ranking order anything at all?". The rebalance gate needs both (plan v4 §3 B2): a
    bucket table built on a ranking whose IC is negative will systematically invert the rule it
    is supposed to enforce, and a window COUNT cannot see that — only the sign can.

    Averaged over COMPLETE windows only; an open window has no forward return to correlate with.
    `effective_windows` counts NON-OVERLAPPING horizons among those: three runs six days apart
    over one 63-day horizon are one observation, and the tilt shrinkage (B1) divides by that
    honest denominator, never by the row count.
    """
    from catalyx.store import lake

    empty = {"ic": None, "se": None, "n_windows": 0, "effective_windows": 0, "n_sectors": None,
             "dimension": dimension, "verdict": "unmeasured",
             "note": "no complete calibration window yet"}
    try:
        df = lake.read_table(_TABLE, lake_dir=lake_dir)
    except Exception:
        return empty
    if df.empty or "dimension" not in df.columns:
        return empty
    df = df[df["dimension"] == dimension]
    if "window_complete" in df.columns:
        df = df[df["window_complete"].fillna(False).astype(bool)]
    ics = [float(v) for v in df.get("as_used_ic", []) if v == v]
    if not ics:
        return empty
    ses = [float(v) for v in df.get("se", []) if v == v]
    ic = round(sum(ics) / len(ics), 4)
    se = round(sum(ses) / len(ses), 4) if ses else None
    n_sec = int(df["n_sectors"].dropna().iloc[0]) if "n_sectors" in df.columns \
        and not df["n_sectors"].dropna().empty else None
    return {"ic": ic, "se": se, "n_windows": len(ics),
            "effective_windows": _effective_windows(df),
            "n_sectors": n_sec, "dimension": dimension,
            "verdict": ic_verdict(ic, se),
            "note": (f"mean {dimension} rank IC {ic:+.3f} over {len(ics)} complete window(s)"
                     + (f", se ≈ {se:.3f} (n={n_sec})" if se else "")
                     + f" → {ic_verdict(ic, se)}")}


def _effective_windows(df) -> int:
    """Non-overlapping horizons among the rows of `df` (columns `start`, `horizon_days`)."""
    try:
        starts = sorted(date.fromisoformat(str(x)[:10]) for x in df["start"].dropna())
    except Exception:
        return 0
    if not starts:
        return 0
    horizons = [int(h) for h in df.get("horizon_days", []) if h == h]
    horizon = horizons[0] if horizons else DEFAULT_HORIZON_DAYS
    n, last = 0, None
    for s in starts:
        if last is None or (s - last).days >= horizon:
            n += 1
            last = s
    return n


def skill_lambda(lake_dir: Path | None = None, dimension: str = "composite",
                 ic_target: float = 0.20, prior_windows: float = 3.0,
                 floor: float = 0.0) -> dict:
    """How much of the model's conviction tilt is EARNED — λ ∈ [0, 1] (plan v4 §3 B1).

        λ = clamp(IC / ic_target, 0, 1) · n_eff/(n_eff + prior_windows)

    Two independent haircuts, because two different things can be wrong with a tilt:
    the ranking may not order returns (the IC leg), and it may not have been measured
    often enough for its IC to mean anything (the credibility leg, the same
    `shrink_factor` the bucket table already uses).

    A NEGATIVE IC clamps to zero; it never goes negative. Shorting your own ranking on one
    non-overlapping window is a superstition with a minus sign — the honest response to an
    anti-signal that small is to stop sizing on it, not to size on its inverse.

    λ decides only HOW the working capital is tilted. It never touches how much is at work:
    the neutral book is the same names at the same gross, so `deploy_ratio` is untouched.
    """
    m = composite_ic(lake_dir=lake_dir, dimension=dimension)
    ic, target = m.get("ic"), float(ic_target)
    n_eff = int(m.get("effective_windows") or 0)
    cred = shrink_factor(n_eff, prior_windows)
    ic_leg = 0.0 if (ic is None or target <= 0) else max(0.0, min(1.0, ic / target))
    lam = round(max(float(floor), ic_leg * cred), 4)
    if ic is None:
        why = f"{dimension} IC unmeasured → tilt not earned yet"
    elif ic <= 0:
        why = (f"{dimension} IC {ic:+.3f} ≤ 0 → no tilt earned "
               f"(a negative IC removes conviction, it never inverts the book)")
    else:
        why = (f"{dimension} IC {ic:+.3f} / target {target:.2f} = {ic_leg:.2f}, "
               f"credibility {cred:.2f} on {n_eff} independent window(s)")
    return {"lambda": lam, "ic": ic, "se": m.get("se"), "verdict": m.get("verdict"),
            "dimension": dimension, "n_windows": m.get("n_windows"), "effective_windows": n_eff,
            "credibility": cred, "ic_target": target, "prior_windows": float(prior_windows),
            "floor": float(floor),
            "note": f"λ = {lam:.2f} — {why}"}


# ── Data assembly ────────────────────────────────────────────────────────────

def _investable_sectors() -> set[str]:
    import yaml

    data = yaml.safe_load(_TAXONOMY.read_text(encoding="utf-8")) or {}
    return {s["id"] for s in data.get("sectors", [])
            if s.get("investable") and not s.get("watch_only")}


def forward_returns(tickers: list[str], start: str, end: str, allow_fetch: bool = True) -> dict:
    """{ticker: simple return over [start, end]} from the shared price cache, IN EUR.

    Reading from the cache (not a fresh fetch) is what makes a calibration re-run reproducible:
    this number is the honest answer to "did the score predict anything" and must not drift
    because it was recomputed on a different afternoon.

    EUR since v10.4, and this is the half that matters most. Everything here — the rank IC that
    gates lambda, the rule scorecard's HOLD baseline, the CA-tilt tally, the override pricing —
    scores a decision against WHAT THE OPERATOR EARNED. Native closes answer a different
    question: what a dollar buyer would have earned. With 15 of 26 vehicles quoted outside the
    euro that gap is not a rounding detail, and it lands on the TARGET, so it contaminated every
    verdict regardless of what the signal was measured in.
    """
    from catalyx.data import prices

    px = prices.read_eur(tickers, start, end, allow_fetch=allow_fetch)
    out = {}
    if px is None or px.empty:
        return out
    for t in px.columns:
        s = px[t].dropna()
        if len(s) >= 2 and float(s.iloc[0]):
            out[t] = float(s.iloc[-1] / s.iloc[0] - 1.0)
    return out


def ca_tilt_edge(portfolio_id: str = "catalyx", horizon_days: int = DEFAULT_HORIZON_DAYS,
                 as_of: str | None = None, lake_dir: Path | None = None,
                 allow_fetch: bool = True, fwd_fn=None, min_gap_days: int = 21) -> dict:
    """Did the CA conviction tilt PAY? `Σ (w_post − w_pre) · fwd_return`, per complete window.

    THE LEDGER OF v10 P8. The CA tilt is a DECLARED PRIOR — the one sizing leg that runs outside
    the λ gate, because a tilt that never moves euros can never be measured. This function is
    what that exemption costs: every model book since P8 persists the δ=0 counterfactual weight
    (`w_pre_ca_pct`) beside the final one, and here the difference is priced over the same
    horizon every other decision is priced on. Positive = the euros the delta moved toward high
    CA beat the euros it took from low CA.

    THE TALLY LOCALIZES, IT NEVER DISABLES (user doctrine, 2026-09-01). CA is the core of the
    approach and does not have an off switch, so the output is deliberately not a verdict on
    CA's existence: contributions are decomposed per SECTOR and per DRIVING CATALYST (clusters
    overlap, same rule as `exposure_eur` — a sector serving two drivers credits both). A
    negative stretch names WHICH catalyst's score misled the sizing, and the fix lives in how
    that score is computed — indicators, decay, noisy-OR, freshness — or, with the tally in
    front, in the SIZE of δ (a frozen-threshold config edit, floored at `ca_tilt_delta_min`).

    One run per review DATE (latest wins — same convention as rebalance's rank streaks), only
    COMPLETE windows, and `effective_windows` counts the non-overlapping ones: five runs inside
    one horizon are one observation, not five.
    """
    from catalyx.store import lake

    fwd_fn = fwd_fn or forward_returns
    today = as_of or date.today().isoformat()
    empty = {"portfolio_id": portfolio_id, "horizon_days": int(horizon_days), "windows": [],
             "n_windows": 0, "effective_windows": 0, "mean_edge_pp": None,
             "total_edge_pp": None, "per_sector": {}, "per_catalyst": {},
             "verdict": "unmeasured"}
    df = lake.read_table("portfolio_holding", lake_dir=lake_dir)
    if df.empty or "w_pre_ca_pct" not in df.columns:
        return {**empty, "note": "no run has persisted the CA-tilt ledger yet (w_pre_ca_pct) — "
                                 "the first post-P8 model book starts the clock"}
    df = df[(df["portfolio_id"] == portfolio_id) & df["w_pre_ca_pct"].notna()
            & df["weight_pct"].notna()].copy()
    if df.empty:
        return {**empty, "note": f"no {portfolio_id} book carries the CA-tilt ledger yet"}

    by_date: dict[str, str] = {}
    for rid in sorted(str(r) for r in df["run_id"].unique()):
        if rid.startswith("run_") and len(rid) >= 12:
            by_date[rid[4:12]] = rid                       # later run of a date wins
    windows: list[dict] = []
    per_sector: dict[str, dict] = {}
    for d8 in _review_cycle_dates(sorted(by_date), min_gap_days):
        try:
            start = date(int(d8[:4]), int(d8[4:6]), int(d8[6:8]))
        except ValueError:
            continue
        window_end = start + timedelta(days=int(horizon_days))
        if window_end.isoformat() > today:
            continue                                       # open window — not priced yet
        rows = df[df["run_id"] == by_date[d8]]
        etfs = sorted({str(t) for t in rows["primary_etf"].dropna() if str(t).strip()})
        fwd = fwd_fn(etfs, start.isoformat(), window_end.isoformat(), allow_fetch=allow_fetch)
        edge, contribs = 0.0, []
        for _, r in rows.iterrows():
            ret = fwd.get(str(r.get("primary_etf")))
            if ret is None:
                continue
            dw = (float(r["weight_pct"]) - float(r["w_pre_ca_pct"])) / 100.0
            if abs(dw) < 1e-9:
                continue
            pp = dw * float(ret) * 100.0                   # points of the book, EUR-neutral
            edge += pp
            sid = str(r["sector_id"])
            contribs.append({"sector_id": sid, "delta_w_pp": round(dw * 100.0, 2),
                             "fwd_pct": round(float(ret) * 100.0, 2), "edge_pp": round(pp, 3)})
            agg = per_sector.setdefault(sid, {"edge_pp": 0.0, "n_windows": 0})
            agg["edge_pp"] += pp
            agg["n_windows"] += 1
        delta = None
        if "ca_tilt_delta" in rows.columns and len(rows):
            v = rows["ca_tilt_delta"].iloc[0]
            delta = None if v is None or v != v else float(v)
        windows.append({"run_id": by_date[d8], "start": start.isoformat(),
                        "end": window_end.isoformat(), "delta": delta,
                        "edge_pp": round(edge, 3),
                        **_gross_decomposition(contribs),
                        "contributions": sorted(contribs, key=lambda c: c["edge_pp"])})

    if not windows:
        return {**empty, "note": "the CA-tilt ledger exists but no window has closed yet — "
                                 f"first verdict ~{horizon_days}d after the first post-P8 run"}
    return _edge_summary(empty, windows, per_sector, int(horizon_days), label="the CA tilt")


def _gross_decomposition(contribs: list[dict]) -> dict:
    """Split a tilt window's edge into the reallocation it intended and the GROSS it did not.

    v12 B9 — `edge_pp = Σ dwᵢ·rᵢ` is only "what the tilt bought" when `Σdw = 0`. `ca_tilt` is
    total-preserving by construction, but `water_fill`, the contested gross-down and the
    deadband all run AFTER it, and they do not have to give back exactly what they took: on
    `run_20260831_184616` the recorded book summed to 99.90% against a pre-tilt 99.33%, so
    +0.57pp of extra GROSS was being priced as tilt skill. That term is beta — it pays whenever
    the book is up, whatever the tilt chose. The identity is exact:

        Σ dwᵢ·rᵢ  =  (Σdw)·r̄  +  Σ dwᵢ·(rᵢ − r̄)

    `edge_pp` keeps its meaning (what the book actually got — the number is not silently
    redefined); `edge_pp_ex_gross` is the reallocation on its own, which is the part the tilt's
    δ is answerable for, and `gross_dw_pp` says how far apart they are.
    """
    if not contribs:
        return {"gross_dw_pp": 0.0, "edge_pp_ex_gross": 0.0, "gross_edge_pp": 0.0}
    gross_dw = sum(c["delta_w_pp"] for c in contribs) / 100.0
    mean_r = sum(c["fwd_pct"] for c in contribs) / len(contribs) / 100.0
    gross_edge = gross_dw * mean_r * 100.0
    total = sum(c["edge_pp"] for c in contribs)
    return {"gross_dw_pp": round(gross_dw * 100.0, 3),
            "gross_edge_pp": round(gross_edge, 3),
            "edge_pp_ex_gross": round(total - gross_edge, 3)}


def _review_cycle_dates(dates: list[str], min_gap_days: int = 21) -> list[str]:
    """One `YYYYMMDD` per review CYCLE — the rebalance's own collapse (`_cycle_runs`), reused so
    "a review" means the same thing in this ledger as in the rule table. The run history is
    development-shaped (five runs one afternoon, three in one week): pricing a window per DATE
    would let a burst of iteration count as several observations of one decision. Each cluster
    keeps its LATEST reading — the run the decision was actually about."""
    from catalyx.execution.rebalance import _cycle_runs         # lazy — rebalance imports us
    return _cycle_runs(sorted(dates), len(dates), min_gap_days)


def _edge_summary(base: dict, windows: list[dict], per_sector: dict[str, dict],
                  horizon_days: int, label: str) -> dict:
    """The shared tail of the live and retro CA-tilt ledgers: effective windows, the
    per-sector/per-driver localization map, and a verdict phrased as a diagnosis — never as an
    off switch (user doctrine, 2026-09-01)."""
    # non-overlapping windows, same arithmetic as _effective_windows
    n_eff, last = 0, None
    for w in windows:
        s = date.fromisoformat(w["start"])
        if last is None or (s - last).days >= int(horizon_days):
            n_eff, last = n_eff + 1, s
    per_sector = {k: {"edge_pp": round(v["edge_pp"], 3), "n_windows": v["n_windows"]}
                  for k, v in sorted(per_sector.items(), key=lambda kv: kv[1]["edge_pp"])}

    # the localization map: sector contributions credited to every driver the sector serves
    per_catalyst: dict[str, float] = {}
    try:
        from catalyx.execution import portfolio as _pf                    # lazy — no cycle
        cmap = _pf._sector_catalyst_map() or {}
    except Exception:                                      # pragma: no cover - defensive
        cmap = {}
    for sid, agg in per_sector.items():
        for cid in (cmap.get(sid) or ["(unattributed)"]):
            per_catalyst[cid] = round(per_catalyst.get(cid, 0.0) + agg["edge_pp"], 3)
    per_catalyst = dict(sorted(per_catalyst.items(), key=lambda kv: kv[1]))

    total = round(sum(w["edge_pp"] for w in windows), 3)
    mean = round(total / len(windows), 3)
    worst = next(iter(per_catalyst.items()), None)
    verdict = "paid" if mean > 0 else ("cost" if mean < 0 else "flat")
    # v12 B9 — the beta-free half, and how big the gross term it removes actually is. Reported
    # beside the realized number, never instead of it: `edge_pp` is what the book got, and
    # `mean_edge_pp_ex_gross` is the part δ is answerable for.
    mean_ex = round(sum(w.get("edge_pp_ex_gross", w["edge_pp"]) for w in windows)
                    / len(windows), 3)
    max_dw = max((abs(w.get("gross_dw_pp") or 0.0) for w in windows), default=0.0)
    return {
        **base,
        "windows": windows, "n_windows": len(windows), "effective_windows": n_eff,
        "mean_edge_pp": mean, "total_edge_pp": total,
        "mean_edge_pp_ex_gross": mean_ex, "max_gross_dw_pp": round(max_dw, 3),
        "per_sector": per_sector, "per_catalyst": per_catalyst, "verdict": verdict,
        "note": (f"{label} {verdict} {mean:+.2f}pp of book per {horizon_days}d window over "
                 f"{len(windows)} window(s) (~{n_eff} independent)."
                 + (f" The priced rows carry up to {max_dw:+.2f}pp of NET weight, so "
                    f"{mean - mean_ex:+.2f}pp of that is gross (beta), not tilt: "
                    f"{mean_ex:+.2f}pp is the reallocation on its own."
                    if max_dw >= 0.1 else "")
                 + (f" Largest drag: {worst[0]} ({worst[1]:+.2f}pp) — evidence about HOW that "
                    f"catalyst's score is computed, never about disabling CA."
                    if worst is not None and worst[1] < 0 else "")),
    }


def ca_tilt_edge_retro(portfolio_id: str = "catalyx", horizon_days: int = DEFAULT_HORIZON_DAYS,
                       as_of: str | None = None, lake_dir: Path | None = None,
                       allow_fetch: bool = True, fwd_fn=None,
                       delta: float | None = None, winsor_z: float | None = None,
                       persist: bool = False, min_gap_days: int = 21,
                       scale: str | None = None) -> dict:
    """What WOULD the CA tilt have earned on the books recorded BEFORE it existed?

    THE COUNTERFACTUAL, MIRRORED. Since v10 P8 the live ledger stores the δ=0 book beside the
    real one; the runs recorded before P8 are the OTHER half of the same question — those books
    ARE the δ=0 reality, so here the tilt is applied a posteriori: take each recorded book's
    weights, tilt them by TODAY'S δ using the `catalyst_alignment` that run's own
    `sector_snapshot` froze at scoring time (point-in-time — the CA is as-recorded, only the
    forward return is hindsight, which is the one hindsight a backtest is allowed), re-cap at
    `max_position_pct`, and price `Σ (w_tilted − w_recorded) · fwd` over the same horizon the
    live ledger uses. Together the two functions give one continuous series: retro before P8,
    live after.

    HONEST LIMITS, stated rather than hidden: the recorded weights already absorbed that run's
    deadband and vol-tilt, so the retro tilt is applied ON TOP of the finished book (the exact
    chain cannot be replayed without that run's config); the cap and δ are TODAY'S. This is
    EVIDENCE for tuning δ and the CA computation — it never feeds sector_snapshot, never gates,
    and per the user doctrine it is never an argument for disabling CA. Same epistemic status
    as `experiments/backtest_signals.py` (v8): a measured prior, not a live window.

    `persist=True` writes one row per priced run to the lake table `ca_tilt_retro`
    (re-runnable; the partition for a run is overwritten, never duplicated).
    """
    from catalyx.execution import portfolio as _pf
    from catalyx.store import lake
    from catalyx.store import snapshot_repo as sr

    pw = _weights_cfg().portfolio_weighting()
    delta = float(pw.get("ca_tilt_delta", 0.20) if delta is None else delta)
    delta = max(delta, float(pw.get("ca_tilt_delta_min", 0.05)))
    winsor = float(pw.get("ca_tilt_winsor_z", 2.5) if winsor_z is None else winsor_z)
    scale = str(scale or pw.get("ca_tilt_scale", "level")).lower()
    cap_pct = float(_weights_cfg().book_shape()["max_position_pct"])
    fwd_fn = fwd_fn or forward_returns
    today = as_of or date.today().isoformat()
    empty = {"portfolio_id": portfolio_id, "horizon_days": int(horizon_days), "mode": "retro",
             "delta": delta, "scale": scale, "windows": [], "n_windows": 0,
             "effective_windows": 0,
             "mean_edge_pp": None, "total_edge_pp": None, "per_sector": {},
             "per_catalyst": {}, "verdict": "unmeasured"}

    hold = lake.read_table("portfolio_holding", lake_dir=lake_dir)
    snaps = lake.read_table("sector_snapshot", lake_dir=lake_dir)
    if hold.empty or snaps.empty:
        return {**empty, "note": "no recorded model books / snapshots to retro-price"}
    hold = hold[(hold["portfolio_id"] == portfolio_id) & hold["weight_pct"].notna()].copy()
    # pre-P8 books only: a book that carries the live ledger is priced by ca_tilt_edge, and
    # pricing it here too would count the same window twice with two different baselines.
    if "w_pre_ca_pct" in hold.columns:
        hold = hold[hold["w_pre_ca_pct"].isna()]
    if hold.empty:
        return {**empty, "note": "every recorded book already carries the live CA-tilt ledger"}

    by_date: dict[str, str] = {}
    for rid in sorted(str(r) for r in hold["run_id"].unique()):
        if rid.startswith("run_") and len(rid) >= 12:
            by_date[rid[4:12]] = rid                       # later run of a date wins
    windows: list[dict] = []
    per_sector: dict[str, dict] = {}
    for d8 in _review_cycle_dates(sorted(by_date), min_gap_days):
        try:
            start = date(int(d8[:4]), int(d8[4:6]), int(d8[6:8]))
        except ValueError:
            continue
        window_end = start + timedelta(days=int(horizon_days))
        if window_end.isoformat() > today:
            continue                                       # open window — not priced yet
        rid = by_date[d8]
        book = hold[hold["run_id"] == rid]
        book = book[book["weight_pct"] > 0]
        sn = snaps[snaps["run_id"] == rid].set_index("sector_id") \
            if "sector_id" in snaps.columns else None
        if book.empty or sn is None or sn.empty:
            continue

        sids = [str(s) for s in book["sector_id"]]
        w_rec = [float(x) for x in book["weight_pct"]]

        def _sn(sid, col):
            try:
                v = sn.at[sid, col] if col in sn.columns else None
            except KeyError:
                return None
            return None if v is None or v != v else v

        ca_vals = [_sn(s, "catalyst_alignment") for s in sids]
        ca_lgts = [_sn(s, "ca_logit") for s in sids]
        imput = [bool(_sn(s, "ca_imputed") or False) for s in sids]
        # Same scale the live tilt runs on, so retro and live windows are one continuous
        # series; a pre-v11 run carries no logit and falls back to the level, which the
        # per-window `scale` records rather than leaving to be inferred.
        tilted, ca_rows = _pf.ca_tilt(w_rec, ca_vals, imput, delta, winsor,
                                      ca_logits=ca_lgts, scale=scale)
        # Re-cap, gross preserved: the tilt must not manufacture a position today's cap would
        # refuse — but it must not SHRINK a position the recorded book actually carried either
        # (that would charge the counterfactual for a cap the baseline never faced), so the
        # effective ceiling is max(today's cap, the book's own largest line). water_fill
        # allocates a unit pool → scale by the recorded gross.
        gross = sum(w_rec)
        if gross <= 0:
            continue
        cap_eff = max(cap_pct, max(w_rec))
        fr = _pf.water_fill(tilted, max_w=min(1.0, cap_eff / gross))
        w_tilt = [f * gross for f in fr]

        # vehicle buyable TODAY, same remap as compute_run: pre-2026-08-27 books reference US
        # non-UCITS tickers this investor could never have held.
        etfs = {s: sr._primary_etf(s) for s in sids}
        usable = sorted({t for t in etfs.values() if isinstance(t, str) and t.strip()})
        fwd = fwd_fn(usable, start.isoformat(), window_end.isoformat(),
                     allow_fetch=allow_fetch)
        edge, contribs, n_priced = 0.0, [], 0
        for sid, wr, wt, cr in zip(sids, w_rec, w_tilt, ca_rows):
            ret = fwd.get(etfs.get(sid) or "")
            dw = (wt - wr) / 100.0
            if ret is None or abs(dw) < 1e-9:
                continue
            pp = dw * float(ret) * 100.0
            edge += pp
            n_priced += 1
            contribs.append({"sector_id": sid, "ca": cr["ca"], "ca_z": cr["z"],
                             "delta_w_pp": round(dw * 100.0, 2),
                             "fwd_pct": round(float(ret) * 100.0, 2),
                             "edge_pp": round(pp, 3)})
            agg = per_sector.setdefault(sid, {"edge_pp": 0.0, "n_windows": 0})
            agg["edge_pp"] += pp
            agg["n_windows"] += 1
        if not n_priced:
            continue
        scale_used = next((r["scale"] for r in ca_rows
                           if r["x"] is not None and not r["imputed"]), scale)
        windows.append({"run_id": rid, "start": start.isoformat(),
                        "end": window_end.isoformat(), "delta": delta,
                        "scale": scale_used,
                        "edge_pp": round(edge, 3), "n_priced": n_priced,
                        **_gross_decomposition(contribs),
                        "contributions": sorted(contribs, key=lambda c: c["edge_pp"])})

    if not windows:
        return {**empty, "note": f"no recorded pre-P8 book has a complete {horizon_days}d "
                                 f"window yet (try --horizon-days 21 for an early, noisier read)"}
    res = _edge_summary(empty, windows, per_sector, int(horizon_days),
                        label=f"RETRO — today's δ={delta:.2f} on the "
                              f"{(windows[0].get('scale') or scale).upper()} scale, applied to "
                              f"the recorded books, would have")
    if persist:
        import pandas as pd
        rows = [{"portfolio_id": portfolio_id, "run_id": w["run_id"], "start": w["start"],
                 "end": w["end"], "horizon_days": int(horizon_days), "delta": delta,
                 "edge_pp": w["edge_pp"], "n_priced": w["n_priced"], "scale": w["scale"],
                 "contributions": json.dumps(w["contributions"]),
                 "computed_at": datetime.now(timezone.utc).isoformat()} for w in windows]
        for w in rows:
            lake.append_partition("ca_tilt_retro", pd.DataFrame([w]),
                                  {"run_id": w["run_id"]}, overwrite=True, lake_dir=lake_dir)
        res["persisted"] = len(rows)
    return res


def _weights_cfg():
    from catalyx.config import weights as weights_cfg
    return weights_cfg


def compute_run(run_id: str, run_start: str, horizon_days: int | None = DEFAULT_HORIZON_DAYS,
                as_of: str | None = None, allow_fetch: bool = True) -> dict:
    """Calibrate ONE run: rank IC per dimension over its forward window."""
    from catalyx.store import lake
    from catalyx.store import snapshot_repo as sr

    today = as_of or date.today().isoformat()
    if horizon_days:
        window_end = (date.fromisoformat(run_start) + timedelta(days=horizon_days)).isoformat()
        end = min(window_end, today)
        complete = window_end <= today
    else:
        end, complete = today, True

    snaps = lake.read_table("sector_snapshot")
    if snaps.empty:
        return {"run_id": run_id, "error": "no sector_snapshot table"}
    sn = snaps[snaps["run_id"] == run_id].copy()
    if sn.empty:
        return {"run_id": run_id, "error": "no snapshots for run"}

    investable = _investable_sectors()
    sn = sn[sn["sector_id"].isin(investable)].copy()
    # Remap to the vehicle buyable TODAY, not the ticker stored back then (pre-2026-08-27 runs
    # reference US non-UCITS ETFs this investor could never have held).
    sn["etf"] = sn["sector_id"].map(sr._primary_etf)
    sn = sn[sn["etf"].apply(lambda t: bool(isinstance(t, str) and t.strip() and " " not in t))]
    if sn.empty:
        return {"run_id": run_id, "error": "no investable sector had a buyable vehicle"}

    fwd = forward_returns(sorted(sn["etf"].unique()), run_start, end, allow_fetch=allow_fetch)
    sn["fwd"] = sn["etf"].map(fwd)
    sn = sn.dropna(subset=["fwd"])
    n = len(sn)
    if n < MIN_SECTORS:
        return {"run_id": run_id, "start": run_start, "end": end, "n_sectors": n,
                "error": f"only {n} sectors had usable forward returns (need {MIN_SECTORS})"}

    se = ic_standard_error(n)
    dims = {}
    for dim, inverted in DIMENSIONS.items():
        if dim not in sn.columns:
            continue
        ic = rank_ic(sn[dim].tolist(), sn["fwd"].tolist())
        dims[dim] = {
            "rank_ic": ic,
            "as_used_ic": contribution_ic(ic, inverted),
            "inverted_in_composite": inverted,
            "candidate": dim in CANDIDATE_DIMENSIONS,
            "verdict": ic_verdict(ic, se),
        }

    spread = top_k_spread(sn["composite"].tolist(), sn["fwd"].tolist()) \
        if "composite" in sn.columns else {}
    buckets = bucket_returns(sn["composite"].tolist(), sn["fwd"].tolist()) \
        if "composite" in sn.columns else {}

    return {
        "run_id": run_id, "start": run_start, "end": end,
        "horizon_days": horizon_days, "window_complete": complete,
        "n_sectors": n, "se": se, "dimensions": dims, "spread": spread,
        "buckets": buckets,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


def compute_all(horizon_days: int | None = DEFAULT_HORIZON_DAYS, as_of: str | None = None,
                min_days: int = 21, allow_fetch: bool = True) -> list[dict]:
    """Calibrate every recorded run that has at least `min_days` of forward history."""
    from catalyx.store import lake

    runs = lake.read_table("score_run")
    if runs.empty:
        return []
    today = date.fromisoformat(as_of or date.today().isoformat())
    out = []
    for _, run in runs.sort_values("run_id").iterrows():
        start = str(run.get("run_at"))[:10]
        try:
            age = (today - date.fromisoformat(start)).days
        except ValueError:
            continue
        if age < min_days:                    # too fresh to have said anything yet
            continue
        out.append(compute_run(str(run["run_id"]), start, horizon_days,
                               as_of=as_of, allow_fetch=allow_fetch))
    return out


def aggregate(results: list[dict]) -> dict:
    """Mean IC per dimension across runs, WITH the effective-sample caveat attached.

    Runs a few weeks apart over the same regime are nearly the same observation, so the mean of
    five is not five samples. `effective_windows` counts non-overlapping horizons: the honest
    denominator for anyone tempted to act on the average.
    """
    ok = [r for r in results if not r.get("error")]
    if not ok:
        return {"runs": 0, "note": "no run had usable forward returns"}

    dims: dict[str, list[float]] = {}
    for r in ok:
        for dim, d in r["dimensions"].items():
            if d.get("as_used_ic") is not None:
                dims.setdefault(dim, []).append(d["as_used_ic"])

    starts = sorted(date.fromisoformat(r["start"]) for r in ok)
    horizon = ok[0].get("horizon_days") or 63
    effective, last = 0, None
    for s in starts:
        if last is None or (s - last).days >= horizon:
            effective += 1
            last = s

    means = {dim: round(sum(v) / len(v), 3) for dim, v in dims.items()}
    spreads = [r["spread"]["spread"] for r in ok
               if r.get("spread", {}).get("spread") is not None]
    return {
        "runs": len(ok),
        "effective_windows": effective,
        "mean_as_used_ic": means,
        "mean_top_k_spread_pct": round(sum(spreads) / len(spreads), 2) if spreads else None,
        "note": (f"{len(ok)} runs but only ~{effective} non-overlapping {horizon}d window(s) — "
                 f"treat the mean as ~{effective} observation(s), not {len(ok)}. Not enough to "
                 f"move a weight in scoring_weights.yaml."),
    }


def persist(results: list[dict], lake_dir: Path | None = None) -> int:
    """One row per (run, dimension) into the lake so calibration ACCUMULATES across reviews."""
    import pandas as pd

    from catalyx.store import lake

    written = 0
    for r in results:
        if r.get("error"):
            continue
        rows = [{
            "run_id": r["run_id"], "start": r["start"], "end": r["end"],
            "horizon_days": r.get("horizon_days"), "window_complete": r.get("window_complete"),
            "n_sectors": r["n_sectors"], "se": r["se"], "dimension": dim,
            "rank_ic": d["rank_ic"], "as_used_ic": d["as_used_ic"], "verdict": d["verdict"],
            "candidate": bool(d.get("candidate", False)),
            "top_k_spread_pct": r.get("spread", {}).get("spread"),
            "computed_at": r["computed_at"],
        } for dim, d in r["dimensions"].items()]
        if rows:
            lake.append_partition(_TABLE, pd.DataFrame(rows), {"run_id": r["run_id"]},
                                  overwrite=True, lake_dir=lake_dir)
            written += 1
        # Bucket returns live in their own table: a different grain (one row per rank bucket,
        # not per dimension) and a different consumer (rebalance's € edge, not the IC report).
        brows = [{"run_id": r["run_id"], "start": r["start"], "end": r["end"],
                  "horizon_days": r.get("horizon_days"),
                  "window_complete": r.get("window_complete"), "n_sectors": r["n_sectors"],
                  "bucket": name, "mean_fwd_pct": val, "computed_at": r["computed_at"]}
                 for name, val in (r.get("buckets") or {}).items()]
        if brows:
            lake.append_partition(_BUCKET_TABLE, pd.DataFrame(brows), {"run_id": r["run_id"]},
                                  overwrite=True, lake_dir=lake_dir)
    return written


# ── CLI ──────────────────────────────────────────────────────────────────────

def _render(results: list[dict], agg: dict) -> str:
    out = ["CATALYX — scoring calibration (does the score predict forward returns?)", ""]
    usable = [r for r in results if not r.get("error")]
    for r in results:
        if r.get("error"):
            out.append(f"  {r['run_id']}: {r['error']}")
    if not usable:
        return "\n".join(out + ["", "  nothing calibratable yet"])

    all_dims = list(usable[0]["dimensions"].keys())
    official = [d for d in all_dims if d not in CANDIDATE_DIMENSIONS]
    candidates = [d for d in all_dims if d in CANDIDATE_DIMENSIONS]

    def _table(dims: list[str]) -> None:
        hdr = f"  {'run':<20}{'window':<26}{'n':>4}{'se':>7}"
        for d in dims:
            hdr += f"{d[:11]:>13}"
        hdr += f"{'top5 spr':>10}"
        out.append(hdr)
        for r in usable:
            partial = "" if r.get("window_complete") else " (partial)"
            line = (f"  {r['run_id'][4:]:<20}{r['start']}→{r['end']}{partial:<8}"
                    f"{r['n_sectors']:>4}{r['se']:>7.2f}")
            for d in dims:
                v = r["dimensions"].get(d, {}).get("as_used_ic")
                line += f"{('—' if v is None else f'{v:+.2f}'):>13}"
            sp = r.get("spread", {}).get("spread")
            line += f"{('—' if sp is None else f'{sp:+.1f}%'):>10}"
            out.append(line)

    _table(official)
    if candidates:
        out += ["", "  CANDIDATE columns (weight 0 in the composite — evidence, not signal):"]
        _table(candidates)

    out += ["", "  IC shown is AS USED BY THE COMPOSITE (crowding is inverted there, so its raw",
            "  correlation is negated). Positive = the score ordered sectors correctly.", ""]
    out.append(f"  mean: " + "  ".join(f"{k}={v:+.2f}" for k, v in agg["mean_as_used_ic"].items()))
    if agg.get("mean_top_k_spread_pct") is not None:
        out.append(f"  mean top-{TOP_K} spread: {agg['mean_top_k_spread_pct']:+.2f}%")
    out += ["", f"  ⚠ {agg['note']}"]

    se = usable[0]["se"]
    noisy = [d for d in all_dims
             if all(r["dimensions"].get(d, {}).get("verdict") in ("noise", "insufficient")
                    for r in usable)]
    if noisy:
        out.append(f"  ⚠ indistinguishable from noise in EVERY window (|IC| < 2·se ≈ {2*se:.2f}): "
                   f"{', '.join(noisy)}")
    return "\n".join(out)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(
        description="CATALYX scoring calibration — measured rank IC per dimension, with its SE")
    p.add_argument("--run-id", default=None, help="Calibrate one run (default: all with history)")
    p.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS,
                   help=f"Equal-length forward window per run (default {DEFAULT_HORIZON_DAYS}); "
                        f"0 = measure every run to today (windows then nest — read with care)")
    p.add_argument("--as-of", default=None)
    p.add_argument("--min-days", type=int, default=21,
                   help="Skip runs younger than this — they have not had time to be right or wrong")
    p.add_argument("--offline", action="store_true", help="Cache-only, never fetch")
    p.add_argument("--write", action="store_true", help="Persist to the lake `calibration` table")
    p.add_argument("--json", action="store_true")
    p.add_argument("--ca-tilt", action="store_true",
                   help="Price the CA conviction tilt (v10 P8): Σ(w_post − w_pre)·fwd per "
                        "complete window, decomposed per sector and per driving catalyst")
    p.add_argument("--ca-tilt-retro", action="store_true",
                   help="Apply TODAY'S δ to the model books recorded BEFORE P8 (their CA as "
                        "frozen at scoring time) and price what the tilt would have earned. "
                        "--write persists per-run rows to the lake `ca_tilt_retro` table.")
    args = p.parse_args()

    if args.ca_tilt or args.ca_tilt_retro:
        if args.ca_tilt_retro:
            res = ca_tilt_edge_retro(horizon_days=args.horizon_days or DEFAULT_HORIZON_DAYS,
                                     as_of=args.as_of, allow_fetch=not args.offline,
                                     persist=args.write)
            title = ("CATALYX — CA conviction tilt, RETRO: what today's δ would have earned "
                     "on the recorded books")
        else:
            res = ca_tilt_edge(horizon_days=args.horizon_days or DEFAULT_HORIZON_DAYS,
                               as_of=args.as_of, allow_fetch=not args.offline)
            title = "CATALYX — CA conviction tilt: what the declared prior earned"
        if args.json:
            print(json.dumps(res, indent=2, default=str))
            return
        print(title)
        print(f"  {res['note'] if res.get('note') else res['verdict']}")
        for w in res.get("windows", []):
            print(f"  {w['start']} → {w['end']}  δ={w['delta']}  edge {w['edge_pp']:+.2f}pp "
                  f"({w['run_id']})")
        if res.get("per_catalyst"):
            print("  per driver (overlapping credit, worst first):")
            for cid, pp in res["per_catalyst"].items():
                print(f"    {pp:+7.2f}pp  {cid}")
        if res.get("persisted"):
            print(f"  persisted {res['persisted']} row(s) → lake `ca_tilt_retro`")
        return

    horizon = args.horizon_days or None
    if args.run_id:
        from catalyx.store import lake
        runs = lake.read_table("score_run")
        match = runs[runs["run_id"] == args.run_id] if not runs.empty else runs
        if match.empty:
            print(f"  no such run: {args.run_id}", file=sys.stderr)
            sys.exit(1)
        results = [compute_run(args.run_id, str(match.iloc[0]["run_at"])[:10], horizon,
                               as_of=args.as_of, allow_fetch=not args.offline)]
    else:
        results = compute_all(horizon, as_of=args.as_of, min_days=args.min_days,
                              allow_fetch=not args.offline)

    agg = aggregate(results)
    if args.write:
        agg["persisted_runs"] = persist(results)
    if args.json:
        print(json.dumps({"runs": results, "aggregate": agg}, indent=2,
                         ensure_ascii=False, default=str))
        return
    print(_render(results, agg))
    if args.write:
        print(f"\n  → lake table `calibration` ({agg.get('persisted_runs', 0)} run partitions)")


if __name__ == "__main__":
    main()
