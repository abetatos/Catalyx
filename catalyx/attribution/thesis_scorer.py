"""ClosedThesis right_reason_score formula.

Formula source: scoring_weights.yaml §CLOSED THESIS RUBRICS

right_reason_score =
    (validated_assumptions / total_assumptions) × 0.40
  + (catalyst_materialized ? 0.35 : 0.0)
  + (alpha_from_catalyst   ? 0.25 : 0.0)

Definitions:
  validated_assumptions: count of assumption_validation entries with outcome="validated"
  total_assumptions:     total count of assumption_validation entries (all outcomes)
  catalyst_materialized: True ONLY if the primary mechanism described in thesis.catalyst
                         actually occurred AND caused the price move (not coincidental).
  alpha_from_catalyst:   True if attribution.catalyst_alpha > 0 AND
                         attribution.attribution_confidence != "low"

Output: float in [0.0, 1.0] (NOT a free-float LLM estimate — always compute).

Usage (callable from skills):
    uv run python -m catalyx.attribution.thesis_scorer <path/to/closed_thesis.json>
    uv run python -m catalyx.attribution.thesis_scorer --all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]
_THESES_DIR = _REPO_ROOT / "data" / "theses"

_WEIGHT_ASSUMPTIONS = 0.40
_WEIGHT_CATALYST_MATERIALIZED = 0.35
_WEIGHT_ALPHA = 0.25


# ── Core ──────────────────────────────────────────────────────────────────────

def compute_right_reason_score(closed_thesis: dict) -> dict:
    """Compute right_reason_score from a ClosedThesis dict.

    Args:
        closed_thesis: Parsed ClosedThesis JSON dict.

    Returns:
        Dict with right_reason_score, component scores, and validation detail.
    """
    errors: list[str] = []

    # Term 1: assumption validation ratio
    assumption_validation = closed_thesis.get("assumption_validation", [])
    total_assumptions = len(assumption_validation)
    if total_assumptions == 0:
        errors.append("assumption_validation is empty — cannot compute term 1")
        term1 = 0.0
        validated_count = 0
    else:
        validated_count = sum(
            1 for av in assumption_validation
            if av.get("outcome") == "validated"
        )
        term1 = (validated_count / total_assumptions) * _WEIGHT_ASSUMPTIONS

    # Term 2: catalyst materialised
    catalyst_materialized = closed_thesis.get("catalyst_materialized")
    if catalyst_materialized is None:
        errors.append("catalyst_materialized field missing — defaulting to False")
        catalyst_materialized = False
    term2 = _WEIGHT_CATALYST_MATERIALIZED if catalyst_materialized else 0.0

    # Term 3: alpha from catalyst
    attribution = closed_thesis.get("attribution", {})
    catalyst_alpha = attribution.get("catalyst_alpha")
    attribution_confidence = attribution.get("attribution_confidence")

    if catalyst_alpha is None:
        errors.append("attribution.catalyst_alpha missing — defaulting to False for term 3")
        alpha_from_catalyst = False
    elif attribution_confidence == "low":
        # Low confidence attribution cannot support True — per spec
        alpha_from_catalyst = False
    else:
        alpha_from_catalyst = catalyst_alpha > 0

    term3 = _WEIGHT_ALPHA if alpha_from_catalyst else 0.0

    right_reason_score = round(term1 + term2 + term3, 4)

    return {
        "thesis_id": closed_thesis.get("id", closed_thesis.get("original_thesis_id")),
        "right_reason_score": right_reason_score,
        "components": {
            "term1_assumption_ratio": {
                "validated": validated_count,
                "total": total_assumptions,
                "ratio": round(validated_count / total_assumptions, 4) if total_assumptions > 0 else 0.0,
                "contribution": round(term1, 4),
                "weight": _WEIGHT_ASSUMPTIONS,
            },
            "term2_catalyst_materialized": {
                "value": bool(catalyst_materialized),
                "contribution": round(term2, 4),
                "weight": _WEIGHT_CATALYST_MATERIALIZED,
            },
            "term3_alpha_from_catalyst": {
                "catalyst_alpha": catalyst_alpha,
                "attribution_confidence": attribution_confidence,
                "value": alpha_from_catalyst,
                "contribution": round(term3, 4),
                "weight": _WEIGHT_ALPHA,
            },
        },
        "errors": errors,
    }


def score_closed_thesis_file(path: Path) -> dict:
    """Load and score a ClosedThesis JSON file."""
    thesis = json.loads(path.read_text(encoding="utf-8"))
    result = compute_right_reason_score(thesis)
    result["_source_file"] = str(path)
    return result


def score_all() -> list[dict]:
    """Score all closed thesis files (files matching closed_thesis_*.json or thesis_*.json with status=closed)."""
    results = []
    for f in sorted(_THESES_DIR.glob("*.json")):
        try:
            thesis = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        # Only closed theses have assumption_validation and attribution
        if thesis.get("status") not in ("closed", "validated", "invalidated"):
            continue
        if not thesis.get("assumption_validation"):
            continue
        result = compute_right_reason_score(thesis)
        result["_source_file"] = str(f)
        results.append(result)
    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

def _format_result(r: dict) -> str:
    score = r.get("right_reason_score")
    score_str = f"{score:.4f}" if score is not None else "N/A"
    c = r.get("components", {})
    t1 = c.get("term1_assumption_ratio", {})
    t2 = c.get("term2_catalyst_materialized", {})
    t3 = c.get("term3_alpha_from_catalyst", {})
    return (
        f"  {r.get('thesis_id', '?'):<50}  score={score_str}  "
        f"assumptions={t1.get('validated', '?')}/{t1.get('total', '?')}  "
        f"catalyst={'✓' if t2.get('value') else '✗'}  "
        f"alpha={'✓' if t3.get('value') else '✗'}"
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="CATALYX thesis scorer — compute right_reason_score for ClosedThesis"
    )
    parser.add_argument(
        "thesis_file", nargs="?", type=Path,
        help="Path to a ClosedThesis JSON file. Omit to use --all."
    )
    parser.add_argument("--all", action="store_true",
                        help=f"Score all closed theses in {_THESES_DIR}")
    parser.add_argument("--json", action="store_true", help="Output raw JSON only.")
    args = parser.parse_args()

    if args.all or args.thesis_file is None:
        results = score_all()
        if not results:
            print("No closed theses found (status=closed/validated/invalidated with assumption_validation).")
            return
    else:
        results = [score_closed_thesis_file(args.thesis_file)]

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2, ensure_ascii=False))
        return

    print("CATALYX — Thesis Scorer (right_reason_score)\n")
    print(f"  Formula: (validated/total)×0.40 + catalyst_materialized×0.35 + alpha×0.25\n")
    for r in results:
        print(_format_result(r))
        if r.get("errors"):
            for e in r["errors"]:
                print(f"    ⚠ {e}", file=sys.stderr)
    print("\n--- JSON output ---")
    print(json.dumps(results if len(results) > 1 else results[0], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
