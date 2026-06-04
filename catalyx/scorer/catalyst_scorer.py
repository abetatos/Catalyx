"""Catalyst alignment scorer for SectorSnapshot.

Implements the confirms/contradicts/independent formula with event decay
(scoring_weights.yaml §STRUCTURAL ↔ EVENT INTERACTION v1.5 — additive).

Formula summary:
  For each structural catalyst active in a sector:
    1. Get structural intensity score (from intensity_engine / YAML)
    2. Find event catalysts relating to this structural catalyst
    3. Apply decay: event_strength_decayed = strength × exp(-ln2/halflife × days_elapsed)
    4. Apply interaction formula by relation_to_structural (ADDITIVE points):
       - confirms:     boost = confirm_max_points × (decayed/100)
                       case_c = structural × 0.45 + decayed × 0.55
                       score = max(structural, min(structural + boost, case_c))
       - contradicts:  penalty = contradict_max_points × (decayed/100)
                       score = max(0, min(structural - penalty, structural))
       - independent:  score = structural × 0.45 + decayed × 0.55
  5. If multiple events relate to the same structural catalyst, use the one
     with the highest |impact| (strongest confirms or contradicts wins).
  6. catalyst_alignment = max-anchored noisy-OR over modified_structural_scores

Multi-structural aggregation: max-anchored noisy-OR (see _aggregate_alignment).
The strongest catalyst sets the floor; each additional catalyst closes part of
the remaining gap to 100 in proportion to its strength. This is monotonic and
makes "more confirming catalysts = stronger signal" actually true — unlike the
arithmetic mean it replaced, where adding a weaker catalyst diluted a strong one.

Usage (callable from skills):
    uv run python -m catalyx.scorer.catalyst_scorer <sector_id>
    uv run python -m catalyx.scorer.catalyst_scorer --all

Output: JSON with catalyst_alignment score and full breakdown.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from catalyx.config import weights

_REPO_ROOT = Path(__file__).parents[2]
_STRUCTURAL_DIR = _REPO_ROOT / "catalyx" / "config" / "structural_catalysts"
_CATALYST_DIR = _REPO_ROOT / "data" / "catalysts"
_STUDY_DIR = _REPO_ROOT / "data" / "sector_studies"

# Single source of truth: scoring_weights.yaml (loaded via catalyx.config.weights)
# v1.5: interaction is ADDITIVE — confirm/contradict adjust the structural score by
# points scaled with the decayed event strength (not multiplicative factors).
_CONFIRM_MAX_POINTS, _CONTRADICT_MAX_POINTS = weights.catalyst_interaction_deltas()
_STRUCTURAL_SUB_WEIGHT, _EVENT_SUB_WEIGHT = weights.catalyst_sub_weights()
_DEFAULT_HALFLIFE = weights.event_default_halflife()

# Multi-catalyst aggregation: how much each additional (weaker) catalyst closes
# the remaining gap to 100. 0.0 → only the strongest catalyst counts; 1.0 → a full
# noisy-OR. See scoring_weights.yaml §MULTI-CATALYST AGGREGATION.
_REINFORCE_FACTOR = weights.reinforce_factor()


def _aggregate_alignment(modified_scores: list[float]) -> float:
    """Combine per-catalyst modified scores into one sector catalyst_alignment.

    Max-anchored noisy-OR: the strongest catalyst sets the floor, and each
    additional catalyst closes part of the remaining gap to 100 in proportion
    to its own strength. This is monotonic (adding any catalyst never lowers the
    score) and bounded to [max(scores), 100].

    Rationale: a sector backed by several strong structural catalysts must rank
    ABOVE one backed by a single equally-strong catalyst. The previous arithmetic
    mean did the opposite — adding a weaker catalyst diluted a strong one, which
    contradicts the stated intent that more confirming catalysts = stronger signal.
    """
    ordered = sorted(modified_scores, reverse=True)
    combined = ordered[0]
    for score in ordered[1:]:
        combined += (100.0 - combined) * (score / 100.0) * _REINFORCE_FACTOR
    return round(min(100.0, combined), 1)


# ── Decay ──────────────────────────────────────────────────────────────────────

def _decayed_strength(strength: float, halflife_days: float, detected_at: str) -> float:
    """Apply exponential decay: strength × exp(-ln2/halflife × days_elapsed)."""
    try:
        detected = datetime.fromisoformat(detected_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        days = (now - detected).total_seconds() / 86400
    except (ValueError, AttributeError):
        days = 0.0
    if halflife_days <= 0:
        return strength
    remaining = math.exp(-math.log(2) / halflife_days * days)
    return round(strength * remaining, 2)


# ── Interaction formulas ───────────────────────────────────────────────────────

def _apply_confirms(structural: float, decayed: float) -> float:
    """Additive boost (v1.5): +confirm_max_points at decayed=100, scaled linearly.
    Floor at structural (a confirm never lowers it), cap at the independent blend
    (a confirm never outscores an equally-strong independent signal)."""
    boost = _CONFIRM_MAX_POINTS * (decayed / 100.0)
    raw = structural + boost
    case_c = structural * _STRUCTURAL_SUB_WEIGHT + decayed * _EVENT_SUB_WEIGHT
    return round(max(structural, min(raw, case_c)), 2)


def _apply_contradicts(structural: float, decayed: float) -> float:
    """Additive penalty (v1.5): -contradict_max_points at decayed=100, scaled linearly.
    Floor at 0, cap at structural (a contradict never raises it)."""
    penalty = _CONTRADICT_MAX_POINTS * (decayed / 100.0)
    raw = structural - penalty
    return round(max(0.0, min(raw, structural)), 2)


def _apply_independent(structural: float, decayed: float) -> float:
    return round(structural * _STRUCTURAL_SUB_WEIGHT + decayed * _EVENT_SUB_WEIGHT, 2)


def _apply_interaction(structural: float, decayed: float, relation: str) -> float:
    if relation == "confirms":
        return _apply_confirms(structural, decayed)
    if relation == "contradicts":
        return _apply_contradicts(structural, decayed)
    return _apply_independent(structural, decayed)


# ── Data loaders ───────────────────────────────────────────────────────────────

def _load_structural(catalyst_id: str) -> dict | None:
    path = _STRUCTURAL_DIR / f"{catalyst_id.removeprefix('struct_')}.yaml"
    if not path.exists():
        # Try full name as filename
        path = _STRUCTURAL_DIR / f"{catalyst_id}.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_all_event_catalysts() -> list[dict]:
    events = []
    for f in _CATALYST_DIR.glob("cat_*.json"):
        try:
            events.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return events


def _load_sector_study(sector_id: str) -> dict | None:
    path = _STUDY_DIR / f"study_{sector_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _all_sector_ids() -> list[str]:
    return [
        f.stem.removeprefix("study_")
        for f in sorted(_STUDY_DIR.glob("study_*.json"))
    ]


# ── Core computation ───────────────────────────────────────────────────────────

def compute_catalyst_alignment(
    sector_id: str,
    structural_ids: list[str] | None = None,
) -> dict:
    """Compute catalyst_alignment for a sector.

    If structural_ids is None, loads them from the sector study's active_catalyst_ids.
    Returns a result dict for JSON serialisation and skill consumption.
    """
    # Resolve structural catalyst IDs
    study = _load_sector_study(sector_id)
    if structural_ids is None:
        if study is None:
            return {"sector_id": sector_id, "error": f"No sector study found at data/sector_studies/study_{sector_id}.json"}
        structural_ids = study.get("active_catalyst_ids", [])

    if not structural_ids:
        return {"sector_id": sector_id, "error": "No active_catalyst_ids in sector study"}

    # Load all active event catalysts once
    all_events = [e for e in _load_all_event_catalysts() if e.get("status") == "active"]

    structural_results = []

    for sid in structural_ids:
        sc = _load_structural(sid)
        if sc is None:
            structural_results.append({
                "structural_id": sid,
                "error": f"YAML not found for {sid}",
                "modified_score": None,
            })
            continue

        base_score = sc.get("intensity", {}).get("current_score")
        if base_score is None:
            structural_results.append({
                "structural_id": sid,
                "error": "intensity.current_score missing — run intensity_engine --write-back first",
                "modified_score": None,
            })
            continue

        # Find events that relate to this structural catalyst
        related_events = [
            e for e in all_events
            if sid in (e.get("related_catalyst_ids") or [])
            and e.get("relation_to_structural") in ("confirms", "contradicts", "independent")
        ]

        event_details = []
        for ev in related_events:
            strength = ev.get("strength_score", 0)
            halflife = ev.get("decay_halflife_days", _DEFAULT_HALFLIFE)
            detected = ev.get("detected_at") or ev.get("created_at", "")
            decayed = _decayed_strength(strength, halflife, detected)
            relation = ev.get("relation_to_structural", "independent")
            modified = _apply_interaction(base_score, decayed, relation)
            event_details.append({
                "event_id": ev["id"],
                "relation": relation,
                "strength_original": strength,
                "strength_decayed": decayed,
                "halflife_days": halflife,
                "modified_structural_score": modified,
            })

        # Pick the dominant event per structural: highest absolute impact
        if event_details:
            dominant = max(event_details, key=lambda e: abs(e["modified_structural_score"] - base_score))
            modified_score = dominant["modified_structural_score"]
        else:
            modified_score = base_score

        structural_results.append({
            "structural_id": sid,
            "base_score": base_score,
            "modified_score": modified_score,
            "events": event_details,
        })

    # Aggregate: max-anchored noisy-OR over modified scores (skip errored catalysts)
    valid = [r["modified_score"] for r in structural_results if r.get("modified_score") is not None]
    if not valid:
        return {"sector_id": sector_id, "error": "No valid structural scores to aggregate", "breakdown": structural_results}

    catalyst_alignment = _aggregate_alignment(valid)

    # Dominant catalyst type: structural if no events, else event
    has_events = any(r.get("events") for r in structural_results)

    return {
        "sector_id": sector_id,
        "catalyst_alignment": catalyst_alignment,
        "structural_count": len(valid),
        "dominant_catalyst_type": "event+structural" if has_events else "structural",
        "computed_at": date.today().isoformat(),
        "breakdown": structural_results,
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def _format_result(r: dict) -> str:
    if "error" in r and "breakdown" not in r:
        return f"  {r['sector_id']}: ERROR — {r['error']}"
    score = r.get("catalyst_alignment")
    score_str = f"{score:.1f}" if score is not None else "N/A"
    return (
        f"  {r['sector_id']:<40} catalyst_alignment={score_str}  "
        f"({r.get('structural_count', 0)} structural, "
        f"type={r.get('dominant_catalyst_type', '?')})"
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="CATALYX catalyst scorer — compute catalyst_alignment per sector"
    )
    parser.add_argument(
        "sector_id", nargs="?",
        help="Sector ID to score (e.g. copper_miners). Omit to use --all."
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Score all sectors that have a sector study"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output raw JSON only"
    )
    args = parser.parse_args()

    if args.all or args.sector_id is None:
        results = [compute_catalyst_alignment(sid) for sid in _all_sector_ids()]
    else:
        results = [compute_catalyst_alignment(args.sector_id)]

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2, ensure_ascii=False))
        return

    print("CATALYX — Catalyst Alignment Scorer\n")
    for r in results:
        print(_format_result(r))
        for sc in r.get("breakdown", []):
            if "error" in sc:
                print(f"    {sc['structural_id']}: ERROR — {sc['error']}")
                continue
            base = sc.get("base_score", "?")
            mod = sc.get("modified_score", "?")
            delta = f"  Δ={mod - base:+.1f}" if isinstance(mod, float) and isinstance(base, float) else ""
            print(f"    {sc['structural_id']}: base={base}  modified={mod}{delta}")
            for ev in sc.get("events", []):
                print(
                    f"      {ev['event_id']}: {ev['relation']}  "
                    f"strength={ev['strength_original']}→{ev['strength_decayed']:.1f}  "
                    f"→ structural_score={ev['modified_structural_score']}"
                )
        print()

    print("--- JSON output ---")
    print(json.dumps(results if len(results) > 1 else results[0], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
