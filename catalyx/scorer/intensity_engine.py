"""Deterministic intensity scorer for StructuralCatalyst objects.

Formula (scoring_weights.yaml §STRUCTURAL CATALYST INTENSITY, v1.5):
  1. CONTINUOUS score per indicator in [0, 100] (no more 🟢/🟡/🔴 buckets):
       method = percentile_with_saturating_fallback
       • >= min_history_points values → empirical percentile of current_value
         within the indicator's own value_history (+ current). lower_is_stronger
         inverts: score = (1 - pct) × 100.
       • otherwise (cold start) → a saturating curve anchored on the thresholds
         (weak → 50, strong → 80, asymptoting to 100 far above strong), clamped.
     The 🟢/🟡/🔴 color is now DERIVED from this score and is display-only.
  2. indicator_avg = weighted mean (equal weight unless indicator_weight set)
  3. trend_delta (ADDITIVE points) from intensity.history last 2-3 periods
  4. score = round(clamp(indicator_avg + trend_delta, min, max), 1)

All constants come from scoring_weights.yaml via catalyx.config.weights.

Usage (callable from skills via Bash):
    uv run python -m catalyx.scorer.intensity_engine <path/to/catalyst.yaml>
    uv run python -m catalyx.scorer.intensity_engine --all
    uv run python -m catalyx.scorer.intensity_engine --all --write-back   # update YAMLs in place

Write-back behaviour:
  - Updates intensity.current_score and intensity.last_updated in the YAML
  - Prepends a new entry to intensity.history (computation_method: "computed")
  - Writes the derived color to indicator.semaphore and the continuous score to
    indicator.score
  - Does NOT change indicator current_value or value_history — those are the
    user/update-skill's job (the update skill appends each new observation)

Output: JSON with computed_score, stored_score, delta, and per-indicator breakdown.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

import yaml

from catalyx.config import weights

# ── Config from scoring_weights.yaml (via weights.py — single source of truth) ──

_SCORING = weights.indicator_scoring()
_MIN_HISTORY_POINTS = int(_SCORING["min_history_points"])
_ANCHOR_WEAK = float(_SCORING["fallback_anchors"]["weak"])
_ANCHOR_STRONG = float(_SCORING["fallback_anchors"]["strong"])
_ABOVE_STRONG_DECAY = float(_SCORING["fallback_above_strong_decay"])
_IND_CLAMP_LO, _IND_CLAMP_HI = (float(x) for x in _SCORING["clamp"])

_COLOR_GREEN, _COLOR_AMBER = weights.indicator_color_thresholds()
_TREND_DELTAS = weights.intensity_trend_deltas()
_INTENSITY_MIN, _INTENSITY_MAX = weights.intensity_bounds()

_CATALYSTS_DIR = Path(__file__).parents[1] / "config" / "structural_catalysts"


# ── Continuous indicator scoring ─────────────────────────────────────────────

def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _history_for_catalyst(catalyst_id) -> dict[str, list[dict]]:
    """Indicator value-history from the parquet lake (Tier 2 truth), {ind_id: [{date,value}]}.
    Returns {} when the lake has no history for this catalyst → callers fall back to YAML."""
    if not catalyst_id:
        return {}
    try:
        from catalyx.store import indicator_history
        return indicator_history.history_for(catalyst_id)
    except Exception:
        return {}


def _indicator_values(ind: dict, ext_history: list[dict] | None = None) -> list[float]:
    """All numeric observations for an indicator: value_history + current_value.

    `ext_history` (the lake's value_history for this indicator) takes precedence; when it
    is None the deprecated inline YAML `value_history` is used as a fallback. Entries are
    {date, value}; order does not matter for percentile. The current value is always
    included so the percentile reflects where today sits.
    """
    source = ext_history if ext_history is not None else (ind.get("value_history") or [])
    values: list[float] = []
    for entry in source:
        v = entry.get("value") if isinstance(entry, dict) else entry
        if isinstance(v, (int, float)):
            values.append(float(v))
    cur = ind.get("current_value")
    if isinstance(cur, (int, float)):
        values.append(float(cur))
    return values


def _percentile_score(value: float, sample: list[float], direction: str) -> float:
    """Empirical percentile of `value` within `sample` (the 'mean' rank method),
    mapped to [0, 100]. lower_is_stronger inverts the result."""
    n = len(sample)
    n_below = sum(1 for s in sample if s < value)
    n_equal = sum(1 for s in sample if s == value)
    pct = (n_below + 0.5 * n_equal) / n * 100.0
    if direction == "lower_is_stronger":
        pct = 100.0 - pct
    return pct


def _fallback_score(value: float, ind: dict) -> float:
    """Cold-start fallback: a SATURATING curve anchored on the thresholds.

      x = (value - weak) / (strong - weak)   # 0 at weak, 1 at strong
      x ≤ 1: linear  weak_anchor → strong_anchor   (and below weak, continues down)
      x > 1: strong_anchor + (100 - strong_anchor)·(1 - exp(-decay·(x-1)))

    The signed (strong - weak) difference makes x correct for both directions
    (for lower_is_stronger, threshold_strong < threshold_weak, so the sign flips).
    Being far above threshold_strong asymptotes toward 100 instead of clamping there,
    so over-threshold values grade by margin instead of all saturating.
    """
    strong = float(ind["threshold_strong"])
    weak = float(ind["threshold_weak"])
    if strong == weak:
        # Degenerate thresholds — fall back to a direction-aware step.
        meets = value <= strong if ind["direction"] == "lower_is_stronger" else value >= strong
        return _ANCHOR_STRONG if meets else _ANCHOR_WEAK

    x = (value - weak) / (strong - weak)
    if x <= 1.0:
        return _ANCHOR_WEAK + x * (_ANCHOR_STRONG - _ANCHOR_WEAK)
    return _ANCHOR_STRONG + (100.0 - _ANCHOR_STRONG) * (1.0 - math.exp(-_ABOVE_STRONG_DECAY * (x - 1.0)))


def _indicator_score(ind: dict, ext_history: list[dict] | None = None) -> float:
    """Continuous [0, 100] score for one indicator. Percentile when enough history
    exists, else the saturating threshold fallback. No data → clamp floor."""
    cur = ind.get("current_value")
    if not isinstance(cur, (int, float)):
        return _IND_CLAMP_LO

    sample = _indicator_values(ind, ext_history)
    if len(sample) >= _MIN_HISTORY_POINTS:
        raw = _percentile_score(float(cur), sample, ind["direction"])
    else:
        raw = _fallback_score(float(cur), ind)
    return round(_clamp(raw, _IND_CLAMP_LO, _IND_CLAMP_HI), 1)


def _color(score: float) -> str:
    """Display-only color derived from a continuous score."""
    if score >= _COLOR_GREEN:
        return "🟢"
    if score >= _COLOR_AMBER:
        return "🟡"
    return "🔴"


def _scoring_mode(ind: dict, ext_history: list[dict] | None = None) -> str:
    return "percentile" if len(_indicator_values(ind, ext_history)) >= _MIN_HISTORY_POINTS else "fallback"


# ── Trend (additive delta) ───────────────────────────────────────────────────

def _trend_delta(history: list[dict]) -> tuple[float, str]:
    """Return (additive_points, label) from the two most-recent history entries.

    History is expected most-recent-first (as written in YAML files).
    Consecutive = both the most-recent AND the prior period moved in the same direction.
    """
    scores = [h["score"] for h in history if isinstance(h.get("score"), (int, float))]
    if len(scores) < 2:
        return float(_TREND_DELTAS["flat"]), "→ (flat — insufficient history)"

    d1 = scores[0] - scores[1]  # most-recent period delta

    if len(scores) >= 3:
        d2 = scores[1] - scores[2]  # prior period delta
        if d1 > 2 and d2 > 2:
            return float(_TREND_DELTAS["rising_2plus"]), "↑↑ (rising 2+ consecutive)"
        if d1 < -2 and d2 < -2:
            return float(_TREND_DELTAS["falling_2plus"]), "↓↓ (falling 2+ consecutive)"

    if d1 > 2:
        return float(_TREND_DELTAS["rising_1"]), "↑ (rising 1 period)"
    if d1 < -2:
        return float(_TREND_DELTAS["falling_1"]), "↓ (falling 1 period)"
    return float(_TREND_DELTAS["flat"]), "→ (flat)"


# ── Core logic ───────────────────────────────────────────────────────────────

def compute_intensity(catalyst: dict) -> dict:
    """Compute intensity score for a parsed StructuralCatalyst dict.

    Returns a result dict suitable for JSON serialisation and skill consumption.
    """
    indicators = catalyst.get("indicators", [])
    if not indicators:
        return {
            "id": catalyst.get("id"),
            "error": "No indicators defined",
        }

    breakdown = []
    scores_weighted: list[tuple[float, float]] = []  # (score, weight)

    ext = _history_for_catalyst(catalyst.get("id"))  # lake value-history per indicator

    for ind in indicators:
        ext_h = ext.get(ind["id"])  # None → falls back to inline YAML value_history
        score = _indicator_score(ind, ext_h)
        color = _color(score)
        weight = float(ind.get("indicator_weight") or 1.0)
        scores_weighted.append((score, weight))

        stored_color = ind.get("semaphore", "")
        breakdown.append({
            "id": ind["id"],
            "name": ind.get("name", ""),
            "current_value": ind.get("current_value"),
            "unit": ind.get("unit", ""),
            "direction": ind["direction"],
            "threshold_strong": ind["threshold_strong"],
            "threshold_weak": ind["threshold_weak"],
            "scoring_mode": _scoring_mode(ind, ext_h),
            "history_points": len(_indicator_values(ind, ext_h)),
            "history_source": "lake" if ext_h is not None else "yaml",
            "indicator_score": score,
            "color_computed": color,
            "color_stored": stored_color,
            "color_drift": color != stored_color and bool(stored_color),
            "weight": weight,
        })

    total_weight = sum(w for _, w in scores_weighted)
    indicator_avg = sum(s * w for s, w in scores_weighted) / total_weight

    history = catalyst.get("intensity", {}).get("history", [])
    trend_delta, trend_label = _trend_delta(history)

    raw = indicator_avg + trend_delta
    computed_score = round(_clamp(raw, _INTENSITY_MIN, _INTENSITY_MAX), 1)

    stored = catalyst.get("intensity", {}).get("current_score")
    delta = round(computed_score - stored, 1) if stored is not None else None

    return {
        "id": catalyst.get("id"),
        "computed_score": computed_score,
        "stored_score": stored,
        "delta": delta,
        "indicator_avg": round(indicator_avg, 2),
        "trend_delta": trend_delta,
        "trend_label": trend_label,
        "capped": raw != computed_score,
        "breakdown": breakdown,
    }


def compute_from_yaml(path: Path) -> dict:
    """Load a YAML file and compute intensity. Returns result dict."""
    catalyst = yaml.safe_load(path.read_text(encoding="utf-8"))
    result = compute_intensity(catalyst)
    result["_source_file"] = str(path)
    return result


def compute_all() -> list[dict]:
    """Compute intensity for every YAML in the structural_catalysts directory."""
    results = []
    for f in sorted(_CATALYSTS_DIR.glob("*.yaml")):
        results.append(compute_from_yaml(f))
    return results


def write_back(path: Path, result: dict, period: str | None = None) -> None:
    """Write computed score back into the YAML file in place.

    Uses ruamel.yaml to preserve original formatting, comments, and block scalars.

    Updates:
      - intensity.current_score, computation_method, last_updated
      - intensity.history — prepends new entry (skips if same period+score already logged)
      - indicators[*].semaphore — set to the derived display color
      - indicators[*].score — set to the continuous indicator score
      Does NOT touch indicator current_value or value_history.
    """
    from ruamel.yaml import YAML as RuamelYAML
    from ruamel.yaml.comments import CommentedMap

    ry = RuamelYAML()
    ry.preserve_quotes = True
    ry.width = 120

    with path.open("r", encoding="utf-8") as fh:
        catalyst = ry.load(fh)

    today = date.today().isoformat()
    new_score = result["computed_score"]
    stored_score = result.get("stored_score")
    breakdown = result.get("breakdown", [])

    intensity = catalyst["intensity"]
    intensity["current_score"] = new_score
    intensity["computation_method"] = "computed"
    intensity["last_updated"] = today

    history = intensity["history"]
    entry_period = period or today
    already_logged = (
        len(history) > 0
        and history[0].get("period") == entry_period
        and history[0].get("score") == new_score
    )
    if not already_logged:
        note = f"Computed {today}: avg={result['indicator_avg']} trend={result['trend_label']}"
        if stored_score is not None and stored_score != new_score:
            note += f" (was {stored_score})"
        new_entry = CommentedMap({
            "period": entry_period,
            "score": new_score,
            "note": note,
            "computation_method": "computed",
        })
        history.insert(0, new_entry)

    by_id = {b["id"]: b for b in breakdown}
    for ind in catalyst.get("indicators", []):
        b = by_id.get(ind["id"])
        if b is not None:
            ind["semaphore"] = b["color_computed"]
            ind["score"] = b["indicator_score"]

    with path.open("w", encoding="utf-8") as fh:
        ry.dump(catalyst, fh)


# ── CLI ────────────────────────────────────────────────────────────────────────

def _format_result(r: dict) -> str:
    """Human-readable one-line summary of a result."""
    if "error" in r:
        return f"  {r['id']}: ERROR — {r['error']}"
    delta_str = f"  Δ={r['delta']:+.1f}" if r["delta"] is not None else ""
    drift = [b for b in r.get("breakdown", []) if b.get("color_drift")]
    drift_str = f"  ⚠ color drift: {[b['id'] for b in drift]}" if drift else ""
    return (
        f"  {r['id']}: computed={r['computed_score']}  "
        f"stored={r['stored_score']}{delta_str}  "
        f"avg={r['indicator_avg']}  trend={r['trend_label']} ({r['trend_delta']:+g}){drift_str}"
    )


def main() -> None:
    # Force UTF-8 on Windows consoles
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="CATALYX intensity engine — compute structural catalyst scores from continuous indicator scores"
    )
    parser.add_argument(
        "yaml_file", nargs="?", type=Path,
        help="Path to a structural catalyst YAML. Omit to use --all."
    )
    parser.add_argument(
        "--all", action="store_true",
        help=f"Compute all catalysts in {_CATALYSTS_DIR}"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output raw JSON (default: human-readable summary + JSON)"
    )
    parser.add_argument(
        "--write-back", action="store_true",
        help="Write computed score back to YAML file(s) in place"
    )
    parser.add_argument(
        "--period", type=str, default=None,
        help="Period label for history entry, e.g. '2026-Q2' (default: today's date)"
    )
    args = parser.parse_args()

    if args.all or args.yaml_file is None:
        results = compute_all()
        paths = sorted(_CATALYSTS_DIR.glob("*.yaml")) if args.write_back else []
    else:
        results = [compute_from_yaml(args.yaml_file)]
        paths = [args.yaml_file] if args.write_back else []

    if args.write_back:
        for path, result in zip(paths, results):
            if "error" not in result:
                write_back(path, result, period=args.period)
                print(f"Updated: {path.name}  score={result['computed_score']}", file=sys.stderr)

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2, ensure_ascii=False))
        return

    print("CATALYX — Structural Catalyst Intensity Engine\n")
    for r in results:
        print(_format_result(r))
        if "breakdown" in r:
            for b in r["breakdown"]:
                drift_flag = " ⚠ DRIFT" if b.get("color_drift") else ""
                print(
                    f"    {b['id']}: {b['color_computed']} "
                    f"score={b['indicator_score']}  val={b['current_value']} {b['unit']}"
                    f"  [{b['scoring_mode']}, n={b['history_points']}]{drift_flag}"
                )
        print()

    print("--- JSON output ---")
    print(json.dumps(results if len(results) > 1 else results[0], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
