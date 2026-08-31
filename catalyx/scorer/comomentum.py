"""Comomentum — measured crowding from abnormal co-movement (v7 N1, K4 direction).

Lou–Polk (2013): capital crowding into a theme shows up as excess correlation of the theme
members' MARKET-RESIDUAL returns, before it shows up anywhere else. This is the measured
counterpart to the `narrative_maturity` label: per sector, the mean pairwise correlation of
weekly SPY-residual returns among the vehicles that share a structural catalyst with it.

CANDIDATE COLUMN (weight 0): recorded per run as `crowding_comomentum`, measured by
`calibration` like every candidate. High = the theme's money moves as one block = crowded.
A sector with no catalyst siblings has no theme to co-move with → None, never 0.

Weekly returns for the same Epps reason as `covariance` (daily UCITS closes are asynchronous).

CLI:
    uv run python -m catalyx.scorer.comomentum [--weeks 52] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]
_STUDY_DIR = _REPO_ROOT / "data" / "sector_studies"
_STRUCTURAL_DIR = _REPO_ROOT / "catalyx" / "config" / "structural_catalysts"
_BENCHMARK = "SPY"
_WEEKS_DEFAULT = 52
_MIN_OVERLAP_WEEKS = 26


def sector_catalysts() -> dict[str, set[str]]:
    """sector_id → its active STRUCTURAL catalyst ids, from the sector studies."""
    out: dict[str, set[str]] = {}
    for f in sorted(_STUDY_DIR.glob("study_*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        ids = {
            c for c in (doc.get("active_catalyst_ids") or [])
            if (_STRUCTURAL_DIR / f"{c.removeprefix('struct_')}.yaml").exists()
            or (_STRUCTURAL_DIR / f"{c}.yaml").exists()
        }
        if ids:
            out[f.stem.removeprefix("study_")] = ids
    return out


def siblings_map(cats: dict[str, set[str]]) -> dict[str, list[str]]:
    """sector → other sectors sharing at least one structural catalyst."""
    return {
        sid: sorted(o for o, ocats in cats.items() if o != sid and (ids & ocats))
        for sid, ids in cats.items()
    }


def residual_returns(weekly, benchmark: str = _BENCHMARK):
    """OLS-residualize every column against the benchmark column. Drops the benchmark."""
    cols = [c for c in weekly.columns if c != benchmark]
    if benchmark not in weekly.columns:
        return weekly[cols]
    out = {}
    b = weekly[benchmark]
    for c in cols:
        j = weekly[[c]].join(b.rename("_b")).dropna()
        if len(j) < _MIN_OVERLAP_WEEKS:
            continue
        var = j["_b"].var()
        beta = j[c].cov(j["_b"]) / var if var and var > 0 else 0.0
        alpha = j[c].mean() - beta * j["_b"].mean()
        out[c] = j[c] - (alpha + beta * j["_b"])
    import pandas as pd

    return pd.DataFrame(out)


def compute(weeks: int = _WEEKS_DEFAULT, as_of: str | None = None, price_fn=None) -> dict:
    """crowding_comomentum per sector. Best-effort: unmeasurable → None, with the reason."""
    from catalyx.scorer.covariance import to_weekly
    from catalyx.store.snapshot_repo import primary_etf

    cats = sector_catalysts()
    sibs = siblings_map(cats)
    vehicles = {sid: primary_etf(sid) for sid in cats}
    tickers = sorted({t for t in vehicles.values() if t}) + [_BENCHMARK]

    end = as_of or date.today().isoformat()
    start = (date.fromisoformat(end) - timedelta(weeks=weeks + 2)).isoformat()
    if price_fn is None:
        from catalyx.data import prices
        price_fn = prices.read
    try:
        px = price_fn(tickers, start, end)
    except Exception as e:  # noqa: BLE001
        return {"error": f"price fetch failed: {e}", "sectors": {}}
    weekly = to_weekly(px)
    resid = residual_returns(weekly)

    sectors: dict[str, dict] = {}
    for sid, ids in cats.items():
        t = vehicles.get(sid)
        sib_ts = [(o, vehicles.get(o)) for o in sibs.get(sid, [])]
        sib_ts = [(o, ot) for o, ot in sib_ts if ot and ot in resid.columns and ot != t]
        if not t or t not in resid.columns or not sib_ts:
            sectors[sid] = {"crowding_comomentum": None, "n_siblings": len(sib_ts),
                            "reason": "no vehicle series" if not t or t not in resid.columns
                            else "no catalyst siblings"}
            continue
        rhos = []
        used = []
        for o, ot in sib_ts:
            j = resid[[t, ot]].dropna()
            if len(j) < _MIN_OVERLAP_WEEKS:
                continue
            r = j[t].corr(j[ot])
            if r == r:
                rhos.append(float(r))
                used.append(o)
        sectors[sid] = {
            "crowding_comomentum": round(sum(rhos) / len(rhos), 4) if rhos else None,
            "n_siblings": len(used),
            "siblings": used,
        }
    return {"as_of": end, "weeks": weeks, "benchmark": _BENCHMARK, "sectors": sectors}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="CATALYX comomentum — measured crowding per sector")
    p.add_argument("--weeks", type=int, default=_WEEKS_DEFAULT)
    p.add_argument("--as-of", default=None)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    r = compute(weeks=args.weeks, as_of=args.as_of)
    if args.json:
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return
    if r.get("error"):
        print(f"ERROR: {r['error']}", file=sys.stderr)
        sys.exit(1)
    print(f"CATALYX — comomentum (residual co-movement vs {r['benchmark']}, {r['weeks']}w)\n")
    rows = sorted(r["sectors"].items(),
                  key=lambda kv: -(kv[1].get("crowding_comomentum") if kv[1].get("crowding_comomentum") is not None else -9))
    for sid, s in rows:
        v = s.get("crowding_comomentum")
        vs = f"{v:+.3f}" if v is not None else f"  n/a ({s.get('reason', 'insufficient overlap')})"
        print(f"  {sid:<40} {vs}  siblings={s.get('n_siblings', 0)}")


if __name__ == "__main__":
    main()
