"""Composite sector scorer — orchestrates catalyst_scorer + momentum_engine + flow_data.

Formula source: scoring_weights.yaml §composite_weights

    composite = catalyst_alignment × 0.35
              + momentum          × 0.29
              + flow_confirmation × 0.24
              + (100 - crowding_risk) × 0.12

Result capped at [0, 100].

v1.6 (2026-06-06): `valuation_relative` was REMOVED from the composite. It had always
been a constant-50 placeholder (no valuation_engine), so it never changed the ranking —
it only diluted the real dimensions. A backtest showed no price-derived metric earns that
15% (momentum acceleration has negative monthly IC), so the weight was redistributed
proportionally across the survivors. The schema keeps the field deprecated for read-back.

Phase 0.5 defaults (used when auto-derivation is unavailable):
  - flow_confirmation: 50 (neutral). Auto-derived from flow_data.py when a flow snapshot exists.
  - crowding_risk: 35. Override via --crowd or from sector study narrative_maturity.
  - catalyst_alignment: computed by catalyst_scorer if not supplied.
  - momentum: computed by momentum_engine if not supplied.

Usage (callable from skills):
    # Full auto-compute (loads sector study + latest momentum + flow snapshots):
    uv run python -m catalyx.scorer.sector_scorer copper_miners

    # All sectors:
    uv run python -m catalyx.scorer.sector_scorer --all

    # Manual override for specific dimensions:
    uv run python -m catalyx.scorer.sector_scorer copper_miners --flow 50 --crowd 35

    # Use pre-computed scores (skip all derivation):
    uv run python -m catalyx.scorer.sector_scorer copper_miners --ca 95 --mom 77.7 --flow 50 --crowd 35

    # JSON output:
    uv run python -m catalyx.scorer.sector_scorer copper_miners --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from catalyx.config import weights
from catalyx.scorer.catalyst_scorer import compute_catalyst_alignment
from catalyx.scorer.momentum_engine import compute_momentum_scores

_REPO_ROOT = Path(__file__).parents[2]
_STUDY_DIR = _REPO_ROOT / "data" / "sector_studies"
_TAXONOMY = _REPO_ROOT / "catalyx" / "config" / "sector_taxonomy.yaml"

# Composite weights — single source of truth: scoring_weights.yaml §composite_weights
_CW = weights.composite_weights()
_W_CATALYST = _CW["catalyst_alignment"]
_W_MOMENTUM = _CW["momentum"]
_W_FLOW = _CW["flow_confirmation"]
_W_CROWDING = _CW["crowding_risk"]  # applied as (100 - crowding_risk) × weight

# Phase 0.5 defaults for dimensions without automated data
_DEFAULT_FLOW = 50.0
_DEFAULT_CROWDING = 35.0


# ── Formula ────────────────────────────────────────────────────────────────────

def compute_composite(
    catalyst_alignment: float,
    momentum: float,
    flow_confirmation: float,
    crowding_risk: float,
) -> dict:
    """Apply composite formula. All inputs in [0, 100].

    Returns composite score + weighted contribution breakdown.

    v1.6: valuation_relative dropped (was a constant-50 placeholder — see module docstring).
    """
    crowding_inverted = 100.0 - crowding_risk

    contrib_catalyst = catalyst_alignment * _W_CATALYST
    contrib_momentum = momentum * _W_MOMENTUM
    contrib_flow = flow_confirmation * _W_FLOW
    contrib_crowding = crowding_inverted * _W_CROWDING

    composite = contrib_catalyst + contrib_momentum + contrib_flow + contrib_crowding
    composite = round(min(100.0, max(0.0, composite)), 1)

    return {
        "composite": composite,
        "score_breakdown": {
            "catalyst_alignment": round(catalyst_alignment, 1),
            "momentum": round(momentum, 1),
            "flow_confirmation": round(flow_confirmation, 1),
            "crowding_risk": round(crowding_risk, 1),
        },
        "weighted_contributions": {
            "catalyst_alignment": round(contrib_catalyst, 2),
            "momentum": round(contrib_momentum, 2),
            "flow_confirmation": round(contrib_flow, 2),
            "crowding_penalty": round(contrib_crowding, 2),
        },
    }


# ── Commensurability (v6 H1) ──────────────────────────────────────────────────
#
# compute_composite() sums four scales that are not commensurable: momentum is a uniform
# cross-sectional percentile (σ≈29), catalyst_alignment a level living in a narrow band
# (σ≈10-15), flow falls to a constant 50 without a snapshot, crowding is an enum of five
# values. In a weighted sum the effective weight of a dimension is ≈ w·σ_cross, so
# momentum has always outweighed its nominal 0.29 and a degenerate dimension has weighed
# ZERO whatever the YAML said — the mechanism behind the valuation_relative bug (v1.6),
# which extracted one instance and left the mechanism running.
#
# So the weights are applied to z-scores taken WITHIN the run. Raw values stay in
# score_breakdown; the standardization is internal to the combination.

_DIMENSIONS = ("catalyst_alignment", "momentum", "flow_confirmation", "crowding_risk")

# Flow data_quality values that are NOT a flow measurement. `volume_proxy` is CMF — a price+volume
# oscillator, which `flow_data` itself labels "⚠ not true flow" — and `estimated` is the neutral
# placeholder. Measured on 2026-08-31, when repairing the stockanalysis source moved 12 sectors off
# CMF onto real share counts: the CMF stand-in was off by a MEAN of 13.1 points on the 0-100 scale,
# max 25.6, and the two largest errors landed on the two names the rebalance table acted on that
# day (gold_miners inflated 78.2 vs 52.6 real, cybersecurity_commercial deflated 28.8 vs 54.2).
# That is not a proxy, so it is treated the way v6 H2 already treats a missing study: imputed to
# the prior (z=0), excluded from the dimension's moments, and FLAGGED AS A COLUMN, never a gate.
_FLOW_NOT_MEASURED = ("volume_proxy", "estimated")

# Which row flag marks a dimension as imputed rather than observed, per dimension.
_IMPUTED_FLAG = {"catalyst_alignment": "ca_imputed", "flow_confirmation": "flow_imputed"}


def _aligned_values(result: dict) -> dict:
    """The four dimensions, all signed so higher is better (crowding inverted)."""
    sb = result["score_breakdown"]
    return {
        "catalyst_alignment": float(sb["catalyst_alignment"]),
        "momentum": float(sb["momentum"]),
        "flow_confirmation": float(sb["flow_confirmation"]),
        "crowding_risk": 100.0 - float(sb["crowding_risk"]),
    }


def _rank_avg(vals: list[float]) -> list[float]:
    """Ranks with ties averaged (scipy-free)."""
    n = len(vals)
    order = sorted(range(n), key=lambda i: vals[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    rx, ry = _rank_avg(xs), _rank_avg(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    return sxy / (sxx * syy) ** 0.5


def rank_key(rows) -> "callable":
    """The sort key a run's sectors are RANKED on — `composite_z` when the run carries it.

    `composite` is rounded to one decimal for display, so ranking on it turns any pair closer
    than 0.05 into a tie broken by list order rather than by score. That is not hypothetical:
    on the 2026-08-31 universe `space_defense_satellite` (49.4, z −0.041) published a better
    rank than `nuclear_energy` (49.4, z −0.038) purely because the taxonomy lists it first.
    v6/H1 already named `composite_z` (3 decimals, the raw Σ wᵢ·zᵢ) the comparable unit; the
    rank is simply still reading the display number.

    A pre-v6 run carries no `composite_z` and falls back to the rounded level — the only unit
    it has. A MIXED collection falls back too: comparing a z against an absolute level ranks
    worse than a tie does.
    """
    def _z(r):
        v = r.get("composite_z")
        return None if v is None or v != v else float(v)   # v != v catches NaN

    rows = list(rows)
    if rows and all(_z(r) is not None for r in rows):
        return lambda r: -float(r["composite_z"])
    return lambda r: -float(r.get("composite") or 0.0)


def commensurate(results: list[dict]) -> dict:
    """Restate every composite in the list on the standardized scale, in place.

    Adds `composite_z` (the raw Σ wᵢ·zᵢ, the unit selection thresholds are expressed in),
    `dimension_z` and `composite_absolute` (the pre-v6 level, kept for read-back), and
    returns the run-level diagnostic — including the dead-dimension lint.
    """
    cfg = weights.composite_scale()
    z_scale = float(cfg.get("z_scale", 15.0))
    winsor = float(cfg.get("winsor_z", 3.0))
    dead_sigma = float(cfg.get("dead_dimension_sigma", 2.0))
    n = len(results)

    if n < int(cfg.get("min_universe", 5)):
        for r in results:
            r["composite_absolute"] = r["composite"]
            r["composite_z"] = None
            r["dimension_z"] = None
        return {"applied": False, "n": n,
                "reason": f"universe of {n} is below min_universe — nothing to standardize against",
                "dimensions": [], "dead_dimensions": []}

    w = weights.composite_weights()
    # v6 H2: an imputed value is not an observation — it must not drag the moments it is then
    # measured against. Excluding it is what makes z=0 mean "the median of what WAS measured".
    # Generalised from catalyst_alignment to any dimension declaring an imputed flag, because
    # flow_confirmation has exactly the same problem whenever its source degrades to CMF.
    stats = {}
    for d in _DIMENSIONS:
        flag = _IMPUTED_FLAG.get(d)
        pool = ([r for r in results if not r.get(flag)] or results) if flag else results
        vals = [_aligned_values(r)[d] for r in pool]
        m = len(vals)
        mu = sum(vals) / m
        sd = (sum((v - mu) ** 2 for v in vals) / (m - 1)) ** 0.5 if m > 1 else 0.0
        stats[d] = (mu, sd)

    for r in results:
        vals = _aligned_values(r)
        zs = {}
        for d in _DIMENSIONS:
            mu, sd = stats[d]
            flag = _IMPUTED_FLAG.get(d)
            if flag and r.get(flag):
                zs[d] = 0.0
                continue
            z = 0.0 if sd <= 0 else (vals[d] - mu) / sd
            zs[d] = round(max(-winsor, min(winsor, z)), 3)
        s = sum(w[d] * zs[d] for d in _DIMENSIONS)
        r["composite_absolute"] = r["composite"]
        # 6dp, not 3: `composite_z` is the unit the RANK is computed on, so rounding it is the
        # same defect the rank had against the 1dp display composite, one decimal deeper —
        # water_infrastructure and semiconductors_design tied at +0.457 on 2026-08-31 and were
        # separated by list order while their composites differed. Ties must be real ties.
        r["composite_z"] = round(s, 6)
        r["composite"] = round(min(100.0, max(0.0, 50.0 + z_scale * s)), 1)
        r["dimension_z"] = zs

    # v7 M5 — flow_resid: the z-flow NOT explained by z-momentum (OLS within the run).
    # Only over rows where flow was actually measured; imputed rows keep None.
    measured = [r for r in results if not r.get("flow_imputed")]
    if len(measured) >= 3:
        zm = [r["dimension_z"]["momentum"] for r in measured]
        zf = [r["dimension_z"]["flow_confirmation"] for r in measured]
        mzm, mzf = sum(zm) / len(zm), sum(zf) / len(zf)
        var = sum((a - mzm) ** 2 for a in zm)
        if var > 0:
            b = sum((a - mzm) * (c - mzf) for a, c in zip(zm, zf)) / var
            a0 = mzf - b * mzm
            for r in measured:
                r["flow_resid"] = round(
                    r["dimension_z"]["flow_confirmation"] - (a0 + b * r["dimension_z"]["momentum"]), 3)

    # v7 M1 — cross-dimension rank correlation + effective dimension count. Imputed rows are
    # excluded per pair, same philosophy as the moments.
    corr_pairs = []
    corr_lint = []
    dim_list = list(_DIMENSIONS)
    cmat = [[1.0] * len(dim_list) for _ in dim_list]
    for i in range(len(dim_list)):
        for j in range(i + 1, len(dim_list)):
            a, b_ = dim_list[i], dim_list[j]
            pool = [r for r in results
                    if not (_IMPUTED_FLAG.get(a) and r.get(_IMPUTED_FLAG[a]))
                    and not (_IMPUTED_FLAG.get(b_) and r.get(_IMPUTED_FLAG[b_]))]
            rho = _spearman([_aligned_values(r)[a] for r in pool],
                            [_aligned_values(r)[b_] for r in pool])
            corr_pairs.append({"a": a, "b": b_,
                               "rho": round(rho, 3) if rho is not None else None,
                               "n": len(pool)})
            cmat[i][j] = cmat[j][i] = rho if rho is not None else 0.0
            if rho is not None and abs(rho) >= 0.6:
                corr_lint.append(f"correlated dimensions: {a} ~ {b_} ρ={rho:+.2f} (n={len(pool)}) "
                                 f"— their weights add de facto")
    n_eff = None
    try:
        import numpy as np
        ev = np.linalg.eigvalsh(np.array(cmat))
        ev = ev[ev > 1e-12]
        p = ev / ev.sum()
        n_eff = round(float(np.exp(-(p * np.log(p)).sum())), 2)
    except Exception:
        pass

    # Nominal vs effective weight under the OLD absolute formula — the evidence for why
    # this step exists. After standardization the effective weight IS the nominal one.
    mass = sum(w[d] * stats[d][1] for d in _DIMENSIONS)
    dims = [{
        "dimension": d,
        "sigma_cross": round(stats[d][1], 2),
        "mean": round(stats[d][0], 1),
        "nominal_weight": round(w[d], 3),
        "effective_weight_before": round(w[d] * stats[d][1] / mass, 3) if mass > 0 else 0.0,
    } for d in _DIMENSIONS]
    dead = [d["dimension"] for d in dims if d["sigma_cross"] < dead_sigma]

    return {
        "applied": True, "n": n, "z_scale": z_scale, "winsor_z": winsor,
        "dead_dimension_sigma": dead_sigma,
        "ca_imputed_n": sum(1 for r in results if r.get("ca_imputed")),
        "flow_imputed_n": sum(1 for r in results if r.get("flow_imputed")),
        "dimensions": sorted(dims, key=lambda x: -x["effective_weight_before"]),
        "dead_dimensions": dead,
        "dimension_correlation": corr_pairs,
        "n_eff_dimensions": n_eff,
        "correlation_lint": corr_lint,
        "lint": [f"dead dimension: {d} (σ_cross={dict((x['dimension'], x['sigma_cross']) for x in dims)[d]}) "
                 f"— it is not ranking anything, whatever its {w[d]:.2f} weight says"
                 for d in dead],
    }


# ── Orchestrator ───────────────────────────────────────────────────────────────

def _load_flow_snapshot() -> dict | None:
    """Load the most recent flow snapshot, if it exists."""
    snapshots_dir = _REPO_ROOT / "data" / "snapshots"
    candidates = sorted(snapshots_dir.glob("flow_snapshot_*.json"), reverse=True)
    if not candidates:
        return None
    try:
        return json.loads(candidates[0].read_text(encoding="utf-8"))
    except Exception:
        return None


def score_sector(
    sector_id: str,
    catalyst_alignment: float | None = None,
    momentum: float | None = None,
    flow_confirmation: float | None = None,
    crowding_risk: float = _DEFAULT_CROWDING,
    momentum_snapshot_path: Path | None = None,
) -> dict:
    """Compute full sector score. Auto-derives catalyst_alignment, momentum, and flow if not supplied.

    Args:
        sector_id: Sector ID (must have a sector study in data/sector_studies/).
        catalyst_alignment: Pre-computed value or None to derive from catalyst_scorer.
        momentum: Pre-computed value or None to derive from momentum_engine.
        flow_confirmation: Manual input [0, 100] or None to auto-load from flow snapshot.
                           Falls back to default (50) if no flow snapshot exists.
        crowding_risk: Manual input [0, 100]. Higher = more crowded = penalty.
        momentum_snapshot_path: Optional explicit path to momentum snapshot.
    """
    errors: list[str] = []
    catalyst_detail: dict | None = None
    momentum_detail: dict | None = None
    flow_detail: dict | None = None
    flow_imputed = False
    ca_imputed = False

    # Derive catalyst_alignment
    if catalyst_alignment is None:
        cat_result = compute_catalyst_alignment(sector_id)
        if "error" in cat_result and "breakdown" not in cat_result:
            errors.append(f"catalyst_scorer: {cat_result['error']}")
            catalyst_alignment = 0.0
            # v6 H2: no study = NOT MEASURED, and a missing value is imputed to the prior,
            # never to the worst case. commensurate() gives it z=0 (the universe median) and
            # excludes it from the CA moments. A study that found no catalysts is different:
            # that zero was measured, and it stands.
            ca_imputed = cat_result.get("reason") == "no_study"
        else:
            catalyst_alignment = cat_result.get("catalyst_alignment", 0.0)
            catalyst_detail = cat_result

    # Derive momentum
    if momentum is None:
        mom_result = compute_momentum_scores(snapshot_path=momentum_snapshot_path)
        if "error" in mom_result:
            errors.append(f"momentum_engine: {mom_result['error']}")
            momentum = 50.0  # neutral fallback
        else:
            sector_scores = mom_result.get("scores", {})
            if sector_id in sector_scores:
                momentum = sector_scores[sector_id]["momentum_score"]
                momentum_detail = sector_scores[sector_id]
            else:
                errors.append(f"momentum_engine: no data for sector '{sector_id}' in snapshot")
                momentum = 50.0

    # Derive flow_confirmation and inst_sponsorship_score from flow snapshot if not manually supplied
    inst_sponsorship_score: float | None = None
    if flow_confirmation is None:
        flow_snap = _load_flow_snapshot()
        if flow_snap:
            sector_flow = flow_snap.get("sector_scores", {}).get(sector_id)
            if sector_flow:
                flow_confirmation = sector_flow.get("flow_confirmation", _DEFAULT_FLOW)
                inst_sponsorship_score = sector_flow.get("inst_sponsorship_score")
                flow_detail = sector_flow
                flow_imputed = str(sector_flow.get("data_quality")) in _FLOW_NOT_MEASURED
            else:
                flow_confirmation = _DEFAULT_FLOW
                flow_imputed = True
        else:
            flow_confirmation = _DEFAULT_FLOW
            flow_imputed = True

    composite_result = compute_composite(
        catalyst_alignment=float(catalyst_alignment),
        momentum=float(momentum),
        flow_confirmation=float(flow_confirmation),
        crowding_risk=float(crowding_risk),
    )

    # v7 candidate columns (weight 0, measured by calibration). flow_resid is set by
    # commensurate(), which needs the whole run.
    md = momentum_detail or {}
    cd = catalyst_detail or {}
    return {
        "sector_id": sector_id,
        **composite_result,
        "ca_imputed": ca_imputed,
        "flow_imputed": flow_imputed,
        "inst_sponsorship_score": inst_sponsorship_score,
        "momentum_12_1": md.get("momentum_12_1"),
        "near_52w_high": md.get("near_52w_high_pct"),
        "ca_unpriced": cd.get("ca_unpriced"),
        "flow_resid": None,
        "catalyst_detail": catalyst_detail,
        "momentum_detail": momentum_detail,
        "flow_detail": flow_detail,
        "errors": errors,
    }


def _all_sector_ids() -> list[str]:
    return [
        f.stem.removeprefix("study_")
        for f in sorted(_STUDY_DIR.glob("study_*.json"))
    ]


def _investable_sector_ids() -> list[str]:
    """All investable sector_ids from the taxonomy (watch_only excluded).

    Used by --universe so the heatmap covers EVERY investable sector with a momentum
    baseline, not only the subset that already has a study file. Sectors without a
    study still score: catalyst_alignment falls back to 0 (no study → no linked
    catalysts), momentum comes from the snapshot, crowding from the default — an
    honest momentum-only baseline that the screen then promotes into studies.
    """
    data = yaml.safe_load(_TAXONOMY.read_text(encoding="utf-8"))
    return [
        s["id"]
        for s in data.get("sectors", [])
        if s.get("investable", False) and not s.get("watch_only", False)
    ]



def universe_crowding(sector_ids: list[str], override: float | None = None) -> dict[str, float]:
    """crowding_risk per sector, derived from the study's `narrative_maturity`.

    The SAME derivation `snapshot_repo` uses to record a run. `--universe` used to pass one flat
    default to every sector, so crowding's σ_cross was 0 and the dead-dimension lint (correctly)
    reported the 0.12-weighted dimension as ranking nothing — in the very view that feeds the
    study work list, while the recorded run ranked with it live. The CLI's ranking and the
    recorded ranking have to be the same ranking. An explicit `--crowd` still overrides,
    universe-wide, which is what asking for one number means.
    """
    # Local import: snapshot_repo imports this module.
    from catalyx.store.snapshot_repo import _crowding_for, _narrative_maturity
    if override is not None:
        return {sid: float(override) for sid in sector_ids}
    return {sid: float(_crowding_for(_narrative_maturity(sid))) for sid in sector_ids}


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="CATALYX sector scorer — composite score from all dimensions"
    )
    parser.add_argument("sector_id", nargs="?", help="Sector ID. Omit with --all or --universe.")
    parser.add_argument("--all", action="store_true", help="Score all sectors with a study.")
    parser.add_argument("--universe", action="store_true",
                        help="Score ALL investable sectors from the taxonomy (momentum baseline "
                             "even without a study). Use for the full-coverage heatmap.")
    parser.add_argument("--ca", type=float, default=None, dest="catalyst_alignment",
                        help="Pre-computed catalyst_alignment [0-100]. Default: auto.")
    parser.add_argument("--mom", type=float, default=None, dest="momentum",
                        help="Pre-computed momentum score [0-100]. Default: auto.")
    parser.add_argument("--flow", type=float, default=None,
                        help="flow_confirmation [0-100]. Default: auto-load from flow snapshot.")
    parser.add_argument("--crowd", type=float, default=None,
                        help="crowding_risk [0-100]. Default: derived per sector from the study's "
                             "narrative_maturity, exactly as the recorded run derives it "
                             f"({_DEFAULT_CROWDING} where there is no study).")
    parser.add_argument("--snapshot", type=Path, default=None,
                        help="Explicit momentum snapshot path.")
    parser.add_argument("--json", action="store_true", help="Output raw JSON only.")
    parser.add_argument("--digest", action="store_true",
                        help="One compact line per sector (id composite momentum etf). This is what "
                             "the review's work list actually reads; the full JSON is 100 KB of "
                             "which ~4 fields are consumed.")
    args = parser.parse_args()

    if args.universe:
        sector_ids = _investable_sector_ids()
    elif args.all or args.sector_id is None:
        sector_ids = _all_sector_ids()
    else:
        sector_ids = [args.sector_id]

    crowd = universe_crowding(sector_ids, args.crowd)

    results = []
    for sid in sector_ids:
        results.append(score_sector(
            sector_id=sid,
            catalyst_alignment=args.catalyst_alignment,
            momentum=args.momentum,
            flow_confirmation=args.flow,
            crowding_risk=crowd[sid],
            momentum_snapshot_path=args.snapshot,
        ))

    scale = commensurate(results)
    for line in scale.get("lint", []) + scale.get("correlation_lint", []):
        print(f"  ! {line}", file=sys.stderr)

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2, ensure_ascii=False))
        return

    if args.digest:
        # The work-list view: rank + the one dimension that drives candidacy + the vehicle it would
        # be bought through (None = not buyable → not a candidate, whatever it scores). Nothing else.
        # Local import: snapshot_repo imports this module.
        from catalyx.store.snapshot_repo import primary_etf
        for i, r in enumerate(sorted(results, key=rank_key(results)), 1):
            # z is printed BESIDE comp because z is what the row is ranked on; showing only the
            # 1dp display composite made a correctly-ordered table look mis-sorted.
            zc = r.get("composite_z")
            zs = f"z={zc:<+7.3f}" if zc is not None else "z=  n/a  "
            flag = " ~flow" if r.get("flow_imputed") else ""
            print(f"{i:>3} {r['sector_id']:<40} comp={r['composite']:<6.1f} {zs} "
                  f"mom={r['score_breakdown']['momentum']:<6.1f} "
                  f"etf={primary_etf(r['sector_id']) or '— (not buyable)'}{flag}")
        return

    print("CATALYX — Sector Scorer\n")
    if scale["applied"]:
        eff = "  ".join(f"{d['dimension'][:4]} σ={d['sigma_cross']:.1f}" for d in scale["dimensions"])
        print(f"  composite = 50 + {scale['z_scale']:.0f} × Σ w·z  (n={scale['n']})   {eff}\n")
    hdr = f"  {'sector_id':<45} {'composite':>9}  {'ca':>6}  {'mom':>6}  {'flow':>6}  {'crowd':>6}  {'inst_sp':>7}"
    print(hdr)
    print(f"  {'-'*45} {'-'*9}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*7}")

    for r in sorted(results, key=rank_key(results)):
        sb = r["score_breakdown"]
        inst_sp = r.get("inst_sponsorship_score")
        inst_str = f"{inst_sp:>7.1f}" if inst_sp is not None else "   n/a "
        print(
            f"  {r['sector_id']:<45} {r['composite']:>9.1f}  "
            f"{sb['catalyst_alignment']:>6.1f}  "
            f"{sb['momentum']:>6.1f}  "
            f"{sb['flow_confirmation']:>6.1f}  "
            f"{sb['crowding_risk']:>6.1f}  "
            f"{inst_str}"
        )
        if r.get("errors"):
            for e in r["errors"]:
                print(f"    ! {e}", file=sys.stderr)



if __name__ == "__main__":
    main()
