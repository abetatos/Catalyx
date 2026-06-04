"""Composite sector scorer — orchestrates catalyst_scorer + momentum_engine + flow_data.

Formula source: scoring_weights.yaml §composite_weights

    composite = catalyst_alignment × 0.30
              + momentum          × 0.25
              + flow_confirmation × 0.20
              + valuation_relative × 0.15
              + (100 - crowding_risk) × 0.10

Result capped at [0, 100].

Phase 0.5 defaults (used when auto-derivation is unavailable):
  - flow_confirmation: 50 (neutral). Auto-derived from flow_data.py when a flow snapshot exists.
  - valuation_relative: 50 (neutral). Manual until valuation_engine.py is built.
  - crowding_risk: 35. Override via --crowd or from sector study narrative_maturity.
  - catalyst_alignment: computed by catalyst_scorer if not supplied.
  - momentum: computed by momentum_engine if not supplied.

Usage (callable from skills):
    # Full auto-compute (loads sector study + latest momentum + flow snapshots):
    uv run python -m catalyx.scorer.sector_scorer copper_miners

    # All sectors:
    uv run python -m catalyx.scorer.sector_scorer --all

    # Manual override for specific dimensions:
    uv run python -m catalyx.scorer.sector_scorer copper_miners --flow 50 --val 55 --crowd 35

    # Use pre-computed scores (skip all derivation):
    uv run python -m catalyx.scorer.sector_scorer copper_miners --ca 95 --mom 77.7 --flow 50 --val 55 --crowd 35

    # JSON output:
    uv run python -m catalyx.scorer.sector_scorer copper_miners --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from catalyx.config import weights
from catalyx.scorer.catalyst_scorer import compute_catalyst_alignment
from catalyx.scorer.momentum_engine import compute_momentum_scores

_REPO_ROOT = Path(__file__).parents[2]
_STUDY_DIR = _REPO_ROOT / "data" / "sector_studies"

# Composite weights — single source of truth: scoring_weights.yaml §composite_weights
_CW = weights.composite_weights()
_W_CATALYST = _CW["catalyst_alignment"]
_W_MOMENTUM = _CW["momentum"]
_W_FLOW = _CW["flow_confirmation"]
_W_VALUATION = _CW["valuation_relative"]
_W_CROWDING = _CW["crowding_risk"]  # applied as (100 - crowding_risk) × weight

# Phase 0.5 defaults for dimensions without automated data
_DEFAULT_FLOW = 50.0
_DEFAULT_VALUATION = 50.0
_DEFAULT_CROWDING = 35.0


# ── Formula ────────────────────────────────────────────────────────────────────

def compute_composite(
    catalyst_alignment: float,
    momentum: float,
    flow_confirmation: float,
    valuation_relative: float,
    crowding_risk: float,
) -> dict:
    """Apply composite formula. All inputs in [0, 100].

    Returns composite score + weighted contribution breakdown.
    """
    crowding_inverted = 100.0 - crowding_risk

    contrib_catalyst = catalyst_alignment * _W_CATALYST
    contrib_momentum = momentum * _W_MOMENTUM
    contrib_flow = flow_confirmation * _W_FLOW
    contrib_valuation = valuation_relative * _W_VALUATION
    contrib_crowding = crowding_inverted * _W_CROWDING

    composite = contrib_catalyst + contrib_momentum + contrib_flow + contrib_valuation + contrib_crowding
    composite = round(min(100.0, max(0.0, composite)), 1)

    return {
        "composite": composite,
        "score_breakdown": {
            "catalyst_alignment": round(catalyst_alignment, 1),
            "momentum": round(momentum, 1),
            "flow_confirmation": round(flow_confirmation, 1),
            "valuation_relative": round(valuation_relative, 1),
            "crowding_risk": round(crowding_risk, 1),
        },
        "weighted_contributions": {
            "catalyst_alignment": round(contrib_catalyst, 2),
            "momentum": round(contrib_momentum, 2),
            "flow_confirmation": round(contrib_flow, 2),
            "valuation_relative": round(contrib_valuation, 2),
            "crowding_penalty": round(contrib_crowding, 2),
        },
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
    valuation_relative: float = _DEFAULT_VALUATION,
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
        valuation_relative: Manual input [0, 100]. Default 50.
        crowding_risk: Manual input [0, 100]. Higher = more crowded = penalty.
        momentum_snapshot_path: Optional explicit path to momentum snapshot.
    """
    errors: list[str] = []
    catalyst_detail: dict | None = None
    momentum_detail: dict | None = None
    flow_detail: dict | None = None

    # Derive catalyst_alignment
    if catalyst_alignment is None:
        cat_result = compute_catalyst_alignment(sector_id)
        if "error" in cat_result and "breakdown" not in cat_result:
            errors.append(f"catalyst_scorer: {cat_result['error']}")
            catalyst_alignment = 0.0
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
            else:
                flow_confirmation = _DEFAULT_FLOW
        else:
            flow_confirmation = _DEFAULT_FLOW

    composite_result = compute_composite(
        catalyst_alignment=float(catalyst_alignment),
        momentum=float(momentum),
        flow_confirmation=float(flow_confirmation),
        valuation_relative=float(valuation_relative),
        crowding_risk=float(crowding_risk),
    )

    return {
        "sector_id": sector_id,
        **composite_result,
        "inst_sponsorship_score": inst_sponsorship_score,
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


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="CATALYX sector scorer — composite score from all dimensions"
    )
    parser.add_argument("sector_id", nargs="?", help="Sector ID. Omit with --all.")
    parser.add_argument("--all", action="store_true", help="Score all sectors with a study.")
    parser.add_argument("--ca", type=float, default=None, dest="catalyst_alignment",
                        help="Pre-computed catalyst_alignment [0-100]. Default: auto.")
    parser.add_argument("--mom", type=float, default=None, dest="momentum",
                        help="Pre-computed momentum score [0-100]. Default: auto.")
    parser.add_argument("--flow", type=float, default=None,
                        help="flow_confirmation [0-100]. Default: auto-load from flow snapshot.")
    parser.add_argument("--val", type=float, default=_DEFAULT_VALUATION,
                        help=f"valuation_relative [0-100]. Default: {_DEFAULT_VALUATION}.")
    parser.add_argument("--crowd", type=float, default=_DEFAULT_CROWDING,
                        help=f"crowding_risk [0-100]. Default: {_DEFAULT_CROWDING}.")
    parser.add_argument("--snapshot", type=Path, default=None,
                        help="Explicit momentum snapshot path.")
    parser.add_argument("--json", action="store_true", help="Output raw JSON only.")
    args = parser.parse_args()

    sector_ids = _all_sector_ids() if (args.all or args.sector_id is None) else [args.sector_id]

    results = []
    for sid in sector_ids:
        results.append(score_sector(
            sector_id=sid,
            catalyst_alignment=args.catalyst_alignment,
            momentum=args.momentum,
            flow_confirmation=args.flow,
            valuation_relative=args.val,
            crowding_risk=args.crowd,
            momentum_snapshot_path=args.snapshot,
        ))

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2, ensure_ascii=False))
        return

    print("CATALYX — Sector Scorer\n")
    hdr = f"  {'sector_id':<45} {'composite':>9}  {'ca':>6}  {'mom':>6}  {'flow':>6}  {'val':>6}  {'crowd':>6}  {'inst_sp':>7}"
    print(hdr)
    print(f"  {'-'*45} {'-'*9}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*7}")

    for r in sorted(results, key=lambda x: x["composite"], reverse=True):
        sb = r["score_breakdown"]
        inst_sp = r.get("inst_sponsorship_score")
        inst_str = f"{inst_sp:>7.1f}" if inst_sp is not None else "   n/a "
        print(
            f"  {r['sector_id']:<45} {r['composite']:>9.1f}  "
            f"{sb['catalyst_alignment']:>6.1f}  "
            f"{sb['momentum']:>6.1f}  "
            f"{sb['flow_confirmation']:>6.1f}  "
            f"{sb['valuation_relative']:>6.1f}  "
            f"{sb['crowding_risk']:>6.1f}  "
            f"{inst_str}"
        )
        if r.get("errors"):
            for e in r["errors"]:
                print(f"    ! {e}", file=sys.stderr)

    print("\n--- JSON output ---")
    print(json.dumps(results if len(results) > 1 else results[0], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
