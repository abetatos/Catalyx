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
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from catalyx.config import weights
from catalyx.thesis import structural_monitor as sm

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
        if detected.tzinfo is None:  # date-only anchors (e.g. parsed "2026-02-28") are naive
            detected = detected.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days = (now - detected).total_seconds() / 86400
    except (ValueError, AttributeError):
        days = 0.0
    if halflife_days <= 0:
        return strength
    remaining = math.exp(-math.log(2) / halflife_days * days)
    return round(strength * remaining, 2)


def _anchor_date(ev: dict) -> str:
    """Decay anchor = when the event OCCURRED, not when we registered it.

    Bug fix (2026-06-05): decay was anchored on `detected_at`, so a late-registered
    event under-counted elapsed time and stayed artificially strong. Example:
    `cat_20260228_hormuz_closure` (event 2026-02-28, priced_in 1.0) carried
    `detected_at=2026-06-04` → it decayed as if ~1 day old (92/100) instead of
    ~97 days old (~31/100), keeping a spent, fully-priced-in event near full force.

    Precedence: explicit `event_date` → date parsed from the catalyst id
    (`cat_YYYYMMDD_...`, the canonical event date) → `detected_at`/`created_at`.
    """
    ed = ev.get("event_date")
    if ed:
        return str(ed)
    cid = ev.get("id") or ev.get("catalyst_id") or ""
    m = re.search(r"(\d{8})", str(cid))
    if m:
        d = m.group(1)
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return ev.get("detected_at") or ev.get("created_at", "")


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

    # Load all active event catalysts once (also indexed by id for direct lookup)
    all_events = [e for e in _load_all_event_catalysts() if e.get("status") == "active"]
    events_by_id = {e.get("id"): e for e in all_events}

    # Which of the listed ids are real structural catalysts (have a YAML)?
    # A direct event already linked to one of these is counted via the structural's
    # confirm/contradict boost — it must NOT also contribute as a direct event.
    present_structurals = {
        sid for sid in structural_ids
        if (_STRUCTURAL_DIR / f"{sid.removeprefix('struct_')}.yaml").exists()
        or (_STRUCTURAL_DIR / f"{sid}.yaml").exists()
    }

    structural_results = []
    direct_event_results = []

    for sid in structural_ids:
        # ── Direct event branch ──────────────────────────────────────────────
        # An event catalyst listed directly in active_catalyst_ids hits the sector
        # on its own (e.g. an `independent` event with no structural parent). It is
        # NOT a structural — give it its own decayed-strength term in the aggregate.
        struct_exists = (
            (_STRUCTURAL_DIR / f"{sid.removeprefix('struct_')}.yaml").exists()
            or (_STRUCTURAL_DIR / f"{sid}.yaml").exists()
        )
        if not struct_exists and (sid.startswith("cat_") or sid in events_by_id):
            ev = events_by_id.get(sid)
            if ev is None:
                direct_event_results.append({
                    "event_id": sid,
                    "type": "direct_event",
                    "error": f"Event not found or not active: {sid}",
                    "modified_score": None,
                })
                continue
            relation = ev.get("relation_to_structural", "independent")
            # Dedup: if this event is already linked (related_catalyst_ids) to a
            # structural that is ALSO present in this sector, it is counted via that
            # structural's confirm/contradict boost — do not double-count it here.
            related = ev.get("related_catalyst_ids") or []
            if any(r in present_structurals for r in related):
                direct_event_results.append({
                    "event_id": sid,
                    "type": "direct_event",
                    "relation": relation,
                    "modified_score": None,
                    "note": "counted via linked structural (not double-counted)",
                })
                continue
            strength = ev.get("strength_score", 0)
            halflife = ev.get("decay_halflife_days", _DEFAULT_HALFLIFE)
            detected = _anchor_date(ev)
            decayed = _decayed_strength(strength, halflife, detected)
            # A direct event contributes its decayed strength as a standalone signal.
            # `contradicts` has no structural target in this context, so it is recorded
            # but not aggregated (nothing to dampen).
            contributes = relation in ("independent", "confirms")
            direct_event_results.append({
                "event_id": sid,
                "type": "direct_event",
                "relation": relation,
                "strength_original": strength,
                "strength_decayed": decayed,
                "halflife_days": halflife,
                "modified_score": round(decayed, 2) if contributes else None,
            })
            continue

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
            detected = _anchor_date(ev)
            decayed = _decayed_strength(strength, halflife, detected)
            relation = ev.get("relation_to_structural", "independent")
            modified = _apply_interaction(base_score, decayed, relation)
            event_details.append({
                "event_id": ev["id"],
                "relation": relation,
                "strength_original": strength,
                "strength_decayed": decayed,
                "halflife_days": halflife,
                "event_date": detected,
                "modified_structural_score": modified,
            })

        # Pick the dominant event per structural: highest absolute impact
        if event_details:
            dominant = max(event_details, key=lambda e: abs(e["modified_structural_score"] - base_score))
            modified_score = dominant["modified_structural_score"]
        else:
            modified_score = base_score

        # Regime state: fundamental gate (indicator health) + TIME-INDEPENDENT persistence gate.
        # Persistence = DISTINCT contradicting developments that are still live (decayed strength
        # ≥ τ) AND within the calendar window — counted from event timestamps, not across runs, so
        # the verdict is the same whether the analysis runs daily/weekly/monthly. A lone live
        # contradict → `contested`; `breaking` needs corroboration (indicators) or a 2nd distinct
        # development before the first decays. See docs/DESIGN_catalyst_regime_discrimination.md.
        monitor = sm.evaluate_structural(sc)
        contradicts = [e for e in event_details if e["relation"] == "contradicts"]
        live_windowed = [e for e in contradicts
                         if e["strength_decayed"] >= sm.TAU_EVT
                         and sm.within_window(e.get("event_date"))]
        n_live = len({e["event_id"] for e in live_windowed})
        regime_state = sm.classify_structural(monitor["degrading"], n_live)
        persistence = sm.persistence_evidence(live_windowed)

        structural_results.append({
            "structural_id": sid,
            "base_score": base_score,
            "modified_score": modified_score,
            "events": event_details,
            "regime_state": regime_state,
            "degrading": monitor["degrading"],
            "persistence": persistence,   # contextual dossier for Claude's escalation judgement
            "monitor": monitor,
        })

    # Aggregate: max-anchored noisy-OR over modified scores (skip errored catalysts).
    # Both structural scores AND direct-event scores enter the same pool.
    valid = [
        r["modified_score"]
        for r in (structural_results + direct_event_results)
        if r.get("modified_score") is not None
    ]
    if not valid:
        return {
            "sector_id": sector_id,
            "error": "No valid structural or event scores to aggregate",
            "breakdown": structural_results,
            "direct_events": direct_event_results,
        }

    catalyst_alignment = _aggregate_alignment(valid)

    # Sector regime state: worst state among the structurals that materially drive the
    # alignment (within MATERIAL_MARGIN of the anchor). Additive annotation only — it does
    # NOT change catalyst_alignment, so no effect on the composite formula / scoring_version.
    anchor = max(valid)
    material_structs = [r for r in structural_results
                        if r.get("modified_score") is not None
                        and r["modified_score"] >= anchor - sm.MATERIAL_MARGIN]
    sector_regime = sm.sector_state(
        [r for r in structural_results if r.get("modified_score") is not None],
        anchor,
    )
    # Advisory: does any material driver warrant a closer look in the monthly review? (dispersed
    # multiple developments, or measured fundamental degradation). NOT a verdict — routes Claude's
    # attention; the escalation decision stays with Claude + the user.
    sector_review = any(
        (r.get("persistence") or {}).get("review_recommended") or r.get("regime_state") == "breaking"
        for r in material_structs
    )

    # Dominant catalyst type
    has_linked_events = any(r.get("events") for r in structural_results)
    has_direct_events = any(
        r.get("modified_score") is not None for r in direct_event_results
    )
    has_structural = any(r.get("modified_score") is not None for r in structural_results)
    if (has_linked_events or has_direct_events) and has_structural:
        dominant_type = "event+structural"
    elif has_direct_events and not has_structural:
        dominant_type = "event"
    else:
        dominant_type = "structural"

    return {
        "sector_id": sector_id,
        "catalyst_alignment": catalyst_alignment,
        "regime_state": sector_regime,
        "regime_review_recommended": sector_review,
        "structural_count": sum(1 for r in structural_results if r.get("modified_score") is not None),
        "direct_event_count": sum(1 for r in direct_event_results if r.get("modified_score") is not None),
        "dominant_catalyst_type": dominant_type,
        "computed_at": date.today().isoformat(),
        "breakdown": structural_results,
        "direct_events": direct_event_results,
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def _format_result(r: dict) -> str:
    if "error" in r and "breakdown" not in r:
        return f"  {r['sector_id']}: ERROR — {r['error']}"
    score = r.get("catalyst_alignment")
    score_str = f"{score:.1f}" if score is not None else "N/A"
    return (
        f"  {r['sector_id']:<40} catalyst_alignment={score_str}  "
        f"[{r.get('regime_state', 'intact')}]  "
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
            state = sc.get("regime_state", "intact")
            print(f"    {sc['structural_id']}: base={base}  modified={mod}{delta}  [{state}]")
            for ev in sc.get("events", []):
                print(
                    f"      {ev['event_id']}: {ev['relation']}  "
                    f"strength={ev['strength_original']}→{ev['strength_decayed']:.1f}  "
                    f"→ structural_score={ev['modified_structural_score']}"
                )
        for de in r.get("direct_events", []):
            if "error" in de:
                print(f"    {de['event_id']} (direct event): ERROR — {de['error']}")
                continue
            ms = de.get("modified_score")
            ms_str = f"{ms:.1f}" if isinstance(ms, float) else "n/a (not aggregated)"
            print(
                f"    {de['event_id']} (direct {de['relation']} event): "
                f"strength={de['strength_original']}→{de['strength_decayed']:.1f}  "
                f"→ contributes={ms_str}"
            )
        print()

    print("--- JSON output ---")
    print(json.dumps(results if len(results) > 1 else results[0], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
