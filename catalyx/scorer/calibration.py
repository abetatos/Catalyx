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
    """{ticker: simple return over [start, end]} from the shared price cache.

    Reading from the cache (not a fresh fetch) is what makes a calibration re-run reproducible:
    this number is the honest answer to "did the score predict anything" and must not drift
    because it was recomputed on a different afternoon.
    """
    from catalyx.data import prices

    px = prices.read(tickers, start, end, allow_fetch=allow_fetch)
    out = {}
    if px is None or px.empty:
        return out
    for t in px.columns:
        s = px[t].dropna()
        if len(s) >= 2 and float(s.iloc[0]):
            out[t] = float(s.iloc[-1] / s.iloc[0] - 1.0)
    return out


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

    hdr = f"  {'run':<20}{'window':<26}{'n':>4}{'se':>7}"
    dims = list(usable[0]["dimensions"].keys())
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

    out += ["", "  IC shown is AS USED BY THE COMPOSITE (crowding is inverted there, so its raw",
            "  correlation is negated). Positive = the score ordered sectors correctly.", ""]
    out.append(f"  mean: " + "  ".join(f"{k}={v:+.2f}" for k, v in agg["mean_as_used_ic"].items()))
    if agg.get("mean_top_k_spread_pct") is not None:
        out.append(f"  mean top-{TOP_K} spread: {agg['mean_top_k_spread_pct']:+.2f}%")
    out += ["", f"  ⚠ {agg['note']}"]

    se = usable[0]["se"]
    noisy = [d for d in dims
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
    args = p.parse_args()

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
